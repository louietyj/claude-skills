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

Import it and call start() to run in a background thread with its state
inspectable (test_session.py does), or run it directly to serve on :8798.
"""
import asyncio
import json
import sys
import threading

from aiohttp import WSMsgType, web

CANNED_BATCH = {"results": {"channels": [{"alternatives": [{"words": [
    {"word": "hello", "punctuated_word": "Hello", "start": 0.5, "end": 0.9, "speaker": 0},
    {"word": "there", "punctuated_word": "there.", "start": 1.0, "end": 1.3, "speaker": 0},
    {"word": "hi", "punctuated_word": "Hi.", "start": 2.0, "end": 2.2, "speaker": 1},
]}]}]}}


class Fake:
    def __init__(self):
        self.streams = []      # one dict per live connection
        self.batches = []      # one dict per POST

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
        rec = {"config": None, "audio": [], "text": [], "closed": None, "on_close": []}
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


def start(port=8798):
    fake = Fake()
    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.router.add_route("GET", "/transcribe-websocket", fake.live)
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
