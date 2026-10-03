"""Intelligent Mail barcode helpers on top of the vendored imb_core encoder.

An IMb is 31 digits here: a 20-digit tracking code followed by an 11-digit
routing code (ZIP5 + ZIP4 + 2-digit delivery point).

CLI:
    imb.py encode <31-digit imb>      -> FADT bar string
    imb.py decode <FADT bar string>   -> 31-digit imb
    imb.py build <serial> <routing11> -> our 31-digit imb (barcode id 00, STID 310, our MID)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import imb_core  # noqa: E402

OUR_MID = "903429007"
# Basic First-Class Mail, no address corrections, with IV-MTR.
OUR_STID = "310"
BARCODE_ID = "00"


def split(imb):
    if len(imb) != 31 or not imb.isdigit():
        raise ValueError(f"IMb must be 31 digits, got {imb!r}")
    tracking, routing = imb[:20], imb[20:]
    mid_len = 9 if tracking[5] == "9" else 6
    return {
        "barcode_id": tracking[0:2],
        "stid": tracking[2:5],
        "mid": tracking[5:5 + mid_len],
        "serial": tracking[5 + mid_len:],
        "tracking": tracking,
        "routing": routing,
        "zip9": routing[:9],
        "dpc": routing[9:],
    }


def encode(imb):
    f = split(imb)
    return imb_core.encode(int(f["barcode_id"]), int(f["stid"]), int(f["mid"]), int(f["serial"]), f["routing"])


def decode(bars):
    tracking, routing = imb_core.decode(bars)
    imb = tracking + routing
    if len(imb) != 31:
        raise ValueError(f"decoded IMb has a {len(routing)}-digit routing code, expected 11: {imb}")
    return imb


def build(serial, routing):
    if len(routing) != 11 or not routing.isdigit():
        raise ValueError(f"routing must be 11 digits, got {routing!r}")
    return f"{BARCODE_ID}{OUR_STID}{OUR_MID}{int(serial):06d}{routing}"


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    if cmd == "encode":
        print(encode(args[0]))
    elif cmd == "decode":
        print(decode(args[0]))
    elif cmd == "build":
        print(build(args[0], args[1]))
    else:
        sys.exit(__doc__)
