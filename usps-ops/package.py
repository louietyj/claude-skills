#!/usr/bin/env python3
"""Build the uploadable skill zip. Bundles no credentials."""

import glob
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "usps-ops"
MEMBERS = ["SKILL.md", "scripts/*", "fonts/*"]
BARCODE_FONT = "fonts/USPSIMBStandard.ttf"


def main() -> int:
    if not os.path.exists(os.path.join(HERE, BARCODE_FONT)):
        print(f"warning: {BARCODE_FONT} is missing; previews will show the barcode as letters")

    out = os.path.join(HERE, f"{NAME}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for pattern in MEMBERS:
            matches = sorted(glob.glob(os.path.join(HERE, pattern)))
            if not matches:
                raise SystemExit(f"Missing {pattern}")
            for src in matches:
                if os.path.isdir(src) or src.endswith(".pyc"):
                    continue
                rel = os.path.relpath(src, HERE).replace(os.sep, "/")
                with open(src, "rb") as fh:
                    data = fh.read()
                if src.endswith((".py", ".sh", ".md")):
                    data = data.replace(b"\r\n", b"\n")
                zf.writestr(f"{NAME}/{rel}", data)

    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
