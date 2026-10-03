"""Stand in for the phone: stream an audio file to the relay at real-time pace.

    python dev/feed.py dev/sample.webm [--id K7QM] [--stop]

Without --id it takes the newest armed session from the bookmark index, as the
watch page would. Prints whatever the relay sends the device (notes,
restarts, the end) while it streams. Needs `websockets` (local only, not the sandbox).
"""
import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parent.parent
CHUNK_S = 0.25


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:   # a recording cut off at STOP has no duration header; decode to measure
        out = subprocess.run(["ffmpeg", "-i", str(path), "-f", "null", "-"],
                             capture_output=True, text=True)
        stamp = [l for l in out.stderr.splitlines() if "time=" in l][-1].split("time=")[1].split()[0]
        h, m, s = stamp.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--id")
    ap.add_argument("--stop", action="store_true", help="tap STOP when the file ends")
    args = ap.parse_args()

    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    base, k = cfg["worker_url"].rstrip("/"), cfg["device_token"]
    sid = args.id
    if not sid:
        req = urllib.request.Request(f"{base}/armed?k={k}", headers={"user-agent": "claude-listen-feed"})
        sessions = json.load(urllib.request.urlopen(req, timeout=15))["sessions"]
        armed = [s for s in sessions if s["state"] == "armed"]
        if not armed:
            sys.exit("nothing armed -- run `listen start` first")
        sid = armed[0]["id"]

    data = Path(args.file).read_bytes()
    secs = duration(args.file)
    step = max(1, int(len(data) / secs * CHUNK_S))
    print(f"feeding {args.file} ({secs:.0f}s, {len(data)} bytes) into {sid}")

    ws = connect(f"{base.replace('https', 'wss')}/device?k={k}&id={sid}", open_timeout=15)
    ws.send(json.dumps({"type": "hello", "ui": "feed"}))
    ws.send(json.dumps({"type": "seg"}))

    def show():
        try:
            for raw in ws:
                print(f"  <- {json.loads(raw)}")
        except Exception:
            pass

    threading.Thread(target=show, daemon=True).start()
    t0 = time.time()
    for i in range(0, len(data), step):
        ws.send(data[i:i + step])
        time.sleep(max(0, t0 + (i // step + 1) * CHUNK_S - time.time()))
    time.sleep(4)                                  # let the last finals land
    if args.stop:
        ws.send(json.dumps({"type": "stop"}))
        time.sleep(2)
    ws.close()


if __name__ == "__main__":
    main()
