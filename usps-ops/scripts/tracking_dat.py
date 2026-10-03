"""Prepend a mailpiece to QuickLetterTracker's tracking.dat, same format as qlt-tracking.py.

    tracking_dat.py <tracking.dat> <31-digit imb> <recipient> [YYYY-MM-DD]

Each line is "M/D/YYYY<TAB>zip9<TAB>dpc<TAB>tracking20<TAB>RECIPIENT" with CRLF
endings, newest first. The date defaults to today. Edits the file in place and
prints the new line.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import imb  # noqa: E402


def make_line(imb_digits, recipient, date):
    f = imb.split(imb_digits)
    return f"{date.month}/{date.day}/{date.year}\t{f['zip9']}\t{f['dpc']}\t{f['tracking']}\t{recipient.upper()}"


def prepend(path, line):
    with open(path, "rb") as fi:
        data = fi.read()
    tracking = line.split("\t")[3].encode()
    if tracking in data:
        raise SystemExit(f"{tracking.decode()} is already in {path}")
    if data and not data.endswith(b"\r\n"):
        raise SystemExit(f"{path} doesn't end in CRLF; check it wasn't truncated or converted")
    with open(path, "wb") as fo:
        fo.write(line.encode() + b"\r\n" + data)


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5):
        sys.exit(__doc__)
    path, imb_digits, recipient = sys.argv[1:4]
    date = datetime.date.fromisoformat(sys.argv[4]) if len(sys.argv) == 5 else datetime.date.today()
    line = make_line(imb_digits, recipient, date)
    prepend(path, line)
    print(line)
