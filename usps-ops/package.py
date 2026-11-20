#!/usr/bin/env python3
"""Build the uploadable skill zip.

The zip embeds usps-iv.env when present, in plaintext: his USPS Business Customer
Gateway login. Treat the artefact as a secret.
"""

import glob
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "usps-ops"
MEMBERS = ["SKILL.md", "scripts/*", "fonts/*"]
CREDS = "usps-iv.env"


def main() -> int:
    members = list(MEMBERS)
    creds = os.path.join(HERE, CREDS)
    if os.path.exists(creds):
        with open(creds, encoding="utf-8") as fh:
            text = fh.read()
        empty = [k for k in ("USPS_USERNAME", "USPS_PASSWORD", "USPS_CLIENT_ID")
                 if not re.search(rf"^{k}=\S", text, re.M)]
        if empty:
            raise SystemExit(f"{CREDS} has no value for {', '.join(empty)}.")
        members.append(CREDS)
    else:
        print(f"No {CREDS}: iv_track.py will fall back to the cfs store for it.")
    out = os.path.join(HERE, f"{NAME}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for pattern in members:
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
