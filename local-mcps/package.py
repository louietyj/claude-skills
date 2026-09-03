#!/usr/bin/env python3
"""Build the uploadable skill zip.

The zip embeds the share link to the config, and the config holds API keys, so
the artefact is a credential: anyone holding it can read those keys.
"""

import json
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "local-mcps"
ALWAYS = ["SKILL.md", "setup.sh", "bin/lmcps.py", "references/config.md"]


def config_member():
    """`config-url.txt` is the normal route: the config stays on the durable
    filesystem and is edited there. A bundled `config.json` is the standalone
    alternative, at the cost of a repackage per config change."""
    url_file = os.path.join(HERE, "config-url.txt")
    if os.path.exists(url_file):
        with open(url_file, encoding="utf-8") as fh:
            links = [l.strip() for l in fh if l.strip() and not l.startswith("#")]
        if not links:
            raise SystemExit("config-url.txt has no link in it -- only comments.")
        if not links[0].startswith("http"):
            raise SystemExit(f"config-url.txt does not look like a URL: {links[0][:60]}")
        return "config-url.txt"

    local = os.path.join(HERE, "config.json")
    if os.path.exists(local):
        with open(local, encoding="utf-8") as fh:
            if not isinstance(json.load(fh).get("mcpServers"), dict):
                raise SystemExit("config.json has no `mcpServers` object.")
        return "config.json"

    raise SystemExit(
        "No config source. Create config-url.txt holding a share link to the "
        "config on the durable filesystem (see config-url.example.txt), or drop "
        "a config.json here to bundle one."
    )


def main() -> int:
    members = ALWAYS + [config_member()]
    out = os.path.join(HERE, f"{NAME}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for member in members:
            src = os.path.join(HERE, member)
            if not os.path.exists(src):
                raise SystemExit(f"Missing {member}")
            # Normalise to LF. The skill runs in a Linux sandbox, where a CRLF
            # shebang line makes bash reject the script -- and every editor and
            # tool on a Windows checkout is one careless text-mode write away
            # from reintroducing it. .gitattributes only covers tracked files,
            # so the zip is the last place this can be guaranteed.
            with open(src, "rb") as fh:
                zf.writestr(f"{NAME}/{member}", fh.read().replace(b"\r\n", b"\n"))

    print(f"Wrote {out}")
    print(f"Contains: {', '.join(members)}")
    print("Reaches the config, which holds API keys -- do not commit or share it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
