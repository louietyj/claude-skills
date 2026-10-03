"""Find the next free IMb serial for our MID.

    next_serial.py <file>...      (or text on stdin)

Scans any text (tracking.dat, a Sent Mail folder listing, filenames) for IMb
tracking codes under our MID and prints the highest serial + 1, with where
each of our serials was seen.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from imb import OUR_MID  # noqa: E402

# Barcode ID (2) + STID (3) + our 9-digit MID + 6-digit serial; routing may follow.
OUR_TRACKING_RE = re.compile(r"(?<!\d)\d{5}" + OUR_MID + r"(\d{6})(?:\d{11})?(?!\d)")


def serials(text):
    return [int(m.group(1)) for m in OUR_TRACKING_RE.finditer(text)]


def main(paths):
    sources = [(p, open(p, encoding="utf-8", errors="replace").read()) for p in paths] or [("stdin", sys.stdin.read())]
    seen = {}
    for name, text in sources:
        for serial in serials(text):
            seen.setdefault(serial, set()).add(name)
    if not seen:
        sys.exit("no IMbs under our MID found; refusing to guess a serial")
    for serial in sorted(seen):
        print(f"seen serial {serial:06d} in: {', '.join(sorted(seen[serial]))}", file=sys.stderr)
    print(max(seen) + 1)


if __name__ == "__main__":
    main(sys.argv[1:])
