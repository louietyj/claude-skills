#!/usr/bin/env python3
"""Client for live-listen: Claude's side of a conversation Louie's phone or
watch is recording.

The relay keeps every speaker turn, device event and note as numbered entries,
so a recycled sandbox only pauses Claude: `watch` resumes from a cursor kept
on disk. Stdlib only, so nothing needs installing mid-conversation.
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
# The skill directory can be read-only (/mnt/skills), so state goes under $HOME.
STATE = os.environ.get("LIVE_LISTEN_STATE") or os.path.expanduser("~/.live-listen")

# Inside the Bash tool's default 120s timeout; it only caps silence.
WATCH_BUDGET = 90
WATCH_INTERVAL = 10
NOTE_MAX = 120
IN_PROGRESS = "  [...still speaking]"


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
    hdrs = {"content-type": "application/json", "user-agent": "live-listen/1.0"}
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
        die("no session -- run `listen start` first, or pass --id")
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
        tail = "" if e.get("delivered", True) else "  (NOT delivered: no device connected)"
        return f"[{at} >>] note to device: {e['text']}{tail}"
    return f"[{at} --] {e['text']}"


def describe_device(d, state):
    """Whether audio really reaches the transcriber: a quiet room vs a dead mic."""
    if not d:
        return "unknown"
    who = d.get("ui") or "device"
    if state in ("ended", "expired"):
        return f"session {state}"
    if state == "armed":
        return "no device yet: Louie has not tapped START"
    if not d.get("connected"):
        gone = d.get("disconnected_s")
        return f"{who} DISCONNECTED" + (f" for {gone}s" if gone is not None else "") + ": no audio"
    if d.get("hidden_s") is not None:
        return f"{who} page OFF SCREEN for {d['hidden_s']}s: mic is off, nothing is being heard"
    if d.get("transcriber") != "open":
        return f"{who} connected, transcriber {d.get('transcriber')}"
    audio = d.get("last_audio_s")
    if audio is None:
        return f"{who} connected, no audio received yet"
    if audio > 5:
        return f"{who} connected but no audio for {audio}s"
    return f"{who} streaming"


# --- commands --------------------------------------------------------------------

def cmd_health(cfg, args):
    status, body = request(f"{cfg['worker_url']}/health", timeout=15)
    if status != 200:
        die(f"relay unreachable at {cfg['worker_url']}: {status} {error_text(body)}")
    missing = [k for k, v in body.get("secrets", {}).items() if not v]
    if missing:
        die(f"relay is missing secrets: {', '.join(missing)} (wrangler secret put)")
    # 404 for a session that cannot exist proves the token passed the gate.
    status, body = relay(cfg, "/status", id="AAAA", timeout=15)
    if status == 401:
        die("relay rejected cli_token -- config.json and the CLI_TOKEN secret disagree")
    if status not in (200, 404):
        die(f"relay error on an authenticated route: {status} {error_text(body)}")
    emit({"ok": True, "relay": cfg["worker_url"], "secrets": "all set", "cli_token": "accepted"})


def read_terms(args):
    terms = list(args.term or [])
    if args.terms_file:
        with open(args.terms_file, encoding="utf-8") as fh:
            for line in fh:
                terms.extend(p.strip() for p in line.split(","))
    seen, out = set(), []
    for t in terms:
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def cmd_start(cfg, args):
    prev = (read_state("current.json") or {}).get("id")
    if prev and not args.force:
        status, body = relay(cfg, "/status", id=prev, timeout=15)
        if status == 200 and body.get("state") in ("armed", "live"):
            die(f"session {prev} is still {body['state']}. Keep watching it, `listen stop` it, "
                "or pass --force to start another alongside it.")
    terms = read_terms(args)
    status, body = relay(cfg, "/session", method="POST", timeout=20,
                         body={"label": args.label or "", "keyterms": terms, "archive": args.archive})
    if status != 200 or not isinstance(body, dict) or not body.get("id"):
        die(f"could not create a session: {status} {error_text(body)}")
    sid = body["id"]
    write_state("current.json", {"id": sid, "label": args.label or "", "at": time.time()})
    write_state(f"cursor-{sid}.json", {"since": 0})
    emit({"id": sid, "state": "armed", "keyterms": len(terms), "archive": args.archive,
          "next": f"Tell Louie: open the listen bookmark and tap START {sid} (check the id "
                  "matches). Then run `listen watch` and keep watching in this same turn."})


def cmd_watch(cfg, args):
    sid = current_id(args)
    cursor_name = f"cursor-{sid}.json"
    since = args.since if args.since is not None else (read_state(cursor_name) or {}).get("since", 0)
    budget = max(5, args.budget)
    status, body = relay(cfg, "/watch", id=sid, since=since, interval=args.interval,
                         wait=budget, timeout=budget + 20)
    if status != 200 or not isinstance(body, dict):
        emit({"event": "error", "id": sid, "error": f"{status} {error_text(body)}",
              "hint": "the relay holds everything; run `listen watch` again"})
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
                       "`listen transcript --batch` gets the clean full-file transcript.")
    else:
        out["hint"] = "steer in chat if anything is worth it, then `listen watch` again in this same turn"
    emit(out)
    # Only after the output is out: a kill mid-request must replay, not skip.
    write_state(cursor_name, {"since": body.get("last", since)})


def read_text(args):
    usage = ("pass the note on stdin in a quoted heredoc, so the shell leaves $, quotes and "
             "backticks alone:\n\n  listen note <<'EOF'\n  ask about the trustee fee\n  EOF")
    if args.text is not None:
        die(f"text given as an argument has already been rewritten by the shell -- {usage}")
    if sys.stdin is None or sys.stdin.isatty():
        die(usage)
    text = " ".join(sys.stdin.read().split())
    if not text:
        die(f"refusing to send an empty note -- {usage}")
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
             {"warning": "no device connected, so nothing vibrated; say it in chat"})})


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
    emit({"id": sid, "state": body.get("state"), "label": body.get("label"),
          "elapsed": mmss(body.get("elapsed_s")), "entries": body.get("seq"),
          "archive": body.get("archive"), "keyterms": body.get("keyterms"),
          "device": describe_device(body.get("device"), body.get("state"))})


def cmd_audio(cfg, args):
    """One webm per segment (every reconnect starts one); --batch deletes them."""
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
                                     headers={"user-agent": "live-listen/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()
        except urllib.error.HTTPError as e:
            die(f"segment {seg['n']}: {e.code} {e.read().decode(errors='replace')[:200]}")
        path = os.path.join(args.out, f"{sid}-seg{seg['n']}.webm")
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
    out = {"bookmark": f"{base}/", "pairing": "open it and enter a code from `listen pair`"}
    if args.with_token and cfg.get("device_token"):
        out["prepaired"] = f"{base}/#k={urllib.parse.quote(cfg['device_token'])}"
    emit(out)


def main():
    ap = argparse.ArgumentParser(prog="listen", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health", help="check the relay, its secrets and the token").set_defaults(fn=cmd_health)

    s = sub.add_parser("start", help="create and arm a session for the device to start")
    s.add_argument("--label", help="a few words shown under the id on the device")
    s.add_argument("--term", action="append", help="a keyterm for the transcriber (repeatable)")
    s.add_argument("--terms-file", help="keyterms, one per line or comma-separated")
    s.add_argument("--archive", action="store_true",
                   help="keep the audio for `transcript --batch`; deleted once that runs")
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
    args.fn(load_config(), args)


if __name__ == "__main__":
    main()
