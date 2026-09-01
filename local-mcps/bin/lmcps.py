#!/usr/bin/env python3
"""Call MCP servers that claude.ai's remote connectors cannot reach: stdio
servers, which have no URL to point a connector at, and HTTP servers whose auth
header name is not on Anthropic's allowlist. Both are reachable from the
code-execution sandbox.

    lmcps servers                          # names + transport + one-liner
    lmcps tools <server> [--schema TOOL]   # live tools/list
    lmcps call <server> <tool> '{"a": 1}'  # the thing that matters
    lmcps describe <server>                # material for a server's `description`
    lmcps refresh                          # re-fetch the config, drop caches

Config is Claude Code's `mcpServers` schema, so a block copied out of any
server's README works unchanged. See SKILL.md for where it comes from.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
LMCPS_HOME = Path(os.environ.get("LMCPS_HOME", Path.home() / ".lmcps"))

# ${VAR} and ${VAR:-default}, as supported in Claude Code's .mcp.json.
VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\}")

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "lmcps", "version": "1"}

# The last handshake's result, carrying `serverInfo` and the server's own
# `instructions`. Stashed rather than returned because only `tools` wants it.
# One process spawns at most one server, so there is nothing to collide with.
LAST_INIT = {}


def die(msg):
    print(f"lmcps: {msg}", file=sys.stderr)
    sys.exit(1)


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


# --- config -----------------------------------------------------------------
#
# Resolution order, first hit wins:
#   1. $LMCPS_CONFIG           an explicit path (tests, local development)
#   2. $LMCPS_HOME/config.json the copy fetched earlier this conversation
#   3. config-url.txt          a Dropbox shared link, fetched and then cached
#   4. config.json             sitting beside the skill, used as-is
#
# Deliberately no fall back to ~/.claude.json: it exists on a Claude Code
# development machine and never on claude.ai, so it would hide a broken config
# exactly where nobody is testing.

CONFIG_CACHE = LMCPS_HOME / "config.json"


def config_url():
    f = SKILL_DIR / "config-url.txt"
    if not f.is_file():
        return None
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return None


def fetch_config(url):
    """Fetch the shared link. Dropbox serves these through a CDN that will
    happily hand back a config you edited ten minutes ago, so force a miss."""
    parts = urllib.parse.urlsplit(url)
    q = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    q = [(k, v) for k, v in q if k not in ("dl", "_")]
    q += [("dl", "1"), ("_", str(int(time.time())))]
    busted = urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(q)))
    try:
        with urllib.request.urlopen(busted, timeout=30) as r:
            return r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        die(f"config link returned HTTP {e.code}. Re-share the file and rebuild the skill.")
    except OSError as e:
        die(f"cannot reach the config link ({e}). Check sandbox network egress for dropbox.com.")


def parse_config(text, source):
    try:
        cfg = json.loads(text)
    except json.JSONDecodeError as e:
        die(f"config at {source} is not valid JSON: {e}")
    servers = cfg.get("mcpServers")
    if not isinstance(servers, dict):
        die(f"config at {source} has no `mcpServers` object")
    return servers


def load_servers(force_refresh=False):
    explicit = os.environ.get("LMCPS_CONFIG")
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            die(f"$LMCPS_CONFIG points at {p}, which does not exist")
        return parse_config(p.read_text(encoding="utf-8"), p)

    if CONFIG_CACHE.is_file() and not force_refresh:
        return parse_config(CONFIG_CACHE.read_text(encoding="utf-8"), CONFIG_CACHE)

    url = config_url()
    if url:
        text = fetch_config(url)
        servers = parse_config(text, url)  # validate before caching
        LMCPS_HOME.mkdir(parents=True, exist_ok=True)
        CONFIG_CACHE.write_text(text, encoding="utf-8")
        return servers

    local = SKILL_DIR / "config.json"
    if local.is_file():
        return parse_config(local.read_text(encoding="utf-8"), local)

    die("no config. Expected config-url.txt or config.json beside the skill, "
        "or $LMCPS_CONFIG. See SKILL.md.")


def pick(servers, name):
    if name not in servers:
        known = ", ".join(sorted(servers)) or "(none)"
        die(f"unknown server '{name}'. Configured: {known}")
    return servers[name]


# --- transports -------------------------------------------------------------


def rpc(method, _id=None, **params):
    m = {"jsonrpc": "2.0", "method": method, "params": params}
    if _id is not None:
        m["id"] = _id
    return m


def call_http(name, cfg, method, params):
    url = expand(cfg["url"], os.environ)
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    # Whatever the config asks for, verbatim. The header name is the whole point
    # of this path: a connector could not have sent `tomtom-api-key` at all.
    for k, v in (cfg.get("headers") or {}).items():
        headers[k] = expand(v, os.environ)
    body = json.dumps(rpc(method, "lmcps-1", **params)).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        raw = urllib.request.urlopen(req, timeout=60).read().decode()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        if e.code in (401, 403):
            die(f"{e.code} from '{name}': the server rejected the configured "
                f"headers. {detail}")
        die(f"HTTP {e.code} from '{name}': {detail}")
    except OSError as e:
        die(f"cannot reach '{name}' at {url}: {e}")
    # Plain JSON, or SSE-framed as one or more `data:` lines.
    if raw.lstrip().startswith("data:") or "\ndata:" in raw:
        raw = "".join(l[len("data:"):].strip() for l in raw.splitlines()
                      if l.startswith("data:"))
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        die(f"'{name}' returned a non-JSON body: {raw[:300]}")


def call_stdio(name, cfg, method, params):
    env = dict(os.environ)
    for k, v in (cfg.get("env") or {}).items():
        env[k] = expand(str(v), os.environ)
    command = expand(cfg["command"], os.environ)
    # Resolving ourselves only sharpens the error on Linux, but it is what finds
    # `npx.cmd` on Windows, so one config exercises both.
    resolved = shutil.which(command, path=env.get("PATH"))
    if resolved is None:
        die(f"'{name}': command not found: {command}")
    cmd = [resolved] + [expand(a, os.environ) for a in cfg.get("args", [])]

    # A server that fails to start says why on stderr. Discard it and every such
    # failure becomes the same unhelpful "exited before responding to initialize".
    errf = tempfile.TemporaryFile()
    work = LMCPS_HOME / "work"
    work.mkdir(parents=True, exist_ok=True)
    try:
        # MCP stdio is UTF-8 by specification. `text=True` alone would decode
        # with the locale encoding, silently mangling non-ASCII rather than failing.
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=errf, env=env, cwd=str(work),
                             text=True, encoding="utf-8", errors="replace",
                             bufsize=1)
    except OSError as e:
        # `which` already proved it exists, so this is unrunnable, not absent.
        die(f"'{name}': cannot execute {cmd[0]}: {e}")

    def stderr_tail():
        try:
            errf.seek(0)
            tail = errf.read().decode(errors="replace").strip().splitlines()[-8:]
            return "\n  " + "\n  ".join(tail) if tail else ""
        except Exception:
            return ""

    def send(obj):
        try:
            p.stdin.write(json.dumps(obj) + "\n")
            p.stdin.flush()
        except (BrokenPipeError, ValueError):
            die(f"'{name}' closed its input before the exchange finished."
                + stderr_tail())

    def await_id(want):
        """Read until our response arrives, answering the server's own requests
        on the way. Skipping one is a real hang: a server blocked on `roots/list`
        waits for a reply that never comes, and we sit there until the timeout."""
        for line in p.stdout:  # newline-delimited JSON
            line = line.strip()
            if not line.startswith("{"):  # log noise, as the SDK's ReadBuffer does
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == want:
                return msg
            if "method" in msg and "id" in msg:
                send(respond_to(msg))
        return None

    send(rpc("initialize", "lmcps-init", protocolVersion=PROTOCOL_VERSION,
             capabilities={}, clientInfo=CLIENT_INFO))
    init = await_id("lmcps-init")
    if init is None:
        die(f"'{name}' exited before responding to initialize." + stderr_tail())
    LAST_INIT.update(init.get("result") or {})
    send(rpc("notifications/initialized"))
    send(rpc(method, "lmcps-call", **params))
    resp = await_id("lmcps-call")
    try:
        p.stdin.close()
        p.wait(timeout=5)
    except Exception:
        p.kill()
    if resp is None:
        die(f"'{name}' gave no response to {method}." + stderr_tail())
    return resp


def respond_to(req):
    """Answer a server->client request. We declare no capabilities, so a
    well-behaved server should not ask; some do anyway, and "method not found"
    unblocks them. roots/list gets an empty list, which is true and cheaper than
    making the server handle an error."""
    if req.get("method") == "roots/list":
        return {"jsonrpc": "2.0", "id": req["id"], "result": {"roots": []}}
    return {"jsonrpc": "2.0", "id": req["id"],
            "error": {"code": -32601,
                      "message": f"lmcps does not implement {req.get('method')}"}}


def call(name, cfg, method, params):
    ttype = cfg.get("type", "stdio")
    if ttype in ("http", "streamable-http"):
        resp = call_http(name, cfg, method, params)
    elif ttype == "stdio":
        resp = call_stdio(name, cfg, method, params)
    else:
        die(f"'{name}': transport '{ttype}' is not supported (stdio and "
            f"http/streamable-http only)")
    if "error" in resp:
        e = resp["error"]
        die(f"'{name}' returned error {e.get('code')}: {e.get('message')}")
    return resp.get("result", {})


# --- verbs ------------------------------------------------------------------


def list_tools(name, cfg, refresh=False):
    """Live tools/list plus what the server said about itself at startup, cached
    for the rest of the conversation. No durable catalog to keep in sync: the
    sandbox disk dies with the conversation, so the cache expires on its own."""
    cache = LMCPS_HOME / "tools" / f"{name}.json"
    if cache.is_file() and not refresh:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    # The call path skips the handshake on HTTP, which would report a server as
    # saying nothing about itself when it was never asked. One extra POST, on a
    # cache miss only; a server that refuses a bare initialize still lists fine.
    if cfg.get("type") in ("http", "streamable-http"):
        try:
            LAST_INIT.update(call_http(name, cfg, "initialize", {
                "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                "clientInfo": CLIENT_INFO}).get("result") or {})
        except SystemExit:
            pass

    entry = {"tools": call(name, cfg, "tools/list", {}).get("tools", []),
             "instructions": LAST_INIT.get("instructions"),
             "serverInfo": LAST_INIT.get("serverInfo")}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(entry), encoding="utf-8")
    return entry


def first_line(text):
    return (text or "").strip().splitlines()[0] if (text or "").strip() else ""


def cmd_servers(args):
    servers = load_servers()
    if not servers:
        print("No servers configured.")
        return
    width = max(len(n) for n in servers)
    for name in sorted(servers):
        cfg = servers[name]
        ttype = cfg.get("type", "stdio")
        desc = first_line(cfg.get("description")) or \
            f"(no description -- run `lmcps tools {name}`)"
        print(f"{name.ljust(width)}  {ttype.ljust(6)}  {desc}")
    print(f"\n`lmcps tools <server>` for a tool list, "
          f"`lmcps call <server> <tool> '{{...}}'` to invoke one.")


def cmd_tools(args):
    servers = load_servers()
    cfg = pick(servers, args.server)
    entry = list_tools(args.server, cfg, refresh=args.refresh)
    tools = entry["tools"]

    if args.schema:
        match = next((t for t in tools if t.get("name") == args.schema), None)
        if match is None:
            known = ", ".join(t.get("name", "") for t in tools) or "(none)"
            die(f"'{args.server}' has no tool '{args.schema}'. Tools: {known}")
        print(json.dumps(match, indent=2))
        return

    # Optional in the protocol; plenty of servers omit both.
    title = (entry.get("serverInfo") or {}).get("title")
    if title:
        print(f"{title}\n")
    if entry.get("instructions"):
        print(entry["instructions"].strip() + "\n")

    if not tools:
        print(f"'{args.server}' exposes no tools.")
        return
    width = max(len(t.get("name", "")) for t in tools)
    for t in tools:
        print(f"{t.get('name', '').ljust(width)}  {first_line(t.get('description'))}")
    print(f"\n`lmcps tools {args.server} --schema <tool>` for a tool's full input schema.")


def cmd_describe(args):
    """Gather what a server says about itself, for the caller to write the
    `description` from. It offers no generated line on purpose: every mechanical
    candidate looked pasteable while answering the wrong question, and so got
    pasted. Reducing this to one useful sentence is a judgement call."""
    servers = load_servers()
    cfg = pick(servers, args.server)
    entry = list_tools(args.server, cfg, refresh=args.refresh)
    info = entry.get("serverInfo") or {}
    instructions = entry.get("instructions")

    print(f"{args.server} -- {info.get('title') or info.get('name') or 'no serverInfo'}, "
          f"{len(entry['tools'])} tools\n")
    for t in entry["tools"]:
        print(f"  {t.get('name', '')}: {first_line(t.get('description'))}")
    if instructions:
        print("\nThe server's own instructions:\n")
        print(instructions.strip())

    print(f"\n{'-' * 70}\nNow write this server's `description` into its block in mcp.json:\n")
    print('  "description": "..."\n')
    print("One line, in the words someone would use when they need this server --\n"
          "the subjects it covers, not the tools it exposes. `lmcps servers` prints\n"
          "it at the top of every conversation and nothing else is read, so it is\n"
          "the whole of what decides whether this server ever gets picked.")


def cmd_call(args):
    servers = load_servers()
    cfg = pick(servers, args.server)
    try:
        arguments = json.loads(args.arguments) if args.arguments else {}
    except json.JSONDecodeError as e:
        die(f"arguments are not valid JSON: {e}")
    result = call(args.server, cfg, "tools/call",
                  {"name": args.tool, "arguments": arguments})

    text = "\n".join(c.get("text", "") for c in result.get("content", [])
                     if c.get("type") == "text")
    if not text and "structuredContent" in result:
        text = json.dumps(result["structuredContent"], indent=2)
    if not text:
        text = json.dumps(result, indent=2)

    # An in-band failure arrives as a perfectly good result, so without this it
    # would print to stdout and exit 0 -- indistinguishable from success.
    if result.get("isError"):
        print(f"lmcps: '{args.server}' tool '{args.tool}' failed:\n{text}",
              file=sys.stderr)
        sys.exit(1)
    print(text)


def cmd_refresh(args):
    for stale in LMCPS_HOME.glob("tools/*.json"):
        stale.unlink()
    servers = load_servers(force_refresh=True)
    print(f"Config refreshed: {len(servers)} server(s) -- "
          f"{', '.join(sorted(servers)) or '(none)'}")


def watchdog(seconds):
    """Portable deadline. signal.alarm is POSIX-only, and this runs on a Windows
    development machine as well as in the Linux sandbox."""
    def fire():
        print(f"lmcps: timed out after {seconds}s", file=sys.stderr)
        os._exit(1)
    t = threading.Timer(seconds, fire)
    t.daemon = True
    t.start()


def main():
    ap = argparse.ArgumentParser(prog="lmcps", description=__doc__.splitlines()[0])
    ap.add_argument("--timeout", type=int, default=120,
                    help="seconds before giving up (default: 120)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("servers", help="configured servers; reads the config, spawns nothing"
                   ).set_defaults(fn=cmd_servers)

    p = sub.add_parser("tools", help="a server's tools")
    p.add_argument("server")
    p.add_argument("--schema", metavar="TOOL", help="full input schema for one tool")
    p.add_argument("--refresh", action="store_true", help="re-enumerate, ignoring the cache")
    p.set_defaults(fn=cmd_tools)

    p = sub.add_parser("call", help="invoke a tool")
    p.add_argument("server")
    p.add_argument("tool")
    p.add_argument("arguments", nargs="?", default="", help="JSON object of arguments")
    p.set_defaults(fn=cmd_call)

    p = sub.add_parser("describe", help="a paste-ready `description` for a newly added server")
    p.add_argument("server")
    p.add_argument("--refresh", action="store_true", help="re-enumerate, ignoring the cache")
    p.set_defaults(fn=cmd_describe)

    sub.add_parser("refresh", help="re-fetch the config and drop cached tool lists"
                   ).set_defaults(fn=cmd_refresh)

    # Servers write real prose in their tool descriptions -- TomTom's contain
    # arrows and dashes. Whatever locale the sandbox hands us, that must print
    # rather than crash the command that was about to do useful work.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")

    args = ap.parse_args()
    watchdog(args.timeout)
    args.fn(args)


if __name__ == "__main__":
    main()
