#!/usr/bin/env python3
"""Make a single MCP tool call from bash and print the result on stdout.

Works in two places:
  * local Claude Code   -- servers from ~/.claude.json / .mcp.json, OAuth tokens
                           from ~/.claude/.credentials.json (as before)
  * cloud Claude Code / Cowork (claude.ai sandbox) -- servers from the config the
                           harness writes to /tmp/mcp-config-<session>.json; every
                           connector is an HTTP server behind Anthropic's relay,
                           authenticated with the session ingress token.

    mcp_call.py <server> <tool> [json-args] [--timeout SECS]
    mcp_call.py <server> <tool> --args-file <path|->   # args as JSON from file/stdin
    mcp_call.py <server> --list                        # tool names, one per line
    mcp_call.py <server> --describe                    # name, approval policy, blurb
    mcp_call.py <server> --schema <tool>[,<tool>...]   # input schemas, one JSON line each
    mcp_call.py --servers                              # configured servers

<tool> may also be given as the harness name, mcp__<server>__<tool>.

The tool's text content is printed verbatim (usually JSON), so `| jq` applies
directly. A tool that reports failure (isError, or a JSON-RPC error) exits 1 with
the message on stderr. A call the relay refuses for lack of approval exits 3.
"""
import base64
import glob
import gzip
import json
import mimetypes
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

HOME = Path.home()
CLAUDE_JSON = HOME / ".claude.json"
CREDS = HOME / ".claude" / ".credentials.json"
INGRESS_TOKEN_DEFAULT = Path("/home/claude/.claude/remote/.session_ingress_token")
EXIT_NEEDS_APPROVAL = 3

# ${VAR} and ${VAR:-default} as supported in .mcp.json command/args/env/url/headers.
VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\}")

TIMEOUT = 120


def die(msg, code=1):
    print(f"mcp_call: {msg}", file=sys.stderr)
    sys.exit(code)


def expand(val, env):
    if not isinstance(val, str):
        return val

    def repl(m):
        name, has_default, default = m.group(1), m.group(2) is not None, m.group(3)
        v = env.get(name)
        if v not in (None, ""):
            return v
        if has_default:
            return default
        die(f"required env var ${{{name}}} is unset and has no default")

    return VAR_RE.sub(repl, val)


# ---------------------------------------------------------------- discovery

def find_mcp_json(start):
    d = start
    while True:
        f = d / ".mcp.json"
        if f.is_file():
            return f
        if d == d.parent:
            return None
        d = d.parent


def ccr_config_path():
    """The config the cloud harness hands Claude Code. Named after the session."""
    sid = os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID")
    if sid:
        p = Path(f"/tmp/mcp-config-{sid}.json")
        if p.is_file():
            return p
    cands = sorted(glob.glob("/tmp/mcp-config-*.json"), key=os.path.getmtime, reverse=True)
    return Path(cands[0]) if cands else None


def load_servers(cwd):
    """Merge every surface, most specific winning:
    cloud harness config -> user ~/.claude.json -> project .mcp.json -> local
    per-project entry."""
    servers = {}
    ccr = ccr_config_path()
    if ccr:
        servers.update(json.loads(ccr.read_text()).get("mcpServers", {}))
    cj = json.loads(CLAUDE_JSON.read_text()) if CLAUDE_JSON.is_file() else {}
    servers.update(cj.get("mcpServers", {}))
    mj = find_mcp_json(cwd)
    if mj:
        servers.update(json.loads(mj.read_text()).get("mcpServers", {}))
    servers.update(cj.get("projects", {}).get(str(cwd), {}).get("mcpServers", {}))
    return servers


def is_ccr_relay(url):
    u = urllib.parse.urlparse(url)
    return u.netloc == "api.anthropic.com" and u.path.startswith("/v2/ccr-sessions/")


def policies(cfg):
    """{tool: permission_policy} as the harness pushed it at session start.
    Informational: the relay checks the live setting, which may have changed."""
    return {t.get("name"): t.get("permission_policy") for t in (cfg.get("tools") or [])
            if isinstance(t, dict)}


# ---------------------------------------------------------------- auth

def ingress_token():
    """Re-read on every call: the harness may rotate it."""
    p = Path(os.environ.get("CLAUDE_SESSION_INGRESS_TOKEN_FILE") or INGRESS_TOKEN_DEFAULT)
    try:
        t = p.read_text().strip()
    except OSError:
        return None
    return t or None


def oauth_token(server, url):
    """Local Claude Code: pick the live OAuth token for a server. Match by
    serverUrl, drop null/expired tokens, prefer serverName == server (two entries
    can share a serverUrl, one stale with a null token)."""
    if not CREDS.is_file():
        return None
    oauth = json.loads(CREDS.read_text()).get("mcpOAuth", {})
    now = time.time() * 1000
    cands = [v for v in oauth.values()
             if v.get("serverUrl") == url and v.get("accessToken") and v.get("expiresAt", 0) > now]
    cands.sort(key=lambda v: v.get("serverName") == server, reverse=True)
    return cands[0]["accessToken"] if cands else None


# ---------------------------------------------------------------- transports

def rpc(method, _id=None, **params):
    m = {"jsonrpc": "2.0", "method": method, "params": params}
    if _id is not None:
        m["id"] = _id
    return m


class NeedsSession(Exception):
    """The server rejected a session-less call (e.g. mcp.atlassian.com)."""


def parse_body(raw, want_id, server):
    """Plain JSON, or SSE with one JSON-RPC message per `data:` event (progress
    notifications may precede the response). Return the message with want_id."""
    if not raw.strip():
        return None
    if not (raw.lstrip().startswith(("data:", "event:", "id:", ":")) or "\ndata:" in raw):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            die(f"'{server}' answered with something that isn't JSON-RPC: {raw[:200]}")
    msgs, buf = [], []
    for line in raw.splitlines() + [""]:
        if line.startswith("data:"):
            buf.append(line[5:].lstrip())
        elif line == "" and buf:
            try:
                msgs.append(json.loads("\n".join(buf)))
            except json.JSONDecodeError:
                pass
            buf = []
    for m in msgs:
        if isinstance(m, dict) and m.get("id") == want_id:
            return m
    return msgs[-1] if msgs else None


def read_body(resp):
    """urllib never decompresses, and servers may gzip unasked (the claude.ai relay does)."""
    b = resp.read()
    enc = (resp.headers.get("Content-Encoding") or "").lower()
    if "gzip" in enc or b[:2] == b"\x1f\x8b":
        b = gzip.decompress(b)
    elif "deflate" in enc:
        try:
            b = zlib.decompress(b)
        except zlib.error:  # raw deflate, no zlib header
            b = zlib.decompress(b, -zlib.MAX_WBITS)
    return b.decode(errors="replace")


def call_http(server, cfg, method, params):
    url = expand(cfg["url"], os.environ)
    ccr = is_ccr_relay(url)
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream",
               "Accept-Encoding": "identity"}
    for k, v in (cfg.get("headers") or {}).items():
        headers[k] = expand(v, os.environ)
    if not any(k.lower() == "authorization" for k in headers):
        tok = ingress_token() if ccr else oauth_token(server, url)
        if not tok:
            if ccr:
                die("no session ingress token (CLAUDE_SESSION_INGRESS_TOKEN_FILE unreadable).")
            die(f"no live OAuth token for '{server}'. Tokens can't be refreshed from here; "
                f"refresh once in an interactive Claude Code session (/mcp or "
                f"`claude mcp login {server}`), then retry.")
        headers["Authorization"] = f"Bearer {tok}"

    def post(payload, extra=None):
        """POST one JSON-RPC message. Returns (parsed-or-None, response headers)."""
        h = dict(headers)
        h.update(extra or {})
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers=h, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=TIMEOUT)
            raw, rhdrs = read_body(resp), resp.headers
        except urllib.error.HTTPError as e:
            detail = read_body(e)
            if e.code in (401, 403):
                if ccr:
                    die(f"{e.code} from the relay for '{server}': the session token was rejected "
                        f"(expired session?) or the connector needs re-authorising in claude.ai "
                        f"settings. {detail[:200]}")
                die(f"{e.code} from '{server}': token likely expired — refresh via /mcp.")
            if e.code == 400 and "session" in detail.lower() and "Mcp-Session-Id" not in h:
                raise NeedsSession(detail[:200])
            die(f"HTTP {e.code} from '{server}': {detail[:200]}")
        except urllib.error.URLError as e:
            die(f"could not reach '{server}': {e.reason}")
        return parse_body(raw, payload.get("id"), server), rhdrs

    # Try the cheap stateless call first: most servers (and the relay) accept it.
    try:
        out, _ = post(rpc(method, 1, **params))
        if out is not None:
            return out
    except NeedsSession:
        pass

    # Session-required server: initialize, carry Mcp-Session-Id on every later POST.
    try:
        init, ihdrs = post(rpc("initialize", 1, protocolVersion="2025-06-18", capabilities={},
                               clientInfo={"name": "mcp_call", "version": "1"}))
    except NeedsSession as e:
        die(f"'{server}' rejected the initialize too: {e}")
    if init is None:
        die(f"'{server}' returned no body for initialize.")
    sid = ihdrs.get("Mcp-Session-Id") or ihdrs.get("mcp-session-id")
    if not sid:
        die(f"'{server}' requires a session but returned no Mcp-Session-Id header.")
    sess = {"Mcp-Session-Id": sid,
            "MCP-Protocol-Version": (init.get("result") or {}).get("protocolVersion", "2025-06-18")}
    post(rpc("notifications/initialized"), sess)
    out, _ = post(rpc(method, 2, **params), sess)
    if out is None:
        die(f"'{server}' returned no body for {method} even with a session.")
    return out


def call_stdio(cfg, method, params, cwd):
    env = dict(os.environ)
    for k, v in (cfg.get("env") or {}).items():
        env[k] = expand(str(v), os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(cwd)  # CC injects this into stdio servers
    cmd = [expand(cfg["command"], os.environ)] + [expand(a, os.environ) for a in cfg.get("args", [])]
    errf = tempfile.TemporaryFile()  # a file, not a pipe: a chatty server can't block on it
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errf,
                             env=env, cwd=str(cwd), text=True, bufsize=1)
    except OSError as e:
        die(f"could not start {cmd[0]}: {e}")

    def fail(msg):
        p.kill()
        errf.seek(0)
        tail = errf.read().decode(errors="replace").strip().splitlines()[-8:]
        die(msg + ("\n  stderr: " + "\n  ".join(tail) if tail else ""))

    def send(obj):
        try:
            p.stdin.write(json.dumps(obj) + "\n")
            p.stdin.flush()
        except BrokenPipeError:
            fail("stdio server exited early")

    def read_id(want):
        for line in p.stdout:  # newline-delimited JSON
            line = line.strip()
            if not line.startswith("{"):  # skip log noise, as the SDK's ReadBuffer does
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == want:
                return msg
        return None

    # Synchronized handshake: send initialize, WAIT for its response, only then send
    # the request. Sending everything up front loses messages on async-startup servers.
    send(rpc("initialize", 1, protocolVersion="2025-06-18", capabilities={},
             clientInfo={"name": "mcp_call", "version": "1"}))
    if read_id(1) is None:
        fail("stdio server exited before responding to initialize")
    send(rpc("notifications/initialized"))
    send(rpc(method, 2, **params))
    resp = read_id(2)
    if resp is None:
        fail(f"stdio server gave no response to {method}")
    try:
        p.stdin.close()
        p.wait(timeout=5)
    except Exception:
        p.kill()
    return resp


def transport(cfg):
    return cfg.get("type", "stdio" if "command" in cfg else "http")


def request(server, cfg, method, params, cwd):
    ttype = transport(cfg)
    if ttype in ("http", "streamable-http"):
        return call_http(server, cfg, method, params)
    if ttype == "stdio":
        return call_stdio(cfg, method, params, cwd)
    die(f"transport '{ttype}' not supported (only stdio + http/streamable-http)")


def list_tools(server, cfg, cwd):
    tools, cursor = [], None
    while True:
        resp = request(server, cfg, "tools/list", {"cursor": cursor} if cursor else {}, cwd)
        if "error" in resp:
            e = resp["error"]
            die(f"tools/list error {e.get('code')}: {e.get('message')}")
        res = resp.get("result", {})
        tools += res.get("tools", [])
        cursor = res.get("nextCursor")
        if not cursor:
            return tools


# ---------------------------------------------------------------- output

def save_b64(data, mime):
    mime = (mime or "").split(";")[0].strip()
    ext = mimetypes.guess_extension(mime) or ".bin"
    fd, path = tempfile.mkstemp(prefix="mcp_call.", suffix=ext)
    os.write(fd, base64.b64decode(data))
    os.close(fd)
    return path


def emit_result(result):
    """Print text content verbatim; save images/audio/blobs to files and print a
    marker line where they sat. Exit 1 on isError."""
    out = []
    for c in result.get("content", []):
        t = c.get("type")
        if t == "text":
            out.append(c.get("text", ""))
        elif t in ("image", "audio") and c.get("data"):
            out.append(f"[{t}: {save_b64(c['data'], c.get('mimeType'))} ({c.get('mimeType')})]")
        elif t == "resource":
            r = c.get("resource", {})
            if "text" in r:
                out.append(r["text"])
            elif r.get("blob"):
                out.append(f"[resource: {save_b64(r['blob'], r.get('mimeType'))} "
                           f"({r.get('mimeType')}, {r.get('uri')})]")
            else:
                out.append(f"[resource: {r.get('uri')}]")
        elif t == "resource_link":
            out.append(f"[resource_link: {c.get('uri')}]")
    text = "\n".join(out)
    if not text and "structuredContent" in result:
        text = json.dumps(result["structuredContent"])
    if result.get("isError"):
        die(f"tool reported an error: {text[:2000]}")
    print(text if text else json.dumps(result))


def main():
    global TIMEOUT
    argv = sys.argv[1:]
    if "--timeout" in argv:
        i = argv.index("--timeout")
        try:
            TIMEOUT = int(argv[i + 1])
        except (IndexError, ValueError):
            die("--timeout needs a whole number of seconds")
        del argv[i:i + 2]
    signal.signal(signal.SIGALRM, lambda *_: die(f"timed out after {TIMEOUT}s"))
    signal.alarm(TIMEOUT)

    cwd = Path.cwd()
    servers = load_servers(cwd)

    if argv[:1] == ["--servers"]:
        for name, cfg in sorted(servers.items()):
            url = cfg.get("url", "")
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            where = q.get("mcp_url", [url or cfg.get("command", "")])[0]
            via = " (via claude.ai relay)" if url and is_ccr_relay(url) else ""
            print(f"{name}\t{transport(cfg)}\t{where}{via}")
        return

    if len(argv) < 2:
        die("usage: mcp_call.py <server> <tool|--list|--describe|--schema T[,T]> "
            "[json-args | --args-file <path|->] [--timeout SECS]   |   mcp_call.py --servers")
    server, tool, rest = argv[0], argv[1], argv[2:]
    if server not in servers:
        die(f"unknown server '{server}'. Known: {', '.join(sorted(servers)) or '(none)'}")
    cfg = servers[server]
    pol = policies(cfg)

    if tool in ("--list", "tools/list"):
        print("\n".join(t.get("name", "") for t in list_tools(server, cfg, cwd)))
        return
    if tool == "--describe":
        for t in list_tools(server, cfg, cwd):
            blurb = " ".join((t.get("description") or "").split())[:100]
            print(f"{t.get('name')}\t{pol.get(t.get('name'), '-')}\t{blurb}")
        return
    if tool == "--schema":
        if not rest:
            die("--schema needs a tool name (comma-separated for several)")
        want = [w.split("__")[-1] for w in rest[0].split(",")]
        by = {t.get("name"): t for t in list_tools(server, cfg, cwd)}
        for w in want:
            if w not in by:
                die(f"'{server}' has no tool '{w}'")
            print(json.dumps({"name": w, "policy": pol.get(w), "inputSchema": by[w].get("inputSchema")}))
        return

    # Accept the harness's own name for a tool: mcp__<server>__<tool>
    pref = f"mcp__{server}__"
    if tool.startswith(pref):
        tool = tool[len(pref):]

    if "--args-file" in rest:
        i = rest.index("--args-file")
        if i + 1 >= len(rest):
            die("--args-file needs a path, or - for stdin")
        src = rest[i + 1]
        raw = sys.stdin.read() if src == "-" else Path(src).read_text()
    else:
        raw = rest[0] if rest and rest[0] else "{}"
    try:
        args = json.loads(raw)
    except json.JSONDecodeError as e:
        die(f"args aren't valid JSON ({e}): {raw[:200]}")
    if not isinstance(args, dict):
        die("args must be a JSON object")

    resp = request(server, cfg, "tools/call", {"name": tool, "arguments": args}, cwd)
    if "error" in resp:
        e = resp["error"]
        if e.get("code") == -32003:
            # The relay answers an unknown tool with -32003 too; the harness config
            # lists every tool, so use it to tell the two apart.
            if pol and tool not in pol:
                die(f"'{server}' has no tool '{tool}'. `mcp_call.py {server} --list` lists them.")
            die(f"'{server}.{tool}' needs your approval (the claude.ai relay refused it: "
                f"{e.get('message')}). This can't be scripted from bash: make it a native "
                f"tool call, or switch the tool to 'Always allow' in the connector's settings "
                f"for the duration.", EXIT_NEEDS_APPROVAL)
        die(f"tool error {e.get('code')}: {e.get('message')}")
    emit_result(resp.get("result", {}))


if __name__ == "__main__":
    main()
