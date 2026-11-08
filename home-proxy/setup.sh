#!/bin/bash
# Puts `home-proxy` on PATH and starts it. Idempotent -- re-run it rather than
# working out whether it already ran.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CLI="$HERE/bin/home-proxy"

fail() {
  printf 'home-proxy is NOT ready: %s\n' "$1" >&2
  exit 1
}

[ -f "$CLI" ] || fail "bin/home-proxy not found -- the skill files are missing."
for tool in curl python3 sha256sum setsid; do
  command -v "$tool" >/dev/null 2>&1 || fail "$tool is not installed."
done

BIN_DIR=""
for dir in /usr/local/bin /usr/bin "$HOME/.local/bin"; do
  mkdir -p "$dir" 2>/dev/null || true
  if [ -w "$dir" ]; then BIN_DIR="$dir"; break; fi
done
[ -n "$BIN_DIR" ] || fail "no writable directory on PATH for the shim."

cat > "$BIN_DIR/home-proxy" <<SHIM_EOF
#!/bin/sh
# home-proxy shim -- installed by home-proxy/setup.sh, rewritten on every run.
exec bash "$CLI" "\$@"
SHIM_EOF
chmod +x "$BIN_DIR/home-proxy"

"$BIN_DIR/home-proxy" start || fail "the lines above say why."
