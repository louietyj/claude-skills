#!/usr/bin/env python3
"""Build the uploadable skill zip. Nothing secret goes in it."""

import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "gmail-unsub"
MEMBERS = ["SKILL.md", "setup.sh", "bin/unsub.py", "page/template.html"]


def main() -> int:
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
    print("Requires durable-filesystem (cfs), bash-mcp, and the Gmail connector with read tools always-allowed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
