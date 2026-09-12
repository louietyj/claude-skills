#!/bin/bash
# Puts `dialer` on PATH and proves the consult queue is reachable. Idempotent --
# re-run it rather than working out whether it already ran.
#
# The sandbox reboots between assistant turns but keeps its disk for the length
# of a conversation, so one run covers every later turn. A new conversation gets
# a fresh disk and needs this again.
set -euo pipefail

find_dialer_py() {
  local candidate
  # Beside this script first -- that is where the CLI sits in an installed
  # skill, and it lets setup.sh run from a checkout. The rest are fallbacks.
  #
  # The `synced` entries are the Claude Code cloud box, where claude.ai skills
  # land under $HOME rather than /mnt/skills. Its path carries two UUIDs and
  # more than one bucket can exist, so the glob is sorted by mtime, newest wins.
  for candidate in \
    "$(cd "$(dirname "$0")" && pwd)/bin/dialer.py" \
    /mnt/skills/user/retell-dialer/bin/dialer.py \
    /mnt/skills/*/retell-dialer/bin/dialer.py \
    "$(ls -1dt "$HOME"/.claude/skills/synced/*/retell-dialer/bin/dialer.py \
       2>/dev/null | head -1)"
  do
    [ -n "$candidate" ] && [ -f "$candidate" ] && \
      { printf '%s' "$candidate"; return 0; }
  done
  candidate=$(find /mnt /opt /home "$HOME" -name dialer.py -path '*retell-dialer*' \
    2>/dev/null | head -1)
  [ -n "$candidate" ] && { printf '%s' "$candidate"; return 0; }
  return 1
}

DIALER_PY=$(find_dialer_py) || {
  echo "dialer.py not found -- the skill files are missing." >&2
  echo "Tell the user the skill is not installed. Do NOT try to place a call" >&2
  echo "another way: the consult loop is the only thing that makes a call" >&2
  echo "supervisable, and without it you are dispatching fire-and-forget." >&2
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

cat > "$BIN_DIR/dialer" <<SHIM_EOF
#!/bin/sh
# dialer shim -- installed by retell-dialer/setup.sh, rewritten on every run.
exec "$PY" "$DIALER_PY" "\$@"
SHIM_EOF
chmod +x "$BIN_DIR/dialer"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "warning: $BIN_DIR is not on PATH; run: export PATH=\"$BIN_DIR:\$PATH\"" >&2 ;;
esac

# Reach the queue now. A dead queue discovered mid-call means a receptionist on
# hold and no way to answer; discovered here it is just a message to the user.
if ! "$BIN_DIR/dialer" health; then
  echo "" >&2
  echo "dialer is on PATH but the consult queue is not answering." >&2
  echo "Do NOT place a call until this is fixed: consult_supervisor would" >&2
  echo "time out mid-conversation with someone on the line." >&2
  exit 1
fi

echo ""
echo "ready -- \`dialer\` is on PATH and the consult queue is up."
echo "Placing a call needs the user's explicit go-ahead, every time."
