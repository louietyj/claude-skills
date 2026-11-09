# bash-mcp

Calls one MCP tool from bash and prints the result on stdout, so Claude can pipe
it instead of reading it. `SKILL.md` is the operating guide; this file is for
repairs.

- `mcp_call.py`: the client. Standard library only, one call per process.
- `monitor_mcp.sh`: polls one tool and prints on change, for the Monitor tool.

`local-mcps`' `lmcps` started as a port of this transport, so a transport fix
here usually belongs there too.

## How it works

- **Discovery**, most specific winning: the cloud harness config
  (`/tmp/mcp-config-$CLAUDE_CODE_REMOTE_SESSION_ID.json`) → `~/.claude.json`
  `mcpServers` → `.mcp.json` at the project root → `~/.claude.json`
  `projects[cwd].mcpServers`.
- **claude.ai connectors** are all `type: http`, pointed at Anthropic's relay
  (`api.anthropic.com/v2/ccr-sessions/<session>/mcp?…&mcp_url=<upstream>`). Auth
  is the session ingress token (`$CLAUDE_SESSION_INGRESS_TOKEN_FILE`, re-read
  every call); the connector's own OAuth / API key / custom header stays inside
  the relay, so every connector looks the same from here.
- **Local http servers**: OAuth token from `~/.claude/.credentials.json`
  `mcpOAuth`, matched by `serverUrl`, non-expired, preferring the `serverName`
  match.
- **stdio** spawns `command`/`args` with your full environment plus the server's
  `env` plus `CLAUDE_PROJECT_DIR`, and does a synchronized handshake (await
  `initialize` before sending the call).
- **Session-required HTTP servers** (e.g. `mcp.atlassian.com`): on a 400
  mentioning a session it does `initialize` → `Mcp-Session-Id` →
  `notifications/initialized` → the call. Never reuse Claude Code's own session.
- **Compressed bodies**: requests send `Accept-Encoding: identity`, but the relay
  has gzipped `tools/list` anyway (2026-11-09), and urllib never decompresses.
  `read_body()` honours `Content-Encoding` and sniffs the gzip magic.
- **Unknown tools**: the relay answers them with the same `-32003` as an
  approval refusal. The harness config lists every tool, so `mcp_call.py`
  tells the two apart there.
- `${VAR}` / `${VAR:-default}` expansion applies to command/args/env/url/headers.
