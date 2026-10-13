// node --test request-credential/test_request_credential.mjs
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Readable, Writable } from "node:stream";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { MARK, redactionTargets, redactStream } from "./bin/request-credential.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CLI = path.join(HERE, "bin", "request-credential.mjs");

const LOGIN = { username: "louie@example.com", password: 'p@ss "w0rd"/é&<x>' };

async function run(chunks, logins = [LOGIN]) {
  let out = "";
  const sink = new Writable({ write(c, _e, cb) { out += c; cb(); } });
  await redactStream(Readable.from(chunks.map((c) => Buffer.from(c))), sink, redactionTargets(logins));
  return out;
}

test("every encoding of the password is replaced", async () => {
  const p = LOGIN.password;
  const basic = Buffer.from(`${LOGIN.username}:${p}`).toString("base64");
  const forms = [p, JSON.stringify(p).slice(1, -1), encodeURIComponent(p),
    encodeURIComponent(p).replace(/%20/g, "+"), Buffer.from(p).toString("base64"),
    "p@ss &quot;w0rd&quot;/é&amp;&lt;x&gt;", `Authorization: Basic ${basic}`];
  for (const form of forms) {
    const out = await run([`before ${form} after\n`]);
    assert.ok(!out.includes(p) && !out.includes(basic), `leaked via ${form}: ${out}`);
    assert.ok(out.includes(MARK), out);
    assert.ok(out.startsWith("before ") && out.endsWith(" after\n"), out);
  }
});

test("a password split across reads is still caught, at every split point", async () => {
  const text = `{"password":"${LOGIN.password}"} and ${LOGIN.password}.`;
  for (let i = 1; i < text.length; i++) {
    const out = await run([text.slice(0, i), text.slice(i)]);
    assert.ok(!out.includes(LOGIN.password), `split at ${i}: ${out}`);
    assert.equal(out, `{"password":"${MARK}"} and ${MARK}.`);
  }
});

test("one character at a time", async () => {
  const text = `x${LOGIN.password}y`;
  assert.equal(await run([...text]), `x${MARK}y`);
});

test("other text passes through untouched, including a near miss", async () => {
  const text = "status 200\np@ss \"w0rd\"/é&<y>\nusername louie@example.com\n";
  assert.equal(await run([text]), text);
});

test("several logins at once", async () => {
  const other = { username: "u", password: "hunter2-long" };
  const out = await run([`${LOGIN.password} ${other.password}`], [LOGIN, other]);
  assert.equal(out, `${MARK} ${MARK}`);
});

test("output stays whole when nothing matches and input is long", async () => {
  const text = "a".repeat(100_000);
  assert.equal(await run([text.slice(0, 33_333), text.slice(33_333)]), text);
});

// --- the CLI, against a fake request-text ----------------------------------------

const ID = "rt-0a1b2c3d";

function sandbox({ received = true, deleteAt } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "request-credential-test-"));
  const home = path.join(root, "home");
  const rt = path.join(root, "rt");
  fs.mkdirSync(home);
  fs.mkdirSync(rt);
  fs.writeFileSync(path.join(home, `${ID}.json`),
    JSON.stringify({ id: ID, rid: "0123456789abcdef", site: "example.com", url: "https://x.hostc.app/t", deleteAt }));
  if (received) fs.writeFileSync(path.join(rt, `${ID}.json`), JSON.stringify(LOGIN));
  const cli = (args, input) => spawnSync(process.execPath, [CLI, ...args], {
    input, encoding: "utf8",
    env: { ...process.env, REQUEST_CREDENTIAL_HOME: home, FAKE_RT_DIR: rt,
      REQUEST_TEXT_BIN: path.join(HERE, "dev", "fake-request-text.mjs") },
  });
  const onDisk = () => fs.existsSync(path.join(home, `${ID}.json`)) || fs.existsSync(path.join(rt, `${ID}.json`));
  return { cli, onDisk };
}

const ECHO = ["-e", "console.log('out ' + process.env.CRED_USERNAME + ' ' + process.env.CRED_PASSWORD);" +
  "console.error('err ' + process.env.CRED_PASSWORD); process.exit(3)"];

test("wait reports the login without printing the password", () => {
  const { cli } = sandbox();
  const r = cli(["wait", ID]);
  assert.equal(r.status, 0, r.stderr);
  assert.ok(r.stdout.includes(LOGIN.username), r.stdout);
  assert.ok(!r.stdout.includes(LOGIN.password) && !r.stderr.includes(LOGIN.password));
});

test("run hands the login to the command, redacts its output, then deletes the login", () => {
  const { cli, onDisk } = sandbox();
  const r = cli(["run", ID, "--", process.execPath, ...ECHO]);
  assert.equal(r.status, 3, "the command's exit code is passed through");
  assert.equal(r.stdout, `out ${LOGIN.username} ${MARK}\n`);
  assert.ok(r.stderr.includes(`err ${MARK}`) && !r.stderr.includes(LOGIN.password), r.stderr);
  assert.ok(!onDisk(), "the login is still on disk");
  assert.equal(cli(["run", ID, "--", process.execPath, ...ECHO]).status, 1, "a second run should fail");
});

test("run --keep-min keeps the login until its deadline, then any command deletes it", () => {
  const { cli, onDisk } = sandbox();
  assert.equal(cli(["run", ID, "--keep-min", "5", "--", process.execPath, ...ECHO]).status, 3);
  assert.ok(onDisk());
  assert.equal(cli(["redact", ID], `x ${LOGIN.password} y`).stdout, `x ${MARK} y`);

  const past = sandbox({ deleteAt: Date.now() - 1 });
  const r = past.cli(["redact", ID], `x ${LOGIN.password} y`);
  assert.equal(r.status, 1);
  assert.equal(r.stdout, "", "redact must print nothing once the login is gone");
  assert.ok(!past.onDisk());
});

test("redact prints nothing when there is no login to redact against", () => {
  const { cli } = sandbox({ received: false });
  const r = cli(["redact", ID], `x ${LOGIN.password} y`);
  assert.equal(r.status, 1);
  assert.equal(r.stdout, "");
});

test("forget deletes the login now", () => {
  const { cli, onDisk } = sandbox();
  assert.equal(cli(["forget", ID]).status, 0);
  assert.ok(!onDisk());
});
