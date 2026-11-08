---
name: home-proxy
description: "Louie's home internet (a residential Comcast IP in Sunnyvale) as a local SOCKS5 proxy on 127.0.0.1:1080 and HTTP proxy on 127.0.0.1:1081, about 30 Mbps, for curl, Python, pip, git, ssh, pinchtab (`pinchtab-proxy start --proxy home`) or anything that takes a proxy. Use it when a site blocks the sandbox's datacenter IP, when a task needs a port other than 443, to reach his home LAN (router at 172.16.0.1), or when Louie wants something done from his own IP. Free. Not when the site should never see his home IP. For his tailnet devices or native TCP/UDP, use home-tailscale instead."
---

# home-proxy

```bash
bash "$(ls -1dt /mnt/skills/*/home-proxy ~/.claude/skills/synced/*/home-proxy 2>/dev/null | head -1)/setup.sh"
```

Connects in a few seconds and prints the exit IP. Idempotent; re-run it rather than checking whether it ran.
The client keeps running after the call returns.

Then point anything at it:

```bash
curl --proxy socks5h://127.0.0.1:1080 https://ipinfo.io/json
pip install --proxy http://127.0.0.1:1081 some-package
ssh -o ProxyCommand='nc -X 5 -x 127.0.0.1:1080 %h %p' user@host
```

- Unset `HTTPS_PROXY` for tools you point at it, or they go to the egress proxy first.
- Any port works, and the egress proxy's host blocks don't apply.
- **His home LAN is reachable** (`172.16.0.0/16`; the router is `172.16.0.1`). The sandbox's `NO_PROXY` lists
  every private range, so also unset `NO_PROXY`/`no_proxy`, or a LAN request skips the proxy and comes back
  `403 private_dest_ip` from the egress proxy.
- TCP only. For UDP, ICMP or tailnet devices, home-tailscale.

| Command | Does |
|---|---|
| `home-proxy status` | running? exit IP |
| `home-proxy tz` | the exit IP's timezone |
| `home-proxy stop` | stops the client |

## How it works

A gost client (fetched once from the cfs store, SHA-256 pinned) offers SOCKS5 and HTTP locally and tunnels to
`proxy.home.louietyj.me:443` as WebSocket over TLS, through the sandbox's egress proxy. That host is a spare
Android phone on his home Wi-Fi ([home-relay](https://github.com/louietyj/home-relay)), which makes the
outbound connections. Password-protected; the credentials come from `gost-auth.env` beside this file,
`$HOME_PROXY_AUTH` (`user:pass`), or the store at `/_devfiles/home-relay/secrets/gost-auth.env`.

## What to expect

- **It is his real IP.** Sites see Louie at home. No scraping at volume, no repeated hits on a site that is
  blocking you: a block lands on his own browsing.
- **Speed** is his home upload, about 30 Mbps down to you.
- **If it can't connect**: the phone is off, off Wi-Fi, or its services are down (home-relay's README has
  the checks), or his public IP changed in the last few minutes and DNS hasn't caught up.
