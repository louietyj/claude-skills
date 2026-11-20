#!/usr/bin/env python3
"""Track a piece by IMb through USPS Informed Visibility, and compare two snapshots.

  iv_track.py fetch <imb> [--tries N]   best of N lookups, printed in the (Tracking).json format
  iv_track.py compare <old|-> <new>     what changed between two snapshots, as JSON
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

import requests

AUTH_URL = "https://services.usps.com/oauth/authenticate"
PIECE_URL = "https://iv.usps.com/ivws_api/informedvisapi/api/mt/get/piece/imb/{}"
STORE_ENV = "/_devfiles/claude-skills/usps-ops/usps-iv.env"
LOCAL_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "usps-iv.env")
KEYS = ("USPS_USERNAME", "USPS_PASSWORD", "USPS_CLIENT_ID")


def _parse_env(path):
    out = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip("'\"")
    return out


def load_credentials():
    """Environment first, then usps-iv.env beside SKILL.md, then the cfs store's copy."""
    creds = {k: os.environ.get(k, "") for k in KEYS}
    if not all(creds.values()):
        if os.path.exists(LOCAL_ENV):
            file_creds = _parse_env(LOCAL_ENV)
        else:
            with tempfile.TemporaryDirectory() as tmp:
                dest = os.path.join(tmp, "usps-iv.env")
                subprocess.run(["cfs", "download", STORE_ENV, "--to", dest], check=True,
                               stdout=subprocess.DEVNULL)
                file_creds = _parse_env(dest)
        creds = {k: creds[k] or file_creds.get(k, "") for k in KEYS}
    missing = [k for k in KEYS if not creds[k]]
    if missing:
        sys.exit(f"Missing {', '.join(missing)} (env, {LOCAL_ENV} or cfs {STORE_ENV})")
    return creds


def get_token(creds):
    resp = requests.post(AUTH_URL, timeout=30, json={
        "username": creds["USPS_USERNAME"],
        "password": creds["USPS_PASSWORD"],
        "grant_type": "authorization",
        "response_type": "token",
        "scope": "user.info.ereg,iv1.apis",
        "client_id": creds["USPS_CLIENT_ID"],
    })
    resp.raise_for_status()
    return resp.json()["access_token"]


def fetch(imb, tries):
    """IV's answers vary from call to call, so keep the one with the most data, as the app does."""
    token = get_token(load_credentials())
    headers = {"Authorization": f"Bearer {token}"}
    responses = [requests.get(PIECE_URL.format(imb), headers=headers, timeout=30)
                 for _ in range(tries)]
    ok = [r.json() for r in responses if r.status_code == 200]
    if not ok:
        r = responses[0]
        sys.exit(f"All {tries} lookups failed; first: HTTP {r.status_code} {r.text[:500]}")
    return max(ok, key=lambda j: len(json.dumps(j)))


def dumps(obj):
    """The (Tracking).json format: 4-space indent, CRLF, trailing newline."""
    return json.dumps(obj, indent=4).replace("\n", "\r\n") + "\r\n"


def _scan_key(scan):
    return json.dumps(scan, sort_keys=True)


def _describe(scan):
    return f"{scan.get('scan_date_time')} {scan.get('scan_event_code')} " \
           f"{scan.get('scan_facility_name')} ({scan.get('mail_phase')})"


def compare(old, new):
    """verdict: empty (nothing to save), first, same, changed (nothing lost) or lost."""
    new_data = (new or {}).get("data") or {}
    old_data = (old or {}).get("data") or {}
    new_scans = new_data.get("scans") or []
    old_scans = old_data.get("scans") or []
    scans = sorted(new_scans, key=lambda s: s.get("scan_date_time") or "")
    report = {
        "scans": len(new_scans),
        "latest_scan": _describe(scans[-1]) if scans else None,
        "expected_delivery_date": new_data.get("expected_delivery_date"),
        "anticipated_delivery_date": new_data.get("anticipated_delivery_date"),
        "message": (new or {}).get("message"),
    }
    if not new_data:
        return {"verdict": "empty", **report}
    if not old_data:
        return {"verdict": "first", **report}

    new_keys = {_scan_key(s) for s in new_scans}
    old_keys = {_scan_key(s) for s in old_scans}
    lost = [_describe(s) for s in old_scans if _scan_key(s) not in new_keys]
    lost += [f"{k}: {old_data[k]!r} -> {new_data.get(k)!r}" for k in old_data
             if k != "scans" and old_data[k] and not new_data.get(k)]
    gained = [_describe(s) for s in new_scans if _scan_key(s) not in old_keys]
    if lost:
        verdict = "lost"
    elif new == old:
        verdict = "same"
    else:
        verdict = "changed"
    return {"verdict": verdict, "lost": lost, "gained": gained, **report}


def _load(path):
    if path == "-" or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("imb")
    f.add_argument("--tries", type=int, default=5)
    c = sub.add_parser("compare")
    c.add_argument("old", help="the saved snapshot, or - if there is none yet")
    c.add_argument("new")
    args = ap.parse_args()

    if args.cmd == "fetch":
        sys.stdout.buffer.write(dumps(fetch(args.imb, args.tries)).encode())
    else:
        print(json.dumps(compare(_load(args.old), _load(args.new)), indent=2))


if __name__ == "__main__":
    main()
