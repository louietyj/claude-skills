import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin"))
import unsub  # noqa: E402


def swept(k, last="2026-11-01", m="one-click", url="https://x.test/u?t=2"):
    return {"k": k, "sender": k, "addr": k, "name": k, "alias": False, "cat": "promotions", "n": 1,
            "first": last, "last": last, "subj": ["s"], "m": m, "url": url}


class Helpers(unittest.TestCase):
    def test_folded_url_loses_whitespace(self):
        got = unsub.parse_list_unsubscribe(
            "<mailto:u@x.test>,\r\n <https://x.test/v1/unsub?q=abc\r\n def>", "List-Unsubscribe=One-Click")
        self.assertEqual(got, {"method": "one-click", "url": "https://x.test/v1/unsub?q=abcdef"})

    def test_methods(self):
        self.assertEqual(unsub.parse_list_unsubscribe("<https://x.test/u>", "")["method"], "https-link")
        self.assertEqual(unsub.parse_list_unsubscribe("<mailto:u@x.test>", "")["method"], "mailto")
        self.assertEqual(unsub.parse_list_unsubscribe("", "")["method"], "none")

    def test_space_inside_brackets(self):
        got = unsub.parse_list_unsubscribe("<mailto:u@x.test>, < https://x.test/u>", "List-Unsubscribe=One-Click")
        self.assertEqual(got, {"method": "one-click", "url": "https://x.test/u"})

    def test_plain_http_is_never_one_click(self):
        got = unsub.parse_list_unsubscribe("<http://x.test/u>", "List-Unsubscribe=One-Click")
        self.assertEqual(got["method"], "none")

    def test_id_timestamp(self):
        self.assertEqual(unsub.ts_from_id("1a17e8c32ee99fd7").date(), dt.date(2026, 10, 27))

    def test_alias(self):
        self.assertEqual(unsub.alias_display("app_at_xtudr_com_zrvnklgxs@simplelogin.co"), ("app@xtudr.com", True))
        self.assertEqual(unsub.alias_display("a@b.com"), ("a@b.com", False))

    def test_doc_key(self):
        self.assertEqual(unsub.doc_key("April.Cayao@Zintro.com"), "april.cayao@zintro.com")
        self.assertEqual(unsub.doc_key("a b@c.com"), "a_b@c.com")

    def test_page_text(self):
        self.assertEqual(unsub.page_text("<script>var a=1</script><p>You have  been\nunsubscribed.</p>"),
                         "You have been unsubscribed.")
        self.assertEqual(unsub.page_text("<form><button>Unsubscribe</button></form>"),
                         "[2 form/button elements] Unsubscribe")
        self.assertEqual(unsub.page_text("<div id=root></div>"), "[no visible text]")


class Plan(unittest.TestCase):
    def test_new_kill_is_a_job_and_keep_is_not(self):
        jobs, _ = unsub.plan({"a": "kill", "b": "keep"}, {}, {}, [swept("a"), swept("b")])
        self.assertEqual([j["k"] for j in jobs], ["a"])

    def test_mailto_and_none_never_run(self):
        jobs, judge = unsub.plan({"a": "kill", "b": "kill"}, {}, {},
                                 [swept("a", m="mailto", url=None), swept("b", m="none", url=None)])
        self.assertEqual(jobs, [])
        self.assertEqual(judge, [])

    def test_registry_url_used_when_sender_is_quiet(self):
        reg = {"a": {"method": "one-click", "url": "https://x.test/old", "tier": "junk"}}
        jobs, _ = unsub.plan({"a": "kill"}, reg, {}, [])
        self.assertEqual(jobs[0]["url"], "https://x.test/old")

    def test_failed_retries_only_with_a_new_url(self):
        st = {"a": {"status": "failed", "actedAt": "2026-10-28T20:00:00+00:00", "url": "https://x.test/u?t=2"}}
        self.assertEqual(unsub.plan({"a": "kill"}, {}, st, [swept("a")])[0], [])
        st["a"]["url"] = "https://x.test/u?t=1"
        self.assertEqual(len(unsub.plan({"a": "kill"}, {}, st, [swept("a")])[0]), 1)

    def test_skipped_is_never_retried(self):
        st = {"a": {"status": "skipped", "actedAt": "2026-10-01T00:00:00+00:00", "url": "old"}}
        self.assertEqual(unsub.plan({"a": "kill"}, {}, st, [swept("a")])[0], [])

    def test_still_mailing_retries_once_after_grace(self):
        st = {"a": {"status": "unsubscribed", "actedAt": "2026-10-01T00:00:00+00:00", "url": "https://x.test/old"}}
        within = unsub.plan({"a": "kill"}, {}, st, [swept("a", last="2026-10-10")])[0]
        self.assertEqual(within, [])
        past = unsub.plan({"a": "kill"}, {}, st, [swept("a", last="2026-10-20")])[0]
        self.assertEqual(len(past), 1)
        self.assertTrue(past[0]["retry"])
        st["a"]["retries"] = 1
        self.assertEqual(unsub.plan({"a": "kill"}, {}, st, [swept("a", last="2026-10-20")])[0], [])

    def test_judge_only_untiered_actionable(self):
        reg = {"a": {"tier": "junk"}}
        _, judge = unsub.plan({}, reg, {}, [swept("a"), swept("b"), swept("c", m="none", url=None)])
        self.assertEqual([s["k"] for s in judge], ["b"])


class Finalize(unittest.TestCase):
    def test_grouping(self):
        reg = {"kept": {"tier": "junk"}, "rec": {"tier": "exclude", "why": "recruiter"},
               "done": {"tier": "junk"}, "loud": {"tier": "junk"}}
        st = {"done": {"status": "unsubscribed", "actedAt": "2026-10-28T00:00:00+00:00"},
              "loud": {"status": "unsubscribed", "actedAt": "2026-10-01T00:00:00+00:00"}}
        sweep = [swept("kept"), swept("rec"), swept("done"), swept("loud"), swept("new"),
                 swept("mail", m="mailto", url=None)]
        _, _, page = unsub.finalize(reg, st, {"kept": "keep"}, sweep, {"new": {"tier": "junk", "why": "promo"}},
                                    [])
        self.assertEqual({p["k"]: p["sug"] for p in page}, {"kept": "prevkept", "loud": "ignoring", "new": "junk"})

    def test_results_update_status_and_count_retries(self):
        res = [{"k": "a", "outcome": "accepted", "ts": "2026-11-02T17:00:00+00:00", "url": "u", "retry": True},
               {"k": "b", "outcome": "failed", "ts": "2026-11-02T17:00:00+00:00", "url": "v", "reason": "HTTP 404"}]
        _, st, _ = unsub.finalize({}, {"a": {"status": "unsubscribed", "actedAt": "x", "retries": 0}}, {}, [], {},
                                  res)
        self.assertEqual(st["a"]["status"], "unsubscribed")
        self.assertEqual(st["a"]["retries"], 1)
        self.assertEqual(st["b"], {"status": "failed", "actedAt": "2026-11-02T17:00:00+00:00", "url": "v",
                                   "retries": 0, "reason": "HTTP 404"})


class Resolve(unittest.TestCase):
    def test_resolve_then_finalize(self):
        import json
        import subprocess
        import tempfile
        w = tempfile.mkdtemp()
        with open(os.path.join(w, "results.jsonl"), "w") as fh:
            fh.write(json.dumps({"k": "a", "outcome": "review", "ts": "2026-11-02T17:00:00+00:00", "url": "u",
                                 "page": "x"}) + "\n")
        cli = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "unsub.py"),
               "--work", w]
        blocked = subprocess.run(cli + ["finalize", "--artifact", "x"], capture_output=True, text=True)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("need `unsub resolve`", blocked.stderr)
        subprocess.run(cli + ["resolve", "a", "skipped", "preferences page"], check=True, capture_output=True)
        subprocess.run(cli + ["finalize", "--artifact", "x"], check=True, capture_output=True)
        st = json.load(open(os.path.join(w, "new", "status.json")))
        self.assertEqual(st["a"]["status"], "skipped")
        self.assertEqual(st["a"]["reason"], "preferences page")


if __name__ == "__main__":
    unittest.main()
