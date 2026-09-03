#!/bin/bash
# Puts `lmcps` on PATH and prints the configured servers. Idempotent -- re-run it
# rather than working out whether it already ran.
#
# The sandbox reboots between assistant turns but keeps its disk for the length
# of a conversation, so one run covers every later turn. A new conversation gets
# a fresh disk and needs this again.
set -euo pipefail

find_lmcps_py() {
  local candidate
  # Beside this script first -- that is where the CLI sits in an installed
  # skill, and it lets setup.sh run from a checkout. The rest are fallbacks.
  #
  # The `synced` entries are the Claude Code cloud box, where claude.ai skills
  # land under $HOME (i.e. /root) rather than /mnt/skills. Its path carries two
  # UUIDs and more than one bucket can exist, so the glob is sorted by mtime
  # and the newest wins -- never whatever order the shell happens to return.
  for candidate in \
    "$(cd "$(dirname "$0")" && pwd)/bin/lmcps.py" \
    /mnt/skills/user/local-mcps/bin/lmcps.py \
    /mnt/skills/*/local-mcps/bin/lmcps.py \
    "$(ls -1dt "$HOME"/.claude/skills/synced/*/local-mcps/bin/lmcps.py \
       2>/dev/null | head -1)"
  do
    [ -n "$candidate" ] && [ -f "$candidate" ] && \
      { printf '%s' "$candidate"; return 0; }
  done
  candidate=$(find /mnt /opt /home "$HOME" -name lmcps.py -path '*local-mcps*' \
    2>/dev/null | head -1)
  [ -n "$candidate" ] && { printf '%s' "$candidate"; return 0; }
  return 1
}

LMCPS_PY=$(find_lmcps_py) || {
  echo "lmcps.py not found -- the skill files are missing." >&2
  echo "Tell the user the skill is not installed. Do NOT try to reach these" >&2
  echo "servers another way: stdio servers have no URL, and the HTTP ones use" >&2
  echo "header names claude.ai's connectors refuse to send." >&2
  exit 1
}

BIN_DIR=""
for dir in /usr/local/bin /usr/bin "$HOME/.local/bin"; do
  mkdir -p "$dir" 2>/dev/null || true
  if [ -w "$dir" ]; then BIN_DIR="$dir"; break; fi
done
[ -n "$BIN_DIR" ] || { echo "No writable directory on PATH for the shim." >&2; exit 1; }

# Bake in an absolute interpreter path, so a later PATH change cannot break the
# shim. Each candidate is run, not just resolved: a name on PATH can be a stub
# that resolves fine and fails the moment it is called.
PY=""
for name in python3 python; do
  candidate=$(command -v "$name" 2>/dev/null) || continue
  if "$candidate" -c 'import sys' >/dev/null 2>&1; then PY="$candidate"; break; fi
done
[ -n "$PY" ] || { echo "No working python interpreter found." >&2; exit 1; }

cat > "$BIN_DIR/lmcps" <<SHIM_EOF
#!/bin/sh
# lmcps shim -- installed by local-mcps/setup.sh, rewritten on every run.
exec "$PY" "$LMCPS_PY" "\$@"
SHIM_EOF
chmod +x "$BIN_DIR/lmcps"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "warning: $BIN_DIR is not on PATH; run: export PATH=\"$BIN_DIR:\$PATH\"" >&2 ;;
esac

# Fetching now surfaces a network failure here rather than mid-task, and the
# listing is the point of running this -- so print it rather than swallowing it.
echo "ready -- \`lmcps\` is on PATH."
echo ""
if ! "$BIN_DIR/lmcps" servers; then
  echo "" >&2
  echo "lmcps is installed at $BIN_DIR/lmcps but could not read its config." >&2
  exit 1
fi
