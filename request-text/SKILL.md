---
name: request-text
description: "Requests text from the user through a link they open on their phone or computer. What they submit lands in your bash tool as JSON, so you can pipe it straight where it needs to go without re-emitting a single token of it. Use it whenever you need a secret (API key, token, password) that should not sit in the chat transcript, or a large or exact blob (SSH key, config file, log, long document) the user would otherwise paste into chat and you would then have to copy out token by token. Also use it when the user offers to send or paste you something like that. Never ask the user to paste a secret into chat when this skill is available."
---

# request-text

You open a request, the user gets a link to a small form, you block until they
submit, and the fields arrive on stdout as one JSON object. They never pass
through your output unless you choose to print them.

## Setup (once per conversation)

```bash
bash /mnt/skills/*/request-text/setup.sh
```

Installs the tunnel client, puts `request-text` on PATH, and runs a real
submission through a real tunnel to prove the chain. Idempotent. If it fails,
tell the user which step failed.

**Never re-derive the path or keep a request id only in a shell variable**:
shell state dies with the bash call. Keep ids in your own reply text.

## The flow

```bash
request-text open --title "GitHub token for gh" --field token:secret
# id:  rt-3f9a1c2e
# url: https://t-xxxxxxxxxx.hostc.dev/<token>
```

Put the URL in your reply as a clickable link and say what to enter. **Then call
`wait` in the same turn**; do not end your turn and wait for them to say "done".

```bash
request-text wait rt-3f9a1c2e | jq -r .token | gh auth login --with-token
```

`wait` blocks for up to 240s. What its exit code means:

| exit | meaning | do |
|---|---|---|
| 0 | received; JSON on stdout | use it |
| 2 | nothing yet, link still live | run `wait` again |
| 3 | tunnel reconnected under a new URL (on stderr); the old link is dead | give the user the new link, then `wait` again |
| 4 | expired or closed | open a new request |
| 1 | error (stderr has the log) | report it |

After a submission `wait` returns instantly every time, so run it again in each
later bash call that needs the value, piping out the field you need then.

## Fields

`--field name[:kind[:Label]]`, repeatable. `name` is the JSON key (`a-z0-9_`).
Kinds: `line` (one-line input), `secret` (masked, with a Show toggle),
`multiline` (big monospace textarea; the default). With no `--field` you get a
single multiline field named `text`.

```bash
request-text open --title "Deploy credentials" \
  --field user:line:"Username" --field password:secret:"Password"
request-text wait rt-... | jq -r .password | some-cmd --password-stdin
```

Other options: `--ttl-min N` (default 30; the link dies after that).

## Handling what comes back

- **Secrets: never let the value reach your output.** Always consume `wait`
  in the same command: pipe it into the program, or redirect it to a file
  (`request-text wait ID | jq -r .token > ~/.config/tool/token`). No `cat`, no
  `echo`, no `jq` without a pipe after it. Check it with `wc -c` if you need to.
- **Large or exact text:** write it to a file (`wait ID | jq -r .text > key.pub`)
  and work from the file. Read it yourself only if you actually need its
  content, not to copy it somewhere.
- `jq -r` adds a trailing newline; use `jq -j` when the bytes must be exact.
- `request-text close ID` stops the tunnel and deletes what was received. Do it
  once you are done with a secret. `request-text list` shows requests.

## What it protects against

The page encrypts in the browser to a key pair made for this request; the
private half never leaves the sandbox server's memory. The tunnel relay
(hostc.dev) and the sandbox's TLS-intercepting egress see only ciphertext. An
actively malicious relay could still serve a tampered page, since it delivers
the page too. The link is one-shot and unguessable; submissions are decrypted
or rejected. If the user asks, say this plainly.
