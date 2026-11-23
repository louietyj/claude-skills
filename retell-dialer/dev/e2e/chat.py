"""Text rehearsal of the test copy's LLM through Retell's Chat API. Needs
`rt.py clone` first. See README.md.

    chat.py setup                      chat agent on the copy's LLM (id kept in test.json)
    chat.py run LINES.json VARS.json   one chat: each line sent as their turn, replies printed
    chat.py start VARS.json            or turn by turn: start a chat,
    chat.py say "their line"           send one turn and print the reply,
    chat.py end                        and end it
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


def new_chat(vars_path):
    return api("/create-chat", "POST", {"agent_id": state()["chat_agent_id"],
                                       "retell_llm_dynamic_variables": json.load(open(vars_path))})["chat_id"]


def turn(cid, line):
    print(f"them: {line}")
    # A consult holds the completion open until it's answered (dialer answer, from another shell).
    msgs = api("/create-chat-completion", "POST", {"chat_id": cid, "content": line},
               timeout=200).get("messages") or []
    if not msgs:
        print("  agent: (nothing: NO_RESPONSE_NEEDED)")
    for m in msgs:
        if m.get("role") == "agent":
            print(f"  agent: {m.get('content')!r}")
        elif m.get("role") == "tool_call_invocation":
            print(f"  [{m.get('name')}] {m.get('arguments')}")


def run(lines_path, vars_path):
    cid = new_chat(vars_path)
    for line in json.load(open(lines_path)):
        turn(cid, line)
    api(f"/end-chat/{cid}", "PATCH")
    print("chat", cid)


def start(vars_path):
    """Turn by turn: start, then one `say` per line, reacting to each reply."""
    s = state()
    s["chat_id"] = new_chat(vars_path)
    json.dump(s, open(STATE, "w"))
    print("chat", s["chat_id"])


def say(line):
    turn(state()["chat_id"], line)


def end():
    s = state()
    api(f"/end-chat/{s.pop('chat_id')}", "PATCH")
    json.dump(s, open(STATE, "w"))
    print("ended")


def teardown():
    s = state()
    if s.get("chat_agent_id"):
        api(f"/delete-chat-agent/{s.pop('chat_agent_id')}", "DELETE")
        json.dump(s, open(STATE, "w"))
    print("chat agent deleted")


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:]
    {"setup": setup, "run": lambda: run(*rest), "start": lambda: start(*rest),
     "say": lambda: say(" ".join(rest)), "end": end, "teardown": teardown}[cmd]()
