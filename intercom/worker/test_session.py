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
CLI, DEV, DGKEY, SXKEY, SFUKEY = "clitok", "devtok", "dgkey", "sxkey", "sfukey"
PLAYOUT_MS = 200
WRANGLER_VARS = (f"--var CLI_TOKEN:{CLI} --var DEVICE_TOKEN:{DEV} --var DEEPGRAM_API_KEY:{DGKEY} "
                 f"--var SONIOX_API_KEY:{SXKEY} "
                 "--var SONIOX_URL:http://127.0.0.1:8798/transcribe-websocket "
                 "--var SONIOX_TTS_URL:http://127.0.0.1:8798/tts-websocket "
                 f"--var REALTIME_APP_ID:app1 --var REALTIME_APP_TOKEN:{SFUKEY} "
                 f"--var SFU_URL:http://127.0.0.1:8798/v1/apps --var VOICE_PLAYOUT_MS:{PLAYOUT_MS} "
                 "--var VOICE_READY_WAIT_MS:2000 --var LISTEN_BATCH_MS:1500 --var TALK_HOLD_MS:1000 "
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
    persist = tempfile.mkdtemp(prefix="intercom-test-")
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
                                          "SONIOX_API_KEY": True, "DEEPGRAM_API_KEY": True,
                                          "REALTIME_APP_ID": True, "REALTIME_APP_TOKEN": True})

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

    def test_closing_right_after_stop_is_not_a_drop(self):
        sid = create()
        ws = device(sid)
        ws.send(json.dumps({"type": "seg"}))
        wait_streams(len(FAKE.streams) or 1)
        ws.send(json.dumps({"type": "stop"}))
        ws.close()
        es = wait_entries(sid, lambda es: any("session ended" in t for t in texts(es, "event")))
        self.assertFalse(any("device disconnected" in t for t in texts(es, "event")), es)

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
        # The key rides on the connection; api_key in the config is deprecated.
        self.assertEqual(s["auth"], f"Bearer {SXKEY}")
        self.assertNotIn("api_key", c)
        self.assertEqual(c["model"], "stt-rt-v5")
        self.assertTrue(c["enable_speaker_diarization"])
        self.assertNotIn("max_endpoint_delay_ms", c)
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

    def test_a_page_that_stops_sending_audio_is_reported(self):
        n = stream_count()
        sid = create()
        ws = device(sid)
        ws.send(json.dumps({"type": "seg"}))
        wait_streams(n + 1)
        script(ws)
        wait_entries(sid, lambda es: any("stopped sending audio" in t for t in texts(es, "event")), timeout=25)
        script(ws)
        wait_entries(sid, lambda es: any("audio is back after" in t for t in texts(es, "event")))
        ws.close()

    def test_a_mic_that_still_hears_off_screen_is_not_news(self):
        sid = create()
        ws = device(sid)
        ws.send(json.dumps({"type": "mic", "state": "live"}))
        ws.send(json.dumps({"type": "vis", "state": "hidden"}))
        out = http("/watch", id=sid, since=0, interval=0, wait=3)[1]
        events = texts(out["entries"], "event")
        self.assertFalse(any("left the screen" in t for t in events), events)
        self.assertEqual(out["device"]["mic"], "live")
        self.assertIsNotNone(out["device"]["hidden_s"])
        ws.send(json.dumps({"type": "mic", "state": "silent"}))
        es = wait_entries(sid, lambda es: any("went silent" in t for t in texts(es, "event")))
        self.assertTrue(any("page off screen" in t for t in texts(es, "event")))
        self.assertIsNotNone(http("/status", id=sid)[1]["device"]["mic_silent_s"])
        ws.send(json.dumps({"type": "mic", "state": "live"}))
        wait_entries(sid, lambda es: any("mic is back after" in t for t in texts(es, "event")))
        ws.send(json.dumps({"type": "vis", "state": "visible"}))
        time.sleep(1)
        out = http("/watch", id=sid, since=0, interval=0, wait=1)[1]
        self.assertFalse(any("back on screen" in t for t in texts(out["entries"], "event")))
        self.assertIsNone(out["device"]["hidden_s"])
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


def create_talk(**body):
    status, out = http("/session", "POST", {"label": "test", "mode": "talk", **body})
    assert status == 200, out
    return out["id"], out["event_key"]


def speak_in_background(sid, text):
    """speak blocks until the device reports playback, so the test plays the device meanwhile."""
    got = {}
    th = threading.Thread(target=lambda: got.update(zip(("status", "out"), http(
        "/speak", "POST", {"text": text}, id=sid, timeout=60))))
    th.start()
    return th, got


SEG_T0 = 0.0


def connect_voice(ws, ready=True):
    """What the page does on connect: subscribe to Claude's track, answer, and say when it plays."""
    ws.send(json.dumps({"type": "voice_request"}))
    recv_until(ws, lambda m: m.get("type") == "voice_offer")
    ws.send(json.dumps({"type": "voice_answer", "sdp": {"type": "answer", "sdp": "v=0 fake-answer"}}))
    if ready:
        ws.send(json.dumps({"type": "voice_ready"}))


def live_talk(with_transcriber=False):
    global SEG_T0
    sid, key = create_talk()
    ws = device(sid)
    wait_entries(sid, lambda es: es)
    connect_voice(ws)
    if with_transcriber:
        n = stream_count()
        SEG_T0 = time.time()
        ws.send(json.dumps({"type": "seg"}))
        wait_streams(n + 1)
    return sid, key, ws


def tokens_now(words, final=True, end=False):
    """Words said right now, by the segment's audio clock: the echo gate matches on it."""
    base = int((time.time() - SEG_T0) * 1000)
    toks = [{"text": (" " if i else "") + w, "start_ms": base + 100 * i, "end_ms": base + 100 * i + 80,
             "is_final": final, "speaker": "0"} for i, w in enumerate(words)]
    if end:
        toks.append({"text": "<end>", "is_final": True})
    return {"tokens": toks}


class Modes(unittest.TestCase):
    def test_listen_session_has_no_voice(self):
        sid = create()
        ws = device(sid)
        wait_entries(sid, lambda es: es)
        status, out = http("/speak", "POST", {"text": "hello"}, id=sid)
        self.assertEqual(status, 409)
        self.assertIn("listen session", out["error"])
        ws.close()

    def test_unknown_mode_is_refused(self):
        self.assertEqual(http("/session", "POST", {"mode": "shout"})[0], 400)

    def test_bookmark_shows_the_mode(self):
        sid, _ = create_talk()
        listed = http("/armed", token=None, k=DEV)[1]["sessions"]
        self.assertEqual([s["mode"] for s in listed if s["id"] == sid], ["talk"])

    def test_talk_transcriber_waits_patiently_and_skips_diarization(self):
        _, _, ws = live_talk(with_transcriber=True)
        c = FAKE.streams[-1]["config"]
        self.assertFalse(c["enable_speaker_diarization"])
        self.assertEqual((c["max_endpoint_delay_ms"], c["endpoint_sensitivity"]), (3000, -1.0))
        ws.close()


class Speak(unittest.TestCase):
    def test_too_long_and_no_device_are_refused_without_speaking(self):
        sid, _ = create_talk()
        before = len(FAKE.speeches)
        status, out = http("/speak", "POST", {"text": "x" * 401}, id=sid)
        self.assertEqual(status, 413)
        self.assertIn("400 at most", out["error"])
        ws = device(sid)
        wait_entries(sid, lambda es: es)
        ws.close()
        wait_entries(sid, lambda es: any("device disconnected" in t for t in texts(es, "event")))
        status, out = http("/speak", "POST", {"text": "hello there"}, id=sid)
        self.assertEqual((status, out["status"]), (200, "not_delivered"))
        self.assertEqual(len(FAKE.speeches), before)

    def test_speech_waits_until_the_device_can_play_it(self):
        sid, _ = create_talk()
        ws = device(sid)
        wait_entries(sid, lambda es: es)
        connect_voice(ws, ready=False)
        before = len(FAKE.speeches)
        th, got = speak_in_background(sid, "Can you hear me?")
        time.sleep(1.0)
        self.assertEqual(len(FAKE.speeches), before, "speech started before the page could play it")
        ws.send(json.dumps({"type": "voice_ready"}))
        recv_until(ws, lambda m: m.get("type") == "tts_start")
        th.join(20)
        self.assertEqual(got["out"]["status"], "done", got)
        ws.close()

    def test_a_voice_that_never_connects_is_not_delivered(self):
        sid, _ = create_talk()
        ws = device(sid)
        wait_entries(sid, lambda es: es)
        connect_voice(ws, ready=False)
        out = http("/speak", "POST", {"text": "hello"}, id=sid, timeout=20)[1]
        self.assertEqual((out["status"], out["reason"]), ("not_delivered", "Claude's voice is not connected on the device"))
        ws.close()

    def packets_since(self, i):
        return FAKE.sfu["packets"][i:]

    def test_speech_is_paced_into_the_voice_track_and_logged_but_not_news(self):
        sid, _, ws = live_talk()
        last = entries(sid)[-1]["n"]
        before = len(FAKE.sfu["packets"])
        t0 = time.time()
        th, got = speak_in_background(sid, "The tests are back.")
        recv_until(ws, lambda m: m.get("type") == "tts_start")
        recv_until(ws, lambda m: m.get("type") == "tts_done", timeout=15)
        th.join(20)
        self.assertEqual((got["out"]["status"], got["out"]["spoken"]), ("done", "The tests are back."), got)
        # Four 0.5 s words of 48 kHz mono, doubled to stereo, fed at real time.
        pk = self.packets_since(before)
        self.assertEqual(sum(p["len"] for p in pk), 4 * 48000 * 2)
        self.assertTrue(all(p["size"] <= 32 * 1024 for p in pk))
        self.assertGreater(pk[-1]["t"] - pk[0]["t"], 1.5, "audio was not paced at real time")
        self.assertGreater(time.time() - t0, 2.0)
        adapter = FAKE.sfu["adapters"][-1]
        self.assertEqual(adapter["auth"], f"Bearer {SFUKEY}")
        self.assertEqual({k: adapter["track"][k] for k in ("location", "trackName", "inputCodec")},
                         {"location": "local", "trackName": "claude", "inputCodec": "pcm"})
        rec = FAKE.speeches[-1]
        self.assertEqual(rec["auth"], f"Bearer {SXKEY}")
        self.assertEqual({k: rec["config"][k] for k in ("model", "voice", "speed", "sample_rate")},
                         {"model": "tts-rt-v2", "voice": "Hazel", "speed": 1.3, "sample_rate": 48000})
        self.assertIn("The tests are back.", texts(entries(sid), "said"))
        out = http("/watch", id=sid, since=last, interval=0, wait=2)[1]
        self.assertEqual(out["event"], "idle", "Claude's own speech must not wake its watch")
        ws.close()

    def test_louie_cutting_in_mutes_the_page_and_stops_feeding_the_track(self):
        sid, _, ws = live_talk(with_transcriber=True)
        th, got = speak_in_background(sid, "one two three four five six seven eight")
        recv_until(ws, lambda m: m.get("type") == "tts_start")
        time.sleep(1.6)
        ws.send(json.dumps({"type": "loud", "on": True}))
        script(ws, emit=[tokens_now(["hold", "on"], final=False)])
        recv_until(ws, lambda m: m.get("type") == "tts_stop")
        th.join(20)
        out = got["out"]
        self.assertEqual(out["status"], "interrupted", out)
        words = "one two three four five six seven eight".split()
        heard = out["spoken"].split()
        # About 1.6 s played less the playout delay: two or three 0.5 s words.
        self.assertIn(len(heard), (2, 3), out)
        self.assertEqual(heard + out["unspoken"].split(), words)
        self.assertEqual(out["heard_so_far"], "hold on")
        # Generation finished long before; what matters is that the queued audio never reaches the track.
        n = len(FAKE.sfu["packets"])
        time.sleep(0.6)
        self.assertEqual(len(FAKE.sfu["packets"]), n, "audio kept flowing after the barge-in")
        ws.close()

    def test_barge_in_can_be_switched_off_for_one_call(self):
        sid, _, ws = live_talk(with_transcriber=True)
        got = {}
        th = threading.Thread(target=lambda: got.update(zip(("status", "out"), http(
            "/speak", "POST", {"text": "one two three", "barge_in": False}, id=sid, timeout=60))))
        th.start()
        recv_until(ws, lambda m: m.get("type") == "tts_start")
        ws.send(json.dumps({"type": "loud", "on": True}))
        script(ws, emit=[tokens_now(["hold", "on"], final=False)])
        th.join(20)
        self.assertEqual(got["out"]["status"], "done", got)
        ws.close()

    def test_words_without_sustained_energy_while_claude_speaks_are_echo(self):
        sid, _, ws = live_talk(with_transcriber=True)
        th, got = speak_in_background(sid, "one two three four five six seven eight")
        recv_until(ws, lambda m: m.get("type") == "tts_start")
        time.sleep(1.0)
        script(ws, emit=[tokens_now(["please", "stay", "silent."], end=True)])
        th.join(20)
        self.assertEqual(got["out"]["status"], "done", "echo interrupted Claude")
        time.sleep(1.0)
        self.assertEqual(texts(entries(sid), "turn"), [], "echo became a turn")
        ws.close()

    def test_after_claude_stops_every_word_counts_again(self):
        sid, _, ws = live_talk(with_transcriber=True)
        th, got = speak_in_background(sid, "one two")
        th.join(20)
        self.assertEqual(got["out"]["status"], "done")
        time.sleep(2.0)                    # past the echo tail
        script(ws, emit=[tokens_now(["Okay."], end=True)])
        es = wait_entries(sid, lambda es: texts(es, "turn"))
        self.assertEqual(texts(es, "turn"), ["Okay."])
        ws.close()

    def test_pause_stops_speech_and_is_not_a_drop(self):
        sid, _, ws = live_talk()
        th, got = speak_in_background(sid, "one two three four")
        recv_until(ws, lambda m: m.get("type") == "tts_start")
        ws.send(json.dumps({"type": "pause"}))
        recv_until(ws, lambda m: m.get("type") == "tts_stop")
        th.join(20)
        self.assertEqual((got["out"]["status"], got["out"]["reason"]), ("interrupted", "Louie paused the device"))
        ws.close()
        end = time.time() + 5
        while time.time() < end and http("/status", id=sid)[1]["device"]["connected"]:
            time.sleep(0.1)
        self.assertIsNotNone(http("/status", id=sid)[1]["device"]["paused_s"])
        out = http("/speak", "POST", {"text": "hello there"}, id=sid)[1]
        self.assertEqual((out["status"], out.get("reason")), ("not_delivered", "Louie paused the device"))
        ws = device(sid)
        es = wait_entries(sid, lambda es: any("resumed after" in t for t in texts(es, "event")))
        self.assertTrue(any("Louie paused" in t for t in texts(es, "event")), es)
        self.assertFalse(any("device disconnected" in t for t in texts(es, "event")), es)
        self.assertIsNone(http("/status", id=sid)[1]["device"]["paused_s"])
        ws.close()

    def test_page_subscribes_to_the_voice_track_through_the_relay(self):
        sid, _, ws = live_talk()
        ws.send(json.dumps({"type": "voice_request"}))
        offer = recv_until(ws, lambda m: m.get("type") == "voice_offer")
        self.assertEqual(offer["sdp"], {"type": "offer", "sdp": "v=0 fake-offer"})
        sub = FAKE.sfu["subscribes"][-1]
        pub = FAKE.sfu["adapters"][-1]["sessionId"]
        self.assertEqual(sub["tracks"], [{"location": "remote", "sessionId": pub, "trackName": "claude"}])
        ws.send(json.dumps({"type": "voice_answer", "sdp": {"type": "answer", "sdp": "v=0 fake-answer"}}))
        end = time.time() + 5
        while time.time() < end and not any(a["session"] == sub["session"] for a in FAKE.sfu["answers"]):
            time.sleep(0.1)
        answer = [a for a in FAKE.sfu["answers"] if a["session"] == sub["session"]][-1]
        self.assertEqual(answer["sessionDescription"], {"type": "answer", "sdp": "v=0 fake-answer"})
        ws.close()

    def test_ending_the_session_closes_the_voice_adapter(self):
        sid, _, ws = live_talk()
        ws.send(json.dumps({"type": "voice_request"}))
        recv_until(ws, lambda m: m.get("type") == "voice_offer")
        adapter = FAKE.sfu["adapters"][-1]["adapterId"]
        http("/stop", "POST", {}, id=sid)
        end = time.time() + 5
        while time.time() < end and adapter not in FAKE.sfu["closed"]:
            time.sleep(0.1)
        self.assertIn(adapter, FAKE.sfu["closed"])
        ws.close()


class Events(unittest.TestCase):
    def events(self, sid, key, since=0):
        return connect(f"{BASE.replace('http', 'ws')}/events?id={sid}&k={key}&since={since}", open_timeout=10)

    def recv_json(self, ev, timeout=8):
        return json.loads(ev.recv(timeout=timeout))

    def test_wrong_key_is_refused(self):
        sid, _ = create_talk()
        with self.assertRaises(Exception) as cm:
            self.events(sid, "nope")
        self.assertIn("401", str(cm.exception))

    def test_replays_past_since_then_pushes_news_but_not_claudes_own_lines(self):
        sid, key, ws = live_talk(with_transcriber=True)
        script(ws, emit=[FAKE.tokens([("First", 0), ("thought.", 0)], end=True)])
        es = wait_entries(sid, lambda es: texts(es, "turn"), timeout=10)
        ev = self.events(sid, key, since=0)
        replay = [self.recv_json(ev) for _ in range(len(es))]
        self.assertEqual([e["n"] for e in replay], [e["n"] for e in es])
        http("/note", "POST", {"text": "a note"}, id=sid)
        script(ws, emit=[FAKE.tokens([("Second", 0), ("thought.", 0)], end=True)])
        pushed = self.recv_json(ev, timeout=10)
        self.assertEqual((pushed["kind"], pushed["text"]), ("turn", "Second thought."))
        # Resuming past what was seen skips it.
        ev2 = self.events(sid, key, since=pushed["n"])
        with self.assertRaises(TimeoutError):
            ev2.recv(timeout=1.5)
        ev.close(); ev2.close(); ws.close()

    def test_listen_turns_are_held_then_sent_together_and_device_news_flushes_them(self):
        out = http("/session", "POST", {"label": "test"})[1]
        sid, key = out["id"], out["event_key"]
        ws = device(sid)
        ws.send(json.dumps({"type": "seg"}))
        wait_streams(len(FAKE.streams) or 1)
        ev = self.events(sid, key, since=10_000)
        script(ws, emit=[FAKE.tokens([("Hello", 1), ("there.", 1)], end=True),
                         FAKE.tokens([("Hi.", 2)], end=True)])
        with self.assertRaises(TimeoutError):
            ev.recv(timeout=0.8)
        got = [self.recv_json(ev, timeout=5)["text"] for _ in range(2)]
        self.assertEqual(got, ["Hello there.", "Hi."])
        script(ws, emit=[FAKE.tokens([("Wait.", 1)], end=True)])
        wait_entries(sid, lambda es: "Wait." in texts(es, "turn"))
        ws.send(json.dumps({"type": "vis", "state": "hidden"}))
        got = [self.recv_json(ev, timeout=1)["text"] for _ in range(2)]
        self.assertEqual(got[0], "Wait.", "device news must not wait behind held turns")
        self.assertIn("mic is OFF", got[1])
        ev.close(); ws.close()

    def test_session_end_closes_the_stream(self):
        sid, key, ws = live_talk()
        ev = self.events(sid, key, since=10_000)
        http("/stop", "POST", {}, id=sid)
        self.assertIn("session ended", self.recv_json(ev)["text"])
        with self.assertRaises(ConnectionClosed):
            ev.recv(timeout=5)
        ws.close()


class TalkTurns(unittest.TestCase):
    def test_turn_closes_on_the_end_marker_and_a_monologue_is_not_split(self):
        sid, _, ws = live_talk(with_transcriber=True)
        tok = lambda text, ms: {"text": text, "start_ms": ms, "is_final": True, "speaker": "0"}
        script(ws, emit=[{"tokens": [tok("First.", 0), tok(" Still", 13000), tok(" going.", 13500)]}])
        time.sleep(1.0)
        self.assertEqual(texts(entries(sid), "turn"), [], "talk mode split a monologue")
        script(ws, emit=[{"tokens": [{"text": "<end>", "is_final": True}]}])
        es = wait_entries(sid, lambda es: texts(es, "turn"), timeout=3)
        self.assertEqual(texts(es, "turn"), ["First. Still going."])
        ws.close()

    def test_a_pause_after_the_end_marker_keeps_the_turn_open(self):
        sid, _, ws = live_talk(with_transcriber=True)
        script(ws, emit=[tokens_now(["First", "thought."], end=True)])
        time.sleep(0.5)
        self.assertEqual(texts(entries(sid), "turn"), [], "delivered before the hold ran out")
        script(ws, emit=[tokens_now(["And", "another."], end=True)])
        es = wait_entries(sid, lambda es: texts(es, "turn"), timeout=5)
        self.assertEqual(texts(es, "turn"), ["First thought. And another."])
        ws.close()

    def test_speech_is_refused_while_louie_has_unread_or_unfinished_turns(self):
        sid, _, ws = live_talk(with_transcriber=True)
        seen = entries(sid)[-1]["n"]
        script(ws, emit=[FAKE.tokens([("Use", 0), ("the", 0), ("blue", 0), ("one.", 0)], end=True)])
        turn = [e for e in wait_entries(sid, lambda es: texts(es, "turn")) if e["kind"] == "turn"][-1]
        before = len(FAKE.speeches)
        status, out = http("/speak", "POST", {"text": "The red one is better.", "since": seen}, id=sid)
        self.assertEqual((status, out["status"]), (200, "not_spoken"), out)
        self.assertEqual(out["turns"], [{"n": turn["n"], "text": "Use the blue one."}])
        # Mid-sentence is refused too, whatever Claude has read.
        script(ws, emit=[FAKE.tokens([("wait", 0), ("actually", 0)], final=False)])
        end = time.time() + 5
        while time.time() < end and not http("/watch", id=sid, since=0, interval=0, wait=1)[1]["tail"]:
            time.sleep(0.1)
        status, out = http("/speak", "POST", {"text": "Okay.", "since": turn["n"]}, id=sid)
        self.assertEqual((out["status"], out["reason"], out["heard_so_far"]),
                         ("not_spoken", "Louie is talking", "wait actually"))
        self.assertEqual(len(FAKE.speeches), before, "a refused line must not reach TTS")
        ws.close()


class TurnWait(unittest.TestCase):
    def talk_device(self, sid, hold_ms=None):
        ws = connect(f"{BASE.replace('http', 'ws')}/device?k={DEV}&id={sid}", open_timeout=10)
        ws.send(json.dumps({"type": "hello", "ui": "phone", **({"hold_ms": hold_ms} if hold_ms is not None else {})}))
        return ws

    def settings(self, ws):
        return recv_until(ws, lambda m: m.get("type") == "settings")["hold_ms"]

    def test_the_device_learns_the_wait_and_claude_can_change_it(self):
        sid, _ = create_talk()
        ws = self.talk_device(sid)
        self.assertEqual(self.settings(ws), 1000)          # the relay's default (TALK_HOLD_MS here)
        self.assertEqual(http("/set", "POST", {"hold_s": 4}, id=sid), (200, {"ok": True, "hold_s": 4}))
        self.assertEqual(self.settings(ws), 4000)
        self.assertEqual(http("/status", id=sid)[1]["hold_s"], 4)
        # Claude's own change is not news to Claude.
        self.assertFalse(any("turn wait" in t for t in texts(entries(sid), "event")))
        ws.close()

    def test_louies_change_is_news(self):
        sid, _ = create_talk()
        ws = self.talk_device(sid)
        self.settings(ws)
        ws.send(json.dumps({"type": "set", "hold_ms": 5000}))
        self.assertEqual(self.settings(ws), 5000)
        es = wait_entries(sid, lambda es: any("turn wait" in t for t in texts(es, "event")))
        self.assertIn("Louie set the turn wait to 5s", " ".join(texts(es, "event")))
        ws.close()

    def test_the_devices_remembered_wait_seeds_only_an_unset_session(self):
        sid, _ = create_talk()
        ws = self.talk_device(sid, hold_ms=2500)
        self.assertEqual(self.settings(ws), 2500)
        http("/set", "POST", {"hold_s": 6}, id=sid)
        ws.close()
        # A rejoin with the old preference must not undo what was set since.
        ws = self.talk_device(sid, hold_ms=2500)
        self.assertEqual(self.settings(ws), 6000)
        ws.close()

    def test_start_can_set_it_and_bad_values_are_refused(self):
        status, out = http("/session", "POST", {"label": "test", "mode": "talk", "hold_s": 2})
        self.assertEqual(http("/status", id=out["id"])[1]["hold_s"], 2)
        self.assertEqual(http("/session", "POST", {"mode": "listen", "hold_s": 2})[0], 400)
        self.assertEqual(http("/session", "POST", {"mode": "talk", "hold_s": 30})[0], 400)
        self.assertEqual(http("/set", "POST", {"hold_s": -1}, id=out["id"])[0], 400)
        sid = create()
        self.assertEqual(http("/set", "POST", {"hold_s": 4}, id=sid)[0], 400)

    def test_no_wait_delivers_on_the_end_marker_and_a_zero_preference_counts(self):
        sid, _ = create_talk()
        ws = self.talk_device(sid, hold_ms=0)
        self.assertEqual(self.settings(ws), 0)
        n = stream_count()
        global SEG_T0
        SEG_T0 = time.time()
        ws.send(json.dumps({"type": "seg"}))
        wait_streams(n + 1)
        script(ws, emit=[tokens_now(["Quick", "one."], end=True)])
        es = wait_entries(sid, lambda es: texts(es, "turn"), timeout=0.8)
        self.assertEqual(texts(es, "turn"), ["Quick one."])
        ws.close()

    def test_a_longer_wait_holds_the_turn_longer(self):
        sid, _, ws = live_talk(with_transcriber=True)
        http("/set", "POST", {"hold_s": 3}, id=sid)
        script(ws, emit=[tokens_now(["One", "thought."], end=True)])
        time.sleep(2)
        self.assertEqual(texts(entries(sid), "turn"), [], "delivered before the longer wait ran out")
        es = wait_entries(sid, lambda es: texts(es, "turn"), timeout=4)
        self.assertEqual(texts(es, "turn"), ["One thought."])
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
