#!/bin/bash
# Puts `unsub` on PATH. Idempotent -- re-run it rather than working out whether
# it already ran. A new conversation gets a fresh disk and needs this again.
set -uo pipefail

find_cli() {
  local candidate
  # Beside this script first (an installed skill, or a checkout). The `synced`
  # entry is Cowork and the cloud Claude Code box, where skills land under $HOME
  # with a bucket id in the path; newest bucket wins.
  for candidate in \
    "$(cd "$(dirname "$0")" && pwd)/bin/unsub.py" \
    /mnt/skills/user/gmail-unsub/bin/unsub.py \
    /mnt/skills/*/gmail-unsub/bin/unsub.py \
    "$(ls -1dt "$HOME"/.claude/skills/synced/*/gmail-unsub/bin/unsub.py 2>/dev/null | head -1)"
  do
    [ -n "$candidate" ] && [ -f "$candidate" ] && { printf '%s' "$candidate"; return 0; }
  done
  return 1
}

fail() {
  printf 'gmail-unsub is NOT ready: %s\n' "$1" >&2
  printf 'Tell Louie which step failed. Do not unsubscribe by hand instead.\n' >&2
  exit 1
}

CLI=$(find_cli) || fail "unsub.py not found -- the skill files are missing."

PY=""
for name in python3 python; do
  candidate=$(command -v "$name" 2>/dev/null) || continue
  if "$candidate" -c 'import sys; assert sys.version_info >= (3, 10)' >/dev/null 2>&1; then PY="$candidate"; break; fi
done
[ -n "$PY" ] || fail "no python 3.10+ interpreter found."

BIN_DIR=""
for dir in /usr/local/bin /usr/bin "$HOME/.local/bin"; do
  mkdir -p "$dir" 2>/dev/null || true
  if [ -w "$dir" ]; then BIN_DIR="$dir"; break; fi
done
[ -n "$BIN_DIR" ] || fail "no writable directory on PATH for the shim."

cat > "$BIN_DIR/unsub" <<SHIM_EOF
#!/bin/sh
# unsub shim -- installed by gmail-unsub/setup.sh, rewritten on every run.
exec "$PY" "$CLI" "\$@"
SHIM_EOF
chmod +x "$BIN_DIR/unsub"

"$BIN_DIR/unsub" --help >/dev/null 2>&1 || fail "the shim does not run (python $PY, cli $CLI)."

command -v cfs >/dev/null 2>&1 || fail "cfs is not on PATH. Run session-init (durable-filesystem) first."
found=""
for f in /mnt/skills/*/bash-mcp/mcp_call.py "$HOME"/.claude/skills/synced/*/bash-mcp/mcp_call.py \
  "$HOME"/.claude/skills/bash-mcp/mcp_call.py; do
  [ -f "$f" ] && { found=1; break; }
done
[ -n "$found" ] || fail "the bash-mcp skill is not installed; unsub reads Gmail through it."

echo "ready -- \`unsub\` is on PATH. Next: unsub state pull"
