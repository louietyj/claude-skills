"""Captcha-solver keys, shared by the package.py of every skill that bundles them.

Each skill ships its own copy in its zip, at the zip's top level under the same
file name, so the installed skills never depend on one another.
"""

import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
KEY = "capsolver.key"
PLACEHOLDER = "CAP-YOUR-CAPSOLVER-API-KEY-HERE"
# Optional: the only provider left for FunCaptcha, and hCaptcha's fallback.
TWOCAPTCHA_KEY = "2captcha.key"
# Optional: hCaptcha only, tried before 2Captcha.
NONECAP_KEY = "nonecap.key"


def read_key() -> str:
    path = os.path.join(HERE, KEY)
    if not os.path.exists(path):
        raise SystemExit(
            f"keys/{KEY} missing -- copy keys/{KEY}.example to keys/{KEY} and put the real key in it."
        )
    with open(path, encoding="utf-8") as fh:
        key = fh.read().strip()
    if not key or key == PLACEHOLDER:
        raise SystemExit(
            f"keys/{KEY} still holds the placeholder -- put the real key in it. "
            f"The skill would install cleanly and then fail every captcha."
        )
    if not key.startswith("CAP-"):
        raise SystemExit(f"keys/{KEY} does not look like a CapSolver key (expected a CAP- prefix).")
    return key


def read_twocaptcha_key() -> str:
    path = os.path.join(HERE, TWOCAPTCHA_KEY)
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as fh:
        key = fh.read().strip()
    if not re.fullmatch(r"[0-9a-f]{32}", key):
        raise SystemExit(f"keys/{TWOCAPTCHA_KEY} does not look like a 2Captcha key (expected 32 hex characters).")
    return key


def read_nonecap_key() -> str:
    path = os.path.join(HERE, NONECAP_KEY)
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as fh:
        key = fh.read().strip()
    if not key.startswith("nc_live_"):
        raise SystemExit(f"keys/{NONECAP_KEY} does not look like a NoneCap key (expected an nc_live_ prefix).")
    return key


def path(name: str) -> str:
    return os.path.join(HERE, name)

