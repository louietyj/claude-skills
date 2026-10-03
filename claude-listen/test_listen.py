#!/usr/bin/env python3
"""Offline tests for the listen CLI. No network: the relay is stubbed in this
process. Named for the symptom Claude or Louie would see, not the function.

    python test_listen.py
"""
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("listen", HERE / "bin" / "listen.py")
listen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(listen)

CFG = {"worker_url": "https://relay.test", "cli_token": "tok", "device_token": "dev"}


class Relay:
    """Stands in for `request`: answers by path, records every call."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, *, method="GET", body=None, timeout=30):
        path, _, query = url.partition("?")
        path = path.replace(CFG["worker_url"], "")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        self.calls.append({"path": path, "params": params, "body": body, "method": method})
        out = self.routes.get(path, (404, {"error": "not found"}))
        return out(params, body) if callable(out) else out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(listen, "STATE", self.tmp.name),
                        mock.patch.object(listen, "load_config", lambda: dict(CFG))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def run_cli(self, argv, relay, stdin=None):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with mock.patch.object(listen, "request", relay), \
             mock.patch.object(sys, "argv", ["listen", *argv]), \
             mock.patch.object(sys, "stdin", stdin), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                listen.main()
            except SystemExit as e:
                code = e.code or 0
        return code, out.getvalue(), err.getvalue()

    def watch_json(self, argv=("watch",), body=None, status=200):
        relay = Relay({"/watch": (status, body)})
        code, out, err = self.run_cli(list(argv), relay)
        return relay, (json.loads(out) if out.strip() else None), err


def watch_body(**kw):
    body = {"event": "transcript", "id": "K7QM", "state": "live", "elapsed_s": 75,
            "entries": [], "last": 0, "more": False, "tail": None,
            "device": {"connected": True, "ui": "watch", "hidden_s": None, "disconnected_s": None,
                       "last_audio_s": 0, "transcriber": "open"}}
    body.update(kw)
    return body


class Watch(Base):
    def setUp(self):
        super().setUp()
        listen.write_state("current.json", {"id": "K7QM"})

    def test_lost_since_resumes_from_saved_cursor_not_from_the_start(self):
        listen.write_state("cursor-K7QM.json", {"since": 41})
        relay, out, _ = self.watch_json(body=watch_body(last=44, entries=[
            {"n": 42, "kind": "turn", "t": 61, "spk": 1, "text": "Only medical."}]))
        self.assertEqual(relay.calls[0]["params"]["since"], "41")
        self.assertEqual(out["new"], ["[01:01 S1] Only medical."])
        self.assertEqual(listen.read_state("cursor-K7QM.json"), {"since": 44})

    def test_failed_watch_keeps_the_cursor_so_nothing_is_skipped(self):
        listen.write_state("cursor-K7QM.json", {"since": 41})
        _, out, _ = self.watch_json(body={"error": "boom"}, status=0)
        self.assertEqual(out["event"], "error")
        self.assertEqual(listen.read_state("cursor-K7QM.json"), {"since": 41})

    def test_explicit_since_overrides_the_cursor(self):
        listen.write_state("cursor-K7QM.json", {"since": 41})
        relay, _, _ = self.watch_json(argv=("watch", "--since", "0"), body=watch_body())
        self.assertEqual(relay.calls[0]["params"]["since"], "0")

    def test_half_spoken_sentence_is_marked_as_unfinished(self):
        _, out, _ = self.watch_json(body=watch_body(tail={"spk": 0, "text": "so what I'm saying"}))
        self.assertEqual(out["tail"], "S0: so what I'm saying  [...still speaking]")

    def test_budget_rides_inside_the_bash_timeout(self):
        relay, _, _ = self.watch_json(body=watch_body())
        self.assertEqual(relay.calls[0]["params"]["wait"], "90")
        self.assertLess(listen.WATCH_BUDGET + 20, 120)

    def test_phone_off_screen_is_not_mistaken_for_a_quiet_room(self):
        dev = {"connected": True, "ui": "phone", "hidden_s": 16, "disconnected_s": None,
               "last_audio_s": 0, "transcriber": "open"}
        _, out, _ = self.watch_json(body=watch_body(event="idle", device=dev))
        self.assertIn("OFF SCREEN for 16s", out["device"])
        self.assertIn("mic is off", out["device"])

    def test_dead_device_and_silent_stream_are_named(self):
        gone = {"connected": False, "ui": "watch", "hidden_s": None, "disconnected_s": 40,
                "last_audio_s": 41, "transcriber": "closed"}
        self.assertEqual(listen.describe_device(gone, "live"), "watch DISCONNECTED for 40s: no audio")
        quiet = {"connected": True, "ui": "watch", "hidden_s": None, "disconnected_s": None,
                 "last_audio_s": 12, "transcriber": "open"}
        self.assertIn("no audio for 12s", listen.describe_device(quiet, "live"))
        self.assertIn("has not tapped START", listen.describe_device({"connected": False}, "armed"))

    def test_events_and_notes_render_distinctly_from_speech(self):
        _, out, _ = self.watch_json(body=watch_body(last=3, entries=[
            {"n": 1, "kind": "event", "t": 0, "text": "recording started"},
            {"n": 2, "kind": "note", "t": 30, "text": "ask about fees", "delivered": False},
            {"n": 3, "kind": "turn", "t": 3700, "spk": 0, "text": "Hi."}]))
        self.assertEqual(out["new"], [
            "[00:00 --] recording started",
            "[00:30 >>] note to device: ask about fees  (NOT delivered: no device connected)",
            "[1:01:40 S0] Hi."])

    def test_ended_session_says_to_wrap_up(self):
        _, out, _ = self.watch_json(body=watch_body(event="ended", state="ended",
                                                    end_reason="stopped on the device"))
        self.assertEqual(out["end_reason"], "stopped on the device")
        self.assertIn("wrap-up", out["hint"])
        # The device always disconnects at the end; that is not news.
        self.assertEqual(out["device"], "session ended")

    def test_backlog_flag_says_to_keep_going(self):
        _, out, _ = self.watch_json(body=watch_body(more=True))
        self.assertIn("backlog", out["more"])


class Note(Base):
    def setUp(self):
        super().setUp()
        listen.write_state("current.json", {"id": "K7QM"})

    def test_long_note_is_refused_before_it_reaches_the_relay(self):
        relay = Relay({})
        code, _, err = self.run_cli(["note"], relay, stdin=io.StringIO("x" * 121))
        self.assertEqual(code, 1)
        self.assertIn("120 at most", err)
        self.assertEqual(relay.calls, [])

    def test_note_as_an_argument_is_refused_because_the_shell_ate_it(self):
        code, _, err = self.run_cli(["note", "costs $50"], Relay({}), stdin=io.StringIO(""))
        self.assertEqual(code, 1)
        self.assertIn("heredoc", err)

    def test_undelivered_note_says_to_use_chat(self):
        relay = Relay({"/note": (200, {"ok": True, "delivered": False})})
        _, out, _ = self.run_cli(["note"], relay, stdin=io.StringIO("ask about\n the fee\n"))
        self.assertEqual(relay.calls[0]["body"], {"text": "ask about the fee"})
        self.assertIn("say it in chat", json.loads(out)["warning"])


class Start(Base):
    def test_second_start_is_refused_while_one_is_live(self):
        listen.write_state("current.json", {"id": "K7QM"})
        relay = Relay({"/status": (200, {"state": "live"})})
        code, _, err = self.run_cli(["start"], relay)
        self.assertEqual(code, 1)
        self.assertIn("still live", err)
        self.assertNotIn("/session", [c["path"] for c in relay.calls])

    def test_force_starts_anyway_and_resets_the_cursor(self):
        listen.write_state("current.json", {"id": "K7QM"})
        relay = Relay({"/status": (200, {"state": "live"}), "/session": (200, {"id": "ACDE"})})
        code, out, _ = self.run_cli(["start", "--force", "--label", "lawyer"], relay)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["id"], "ACDE")
        self.assertEqual(listen.read_state("current.json")["id"], "ACDE")
        self.assertEqual(listen.read_state("cursor-ACDE.json"), {"since": 0})
        self.assertIn("START ACDE", json.loads(out)["next"])

    def test_keyterms_merge_dedupe_and_split_on_commas(self):
        terms = Path(self.tmp.name) / "terms.txt"
        terms.write_text("trustee, Wooga\nbeneficiary\n\nTRUSTEE\n", encoding="utf-8")
        relay = Relay({"/session": (200, {"id": "ACDE"})})
        self.run_cli(["start", "--term", "Louie", "--terms-file", str(terms), "--archive"], relay)
        body = relay.calls[-1]["body"]
        self.assertEqual(body["keyterms"], ["Louie", "trustee", "Wooga", "beneficiary"])
        self.assertTrue(body["archive"])


class Health(Base):
    def test_token_mismatch_is_named(self):
        relay = Relay({"/health": (200, {"secrets": {"CLI_TOKEN": True}}), "/status": (401, {})})
        code, _, err = self.run_cli(["health"], relay)
        self.assertEqual(code, 1)
        self.assertIn("cli_token", err)

    def test_missing_secret_is_named(self):
        relay = Relay({"/health": (200, {"secrets": {"CLI_TOKEN": True, "DEEPGRAM_API_KEY": False}})})
        code, _, err = self.run_cli(["health"], relay)
        self.assertIn("DEEPGRAM_API_KEY", err)

    def test_unknown_session_404_means_the_token_works(self):
        relay = Relay({"/health": (200, {"secrets": {"CLI_TOKEN": True}}),
                       "/status": (404, {"error": "no such session"})})
        code, out, _ = self.run_cli(["health"], relay)
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["ok"])


class Pair(Base):
    def test_code_comes_with_where_to_type_it(self):
        relay = Relay({"/pair": (200, {"code": "482913", "expires_s": 300})})
        code, out, _ = self.run_cli(["pair"], relay)
        out = json.loads(out)
        self.assertEqual((code, out["code"], out["expires_in"]), (0, "482913", "5 minutes"))
        self.assertIn("https://relay.test", out["next"])

    def test_plain_bookmark_leaves_the_token_out_unless_asked(self):
        _, out, _ = self.run_cli(["bookmark"], Relay({}))
        self.assertNotIn("dev", json.loads(out)["bookmark"])
        self.assertNotIn("prepaired", json.loads(out))
        _, out, _ = self.run_cli(["bookmark", "--with-token"], Relay({}))
        self.assertTrue(json.loads(out)["prepaired"].endswith("#k=dev"))


class Plumbing(unittest.TestCase):
    def test_request_never_raises_on_a_dead_network(self):
        with mock.patch.object(listen.urllib.request, "urlopen",
                               side_effect=urllib.error.URLError("down")):
            status, body = listen.request("https://relay.test/health")
        self.assertEqual(status, 0)
        self.assertIn("down", body["error"])

    def test_no_session_yet_says_to_start(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(listen, "STATE", d):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()) as err:
                listen.current_id(type("A", (), {"id": None})())
        self.assertIn("listen start", err.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=1)
