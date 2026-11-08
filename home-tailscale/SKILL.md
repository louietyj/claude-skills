---
name: home-tailscale
description: "Joins Louie's tailnet from this sandbox, giving native TCP and UDP (any port, any protocol, no proxy) to his tailnet devices and his home LAN (172.16.0.0/16, router at 172.16.0.1), and optionally his home internet through an exit node. Use it to reach a device or service at home or on the tailnet, to run something that needs raw TCP/UDP the egress proxy blocks, or to ssh into his machines. Slow (about 5 Mbps). For plain web traffic from his home IP, use home-proxy instead: it is faster and doesn't join the tailnet."
---

# home-tailscale

```bash
bash "$(ls -1dt /mnt/skills/*/home-tailscale ~/.claude/skills/synced/*/home-tailscale 2>/dev/null | head -1)/setup.sh"
```

Downloads Tailscale once (pinned SHA-256), joins as an ephemeral device and prints how to reach things. About
10 s cold; idempotent. It is ordinary `tailscaled`, so the usual `tailscale` commands work against its
socket, `$HOME/.cache/home-tailscale/tailscaled.sock`.

## Modes

- **Kernel** (default, where the sandbox can create a TUN device, as in cloud Claude Code): a real
  `tailscale0`, so any program reaches tailnet IPs and `172.16.0.0/16` natively, TCP and UDP. The sandbox's
  `NO_PROXY` covers private ranges, but curl and friends still need `HTTPS_PROXY` unset for these hosts.
  MagicDNS is off (resolv.conf is left alone): use 100.x addresses (`status` lists them).
- **Userspace** (`home-tailscale start --userspace`, or automatically where there is no TUN): no interface,
  a SOCKS5 proxy on `127.0.0.1:1055` and HTTP on `:1056` instead. TCP only.
- **Internet through his home** (`home-tailscale start --exit`, userspace): the proxy's internet traffic
  leaves from his exit node, any port, TCP only. Native TCP/UDP to the internet from his IP is possible in
  principle but not working yet: a kernel-mode exit node also captures the sandbox's own egress tunnels and
  cuts it off, and scoping it to one user fails (tailscaled restores its catch-all rule). Untried:
  tailscaled in its own network namespace.

`home-tailscale routes` lists the home ranges; `home-tailscale stop` leaves the tailnet.

## His network

- The exit node and subnet router is a spare phone on his home Wi-Fi (`b1661v`), which also advertises
  `172.16.0.0/16`. It runs [home-relay](https://github.com/louietyj/home-relay): Termux with sshd on
  `:8022` (key in the cfs store at `/_devfiles/home-relay/secrets/admin_ed25519`).
- The sandbox can't send UDP, so everything goes over DERP relays (WireGuard inside TLS on 443). The tailnet
  has its own relay on that phone, `derp.home.louietyj.me` (region 999).
- **Speed: about 5 Mbps**, whichever relay: userspace TCP on both ends over TCP. Fine for ssh, APIs and
  control traffic; for bulk web traffic from his IP use home-proxy (about 30 Mbps).
- **Through an exit node it is his real IP.** Nothing that could get it flagged.

The auth key (reusable, ephemeral, expires 2027-02-04; Louie renews it in the Tailscale admin console under
Settings, Keys) comes from `tailscale-authkey` beside this file, `$HOME_TAILSCALE_AUTHKEY`, or the store at
`/_devfiles/dot-claude/secrets/tailscale-authkey`. If no exit node answers, the phone is off, off Wi-Fi, or its
"Run as exit node" toggle is off.
