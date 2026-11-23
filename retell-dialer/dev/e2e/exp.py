"""Play a scripted 'business' into the test Retell agent over intercom, answering
consults on a script, then print Retell's transcript with tool calls. Needs
`rt.py clone` + `rt.py bind` first, and intercom's config.json. See README.md.

    exp.py SCRIPT.json      transcript saved next to it as SCRIPT.out.txt

SCRIPT: {"lines": [[delay_s, "text"], ...], "answers": [[delay_s, "text"], ...],
         "tail_s": 15}
Each line waits delay_s after the previous step, then is spoken (blocking
until played). Each consult that arrives is answered with the next answer,
delay_s after it arrived.
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from rt import CFG, SKILL, state  # noqa: E402

IC = os.path.join(os.path.dirname(SKILL), "intercom", "bin", "intercom.py")
TEST = state()
SIP = f"sip:{CFG['from_number']}@sip.retellai.com;transport=tcp"
T0 = time.time()
LOG = []


def log(*a):
    line = f"[{time.time() - T0:6.1f}] " + " ".join(str(x) for x in a)
    LOG.append(line)
    print(line, flush=True)


def ic(*args, stdin=None):
    out = subprocess.run(["python3", IC, *args], input=stdin, capture_output=True, text=True,
                         cwd=os.path.dirname(os.path.dirname(IC)))
    if out.returncode:
        log("intercom", args[0], "FAILED", out.stderr.strip()[:300])
    return out.stdout


def http(url, method="GET", body=None, headers=None, timeout=40):
    req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"content-type": "application/json", "User-Agent": "rt-harness/1",
                                          **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def retell(path, method="GET", body=None):
    return http(f"https://api.retellai.com{path}", method, body,
                {"Authorization": f"Bearer {CFG['retell_api_key']}"})


def queue(path, method="GET", body=None, **params):
    q = urllib.parse.urlencode({"token": CFG["consult_token"], **params})
    return http(f"{CFG['worker_url']}{path}?{q}", method, body, timeout=60)


def find_call():
    for _ in range(30):
        st, body = retell("/v3/list-calls", "POST", {"sort_order": "descending", "limit": 10})
        for c in (body.get("items") if isinstance(body, dict) else []) or []:
            if c.get("agent_id") == TEST["agent_id"] and c.get("call_status") in ("registered", "ongoing"):
                return c["call_id"]
        time.sleep(1)
    return None


def answerer(call_id, answers, stop):
    answers = list(answers)
    while not stop.is_set():
        st, body = queue("/poll", wait=15, call_id=call_id)
        if st == 200 and isinstance(body, dict) and body.get("question"):
            log("CONSULT", body["consult_id"], "|", body["question"])
            delay, text = answers.pop(0) if answers else (1, "Noted.")
            time.sleep(delay)
            st, out = queue("/answer", "POST", {"consult_id": body["consult_id"], "text": text})
            log("ANSWERED", st, "|", text)


def main():
    script = json.load(open(sys.argv[1]))
    sid = json.loads(ic("start", "--mode", "talk", "--no-isolate", "--label", "retell harness test", "--force"))["id"]
    log("session", sid)
    log("call", ic("call", "--to", SIP, "--id", sid).strip().replace("\n", " "))
    for _ in range(60):
        st = json.loads(ic("status", "--id", sid) or "{}")
        if "on the phone line" in str(st.get("device")):
            break
        time.sleep(1)
    else:
        log("never connected", st)
        ic("stop", "--id", sid)
        return
    log("line up")
    call_id = find_call()
    log("retell call", call_id)
    stop = threading.Event()
    threading.Thread(target=answerer, args=(call_id, script.get("answers", []), stop), daemon=True).start()
    for delay, text in script["lines"]:
        time.sleep(delay)
        log("SAY", text)
        for _ in range(20):   # the agent may still be talking: wait for a gap
            out = ic("speak", "--no-barge-in", "--since", "999999", "--id", sid, stdin=text)
            if '"not_spoken"' not in out:
                break
            time.sleep(1)
        log("  ->", out.strip().replace("\n", " ")[:160])
    time.sleep(script.get("tail_s", 15))
    stop.set()
    ic("hangup", "--id", sid)
    time.sleep(3)
    ic("stop", "--id", sid)
    time.sleep(5)
    st, call = retell(f"/v2/get-call/{call_id}")
    tr = []
    for m in (call.get("transcript_with_tool_calls") or []) if isinstance(call, dict) else []:
        if m.get("role") in ("agent", "user"):
            tr.append(f"{m['role']}: {m.get('content')}")
        elif m.get("role") == "tool_call_invocation":
            tr.append(f"  [TOOL {m.get('name')}] {m.get('arguments')}")
        elif m.get("role") == "tool_call_result":
            tr.append(f"  [RESULT] {str(m.get('content'))[:200]}")
        else:
            tr.append(f"  [{m.get('role')}] {json.dumps(m)[:200]}")
    out = os.path.splitext(sys.argv[1])[0] + ".out.txt"
    with open(out, "w") as f:
        f.write("\n".join(LOG) + "\n\n=== RETELL ===\n" + "\n".join(tr) + "\n")
    print("\n=== RETELL ===\n" + "\n".join(tr))
    print("\nsaved", out, "| ended:", call.get("disconnection_reason") if isinstance(call, dict) else call)


if __name__ == "__main__":
    main()
