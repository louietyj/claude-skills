"""Open a quick room from the real page, against `wrangler dev` and the fakes
(as worker/test_session.py runs them), in headless Chromium with a fake mic:

- /launch with no tap, under the browser's default autoplay policy and the
  page's own echo canceller (as on the watch): the room opens, Claude is woken,
  and audio reaches the transcriber, after one tap if the browser asks for it;
- the landing page's "Talk to Claude" button: the same, and no tap beyond it.

    python dev/page_launch.py

Needs Playwright with Chromium (CHROME=<path> for a preinstalled one) and websockets.
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


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        raise SystemExit(1)


def opened_room(page, n):
    """The room the page opened, once its audio reaches a new transcriber stream."""
    page.wait_for_function("sid && holding", timeout=15000)
    sid = page.evaluate("sid")
    ts.wait_streams(n + 1, timeout=15)
    ts.wait_for(lambda: any(f["body"] == {"text": f"intercom room {sid}"} for f in ts.ROUTINE.fires))
    return sid


def main():
    ts.setUpModule()
    try:
        with sync_playwright() as p:
            # No autoplay flag: what a page opened from a bookmark gets.
            browser = p.chromium.launch(executable_path=os.environ.get("CHROME"), args=[
                "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])
            ctx = browser.new_context()
            ctx.add_init_script(f"localStorage.setItem('intercom-k', '{ts.DEV}')")

            page = ctx.new_page()
            n = ts.stream_count()
            page.goto(f"{ts.BASE}/launch?ui=watch&aec=on")
            sid = opened_room(page, n)
            check(True, f"/launch opened {sid}, woke Claude, and its audio reached the transcriber")
            time.sleep(1)
            asked = page.evaluate("tapRetries !== null")
            print(f"     the browser {'held audio back until a tap' if asked else 'let audio start without a tap'}")
            if asked:
                check("Tap to start audio" in page.inner_text("#status"), "the page asks for the tap")
                page.mouse.click(10, 10)
                page.wait_for_function("meterCtx.state === 'running' && tapRetries === null", timeout=5000)
                check(True, "one tap starts it")
            check(page.evaluate("meterCtx.state") == "running", "the echo canceller's context runs")
            es = ts.entries(sid)
            check("recording started on the watch" in ts.texts(es, "event"), "the room is recording")
            page.click("#stop")
            ts.wait_for(lambda: ts.http("/status", id=sid)[1]["state"] == "ended")

            page = ctx.new_page()
            n = ts.stream_count()
            page.goto(f"{ts.BASE}/?ui=phone")
            page.click("#launchbtn")
            sid2 = opened_room(page, n)
            check(sid2 != sid, f"the landing page's button opened a new room, {sid2}")
            time.sleep(1)
            check(page.evaluate("tapRetries === null && meterCtx.state === 'running'"),
                  "its tap was enough: nothing more asked")
            page.click("#menu")
            options = lambda sel: page.eval_on_selector_all(f"{sel} option", "os => os.map((o) => o.text)")
            check(page.is_visible("#mic") and page.is_visible("#speaker")
                  and options("#mic") and options("#speaker"), "the sheet shows a mic and a speaker list")
            page.click("#done")
            page.click("#stop")

            # Chrome on Android: its call devices listed as mics, and only "default" for
            # output. The fake mic plays the earpiece; every mic the page opens is logged.
            ctx = browser.new_context()
            ctx.add_init_script(f"localStorage.setItem('intercom-k', '{ts.DEV}')")
            ctx.add_init_script("""
                window.__opened = [];
                const md = navigator.mediaDevices;
                const enumerate = md.enumerateDevices.bind(md), gum = md.getUserMedia.bind(md);
                md.enumerateDevices = async () => {
                  const mic = (await enumerate()).find((d) => d.kind === "audioinput" && d.deviceId !== "default");
                  return [{ kind: "audioinput", deviceId: "default", groupId: "", label: "Default" },
                          { kind: "audioinput", deviceId: "spk", groupId: "", label: "Speakerphone" },
                          { kind: "audioinput", deviceId: mic.deviceId, groupId: "", label: "Headset earpiece" },
                          { kind: "audiooutput", deviceId: "default", groupId: "", label: "Default" }];
                };
                md.getUserMedia = (c) => {
                  const id = c.audio?.deviceId?.exact ?? null;
                  __opened.push(id);
                  return gum(id === "spk" ? { audio: true } : c);
                };
            """)
            page = ctx.new_page()
            n = ts.stream_count()
            page.goto(f"{ts.BASE}/?ui=phone")
            page.click("#launchbtn")
            opened_room(page, n)
            check(page.evaluate("__opened.every((id) => id === null)"), "the mic opens with no device asked for")
            page.click("#menu")
            page.wait_for_function("mics.length === 3", timeout=5000)
            check(options("#mic") == ["Default", "Speakerphone", "Headset earpiece"]
                  and options("#speaker") == ["Default"], "the lists are what the browser reports")
            earpiece = page.evaluate("mics[2].id")
            rec = page.evaluate("recId")
            page.select_option("#mic", earpiece)
            page.wait_for_function(f"recId !== '{rec}' && holding", timeout=5000)
            ts.wait_streams(n + 2, timeout=15)
            check(page.evaluate(f"__opened.at(-1) === '{earpiece}'"),
                  "picking a mic reopens it on that device, with a new transcriber stream")
            rec = page.evaluate("recId")
            page.select_option("#mic", "default")
            page.wait_for_function(f"recId !== '{rec}'", timeout=5000)
            check(page.evaluate("__opened.at(-1) === null"), "Default reopens it with no device asked for")
            page.click("#done")
            page.click("#stop")
            browser.close()

            # The strictest policy a browser applies: no sound before a tap on this page.
            browser = p.chromium.launch(executable_path=os.environ.get("CHROME"), args=[
                "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                "--autoplay-policy=document-user-activation-required"])
            ctx = browser.new_context()
            ctx.add_init_script(f"localStorage.setItem('intercom-k', '{ts.DEV}')")
            page = ctx.new_page()
            n = ts.stream_count()
            page.goto(f"{ts.BASE}/launch?ui=watch&aec=on")
            sid3 = opened_room(page, n)
            time.sleep(1)
            # Chromium exempts a page that is capturing the mic, so this may well run untapped.
            check(page.evaluate("meterCtx.state === 'running' || tapRetries !== null"),
                  f"under a strict policy, /launch ({sid3}) either runs or asks for a tap "
                  f"({'asked' if page.evaluate('tapRetries !== null') else 'ran untapped'})")
            # The fallback itself, on a context held back as a stricter browser would.
            page.evaluate("meterCtx.suspend().then(() => needTap(() => meterCtx.resume()))")
            page.wait_for_function("tapRetries !== null", timeout=5000)
            check("Tap to start audio" in page.inner_text("#status"), "a held-back context asks for a tap")
            page.mouse.click(10, 10)
            page.wait_for_function("meterCtx.state === 'running' && tapRetries === null", timeout=5000)
            check(page.inner_text("#status") == "", "one tap resumes it and clears the ask")
            page.click("#stop")
            browser.close()
    finally:
        ts.tearDownModule()


if __name__ == "__main__":
    main()
