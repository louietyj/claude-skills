"""Call Telnyx's API with the key in dev/telnyx.key (gitignored), for setting up
the phone line from a checkout. Prints the JSON reply.

    python dev/telnyx_api.py GET /v2/phone_numbers
    python dev/telnyx_api.py POST /v2/call_control_applications '{"application_name": "..."}'
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

KEY = (Path(__file__).with_name("telnyx.key")).read_text().strip()


def call(method, path, body=None):
    req = urllib.request.Request(f"https://api.telnyx.com{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {KEY}", "content-type": "application/json",
                                          "accept": "application/json", "user-agent": "intercom-setup/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


if __name__ == "__main__":
    method, path = sys.argv[1], sys.argv[2]
    status, out = call(method, path, json.loads(sys.argv[3]) if len(sys.argv) > 3 else None)
    print(status)
    print(json.dumps(out, indent=2))
