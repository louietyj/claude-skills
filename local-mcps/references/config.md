# Where the config comes from

Reference for the `local-mcps` skill. Read this when adding or changing a
server; the SKILL.md covers everything needed to *use* the ones already there.


One JSON file, in Claude Code's `mcpServers` schema, so a block copied out of
any server's README works unchanged. It lives on the user's durable filesystem
and is fetched over a share link at setup, then cached for the conversation.

To **add or change a server**, edit that file — `/mcp.json` on the durable
filesystem, via the `durable-filesystem` skill — then run `lmcps refresh`. The
share link is served through a CDN, so a very recent edit can take a few minutes
to show up; `refresh` already asks for an uncached copy.

A top-level `catalogUrl` beside `mcpServers` holds a share link to
`/mcp-catalog.json`, the tool index that `lmcps servers` prints under each
server. It is optional: without it, `servers` prints server lines only. The
index is rebuilt every few hours by `routine/refresh.py`, so a server added here
shows `(tools not indexed)` until the next run — which is correct, not broken.

**Every server needs a `description`.** It is a non-standard key `lmcps` adds to
the schema, and it is what `lmcps servers` prints — the trigger conditions for
this skill live there and nowhere else, so a server without one will never be
picked.

It is a **cache of what the server says about itself**, not an independent
opinion. The authoritative account is the server's `serverInfo` and
`instructions` from the handshake, which `lmcps tools` prints — but reading it
costs a spawn, which is exactly what `servers` exists to avoid. So the config
holds a copy.

After adding a server, run `lmcps describe <server>`. It spawns the server once
and prints everything the server says about itself — title, every tool with its
description, and its full `instructions`. Write the line from that and paste it
into the server's block. `describe` offers no generated line: reducing that
material to one useful sentence is the judgement, and it is yours to make.

