#!/usr/bin/env python3
"""Build the uploadable skill zip. Nothing secret goes in it."""

import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "request-text"
MEMBERS = ["SKILL.md", "setup.sh", "bin/request-text.mjs", "web/form.html"]


def main() -> int:
    out = os.path.join(HERE, f"{NAME}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for member in MEMBERS:
            src = os.path.join(HERE, member)
            if not os.path.exists(src):
                raise SystemExit(f"Missing {member}")
            zf.write(src, f"{NAME}/{member}")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
