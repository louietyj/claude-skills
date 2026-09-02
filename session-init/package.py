#!/usr/bin/env python3
"""Build the uploadable skill zip.

Unlike the other skills here, this one bundles no credentials -- it only
orchestrates its siblings, which carry their own.
"""

import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "session-init"
MEMBERS = ["SKILL.md", "session-init.sh"]


def main() -> int:
    out = os.path.join(HERE, f"{NAME}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for member in MEMBERS:
            src = os.path.join(HERE, member)
            if not os.path.exists(src):
                raise SystemExit(f"Missing {member}")
            # Normalise to LF. The skill runs in a Linux sandbox, where a CRLF
            # shebang line makes bash reject the script -- and every editor and
            # tool on a Windows checkout is one careless text-mode write away
            # from reintroducing it. .gitattributes only covers tracked files,
            # so the zip is the last place this can be guaranteed.
            with open(src, "rb") as fh:
                zf.writestr(f"{NAME}/{member}", fh.read().replace(b"\r\n", b"\n"))

    print(f"Wrote {out}")
    print("Requires durable-filesystem and local-mcps to be installed too.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
