---
name: request-credential
description: "Gets a website or app login (username and password) from Louie's phone: a notification asks him, his password manager fills it in, he taps Send, and the login reaches a command you run (output redacted, login deleted after) without entering the chat. Use whenever a task needs one of his logins -- signing in to a site through the browser, an API that takes a username and password, a CLI login. Prefer it over request-text for logins: he does not have to look the password up or type it, which works while he is driving. Not for API keys or tokens that are not saved as a site login (use request-text)."
---

# request-credential

You name a site and say why; Louie's phone shows "Claude wants your <site>
login" with your reason. He taps it, picks the account his password manager
offers for that site, and taps Send. You then use it in one `run` command,
which never shows it to you and deletes it afterwards.

## Setup (once per conversation)

```bash
bash "$(ls -1dt /mnt/skills/*/request-credential ~/.claude/skills/synced/*/request-credential 2>/dev/null | head -1)/setup.sh"
```

It also sets up request-text, which this skill builds on. If it fails, tell
Louie which step failed.

**Keep the id in your reply text, not only in a shell variable**: shell state
dies with the bash call.

## The flow

**First, have the script ready.** Work out everything the login needs without
the password: open the login page, find the selectors of every step
(snapshots, `eval` over the inputs -- reading names and types, never values),
and note whether the password page only appears after the username (two-step
logins like Auth0). Write the script before asking. A login is kept only a
minute for a retry, so a script you debug after it arrives costs Louie another
tap.

```bash
request-credential ask chase.com "Download the October statement"
# id: rt-3f9a1c2e
request-credential wait rt-3f9a1c2e && request-credential run rt-3f9a1c2e -- sh /tmp/login.sh
```

Tell Louie in one line that his phone is asking (no link to give him), **then
call `wait` in the same turn, chained to the `run`** as above, so the login is
used the moment it arrives.

- **site**: the bare domain his password manager saved it under: `chase.com`,
  not `https://secure.chase.com/login`. Subdomains match the parent, so the
  registrable domain is right. For an Android app login, the domain of the
  service.
- **reason**: one short line he reads on a notification, possibly while
  driving. Say what you will do with it: "Sign in to download the October
  statement", not "need credentials".

`wait` blocks up to 240s:

| exit | meaning | do |
|---|---|---|
| 0 | received (it prints the username, never the password) | `run` it within the minute |
| 2 | nothing yet, request still open | run `wait` again |
| 4 | he declined or it expired (stderr says which) | stop; tell him; ask again only if he says to |
| 1 | error (stderr) | report it |

`request-credential status <id>` shows where it is if he asks: pending (not
yet on the phone), delivered (notification showing), opened (he is looking at
it), sent, declined, expired.

**No notification is not a dead request.** Every open request also waits in
the Credential Request app's list. If he says nothing showed up, or `ask`
warns that the push failed, have him open the app and tap it there. Do not
ask again, and do not re-pair unless the app says the relay doesn't know the
phone.

## Using the login: `run`

`request-credential run <id> -- <command>...` runs the command with
`$CRED_USERNAME` and `$CRED_PASSWORD` in its environment and passes its stdout
and stderr through a redactor. The redactor replaces the password, including
its JSON, URL, HTML, base64 and Basic-auth forms, with `[REDACTED]`, so a
browser dump, page text or verbose HTTP log that echoes it does not leak it
into the transcript. It exits with the command's exit code.

**If the command succeeds, the login is deleted. If it fails, it is kept one
minute** for a single fixed-up retry, then deleted. That is the only way to
reuse it: there is no keeping it for later. Logins are held in RAM
(`/dev/shm`), not on disk.

```bash
request-credential run rt-3f9a1c2e -- sh -c '
  curl -sv -c jar -d "user=$CRED_USERNAME" --data-urlencode "pass=$CRED_PASSWORD" https://example.com/login
  curl -s -b jar https://example.com/statements/2026-10.pdf -o statement.pdf
'
```

- **Do the whole job in one `run`.** Every step that needs the login goes in
  one script, ending with a check that it worked. Make the script exit
  non-zero when a step fails (`set -e`, or `|| exit 1`), so a failure keeps the
  login for the retry instead of deleting it.
- **A login that stops at MFA:** the session is what matters afterwards, not
  the password. Let the `run` end at the code prompt (exit 0); get the code
  separately (Gmail, or his texts through mcp-android; otherwise ask him for
  it in chat, since a used code is worthless).
- **Never read the password any other way**: not `request-text wait`, not
  the files under `/dev/shm`. Do not print it, its length, or any part of it;
  `[ -n "$CRED_PASSWORD" ]` is the only check you need. The username is fine
  to show.
- `forget <id>` deletes a kept login at once. `... 2>&1 | request-credential
  redact <id>` filters a command that is not under `run`, while a login is
  still kept; it prints nothing at all if the login is gone.

## Pairing

Only when `ask` says no phone is paired, or the app itself says the relay
doesn't know the phone (it shows a pairing box then, and only then): run
`request-credential pair`, give Louie the 6-digit code, and have him enter it
in the Credential Request app. The app checks its pairing with the relay and
refreshes its push token every time it opens, so a stale token fixes itself.
