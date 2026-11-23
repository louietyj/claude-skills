"""Throwaway copy of the dialer agent for E2E tests. See README.md here.

    rt.py clone             copy the live LLM + agent; ids saved to test.json
    rt.py draft             push agent/agent.json's prompt and tools to the copy
    rt.py patch FILE.json   update-retell-llm on the copy with FILE's fields
    rt.py vars FILE.json    set the copy's default dynamic variables
    rt.py bind | unbind     inbound agent on the number = the copy | nothing
    rt.py show              the copy's tools and variables
    rt.py teardown          unbind, delete the copy
"""
import glob
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(os.path.dirname(HERE))
# The checkout's config.json if it has one, else the installed skill's.
CFG = json.load(open(([os.path.join(SKILL, "config.json")] if os.path.exists(os.path.join(SKILL, "config.json"))
                      else sorted(glob.glob(os.path.expanduser("~/.claude/skills/synced/*/retell-dialer/config.json"))))[0]))
STATE = os.path.join(HERE, "test.json")
NUMBER = CFG["from_number"]


def api(path, method="GET", body=None):
    req = urllib.request.Request(
        f"https://api.retellai.com{path}", method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {CFG['retell_api_key']}", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} -> {e.code}: {e.read().decode()[:500]}")


def state():
    return json.load(open(STATE))


def clone():
    llm = api(f"/get-retell-llm/{CFG['llm_id']}")
    for k in ("llm_id", "version", "is_published", "last_modification_timestamp", "model"):
        llm.pop(k, None)
    new_llm = api("/create-retell-llm", "POST", llm)
    agent = api(f"/get-agent/{CFG['agent_id']}")
    for k in ("agent_id", "version", "is_published", "last_modification_timestamp"):
        agent.pop(k, None)
    agent["agent_name"] = "Retell Dialer TEST (delete me)"
    agent["response_engine"] = {"type": "retell-llm", "llm_id": new_llm["llm_id"]}
    new_agent = api("/create-agent", "POST", agent)
    json.dump({"llm_id": new_llm["llm_id"], "agent_id": new_agent["agent_id"]}, open(STATE, "w"))
    print(json.load(open(STATE)))


def patch(path):
    print(json.dumps(api(f"/update-retell-llm/{state()['llm_id']}", "PATCH", json.load(open(path))))[:300])


def draft():
    """The checkout's agent.json (run the inline step of `dialer agent-push`
    first if prompt.md changed) onto the copy, never the live agent."""
    with open(os.path.join(SKILL, "agent", "agent.json"), encoding="utf-8") as fh:
        llm = json.loads(fh.read().replace("{{WORKER_URL}}", CFG["worker_url"]))["retell_llm"]
    api(f"/update-retell-llm/{state()['llm_id']}", "PATCH",
        {k: llm[k] for k in ("general_prompt", "general_tools")})
    print("copy now runs the checkout's prompt and tools")


def set_vars(path):
    print(json.dumps(api(f"/update-retell-llm/{state()['llm_id']}", "PATCH",
                         {"default_dynamic_variables": json.load(open(path))}))[:300])


def bind(on):
    agents = [{"agent_id": state()["agent_id"], "weight": 1}] if on else []
    out = api(f"/update-phone-number/{NUMBER}", "PATCH", {"inbound_agents": agents})
    print({k: out.get(k) for k in ("inbound_agents", "outbound_agents")})


def show():
    llm = api(f"/get-retell-llm/{state()['llm_id']}")
    print(json.dumps({"tools": llm["general_tools"], "vars": llm.get("default_dynamic_variables")}, indent=1))


def teardown():
    bind(False)
    s = state()
    if s.get("chat_agent_id"):
        api(f"/delete-chat-agent/{s['chat_agent_id']}", "DELETE")
    api(f"/delete-agent/{s['agent_id']}", "DELETE")
    api(f"/delete-retell-llm/{s['llm_id']}", "DELETE")
    os.remove(STATE)
    print("deleted", s)


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:]
    {"clone": clone, "draft": draft, "patch": lambda: patch(rest[0]), "vars": lambda: set_vars(rest[0]),
     "bind": lambda: bind(True), "unbind": lambda: bind(False), "show": show,
     "teardown": teardown}[cmd]()
