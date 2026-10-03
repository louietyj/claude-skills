#!/usr/bin/env python3
"""Build the uploadable skill zip.

The zip embeds config.json, which holds the CLI token (reads any live
conversation's transcript, buzzes Louie's watch) and the device token (streams
audio into a session). The artefact is a credential.
"""

import json
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "claude-listen"

# dev/, spike/ and worker/ are deliberately absent: the Worker is deployed from a
# checkout with wrangler, never from inside a conversation.
MEMBERS = [
    "SKILL.md",
    "GUIDE.md",
    "setup.sh",
    "bin/listen.py",
    "config.json",
]

REQUIRED_CONFIG = ("worker_url", "cli_token", "device_token")


def check_config():
    path = os.path.join(HERE, "config.json")
    if not os.path.exists(path):
        raise SystemExit("No config.json. Copy config.example.json and fill it in.")
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    missing = [k for k in REQUIRED_CONFIG if not cfg.get(k)]
    if missing:
        raise SystemExit(f"config.json is missing: {', '.join(missing)}")
    return cfg


def main() -> int:
    cfg = check_config()
    out = os.path.join(HERE, f"{NAME}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for member in MEMBERS:
            src = os.path.join(HERE, member)
            if not os.path.exists(src):
                raise SystemExit(f"Missing {member}")
            # Normalise to LF. The skill runs in a Linux sandbox, where a CRLF
            # shebang makes bash reject the script -- and every tool on a
            # Windows checkout is one text-mode write away from reintroducing
            # it. .gitattributes only covers tracked files, and config.json is
            # untracked, so the zip is the last place this can be guaranteed.
            with open(src, "rb") as fh:
                zf.writestr(f"{NAME}/{member}", fh.read().replace(b"\r\n", b"\n"))

    print(f"Wrote {out}")
    print(f"Contains: {', '.join(MEMBERS)}")
    print(f"Points at: {cfg['worker_url']}")
    print("Embeds the CLI and device tokens -- do not commit or share it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
