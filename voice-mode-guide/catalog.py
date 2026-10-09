#!/usr/bin/env python3
"""Print what text-Claude can do, so voice-Claude knows what to hand off.

Kept terse: voice only routes, never runs, and the guide's output must stay
small enough to land in the transcript whole. Each list fails soft.
"""

import glob
import json
import os
import re
import subprocess
import sys

SKILLS_ROOT = os.environ.get("SKILLS_ROOT", "/mnt/skills")
SYNCED_ROOT = os.path.expanduser("~/.claude/skills/synced")
LMCPS_HOME = os.environ.get("LMCPS_HOME", os.path.expanduser("~/.lmcps"))
BLURB = 150
MAX_TOOL_NAMES = 30  # Alpha Vantage alone has 133


def parse_frontmatter(path):
    """Return (name, description) from a SKILL.md, tolerating YAML we can't parse.

    Hand-rolled rather than pyyaml: a missing import would cost the whole index.
    Handles the three forms seen in practice -- bare scalar, quoted scalar that
    may wrap across lines, and a block scalar introduced by > or |.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        return None, f"<unreadable: {exc}>"

    if not text.startswith("---"):
        return None, None
    end = text.find("\n---", 3)
    if end == -1:
        return None, None
    lines = text[3:end].splitlines()

    fields = {}
    key = None
    buf = []

    def flush():
        if key:
            fields[key] = " ".join(buf).strip()

    for line in lines:
        stripped = line.strip()
        # A new key starts only at column 0; indented lines are continuations,
        # which is what makes block scalars work.
        if line[:1] not in (" ", "\t") and ":" in stripped:
            flush()
            key, _, rest = stripped.partition(":")
            key = key.strip()
            buf = [rest.strip()]
        elif key:
            buf.append(stripped)
    flush()

    def clean(value):
        if value is None:
            return None
        value = value.strip()
        if value[:1] in (">", "|"):
            value = value[1:].lstrip("-+ ")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return " ".join(value.split())

    return clean(fields.get("name")), clean(fields.get("description"))


def blurb(text):
    """First sentence, capped. ASCII "..." to print through any encoding."""
    s = " ".join((text or "").split())
    # A capital must follow, or "e.g. ..." would end the sentence.
    s = re.split(r"(?<=[.!?])\s(?=[A-Z])", s, maxsplit=1)[0]
    return s if len(s) <= BLURB else s[:BLURB - 3] + "..."


def skills():
    # Every tier but examples/, which chat never announces either. The same
    # skill sits in several tiers (organization/, private/, user/ in a Cowork
    # container), so dedupe by name.
    paths = [p for p in sorted(glob.glob(os.path.join(SKILLS_ROOT, "*", "*", "SKILL.md")))
             if os.path.basename(os.path.dirname(os.path.dirname(p))) != "examples"]
    paths += sorted(glob.glob(os.path.join(SYNCED_ROOT, "*", "*", "SKILL.md")))
    seen = {}
    for path in paths:
        name, description = parse_frontmatter(path)
        name = name or os.path.basename(os.path.dirname(path))
        if name != "voice-mode-guide" and name not in seen:
            seen[name] = description
    return seen


def servers():
    with open(os.path.join(LMCPS_HOME, "config.json"), encoding="utf-8") as fh:
        configured = json.load(fh)["mcpServers"]
    # Cached by session-init's `lmcps servers`. Without it, no tool names.
    try:
        with open(os.path.join(LMCPS_HOME, "catalog.json"), encoding="utf-8") as fh:
            indexed = json.load(fh)["servers"]
    except (OSError, ValueError, KeyError):
        indexed = {}
    return {name: (cfg.get("description"),
                   [t.get("name", "") for t in (indexed.get(name) or {}).get("tools") or []])
            for name, cfg in configured.items()}


def memory_topics():
    out = subprocess.run(["cfs", "read", "/memory/INDEX.md"], capture_output=True,
                         text=True, timeout=60, check=True).stdout
    # `cfs read` numbers its lines: "    12\t- [Title](path) — hook".
    return re.findall(r"^\s*\d*\t?- \[([^\]]+)\]", out, re.M)


def section(title, fetch, render):
    print(f"\n#### {title}\n")
    try:
        render(fetch())
    except Exception as exc:  # noqa: BLE001
        print(f"(unavailable: {exc}. Text-Claude can still list these.)")


def render_entry(name, description):
    said = blurb(description)
    print(f"- {name} -- {said}" if said else f"- {name}")


def render_skills(entries):
    if not entries:
        print("(none)")
    for name in sorted(entries):
        render_entry(name, entries[name])


def render_servers(entries):
    for name in sorted(entries):
        description, tools = entries[name]
        render_entry(name, description)
        if tools:
            more = len(tools) - MAX_TOOL_NAMES
            tail = f" (+{more} more)" if more > 0 else ""
            print(f"  tools: {', '.join(tools[:MAX_TOOL_NAMES])}{tail}")


def render_topics(topics):
    print("Pointers in /memory/INDEX.md; the pages behind them hold the detail.\n")
    print("; ".join(topics) if topics else "(index is empty)")


def main():
    print("\n### What text-Claude can do\n")
    print("Not runnable by you. A request that matches a line below, or touches")
    print("the user's own information, goes through the handoff.")
    section("Skills", skills, render_skills)
    section("Local MCP servers (via `lmcps`)", servers, render_servers)
    section("Durable memory (via `cfs`)", memory_topics, render_topics)
    return 0


if __name__ == "__main__":
    sys.exit(main())
