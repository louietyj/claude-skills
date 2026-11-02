#!/usr/bin/env python3
"""Build the uploadable skill zip.

The zip embeds capsolver.key, and 2captcha.key when present, from the repo's
keys/, in plaintext. Treat the artefact as a secret: anyone holding it can spend
both balances.
"""

import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "keys"))
from solver_keys import KEY, TWOCAPTCHA_KEY, read_key, read_twocaptcha_key  # noqa: E402

NAME = "archive-today"
MEMBERS = ["SKILL.md", "TROUBLESHOOTING.md", "setup.sh", "bin/archive_today.py"]


def main() -> int:
    with open(os.path.join(HERE, "SKILL.md"), encoding="utf-8") as fh:
        m = re.search(r'^description: "(.*)"$', fh.read(), re.M)
    if not m or len(m.group(1)) > 1024:
        raise SystemExit(f"SKILL.md's description is {len(m.group(1)) if m else '?'} characters; claude.ai allows 1024.")
    key, two_key = read_key(), read_twocaptcha_key()

    out = os.path.join(HERE, f"{NAME}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for member in MEMBERS:
            src = os.path.join(HERE, member)
            if not os.path.exists(src):
                raise SystemExit(f"Missing {member}")
            # LF only: this runs in a Linux sandbox. See session-init/package.py.
            with open(src, "rb") as fh:
                zf.writestr(f"{NAME}/{member}", fh.read().replace(b"\r\n", b"\n"))
        zf.writestr(f"{NAME}/{KEY}", key)
        if two_key:
            zf.writestr(f"{NAME}/{TWOCAPTCHA_KEY}", two_key)

    print(f"Wrote {out}")
    print(f"Contains {KEY} in plaintext (ending ...{key[-4:]}) -- do not commit or share it.")
    print(f"Contains {TWOCAPTCHA_KEY} (ending ...{two_key[-4:]}) too." if two_key
          else f"No {TWOCAPTCHA_KEY}: no fallback when CapSolver fails.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
