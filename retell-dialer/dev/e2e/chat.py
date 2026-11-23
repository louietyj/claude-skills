"""Text rehearsal of the test copy's LLM through Retell's Chat API. Needs
`rt.py clone` first. See README.md.

    chat.py setup                      chat agent on the copy's LLM (id kept in test.json)
    chat.py run LINES.json VARS.json   one chat: each line sent as their turn, replies printed
    chat.py teardown                   delete the chat agent

Billed per agent message ($0.02 as of 2026-11). Consults reach the real queue
and hang until answered: answer them with the dialer, or keep scripts consult-free.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rt import STATE, api, state  # noqa: E402


def setup():
    s = state()
    out = api("/create-chat-agent", "POST", {
        "response_engine": {"type": "retell-llm", "llm_id": s["llm_id"]},
        "agent_name": "Retell Dialer TEST chat (delete me)"})
    s["chat_agent_id"] = out["agent_id"]
    json.dump(s, open(STATE, "w"))
    print(s)


def run(lines_path, vars_path):
    variables = json.load(open(vars_path))
    chat = api("/create-chat", "POST", {"agent_id": state()["chat_agent_id"],
                                       "retell_llm_dynamic_variables": variables})
    cid = chat["chat_id"]
    for line in json.load(open(lines_path)):
        print(f"them: {line}")
        out = api("/create-chat-completion", "POST", {"chat_id": cid, "content": line})
        msgs = out.get("messages") or []
        if not msgs:
            print("  agent: (no message)")
        for m in msgs:
            if m.get("role") == "agent":
                print(f"  agent: {m.get('content')!r}")
            else:
                print(f"  [{m.get('role')}] {json.dumps({k: v for k, v in m.items() if k not in ('message_id', 'created_timestamp')})[:200]}")
    api(f"/end-chat/{cid}", "PATCH")
    print("chat", cid)


def teardown():
    s = state()
    if s.get("chat_agent_id"):
        api(f"/delete-chat-agent/{s.pop('chat_agent_id')}", "DELETE")
        json.dump(s, open(STATE, "w"))
    print("chat agent deleted")


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:]
    {"setup": setup, "run": lambda: run(*rest), "teardown": teardown}[cmd]()
