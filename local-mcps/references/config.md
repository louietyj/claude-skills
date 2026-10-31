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
The same link as a second line of `config-url.txt` lets the two downloads
overlap; it is ignored unless it matches `catalogUrl`.

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

## Permissions

Each server can carry a `permissions` block, another non-standard key, deciding
per tool whether it runs freely, needs the user's approval, or never runs:

```json
"firecrawl": {
  "type": "http", "url": "...",
  "permissions": {
    "deny":  ["firecrawl_monitor_delete"],
    "ask":   [],
    "allow": ["*feedback"],
    "default": "annotations"
  }
}
```

Entries are globs over tool names. They are checked in Claude Code's order:
`deny`, then `ask`, then `allow`, then `default`. `default` is one of:

- `"annotations"`, used when it is omitted: a tool runs only if the server marks
  it `readOnlyHint: true`. Anything else, including a tool with no annotation at
  all, needs approval.
- `"allow"`: everything not listed runs. For servers that declare no annotations
  but only read; a tool such a server adds later then runs too.
- `"ask"`: everything not listed needs approval.

`lmcps tools <server>` shows the result per tool. A malformed block stops every
call to that server rather than guessing.

Approvals come from the `lmcps-authorize` worker (`authorize-worker/` in the
repo), named by a top-level `authorizeUrl` beside `catalogUrl`:

```json
"authorizeUrl": "https://lmcps-authorize.louietyj.me"
```

Without it, an `ask` tool cannot run at all.

## Several keys for one server

For services whose free tier is too small to use alone, give the server a pool
of keys and write `${LMCPS_KEY}` wherever the key goes:

```json
"alphavantage": {
  "type": "http",
  "url": "https://mcp.alphavantage.co/mcp?apikey=${LMCPS_KEY}",
  "keys": [
    { "label": "me@gmail.com",  "LMCPS_KEY": "..." },
    { "label": "alt@gmail.com", "LMCPS_KEY": "..." }
  ]
}
```

One key is picked at random per conversation and kept until `lmcps rotate`.
`label` is what gets printed, so make it something that tells you which account
it is, such as the login email. Every other field in an entry becomes a `${VAR}`
usable anywhere in the block (`url`, `headers`, `env`, `args`, `command`), so a
credential that comes in parts is one entry with several fields. A bare string
entry is shorthand for `{"LMCPS_KEY": "..."}`, labelled `#1`, `#2`, and so on.

`keys` is another non-standard key. Claude Code ignores it, but it leaves
`${LMCPS_KEY}` unset there.

## Servers that want a token file

Some OAuth servers read credentials from a *file* their auth flow writes rather
than from `env` — no good here, because the sandbox wipes the filesystem between
turns.

Bridge env to file in the `command`: carry the file's contents in a variable of
your own, write it, then `exec` the real server so stdio stays wired for
JSON-RPC.

```json
"google-health": {
  "command": "sh",
  "args": ["-c", "printf '%s' \"$GH_TOKENS\" > /tmp/gh-tokens.json && exec npx -y google-health-mcp-unofficial"],
  "env": {
    "GOOGLE_HEALTH_TOKEN_PATH": "/tmp/gh-tokens.json",
    "GH_TOKENS": "{\"access_token\":\"x\",\"expires_at\":1000000000,\"refresh_token\":\"...\"}"
  }
}
```

`GH_TOKENS` is a name we chose; the server never sees it. The placeholder
`access_token` and the long-past `expires_at` both matter — a server will often
reject a file holding only a refresh token, and a stale expiry makes it refresh
on the first call. Get the refresh token by running the server's own auth flow
once on a machine that has a browser.

That refresh token is long-lived and sits here in plaintext, so it is only as
private as the config's share link.

