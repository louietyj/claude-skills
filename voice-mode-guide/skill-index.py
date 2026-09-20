#!/usr/bin/env python3
"""Reconstruct the `<available_skills>` block from disk.

Voice drops that block, and a bare filename is useless as a trigger -- "hostc"
conveys nothing about when to reach for it. The descriptions are in each
SKILL.md's frontmatter, on the disk both modes share.

Tiers are the whole selection rule. Measured against a chat session's actual
block: it is exactly public/ + user/ + plugins/, omitting examples/ wholesale
(44 on disk, none announced). Excluded here too -- announcing a skill in voice
that chat would never surface is a divergence, not a fix.
"""

import glob
import os
import sys
import textwrap

TIERS = ("public", "user", "plugins")
ROOT = os.environ.get("SKILLS_ROOT", "/mnt/skills")
WIDTH = 78


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


def count(n):
    return f"{n} skill" if n == 1 else f"{n} skills"


def main():
    total = 0
    for tier in TIERS:
        paths = sorted(glob.glob(os.path.join(ROOT, tier, "*", "SKILL.md")))
        print(f"\n──── {tier}/ ──── {count(len(paths))}\n")
        if not paths:
            print("  (none installed)")
            continue
        for path in paths:
            directory = os.path.basename(os.path.dirname(path))
            name, description = parse_frontmatter(path)
            print(f"  {name or directory}")
            print(f"    {path}")
            if description:
                print(textwrap.fill(description, WIDTH,
                                    initial_indent="    ", subsequent_indent="    "))
            else:
                print("    <no description in frontmatter -- read the file to find out>")
            print()
            total += 1

    skipped = len(glob.glob(os.path.join(ROOT, "examples", "*", "SKILL.md")))
    print(f"──── {count(total)} listed ────")
    if skipped:
        # Said rather than hidden: they are runnable, and a task that genuinely
        # needs one shouldn't be blocked by a silent filter.
        verb = "are" if skipped != 1 else "is"
        print(f"\n{skipped} more under {ROOT}/examples/ {verb} omitted, because chat does")
        print("not announce them either. They are on disk and readable if a task")
        print(f"ever calls for one: ls {ROOT}/examples/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
