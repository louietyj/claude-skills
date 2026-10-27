#!/usr/bin/env python3
"""Tests for the authorize worker, against a running copy of it.

    npx wrangler dev --port 8787 --var BEARER:test     # in another shell
    python test_worker.py http://127.0.0.1:8787 test

Each test uses its own random conversation id, so it can also run against the
deployed worker (with the real bearer) without disturbing live approvals.
"""
import json
import random
import sys
import unittest
import urllib.error
import urllib.request

BASE, BEARER = (sys.argv.pop(1), sys.argv.pop(1)) if len(sys.argv) >= 3 else ("http://127.0.0.1:8787", "test")


def post(path, body, bearer=None):
    # Cloudflare 403s urllib's default `Python-urllib/3.x`.
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
               "User-Agent": "lmcps-authorize-test/1"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(BASE + path, json.dumps(body).encode(), headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw.decode(errors="replace")


def rpc(method, params=None, bearer=BEARER):
    return post("/mcp", {"jsonrpc": "2.0", "id": "t-1", "method": method, "params": params or {}}, bearer)


def new_conversation():
    return "".join(random.choice("abcdefghijkmnpqrstuvwxyz23456789") for _ in range(8))


class TestMcp(unittest.TestCase):
    def test_wrong_or_missing_bearer_is_refused(self):
        self.assertEqual(rpc("tools/list", bearer="wrong")[0], 401)
        self.assertEqual(rpc("tools/list", bearer=None)[0], 401)

    def test_discover_carries_the_2026_07_28_fields(self):
        status, body = rpc("server/discover")
        self.assertEqual(status, 200)
        r = body["result"]
        self.assertEqual(r["supportedVersions"], ["2026-07-28"])
        self.assertEqual(r["resultType"], "complete")
        self.assertIn("ttlMs", r)
        self.assertIn("cacheScope", r)

    def test_initialize_echoes_a_known_version(self):
        r = rpc("initialize", {"protocolVersion": "2025-06-18"})[1]["result"]
        self.assertEqual(r["protocolVersion"], "2025-06-18")

    def test_lists_both_tools_as_destructive(self):
        tools = rpc("tools/list")[1]["result"]["tools"]
        self.assertEqual({t["name"] for t in tools}, {"authorize_call", "authorize_tool_for_conversation"})
        for t in tools:
            self.assertTrue(t["annotations"]["destructiveHint"])
            self.assertNotIn("outputSchema", t)

    def test_a_notification_is_acknowledged(self):
        status, _ = post("/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"}, BEARER)
        self.assertEqual(status, 202)

    def test_bad_arguments_approve_nothing(self):
        r = rpc("tools/call", {"name": "authorize_call", "arguments": {
            "conversation": "NOPE", "server": "s", "tool": "t", "args": {}}})[1]["result"]
        self.assertTrue(r["isError"])
        self.assertIn("Nothing was approved", r["content"][0]["text"])


def approve(name, **arguments):
    r = rpc("tools/call", {"name": name, "arguments": arguments})[1]["result"]
    assert not r["isError"], r
    assert r["resultType"] == "complete", r
    return r


def redeem(**target):
    return post("/redeem", target)[1]


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.conv = new_conversation()

    def test_a_call_approval_redeems_once(self):
        approve("authorize_call", conversation=self.conv, server="s", tool="t", args={"a": 1})
        self.assertEqual(redeem(conversation=self.conv, server="s", tool="t", args={"a": 1}),
                         {"ok": True, "kind": "call"})
        self.assertEqual(redeem(conversation=self.conv, server="s", tool="t", args={"a": 1}),
                         {"ok": False})

    def test_args_compare_as_values_not_text(self):
        approve("authorize_call", conversation=self.conv, server="s", tool="t",
                args={"a": 1, "b": {"x": [1, 2]}})
        self.assertTrue(redeem(conversation=self.conv, server="s", tool="t",
                               args={"b": {"x": [1.0, 2]}, "a": 1.0})["ok"])

    def test_different_args_conversation_server_or_tool_do_not_match(self):
        approve("authorize_call", conversation=self.conv, server="s", tool="t", args={"a": 1})
        for target in ({"conversation": self.conv, "server": "s", "tool": "t", "args": {"a": 2}},
                       {"conversation": new_conversation(), "server": "s", "tool": "t", "args": {"a": 1}},
                       {"conversation": self.conv, "server": "other", "tool": "t", "args": {"a": 1}},
                       {"conversation": self.conv, "server": "s", "tool": "other", "args": {"a": 1}},
                       {"conversation": self.conv, "server": "s", "tool": "t", "args": {"a": 1, "b": 2}}):
            self.assertEqual(redeem(**target), {"ok": False}, target)
        self.assertTrue(redeem(conversation=self.conv, server="s", tool="t", args={"a": 1})["ok"])

    def test_a_tool_approval_covers_any_args_and_is_not_consumed(self):
        approve("authorize_tool_for_conversation", conversation=self.conv, server="s", tool="t")
        for args in ({"a": 1}, {"a": 2}, {}):
            self.assertEqual(redeem(conversation=self.conv, server="s", tool="t", args=args),
                             {"ok": True, "kind": "tool"})

    def test_redeem_rejects_malformed_bodies(self):
        status, body = post("/redeem", {"conversation": self.conv, "server": "s", "tool": "t"})
        self.assertEqual(status, 400)
        self.assertIn("args", body["error"])


if __name__ == "__main__":
    unittest.main()
