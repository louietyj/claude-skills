#!/usr/bin/env python3
"""Client for the Retell dialer's consult loop.

A Retell voice agent runs the call at conversational speed. When it hits
something outside its brief it calls `consult_supervisor`, which POSTs to a
Cloudflare Durable Object that holds the HTTP request open. Claude polls that
queue, answers, and the held request is released -- the agent speaks the answer.

Three things here are structural rather than advisory, because each one cost
live calls when it was left to the caller:

  * `answer` re-polls in the same process -- two commands invite prose between
    them, and that prose is dead air on a stranger's phone.
  * Requests are built by urllib, never curl. `curl -G` folds the body into the
    query string and sends none, which hung three calls on an empty req.json().
  * `poll` races the queue against call status, the only way to tell "still
    talking" from "hung up twenty seconds ago".
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(os.path.dirname(HERE), "config.json")
STAGED = os.path.join(os.path.dirname(HERE), "state", "staged-call.json")

RETELL = "https://api.retellai.com"

# Nests inside the claude.ai sandbox's 300s hard kill, which discards all output
# when it fires -- an overrun loses the question, not just the tail of it.
POLL_BUDGET = 200
POLL_WINDOW = 20          # per HTTP request; also the call-status check interval
STATUS_COST = 6           # headroom for the Retell round trip after each window
DYNAMIC_VARS = ("opening", "brief", "call_purpose")


def die(msg, code=1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def load_config():
    if not os.path.exists(CONFIG):
        die(f"no config at {CONFIG} -- copy config.example.json and fill it in")
    with open(CONFIG, encoding="utf-8") as fh:
        cfg = json.load(fh)
    for key in ("retell_api_key", "agent_id", "worker_url", "consult_token"):
        if not cfg.get(key):
            die(f"config.json is missing {key}")
    cfg["worker_url"] = cfg["worker_url"].rstrip("/")
    return cfg


def request(url, *, method="GET", body=None, headers=None, timeout=30):
    """Returns (status, parsed_or_text). Never raises on an HTTP error status --
    a 4xx that says what was wrong is more useful than a traceback mid-call."""
    data = json.dumps(body).encode() if body is not None else None
    # Cloudflare answers urllib's default User-Agent with a 403 (error 1010).
    hdrs = {"content-type": "application/json", "user-agent": "retell-dialer/1.0"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw, status = resp.read().decode(), resp.status
    except urllib.error.HTTPError as e:
        raw, status = e.read().decode(), e.code
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


def retell(cfg, path, *, method="GET", body=None, timeout=30):
    return request(
        f"{RETELL}{path}", method=method, body=body, timeout=timeout,
        headers={"Authorization": f"Bearer {cfg['retell_api_key']}"},
    )


def queue(cfg, path, *, method="GET", body=None, timeout=30, params=None):
    query = {"token": cfg["consult_token"], **(params or {})}
    url = f"{cfg['worker_url']}{path}?{urllib.parse.urlencode(query)}"
    return request(url, method=method, body=body, timeout=timeout)


def emit(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


# --- call status -------------------------------------------------------------

def call_status(cfg, call_id):
    """'ongoing' | 'ended' | 'error' | 'registered' | None if unknown."""
    if not call_id:
        return None
    status, body = retell(cfg, f"/v2/get-call/{call_id}", timeout=15)
    return body.get("call_status") if status == 200 and isinstance(body, dict) else None


def find_ongoing_call(cfg):
    """The first poll happens before any consult has revealed an id."""
    status, body = retell(
        cfg, "/v2/list-calls", method="POST", timeout=15,
        body={"filter_criteria": {"call_status": ["ongoing"]}, "limit": 10},
    )
    if status != 200 or not isinstance(body, list) or not body:
        return None
    newest = max(body, key=lambda c: c.get("start_timestamp") or 0)
    return newest.get("call_id")


# --- commands ----------------------------------------------------------------

def check_vars(variables):
    """An unset variable renders as a literal `{{brief}}`, and since the whole
    prompt lives in one, a typo means an agent on a live call with no brief."""
    missing = [k for k in DYNAMIC_VARS if not str(variables.get(k, "")).strip()]
    if missing:
        die("refusing to dispatch -- these render as literal {{...}} in the "
            f"prompt: {', '.join(missing)}")


def read_variables(args):
    brief = args.brief
    if args.brief_file:
        with open(args.brief_file, encoding="utf-8") as fh:
            brief = fh.read().strip()
    return {"opening": args.opening, "brief": brief, "call_purpose": args.purpose}


def create_call(cfg, variables, *, web, to=None):
    check_vars(variables)
    if web:
        path, body = "/v2/create-web-call", {"agent_id": cfg["agent_id"]}
    else:
        path = "/v2/create-phone-call"
        body = {"from_number": cfg["from_number"], "to_number": to,
                "override_agent_id": cfg["agent_id"]}
    body["retell_llm_dynamic_variables"] = variables
    return retell(cfg, path, method="POST", body=body)


def cmd_dispatch(cfg, args):
    if not args.to:
        die("--to is required; for a browser call use `stage` + `serve`")
    if not cfg.get("from_number"):
        die("no from_number in config -- buy a Retell number for PSTN, or use "
            "`stage` + `serve` to run the same loop as a browser call")
    status, resp = create_call(cfg, read_variables(args), web=False, to=args.to)
    if status not in (200, 201):
        die(f"dispatch failed ({status}): {json.dumps(resp)[:400]}")
    emit({"call_id": resp.get("call_id"), "call_status": resp.get("call_status"),
          "call_type": resp.get("call_type")})


def cmd_stage(cfg, args):
    """Park a brief for the launcher to dispatch when the browser is ready.

    A registered web call expires within about a minute, so a token minted up
    front loses the race against however long it takes someone to read the
    message and click. Staging inverts that: the call is created by the click,
    so it is always seconds old."""
    variables = read_variables(args)
    check_vars(variables)
    os.makedirs(os.path.dirname(STAGED), exist_ok=True)
    with open(STAGED, "w", encoding="utf-8") as fh:
        json.dump(variables, fh, indent=2, ensure_ascii=False)
    emit({"staged": os.path.basename(STAGED),
          "call_purpose": variables["call_purpose"],
          "next": f"open http://localhost:{args.port}/ and click Join call"})


def cmd_serve(cfg, args):
    """Launcher for browser calls. Serves the page and mints a call on demand.

    localhost specifically: getUserMedia needs a secure context, and file:// is
    not one."""
    import http.server

    root = os.path.join(os.path.dirname(HERE), "dev", "webcall")
    if not os.path.exists(os.path.join(root, "retell-sdk.js")):
        die(f"no bundled SDK at {root} -- run dev/webcall/build.sh")

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=root, **kw)

        def reply(self, obj, code=200):
            raw = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            if self.path != "/dispatch":
                return self.reply({"error": "not found"}, 404)
            if not os.path.exists(STAGED):
                return self.reply({"error": "nothing staged -- run `dialer stage`"}, 409)
            with open(STAGED, encoding="utf-8") as fh:
                variables = json.load(fh)
            status, resp = create_call(cfg, variables, web=True)
            if status not in (200, 201):
                return self.reply({"error": "create-web-call failed",
                                   "status": status, "body": resp}, 502)
            print(f"dispatched {resp.get('call_id')}", flush=True)
            self.reply({"call_id": resp.get("call_id"),
                        "access_token": resp.get("access_token")})

    class Server(http.server.ThreadingHTTPServer):
        # On Windows the inherited allow_reuse_address lets a second bind to a
        # live port succeed silently, while the first process goes on answering.
        # That presents as code changes having no effect -- and here, as a stale
        # launcher serving a page that no longer matches the CLI.
        allow_reuse_address = False

    print(f"serving {root} on http://localhost:{args.port}/", flush=True)
    try:
        Server(("127.0.0.1", args.port), Handler).serve_forever()
    except OSError as e:
        die(f"port {args.port} is already in use ({e}) -- stop the other launcher")


def cmd_pending(cfg, args):
    """Non-blocking peek. Worth doing before committing to a blocking poll."""
    _, body = queue(cfg, "/pending", timeout=15)
    emit(body)


def poll_loop(cfg, call_id, budget):
    """Returns a dict: a consult, {'call_ended': ...}, or {'pending': None}."""
    deadline = time.monotonic() + budget
    # Not fatal if absent: a call that has not registered yet just means the
    # queue is the only signal for now.
    call_id = call_id or find_ongoing_call(cfg)

    while True:
        # `budget` is wall-clock, not a sum of sleeps, so the status check after
        # each window is paid for inside it.
        remaining = deadline - time.monotonic() - STATUS_COST
        if remaining <= 0:
            return {"pending": None, "call_id": call_id,
                    "note": "window expired, nothing pending -- poll again"}

        window = int(min(POLL_WINDOW, remaining)) or 1
        status, body = queue(cfg, "/poll", params={"wait": window}, timeout=window + 10)
        if status == 200 and isinstance(body, dict) and body.get("question"):
            return body
        if status not in (200, 0):
            return {"error": f"queue returned {status}", "body": body}

        state = call_status(cfg, call_id)
        if state in ("ended", "error"):
            return ended_result(cfg, call_id, state)
        if call_id is None:
            call_id = find_ongoing_call(cfg)


def cmd_watch(cfg, args):
    """Supervise on two channels: the consult queue and the live transcript.

    `poll` only wakes when the agent asks, which leaves the other half of
    supervision unserved -- the agent mishandling something it does not know it
    is mishandling. Retell's monitor websocket is consumed inside this call and
    never surfaces as a stream; it just gives the command something to return
    on besides a consult. Acting on what comes back is `steer`, which needs
    nobody waiting on the far end."""
    import threading

    import ws

    call_id = args.call_id or find_ongoing_call(cfg)
    if not call_id:
        die("no ongoing call found -- pass --call-id")

    state = {"turns": [], "by_id": {}, "ended": None, "consult": None,
             "error": None, "types": set()}
    stop = threading.Event()

    def run_monitor():
        # 4004 is "call not live yet", not "call over": a call that is still
        # ringing closes with it, and the stream only exists once the callee
        # picks up. Treating it as the end reports a hangup during the ring.
        attempts = 0
        while not stop.is_set():
            try:
                sock = ws.monitor(cfg["retell_api_key"], call_id, timeout=20)
            except Exception as e:                  # noqa: BLE001 - reported, not raised
                state["error"] = f"monitor: {type(e).__name__}: {e}"
                return
            code = pump(sock)
            if code != 4004 or attempts >= 8 or stop.is_set():
                if code is not None and code != 4004:
                    state["ended"] = state["ended"] or f"ws {code}"
                return
            attempts += 1
            time.sleep(min(4.0, 0.5 * 2 ** attempts))

    def pump(sock):
        """Drain one connection. Returns its close code, or None if we stopped."""
        try:
            while not stop.is_set():
                raw = sock.recv(timeout=2)
                if raw is None:
                    if sock.close_code is not None:
                        return sock.close_code
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                kind = msg.get("type", "?")
                state["types"].add(kind)
                if kind == "call_ended":
                    state["ended"] = "call_ended"
                    return
                # `transcript_snapshot` carries the whole conversation;
                # `transcript_updated` carries only the turns that changed,
                # including a turn already sent, growing word by word as it is
                # spoken. So merge by id rather than replacing -- and never
                # append, which would emit the same sentence a dozen times.
                incoming = msg.get("transcripts")
                if not isinstance(incoming, list):
                    continue
                if kind == "transcript_snapshot":
                    state["by_id"].clear()
                for turn in incoming:
                    state["by_id"][turn.get("id") or len(state["by_id"])] = turn
                state["turns"] = list(state["by_id"].values())
        finally:
            sock.close()

    def run_poll():
        _, body = queue(cfg, "/poll", params={"wait": args.budget}, timeout=args.budget + 15)
        if isinstance(body, dict) and body.get("question"):
            state["consult"] = body

    threads = [threading.Thread(target=t, daemon=True) for t in (run_monitor, run_poll)]
    for t in threads:
        t.start()

    deadline = time.monotonic() + args.budget
    seen = args.since
    last_report = time.monotonic()
    while time.monotonic() < deadline:
        if state["consult"]:
            stop.set()
            return emit({"event": "consult", **state["consult"],
                         "live_turns": len(state["turns"])})
        if state["ended"]:
            stop.set()
            return emit({"event": "call_ended",
                         **ended_result(cfg, call_id, state["ended"])})
        grown = len(state["turns"]) > seen
        if grown and time.monotonic() - last_report >= args.interval:
            stop.set()
            return emit({"event": "transcript", "call_id": call_id,
                         "turns_total": len(state["turns"]),
                         "new": render_turns(state["turns"][seen:]),
                         "hint": "steer with `dialer steer`, or `watch --since N` to continue"})
        time.sleep(0.4)

    stop.set()
    emit({"event": "idle", "call_id": call_id, "turns_total": len(state["turns"]),
          "monitor_error": state["error"], "message_types": sorted(state["types"])})


def ended_result(cfg, call_id, why):
    """A hangup and the record of it, in one return. Whoever is supervising
    wants the transcript every time a call ends, so charging them a round trip
    for it buys nothing."""
    out = {"call_ended": True, "call_id": call_id, "call_status": why}
    rec = call_record(cfg, call_id, settle=10)
    if not rec:
        return {**out, "transcript": None, "note": "nothing retained yet"}
    out["disconnection_reason"] = rec["disconnection_reason"]
    out["duration_ms"] = rec["duration_ms"]
    out["source"] = rec["source"]
    content = rec["content"]
    out["transcript"] = render_turns(content) if isinstance(content, list) else content
    return out


def cmd_poll(cfg, args):
    emit(poll_loop(cfg, args.call_id, args.budget))


def cmd_answer(cfg, args):
    """Answer, then resume polling in the same process -- see the module
    docstring for why these are not two commands."""
    text = args.text
    if args.text_file:
        with open(args.text_file, encoding="utf-8") as fh:
            text = fh.read().strip()
    if not text.strip():
        die("refusing to send an empty answer")

    status, body = queue(cfg, "/answer", method="POST", timeout=25,
                         body={"call_id": args.call_id, "text": text})
    answered = {"answer_status": status, "answer_result": body}
    if status != 200:
        # Do not fall through to polling: the agent is still holding, and the
        # operator needs to see the failure now, not after a 200s block.
        emit({**answered, "hint": "the consult is still held -- check `pending` "
                                  "for the live call_id and resend"})
        sys.exit(1)

    print(json.dumps(answered, ensure_ascii=False), file=sys.stderr)
    emit(poll_loop(cfg, args.call_id, args.budget))


def cmd_steer(cfg, args):
    """Push-side: inject context without waiting to be asked. Lands in the
    transcript as role `injected` -- not spoken by either party."""
    body = {"call_control": {"additional_context": args.text,
                             "trigger_response": args.speak_now}}
    status, resp = retell(cfg, f"/v2/update-live-call/{args.call_id}",
                          method="PATCH", body=body)
    emit({"status": status, "result": resp})


def render_turns(turns):
    """Flatten to readable lines, dropping the per-word timings each turn
    carries -- they outweigh the text about ten to one."""
    lines = []
    for t in turns:
        role = t.get("role")
        if role == "tool_call_invocation":
            lines.append(f"  [TOOL {t.get('name')}] {t.get('arguments', '')}")
        elif role == "tool_call_result":
            lines.append(f"  [RESULT] {t.get('content', '')}")
        else:
            lines.append(f"{role}: {(t.get('content') or '').strip()}")
    return lines


# Under `everything_except_pii` Retell retains only the scrubbed variants, so
# the unscrubbed keys are absent rather than empty. Take whichever exists.
TRANSCRIPT_KEYS = ("transcript_with_tool_calls", "scrubbed_transcript_with_tool_calls",
                   "transcript", "scrubbed_transcript")


def call_record(cfg, call_id, settle=0):
    """The finished call, or None if nothing is retained yet.

    `settle` retries for a few seconds, because Retell finalises a transcript a
    moment after the call drops: a read issued the instant a hangup is detected
    comes back empty."""
    deadline = time.monotonic() + settle
    while True:
        status, body = retell(cfg, f"/v2/get-call/{call_id}", timeout=15)
        if status == 200:
            for key in TRANSCRIPT_KEYS:
                if body.get(key):
                    return {"source": key, "call_status": body.get("call_status"),
                            "duration_ms": body.get("duration_ms"),
                            "disconnection_reason": body.get("disconnection_reason"),
                            "content": body[key]}
        if time.monotonic() >= deadline:
            return None
        time.sleep(2)


def show_record(rec, raw=False):
    head = {k: v for k, v in rec.items() if k != "content"}
    if raw or not isinstance(rec["content"], list):
        emit({**head, "content": rec["content"]})
        return
    print(json.dumps(head, indent=2))
    print("\n".join(render_turns(rec["content"])))


def cmd_transcript(cfg, args):
    rec = call_record(cfg, args.call_id, settle=args.settle)
    if not rec:
        return emit({"note": "nothing retained yet",
                     "call_status": call_status(cfg, args.call_id)})
    show_record(rec, args.raw)


def cmd_call(cfg, args):
    """The call as Retell's webhook sent it, captured by the Worker.

    Distinct from `transcript`, which reads Retell's stored copy: this one
    predates any post-call PII scrub, so it is the only unredacted record when
    scrubbing is on."""
    status, body = queue(cfg, "/call", params={"call_id": args.call_id}, timeout=20)
    if status != 200:
        die(f"queue returned {status}: {json.dumps(body)[:300]}")
    if not body.get("events"):
        die(f"nothing captured for {args.call_id} -- is the agent's webhook_url set?")

    for event in ("call_analyzed", "call_ended"):
        call = (body["payloads"].get(event) or {}).get("call")
        if not call:
            continue
        turns = call.get("transcript_with_tool_calls") or call.get("transcript_object")
        head = {"source": f"webhook:{event}", "events": body["events"],
                "call_status": call.get("call_status"),
                "duration_ms": call.get("duration_ms"),
                "disconnection_reason": call.get("disconnection_reason")}
        if args.raw or not isinstance(turns, list):
            emit({**head, "transcript": call.get("transcript"), "turns": turns})
        else:
            print(json.dumps(head, indent=2))
            print("\n".join(render_turns(turns)))
        return
    emit(body)


def cmd_health(cfg, args):
    """Check every link before a call rather than during one -- a dead queue
    otherwise strands `consult_supervisor` with someone on hold."""
    checks, ok = [], True

    status, body = request(f"{cfg['worker_url']}/health", timeout=15)
    good = status == 200 and isinstance(body, dict) and body.get("ok")
    checks.append(("consult queue", good, f"{cfg['worker_url']} -> {status}"))
    ok &= bool(good)

    status, _ = queue(cfg, "/pending", timeout=15)
    checks.append(("queue token", status == 200, f"/pending -> {status}"))
    ok &= status == 200

    status, agent = retell(cfg, f"/get-agent/{cfg['agent_id']}", timeout=20)
    checks.append(("retell agent", status == 200,
                   agent.get("agent_name", "?") if status == 200 else f"-> {status}"))
    ok &= status == 200

    if status == 200:
        hook = agent.get("webhook_url") or ""
        checks.append(("agent webhook", hook.startswith(cfg["worker_url"]),
                       hook or "unset -- hangups will not punt a held consult"))

    have_number = bool(cfg.get("from_number"))
    checks.append(("from_number", have_number,
                   cfg.get("from_number") or "unset -- PSTN calls will be refused"))

    width = max(len(name) for name, _, _ in checks)
    for name, good, detail in checks:
        print(f"  [{'ok' if good else 'FAIL'}] {name.ljust(width)}  {detail}")
    if not ok:
        sys.exit(1)


def cmd_agent_pull(cfg, args):
    out = {}
    for name, path in (("agent", f"/get-agent/{cfg['agent_id']}"),
                       ("llm", f"/get-retell-llm/{cfg['llm_id']}")):
        status, body = retell(cfg, path)
        if status != 200:
            die(f"{name} pull returned {status}: {json.dumps(body)[:300]}")
        out[name] = body
    emit(out)


def main():
    # Transcripts carry whatever the agent said, and a brief written with an em
    # dash comes back through here. On a Windows console that is cp1252 by
    # default, which turns them into mojibake or raises mid-print.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(prog="dialer", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def brief_args(parser):
        parser.add_argument("--opening", required=True,
                            help="first line; carries the AI announcement")
        parser.add_argument("--purpose", required=True,
                            help="one phrase, for call screening")
        parser.add_argument("--brief")
        parser.add_argument("--brief-file")
        parser.add_argument("--port", type=int, default=8765)

    d = sub.add_parser("dispatch", help="place a PSTN call with a brief written for it")
    d.add_argument("--to", help="callee, E.164")
    brief_args(d)
    d.set_defaults(fn=cmd_dispatch)

    st = sub.add_parser("stage", help="park a brief for the browser launcher to dial")
    brief_args(st)
    st.set_defaults(fn=cmd_stage)

    sv = sub.add_parser("serve", help="browser-call launcher (dev/test)")
    sv.add_argument("--port", type=int, default=8765)
    sv.set_defaults(fn=cmd_serve)

    w = sub.add_parser("watch", help="block until a consult, new dialogue, or the call ends")
    w.add_argument("--call-id")
    w.add_argument("--budget", type=int, default=POLL_BUDGET)
    w.add_argument("--interval", type=int, default=15,
                   help="seconds of new dialogue to accumulate before returning")
    w.add_argument("--since", type=int, default=0,
                   help="turns already seen; report only what is new")
    w.set_defaults(fn=cmd_watch)

    p = sub.add_parser("poll", help="block until the agent consults, or the call ends")
    p.add_argument("--call-id")
    p.add_argument("--budget", type=int, default=POLL_BUDGET)
    p.set_defaults(fn=cmd_poll)

    a = sub.add_parser("answer", help="answer a consult and resume polling")
    a.add_argument("call_id")
    a.add_argument("text", nargs="?", default="")
    a.add_argument("--text-file")
    a.add_argument("--budget", type=int, default=POLL_BUDGET)
    a.set_defaults(fn=cmd_answer)

    s = sub.add_parser("steer", help="inject context mid-call, unprompted")
    s.add_argument("call_id")
    s.add_argument("text")
    s.add_argument("--speak-now", action="store_true",
                   help="speak immediately instead of waiting for their turn")
    s.set_defaults(fn=cmd_steer)

    sub.add_parser("pending", help="non-blocking peek at the queue").set_defaults(fn=cmd_pending)

    t = sub.add_parser("transcript", help="post-call transcript with tool calls woven in")
    t.add_argument("call_id")
    t.add_argument("--raw", action="store_true",
                   help="full objects including per-word timings (~10x larger)")
    t.add_argument("--settle", type=int, default=10,
                   help="seconds to retry while Retell finalises the transcript")
    t.set_defaults(fn=cmd_transcript)

    c = sub.add_parser("call", help="the call as Retell's webhook sent it, pre-scrub")
    c.add_argument("call_id")
    c.add_argument("--raw", action="store_true")
    c.set_defaults(fn=cmd_call)

    sub.add_parser("health", help="check queue, token, agent and number").set_defaults(fn=cmd_health)
    sub.add_parser("agent-pull", help="dump the live agent and LLM config").set_defaults(fn=cmd_agent_pull)

    args = ap.parse_args()
    args.fn(load_config(), args)


if __name__ == "__main__":
    main()
