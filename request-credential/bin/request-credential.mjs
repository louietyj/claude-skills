#!/usr/bin/env node
// request-credential: ask Louie's phone for a site's login. A request-text
// request receives it (sealed to a key only this sandbox holds); the relay
// Worker only carries the ask -- site, reason, link -- to the cred-bridge app.
//
//   request-credential ask <site> "<reason>" [--ttl-min N]   -> id: rt-...
//   request-credential wait <id> [--timeout S]               -> received (never the password)
//   request-credential run <id> [--keep-min N] -- <cmd>...   -> cmd with $CRED_USERNAME,
//                                                               $CRED_PASSWORD; output redacted
//   ... | request-credential redact <id>...                  -> stdin, password replaced
//   request-credential forget <id>
//   request-credential status <id>
//   request-credential pair
//   request-credential health
//
// wait's exit codes: 0 received, 2 nothing yet (wait again), 4 declined,
// expired or undeliverable (ask again only if Louie wants), 1 error.
//
// The login sits on disk only between `wait` and the `run` that uses it: run
// deletes it afterwards unless --keep-min, and every command first deletes kept
// logins past their deadline. Nothing runs in the background -- the sandbox
// suspends between turns and would kill it.
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const CONFIG = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "config.json");
const HOME = process.env.REQUEST_CREDENTIAL_HOME || path.join(os.homedir(), ".request-credential");
const REQUEST_TEXT = process.env.REQUEST_TEXT_BIN || "request-text";
const EXIT = { OK: 0, ERROR: 1, TIMEOUT: 2, GONE: 4 };

class Fail extends Error {}

function config() {
  const cfg = JSON.parse(fs.readFileSync(CONFIG, "utf8"));
  if (!cfg.worker_url || !cfg.cli_token) throw new Fail(`${CONFIG} needs worker_url and cli_token`);
  return cfg;
}

async function relay(method, route, body) {
  const cfg = config();
  const res = await fetch(cfg.worker_url.replace(/\/+$/, "") + route, {
    method,
    headers: { authorization: `Bearer ${cfg.cli_token}`, "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const out = await res.json().catch(() => ({}));
  return { status: res.status, out };
}

function requestText(args) {
  // A script path (the tests' fake) runs under this node.
  const [cmd, pre] = /\.m?js$/.test(REQUEST_TEXT) ? [process.execPath, [REQUEST_TEXT]] : [REQUEST_TEXT, []];
  const r = spawnSync(cmd, [...pre, ...args], { stdio: ["ignore", "pipe", "pipe"], encoding: "utf8" });
  if (r.error) throw new Fail(`could not run ${REQUEST_TEXT}: ${r.error.message} -- run request-text's setup.sh`);
  return { code: r.status, stdout: r.stdout ?? "", stderr: r.stderr ?? "" };
}

const recordPath = (id) => path.join(HOME, `${id}.json`);
const saveRecord = (rec) => fs.writeFileSync(recordPath(rec.id), JSON.stringify(rec), { mode: 0o600 });

function record(id) {
  if (!/^rt-[0-9a-f]+$/.test(id ?? "")) throw new Fail("need the id `ask` printed (rt-...)");
  try {
    return JSON.parse(fs.readFileSync(recordPath(id), "utf8"));
  } catch {
    throw new Fail(`no request ${id} in this sandbox (used and deleted, or never asked)`);
  }
}

function forgetNow(id) {
  requestText(["close", id]);
  fs.rmSync(recordPath(id), { force: true });
}

export function sweep(now = Date.now()) {
  if (!fs.existsSync(HOME)) return;
  for (const f of fs.readdirSync(HOME).filter((n) => /^rt-[0-9a-f]+\.json$/.test(n))) {
    const rec = JSON.parse(fs.readFileSync(path.join(HOME, f), "utf8"));
    if (rec.deleteAt && rec.deleteAt <= now) forgetNow(rec.id);
  }
}

function login(id) {
  record(id);
  const w = requestText(["wait", id, "--timeout", "0"]);
  if (w.code !== 0) throw new Fail(`${id} has no login yet: run wait first`);
  return JSON.parse(w.stdout);
}

function flags(args, known) {
  const out = { _: [] };
  for (let i = 0; i < args.length; i++) {
    if (args[i].startsWith("--")) {
      const name = args[i].slice(2);
      if (!known.includes(name) || i + 1 >= args.length) throw new Fail(`bad option ${args[i]}`);
      out[name] = args[++i];
    } else {
      out._.push(args[i]);
    }
  }
  return out;
}

async function ask(args) {
  const a = flags(args, ["ttl-min"]);
  const [site, reason] = a._;
  if (!site || !reason || a._.length !== 2) throw new Fail('usage: ask <site> "<reason>"');
  const ttlMin = Number(a["ttl-min"] ?? 10);

  const opened = requestText(["open", "--title", site, "--ttl-min", String(ttlMin),
    "--field", "username:line:Username", "--field", "password:secret:Password"]);
  const id = opened.stdout.match(/^id:\s+(\S+)/m)?.[1];
  const url = opened.stdout.match(/^url:\s+(\S+)/m)?.[1];
  if (opened.code !== 0 || !id || !url) throw new Fail(`request-text open failed:\n${opened.stderr}`);

  const { status, out } = await relay("POST", "/request", { site, reason, url, ttl_s: ttlMin * 60 });
  if (status !== 200) {
    requestText(["close", id]);
    throw new Fail(`the relay refused it (HTTP ${status}): ${out.error ?? "no detail"}`);
  }
  fs.mkdirSync(HOME, { recursive: true, mode: 0o700 });
  saveRecord({ id, rid: out.rid, site, url });
  // No URL: the phone has it, and Louie never needs to open it himself.
  process.stdout.write(`id: ${id}\nsent to the phone: ${site}\n`);
  return EXIT.OK;
}

async function wait(args) {
  const a = flags(args, ["timeout"]);
  const rec = record(a._[0]);
  const deadline = Date.now() + Number(a.timeout ?? 240) * 1000;
  for (;;) {
    const left = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
    const w = requestText(["wait", rec.id, "--timeout", String(Math.min(left, 30))]);
    if (w.code === 0) {
      process.stdout.write(`${rec.id}: received the ${rec.site} login for ${JSON.parse(w.stdout).username}\n`);
      return EXIT.OK;
    }
    if (w.code === 3) {
      // The tunnel reconnected under a new URL; point the phone at it.
      const newUrl = w.stderr.match(/https?:\/\/\S+/)?.[0];
      if (newUrl) {
        await relay("PATCH", `/request/${rec.rid}`, { url: newUrl });
        rec.url = newUrl;
        saveRecord(rec);
      }
      continue;
    }
    if (w.code !== 2) {
      process.stderr.write(w.stderr);
      return w.code === 4 ? EXIT.GONE : EXIT.ERROR;
    }
    const { out } = await relay("GET", `/request/${rec.rid}`);
    if (["declined", "expired", "undelivered"].includes(out.status)) {
      forgetNow(rec.id);
      process.stderr.write(`${rec.id}: Louie's phone reports the request ${out.status}. ` +
        "Do not ask again unless he says to.\n");
      return EXIT.GONE;
    }
    if (Date.now() >= deadline) {
      process.stderr.write(`${rec.id}: nothing yet (phone: ${out.status ?? "unknown"}); run wait again\n`);
      return EXIT.TIMEOUT;
    }
  }
}

// The forms a password takes in other tools' output, longest first so a longer
// form is not left half-replaced.
export function redactionTargets(logins) {
  const out = new Set();
  for (const { username = "", password } of logins) {
    if (!password) continue;
    const b64 = (s) => Buffer.from(s, "utf8").toString("base64");
    const html = password.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    for (const t of [password, JSON.stringify(password).slice(1, -1), encodeURIComponent(password),
      encodeURIComponent(password).replace(/%20/g, "+"), html, b64(password),
      b64(password).replace(/=+$/, ""), b64(`${username}:${password}`)]) {
      if (t) out.add(t);
    }
  }
  return [...out].sort((a, b) => b.length - a.length);
}

export const MARK = "[REDACTED]";

// Holds back the last (longest target - 1) characters, so a target split across
// two reads is still caught.
export async function redactStream(input, output, targets) {
  const hold = Math.max(0, ...targets.map((t) => t.length)) - 1;
  const scrub = (s) => targets.reduce((acc, t) => acc.split(t).join(MARK), s);
  let pending = "";
  input.setEncoding("utf8");
  for await (const chunk of input) {
    pending = scrub(pending + chunk);
    if (pending.length > hold) {
      output.write(pending.slice(0, pending.length - hold));
      pending = pending.slice(pending.length - hold);
    }
  }
  output.write(scrub(pending));
}

function warnIfShort(targets) {
  if (targets.some((t) => t.length < 4)) {
    process.stderr.write("request-credential: the password is very short, so redaction may hit unrelated text\n");
  }
}

async function run(args) {
  const split = args.indexOf("--");
  if (split < 0 || split === args.length - 1) throw new Fail("usage: run <id> [--keep-min N] -- <command>...");
  const a = flags(args.slice(0, split), ["keep-min"]);
  const [cmd, ...cmdArgs] = args.slice(split + 1);
  const rec = record(a._[0]);
  const creds = login(rec.id);
  const targets = redactionTargets([creds]);
  warnIfShort(targets);

  const child = spawn(cmd, cmdArgs, {
    stdio: ["inherit", "pipe", "pipe"],
    env: { ...process.env, CRED_USERNAME: creds.username, CRED_PASSWORD: creds.password },
  });
  const exited = new Promise((resolve) => {
    child.on("error", (e) => resolve({ error: e }));
    child.on("close", (code, signal) => resolve({ code, signal }));
  });
  await Promise.all([redactStream(child.stdout, process.stdout, targets),
    redactStream(child.stderr, process.stderr, targets)]);
  const result = await exited;

  if (a["keep-min"]) {
    rec.deleteAt = Date.now() + Number(a["keep-min"]) * 60_000;
    saveRecord(rec);
  } else {
    forgetNow(rec.id);
  }
  if (result.error) throw new Fail(`could not run ${cmd}: ${result.error.message}`);
  return result.code ?? EXIT.ERROR;
}

async function redact(args) {
  if (!args.length) throw new Fail("usage: ... | redact <id>...");
  // Fail closed: `login` throws before anything is read, so nothing passes through unfiltered.
  const targets = redactionTargets(args.map(login));
  warnIfShort(targets);
  await redactStream(process.stdin, process.stdout, targets);
  return EXIT.OK;
}

async function forget(args) {
  record(args[0]);
  forgetNow(args[0]);
  process.stdout.write(`${args[0]}: deleted\n`);
  return EXIT.OK;
}

async function status(args) {
  const rec = record(args[0]);
  const { status, out } = await relay("GET", `/request/${rec.rid}`);
  if (status !== 200) throw new Fail(`relay: HTTP ${status} ${out.error ?? ""}`);
  process.stdout.write(`${rec.id}  ${out.site}  ${out.status}\n`);
  return EXIT.OK;
}

async function pair() {
  const { status, out } = await relay("POST", "/pair", {});
  if (status !== 200) throw new Fail(`relay: HTTP ${status} ${out.error ?? ""}`);
  process.stdout.write(`code: ${out.code}  (valid ${Math.round(out.expires_s / 60)} minutes)\n` +
    "Louie enters it in the Credential Request app on his phone.\n");
  return EXIT.OK;
}

async function health() {
  const cfg = config();
  const h = await (await fetch(`${cfg.worker_url.replace(/\/+$/, "")}/health`)).json();
  const unset = Object.entries(h.secrets ?? {}).filter(([, v]) => !v).map(([k]) => k);
  if (unset.length) throw new Fail(`relay is missing secrets: ${unset.join(", ")}`);
  // A well-formed rid that does not exist: 404 means the token was accepted.
  const { status } = await relay("GET", "/request/0000000000000000");
  if (status === 401) throw new Fail("the relay rejected this skill's cli_token");
  if (status !== 404) throw new Fail(`relay: unexpected HTTP ${status}`);
  process.stdout.write(`relay OK: ${cfg.worker_url}\n`);
  return EXIT.OK;
}

const COMMANDS = { ask, wait, run, redact, forget, status, pair, health };

// Run as a CLI, not when the tests import it.
if (import.meta.url === pathToFileURL(fs.realpathSync(process.argv[1] ?? "")).href) {
  const [cmd, ...rest] = process.argv.slice(2);
  try {
    if (!COMMANDS[cmd]) throw new Fail(`usage: request-credential ${Object.keys(COMMANDS).join("|")} ...`);
    sweep();
    process.exitCode = await COMMANDS[cmd](rest);
  } catch (e) {
    process.stderr.write(`request-credential: ${e instanceof Fail ? e.message : e.stack}\n`);
    process.exitCode = EXIT.ERROR;
  }
}
