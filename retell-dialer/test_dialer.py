#!/usr/bin/env python3
"""Offline tests for dialer. No network and no live call -- the Retell and
queue calls under test are stubbed in this process.

Nearly every case here is a regression for a bug that reached a real phone
call. They are named for the symptom the caller saw, not the function.

    python test_dialer.py
"""
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent

spec = importlib.util.spec_from_file_location("dialer", HERE / "bin" / "dialer.py")
dialer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dialer)


class Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class MonitorFrames(unittest.TestCase):
    """The live transcript arrived empty for every call until this was fixed:
    the field is `transcripts`, and an update is a delta keyed by turn id."""

    def merge(self, *frames):
        by_id = {}
        for f in frames:
            dialer.merge_turns(by_id, f)
        return list(by_id.values())

    def test_snapshot_populates(self):
        turns = self.merge({"type": "transcript_snapshot", "transcripts": [
            {"id": "agent_0", "role": "agent", "content": "Hello"},
            {"id": "user_0", "role": "user", "content": "Yes?"}]})
        self.assertEqual([t["content"] for t in turns], ["Hello", "Yes?"])

    def test_update_grows_a_turn_in_place(self):
        # The symptom this prevents: appending emitted the same sentence once
        # per word, because an utterance is streamed as it is spoken.
        turns = self.merge(
            {"type": "transcript_snapshot", "transcripts": [
                {"id": "agent_0", "role": "agent", "content": "I'll need"}]},
            {"type": "transcript_updated", "transcripts": [
                {"id": "agent_0", "role": "agent", "content": "I'll need to"}]},
            {"type": "transcript_updated", "transcripts": [
                {"id": "agent_0", "role": "agent", "content": "I'll need to check"}]})
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["content"], "I'll need to check")

    def test_update_appends_a_new_turn(self):
        turns = self.merge(
            {"type": "transcript_snapshot", "transcripts": [
                {"id": "agent_0", "role": "agent", "content": "Hello"}]},
            {"type": "transcript_updated", "transcripts": [
                {"id": "user_0", "role": "user", "content": "Hi"}]})
        self.assertEqual([t["id"] for t in turns], ["agent_0", "user_0"])

    def test_snapshot_resets_but_update_does_not(self):
        by_id = {}
        dialer.merge_turns(by_id, {"type": "transcript_snapshot", "transcripts": [
            {"id": "a", "content": "one"}]})
        dialer.merge_turns(by_id, {"type": "transcript_updated", "transcripts": [
            {"id": "b", "content": "two"}]})
        self.assertEqual(len(by_id), 2)
        dialer.merge_turns(by_id, {"type": "transcript_snapshot", "transcripts": [
            {"id": "c", "content": "three"}]})
        self.assertEqual([t["content"] for t in by_id.values()], ["three"])

    def test_non_transcript_frames_are_ignored(self):
        by_id = {}
        for frame in ({"type": "call_ended"}, {"type": "metadata", "transcripts": None}):
            self.assertFalse(dialer.merge_turns(by_id, frame))
        self.assertEqual(by_id, {})


class RenderTurns(unittest.TestCase):
    def test_live_marks_only_the_last_line(self):
        lines = dialer.render_turns(
            [{"role": "agent", "content": "Hello"},
             {"role": "user", "content": "And what is the last 4 of his"}], live=True)
        self.assertNotIn(dialer.IN_PROGRESS, lines[0])
        self.assertTrue(lines[-1].endswith(dialer.IN_PROGRESS))

    def test_finished_transcript_is_not_marked(self):
        lines = dialer.render_turns([{"role": "agent", "content": "Bye"}])
        self.assertNotIn(dialer.IN_PROGRESS, lines[0])

    def test_live_with_no_turns_does_not_crash(self):
        self.assertEqual(dialer.render_turns([], live=True), [])

    def test_tool_calls_are_visible(self):
        lines = dialer.render_turns([
            {"role": "tool_call_invocation", "name": "press_digit",
             "arguments": '{"digit_to_press": "3"}'},
            {"role": "tool_call_result", "content": "ok"}])
        self.assertIn("press_digit", lines[0])
        self.assertIn("ok", lines[1])

    def test_word_timings_are_dropped(self):
        lines = dialer.render_turns(
            [{"role": "agent", "content": "Hi", "words": [{"word": "Hi"} for _ in range(50)]}])
        self.assertEqual(lines, ["agent: Hi"])


class LiveView(unittest.TestCase):
    """A cursor that counted the half-spoken last turn skipped the rest of it:
    "agent: It is." was reported cut off, and the next watch started after it."""

    def turns(self, *contents):
        return [{"role": "agent", "content": c} for c in contents]

    def test_in_progress_turn_is_not_counted_as_seen(self):
        view = dialer.live_view(self.turns("a", "b", "It is."), since=1)
        self.assertEqual(view["turns"], "2 + 1 in progress")
        self.assertIn("--since 2", view["hint"])

    def test_resuming_shows_that_turn_finished(self):
        first = dialer.live_view(self.turns("a", "It is."), since=0)
        resume = int(first["hint"].split("--since ")[1].split("`")[0])
        later = dialer.live_view(
            self.turns("a", "It is. How's the audio?", "Fine"), since=resume)
        self.assertEqual(later["new"][0], "agent: It is. How's the audio?")

    def test_nothing_past_the_cursor(self):
        view = dialer.live_view(self.turns("a", "b"), since=2)
        self.assertEqual((view["turns"], view["new"]), ("2", []))
        self.assertIn("--since 2", view["hint"])


class CheckVars(unittest.TestCase):
    """An unset variable renders as a literal `{{brief}}` -- a live call with
    no instructions at all."""

    def test_empty_and_whitespace_are_refused(self):
        for bad in ("", "   ", None):
            with self.assertRaises(SystemExit):
                dialer.check_vars({"opening": "hi", "call_purpose": "x", "brief": bad})

    def test_complete_set_passes(self):
        dialer.check_vars({"opening": "hi", "brief": "b", "call_purpose": "p"})


class QueueUrl(unittest.TestCase):
    """A hand-built query string once produced `/poll&wait=20?token=...`."""

    def setUp(self):
        self.seen = {}
        self.orig = dialer.request
        dialer.request = lambda url, **kw: (self.seen.update(url=url, **kw), (200, {}))[1]

    def tearDown(self):
        dialer.request = self.orig

    def test_token_and_params_are_encoded_once(self):
        dialer.queue({"worker_url": "https://q.example", "consult_token": "t k/&"},
                     "/poll", params={"wait": 20})
        url = self.seen["url"]
        self.assertEqual(url.count("?"), 1)
        self.assertIn("token=t+k%2F%26", url)
        self.assertIn("wait=20", url)


class CallRecord(unittest.TestCase):
    """Scrubbing hides the unredacted keys entirely rather than blanking them,
    so the preference order decides what the supervisor reads back."""

    def stub(self, payload):
        dialer.retell = lambda cfg, path, **kw: (200, payload)

    def setUp(self):
        self.orig = dialer.retell

    def tearDown(self):
        dialer.retell = self.orig

    def test_prefers_unscrubbed_with_tool_calls(self):
        self.stub({"transcript_with_tool_calls": [{"role": "agent", "content": "a"}],
                   "scrubbed_transcript_with_tool_calls": [{"role": "agent", "content": "b"}],
                   "transcript": "c"})
        self.assertEqual(dialer.call_record({}, "id")["source"], "transcript_with_tool_calls")

    def test_falls_back_to_scrubbed(self):
        self.stub({"scrubbed_transcript_with_tool_calls": [{"role": "agent", "content": "b"}]})
        self.assertEqual(dialer.call_record({}, "id")["source"],
                         "scrubbed_transcript_with_tool_calls")

    def test_nothing_retained_returns_none(self):
        self.stub({"call_status": "ended"})
        self.assertIsNone(dialer.call_record({}, "id", settle=0))


class EndedResult(unittest.TestCase):
    """A take-over ends the agent's leg while Louie stays on the phone, and
    transcription stops there -- reporting it as a hangup would present a
    fragment as the outcome."""

    def setUp(self):
        self.orig = dialer.call_record

    def tearDown(self):
        dialer.call_record = self.orig

    def stub(self, reason):
        dialer.call_record = lambda cfg, cid, settle=0: {
            "source": "transcript_with_tool_calls", "call_status": "ended",
            "duration_ms": 1000, "disconnection_reason": reason,
            "content": [{"role": "agent", "content": "hi"}]}

    def test_hangup_is_an_ended_call(self):
        self.stub("agent_hangup")
        out = dialer.ended_result({}, "id", "ended")
        self.assertTrue(out["call_ended"])
        self.assertNotIn("event", out)

    def test_take_over_is_not(self):
        self.stub("call_take_over")
        out = dialer.ended_result({}, "id", "ended")
        self.assertFalse(out["call_ended"])
        self.assertEqual(out["event"], "taken_over")
        self.assertIn("still on the call", out["note"])

    def test_missing_transcript_is_reported_not_invented(self):
        dialer.call_record = lambda cfg, cid, settle=0: None
        out = dialer.ended_result({}, "id", "ended")
        self.assertIsNone(out["transcript"])


class Journal(unittest.TestCase):
    """The only record that survives claude.ai dropping an interrupted tool
    call, so the intent must be on disk before the request goes out."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig = dialer.JOURNAL
        dialer.JOURNAL = os.path.join(self.tmp, "state", "journal.jsonl")

    def tearDown(self):
        dialer.JOURNAL = self.orig

    def test_entries_round_trip_in_order(self):
        dialer.journal("answer", call_id="c1", text="first")
        dialer.journal("steer", call_id="c1", text="second")
        with open(dialer.JOURNAL, encoding="utf-8") as fh:
            rows = [json.loads(l) for l in fh if l.strip()]
        self.assertEqual([r["command"] for r in rows], ["answer", "steer"])
        self.assertEqual(rows[0]["text"], "first")
        self.assertIn("at", rows[0])

    def test_intent_survives_without_an_outcome(self):
        # What an interrupted turn leaves behind: the attempt, never the result.
        dialer.journal("dispatch", to="+15550123")
        with open(dialer.JOURNAL, encoding="utf-8") as fh:
            rows = [json.loads(l) for l in fh if l.strip()]
        self.assertEqual(len(rows), 1)
        self.assertNotIn("status", rows[0])

    def test_unwritable_path_does_not_break_a_live_call(self):
        dialer.JOURNAL = os.path.join(self.tmp, "nul\0bad", "journal.jsonl")
        dialer.journal("answer", call_id="c1")  # must not raise

    def test_reader_handles_a_missing_file(self):
        dialer.JOURNAL = os.path.join(self.tmp, "absent.jsonl")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            dialer.cmd_journal({}, Args(limit=10))
        self.assertIn("nothing recorded yet", out.getvalue())


class ResumeAfterActing(unittest.TestCase):
    """Acting on a call is a bet on how the agent will use what you sent, so
    both commands resume watching -- at a tighter interval than an idle watch,
    because this is the stretch you most need to see."""

    def setUp(self):
        self.saved = {n: getattr(dialer, n)
                      for n in ("load_config", "queue", "retell", "watch_loop", "journal")}
        self.seen = {}
        dialer.load_config = lambda: {}
        dialer.journal = lambda *a, **kw: None
        dialer.queue = lambda cfg, path, **kw: (200, {"ok": True})
        dialer.retell = lambda cfg, path, **kw: (200, {"ok": True})
        dialer.watch_loop = lambda cfg, call_id, budget, interval, since=0: (
            self.seen.update(call_id=call_id, budget=budget,
                             interval=interval, since=since),
            {"event": "idle"})[1]

    def tearDown(self):
        for name, fn in self.saved.items():
            setattr(dialer, name, fn)

    def run_cli(self, *argv):
        old = sys.argv
        sys.argv = ["dialer", *argv]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                with contextlib.redirect_stderr(io.StringIO()):
                    dialer.main()
        finally:
            sys.argv = old

    def test_answer_resumes_watching(self):
        self.run_cli("answer", "call_1:qab12", "go ahead")
        self.assertEqual(self.seen["call_id"], "call_1")
        self.assertEqual(self.seen["interval"], dialer.FOLLOW_INTERVAL)

    def test_answer_sends_the_consult_id(self):
        # The agent can ask a second question while the first is waiting; an
        # answer addressed only to the call could land on either.
        sent = {}
        dialer.queue = lambda cfg, path, **kw: (sent.update(kw.get("body") or {}),
                                                (200, {"ok": True}))[1]
        self.run_cli("answer", "call_1:qab12", "go ahead")
        self.assertEqual(sent["consult_id"], "call_1:qab12")

    def test_answer_refuses_a_bare_call_id(self):
        with self.assertRaises(SystemExit):
            self.run_cli("answer", "call_1", "go ahead")
        self.assertEqual(self.seen, {})

    def test_a_settled_consult_does_not_resume(self):
        dialer.queue = lambda cfg, path, **kw: (404, {"prior": {"outcome": "timed_out"}})
        with self.assertRaises(SystemExit):
            self.run_cli("answer", "call_1:qab12", "too late")
        self.assertEqual(self.seen, {})

    def test_steer_resumes_watching_too(self):
        self.run_cli("steer", "call_1", "he is free after 5")
        self.assertEqual(self.seen["call_id"], "call_1")
        self.assertEqual(self.seen["interval"], dialer.FOLLOW_INTERVAL)

    def test_both_accept_the_same_overrides(self):
        for cmd, target, text in (("answer", "call_1:qab12", "yes"),
                                  ("steer", "call_1", "context")):
            self.seen.clear()
            self.run_cli(cmd, target, text, "--interval", "5",
                         "--budget", "60", "--since", "7")
            self.assertEqual((self.seen["interval"], self.seen["budget"],
                              self.seen["since"]), (5, 60, 7))

    def test_a_failed_steer_does_not_resume(self):
        dialer.retell = lambda cfg, path, **kw: (400, {"error": "call ended"})
        with self.assertRaises(SystemExit):
            self.run_cli("steer", "call_1", "too late")
        self.assertEqual(self.seen, {})

    def test_an_idle_watch_stays_slower(self):
        self.assertGreater(dialer.WATCH_INTERVAL, dialer.FOLLOW_INTERVAL)


if __name__ == "__main__":
    unittest.main(verbosity=2)
