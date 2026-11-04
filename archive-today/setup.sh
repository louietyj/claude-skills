#!/bin/bash
# Puts `archive-today` on PATH and installs trafilatura. Idempotent -- re-run it
# rather than working out whether it already ran.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CLI="$HERE/bin/archive_today.py"

fail() {
  printf 'archive-today is NOT ready: %s\n' "$1" >&2
  printf 'Tell Louie which step failed. Do not drive archive.today by hand instead.\n' >&2
  exit 1
}

[ -f "$CLI" ] || fail "bin/archive_today.py not found -- the skill files are missing."
[ -s "$HERE/capsolver.key" ] || [ -s "$HERE/2captcha.key" ] || [ -n "${CAPSOLVER_API_KEY:-}${TWOCAPTCHA_API_KEY:-}" ] \
  || fail "no capsolver.key or 2captcha.key in the skill -- it was packaged without solver keys."

PY=""
for name in python3 python; do
  candidate=$(command -v "$name" 2>/dev/null) || continue
  if "$candidate" -c 'import sys; assert sys.version_info >= (3, 9)' >/dev/null 2>&1; then PY="$candidate"; break; fi
done
[ -n "$PY" ] || fail "no python 3.9+ interpreter found."

# PySocks carries --tor and --proxy socks5h://.
if ! "$PY" -c 'import trafilatura, socks' >/dev/null 2>&1; then
  "$PY" -m pip install -q --break-system-packages trafilatura PySocks >/dev/null 2>&1 \
    || "$PY" -m pip install -q trafilatura PySocks >/dev/null 2>&1 \
    || fail "pip install trafilatura PySocks failed."
fi

BIN_DIR=""
for dir in /usr/local/bin /usr/bin "$HOME/.local/bin"; do
  mkdir -p "$dir" 2>/dev/null || true
  if [ -w "$dir" ]; then BIN_DIR="$dir"; break; fi
done
[ -n "$BIN_DIR" ] || fail "no writable directory on PATH for the shim."

cat > "$BIN_DIR/archive-today" <<SHIM_EOF
#!/bin/sh
# archive-today shim -- installed by archive-today/setup.sh, rewritten on every run.
exec "$PY" "$CLI" "\$@"
SHIM_EOF
chmod +x "$BIN_DIR/archive-today"
"$BIN_DIR/archive-today" --help >/dev/null 2>&1 || fail "the shim does not run (python $PY, cli $CLI)."

echo "ready -- \`archive-today\` is on PATH."
