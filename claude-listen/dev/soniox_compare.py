"""Stream an audio file to Soniox real-time at real-time pace and print the
speaker-labelled turns, for comparing live diarization against Deepgram on the
exact same bytes.

    python dev/soniox_compare.py recording.webm [--key-file ~/Downloads/soniox_key]

The key is read from a file so it never appears on a command line or in output.
Soniox real-time keeps no audio or transcripts (docs: security-and-privacy).
"""
import argparse
import json
import os
import subprocess
import threading
import time
from pathlib import Path

from websockets.sync.client import connect

URL = "wss://stt-rt.soniox.com/transcribe-websocket"
CHUNK_S = 0.25


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:   # a recorder cut off at STOP has no duration header; decode to measure
        out = subprocess.run(["ffmpeg", "-i", str(path), "-f", "null", "-"],
                             capture_output=True, text=True)
        stamp = [l for l in out.stderr.splitlines() if "time=" in l][-1].split("time=")[1].split()[0]
        h, m, s = stamp.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--key-file", default=os.path.expanduser("~/Downloads/soniox_key"))
    ap.add_argument("--term", action="append", default=[])
    args = ap.parse_args()

    key = Path(args.key_file).read_text(encoding="utf-8").strip()
    data = Path(args.file).read_bytes()
    step = max(1, int(len(data) / duration(args.file) * CHUNK_S))

    ws = connect(URL, open_timeout=15, max_size=None)
    ws.send(json.dumps({
        "api_key": key, "model": "stt-rt-v5", "audio_format": "auto",
        "language_hints": ["en"], "enable_speaker_diarization": True,
        "enable_endpoint_detection": True,
        **({"context": {"terms": args.term}} if args.term else {}),
    }))

    finals = []

    def receive():
        for raw in ws:
            m = json.loads(raw)
            if m.get("error_code"):
                print("error:", m.get("error_code"), m.get("error_message"))
                return
            finals.extend(t for t in m.get("tokens", []) if t.get("is_final") and t["text"] != "<end>")
            if m.get("finished"):
                return

    rx = threading.Thread(target=receive, daemon=True)
    rx.start()
    t0 = time.time()
    for i in range(0, len(data), step):
        ws.send(data[i:i + step])
        time.sleep(max(0, t0 + (i // step + 1) * CHUNK_S - time.time()))
    ws.send(b"")                      # end of stream: Soniox flushes and closes
    rx.join(60)

    turns, cur = [], None
    for t in finals:
        spk = t.get("speaker", "?")
        if not cur or cur["spk"] != spk:
            cur = {"spk": spk, "ms": t.get("start_ms", 0), "text": ""}
            turns.append(cur)
        cur["text"] += t["text"]
    for tr in turns:
        s = tr["ms"] // 1000
        print(f"[{s // 60:02d}:{s % 60:02d} S{tr['spk']}] {tr['text'].strip()}")


if __name__ == "__main__":
    main()
