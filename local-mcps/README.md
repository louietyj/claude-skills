# local-mcps

Runs MCP servers that claude.ai's remote connectors cannot reach, from the code-execution sandbox.

```
SKILL.md                 the skill Claude reads; commands, costs, limits
bin/lmcps.py             the CLI (stdlib only — the sandbox cannot pip install)
setup.sh                 puts `lmcps` on PATH and prints the configured servers
routine/refresh.py       rebuilds the tool index; runs on the cloud box, not here
routine/make-setup-script.py   prints the cloud environment's setup script
user-preferences.md      text to paste into claude.ai personal preferences
package.py               builds local-mcps.zip for upload
config-url.txt           share link to the config — gitignored, and a secret
config.example.json      the config schema, with a worked example of each transport
test_lmcps.py            offline tests (no network, no npx)
fake_mcp_server.py       the misbehaving stdio server those tests run against
integration_test.sh      live tests against real npx and uvx servers
```

## The problem

claude.ai supports MCP only through **remote connectors**, and that leaves two classes of server unreachable.

**Remote servers whose auth isn't OAuth.** Custom connectors let you add request headers, but Anthropic allowlists the header *names*: `authorization`, `x-api-key` and `x-auth-token` are approved, anything else is rejected at save time. TomTom is the worked example — probing `https://mcp.tomtom.com/maps` directly, `tomtom-api-key: <key>` is the only auth form that returns 200, and it is the only one Claude won't send. Its OAuth route (the directory connector) drops every few hours, because Claude stores the refresh token but never exercises it. Both doors are shut.

**stdio servers nobody hosts.** Most of the ecosystem ships as `npx -y some-server` or `uvx some-server`. There is no URL to point a connector at, and there never will be for most of them — they're libraries, not services. This is the much bigger class, and the real reason the skill exists.

The sandbox, meanwhile, has node 22, npm, npx, uv, uvx and python 3.12, and its egress is not restricted to a domain list. Everything needed to run these servers is already there; only a client was missing.

## Where this came from

`bin/lmcps.py` is a port of the `monitor-mcp` Claude Code skill's `mcp_call.py`, which already called MCP servers from outside Claude Code so the Monitor tool could poll them. The transport work carried over intact. What changed:

- **Discovery was replaced entirely.** The original merged `~/.claude.json`, a `.mcp.json` found by walking up from the cwd, and `projects[cwd]`. None of that exists on claude.ai. There is deliberately **no** fall back to `~/.claude.json` — it would work on a Claude Code development machine and fail on the one platform this targets, which is the worst possible place for a fallback to hide a bug. A test pins that.
- **OAuth was deleted.** `call_http` demanded a token from `~/.claude/.credentials.json` whenever `Authorization` was absent, which killed exactly the header-auth servers this exists for. Configured headers now go out verbatim and nothing is invented.
- **`monitor_mcp.sh` was dropped.** There is no Monitor tool on claude.ai, and a `while true` loop cannot outlive the turn that starts it.

Two latent bugs in the original were fixed on the way:

- **JSON-RPC ids are strings now** (`lmcps-init`, `lmcps-call`). They were fixed integers `1` and `2`, which can collide with a server-initiated request's id.
- **Server→client requests are answered, not skipped.** The original's read loop discarded anything that wasn't the response it wanted. A server that blocks waiting on `roots/list` would hang until the timeout with nothing to report. There is now a dispatch loop: `roots/list` gets an empty list, anything else gets a polite `-32601`. `test_a_server_request_is_answered_rather_than_skipped` fails by timing out if that dispatch is removed — it was checked.

## Design: the catalog, and why it came back

An earlier design had `lmcps sync` enumerate every server's `tools/list` into a durable catalog, invalidated on a hash of the config, so that `lmcps servers` could be cheap. It was deleted, for a good reason: the preferences text makes `servers` run at the top of every conversation, and spawning N servers at ~10-25s each to build a catalog there would make the instruction something to resent and then ignore.

It is back, because a **free cloud cron** moved that cost off the boot path entirely. A Claude Code routine fires every five hours to refresh a session limit; `routine/refresh.py` rides along, spawns every server on that box, and leaves `/mcp-catalog.json` beside the config. Measured cold on that box: ~18s for three servers, which nobody waits for. `lmcps servers` then reads the result over a share link, exactly as it reads the config.

What makes it safe this time is that **the catalog is display-only**. It holds tool names and descriptions. No schemas, no `instructions`. `lmcps tools` still spawns and is still live, `--schema` included.

Descriptions are stored **whole** and truncated to 80 columns only when printed. The index is cheap to store and expensive to rebuild, so the fidelity is worth keeping: the display budget is then a rendering decision that can be changed without waiting five hours for a refetch. Truncation collapses whitespace rather than cutting at the first newline, because a tool's `description` is frequently a paragraph whose first line is a fragment.

That single constraint answers the objection the original deletion rested on -- *a live `tools/list` can never be stale the way a cached schema silently rots* -- because nothing the model acts on is ever served from the cache. A stale catalog can mislead about **what exists**, never about **how to call it**. So there is no config-hash invalidation, no stale-schema warning, and no retry-on-invalid-params machinery; staleness is a display concern by construction rather than by promise.

The split is the one claude.ai itself makes for deferred tool loading: **catalog** (what exists -- cheap, always in context) against **schema** (how to call -- expensive, fetched on demand). What was missing here was never phase 2; `tools --schema` has always existed. It was phase 1. Without a tool-level index, deciding whether a request is servable cost a spawn per server, so in practice the question went unasked and configured servers went unused. A hand-written per-server `description` is a good routing trigger and a poor answer to *"can this server do X?"*.

`servers` closes with a blunt reminder that it has shown names only and that parameters must come from `tools --schema`. The redundancy is deliberate: a catalog reads like enough to call from, and a confabulated `lmcps call` costs a full server spawn to discover.

Within a conversation, `tools` results are still cached under `$LMCPS_HOME`, and that still needs no invalidation story: the sandbox disk lives exactly as long as the conversation. Measured on `@modelcontextprotocol/server-everything`: 23s cold, 0.5s cached.

The config stays **read-only** from the claude.ai side, and `local-mcps` still needs nothing else installed there -- both the config and the catalog arrive over plain Dropbox share links with `urllib`. The only writer is `refresh.py`, and it runs somewhere else entirely.

### Nothing watches the cron

The refresh job has no alerting channel and does not deserve one. It stamps `builtAt` into the catalog, and `servers` prints `(index last built 3d ago -- the refresh routine may be broken)` once that passes 24 hours. That surfaces a dead cron in the one place where it is actually costing something, and nowhere else.

`builtBy` carries a hash of `refresh.py` itself, because the script is deployed by pasting it into a cloud environment's setup script -- so the running copy can drift from the repo, and the hash is what makes that visible.

A failed run is inert by design: a server that fails to enumerate keeps its last-good entry (`--previous` carries the merge), so one npm-registry hiccup cannot blank the index for five hours, and `refresh.py` always exits 0 so it can never take down the routine whose real job is the session limit.

## Setup

1. Put the config at the root of the durable filesystem — `.../Apps/louietyj-claude-ai/mcp.json`. Use Claude Code's `mcpServers` schema so blocks copy-paste from any server's README; see `config.example.json`. Give every server a `description` (`lmcps describe <server>` gathers the material), because that is the only thing standing between a configured server and never being used.
2. In Dropbox, right-click it → **Copy link**, and paste the URL into `config-url.txt` (see `config-url.example.txt`). Paste it as-is — `lmcps` rewrites the query string itself.
3. `python package.py` → produces `local-mcps.zip`.
4. Upload the zip to claude.ai under Settings → Capabilities → Skills. It lands under `/mnt/skills/user/` or `/mnt/skills/plugins/` depending on the install route, which is why `SKILL.md` invokes setup through a glob rather than a literal path.
5. Paste `user-preferences.md` into Settings → Profile → personal preferences.
6. Confirm code-execution network egress allows `dropbox.com` (for the config) plus whatever your servers need — `registry.npmjs.org` and `pypi.org` for `npx`/`uvx`.

Changing a server afterwards is an edit to `mcp.json` and `lmcps refresh` in the conversation. No repackaging, no re-upload.

### Setting up the refresh routine

The tool index is optional — without it `servers` prints what it always printed — so this is a second pass, and the order matters: a Dropbox share link can only be made for a file that already exists.

1. Run `refresh.py` once by hand, on a box that can reach both Dropbox and your servers. `/mcp-catalog.json` appears beside the config.
2. Run it a **second** time, then confirm the file's share link still serves. The whole read path depends on `mode=overwrite` updating the file in place rather than replacing it; check that once here rather than discovering it silently broken weeks later.
3. In Dropbox, right-click `/mcp-catalog.json` → **Copy link**, and add it to `mcp.json` as a top-level `catalogUrl`, beside `mcpServers`.

   **A share link follows a rename.** If `/mcp-catalog.json` came into being by renaming `/mcp.json` rather than by a fresh upload, the link already in `config-url.txt` now points at the catalog — and its URL still *says* `mcp.json`, so it looks right. Both links must be re-checked against `get_shared_link_metadata`, not against what the URL reads. `lmcps` names this specific mix-up rather than reporting a config with no `mcpServers`, because the generic error sends you to inspect a config that is fine.
4. `python package.py` and re-upload, so the skill knows to look for `catalogUrl`.
5. Create a cloud environment (**Cron**), and paste `python routine/make-setup-script.py` output into its Setup script. Give it no connectors — the job authenticates to Dropbox directly and needs none.
6. Point a scheduled routine at that environment. The prompt runs one line and reads nothing:

   ```
   Run exactly this, then respond with "hi". Do not read its output or do anything else.

   lmcps-refresh || true
   ```

The `|| true` is not decoration. That routine's real job is refreshing a session limit; the index is a passenger, and a passenger must never be able to stop the car.

Note what is **not** in this list: no new skill to upload, no new credential. `refresh.py` reads `credentials.json` out of the `durable-filesystem` skill's own mount — the one file it needs from that skill — and everything else it uses is already on the box.

### Who describes a server?

`description` is **not** part of the standard `mcpServers` schema — `lmcps` adds the key. MCP's own answer to "what is this server for?" is `serverInfo` and the `instructions` string in the initialize result, plus each tool's own `description`.

That is the only real source, so the config's `description` is a **cache of it**, not an independent opinion. It exists because reading the real thing costs a spawn, and `lmcps servers` runs at the top of every conversation.

In practice `instructions` is usually absent. Of the three real servers measured here, only `@modelcontextprotocol/server-everything` — the reference implementation — ships one; `mcp-server-time` and TomTom both answer `initialize` with a `serverInfo` and nothing else. Server authors instead put their routing guidance *inside tool descriptions*, because that is what every client reliably surfaces: TomTom's read *"Use this tool FIRST when the user asks about traffic, accidents, road closures"* and *"Do NOT use tomtom-dynamic-map to plot traffic incidents as markers"*.

So for most servers the config line is less a cache than a genuine summary, synthesised from eighteen tool descriptions that each assume you have already picked the server. That is the work `describe` sets up and declines to do itself.

`lmcps describe <server>` populates the cache: it spawns the server once and prints everything the server says about itself — title, every tool with its description, and the full `instructions` — then asks the caller to write the line.

It offers **no generated candidate**, and that took two attempts to get right. Extracting a sentence from `instructions` fails because that prose is written to be read once you have already chosen the server: the first real sentence of `server-everything`'s is *"Follow them to use, extend, and troubleshoot the server safely and effectively"* — confident, well-formed, useless. Falling back to joined tool names fails for a subtler reason. It is accurate, so it survived review, but `"tomtom-get-api-key, tomtom-get-app-config, tomtom-get-viz-data, ..."` answers *what the server exposes* when the question is *what it is for*, and it looks pasteable enough that it gets pasted.

Reducing eighteen tool descriptions to "geocoding, routing, live traffic, EV range" is a judgement call. `describe` is nearly always run by a model mid-conversation, which is good at exactly that, so the command does retrieval and hands the judgement over instead of faking it with a default nobody should accept.

Unknown keys are ignored by Claude Code, so a block still copy-pastes in both directions.

**Triggers live in the config, not in `SKILL.md`.** It is tempting to put domain keywords in the skill's `description` — "geocoding, routing, live traffic" — so it fires on a request that needs routing and never says the word MCP. Don't: the configured set changes, the description would go stale silently, and nothing enforces the two staying in step. `SKILL.md` describes only the mechanism; the per-server `description` in `mcp.json` carries the trigger and is surfaced at discovery time, which is the one place it cannot be out of date.

The cost of that choice is real and worth stating: the preferences text is now the *only* thing that reliably gets the skill loaded, and it is per-account configuration rather than a property of the skill, so it doesn't travel if the skill is shared or used on an account without it. That is the right trade — a stale trigger is worse than an absent one, because it fires confidently on the wrong things — but it means the preferences text is load-bearing rather than belt-and-braces.

## Discoverability is the hard part

The servers do not appear in Claude's tool list and are not connectors, so nothing in the harness hints they exist. Discovery is therefore two-stage, and both stages are unconditional:

- **The preferences text** loads into every conversation and says to run setup *before answering*. Two things in it are load-bearing. It states that absence from the tool list is not evidence the capability is missing, since that inference is otherwise the obvious one to draw. And it says to load the skill to find out what exists, rather than once something looks worth reaching for — because the skill describes only the mechanism, you cannot tell whether a request is covered until you have read the listing, so deferring the load until it "seems relevant" is circular.
- **`setup.sh` prints the server listing** rather than swallowing it. Setup and discovery are the same act, so the listing lands in context as a side effect of installing, and each server's own `description` supplies the trigger from there.

This is also why `servers` must not spawn. A test pins that a server with a nonexistent `command` still lists cleanly: an instruction that runs at the top of every conversation has to be safe when part of the config is broken, or it gets dropped.

## Security posture

The config sits in plaintext in a private Dropbox app folder, and holds API keys. `${VAR}` and `${VAR:-default}` expansion works in `command`, `args`, `env`, `url` and `headers`, so a key can be pasted per conversation instead — but the default is plaintext at rest, chosen deliberately over a manual step every conversation.

**`config-url.txt` ships in the zip, so the zip is a credential.** Don't commit it, don't share it. It is a strictly weaker one than `durable-filesystem`'s `credentials.json`, which grants read *and write* on the whole store; this grants read on one file.

The share link is unlisted rather than secret — anyone with the URL can fetch the config, without a Dropbox account. Treat it accordingly, and revoke it in Dropbox if it leaks.

## Testing

```
python test_lmcps.py       # 67 offline tests, no network and no npx
bash integration_test.sh   # 18 live tests against real npx and uvx servers
TOMTOM_KEY=... bash integration_test.sh   # +2, against the real TomTom server
```

The offline suite runs against `fake_mcp_server.py`, a stdio server with switchable misbehaviour — it interleaves log lines with responses, blocks on a `roots/list` request until answered, sends an unsupported request, advertises `instructions`, or dies without a handshake — and an in-process HTTP stub. It covers config resolution and every way it can fail, `${VAR}` expansion, string JSON-RPC ids, the dispatch loop, stderr surfacing, in-band `isError`, SSE unwrapping, and that no `Authorization` header is ever invented.

The catalog gets its own group, and it is mostly failure cases, because `servers` runs at the top of every conversation and every one of them has to degrade rather than break: a missing index, a corrupt one, a server the index has never seen, a server that left the config, a per-server build error, and a build older than a day. `$LMCPS_CATALOG` points the tests at a fixture so none of it touches the network. The build side is pinned separately — that a broken server cannot fail the build, that it keeps its last-good entry when `--previous` has one, and that no `inputSchema` ever reaches the catalog.

The live suite covers the part a fake cannot, and earned its keep twice. Both bugs were in the print path that every server's output goes through, and neither was reachable from a fixture, because both needed a real server writing real prose:

- **A crash on non-ASCII output.** TomTom's tool descriptions contain arrows and dashes, and printing them died on a non-UTF-8 stdout. `sys.stdout` is now reconfigured explicitly.
- **Mojibake on the way in.** `subprocess` with `text=True` decodes the child's stdout using the *locale* encoding — cp1252 on Windows, whatever `LANG` says in the sandbox. MCP stdio is UTF-8 by specification, so the encoding is now stated rather than inherited. The failure mode was silent corruption, not an error, and it survived one bad verification: at a terminal, mojibake and correct text can each render as the other, so the fix was confirmed by comparing code points.

## Limits

Inherited from the sandbox, and not fixable here:

- **Stateless servers only.** The sandbox reboots between turns and no daemon survives one, so every call respawns. Fine for maps, search, docs and APIs; useless for a browser MCP holding a session or anything with a server-side cursor.
- **No OAuth-gated servers.** Would need a device flow or a hand-pasted token. Use a connector for those.
- **No session-required HTTP.** The HTTP path is a stateless POST with no `initialize`; a server demanding `Mcp-Session-Id` will fail. TomTom tolerates statelessness.
- **No `sse` or WebSocket transports.**
- **No MCP Apps UI resources.** They won't render from script output regardless.
