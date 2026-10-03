#!/bin/bash
# Puts `listen` on PATH and proves the relay is reachable, printing each
# step as it runs so the caller reads the output as work already done rather
# than a list of things to go and do.
#
# The sandbox reboots between assistant turns but keeps its disk for the length
# of a conversation, so one run covers every later turn. A new conversation gets
# a fresh disk and needs this again.
set -euo pipefail

find_listen_py() {
  local candidate
  # Beside this script first -- that is where the CLI sits in an installed
  # skill, and it lets setup.sh run from a checkout. The rest are fallbacks.
  #
  # The `synced` entries are the Claude Code cloud box, where claude.ai skills
  # land under $HOME rather than /mnt/skills. Its path carries two UUIDs and
  # more than one bucket can exist, so the glob is sorted by mtime, newest wins.
  for candidate in \
    "$(cd "$(dirname "$0")" && pwd)/bin/listen.py" \
    /mnt/skills/user/claude-listen/bin/listen.py \
    /mnt/skills/*/claude-listen/bin/listen.py \
    "$(ls -1dt "$HOME"/.claude/skills/synced/*/claude-listen/bin/listen.py \
       2>/dev/null | head -1)"
  do
    [ -n "$candidate" ] && [ -f "$candidate" ] && \
      { printf '%s' "$candidate"; return 0; }
  done
  candidate=$(find /mnt /opt /home "$HOME" -name listen.py -path '*claude-listen*' \
    2>/dev/null | head -1)
  [ -n "$candidate" ] && { printf '%s' "$candidate"; return 0; }
  return 1
}

LISTEN_PY=$(find_listen_py) || {
  echo "listen.py not found -- the skill files are missing." >&2
  echo "Tell the user the skill is not installed." >&2
  exit 1
}

RULE_H='════════'
RULE_S='────────'
TOTAL=3

begin() {  # $1 = step number, $2 = what runs, as displayed
  printf '\n%s[%d/%d] %s\n' "$RULE_S" "$1" "$TOTAL" "$2"
}

end() {  # $1 = step number, $2 = exit status
  printf '%s[%d/%d] end (exit %d)\n' "$RULE_S" "$1" "$TOTAL" "$2"
}

printf '%s CLAUDE LISTEN SETUP %s\n' "$RULE_H" "$RULE_H"
printf 'What follows is a transcript of %d steps as they ran.\n' "$TOTAL"

begin 1 'install the `listen` shim'
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

cat > "$BIN_DIR/listen" <<SHIM_EOF
#!/bin/sh
# listen shim -- installed by claude-listen/setup.sh, rewritten on every run.
exec "$PY" "$LISTEN_PY" "\$@"
SHIM_EOF
chmod +x "$BIN_DIR/listen"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "warning: $BIN_DIR is not on PATH; run: export PATH=\"$BIN_DIR:\$PATH\"" >&2 ;;
esac

echo "$BIN_DIR/listen -> $PY $LISTEN_PY"
end 1 0

# Reach the relay now. A dead relay discovered after Louie has started talking
# is a conversation nobody heard; discovered here it is just a message to him.
begin 2 'listen health'
rc=0
"$BIN_DIR/listen" health || rc=$?
end 2 $rc

# The guide rides in this output rather than in SKILL.md: a long SKILL.md read
# with `view` can come back middle-truncated, silently dropping a rule.
GUIDE="$(dirname "$(dirname "$LISTEN_PY")")/GUIDE.md"
begin 3 "cat $GUIDE"
guide_rc=0
if [ $rc -ne 0 ]; then
  printf 'SKIPPED -- listen health failed, so nothing can be heard.\n'
elif cat "$GUIDE"; then
  printf '%s END OF GUIDE %s\n' "$RULE_H" "$RULE_H"
else
  guide_rc=1
fi
end 3 $guide_rc

status=READY; [ $rc -eq 0 ] || status=DOWN
printf '\n%s CLAUDE LISTEN %s %s\n' "$RULE_H" "$status" "$RULE_H"
printf '  [1/3] listen shim ..... OK -- installed\n'
if [ $rc -ne 0 ]; then
  printf '  [2/3] listen health ... FAILED\n'
  printf '\nDo NOT start a session. Tell Louie which check failed above.\n'
  exit 1
fi
printf '  [2/3] listen health ... OK -- relay, secrets, token\n'
if [ $guide_rc -eq 0 ]; then
  printf '  [3/3] guide ........... OK -- printed in full above; do not view or cat it again\n'
else
  printf '  [3/3] guide ........... FAILED -- cat %s in full before starting\n' "$GUIDE"
fi
printf '\nAll three steps are done for the rest of this conversation. This output is a\n'
printf 'transcript of work already done, not a plan: do not re-run this script or\n'
printf '`listen health`.\n'
printf '\nNext: build the keyterm list, `listen start`, tell Louie the id, then\n'
printf '`listen watch` -- and keep watching in this same turn.\n'
