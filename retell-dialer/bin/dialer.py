#!/usr/bin/env python3
"""Client for the Retell dialer's consult loop.

A Retell voice agent runs the call at conversational speed. When it hits
something outside its brief it calls `consult_supervisor`, which POSTs to a
Cloudflare Durable Object that holds the HTTP request open. Claude polls that
queue, answers, and the held request is released -- the agent speaks the answer.

Three things here are structural rather than advisory, because each one cost
live calls when it was left to the caller:

  * `answer` and `steer` resume watching in the same process -- two commands
    invite prose between them, and that prose is dead air on a stranger's
    phone.
  * Requests are built by urllib, never curl. `curl -G` folds the body into the
    query string and sends none, which hung three calls on an empty req.json().
  * Both `watch` and `poll` check Retell's call status themselves. Neither the
    queue nor the monitor socket can tell "still talking" from "hung up twenty
    seconds ago" — the queue never fires again, and the socket only reports an
    end it was present for.
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
JOURNAL = os.path.join(os.path.dirname(HERE), "state", "journal.jsonl")

RETELL = "https://api.retellai.com"

# Nests inside the claude.ai sandbox's 300s hard kill, which discards all output
# when it fires -- an overrun loses the question, not just the tail of it.
POLL_BUDGET = 200
WATCH_INTERVAL = 15       # batching floor; SKILL.md says when to raise it
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


def journal(command, **fields):
    """Record the intent before the request, flushed, so a process killed
    mid-flight still leaves a trace. claude.ai drops an interrupted tool call
    from the transcript; the sandbox disk outlives the turn, so this is the
    only record that survives one. Never allowed to break a live call."""
    try:
        os.makedirs(os.path.dirname(JOURNAL), exist_ok=True)
        with open(JOURNAL, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                 "command": command, **fields},
                                ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except (OSError, ValueError):
        pass


def cmd_journal(cfg, args):
    """What this conversation actually did, transcript or no transcript."""
    if not os.path.exists(JOURNAL):
        return emit({"entries": [], "note": "nothing recorded yet"})
    with open(JOURNAL, encoding="utf-8") as fh:
        lines = [l for l in fh.read().splitlines() if l.strip()]
    for line in lines[-args.limit:]:
        print(line)


def merge_turns(by_id, msg):
    """Apply one monitor frame to the turn map. True if it carried turns.

    `transcript_snapshot` is the whole conversation; `transcript_updated` is
    only what changed, including a turn already sent, growing word by word as
    it is spoken. Replacing loses everything; appending emits the same sentence
    a dozen times.
    """
    incoming = msg.get("transcripts")
    if not isinstance(incoming, list):
        return False
    if msg.get("type") == "transcript_snapshot":
        by_id.clear()
    for turn in incoming:
        by_id[turn.get("id") or len(by_id)] = turn
    return True


# --- call status -------------------------------------------------------------

def call_status(cfg, call_id):
    """'ongoing' | 'ended' | 'error' | 'registered' | None if unknown."""
    if not call_id:
        return None
    status, body = retell(cfg, f"/v2/get-call/{call_id}", timeout=15)
    return body.get("call_status") if status == 200 and isinstance(body, dict) else None


LIVE_STATUSES = ("registered", "ongoing")


def live_calls(cfg):
    """Every call on the account not yet over, newest first; None if Retell
    could not be asked.

    Filtered here rather than by Retell: its status filter has no "registered",
    which is what a call not yet connected reports -- and a call still ringing
    is exactly what an interrupted dispatch leaves behind."""
    status, body = retell(cfg, "/v3/list-calls", method="POST", timeout=15,
                          body={"sort_order": "descending", "limit": 50})
    if status != 200 or not isinstance(body, dict) or not isinstance(body.get("items"), list):
        return None
    return [c for c in body["items"] if c.get("call_status") in LIVE_STATUSES]


def find_ongoing_call(cfg):
    """The first poll happens before any consult has revealed an id."""
    calls = live_calls(cfg)
    return calls[0].get("call_id") if calls else None


def describe_call(c):
    """Enough of a live call to recognise it as the one you meant to place."""
    ts = c.get("start_timestamp")
    variables = c.get("retell_llm_dynamic_variables") or {}
    return {"call_id": c.get("call_id"), "call_status": c.get("call_status"),
            "to": c.get("to_number"),
            "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts / 1000)) if ts else None,
            "purpose": variables.get("call_purpose"),
            "opening": variables.get("opening"),
            "brief": variables.get("brief")}


# --- commands ----------------------------------------------------------------

def check_vars(variables):
    """An unset variable renders as a literal `{{brief}}`, and since the whole
    prompt lives in one, a typo means an agent on a live call with no brief."""
    # `str(None)` is "None" -- truthy, and exactly what `--brief-file` omitted
    # leaves behind, so a bare None has to be caught before the strip().
    missing = [k for k in DYNAMIC_VARS
               if not str(variables.get(k) or "").strip()]
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
        path, body = "/v3/create-web-call", {"agent_id": cfg["agent_id"]}
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

    # An interrupted turn has its tool calls stripped from the transcript, so a
    # dispatch that already happened can look like one that never did -- and
    # the cost of believing that is ringing a stranger twice. Retell is the
    # record, not the transcript. But a live call may equally belong to another
    # conversation, so show each one in full and let the caller tell them apart
    # rather than guessing which it is.
    if not args.force:
        live = live_calls(cfg)
        if live is None:
            die("could not list live calls to check that none is yours -- retry, "
                "or pass --force")
        if live:
            emit({"refused": "calls are already live on this account",
                  "live_calls": [describe_call(c) for c in live],
                  "next": ("If one of these is the call you were placing (an interrupted "
                           "turn loses the tool call, not the call), watch it: "
                           "`dialer watch CALL_ID`. If none is, it belongs to another "
                           "conversation: leave it alone and dispatch again with --force.")})
            sys.exit(1)
    journal("dispatch", to=args.to, purpose=args.purpose)
    status, resp = create_call(cfg, read_variables(args), web=False, to=args.to)
    journal("dispatch", to=args.to, status=status, call_id=(resp or {}).get("call_id"))
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
                        "access_token": resp.get("access_token"),
                        "transport": resp.get("transport"),
                        "ice_servers": resp.get("ice_servers")})

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
        params = {"wait": window, **({"call_id": call_id} if call_id else {})}
        status, body = queue(cfg, "/poll", params=params, timeout=window + 10)
        if status == 200 and isinstance(body, dict) and body.get("question"):
            return body
        if status not in (200, 0):
            return {"error": f"queue returned {status}", "body": body}

        state = call_status(cfg, call_id)
        if state in ("ended", "error"):
            return ended_result(cfg, call_id, state)
        if call_id is None:
            call_id = find_ongoing_call(cfg)


def watch_loop(cfg, call_id, budget, interval, since=0):
    """Supervise on two channels: the consult queue and the live transcript.

    `poll` only wakes when the agent asks, which leaves the other half of
    supervision unserved -- the agent mishandling something it does not know it
    is mishandling. Retell's monitor websocket is consumed inside this call and
    never surfaces as a stream; it just gives the command something to return
    on besides a consult. Acting on what comes back is `steer`, which needs
    nobody waiting on the far end."""
    import threading

    import ws

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
                if merge_turns(state["by_id"], msg):
                    state["turns"] = list(state["by_id"].values())
        finally:
            sock.close()

    def run_poll():
        _, body = queue(cfg, "/poll", params={"wait": budget, "call_id": call_id},
                        timeout=budget + 15)
        if isinstance(body, dict) and body.get("question"):
            state["consult"] = body

    threads = [threading.Thread(target=t, daemon=True) for t in (run_monitor, run_poll)]
    for t in threads:
        t.start()

    deadline = time.monotonic() + budget
    seen = since
    last_report = time.monotonic()
    next_status = 0.0
    while time.monotonic() < deadline:
        if time.monotonic() >= next_status:
            next_status = time.monotonic() + 15
            live = call_status(cfg, call_id)
            if live in ("ended", "error") and not state["consult"]:
                stop.set()
                return {"event": "call_ended", **ended_result(cfg, call_id, live)}
        if state["consult"]:
            stop.set()
            consult = dict(state["consult"])
            if isinstance(consult.get("transcript"), str):
                consult["transcript"] += IN_PROGRESS
            view = live_view(state["turns"], seen)
            if consult.get("consult_id"):
                view["hint"] = (f'answer with: dialer answer {consult["consult_id"]} '
                                f'--since {resume_point(state["turns"], seen)} '
                                "<<'EOF', your text, then EOF alone on the last line")
            return {"event": "consult", **consult,
                    "turns": view["turns"], "hint": view["hint"]}
        if state["ended"]:
            stop.set()
            return {"event": "call_ended",
                    **ended_result(cfg, call_id, state["ended"])}
        # The turn at `seen` is the one left in progress last time; it only
        # counts as news once a spoken turn after it exists, which also means
        # it is finished. Otherwise the same half-sentence is reported again.
        finished = open_from(state["turns"]) > seen
        if finished and time.monotonic() - last_report >= interval:
            stop.set()
            return {"event": "transcript", "call_id": call_id,
                    **live_view(state["turns"], seen)}
        time.sleep(0.4)

    stop.set()
    # An empty `new` alone is ambiguous -- a silent call and a dead socket look
    # identical -- so say which it was.
    return {"event": "idle", "call_id": call_id, **live_view(state["turns"], seen),
            "monitor": state["error"] or ("receiving" if state["types"] else "no frames")}


def cmd_watch(cfg, args):
    emit(watch_loop(cfg, args.call_id, args.budget, args.interval, args.since))


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

    # A take-over is not a hangup. Retell ends the *agent's* leg and stops the
    # clock, but the human is still on the phone -- and nothing said from here
    # on is recorded anywhere, so a supervisor told "call ended" would go on to
    # summarise a call it saw only the first minute of.
    if rec["disconnection_reason"] == "call_take_over":
        out["event"] = "taken_over"
        out["call_ended"] = False
        out["note"] = ("Louie took over and the agent was dropped. He is still on "
                       "the call; you are not supervising it any more. The "
                       "transcript above stops at the take-over and nothing after "
                       "it is recorded -- ask him how it went rather than "
                       "reporting this as the outcome.")
    return out


def cmd_poll(cfg, args):
    emit(poll_loop(cfg, args.call_id, args.budget))


def read_text(args, target):
    """The answer or steer text, read raw from stdin.

    As an argument it passes through the shell first, and under call pressure
    nobody escapes it: a "$50" in double quotes reached the agent as "0". A
    quoted heredoc hands the bytes over untouched."""
    usage = ("pass the text on stdin in a quoted heredoc, so the shell leaves $, "
             f"quotes and backticks alone:\n\n  dialer {args.cmd} {target} <<'EOF'\n"
             "  your text\n  EOF")
    if args.text is not None:
        die(f"text given as an argument has already been rewritten by the shell -- {usage}")
    if sys.stdin is None or sys.stdin.isatty():
        die(usage)
    # A forgotten heredoc can leave stdin an open pipe with nothing coming, and
    # blocking on that with someone on the line is the worst way to fail.
    try:
        import select
        if not select.select([sys.stdin], [], [], 2)[0]:
            die(usage)
    except (OSError, ValueError):
        pass  # not selectable: a test double, or a pipe on Windows
    text = sys.stdin.read().strip()
    if not text:
        die(f"refusing to send empty text -- {usage}")
    return text


def cmd_answer(cfg, args):
    """Answer, then resume watching in the same process -- see the module
    docstring for why these are not two commands.

    Answers by consult id, not call id: the agent can ask a second question
    while the first is still waiting, and an answer written against one must
    never land on the other."""
    # Ids are `<call_id>:q<hex>`, so the call to resume watching needs no
    # second argument.
    call_id, sep, _ = args.consult_id.partition(":")
    if not sep:
        die("pass the consult_id from the consult (e.g. call_8f2e:q7c1), not the call_id")
    text = read_text(args, args.consult_id)

    journal("answer", call_id=call_id, consult_id=args.consult_id, text=text)
    status, body = queue(cfg, "/answer", method="POST", timeout=25,
                         body={"consult_id": args.consult_id, "text": text})
    journal("answer", call_id=call_id, consult_id=args.consult_id, status=status)
    answered = {"answer_status": status, "answer_result": body}
    if status != 200:
        # Do not fall through to watching: the operator needs to see this now,
        # not after a 200s block.
        hint = "the answer did not land -- check the consult_id and resend"
        if status == 404:
            hint = ("nothing is waiting on that consult. answer_result.prior says "
                    "what became of it; answer_result.pending lists any still waiting.")
        emit({**answered, "hint": hint})
        sys.exit(1)

    print(json.dumps(answered, ensure_ascii=False), file=sys.stderr)
    emit(watch_loop(cfg, call_id, args.budget, args.interval, args.since))


def cmd_steer(cfg, args):
    """Push-side: inject context without waiting to be asked. Lands in the
    transcript as role `injected` -- not spoken by either party.

    Resumes watching afterwards for the same reason `answer` does: an
    injection is a bet on how the agent will use it, and the only way to know
    is to hear the next few turns."""
    text = read_text(args, args.call_id)
    body = {"call_control": {"additional_context": text,
                             "trigger_response": args.speak_now}}
    journal("steer", call_id=args.call_id, text=text, speak_now=args.speak_now)
    status, resp = retell(cfg, f"/v2/update-live-call/{args.call_id}",
                          method="PATCH", body=body)
    journal("steer", call_id=args.call_id, status=status)
    sent = {"steer_status": status, "steer_result": resp}
    if status not in (200, 204):
        emit({**sent, "hint": "the context did not land -- the call may have ended"})
        sys.exit(1)

    print(json.dumps(sent, ensure_ascii=False), file=sys.stderr)
    emit(watch_loop(cfg, args.call_id, args.budget, args.interval, args.since))


IN_PROGRESS = "  [...utterance may still be in progress]"
SPOKEN = ("agent", "user")


def open_from(turns):
    """Index of the last spoken turn: the first that may still change. Tool
    calls do not end it -- the agent's holding phrase and its reply once the
    consult returns share one turn id, so the reply grows behind the tool entries."""
    for i in range(len(turns) - 1, -1, -1):
        if turns[i].get("role") in SPOKEN:
            return i
    return len(turns)


def render_turns(turns, live=False):
    """Flatten to readable lines, dropping the per-word timings each turn
    carries -- they outweigh the text about ten to one.

    `live` marks the last spoken line, because the monitor streams an utterance
    as it is spoken: that turn is routinely a sentence cut mid-word, and a steer
    written against it can contradict what was actually said."""
    lines = []
    for t in turns:
        role = t.get("role")
        if role == "tool_call_invocation":
            lines.append(f"  [TOOL {t.get('name')}] {t.get('arguments', '')}")
        elif role == "tool_call_result":
            lines.append(f"  [RESULT] {t.get('content', '')}")
        else:
            lines.append(f"{role}: {(t.get('content') or '').strip()}")
    open_at = open_from(turns)
    if live and open_at < len(lines):
        lines[open_at] += IN_PROGRESS
    return lines


def resume_point(turns, since):
    """The last spoken turn may still be in progress, so it is never counted as
    seen: the next watch starts *at* it and shows the finished sentence, rather
    than after it with the rest of the sentence lost."""
    return max(since, open_from(turns)) if len(turns) > since else since


def live_view(turns, since):
    """What a live watch reports past `since`, and where to resume."""
    done = resume_point(turns, since)
    in_progress = len(turns) - done
    return {"turns": f"{done} + {in_progress} in progress" if in_progress else str(done),
            "new": render_turns(turns[since:], live=True),
            "hint": f"continue with `--since {done}` on watch, answer or steer"}


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

    # The caller works from SKILL.md, so the help lists only what it uses. The
    # module docstring and the dev commands (added with `description=` rather
    # than `help=`, so they stay out of the listing) read as more manual to go
    # digging in.
    ap = argparse.ArgumentParser(
        prog="dialer",
        description="Place a supervised call: dispatch, then watch / answer / steer "
                    "until it ends. SKILL.md is the full reference.")
    sub = ap.add_subparsers(
        dest="cmd", required=True,
        metavar="{dispatch,watch,answer,steer,transcript,journal,health}")

    def brief_args(parser):
        parser.add_argument("--opening", required=True,
                            help="first line; carries the AI announcement")
        parser.add_argument("--purpose", required=True,
                            help="one phrase, for call screening")
        parser.add_argument("--brief", help="the brief, inline")
        parser.add_argument("--brief-file", help="or the brief, from a file")

    d = sub.add_parser("dispatch", help="place a PSTN call with a brief written for it")
    d.add_argument("--to", help="callee, E.164")
    d.add_argument("--force", action="store_true",
                   help="dial even though another call is already ongoing")
    brief_args(d)
    d.set_defaults(fn=cmd_dispatch)

    st = sub.add_parser("stage", description="park a brief for the browser launcher to dial")
    brief_args(st)
    st.add_argument("--port", type=int, default=8765)
    st.set_defaults(fn=cmd_stage)

    sv = sub.add_parser("serve", description="browser-call launcher (dev/test)")
    sv.add_argument("--port", type=int, default=8765)
    sv.set_defaults(fn=cmd_serve)

    def watch_args(parser):
        parser.add_argument("--budget", type=int, default=POLL_BUDGET)
        parser.add_argument("--interval", type=int, default=WATCH_INTERVAL,
                            help="seconds of new dialogue to accumulate before "
                                 "returning: 15 for anything you must react to fast, "
                                 "30 while the call is slow or scripted. A consult "
                                 f"returns immediately either way (default {WATCH_INTERVAL})")
        parser.add_argument("--since", type=int, default=0,
                            help="turns already seen; report only what is new")

    w = sub.add_parser("watch", help="block until a consult, new dialogue, or the call ends")
    # Required: guessing "the ongoing call" picks another conversation's.
    w.add_argument("call_id", help="as dispatch printed it")
    watch_args(w)
    w.set_defaults(fn=cmd_watch)

    p = sub.add_parser("poll", description="block until the agent consults, or the call ends")
    p.add_argument("--call-id")
    p.add_argument("--budget", type=int, default=POLL_BUDGET)
    p.set_defaults(fn=cmd_poll)

    a = sub.add_parser("answer", help="answer a consult and resume watching")
    a.add_argument("consult_id", help="from the consult, e.g. call_8f2e:q7c1")
    # Accepted only to be refused with the heredoc form; see read_text.
    a.add_argument("text", nargs="?", help=argparse.SUPPRESS)
    watch_args(a)
    a.set_defaults(fn=cmd_answer)

    s = sub.add_parser("steer", help="inject context mid-call, then resume watching")
    s.add_argument("call_id")
    s.add_argument("text", nargs="?", help=argparse.SUPPRESS)
    s.add_argument("--speak-now", action="store_true",
                   help="speak immediately instead of waiting for their turn")
    watch_args(s)
    s.set_defaults(fn=cmd_steer)

    sub.add_parser("pending", description="non-blocking peek at the queue").set_defaults(fn=cmd_pending)

    t = sub.add_parser("transcript", help="post-call transcript with tool calls woven in")
    t.add_argument("call_id")
    t.add_argument("--raw", action="store_true",
                   help="full objects including per-word timings (~10x larger)")
    t.add_argument("--settle", type=int, default=10,
                   help="seconds to retry while Retell finalises the transcript")
    t.set_defaults(fn=cmd_transcript)

    c = sub.add_parser("call", description="the call as Retell's webhook sent it, pre-scrub")
    c.add_argument("call_id")
    c.add_argument("--raw", action="store_true")
    c.set_defaults(fn=cmd_call)

    j = sub.add_parser("journal", help="what this conversation actually did")
    j.add_argument("--limit", type=int, default=30)
    j.set_defaults(fn=cmd_journal)

    sub.add_parser("health", help="check queue, token, agent and number").set_defaults(fn=cmd_health)
    sub.add_parser("agent-pull", description="dump the live agent and LLM config").set_defaults(fn=cmd_agent_pull)

    args = ap.parse_args()
    args.fn(load_config(), args)


if __name__ == "__main__":
    main()
