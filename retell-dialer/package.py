#!/usr/bin/env python3
"""Build the uploadable skill zip.

The zip embeds config.json, which holds the Retell API key (places calls, spends
credit), the consult token (writes words into the mouth of an agent on a live
call) and the worker URL (whose /consult is unauthenticated). The artefact is a
credential: anyone holding it can phone people as Louie.
"""

import json
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "retell-dialer"

# dev/ and worker/ are deliberately absent. A browser call needs the user's own
# microphone, which the sandbox cannot reach, and the Worker is deployed from a
# checkout with wrangler, never from inside a conversation.
MEMBERS = [
    "SKILL.md",
    "setup.sh",
    "bin/dialer.py",
    "bin/ws.py",
    "references/brief.md",
    "references/provisioning.md",
    "config.json",
]

REQUIRED_CONFIG = ("retell_api_key", "agent_id", "llm_id", "worker_url", "consult_token")


def check_config():
    path = os.path.join(HERE, "config.json")
    if not os.path.exists(path):
        raise SystemExit("No config.json. Copy config.example.json and fill it in.")
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    missing = [k for k in REQUIRED_CONFIG if not cfg.get(k)]
    if missing:
        raise SystemExit(f"config.json is missing: {', '.join(missing)}")
    if not cfg.get("from_number"):
        print("warning: from_number is empty -- the packaged skill cannot place "
              "PSTN calls, only fail at dispatch.", file=sys.stderr)
    return cfg


def main() -> int:
    cfg = check_config()
    out = os.path.join(HERE, f"{NAME}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for member in MEMBERS:
            src = os.path.join(HERE, member)
            if not os.path.exists(src):
                raise SystemExit(f"Missing {member}")
            # Normalise to LF. The skill runs in a Linux sandbox, where a CRLF
            # shebang makes bash reject the script -- and every tool on a
            # Windows checkout is one text-mode write away from reintroducing
            # it. .gitattributes only covers tracked files, and config.json is
            # untracked, so the zip is the last place this can be guaranteed.
            with open(src, "rb") as fh:
                zf.writestr(f"{NAME}/{member}", fh.read().replace(b"\r\n", b"\n"))

    print(f"Wrote {out}")
    print(f"Contains: {', '.join(MEMBERS)}")
    print(f"Points at: {cfg['worker_url']} / {cfg['agent_id']}")
    print("Embeds a live API key and consult token -- do not commit or share it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
