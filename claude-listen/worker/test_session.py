"""Exercise the relay end to end against `wrangler dev` and fake transcribers
(Soniox live, Deepgram batch): turn assembly, the device socket, notes,
reconnects, visibility, archive and batch, pairing, and the watch long-poll
Claude lives on.

    python worker/test_session.py            # starts wrangler dev itself
    python worker/test_session.py http://127.0.0.1:8799   # or use a running one

A running one must have been started with WRANGLER_VARS below.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "dev"))
import fake_stt  # noqa: E402

PORT = 8799
BASE = sys.argv[1] if len(sys.argv) > 1 else f"http://127.0.0.1:{PORT}"
CLI, DEV, DGKEY, SXKEY = "clitok", "devtok", "dgkey", "sxkey"
WRANGLER_VARS = (f"--var CLI_TOKEN:{CLI} --var DEVICE_TOKEN:{DEV} --var DEEPGRAM_API_KEY:{DGKEY} "
                 f"--var SONIOX_API_KEY:{SXKEY} "
                 "--var SONIOX_URL:http://127.0.0.1:8798/transcribe-websocket "
                 "--var DEEPGRAM_URL:http://127.0.0.1:8798/v1/listen")
FAKE = None
WRANGLER = None


def http(path, method="GET", body=None, token=CLI, timeout=60, **params):
    q = {**params}
    if token is not None:
        q["token"] = token
    url = f"{BASE}{path}?{urllib.parse.urlencode(q)}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"null")
        except ValueError:
            return e.code, {"error": raw.decode(errors="replace")[:500]}


def create(**body):
    status, out = http("/session", "POST", {"label": "test", **body})
    assert status == 200, out
    return out["id"]


def device(sid, k=DEV):
    ws = connect(f"{BASE.replace('http', 'ws')}/device?k={k}&id={sid}", open_timeout=10)
    ws.send(json.dumps({"type": "hello", "ui": "watch"}))
    return ws


def script(ws, **s):
    ws.send(json.dumps(s).encode())


def recv_until(ws, pred, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        try:
            m = json.loads(ws.recv(timeout=max(0.1, end - time.time())))
        except TimeoutError:
            break
        if pred(m):
            return m
    raise AssertionError("device never received the expected message")


def entries(sid):
    return http("/transcript", id=sid)[1]["entries"]


def wait_entries(sid, pred, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        es = entries(sid)
        if pred(es):
            return es
        time.sleep(0.2)
    raise AssertionError(f"entries never matched: {entries(sid)}")


def texts(es, kind):
    return [e["text"] for e in es if e["kind"] == kind]


def stream_count():
    return len(FAKE.streams)


def wait_streams(n, timeout=8):
    end = time.time() + timeout
    while time.time() < end and len(FAKE.streams) < n:
        time.sleep(0.1)
    assert len(FAKE.streams) >= n, f"only {len(FAKE.streams)} transcriber streams"
    return FAKE.streams[n - 1]


def setUpModule():
    global FAKE, WRANGLER
    FAKE = fake_stt.start(8798)
    if len(sys.argv) > 1:
        return
    persist = tempfile.mkdtemp(prefix="claude-listen-test-")
    cmd = f"npx wrangler dev --port {PORT} --persist-to {persist} {WRANGLER_VARS}"
    WRANGLER = subprocess.Popen(cmd, cwd=HERE, shell=True, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    end = time.time() + 90
    while time.time() < end:
        try:
            if http("/health", token=None, timeout=2)[0] == 200:
                return
        except OSError:
            pass
        time.sleep(1)
    raise RuntimeError("wrangler dev never came up")


def tearDownModule():
    if WRANGLER:
        if os.name == "nt":
            subprocess.run(f"taskkill /F /T /PID {WRANGLER.pid}", shell=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            WRANGLER.terminate()


class Gates(unittest.TestCase):
    def test_health_names_secrets_without_values(self):
        status, out = http("/health", token=None)
        self.assertEqual(status, 200)
        self.assertEqual(out["secrets"], {"CLI_TOKEN": True, "DEVICE_TOKEN": True,
                                          "SONIOX_API_KEY": True, "DEEPGRAM_API_KEY": True})

    def test_wrong_tokens_are_refused(self):
        self.assertEqual(http("/watch", token="nope", id="AAAA")[0], 401)
        self.assertEqual(http("/armed", token=None, k="nope")[0], 401)
        # The device token does not open Claude's routes, nor the reverse.
        self.assertEqual(http("/session", "POST", {}, token=DEV)[0], 401)
        self.assertEqual(http("/armed", token=None, k=CLI)[0], 401)

    def test_page_is_served_without_a_token(self):
        with urllib.request.urlopen(BASE + "/", timeout=10) as r:
            self.assertIn(b"Waiting for Claude", r.read())


class Pairing(unittest.TestCase):
    def claim(self, code):
        return http("/pair/claim", "POST", {"code": code}, token=None)

    def test_only_claude_can_issue_a_code(self):
        self.assertEqual(http("/pair", "POST", {}, token=DEV)[0], 401)
        self.assertEqual(http("/pair", "POST", {}, token=None)[0], 401)

    def test_code_trades_once_for_the_device_token(self):
        code = http("/pair", "POST", {})[1]["code"]
        self.assertRegex(code, r"^\d{6}$")
        wrong = "000000" if code != "000000" else "111111"
        self.assertEqual(self.claim(wrong), (401, {"ok": False, "error": "wrong code"}))
        self.assertEqual(self.claim(code), (200, {"ok": True, "k": DEV}))
        status, out = self.claim(code)
        self.assertEqual(status, 401)
        self.assertIn("no code waiting", out["error"])

    def test_code_dies_after_five_wrong_tries(self):
        code = http("/pair", "POST", {})[1]["code"]
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            self.claim(wrong)
        self.assertEqual(self.claim(code)[0], 401)


class Lifecycle(unittest.TestCase):
    def test_armed_session_shows_on_the_bookmark_then_goes_live(self):
        sid = create(label="estate lawyer")
        listed = http("/armed", token=None, k=DEV)[1]["sessions"]
        self.assertIn({"id": sid, "label": "estate lawyer", "state": "armed"},
                      [{k: s[k] for k in ("id", "label", "state")} for s in listed])
        ws = device(sid)
        es = wait_entries(sid, lambda es: "recording started" in texts(es, "event"))
        self.assertEqual(es[0]["n"], 1)
        listed = http("/armed", token=None, k=DEV)[1]["sessions"]
        self.assertEqual([s["state"] for s in listed if s["id"] == sid], ["live"])
        ws.close()

    def test_stop_on_device_ends_session_and_drops_it_from_the_bookmark(self):
        sid = create()
        ws = device(sid)
        ws.send(json.dumps({"type": "stop"}))
        recv_until(ws, lambda m: m.get("type") == "ended")
        out = http("/watch", id=sid, since=0, wait=5)[1]
        self.assertEqual(out["event"], "ended")
        self.assertEqual(out["end_reason"], "stopped on the device")
        listed = http("/armed", token=None, k=DEV)[1]["sessions"]
        self.assertNotIn(sid, [s["id"] for s in listed])
        with self.assertRaises(Exception):
            device(sid).recv(timeout=3)

    def test_last_words_land_before_the_end(self):
        sid = create()
        ws = device(sid)
        ws.send(json.dumps({"type": "seg"}))
        wait_streams(len(FAKE.streams) or 1)
        script(ws, on_close=[FAKE.tokens([("Bye", 1), ("now.", 1)])])
        time.sleep(0.5)
        ws.send(json.dumps({"type": "stop"}))
        recv_until(ws, lambda m: m.get("type") == "ended")
        es = entries(sid)
        kinds = [(e["kind"], e["text"]) for e in es]
        self.assertIn(("turn", "Bye now."), kinds)
        self.assertEqual(kinds[-1][1], "session ended: stopped on the device", kinds)

    def test_stop_from_claude_tells_the_device(self):
        sid = create()
        ws = device(sid)
        wait_entries(sid, lambda es: es)
        self.assertEqual(http("/stop", "POST", {}, id=sid)[0], 200)
        recv_until(ws, lambda m: m.get("type") == "ended")

    def test_second_device_replaces_the_first(self):
        sid = create()
        a = device(sid)
        wait_entries(sid, lambda es: es)
        b = device(sid)
        with self.assertRaises(ConnectionClosed) as cm:
            while True:
                a.recv(timeout=5)
        self.assertEqual(cm.exception.rcvd.code, 4000)
        b.close()


class Turns(unittest.TestCase):
    def test_header_chunk_and_config_reach_the_transcriber(self):
        n = stream_count()
        sid = create(keyterms=["Wooga", "trustee fee"])
        ws = device(sid)
        ws.send(json.dumps({"type": "seg"}))
        ws.send(b"WEBM-HEADER")          # right behind seg, before the transcriber is open
        ws.send(b"audio-1")
        s = wait_streams(n + 1)
        end = time.time() + 5
        while time.time() < end and len(s["audio"]) < 2:
            time.sleep(0.1)
        self.assertEqual(s["audio"][:2], [b"WEBM-HEADER", b"audio-1"])
        c = s["config"]
        self.assertEqual(c["api_key"], SXKEY)
        self.assertEqual(c["model"], "stt-rt-v5")
        self.assertTrue(c["enable_speaker_diarization"])
        self.assertEqual(c["context"]["terms"], ["Wooga", "trustee fee"])
        ws.close()

    def test_speaker_change_and_end_marker_split_turns(self):
        sid = create()
        ws = device(sid)
        ws.send(json.dumps({"type": "seg"}))
        t = FAKE.tokens
        script(ws, emit=[
            t([("So", 1), ("the", 1), ("trust", 1)]),
            t([("pays", 1), ("rent?", 1), ("Only", 2), ("medical.", 2)], lead=True, end=True),
            t([("Okay.", 1)], end=True),
        ])
        es = wait_entries(sid, lambda es: len(texts(es, "turn")) >= 3)
        turns = [(e["spk"], e["text"]) for e in es if e["kind"] == "turn"]
        self.assertEqual(turns, [(1, "So the trust pays rent?"), (2, "Only medical."), (1, "Okay.")])
        ws.close()

    def wait_tail(self, sid, want, timeout=8):
        end = time.time() + timeout
        while time.time() < end:
            out = http("/watch", id=sid, since=0, interval=0, wait=1)[1]
            if out["tail"] == want:
                return out
            time.sleep(0.2)
        self.fail(f"tail never became {want}: {out['tail']}")

    def test_interim_tokens_are_a_tail_not_an_entry(self):
        sid = create()
        ws = device(sid)
        ws.send(json.dumps({"type": "seg"}))
        script(ws, emit=[FAKE.tokens([("what", 1), ("I'm", 1), ("saying", 1)], final=False)])
        out = self.wait_tail(sid, {"spk": 1, "text": "what I'm saying"})
        self.assertEqual(texts(out["entries"], "turn"), [])
        ws.close()

    def test_finished_words_of_an_open_turn_show_in_the_tail(self):
        # Final but not yet a turn (no <end>, no speaker change): Claude must
        # still see them, or a monologue is invisible until it stops.
        sid = create()
        ws = device(sid)
        ws.send(json.dumps({"type": "seg"}))
        script(ws, emit=[FAKE.tokens([("So", 2), ("the", 2), ("core", 2)]),
                         FAKE.tokens([("idea", 2)], final=False, lead=True)])
        self.wait_tail(sid, {"spk": 2, "text": "So the core idea"})
        ws.close()

    def test_monologue_is_split_at_a_sentence_end_after_twelve_seconds(self):
        sid = create()
        ws = device(sid)
        ws.send(json.dumps({"type": "seg"}))
        tok = lambda text, ms: {"text": text, "start_ms": ms, "is_final": True, "speaker": "2"}
        script(ws, emit=[{"tokens": [tok("First.", 0), tok(" Still", 13000),
                                     tok(" going.", 13500), tok(" More", 14000)]}])
        es = wait_entries(sid, lambda es: texts(es, "turn"))
        self.assertEqual(texts(es, "turn"), ["First. Still going."])
        self.wait_tail(sid, {"spk": 2, "text": "More"})
        ws.close()

    def test_transcriber_that_keeps_failing_stops_restarting(self):
        sid = create()
        ws = device(sid)
        restarts = 0
        for _ in range(3):
            ws.send(json.dumps({"type": "seg"}))
            script(ws, emit=[{"error_code": 401, "error_message": "Incorrect API key provided."}], close=1008)
            try:
                recv_until(ws, lambda m: m.get("type") == "restart", timeout=4)
                restarts += 1
            except AssertionError:
                break
        self.assertEqual(restarts, 2, "should give up on the third quick failure")
        es = wait_entries(sid, lambda es: any("keeps failing" in t for t in texts(es, "event")))
        self.assertTrue(any("Incorrect API key" in t for t in texts(es, "event")))
        ws.close()


class Watch(unittest.TestCase):
    def test_quiet_room_returns_idle_with_device_status(self):
        sid = create()
        ws = device(sid)
        last = wait_entries(sid, lambda es: es)[-1]["n"]
        t0 = time.time()
        out = http("/watch", id=sid, since=last, wait=2)[1]
        self.assertGreaterEqual(time.time() - t0, 1.8)
        self.assertEqual(out["event"], "idle")
        self.assertEqual(out["entries"], [])
        self.assertEqual(out["last"], last)
        self.assertTrue(out["device"]["connected"])
        ws.close()

    def test_backlog_returns_at_once_and_news_waits_for_the_interval(self):
        sid = create()
        ws = device(sid)
        wait_entries(sid, lambda es: es)
        t0 = time.time()
        out = http("/watch", id=sid, since=0, interval=5, wait=20)[1]
        self.assertLess(time.time() - t0, 2, "a backlog should not wait out the interval")
        self.assertEqual(out["event"], "transcript")
        since = out["last"]
        ws.send(json.dumps({"type": "seg"}))
        got = {}
        t0 = time.time()
        th = threading.Thread(target=lambda: got.update(
            out=http("/watch", id=sid, since=since, interval=3, wait=20)[1], at=time.time()))
        th.start()
        time.sleep(0.5)                  # news lands after the watch is waiting
        script(ws, emit=[FAKE.tokens([("Hi.", 1)], end=True)])
        th.join(30)
        self.assertGreaterEqual(got["at"] - t0, 2.8)
        self.assertEqual(texts(got["out"]["entries"], "turn"), ["Hi."])
        ws.close()


class Device(unittest.TestCase):
    def test_note_reaches_the_device_and_is_logged(self):
        sid = create()
        ws = device(sid)
        wait_entries(sid, lambda es: es)
        status, out = http("/note", "POST", {"text": "x" * 121}, id=sid)
        self.assertEqual(status, 400)
        self.assertIn("120", out["error"])
        status, out = http("/note", "POST", {"text": "ask about the trustee fee"}, id=sid)
        self.assertEqual((status, out["delivered"]), (200, True))
        recv_until(ws, lambda m: m.get("type") == "note" and m["text"] == "ask about the trustee fee")
        self.assertIn("ask about the trustee fee", texts(entries(sid), "note"))
        ws.close()

    def test_own_note_does_not_wake_claudes_watch(self):
        sid = create()
        ws = device(sid)
        last = wait_entries(sid, lambda es: es)[-1]["n"]
        http("/note", "POST", {"text": "check the fee"}, id=sid)
        t0 = time.time()
        out = http("/watch", id=sid, since=last, interval=0, wait=2)[1]
        self.assertGreaterEqual(time.time() - t0, 1.8, "a note alone returned the watch early")
        self.assertEqual(out["event"], "idle")
        self.assertEqual(texts(out["entries"], "note"), ["check the fee"])   # still shown
        ws.close()

    def test_deepgram_drop_restarts_the_recorder(self):
        n = stream_count()
        sid = create()
        ws = device(sid)
        ws.send(json.dumps({"type": "seg"}))
        wait_streams(n + 1)
        script(ws, close=1011)
        recv_until(ws, lambda m: m.get("type") == "restart")
        ws.send(json.dumps({"type": "seg"}))
        wait_streams(n + 2)
        self.assertTrue(any("transcriber dropped" in t for t in texts(entries(sid), "event")))
        ws.close()

    def test_page_hidden_is_reported_as_mic_off(self):
        sid = create()
        ws = device(sid)
        ws.send(json.dumps({"type": "vis", "state": "hidden"}))
        out = http("/watch", id=sid, since=0, interval=0, wait=3)[1]
        self.assertTrue(any("mic is OFF" in t for t in texts(out["entries"], "event")))
        self.assertIsNotNone(out["device"]["hidden_s"])
        ws.send(json.dumps({"type": "vis", "state": "visible"}))
        wait_entries(sid, lambda es: any("back on screen" in t for t in texts(es, "event")))
        ws.close()

    def test_disconnect_and_reconnect_are_reported(self):
        sid = create()
        ws = device(sid)
        wait_entries(sid, lambda es: es)
        ws.close()
        wait_entries(sid, lambda es: any("device disconnected" in t for t in texts(es, "event")))
        ws = device(sid)
        es = wait_entries(sid, lambda es: any("reconnected after" in t for t in texts(es, "event")))
        # A clean close is not an object restart, so nothing may claim one.
        self.assertFalse(any("relay restarted" in t for t in texts(es, "event")), es)
        ws.close()


class Archive(unittest.TestCase):
    def test_batch_gets_each_segment_whole_then_deletes_audio(self):
        before = len(FAKE.batches)
        sid = create(archive=True, keyterms=["Wooga"])
        ws = device(sid)
        ws.send(json.dumps({"type": "seg"}))
        chunks = [bytes([65 + i]) * 1000 for i in range(5)]
        for c in chunks:
            ws.send(c)
        time.sleep(1)
        http("/stop", "POST", {}, id=sid)
        status, out = http("/transcript", id=sid, batch=1, timeout=120)
        self.assertEqual(status, 200, out)
        self.assertEqual(len(FAKE.batches), before + 1)
        self.assertEqual(FAKE.batches[-1]["body"], b"".join(chunks))
        self.assertIn("keyterm=Wooga", FAKE.batches[-1]["query"])
        self.assertTrue(out["audio_deleted"])
        self.assertIn("[SPEAKER_0] 00:00\nHello there.", out["transcript"])
        self.assertIn("[SPEAKER_1] 00:02\nHi.", out["transcript"])
        # Cached: a second call must not spend another batch request.
        self.assertEqual(http("/transcript", id=sid, batch=1)[1]["transcript"], out["transcript"])
        self.assertEqual(len(FAKE.batches), before + 1)

    def test_saved_audio_downloads_whole_while_live_and_is_gone_after_batch(self):
        sid = create(archive=True)
        ws = device(sid)
        ws.send(json.dumps({"type": "seg"}))
        first = [b"H" * 70_000, b"I" * 70_000]   # crosses a row boundary
        for c in first:
            ws.send(c)
        time.sleep(1)
        got = urllib.request.urlopen(f"{BASE}/audio?token={CLI}&id={sid}&seg=0", timeout=30).read()
        self.assertEqual(got, b"".join(first))
        ws.send(b"J" * 1000)                      # keeps appending after a mid-session flush
        time.sleep(1)
        http("/stop", "POST", {}, id=sid)
        got = urllib.request.urlopen(f"{BASE}/audio?token={CLI}&id={sid}&seg=0", timeout=30).read()
        self.assertEqual(got, b"".join(first) + b"J" * 1000)
        self.assertEqual(http("/transcript", id=sid, batch=1, timeout=120)[0], 200)
        status, out = http("/audio", id=sid, seg=0)
        self.assertEqual(status, 404)
        self.assertIn("deleted by the batch pass", out["error"])

    def test_batch_without_archive_says_why(self):
        sid = create()
        status, out = http("/transcript", id=sid, batch=1)
        self.assertEqual(status, 400)
        self.assertIn("--archive", out["error"])


if __name__ == "__main__":
    unittest.main(argv=sys.argv[:1], verbosity=2)
