#!/usr/bin/env python3
"""Build the uploadable skill zip. Bundles no credentials."""

import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "transcript-export"
MEMBERS = ["SKILL.md", "export.sh"]


def main() -> int:
    out = os.path.join(HERE, f"{NAME}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for member in MEMBERS:
            src = os.path.join(HERE, member)
            if not os.path.exists(src):
                raise SystemExit(f"Missing {member}")
            # Normalise to LF: a CRLF shebang breaks the script in the sandbox.
            with open(src, "rb") as fh:
                zf.writestr(f"{NAME}/{member}", fh.read().replace(b"\r\n", b"\n"))
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
