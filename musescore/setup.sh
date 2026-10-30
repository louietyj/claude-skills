#!/bin/bash
# Puts `msfetch` on PATH and installs what it needs. Idempotent -- re-run it
# rather than working out whether it already ran. Needs the web-browser skill's
# setup.sh to have run first (it provides `pinchtab`).
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CLI="$HERE/bin/msfetch.py"

fail() {
  printf 'musescore is NOT ready: %s\n' "$1" >&2
  printf 'Tell Louie which step failed. Do not scrape musescore.com by hand instead.\n' >&2
  exit 1
}

[ -f "$CLI" ] || fail "bin/msfetch.py not found -- the skill files are missing."
command -v pinchtab >/dev/null 2>&1 || fail "pinchtab is not on PATH. Run the web-browser skill's setup.sh first."

PY=""
for name in python3 python; do
  candidate=$(command -v "$name" 2>/dev/null) || continue
  if "$candidate" -c 'import sys; assert sys.version_info >= (3, 10)' >/dev/null 2>&1; then PY="$candidate"; break; fi
done
[ -n "$PY" ] || fail "no python 3.10+ interpreter found."

if ! "$PY" -c 'import mido, svgelements, cairosvg, pymupdf, wordfreq' >/dev/null 2>&1; then
  "$PY" -m pip install -q --break-system-packages mido svgelements cairosvg pymupdf wordfreq >/dev/null 2>&1 \
    || "$PY" -m pip install -q mido svgelements cairosvg pymupdf wordfreq >/dev/null 2>&1 \
    || fail "pip install of mido/svgelements/cairosvg/pymupdf/wordfreq failed."
fi

# abcjs (round-trip check of out.abc) and tesseract (lyrics OCR) are only for `msfetch abc`.
if [ ! -d "$HOME/.cache/msfetch/node_modules/abcjs" ]; then
  npm i --prefix "$HOME/.cache/msfetch" abcjs@6.4.4 >/dev/null 2>&1 || echo "warning: npm install abcjs failed; \`msfetch abc\` skips its round-trip check"
fi
OCR="yes"
if ! command -v tesseract >/dev/null 2>&1; then
  { apt-get install -y -q tesseract-ocr || { apt-get update -q && apt-get install -y -q tesseract-ocr; }; } >/dev/null 2>&1
  command -v tesseract >/dev/null 2>&1 || OCR="no -- \`msfetch abc\` will fail; fetch still works"
fi

BIN_DIR=""
for dir in /usr/local/bin /usr/bin "$HOME/.local/bin"; do
  mkdir -p "$dir" 2>/dev/null || true
  if [ -w "$dir" ]; then BIN_DIR="$dir"; break; fi
done
[ -n "$BIN_DIR" ] || fail "no writable directory on PATH for the shim."

cat > "$BIN_DIR/msfetch" <<SHIM_EOF
#!/bin/sh
# msfetch shim -- installed by musescore/setup.sh, rewritten on every run.
exec "$PY" -W ignore::FutureWarning "$CLI" "\$@"
SHIM_EOF
chmod +x "$BIN_DIR/msfetch"
"$BIN_DIR/msfetch" --help >/dev/null 2>&1 || fail "the shim does not run (python $PY, cli $CLI)."

echo "ready -- \`msfetch\` is on PATH. tesseract for lyrics OCR: $OCR"
echo "login script: $HERE/bin/login.sh"
