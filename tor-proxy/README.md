# tor-proxy

A local Tor client as a generic proxy for the sandbox, for hosts the egress proxy blocks
(web.archive.org, archive.today) and for `.onion` addresses.

Tor's relays sit on bare IPs and non-443 ports, which the cloud egress proxy won't pass.
So behind `$HTTPS_PROXY` the torrc uses the meek bridge that Tor Browser ships: lyrebird
speaks HTTPS to a CDN (CDN77, fronted as www.phpmyadmin.net as of 16.0), and tor passes
it the egress proxy as `HTTPSProxy`. The bridge line is read from the bundle's own
`pt_config.json`, so it moves with the pinned version.

## Updating the pinned bundle

`VERSION` and `SHA256` at the top of `bin/tor-proxy` pin Tor's expert bundle. To move to
a new release, download `tor-expert-bundle-linux-x86_64-<version>.tar.gz` and its `.asc`
from `https://dist.torproject.org/torbrowser/<version>/`, verify the signature against
the Tor Browser Developers key (`EF6E 286D DA85 EA2A 4BA7 DE68 4E2C 6E87 9329 8290`), then
put the version and its `sha256sum` in the script. Old versions stay downloadable from
archive.torproject.org, which the script falls back to.

## Measured (cloud Claude Code, 2026-11-04)

- Cold start (download, check, bootstrap) 48 s; bootstrap alone 36-44 s.
- 1.2 MB Wikipedia page in 14 s; a 2.7 MB archive.today snapshot in 90 s.
- web.archive.org works through it. archive.ph over an exit: HTTPS hangs after the
  ClientHello on every exit tried, plain HTTP and the onion address work.

`python package.py` writes `tor-proxy.zip`.
