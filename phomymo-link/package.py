#!/usr/bin/env python3
"""Build the uploadable skill zip. Bundles no templates; those live in Dropbox."""

import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "phomymo-link"
MEMBERS = ["SKILL.md", "design-format.md", "make_link.py", "preview.mjs"]


def main() -> int:
    out = os.path.join(HERE, f"{NAME}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for member in MEMBERS:
            src = os.path.join(HERE, member)
            if not os.path.exists(src):
                raise SystemExit(f"Missing {member}")
            with open(src, "rb") as fh:
                zf.writestr(f"{NAME}/{member}", fh.read().replace(b"\r\n", b"\n"))

    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
