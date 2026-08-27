#!/bin/bash
# Puts `cfs` on PATH. Idempotent -- re-run it rather than working out whether
# it already ran.
#
# The sandbox reboots between assistant turns but keeps its disk for the length
# of a conversation, so one run covers every later turn. A new conversation gets
# a fresh disk and needs this again.
set -euo pipefail

find_cfs_py() {
  local candidate
  for candidate in \
    /mnt/skills/user/durable-filesystem/bin/cfs.py \
    /mnt/skills/*/durable-filesystem/bin/cfs.py
  do
    [ -f "$candidate" ] && { printf '%s' "$candidate"; return 0; }
  done
  candidate=$(find /mnt /opt /home -name cfs.py -path '*durable-filesystem*' \
    2>/dev/null | head -1)
  [ -n "$candidate" ] && { printf '%s' "$candidate"; return 0; }
  return 1
}

CFS_PY=$(find_cfs_py) || {
  echo "cfs.py not found -- the skill files are missing." >&2
  echo "Do NOT fall back to local files or the Dropbox connector: local files" >&2
  echo "do not persist, and connector writes raise a dialog the user must" >&2
  echo "answer. Tell them the skill is not installed." >&2
  exit 1
}

# Must land on PATH; the whole point is that callers type `cfs` and nothing else.
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

cat > "$BIN_DIR/cfs" <<SHIM_EOF
#!/bin/sh
# cfs shim -- installed by durable-filesystem/setup.sh, rewritten on every run.
exec "$PY" "$CFS_PY" "\$@"
SHIM_EOF
chmod +x "$BIN_DIR/cfs"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "warning: $BIN_DIR is not on PATH; run: export PATH=\"$BIN_DIR:\$PATH\"" >&2 ;;
esac

# Prove the whole path works -- shim, interpreter, credentials, network. A
# failure here is worth hitting now rather than inside the first real command.
if ! "$BIN_DIR/cfs" list / >/dev/null 2>&1; then
  echo "cfs is installed at $BIN_DIR/cfs but cannot reach the store:" >&2
  "$BIN_DIR/cfs" list / >&2 || true
  exit 1
fi

echo "ready -- \`cfs\` is on PATH and the store is reachable."
echo "Use it directly: cfs read /memory/INDEX.md"
