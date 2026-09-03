---
name: local-mcps
description: "Runs MCP servers that claude.ai cannot otherwise reach, from the code-execution sandbox: stdio servers shipped as `npx`/`uvx` packages, which have no URL for a connector to point at, and HTTP servers whose auth header name is not on the connector allowlist. Load this and run `lmcps servers` at the start of a conversation, before concluding that a request cannot be served. Which servers are configured is personal, changes between conversations, and never appears in the tool list, so that listing is the only way to know what is available; each server's own one-line description then says what it covers. Not for stateful sessions -- every call spawns a fresh process."
---

# local-mcps

claude.ai reaches MCP only through remote connectors, which shuts out two whole
classes of server. Both are reachable from the code-execution sandbox, and this
is the client for them:

- **stdio servers.** Most of the ecosystem ships as `npx -y some-server` or
  `uvx some-server`. There is no URL to point a connector at and there never
  will be — they are libraries, not services.
- **HTTP servers with non-allowlisted auth headers.** Custom connectors let you
  add headers, but Anthropic allowlists the header *names*. A server wanting
  `tomtom-api-key` cannot be configured at all, however valid the key.

## Setup (once per conversation)

```bash
bash /mnt/skills/*/local-mcps/setup.sh
```

Puts `lmcps` on PATH, fetches the config, and prints the configured servers.
**`session-init` runs this for you — if this file reached you through its
transcript, setup is done and the listing is already above; do not run it
again.**

**Never re-derive the path or set a shell variable for it** — shell state dies
with the bash call that set it, so a command that worked last turn silently
becomes `python3: can't open file` the next. Just call `lmcps`.

If setup **fails**, stop and say so. Do not look for another way in: stdio
servers have no URL to fetch, and the HTTP ones are configured precisely because
a connector cannot send their auth header.

## Commands

```bash
lmcps servers                          # configured servers; reads config, spawns nothing
lmcps tools <server>                   # the server's own instructions, then its tools
lmcps tools <server> --schema <tool>   # one tool's full input schema
lmcps call <server> <tool> '{"a": 1}'  # invoke it
lmcps describe <server>                # what a server says about itself, to write its `description`
lmcps refresh                          # re-fetch config after editing it
```

Arguments are one JSON object, quoted. `--timeout SECS` (default 120) goes
before the verb: `lmcps --timeout 240 call ...`.

`call` prints the tool's text content verbatim, so `| jq` works directly when
that text is JSON. A tool that reports failure exits non-zero with the message
on stderr — check it rather than assuming a call worked.

`tools` results are cached for the conversation, so enumerate freely. `call`
always spawns the server — ~10–25s the first time npx or uv fetches a package,
~1–2s after — so batch your work into as few calls as the task allows rather
than probing incrementally.

## Where the config comes from

`/mcp.json` on the user's durable filesystem, in Claude Code's `mcpServers`
schema. **Read `references/config.md`, beside this file, before adding or
changing a server** — there is a required non-standard key whose absence fails
silently.

## Limits

- **Stateless only.** The sandbox reboots between turns and no daemon survives,
  so every call respawns the server. Fine for maps, search, docs and APIs;
  useless for anything holding a session — a browser MCP, a server-side cursor,
  a transaction.
- **No OAuth.** Servers behind an OAuth flow are not supported; use a claude.ai
  connector for those. Header and query-parameter auth work.
- **stdio and http/streamable-http only.** No `sse`, no WebSocket.
- **No session-required HTTP.** The HTTP path is a stateless POST; a server that
  demands an `Mcp-Session-Id` handshake will fail.
- **No MCP Apps UI.** Interactive UI resources will not render from script
  output.
