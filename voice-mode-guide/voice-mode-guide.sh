#!/bin/bash
# Briefing to run in chat, in the turn before the user switches to voice.
#
# Voice replaces the injected context but not the conversation, so everything
# this skill says goes to stdout, where it survives the toggle. SKILL.md is a
# shim: it is not certain a skill body lands on the durable side of that line.
#
# Deliberately NOT `set -e` -- a failed stage must not cost the other two.

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SENTINEL=${SESSION_INIT_SENTINEL:-/tmp/.session-init.done}
GUIDE_SENTINEL=${VOICE_MODE_GUIDE_SENTINEL:-/tmp/.voice-mode-guide.done}

RULE_H='════════'
RULE_S='────────'

# Short-circuit a second run, so the preferences can say "run this" flatly. The
# conditional phrasing -- "unless it already ran" -- would ask a model that has
# just lost its context to judge what happened earlier in it. The sentinel
# answers that instead. A duplicate is ~200 lines restating what is above.
if [ -f "$GUIDE_SENTINEL" ] && [ "${1:-}" != "--force" ]; then
  printf '%s VOICE MODE GUIDE -- ALREADY RUN %s\n' "$RULE_H" "$RULE_H"
  sed 's/^/  /' "$GUIDE_SENTINEL"
  printf '\nThe briefing and the skill index are above in this conversation and\n'
  printf 'survive the mode toggle. Scroll back rather than re-running.\n'
  printf '\nWorth restating, because it is where this goes wrong: bash_tool is not\n'
  printf 'registered in voice, and the same shell is reachable through\n'
  printf 'code_execution with subprocess.run(..., shell=True). A tool missing\n'
  printf 'from your registry is not a capability missing from the machine.\n'
  printf '\n(`bash voice-mode-guide.sh --force` reprints everything.)\n'
  exit 0
fi

find_skill() {
  local dir
  for dir in /mnt/skills/*/"$1"; do
    [ -d "$dir" ] && { printf '%s' "$dir"; return 0; }
  done
  return 1
}

printf '%s VOICE MODE GUIDE %s\n' "$RULE_H" "$RULE_H"
printf 'Three parts: the boot, the briefing, the skill index. All three are\n'
printf 'output, not a plan -- nothing below is waiting on you to run it.\n'

# --- 1. boot -----------------------------------------------------------------
#
# There is no boot process on claude.ai, only a preference asking for
# session-init on the first turn, which may or may not have been followed. The
# sentinel rather than a `cfs`-on-PATH probe, because it records which stages
# passed: a half-failed boot then reports as half-failed instead of as booted.
printf '\n%s[1/3] session-init\n' "$RULE_S"
if [ -f "$SENTINEL" ]; then
  printf 'ALREADY RUN. Its output is above in this conversation and survives the\n'
  printf 'mode toggle. Do not run it again. It reported:\n\n'
  sed 's/^/  /' "$SENTINEL"
elif INIT=$(find_skill session-init); then
  printf 'NOT YET RUN. voice-mode-guide is invoking it now, because a voice\n'
  printf 'session with no `cfs` and no memory index is not worth having.\n'
  printf 'Everything between here and the [2/3] marker is session-init output.\n'
  printf 'By the time you are reading this it HAS run: do not run it again, and\n'
  printf 'do not re-read the files it prints.\n\n'
  bash "$INIT/session-init.sh" 2>&1
  printf '\nThat was session-init, invoked by this script rather than by you. You do\n'
  printf 'not need to call it separately later in this conversation; its own\n'
  printf 'summary above is the authority on which stages worked.\n'
else
  printf 'NOT RUN, and session-init is not installed. `cfs` and `lmcps` will be\n'
  printf 'absent and the memory index unavailable. Tell the user rather than\n'
  printf 'working around it.\n'
fi

# --- 2. briefing -------------------------------------------------------------
#
# After the boot, not before: session-init runs to hundreds of lines, and the
# briefing is what the next few turns need closest to hand.
printf '\n%s[2/3] briefing\n' "$RULE_S"
if ! cat "$HERE/voice-brief.md"; then
  printf 'FAILED to read voice-brief.md. The short version: the machine is\n'
  printf 'identical in voice, only the injected context changes. bash_tool is\n'
  printf 'unregistered -- use code_execution with subprocess.run(..., shell=True)\n'
  printf 'for the same shell. Connectors, tool_search, conversation_search and\n'
  printf 'memory all work natively. Route around a missing tool; do not report a\n'
  printf 'wall.\n'
fi

# --- 3. skill index ----------------------------------------------------------
printf '\n%s[3/3] skill index\n' "$RULE_S"
printf 'This is the <available_skills> block, read back off the disk that both\n'
printf 'modes share. Once the user switches to voice it is the only copy you\n'
printf 'have. Use it the way you use the injected block: match a request against\n'
printf 'a description, then read that path.\n'
if ! python3 "$HERE/skill-index.py"; then
  printf '\nFAILED to build the index. The skills are still on disk:\n'
  printf '  ls -d /mnt/skills/{public,user,plugins}/*/\n'
  printf 'and each holds a SKILL.md whose frontmatter says when to use it.\n'
fi

printf 'voice-mode-guide ran at %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
  >"$GUIDE_SENTINEL" 2>/dev/null

printf '\n%s VOICE MODE GUIDE COMPLETE %s\n' "$RULE_H" "$RULE_H"
printf 'Everything above is in your context now and stays there across the\n'
printf 'toggle. Acknowledge in one sentence -- do not recite it back -- and\n'
printf 'answer whatever the user actually asked.\n'
