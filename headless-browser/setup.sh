#!/bin/bash
# Installs and configures pinchtab (real Chrome via CLI/HTTP). Idempotent.
set -euo pipefail

NPM_ROOT="$(npm root -g)"
REAL="$NPM_ROOT/pinchtab/bin/pinchtab"
BIN_DIR="$(dirname "$(dirname "$NPM_ROOT")")/bin"
SHIM="$BIN_DIR/pinchtab"
SESSION_FILE="${PINCHTAB_SESSION_FILE:-/home/claude/.pinchtab-session}"

# A shim from a previous run would open a session before the server exists, so
# drop it and drive setup through $REAL directly. It is reinstalled at the end.
[ -f "$SHIM" ] && [ ! -L "$SHIM" ] && rm -f "$SHIM" || true

# Check the package, not `command -v pinchtab`: the shim satisfies that test
# even when the package underneath it is missing.
[ -x "$REAL" ] || npm install -g pinchtab

# Most sandboxes ship a Chromium. If not, pull puppeteer's bundled build.
if ! "$REAL" doctor 2>&1 | grep -qi "OK.*chrome_present"; then
  mkdir -p /tmp/pinchtab-chrome && cd /tmp/pinchtab-chrome
  npm init -y >/dev/null 2>&1
  npm install puppeteer >/dev/null 2>&1
  CHROME_BIN=$(find /tmp/pinchtab-chrome /root/.cache/puppeteer "$HOME/.cache/puppeteer" \
    -type f -path "*chrome-linux64/chrome" 2>/dev/null | head -1)
  [ -n "$CHROME_BIN" ] && "$REAL" config set browser.binary "$CHROME_BIN" >/dev/null
fi

# Open up pinchtab's capability gates. These are Chrome features, gated by
# default for people running pinchtab on their own machine; in a disposable
# sandbox there's nothing for them to expose.
"$REAL" security down >/dev/null
for k in allowClipboard allowStateExport allowFileScheme; do
  "$REAL" config set "security.$k" true >/dev/null
done
"$REAL" config set security.allowedDomains '*' >/dev/null

"$REAL" server stop >/dev/null 2>&1 || true
"$REAL" server restart >/dev/null
sleep 2

# Session shim. Callers never manage PINCHTAB_SESSION: the shim resolves one on
# first use, reuses it across bash calls, and recreates it if it goes stale.
#
# Installed LAST and unconditionally, because `npm install -g` recreates the bin
# symlink and would otherwise wipe it. The `rm -f` is load-bearing: that entry is
# a SYMLINK into the package, so writing through it overwrites pinchtab's own
# entry point and bricks the install with no backup.
rm -f "$SHIM" "$SESSION_FILE"
cat > "$SHIM" <<SHIM_EOF
#!/bin/sh
# pinchtab shim -- installed by headless-browser/setup.sh. Do not edit in place;
# setup.sh rewrites it on every run.
REAL=$REAL
SESSION_FILE_DEFAULT=$SESSION_FILE
SHIM_EOF
cat >> "$SHIM" <<'SHIM_EOF'
SELF=$(readlink -f "$0" 2>/dev/null)
if [ ! -x "$REAL" ] || [ "$(readlink -f "$REAL" 2>/dev/null)" = "$SELF" ]; then
  echo "pinchtab: shim resolves to itself; run: npm install -g pinchtab --force" >&2
  exit 70
fi
F=${PINCHTAB_SESSION_FILE:-$SESSION_FILE_DEFAULT}

resolve() {
  unset PINCHTAB_SESSION            # a stale value makes `session create` fail
  if [ ! -s "$F" ]; then
    id=$("$REAL" session create --agent-id claude 2>/dev/null | tr -d '\n')
    case "$id" in
      ses_*) printf '%s' "$id" > "$F" ;;
      *)     rm -f "$F"; return 1 ;;
    esac
  fi
  PINCHTAB_SESSION=$(cat "$F" 2>/dev/null); export PINCHTAB_SESSION
}

# `session create` must run unscoped -- a session-scoped caller gets 403, which
# makes a hand-rolled `export PINCHTAB_SESSION=$(pinchtab session create ...)`
# capture an empty string and silently look like it worked.
case "${1:-}" in
  session) unset PINCHTAB_SESSION ;;
  *)       [ -n "${PINCHTAB_SESSION:-}" ] || resolve ;;
esac

# A caller may export a session by hand -- pinchtab's own bundled docs tell them
# to. Adopt it as the persistent one, so the NEXT bash call (which has no export)
# attaches to that same tab instead of minting a fresh, empty one. Without this,
# mixing the two styles silently queries a blank tab.
case "${PINCHTAB_SESSION:-}" in
  ses_*)
    [ "$(cat "$F" 2>/dev/null)" = "$PINCHTAB_SESSION" ] \
      || printf '%s' "$PINCHTAB_SESSION" > "$F"
    ;;
esac

E=$(mktemp)
"$REAL" "$@" 2>"$E"; rc=$?
# pinchtab exits 0 on a bad session, so the retry must never be gated on $rc
if grep -q 'bad_session' "$E"; then
  rm -f "$F"; resolve
  "$REAL" "$@" 2>"$E"; rc=$?
fi
cat "$E" >&2; rm -f "$E"
exit $rc
SHIM_EOF
chmod +x "$SHIM"

# Create the session now: makes the notice below true, and warms the first nav.
SID=$("$REAL" session create --agent-id claude 2>/dev/null | tr -d '\n' || true)
case "$SID" in
  ses_*) printf '%s' "$SID" > "$SESSION_FILE" ;;
  *)     rm -f "$SESSION_FILE" ;;   # shim will create one lazily on first use
esac

echo "ready."
echo "PINCHTAB_SESSION IS ALREADY INITIALIZED. Do not create or export one."
echo ""
echo "NOW READ: $NPM_ROOT/pinchtab/skills/pinchtab/SKILL.md"
echo "  ^^ IGNORE ITS FIRST INSTRUCTION. That file opens by telling you to run"
echo "     export PINCHTAB_SESSION=\$(pinchtab session create ...)"
echo "     DO NOT. It is done. Everything else in that file applies as written."
echo ""
echo "THEN RUN: pinchtab nav <url>"
