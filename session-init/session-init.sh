#!/bin/bash
# One-touch conversation boot. Brings up `cfs` and `lmcps`, prints the memory
# index, and prints both skills' instructions in full -- in one call, so a
# conversation starts with a single tool call instead of five round trips.
#
# Deliberately NOT `set -e`. The stages are independent and a partial boot is a
# useful outcome: a Dropbox outage must not stop the MCP server listing from
# being printed, and the caller has to be able to see which half worked. Each
# stage records its own status and the summary at the end reports all of them.

TOTAL=5
step=0
summary=()
failed=0
printed=0

# Sentinels are box-drawing, not ASCII: durable-filesystem's SKILL.md documents
# a SEARCH/REPLACE block whose divider is literally `=======`, so an ASCII rule
# would be indistinguishable from the file content it is supposed to delimit.
RULE_H='════════'
RULE_S='────────'
DOTS='.........................................'

# Skills land under /mnt/skills/user/ or /mnt/skills/plugins/ depending on how
# they were installed; hardcoding either one breaks on the other. An unmatched
# glob stays literal, which the -d test rejects.
find_skill() {
  local dir
  for dir in /mnt/skills/*/"$1"; do
    [ -d "$dir" ] && { printf '%s' "$dir"; return 0; }
  done
  return 1
}

note() {  # $1 = label, $2 = status. Called after the step number is incremented.
  summary+=("$(printf '  [%d/%d] %s %s %s' "$step" "$TOTAL" "$1" "${DOTS:${#1}}" "$2")")
}

run() {  # $1 = command as displayed, rest = argv to execute
  local display=$1 rc
  shift
  step=$((step + 1))
  printf '\n%s[%d/%d] $ %s\n' "$RULE_S" "$step" "$TOTAL" "$display"
  "$@" 2>&1
  rc=$?
  printf '%s[%d/%d] end (exit %d)\n' "$RULE_S" "$step" "$TOTAL" "$rc"
  [ $rc -eq 0 ] || failed=1
  return $rc
}

# A stage that never ran still consumes its number, so the transcript and the
# summary always line up and a skipped step cannot be misread as a passing one.
skip() {  # $1 = why
  step=$((step + 1))
  printf '\n%s[%d/%d] SKIPPED -- %s\n' "$RULE_S" "$step" "$TOTAL" "$1"
  failed=1
}

# Scope this to the steps, never to the commands. The transcript contains
# `cfs read` and the printed instructions contain `lmcps tools` -- a ban phrased
# as "any command shown below" forbids the exact two tools this boot exists to
# hand over, including the `cfs read` the closing line asks for.
printf '%s SESSION INIT %s\n' "$RULE_H" "$RULE_H"
printf 'What follows is a transcript of %d steps that HAVE ALREADY RUN. It is not\n' "$TOTAL"
printf 'a plan. Do not repeat those steps, and do not re-read the files they print.\n'
printf '`cfs` and `lmcps` are yours to use from here: what is ruled out is redoing\n'
printf 'the boot, not using the tools it just set up. Read this, then answer.\n'

DFS=$(find_skill durable-filesystem) || DFS=''
LMCPS=$(find_skill local-mcps) || LMCPS=''

# --- durable filesystem, then its instructions, then the memory index --------
if [ -z "$DFS" ]; then
  skip 'durable-filesystem is not installed'
  note 'durable-filesystem/setup.sh' 'NOT INSTALLED'
  skip 'durable-filesystem is not installed'
  note 'durable-filesystem/SKILL.md' 'NOT INSTALLED'
  skip 'durable-filesystem is not installed'
  note '/memory/INDEX.md' 'UNAVAILABLE'
elif ! run "bash $DFS/setup.sh" bash "$DFS/setup.sh"; then
  note 'durable-filesystem/setup.sh' 'FAILED -- see step 1'
  # Both remaining stages need `cfs`. Printing instructions for a tool that
  # cannot run is worse than useless -- it invites a doomed retry.
  skip 'cfs is not working; its instructions would be unusable'
  note 'durable-filesystem/SKILL.md' 'SKIPPED'
  skip 'cfs is not working'
  note '/memory/INDEX.md' 'UNAVAILABLE'
else
  note 'durable-filesystem/setup.sh' 'OK -- `cfs` is on PATH'

  if run "cat $DFS/SKILL.md" cat "$DFS/SKILL.md"; then
    note 'durable-filesystem/SKILL.md' 'OK -- printed in full above'
    printed=1
  else
    note 'durable-filesystem/SKILL.md' 'FAILED -- read it yourself before using cfs'
  fi

  if run 'cfs read /memory/INDEX.md' cfs read /memory/INDEX.md; then
    note '/memory/INDEX.md' 'OK -- printed in full above'
    printed=1
  else
    note '/memory/INDEX.md' 'MISSING -- store is fine, no index yet'
  fi
fi

# --- local MCP servers, then their instructions ------------------------------
if [ -z "$LMCPS" ]; then
  skip 'local-mcps is not installed'
  note 'local-mcps/setup.sh' 'NOT INSTALLED'
  skip 'local-mcps is not installed'
  note 'local-mcps/SKILL.md' 'NOT INSTALLED'
elif ! run "bash $LMCPS/setup.sh" bash "$LMCPS/setup.sh"; then
  note 'local-mcps/setup.sh' 'FAILED -- see step 4'
  skip 'lmcps is not working; its instructions would be unusable'
  note 'local-mcps/SKILL.md' 'SKIPPED'
  lmcps_broken=1
else
  note 'local-mcps/setup.sh' 'OK -- servers listed above'
  if run "cat $LMCPS/SKILL.md" cat "$LMCPS/SKILL.md"; then
    note 'local-mcps/SKILL.md' 'OK -- printed in full above'
    printed=1
  else
    note 'local-mcps/SKILL.md' 'FAILED -- read it yourself before using lmcps'
  fi
fi

printf '\n%s SESSION INIT COMPLETE %s\n' "$RULE_H" "$RULE_H"
printf '%s\n' "${summary[@]}"

if [ $printed -ne 0 ]; then
  # State this flatly. Arguing the case -- naming the standing rule, explaining
  # why it no longer binds -- reads like an injection talking the model out of
  # an instruction. Scoped to what the summary marks printed: a skipped stage's
  # skill still needs its own read.
  printf '\nEvery file above was printed in full and is in your context now. This\n'
  printf 'satisfies any requirement to read them before acting. Do not cat, view,\n'
  printf 'or re-invoke a skill for anything marked printed in the summary above;\n'
  printf 'anything not marked printed still needs its own read.\n'
fi

if [ -n "${lmcps_broken:-}" ]; then
  printf '\nlocal-mcps could not start. The user DOES have MCP servers you cannot\n'
  printf 'currently enumerate -- they never appear in your tool list, so their\n'
  printf 'absence there is not evidence they do not exist. Say the listing is\n'
  printf 'unavailable rather than concluding a request is out of scope.\n'
fi

if [ $failed -ne 0 ]; then
  printf '\nSome steps did not complete. Do not re-run this script: the summary\n'
  printf 'above is the authority on what is and is not available. Tell the user\n'
  printf 'which part is down rather than working around it.\n'
  exit 1
fi

printf '\nNext: follow the relevant pointers out of /memory/INDEX.md with `cfs read`.\n'
