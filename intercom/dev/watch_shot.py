"""Screenshot a device over adb. For the watch, draw its round face on it, so
anything crossing the edge of the circle shows up.

    python dev/watch_shot.py out.png [--serial S] [--open URL] [--phone]

--open loads a URL first (e.g. the page with ?debug): in Samsung Internet on
the watch, in Chrome with --phone, which also skips the circle.
Needs adb and Pillow (local only).
"""
import argparse
import io
import subprocess
import time

from PIL import Image, ImageDraw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--serial")
    ap.add_argument("--open", help="URL to load in the browser before the shot")
    ap.add_argument("--phone", action="store_true", help="Chrome, and no circle")
    args = ap.parse_args()
    adb = ["adb"] + (["-s", args.serial] if args.serial else [])
    browser = "com.android.chrome" if args.phone else "com.sec.android.app.sbrowser"

    if args.open:
        subprocess.run(adb + ["shell", "am", "start", "-a", "android.intent.action.VIEW",
                              "-d", f"'{args.open}'", browser],
                       check=True, capture_output=True)
        time.sleep(6)
    png = subprocess.run(adb + ["exec-out", "screencap", "-p"], check=True, capture_output=True).stdout
    im = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = im.size
    if not args.phone:
        ImageDraw.Draw(im).ellipse([0, 0, w - 1, h - 1], outline=(255, 0, 0), width=2)
    im.save(args.out)
    print(f"{args.out} ({w}x{h})")


if __name__ == "__main__":
    main()
