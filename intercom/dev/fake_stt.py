"""Fake transcribers for testing the relay without spending minutes.

Live, Soniox-shaped (`/transcribe-websocket`): the first text frame is the
config and is recorded. After that, binary frames that parse as JSON are
scripts, and everything else is audio, only recorded.
    {"emit": [<soniox message>, ...]}   sent back verbatim, in order
    {"close": 1011}                     drop the connection with that code
    {"on_close": [<soniox message>]}    held back until the end-of-stream empty
                                        frame, as Soniox flushes its last finals then
Text frames after the config (keepalives) are recorded. An empty frame ends the
stream: the held messages go out, then {"finished": true}, then close 1000.

Batch, Deepgram-shaped (`POST /v1/listen`): returns CANNED_BATCH and records the body.

TTS, Soniox-shaped (`/tts-websocket`): records the config and auth header, then
speaks the text one word per TTS_WORD_S of audio, each word its own audio frame
with character timestamps, paced so a test can cut in. `cancel` stops it.

SFU, Cloudflare Realtime-shaped (`/v1/apps/{app}/...`): creating a WebSocket
adapter dials its endpoint, as the real one does, and records the payload size
and arrival time of every packet the relay sends. Sessions, track requests,
renegotiation and adapter closes are recorded.

Import it and call start() to run in a background thread with its state
inspectable (test_session.py does), or run it directly to serve on :8798.
"""
import asyncio
import base64
import json
import sys
import threading
import time

import aiohttp
from aiohttp import WSMsgType, web

CANNED_BATCH = {"results": {"channels": [{"alternatives": [{"words": [
    {"word": "hello", "punctuated_word": "Hello", "start": 0.5, "end": 0.9, "speaker": 0},
    {"word": "there", "punctuated_word": "there.", "start": 1.0, "end": 1.3, "speaker": 0},
    {"word": "hi", "punctuated_word": "Hi.", "start": 2.0, "end": 2.2, "speaker": 1},
]}]}]}}


TTS_WORD_S = 0.5
TTS_PACE_S = 0.15            # wall time between word frames


class Fake:
    def __init__(self):
        self.streams = []      # one dict per live connection
        self.batches = []      # one dict per POST
        self.speeches = []     # one dict per TTS connection
        self.sfu = {"adapters": [], "packets": [], "sessions": 0, "subscribes": [],
                    "answers": [], "closed": []}
        self._client = None

    def tokens(self, words, *, final=True, end=False, lead=False):
        """A Soniox response. words: [(text, speaker), ...]; tokens carry their
        own leading space, as Soniox's do (lead=True: the first one too, for a
        message continuing a sentence). end=True appends the <end> marker."""
        toks = [{"text": (" " if i or lead else "") + w, "start_ms": 100 * i, "end_ms": 100 * i + 80,
                 "is_final": final, "speaker": str(s), "confidence": 0.9}
                for i, (w, s) in enumerate(words)]
        if end:
            toks.append({"text": "<end>", "is_final": True})
        return {"tokens": toks, "final_audio_proc_ms": 0, "total_audio_proc_ms": 0}

    async def batch(self, request):
        body = await request.read()
        self.batches.append({"query": request.query_string, "len": len(body),
                             "body": body, "auth": request.headers.get("Authorization")})
        return web.json_response(CANNED_BATCH)

    async def live(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        rec = {"config": None, "audio": [], "text": [], "closed": None, "on_close": [],
               "auth": request.headers.get("Authorization")}
        self.streams.append(rec)

        async def finish():
            for out in rec["on_close"]:
                await asyncio.sleep(0.3)          # finals trail the end-of-stream frame
                await ws.send_str(json.dumps(out))
            await ws.send_str(json.dumps({"tokens": [], "finished": True}))
            rec["closed"] = 1000
            await ws.close(code=1000)

        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                if rec["config"] is None:
                    rec["config"] = json.loads(msg.data)
                elif msg.data == "":
                    await finish()
                    break
                else:
                    rec["text"].append(msg.data)
            elif msg.type == WSMsgType.BINARY:
                if not msg.data:
                    await finish()
                    break
                try:
                    script = json.loads(msg.data)
                except ValueError:
                    rec["audio"].append(msg.data)
                    continue
                for out in script.get("emit", []):
                    await ws.send_str(json.dumps(out))
                rec["on_close"].extend(script.get("on_close", []))
                if "close" in script:
                    rec["closed"] = script["close"]
                    await ws.close(code=script["close"])
                    break
        return ws

    async def tts(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        rec = {"config": None, "text": "", "auth": request.headers.get("Authorization"),
               "cancelled": False, "words_sent": 0}
        self.speeches.append(rec)
        speaking = None

        async def speak(sid, text):
            rate = rec["config"].get("sample_rate", 24000)
            pcm = base64.b64encode(b"\0" * int(TTS_WORD_S * rate * 2)).decode()
            t = 0.0
            for i, word in enumerate(text.split()):
                chars = list(word) + ([" "] if i < len(text.split()) - 1 else [])
                step = TTS_WORD_S / len(chars)
                ts = {"characters": chars,
                      "character_start_times_seconds": [round(t + k * step, 3) for k in range(len(chars))],
                      "character_end_times_seconds": [round(t + (k + 1) * step, 3) for k in range(len(chars))]}
                await ws.send_str(json.dumps({"stream_id": sid, "audio": pcm, "audio_end": False, "timestamps": ts}))
                rec["words_sent"] += 1
                t += TTS_WORD_S
                await asyncio.sleep(TTS_PACE_S)
            await ws.send_str(json.dumps({"stream_id": sid, "audio": "", "audio_end": True}))
            await ws.send_str(json.dumps({"stream_id": sid, "terminated": True}))

        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            m = json.loads(msg.data)
            if rec["config"] is None:
                rec["config"] = m
            elif m.get("cancel"):
                rec["cancelled"] = True
                if speaking:
                    speaking.cancel()
                await ws.send_str(json.dumps({"stream_id": m["stream_id"], "terminated": True}))
            elif "text" in m:
                rec["text"] += m["text"]
                if m.get("text_end"):
                    speaking = asyncio.ensure_future(speak(m["stream_id"], rec["text"]))
        return ws

    # --- SFU -------------------------------------------------------------------------

    @staticmethod
    def payload_len(packet):
        """Length of `bytes payload = 5` in the adapter's protobuf Packet."""
        assert packet[0] == (5 << 3) | 2, packet[:4]
        n, shift, i = 0, 0, 1
        while True:
            b = packet[i]
            n |= (b & 0x7F) << shift
            i += 1
            if b < 0x80:
                return n if len(packet) - i == n else -1
            shift += 7

    async def adapter_new(self, request):
        body = await request.json()
        t = body["tracks"][0]
        n = len(self.sfu["adapters"]) + 1
        rec = {"auth": request.headers.get("Authorization"), "track": t, "adapterId": f"ad{n}",
               "sessionId": f"pub{n}"}
        self.sfu["adapters"].append(rec)
        self._client = self._client or aiohttp.ClientSession()
        ws = await self._client.ws_connect(t["endpoint"])       # the real SFU dials before answering

        async def drain():
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    self.sfu["packets"].append({"adapter": rec["adapterId"], "t": time.time(),
                                                "len": self.payload_len(msg.data), "size": len(msg.data)})
        asyncio.ensure_future(drain())
        return web.json_response({"tracks": [{"trackName": t["trackName"], "adapterId": rec["adapterId"],
                                              "sessionId": rec["sessionId"], "endpoint": t["endpoint"]}]})

    async def adapter_close(self, request):
        body = await request.json()
        self.sfu["closed"] += [t["adapterId"] for t in body["tracks"]]
        return web.json_response({"tracks": body["tracks"]})

    async def session_new(self, request):
        self.sfu["sessions"] += 1
        return web.json_response({"sessionId": f"sub{self.sfu['sessions']}"})

    async def tracks_new(self, request):
        body = await request.json()
        self.sfu["subscribes"].append({"session": request.match_info["sid"], "tracks": body["tracks"]})
        return web.json_response({"requiresImmediateRenegotiation": True,
                                  "sessionDescription": {"type": "offer", "sdp": "v=0 fake-offer"},
                                  "tracks": [{"mid": "0", **body["tracks"][0]}]})

    async def renegotiate(self, request):
        body = await request.json()
        self.sfu["answers"].append({"session": request.match_info["sid"], **body})
        return web.json_response({})


def start(port=8798):
    fake = Fake()
    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.router.add_route("GET", "/transcribe-websocket", fake.live)
    app.router.add_route("GET", "/tts-websocket", fake.tts)
    app.router.add_route("POST", "/v1/apps/{app}/adapters/websocket/new", fake.adapter_new)
    app.router.add_route("POST", "/v1/apps/{app}/adapters/websocket/close", fake.adapter_close)
    app.router.add_route("POST", "/v1/apps/{app}/sessions/new", fake.session_new)
    app.router.add_route("POST", "/v1/apps/{app}/sessions/{sid}/tracks/new", fake.tracks_new)
    app.router.add_route("PUT", "/v1/apps/{app}/sessions/{sid}/renegotiate", fake.renegotiate)
    app.router.add_route("POST", "/v1/listen", fake.batch)
    ready = threading.Event()

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        runner = web.AppRunner(app)
        loop.run_until_complete(runner.setup())
        loop.run_until_complete(web.TCPSite(runner, "127.0.0.1", port).start())
        ready.set()
        loop.run_forever()

    threading.Thread(target=run, daemon=True).start()
    ready.wait(10)
    return fake


if __name__ == "__main__":
    start(int(sys.argv[1]) if len(sys.argv) > 1 else 8798)
    print("fake transcribers on :8798 -- ctrl-c to stop")
    threading.Event().wait()
