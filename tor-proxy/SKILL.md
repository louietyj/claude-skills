---
name: tor-proxy
description: "Runs a local Tor client as a generic proxy: SOCKS5 on 127.0.0.1:9050 and HTTP CONNECT on 127.0.0.1:9080, usable by curl, Python, pinchtab or any tool that takes a proxy. Use it when this sandbox's egress proxy blocks a host (a curl exit 35 on every try, or a 403 with x-block-reason: hostname_blocked over plain HTTP), as it does web.archive.org and archive.today, or to reach a .onion address. Slow (30-90 KB/s) and many sites block Tor exits, so it's for reaching what is otherwise unreachable, not for everyday fetches."
---

# tor-proxy

```bash
bash "$(ls -1dt /mnt/skills/*/tor-proxy ~/.claude/skills/synced/*/tor-proxy 2>/dev/null | head -1)/setup.sh"
```

It downloads Tor's expert bundle once (about 30 MB, checked against a pinned SHA-256),
starts tor and waits until it can carry traffic: under a minute. Idempotent; re-run it
rather than checking whether it ran. Tor keeps running in the background after the
call returns.

Then point anything at it:

```bash
curl --proxy socks5h://127.0.0.1:9050 https://check.torproject.org/api/ip
curl --proxy http://127.0.0.1:9080 https://web.archive.org/...
```

- Use `socks5h://`, not `socks5://`. With `h`, tor looks up the name: required for `.onion`,
  and it keeps lookups off the sandbox's DNS.
- The HTTP port speaks only `CONNECT`. curl does that for `https://` URLs. For a plain
  `http://` URL add `-p` (`--proxytunnel`); a client that sends plain proxy requests
  won't work with it.
- Python: `requests` with `proxies={"https": "socks5h://127.0.0.1:9050"}` (needs `PySocks`), or
  PySocks' `sockshandler.SocksiPyHandler` for urllib.

| Command | Does |
|---|---|
| `tor-proxy status [--check]` | running and bootstrapped? `--check` asks check.torproject.org |
| `tor-proxy newnym` | new circuits, so new connections leave from another exit |
| `tor-proxy stop` | stops tor |

## What to expect

- **Speed**: about 30-90 KB/s here. A 1 MB page takes 10-20 s, so set timeouts of a
  minute or more.
- **Exits get blocked.** Cloudflare, DataDome, Akamai and others challenge or refuse Tor
  exits. Try `newnym` once; if the site still refuses, Tor is the wrong tool.
- **archive.today**: over a Tor exit, HTTPS to archive.ph hangs, though plain HTTP and
  its onion address work. The archive-today skill handles this with `--tor`.
- **It's a proxy, not anonymity.** Nothing else in the sandbox is configured for privacy.

Behind an egress proxy (cloud Claude Code, Cowork), tor reaches the network through
Tor Browser's meek bridge, which looks like HTTPS to a CDN; plain Tor relays are
unreachable there. In the claude.ai chat sandbox, which intercepts all HTTPS, this is
untested and probably fails.

If `start` fails, it prints the end of tor's log; the full log is
`~/.cache/tor-proxy/tor.log`.
