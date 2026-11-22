#!/bin/bash
# Puts `ytdl` on PATH. Idempotent -- re-run it rather than working out whether
# it already ran. The browser comes from the web-browser skill, the home IP
# from home-proxy; nothing here starts either.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CLI="$HERE/bin/ytdl.py"

fail() {
  printf 'youtube-download is NOT ready: %s\n' "$1" >&2
  exit 1
}

[ -f "$CLI" ] || fail "bin/ytdl.py not found -- the skill files are missing."
PY=$(command -v python3) || fail "no python3."
command -v curl >/dev/null || fail "no curl."
FFMPEG="yes"
if ! command -v ffmpeg >/dev/null; then
  { apt-get install -y -q ffmpeg || { apt-get update -q && apt-get install -y -q ffmpeg; }; } >/dev/null 2>&1
  command -v ffmpeg >/dev/null || FFMPEG="no -- \`ytdl frames\` and the ffprobe check will not work"
fi

BIN_DIR=""
for dir in /usr/local/bin /usr/bin "$HOME/.local/bin"; do
  mkdir -p "$dir" 2>/dev/null || true
  if [ -w "$dir" ]; then BIN_DIR="$dir"; break; fi
done
[ -n "$BIN_DIR" ] || fail "no writable directory on PATH for the shim."

cat > "$BIN_DIR/ytdl" <<SHIM_EOF
#!/bin/sh
# ytdl shim -- installed by youtube-download/setup.sh, rewritten on every run.
exec "$PY" "$CLI" "\$@"
SHIM_EOF
chmod +x "$BIN_DIR/ytdl"
"$BIN_DIR/ytdl" --help >/dev/null 2>&1 || fail "the shim does not run (python $PY, cli $CLI)."

echo "ready -- \`ytdl\` is on PATH. ffmpeg: $FFMPEG"
