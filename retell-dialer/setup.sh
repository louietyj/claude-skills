#!/bin/bash
# Puts `dialer` on PATH and proves the consult queue is reachable, printing each
# step as it runs so the caller reads the output as work already done rather
# than a list of things to go and do.
#
# The sandbox reboots between assistant turns but keeps its disk for the length
# of a conversation, so one run covers every later turn. A new conversation gets
# a fresh disk and needs this again.
set -euo pipefail

find_dialer_py() {
  local candidate
  # Beside this script first -- that is where the CLI sits in an installed
  # skill, and it lets setup.sh run from a checkout. The rest are fallbacks.
  #
  # The `synced` entries are the Claude Code cloud box, where claude.ai skills
  # land under $HOME rather than /mnt/skills. Its path carries two UUIDs and
  # more than one bucket can exist, so the glob is sorted by mtime, newest wins.
  for candidate in \
    "$(cd "$(dirname "$0")" && pwd)/bin/dialer.py" \
    /mnt/skills/user/retell-dialer/bin/dialer.py \
    /mnt/skills/*/retell-dialer/bin/dialer.py \
    "$(ls -1dt "$HOME"/.claude/skills/synced/*/retell-dialer/bin/dialer.py \
       2>/dev/null | head -1)"
  do
    [ -n "$candidate" ] && [ -f "$candidate" ] && \
      { printf '%s' "$candidate"; return 0; }
  done
  candidate=$(find /mnt /opt /home "$HOME" -name dialer.py -path '*retell-dialer*' \
    2>/dev/null | head -1)
  [ -n "$candidate" ] && { printf '%s' "$candidate"; return 0; }
  return 1
}

DIALER_PY=$(find_dialer_py) || {
  echo "dialer.py not found -- the skill files are missing." >&2
  echo "Tell the user the skill is not installed. Do NOT try to place a call" >&2
  echo "another way: the consult loop is the only thing that makes a call" >&2
  echo "supervisable, and without it you are dispatching fire-and-forget." >&2
  exit 1
}

RULE_H='════════'
RULE_S='────────'
TOTAL=4

begin() {  # $1 = step number, $2 = what runs, as displayed
  printf '\n%s[%d/%d] %s\n' "$RULE_S" "$1" "$TOTAL" "$2"
}

end() {  # $1 = step number, $2 = exit status
  printf '%s[%d/%d] end (exit %d)\n' "$RULE_S" "$1" "$TOTAL" "$2"
}

printf '%s RETELL DIALER SETUP %s\n' "$RULE_H" "$RULE_H"
printf 'What follows is a transcript of %d steps as they ran.\n' "$TOTAL"

begin 1 'install the `dialer` shim'
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

cat > "$BIN_DIR/dialer" <<SHIM_EOF
#!/bin/sh
# dialer shim -- installed by retell-dialer/setup.sh, rewritten on every run.
exec "$PY" "$DIALER_PY" "\$@"
SHIM_EOF
chmod +x "$BIN_DIR/dialer"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "warning: $BIN_DIR is not on PATH; run: export PATH=\"$BIN_DIR:\$PATH\"" >&2 ;;
esac

echo "$BIN_DIR/dialer -> $PY $DIALER_PY"
end 1 0

# Reach the queue now. A dead queue discovered mid-call means a receptionist on
# hold and no way to answer; discovered here it is just a message to the user.
begin 2 'dialer health'
rc=0
"$BIN_DIR/dialer" health || rc=$?
end 2 $rc

# Google Calendar gives bare timestamps, and a weekday worked out from one under
# call pressure has already been wrong on a live call.
begin 3 'dialer gregorian_calendar'
cal_rc=0
"$BIN_DIR/dialer" gregorian_calendar || cal_rc=$?
end 3 $cal_rc

# The guide rides in this output rather than in SKILL.md: a long SKILL.md read
# with `view` came back middle-truncated, and the compression gate in the part
# it dropped was skipped without a word. Printed on every run -- setup runs
# once per conversation, and a skipped print costs more than a repeated one.
GUIDE="$(dirname "$(dirname "$DIALER_PY")")/GUIDE.md"
begin 4 "cat $GUIDE"
guide_rc=0
if [ $rc -ne 0 ]; then
  printf 'SKIPPED -- dialer health failed, so no call can be placed.\n'
elif cat "$GUIDE"; then
  printf '%s END OF GUIDE %s\n' "$RULE_H" "$RULE_H"
else
  guide_rc=1
fi
end 4 $guide_rc

status=READY; [ $rc -eq 0 ] || status=DOWN
printf '\n%s RETELL DIALER %s %s\n' "$RULE_H" "$status" "$RULE_H"
printf '  [1/4] dialer shim ..... OK -- installed\n'
if [ $rc -ne 0 ]; then
  printf '  [2/4] dialer health ... FAILED\n'
  printf '\nDo NOT place a call: consult_supervisor would time out mid-conversation\n'
  printf 'with someone on the line. Tell Louie which check failed above.\n'
  exit 1
fi
printf '  [2/4] dialer health ... OK -- queue, token, agent, webhook, number\n'
if [ $cal_rc -eq 0 ]; then
  printf '  [3/4] calendar ........ OK -- take every weekday from it\n'
else
  printf '  [3/4] calendar ........ FAILED -- run `dialer gregorian_calendar` before naming a weekday\n'
fi
if [ $guide_rc -eq 0 ]; then
  printf '  [4/4] guide ........... OK -- printed in full above; do not view or cat it again\n'
else
  printf '  [4/4] guide ........... FAILED -- cat %s in full before writing a brief\n' "$GUIDE"
fi
printf '\nAll four steps are done for the rest of this conversation. This output is a\n'
printf 'transcript of work already done, not a plan: do not re-run this script or\n'
printf '`dialer health`.\n'
printf "\nNext: write the brief, pass it through the MANDATORY GATE, get Louie's\n"
printf "explicit go-ahead for this call, then \`dialer dispatch\`.\n"
