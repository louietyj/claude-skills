#!/usr/bin/env python3
"""Offline tests for the logic that does not need Dropbox."""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin"))

import cfs  # noqa: E402


class TestNormalise(unittest.TestCase):
    def test_adds_leading_slash(self):
        self.assertEqual(cfs.normalise("memory/INDEX.md"), "/memory/INDEX.md")

    def test_collapses_redundant_separators(self):
        self.assertEqual(cfs.normalise("//memory///a.md"), "/memory/a.md")
        self.assertEqual(cfs.normalise("/memory/./a.md"), "/memory/a.md")

    def test_rejects_traversal(self):
        for bad in ("/memory/../../secrets", "/memory/..", "../x"):
            with self.assertRaises(cfs.CfsError):
                cfs.normalise(bad)

    def test_rejects_url_encoded_traversal(self):
        with self.assertRaises(cfs.CfsError):
            cfs.normalise("/memory/%2e%2e/secrets")

    def test_root_maps_to_empty_api_path(self):
        self.assertEqual(cfs.api_path("/"), "")
        self.assertEqual(cfs.api_path("/memory"), "/memory")


class TestApplyReplacement(unittest.TestCase):
    def test_unique_match_replaced(self):
        self.assertEqual(
            cfs.apply_replacement("alpha beta gamma", "beta", "delta", "/f"),
            "alpha delta gamma",
        )

    def test_no_match_raises(self):
        with self.assertRaises(cfs.CfsError) as ctx:
            cfs.apply_replacement("alpha", "zeta", "x", "/f")
        self.assertIn("did not appear verbatim", str(ctx.exception))

    def test_ambiguous_match_raises_with_line_numbers(self):
        text = "todo\nkeep\ntodo\n"
        with self.assertRaises(cfs.CfsError) as ctx:
            cfs.apply_replacement(text, "todo", "done", "/f")
        message = str(ctx.exception)
        self.assertIn("appears 2 times", message)
        self.assertIn("lines 1, 3", message)

    def test_multiline_old_str(self):
        text = "a\nb\nc\n"
        self.assertEqual(cfs.apply_replacement(text, "a\nb", "z", "/f"), "z\nc\n")

    def test_ambiguity_error_mentions_the_all_escape(self):
        with self.assertRaises(cfs.CfsError) as ctx:
            cfs.apply_replacement("x\nx\n", "x", "y", "/f")
        self.assertIn("--all", str(ctx.exception))

    def test_replace_all_replaces_every_occurrence(self):
        self.assertEqual(
            cfs.apply_replacement("x\nkeep\nx\n", "x", "y", "/f", replace_all=True),
            "y\nkeep\ny\n",
        )

    def test_replace_all_still_requires_a_match(self):
        with self.assertRaises(cfs.CfsError):
            cfs.apply_replacement("abc", "zzz", "y", "/f", replace_all=True)


class TestRetryPolicy(unittest.TestCase):
    def _exc(self, code, headers=None):
        return urllib.error.HTTPError(
            "https://x", code, "err", headers or {}, None
        )

    def test_write_contention_is_retried(self):
        body = '{"error": {".tag": "too_many_write_operations"}}'
        self.assertIsNotNone(cfs._retry_after(self._exc(429), body, 0))

    def test_rate_limit_is_retried(self):
        self.assertIsNotNone(cfs._retry_after(self._exc(429), "rate_limit", 0))

    def test_503_is_retried(self):
        self.assertIsNotNone(cfs._retry_after(self._exc(503), "", 0))

    def test_conflict_is_not_retried(self):
        body = '{"error": {".tag": "path", "reason": {".tag": "conflict"}}}'
        self.assertIsNone(cfs._retry_after(self._exc(409), body, 0))

    def test_auth_failure_is_not_retried(self):
        self.assertIsNone(cfs._retry_after(self._exc(401), "missing_scope", 0))

    def test_gives_up_after_max_retries(self):
        self.assertIsNone(
            cfs._retry_after(self._exc(429), "too_many_write_operations", cfs.MAX_RETRIES)
        )

    def test_honours_retry_after_header(self):
        exc = self._exc(429, {"Retry-After": "3"})
        self.assertEqual(cfs._retry_after(exc, "too_many_", 0), 3.0)

    def test_honours_retry_after_in_body(self):
        body = '{"error": {".tag": "too_many_write_operations"}, "retry_after": 7}'
        self.assertEqual(cfs._retry_after(self._exc(429), body, 0), 7.0)

    def test_caps_absurd_retry_after(self):
        exc = self._exc(429, {"Retry-After": "9999"})
        self.assertEqual(cfs._retry_after(exc, "too_many_", 0), 30.0)

    def test_backoff_grows_and_is_jittered(self):
        delays = [
            cfs._retry_after(self._exc(429), "too_many_", n) for n in range(4)
        ]
        self.assertTrue(all(d is not None for d in delays))
        # Jitter must make identical attempts diverge, or parallel callers
        # resynchronise and collide again on the same instant.
        repeats = {cfs._retry_after(self._exc(429), "too_many_", 2) for _ in range(20)}
        self.assertGreater(len(repeats), 1)


class TestProtectedRoots(unittest.TestCase):
    def test_memory_root_cannot_be_deleted(self):
        with self.assertRaises(cfs.CfsError) as ctx:
            cfs.guard_protected("/memory", "delete")
        self.assertIn("Refusing to delete", str(ctx.exception))

    def test_trailing_slash_still_protected(self):
        with self.assertRaises(cfs.CfsError):
            cfs.guard_protected("/memory/", "delete")

    def test_entries_inside_memory_are_not_protected(self):
        cfs.guard_protected("/memory/hawaii.md", "delete")
        cfs.guard_protected("/memory/tack/INDEX.md", "delete")

    def test_similarly_named_paths_are_not_protected(self):
        cfs.guard_protected("/memory-old", "rename")
        cfs.guard_protected("/memories", "delete")


class TestFormatting(unittest.TestCase):
    def test_numbered_is_one_indexed_and_tab_separated(self):
        self.assertEqual(cfs.numbered("x\ny"), "     1\tx\n     2\ty")

    def test_numbered_respects_start_offset(self):
        self.assertEqual(cfs.numbered("x", start=10), "    10\tx")

    def test_human_size(self):
        self.assertEqual(cfs.human_size(512), "512B")
        self.assertEqual(cfs.human_size(2048), "2.0K")
        self.assertEqual(cfs.human_size(5 * 1024 * 1024), "5.0M")


class TestReadPayload(unittest.TestCase):
    """stdin is replaced with a StringIO; isatty() is False on those, as in a pipe."""

    def _stdin(self, text):
        sys.stdin = io.StringIO(text)
        self.addCleanup(setattr, sys, "stdin", sys.__stdin__)

    def test_parses_object(self):
        self._stdin('{"old_str": "a", "new_str": "b"}')
        payload = cfs.read_payload(("old_str", "new_str"), "example")
        self.assertEqual(payload["old_str"], "a")

    def test_escaped_newlines_become_real_newlines(self):
        self._stdin('{"content": "one\\ntwo\\n"}')
        self.assertEqual(cfs.read_payload(("content",), "ex")["content"], "one\ntwo\n")

    def test_embedded_quotes_and_backslashes_survive(self):
        self._stdin(r'{"content": "say \"hi\" and \\ done"}')
        self.assertEqual(
            cfs.read_payload(("content",), "ex")["content"], 'say "hi" and \\ done'
        )

    def test_malformed_json_raises_clean_error(self):
        self._stdin('{"old_str": "unterminated}')
        with self.assertRaises(cfs.CfsError) as ctx:
            cfs.read_payload(("old_str",), "example")
        self.assertIn("Could not parse stdin as JSON", str(ctx.exception))

    def test_missing_key_raises(self):
        self._stdin('{"old_str": "a"}')
        with self.assertRaises(cfs.CfsError) as ctx:
            cfs.read_payload(("old_str", "new_str"), "example")
        self.assertIn("new_str", str(ctx.exception))

    def test_non_string_value_raises(self):
        self._stdin('{"content": 42}')
        with self.assertRaises(cfs.CfsError):
            cfs.read_payload(("content",), "example")

    def test_empty_stdin_raises(self):
        self._stdin("   ")
        with self.assertRaises(cfs.CfsError) as ctx:
            cfs.read_payload(("content",), "example")
        self.assertIn("Empty stdin", str(ctx.exception))

    def test_array_rejected(self):
        self._stdin("[1, 2]")
        with self.assertRaises(cfs.CfsError):
            cfs.read_payload(("content",), "example")


def pairs(blocks):
    return [(b.old, b.new) for b in blocks]


def block(old, new):
    return f"<<<<<<< SEARCH\n{old}\n=======\n{new}\n>>>>>>> REPLACE\n"


class TestSearchReplace(unittest.TestCase):
    def parse(self, text, tag=None):
        return cfs.parse_search_replace(text, tag)

    def only_error(self, raw):
        (parsed,) = self.parse(raw)
        self.assertIsNotNone(parsed.error)
        return parsed.error

    def test_single_block(self):
        self.assertEqual(pairs(self.parse(block("old", "new"))), [("old", "new")])

    def test_multiline_sides(self):
        raw = "<<<<<<< SEARCH\na\nb\n=======\nA\nB\n>>>>>>> REPLACE\n"
        self.assertEqual(pairs(self.parse(raw)), [("a\nb", "A\nB")])

    def test_several_blocks_parse_in_order(self):
        raw = block("a", "A") + block("b", "B")
        self.assertEqual(pairs(self.parse(raw)), [("a", "A"), ("b", "B")])

    def test_empty_replace_is_a_deletion(self):
        raw = "<<<<<<< SEARCH\ngone\n=======\n>>>>>>> REPLACE\n"
        self.assertEqual(pairs(self.parse(raw)), [("gone", "")])

    def test_content_with_shell_and_json_hostile_chars(self):
        raw = block('say "hi" $HOME `x` \\ done', "new")
        self.assertEqual(self.parse(raw)[0].old, 'say "hi" $HOME `x` \\ done')

    def test_setext_heading_underline_is_not_a_divider(self):
        # Markdown H1 underlines are '=' runs; only exactly seven counts.
        raw = "<<<<<<< SEARCH\nTitle\n=====\n=========\n=======\nnew\n>>>>>>> REPLACE\n"
        self.assertEqual(pairs(self.parse(raw)), [("Title\n=====\n=========", "new")])

    def test_repeated_divider_as_a_closer_is_rejected(self):
        # The exact mistake seen in the wild: closing the block by repeating
        # the separator instead of using the REPLACE marker.
        raw = "<<<<<<< SEARCH\nold\n=======\nnew\n=======\n>>>>>>> REPLACE\n"
        error = self.only_error(raw)
        self.assertIn("second", error)
        self.assertIn("do not repeat the divider", error)

    def test_missing_divider_is_rejected(self):
        self.assertIn("without a", self.only_error("<<<<<<< SEARCH\nold\n>>>>>>> REPLACE\n"))

    def test_unclosed_block_is_rejected(self):
        self.assertIn("never closed", self.only_error("<<<<<<< SEARCH\nold\n=======\nnew\n"))

    def test_nested_start_is_rejected(self):
        raw = "<<<<<<< SEARCH\na\n=======\nA\n<<<<<<< SEARCH\n"
        first = self.parse(raw)[0]
        self.assertIn("never closed", first.error)

    def test_a_bad_block_does_not_hide_the_ones_after_it(self):
        # Resynchronising on the next opening marker is what lets one report
        # cover every block, so the retry fixes them all at once.
        raw = (
            block("a", "A")
            + "<<<<<<< SEARCH\nb\n=======\nB\n=======\n"
            + block("c", "C")
        )
        parsed = self.parse(raw)
        self.assertEqual(len(parsed), 3)
        self.assertIsNone(parsed[0].error)
        self.assertIn("line 10", parsed[1].error)
        self.assertEqual((parsed[2].old, parsed[2].new, parsed[2].error), ("c", "C", None))

    def test_divider_closers_on_every_block_are_each_reported(self):
        raw = (
            "<<<<<<< SEARCH\na\n=======\nA\n=======\n"
            "<<<<<<< SEARCH\nb\n=======\nB\n=======\n"
        )
        parsed = self.parse(raw)
        self.assertEqual(len(parsed), 2)
        self.assertTrue(all("do not repeat the divider" in b.error for b in parsed))

    def test_a_missing_divider_does_not_swallow_the_next_block(self):
        raw = "<<<<<<< SEARCH\na\n" + block("b", "B")
        parsed = self.parse(raw)
        self.assertIn("opens another block", parsed[0].error)
        self.assertEqual(pairs(parsed[1:]), [("b", "B")])

    def test_no_block_at_all_is_rejected(self):
        with self.assertRaises(cfs.CfsError) as ctx:
            self.parse("just some text\n")
        self.assertIn("No SEARCH/REPLACE block", str(ctx.exception))


class TestSearchReplaceTag(unittest.TestCase):
    TAG = "@@X@@"

    def test_tagged_markers_parse(self):
        raw = (
            f"<<<<<<< SEARCH {self.TAG}\nold\n======= {self.TAG}\nnew\n"
            f">>>>>>> REPLACE {self.TAG}\n"
        )
        self.assertEqual(pairs(cfs.parse_search_replace(raw, self.TAG)), [("old", "new")])

    def test_untagged_markers_inside_content_are_literal(self):
        # The whole point: a file containing real conflict markers can still be
        # edited, because only the tagged lines are structural.
        raw = (
            f"<<<<<<< SEARCH {self.TAG}\n"
            "<<<<<<< HEAD\nmine\n=======\ntheirs\n>>>>>>> branch\n"
            f"======= {self.TAG}\nresolved\n>>>>>>> REPLACE {self.TAG}\n"
        )
        (parsed,) = cfs.parse_search_replace(raw, self.TAG)
        old, new = parsed.old, parsed.new
        self.assertEqual(old, "<<<<<<< HEAD\nmine\n=======\ntheirs\n>>>>>>> branch")
        self.assertEqual(new, "resolved")

    def test_tagged_mode_ignores_untagged_blocks_entirely(self):
        raw = "<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE\n"
        with self.assertRaises(cfs.CfsError):
            cfs.parse_search_replace(raw, self.TAG)


class TestApplyEdits(unittest.TestCase):
    TEXT = "alpha\nbeta\ngamma\n"

    def apply(self, edits, text=TEXT, **kwargs):
        return cfs.apply_edits(text, [cfs.EditBlock(o, n) for o, n in edits], "/f", **kwargs)

    def refusal(self, edits, **kwargs):
        with self.assertRaises(cfs.CfsError) as ctx:
            self.apply(edits, **kwargs)
        return str(ctx.exception)

    def test_every_block_applies(self):
        self.assertEqual(
            self.apply([("alpha", "ONE"), ("gamma", "THREE")]), "ONE\nbeta\nTHREE\n"
        )

    def test_a_later_block_matches_what_an_earlier_one_wrote(self):
        self.assertEqual(self.apply([("beta", "BETA"), ("BETA", "B2")]), "alpha\nB2\ngamma\n")

    def test_one_miss_refuses_the_whole_batch_and_reports_every_block(self):
        message = self.refusal([("alpha", "ONE"), ("zeta", "Z"), ("gamma", "THREE")])
        self.assertIn("1 of 3 blocks failed", message)
        self.assertIn("block 1: OK, matched line 1", message)
        self.assertIn("block 2: FAILED. No replacement performed", message)
        self.assertIn("block 3: OK, matched line 3", message)
        self.assertIn("resend all 3", message)

    def test_text_removed_by_an_earlier_block_says_so(self):
        message = self.refusal([("alpha\nbeta", "AB"), ("beta", "B")])
        self.assertIn("an earlier block in this call changed or removed it", message)

    def test_diagnostics_are_indented_under_their_block_without_trailing_spaces(self):
        message = self.refusal([("alpha", "ONE"), ("beta ", "B")])
        self.assertIn("block 2: FAILED.", message)
        self.assertIn("\n      The text matches once trailing whitespace", message)
        self.assertFalse(any(line != line.rstrip() for line in message.split("\n")))

    def test_ambiguity_is_judged_against_the_text_at_that_point(self):
        message = self.refusal([("alpha", "gamma"), ("gamma", "G")])
        self.assertIn("appears 2 times", message)

    def test_replace_all_applies_to_every_block(self):
        text = "x\ny\nx\ny\n"
        self.assertEqual(
            self.apply([("x", "X"), ("y", "Y")], text=text, replace_all=True), "X\nY\nX\nY\n"
        )

    def test_a_single_edit_keeps_the_plain_error(self):
        message = self.refusal([("zeta", "Z")])
        self.assertTrue(message.startswith("No replacement performed"))
        self.assertNotIn("block 1", message)

    def test_identical_old_and_new_is_a_failed_block(self):
        self.assertIn("identical", self.refusal([("alpha", "ONE"), ("beta", "beta")]))

    def test_blocks_that_cancel_out_write_nothing(self):
        self.assertIn("no change", self.refusal([("alpha", "A"), ("A", "alpha")]))

    def test_syntax_errors_join_the_report_and_bring_the_example_once(self):
        raw = block("alpha", "ONE") + "<<<<<<< SEARCH\nbeta\n=======\nB\n=======\n"
        edits = cfs.parse_search_replace(raw)
        with self.assertRaises(cfs.CfsError) as ctx:
            cfs.apply_edits(self.TEXT, edits, "/f", syntax_help=cfs.SR_EXAMPLE)
        message = str(ctx.exception)
        self.assertIn("block 1: OK", message)
        self.assertIn("block 2: FAILED. The block starting on line 6", message)
        self.assertEqual(message.count(cfs.SR_EXAMPLE), 1)


class TestMarkerDetection(unittest.TestCase):
    def test_detects_conflict_markers_in_a_file(self):
        self.assertTrue(cfs.file_has_marker_lines("a\n<<<<<<< HEAD\nb\n"))
        self.assertTrue(cfs.file_has_marker_lines("a\n=======\nb\n"))
        self.assertTrue(cfs.file_has_marker_lines("a\n>>>>>>> branch\n"))

    def test_ordinary_markdown_is_not_flagged(self):
        self.assertFalse(cfs.file_has_marker_lines("# Title\n\n- a\n- b\n"))

    def test_setext_headings_are_not_flagged(self):
        # Only exactly seven '=' is structural, so real headings pass.
        self.assertFalse(cfs.file_has_marker_lines("Title\n=====\n\nBody\n"))
        self.assertFalse(cfs.file_has_marker_lines("Title\n==========\n"))

    def test_exactly_seven_equals_is_flagged(self):
        self.assertTrue(cfs.file_has_marker_lines("Title\n=======\n"))


class TestReadDelimited(unittest.TestCase):
    def _stdin(self, text):
        sys.stdin = io.StringIO(text)
        self.addCleanup(setattr, sys, "stdin", sys.__stdin__)

    def test_splits_on_the_marker_line(self):
        self._stdin("old line\n@@\nnew line\n")
        self.assertEqual(cfs.read_delimited("@@"), ("old line", "new line"))

    def test_multiline_both_sides(self):
        self._stdin("a\nb\n@@\nc\nd\n")
        self.assertEqual(cfs.read_delimited("@@"), ("a\nb", "c\nd"))

    def test_empty_new_side_is_a_deletion(self):
        self._stdin("gone\n@@\n")
        self.assertEqual(cfs.read_delimited("@@"), ("gone", ""))

    def test_content_needing_json_escapes_passes_through_untouched(self):
        raw = 'say "hi" $HOME `now` \\ done\n@@\nreplaced\n'
        self._stdin(raw)
        old, new = cfs.read_delimited("@@")
        self.assertEqual(old, 'say "hi" $HOME `now` \\ done')
        self.assertEqual(new, "replaced")

    def test_marker_must_be_a_whole_line(self):
        # "@@" inside a line is content, not a delimiter.
        self._stdin("prefix @@ suffix\n@@\nnew\n")
        self.assertEqual(cfs.read_delimited("@@"), ("prefix @@ suffix", "new"))

    def test_missing_delimiter_raises(self):
        self._stdin("no marker here\n")
        with self.assertRaises(cfs.CfsError) as ctx:
            cfs.read_delimited("@@")
        self.assertIn("not found", str(ctx.exception))

    def test_duplicate_delimiter_raises_with_line_numbers(self):
        self._stdin("a\n@@\nb\n@@\nc\n")
        with self.assertRaises(cfs.CfsError) as ctx:
            cfs.read_delimited("@@")
        message = str(ctx.exception)
        self.assertIn("appears 2 times", message)
        self.assertIn("lines 2, 4", message)

    def test_empty_delimiter_is_rejected(self):
        # A blank marker would match the empty final element every heredoc
        # produces, so it can never be unique. Reject it by name rather than
        # letting it fail as a confusing duplicate.
        self._stdin("old\n\nnew\n")
        with self.assertRaises(cfs.CfsError) as ctx:
            cfs.read_delimited("")
        self.assertIn("must not be empty", str(ctx.exception))


class TestNoFileIndirection(unittest.TestCase):
    """old_str must be generated inline; reading it from a file would defeat
    the proof-of-knowledge that matching on old_str exists to provide."""

    def test_edit_parser_has_no_old_or_new_flags(self):
        parser = cfs.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["edit", "/f", "--rev", "r", "--old", "a", "--new", "b"])

    def test_write_content_flag_does_not_read_files(self):
        args = cfs.build_parser().parse_args(
            ["write", "/f", "--new", "--content", "@/etc/passwd"]
        )
        self.assertEqual(args.content, "@/etc/passwd")  # literal, not a file read


class TestRetiredFlags(unittest.TestCase):
    """Old spellings parse, then are refused by name. They collided with the same
    flag meaning something else elsewhere, and a working alias would teach that."""

    def _args(self, argv):
        return cfs.build_parser().parse_args(argv)

    def test_new_spellings_carry_the_values(self):
        args = self._args(["diff", "/f", "--since", "r1", "--until", "r2", "--full"])
        self.assertEqual(args.from_rev, "r1")
        self.assertEqual(args.to_rev, "r2")
        self.assertTrue(args.full)
        cfs.reject_retired_flags(args)  # nothing retired was passed

    def test_each_retired_spelling_names_its_replacement(self):
        for argv, expected in (
            (["diff", "/f", "--from", "r1"], "--since"),
            (["diff", "/f", "--since", "r1", "--to", "r2"], "--until"),
            (["diff", "/f", "--since", "r1", "--force"], "--full"),
            (["search", "q", "--max", "5"], "--limit"),
        ):
            with self.assertRaises(cfs.CfsError) as ctx:
                cfs.reject_retired_flags(self._args(argv))
            self.assertIn(expected, str(ctx.exception), argv)

    def test_missing_since_is_a_cfserror_not_a_parser_exit(self):
        # argparse `required` would fire before cmd_diff runs, which would stop
        # the retired --from from ever being named.
        with self.assertRaises(cfs.CfsError) as ctx:
            cfs.cmd_diff(self._args(["diff", "/f"]))
        self.assertIn("--since", str(ctx.exception))

    def test_abbreviations_are_off_so_retired_flags_stay_hidden(self):
        with self.assertRaises(SystemExit):
            self._args(["diff", "/f", "--f", "r1"])


class TestStaleRevError(unittest.TestCase):
    """The diff rides on .stdout, never in the message: it ends in a rev, and a
    rev must not reach the caller without the content backing it."""

    def _with_report(self, report):
        original = cfs.diff_report
        cfs.diff_report = lambda *a, **k: report
        self.addCleanup(setattr, cfs, "diff_report", original)

    def _raising(self, exc):
        original = cfs.diff_report
        def boom(*a, **k):
            raise exc
        cfs.diff_report = boom
        self.addCleanup(setattr, cfs, "diff_report", original)

    def test_report_goes_to_stdout_not_the_message(self):
        self._with_report("CHANGED: ...\nrev: r9")
        err = cfs.stale_rev_error("/memory/a.md", "r1", "write")
        self.assertEqual(err.stdout, "CHANGED: ...\nrev: r9")
        self.assertNotIn("r9", str(err))
        self.assertIn("stdout", str(err))

    def test_delete_asks_to_reconfirm_not_to_reapply(self):
        self._with_report("CHANGED: ...")
        self.assertIn("still mean to delete", str(cfs.stale_rev_error("/a.md", "r1", "delete")))

    def test_binary_paths_point_at_download_and_carry_no_payload(self):
        err = cfs.stale_rev_error("/memory/chart.png", "r1", "upload")
        self.assertIsNone(err.stdout)
        self.assertIn("cfs download", str(err))

    def test_a_failing_diff_never_replaces_the_error(self):
        self._raising(cfs.CfsError("Path does not exist."))
        err = cfs.stale_rev_error("/memory/a.md", "r1", "write")
        self.assertIsNone(err.stdout)
        self.assertIn("Stale rev", str(err))
        self.assertIn("Re-read", str(err))

    def test_non_utf8_content_degrades_too(self):
        # The suffix list cannot catch a latin-1 .log; diff_report reports it.
        self._with_report("/memory/a.log is not text at one of these revisions; cannot diff.")
        err = cfs.stale_rev_error("/memory/a.log", "r1", "write")
        self.assertIsNone(err.stdout)
        self.assertIn("Re-read", str(err))

    def test_plain_errors_carry_no_payload(self):
        self.assertIsNone(cfs.CfsError("boom").stdout)


class TestCheckRevBelongs(unittest.TestCase):
    """`rev:<id>` resolves globally, so a rev from another file downloads fine."""

    def test_matching_path_passes(self):
        cfs.check_rev_belongs({"path_display": "/memory/a.md"}, "/memory/a.md", "r1")

    def test_case_differences_pass(self):
        # Dropbox paths are case-insensitive, so path_display may differ in case
        # from what the caller typed without naming a different file.
        cfs.check_rev_belongs({"path_display": "/Memory/A.md"}, "/memory/a.md", "r1")

    def test_foreign_rev_is_refused_by_name(self):
        with self.assertRaises(cfs.CfsError) as ctx:
            cfs.check_rev_belongs({"path_display": "/memory/b.md"}, "/memory/a.md", "r1")
        message = str(ctx.exception)
        self.assertIn("/memory/b.md", message)
        self.assertIn("r1", message)

    def test_absent_path_display_is_not_second_guessed(self):
        cfs.check_rev_belongs({}, "/memory/a.md", "r1")


class TestRevIfContentUnchanged(unittest.TestCase):
    def _patch_download(self, held_data, current_data, current_rev="r2"):
        def fake(payload):
            if payload["path"].startswith("rev:"):
                return held_data, {"path_display": "/f", "rev": "r1"}
            return current_data, {"path_display": "/f", "rev": current_rev}

        original = cfs.content_download
        cfs.content_download = fake
        self.addCleanup(setattr, cfs, "content_download", original)

    def test_returns_current_rev_when_content_matches(self):
        self._patch_download(b"same", b"same")
        self.assertEqual(cfs._rev_if_content_unchanged("/f", "r1"), "r2")

    def test_returns_none_when_content_differs(self):
        self._patch_download(b"old", b"new")
        self.assertIsNone(cfs._rev_if_content_unchanged("/f", "r1"))

    def test_an_unfetchable_held_rev_is_not_tolerated(self):
        # An invented or expired rev can't even be downloaded; that must
        # degrade to None (a real stale-rev rejection), not propagate as a
        # raw error from inside the tolerance check.
        def fake(payload):
            raise cfs.CfsError("Path does not exist.")

        original = cfs.content_download
        cfs.content_download = fake
        self.addCleanup(setattr, cfs, "content_download", original)
        self.assertIsNone(cfs._rev_if_content_unchanged("/f", "invented"))

    def test_a_held_rev_belonging_to_another_file_is_not_tolerated(self):
        # Same degrade-gracefully path as an invented or expired rev: this
        # isn't proof of anything for /f, so it's None, not a raised error --
        # the caller's own stale_rev_error still explains what's wrong.
        def fake(payload):
            return b"x", {"path_display": "/other.md", "rev": "r1"}

        original = cfs.content_download
        cfs.content_download = fake
        self.addCleanup(setattr, cfs, "content_download", original)
        self.assertIsNone(cfs._rev_if_content_unchanged("/f", "r1"))


class TestUploadBytesTolerance(unittest.TestCase):
    """upload_bytes backs both write and upload."""

    def _args(self, argv):
        return cfs.build_parser().parse_args(argv)

    def _patch(self, download, upload):
        original_d, original_u = cfs.content_download, cfs.content_upload
        cfs.content_download, cfs.content_upload = download, upload
        self.addCleanup(setattr, cfs, "content_download", original_d)
        self.addCleanup(setattr, cfs, "content_upload", original_u)

    def test_stale_rev_with_identical_content_retries_and_succeeds(self):
        modes_tried = []

        def fake_download(payload):
            return b"same", {"path_display": "/f", "rev": "r2"}

        def fake_upload(payload, data):
            modes_tried.append(payload["mode"])
            if payload["mode"]["update"] == "r1":
                raise cfs.CfsError("conflict")
            return {"rev": "r3"}

        self._patch(fake_download, fake_upload)
        args = self._args(["write", "/f", "--rev", "r1"])
        self.assertEqual(cfs.upload_bytes("/f", b"same", args), {"rev": "r3"})
        self.assertEqual(modes_tried[-1], {".tag": "update", "update": "r2"})

    def test_stale_rev_with_different_content_still_raises(self):
        def fake_download(payload):
            if payload["path"].startswith("rev:"):
                return b"old", {"path_display": "/f", "rev": "r1"}
            return b"new", {"path_display": "/f", "rev": "r2"}

        def fake_upload(payload, data):
            raise cfs.CfsError("conflict")

        self._patch(fake_download, fake_upload)
        original_diff = cfs.diff_report
        cfs.diff_report = lambda *a, **k: "CHANGED: ...\nrev: r2"
        self.addCleanup(setattr, cfs, "diff_report", original_diff)

        args = self._args(["write", "/f", "--rev", "r1"])
        with self.assertRaises(cfs.CfsError) as ctx:
            cfs.upload_bytes("/f", b"old", args)
        self.assertIn("Stale rev", str(ctx.exception))

    def test_sustained_contention_gives_up_rather_than_looping_forever(self):
        counter = {"n": 1}

        def fake_download(payload):
            counter["n"] += 1
            return b"same", {"path_display": "/f", "rev": f"r{counter['n']}"}

        def fake_upload(payload, data):
            raise cfs.CfsError("conflict")

        self._patch(fake_download, fake_upload)
        args = self._args(["write", "/f", "--rev", "r1"])
        with self.assertRaises(cfs.CfsError) as ctx:
            cfs.upload_bytes("/f", b"same", args)
        self.assertIn("Gave up", str(ctx.exception))


class TestEditTolerance(unittest.TestCase):
    def _args(self, argv):
        return cfs.build_parser().parse_args(argv)

    def _stdin(self, text):
        sys.stdin = io.StringIO(text)
        self.addCleanup(setattr, sys, "stdin", sys.__stdin__)

    def _patch(self, download, upload):
        original_d, original_u = cfs.content_download, cfs.content_upload
        cfs.content_download, cfs.content_upload = download, upload
        self.addCleanup(setattr, cfs, "content_download", original_d)
        self.addCleanup(setattr, cfs, "content_upload", original_u)

    def test_round_tripped_content_edits_against_the_current_rev(self):
        text = "hello\n"
        modes_tried = []

        def fake_download(payload):
            return text.encode(), {"path_display": "/f", "rev": "r2"}

        def fake_upload(payload, data):
            modes_tried.append(payload["mode"])
            return {"rev": "r3"}

        self._patch(fake_download, fake_upload)
        self._stdin("hello\n@@\ngoodbye\n")
        result = cfs.cmd_edit(self._args(["edit", "/f", "--rev", "r1", "--delim", "@@"]))
        self.assertEqual(result, "Edited /f.\nnew rev: r3")
        self.assertEqual(modes_tried, [{".tag": "update", "update": "r2"}])

    def _fake_file(self, text, uploads):
        def fake_download(payload):
            return text.encode(), {"path_display": "/f", "rev": "r1"}

        def fake_upload(payload, data):
            uploads.append(data.decode())
            return {"rev": "r2"}

        self._patch(fake_download, fake_upload)

    def test_several_blocks_land_in_one_upload(self):
        uploads = []
        self._fake_file("alpha\nbeta\ngamma\n", uploads)
        self._stdin(block("alpha", "ONE") + block("gamma", "THREE"))
        result = cfs.cmd_edit(self._args(["edit", "/f", "--rev", "r1"]))
        self.assertEqual(result, "Edited /f (2 blocks).\nnew rev: r2")
        self.assertEqual(uploads, ["ONE\nbeta\nTHREE\n"])

    def test_a_failed_block_uploads_nothing(self):
        uploads = []
        self._fake_file("alpha\nbeta\n", uploads)
        self._stdin(block("alpha", "ONE") + block("zeta", "Z"))
        with self.assertRaises(cfs.CfsError):
            cfs.cmd_edit(self._args(["edit", "/f", "--rev", "r1"]))
        self.assertEqual(uploads, [])

    def test_a_json_array_is_a_batch(self):
        uploads = []
        self._fake_file("alpha\nbeta\n", uploads)
        self._stdin('[{"old_str": "alpha", "new_str": "A"}, {"old_str": "beta", "new_str": "B"}]')
        cfs.cmd_edit(self._args(["edit", "/f", "--rev", "r1"]))
        self.assertEqual(uploads, ["A\nB\n"])

    def test_a_malformed_json_array_item_is_named(self):
        self._fake_file("alpha\n", [])
        self._stdin('[{"old_str": "alpha", "new_str": "A"}, {"old_str": "beta"}]')
        with self.assertRaises(cfs.CfsError) as ctx:
            cfs.cmd_edit(self._args(["edit", "/f", "--rev", "r1"]))
        self.assertIn("Edit 2 of the JSON array", str(ctx.exception))

    def test_genuinely_changed_content_still_raises_before_touching_the_edit(self):
        def fake_download(payload):
            if payload["path"].startswith("rev:"):
                return b"old\n", {"path_display": "/f", "rev": "r1"}
            return b"new\n", {"path_display": "/f", "rev": "r2"}

        self._patch(fake_download, lambda payload, data: self.fail("should not upload"))
        original_diff = cfs.diff_report
        cfs.diff_report = lambda *a, **k: "CHANGED: ...\nrev: r2"
        self.addCleanup(setattr, cfs, "diff_report", original_diff)

        self._stdin("old\n@@\nchanged\n")
        with self.assertRaises(cfs.CfsError) as ctx:
            cfs.cmd_edit(self._args(["edit", "/f", "--rev", "r1", "--delim", "@@"]))
        self.assertIn("Stale rev", str(ctx.exception))


class TestDiffThresholds(unittest.TestCase):
    def test_fraction_stays_under_the_context_break_even(self):
        # Past ~1/7 the diff is the longer read, so a larger fraction would
        # invert the point of having a fallback. Reasoning at the constant.
        self.assertLess(cfs.DIFF_MAX_CHANGED_FRACTION, 1 / 7)


class TestListRecursion(unittest.TestCase):
    """Recursion follows depth. The old --recursive flag was store_true with
    default=True, so it was always on and depth 1 fetched the whole tree."""

    def _payload_recursive(self, argv):
        captured = {}
        args = cfs.build_parser().parse_args(argv)
        def fake_rpc(endpoint, payload):
            captured.update(payload)
            return {"entries": []}
        original, cfs.rpc = cfs.rpc, fake_rpc
        try:
            cfs.cmd_list(args)
        finally:
            cfs.rpc = original
        return captured["recursive"]

    def test_depth_one_is_a_shallow_call(self):
        self.assertFalse(self._payload_recursive(["list", "/", "--depth", "1"]))

    def test_the_default_depth_recurses(self):
        self.assertTrue(self._payload_recursive(["list", "/"]))

    def test_deeper_still_recurses(self):
        self.assertTrue(self._payload_recursive(["list", "/", "--depth", "5"]))

    def test_the_inert_flag_is_gone(self):
        with self.assertRaises(SystemExit):
            cfs.build_parser().parse_args(["list", "/", "--recursive"])


class TestJsonEnvelopeWarning(unittest.TestCase):
    """write's stdin is raw, so the old JSON envelope stores verbatim. Unwrapping
    it by sniffing would corrupt real JSON files, so it warns instead."""

    def _warn(self, content):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            cfs.warn_if_json_envelope(content)
        return err.getvalue()

    def test_lone_content_envelope_warns(self):
        out = self._warn('{"content": "hello\\n"}')
        self.assertIn("verbatim", out)
        self.assertIn("--json", out)

    def test_a_real_json_file_is_silent(self):
        self.assertEqual(self._warn('{\n  "port": 8080\n}\n'), "")

    def test_an_object_with_other_keys_is_silent(self):
        # Only the exact envelope shape is confusable; anything else is a
        # document that happens to be JSON.
        self.assertEqual(self._warn('{"content": "x", "rev": "abc"}'), "")

    def test_ordinary_prose_is_silent(self):
        self.assertEqual(self._warn("# Notes\n\nnothing json here\n"), "")

    def test_malformed_json_is_silent(self):
        self.assertEqual(self._warn('{"content": "oops'), "")


class TestWriteDefaultsToRaw(unittest.TestCase):
    """The asymmetry that tripped callers: edit took raw stdin while write
    required --stdin to get the same thing."""

    def test_bare_write_is_raw_not_json(self):
        args = cfs.build_parser().parse_args(["write", "/f", "--new"])
        self.assertFalse(args.json)

    def test_stdin_flag_still_parses(self):
        args = cfs.build_parser().parse_args(["write", "/f", "--new", "--stdin"])
        self.assertTrue(args.stdin)
        self.assertFalse(args.json)

    def test_json_is_opt_in(self):
        args = cfs.build_parser().parse_args(["write", "/f", "--new", "--json"])
        self.assertTrue(args.json)


class TestWriteMode(unittest.TestCase):
    def _args(self, argv):
        return cfs.build_parser().parse_args(argv)

    def test_new_uses_add(self):
        self.assertEqual(cfs.write_mode(self._args(["write", "/f", "--new"]), "/f"), "add")

    def test_rev_uses_update_cas(self):
        mode = cfs.write_mode(self._args(["write", "/f", "--rev", "abc"]), "/f")
        self.assertEqual(mode, {".tag": "update", "update": "abc"})

    def test_neither_raises(self):
        with self.assertRaises(cfs.CfsError) as ctx:
            cfs.write_mode(self._args(["write", "/f"]), "/f")
        self.assertIn("without --rev", str(ctx.exception))

    def test_both_raises(self):
        with self.assertRaises(cfs.CfsError):
            cfs.write_mode(self._args(["write", "/f", "--new", "--rev", "abc"]), "/f")

    def test_upload_shares_the_same_rule(self):
        args = self._args(["upload", "/f", "--from", "x.png"])
        with self.assertRaises(cfs.CfsError):
            cfs.write_mode(args, "/f")


class _ByteStream:
    """Stands in for sys.stdout, which cmd_grep writes to via .buffer."""

    def __init__(self):
        self.buffer = io.BytesIO()

    def text(self):
        return self.buffer.getvalue().decode("utf-8", "replace")


class TestGrepArgvSplit(unittest.TestCase):
    """Telling a path operand from an option value is the whole job; if this
    is wrong, grep searches the wrong thing or rewrites a pattern."""

    def split(self, argv):
        return cfs.GrepArgv(argv)

    def test_first_operand_is_the_pattern(self):
        parsed = self.split(["alpha", "/memory"])
        self.assertEqual(parsed.operands, [0, 1])
        self.assertEqual(parsed.path_indices(), [1])

    def test_dash_e_makes_every_operand_a_path(self):
        # The case the option table exists for: identical tokens, one a
        # pattern and one a path.
        parsed = self.split(["-e", "/memory", "-r", "/memory"])
        self.assertTrue(parsed.pattern_is_an_option)
        self.assertEqual(parsed.path_indices(), [3])

    def test_long_pattern_option_with_equals(self):
        parsed = self.split(["--regexp=alpha", "/memory"])
        self.assertEqual(parsed.path_indices(), [1])

    def test_separated_option_value_is_not_an_operand(self):
        parsed = self.split(["-i", "-C", "2", "alpha", "/memory"])
        self.assertEqual(parsed.path_indices(), [4])

    def test_attached_option_value(self):
        self.assertEqual(self.split(["-C2", "alpha", "/m"]).path_indices(), [2])

    def test_cluster_ending_in_an_arg_taking_option(self):
        self.assertEqual(self.split(["-inC", "2", "alpha", "/m"]).path_indices(), [3])

    def test_cluster_with_attached_value(self):
        self.assertEqual(self.split(["-inC2", "alpha", "/m"]).path_indices(), [2])

    def test_long_option_value_separated_and_attached(self):
        self.assertEqual(self.split(["--include", "*.md", "a", "/m"]).path_indices(), [3])
        self.assertEqual(self.split(["--include=*.md", "a", "/m"]).path_indices(), [2])

    def test_numeric_context_shorthand_is_not_an_operand(self):
        self.assertEqual(self.split(["-5", "alpha", "/m"]).path_indices(), [2])

    def test_double_dash_ends_options(self):
        parsed = self.split(["--", "-alpha", "/m"])
        self.assertEqual(parsed.operands, [1, 2])
        self.assertEqual(parsed.path_indices(), [2])

    def test_option_value_beginning_with_a_dash(self):
        # -foo is -e's value, not a cluster of f, o, o.
        parsed = self.split(["-e", "-foo", "/m"])
        self.assertEqual(parsed.path_indices(), [2])
        self.assertEqual(parsed.unknown, [])

    def test_recursion_is_read_from_the_options_not_the_pattern(self):
        for argv in (["-r", "a"], ["-Rn", "a"], ["--recursive", "a"], ["-inr", "a"]):
            self.assertTrue(self.split(argv).recursive, argv)
        for argv in (["a"], ["-in", "a"], ["-e", "-r", "/m"]):
            self.assertFalse(self.split(argv).recursive, argv)

    def test_unknown_options_are_recorded_not_guessed_at(self):
        self.assertEqual(self.split(["--frobnicate", "a"]).unknown, ["--frobnicate"])
        self.assertEqual(self.split(["-Q", "a"]).unknown, ["-Q"])

    def test_known_options_are_not_reported_unknown(self):
        for argv in (["-rniI", "a"], ["--color=auto", "a"], ["--null-data", "a"]):
            self.assertEqual(self.split(argv).unknown, [], argv)


class TestStoreScope(unittest.TestCase):
    def test_no_paths_means_the_whole_store(self):
        self.assertEqual(cfs.store_scope([]), "/")

    def test_common_ancestor_of_several_paths(self):
        self.assertEqual(cfs.store_scope(["/memory/a.md", "/memory/b.md"]), "/memory")

    def test_divergent_paths_fall_back_to_the_root(self):
        self.assertEqual(cfs.store_scope(["/memory/a.md", "/notes/b.md"]), "/")

    def test_single_path_is_its_own_scope(self):
        # May name a file; list_tree retries at the parent if Dropbox says so.
        self.assertEqual(cfs.store_scope(["/memory/a.md"]), "/memory/a.md")


class TestGrepAgainstAMirror(unittest.TestCase):
    """End to end against real GNU grep, with the mirror hand-built so no
    Dropbox call is needed."""

    def setUp(self):
        if not shutil.which("grep"):
            self.skipTest("GNU grep not installed")
        self.root = tempfile.mkdtemp(prefix="cfs-mirror-test")
        os.makedirs(os.path.join(self.root, "memory"))
        self._write("memory/a.md", "alpha BETA\ngamma\n")
        self._write("memory/b.md", "delta\nalpha again\n")
        self._write("memory/notes.txt", "alpha in a txt file\n")
        self._patch("mirror_root", lambda: self.root)
        self._patch("mirror_sync", lambda scope: None)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, rel, text):
        with open(os.path.join(self.root, rel), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    def _patch(self, name, replacement):
        original = getattr(cfs, name)
        setattr(cfs, name, replacement)
        self.addCleanup(setattr, cfs, name, original)

    def run_grep(self, argv):
        """cmd_grep writes bytes to the underlying buffers, as grep does."""
        out, err = _ByteStream(), _ByteStream()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cfs.cmd_grep(argv)
        return code, out.text(), err.text()

    def test_single_file_prints_content_without_a_path(self):
        # Real grep omits the filename here, so output rewriting must not
        # blindly prefix every line.
        code, out, _ = self.run_grep(["alpha", "/memory/a.md"])
        self.assertEqual((code, out), (0, "alpha BETA\n"))

    def test_recursive_search_reports_store_paths(self):
        code, out, _ = self.run_grep(["-rn", "alpha", "/memory"])
        self.assertEqual(code, 0)
        self.assertIn("/memory/a.md:1:alpha BETA", out)
        self.assertNotIn(self.root, out)

    def test_no_path_searches_the_whole_store_recursively(self):
        code, out, _ = self.run_grep(["-n", "delta"])
        self.assertEqual(code, 0)
        self.assertIn("/memory/b.md:1:delta", out)

    def test_no_match_exits_one_and_prints_nothing(self):
        self.assertEqual(self.run_grep(["nothingmatchesthis"]), (1, "", ""))

    def test_gnu_flags_reach_the_real_binary(self):
        self.assertIn("BETA", self.run_grep(["-i", "beta", "/memory/a.md"])[1])
        self.assertEqual(self.run_grep(["-c", "alpha", "/memory/a.md"])[1], "1\n")
        self.assertIn("alpha", self.run_grep(["-w", "alpha", "/memory/a.md"])[1])

    def test_include_filter_applies(self):
        _, out, _ = self.run_grep(["-rl", "--include=*.md", "alpha", "/memory"])
        self.assertIn("/memory/a.md", out)
        self.assertNotIn("notes.txt", out)

    def test_alternation_needs_dash_e_exactly_as_in_real_grep(self):
        # The point of the rewrite: grep's own dialect rules, not a lookalike.
        self.assertEqual(self.run_grep(["-r", "delta|gamma", "/memory"])[0], 1)
        self.assertEqual(self.run_grep(["-rE", "delta|gamma", "/memory"])[0], 0)

    def test_directory_without_dash_r_errors_as_grep_would(self):
        code, _, err = self.run_grep(["alpha", "/memory"])
        self.assertEqual(code, 2)
        self.assertIn("Is a directory", err)
        self.assertNotIn(self.root, err)

    def test_missing_path_is_named(self):
        with self.assertRaises(cfs.CfsError) as ctx:
            self.run_grep(["alpha", "/memory/nope.md"])
        self.assertIn("/memory/nope.md", str(ctx.exception))

    def test_misclassified_unknown_option_names_itself_as_the_suspect(self):
        # -Q is not a grep option; if it took a value, "alpha" would be it.
        with self.assertRaises(cfs.CfsError) as ctx:
            self.run_grep(["-Q", "pattern", "alpha"])
        self.assertIn("-Q", str(ctx.exception))

    def test_pattern_is_required(self):
        with self.assertRaises(cfs.CfsError) as ctx:
            self.run_grep([])
        self.assertIn("needs a pattern", str(ctx.exception))

if __name__ == "__main__":
    unittest.main(verbosity=2)
