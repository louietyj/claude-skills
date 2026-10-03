# claude-skills

Skills for Claude, one subdirectory each.

- [`session-init/`](session-init/) — one-touch conversation boot: sets up the two skills below, prints the memory index, prints their instructions. One tool call in place of five.
- [`durable-filesystem/`](durable-filesystem/) — persistent filesystem for Claude on claude.ai, backed by a scoped Dropbox app folder.
- [`headless-browser/`](headless-browser/) — fetches a page when `web_fetch` didn't, via a headless browser (pinchtab).
- [`local-mcps/`](local-mcps/) — runs MCP servers claude.ai's connectors can't reach: `npx`/`uvx` stdio servers, and HTTP servers whose auth header name isn't allowlisted.
- [`polish-message/`](polish-message/) — drafts emails and messages in Louie's voice: a throwaway first draft in a discarded tool call, a forced rethink, then the final. `voice.md` is his style, distilled from his sent mail.
- [`request-text/`](request-text/) — asks the user for text through a one-shot link (a form, fine on a phone). The submission lands in the sandbox as JSON, end-to-end encrypted, so secrets stay out of the transcript and long pastes are never re-emitted.
- [`retell-dialer/`](retell-dialer/) — places a real phone call and supervises it live: a voice agent runs the conversation and consults Claude mid-call, which answers within seconds and can steer unprompted.
- [`usps-ops/`](usps-ops/) — turns letters, postcards and stamps.com postage into print-ready Word files carrying Louie's own Intelligent Mail barcode, so USPS tracks each piece under his mailer ID, and logs it to QuickLetterTracker.
- [`voice-mode-guide/`](voice-mode-guide/) — run in chat before switching the mobile app to voice: prints what changes, the workarounds, and the skill index into the transcript, which survives the toggle when the injected context does not.

Each subdirectory is self-contained: its own `SKILL.md`, setup script, and README/docs where applicable. `session-init/` is the exception — it orchestrates the other two and expects them installed alongside it, reporting them as missing rather than failing if they aren't.

## Local sandbox

Everything here runs in a Linux sandbox — claude.ai's code-execution sandbox, or the Cowork / cloud Claude Code box — so debug it there rather than on Windows. [`dev/sandbox.sh`](dev/sandbox.sh) is a Docker stand-in:

```bash
dev/sandbox.sh up                  # once; idempotent
dev/sandbox.sh sh '<command>'      # SKILL.md commands run verbatim
dev/sandbox.sh sh                  # interactive shell
dev/sandbox.sh down
```

Each skill is mounted read-only at `/mnt/skills/user/<name>` straight from the working tree, so there is no zip to rebuild between edits; untracked secrets beside a skill come along as they would in the zip. `dev/work/` (gitignored) is `/work` inside, for handing in a locally built binary or pulling out logs.

The two surfaces keep skills in different places: chat under `/mnt/skills/{user,plugins}/`, Cowork and cloud Claude Code under `~/.claude/skills/synced/<bucket>/` with no `/mnt/skills/user`. Every entry point has to find both. `SANDBOX_LAYOUT=cowork SANDBOX_NAME=cw-sandbox dev/sandbox.sh up` builds a second container with the synced layout (and no `/home/claude`); pass the same `SANDBOX_NAME` to `sh`.

It is a stand-in, not a copy: `node:24-bookworm` plus Chromium's libraries, running as root. It does not reproduce the real sandbox's datacenter IP, which sites that score visitors treat differently, or claude.ai's egress policy.

[`userPreferences.md`](userPreferences.md) holds the always-loaded claude.ai preferences that steer these skills — what each is for, when to reach for it, what to boot at the start of a conversation. It covers the whole repo; individual skills no longer carry their own copies.
