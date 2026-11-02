#!/usr/bin/env python3
"""Build the uploadable skill zip.

The zip embeds capsolver.key, and 2captcha.key and nonecap.key when present (all
three from the repo's keys/), plus windows.json and browser-use.key when present,
in plaintext. Treat the artefact as a secret: anyone holding it can spend every
balance, and ask Louie's laptop for browser grants.
"""

import json
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "keys"))
from solver_keys import (KEY, NONECAP_KEY, TWOCAPTCHA_KEY, read_key,  # noqa: E402
                         read_nonecap_key, read_twocaptcha_key)
from solver_keys import path as key_path  # noqa: E402

SOLVER_KEYS = {KEY, TWOCAPTCHA_KEY, NONECAP_KEY}

NAME = "web-browser"
MEMBERS = ["SKILL.md", "setup.sh", "cloak/package.json",
           "cloak/package-lock.json", "cloak/refresh.sh"]
# Optional: the gate key for pinchtab-windows, Louie's Chrome.
WINDOWS = "windows.json"
# Optional: the Browser Use key behind pinchtab-cloud.
BROWSER_USE_KEY = "browser-use.key"


def read_browser_use_key() -> str:
    path = os.path.join(HERE, BROWSER_USE_KEY)
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as fh:
        key = fh.read().strip()
    if not key.startswith("bu_"):
        raise SystemExit(f"{BROWSER_USE_KEY} does not look like a Browser Use key (expected a bu_ prefix).")
    return key


def read_windows() -> dict:
    path = os.path.join(HERE, WINDOWS)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    for field in ("host", "gate_key"):
        if not str(cfg.get(field, "")).strip():
            raise SystemExit(f"{WINDOWS} lacks {field}.")
    if cfg["gate_key"].startswith("replace-"):
        raise SystemExit(f"{WINDOWS} still holds the example key.")
    return cfg


def check_description() -> None:
    with open(os.path.join(HERE, "SKILL.md"), encoding="utf-8") as fh:
        m = re.search(r'^description: "(.*)"$', fh.read(), re.M)
    if not m or len(m.group(1)) > 1024:
        raise SystemExit(f"SKILL.md's description is {len(m.group(1)) if m else '?'} characters; claude.ai allows 1024.")


def main() -> int:
    check_description()
    with_key = "--no-key" not in sys.argv[1:]
    two_key = none_key = ""
    if with_key:
        key = read_key()
        two_key = read_twocaptcha_key()
        none_key = read_nonecap_key()
    windows = read_windows()
    bu_key = read_browser_use_key()

    members = (list(MEMBERS) + ([KEY] if with_key else []) + ([TWOCAPTCHA_KEY] if two_key else [])
               + ([NONECAP_KEY] if none_key else [])
               + ([WINDOWS] if windows else []) + ([BROWSER_USE_KEY] if bu_key else []))
    out = os.path.join(HERE, f"{NAME}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for member in members:
            src = key_path(member) if member in SOLVER_KEYS else os.path.join(HERE, member)
            if not os.path.exists(src):
                raise SystemExit(f"Missing {member}")
            zf.write(src, f"{NAME}/{member}")

    print(f"Wrote {out}")
    if with_key:
        print(f"Contains {KEY} in plaintext (ending ...{key[-4:]}) -- do not commit or share it.")
        if two_key:
            print(f"Contains {TWOCAPTCHA_KEY} in plaintext (ending ...{two_key[-4:]}) too.")
        else:
            print(f"No {TWOCAPTCHA_KEY}: FunCaptcha will go unsolved, and hCaptcha has no fallback.")
        if none_key:
            print(f"Contains {NONECAP_KEY} in plaintext (ending ...{none_key[-4:]}) too.")
        else:
            print(f"No {NONECAP_KEY}: hCaptcha goes to 2Captcha alone.")
    else:
        print("Built without a key: the browser works, captcha solving is off.")
    if windows:
        print(f"Contains {WINDOWS} (gate key for {windows['host']}) -- pinchtab-windows is on.")
    else:
        print(f"No {WINDOWS}: pinchtab-windows (Louie's Chrome) is off.")
    if bu_key:
        print(f"Contains {BROWSER_USE_KEY} in plaintext (ending ...{bu_key[-4:]}) -- pinchtab-cloud is on.")
    else:
        print(f"No {BROWSER_USE_KEY}: pinchtab-cloud is off.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
