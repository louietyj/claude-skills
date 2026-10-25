#!/bin/bash
# Puts `commute` on PATH. Idempotent -- re-run it rather than working out
# whether it already ran.
#
# The sandbox keeps its disk for the length of a conversation, so one run covers
# every later turn. A new conversation gets a fresh disk and needs this again.
set -uo pipefail

find_cli() {
  local candidate
  # Beside this script first -- where it sits in an installed skill, and what
  # lets setup.sh run from a checkout. The rest are fallbacks. The `synced`
  # entry is Cowork and the Claude Code cloud box, where skills land under $HOME
  # and there is no /mnt/skills at all; its path carries a bucket id, and more
  # than one bucket can exist, so the glob is sorted by mtime and newest wins.
  for candidate in \
    "$(cd "$(dirname "$0")" && pwd)/bin/commute.py" \
    /mnt/skills/user/calendar-commute/bin/commute.py \
    /mnt/skills/*/calendar-commute/bin/commute.py \
    "$(ls -1dt "$HOME"/.claude/skills/synced/*/calendar-commute/bin/commute.py \
       2>/dev/null | head -1)"
  do
    [ -n "$candidate" ] && [ -f "$candidate" ] && { printf '%s' "$candidate"; return 0; }
  done
  candidate=$(find /mnt /opt /home "$HOME" -name commute.py -path '*calendar-commute*' \
    2>/dev/null | head -1)
  [ -n "$candidate" ] && { printf '%s' "$candidate"; return 0; }
  return 1
}

fail() {
  printf 'calendar-commute is NOT ready: %s\n' "$1" >&2
  printf 'Tell Louie which step failed. Do not estimate drive times or round them\n' >&2
  printf 'by hand instead -- that is what this script exists to stop.\n' >&2
  exit 1
}

CLI=$(find_cli) || fail "commute.py not found -- the skill files are missing."

# Bake in an absolute interpreter path, so a later PATH change cannot break the
# shim. Run it rather than just resolving it: a name on PATH can be a stub.
PY=""
for name in python3 python; do
  candidate=$(command -v "$name" 2>/dev/null) || continue
  if "$candidate" -c 'import sys' >/dev/null 2>&1; then PY="$candidate"; break; fi
done
[ -n "$PY" ] || fail "no working python interpreter found."

BIN_DIR=""
for dir in /usr/local/bin /usr/bin "$HOME/.local/bin"; do
  mkdir -p "$dir" 2>/dev/null || true
  if [ -w "$dir" ]; then BIN_DIR="$dir"; break; fi
done
[ -n "$BIN_DIR" ] || fail "no writable directory on PATH for the shim."

cat > "$BIN_DIR/commute" <<SHIM_EOF
#!/bin/sh
# commute shim -- installed by calendar-commute/setup.sh, rewritten on every run.
exec "$PY" "$CLI" "\$@"
SHIM_EOF
chmod +x "$BIN_DIR/commute"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "warning: $BIN_DIR is not on PATH; run: export PATH=\"$BIN_DIR:\$PATH\"" >&2 ;;
esac

"$BIN_DIR/commute" --help >/dev/null 2>&1 || fail "the shim does not run (python $PY, cli $CLI)."

# The Maps key lives in local-mcps' config, fetched by session-init.
[ -n "${COMMUTE_MAPS_KEY:-}" ] || [ -f "${LMCPS_HOME:-$HOME/.lmcps}/config.json" ] \
  || fail "no local-mcps config (it holds the Maps key). Run session-init first."

echo "ready -- \`commute\` is on PATH. Next: commute drive --from ... --to ... --arrive ..."
