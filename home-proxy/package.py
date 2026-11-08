#!/usr/bin/env python3
"""Build the uploadable skill zip.

The zip embeds gost-auth.env when present, in plaintext: anyone holding it can use
Louie's home IP as a proxy. Treat the artefact as a secret.
"""

import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "home-proxy"
MEMBERS = ["SKILL.md", "setup.sh", "bin/home-proxy"]
AUTHKEY = "gost-auth.env"


def main() -> int:
    with open(os.path.join(HERE, "SKILL.md"), encoding="utf-8") as fh:
        m = re.search(r'^description: "(.*)"$', fh.read(), re.M)
    if not m or len(m.group(1)) > 1024:
        raise SystemExit(f"SKILL.md's description is {len(m.group(1)) if m else '?'} characters; claude.ai allows 1024.")
    members = list(MEMBERS)
    key = os.path.join(HERE, AUTHKEY)
    if os.path.exists(key):
        with open(key, encoding="utf-8") as fh:
            if "GOST_PASS=" not in fh.read():
                raise SystemExit(f"{AUTHKEY} has no GOST_PASS= line.")
        members.append(AUTHKEY)
    else:
        print(f"No {AUTHKEY}: the skill will fall back to the cfs store for it.")
    out = os.path.join(HERE, f"{NAME}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for member in members:
            src = os.path.join(HERE, member)
            if not os.path.exists(src):
                raise SystemExit(f"Missing {member}")
            # LF only: this runs in a Linux sandbox. See session-init/package.py.
            with open(src, "rb") as fh:
                zf.writestr(f"{NAME}/{member}", fh.read().replace(b"\r\n", b"\n"))
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
