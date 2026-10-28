#!/usr/bin/env python3
"""Offline tests for the intercom CLI. No network: the relay is stubbed in this
process. Named for the symptom Claude or Louie would see, not the function.

    python test_intercom.py
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
spec = importlib.util.spec_from_file_location("intercom", HERE / "bin" / "intercom.py")
intercom = importlib.util.module_from_spec(spec)
spec.loader.exec_module(intercom)

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
        self.patches = [mock.patch.object(intercom,"STATE", self.tmp.name),
                        mock.patch.object(intercom,"load_config", lambda: dict(CFG))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def run_cli(self, argv, relay, stdin=None):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with mock.patch.object(intercom,"request", relay), \
             mock.patch.object(sys, "argv", ["intercom", *argv]), \
             mock.patch.object(sys, "stdin", stdin), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                intercom.main()
            except SystemExit as e:
                code = e.code or 0
        return code, out.getvalue(), err.getvalue()

    def watch_json(self, argv=("watch",), body=None, status=200):
        relay = Relay({"/watch": (status, body)})
        code, out, err = self.run_cli(list(argv), relay)
        return relay, (json.loads(out) if out.strip() else None), err


def monitor_lines(out):
    """`follow`'s output as {"n", "text"}: `51 [07:20 S1] text`, or `[--] text` without an n."""
    lines = []
    for line in out.splitlines():
        if line.startswith("[--] "):
            lines.append({"text": line[5:]})
        else:
            n, _, rest = line.partition(" ")
            lines.append({"n": int(n), "text": rest.split("] ", 1)[1]})
    return lines


def watch_body(**kw):
    body = {"event": "transcript", "id": "K7QM", "state": "live", "elapsed_s": 75,
            "entries": [], "last": 0, "more": False, "tail": None,
            "device": {"screens": ["watch"], "audio": "watch", "hidden_s": None,
                       "last_audio_s": 0, "transcriber": "open"}}
    body.update(kw)
    return body


class Follow(Base):
    def setUp(self):
        super().setUp()
        intercom.write_state("current.json", {"id": "K7QM"})

    def follow(self, watches, mode="talk", argv=("follow",)):
        answers = iter(watches)
        relay = Relay({"/status": (200, {"mode": mode}), "/watch": lambda p, b: next(answers)})
        with mock.patch.object(intercom.time, "sleep", lambda s: None):
            code, out, _ = self.run_cli(list(argv), relay)
        return relay, monitor_lines(out)

    def test_entries_stream_one_line_each_until_the_end_without_claudes_own(self):
        relay, lines = self.follow([
            (200, watch_body(last=3, entries=[{"n": 2, "kind": "turn", "text": "Hi."},
                                              {"n": 3, "kind": "said", "text": "Hello."}])),
            (0, {"error": "TimeoutError"}),
            (200, watch_body(event="idle")),
            (200, watch_body(event="ended", last=4, entries=[{"n": 4, "kind": "event", "text": "session ended"}])),
        ], argv=("follow", "--since", "1"))
        self.assertEqual([l["n"] for l in lines], [2, 4])
        self.assertEqual([c["params"]["since"] for c in relay.calls if c["path"] == "/watch"],
                         ["1", "3", "3", "3"])
        self.assertEqual(relay.calls[1]["params"]["interval"], "0")

    def test_listen_mode_polls_often_so_the_gates_answers_land_fast(self):
        relay, _ = self.follow([(200, watch_body(event="ended"))], mode="listen")
        self.assertEqual((relay.calls[1]["params"]["interval"], relay.calls[1]["params"]["wait"]),
                         ("0", str(intercom.GATE_POLL_S)))

    def test_a_gone_session_stops_with_a_line_instead_of_retrying(self):
        _, lines = self.follow([(404, {"error": "no such session"})])
        self.assertEqual(lines, [{"text": "follow stopped: 404 no such session"}])

    def test_monitor_lines_are_compact_and_lead_with_the_entry_number(self):
        _, lines = self.follow([(200, watch_body(event="ended", last=2, entries=[
            {"n": 2, "kind": "turn", "t": 61, "spk": 1, "text": "Only medical."}]))])
        self.assertEqual(lines, [{"n": 2, "text": "Only medical."}])
        self.assertEqual(intercom.follow_line({"n": 2, "kind": "turn", "t": 61, "spk": 1, "text": "Only medical."}),
                         "2 [01:01 S1] Only medical.")

    def test_a_long_entry_wraps_onto_continuation_lines_monitor_keeps_whole(self):
        text = " ".join(f"word{i}" for i in range(200))
        lines = intercom.follow_line({"n": 7, "kind": "turn", "t": 5, "text": text}).split("\n")
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(len(l) <= intercom.FOLLOW_WIDTH for l in lines))
        self.assertTrue(lines[0].startswith("7 [00:05 S0] word0 "))
        self.assertTrue(all(l.startswith("7+ ") for l in lines[1:]))
        rejoined = " ".join([lines[0].split("] ", 1)[1], *(l[3:] for l in lines[1:])])
        self.assertEqual(rejoined, text)

    def test_follow_never_moves_the_cursor_speak_defaults_to(self):
        intercom.write_state("cursor-K7QM.json", {"since": 1})
        self.follow([(200, watch_body(event="ended", last=9))])
        self.assertEqual(intercom.read_state("cursor-K7QM.json"), {"since": 1})


class Clock:
    """time.time and time.sleep for the gate's waits, so 30s pass instantly."""

    def __init__(self, now=1000.0):
        self.now = now

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s


def turn(n, text, at=None, t=None):
    e = {"n": n, "kind": "turn", "t": n * 5 if t is None else t, "spk": 0, "text": text}
    return e if at is None else {"at": at, **e}


class Schedule(unittest.TestCase):
    def test_plain_talk_waits_for_the_gate(self):
        self.assertFalse(intercom.Scheduler().add(turn(1, "So the fee is fifteen hundred."), 0))

    def test_naming_claude_device_news_and_wake_next_go_at_once(self):
        s = intercom.Scheduler()
        self.assertTrue(s.add(turn(1, "Claude, can you check that?"), 0))
        self.assertTrue(s.add({"n": 2, "kind": "event", "text": "mic silent"}, 0))
        s.next_turn = True
        self.assertTrue(s.add(turn(3, "and the second thing"), 0))
        self.assertFalse(s.add(turn(4, "and a third"), 0), "wake-next is for one turn only")

    def test_the_oldest_waiting_line_sets_the_ceiling(self):
        s = intercom.Scheduler()
        s.add(turn(1, "a"), 100)
        s.add(turn(2, "b"), 125)
        self.assertFalse(s.overdue(100 + intercom.CEILING_S - 1))
        self.assertTrue(s.overdue(100 + intercom.CEILING_S))
        self.assertEqual([e["n"] for e in s.take()], [1, 2])
        self.assertFalse(s.overdue(10_000))


class Gate(Base):
    def setUp(self):
        super().setUp()
        intercom.write_state("current.json", {"id": "K7QM"})
        self.clock = Clock()
        self.patches += [mock.patch.object(intercom.time, "time", self.clock.time),
                         mock.patch.object(intercom.time, "sleep", self.clock.sleep),
                         mock.patch.object(intercom, "main_transcript", lambda: None)]
        for p in self.patches[-3:]:
            p.start()

    def path(self, name):
        return intercom.gate_file("K7QM", name)

    def spool(self, *entries):
        with open(self.path("spool.jsonl"), "a", encoding="utf-8") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")

    def follow(self, steps):
        """Each step runs at the next poll, then answers it; the run ends with the session."""
        steps = iter(steps)

        def watch(params, body):
            out = next(steps)()
            self.clock.now += intercom.GATE_POLL_S
            return out
        relay = Relay({"/status": (200, {"mode": "listen"}), "/watch": watch})
        code, out, _ = self.run_cli(["follow"], relay)
        return monitor_lines(out)

    def delivered(self):
        return intercom.read_json(self.path("delivered.json"))["upto"]

    def test_plain_talk_is_spooled_and_held_until_the_gate_says_yes(self):
        seen = {}

        def gate_says_yes():
            seen["before"] = self.delivered()
            intercom.write_json(self.path("wake"), {"upto": 1})
            return 200, watch_body(event="idle")

        def end():
            seen["after"] = self.delivered()
            return 200, watch_body(event="ended", last=2, entries=[{"n": 2, "kind": "event", "text": "session ended"}])
        lines = self.follow([lambda: (200, watch_body(last=1, entries=[turn(1, "The fee is standard.")])),
                             gate_says_yes, end])
        self.assertEqual(seen, {"before": 0, "after": 1})
        self.assertEqual([l["n"] for l in lines], [1, 2])
        spooled = intercom.read_spool("K7QM")
        self.assertEqual([e.get("n") for e in spooled], [1, 2, None])
        self.assertEqual(spooled[-1]["kind"], "end")

    def test_nothing_waits_past_the_ceiling_even_with_no_gate(self):
        seen = {}

        def later():
            self.clock.now += intercom.CEILING_S
            return 200, watch_body(event="idle")

        def end():
            seen["delivered"] = self.delivered()
            return 200, watch_body(event="ended")
        lines = self.follow([lambda: (200, watch_body(last=1, entries=[turn(1, "Out the door it's 24,900.")])),
                             later, end])
        self.assertEqual(seen["delivered"], 1)
        self.assertEqual(lines[0]["n"], 1)
        self.assertIn("gate agent is not running", lines[1]["text"])

    def test_a_polling_gate_raises_no_alarm(self):
        def later():
            intercom.write_json(self.path("gate.json"), {"polled_at": self.clock.now})
            self.clock.now += intercom.CEILING_S
            intercom.write_json(self.path("gate.json"), {"polled_at": self.clock.now})
            return 200, watch_body(event="idle")
        lines = self.follow([lambda: (200, watch_body(last=1, entries=[turn(1, "hm")])),
                             later, lambda: (200, watch_body(event="ended"))])
        self.assertEqual([l.get("n") for l in lines], [1])

    def test_wake_next_delivers_the_next_closed_turn(self):
        seen = {}

        def ask():
            intercom.write_json(self.path("wake-next"), {})
            return 200, watch_body(event="idle")

        def end():
            seen["delivered"] = self.delivered()
            return 200, watch_body(event="ended", last=1)
        self.follow([ask, lambda: (200, watch_body(last=1, entries=[turn(1, "so the point is")])), end])
        self.assertEqual(seen["delivered"], 1)

    def test_start_hands_over_the_whole_conversation_then_asks_about_the_newest(self):
        now = self.clock.now
        self.spool(turn(1, "Hi everyone.", at=now - 300), turn(2, "Claude, listen in.", at=now - 290),
                   turn(3, "So the reindeer is a filler.", at=now - 20))
        intercom.write_json(self.path("delivered.json"), {"upto": 2})
        intercom.write_json(self.path("gate.json"), {"instance": 4})
        with open(self.path("brief.txt"), "w", encoding="utf-8") as fh:
            fh.write("Alaska planning with Mum and Dad.")
        code, out, _ = self.run_cli(["gate", "start"], Relay({}))
        self.assertEqual(code, 0)
        self.assertIn("## Steering or collaborating", out)
        self.assertNotIn("{{", out)
        self.assertIn("Alaska planning with Mum and Dad.", out)
        handover = out.rpartition("<conversation_so_far>")[2].partition("</conversation_so_far>")[0]
        self.assertIn("Hi everyone.", handover)
        self.assertNotIn("reindeer", handover)
        self.assertNotIn("[delivered]", handover)
        self.assertTrue(out.rstrip().endswith(intercom.GATE_QUESTION))
        self.assertIn("[00:15 S0] So the reindeer is a filler.\n", out)
        g = intercom.read_json(self.path("gate.json"))
        self.assertEqual((g["instance"], g["cursor"], g["asked"]), (5, 3, True))

    def test_speech_is_gathered_before_the_gate_judges_it(self):
        self.spool(turn(1, "So", at=self.clock.now))
        intercom.write_json(self.path("delivered.json"), {"upto": 0})
        before = self.clock.now
        self.run_cli(["gate", "start"], Relay({}))
        self.assertGreaterEqual(self.clock.now - before, intercom.GATE_GATHER_S)

    def test_yes_wakes_the_main_agent_and_is_logged(self):
        intercom.write_json(self.path("gate.json"), {"instance": 1, "cursor": 3, "asked": True, "budget": 9999})
        _, out, _ = self.run_cli(["gate", "y"], Relay({}))
        self.assertEqual(intercom.read_json(self.path("wake")), {"upto": 3})
        answers = [json.loads(l) for l in Path(self.path("answers.jsonl")).read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(a["answer"], a["upto"]) for a in answers], [("y", 3)])
        self.assertIn("intercom gate wait", out)

    def test_no_is_logged_but_wakes_nobody(self):
        intercom.write_json(self.path("gate.json"), {"instance": 1, "cursor": 3, "asked": True, "budget": 9999})
        self.run_cli(["gate", "n"], Relay({}))
        self.assertFalse(os.path.exists(self.path("wake")))

    def test_what_the_rules_delivered_is_context_not_a_question(self):
        now = self.clock.now
        self.spool(turn(4, "Claude, check the fee.", at=now - 40), turn(5, "And the parking?", at=now - 15))
        intercom.write_json(self.path("delivered.json"), {"upto": 4})
        intercom.write_json(self.path("gate.json"), {"instance": 1, "cursor": 3, "asked": False, "budget": 9999})
        _, out, _ = self.run_cli(["gate", "wait"], Relay({}))
        self.assertIn("Claude, check the fee.  [delivered]", out)
        self.assertIn("And the parking?\n", out)
        self.assertEqual(out.count(intercom.GATE_QUESTION), 1)

    def test_a_gate_that_has_read_enough_is_told_to_hand_over(self):
        intercom.write_json(self.path("gate.json"), {"instance": 1, "cursor": 3, "asked": True, "budget": 0})
        _, out, _ = self.run_cli(["gate", "n"], Relay({}))
        self.assertTrue(out.startswith("RESPAWN"))

    def test_the_end_of_the_session_ends_the_gate(self):
        self.spool({"at": self.clock.now, "kind": "end"})
        intercom.write_json(self.path("gate.json"), {"instance": 1, "cursor": 3, "asked": False, "budget": 9999})
        _, out, _ = self.run_cli(["gate", "wait"], Relay({}))
        self.assertIn("ENDED", out)

    def test_answering_before_any_gate_started_says_to_start(self):
        code, _, err = self.run_cli(["gate", "y"], Relay({}))
        self.assertEqual(code, 1)
        self.assertIn("intercom gate start", err)

    def test_wake_next_and_brief_leave_files_for_follow_and_the_gate(self):
        code, _, _ = self.run_cli(["wake-next"], Relay({}))
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(self.path("wake-next")))
        self.run_cli(["brief"], Relay({}), stdin=io.StringIO("Steering: Louie at the dealer.\n"))
        self.assertEqual(Path(self.path("brief.txt")).read_text(encoding="utf-8"), "Steering: Louie at the dealer.")


class MainPosts(unittest.TestCase):
    def test_chat_text_and_edits_count_but_blank_turns_and_subagents_do_not(self):
        rows = [
            {"type": "assistant", "timestamp": "2026-10-25T07:41:31Z",
             "message": {"content": [{"type": "text", "text": "."}]}},
            {"type": "assistant", "timestamp": "2026-10-25T07:41:59Z",
             "message": {"content": [{"type": "text", "text": "The modern one is SpringHill."}]}},
            {"type": "assistant", "timestamp": "2026-10-25T07:42:17Z",
             "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "/x/plan.md"}},
                                     {"type": "tool_use", "name": "Bash",
                                      "input": {"command": "cfs edit /memory/trip/todo.md --rev 1 <<'EOF'"}}]}},
            {"type": "assistant", "isSidechain": True, "timestamp": "2026-10-25T07:43:00Z",
             "message": {"content": [{"type": "text", "text": "a subagent's notes"}]}},
            {"type": "user", "timestamp": "2026-10-25T07:44:00Z", "message": {"content": "hi"}},
        ]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "main.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(json.dumps(r) for r in rows) + "\n" + '{"type": "assist')
            posts, offset = intercom.main_posts(path, 0)
            self.assertEqual([p[1] for p in posts],
                             ["The modern one is SpringHill.", "updated plan.md", "updated todo.md"])
            self.assertEqual(intercom.main_posts(path, offset), ([], offset), "a half-written line waits")


class Watch(Base):
    def setUp(self):
        super().setUp()
        intercom.write_state("current.json", {"id": "K7QM"})

    def test_lost_since_resumes_from_saved_cursor_not_from_the_start(self):
        intercom.write_state("cursor-K7QM.json", {"since": 41})
        relay, out, _ = self.watch_json(body=watch_body(last=44, entries=[
            {"n": 42, "kind": "turn", "t": 61, "spk": 1, "text": "Only medical."}]))
        self.assertEqual(relay.calls[0]["params"]["since"], "41")
        self.assertEqual(out["new"], ["[01:01 S1] Only medical."])
        self.assertEqual(intercom.read_state("cursor-K7QM.json"), {"since": 44})

    def test_failed_watch_keeps_the_cursor_so_nothing_is_skipped(self):
        intercom.write_state("cursor-K7QM.json", {"since": 41})
        _, out, _ = self.watch_json(body={"error": "boom"}, status=0)
        self.assertEqual(out["event"], "error")
        self.assertEqual(intercom.read_state("cursor-K7QM.json"), {"since": 41})

    def test_explicit_since_overrides_the_cursor(self):
        intercom.write_state("cursor-K7QM.json", {"since": 41})
        relay, _, _ = self.watch_json(argv=("watch", "--since", "0"), body=watch_body())
        self.assertEqual(relay.calls[0]["params"]["since"], "0")

    def test_half_spoken_sentence_is_marked_as_unfinished(self):
        _, out, _ = self.watch_json(body=watch_body(tail={"spk": 0, "text": "so what I'm saying"}))
        self.assertEqual(out["tail"], "S0: so what I'm saying  [...still speaking]")

    def test_budget_rides_inside_the_bash_timeout(self):
        relay, _, _ = self.watch_json(body=watch_body())
        self.assertEqual(relay.calls[0]["params"]["wait"], "90")
        self.assertLess(intercom.WATCH_BUDGET + 20, 120)

    def test_phone_off_screen_is_not_mistaken_for_a_quiet_room(self):
        dev = {"screens": ["phone"], "audio": "phone", "hidden_s": 16, "last_audio_s": 0, "transcriber": "open"}
        _, out, _ = self.watch_json(body=watch_body(event="idle", device=dev))
        self.assertIn("OFF SCREEN for 16s", out["device"])
        self.assertIn("mic is off", out["device"])

    def test_the_pages_mic_reading_overrides_off_screen(self):
        dev = {"screens": ["phone"], "audio": "phone", "hidden_s": 16, "mic": "live", "mic_silent_s": None,
               "last_audio_s": 0, "transcriber": "open"}
        self.assertEqual(intercom.describe_device(dev, "live"),
                         "phone streaming, page off screen for 16s but its mic hears")
        silent = dict(dev, mic="silent", mic_silent_s=9)
        self.assertEqual(intercom.describe_device(silent, "live"),
                         "phone mic SILENT for 9s with its page off screen: nothing is being heard")
        self.assertIn("mic SILENT for 9s: nothing", intercom.describe_device(dict(silent, hidden_s=None), "live"))

    def test_every_way_of_having_no_audio_is_named(self):
        none = {"screens": ["watch"], "audio": None, "no_audio_s": 40, "transcriber": "closed"}
        self.assertIn("holding the audio DISCONNECTED 40s ago",
                      intercom.describe_device(dict(none, no_audio_why="dropped"), "live"))
        self.assertIn("LEFT AUDIO 40s ago (in the room on the watch)",
                      intercom.describe_device(dict(none, no_audio_why="left"), "live"))
        self.assertIn("hasn't turned audio on", intercom.describe_device(dict(none, no_audio_why="unpicked"), "live"))
        self.assertIn("NO SCREEN OPEN", intercom.describe_device(dict(none, screens=[]), "live"))
        self.assertIn("has not opened the room", intercom.describe_device({"screens": []}, "armed"))
        quiet = {"screens": ["watch"], "audio": "watch", "hidden_s": None, "last_audio_s": 12, "transcriber": "open"}
        self.assertIn("no audio for 12s", intercom.describe_device(quiet, "live"))

    def test_events_and_notes_render_distinctly_from_speech(self):
        _, out, _ = self.watch_json(body=watch_body(last=3, entries=[
            {"n": 1, "kind": "event", "t": 0, "text": "recording started"},
            {"n": 2, "kind": "note", "t": 30, "text": "ask about fees", "delivered": False},
            {"n": 3, "kind": "turn", "t": 3700, "spk": 0, "text": "Hi."}]))
        self.assertEqual(out["new"], [
            "[00:00 --] recording started",
            "[00:30 >>] note to device: ask about fees  (NOT delivered: no screen open)",
            "[1:01:40 S0] Hi."])

    def test_a_turn_carries_its_confidence_and_how_far_isolation_turned_it_down(self):
        turn = {"n": 4, "kind": "turn", "t": 5, "spk": 0, "text": "Last call."}
        self.assertEqual(intercom.render(dict(turn, conf=0.94, drop_db=41)), "[00:05 S0 c.94 -41dB] Last call.")
        self.assertEqual(intercom.render(dict(turn, conf=1.0, drop_db=-2)), "[00:05 S0 c1.00 +2dB] Last call.")
        self.assertEqual(intercom.render(dict(turn, conf=0.3)), "[00:05 S0 c.30] Last call.")

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
        intercom.write_state("current.json", {"id": "K7QM"})

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
        intercom.write_state("current.json", {"id": "K7QM"})
        relay = Relay({"/status": (200, {"state": "live"})})
        code, _, err = self.run_cli(["start"], relay)
        self.assertEqual(code, 1)
        self.assertIn("still live", err)
        self.assertNotIn("/session", [c["path"] for c in relay.calls])

    def test_force_starts_anyway_and_resets_the_cursor(self):
        intercom.write_state("current.json", {"id": "K7QM"})
        relay = Relay({"/status": (200, {"state": "live"}), "/session": (200, {"id": "ACDE"})})
        code, out, _ = self.run_cli(["start", "--force", "--label", "lawyer"], relay)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["id"], "ACDE")
        self.assertEqual(intercom.read_state("current.json")["id"], "ACDE")
        self.assertEqual(intercom.read_state("cursor-ACDE.json"), {"since": 0})
        self.assertIn("join ACDE", json.loads(out)["next"])

    def test_keyterms_merge_dedupe_and_split_on_commas(self):
        terms = Path(self.tmp.name) / "terms.txt"
        terms.write_text("trustee, Wooga\nbeneficiary\n\nTRUSTEE\n", encoding="utf-8")
        relay = Relay({"/session": (200, {"id": "ACDE"})})
        self.run_cli(["start", "--term", "Louie", "--terms-file", str(terms), "--archive"], relay)
        body = relay.calls[-1]["body"]
        self.assertEqual(body["keyterms"], ["Louie", "trustee", "Wooga", "beneficiary"])
        self.assertTrue(body["archive"])

    def start_with_default_terms(self, cfg, list_answer):
        relay = Relay({"/session": (200, {"id": "ACDE"}), "https://dl.test/terms.txt": list_answer})
        with mock.patch.object(intercom, "load_config", lambda: dict(cfg)):
            code, out, _ = self.run_cli(["start", "--term", "Louie", "--default-terms"], relay)
        self.assertEqual(code, 0)
        return relay.calls[-1]["body"]["keyterms"], json.loads(out)

    def test_default_terms_are_fetched_from_the_config_url_and_merged(self):
        cfg = {**CFG, "keyterms_url": "https://dl.test/terms.txt"}
        terms, out = self.start_with_default_terms(cfg, (200, "Felicia\nlouie\n\nFragomen, GMT\n"))
        self.assertEqual(terms, ["Louie", "Felicia", "Fragomen", "GMT"])
        self.assertNotIn("keyterms_warning", out)

    def test_an_unreachable_default_list_still_starts_the_session_and_says_so(self):
        cfg = {**CFG, "keyterms_url": "https://dl.test/terms.txt"}
        terms, out = self.start_with_default_terms(cfg, (0, {"error": "URLError: timed out"}))
        self.assertEqual(terms, ["Louie"])
        self.assertIn("timed out", out["keyterms_warning"])

    def test_default_terms_without_a_configured_url_still_starts_and_says_so(self):
        terms, out = self.start_with_default_terms(CFG, (200, "unused"))
        self.assertEqual(terms, ["Louie"])
        self.assertIn("keyterms_url", out["keyterms_warning"])


class Talk(Base):
    def setUp(self):
        super().setUp()
        intercom.write_state("current.json", {"id": "K7QM"})

    def test_talk_mode_reaches_the_relay_and_says_to_arm_monitor(self):
        intercom.write_state("current.json", {})
        relay = Relay({"/session": (200, {"id": "ACDE", "event_key": "ek"})})
        code, out, _ = self.run_cli(["start", "--mode", "talk"], relay)
        self.assertEqual((code, relay.calls[-1]["body"]["mode"]), (0, "talk"))
        self.assertIn("TALK", json.loads(out)["next"])
        self.assertIn("intercom follow", json.loads(out)["next"])

    def test_listen_is_the_default_mode(self):
        intercom.write_state("current.json", {})
        relay = Relay({"/session": (200, {"id": "ACDE"})})
        self.run_cli(["start"], relay)
        self.assertEqual(relay.calls[-1]["body"]["mode"], "listen")

    def test_talk_mode_isolates_unless_told_not_to_and_listen_never_does(self):
        intercom.write_state("current.json", {})
        relay = Relay({"/session": (200, {"id": "ACDE"})})
        self.run_cli(["start", "--mode", "talk"], relay)
        self.assertTrue(relay.calls[-1]["body"]["isolate"])
        self.run_cli(["start", "--mode", "talk", "--no-isolate", "--force"], relay)
        self.assertFalse(relay.calls[-1]["body"]["isolate"])
        self.run_cli(["start", "--force"], relay)
        self.assertFalse(relay.calls[-1]["body"]["isolate"])

    def test_turn_wait_reaches_the_relay_from_start_and_set(self):
        intercom.write_state("current.json", {})
        relay = Relay({"/session": (200, {"id": "ACDE"}), "/set": (200, {"ok": True, "hold_s": 5})})
        self.run_cli(["start", "--mode", "talk", "--hold", "4"], relay)
        self.assertEqual(relay.calls[-1]["body"]["hold_s"], 4)
        code, out, _ = self.run_cli(["set", "--hold", "5"], relay)
        self.assertEqual((code, relay.calls[-1]["body"], json.loads(out)["hold_s"]), (0, {"hold_s": 5}, 5))

    def test_call_sends_the_number_given_and_shows_a_refusal(self):
        relay = Relay({"/call": (200, {"ok": True, "state": "dialing"})})
        code, out, _ = self.run_cli(["call", "--to", "+14155550123"], relay)
        self.assertEqual((code, relay.calls[-1]["body"], json.loads(out)["call"]), (0, {"to": "+14155550123"}, "dialing"))
        relay = Relay({"/call": (400, {"ok": False, "error": "the relay has no phone line set up"})})
        code, _, err = self.run_cli(["call", "--to", "+14155550123"], relay)
        self.assertEqual(code, 1)
        self.assertIn("no phone line set up", err)

    def test_the_device_line_names_the_call(self):
        ringing = {"screens": [], "audio": None, "call": {"state": "ringing", "to": "…0123", "s": 4}}
        self.assertEqual(intercom.describe_device(ringing, "live"), "calling Louie's phone (…0123), ringing for 4s")
        up = dict(ringing, call={"state": "up", "to": "…0123", "s": 30})
        self.assertIn("its audio not started", intercom.describe_device(up, "live"))
        on_line = dict(up, audio="line", last_audio_s=0)
        self.assertEqual(intercom.describe_device(on_line, "live"), "on the phone line for 30s (…0123)")
        self.assertIn("no call audio for 9s", intercom.describe_device(dict(on_line, last_audio_s=9), "live"))
        ended = {"screens": ["watch"], "audio": None, "no_audio_why": "call ended", "no_audio_s": 3}
        self.assertIn("PHONE LINE ENDED 3s ago", intercom.describe_device(ended, "live"))

    def test_health_says_whether_the_phone_line_is_set_up(self):
        secrets = {"CLI_TOKEN": True}
        relay = Relay({"/health": (200, {"secrets": secrets, "phone_line": {"TELNYX_API_KEY": True,
                                                                             "TELNYX_FROM": False}}),
                       "/status": (404, {"error": "no such session"})})
        code, out, _ = self.run_cli(["health"], relay)
        self.assertEqual(code, 0)
        self.assertIn("missing TELNYX_FROM", json.loads(out)["phone_line"])
        self.assertIn("no KRISP_API_KEY", json.loads(out)["voice_isolation"])

    def test_speech_cut_by_the_audio_moving_says_to_say_the_rest_again(self):
        relay = Relay({"/speak": (200, {"status": "interrupted", "spoken": "Yes, I can",
                                        "unspoken": "see that.", "reason": "the audio moved to the phone"})})
        _, out, _ = self.run_cli(["speak"], relay, stdin=io.StringIO("Yes, I can see that."))
        self.assertIn("Say `unspoken` again now", json.loads(out)["hint"])

    def test_refused_turn_wait_shows_the_relays_reason(self):
        relay = Relay({"/set": (400, {"ok": False, "error": "the turn wait must be 0-10s"})})
        code, _, err = self.run_cli(["set", "--hold", "30"], relay)
        self.assertEqual(code, 1)
        self.assertIn("0-10s", err)

    def test_long_speech_goes_to_the_relay_whole(self):
        relay = Relay({"/speak": (200, {"status": "done", "spoken": "", "unspoken": ""})})
        self.run_cli(["speak"], relay, stdin=io.StringIO("word " * 300))
        self.assertEqual(relay.calls[-1]["body"]["text"], ("word " * 300).strip())

    def test_speech_as_an_argument_is_refused(self):
        code, _, err = self.run_cli(["speak", "it costs $50"], Relay({}), stdin=io.StringIO(""))
        self.assertEqual(code, 1)
        self.assertIn("intercom speak <<'EOF'", err)

    def test_listen_session_refusal_is_shown_as_is(self):
        relay = Relay({"/speak": (409, {"error": "this is a listen session: Claude has no voice in it"})})
        code, _, err = self.run_cli(["speak"], relay, stdin=io.StringIO("hello"))
        self.assertEqual(code, 1)
        self.assertIn("listen session", err)

    def test_interrupted_speech_says_what_was_heard_and_what_comes_next(self):
        relay = Relay({"/speak": (200, {"status": "interrupted", "spoken": "one two",
                                        "unspoken": "three", "heard_so_far": "hold on"})})
        _, out, _ = self.run_cli(["speak"], relay, stdin=io.StringIO("one two three"))
        out = json.loads(out)
        self.assertEqual(out["spoken"], "one two")
        self.assertIn("next event", out["hint"])

    def test_barge_in_switch_reaches_the_relay(self):
        relay = Relay({"/speak": (200, {"status": "done", "spoken": "hi", "unspoken": ""})})
        self.run_cli(["speak", "--no-barge-in"], relay, stdin=io.StringIO("hi"))
        self.assertIs(relay.calls[-1]["body"]["barge_in"], False)
        self.run_cli(["speak"], relay, stdin=io.StringIO("hi"))
        self.assertIs(relay.calls[-1]["body"]["barge_in"], True)

    def test_since_comes_from_the_flag_or_else_the_watch_cursor(self):
        relay = Relay({"/speak": (200, {"status": "done", "spoken": "hi", "unspoken": ""})})
        self.run_cli(["speak", "--since", "41"], relay, stdin=io.StringIO("hi"))
        self.assertEqual(relay.calls[-1]["body"]["since"], 41)
        intercom.write_state("cursor-K7QM.json", {"since": 12})
        self.run_cli(["speak"], relay, stdin=io.StringIO("hi"))
        self.assertEqual(relay.calls[-1]["body"]["since"], 12)

    def test_refused_speech_says_to_read_louies_turn_first(self):
        relay = Relay({"/speak": (200, {"status": "not_spoken", "turns": [{"n": 9, "text": "blue"}]})})
        _, out, _ = self.run_cli(["speak", "--since", "8"], relay, stdin=io.StringIO("red"))
        out = json.loads(out)
        self.assertEqual(out["turns"], [{"n": 9, "text": "blue"}])
        self.assertIn("nothing was played", out["hint"])

    def test_monitor_url_carries_the_event_key_not_the_cli_token(self):
        relay = Relay({"/status": (200, {"state": "live", "event_key": "ek123"})})
        code, out, _ = self.run_cli(["monitor-url", "--since", "41"], relay)
        url = out.strip()
        self.assertEqual(code, 0)
        self.assertTrue(url.startswith("wss://relay.test/events?"), url)
        self.assertIn("k=ek123", url)
        self.assertIn("since=41", url)
        self.assertNotIn("tok", url)

    def test_said_lines_render_as_claudes_voice(self):
        self.assertEqual(intercom.render({"kind": "said", "t": 5, "text": "one two", "interrupted": True}),
                         "[00:05 <<] said: one two  (interrupted)")


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
        with mock.patch.object(intercom.urllib.request, "urlopen",
                               side_effect=urllib.error.URLError("down")):
            status, body = intercom.request("https://relay.test/health")
        self.assertEqual(status, 0)
        self.assertIn("down", body["error"])

    def test_no_session_yet_says_to_start(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(intercom,"STATE", d):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()) as err:
                intercom.current_id(type("A", (), {"id": None})())
        self.assertIn("intercom start", err.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=1)
