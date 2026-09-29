// Integration tests for request-text. Run inside the sandbox stand-in:
//
//   dev/sandbox.sh sh 'node --test /mnt/skills/user/request-text/test_request_text.mjs'
//
// Most cases use `open --local` (no tunnel); the tunnel cases run against a
// fake hostc, so nothing here needs network. setup.sh's self-test covers the
// real tunnel.

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { randomBytes, webcrypto } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { pageSeal, submitViaPage } from "./bin/request-text.mjs";

const CLI = path.join(path.dirname(fileURLToPath(import.meta.url)), "bin", "request-text.mjs");
const HOME = fs.mkdtempSync(path.join(os.tmpdir(), "rt-test-"));

function run(args, env = {}) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [CLI, ...args], {
      env: { ...process.env, REQUEST_TEXT_HOME: HOME, ...env },
      stdio: ["ignore", "pipe", "pipe"],
    });
    const out = [];
    const err = [];
    child.stdout.on("data", (c) => out.push(c));
    child.stderr.on("data", (c) => err.push(c));
    child.on("close", (code) => resolve({
      code, out: Buffer.concat(out).toString(), err: Buffer.concat(err).toString(),
    }));
  });
}

async function open(...args) {
  const r = await run(["open", ...args]);
  assert.equal(r.code, 0, r.err);
  return { id: r.out.match(/^id:\s+(\S+)/m)[1], url: r.out.match(/^url:\s+(\S+)/m)[1] };
}

async function sealed(url, fields) {
  const html = await (await fetch(url)).text();
  const cfg = JSON.parse(html.match(/id="config">([\s\S]*?)<\/script>/)[1]);
  return pageSeal(html)(webcrypto.subtle, cfg.key, cfg.id, fields);
}

const post = (url, body) => fetch(url, {
  method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
});

test("default single field round-trips exactly, as one JSON line", async () => {
  const { id, url } = await open("--local");
  const text = "line one\n  indented \"quoted\" \\ back\u00e9\u6f22\ud83d\ude00\n\ttab";
  const res = await submitViaPage(url, { text });
  assert.equal(res.status, 200);
  const w = await run(["wait", id, "--timeout", "10"]);
  assert.equal(w.code, 0, w.err);
  assert.equal(w.out, `${JSON.stringify({ text })}\n`);
  await run(["close", id]);
});

test("multiple fields of every kind, and labels reach the page", async () => {
  const { id, url } = await open("--local", "--title", "Creds <b>&",
    "--field", "user:line:User name", "--field", "pw:secret", "--field", "notes:multiline");
  const html = await (await fetch(url)).text();
  assert.match(html, /"label":"User name"/);
  assert.match(html, /Creds \\u003cb>&/);  // escaped inside the JSON block
  const fields = { user: "louie", pw: "hunter2", notes: "a\nb" };
  assert.equal((await submitViaPage(url, fields)).status, 200);
  const w = await run(["wait", id]);
  assert.deepEqual(JSON.parse(w.out), fields);
  await run(["close", id]);
});

test("a 1 MB paste arrives byte for byte", async () => {
  const { id, url } = await open("--local");
  const text = randomBytes(768 * 1024).toString("base64");
  assert.equal((await submitViaPage(url, { text })).status, 200);
  const w = await run(["wait", id]);
  assert.equal(JSON.parse(w.out).text, text);
  await run(["close", id]);
});

test("wait with nothing submitted times out with exit 2; wait after receipt is instant", async () => {
  const { id, url } = await open("--local");
  const w1 = await run(["wait", id, "--timeout", "1"]);
  assert.equal(w1.code, 2);
  assert.equal(w1.out, "");
  assert.match(w1.err, /still live/);

  await submitViaPage(url, { text: "x" });
  const first = await run(["wait", id]);
  const t0 = Date.now();
  const again = await run(["wait", id, "--timeout", "0"]);
  assert.equal(again.code, 0);
  assert.equal(again.out, first.out);
  assert.ok(Date.now() - t0 < 2000);
  await run(["close", id]);
});

test("wrong token 404s; a second submission 410s", async () => {
  const { id, url } = await open("--local");
  const wrong = url.replace(/\/[^/]+$/, "/nope");
  assert.equal((await fetch(wrong)).status, 404);
  assert.equal((await submitViaPage(url, { text: "first" })).status, 200);
  assert.equal((await post(url, { epk: "", iv: "", ct: "" })).status, 410);
  const w = await run(["wait", id]);
  assert.equal(JSON.parse(w.out).text, "first");
  await run(["close", id]);
});

test("tampered ciphertext and a wrong field set are rejected and the form stays open", async () => {
  const { id, url } = await open("--local", "--field", "a:line");
  const body = await sealed(url, { a: "real" });
  const flipped = body.ct[5] === "A" ? "B" : "A";
  const res = await post(url, { ...body, ct: body.ct.slice(0, 5) + flipped + body.ct.slice(6) });
  assert.equal(res.status, 400);
  assert.equal((await post(url, await sealed(url, { a: "x", extra: "y" }))).status, 400);
  assert.equal((await post(url, await sealed(url, { a: 5 }))).status, 400);
  assert.equal((await submitViaPage(url, { a: "real" })).status, 200);
  const w = await run(["wait", id]);
  assert.deepEqual(JSON.parse(w.out), { a: "real" });
  await run(["close", id]);
});

test("close deletes everything and stops the server; wait then exits 4", async () => {
  const { id, url } = await open("--local");
  const st = JSON.parse(fs.readFileSync(path.join(HOME, id, "state.json"), "utf8"));
  assert.equal((await run(["close", id])).code, 0);
  assert.ok(!fs.existsSync(path.join(HOME, id)));
  // Gone, or a zombie PID 1 has not reaped yet: either way not running.
  const stat = fs.existsSync(`/proc/${st.pid}/stat`) ? fs.readFileSync(`/proc/${st.pid}/stat`, "utf8") : "";
  assert.ok(!stat || stat.slice(stat.lastIndexOf(")") + 2)[0] === "Z", stat);
  await assert.rejects(fetch(url));
  assert.equal((await run(["wait", id])).code, 4);
});

test("an expired request makes wait exit 4", async () => {
  const { id } = await open("--local", "--ttl-min", "0.02");
  await new Promise((r) => setTimeout(r, 2000));
  const w = await run(["wait", id, "--timeout", "5"]);
  assert.equal(w.code, 4, w.err);
  assert.match(w.err, /expired/);
  await run(["close", id]);
});

test("usage errors exit 64", async () => {
  for (const args of [["open", "--field", "Bad-Name"], ["open", "--field", "a:weird"],
    ["open", "--field", "a", "--field", "a"], ["wait"], ["frob"]]) {
    assert.equal((await run(args)).code, 64, args.join(" "));
  }
});

test("state is private to the sandbox user", async () => {
  const { id, url } = await open("--local");
  await submitViaPage(url, { text: "secret" });
  await run(["wait", id]);
  assert.equal(fs.statSync(HOME).mode & 0o777, 0o700);
  assert.equal(fs.statSync(path.join(HOME, id, "payload.json")).mode & 0o777, 0o600);
  await run(["close", id]);
});

// Stands in for hostc 2.x: same argv and output, no tunnel. SIGUSR2 plays a
// tunnel that expired while offline, the one reconnect that changes the URL.
const FAKE_HOSTC = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "rt-hostc-")), "hostc");
fs.writeFileSync(FAKE_HOSTC, `#!/usr/bin/env node
const target = process.argv[2] ?? "";
if (process.argv.length !== 3 || !/^127\\.0\\.0\\.1:\\d+$/.test(target)) {
  console.error("error Unknown arguments " + process.argv.slice(2).join(" "));
  process.exit(2);
}
const show = (host) =>
  console.log("\\n  \\x1b[1mhttps://" + host + ".hostc.app\\x1b[22m  \\x1b[2m→ http://" + target + "\\x1b[22m\\n");
show("first");
process.on("SIGUSR2", () => {
  console.log("The tunnel expired while offline. It has a new URL:");
  show("second");
});
process.on("SIGTERM", () => process.exit(0));
setInterval(() => {}, 1 << 30);
`, { mode: 0o755 });
process.env.REQUEST_TEXT_HOSTC = FAKE_HOSTC;

test("a tunnel's public URL is picked out of hostc's output", async () => {
  const { id, url } = await open("--field", "a:line");
  assert.match(url, /^https:\/\/first\.hostc\.app\/\S+$/);
  await run(["close", id]);
});

test("a hostc URL change is reported once as exit 3", async () => {
  const { id, url: oldUrl } = await open("--field", "a:line");
  const st = JSON.parse(fs.readFileSync(path.join(HOME, id, "state.json"), "utf8"));
  process.kill(st.hostcPid, "SIGUSR2");

  const w = await run(["wait", id, "--timeout", "10"]);
  assert.equal(w.code, 3, w.err);
  const newUrl = w.err.match(/https:\/\/\S+/)[0];
  assert.equal(newUrl, oldUrl.replace("first", "second"), "same token, new host");

  const again = await run(["wait", id, "--timeout", "1"]);
  assert.equal(again.code, 2, "a new URL is reported once, not on every wait");
  await run(["close", id]);
});
