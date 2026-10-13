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

```bash
request-credential ask chase.com "Download the October statement"
# id: rt-3f9a1c2e
request-credential wait rt-3f9a1c2e
# rt-3f9a1c2e: received the chase.com login for louie@example.com
request-credential run rt-3f9a1c2e -- sh -c 'printf %s "$CRED_PASSWORD" | some-cmd --password-stdin'
```

Tell Louie in one line that his phone is asking (no link to give him), **then
call `wait` in the same turn**.

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
| 0 | received (it prints the username, never the password) | `run` it |
| 2 | nothing yet, request still open | run `wait` again |
| 4 | he declined, it expired, or the phone could not be reached (stderr says which) | stop; tell him; ask again only if he says to |
| 1 | error (stderr) | report it |

`request-credential status <id>` shows where it is if he asks: pending (not
yet on the phone), delivered (notification showing), opened (he is looking at
it), sent, declined, expired.

## Using the login: `run`

`request-credential run <id> -- <command>...` runs the command with
`$CRED_USERNAME` and `$CRED_PASSWORD` in its environment, passes its stdout and
stderr through a redactor, and **then deletes the login from disk**. The
redactor replaces the password, including its JSON, URL, HTML, base64 and
Basic-auth forms, with `[REDACTED]`, so a browser dump, page text or verbose
HTTP log that echoes it does not leak it into the transcript. It exits with the
command's exit code.

```bash
request-credential run rt-3f9a1c2e -- sh -c '
  curl -sv -c jar -d "user=$CRED_USERNAME" --data-urlencode "pass=$CRED_PASSWORD" https://example.com/login
  curl -s -b jar https://example.com/statements/2026-10.pdf -o statement.pdf
'
```

- **Do the whole job in one `run`.** Put every step that needs the login in a
  single script (`sh -c '...'`, or write a script file first). The login is
  gone afterwards; a second `run` means asking Louie again.
- **Several commands across turns** only when one script truly cannot do it:
  `run <id> --keep-min N -- ...` keeps the login for N minutes. Each command
  then still goes through `run` (or ends `2>&1 | request-credential redact
  <id>`), and **finish with `request-credential forget <id>`**. Nothing deletes
  it on a timer -- the sandbox suspends between turns -- only the next
  `request-credential` command after the deadline does, so forget it yourself.
- **Never read the password any other way**: not `request-text wait`, not
  `cat ~/.request-text/...`. Do not print `$CRED_PASSWORD` to check it; use
  `printf %s "$CRED_PASSWORD" | wc -c`. The username is fine to show.
- `redact` prints nothing at all if the login is already gone, rather than
  pass text through unfiltered.

## Pairing

If `ask` says no phone is paired, or the phone's push registration is stale:
run `request-credential pair`, give Louie the 6-digit code, and have him enter
it in the Credential Request app.
