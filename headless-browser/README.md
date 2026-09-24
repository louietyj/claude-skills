# headless-browser

Fetches a page when `web_fetch` didn't — a thin JS-rendering fallback for claude.ai's code-execution sandbox, backed by [pinchtab](https://www.npmjs.com/package/pinchtab) driving a real headless Chrome.

```
SKILL.md    the skill Claude reads; when to reach for this over web_fetch
cloak/      pinned package.json + package-lock.json for the cloakbrowser install,
            and refresh.sh to re-pin it
setup.sh    the whole boot: installs pinchtab, finds/downloads Chrome, opens capability
            gates, starts the server, mints a session, smoke-tests it, and prints
            pinchtab's own instructions in full
```

`setup.sh` is deliberately one call that leaves nothing to read afterwards — same shape as `session-init/`. Claude runs it and its next tool call is a real browser command; it never has to `cat` pinchtab's bundled SKILL.md itself or be told separately which of that file's instructions to disregard. The script prints the file and the corrections to it in the same output.

## Setup

Nothing to configure ahead of time — `setup.sh` is self-contained and idempotent. Claude runs it once per conversation:

```bash
bash setup.sh
```

It installs pinchtab globally if missing, locates a system Chrome or pulls puppeteer's bundled build, relaxes pinchtab's security gates (clipboard, state export, file scheme, all domains — safe defaults for a disposable sandbox but not for a personal machine), starts the pinchtab server, and navigates to `example.com` once as an end-to-end check. Output is a numbered transcript with a status summary; stages are independent, so a failure in one is reported rather than aborting the rest.

## Cloak mode (default)

The browser runtime is [CloakBrowser](https://pinchtab.com/blog/pinchtab-0-14-0-cloakbrowser), the patched Chromium pinchtab 0.14.0 added support for: sites that reject a plain headless Chrome on fingerprint alone accept it. Measured in the claude.ai sandbox: **17.5s cold** against **6.6s** for plain Chrome, and **4.4s** when the binary is already cached (`/root/.cloakbrowser/*/chrome`) — ~12s of the difference is the browser download itself.

It was briefly opt-in, when the same run cost 161s. That turned out to be npm, not the download; with the solve pinned, ~11s is a fair price for not being turned away, so it is the default again. `bash setup.sh --no-cloak` (or `HEADLESS_BROWSER_CLOAK=0`) forces plain Chrome.

The install runs `npm ci` against the lockfile in `cloak/`, not `npm install` off a bare `npm init`. That distinction was worth 215s: with no lockfile npm re-solves the 8-package tree from the registry on every run, and spent that long concluding "up to date, audited 9 packages" on a tree that was already complete — `npm ping` answers in 134ms, so it was resolution, not bandwidth. The single biggest term in that solve was `playwright-core`: an optional peer this skill never calls, whose packument is 18.4 MB across 5,653 versions. It is deliberately absent from the pin — `ensureBinary()` and `binaryInfo()` work without it, and dropping it takes the metadata npm must fetch from 19.7 MB to ~1.3 MB, which speeds up the unpinned fallback too.

Re-pin with `bash cloak/refresh.sh` (a few seconds; resolves without installing or downloading a browser). Monthly is plenty — a stale pin installs an older cloakbrowser and an older patched Chromium, which still works, and setup falls back to an unpinned install if the pin ever stops resolving. Drift costs time, never the feature.

Both downloads are timed (`SETUP_TIMEOUT`, default 300s) and leave npm's progress in the transcript; a silent multi-minute step is indistinguishable from a wedged one, and falling back beats waiting forever. `CLOAK_PLATFORM`, `CLOAK_TIMEZONE`, `CLOAK_LOCALE`, `CLOAK_SEED` and `CLOAK_WINDOW` override the presented fingerprint. The seed defaults to a random one per sandbox, kept across re-runs in it: the old fixed `42069` — the example value in pinchtab's docs — made every sandbox the same device on a different IP.

`browsers.default` is flipped to `cloak` only after the binary is confirmed on disk, so a failed install falls back to plain Chrome — reported in the summary — rather than leaving the config pointing at a runtime that isn't there. `--no-cloak` resets it for the same reason: a sandbox where cloak already succeeded must not silently keep using it.

## Window size and screenshots

Cloak draws a random window size per launch by default, up to 1920x1080. `setup.sh` pins it to 1440x900 instead (`browser.cloak.windowSize`, a fork-only key; `CLOAK_WINDOW` overrides), because the random pick broke coordinate clicks. claude.ai shrinks an image past ~1.15 megapixels before the model sees it, and the model then reads coordinates off the shrunken copy. In blind click trials on a 1920x1080 viewport, 7 of 13 clicks fell short, most at ~0.75x the target's true position on one or both axes: the shrink ratio for a 1.15MP cap. At 1366x768 all 7 trials were within 1.4px. 1440x900 gives a 1440x779 viewport (1.12MP) and is a common laptop size. Pinchtab itself is exact at every size: the screenshot matched the page to 0.5px.

The shim screenshots after every page-changing command (`nav`, `click`, `fill`, ...) and prints the path with its dimensions, saving the agent a separate screenshot call. Shots go to `/mnt/user-data/outputs/pinchtab-shots/pinchtab-NNNN-<cmd>.jpg`, where claude.ai lets the user open them too (`~/pinchtab-shots` outside claude.ai; `PINCHTAB_SHOT_DIR` overrides). The outputs view flattens directories, so the `pinchtab-` prefix is what keeps the shots together. It adds ~150ms per command; a bare pinchtab call is ~70ms. Changes a click makes synchronously, or within a couple of animation frames, are in the shot; content that waits on the network may not be yet. `PINCHTAB_AUTOSHOT=0` turns it off.

## Captcha solving

`setup.sh` installs pinchtab from [louietyj/pinchtab](https://github.com/louietyj/pinchtab) rather than upstream npm, replacing the managed binary the npm package fetched and leaving its bundled docs alone. Upstream ships the CapSolver solver as an unimplemented stub (`internal/autosolver/external/capsolver.go` returns `"not yet implemented"`), so a captcha page is detected, abandoned, and reported as an unexplained `solved:false`. The fork implements it and fixes three bugs found around it: detection matching vendor names as bare substrings anywhere in the document, sitekeys being unreachable on explicitly-rendered widgets, and `/solve` reporting `solved:true` on a challenge it never detected. `PINCHTAB_USE_FORK=0` stays on the npm build.

Most sites never need it. Cloudflare, DataDome and friends score each visitor and mostly challenge only a bad one; cloak's fingerprint scores fine, and SteamDB, g2.com and scrapingcourse's own "Cloudflare challenge" page all load with no challenge at all. What needs solving is a site that gates *every* visitor behind a real captcha regardless of reputation — archive.today and its mirrors are the ones worth caring about. Most public "captcha demo" pages are useless for testing: they use dummy sitekeys (`1x0000…`, `3x0000…`) that no solving service will process.

### Two providers

CapSolver dropped hCaptcha and FunCaptcha: `createTask` answers both with "We don't support this service". 2Captcha still solves both (hCaptcha is undocumented but works), so `setup.sh` loads `2captcha.key` beside `capsolver.key`. Each provider handles only the types it can solve, and CapSolver is tried first:

| Captcha | CapSolver | 2Captcha |
|---|---|---|
| reCAPTCHA v2/v3, Enterprise, invisible; Turnstile; MTCaptcha; GeeTest v4 | yes | fallback |
| AWS WAF | yes | no (its Amazon task returns a voucher that needs one more exchange) |
| hCaptcha, FunCaptcha | no | yes |
| Tencent, NetEase Yidun, Yandex SmartCaptcha, Prosopo, Lemin | no | yes |

2Captcha's solves are done by people: hCaptcha took 55–150s live. The fork gives 2Captcha 180s whatever `solverTimeoutSec` says. Any solve that fails after it was paid for, including a poll that runs out of time (the task is still billed when it finishes), ends the run instead of buying another. The fork also stops re-solving a challenge that is already solved: the widget stays in the DOM, and with `triggerOnAction` every `fill` on the solved page used to buy and wait for a new token.

Not solvable, and refused before anything is spent:
- **GeeTest v3.** Its `challenge` is single-use, and the widget's own `get.php` has spent it before the page shows the puzzle.
- **DataDome and Cloudflare Challenge (`AntiCloudflareTask`).** Both need a proxy that shares the browser's IP.
- **AliExpress punish (baxia/NoCaptcha).** No provider has a task for it. From a residential IP with cloak it never appeared. If it does, `pinchtab drag <handle> --drag-x <width> --humanize` is the tool to try.

### Vision

Puzzles without a sitekey go through `pinchtab vision` (CapSolver Vision Engine): slider, rotate, image select, GIF text. Vision Engine validates nothing (three bytes of garbage came back as "angle 0" and were billed), so the fork checks every image first. It reads the images from Chrome's cache and never takes a slider from a screenshot. Live, it cleared nopecha.com's GeeTest v4 slide first time ("1.7 s, you beat 97% of users"). Its `rotate_2` model refused 2Captcha's rotate demo image, a plain rotated photo rather than the concentric type it expects, without charging.

The Vision models are each trained on a particular vendor's puzzle, and elsewhere they are unreliable:
- **Every module answers CapSolver's own reference images** in the expected shape: `ocr_gif` text, a `rotate_2` angle, and `shein` rects as `{x1,y1,x2,y2}`. `rotate_1` said 0° for a piece that visibly needs turning.
- **DingXiang's rotate and jigsaw demos** (`dingxiang-inc.com/business/captcha`) failed every Vision attempt. The jigsaw's `slider_1` distance pointed at nothing near the real gap: the puzzle has a decoy gap, and the answer left the piece almost where it started. Dragging the piece onto the gap by eye (mouse down, move, screenshot, up) passed, with "验证成功", so DingXiang's behaviour check accepts pinchtab's pointer. The wrong answers came from the engine.

### The 2Captcha-only vendors

Tencent and Yidun give their answer to a callback the site registers, so the fork hooks `new TencentCaptcha` and `initNECaptcha` at document start. The hook must be a Proxy: Yidun's loader reads static properties off `initNECaptcha`, and a plain wrapper function silently stopped its widget from rendering. Both vendors load their scripts on pages that never show a captcha, so only the rendered widget triggers a solve.

| Vendor | Page | Result |
|---|---|---|
| Yandex SmartCaptcha | `smartcaptcha.yandexcloud.net/demo` | token accepted server-side ("Hello, user!"; a bogus token gets "Captcha validation failed") |
| Tencent | `cloud.tencent.com/product/captcha`, then "立即体验" | ticket in 17s, delivered to the site's callback; no server check available |
| NetEase Yidun | `dun.163.com/trial/jigsaw`, scrolled to the widget | token in 44s, in `NECaptchaValidate`; no server check available |
| Prosopo | `demo.prosopo.io` | token in `procaptcha-response`; the demo posts to `localhost:9228`, so it verifies nothing |
| Lemin | `2captcha.com/demo/lemin` | 2Captcha's workers returned unsolvable; the demo passes even with no solve, so it proves nothing |

None of this is shared anywhere. `puppeteer-extra-plugin-recaptcha` covers only reCAPTCHA and hCaptcha, and CapSolver's SDK covers reCAPTCHA and Turnstile. 2Captcha's own extension (`rucaptcha/2captcha-solver`, MIT) is the broadest reference for where each vendor keeps its parameters and how it takes its answer: start there when adding a vendor.

### AliExpress's slider

From claude.ai's sandbox, AliExpress item pages redirect to `/_____tmd_____/punish`: Alibaba's slide-to-end NoCaptcha. Its config names a cloud-IP blocklist (`cloud_ip_bl`), so the IP is what triggers it; search pages never do. No solving service covers it. 2Captcha's `AlibabaTask` is for Aliyun Captcha 2.0, a different product.

The server scores the drag itself, and a drag can pass from the flagged IP. The old drag, which appeared on the handle and pressed at once, passed 1 of 4. After porting ghost-cursor's path, so the pointer travels onto the handle first, it passed 3 of 5. The fork's `nocaptcha` solver does this on `nav`: it waits for the slider to mount, drags past the end of the track, and judges by whether the URL leaves `_____tmd_____`. A refused drag is retried on a fresh slider, loaded from the item URL, up to three times.

A pass sets an `x5sec` cookie, but the slider returns after 15-20 item pages anyway, sometimes after an in-page reCAPTCHA modal. The no-slider "Sorry, there was a problem accessing the page" is a hard block, and the solver reports it without dragging. AliExpress also punishes a page's data API (item data, recommendations) instead of the page. The same punish page then appears in an iframe over the item, and on its reCAPTCHA step the widget is two frames down: reCAPTCHA Enterprise, with an action computed at runtime, that takes its token only through the frame's own `__recaptchaValidateCB__`. The fork reads the sitekey and action off Google's anchor frame inside it, solves it as a v2 Enterprise task, calls that callback, and counts the solve only once the iframe leaves. That path is proven against a local copy of the captured frames, not yet on AliExpress itself. The run notes are in the user's Dropbox, under `Apps/louietyj-claude-ai/drafts/aliexpress-punish/`.

### Captchas in child frames

A widget whose page is an iframe (a payment or signup form embedded from another site) is invisible to the top document. The fork lists the tab's frames, and a vendor frame (reCAPTCHA anchor, hCaptcha checkbox, Turnstile) whose parent is not the top document marks that parent as the widget's page. The providers then read, solve and deliver inside it.

A frame from another site runs in its own process, as a separate CDP target that the page's frame tree does not list. Chrome also leaves its `parentId` empty; `parentFrameId` is what ties it to the tab. Pinchtab reaches it by attaching, with one trap: chromedp closes the target it attached to on cancel, and closing an iframe target closes the whole tab, so the target ID is cleared before cancelling. Verified with a reCAPTCHA in a `127.0.0.1` form framed by a `localhost` page: solved on `nav`, and the form's own callback fired.

### More verified targets

| Captcha | Page | Result |
|---|---|---|
| Turnstile, managed, real sitekey | `solvegate.io/demo/managed` | CapSolver solved it in 3.4s; the page's "Verify this token" returned Cloudflare siteverify "Token accepted" |
| Slider / rotate (DingXiang) | `dingxiang-inc.com/business/captcha` | see above |

FunCaptcha is still unproven. 2Captcha returns unsolvable for `demo.arkoselabs.com` with or without `userAgent` and `funcaptchaApiJSSubdomain`. capzy.ai's "live widget" demos render nothing. The real Arkose pages (GitHub, Roblox and Microsoft signup) sit behind account creation.

### Test targets with real sitekeys

Each page below runs a real, server-checked challenge. Submit the page's own form after the solve to see the verdict:

| Captcha | Page |
|---|---|
| reCAPTCHA v2, invisible, v2 Enterprise, v3, v3 Enterprise | `2captcha.com/demo/recaptcha-*` (CapSolver refuses 2Captcha's own sitekeys, so these exercise the fallback) |
| reCAPTCHA via CapSolver | `www.google.com/recaptcha/api2/demo` |
| hCaptcha | `accounts.hcaptcha.com/demo` |
| MTCaptcha, GeeTest v4 | `2captcha.com/demo/mtcaptcha`, `2captcha.com/demo/geetest-v4` |
| GeeTest v4 slide (Vision) | `nopecha.com/captcha/geetest?nocache#slide`, after switching auto-solve triggers off |
| AWS WAF | `hiring.amazon.ca/application/api/candidate-application/update-application` |
| FunCaptcha | `demo.arkoselabs.com/?key=DF9C4D87-CB7B-4062-9FEB-BADB6ADA61E6` (2Captcha's workers returned unsolvable twice) |

Cloudflare's current *managed* challenge ("Just a moment…", `cType: 'managed'`) is different, and egov.uscis.gov shows it to every visitor. Cloak passes it unaided in a few seconds, which pinchtab logs as solver `cleared`. CapSolver cannot clear it: the widget sits in a closed shadow root with no readable sitekey, and Cloudflare issues `cf_clearance` to the browser that ran the check — CapSolver only offers that as a proxied `AntiCloudflareTask`.

## Debugging

Reproduce in the local sandbox ([`../dev/sandbox.sh`](../dev/sandbox.sh), see the repo README) rather than on Windows. To test a fork change, build it for linux into the work directory and point setup at it:

```bash
(cd ~/pinchtab && GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -o ~/claude-skills/dev/work/pinchtab-linux-amd64 ./cmd/pinchtab)
dev/sandbox.sh sh 'PINCHTAB_FORK_BIN_URL=file:///work/pinchtab-linux-amd64 bash /mnt/skills/user/headless-browser/setup.sh'
```

The solver's log lines (`autosolver_start`, `autosolver_attempt`, `autosolver_failure`, …) are not in `/root/.pinchtab/server.log`; they belong to the browser instance. `pinchtab config get server.token` prints a redacted value, so read the token from the file:

```bash
T=$(node -p 'require("/root/.pinchtab/config.json").server.token')
for id in $(curl -s -H "Authorization: Bearer $T" localhost:9867/instances | grep -oE 'inst_[a-z0-9]+' | sort -u); do
  curl -s -H "Authorization: Bearer $T" localhost:9867/instances/$id/logs
done | grep autosolver
```

`pinchtab cookies clear` drops a `cf_clearance` from an earlier pass, which otherwise makes a rerun load the page without a challenge.

`autoSolver.solverTimeoutSec` is set to 150, not the 30s default. A reCAPTCHA image challenge routinely runs past 60s, and the default kills the poll *after* CapSolver has already been paid for the solve.

## Package for claude.ai

`python package.py` writes `headless-browser.zip`; upload it under Settings → Capabilities → Skills. It ships an explicit member list rather than globbing the directory, so scratch files and the README never reach the sandbox, and it refuses to build against a `capsolver.key` still holding the placeholder — that combination installs cleanly and then fails every captcha. `--no-key` builds a keyless zip deliberately.

There is now a credentials file:

- **`capsolver.key` ships in the zip in plaintext**, exactly as `durable-filesystem/credentials.json` does. The uploaded skill is a credential — anyone holding it can spend the CapSolver balance. Don't commit it, don't share the zip.
- Copy `capsolver.key.example` to `capsolver.key` and put the real key in it, or set `CAPSOLVER_API_KEY` in the environment instead. Without either, the solver is simply absent and everything else works unchanged.
- `2captcha.key` (or `TWOCAPTCHA_API_KEY`) is optional and handled the same way. `package.py` ships it when it is present and checks that it looks like a key (32 hex characters). Without it, hCaptcha and FunCaptcha go unsolved.

## The session shim

The interesting part of `setup.sh` is the shim it installs over the `pinchtab` binary. Pinchtab's own bundled docs tell callers to open a session by hand:

```bash
export PINCHTAB_SESSION=$(pinchtab session create ...)
```

That instruction is wrong for this environment and the shim exists to route around it without editing pinchtab itself:

- **Sessions must survive across bash calls**, since a multi-step flow (nav, then click, then read) runs as separate tool calls with no shared shell state. The shim resolves a session from a file on disk on first use and reuses it — callers never create or export one themselves.
- **`session create` must run unscoped.** A session-scoped caller gets `403`, so a hand-rolled `export PINCHTAB_SESSION=$(pinchtab session create ...)` silently captures an empty string and looks like it worked. The shim always resolves the ambient session before dispatching, except when the command itself is `session`.
- **A stale session is retried transparently.** Pinchtab exits `0` even on a bad session, so the shim greps stderr for `bad_session` and retries with a freshly minted one rather than trusting the exit code.
- **The shim is reinstalled last, unconditionally**, because `npm install -g` recreates the real `pinchtab` bin symlink and would otherwise wipe it out from under the shim.

If a caller does export `PINCHTAB_SESSION` by hand anyway (because they read pinchtab's own docs instead of this skill's), the shim adopts it as the persistent session so the *next* call — which has no export — attaches to the same tab instead of silently starting a blank one.

## Notes

- Never run `pinchtab skill update` or `pinchtab skill sync` — they write into other agent skill directories found on the machine. This skill is self-contained by design and doesn't need either.
- SKILL.md says to leave the server running. In a disposable sandbox, stopping it saves nothing, costs a tool call, and makes any follow-up re-run setup.
