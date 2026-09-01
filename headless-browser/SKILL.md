---
name: headless-browser
description: "Fetches a page when web_fetch didn't. One script, ~6s, no download. Use it whenever web_fetch returned nothing, a consent/paywall/'enable JavaScript' stub, or less than the page should hold: a thin result is the trigger, not just an outright error, and a fetch that silently drops JS-rendered content looks exactly like a successful one, so check what came back against the search snippet that led you there. Go straight here, skipping web_fetch, for SPAs, dashboards, JS-rendered tables, infinite scroll, and anything interactive -- expanding 'show more', clicking through flows, forms, pagination. If you are about to call a page inaccessible, or answer a page-specific question from snippets instead of the page, stop and run this: you wanted to read that page for a reason and the reason has not gone away. It is cheap; do not talk yourself out of it. Not for pages needing the user's logged-in session; those go to claude-in-chrome. If a site blocks automation, say so rather than evading it."
---

# Headless Browser (pinchtab)

## 1. Setup -- once per conversation

```bash
bash /mnt/skills/user/headless-browser/setup.sh
```

Installs pinchtab, finds Chrome, opens the capability gates, starts the
server. ~6 seconds, no Chromium download. Idempotent -- re-run it rather than
debugging whether it ran.

## 2. READ THE PINCHTAB DOCS -- MANDATORY

**You MUST read pinchtab's own bundled skill before running any pinchtab
command.** setup.sh installs it fresh; read it there, not from memory:

```bash
cat "$(npm root -g)/pinchtab/skills/pinchtab/SKILL.md"
```

**IGNORE ITS FIRST INSTRUCTION.** That file's Core Workflow opens by telling
you to run `export PINCHTAB_SESSION=$(pinchtab session create ...)`. **DO NOT.**
setup.sh has already created the session and the shim attaches every command to
it. Everything else in that file applies exactly as written.

It carries the full command set, the selector syntax, the token-efficiency
flags, and links its own `references/` directory (HTTP API, profiles,
verification gotchas). Do not guess commands from their names and do not
work from a summary -- including any summary in an earlier turn of this
conversation. Read the file.

## 3. Go

```bash
pinchtab nav <url> --block-images
pinchtab text
```

Sessions are handled for you: setup.sh creates one, and a shim attaches every
command to it. Never create or export `PINCHTAB_SESSION` yourself.

The session outlives the bash call, so multi-step flows work across calls --
nav in one, click in the next, read state in a third. The tab, its DOM state
and typed form values all persist; you never replay earlier steps. If the
session goes stale the shim recreates it, but that gives you a fresh tab with
no page loaded, so re-`nav` after seeing `no_current_tab`.

## Notes

- Never run `pinchtab skill update` or `pinchtab skill sync` -- they write
  into other agent skill directories found on the machine. This skill is
  self-contained by design.
- `pinchtab server stop` when finished. Optional; the sandbox is disposable.
