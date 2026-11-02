"""Stand-ins for what a button press reaches besides Telnyx: Anthropic's
routine fire endpoint (`/v1/claude_code/routines/{id}/fire`), which records each
fire and answers as the real one does, and the standing keyterm list
(`/keyterms.txt`, the relay's KEYTERMS_URL).

Set `fail` to an HTTP status to make fires fail. Import it and call start() to
run in a background thread (test_session.py does).
"""
import asyncio
import itertools
import threading

from aiohttp import web

KEYTERMS = "Fragomen, Shizuku\nTelnyx\n\nfragomen\n"


class Fake:
    def __init__(self):
        self.fires = []        # {routine, auth, beta, version, body}
        self.fail = None
        self._ids = itertools.count(1)

    async def fire(self, request):
        self.fires.append({"routine": request.match_info["rid"], "auth": request.headers.get("Authorization"),
                           "beta": request.headers.get("anthropic-beta"),
                           "version": request.headers.get("anthropic-version"), "body": await request.json()})
        if self.fail:
            return web.json_response({"type": "error", "error": {"type": "authentication_error",
                                                                 "message": "invalid token"}}, status=self.fail)
        n = next(self._ids)
        return web.json_response({"type": "routine_fire", "claude_code_session_id": f"session_fake{n}",
                                  "claude_code_session_url": f"https://claude.ai/code/session_fake{n}"})

    async def keyterms(self, request):
        return web.Response(text=KEYTERMS)


def start(port=8796):
    fake = Fake()
    app = web.Application()
    app.router.add_route("POST", "/v1/claude_code/routines/{rid}/fire", fake.fire)
    app.router.add_route("GET", "/keyterms.txt", fake.keyterms)
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
