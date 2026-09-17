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
import time
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent

spec = importlib.util.spec_from_file_location("dialer", HERE / "bin" / "dialer.py")
dialer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dialer)


class Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


@contextlib.contextmanager
def watch_stubs(queue, monitor):
    """watch_loop with the queue, the monitor socket and call status stubbed."""
    fake_ws = types.ModuleType("ws")
    fake_ws.monitor = monitor
    fake_ws.WSError = type("WSError", (Exception,), {})
    saved = sys.modules.get("ws"), dialer.queue, dialer.call_status
    sys.modules["ws"], dialer.queue = fake_ws, queue
    dialer.call_status = lambda cfg, cid: "ongoing"
    try:
        yield
    finally:
        if saved[0] is None:
            sys.modules.pop("ws", None)
        else:
            sys.modules["ws"] = saved[0]
        dialer.queue, dialer.call_status = saved[1], saved[2]


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

    def test_keypad_press_shows_its_digit(self):
        # A dtmf item carries `digit`, not `content`.
        self.assertEqual(dialer.render_turns([{"role": "dtmf", "digit": "3"}]),
                         ["  [KEYPAD] 3"])

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


class ReplyAfterAConsult(unittest.TestCase):
    """Every consult lost the agent's reply. The holding phrase and what it says
    once the answer lands share one turn id, so the reply grew in place behind
    the tool entries, where a cursor already past them never looked."""

    TOOL = [{"role": "tool_call_invocation", "name": "consult_supervisor", "arguments": "{}"},
            {"role": "tool_call_result", "content": "Decline the 24 months."}]

    def turns(self, reply, *after):
        return [{"role": "user", "content": "$50 on 24 months?"},
                {"role": "agent", "content": reply}, *self.TOOL, *after]

    def test_cursor_waits_at_the_holding_phrase(self):
        view = dialer.live_view(self.turns("Let me check on that"), since=0)
        self.assertIn("--since 1", view["hint"])
        self.assertTrue(view["new"][1].endswith(dialer.IN_PROGRESS))
        self.assertNotIn(dialer.IN_PROGRESS, view["new"][-1])

    def test_resuming_shows_the_reply(self):
        reply = "Let me check on that one. 24 months is a no-go."
        later = dialer.live_view(self.turns(reply, {"role": "user", "content": "Well"}), since=1)
        self.assertEqual(later["new"][0], f"agent: {reply}")

    def watch(self, turns):
        frame = json.dumps({"type": "transcript_snapshot",
                            "transcripts": [{"id": str(i), **t} for i, t in enumerate(turns)]})

        class Socket:
            close_code = None
            frames = [frame]

            def recv(self, timeout):
                if self.frames:
                    return self.frames.pop()
                time.sleep(0.05)
                return None

            def close(self):
                pass

        queue = lambda cfg, path, **kw: (200, {"pending": None})
        with watch_stubs(queue, lambda *a, **kw: Socket()):
            return dialer.watch_loop({"retell_api_key": "k"}, "call_a",
                                     budget=1, interval=0, since=1)

    def test_watch_does_not_return_mid_reply(self):
        self.assertEqual(self.watch(self.turns("Let me check on that"))["event"], "idle")

    def test_watch_returns_once_someone_speaks_after_it(self):
        out = self.watch(self.turns("Let me check on that one.", {"role": "user", "content": "Ok"}))
        self.assertEqual(out["event"], "transcript")
        self.assertEqual(out["new"][0], "agent: Let me check on that one.")


class DispatchGuard(unittest.TestCase):
    """dispatch refuses while any call is live, because an interrupted turn can
    hide a dispatch that happened. But the live call may be another
    conversation's, so the refusal must show enough to tell them apart."""

    def setUp(self):
        self.saved = {n: getattr(dialer, n) for n in ("retell", "journal", "create_call")}
        dialer.journal = lambda *a, **kw: None
        self.created = []
        dialer.create_call = lambda cfg, variables, **kw: (
            self.created.append(kw), (201, {"call_id": "call_new", "call_status": "registered"}))[1]

    def tearDown(self):
        for name, fn in self.saved.items():
            setattr(dialer, name, fn)

    def live(self, *calls, status=200):
        dialer.retell = lambda cfg, path, **kw: (status, {"items": list(calls)})

    def dispatch(self, force=False):
        args = Args(to="+15550100", force=force, opening="hi", purpose="p",
                    brief="the brief", brief_file=None)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                dialer.cmd_dispatch({"from_number": "+15550199"}, args)
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue()

    def call(self, call_id, status, brief):
        return {"call_id": call_id, "call_status": status, "to_number": "+15550142",
                "start_timestamp": 1_700_000_000_000,
                "retell_llm_dynamic_variables": {"brief": brief, "call_purpose": "reschedule",
                                                 "opening": "Hello"}}

    def test_refusal_shows_every_live_call_with_its_brief(self):
        # "registered" is a call not yet connected -- the likeliest shape of a
        # dispatch this conversation lost to an interruption.
        self.live(self.call("call_a", "ongoing", "dentist brief"),
                  self.call("call_b", "registered", "pharmacy brief"),
                  self.call("call_c", "ended", "old brief"))
        code, out = self.dispatch()
        self.assertNotEqual(code, 0)
        self.assertEqual(self.created, [])
        shown = json.loads(out)["live_calls"]
        self.assertEqual([c["call_id"] for c in shown], ["call_a", "call_b"])
        self.assertEqual(shown[0]["brief"], "dentist brief")
        self.assertEqual(shown[0]["to"], "+15550142")

    def test_nothing_live_dials(self):
        self.live(self.call("call_c", "ended", "old brief"))
        code, _ = self.dispatch()
        self.assertEqual((code, len(self.created)), (0, 1))

    def test_force_dials_alongside_another_conversations_call(self):
        self.live(self.call("call_a", "ongoing", "someone else's brief"))
        code, _ = self.dispatch(force=True)
        self.assertEqual((code, len(self.created)), (0, 1))

    def test_unlistable_calls_refuse_rather_than_dial_blind(self):
        self.live(status=500)
        code, _ = self.dispatch()
        self.assertNotEqual(code, 0)
        self.assertEqual(self.created, [])


class WatchIsolation(unittest.TestCase):
    """Two conversations can each be supervising a call. A watch has to ask the
    queue for its own call's consults, or it hands over the other one's."""

    def test_watch_polls_only_its_own_call(self):
        params_seen = []

        def queue(cfg, path, params=None, **kw):
            params_seen.append(params or {})
            if (params or {}).get("call_id") == "call_b":
                return 200, {"consult_id": "call_b:q1", "call_id": "call_b", "question": "B?"}
            return 200, {"pending": None}

        def no_socket(*a, **kw):
            raise OSError("no monitor socket in tests")

        with watch_stubs(queue, no_socket):
            out = dialer.watch_loop({"retell_api_key": "k"}, "call_a", budget=1, interval=30)
        self.assertEqual(out["event"], "idle")
        self.assertTrue(params_seen)
        self.assertTrue(all(p.get("call_id") == "call_a" for p in params_seen), params_seen)


class MonitorCloseCodes(unittest.TestCase):
    """Only 1000 means the call ended. Any other close used to be reported as a
    hangup, so a dashboard tab too many (4008) or a Retell hiccup (1011) ended
    supervision of a call still in progress."""

    def sockets(self, *connections):
        """One fake connection per argument: a close code, or a list of frames."""
        pending = list(connections)

        class Socket:
            def __init__(self, spec):
                self.frames = [] if isinstance(spec, int) else list(spec)
                self.code = spec if isinstance(spec, int) else None
                self.close_code = None

            def recv(self, timeout):
                if self.frames:
                    return self.frames.pop(0)
                if self.code is not None:
                    self.close_code = self.code
                    return None
                time.sleep(0.05)
                return None

            def close(self):
                pass

        return lambda *a, **kw: Socket(pending.pop(0) if pending else [])

    def watch(self, monitor):
        queue = lambda cfg, path, **kw: (200, {"pending": None})
        with watch_stubs(queue, monitor):
            return dialer.watch_loop({"retell_api_key": "k"}, "call_a", budget=3, interval=0)

    def test_too_many_watchers_is_not_a_hangup(self):
        out = self.watch(self.sockets(4008))
        self.assertEqual(out["event"], "idle")
        self.assertIn("4008", out["monitor"])

    def test_internal_error_reconnects(self):
        snapshot = json.dumps({"type": "transcript_snapshot", "transcripts": [
            {"id": "agent_0", "role": "agent", "content": "Hello."},
            {"id": "user_0", "role": "user", "content": "Hi"}]})
        out = self.watch(self.sockets(1011, [snapshot]))
        self.assertEqual(out["event"], "transcript")
        self.assertEqual(out["new"][0], "agent: Hello.")


class WatchNotConnected(unittest.TestCase):
    """A call nobody answers ends as "not_connected", and watch used to block
    for its whole budget on one."""

    def setUp(self):
        self.orig = dialer.retell
        dialer.retell = lambda cfg, path, **kw: (
            200, {"call_status": "not_connected", "disconnection_reason": "dial_no_answer"})

    def tearDown(self):
        dialer.retell = self.orig

    def test_watch_returns_on_no_answer(self):
        def no_socket(*a, **kw):
            raise OSError("no monitor socket in tests")

        with watch_stubs(lambda cfg, path, **kw: (200, {"pending": None}), no_socket):
            dialer.call_status = lambda cfg, cid: "not_connected"
            start = time.monotonic()
            out = dialer.watch_loop({"retell_api_key": "k"}, "call_a", budget=30, interval=15)
        self.assertLess(time.monotonic() - start, 5)
        self.assertEqual(out["event"], "call_ended")
        self.assertEqual(out["disconnection_reason"], "dial_no_answer")
        self.assertIsNone(out["transcript"])

    def test_poll_returns_on_no_answer(self):
        saved = dialer.queue, dialer.call_status
        dialer.queue = lambda cfg, path, **kw: (200, {"pending": None})
        dialer.call_status = lambda cfg, cid: "not_connected"
        try:
            out = dialer.poll_loop({}, "call_a", budget=30)
        finally:
            dialer.queue, dialer.call_status = saved
        self.assertTrue(out["call_ended"])
        self.assertEqual(out["disconnection_reason"], "dial_no_answer")


class CheckVars(unittest.TestCase):
    """An unset variable renders as a literal `{{brief}}` -- a live call with
    no instructions at all."""

    def test_empty_and_whitespace_are_refused(self):
        for bad in ("", "   ", None):
            with self.assertRaises(SystemExit):
                dialer.check_vars({"opening": "hi", "call_purpose": "x", "brief": bad})

    def test_complete_set_passes(self):
        dialer.check_vars({"opening": "hi", "brief": "b", "call_purpose": "p"})


class GregorianCalendar(unittest.TestCase):
    """A weekday worked out in the head went to a real rep wrong."""

    def test_weekdays_match_the_real_calendar(self):
        lines = dialer.gregorian_calendar(start=(2026, 9), months=2).splitlines()
        self.assertTrue(lines[-2].startswith("Sep 2026: 1T 2W 3R 4F 5S 6U 7M "))
        self.assertTrue(lines[-2].endswith(" 30W"))
        self.assertTrue(lines[-1].startswith("Oct 2026: 1R 2F 3S 4U 5M 6T 7W "))
        self.assertTrue(lines[-1].endswith(" 31S"))

    def test_rolls_over_the_year(self):
        lines = dialer.gregorian_calendar(start=(2026, 12), months=2).splitlines()
        self.assertTrue(lines[-1].startswith("Jan 2027: 1F "))

    def test_every_call_carries_it_in_the_brief(self):
        sent = {}
        saved = dialer.retell
        dialer.retell = lambda cfg, path, **kw: (sent.update(kw["body"]), (201, {}))[1]
        variables = {"opening": "hi", "brief": "the brief", "call_purpose": "p"}
        try:
            dialer.create_call({"from_number": "+1", "agent_id": "a"}, variables, web=False, to="+2")
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                dialer.create_call({"agent_id": "a"}, {**variables, "brief": ""}, web=True)
        finally:
            dialer.retell = saved
        brief = sent["retell_llm_dynamic_variables"]["brief"]
        self.assertTrue(brief.startswith("the brief\n\n## Gregorian calendar"))
        self.assertIn(dialer.gregorian_calendar(), brief)

    def test_a_lookup_mid_call_sends_the_agent_a_copy(self):
        sent = []
        saved = dialer.retell, dialer.journal
        dialer.retell = lambda cfg, path, **kw: (sent.append((path, kw["body"])), (200, {}))[1]
        dialer.journal = lambda *a, **kw: None
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                dialer.cmd_gregorian_calendar({}, Args(start="2027-01", months=1, call_id=None))
                self.assertEqual(sent, [])
                dialer.cmd_gregorian_calendar({}, Args(start="2027-01", months=1, call_id="call_1"))
        finally:
            dialer.retell, dialer.journal = saved
        path, body = sent[0]
        self.assertEqual(path, "/v2/update-live-call/call_1")
        self.assertFalse(body["call_control"]["trigger_response"])
        self.assertIn(dialer.gregorian_calendar((2027, 1), 1),
                      body["call_control"]["additional_context"])
        self.assertIn("do not pass it on", out.getvalue())


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
    both commands resume watching."""

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

    def run_cli(self, *argv, stdin="he is free after 5\n"):
        old = sys.argv, sys.stdin
        sys.argv, sys.stdin = ["dialer", *argv], io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                with contextlib.redirect_stderr(io.StringIO()):
                    dialer.main()
        finally:
            sys.argv, sys.stdin = old

    def test_answer_resumes_watching(self):
        self.run_cli("answer", "call_1:qab12")
        self.assertEqual(self.seen["call_id"], "call_1")
        self.assertEqual(self.seen["interval"], dialer.WATCH_INTERVAL)

    def test_answer_sends_the_consult_id(self):
        # The agent can ask a second question while the first is waiting; an
        # answer addressed only to the call could land on either.
        sent = {}
        dialer.queue = lambda cfg, path, **kw: (sent.update(kw.get("body") or {}),
                                                (200, {"ok": True}))[1]
        self.run_cli("answer", "call_1:qab12")
        self.assertEqual(sent["consult_id"], "call_1:qab12")

    def test_answer_refuses_a_bare_call_id(self):
        with self.assertRaises(SystemExit):
            self.run_cli("answer", "call_1")
        self.assertEqual(self.seen, {})

    def test_a_settled_consult_does_not_resume(self):
        dialer.queue = lambda cfg, path, **kw: (404, {"prior": {"outcome": "timed_out"}})
        with self.assertRaises(SystemExit):
            self.run_cli("answer", "call_1:qab12")
        self.assertEqual(self.seen, {})

    def test_steer_resumes_watching_too(self):
        self.run_cli("steer", "call_1")
        self.assertEqual(self.seen["call_id"], "call_1")
        self.assertEqual(self.seen["interval"], dialer.WATCH_INTERVAL)

    def test_both_accept_the_same_overrides(self):
        for cmd, target in (("answer", "call_1:qab12"), ("steer", "call_1")):
            self.seen.clear()
            self.run_cli(cmd, target, "--interval", "5",
                         "--budget", "60", "--since", "7")
            self.assertEqual((self.seen["interval"], self.seen["budget"],
                              self.seen["since"]), (5, 60, 7))

    def test_a_failed_steer_does_not_resume(self):
        dialer.retell = lambda cfg, path, **kw: (400, {"error": "call ended"})
        with self.assertRaises(SystemExit):
            self.run_cli("steer", "call_1")
        self.assertEqual(self.seen, {})

    def test_text_arrives_verbatim_from_stdin(self):
        # A "$50" passed as a double-quoted argument reached the queue as "0".
        sent = {}
        stub = lambda cfg, path, **kw: (sent.update(kw.get("body") or {}), (200, {"ok": True}))[1]
        dialer.queue = dialer.retell = stub
        literal = 'It\'s $50 -- `not` a "$(command)" \\n\n'
        self.run_cli("answer", "call_1:qab12", stdin=literal)
        self.assertEqual(sent["text"], literal.strip())
        self.run_cli("steer", "call_1", stdin=literal)
        self.assertEqual(sent["call_control"]["additional_context"], literal.strip())

    def test_text_as_an_argument_is_refused(self):
        for argv in (("answer", "call_1:qab12", "costs 0"), ("steer", "call_1", "costs 0")):
            with self.assertRaises(SystemExit):
                self.run_cli(*argv)
        self.assertEqual(self.seen, {})

    def test_empty_stdin_is_refused(self):
        for argv in (("answer", "call_1:qab12"), ("steer", "call_1")):
            with self.assertRaises(SystemExit):
                self.run_cli(*argv, stdin="  \n")
        self.assertEqual(self.seen, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
