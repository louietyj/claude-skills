#!/bin/bash
# Puts `request-text` on PATH, installs the hostc tunnel client and jq, and
# proves the whole chain with a real submission through a real tunnel.
# Idempotent -- re-run it rather than working out whether it already ran.
#
# The sandbox keeps its disk for the length of a conversation, so one run covers
# every later turn. A new conversation gets a fresh disk and needs this again.
set -uo pipefail

find_cli() {
  local candidate
  # Beside this script first -- where it sits in an installed skill, and what
  # lets setup.sh run from a checkout. The rest are fallbacks. The `synced`
  # entry is the Claude Code cloud box, where claude.ai skills land under $HOME.
  for candidate in \
    "$(cd "$(dirname "$0")" && pwd)/bin/request-text.mjs" \
    /mnt/skills/user/request-text/bin/request-text.mjs \
    /mnt/skills/*/request-text/bin/request-text.mjs \
    "$(ls -1dt "$HOME"/.claude/skills/synced/*/request-text/bin/request-text.mjs \
       2>/dev/null | head -1)"
  do
    [ -n "$candidate" ] && [ -f "$candidate" ] && { printf '%s' "$candidate"; return 0; }
  done
  candidate=$(find /mnt /opt /home "$HOME" -name request-text.mjs -path '*request-text*' \
    2>/dev/null | head -1)
  [ -n "$candidate" ] && { printf '%s' "$candidate"; return 0; }
  return 1
}

fail() {
  printf 'request-text is NOT ready: %s\n' "$1" >&2
  printf 'Tell the user which step failed. Do not fall back to asking them to paste\n' >&2
  printf 'a secret into the chat without saying that this is what you are doing.\n' >&2
  exit 1
}

CLI=$(find_cli) || fail "request-text.mjs not found -- the skill files are missing."

# Bake in an absolute interpreter path, so a later PATH change cannot break the
# shim. Run it rather than just resolving it: a name on PATH can be a stub.
NODE=$(command -v node 2>/dev/null) && "$NODE" -e '' 2>/dev/null \
  || fail "no working node on PATH."

# hostc's protocol moves; its own docs say to stay on @latest.
NPM_PREFIX=$(npm prefix -g)
HOSTC="$NPM_PREFIX/bin/hostc"
echo "installing hostc@latest ..."
timeout 180 npm install -g --no-fund --no-audit hostc@latest >/tmp/request-text-npm.log 2>&1
[ -x "$HOSTC" ] || { tail -20 /tmp/request-text-npm.log >&2; fail "hostc did not install."; }
echo "hostc $("$HOSTC" --version 2>/dev/null || echo '?')"

# wait prints JSON; jq is how callers pull a field out. apt's index can be stale
# enough to 404 on a package, so update and retry once before giving up.
JQ_NOTE="jq $(jq --version 2>/dev/null)"
if ! command -v jq >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  if apt-get install -y -qq jq >/dev/null 2>&1 \
     || { apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq jq >/dev/null 2>&1; }; then
    JQ_NOTE="jq installed"
  else
    JQ_NOTE="jq UNAVAILABLE -- extract fields with python3 -c 'import json,sys; print(json.load(sys.stdin)[\"name\"], end=\"\")'"
  fi
fi
echo "$JQ_NOTE"

BIN_DIR=""
for dir in /usr/local/bin /usr/bin "$HOME/.local/bin"; do
  mkdir -p "$dir" 2>/dev/null || true
  if [ -w "$dir" ]; then BIN_DIR="$dir"; break; fi
done
[ -n "$BIN_DIR" ] || fail "no writable directory on PATH for the shim."

cat > "$BIN_DIR/request-text" <<SHIM_EOF
#!/bin/sh
# request-text shim -- installed by request-text/setup.sh, rewritten on every run.
REQUEST_TEXT_HOSTC="$HOSTC" exec "$NODE" "$CLI" "\$@"
SHIM_EOF
chmod +x "$BIN_DIR/request-text"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "warning: $BIN_DIR is not on PATH; run: export PATH=\"$BIN_DIR:\$PATH\"" >&2 ;;
esac

# A real submission through a real tunnel: fail here, not with the user holding a dead link.
echo "self-test through hostc ..."
timeout 120 "$BIN_DIR/request-text" selftest || fail "self-test failed (output above)."

echo
echo "ready -- \`request-text\` is on PATH. Next: request-text open --title '...' --field name:kind"
