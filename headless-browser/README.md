# headless-browser

Fetches a page when `web_fetch` didn't — a thin JS-rendering fallback for claude.ai's code-execution sandbox, backed by [pinchtab](https://www.npmjs.com/package/pinchtab) driving a real headless Chrome.

```
SKILL.md    the skill Claude reads; when to reach for this over web_fetch
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

## Cloak mode

```bash
bash setup.sh --cloak     # or HEADLESS_BROWSER_CLOAK=1
```

Swaps the browser runtime for [CloakBrowser](https://pinchtab.com/blog/pinchtab-0-14-0-cloakbrowser), the patched Chromium build pinchtab 0.14.0 added support for, for sites that reject a plain headless Chrome on fingerprint alone. Off by default because it downloads a browser build — a minute or two against the ordinary path's seconds. `CLOAK_PLATFORM`, `CLOAK_TIMEZONE`, `CLOAK_LOCALE` and `CLOAK_SEED` override the presented fingerprint.

`browsers.default` is flipped to `cloak` only after the binary is confirmed on disk; if the install fails the script says so and falls back to plain Chrome rather than leaving the config pointing at a runtime that isn't there. To go back, `pinchtab config init` and re-run without the flag.

## Package for claude.ai

Zip this directory and upload it under Settings → Capabilities → Skills. There's no build step and no credentials file — unlike `durable-filesystem/`, nothing here is a secret.

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
