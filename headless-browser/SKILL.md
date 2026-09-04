---
name: headless-browser
description: "Fetches a page when web_fetch didn't -- through a browser that ordinary bot detection does not turn away. One script, ~18s, no second step. Use it whenever web_fetch returned nothing, a consent/paywall/'enable JavaScript' stub, or less than the page should hold: a thin result is the trigger, not just an outright error, and a fetch that silently drops JS-rendered content looks exactly like a successful one, so check what came back against the search snippet that led you there. Go straight here, skipping web_fetch, for SPAs, dashboards, JS-rendered tables, infinite scroll, and anything interactive -- expanding 'show more', clicking through flows, forms, pagination. If you are about to call a page inaccessible, or answer a page-specific question from snippets instead of the page, stop and run this: you wanted to read that page for a reason and the reason has not gone away. It is cheap; do not talk yourself out of it. Not for pages needing the user's logged-in session; those go to claude-in-chrome."
---

# Headless Browser (pinchtab)

There is nothing else to read here. The skill is one script:

```bash
bash /mnt/skills/*/headless-browser/setup.sh
```

**DO NOT head/tail/grep this command's output.** It prints instructions into
your context; truncating it silently costs you them.

Run it, then use the browser. It installs pinchtab, sets up the browser, opens
the capability gates, starts the server, creates the session, proves the chain
works with a test nav -- and prints pinchtab's own bundled instructions in full,
with the corrections that apply in this sandbox. Nothing to `cat` afterwards and
no other skill to load. Idempotent -- re-run it rather than debugging whether it
ran.

Its output is a transcript of work already done. Do not re-run the commands it
shows, do not re-read the files it prints, and do not create or export
`PINCHTAB_SESSION` yourself no matter what pinchtab's own docs say -- setup.sh
made one and a shim attaches every command to it.

The glob is deliberate: an uploaded skill lands under `/mnt/skills/user/` or
`/mnt/skills/plugins/` depending on how it was installed, and hardcoding either
one breaks on the other.

## Then

```bash
pinchtab nav <url> --block-images
pinchtab text
```

The session outlives the bash call, so multi-step flows work across calls --
nav in one, click in the next, read state in a third. The tab, its DOM state
and typed form values all persist; you never replay earlier steps. If the
session goes stale the shim recreates it, but that gives you a fresh tab with
no page loaded, so re-`nav` after seeing `no_current_tab`.

## The browser

The runtime is
[CloakBrowser](https://pinchtab.com/blog/pinchtab-0-14-0-cloakbrowser), a
patched Chromium that sites do not reject on fingerprint the way they reject a
plain headless Chrome. That is why setup takes ~18s rather than ~7s, and it is
why a nav that would have hit a block page does not. Do not go looking for a
faster mode: the difference is one browser download, once per sandbox.

If the download fails, setup falls back to the sandbox's own Chrome, says so
in the summary, and everything else still works -- so a nav that comes back
blocked is worth checking against that summary line before you conclude the
site is unreadable.

`CLOAK_PLATFORM`, `CLOAK_TIMEZONE`, `CLOAK_LOCALE` and `CLOAK_SEED` override
the fingerprint it presents. `--no-cloak` forces plain Chrome.

## Captchas

Nothing to call: `nav` solves a challenge on its own when it detects one, at
~$0.001 and 20-60s. On failure the response carries `error` and a per-attempt
`history` naming the solver that refused and why, and the tab is left in
`paused_handoff` -- the next action on it returns 409 until you
`POST /tabs/{id}/resume`.

Most pages never reach this. Cloudflare and DataDome only challenge a visitor
they score badly, and cloak's fingerprint scores fine; SteamDB, g2.com and
scrapingcourse's own "Cloudflare challenge" all load untouched. Solving is for
sites that gate *every* visitor -- archive.today and its mirrors (archive.ph,
archive.is, archive.md) are the ones you will hit.

This runs a [fork](https://github.com/louietyj/pinchtab) because upstream ships
the solver as a stub. Without a key the solver is absent and nothing else
changes.

## Notes

- Never run `pinchtab skill update` or `pinchtab skill sync` -- they write
  into other agent skill directories found on the machine. This skill is
  self-contained by design.
- `pinchtab server stop` when finished. Optional; the sandbox is disposable.
