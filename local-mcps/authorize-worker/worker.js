// lmcps-authorize: the ledger of the user's approvals for lmcps calls.
//
// claude.ai's approval card is the gate. The two tools below are set to "Needs
// approval" in claude.ai, so reaching tools/call at all means the user clicked
// Allow, and recording the approval is all there is to do. lmcps then redeems it
// over /redeem, which can consume approvals but never create them, so the
// sandbox needs no secret. Only the connector, whose bearer stays with
// Anthropic's relay, can write one.
import { DurableObject } from "cloudflare:workers";

const CALL_TTL_MS = 10 * 60 * 1000;
const TOOL_TTL_MS = 12 * 60 * 60 * 1000;
const MAX_ARGS_BYTES = 64 * 1024;

// The alphabet lmcps draws conversation ids from.
const CONVERSATION_RE = /^[a-z2-9]{8}$/;

const SERVER_INFO = { name: "lmcps-authorize", title: "lmcps approvals", version: "1" };
const HANDSHAKE_VERSIONS = ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"];
const MODERN_VERSIONS = ["2026-07-28"];

const INSTRUCTIONS =
  "Approves lmcps tool calls that need the user's consent. When `lmcps call` refuses " +
  "for lack of approval, it prints the arguments to pass here. Put the authorize call " +
  "BEFORE the `lmcps call` in the same batch of tool calls; lmcps finds the approval " +
  "once the user has clicked Allow.";

const TARGET = {
  conversation: { type: "string", description: "lmcps's conversation id, as `lmcps` printed it" },
  server: { type: "string", description: "lmcps server name" },
  tool: { type: "string", description: "tool name on that server" },
};

const TOOLS = [
  {
    name: "authorize_call",
    title: "Approve one lmcps call",
    description:
      "Ask the user to approve ONE lmcps tool call with exactly these arguments. Pass " +
      "the JSON `lmcps call` printed, unchanged. The approval is spent by the first " +
      "matching `lmcps call` and expires after 10 minutes.",
    inputSchema: {
      type: "object",
      properties: { ...TARGET, args: { type: "object", description: "the tool's arguments, exactly as `lmcps call` will send them" } },
      required: ["conversation", "server", "tool", "args"],
      additionalProperties: false,
    },
    annotations: { readOnlyHint: false, destructiveHint: true, idempotentHint: false, openWorldHint: true },
  },
  {
    name: "authorize_tool_for_conversation",
    title: "Approve an lmcps tool for this conversation",
    description:
      "Ask the user to approve EVERY call to one lmcps tool, with any arguments, for " +
      "the rest of this conversation (at most 12 hours). Only when you expect several " +
      "calls to that tool; say so in chat first.",
    inputSchema: {
      type: "object",
      properties: TARGET,
      required: ["conversation", "server", "tool"],
      additionalProperties: false,
    },
    annotations: { readOnlyHint: false, destructiveHint: true, idempotentHint: false, openWorldHint: true },
  },
];

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (pathname === "/mcp") return mcp(request, env);
    if (pathname === "/redeem") return redeem(request, env);
    return new Response("not found", { status: 404 });
  },
};

function ledger(env) {
  return env.LEDGER.get(env.LEDGER.idFromName("ledger"));
}

async function redeem(request, env) {
  if (request.method !== "POST") return new Response(null, { status: 405, headers: { Allow: "POST" } });
  let body;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "body is not JSON" }, { status: 400 });
  }
  const problem = invalid(body, true);
  if (problem) return Response.json({ error: problem }, { status: 400 });
  return Response.json(await ledger(env).redeem(body));
}

// Returns what is wrong with an approval target, or null.
function invalid(a, withArgs) {
  if (!a || typeof a !== "object") return "arguments must be an object";
  if (typeof a.conversation !== "string" || !CONVERSATION_RE.test(a.conversation))
    return "`conversation` must be the 8-character id `lmcps` printed";
  for (const k of ["server", "tool"])
    if (typeof a[k] !== "string" || !a[k] || a[k].length > 200) return `\`${k}\` must be a non-empty name`;
  if (withArgs) {
    if (!a.args || typeof a.args !== "object" || Array.isArray(a.args)) return "`args` must be an object";
    if (JSON.stringify(a.args).length > MAX_ARGS_BYTES) return "`args` is too large to approve";
  }
  return null;
}

// --- MCP --------------------------------------------------------------------
//
// Stateless JSON over POST. claude.ai speaks 2026-07-28 (server/discover, no
// initialize, `resultType` required); older clients get the initialize
// handshake. Results carry the 2026-07-28 fields either way: older peers ignore them.

async function mcp(request, env) {
  if (!env.BEARER) return new Response("BEARER secret is not set", { status: 503 });
  if (!(await bearerMatches(request.headers.get("Authorization"), env.BEARER)))
    return new Response("unauthorized", { status: 401 });
  if (request.method !== "POST") return new Response(null, { status: 405, headers: { Allow: "POST" } });

  let msg;
  try {
    msg = await request.json();
  } catch {
    return rpcError(null, -32700, "parse error");
  }
  if (!msg || typeof msg !== "object" || Array.isArray(msg)) return rpcError(null, -32600, "expected one JSON-RPC message");
  if (msg.id === undefined) return new Response(null, { status: 202 });

  const params = msg.params || {};
  switch (msg.method) {
    case "server/discover":
      return rpcResult(msg.id, {
        supportedVersions: MODERN_VERSIONS,
        capabilities: { tools: {} },
        instructions: INSTRUCTIONS,
        cacheScope: "public",
        ttlMs: 0,
        _meta: { "io.modelcontextprotocol/serverInfo": SERVER_INFO },
      });
    case "initialize":
      return rpcResult(msg.id, {
        protocolVersion: HANDSHAKE_VERSIONS.includes(params.protocolVersion)
          ? params.protocolVersion
          : HANDSHAKE_VERSIONS.at(-1),
        capabilities: { tools: {} },
        serverInfo: SERVER_INFO,
        instructions: INSTRUCTIONS,
      });
    case "ping":
      return rpcResult(msg.id, {});
    case "tools/list":
      return rpcResult(msg.id, { tools: TOOLS, cacheScope: "public", ttlMs: 0 });
    case "tools/call":
      return rpcResult(msg.id, await callTool(env, params.name, params.arguments || {}));
    default:
      return rpcError(msg.id, -32601, `method not found: ${msg.method}`);
  }
}

async function callTool(env, name, args) {
  const tool = TOOLS.find((t) => t.name === name);
  if (!tool) return toolText(`unknown tool ${name}`, true);
  const whole = name === "authorize_call";
  const problem = invalid(args, whole);
  if (problem) return toolText(`Nothing was approved: ${problem}.`, true);

  const ttl = whole ? CALL_TTL_MS : TOOL_TTL_MS;
  await ledger(env).record({
    conversation: args.conversation,
    server: args.server,
    tool: args.tool,
    args: whole ? args.args : null,
    expires: Date.now() + ttl,
  });
  const until = new Date(Date.now() + ttl).toISOString().slice(11, 16);
  return toolText(
    whole
      ? `Approved once: ${args.server}/${args.tool} with these arguments, in conversation ${args.conversation}, until ${until} UTC. Run the \`lmcps call\` now.`
      : `Approved: every call to ${args.server}/${args.tool} in conversation ${args.conversation}, until ${until} UTC.`,
    false,
  );
}

function toolText(text, isError) {
  return { content: [{ type: "text", text }], isError };
}

function rpcResult(id, result) {
  return Response.json({ jsonrpc: "2.0", id, result: { ...result, resultType: "complete" } });
}

function rpcError(id, code, message) {
  return Response.json({ jsonrpc: "2.0", id, error: { code, message } });
}

// Compares digests, so the comparison takes the same time whatever the input.
async function bearerMatches(header, secret) {
  const digest = async (s) => new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s)));
  const [a, b] = await Promise.all([digest(header || ""), digest(`Bearer ${secret}`)]);
  return crypto.subtle.timingSafeEqual(a, b);
}

// --- the ledger ---------------------------------------------------------------
//
// One instance for everything. A Durable Object runs one request at a time, so
// a redeem's check-and-delete cannot race another.

export class Ledger extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    this.sql.exec(`CREATE TABLE IF NOT EXISTS approvals (
      id INTEGER PRIMARY KEY,
      conversation TEXT NOT NULL,
      server TEXT NOT NULL,
      tool TEXT NOT NULL,
      args TEXT,            -- NULL: any arguments, and not consumed by use
      expires INTEGER NOT NULL
    )`);
  }

  record({ conversation, server, tool, args, expires }) {
    this.purge();
    this.sql.exec(
      "INSERT INTO approvals (conversation, server, tool, args, expires) VALUES (?, ?, ?, ?, ?)",
      conversation, server, tool, args === null ? null : JSON.stringify(args), expires,
    );
  }

  redeem({ conversation, server, tool, args }) {
    this.purge();
    const rows = this.sql
      .exec(
        "SELECT id, args FROM approvals WHERE conversation = ? AND server = ? AND tool = ? ORDER BY args IS NULL DESC, id",
        conversation, server, tool,
      )
      .toArray();
    for (const row of rows) {
      if (row.args === null) return { ok: true, kind: "tool" };
      // Compared as parsed values, so key order and 1 vs 1.0 cannot matter.
      if (sameJson(JSON.parse(row.args), args)) {
        this.sql.exec("DELETE FROM approvals WHERE id = ?", row.id);
        return { ok: true, kind: "call" };
      }
    }
    return { ok: false };
  }

  purge() {
    this.sql.exec("DELETE FROM approvals WHERE expires <= ?", Date.now());
  }
}

function sameJson(a, b) {
  if (a === b) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const ka = Object.keys(a), kb = Object.keys(b);
  return ka.length === kb.length && ka.every((k) => Object.hasOwn(b, k) && sameJson(a[k], b[k]));
}
