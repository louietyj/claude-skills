#!/usr/bin/env python3
"""Build the uploadable skill zip. Nothing secret goes in it."""

import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "tor-proxy"
MEMBERS = ["SKILL.md", "setup.sh", "bin/tor-proxy"]


def main() -> int:
    with open(os.path.join(HERE, "SKILL.md"), encoding="utf-8") as fh:
        m = re.search(r'^description: "(.*)"$', fh.read(), re.M)
    if not m or len(m.group(1)) > 1024:
        raise SystemExit(f"SKILL.md's description is {len(m.group(1)) if m else '?'} characters; claude.ai allows 1024.")
    out = os.path.join(HERE, f"{NAME}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for member in MEMBERS:
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
