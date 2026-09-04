#!/usr/bin/env python3
"""Build the uploadable skill zip.

The zip embeds capsolver.key in plaintext. Treat the artefact as a secret:
anyone holding it can spend the CapSolver balance.
"""

import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "headless-browser"
MEMBERS = ["SKILL.md", "setup.sh", "cloak/package.json",
           "cloak/package-lock.json", "cloak/refresh.sh"]
KEY = "capsolver.key"
PLACEHOLDER = "CAP-YOUR-CAPSOLVER-API-KEY-HERE"


def read_key() -> str:
    path = os.path.join(HERE, KEY)
    if not os.path.exists(path):
        raise SystemExit(
            f"{KEY} missing -- copy {KEY}.example to {KEY} and put the real key "
            f"in it, or pass --no-key to build without captcha solving."
        )
    with open(path, encoding="utf-8") as fh:
        key = fh.read().strip()
    if not key or key == PLACEHOLDER:
        raise SystemExit(
            f"{KEY} still holds the placeholder -- put the real key in it. "
            f"The skill would install cleanly and then fail every captcha."
        )
    if not key.startswith("CAP-"):
        raise SystemExit(f"{KEY} does not look like a CapSolver key (expected a CAP- prefix).")
    return key


def main() -> int:
    with_key = "--no-key" not in sys.argv[1:]
    if with_key:
        key = read_key()

    members = list(MEMBERS) + ([KEY] if with_key else [])
    out = os.path.join(HERE, f"{NAME}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for member in members:
            src = os.path.join(HERE, member)
            if not os.path.exists(src):
                raise SystemExit(f"Missing {member}")
            zf.write(src, f"{NAME}/{member}")

    print(f"Wrote {out}")
    if with_key:
        print(f"Contains {KEY} in plaintext (ending ...{key[-4:]}) -- do not commit or share it.")
    else:
        print("Built without a key: the browser works, captcha solving is off.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
