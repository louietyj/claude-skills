"""Screenshot the device page at watch and phone sizes in a talk session:
joined with no audio, holding it, the settings sheet, then calling the phone
and on the call (answered by the fake Telnyx), against
`wrangler dev` and the fakes (as
worker/test_session.py runs them). The watch shot gets its round face drawn
on, so anything crossing the edge of the circle shows up.

    python dev/page_shot.py OUTDIR

Needs Chrome, Pillow and websockets (local only).
"""
import base64
import io
import itertools
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw
from websockets.sync.client import connect

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "worker"))
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
sys.argv = sys.argv[:1]          # test_session would take OUTDIR for a running relay's URL
import test_session as ts  # noqa: E402

CHROME = os.environ.get("CHROME", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
CDP_PORT = 9333
# name, width, height, round face
SCREENS = [("watch", 456, 456, True), ("phone-small", 320, 568, False), ("phone", 412, 915, False)]


class Tab:
    def __init__(self, ws_url):
        self.ws = connect(ws_url, max_size=None)
        self.ids = itertools.count(1)

    def call(self, method, **params):
        i = next(self.ids)
        self.ws.send(json.dumps({"id": i, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv(timeout=30))
            if m.get("id") == i:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})

    def js(self, expr):
        return self.call("Runtime.evaluate", expression=expr, returnByValue=True)["result"].get("value")

    def wait(self, expr, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            if self.js(f"!!({expr})"):      # an element doesn't come back by value
                return
            time.sleep(0.2)
        raise RuntimeError(f"page never got to: {expr}")

    def shot(self, path, round_face):
        png = base64.b64decode(self.call("Page.captureScreenshot", format="png")["data"])
        im = Image.open(io.BytesIO(png)).convert("RGB")
        if round_face:
            w, h = im.size
            ImageDraw.Draw(im).ellipse([0, 0, w - 1, h - 1], outline=(255, 0, 0), width=2)
        im.save(path)
        print(path)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ts.setUpModule()
    profile = tempfile.mkdtemp(prefix="page-shot-")
    chrome = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={CDP_PORT}",
                               f"--user-data-dir={profile}", "--use-fake-ui-for-media-stream",
                               "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required",
                               "about:blank"])
    try:
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=2) as r:
                    page = next(t for t in json.load(r) if t["type"] == "page")
                break
            except (OSError, StopIteration):
                time.sleep(0.2)
        tab = Tab(page["webSocketDebuggerUrl"])
        for name, w, h, round_face in SCREENS:
            ts.create_talk()
            tab.call("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=True)
            tab.call("Page.navigate", url=f"{ts.BASE}/?ui={name.split('-')[0]}#k={ts.DEV}")
            tab.wait("document.querySelector('#list button.big')")
            tab.js("document.querySelector('#list button.big').click()")
            tab.wait("!document.getElementById('choices').classList.contains('hide')")
            tab.js("showNote('Second long note: still capped at 120 characters. For more detail, read the chat, "
                   "where the full steer is.')")
            tab.shot(OUT / f"{name}-joined.png", round_face)
            tab.js("document.getElementById('usehere').click()")
            tab.wait("!document.getElementById('pause').classList.contains('hide')")
            tab.js("showNote('Ask whether the trustee fee is capped')")
            tab.shot(OUT / f"{name}-live.png", round_face)
            tab.js("document.getElementById('menu').click()")
            tab.wait("document.getElementById('holdval').textContent.endsWith('s')")
            tab.shot(OUT / f"{name}-settings.png", round_face)
            tab.js("myNumber = '+14155550123'; document.getElementById('callme2').click()")
            tab.wait("document.getElementById('where').textContent.startsWith('Calling')")
            tab.shot(OUT / f"{name}-calling.png", round_face)
            ts.TELNYX.answer(ts.TELNYX.last())
            tab.wait("document.getElementById('where').textContent.startsWith('Phone')")
            tab.shot(OUT / f"{name}-oncall.png", round_face)
            tab.js("document.getElementById('stop').click()")
            time.sleep(1)
    finally:
        chrome.terminate()
        ts.tearDownModule()


if __name__ == "__main__":
    main()
