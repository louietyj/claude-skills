#!/bin/bash
# Puts `home-exit` on PATH and starts it. Idempotent -- re-run it rather than
# working out whether it already ran.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CLI="$HERE/bin/home-exit"

fail() {
  printf 'home-exit is NOT ready: %s\n' "$1" >&2
  exit 1
}

[ -f "$CLI" ] || fail "bin/home-exit not found -- the skill files are missing."
for tool in curl python3 sha256sum tar setsid; do
  command -v "$tool" >/dev/null 2>&1 || fail "$tool is not installed."
done

BIN_DIR=""
for dir in /usr/local/bin /usr/bin "$HOME/.local/bin"; do
  mkdir -p "$dir" 2>/dev/null || true
  if [ -w "$dir" ]; then BIN_DIR="$dir"; break; fi
done
[ -n "$BIN_DIR" ] || fail "no writable directory on PATH for the shim."

cat > "$BIN_DIR/home-exit" <<SHIM_EOF
#!/bin/sh
# home-exit shim -- installed by home-exit/setup.sh, rewritten on every run.
exec bash "$CLI" "\$@"
SHIM_EOF
chmod +x "$BIN_DIR/home-exit"

"$BIN_DIR/home-exit" start || fail "the lines above say why."
