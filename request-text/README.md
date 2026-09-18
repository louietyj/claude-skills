# request-text

Lets Claude on claude.ai ask you for text through a link instead of the chat. You
open the link (on your phone is fine), fill in a small form, and what you submit
lands in Claude's bash sandbox as JSON. Claude pipes it where it's needed
without the value going through its own output:

- **Secrets** (API tokens, passwords) never enter the transcript.
- **Large or exact text** (SSH keys, configs, logs) isn't retyped by the model
  token by token, which is slow and can introduce typos.

## How it works

```
phone ──https──▶ hostc.dev (Cloudflare Worker) ══WSS══▶ hostc client ──▶ serve (127.0.0.1)
                                                        └───────── claude.ai sandbox ─────────┘
```

1. `request-text open` starts a detached `serve` process (its own session, so
   the sandbox doesn't kill it when the bash call ends). The process makes an
   ECDH P-256 key pair, keeps the private key in memory only, and runs
   `hostc <port>` to get a public URL. The URL ends in a random 128-bit path;
   every other path returns 404.
2. The page embeds the public key. On submit, the browser makes its own
   temporary key pair and derives an AES-256-GCM key (ECDH → HKDF-SHA256, salt
   and AAD = request id). It POSTs `{epk, iv, ct}`.
3. `serve` decrypts, checks the fields match the form, writes
   `~/.request-text/<id>/payload.json` (0600), and replies 200. It then shuts
   down itself and the tunnel after 5s. It can't do that immediately: killing
   hostc straight away drops the reply in transit, and the browser shows a 502
   for a submission that actually landed.
4. `request-text wait <id>` polls for the payload and prints it. It stays under
   the sandbox's 300s per-command limit and uses exit codes to tell Claude
   whether to wait again (2), give the user a new URL (3), or give up (4).

hostc makes a new tunnel, with a new URL, whenever it reconnects. `serve`
watches for a second `Public URL:` line and `wait` reports it once as exit 3.

## Threat model

TLS ends at hostc's Cloudflare Worker, and the claude.ai sandbox's egress
gateway intercepts TLS on the way out too. The payload encryption means that
neither of them, nor any log they keep, sees plaintext. It does **not** protect
against an *actively* malicious hostc operator, which could serve a modified
page, since the relay delivers the page as well as the ciphertext. Pointing
hostc at your own deployment (`hostc config set server-url ...`, or
`HOSTC_SERVER_URL`) closes that. The link is single-use and expires (default 30
minutes).

## Why hostc

In the claude.ai sandbox only genuine HTTPS/WSS to a *hostname* gets out
(measured 2026-09-17). SSH tunnels, cloudflared (port 7844), and anything
addressed by bare IP fail. loca.lt and tunnelmole connected but were unreliable.
hostc runs on Workers + Durable Objects and uses WSS on 443, and it forwards
GET and POST (tested to 5 MB, byte-exact).

## Development

```bash
SANDBOX_NAME=rt-sandbox dev/sandbox.sh up
SANDBOX_NAME=rt-sandbox dev/sandbox.sh sh 'bash /mnt/skills/*/request-text/setup.sh'
SANDBOX_NAME=rt-sandbox dev/sandbox.sh sh 'node --test /mnt/skills/user/request-text/test_request_text.mjs'
python request-text/package.py   # -> request-text/request-text.zip
```

`open --local` skips the tunnel and serves on 127.0.0.1, which is what most
tests use. The reconnect test uses hostc's own `HOSTC_E2E_RECONNECT_SIGNAL`
hook; set `RT_SKIP_TUNNEL=1` to skip it when offline.
