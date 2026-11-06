---
name: home-exit
description: "Routes this sandbox's traffic out through Louie's home internet (a residential Comcast IP in Sunnyvale) via Tailscale, as a local SOCKS5 proxy on 127.0.0.1:1055 and HTTP proxy on 127.0.0.1:1056, usable by curl, Python, pinchtab (`pinchtab-proxy start --proxy home`), ssh or anything that takes a proxy, on any port. Use it when a site blocks the sandbox's datacenter IP, when a task needs a port other than 443, or when Louie wants something done from his own IP. Free, about 5 Mbps. Not for bulk downloads, and not when the site should never see his home IP."
---

# home-exit

```bash
bash "$(ls -1dt /mnt/skills/*/home-exit ~/.claude/skills/synced/*/home-exit 2>/dev/null | head -1)/setup.sh"
```

It downloads Tailscale once (about 30 MB, checked against a pinned SHA-256), joins
Louie's tailnet with an ephemeral auth key, and sends traffic out through whichever
device offers itself as an exit node: a phone on his home Wi-Fi. About 12 s cold, and
it prints the exit IP. Idempotent; re-run it rather than checking whether it ran.
tailscaled keeps running after the call returns.

Then point anything at it:

```bash
curl --proxy socks5h://127.0.0.1:1055 https://ipinfo.io/json
pip install --proxy http://127.0.0.1:1056 some-package
ssh -o ProxyCommand='nc -X 5 -x 127.0.0.1:1055 %h %p' user@host
```

- A browser: the web-browser skill's `pinchtab-proxy start --proxy home` starts this
  and a second browser behind it, with a fingerprint matched to the phone.
- Any port works, not just 443, and the egress proxy's host blocks don't apply.
- Unset `HTTPS_PROXY` for tools you point at it, or they go to the egress proxy first.

| Command | Does |
|---|---|
| `home-exit status [--check]` | joined? which exit? `--check` asks ipinfo.io for the exit IP |
| `home-exit tz` | the exit IP's timezone |
| `home-exit stop` | leaves the tailnet and stops tailscaled |

## What to expect

- **Speed**: about 5 Mbps and 0.4 s per new HTTPS connection: Tailscale relays every
  packet through its DERP servers, because the sandbox can't send UDP.
- **It is his real IP.** Sites see Louie at home. Don't run anything there that could
  get the IP flagged: no scraping at volume, no repeated hits on a site that is
  blocking you. A block lands on his own browsing.
- **If it can't join**: the auth key expires on 2027-02-04; Louie makes a new one in the
  Tailscale admin console (Settings, Keys, reusable and ephemeral). **If no exit node
  answers**: the phone is off, off Wi-Fi, or its "Run as exit node" toggle is off.
  If he ever runs two exit nodes, `HOME_EXIT_NODE=<name>` picks one.

The auth key comes from `tailscale-authkey` beside this file, `$HOME_EXIT_AUTHKEY`, or
the store at `/_devfiles/dot-claude/secrets/tailscale-authkey`. Untested in the claude.ai
chat sandbox, which intercepts all HTTPS; tailscaled may reject its certificates there.
