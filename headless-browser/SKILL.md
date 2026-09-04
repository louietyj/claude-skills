---
name: headless-browser
description: "Fetches a page when web_fetch didn't. One script, ~6s, no download. Use it whenever web_fetch returned nothing, a consent/paywall/'enable JavaScript' stub, or less than the page should hold: a thin result is the trigger, not just an outright error, and a fetch that silently drops JS-rendered content looks exactly like a successful one, so check what came back against the search snippet that led you there. Go straight here, skipping web_fetch, for SPAs, dashboards, JS-rendered tables, infinite scroll, and anything interactive -- expanding 'show more', clicking through flows, forms, pagination. If you are about to call a page inaccessible, or answer a page-specific question from snippets instead of the page, stop and run this: you wanted to read that page for a reason and the reason has not gone away. It is cheap; do not talk yourself out of it. Not for pages needing the user's logged-in session; those go to claude-in-chrome."
---

# Headless Browser (pinchtab)

There is nothing else to read here. The skill is one script:

```bash
bash /mnt/skills/*/headless-browser/setup.sh
```

**DO NOT head/tail/grep this command's output.** It prints instructions into
your context; truncating it silently costs you them.

Run it, then use the browser. It installs pinchtab, finds Chrome, opens the
capability gates, starts the server, creates the session, proves the chain works
with a test nav -- and prints pinchtab's own bundled instructions in full, with
the corrections that apply in this sandbox. Nothing to `cat` afterwards and no
other skill to load. ~6 seconds, no Chromium download. Idempotent -- re-run it
rather than debugging whether it ran.

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

## Cloak mode -- opt in, only when plain Chrome is refused

Some sites reject a plain headless Chrome on fingerprint alone, before serving
any content. For those, re-run setup with a patched Chromium runtime
([CloakBrowser](https://pinchtab.com/blog/pinchtab-0-14-0-cloakbrowser)):

```bash
bash /mnt/skills/*/headless-browser/setup.sh --cloak
```

Not the default: it downloads a browser build, so it costs a minute or two
where the ordinary path costs seconds. Reach for it after a nav comes back
blocked, not ahead of one. `CLOAK_PLATFORM`, `CLOAK_TIMEZONE`, `CLOAK_LOCALE`
and `CLOAK_SEED` override the presented fingerprint. To go back to plain
Chrome, `pinchtab config init` then re-run setup.sh without the flag.

This is for reaching ordinary public pages that blanket-block automation. It is
not a way around a login, a paywall, or a CAPTCHA, and it does not change what
you should do when a site says no: if the content is genuinely gated, tell the
user rather than working at it.

## Notes

- Never run `pinchtab skill update` or `pinchtab skill sync` -- they write
  into other agent skill directories found on the machine. This skill is
  self-contained by design.
- `pinchtab server stop` when finished. Optional; the sandbox is disposable.
