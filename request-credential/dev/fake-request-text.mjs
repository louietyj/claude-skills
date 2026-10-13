// Stands in for request-text in the CLI tests: `wait` prints $FAKE_RT_DIR/<id>.json
// if it exists (exit 0) or exits 2; `close` deletes it.
import fs from "node:fs";
import path from "node:path";

const [cmd, id] = process.argv.slice(2);
const payload = path.join(process.env.FAKE_RT_DIR, `${id}.json`);
if (cmd === "wait") {
  if (!fs.existsSync(payload)) process.exit(2);
  process.stdout.write(fs.readFileSync(payload, "utf8"));
} else if (cmd === "close") {
  fs.rmSync(payload, { force: true });
} else {
  process.stderr.write(`fake request-text: no ${cmd}\n`);
  process.exit(1);
}
