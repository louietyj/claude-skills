"""Exercise the relay against `wrangler dev` and a fake Google (OAuth token
endpoint + FCM send): token gates, pairing, the request lifecycle Claude and
the phone each see, URL replacement, expiry, and push failures.

    python worker/test_worker.py
"""
import base64
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

HERE = Path(__file__).resolve().parent
PORT, FAKE_PORT = 8795, 8796
BASE = f"http://127.0.0.1:{PORT}"
FAKE_BASE = f"http://127.0.0.1:{FAKE_PORT}"
CLI, DEV = "clitok", "devtok"
SA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
TUNNEL = "https://abc123.hostc.app/tok"
WRANGLER = None


class FakeGoogle(BaseHTTPRequestHandler):
    # workerd pools connections; an HTTP/1.0 server closing them under it surfaces
    # as "Network connection lost" on the next fetch.
    protocol_version = "HTTP/1.1"
    tokens_issued = 0
    sent = []          # FCM message bodies, oldest first
    jwt_claims = []

    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        body = self.rfile.read(int(self.headers["content-length"]))
        if self.path == "/token":
            assertion = urllib.parse.parse_qs(body.decode())["assertion"][0]
            head, claims, sig = assertion.split(".")
            pad = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
            SA_KEY.public_key().verify(pad(sig), f"{head}.{claims}".encode(),
                                       padding.PKCS1v15(), hashes.SHA256())
            FakeGoogle.jwt_claims.append(json.loads(pad(claims)))
            FakeGoogle.tokens_issued += 1
            return self.reply(200, {"access_token": "fake-access", "expires_in": 3600})
        if self.path == "/v1/projects/test-proj/messages:send":
            if self.headers.get("authorization") != "Bearer fake-access":
                return self.reply(401, {"error": "bad auth"})
            msg = json.loads(body)["message"]
            if msg["token"] == "stale":
                return self.reply(404, {"error": {"status": "NOT_FOUND", "details": [{"errorCode": "UNREGISTERED"}]}})
            FakeGoogle.sent.append(msg)
            return self.reply(200, {"name": "projects/test-proj/messages/1"})
        self.reply(404, {"error": "fake: no route"})


class FakeServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # workerd drops idle pooled connections; that is not a test failure.
        if not isinstance(sys.exc_info()[1], ConnectionResetError):
            super().handle_error(request, client_address)


def http(path, method="GET", body=None, token=CLI):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"content-type": "application/json"}
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def pair(fcm_token="fcm-1"):
    code = http("/pair", "POST", {})[1]["code"]
    status, out = http("/pair/claim", "POST", {"code": code, "fcm_token": fcm_token}, token=None)
    assert status == 200, out
    return out["k"]


def ask(site="chase.com", reason="pay the card", url=TUNNEL, **extra):
    return http("/request", "POST", {"site": site, "reason": reason, "url": url, **extra})


def setUpModule():
    global WRANGLER
    server = FakeServer(("127.0.0.1", FAKE_PORT), FakeGoogle)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    tmp = Path(tempfile.mkdtemp(prefix="cred-bridge-test-"))
    sa = {
        "type": "service_account", "project_id": "test-proj",
        "client_email": "fcm@test-proj.iam.gserviceaccount.com", "token_uri": f"{FAKE_BASE}/token",
        "private_key": SA_KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption()).decode(),
    }
    env_file = tmp / "test.env"
    env_file.write_text(f"CLI_TOKEN={CLI}\nDEVICE_TOKEN={DEV}\nFCM_URL={FAKE_BASE}\n"
                        f"FCM_SA_JSON='{json.dumps(sa)}'\n")
    cmd = f'npx wrangler dev --port {PORT} --persist-to "{tmp / "state"}" --env-file "{env_file}"'
    WRANGLER = subprocess.Popen(cmd, cwd=HERE, shell=True, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    end = time.time() + 90
    while time.time() < end:
        try:
            if http("/health", token=None)[0] == 200:
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


# Unpaired behaviour runs first (unittest orders classes and methods by name).
class A_Unpaired(unittest.TestCase):
    def test_health_names_secrets_without_values(self):
        status, out = http("/health", token=None)
        self.assertEqual(status, 200)
        self.assertEqual(out["secrets"], {"CLI_TOKEN": True, "DEVICE_TOKEN": True, "FCM_SA_JSON": True})

    def test_request_before_pairing_is_refused(self):
        status, out = ask()
        self.assertEqual(status, 409)
        self.assertIn("pair", out["error"])


class B_Gates(unittest.TestCase):
    def test_tokens_open_only_their_own_routes(self):
        self.assertEqual(http("/pair", "POST", {}, token=None)[0], 401)
        self.assertEqual(http("/pair", "POST", {}, token=DEV)[0], 401)
        self.assertEqual(http("/request", "POST", {}, token=DEV)[0], 401)
        self.assertEqual(http("/device", "PUT", {"fcm_token": "x"}, token=CLI)[0], 401)
        self.assertEqual(http("/device/request/0123456789abcdef", token=CLI)[0], 401)
        self.assertEqual(http("/device/request/0123456789abcdef", token=None)[0], 401)

    def test_pairing_code_is_single_use_and_wrong_codes_fail(self):
        code = http("/pair", "POST", {})[1]["code"]
        wrong = str((int(code) + 1) % 1_000_000).zfill(6)
        self.assertEqual(http("/pair/claim", "POST", {"code": wrong, "fcm_token": "f"}, token=None)[0], 401)
        status, out = http("/pair/claim", "POST", {"code": code, "fcm_token": "fcm-1"}, token=None)
        self.assertEqual((status, out["k"]), (200, DEV))
        self.assertEqual(http("/pair/claim", "POST", {"code": code, "fcm_token": "f"}, token=None)[0], 401)


class C_Requests(unittest.TestCase):
    def setUp(self):
        pair("fcm-1")

    def test_bad_requests_are_rejected(self):
        for body in ({"site": "https://chase.com/login"}, {"site": "chase"}, {"reason": ""},
                     {"reason": "x" * 201}, {"url": "ftp://x"}):
            with self.subTest(body=body):
                self.assertEqual(ask(**body)[0], 400)

    def test_push_carries_only_the_rid(self):
        status, out = ask(site="Chase.COM")
        self.assertEqual(status, 200, out)
        msg = FakeGoogle.sent[-1]
        self.assertEqual(msg["token"], "fcm-1")
        self.assertEqual(msg["data"], {"rid": out["rid"]})
        self.assertEqual(msg["android"]["priority"], "high")
        claims = FakeGoogle.jwt_claims[-1]
        self.assertEqual(claims["scope"], "https://www.googleapis.com/auth/firebase.messaging")
        self.assertEqual(claims["iss"], "fcm@test-proj.iam.gserviceaccount.com")

    def test_access_token_is_cached(self):
        ask()
        before = FakeGoogle.tokens_issued
        ask()
        ask()
        self.assertEqual(FakeGoogle.tokens_issued, before)

    def test_lifecycle_sent(self):
        rid = ask()[1]["rid"]
        status, mine = http(f"/request/{rid}")
        self.assertEqual((status, mine["status"], mine["site"]), (200, "pending", "chase.com"))
        self.assertNotIn("url", mine)

        status, theirs = http(f"/device/request/{rid}", token=DEV)
        self.assertEqual((status, theirs["status"], theirs["url"], theirs["reason"]),
                         (200, "delivered", TUNNEL, "pay the card"))
        self.assertEqual(http(f"/request/{rid}")[1]["status"], "delivered")

        self.assertEqual(http(f"/device/request/{rid}?seen=opened", token=DEV)[1]["status"], "opened")
        # A late push fetch does not move it back.
        self.assertEqual(http(f"/device/request/{rid}", token=DEV)[1]["status"], "opened")
        self.assertEqual(http(f"/request/{rid}")[1]["status"], "opened")

        self.assertEqual(http(f"/device/request/{rid}/sent", "POST", {}, token=DEV)[0], 200)
        self.assertEqual(http(f"/request/{rid}")[1]["status"], "sent")
        self.assertEqual(http(f"/device/request/{rid}/declined", "POST", {}, token=DEV)[0], 409)

    def test_lifecycle_declined(self):
        rid = ask()[1]["rid"]
        self.assertEqual(http(f"/device/request/{rid}/declined", "POST", {}, token=DEV)[0], 200)
        self.assertEqual(http(f"/request/{rid}")[1]["status"], "declined")
        self.assertEqual(http(f"/request/{rid}", "PATCH", {"url": TUNNEL})[0], 409)

    def test_url_can_be_replaced_while_open(self):
        rid = ask()[1]["rid"]
        new = "https://def456.hostc.app/tok"
        self.assertEqual(http(f"/request/{rid}", "PATCH", {"url": new})[0], 200)
        self.assertEqual(http(f"/device/request/{rid}", token=DEV)[1]["url"], new)

    def test_unknown_rid(self):
        self.assertEqual(http("/request/0123456789abcdef")[0], 404)
        self.assertEqual(http("/request/nothex")[0], 404)

    def test_stale_fcm_token_is_reported(self):
        self.assertEqual(http("/device", "PUT", {"fcm_token": "stale"}, token=DEV)[0], 200)
        status, out = ask()
        self.assertEqual(status, 502)
        self.assertIn("stale", out["error"])
        self.assertEqual(http(f"/request/{out['rid']}")[1]["status"], "undelivered")

    def test_requests_expire(self):
        rid = ask(ttl_s=30)[1]["rid"]
        time.sleep(31)
        self.assertEqual(http(f"/request/{rid}")[1]["status"], "expired")
        self.assertEqual(http(f"/device/request/{rid}", token=DEV)[1]["status"], "expired")
        self.assertEqual(http(f"/device/request/{rid}/sent", "POST", {}, token=DEV)[0], 409)


if __name__ == "__main__":
    unittest.main(argv=sys.argv[:1], verbosity=2)
