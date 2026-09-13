"""Exercise the consult queue against `wrangler dev`: concurrent consults on
one call, retries, stale answers, hangups.

    (cd worker && npx wrangler dev --port 8799 --var CONSULT_TOKEN:testtoken)
    python worker/test_queue.py

Not covered: the 90s timeout, which is too slow to run every time. It
settles through the same path as a hangup, with outcome `timed_out`."""
import json
import sys
import threading
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8799"
TOKEN = "testtoken"
failed = False


def req(path, method="GET", body=None, auth=True, timeout=120):
    sep = "&" if "?" in path else "?"
    url = BASE + path + (f"{sep}token={TOKEN}" if auth else "")
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def held(call_id, question):
    """POST a consult on a thread; it blocks until the queue releases it."""
    out = []
    body = {"call": {"call_id": call_id}, "args": {"question": question}}
    t = threading.Thread(target=lambda: out.append(req("/consult", "POST", body, auth=False)))
    t.start()
    time.sleep(0.7)
    return t, out


def check(name, cond, detail=""):
    global failed
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"\n     {detail}"))
    failed |= not cond


# Two different questions on one call queue up, oldest first, each answered by id.
t1, o1 = held("call_a", "Tuesday 2pm?")
t2, o2 = held("call_a", "Date of birth?")
_, p = req("/poll?wait=2&call_id=call_a")
check("poll returns the oldest", p.get("question") == "Tuesday 2pm?", p)
check("the second is queued behind it", p.get("queued_behind") == 1, p)
first = p["consult_id"]
req("/answer", "POST", {"consult_id": first, "text": "no afternoons"})
t1.join(5)
check("first consult gets its own answer", o1 and o1[0][1] == "no afternoons", o1)
check("second is still held", t2.is_alive())
_, p2 = req("/poll?wait=2&call_id=call_a")
check("poll now hands over the second", p2.get("question") == "Date of birth?", p2)
req("/answer", "POST", {"consult_id": p2["consult_id"], "text": "1990-03-03"})
t2.join(5)
check("second consult gets its own answer", o2 and o2[0][1] == "1990-03-03", o2)

# Answering a settled consult reports what became of it.
s, stale = req("/answer", "POST", {"consult_id": first, "text": "again"})
check("re-answer is a 404", s == 404, stale)
check("the 404 carries the answer that landed",
      (stale.get("prior") or {}).get("text") == "no afternoons", stale)

# The poll filter keeps other calls' consults out.
t0, o0 = held("call_z", "Other call?")
_, pz = req("/poll?wait=1&call_id=call_a")
check("poll filtered to a call ignores other calls", pz.get("pending", "x") is None, pz)
req("/event", "POST", {"event": "call_ended", "call": {"call_id": "call_z"}}, auth=False)
t0.join(5)

# A retry of the same question takes over its slot rather than queueing.
t3, o3 = held("call_b", "Accept 9am?")
t4, o4 = held("call_b", "Accept 9am?")
t3.join(5)
check("the superseded retry socket is released", o3 and "call back" in str(o3[0][1]), o3)
_, pend = req("/pending")
items = [i for i in pend["items"] if i["call_id"] == "call_b"]
check("one consult pending after a retry", len(items) == 1, pend)

# An answer naming only the call is refused once it is ambiguous.
t5, o5 = held("call_b", "And the hygienist?")
s, amb = req("/answer", "POST", {"call_id": "call_b", "text": "x"})
check("call_id-only answer with two waiting is a 409", s == 409, amb)

# A hangup releases everything held for that call, and says so afterwards.
req("/event", "POST", {"event": "call_ended", "call": {"call_id": "call_b"}}, auth=False)
t4.join(5)
t5.join(5)
check("hangup releases every held consult",
      bool(o4) and bool(o5) and not t4.is_alive() and not t5.is_alive(), (o4, o5))
_, gone = req("/answer", "POST", {"consult_id": items[0]["consult_id"], "text": "late"})
check("a late answer after hangup says the call ended",
      (gone.get("prior") or {}).get("outcome") == "call_ended", gone)

sys.exit(1 if failed else 0)
