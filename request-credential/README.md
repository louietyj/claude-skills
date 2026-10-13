# request-credential

Claude asks for a site's login; Louie's phone shows a notification; the
[cred-bridge](https://github.com/louietyj/cred-bridge) app opens a login form
that tells Android autofill it belongs to that site, so his password manager
offers that site's accounts; he picks one and taps Send.

```
sandbox ── POST /request {site, reason, url} ──▶ Worker ── FCM {rid} ──▶ phone
                                                  ◀── GET /device/request/:rid ──
sandbox ◀═══════════ sealed login, POSTed to the request-text link ═══════════ phone
```

- **The login never touches the Worker.** The app seals it to the
  request-text request Claude opened (the same ECDH/HKDF/AES-GCM scheme as
  request-text's page), so only that sandbox can read it.
- **FCM carries only a request id.** Site, reason and link come from the
  Worker over the device token.
- **On the sandbox side it lives in RAM, briefly.** request-credential points
  request-text at `/dev/shm` (tmpfs in claude.ai's sandbox), since deleted bytes
  on disk or an overlay layer can survive. `wait` never prints the login; `run`
  hands it to one command as env vars, redacts that command's output, and
  deletes it on success. Unused, or after a failed run, it is kept one minute,
  enforced at the start of every `request-credential` command because nothing
  survives in the background between turns.

## Trust

Anyone with the CLI token can push a request whose link they control, so the
skill zip is a credential. What Louie checks: the domain shown in large type
is exactly what autofill matches on (the app refuses anything but plain ASCII
domains), and the reason is Claude's own words. Rotate with
`wrangler secret put CLI_TOKEN`, update `config.json`, re-package.

## Deploy

```bash
cd worker
wrangler deploy
wrangler secret put CLI_TOKEN      # also in ../config.json
wrangler secret put DEVICE_TOKEN   # only the app holds it, via pairing
wrangler secret put FCM_SA_JSON    # key of a service account with only roles/firebasecloudmessaging.admin
```

The Firebase project is `cred-bridge`; the app's `google-services.json` comes
from `firebase apps:sdkconfig`. Pair the phone with `request-credential pair`.

`python package.py` builds the zip (it embeds `config.json`).

## Tests

```bash
python worker/test_worker.py               # Worker vs wrangler dev and a fake Google
node --test test_request_credential.mjs    # redaction, run/forget/expiry vs a fake request-text
```
