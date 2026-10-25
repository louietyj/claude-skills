#!/usr/bin/env python3
"""Offline tests for bin/commute.py. No network, no Maps key.

The grid cases are values the Maps panel actually displayed during the
reverse-engineering (2026-10-16), including the one boundary case it got the
other way.
"""

import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "bin"))

import commute  # noqa: E402

ARRIVE = "2026-10-19T12:00:00-07:00"
ARRIVAL = dt.datetime.fromisoformat(ARRIVE).timestamp()
NOW = ARRIVAL - 86400


class FakeMaps:
    """Stands in for commute.get. `durations(route, depart)` gives each
    route's pessimistic seconds for a departure time."""

    def __init__(self, routes, durations):
        self.routes = routes
        self.durations = durations
        self.calls = []

    def __call__(self, path, **params):
        self.calls.append((path, params))
        if path == "geocode/json":
            return {"status": "OK", "results": [{
                "formatted_address": params["address"] + ", USA",
                "geometry": {"location": {"lat": 37.4, "lng": -122.0}}}]}
        depart = params["departure_time"]
        return {"status": "OK", "routes": [
            {"summary": name, "legs": [{"distance": {"value": dist},
                                        "duration_in_traffic": {"value": self.durations(name, depart)}}]}
            for name, dist in self.routes(depart)]}


class Base(unittest.TestCase):
    def fake(self, routes, durations):
        maps = FakeMaps(routes, durations)
        orig = commute.get
        commute.get = maps
        self.addCleanup(setattr, commute, "get", orig)
        return maps


class RoundUp(unittest.TestCase):
    def test_grid(self):
        cases = [
            (540, 9), (541, 10),             # whole minutes under 10
            (599, 10), (601, 12),            # 2-minute steps from 10
            (1398, 24),                      # 23.3 -> 24
            (1799, 30), (1801, 35),          # 5-minute steps from 30
            (1926, 35),                      # 32.1 -> 35: the 11:25 for a 12:00 arrival
            (3000, 50), (5099, 85),          # 85 was displayed: still 5-minute steps
            (5399, 90), (5401, 100),         # 10-minute steps from 90
        ]
        for seconds, minutes in cases:
            self.assertEqual(commute.round_up(seconds), minutes, seconds)

    def test_raw_seconds_not_whole_minutes(self):
        # 3001 s is one second past 50:00. The panel showed 50 here, the only
        # miss in the controlled run; rounding to a minute first would match
        # it but lost on four others. Pinned so a "fix" has to face that.
        self.assertEqual(commute.round_up(3001), 55)


class Labels(unittest.TestCase):
    def test_first_part(self):
        self.assertEqual(commute.label("Example Dental, 100 Main St, Springfield, CA"), "Example Dental")
        self.assertEqual(commute.label("100 Main St Suite 2, Springfield, CA"), "100 Main St Suite 2")


class Resolve(Base):
    def test_latlng_skips_geocoding(self):
        maps = self.fake(lambda d: [], lambda n, d: 0)
        self.assertEqual(commute.resolve("37.39, -122.01"), ("37.39,-122.01", "37.39,-122.01"))
        self.assertEqual(maps.calls, [])

    def test_name_is_geocoded_to_coordinates(self):
        self.fake(lambda d: [], lambda n, d: 0)
        self.assertEqual(commute.resolve("100 Main St")[0], "37.4,-122.0")


class ArriveBy(Base):
    def test_fixed_point(self):
        # Later departures hit more traffic: 25 min plus 1 s per minute later.
        dur = lambda name, depart: 1500 + int((depart - (ARRIVAL - 3600)) / 60)
        maps = self.fake(lambda d: [("US-101 N", 18500)], dur)
        seconds, summary, dist, converged = commute.arrive_by("a", "b", ARRIVAL, now=NOW)
        self.assertTrue(converged)
        self.assertEqual(seconds, dur("US-101 N", ARRIVAL - seconds))
        self.assertEqual((summary, dist), ("US-101 N", 18500))
        self.assertTrue(all(p["traffic_model"] == "pessimistic" for _, p in maps.calls))

    def test_follows_the_first_route_when_order_flips(self):
        order = [[("CA-237 E", 14400), ("US-101 S", 22100)],
                 [("US-101 S", 22100), ("CA-237 E", 14410)]]
        calls = []

        def routes(depart):
            calls.append(depart)
            return order[min(len(calls) - 1, 1)]
        self.fake(routes, lambda name, d: 1900 if name == "CA-237 E" else 1500)
        _, summary, _, _ = commute.arrive_by("a", "b", ARRIVAL, now=NOW)
        self.assertEqual(summary, "CA-237 E")

    def test_departure_in_the_past_stops(self):
        self.fake(lambda d: [("X", 1000)], lambda n, d: 1500)
        with self.assertRaises(SystemExit):
            commute.arrive_by("a", "b", ARRIVAL, now=ARRIVAL - 600)


class DriveEvent(Base):
    def test_event_fields(self):
        self.fake(lambda d: [("CA-237 E", 14400)], lambda n, d: 1926)
        ev = commute.drive_event("100 Main St, Springfield, CA", "Example Sushi, 1 Elm St, Milpitas, CA",
                                 ARRIVE, now=NOW)
        self.assertEqual(ev["create_event"], {
            "summary": "Drive from 100 Main St to Example Sushi",
            "startTime": "2026-10-19T11:25:00",
            "endTime": "2026-10-19T12:00:00",
            "description": "CA-237 E",
            "visibility": "private",
        })
        self.assertEqual(ev["start"], "2026-10-19T11:25:00-07:00")
        self.assertEqual(ev["minutes"], 35)

    def test_keeps_seconds_and_labels(self):
        self.fake(lambda d: [("US-101 N", 40000)], lambda n, d: 2700)
        ev = commute.drive_event("1,2", "3,4", "2026-10-18T13:36:45-07:00",
                                 from_label="Home", to_label="BART", now=NOW)
        self.assertEqual(ev["create_event"]["summary"], "Drive from Home to BART")
        self.assertEqual(ev["create_event"]["startTime"], "2026-10-18T12:51:45")
        self.assertEqual(ev["create_event"]["endTime"], "2026-10-18T13:36:45")

    def test_buffer_is_its_own_contiguous_event(self):
        # A 3:45 flight with a 2-hour buffer: drive ends 1:45, buffer 1:45-3:45.
        self.fake(lambda d: [("US-101 N", 45000)], lambda n, d: 2600)
        ev = commute.drive_event("1,2", "SFO", "2026-10-19T15:45:00-07:00",
                                 buffer_min=120, buffer_title="Airport Buffer", now=NOW)
        drive, buffer = ev["create_event"], ev["buffer_event"]
        self.assertEqual((drive["startTime"], drive["endTime"]), ("2026-10-19T13:00:00", "2026-10-19T13:45:00"))
        self.assertEqual(buffer, {"summary": "Airport Buffer", "startTime": "2026-10-19T13:45:00",
                                  "endTime": "2026-10-19T15:45:00", "visibility": "private"})

    def test_no_buffer_event_without_buffer(self):
        self.fake(lambda d: [("X", 1000)], lambda n, d: 600)
        self.assertNotIn("buffer_event", commute.drive_event("1,2", "3,4", ARRIVE, now=NOW))

    def test_naive_arrival_rejected(self):
        with self.assertRaises(SystemExit):
            commute.drive_event("1,2", "3,4", "2026-10-19T12:00:00", now=NOW)


class Key(unittest.TestCase):
    def test_reads_lmcps_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "config.json").write_text(json.dumps({"mcpServers": {"mcp-google-maps": {
                "env": {"GOOGLE_MAPS_API_KEY": "k123"}}}}))
            orig, commute.LMCPS_HOME = commute.LMCPS_HOME, Path(tmp)
            env = os.environ.pop("COMMUTE_MAPS_KEY", None)
            try:
                self.assertEqual(commute.api_key(), "k123")
            finally:
                commute.LMCPS_HOME = orig
                if env is not None:
                    os.environ["COMMUTE_MAPS_KEY"] = env


if __name__ == "__main__":
    unittest.main()
