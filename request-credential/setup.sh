#!/bin/bash
# Sets up request-text (which receives the credential), puts
# `request-credential` on PATH, and proves the relay accepts this skill's token.
# Idempotent -- re-run it rather than working out whether it already ran.
#
# The sandbox keeps its disk for the length of a conversation, so one run covers
# every later turn. A new conversation gets a fresh disk and needs this again.
set -uo pipefail

# Beside this script first -- where it sits in an installed skill, and what lets
# setup.sh run from a checkout. The `synced` entries are the Claude Code cloud
# box, where claude.ai skills land under $HOME.
find_file() {  # $1 = skill, $2 = path inside it
  local candidate
  for candidate in \
    "$(cd "$(dirname "$0")/.." && pwd)/$1/$2" \
    "/mnt/skills/user/$1/$2" \
    /mnt/skills/*/"$1/$2" \
    "$(ls -1dt "$HOME"/.claude/skills/synced/*/"$1/$2" 2>/dev/null | head -1)"
  do
    [ -n "$candidate" ] && [ -f "$candidate" ] && { printf '%s' "$candidate"; return 0; }
  done
  return 1
}

fail() {
  printf 'request-credential is NOT ready: %s\n' "$1" >&2
  printf 'Tell Louie which step failed. Do not ask him to paste a password into the chat.\n' >&2
  exit 1
}

CLI=$(find_file request-credential bin/request-credential.mjs) \
  || fail "request-credential.mjs not found -- the skill files are missing."
RT_SETUP=$(find_file request-text setup.sh) \
  || fail "the request-text skill is not installed; this skill needs it."

echo "== request-text setup =="
bash "$RT_SETUP" || fail "request-text's setup failed (output above)."
echo

NODE=$(command -v node 2>/dev/null) && "$NODE" -e '' 2>/dev/null || fail "no working node on PATH."
BIN_DIR=$(dirname "$(command -v request-text)")
cat > "$BIN_DIR/request-credential" <<SHIM_EOF
#!/bin/sh
# request-credential shim -- installed by request-credential/setup.sh, rewritten on every run.
exec "$NODE" "$CLI" "\$@"
SHIM_EOF
chmod +x "$BIN_DIR/request-credential"
echo "$BIN_DIR/request-credential -> $NODE $CLI"

request-credential health || fail "the relay check failed (output above)."

echo
echo "ready -- \`request-credential ask <site> \"<reason>\"\`, then \`request-credential wait <id>\` in the same turn."
