"""The talk page's isolation and separate-channels settings, in headless Chromium
with a fake mic, against `wrangler dev` and the fakes (as worker/test_session.py
runs them). Android's audio routing can't be checked here: only that each
switch reaches the relay, restarts the recorder, and routes Claude's voice.

    python dev/page_settings.py

Needs Playwright with Chromium, websockets and ffmpeg.
"""
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "worker"))
sys.argv = sys.argv[:1]
import test_session as ts  # noqa: E402

failed = False


def check(ok, what):
    global failed
    failed |= not ok
    print(("ok   " if ok else "FAIL ") + what)


def main():
    ts.setUpModule()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ.get("CHROME"), args=[
                "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                "--autoplay-policy=no-user-gesture-required"])
            page = browser.new_page()
            sid = ts.create(mode="talk", isolate=True)
            page.goto(f"{ts.BASE}/?ui=phone#k={ts.DEV}")
            page.click("#list button.big")
            n = ts.stream_count()
            page.click("#usehere")
            ts.wait_streams(n + 1)
            page.click("#menu")
            page.wait_for_function("document.getElementById('isoon').classList.contains('on')")
            check(page.is_visible("#isorow") and page.is_visible("#seprow"), "both settings show in a talk room")

            page.click("#isooff")
            page.wait_for_function("document.getElementById('isooff').classList.contains('on')", timeout=8000)
            ts.wait_streams(n + 2)
            check(ts.http("/status", id=sid)[1]["isolate"] is False, "isolation off at the relay, on a fresh stream")
            es = ts.wait_entries(sid, lambda es: any("turned voice isolation off" in t for t in ts.texts(es, "event")))
            check(bool(es), "Claude is told")

            lit = "[...document.querySelectorAll('#seprow .on')].map((b) => b.id).join()"
            check(page.evaluate(lit) == "sepcall", "voice starts as call audio")
            page.click("#sepmedia")
            ts.wait_streams(n + 3)
            page.wait_for_function("mediaCtx !== null && aec !== null", timeout=8000)
            check(page.evaluate(lit) == "sepmedia", "switched to media audio, on a fresh stream")
            check(page.evaluate("raw === null && stream.getAudioTracks()[0].getSettings().echoCancellation === false"),
                  "one mic, opened raw: no processed mic to start full call mode")
            page.evaluate("onMessage({type: 'tts_start'})")
            check(page.evaluate("voiceEl.muted && mediaOut.gain.value === 1"),
                  "Claude speaks through the media context, the element silent")
            page.evaluate("claudeTalking = true; setVoice(false, DUCK_VOLUME)")
            time.sleep(0.5)
            check(page.evaluate("mediaOut.gain.value === 1"), "no ducking with separate channels")
            page.evaluate("onMessage({type: 'tts_stop'})")
            check(page.evaluate("mediaOut.gain.value === 0 && aec.gain.gain.value === 0"),
                  "stopped: media and the echo reference both silent")

            page.click("#sepcall")
            ts.wait_streams(n + 4)
            page.wait_for_function("mediaCtx === null", timeout=8000)
            page.evaluate("onMessage({type: 'tts_start'})")
            check(page.evaluate("!voiceEl.muted") and page.evaluate(lit) == "sepcall",
                  "back to call audio on the element")
            page.click("#done")
            page.click("#stop")
            browser.close()
    finally:
        ts.tearDownModule()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
