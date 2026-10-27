#!/usr/bin/env python3
"""Client for intercom: Claude's side of a conversation Louie's phone or
watch is recording.

The relay keeps every speaker turn, device event and note as numbered entries,
so a recycled sandbox only pauses Claude: `watch` resumes from a cursor kept
on disk. Stdlib only, so nothing needs installing mid-conversation.
"""

import argparse
import datetime
import glob
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(os.path.dirname(HERE), "config.json")
GUIDE = os.path.join(os.path.dirname(HERE), "GUIDE.md")
GATE_PROMPT = os.path.join(os.path.dirname(HERE), "GATE.md")
# The skill directory can be read-only (/mnt/skills), so state goes under $HOME.
STATE = os.environ.get("INTERCOM_STATE") or os.path.expanduser("~/.intercom")

# Inside the Bash tool's default 120s timeout; it only caps silence.
WATCH_BUDGET = 90
WATCH_INTERVAL = 10
NOTE_MAX = 120
SPEAK_CHARS_PER_S = 8     # deliberately slow, so the timeout never cuts off playback
HOLD_RANGE = "0-10"       # the relay's bounds on the turn wait
IN_PROGRESS = "  [...still speaking]"
NOT_NEWS = {"note", "said", "setting"}   # as the relay's: logged, but wake nobody

# The gate (listen mode under Monitor): `follow` decides when the main agent wakes,
# and a cheap gate agent says which batches are worth waking it for.
CEILING_S = 30            # no closed turn waits longer than this for the main agent
GATE_GATHER_S = 10        # the gate judges speech in batches, as listen mode always has
GATE_STALE_S = 30         # a gate that hasn't polled this long while speech waits isn't running
GATE_POLL_S = 2           # follow's long-poll, short so the gate's answers land fast
GATE_PREFIX_TOKENS = 60_000    # a subagent's own system prompt and tools, before anything we print
GATE_CONTEXT_TOKENS = 95_000   # respawn before this: past 100K, Haiku's input costs 5x
GATE_MIN_TOKENS = 15_000       # a late handover is large; still give each gate this much to read
GATE_CALL_TOKENS = 120         # each call's own framing, beyond what it prints
NAME = re.compile(r"\bclaude\b", re.I)
GATE_QUESTION = ("Would the main-agent have a 40% or greater chance of acting on this batch under "
                 "<main-agent-guidance>? Think briefly, then answer with `intercom gate y` or "
                 "`intercom gate n`.")
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
CFS_EDIT = re.compile(r"\bcfs (?:edit|write)(?: --new)? (\S+)")


def die(msg, code=1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def load_config():
    if not os.path.exists(CONFIG):
        die(f"no config at {CONFIG} -- copy config.example.json and fill it in")
    with open(CONFIG, encoding="utf-8") as fh:
        cfg = json.load(fh)
    for key in ("worker_url", "cli_token"):
        if not cfg.get(key):
            die(f"config.json is missing {key}")
    cfg["worker_url"] = cfg["worker_url"].rstrip("/")
    return cfg


def request(url, *, method="GET", body=None, timeout=30):
    """(status, parsed_or_text); never raises, so errors stay readable mid-conversation."""
    data = json.dumps(body).encode() if body is not None else None
    # Cloudflare answers urllib's default User-Agent with a 403 (error 1010).
    hdrs = {"content-type": "application/json", "user-agent": "intercom/1.0"}
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


def relay(cfg, path, *, method="GET", body=None, timeout=30, **params):
    query = {"token": cfg["cli_token"], **{k: v for k, v in params.items() if v is not None}}
    return request(f"{cfg['worker_url']}{path}?{urllib.parse.urlencode(query)}",
                   method=method, body=body, timeout=timeout)


def emit(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))
    sys.stdout.flush()


def error_text(body):
    return body.get("error") if isinstance(body, dict) else str(body)[:300]


# --- local state ---------------------------------------------------------------

def state_path(name):
    os.makedirs(STATE, exist_ok=True)
    return os.path.join(STATE, name)


def read_state(name, default=None):
    try:
        with open(state_path(name), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_state(name, value):
    try:
        with open(state_path(name), "w", encoding="utf-8") as fh:
            json.dump(value, fh)
    except OSError:
        pass


def current_id(args):
    sid = getattr(args, "id", None) or (read_state("current.json") or {}).get("id")
    if not sid:
        die("no session -- run `intercom start` first, or pass --id")
    return sid.upper()


# --- rendering -------------------------------------------------------------------

def mmss(sec):
    s = max(0, int(sec or 0))
    h, m, r = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{r:02d}" if h else f"{m:02d}:{r:02d}"


def render(e):
    at = mmss(e.get("t"))
    if e["kind"] == "turn":
        return f"[{at} S{e.get('spk', 0)}] {e['text']}"
    if e["kind"] == "note":
        tail = "" if e.get("delivered", True) else "  (NOT delivered: no screen open)"
        return f"[{at} >>] note to device: {e['text']}{tail}"
    if e["kind"] == "said":
        return f"[{at} <<] said: {e['text']}" + ("  (interrupted)" if e.get("interrupted") else "")
    return f"[{at} --] {e['text']}"


FOLLOW_WIDTH = 380  # Monitor truncates each stdout line at ~400 chars; lines in one batch stay whole


def follow_line(e):
    """A Monitor event: the entry's n first, for `follow --since` and `speak --since`.
    A long entry wraps at word boundaries onto `n+` continuation lines, so it arrives whole."""
    line = f"{e['n']} {render(e)}" if "n" in e else f"[--] {e['text']}"
    if len(line) <= FOLLOW_WIDTH:
        return line
    cont = f"{e['n']}+ " if "n" in e else "[--]+ "
    out, rest = [], line
    while rest:
        room = FOLLOW_WIDTH - (len(cont) if out else 0)
        cut = len(rest) if len(rest) <= room else rest.rfind(" ", 0, room + 1)
        if cut <= 0:
            cut = room
        out.append((cont if out else "") + rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    return "\n".join(out)


def describe_device(d, state):
    """Whether audio really reaches the transcriber: a quiet room vs a dead mic."""
    if not d:
        return "unknown"
    if state in ("ended", "expired"):
        return f"session {state}"
    if state == "armed":
        return "no screen yet: Louie has not opened the room"
    screens = d.get("screens") or []
    who = d.get("audio")
    call = d.get("call")
    if call and call.get("state") != "up":
        return f"calling Louie's phone ({call.get('to')}), {call.get('state')} for {call.get('s')}s"
    if call and who == "line":
        audio = d.get("last_audio_s")
        if audio is not None and audio > 5:
            return f"on the phone line, but no call audio for {audio}s"
        return f"on the phone line for {call.get('s')}s ({call.get('to')})"
    if call:
        return f"phone line up for {call.get('s')}s ({call.get('to')}), its audio not started"
    if not who:
        since = d.get("no_audio_s")
        ago = f" {since}s ago" if since is not None else ""
        if not screens:
            return f"NO SCREEN OPEN and no audio{ago}: nothing is heard, notes don't reach him"
        why = d.get("no_audio_why")
        if why == "left":
            return f"Louie LEFT AUDIO{ago} (in the room on the {', '.join(screens)}): nothing is heard, no speech"
        if why == "dropped":
            return f"the screen holding the audio DISCONNECTED{ago}: no audio until it is back"
        if why == "call ended":
            return f"the PHONE LINE ENDED{ago}: no audio until a screen takes it or he is called again"
        return f"Louie is in the room on the {', '.join(screens)} but hasn't turned audio on"
    if d.get("mic_silent_s") is not None:
        where = " with its page off screen" if d.get("hidden_s") is not None else ""
        return f"{who} mic SILENT for {d['mic_silent_s']}s{where}: nothing is being heard"
    off = ""
    if d.get("hidden_s") is not None:
        if d.get("mic") != "live":
            return f"{who} page OFF SCREEN for {d['hidden_s']}s: mic is off, nothing is being heard"
        off = f", page off screen for {d['hidden_s']}s but its mic hears"
    if d.get("transcriber") != "open":
        return f"{who} connected, transcriber {d.get('transcriber')}{off}"
    audio = d.get("last_audio_s")
    if audio is None:
        return f"{who} connected, no audio received yet{off}"
    if audio > 5:
        return f"{who} connected but no audio for {audio}s{off}"
    return f"{who} streaming{off}"


# --- commands --------------------------------------------------------------------

def cmd_health(cfg, args):
    status, body = request(f"{cfg['worker_url']}/health", timeout=15)
    if status != 200:
        die(f"relay unreachable at {cfg['worker_url']}: {status} {error_text(body)}")
    missing = [k for k, v in body.get("secrets", {}).items() if not v]
    if missing:
        die(f"relay is missing secrets: {', '.join(missing)} (wrangler secret put)")
    phone = body.get("phone_line") or {}
    phone_line = "set up" if phone and all(phone.values()) else \
        f"not set up (missing {', '.join(k for k, v in phone.items() if not v) or 'everything'}): `call` will fail"
    isolation = "set up" if (body.get("voice_isolation") or {}).get("KRISP_API_KEY") else \
        "not set up (no KRISP_API_KEY): talk mode hears music and other voices"
    # 404 for a session that cannot exist proves the token passed the gate.
    status, body = relay(cfg, "/status", id="AAAA", timeout=15)
    if status == 401:
        die("relay rejected cli_token -- config.json and the CLI_TOKEN secret disagree")
    if status not in (200, 404):
        die(f"relay error on an authenticated route: {status} {error_text(body)}")
    emit({"ok": True, "relay": cfg["worker_url"], "secrets": "all set", "cli_token": "accepted",
          "phone_line": phone_line, "voice_isolation": isolation})


def fetch_default_terms(cfg):
    """(lines, warning); never raises, so a failed fetch can't hold up a fast dial."""
    url = cfg.get("keyterms_url")
    if not url:
        return [], "config.json has no keyterms_url; started without the default terms"
    status, body = request(url, timeout=10)
    if status != 200 or not isinstance(body, str):
        return [], f"default terms not fetched ({status} {error_text(body)}); started without them"
    return body.splitlines(), None


def read_terms(cfg, args):
    lines, warning = fetch_default_terms(cfg) if args.default_terms else ([], None)
    terms = list(args.term or [])
    if args.terms_file:
        with open(args.terms_file, encoding="utf-8") as fh:
            lines.extend(fh)
    for line in lines:
        terms.extend(p.strip() for p in line.split(","))
    seen, out = set(), []
    for t in terms:
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out, warning


def cmd_start(cfg, args):
    prev = (read_state("current.json") or {}).get("id")
    if prev and not args.force:
        status, body = relay(cfg, "/status", id=prev, timeout=15)
        if status == 200 and body.get("state") in ("armed", "live"):
            die(f"session {prev} is still {body['state']}. Keep watching it, `intercom stop` it, "
                "or pass --force to start another alongside it.")
    terms, terms_warning = read_terms(cfg, args)
    status, body = relay(cfg, "/session", method="POST", timeout=20,
                         body={"label": args.label or "", "keyterms": terms, "archive": args.archive,
                               "mode": args.mode, "isolate": args.mode == "talk" and not args.no_isolate,
                               **({"hold_s": args.hold} if args.hold is not None else {})})
    if status != 200 or not isinstance(body, dict) or not body.get("id"):
        die(f"could not create a session: {status} {error_text(body)}")
    sid = body["id"]
    write_state("current.json", {"id": sid, "label": args.label or "", "mode": args.mode, "at": time.time()})
    write_state(f"cursor-{sid}.json", {"since": 0})
    tap = (f"Tell Louie: open the intercom bookmark, join {sid} (check the id matches; it should say "
           f"{args.mode.upper()}), and tap Web (or Phone, to be called).")
    then = (" Then, if you have a Monitor tool (in any environment), arm it on the command `intercom follow`"
            + ("; in listen mode, then write the brief and spawn the gate (guide: \"The gate\")"
               if args.mode == "listen" else "")
            + "; only without one, run `intercom watch` and keep watching in this same turn.")
    emit({"id": sid, "mode": args.mode, "state": "armed", "keyterms": len(terms),
          **({"keyterms_warning": terms_warning} if terms_warning else {}),
          "archive": args.archive, "next": tap + then})


def cmd_watch(cfg, args):
    sid = current_id(args)
    cursor_name = f"cursor-{sid}.json"
    since = args.since if args.since is not None else (read_state(cursor_name) or {}).get("since", 0)
    budget = max(5, args.budget)
    status, body = relay(cfg, "/watch", id=sid, since=since, interval=args.interval,
                         wait=budget, timeout=budget + 20)
    if status != 200 or not isinstance(body, dict):
        emit({"event": "error", "id": sid, "error": f"{status} {error_text(body)}",
              "hint": "the relay holds everything; run `intercom watch` again"})
        return
    tail = body.get("tail")
    out = {
        "event": body["event"],
        "id": sid,
        "elapsed": mmss(body.get("elapsed_s")),
        "new": [render(e) for e in body.get("entries", [])],
        "tail": f"S{tail['spk']}: {tail['text']}{IN_PROGRESS}" if tail else None,
        "device": describe_device(body.get("device"), body.get("state")),
    }
    if body.get("more"):
        out["more"] = "backlog continues -- watch again straight away"
    if body["event"] == "ended":
        out["end_reason"] = body.get("end_reason")
        out["hint"] = ("The session is over. Give Louie a short wrap-up. If it was archived, "
                       "`intercom transcript --batch` gets the clean full-file transcript.")
    elif body.get("mode") == "talk":
        out["hint"] = ("new turns are Louie talking to you: answer with `intercom speak`, "
                       "then `intercom watch` again in this same turn")
    else:
        out["hint"] = ("speak only if it adds something (no 'still listening'), "
                       "then `intercom watch` again in this same turn")
    emit(out)
    # Only after the output is out: a kill mid-request must replay, not skip.
    write_state(cursor_name, {"since": body.get("last", since)})


def read_text(args, cmd="note", example="Ask about the trustee fee"):
    usage = (f"pass the text on stdin in a quoted heredoc, so the shell leaves $, quotes and "
             f"backticks alone:\n\n  intercom {cmd} <<'EOF'\n  {example}\n  EOF")
    if args.text is not None:
        die(f"text given as an argument has already been rewritten by the shell -- {usage}")
    if sys.stdin is None or sys.stdin.isatty():
        die(usage)
    # Windows PowerShell 5.1 starts piped text with a byte-order mark, which isn't whitespace.
    text = " ".join(sys.stdin.read().replace("﻿", "").split())
    if not text:
        die(f"refusing to send nothing -- {usage}")
    return text


def cmd_note(cfg, args):
    sid = current_id(args)
    text = read_text(args)
    if len(text) > NOTE_MAX:
        die(f"note is {len(text)} chars; {NOTE_MAX} at most. Cut it to the action, and put "
            "the full steer in chat.")
    status, body = relay(cfg, "/note", method="POST", id=sid, body={"text": text}, timeout=15)
    if status != 200:
        die(f"note not sent: {status} {error_text(body)}")
    emit({"ok": True, "delivered": body.get("delivered"),
          **({} if body.get("delivered") else
             {"warning": "no screen open, so nothing vibrated; say it in chat"})})


def speak_timeout(text):
    """Covers playback at a slow rate, the device's voice connecting, and generation."""
    return min(300, len(text) // SPEAK_CHARS_PER_S + 50)


def cmd_speak(cfg, args):
    sid = current_id(args)
    text = read_text(args, "speak", "Okay, the tests are back and two failed.")
    # Monitor users pass the last event's n; watch users have it in the saved cursor.
    since = args.since if args.since is not None else (read_state(f"cursor-{sid}.json") or {}).get("since")
    status, body = relay(cfg, "/speak", method="POST", id=sid,
                         body={"text": text, "barge_in": not args.no_barge_in,
                               **({"since": since} if since is not None else {})},
                         timeout=speak_timeout(text))
    if status != 200 or not isinstance(body, dict):
        die(f"not spoken: {status} {error_text(body)}")
    out = dict(body)
    if body.get("status") == "interrupted" and "audio moved" in (body.get("reason") or ""):
        out["hint"] = ("the audio moved to another device mid-sentence: Louie heard only `spoken`. Say "
                       "`unspoken` again now; speak waits for the new device's voice to connect.")
    elif body.get("status") == "interrupted" and "phone line ended" in (body.get("reason") or ""):
        out["hint"] = ("the call ended mid-sentence: Louie heard only `spoken`. Nothing redials; call again "
                       "only if he asks.")
    elif body.get("status") == "interrupted" and "left audio" in (body.get("reason") or ""):
        out["hint"] = ("Louie left audio mid-sentence and heard only `spoken`. When he turns it back on, "
                       "pick up from there if it still matters.")
    elif body.get("status") == "interrupted":
        out["hint"] = ("Louie heard only `spoken`; don't repeat it. What he said arrives as the next "
                       "event (heard_so_far is a preview: don't act on it).")
    elif body.get("status") == "not_delivered" and "left audio" in (body.get("reason") or ""):
        out["hint"] = "nothing was heard: Louie left audio. Say it when he's back, or in chat if it can't wait"
    elif body.get("status") == "not_delivered":
        out["hint"] = "nothing was heard: nothing in the room can play your voice right now. Tell him in chat"
    elif body.get("status") == "not_spoken":
        out["hint"] = ("nothing was played: Louie said something you hadn't read, or is still talking. "
                       "Read his turn, then decide what to say now.")
    emit(out)


def cmd_call(cfg, args):
    sid = current_id(args)
    status, body = relay(cfg, "/call", method="POST", id=sid, body={"to": args.to}, timeout=30)
    if status != 200 or not isinstance(body, dict):
        die(f"not called: {status} {error_text(body)}")
    emit({"id": sid, "call": body.get("state"),
          "next": "his phone is ringing; the device line and events say when it connects or ends"})


def cmd_hangup(cfg, args):
    sid = current_id(args)
    status, body = relay(cfg, "/hangup", method="POST", id=sid, body={}, timeout=30)
    if status != 200:
        die(f"not hung up: {status} {error_text(body)}")
    emit({"id": sid, "ok": True})


def cmd_set(cfg, args):
    sid = current_id(args)
    status, body = relay(cfg, "/set", method="POST", id=sid, body={"hold_s": args.hold}, timeout=15)
    if status != 200 or not isinstance(body, dict):
        die(f"not set: {status} {error_text(body)}")
    emit({"id": sid, "hold_s": body.get("hold_s")})


def cmd_monitor_url(cfg, args):
    sid = current_id(args)
    status, body = relay(cfg, "/status", id=sid, timeout=15)
    if status != 200 or not isinstance(body, dict):
        die(f"status failed: {status} {error_text(body)}")
    if not body.get("event_key"):
        die(f"session {sid} predates event streams; start a new one")
    base = cfg["worker_url"].replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    query = urllib.parse.urlencode({"id": sid, "k": body["event_key"], "since": args.since})
    print(f"{base}/events?{query}")


def cmd_follow(cfg, args):
    """For Monitor's command mode, whose websocket goes deaf on a quiet socket
    (README). Keeps its own cursor, not watch's, because `speak` defaults to that one.
    Talk turns go out as they close; listen mode goes through the gate."""
    sid = current_id(args)
    status, body = relay(cfg, "/status", id=sid, timeout=15)
    if status != 200 or not isinstance(body, dict):
        die(f"status failed: {status} {error_text(body)}")
    if body.get("mode") != "talk":
        return follow_gated(cfg, sid, args.since)
    since, backoff = args.since, 1
    while True:
        status, body = relay(cfg, "/watch", id=sid, since=since, interval=0,
                             wait=WATCH_BUDGET, timeout=WATCH_BUDGET + 20)
        if status in (401, 403, 404):
            print(follow_line({"kind": "error", "text": f"follow stopped: {status} {error_text(body)}"}), flush=True)
            return
        if status != 200 or not isinstance(body, dict):
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)
            continue
        backoff = 1
        for e in body.get("entries", []):
            if e.get("kind") not in NOT_NEWS:
                print(follow_line(e), flush=True)
        since = max(since, body.get("last") or 0)
        if body["event"] == "ended" and not body.get("more"):
            return


# --- the gate ----------------------------------------------------------------------
# `follow` alone reads the relay and spools to gate-<id>/; the gate reads the spool and
# answers through flag files. One writer per file, so nothing needs locking.

def gate_file(sid, name):
    d = state_path(f"gate-{sid}")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, value):
    """Atomic, so the other process never reads half a file. Windows won't replace a
    file someone has open, hence the retry."""
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh)
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05)


def take_flag(sid, name):
    try:
        os.remove(gate_file(sid, name))
        return True
    except OSError:
        return False


def read_spool(sid):
    """Every entry follow has seen, with `at`, its arrival time. A last line with no
    newline is still being written."""
    try:
        with open(gate_file(sid, "spool.jsonl"), encoding="utf-8") as fh:
            lines = fh.read().split("\n")[:-1]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


class Scheduler:
    """Which closed turns the main agent gets, and when: the gate's yes, its name, a
    `wake-next`, device news, or the oldest waiting line reaching CEILING_S."""

    def __init__(self):
        self.pending = []          # (arrived, entry)
        self.next_turn = False     # `wake-next`: deliver with the next closed turn

    def add(self, entry, now):
        """True when this entry makes a delivery due right away."""
        self.pending.append((now, entry))
        if entry.get("kind") != "turn":
            return True
        if NAME.search(entry.get("text") or ""):
            return True
        if self.next_turn:
            self.next_turn = False
            return True
        return False

    def overdue(self, now):
        return bool(self.pending) and now - self.pending[0][0] >= CEILING_S

    def take(self):
        out = [e for _, e in self.pending]
        self.pending = []
        return out


def gate_alert(sid, sched, now):
    """A line for the main agent when speech waits and no gate is polling."""
    polled = (read_json(gate_file(sid, "gate.json")) or {}).get("polled_at", 0)
    waiting = [at for at, e in sched.pending if e.get("kind") == "turn"]
    if waiting and now - polled > GATE_STALE_S and now - waiting[0] >= GATE_STALE_S:
        return {"kind": "event", "text": "the gate agent is not running (it has not polled for "
                f"{int(now - polled) if polled else 'ever'}s): start it again, as in the guide's \"The gate\""}
    return None


def follow_gated(cfg, sid, since):
    spool = gate_file(sid, "spool.jsonl")
    seen = max((e.get("n", 0) for e in read_spool(sid)), default=0)
    write_json(gate_file(sid, "delivered.json"), {"upto": since})
    sched, polled_since, backoff = Scheduler(), since, 1

    def deliver(extra=()):
        nonlocal since
        batch = sched.take()
        for e in [*batch, *extra]:
            print(follow_line(e), flush=True)
        since = max([since, *(e["n"] for e in batch if "n" in e)])
        write_json(gate_file(sid, "delivered.json"), {"upto": since})

    while True:
        if take_flag(sid, "wake-next"):
            sched.next_turn = True
        status, body = relay(cfg, "/watch", id=sid, since=polled_since, interval=0,
                             wait=GATE_POLL_S, timeout=GATE_POLL_S + 20)
        now = time.time()
        if status in (401, 403, 404):
            deliver([{"kind": "error", "text": f"follow stopped: {status} {error_text(body)}"}])
            return
        due = False
        if status == 200 and isinstance(body, dict):
            backoff = 1
            with open(spool, "a", encoding="utf-8") as fh:
                for e in body.get("entries", []):
                    if e["n"] > seen:
                        fh.write(json.dumps({"at": now, **e}, ensure_ascii=False) + "\n")
                        seen = e["n"]
                    if e["n"] > since and e.get("kind") not in NOT_NEWS:
                        due = sched.add(e, now) or due
            polled_since = max(polled_since, body.get("last") or 0)
        else:
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)
        due = take_flag(sid, "wake") or due or sched.overdue(now)
        # Needs speech that has waited as long as the ceiling, so it never adds a wake.
        alert = gate_alert(sid, sched, now)
        ended = (status == 200 and isinstance(body, dict) and body.get("event") == "ended"
                 and not body.get("more"))
        if (due or alert or ended) and sched.pending:
            deliver([alert] if alert else [])
        if ended:
            with open(spool, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"at": now, "kind": "end"}) + "\n")
            return


def main_transcript():
    """The main agent's JSONL: subagents inherit its session id, so the gate finds it by name."""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not sid:
        return None
    hits = glob.glob(os.path.join(os.path.expanduser("~/.claude/projects"), "*", f"{sid}.jsonl"))
    return max(hits, key=os.path.getmtime) if hits else None


def epoch(iso):
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return 0


def main_posts(path, offset):
    """(posts, new offset): the main agent's chat text and file edits after `offset`,
    as (time, text)."""
    if not path:
        return [], offset
    try:
        with open(path, "rb") as fh:
            fh.seek(offset)
            data = fh.read()
    except OSError:
        return [], offset
    end = data.rfind(b"\n") + 1
    posts = []
    for line in data[:end].splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("type") != "assistant" or r.get("isSidechain"):
            continue
        at = epoch(r.get("timestamp"))
        for b in (r.get("message") or {}).get("content") or []:
            if b.get("type") == "text" and b.get("text", "").strip() not in ("", "."):
                posts.append((at, b["text"].strip()))
            elif b.get("type") == "tool_use":
                inp = b.get("input") or {}
                if b.get("name") in EDIT_TOOLS and inp.get("file_path"):
                    posts.append((at, f"updated {os.path.basename(inp['file_path'])}"))
                for m in CFS_EDIT.finditer(inp.get("command") or ""):
                    posts.append((at, f"updated {os.path.basename(m.group(1))}"))
    return posts, offset + end


def gate_lines(entries, posts, delivered=None):
    """The transcript as the gate reads it, the main agent's posts in time order.
    With `delivered`, lines the rules already sent to the main agent say so."""
    items = [(e.get("at", 0), 0, e) for e in entries if e.get("kind") in ("turn", "event", "note")]
    items += [(at, 1, text) for at, text in posts]
    out = []
    for _, is_post, x in sorted(items, key=lambda i: (i[0], i[1])):
        if is_post:
            out.append(f"<main-agent-post>\n{x}\n</main-agent-post>")
        else:
            sent = delivered is not None and x.get("n", 0) <= delivered
            out.append(render(x) + ("  [delivered]" if sent else ""))
    return out


def gate_print(g, lines):
    text = "\n".join(lines)
    print(text, flush=True)
    g["budget"] = g.get("budget", 0) - len(text) // 4 - GATE_CALL_TOKENS


def gate_next(sid, g):
    """Block until speech the rules haven't delivered has gathered for GATE_GATHER_S,
    then print it with the question. What the rules delivered meanwhile is context."""
    path, start, shown = main_transcript(), time.time(), []
    while True:
        g["polled_at"] = time.time()
        write_json(gate_file(sid, "gate.json"), g)
        entries = read_spool(sid)
        delivered = (read_json(gate_file(sid, "delivered.json")) or {}).get("upto", 0)
        new = [e for e in entries if e.get("n", 0) > g["cursor"]]
        if any(e.get("kind") == "end" for e in entries):
            posts, g["posts_at"] = main_posts(path, g.get("posts_at", 0))
            gate_print(g, [*shown, *gate_lines(new, posts, delivered),
                           "ENDED: the session is over. End your turn now with the single word: ended."])
            g["asked"] = False
            break
        open_turns = [e for e in new if e.get("kind") == "turn" and e["n"] > delivered]
        if open_turns and time.time() - open_turns[0]["at"] >= GATE_GATHER_S:
            posts, g["posts_at"] = main_posts(path, g.get("posts_at", 0))
            g["cursor"], g["asked"] = new[-1]["n"], True
            gate_print(g, [*shown, *gate_lines(new, posts, delivered), GATE_QUESTION])
            break
        if new and not open_turns:
            posts, g["posts_at"] = main_posts(path, g.get("posts_at", 0))
            shown += gate_lines(new, posts, delivered)
            g["cursor"] = new[-1]["n"]
        if time.time() - start >= WATCH_BUDGET:
            g["asked"] = False
            gate_print(g, [*shown, "Nothing new to judge yet. Run `intercom gate wait`."])
            break
        time.sleep(0.5)
    g["polled_at"] = time.time()
    write_json(gate_file(sid, "gate.json"), g)


def guide_section(title):
    with open(GUIDE, encoding="utf-8") as fh:
        text = fh.read()
    start = text.index(f"## {title}")
    end = text.find("\n## ", start + 1)
    return text[start:end if end != -1 else None].strip()


def cmd_gate(cfg, args):
    """`start`: the prompt, the whole conversation so far, then the first batch. `y`/`n`
    answer the batch shown and wait for the next; `wait` only waits."""
    sid = current_id(args)
    g = read_json(gate_file(sid, "gate.json")) or {}
    if args.action == "start":
        delivered = (read_json(gate_file(sid, "delivered.json")) or {}).get("upto", 0)
        entries = [e for e in read_spool(sid) if e.get("n", 0) <= delivered]
        path = main_transcript()
        posts, offset = main_posts(path, 0)
        first = entries[0]["at"] if entries else time.time()
        posts = [(at, text) for at, text in posts if at >= first - 60]
        with open(GATE_PROMPT, encoding="utf-8") as fh:
            prompt = fh.read()
        brief = ""
        try:
            with open(gate_file(sid, "brief.txt"), encoding="utf-8") as fh:
                brief = fh.read().strip()
        except OSError:
            pass
        prompt = (prompt.replace("{{MAIN_AGENT_GUIDANCE}}", guide_section("Steering or collaborating"))
                        .replace("{{BRIEF}}", brief or "(none given: work it out from the conversation)"))
        handover = gate_lines(entries, posts)
        text = "\n".join([prompt, "", "<conversation_so_far>", *handover, "</conversation_so_far>",
                          *([] if path else ["(The main-agent's posts are not available here.)"])])
        print(text, flush=True)
        g = {"instance": g.get("instance", 0) + 1, "cursor": delivered, "posts_at": offset, "asked": False,
             "budget": max(GATE_MIN_TOKENS, GATE_CONTEXT_TOKENS - GATE_PREFIX_TOKENS - len(text) // 4)}
        print("\nThe newest batch follows.", flush=True)
        return gate_next(sid, g)
    if args.action in ("y", "n") and g.get("asked"):
        with open(gate_file(sid, "answers.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"instance": g.get("instance"), "upto": g["cursor"],
                                 "answer": args.action, "at": time.time()}) + "\n")
        if args.action == "y":
            write_json(gate_file(sid, "wake"), {"upto": g["cursor"]})
        g["asked"] = False
    if not g:
        die("no gate here yet -- run `intercom gate start`")
    if g.get("budget", 0) <= 0:
        print("RESPAWN: this gate has read as much as it should. End your turn now with the single "
              "word: respawn. A fresh gate takes over.", flush=True)
        write_json(gate_file(sid, "gate.json"), g)
        return
    gate_next(sid, g)


def cmd_wake_next(cfg, args):
    sid = current_id(args)
    write_json(gate_file(sid, "wake-next"), {"at": time.time()})
    emit({"ok": True, "next": "the next closed turn reaches you whatever the gate says"})


def cmd_brief(cfg, args):
    sid = current_id(args)
    text = read_text(args, "brief", "Louie and his parents planning an Alaska trip; I keep plan.md current.")
    with open(gate_file(sid, "brief.txt"), "w", encoding="utf-8") as fh:
        fh.write(text)
    emit({"ok": True, "next": "every gate reads it from its next `intercom gate start`"})


def cmd_stop(cfg, args):
    sid = current_id(args)
    status, body = relay(cfg, "/stop", method="POST", id=sid, body={}, timeout=20)
    if status != 200:
        die(f"stop failed: {status} {error_text(body)}")
    emit({"id": sid, "state": body.get("state")})


def cmd_transcript(cfg, args):
    sid = current_id(args)
    if args.batch:
        status, body = relay(cfg, "/transcript", id=sid, batch=1, timeout=600)
        if status != 200:
            die(f"batch transcript failed: {status} {error_text(body)}")
        print(body.get("transcript", ""))
        return
    status, body = relay(cfg, "/transcript", id=sid, timeout=60)
    if status != 200:
        die(f"transcript failed: {status} {error_text(body)}")
    print(f"# {sid} {body.get('label') or ''} ({body.get('state')})".rstrip())
    for e in body.get("entries", []):
        print(render(e))


def cmd_status(cfg, args):
    sid = current_id(args)
    status, body = relay(cfg, "/status", id=sid, timeout=15)
    if status != 200:
        die(f"status failed: {status} {error_text(body)}")
    emit({"id": sid, "mode": body.get("mode") or "listen", "state": body.get("state"),
          **({"hold_s": body["hold_s"]} if "hold_s" in body else {}),
          **({"isolate": body.get("isolate", False)} if body.get("mode") == "talk" else {}),
          "label": body.get("label"), "elapsed": mmss(body.get("elapsed_s")), "entries": body.get("seq"),
          "archive": body.get("archive"), "keyterms": body.get("keyterms"),
          "device": describe_device(body.get("device"), body.get("state"))})


def cmd_audio(cfg, args):
    """One file per segment (every reconnect starts one): webm from a page, WAV from the phone line;
    --batch deletes them."""
    sid = current_id(args)
    status, body = relay(cfg, "/status", id=sid, timeout=15)
    if status != 200:
        die(f"status failed: {status} {error_text(body)}")
    if not body.get("archive"):
        die("this session was not archived (start --archive)")
    os.makedirs(args.out, exist_ok=True)
    saved = []
    for seg in body.get("segs") or []:
        query = urllib.parse.urlencode({"token": cfg["cli_token"], "id": sid, "seg": seg["n"]})
        req = urllib.request.Request(f"{cfg['worker_url']}/audio?{query}",
                                     headers={"user-agent": "intercom/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()
        except urllib.error.HTTPError as e:
            die(f"segment {seg['n']}: {e.code} {e.read().decode(errors='replace')[:200]}")
        ext = "wav" if seg.get("raw") else "webm"           # a phone-line segment is WAV
        path = os.path.join(args.out, f"{sid}-seg{seg['n']}.{ext}")
        with open(path, "wb") as fh:
            fh.write(data)
        saved.append({"file": path, "starts_at": mmss(seg.get("start_s")), "bytes": len(data)})
    emit({"id": sid, "segments": saved})


def cmd_pair(cfg, args):
    status, body = relay(cfg, "/pair", method="POST", body={}, timeout=15)
    if status != 200 or not isinstance(body, dict) or not body.get("code"):
        die(f"could not issue a pairing code: {status} {error_text(body)}")
    emit({"code": body["code"], "expires_in": f"{body.get('expires_s', 300) // 60} minutes",
          "next": f"Tell Louie: open {cfg['worker_url']} on the device and enter {body['code']}."})


def cmd_bookmark(cfg, args):
    """--with-token adds a link that pairs without a code: a credential."""
    base = cfg["worker_url"]
    out = {"bookmark": f"{base}/", "pairing": "open it and enter a code from `intercom pair`"}
    if args.with_token and cfg.get("device_token"):
        out["prepaired"] = f"{base}/#k={urllib.parse.quote(cfg['device_token'])}"
    emit(out)


def main():
    ap = argparse.ArgumentParser(prog="intercom", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health", help="check the relay, its secrets and the token").set_defaults(fn=cmd_health)

    s = sub.add_parser("start", help="create and arm a session for the device to start")
    s.add_argument("--mode", choices=("listen", "talk"), default="listen",
                   help="listen: Claude hears but has no voice. talk: Claude can also speak "
                        "through the device. Fixed for the session (default %(default)s)")
    s.add_argument("--label", help="a few words shown under the id on the device")
    s.add_argument("--term", action="append", help="a keyterm for the transcriber (repeatable)")
    s.add_argument("--terms-file", help="keyterms, one per line or comma-separated")
    s.add_argument("--default-terms", action="store_true",
                   help="also fetch the standing keyterm list from config.json's keyterms_url; "
                        "if that fails, start without it and say so")
    s.add_argument("--archive", action="store_true",
                   help="keep the audio for `transcript --batch`; deleted once that runs")
    s.add_argument("--hold", type=float,
                   help=f"talk mode: seconds of silence that close Louie's turn ({HOLD_RANGE}; default: "
                        "his device's setting, else 0)")
    s.add_argument("--no-isolate", action="store_true",
                   help="talk mode: transcribe the room as it is, without Krisp's voice isolation "
                        "dropping music, chatter and other voices")
    s.add_argument("--force", action="store_true", help="start even though one is still live")
    s.set_defaults(fn=cmd_start)

    w = sub.add_parser("watch", help="block until new speech, a device event, or the end")
    w.add_argument("--since", type=int, help="entry number to resume after (default: saved cursor)")
    w.add_argument("--interval", type=int, default=WATCH_INTERVAL,
                   help="seconds to gather speech before returning (default %(default)s)")
    w.add_argument("--budget", type=int, default=WATCH_BUDGET,
                   help="max seconds to wait through silence (default %(default)s; keep it under "
                        "the bash tool's timeout)")
    w.add_argument("--id")
    w.set_defaults(fn=cmd_watch)

    n = sub.add_parser("note", help=f"a short note to the device, <= {NOTE_MAX} chars, on stdin")
    n.add_argument("text", nargs="?", help=argparse.SUPPRESS)
    n.add_argument("--id")
    n.set_defaults(fn=cmd_note)

    sp = sub.add_parser("speak", help="talk mode: say text on stdin through the "
                                      "device; blocks until played or Louie cuts in")
    sp.add_argument("text", nargs="?", help=argparse.SUPPRESS)
    sp.add_argument("--since", type=int,
                    help="the last entry number you read; newer turns from Louie mean nothing is played "
                         "(default: the watch cursor)")
    sp.add_argument("--no-barge-in", action="store_true",
                    help="testing: Louie's voice doesn't stop this one (pausing still does)")
    sp.add_argument("--id")
    sp.set_defaults(fn=cmd_speak)

    ca = sub.add_parser("call", help="have Telnyx ring a phone (Louie's) for the room's audio")
    ca.add_argument("--to", required=True, help="E.164, e.g. +14155550123; Louie's is in memory, never in config")
    ca.add_argument("--id")
    ca.set_defaults(fn=cmd_call)

    hu = sub.add_parser("hangup", help="end the room's phone call")
    hu.add_argument("--id")
    hu.set_defaults(fn=cmd_hangup)

    se = sub.add_parser("set", help="talk mode: change a live session's settings")
    se.add_argument("--hold", type=float, required=True,
                    help=f"seconds of silence that close Louie's turn ({HOLD_RANGE})")
    se.add_argument("--id")
    se.set_defaults(fn=cmd_set)

    mu = sub.add_parser("monitor-url", help="the websocket URL of the event stream (not for Monitor({ws}), "
                                            "which goes deaf on a quiet socket: use follow)")
    mu.add_argument("--since", type=int, default=0, help="the last entry number you saw (default 0)")
    mu.add_argument("--id")
    mu.set_defaults(fn=cmd_monitor_url)

    fo = sub.add_parser("follow", help="for Monitor({command}): each news entry past --since as a "
                                       "JSON line, until the session ends")
    fo.add_argument("--since", type=int, default=0, help="the last entry number you saw (default 0)")
    fo.add_argument("--id")
    fo.set_defaults(fn=cmd_follow)

    ga = sub.add_parser("gate", help="for the gate agent only: start, then answer each batch y or n")
    ga.add_argument("action", choices=("start", "y", "n", "wait"))
    ga.add_argument("--id")
    ga.set_defaults(fn=cmd_gate)

    wn = sub.add_parser("wake-next", help="listen mode: the next closed turn reaches you whatever the gate says")
    wn.add_argument("--id")
    wn.set_defaults(fn=cmd_wake_next)

    br = sub.add_parser("brief", help="listen mode: what this session is, on stdin, for every gate to read")
    br.add_argument("text", nargs="?", help=argparse.SUPPRESS)
    br.add_argument("--id")
    br.set_defaults(fn=cmd_brief)

    st = sub.add_parser("stop", help="end the session")
    st.add_argument("--id")
    st.set_defaults(fn=cmd_stop)

    t = sub.add_parser("transcript", help="every entry so far, or --batch for the full-file pass")
    t.add_argument("--batch", action="store_true")
    t.add_argument("--id")
    t.set_defaults(fn=cmd_transcript)

    ss = sub.add_parser("status", help="one session's state and device")
    ss.add_argument("--id")
    ss.set_defaults(fn=cmd_status)

    a = sub.add_parser("audio", help="download an archived session's audio (before --batch deletes it)")
    a.add_argument("--out", default=".", help="directory for the .webm files (default: here)")
    a.add_argument("--id")
    a.set_defaults(fn=cmd_audio)

    sub.add_parser("pair", help="a one-time code to pair a device's browser").set_defaults(fn=cmd_pair)

    b = sub.add_parser("bookmark", help="the device bookmark (one-time setup)")
    b.add_argument("--with-token", action="store_true", help="also the pre-paired link (a credential)")
    b.set_defaults(fn=cmd_bookmark)

    args = ap.parse_args()
    # A Windows console defaults to cp1252, which mangles transcribed dashes and quotes.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args.fn(load_config(), args)


if __name__ == "__main__":
    main()
