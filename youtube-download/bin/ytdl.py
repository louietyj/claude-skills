#!/usr/bin/env python3
"""YouTube video download: everything around the browser step. See SKILL.md."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
JS = os.path.join(HERE, "..", "js", "dlsrv.js")
EMBED = "https://embed.dlsrv.online/v1/full?videoId={}"
VIDEO_QUALITIES = ["2160", "1440", "1080", "720", "480", "360", "240", "144"]
AUDIO_QUALITIES = ["320", "256", "128", "96", "64"]


def video_id(s: str) -> str:
    s = s.strip()
    if re.fullmatch(r"[\w-]{11}", s):
        return s
    u = urllib.parse.urlparse(s if "://" in s else "https://" + s)
    host = u.netloc.lower().removeprefix("www.").removeprefix("m.").removeprefix("music.")
    if host == "youtu.be":
        cand = u.path.strip("/").split("/")[0]
    elif host.endswith("youtube.com") or host.endswith("youtube-nocookie.com"):
        q = urllib.parse.parse_qs(u.query)
        if "v" in q:
            cand = q["v"][0]
        else:
            m = re.match(r"/(?:shorts|embed|live|v)/([\w-]{11})", u.path)
            cand = m.group(1) if m else ""
    else:
        cand = ""
    if not re.fullmatch(r"[\w-]{11}", cand):
        sys.exit(f"ytdl: can't find a video id in {s!r}")
    return cand


def parse_time(t: str) -> float:
    secs = 0.0
    for part in t.split(":"):
        secs = secs * 60 + float(part)
    return secs


def cmd_page(a):
    print(EMBED.format(video_id(a.video)))


def cmd_js(a):
    fmt, q = a.format, a.quality
    allowed = VIDEO_QUALITIES if fmt == "mp4" else AUDIO_QUALITIES
    if a.probe:
        if fmt != "mp4" or q is not None:
            sys.exit("ytdl: --probe takes neither --format nor --quality")
        req = {"videoId": video_id(a.video), "format": "mp4", "probe": True}
        with open(JS) as fh:
            print(fh.read().replace("__REQUEST__", json.dumps(req)))
        return
    if fmt in ("m4a", "opus"):
        q = "320"  # the page always sends 320 for these
    elif q is None:
        q = "720" if fmt == "mp4" else "128"
    elif q not in allowed:
        sys.exit(f"ytdl: --quality for {fmt} is one of {', '.join(allowed)}")
    req = {"videoId": video_id(a.video), "format": fmt, "quality": q}
    with open(JS) as fh:
        print(fh.read().replace("__REQUEST__", json.dumps(req)))


def cmd_fetch(a):
    with open(a.link) as fh:
        raw = fh.read()
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        link = json.loads(m.group(0)) if m else {}
        if isinstance(link, str):  # the eval printed the JSON string itself, quoted
            link = json.loads(link)
    except json.JSONDecodeError:
        link = {}
    if not link.get("url"):
        print(f"ytdl: no download link in {a.link}: {raw.strip()[:400]}", file=sys.stderr)
        return 2
    out = a.output
    if os.path.isdir(out):
        name = re.sub(r'[\\/:*?"<>|]+', "_", link.get("filename") or "video.mp4")
        out = os.path.join(out, name)
    r = subprocess.run(["curl", "-sS", "--fail", "-m", str(a.timeout), "-o", out, link["url"],
                        "-w", "%{http_code} %{size_download} %{speed_download}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"ytdl: download failed ({r.stderr.strip() or r.stdout.strip()}). "
              "Tunnel links expire about 80 s after the eval: evaluate again, then fetch at once.",
              file=sys.stderr)
        return 3
    _, size, speed = r.stdout.split()
    info = probe(out)
    # Audio comes named .mp3 whatever was asked for, but has so far been AAC.
    if os.path.isdir(a.output) and out.endswith(".mp3") and info.endswith("audio aac"):
        m4a = out[:-4] + ".m4a"
        os.replace(out, m4a)
        out = m4a
    print(f"saved {out}  {int(size) / 1e6:.1f} MB at {float(speed) / 1e6:.1f} MB/s")
    print(info)
    return 0


def probe(path: str) -> str:
    if not shutil.which("ffprobe"):
        return "(ffprobe not installed; not checked)"
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration:stream=codec_type,codec_name,width,height",
                        "-of", "json", path], capture_output=True, text=True)
    try:
        info = json.loads(r.stdout)
    except json.JSONDecodeError:
        return f"ffprobe could not read it: {r.stderr.strip()[:200]}"
    parts = [f"{float(info['format']['duration']):.0f} s"]
    for s in info.get("streams", []):
        if s.get("codec_type") == "video":
            parts.append(f"video {s['codec_name']} {s.get('width')}x{s.get('height')}")
        elif s.get("codec_type") == "audio":
            parts.append(f"audio {s['codec_name']}")
    return ", ".join(parts)


def cmd_frames(a):
    os.makedirs(a.output, exist_ok=True)
    for t in a.times:
        secs = parse_time(t)
        out = os.path.join(a.output, f"frame_{int(secs // 60):02d}m{secs % 60:05.2f}s.jpg")
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", str(secs), "-i", a.file,
                            "-frames:v", "1", "-q:v", "2", out], capture_output=True, text=True)
        print(out if r.returncode == 0 and os.path.exists(out) else f"{t}: failed {r.stderr.strip()[:200]}")


def cmd_thumb(a):
    vid = video_id(a.video)
    for name in ("maxresdefault", "sddefault", "hqdefault"):
        r = subprocess.run(["curl", "-sS", "--fail", "-m", "30", "-o", a.output,
                            f"https://i.ytimg.com/vi/{vid}/{name}.jpg"], capture_output=True)
        if r.returncode == 0:
            print(f"saved {a.output} ({name})")
            return 0
    print("ytdl: no thumbnail found", file=sys.stderr)
    return 1


def main() -> int:
    p = argparse.ArgumentParser(prog="ytdl", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("page", help="print the dlsrv embed URL to navigate to")
    s.add_argument("video", help="YouTube URL or 11-character id")
    s.set_defaults(fn=cmd_page)

    s = sub.add_parser("js", help="print the page script, with the request filled in")
    s.add_argument("video")
    s.add_argument("--format", choices=["mp4", "mp3", "m4a", "opus"], default="mp4")
    s.add_argument("--quality", help="mp4: 2160 1440 1080 720 480 360 240 144 (default 720; "
                                     "above 1080 is AV1); mp3: 320 256 128 96 64 (default 128)")
    s.add_argument("--probe", action="store_true",
                   help="instead of a link, report the best quality on offer and what 1080 gives")
    s.set_defaults(fn=cmd_js)

    s = sub.add_parser("fetch", help="download the file a page-script result points to")
    s.add_argument("link", help="file holding the page script's output")
    s.add_argument("-o", "--output", required=True, help="file, or a directory to use the site's filename")
    s.add_argument("--timeout", type=int, default=600)
    s.set_defaults(fn=cmd_fetch)

    s = sub.add_parser("frames", help="save a JPEG frame at each timestamp")
    s.add_argument("file")
    s.add_argument("times", nargs="+", help="seconds, m:ss or h:mm:ss")
    s.add_argument("-o", "--output", required=True, help="directory")
    s.set_defaults(fn=cmd_frames)

    s = sub.add_parser("thumb", help="download the video's thumbnail (no video download)")
    s.add_argument("video")
    s.add_argument("-o", "--output", required=True)
    s.set_defaults(fn=cmd_thumb)

    a = p.parse_args()
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
