#!/usr/bin/env python3
"""Build the uploadable skill zip. Nothing secret goes in it: the Maps key
lives in local-mcps' config, not here."""

import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "calendar-commute"
MEMBERS = ["SKILL.md", "setup.sh", "bin/commute.py"]


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
    print("Requires local-mcps (the mcp-google-maps key) and the Google Calendar connector.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
