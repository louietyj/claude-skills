#!/usr/bin/env python3
"""Rebuild the local-mcps tool index and put it back on the durable filesystem.

Runs on the Claude Code cloud box, from a 5-hourly routine whose real job is
refreshing a session limit. This is a passenger on that trip: it must never be
the reason the trip fails, so every failure here is reported and swallowed, and
the exit status is always 0.

    lmcps-refresh

Deployed by the `Cron` cloud environment's setup script, which writes this file
to /usr/local/bin/lmcps-refresh. Edit the canonical copy in the repo, then paste
it there; `builtBy` carries a hash of this file, so a paste that has drifted
shows up in `lmcps servers`.

It deliberately does NOT use cfs.py. `cfs` enforces read-before-write via `rev`
to stop a model clobbering content it has not read; the index is derived and
fully regenerable, so last-writer-wins is correct and the rev dance would buy a
read-merge-write-retry loop for nothing. All it borrows is credentials.json.
"""
import base64
import glob
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.dropboxapi.com"
CONTENT = "https://content.dropboxapi.com"

CONFIG_PATH = "/mcp.json"
CATALOG_PATH = "/mcp-catalog.json"

# Ceilings for a wedged server, not expectations: most finish in seconds, and
# an HTTP server that spawns nothing takes about one.
#
# 120s was too tight for an npx server on a cold box. mcp-google-maps installs
# ~60 packages before it can answer, and the deadline kept landing mid-install
# -- the tell was npm's own deprecation warnings arriving as the server's last
# words. Nobody waits on a cron, so buy the install time rather than pinning
# the version and pre-warming the npx cache at environment build, which is
# faster but has to be kept in sync to stay faster.
PER_SERVER_TIMEOUT = 420
OVERALL_TIMEOUT = 900


def log(msg):
    print(f"lmcps-refresh: {msg}", file=sys.stderr)


def newest(pattern):
    """Skills sync to $HOME/.claude/skills/synced/<uuid>_<uuid>/<skill>/ and more
    than one bucket can exist, so pick by mtime rather than glob order."""
    hits = sorted(glob.glob(pattern), key=lambda p: os.stat(p).st_mtime, reverse=True)
    return hits[0] if hits else None


def find(*patterns):
    for pattern in patterns:
        hit = newest(os.path.expandvars(os.path.expanduser(pattern)))
        if hit:
            return hit
    return None


def access_token(creds):
    """Mint one and throw it away. cfs.py caches because it runs many commands
    per conversation; this runs once, on a box that is discarded afterwards."""
    body = urllib.parse.urlencode(
        {"grant_type": "refresh_token", "refresh_token": creds["refresh_token"]}
    ).encode()
    basic = base64.b64encode(
        f"{creds['app_key']}:{creds['app_secret']}".encode()
    ).decode()
    req = urllib.request.Request(
        f"{API}/oauth2/token", data=body,
        headers={"Authorization": f"Basic {basic}",
                 "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["access_token"]


def api_arg(payload):
    """Dropbox-API-Arg must be HTTP-header-safe: escape non-ASCII."""
    return json.dumps(payload, ensure_ascii=True)


def download(token, path):
    """File contents, or None if it is not there yet -- which is the normal
    state of the index on the very first run."""
    req = urllib.request.Request(
        f"{CONTENT}/2/files/download", data=b"",
        headers={"Authorization": f"Bearer {token}",
                 "Dropbox-API-Arg": api_arg({"path": path})}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        if exc.code == 409 and "not_found" in detail:
            return None
        raise RuntimeError(f"download {path} failed ({exc.code}): {detail[:300]}")


def upload(token, path, text):
    """mode=overwrite updates the existing file rather than replacing it, which
    is what keeps the share link the claude.ai side reads through alive."""
    req = urllib.request.Request(
        f"{CONTENT}/2/files/upload", data=text.encode("utf-8"),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/octet-stream",
                 "Dropbox-API-Arg": api_arg({"path": path, "mode": "overwrite",
                                             "mute": True})}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)


def build(lmcps_py, config_text, previous_text, built_by, workdir):
    """Hand the config to `lmcps index` and take back the JSON. The index verb
    owns the spawning and the merge; this function owns nothing but files."""
    config = workdir / "mcp.json"
    config.write_text(config_text, encoding="utf-8")
    out = workdir / "catalog.json"

    cmd = [sys.executable, lmcps_py, "--timeout", str(OVERALL_TIMEOUT), "index",
           "--out", str(out), "--built-by", built_by,
           "--per-server-timeout", str(PER_SERVER_TIMEOUT)]
    if previous_text is not None:
        previous = workdir / "previous.json"
        previous.write_text(previous_text, encoding="utf-8")
        cmd += ["--previous", str(previous)]

    env = dict(os.environ)
    env["LMCPS_CONFIG"] = str(config)
    env["LMCPS_HOME"] = str(workdir / "home")
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=OVERALL_TIMEOUT + 60, env=env)
    for line in (p.stderr or "").strip().splitlines():
        log(line)
    if p.returncode != 0:
        raise RuntimeError(f"lmcps index exited {p.returncode}")
    return out.read_text(encoding="utf-8")


def run():
    creds_path = find(
        "~/.claude/skills/synced/*/durable-filesystem/credentials.json",
        "/mnt/skills/*/durable-filesystem/credentials.json",
    )
    if not creds_path:
        raise RuntimeError("no durable-filesystem credentials.json on this box")
    lmcps_py = find(
        "~/.claude/skills/synced/*/local-mcps/bin/lmcps.py",
        "/mnt/skills/*/local-mcps/bin/lmcps.py",
    )
    if not lmcps_py:
        raise RuntimeError("no local-mcps/bin/lmcps.py on this box")
    log(f"lmcps at {lmcps_py}")

    creds = json.loads(Path(creds_path).read_text(encoding="utf-8"))
    token = access_token(creds)

    # Read the config live rather than through the share link the claude.ai side
    # uses: that one is CDN-cached and can be minutes behind an edit.
    config_text = download(token, CONFIG_PATH)
    if config_text is None:
        raise RuntimeError(f"{CONFIG_PATH} is not on the durable filesystem")
    previous_text = download(token, CATALOG_PATH)

    built_by = "refresh.py@" + hashlib.sha256(
        Path(__file__).resolve().read_bytes()).hexdigest()[:8]

    with tempfile.TemporaryDirectory(prefix="lmcps-refresh-") as tmp:
        catalog = build(lmcps_py, config_text, previous_text, built_by, Path(tmp))

    upload(token, CATALOG_PATH, catalog)
    servers = json.loads(catalog).get("servers") or {}
    ok = sum(1 for e in servers.values() if not e.get("error"))
    return f"wrote {CATALOG_PATH}: {ok}/{len(servers)} server(s) indexed, {built_by}"


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    started = time.time()
    try:
        summary = run()
    except Exception as exc:  # the routine's real job must survive this one
        summary = f"FAILED: {type(exc).__name__}: {exc}"
    print(f"lmcps-refresh: {summary} ({time.time() - started:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
