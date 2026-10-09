#!/bin/bash
# Briefing to run in chat, in the turn before the user switches to voice.
#
# Voice is answered by a separate agent that never gets the user's preferences
# or skills, but does read this conversation as it stood at the switch -- tool
# output included. So everything voice-Claude needs goes to stdout here.
#
# It must arrive as ONE SMALL tool result. Past a size threshold the harness
# swaps a result for a 2 KB preview plus a file path, and voice-Claude would get
# the preview. Hence session-init is never run inline: its ~60 KB would take the
# briefing down with it.
#
# Deliberately NOT `set -e` -- a failed catalog must not cost the briefing.

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SENTINEL=${SESSION_INIT_SENTINEL:-/tmp/.session-init.done}
GUIDE_SENTINEL=${VOICE_MODE_GUIDE_SENTINEL:-/tmp/.voice-mode-guide.done}
RULE_H='════════'

# Short-circuit a second run, so the preferences can say "run this" flatly. The
# conditional phrasing -- "unless it already ran" -- would ask the model to judge
# what happened earlier in a long transcript. The sentinel answers that instead.
if [ -f "$GUIDE_SENTINEL" ] && [ "${1:-}" != "--force" ]; then
  printf '%s VOICE MODE GUIDE -- ALREADY RUN %s\n' "$RULE_H" "$RULE_H"
  sed 's/^/  /' "$GUIDE_SENTINEL"
  printf '\nThe briefing is above in this conversation, where voice-Claude will\n'
  printf 'read it. Tell the user they can switch.\n'
  printf '(`bash voice-mode-guide.sh --force` reprints it.)\n'
  exit 0
fi

if [ ! -f "$SENTINEL" ]; then
  printf '%s VOICE MODE GUIDE -- NOT RUN YET %s\n' "$RULE_H" "$RULE_H"
  printf 'session-init has not run in this conversation. Run it now, as its own\n'
  printf 'tool call, then run this script again as another:\n\n'
  # shellcheck disable=SC2016 -- printed for the model to run, not expanded here
  printf '  bash "$(ls -1dt /mnt/skills/*/session-init ~/.claude/skills/synced/*/session-init 2>/dev/null | head -1)/session-init.sh"\n\n'
  printf 'Not inside this call: its output would push this briefing past the size\n'
  printf 'at which the harness replaces a tool result with a preview, and the\n'
  printf 'preview is all voice-Claude would see.\n'
  exit 2
fi

if ! cat "$HERE/voice-brief.md"; then
  printf 'FAILED to read voice-brief.md. Tell the user the voice briefing is\n'
  printf 'missing; voice-Claude will not know how to hand off without it.\n'
fi

printf '\n'
python3 "$HERE/catalog.py" ||
  printf '\nFAILED to build the catalog. The briefing above still stands.\n'

printf 'voice-mode-guide ran at %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
  >"$GUIDE_SENTINEL" 2>/dev/null

printf '\n%s END OF VOICE BRIEFING %s\n' "$RULE_H" "$RULE_H"
printf 'Text-Claude: tell the user in one sentence that they can switch to voice.\n'
printf 'Do not recite any of this back.\n'
