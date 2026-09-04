#!/usr/bin/env python3
"""Offline tests for lmcps. No network, no npx -- the stdio server under test is
fake_mcp_server.py and the HTTP server is a stub in this process.

    python test_lmcps.py
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
LMCPS = HERE / "bin" / "lmcps.py"
FAKE = HERE / "fake_mcp_server.py"
LF, CRLF = b"\n", b"\r\n"


def fake_server(mode="plain", **extra):
    cfg = {"command": sys.executable, "args": [str(FAKE), mode]}
    cfg.update(extra)
    return cfg


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lmcps-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.config_path = self.tmp / "config.json"

    def write_config(self, servers):
        self.config_path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")

    def run_lmcps(self, *args, env=None, timeout=30):
        e = dict(os.environ)
        e["LMCPS_HOME"] = str(self.tmp / "home")
        e["LMCPS_CONFIG"] = str(self.config_path)
        e.update(env or {})
        return subprocess.run([sys.executable, str(LMCPS), *args], capture_output=True,
                              text=True, timeout=timeout, env=e)


class TestServers(Base):
    def test_lists_name_transport_and_description(self):
        self.write_config({
            "tomtom": {"type": "http", "url": "https://example.invalid",
                       "description": "geocoding, routing, live traffic"},
            "adder": fake_server(description="adds numbers"),
        })
        r = self.run_lmcps("servers")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"adder\s+stdio\s+adds numbers")
        self.assertRegex(r.stdout, r"tomtom\s+http\s+geocoding, routing, live traffic")

    def test_missing_description_points_at_tools(self):
        self.write_config({"adder": fake_server()})
        r = self.run_lmcps("servers")
        self.assertIn("lmcps tools adder", r.stdout)

    def test_servers_never_spawns(self):
        """The preferences line runs this at the top of every conversation, so a
        server that cannot start must not make it fail or hang."""
        self.write_config({"broken": {"command": "definitely-not-a-real-binary"}})
        r = self.run_lmcps("servers")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("broken", r.stdout)

    def test_unknown_server_names_the_configured_ones(self):
        self.write_config({"adder": fake_server()})
        r = self.run_lmcps("tools", "nope")
        self.assertEqual(r.returncode, 1)
        self.assertIn("unknown server 'nope'", r.stderr)
        self.assertIn("adder", r.stderr)


class TestConfigLoading(Base):
    def test_invalid_json_is_reported_with_the_source(self):
        self.config_path.write_text("{not json", encoding="utf-8")
        r = self.run_lmcps("servers")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not valid JSON", r.stderr)

    def test_missing_mcpservers_key(self):
        self.config_path.write_text('{"servers": {}}', encoding="utf-8")
        r = self.run_lmcps("servers")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no `mcpServers` object", r.stderr)

    def test_the_catalog_served_as_the_config_names_the_mix_up(self):
        """A share link follows a rename, so a link made for mcp.json keeps
        working -- and keeps saying mcp.json -- once that file has become the
        catalog. Observed in the wild."""
        self.config_path.write_text(json.dumps(
            {"version": 1, "builtAt": "2026-09-03T07:00:00Z", "servers": {}}),
            encoding="utf-8")
        r = self.run_lmcps("servers")
        self.assertEqual(r.returncode, 1)
        self.assertIn("TOOL INDEX", r.stderr)
        self.assertIn("swapped", r.stderr)

    def test_explicit_config_path_must_exist(self):
        r = self.run_lmcps("servers", env={"LMCPS_CONFIG": str(self.tmp / "gone.json")})
        self.assertEqual(r.returncode, 1)
        self.assertIn("does not exist", r.stderr)

    def test_no_config_anywhere_does_not_fall_back_to_claude_json(self):
        """A fall back to ~/.claude.json would work on a Claude Code machine and
        fail on claude.ai -- the one place it is never tested.

        Runs from a copy of the script with no config beside it. Running it in
        place would pick up the developer's own config-url.txt and pass or fail
        depending on whose checkout it is.
        """
        bare = self.tmp / "skill" / "bin"
        bare.mkdir(parents=True)
        shutil.copy(LMCPS, bare / "lmcps.py")
        e = dict(os.environ)
        e.pop("LMCPS_CONFIG", None)
        e["LMCPS_HOME"] = str(self.tmp / "home")
        r = subprocess.run([sys.executable, str(bare / "lmcps.py"), "servers"],
                           capture_output=True, text=True, timeout=30, env=e)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no config", r.stderr)


class TestTools(Base):
    def test_lists_tools_with_first_description_line_only(self):
        self.write_config({"adder": fake_server()})
        r = self.run_lmcps("tools", "adder")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Add two numbers.", r.stdout)
        self.assertNotIn("Second line", r.stdout)
        self.assertIn("boom", r.stdout)

    def test_schema_prints_the_full_tool(self):
        self.write_config({"adder": fake_server()})
        r = self.run_lmcps("tools", "adder", "--schema", "add")
        self.assertEqual(r.returncode, 0, r.stderr)
        schema = json.loads(r.stdout)
        self.assertEqual(schema["name"], "add")
        self.assertIn("a", schema["inputSchema"]["properties"])

    def test_schema_for_unknown_tool_lists_the_real_ones(self):
        self.write_config({"adder": fake_server()})
        r = self.run_lmcps("tools", "adder", "--schema", "subtract")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no tool 'subtract'", r.stderr)
        self.assertIn("add", r.stderr)

    def test_server_instructions_are_surfaced(self):
        """MCP servers describe themselves via `instructions` in the initialize
        result. It arrives during a handshake we already pay for, so `tools`
        prints it rather than relying only on the hand-written config line."""
        self.write_config({"adder": fake_server("instructions")})
        r = self.run_lmcps("tools", "adder")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Prefer `add` over doing arithmetic yourself", r.stdout)
        self.assertIn("Fake Adder", r.stdout)

    def test_absent_instructions_are_not_faked(self):
        self.write_config({"adder": fake_server()})
        r = self.run_lmcps("tools", "adder")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("None", r.stdout)

    def test_cached_within_a_conversation_and_refresh_bypasses_it(self):
        self.write_config({"adder": fake_server()})
        self.assertEqual(self.run_lmcps("tools", "adder").returncode, 0)

        # Repoint the config at a server that cannot start. A cache hit is now
        # the only way the second call can succeed.
        self.write_config({"adder": fake_server("crash")})
        self.assertEqual(self.run_lmcps("tools", "adder").returncode, 0)
        self.assertEqual(self.run_lmcps("tools", "adder", "--refresh").returncode, 1)


class TestWarm(Base):
    """`warm` exists because a cold `npx -y <pkg>` install can outlast the
    sandbox's tool-call limit, killing the model's first real call however
    patient lmcps is."""

    def test_returns_at_once_and_names_what_it_started(self):
        self.write_config({"adder": fake_server()})
        r = self.run_lmcps("warm", timeout=15)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("adder", r.stdout)

    def test_skips_http_servers(self):
        """Nothing to install, and an HTTP server answers in about a second."""
        self.write_config({"tomtom": {"type": "http", "url": "https://example.invalid"},
                           "adder": fake_server()})
        r = self.run_lmcps("warm", timeout=15)
        self.assertIn("adder", r.stdout)
        self.assertNotIn("tomtom", r.stdout)

    def test_an_unstartable_server_is_not_fatal(self):
        self.write_config({"broken": {"command": "definitely-not-a-real-binary"},
                           "adder": fake_server()})
        r = self.run_lmcps("warm", timeout=15)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("adder", r.stdout)


class TestDescribe(Base):
    """`describe` exists so the config's `description` is a cache of what the
    server says about itself, rather than something hand-invented."""

    def test_shows_everything_the_server_says_about_itself(self):
        self.write_config({"adder": fake_server("instructions")})
        r = self.run_lmcps("describe", "adder")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Fake Adder", r.stdout)
        self.assertIn("Add two numbers.", r.stdout)
        self.assertIn("Prefer `add` over doing arithmetic yourself", r.stdout)

    def test_offers_no_generated_description(self):
        """Every mechanical candidate read as a pasteable answer while being a
        bad one. The placeholder has to stay empty, or it gets pasted."""
        self.write_config({"adder": fake_server("instructions")})
        r = self.run_lmcps("describe", "adder")
        self.assertIn('"description": "..."', r.stdout)
        self.assertNotIn('"description": "add', r.stdout)
        self.assertNotIn('"description": "Fake Adder', r.stdout)

    def test_falls_back_to_tool_descriptions_without_instructions(self):
        self.write_config({"adder": fake_server()})
        r = self.run_lmcps("describe", "adder")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("adder -- fake, 2 tools", r.stdout)
        self.assertIn("add: Add two numbers.", r.stdout)
        self.assertNotIn("own instructions", r.stdout)


class TestStdioProtocol(Base):
    def test_call_returns_the_text_content(self):
        self.write_config({"adder": fake_server()})
        r = self.run_lmcps("call", "adder", "add", '{"a": 1, "b": 2}')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "3")

    def test_log_lines_interleaved_with_responses_are_skipped(self):
        self.write_config({"adder": fake_server("noisy")})
        r = self.run_lmcps("call", "adder", "add", '{"a": 20, "b": 22}')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "42")

    def test_a_server_request_is_answered_rather_than_skipped(self):
        """fake_mcp_server 'roots' blocks on roots/list, so skipping the request
        instead of answering it hangs until the timeout."""
        self.write_config({"adder": fake_server("roots")})
        r = self.run_lmcps("--timeout", "15", "call", "adder", "add", '{"a": 2, "b": 3}')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "5")

    def test_an_unsupported_server_request_is_declined_not_ignored(self):
        self.write_config({"adder": fake_server("sampling")})
        r = self.run_lmcps("--timeout", "15", "call", "adder", "add", '{"a": 4, "b": 4}')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "8")

    def test_json_rpc_ids_are_strings(self):
        """Integer 1 and 2 can collide with a server-initiated request's id."""
        log = self.tmp / "seen.jsonl"
        self.write_config({"adder": fake_server()})
        self.run_lmcps("call", "adder", "add", '{"a": 1, "b": 1}',
                       env={"FAKE_LOG": str(log)})
        ids = [json.loads(l).get("id") for l in log.read_text().splitlines()]
        ids = [i for i in ids if i is not None]
        self.assertTrue(ids)
        for i in ids:
            self.assertIsInstance(i, str, f"id {i!r} is not a string")

    def test_startup_failure_reports_the_servers_own_diagnostic(self):
        self.write_config({"adder": fake_server("crash")})
        r = self.run_lmcps("call", "adder", "add", "{}")
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAKE_TOKEN is not set", r.stderr)

    def test_timeout_reports_the_servers_own_diagnostic(self):
        """The deadline used to be the one failure that said nothing about the
        server, which is the failure you are least able to reproduce later."""
        self.write_config({"adder": fake_server("hang")})
        r = self.run_lmcps("--timeout", "2", "call", "adder", "add", "{}")
        self.assertEqual(r.returncode, 1)
        self.assertIn("timed out after 2s", r.stderr)
        self.assertIn("waiting on a lock held by nobody", r.stderr)

    def test_a_swallowed_initialize_is_repeated(self):
        """A cold `npx -y <pkg>` drains stdin while installing, so the first
        request never reaches the server that eventually execs. Both sides then
        wait forever and the call dies at whatever the deadline is -- which is
        why it timed out at 120s and again at 300s, and why a warm retry, with
        nothing to install, answered instantly."""
        self.write_config({"adder": fake_server("eatstdin")})
        r = self.run_lmcps("tools", "adder", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Add two numbers.", r.stdout)

    def test_missing_command_is_named(self):
        self.write_config({"ghost": {"command": "definitely-not-a-real-binary"}})
        r = self.run_lmcps("call", "ghost", "x", "{}")
        self.assertEqual(r.returncode, 1)
        self.assertIn("command not found", r.stderr)

    def test_tool_reported_failure_exits_nonzero(self):
        """isError is an in-band failure inside a successful response; printed to
        stdout with exit 0 it would read as the tool having worked."""
        self.write_config({"adder": fake_server()})
        r = self.run_lmcps("call", "adder", "boom", "{}")
        self.assertEqual(r.returncode, 1)
        self.assertIn("it went boom", r.stderr)

    def test_protocol_error_is_surfaced(self):
        self.write_config({"adder": fake_server()})
        r = self.run_lmcps("call", "adder", "nosuchtool", "{}")
        self.assertEqual(r.returncode, 1)
        self.assertIn("unknown tool", r.stderr)

    def test_bad_argument_json_is_rejected_before_spawning(self):
        self.write_config({"adder": fake_server()})
        r = self.run_lmcps("call", "adder", "add", "{a: 1}")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not valid JSON", r.stderr)

    def test_unsupported_transport(self):
        self.write_config({"old": {"type": "sse", "url": "https://example.invalid"}})
        r = self.run_lmcps("call", "old", "x", "{}")
        self.assertEqual(r.returncode, 1)
        self.assertIn("'sse' is not supported", r.stderr)


class TestVarExpansion(Base):
    def test_env_var_is_expanded_into_the_servers_environment(self):
        self.write_config({"e": fake_server("echoenv", env={"FAKE_TOKEN": "${TEST_KEY}"})})
        r = self.run_lmcps("call", "e", "whatever", "{}", env={"TEST_KEY": "s3cret"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "s3cret")

    def test_default_is_used_when_unset(self):
        self.write_config({"e": fake_server("echoenv", env={"FAKE_TOKEN": "${NOPE:-fallback}"})})
        r = self.run_lmcps("call", "e", "whatever", "{}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "fallback")

    def test_unset_without_a_default_is_an_error(self):
        self.write_config({"e": fake_server("echoenv", env={"FAKE_TOKEN": "${NOPE}"})})
        r = self.run_lmcps("call", "e", "whatever", "{}")
        self.assertEqual(r.returncode, 1)
        self.assertIn("${NOPE}", r.stderr)


class StubHandler(BaseHTTPRequestHandler):
    """A JSON-RPC endpoint that records the headers it was sent."""
    seen_headers = {}
    mode = "json"

    def do_POST(self):
        # Lowercased: urllib title-cases outgoing header names, so `tomtom-api-key`
        # leaves as `Tomtom-api-key`. Header names are case-insensitive per RFC
        # 9110 and every server framework folds them, so this is not worth
        # fighting -- but asserting on the exact case sent would be a lie.
        StubHandler.seen_headers = {k.lower(): v for k, v in self.headers.items()}
        if StubHandler.mode == "401":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"no bearer token provided")
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        payload = {"jsonrpc": "2.0", "id": body["id"]}
        if body["method"] == "initialize":
            if StubHandler.mode == "no-initialize":
                payload["error"] = {"code": -32601, "message": "not supported"}
            else:
                payload["result"] = {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "serverInfo": {"name": "stub-maps",
                                                    "title": "Stub Maps Server"}}
        elif body["method"] == "tools/list":
            payload["result"] = {"tools": [{"name": "geocode", "description": "Find a place.",
                                            "inputSchema": {"type": "object"}}]}
        else:
            payload["result"] = {"content": [{"type": "text", "text": "51.5,-0.1"}]}
        raw = json.dumps(payload)
        if StubHandler.mode == "sse":
            raw = f"event: message\ndata: {raw}\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(raw.encode())

    def do_GET(self):
        """Serves the config file, standing in for the Dropbox shared link."""
        # Lowercased: urllib title-cases outgoing header names, so `tomtom-api-key`
        # leaves as `Tomtom-api-key`. Header names are case-insensitive per RFC
        # 9110 and every server framework folds them, so this is not worth
        # fighting -- but asserting on the exact case sent would be a lie.
        StubHandler.seen_headers = {k.lower(): v for k, v in self.headers.items()}
        StubHandler.last_path = self.path
        if StubHandler.mode == "deadlink":
            # What Dropbox actually serves once the linked file is gone: 200, and
            # a web page.
            body = "<!DOCTYPE html><html><body>This link doesn't work</body></html>"
        else:
            body = json.dumps({"mcpServers": {"fetched": {
                "type": "http", "url": "http://x.invalid",
                "description": "came over the wire"}}})
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *a):
        pass


class TestHttp(Base):
    def setUp(self):
        super().setUp()
        StubHandler.mode = "json"
        StubHandler.seen_headers = {}
        self.httpd = HTTPServer(("127.0.0.1", 0), StubHandler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/mcp"

    def test_a_non_allowlisted_header_name_is_sent_verbatim(self):
        """The entire reason this path exists: claude.ai's connectors reject
        `tomtom-api-key` at save time, so it can only be sent from here."""
        self.write_config({"maps": {"type": "http", "url": self.url,
                                    "headers": {"tomtom-api-key": "${MAPS_KEY}"}}})
        r = self.run_lmcps("call", "maps", "geocode", '{"q": "London"}',
                           env={"MAPS_KEY": "abc123"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "51.5,-0.1")
        self.assertEqual(StubHandler.seen_headers.get("tomtom-api-key"), "abc123")

    def test_no_authorization_header_is_invented(self):
        """The ported code demanded an OAuth token whenever Authorization was
        absent, which killed exactly the header-auth servers this exists for."""
        self.write_config({"maps": {"type": "http", "url": self.url,
                                    "headers": {"tomtom-api-key": "k"}}})
        self.assertEqual(self.run_lmcps("call", "maps", "geocode", "{}").returncode, 0)
        self.assertNotIn("authorization", StubHandler.seen_headers)

    def test_streamable_http_alias(self):
        self.write_config({"maps": {"type": "streamable-http", "url": self.url}})
        self.assertEqual(self.run_lmcps("tools", "maps").returncode, 0)

    def test_sse_framed_response_is_unwrapped(self):
        StubHandler.mode = "sse"
        self.write_config({"maps": {"type": "http", "url": self.url}})
        r = self.run_lmcps("call", "maps", "geocode", "{}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "51.5,-0.1")

    def test_401_says_the_headers_were_rejected(self):
        StubHandler.mode = "401"
        self.write_config({"maps": {"type": "http", "url": self.url,
                                    "headers": {"tomtom-api-key": "wrong"}}})
        r = self.run_lmcps("call", "maps", "geocode", "{}")
        self.assertEqual(r.returncode, 1)
        self.assertIn("rejected the configured headers", r.stderr)

    def test_describe_over_http_asks_for_serverinfo(self):
        """The call path skips the handshake, but `describe` must not report a
        server as saying nothing about itself when it was never asked. TomTom
        answers a bare initialize with a serverInfo and no instructions."""
        self.write_config({"maps": {"type": "http", "url": self.url}})
        r = self.run_lmcps("describe", "maps")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Stub Maps Server", r.stdout)
        self.assertNotIn("no serverInfo", r.stdout)
        self.assertIn("geocode: Find a place.", r.stdout)

    def test_a_server_refusing_initialize_still_lists_tools(self):
        StubHandler.mode = "no-initialize"
        self.write_config({"maps": {"type": "http", "url": self.url}})
        r = self.run_lmcps("tools", "maps")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("geocode", r.stdout)

    def test_unreachable_url(self):
        self.write_config({"maps": {"type": "http", "url": "http://127.0.0.1:1/mcp"}})
        r = self.run_lmcps("call", "maps", "geocode", "{}")
        self.assertEqual(r.returncode, 1)
        self.assertIn("cannot reach 'maps'", r.stderr)


class TestConfigOverHttp(Base):
    """The shared-link path: fetch once, cache, and re-fetch only on `refresh`."""

    def setUp(self):
        super().setUp()
        self.httpd = HTTPServer(("127.0.0.1", 0), StubHandler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)
        self.skill_dir = self.tmp / "skill"
        (self.skill_dir / "bin").mkdir(parents=True)
        shutil.copy(LMCPS, self.skill_dir / "bin" / "lmcps.py")
        port = self.httpd.server_address[1]
        (self.skill_dir / "config-url.txt").write_text(
            f"# a Dropbox shared link\nhttp://127.0.0.1:{port}/config.json?rlkey=abc\n",
            encoding="utf-8")

    def run_linked(self, *args):
        e = dict(os.environ)
        e.pop("LMCPS_CONFIG", None)
        e["LMCPS_HOME"] = str(self.tmp / "home")
        return subprocess.run([sys.executable, str(self.skill_dir / "bin" / "lmcps.py"), *args],
                              capture_output=True, text=True, timeout=30, env=e)

    def test_fetches_caches_and_busts_the_cdn(self):
        r = self.run_linked("servers")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("came over the wire", r.stdout)
        self.assertIn("rlkey=abc", StubHandler.last_path)
        self.assertIn("dl=1", StubHandler.last_path)
        self.assertRegex(StubHandler.last_path, r"[?&]_=\d+")
        self.assertTrue((self.tmp / "home" / "config.json").is_file())

        # Second run must be served from the cache, so take the server away.
        self.httpd.shutdown()
        self.assertEqual(self.run_linked("servers").returncode, 0)

    def test_refresh_reports_what_it_found(self):
        r = self.run_linked("refresh")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("fetched", r.stdout)

    def test_a_link_whose_file_is_gone_says_so(self):
        """Dropbox answers 200 with a web page once the linked file is deleted,
        moved out, or unshared -- reported as "not valid JSON" until now."""
        StubHandler.mode = "deadlink"
        self.addCleanup(setattr, StubHandler, "mode", "json")
        r = self.run_linked("servers")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no longer resolves to a file", r.stderr)
        self.assertNotIn("not valid JSON", r.stderr)


def catalog(servers, built_at="2099-01-01T00:00:00Z"):
    return {"version": 1, "builtAt": built_at, "builtBy": "test", "servers": servers}


def indexed(*names):
    return {"builtAt": "2099-01-01T00:00:00Z", "error": None,
            "tools": [{"name": n, "description": f"does {n}"} for n in names]}


class TestCatalogDisplay(Base):
    """`servers` with an index. Every degradation case has to print something and
    none of them may fail: this runs at the top of every conversation."""

    def setUp(self):
        super().setUp()
        self.catalog_path = self.tmp / "catalog.json"
        self.write_config({"adder": fake_server(description="adds numbers")})

    def run_with(self, cat):
        self.catalog_path.write_text(json.dumps(cat) if isinstance(cat, dict) else cat,
                                     encoding="utf-8")
        return self.run_lmcps("servers", env={"LMCPS_CATALOG": str(self.catalog_path)})

    def test_tool_lines_are_printed_under_their_server(self):
        r = self.run_with(catalog({"adder": indexed("add", "boom")}))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"adder\s+stdio\s+adds numbers")
        self.assertIn("  add -- does add", r.stdout)
        self.assertIn("  boom -- does boom", r.stdout)

    def test_the_framing_says_parameters_are_not_known(self):
        r = self.run_with(catalog({"adder": indexed("add")}))
        self.assertIn("do not guess", r.stdout)
        self.assertIn("--schema", r.stdout)

    def test_a_missing_catalog_degrades_to_the_plain_listing(self):
        r = self.run_lmcps("servers", env={"LMCPS_CATALOG": str(self.tmp / "nope.json")})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"adder\s+stdio\s+adds numbers")
        self.assertIn("No tool index available", r.stdout)

    def test_a_corrupt_catalog_degrades_rather_than_failing(self):
        r = self.run_with("{not json at all")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"adder\s+stdio\s+adds numbers")
        self.assertIn("No tool index available", r.stdout)

    def test_a_server_the_config_dropped_is_not_printed(self):
        r = self.run_with(catalog({"adder": indexed("add"), "ghost": indexed("haunt")}))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("ghost", r.stdout)
        self.assertNotIn("haunt", r.stdout)

    def test_a_server_the_index_has_not_seen_says_so(self):
        r = self.run_with(catalog({}))
        self.assertIn("(tools not indexed -- lmcps tools adder)", r.stdout)

    def test_a_per_server_error_is_surfaced(self):
        r = self.run_with(catalog({"adder": {"tools": [], "error": "npx: not found"}}))
        self.assertIn("(index failed: npx: not found)", r.stdout)

    def test_a_stale_index_reports_the_routine_may_be_broken(self):
        old = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        r = self.run_with(catalog({"adder": indexed("add")}, built_at=old))
        self.assertIn("3d ago", r.stdout)
        self.assertIn("may be broken", r.stdout)

    def test_a_fresh_index_says_nothing_about_staleness(self):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        r = self.run_with(catalog({"adder": indexed("add")}, built_at=now))
        self.assertNotIn("may be broken", r.stdout)

    def test_blurbs_are_truncated_and_long_lists_are_capped(self):
        entry = {"builtAt": "2099-01-01T00:00:00Z", "error": None,
                 "tools": [{"name": f"t{i}", "description": "x " * 200} for i in range(45)]}
        r = self.run_with(catalog({"adder": entry}))
        self.assertIn("... and 5 more", r.stdout)
        longest = max((l for l in r.stdout.splitlines() if l.startswith("  t")), key=len)
        self.assertLessEqual(len(longest.split(" -- ", 1)[1]), 80)

    def test_servers_still_spawns_nothing_with_an_index(self):
        self.write_config({"adder": {"command": "definitely-not-a-real-binary",
                                     "description": "adds numbers"}})
        r = self.run_with(catalog({"adder": indexed("add")}))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("  add -- does add", r.stdout)


class TestIndexBuild(Base):
    """`lmcps index`: spawn, enumerate, merge, emit. No store, ever."""

    def build(self, *args, **kw):
        r = self.run_lmcps("index", *args, **kw)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def test_builds_names_and_blurbs_from_a_live_server(self):
        self.write_config({"adder": fake_server(description="adds numbers")})
        out = json.loads(self.build().stdout)
        self.assertEqual(out["version"], 1)
        names = [t["name"] for t in out["servers"]["adder"]["tools"]]
        self.assertIn("add", names)
        add = next(t for t in out["servers"]["adder"]["tools"] if t["name"] == "add")
        # Stored whole, including the second line that listings drop.
        self.assertEqual(add["description"],
                         "Add two numbers.\nSecond line ignored by listings.")

    def test_carries_no_schemas(self):
        self.write_config({"adder": fake_server(description="adds numbers")})
        out = json.loads(self.build().stdout)
        self.assertNotIn("inputSchema", json.dumps(out))

    def test_built_by_is_stamped_through(self):
        self.write_config({"adder": fake_server(description="adds numbers")})
        out = json.loads(self.build("--built-by", "refresh.py@deadbeef").stdout)
        self.assertEqual(out["builtBy"], "refresh.py@deadbeef")

    def test_a_timed_out_server_reports_what_it_said(self):
        """The child hits its own watchdog and exits, so this arrives as a
        non-zero return, not a TimeoutExpired -- and `why` is one line by
        contract. The server's own diagnostic has to reach the log some other
        way or a cron timeout stays unreadable."""
        self.write_config({"adder": fake_server("hang")})
        r = self.build("--per-server-timeout", "2")
        out = json.loads(r.stdout)
        self.assertIn("timed out after 2s", out["servers"]["adder"]["error"])
        self.assertIn("waiting on a lock held by nobody", r.stderr)

    def test_a_broken_server_does_not_stop_the_build(self):
        self.write_config({"adder": fake_server(description="adds numbers"),
                           "broken": {"command": "definitely-not-a-real-binary",
                                      "description": "nope"}})
        out = json.loads(self.build().stdout)
        self.assertTrue(out["servers"]["adder"]["tools"])
        self.assertTrue(out["servers"]["broken"]["error"])

    def test_a_failed_server_keeps_its_last_good_entry(self):
        self.write_config({"broken": {"command": "definitely-not-a-real-binary",
                                      "description": "nope"}})
        prev = self.tmp / "prev.json"
        prev.write_text(json.dumps(catalog({"broken": indexed("worked-before")})),
                        encoding="utf-8")
        out = json.loads(self.build("--previous", str(prev)).stdout)
        self.assertEqual([t["name"] for t in out["servers"]["broken"]["tools"]],
                         ["worked-before"])

    def test_a_server_that_left_the_config_is_dropped_from_the_merge(self):
        self.write_config({"adder": fake_server(description="adds numbers")})
        prev = self.tmp / "prev.json"
        prev.write_text(json.dumps(catalog({"adder": indexed("add"),
                                            "ghost": indexed("haunt")})),
                        encoding="utf-8")
        out = json.loads(self.build("--previous", str(prev)).stdout)
        self.assertNotIn("ghost", out["servers"])

    def test_out_writes_a_file_and_keeps_stdout_clean(self):
        self.write_config({"adder": fake_server(description="adds numbers")})
        dest = self.tmp / "catalog.json"
        r = self.build("--out", str(dest))
        self.assertEqual(r.stdout.strip(), "")
        self.assertTrue(json.loads(dest.read_text(encoding="utf-8"))["servers"])

    def test_tools_json_is_the_raw_entry(self):
        self.write_config({"adder": fake_server(description="adds numbers")})
        r = self.run_lmcps("tools", "adder", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        entry = json.loads(r.stdout)
        self.assertIn("add", [t["name"] for t in entry["tools"]])
        self.assertIn("inputSchema", entry["tools"][0])


class TestRefreshBuild(Base):
    """refresh.py's one piece of local logic. Everything else it does is Dropbox
    HTTP, which needs the network; this is the part a fixture can reach."""

    def setUp(self):
        super().setUp()
        spec = importlib.util.spec_from_file_location(
            "refresh", HERE / "routine" / "refresh.py")
        self.refresh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.refresh)

    def build(self, config, previous=None):
        return json.loads(self.refresh.build(
            str(LMCPS), json.dumps({"mcpServers": config}),
            json.dumps(previous) if previous else None, "refresh.py@test", self.tmp))

    def test_hands_the_config_to_index_and_takes_back_a_catalog(self):
        out = self.build({"adder": fake_server(description="adds numbers")})
        self.assertEqual(out["builtBy"], "refresh.py@test")
        self.assertIn("add", [t["name"] for t in out["servers"]["adder"]["tools"]])

    def test_previous_is_passed_through_for_the_merge(self):
        out = self.build({"broken": {"command": "definitely-not-a-real-binary"}},
                         previous=catalog({"broken": indexed("worked-before")}))
        self.assertEqual([t["name"] for t in out["servers"]["broken"]["tools"]],
                         ["worked-before"])

    def test_the_setup_script_reproduces_refresh_py_verbatim(self):
        """The heredoc must be quoted, or every $VAR in refresh.py is expanded at
        write time -- silent mangling that only shows up five hours later."""
        r = subprocess.run([sys.executable, str(HERE / "routine" / "make-setup-script.py")],
                           capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("<<'REFRESH_PY_EOF'", r.stdout)
        body = (HERE / "routine" / "refresh.py").read_text(encoding="utf-8")
        self.assertIn(body, r.stdout.replace("\r\n", "\n"))
        self.assertTrue(r.stdout.rstrip().endswith("exit 0"))


class TestPackaging(Base):
    """The zip is the artefact that actually runs, and it runs on Linux."""

    def setUp(self):
        super().setUp()
        self.skill = self.tmp / "skill"
        (self.skill / "bin").mkdir(parents=True)
        (self.skill / "references").mkdir(parents=True)
        for member in ("SKILL.md", "setup.sh", "bin/lmcps.py", "package.py",
                       "references/config.md"):
            shutil.copy(HERE / member, self.skill / member)
        # CRLF here too: config-url.txt is hand-pasted, so it routinely has it.
        (self.skill / "config-url.txt").write_bytes(
            CRLF.join([b"# a link", b"https://example.invalid/mcp.json?rlkey=abc", b""]))

    def test_crlf_is_normalised_on_the_way_into_the_zip(self):
        """A CRLF shebang makes bash reject the script outright. Windows text-mode
        writes reintroduce it silently, and .gitattributes only covers tracked
        files -- so the zip is the last place it can be caught."""
        sh = self.skill / "setup.sh"
        sh.write_bytes(sh.read_bytes().replace(LF, CRLF))
        r = subprocess.run([sys.executable, str(self.skill / "package.py")],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        with zipfile.ZipFile(self.skill / "local-mcps.zip") as zf:
            self.assertTrue(zf.namelist())
            for member in zf.namelist():
                self.assertNotIn(CRLF, zf.read(member), f"{member} has CRLF")

    def test_refuses_to_build_without_a_config_source(self):
        (self.skill / "config-url.txt").unlink()
        r = subprocess.run([sys.executable, str(self.skill / "package.py")],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 1)
        self.assertIn("No config source", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
