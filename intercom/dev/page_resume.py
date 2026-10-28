"""Drop the device page's socket three ways and check what reaches the
transcriber, against `wrangler dev` and the fakes (as worker/test_session.py
runs them), in headless Chromium with a fake mic:

- the socket closes: the page reconnects and resends, on the same stream;
- the socket goes silent (no pings): the page opens a new one, the old is replaced;
- the page stays away past the grace: the relay says so, and a fresh stream starts.

On the same stream, the audio the transcriber got must decode to as long as
the recorder ran: nothing lost in the gaps.

    python dev/page_resume.py

Needs Playwright with Chromium, websockets and ffmpeg.
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "worker"))
sys.argv = sys.argv[:1]
import test_session as ts  # noqa: E402


def decoded_s(chunks):
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
        f.write(b"".join(chunks))
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", f.name, "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True)
    return len(out.stdout) / 32000


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        raise SystemExit(1)


def main():
    ts.setUpModule()
    try:
        with sync_playwright() as p:
            # A preinstalled Chromium if there is one (CHROME), else Playwright's own.
            browser = p.chromium.launch(executable_path=os.environ.get("CHROME"), args=[
                "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                "--autoplay-policy=no-user-gesture-required"])
            page = browser.new_page()
            sid = ts.create()
            page.goto(f"{ts.BASE}/?ui=phone#k={ts.DEV}")
            page.click("#list button.big")
            n = ts.stream_count()
            page.click("#usehere")
            stream = ts.wait_streams(n + 1)
            t0 = time.time()
            time.sleep(3)

            page.evaluate("ws.close()")                    # the network dropped the socket
            time.sleep(3)
            check(ts.stream_count() == n + 1, "after a closed socket: the same transcriber stream")

            page.evaluate("lastRx = 0")                    # a socket that died without closing
            time.sleep(3)
            check(ts.stream_count() == n + 1, "after a silent socket: the same transcriber stream")

            # Every racing socket fails while offline. Chromium's emulation spares the open
            # socket, so its next ping calls the race off: the old socket came back first.
            page.context.set_offline(True)
            page.evaluate("lastRx = 0")
            time.sleep(5)
            page.context.set_offline(False)
            time.sleep(4)
            check(ts.stream_count() == n + 1 and page.evaluate("racer === null"),
                  "a race run offline ends once a socket is heard, on the same stream")
            ran = time.time() - t0
            got = decoded_s(stream["audio"])
            check(abs(got - ran) < 1.0, f"transcriber got {got:.1f}s of audio in {ran:.1f}s: nothing lost")
            es = ts.entries(sid)
            # Coming back online can start one more race, which either side may win.
            check(len([t for t in ts.texts(es, "net") if "reconnected" in t]) >= 2,
                  f"the switches logged quietly: {ts.texts(es, 'net')}")
            check(not any("disconnected" in t for t in ts.texts(es, "event")), "no drop reported to Claude")

            page.evaluate("window._connect = connect; connect = () => {}; ws.close()")   # away past the grace
            time.sleep(3)
            page.evaluate("connect = window._connect; connect()")
            ts.wait_streams(n + 2)
            es = ts.wait_entries(sid, lambda es: any("reconnected after" in t for t in ts.texts(es, "event")))
            check(any("holding the audio disconnected" in t for t in ts.texts(es, "event")),
                  "a drop past the grace is news, and a fresh stream starts")
            page.click("#stop")
            browser.close()
    finally:
        ts.tearDownModule()


if __name__ == "__main__":
    main()
