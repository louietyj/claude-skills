#!/usr/bin/env python3
"""A stdio MCP server for the test suite, with switchable misbehaviour.

    fake_mcp_server.py [mode]

    plain    well-behaved
    noisy    interleaves non-JSON log lines into stdout, as real servers do
    roots    sends a roots/list REQUEST and blocks until the client answers it
    sampling sends an unsupported request and continues once it is declined
    instructions advertises a serverInfo title and its own `instructions`
    crash    writes a diagnostic to stderr and exits without a handshake
    echoenv  tools/call returns the value of $FAKE_TOKEN
"""
import json
import os
import sys

MODE = sys.argv[1] if len(sys.argv) > 1 else "plain"

TOOLS = [
    {"name": "add", "description": "Add two numbers.\nSecond line ignored by listings.",
     "inputSchema": {"type": "object", "properties": {"a": {"type": "number"},
                                                      "b": {"type": "number"}}}},
    {"name": "boom", "description": "Always fails.", "inputSchema": {"type": "object"}},
]


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def log(text):
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


PENDING = []


def read():
    """Next JSON message from the client, deferred ones first."""
    return PENDING.pop(0) if PENDING else read_stdin()


def read_stdin():
    """Next JSON message off the wire, skipping anything unparseable."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if os.environ.get("FAKE_LOG"):
            with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return msg
    return None


def ask(request):
    """Send a server->client request and block for its response, which is what
    makes an unanswered request a hang rather than a slow path.

    Anything else the client sends meanwhile is deferred, not dropped -- the
    client is entitled to pipeline its next call while we wait, and a real
    server would not lose it. Deferring happens off to the side and lands in
    PENDING only on the way out; pushing straight onto PENDING would have this
    loop read its own deferrals back forever."""
    out(request)
    deferred = []
    while True:
        msg = read_stdin()
        if msg is None:
            sys.exit("client closed input without answering")
        if msg.get("id") == request["id"]:
            PENDING.extend(deferred)
            return msg
        deferred.append(msg)


def result(req_id, payload):
    return {"jsonrpc": "2.0", "id": req_id, "result": payload}


def main():
    if MODE == "crash":
        print("fake: FAKE_TOKEN is not set, refusing to start", file=sys.stderr)
        sys.exit(2)

    if MODE == "noisy":
        log("[info] starting up")
        log("not json at all")

    while True:
        msg = read()
        if msg is None:
            return
        method, rid = msg.get("method"), msg.get("id")

        if method == "initialize":
            info = {"protocolVersion": "2025-06-18", "capabilities": {},
                    "serverInfo": {"name": "fake", "version": "0"}}
            if MODE == "instructions":
                info["serverInfo"]["title"] = "Fake Adder"
                info["instructions"] = "Prefer `add` over doing arithmetic yourself."
            out(result(rid, info))
            if MODE == "roots":
                ask({"jsonrpc": "2.0", "id": "srv-1", "method": "roots/list", "params": {}})
            elif MODE == "sampling":
                ask({"jsonrpc": "2.0", "id": "srv-1", "method": "sampling/createMessage",
                     "params": {}})
        elif method == "notifications/initialized":
            if MODE == "noisy":
                log("[info] client ready")
        elif method == "tools/list":
            if MODE == "noisy":
                log("[debug] enumerating tools")
            out(result(rid, {"tools": TOOLS}))
        elif method == "tools/call":
            name = msg.get("params", {}).get("name")
            args = msg.get("params", {}).get("arguments", {})
            if MODE == "echoenv":
                out(result(rid, {"content": [{"type": "text",
                                              "text": os.environ.get("FAKE_TOKEN", "<unset>")}]}))
            elif name == "boom":
                out(result(rid, {"content": [{"type": "text", "text": "it went boom"}],
                                 "isError": True}))
            elif name == "add":
                total = args.get("a", 0) + args.get("b", 0)
                out(result(rid, {"content": [{"type": "text", "text": str(total)}]}))
            else:
                out({"jsonrpc": "2.0", "id": rid,
                     "error": {"code": -32602, "message": f"unknown tool {name}"}})
        elif rid is not None:
            out({"jsonrpc": "2.0", "id": rid,
                 "error": {"code": -32601, "message": "not implemented"}})


if __name__ == "__main__":
    main()
