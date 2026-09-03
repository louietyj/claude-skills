#!/usr/bin/env python3
"""Print the setup script for the `Cron` cloud environment. Paste the output
into Settings -> cloud environment -> Setup script.

    python routine/make-setup-script.py

The setup script only *writes* refresh.py to /usr/local/bin/lmcps-refresh; the
routine prompt runs it afterwards. It cannot run the job itself, because the
setup stage happens before Claude Code launches and therefore before the skills
it needs are synced onto the box.

Generated rather than checked in, so there is only one copy of refresh.py in the
repo. The paste in the settings textbox can still drift from the repo -- nothing
here can prevent that -- which is why refresh.py stamps a hash of itself into
the index for `lmcps servers` to surface.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REFRESH = HERE / "refresh.py"
EOF = "REFRESH_PY_EOF"

HEADER = f"""\
#!/bin/bash
# Writes the local-mcps index refresher to /usr/local/bin/lmcps-refresh.
#
# Verbatim paste of local-mcps/routine/refresh.py from louietyj/claude-skills,
# produced by local-mcps/routine/make-setup-script.py. Edit it there, not here.
#
# This script must stay infallible: a setup script that fails takes the whole
# session down with it, and this environment's actual job is refreshing a
# session limit. So it writes a file and nothing else -- no network, no
# conditionals, no `set -e`, and an explicit `exit 0`.
cat > /usr/local/bin/lmcps-refresh <<'{EOF}'
"""

FOOTER = f"""\
{EOF}
chmod +x /usr/local/bin/lmcps-refresh
exit 0
"""


def main():
    body = REFRESH.read_text(encoding="utf-8")
    # A quoted heredoc ends at the delimiter alone on a line. If refresh.py ever
    # contains that, the setup script silently truncates and the cron writes a
    # broken file that only fails five hours later.
    if any(line.strip() == EOF for line in body.splitlines()):
        raise SystemExit(f"refresh.py contains the heredoc delimiter {EOF}")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    sys.stdout.write(HEADER + body + FOOTER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
