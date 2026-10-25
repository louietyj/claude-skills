#!/usr/bin/env python3
"""commute -- the arithmetic behind calendar-commute.

  drive   the drive event Google Calendar's Maps panel would add before an
          event: pessimistic traffic for an arrive-by time, snapped up onto
          Maps' display grid, titled and described the way the panel does it

The Maps key comes from local-mcps' config (the mcp-google-maps server), so
nothing secret lives in this skill. stdlib only: the claude.ai sandbox cannot
pip install.
"""

import argparse
import datetime as dt
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://maps.googleapis.com/maps/api/"
LMCPS_HOME = Path(os.environ.get("LMCPS_HOME", Path.home() / ".lmcps"))
LATLNG = re.compile(r"^\s*-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?\s*$")

# Any start converges; this one usually takes two or three calls.
FIRST_GUESS_S = 20 * 60
MAX_CALLS = 6


def die(msg):
    print(f"commute: {msg}", file=sys.stderr)
    sys.exit(1)


# --- Maps API -----------------------------------------------------------------

def api_key():
    key = os.environ.get("COMMUTE_MAPS_KEY")
    if key:
        return key
    cfg = LMCPS_HOME / "config.json"
    try:
        servers = json.loads(cfg.read_text())["mcpServers"]
        return servers["mcp-google-maps"]["env"]["GOOGLE_MAPS_API_KEY"]
    except FileNotFoundError:
        die(f"no {cfg}. Run session-init (it sets up local-mcps), then retry.")
    except (KeyError, ValueError):
        die(f"{cfg} has no mcp-google-maps GOOGLE_MAPS_API_KEY.")


def get(path, **params):
    what = params.get("address") or f"{params.get('origin')} -> {params.get('destination')}"
    params["key"] = api_key()
    url = API + path + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as resp:
        body = json.load(resp)
    if body.get("status") != "OK":
        die(f"{path} returned {body.get('status')} for {what}. {body.get('error_message', '')}".rstrip())
    return body


def resolve(location):
    """Coordinates, never names, go to routing: a name let Google geocode it
    elsewhere, and that confound alone turned 23/24 matches into 25/54."""
    if LATLNG.match(location):
        latlng = location.replace(" ", "")
        return latlng, latlng
    result = get("geocode/json", address=location)["results"][0]
    loc = result["geometry"]["location"]
    return f"{loc['lat']},{loc['lng']}", result["formatted_address"]


def routes(origin, dest, depart, model):
    body = get("directions/json", origin=origin, destination=dest,
               departure_time=int(depart), traffic_model=model, alternatives="true")
    return [(r["summary"], r["legs"][0]["distance"]["value"],
             r["legs"][0]["duration_in_traffic"]["value"]) for r in body["routes"]]


# --- the panel's arithmetic ---------------------------------------------------

def label(location):
    """Stand-in for the panel's place name: a calendar location leads with it."""
    return location.split(",")[0].strip()


def grid_step(minutes):
    return 1 if minutes < 10 else 2 if minutes < 30 else 5 if minutes < 90 else 10


def round_up(seconds):
    """Ceil the raw seconds onto Maps' grid: 23/24 matched, vs 20/24 when
    rounding to a whole minute first."""
    minutes = seconds / 60
    step = grid_step(minutes)
    return math.ceil(minutes / step - 1e-9) * step


def same_route(found, summary, distance):
    match = [r for r in found
             if r[0] == summary and abs(r[1] - distance) <= max(150, 0.015 * distance)]
    return min(match, key=lambda r: abs(r[1] - distance)) if match else None


def arrive_by(origin, dest, arrival, model="pessimistic", now=None):
    """The API ignores traffic on a driving arrival_time, so iterate
    departure = arrival - duration to a fixed point, following the first
    call's route (the one Add to Calendar sits under)."""
    now = time.time() if now is None else now
    seconds, route, converged = FIRST_GUESS_S, None, False
    for _ in range(MAX_CALLS):
        depart = arrival - seconds
        if depart <= now:
            die("the drive would have to start in the past; there is nothing to add.")
        found = routes(origin, dest, depart, model)
        if not found:
            die(f"no driving route from {origin} to {dest}.")
        route = (same_route(found, route[0], route[1]) if route else None) or found[0]
        if route[2] == seconds:
            converged = True
            break
        seconds = route[2]
    return seconds, route[0], route[1], converged


def local(t):
    return t.replace(tzinfo=None).isoformat()


def drive_event(from_loc, to_loc, arrive_iso, from_label=None, to_label=None,
                buffer_min=0, buffer_title="Buffer", now=None):
    """A buffer is its own event, never folded into the drive, so drive,
    buffer and event stay contiguous."""
    event_start = dt.datetime.fromisoformat(arrive_iso.replace("Z", "+00:00"))
    if event_start.tzinfo is None:
        die("--arrive needs a UTC offset, as the event's start.dateTime carries.")
    arrival = event_start - dt.timedelta(minutes=buffer_min)
    origin, origin_addr = resolve(from_loc)
    dest, dest_addr = resolve(to_loc)
    seconds, summary, distance, converged = arrive_by(origin, dest, arrival.timestamp(), now=now)
    minutes = round_up(seconds)
    start = arrival - dt.timedelta(minutes=minutes)
    out = {
        "create_event": {
            "summary": f"Drive from {from_label or label(from_loc)} to {to_label or label(to_loc)}",
            "startTime": local(start),
            "endTime": local(arrival),
            "description": summary,
            "visibility": "private",
        },
        "start": start.isoformat(),
        "end": arrival.isoformat(),
        "minutes": minutes,
        "pessimistic_s": seconds,
        "converged": converged,
        "route": {"summary": summary, "distance_m": distance},
        "from": {"location": from_loc, "resolved": origin_addr, "latlng": origin},
        "to": {"location": to_loc, "resolved": dest_addr, "latlng": dest},
    }
    if buffer_min:
        out["buffer_event"] = {
            "summary": buffer_title,
            "startTime": local(arrival),
            "endTime": local(event_start),
            "visibility": "private",
        }
    return out


# --- main ---------------------------------------------------------------------

def cmd_drive(args):
    event = drive_event(args.from_loc, args.to_loc, args.arrive, args.from_label, args.to_label,
                        args.buffer, args.buffer_title)
    print(json.dumps(event, indent=2, ensure_ascii=False))
    return 0


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="commute", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("drive", help="the drive event to add before an event")
    d.add_argument("--from", dest="from_loc", required=True, metavar="LOCATION",
                   help="where the drive starts: an address, place, or lat,lng")
    d.add_argument("--to", dest="to_loc", required=True, metavar="LOCATION",
                   help="the event's location string, verbatim")
    d.add_argument("--arrive", required=True, metavar="ISO",
                   help="the event's start.dateTime, offset included")
    d.add_argument("--from-label", help="title label for the origin (default: first part of --from)")
    d.add_argument("--to-label", help="title label for the destination (default: first part of --to)")
    d.add_argument("--buffer", type=int, default=0, metavar="MINUTES",
                   help="a separate buffer event between the drive and the event")
    d.add_argument("--buffer-title", default="Buffer", help='e.g. "Airport Buffer"')
    d.set_defaults(func=cmd_drive)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
