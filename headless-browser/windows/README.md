# pinchtab-windows: cloud Claude in Louie's Chrome

The Windows half of the headless-browser skill's second tier. The sandbox's
`pinchtab-windows` drives a pinchtab bridge attached to Louie's real Chrome on
this laptop, through `gatekeeper.py`, after he approves each grant in a dialog.

```
sandbox: pinchtab-windows ── https:443 ──► Cloudflare Access (service token)
                                              │ cloudflared tunnel
                                              ▼
          gatekeeper.py :19870   grants, OK/Cancel dialog, tray, audit log
              │ swaps the grant for the bridge's own token
              ▼
          pinchtab.exe bridge :19877  (fork release, guards down)
              │ :19871, gatekeeper's /json/version shim
              ▼
          Chrome, remote debugging enabled in chrome://inspect
```

Only pinchtab's HTTP API crosses the tunnel. pinchtab sends 12-40 CDP messages
per command, one after another, so carrying CDP itself over the ~120 ms
round trip from claude.ai cost 1.5 s per `snap` and 6 s per `nav`; through the
gatekeeper they take 0.24 s and 1 s.

## Pieces

- **Grants.** `POST /grant {purpose, code, agent}` pops a topmost OK/Cancel
  dialog (Cancel is the default button, so a stray Enter denies; 120 s unanswered
  denies). `GET /grant/<id>?wait=55` long-polls under Cloudflare's 100 s limit.
  A grant lapses after 30 min idle or 4 h. One request pending at a time, 30 s
  cooldown after a denial. `DELETE /grant` releases one.
- **Proxy.** Every other path needs `Authorization: Bearer <grant>`; the
  gatekeeper swaps in the bridge's token, which never leaves the laptop.
  `/shutdown`, `/ensure-chrome` and `/browser/restart` are refused.
- **Bridge lifecycle.** Chrome asks "Allow remote debugging?" on every attach,
  so the bridge starts on the first grant and stays attached between grants,
  detaching after 60 min without one. The tray's Revoke, Pause and Quit
  detach at once. It is stopped by killing the process, never `/shutdown`:
  see "Never call /shutdown" below.
- **`/active-tab`** lists Chrome's tabs with `visibilityState` and
  `hasFocus()`, so "the tab I'm on" is answerable.
- **Binary.** `pinchtab-windows-amd64.exe` from the fork's latest release, cached
  under `%LOCALAPPDATA%\pinchtab-remote\bin\<tag>\`, rechecked at start and daily.
  The sandbox installs the Linux build of the same release, and `grant --wait`
  warns when the two versions differ. `--pinchtab-bin <exe>` runs a local build.
- **State** in `%LOCALAPPDATA%\pinchtab-remote\`: `audit.jsonl` (every grant
  event and proxied call), `gatekeeper.log`, `bridge.log`, the bridge's config.

## Never call /shutdown

The bridge's Cleanup cancels the tab contexts it attached, and chromedp closes
the tab behind every non-first context when it is cancelled
(`chromedp.go:188-204`). On 2026-10-09 one `/shutdown` closed every one of
Louie's tabs, and Chrome with them. The fork now detaches instead
(`TabManager.ReleaseTargets`, from capsolver.32), but the gatekeeper still never
calls it: a killed process only drops its connection. Test anything that could
close tabs against a throwaway Chrome (`--remote-debugging-port` and its own
`--user-data-dir`), never his.

The bridge config also raises `maxTabs` to 500 with `tabEvictionPolicy:
reject`: pinchtab's default 20 with `close_lru` evicts the least recently used
tab, which here is one of his.

## Setup

1. **Chrome:** enable remote debugging in `chrome://inspect/#remote-debugging`.
2. **Gatekeeper:** `powershell -File install.ps1` installs the dependencies and a
   logon task (pythonw, this checkout), and starts it. `-Uninstall` removes it.
3. **Tunnel**, remotely managed (a locally managed one needs `config.yml` under the
   service's LocalSystem profile, a common trap). In Cloudflare Zero Trust:
   1. Networks > Tunnels > Create, cloudflared. Run the `cloudflared service
      install <token>` it shows, in an admin shell.
   2. Public hostname `browser.louietyj.me` -> `http://127.0.0.1:19870`.
   3. Access > Service auth > Create service token. Keep the ID and secret.
   4. Access > Applications > Self-hosted, `browser.louietyj.me`, with one
      policy whose action is **Service Auth** (not Allow: Allow redirects a
      service token to the login page) including that token.
4. **Skill:** copy `../windows.example.json` to `../windows.json` with the host and
   token, then `python ../package.py` and upload the zip.

Check from anywhere: `curl https://browser.louietyj.me/gate/status` is 403 at
Cloudflare; with `-H "CF-Access-Client-Id: …" -H "CF-Access-Client-Secret: …"`
it returns `{"protocol": 1, ...}`.

## Development

`python gatekeeper.py --no-tray --pinchtab-bin <local fork build>` runs it in a
console. `cloudflared tunnel --url http://127.0.0.1:19870` gives a throwaway
URL; `windows.json` takes an `http://` host for the docker sandbox
(`../../dev/sandbox.sh`).
