"""A fake Telnyx, for testing the relay's phone line without placing calls.

REST (`/v2/calls`, `/v2/calls/{id}/actions/hangup`) records each request and
answers as Telnyx does. Call progress goes back to the relay as webhooks
(`call.initiated`, `call.answered`, `call.hangup`) on the call's webhook_url,
signed with Ed25519 over `{timestamp}|{body}` as Telnyx signs them, with the
fixed test key below: hand the relay PUBLIC_KEY as TELNYX_PUBLIC_KEY.

Nothing answers by itself. Tests drive each call (answer, hang_up), and so
does dev/phone.html through /fake/*: it is the handset, served at /phone.

Import it and call start() to run in a background thread with its state
inspectable (test_session.py does), or run it directly to serve on :8797.
"""
import asyncio
import base64
import itertools
import json
import sys
import threading
import time
import uuid
from pathlib import Path

import aiohttp
from aiohttp import web
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
PUBLIC_KEY = base64.b64encode(KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
PHONE_PAGE = Path(__file__).with_name("phone.html")


class Fake:
    def __init__(self):
        self.calls = {}        # call_control_id -> {body, auth, state, events}
        self.hangups = []      # call_control_ids the relay hung up
        self.webhooks = []     # every webhook sent: {event_type, status}
        self.loop = None
        self._ids = itertools.count(1)
        self._client = None

    # --- driving calls, from a test's thread --------------------------------------

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(15)

    def answer(self, ccid):
        self.run(self._answer(ccid))

    def hang_up(self, ccid, cause="normal_clearing", source="callee"):
        self.run(self._hang_up(ccid, cause, source))

    def last(self):
        return list(self.calls)[-1]

    def post_signed(self, url, body, *, ts=None, sign_ts=None, key=KEY):
        """Sends a webhook body as Telnyx would, or a forged one (another key, a stale or mismatched stamp)."""
        return self.run(self._post_signed(url, body, ts=ts, sign_ts=sign_ts, key=key))

    # --- webhooks --------------------------------------------------------------------

    async def _post_signed(self, url, body, *, ts=None, sign_ts=None, key=KEY):
        ts = str(int(time.time()) if ts is None else ts)
        sig = key.sign(f"{sign_ts or ts}|{body}".encode())
        self._client = self._client or aiohttp.ClientSession()
        async with self._client.post(url, data=body, headers={
                "content-type": "application/json", "telnyx-timestamp": ts,
                "telnyx-signature-ed25519": base64.b64encode(sig).decode()}) as r:
            return r.status

    async def _webhook(self, ccid, event_type, **extra):
        call = self.calls[ccid]
        b = call["body"]
        payload = {"call_control_id": ccid, "call_leg_id": call["leg"], "call_session_id": call["session"],
                   "client_state": b.get("client_state"), "connection_id": b.get("connection_id"),
                   "from": b.get("from"), "to": b.get("to"), **extra}
        body = json.dumps({"data": {"event_type": event_type, "id": str(uuid.uuid4()), "record_type": "event",
                                    "occurred_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                    "payload": payload}, "meta": {"attempt": 1}})
        status = await self._post_signed(b["webhook_url"], body)
        self.webhooks.append({"event_type": event_type, "status": status})
        call["events"].append(event_type)

    async def _answer(self, ccid):
        call = self.calls[ccid]
        if call["state"] != "ringing":
            return
        call["state"] = "up"
        await self._webhook(ccid, "call.answered", state="answered")

    async def _hang_up(self, ccid, cause, source):
        call = self.calls[ccid]
        if call["state"] == "ended":
            return
        call["state"] = "ended"
        await self._webhook(ccid, "call.hangup", state="hangup", hangup_cause=cause, hangup_source=source)

    # --- REST ------------------------------------------------------------------------

    async def dial(self, request):
        body = await request.json()
        n = next(self._ids)
        ccid = f"v3:fake-call-{n}"
        self.calls[ccid] = {"body": body, "auth": request.headers.get("Authorization"), "state": "ringing",
                            "events": [], "leg": f"leg-{n}", "session": f"session-{n}", "at": time.time()}

        async def initiated():
            await asyncio.sleep(0.05)         # after the relay has its answer
            await self._webhook(ccid, "call.initiated", state="bridging", direction="outgoing")
        asyncio.ensure_future(initiated())
        return web.json_response({"data": {"call_control_id": ccid, "call_leg_id": f"leg-{n}",
                                           "call_session_id": f"session-{n}", "is_alive": False,
                                           "record_type": "call"}})

    async def hangup_action(self, request):
        ccid = request.match_info["ccid"]
        if ccid not in self.calls:
            return web.json_response({"errors": [{"code": "90018", "title": "Call has already ended"}]}, status=422)
        self.hangups.append(ccid)
        asyncio.ensure_future(self._hang_up(ccid, "normal_clearing", "caller"))
        return web.json_response({"data": {"result": "ok"}})

    # --- the handset page (dev/phone.html) ---------------------------------------------

    async def phone_page(self, request):
        return web.FileResponse(PHONE_PAGE)

    async def list_calls(self, request):
        return web.json_response([{"id": k, "to": v["body"].get("to"), "state": v["state"]}
                                  for k, v in self.calls.items() if v["state"] != "ended"])

    async def control(self, request):
        ccid, action = request.match_info["ccid"], request.match_info["action"]
        if ccid not in self.calls:
            return web.json_response({"error": "no such call"}, status=404)
        if action == "answer":
            await self._answer(ccid)
        elif action == "hangup":
            await self._hang_up(ccid, "normal_clearing", "callee")
        elif action == "drop":                # signal lost: Telnyx sees the far end vanish
            await self._hang_up(ccid, "normal_clearing", "unknown")
        return web.json_response({"ok": True})


def start(port=8797):
    fake = Fake()
    app = web.Application()
    app.router.add_route("POST", "/v2/calls", fake.dial)
    app.router.add_route("POST", "/v2/calls/{ccid}/actions/hangup", fake.hangup_action)
    app.router.add_route("GET", "/phone", fake.phone_page)
    app.router.add_route("GET", "/fake/calls", fake.list_calls)
    app.router.add_route("POST", "/fake/{action}/{ccid}", fake.control)
    ready = threading.Event()

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        fake.loop = loop
        runner = web.AppRunner(app)
        loop.run_until_complete(runner.setup())
        loop.run_until_complete(web.TCPSite(runner, "127.0.0.1", port).start())
        ready.set()
        loop.run_forever()

    threading.Thread(target=run, daemon=True).start()
    ready.wait(10)
    return fake


if __name__ == "__main__":
    start(int(sys.argv[1]) if len(sys.argv) > 1 else 8797)
    print(f"fake Telnyx on :8797; the handset is http://127.0.0.1:8797/phone\nTELNYX_PUBLIC_KEY={PUBLIC_KEY}")
    threading.Event().wait()
