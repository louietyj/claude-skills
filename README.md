# claude-skills

Skills for Claude, one subdirectory each.

- [`durable-filesystem/`](durable-filesystem/) — persistent filesystem for Claude on claude.ai, backed by a scoped Dropbox app folder.
- [`headless-browser/`](headless-browser/) — fetches a page when `web_fetch` didn't, via a headless browser (pinchtab).
- [`local-mcps/`](local-mcps/) — runs MCP servers claude.ai's connectors can't reach: `npx`/`uvx` stdio servers, and HTTP servers whose auth header name isn't allowlisted.

Each subdirectory is self-contained: its own `SKILL.md`, setup script, and README/docs where applicable.

[`userPreferences.md`](userPreferences.md) holds the always-loaded claude.ai preferences that steer these skills — what each is for, when to reach for it, what to boot at the start of a conversation. It covers the whole repo; individual skills no longer carry their own copies.
