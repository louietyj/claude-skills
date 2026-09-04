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

Both downloads are timed (`SETUP_TIMEOUT`, default 300s) and leave npm's progress in the transcript; a silent multi-minute step is indistinguishable from a wedged one, and falling back beats waiting forever. `CLOAK_PLATFORM`, `CLOAK_TIMEZONE`, `CLOAK_LOCALE` and `CLOAK_SEED` override the presented fingerprint.

`browsers.default` is flipped to `cloak` only after the binary is confirmed on disk, so a failed install falls back to plain Chrome — reported in the summary — rather than leaving the config pointing at a runtime that isn't there. `--no-cloak` resets it for the same reason: a sandbox where cloak already succeeded must not silently keep using it.

## Captcha solving

`setup.sh` installs pinchtab from [louietyj/pinchtab](https://github.com/louietyj/pinchtab) rather than upstream npm, replacing the managed binary the npm package fetched and leaving its bundled docs alone. Upstream ships the CapSolver solver as an unimplemented stub (`internal/autosolver/external/capsolver.go` returns `"not yet implemented"`), so a captcha page is detected, abandoned, and reported as an unexplained `solved:false`. The fork implements it and fixes three bugs found around it: detection matching vendor names as bare substrings anywhere in the document, sitekeys being unreachable on explicitly-rendered widgets, and `/solve` reporting `solved:true` on a challenge it never detected. `PINCHTAB_USE_FORK=0` stays on the npm build.

Most sites never need it. Cloudflare, DataDome and friends score each visitor and only challenge a bad one; cloak's fingerprint scores fine, and SteamDB, g2.com and scrapingcourse's own "Cloudflare challenge" page all load with no challenge at all. What needs solving is a site that gates *every* visitor regardless of reputation — archive.today and its mirrors are the ones worth caring about. Most public "captcha demo" pages are useless for testing: they use dummy sitekeys (`1x0000…`, `3x0000…`) that no solving service will process.

`autoSolver.solverTimeoutSec` is set to 150, not the 30s default. A reCAPTCHA image challenge routinely runs past 60s, and the default kills the poll *after* CapSolver has already been paid for the solve.

## Package for claude.ai

Zip this directory and upload it under Settings → Capabilities → Skills. There's no build step, but there is now a credentials file:

- **`capsolver.key` ships in the zip in plaintext**, exactly as `durable-filesystem/credentials.json` does. The uploaded skill is a credential — anyone holding it can spend the CapSolver balance. Don't commit it, don't share the zip.
- Copy `capsolver.key.example` to `capsolver.key` and put the real key in it, or set `CAPSOLVER_API_KEY` in the environment instead. Without either, the solver is simply absent and everything else works unchanged.

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
- `pinchtab server stop` when finished is optional; the sandbox is disposable.
