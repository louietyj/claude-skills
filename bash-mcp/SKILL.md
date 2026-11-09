---
name: bash-mcp
description: >-
  Call any MCP server or claude.ai connector from bash (`mcp_call.py SERVER TOOL
  'JSON-ARGS'` → result on stdout), so an MCP result can feed a pipeline instead of landing
  in your context. Works in local Claude Code and the claude.ai cloud sandbox (Gmail, Calendar,
  Drive, Dropbox, IBKR, custom connectors). Use it whenever the useful thing is a pipeline, not a
  paste: sink a huge result to a file and read only the slice you need; `jq`/`grep` a field out;
  loop one tool over N inputs in one call; `diff` two calls; feed output to another CLI; or hand
  `monitor_mcp.sh` to the Monitor tool to get pinged when a value changes. On claude.ai only
  auto-approved tools work; tools that need an approval card stay native calls. Triggers: "grep
  the MCP output", "pipe that to a file", "result too big for context", "call this connector for
  each of these", "poll/monitor an MCP tool", "run an MCP from bash".
---

# bash-mcp

An MCP tool call normally lands its whole result in your context. `mcp_call.py`
makes the same call from bash and prints the result to **stdout**, so it becomes
bytes you can redirect, filter, loop over or stream, and it costs context only
where you choose to read it.

## Finding the scripts

Shell variables die with the bash call that set them, so resolve the path inside
each command that uses it:

```bash
M="python3 -I $(ls -1dt /mnt/skills/*/bash-mcp ~/.claude/skills/synced/*/bash-mcp ~/.claude/skills/bash-mcp 2>/dev/null | head -1)/mcp_call.py"
$M --servers
```

## Commands

```bash
$M --servers                              # every server it can reach, and where
$M <server> --list                        # tool names, one per line
$M <server> --describe                    # name <TAB> approval policy <TAB> blurb
$M <server> --schema <tool>[,<tool>]      # input schemas, one JSON line each — read before calling
$M <server> <tool> '<json-args>'          # call it; text content on stdout
$M <server> <tool> --args-file <path|->   # args from a file or stdin (build them with jq)
$M --timeout 300 <server> <tool> ...      # default 120 s
```

`<server>` is the name in the native tool name (`mcp__Google_Calendar__list_events`
→ `Google_Calendar`). `<tool>` may be the bare name or the full native name.

Exit codes: **0** ok · **1** error (tool `isError`, JSON-RPC error, HTTP failure;
message on stderr) · **3** the claude.ai relay refused the call because it needs
your approval. Check them; don't assume a call worked.

Images, audio and binary resources are saved to temp files, each leaving a line
like `[image: /tmp/mcp_call.xxxx.png (image/png)]` where it sat. To *see* an
image, use the native tool instead: it shows it to you and also saves a copy
under `tool-results/`. Go through bash only when a script handles many images
unseen, e.g. diffing a run of screenshots.

## Examples

**Pull one field out of a big payload.** Read counts, ids and the fields you
need, not the payload — the point is that it never enters your context.

```bash
$M Gmail search_threads '{"query":"newer_than:7d has:attachment","pageSize":50}' > /tmp/t.json
jq '.threads | length' /tmp/t.json
```

**Loop one tool over N inputs** — one bash call instead of N tool calls:

```bash
for q in "from:belong" "from:schwab" "from:fragomen"; do
  printf '%s\t' "$q"; $M Gmail search_threads "$(jq -nc --arg q "$q" '{query:$q,pageSize:50}')" | jq '.threads|length'
done
```

**Args with awkward text** (quotes, backticks, `$`, multi-KB bodies): build them
with `jq` and pipe them in, never hand-quote into argv.

```bash
jq -n --rawfile body /tmp/draft.html '{fileId:"…", content:$body}' | $M Google_Drive update_file --args-file -
```

**Stream to the Monitor tool.** Monitor turns each stdout line into a
notification but can't watch an MCP tool; `monitor_mcp.sh` bridges it:
`<server> <tool> <json-args> [jq-filter] [interval-secs] [--match REGEX]`. It
prints on change (with `--match`, on every matching poll), always prints errors,
and stops with exit 3 if the tool needs approval.

```
Monitor(
  command: "bash \"$(ls -1dt /mnt/skills/*/bash-mcp ~/.claude/skills/synced/*/bash-mcp ~/.claude/skills/bash-mcp 2>/dev/null | head -1)/monitor_mcp.sh\" \
            Interactive_Brokers_IBKR get_account_summary '{}' '.excess_liquidity' 300",
  description: "IBKR excess liquidity",
  persistent: true
)
```

## Approval-gated tools (claude.ai)

On claude.ai each connector tool is "always allow" or "always ask"
(`--describe` shows which). The relay enforces "always ask" server-side: an
approval is minted only by the person clicking a card that Claude Code raises
for a native tool call, bound to that call's id and exact arguments, and used
once. A call from bash gets no card, just exit 3. Don't retry or poll — it can't
succeed. Instead:

- **Do the gated step as a native tool call.** Bash does the reads, filtering
  and building of the arguments; the one write goes through the native tool,
  and the person approves the card as usual.
- **Or ask the person** to switch that tool to "Always allow" in the
  connector's settings for the few minutes the script needs, then back.

## Known limits

- **The policy `--describe` shows is a snapshot** from session start. The relay
  checks the live setting.
- **Local OAuth expiry (~1h)** can't be refreshed from outside Claude Code;
  refresh once via `/mcp` or `claude mcp login <server>`.
- **Transports**: `stdio` and `http`/`streamable-http` only. `headersHelper` is
  not run.
- Each call spins up fresh — fine at intervals of seconds, not sub-second loops.

`README.md` covers discovery, auth and transport internals, for repairs.
