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


def cmd_dispatch(cfg, args):
    variables = read_variables(args)
    check_vars(variables)
    if not args.web:
        if not args.to:
            die("--to is required for a PSTN call; --web needs no number")
        if not cfg.get("from_number"):
            die("no from_number in config -- buy a Retell number for PSTN, or "
                "use `dispatch --web` to run the same loop as a browser call")

    if args.web:
        path, body = "/v2/create-web-call", {"agent_id": cfg["agent_id"]}
    else:
        path = "/v2/create-phone-call"
        body = {"from_number": cfg["from_number"], "to_number": args.to,
                "override_agent_id": cfg["agent_id"]}
    body["retell_llm_dynamic_variables"] = variables

    status, resp = retell(cfg, path, method="POST", body=body)
    if status not in (200, 201):
        die(f"dispatch failed ({status}): {json.dumps(resp)[:400]}")

    out = {"call_id": resp.get("call_id"), "call_status": resp.get("call_status"),
           "call_type": resp.get("call_type")}
    if args.web:
        # In the fragment, so the token never reaches the dev server's log.
        out["join_url"] = f"http://localhost:{args.port}/#token={resp['access_token']}"
        out["note"] = "run `dialer serve` first, then open join_url"
    emit(out)


def cmd_serve(cfg, args):
    """Static server for the web-call launcher. localhost specifically:
    getUserMedia needs a secure context and file:// is not one."""
    import functools
    import http.server

    root = os.path.join(os.path.dirname(HERE), "dev", "webcall")
    if not os.path.exists(os.path.join(root, "retell-sdk.js")):
        die(f"no bundled SDK at {root} -- run dev/webcall/build.sh")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=root)
    print(f"serving {root} on http://localhost:{args.port}/", flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler).serve_forever()


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
            return {"call_ended": True, "call_id": call_id, "call_status": state}
        if call_id is None:
            call_id = find_ongoing_call(cfg)


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


def cmd_transcript(cfg, args):
    status, body = retell(cfg, f"/v2/get-call/{args.call_id}")
    if status != 200:
        die(f"get-call returned {status}: {json.dumps(body)[:300]}")
    # Under `everything_except_pii` Retell retains only the scrubbed variants,
    # so the unscrubbed keys are absent rather than empty. Take what exists.
    for key in ("transcript_with_tool_calls", "scrubbed_transcript_with_tool_calls",
                "transcript", "scrubbed_transcript"):
        content = body.get(key)
        if not content:
            continue
        head = {"source": key, "call_status": body.get("call_status"),
                "duration_ms": body.get("duration_ms"),
                "disconnection_reason": body.get("disconnection_reason")}
        if args.raw or not isinstance(content, list):
            emit({**head, "content": content})
        else:
            print(json.dumps(head, indent=2))
            print("\n".join(render_turns(content)))
        return
    emit({"note": "no transcript retained", "call_status": body.get("call_status"),
          "keys": sorted(body.keys())})


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

    d = sub.add_parser("dispatch", help="start a call with a brief written for it")
    d.add_argument("--to", help="callee, E.164")
    d.add_argument("--web", action="store_true",
                   help="browser call instead of PSTN -- same consult loop, no number needed")
    d.add_argument("--opening", required=True, help="first line; carries AI disclosure and recording consent")
    d.add_argument("--purpose", required=True, help="one phrase, for call screening")
    d.add_argument("--brief")
    d.add_argument("--brief-file")
    d.add_argument("--port", type=int, default=8765, help="dev launcher port, for --web")
    d.set_defaults(fn=cmd_dispatch)

    sv = sub.add_parser("serve", help="dev static server for the --web launcher")
    sv.add_argument("--port", type=int, default=8765)
    sv.set_defaults(fn=cmd_serve)

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
    t.set_defaults(fn=cmd_transcript)

    c = sub.add_parser("call", help="the call as Retell's webhook sent it, pre-scrub")
    c.add_argument("call_id")
    c.add_argument("--raw", action="store_true")
    c.set_defaults(fn=cmd_call)

    sub.add_parser("agent-pull", help="dump the live agent and LLM config").set_defaults(fn=cmd_agent_pull)

    args = ap.parse_args()
    args.fn(load_config(), args)


if __name__ == "__main__":
    main()
