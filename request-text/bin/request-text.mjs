#!/usr/bin/env node
// request-text: ask the user for text through a link, and get it back in the
// shell as JSON without it ever passing through the model's own output.
//
// Each request is a detached `serve` process that owns the tunnel. It holds an
// ECDH private key in memory only; the page encrypts to the matching public
// key, so the relay and the sandbox's TLS-intercepting egress see ciphertext.

import { spawn } from "node:child_process";
import { webcrypto, randomBytes } from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const { subtle } = webcrypto;
const SELF = fileURLToPath(import.meta.url);
const FORM = path.join(path.dirname(SELF), "..", "web", "form.html");
const HOME = process.env.REQUEST_TEXT_HOME || path.join(os.homedir(), ".request-text");
const HOSTC = process.env.REQUEST_TEXT_HOSTC || "hostc";
const MAX_BODY = 8 * 1024 * 1024;
const KINDS = ["line", "secret", "multiline"];

const EXIT = { OK: 0, ERROR: 1, TIMEOUT: 2, URL_CHANGED: 3, GONE: 4, USAGE: 64 };

class UsageError extends Error {}

// --- state on disk -----------------------------------------------------------

const dirOf = (id) => path.join(HOME, id);

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return null;
  }
}

// Rename is atomic, so a reader never sees a half-written file.
function writeJson(file, obj, mode = 0o600) {
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(obj) + "\n", { mode });
  fs.renameSync(tmp, file);
}

// A detached server is reparented to PID 1, which may never reap it, so an
// exited one can linger as a zombie that still answers kill(pid, 0).
function alive(pid) {
  if (!pid) return false;
  try {
    process.kill(pid, 0);
  } catch {
    return false;
  }
  try {
    const stat = fs.readFileSync(`/proc/${pid}/stat`, "utf8");
    return stat.slice(stat.lastIndexOf(")") + 2)[0] !== "Z";
  } catch {
    return true;  // no procfs: trust kill()
  }
}

function tail(file, n = 20) {
  try {
    return fs.readFileSync(file, "utf8").trimEnd().split("\n").slice(-n).join("\n");
  } catch {
    return "(no log)";
  }
}

function requireRequest(id) {
  if (!id) throw new UsageError("missing request id");
  const meta = readJson(path.join(dirOf(id), "meta.json"));
  if (!meta) {
    const err = new Error(`no such request: ${id} (closed, or opened in another sandbox)`);
    err.exitCode = EXIT.GONE;
    throw err;
  }
  return meta;
}

// --- crypto --------------------------------------------------------------------

const b64u = (buf) => Buffer.from(buf).toString("base64url");
const unb64u = (s) => new Uint8Array(Buffer.from(String(s), "base64url"));
const ECDH = { name: "ECDH", namedCurve: "P-256" };

async function aesKey(privateKey, peerPublic, id, usage) {
  const enc = new TextEncoder();
  const shared = await subtle.deriveBits({ name: "ECDH", public: peerPublic }, privateKey, 256);
  const hkdf = await subtle.importKey("raw", shared, "HKDF", false, ["deriveKey"]);
  return subtle.deriveKey(
    { name: "HKDF", hash: "SHA-256", salt: enc.encode(id), info: enc.encode("request-text v1") },
    hkdf, { name: "AES-GCM", length: 256 }, false, [usage]);
}

async function unseal(privateKey, id, body) {
  const epk = await subtle.importKey("raw", unb64u(body.epk), ECDH, false, []);
  const key = await aesKey(privateKey, epk, id, "decrypt");
  const plain = await subtle.decrypt(
    { name: "AES-GCM", iv: unb64u(body.iv), additionalData: new TextEncoder().encode(id) },
    key, unb64u(body.ct));
  return JSON.parse(new TextDecoder().decode(plain));
}

// The page's own `seal`, lifted out of form.html and run here, so the self-test
// and the tests exercise exactly the code a phone runs.
export function pageSeal(html) {
  const m = html.match(/<script id="seal">([\s\S]*?)<\/script>/);
  if (!m) throw new Error("form page has no seal script");
  return new Function("crypto", "btoa", "atob", `${m[1]}\nreturn seal;`)(webcrypto, btoa, atob);
}

// Fill in a form the way a browser would: fetch the page, encrypt with its key, post.
export async function submitViaPage(url, fields) {
  const page = await fetch(url);
  if (!page.ok) throw new Error(`GET ${url}: HTTP ${page.status}`);
  const html = await page.text();
  const cfg = JSON.parse(html.match(/<script type="application\/json" id="config">([\s\S]*?)<\/script>/)[1]);
  const body = await pageSeal(html)(subtle, cfg.key, cfg.id, fields);
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return { status: res.status, text: await res.text() };
}

// --- serve (the detached per-request process) ----------------------------------

async function serve(id) {
  const dir = dirOf(id);
  const meta = requireRequest(id);
  const statePath = path.join(dir, "state.json");
  const state = { status: "starting", pid: process.pid, hostcPid: null, port: null, url: null, urls: [] };
  const save = () => writeJson(statePath, state);
  const log = (msg) => process.stdout.write(`${new Date().toISOString()} ${msg}\n`);

  const keys = await subtle.generateKey(ECDH, false, ["deriveBits"]);
  const publicKey = b64u(await subtle.exportKey("raw", keys.publicKey));
  const config = JSON.stringify({ id, key: publicKey, title: meta.title, fields: meta.fields })
    .replace(/</g, "\\u003c");
  const page = fs.readFileSync(FORM, "utf8").replace("__CONFIG__", () => config);

  let hostc = null;
  let finished = false;

  // lingerMs keeps the tunnel up after the state is final: killing hostc right
  // after a reply drops it in transit, and the browser shows a 502 for a success.
  let lingering = null;
  function teardown() {
    clearTimeout(lingering);
    if (hostc && hostc.exitCode === null) hostc.kill("SIGTERM");
    server.close();
    setTimeout(() => process.exit(0), 1500).unref();
  }
  function finish(status, detail, lingerMs = 0) {
    if (finished) return;
    finished = true;
    state.status = status;
    if (detail) state.detail = detail;
    save();
    log(`finishing: ${status}${detail ? ` (${detail})` : ""}`);
    lingering = setTimeout(teardown, lingerMs);
  }

  const headers = {
    "cache-control": "no-store",
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
    "x-robots-tag": "noindex",
  };
  const reply = (res, code, type, body, extra = {}) => {
    res.writeHead(code, { ...headers, "content-type": type, ...extra });
    res.end(body);
  };

  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, "http://x").pathname.replace(/\/+$/, "");
    if (pathname !== `/${meta.token}`) return reply(res, 404, "text/plain", "not found\n");
    if (finished) return reply(res, 410, "text/plain", "this link has already been used\n");

    if (req.method === "GET" || req.method === "HEAD") {
      return reply(res, 200, "text/html; charset=utf-8", req.method === "HEAD" ? "" : page, {
        "content-security-policy":
          "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; " +
          "connect-src 'self'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'",
      });
    }
    if (req.method !== "POST") return reply(res, 405, "text/plain", "method not allowed\n");

    const chunks = [];
    let size = 0;
    req.on("data", (c) => {
      size += c.length;
      if (size > MAX_BODY) {
        reply(res, 413, "text/plain", "too large\n");
        req.destroy();
      } else {
        chunks.push(c);
      }
    });
    req.on("end", async () => {
      if (size > MAX_BODY || finished) return;
      let fields;
      try {
        fields = await unseal(keys.privateKey, id, JSON.parse(Buffer.concat(chunks).toString("utf8")));
      } catch (err) {
        log(`rejected a submission: ${err.name}: ${err.message}`);
        return reply(res, 400, "text/plain", "could not decrypt the submission\n");
      }
      const expected = meta.fields.map((f) => f.name).sort().join(",");
      const valid = fields && typeof fields === "object" && !Array.isArray(fields) &&
        Object.keys(fields).sort().join(",") === expected &&
        Object.values(fields).every((v) => typeof v === "string");
      if (!valid) return reply(res, 400, "text/plain", "the submission does not match this form\n");

      writeJson(path.join(dir, "payload.json"), fields);
      log(`received ${size} bytes`);
      reply(res, 200, "application/json", '{"ok":true}\n');
      finish("received", null, 5000);
    });
  });

  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  state.port = server.address().port;
  log(`listening on 127.0.0.1:${state.port}`);

  const ttl = setTimeout(() => finish("expired"), meta.ttlMin * 60_000);
  ttl.unref();
  // An explicit close skips the linger after a submission.
  for (const sig of ["SIGTERM", "SIGINT", "SIGHUP"]) {
    process.on(sig, () => (finished ? teardown() : finish("closed")));
  }

  if (meta.local) {
    state.url = `http://127.0.0.1:${state.port}/${meta.token}`;
    state.urls.push(state.url);
    state.status = "open";
    save();
    return;
  }
  save();

  hostc = spawn(HOSTC, [String(state.port), "--local-host", "127.0.0.1"], {
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, NO_COLOR: "1", FORCE_COLOR: "0" },
  });
  state.hostcPid = hostc.pid;
  save();

  // hostc prints "Public URL:" once per tunnel. A reconnect mints a new tunnel
  // with a new URL, which strands the link the user already has.
  let buf = "";
  const onOutput = (chunk) => {
    const text = chunk.toString();
    fs.appendFileSync(path.join(dir, "hostc.log"), text);
    buf += text;
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl);
      buf = buf.slice(nl + 1);
      const m = line.match(/Public URL:\s*(https:\/\/\S+)/);
      if (!m) continue;
      const url = `${m[1].replace(/\/+$/, "")}/${meta.token}`;
      if (url === state.url) continue;
      state.url = url;
      state.urls.push(url);
      if (state.status === "starting") state.status = "open";
      save();
      log(`tunnel ready: ${m[1]}`);
    }
  };
  hostc.stdout.on("data", onOutput);
  hostc.stderr.on("data", onOutput);
  hostc.on("error", (err) => finish("error", `could not start hostc: ${err.message}`));
  hostc.on("exit", (code, sig) => {
    if (!finished) finish("error", `hostc exited (${sig || `code ${code}`}); see ${path.join(dir, "hostc.log")}`);
  });
}

// --- open ------------------------------------------------------------------------

function parseField(spec) {
  const [name, kind = "multiline", ...rest] = spec.split(":");
  const label = rest.join(":") || name;
  if (!/^[a-z0-9_]{1,40}$/.test(name)) {
    throw new UsageError(`bad field name '${name}': use a-z, 0-9, _ (it becomes a JSON key)`);
  }
  if (!KINDS.includes(kind)) throw new UsageError(`bad field kind '${kind}': one of ${KINDS.join(", ")}`);
  return { name, kind, label };
}

async function open(args) {
  const fields = [];
  let title = "Claude is asking for some text";
  let ttlMin = 30;
  let local = false;
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    const val = () => {
      if (i + 1 >= args.length) throw new UsageError(`${a} needs a value`);
      return args[++i];
    };
    if (a === "--field") fields.push(parseField(val()));
    else if (a === "--title") title = val();
    else if (a === "--ttl-min") ttlMin = Number(val());
    else if (a === "--local") local = true;
    else throw new UsageError(`unknown option: ${a}`);
  }
  if (!fields.length) fields.push(parseField("text:multiline:Text"));
  if (new Set(fields.map((f) => f.name)).size !== fields.length) throw new UsageError("duplicate field name");
  if (!(ttlMin > 0)) throw new UsageError("--ttl-min must be a positive number");

  const id = `rt-${randomBytes(4).toString("hex")}`;
  const dir = dirOf(id);
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  fs.chmodSync(HOME, 0o700);
  const meta = { id, token: randomBytes(16).toString("base64url"), title, fields, ttlMin, local,
    created: new Date().toISOString() };
  writeJson(path.join(dir, "meta.json"), meta);

  // detached puts serve in its own session (setsid), outside the process group
  // the sandbox reaps when this bash call ends.
  const logFd = fs.openSync(path.join(dir, "serve.log"), "a", 0o600);
  const child = spawn(process.execPath, [SELF, "serve", id], {
    detached: true,
    stdio: ["ignore", logFd, logFd],
    env: process.env,
  });
  child.unref();

  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    const st = readJson(path.join(dir, "state.json"));
    if (st && st.status === "open" && st.url) {
      writeJson(path.join(dir, "announced.json"), { url: st.url });
      process.stdout.write(`id:  ${id}\nurl: ${st.url}\n`);
      return EXIT.OK;
    }
    if (st && (st.status === "error" || !alive(child.pid))) break;
    await new Promise((r) => setTimeout(r, 250));
  }
  process.stderr.write(`could not open ${id}: ${readJson(path.join(dir, "state.json"))?.detail || "no public URL within 60s"}\n` +
    `--- serve.log ---\n${tail(path.join(dir, "serve.log"))}\n--- hostc.log ---\n${tail(path.join(dir, "hostc.log"))}\n`);
  await close([id], { quiet: true });
  return EXIT.ERROR;
}

// --- wait ------------------------------------------------------------------------

async function wait(args) {
  let id = null;
  let timeout = 240;
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--timeout") timeout = Number(args[++i]);
    else if (!id && !args[i].startsWith("-")) id = args[i];
    else throw new UsageError(`unexpected argument: ${args[i]}`);
  }
  if (!(timeout >= 0)) throw new UsageError("--timeout must be a number of seconds");
  requireRequest(id);
  const dir = dirOf(id);
  const payloadPath = path.join(dir, "payload.json");
  const announcedPath = path.join(dir, "announced.json");

  const deadline = Date.now() + timeout * 1000;
  for (;;) {
    if (fs.existsSync(payloadPath)) {
      process.stdout.write(fs.readFileSync(payloadPath, "utf8"));
      return EXIT.OK;
    }
    const st = readJson(path.join(dir, "state.json")) || {};
    if (st.status === "expired" || st.status === "closed") {
      process.stderr.write(`${id} ${st.status} without a submission; open a new request\n`);
      return EXIT.GONE;
    }
    if (st.status === "error" || (st.pid && !alive(st.pid))) {
      process.stderr.write(`${id} failed: ${st.detail || "its server died"}\n` +
        `--- serve.log ---\n${tail(path.join(dir, "serve.log"))}\n`);
      return EXIT.ERROR;
    }
    const announced = readJson(announcedPath);
    if (st.url && announced && st.url !== announced.url) {
      writeJson(announcedPath, { url: st.url });
      process.stderr.write(
        "The tunnel reconnected under a new address, so the link the user has is dead.\n" +
        `Give them this one instead, then wait again:\n${st.url}\n`);
      return EXIT.URL_CHANGED;
    }
    if (Date.now() >= deadline) {
      process.stderr.write(`${id}: nothing submitted in ${timeout}s; the link is still live -- run wait again\n`);
      return EXIT.TIMEOUT;
    }
    await new Promise((r) => setTimeout(r, 500));
  }
}

// --- close / list -------------------------------------------------------------------

async function close(args, { quiet = false } = {}) {
  const ids = args.includes("--all")
    ? (fs.existsSync(HOME) ? fs.readdirSync(HOME).filter((d) => d.startsWith("rt-")) : [])
    : args;
  if (!ids.length) throw new UsageError("close <id>... | close --all");
  for (const id of ids) {
    const dir = dirOf(id);
    const st = readJson(path.join(dir, "state.json")) || {};
    // Exact pids only: a loose `pkill -f` pattern can take out an unrelated
    // long-lived process in the sandbox.
    const pids = [st.pid, st.hostcPid].filter(alive);
    for (const pid of pids) {
      try { process.kill(pid, "SIGTERM"); } catch { /* already gone */ }
    }
    // serve writes its final state as it dies; deleting under it races.
    const deadline = Date.now() + 5000;
    while (pids.some(alive) && Date.now() < deadline) await new Promise((r) => setTimeout(r, 100));
    for (const pid of pids.filter(alive)) {
      try { process.kill(pid, "SIGKILL"); } catch { /* already gone */ }
    }
    fs.rmSync(dir, { recursive: true, force: true, maxRetries: 5 });
    if (!quiet) process.stdout.write(`closed ${id}\n`);
  }
  return EXIT.OK;
}

function list() {
  const ids = fs.existsSync(HOME) ? fs.readdirSync(HOME).filter((d) => d.startsWith("rt-")) : [];
  if (!ids.length) {
    process.stdout.write("no requests\n");
    return EXIT.OK;
  }
  for (const id of ids) {
    const dir = dirOf(id);
    const meta = readJson(path.join(dir, "meta.json")) || {};
    const st = readJson(path.join(dir, "state.json")) || {};
    let status = st.status || "unknown";
    if (fs.existsSync(path.join(dir, "payload.json"))) status = "received";
    else if (status === "open" && !alive(st.pid)) status = "dead";
    const fields = (meta.fields || []).map((f) => f.name).join(",");
    process.stdout.write(`${id}  ${status.padEnd(8)}  fields=${fields}  ${st.url || ""}\n`);
  }
  return EXIT.OK;
}

// --- selftest -----------------------------------------------------------------------

async function selftest(args) {
  const local = args.includes("--local");
  const run = async (sub) => {
    const out = [];
    const err = [];
    const child = spawn(process.execPath, [SELF, ...sub], { stdio: ["ignore", "pipe", "pipe"] });
    child.stdout.on("data", (c) => out.push(c));
    child.stderr.on("data", (c) => err.push(c));
    const code = await new Promise((r) => child.on("close", r));
    return { code, out: Buffer.concat(out).toString(), err: Buffer.concat(err).toString() };
  };

  const opened = await run(["open", "--title", "self-test", "--field", "a:line", "--field", "b:secret",
    "--ttl-min", "2", ...(local ? ["--local"] : [])]);
  if (opened.code !== 0) throw new Error(`open failed:\n${opened.err}`);
  const id = opened.out.match(/^id:\s+(\S+)/m)[1];
  const url = opened.out.match(/^url:\s+(\S+)/m)[1];
  try {
    const sent = { a: "probe é漢😀", b: randomBytes(3000).toString("base64") };
    const res = await submitViaPage(url, sent);
    if (res.status !== 200) throw new Error(`submit through ${url}: HTTP ${res.status} ${res.text}`);
    const waited = await run(["wait", id, "--timeout", "20"]);
    if (waited.code !== 0) throw new Error(`wait exited ${waited.code}:\n${waited.err}`);
    const got = JSON.parse(waited.out);
    if (got.a !== sent.a || got.b !== sent.b) throw new Error("round trip changed the payload");
  } finally {
    await run(["close", id]);
  }
  process.stdout.write(`self-test OK -- page, encryption and submission all work through ${local ? "localhost" : "the hostc tunnel"}\n`);
  return EXIT.OK;
}

// --- main -----------------------------------------------------------------------------

const USAGE = `usage:
  request-text open [--title T] [--field name[:line|secret|multiline[:Label]]]... [--ttl-min 30]
  request-text wait <id> [--timeout 240]    fields as one JSON object on stdout
  request-text close <id>... | --all
  request-text list
  request-text selftest [--local]
wait exit codes: 0 received, 2 still waiting (run again), 3 new URL for the user (on stderr),
4 expired/closed, 1 error
`;

async function main(argv) {
  const [cmd, ...args] = argv;
  switch (cmd) {
    case "open": return open(args);
    case "wait": return wait(args);
    case "close": return close(args);
    case "list": return list();
    case "selftest": return selftest(args);
    case "serve": await serve(args[0]); return null;
    case "-h": case "--help": case "help":
      process.stdout.write(USAGE);
      return EXIT.OK;
    default:
      process.stderr.write(USAGE);
      return EXIT.USAGE;
  }
}

if (process.argv[1] && fs.realpathSync(process.argv[1]) === fs.realpathSync(SELF)) {
  main(process.argv.slice(2)).then(
    (code) => { if (code !== null) process.exitCode = code; },
    (err) => {
      process.stderr.write(`request-text: ${err.message}\n`);
      if (err instanceof UsageError) process.stderr.write(USAGE);
      process.exitCode = err instanceof UsageError ? EXIT.USAGE : (err.exitCode ?? EXIT.ERROR);
    });
}
