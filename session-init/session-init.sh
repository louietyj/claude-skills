#!/bin/bash
# One-touch conversation boot. Brings up `cfs` and `lmcps`, prints the memory
# index, prints both skills' instructions in full, and denies Claude Code's
# native web tools in favour of mcp-parallel -- in one call, so a conversation
# starts with a single tool call instead of five round trips.
#
# The slow stages are network round trips, so they run concurrently, each into
# its own file, and the transcript is assembled afterwards in step order.
#
# Deliberately NOT `set -e`. The stages are independent and a partial boot is a
# useful outcome: a Dropbox outage must not stop the MCP server listing from
# being printed, and the caller has to be able to see which half worked. Each
# stage records its own status and the summary at the end reports all of them.

TOTAL=7
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

# Chat puts skills under /mnt/skills/user/ or /mnt/skills/plugins/; Cowork and
# cloud Claude Code sync them to $HOME/.claude/skills/synced/<bucket>/, newest
# bucket wins. An unmatched glob stays literal, which the -d test rejects.
find_skill() {
  local dir
  for dir in /mnt/skills/*/"$1"; do
    [ -d "$dir" ] && { printf '%s' "$dir"; return 0; }
  done
  dir=$(ls -1dt "$HOME"/.claude/skills/synced/*/"$1" 2>/dev/null | head -1)
  [ -d "$dir" ] && { printf '%s' "$dir"; return 0; }
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

capture() {  # $1 = key, rest = argv; output and exit code land in $WORK for show()
  local key=$1
  shift
  "$@" >"$WORK/$key.out" 2>&1
  echo $? >"$WORK/$key.rc"
}

show() {  # $1 = key, $2 = command as displayed. run(), but replaying a capture.
  local rc
  step=$((step + 1))
  printf '\n%s[%d/%d] $ %s\n' "$RULE_S" "$step" "$TOTAL" "$2"
  cat "$WORK/$1.out" 2>/dev/null
  rc=$(cat "$WORK/$1.rc" 2>/dev/null || echo 1)
  printf '%s[%d/%d] end (exit %d)\n' "$RULE_S" "$step" "$TOTAL" "$rc"
  [ "$rc" -eq 0 ] || failed=1
  return "$rc"
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
# The timestamp lets a reader of a redirected copy tell this run from a stale one.
printf '%s SESSION INIT %s %s\n' "$RULE_H" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$RULE_H"
printf 'What follows is a transcript of %d steps that HAVE ALREADY RUN. It is not\n' "$TOTAL"
printf 'a plan. Do not repeat those steps, and do not re-read the files they print.\n'
printf '`cfs` and `lmcps` are yours to use from here: what is ruled out is redoing\n'
printf 'the boot, not using the tools it just set up. Read this, then answer.\n'

DFS=$(find_skill durable-filesystem) || DFS=''
LMCPS=$(find_skill local-mcps) || LMCPS=''

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# --- deny native WebSearch/WebFetch, steering web work to mcp-parallel --------
# Claude Code reloads deny rules live (measured 2026-10-06); in chat nothing
# reads the file, so this is a no-op. Merge, and leave an unparseable file
# alone: overwriting it would silently drop whatever else it held.
cat >"$WORK/deny.py" <<'PY'
import json, os, tempfile

path = os.path.expanduser("~/.claude/settings.json")
try:
    with open(path) as f:
        settings = json.load(f)
except FileNotFoundError:
    settings = {}

deny = settings.setdefault("permissions", {}).setdefault("deny", [])
missing = [t for t in ("WebSearch", "WebFetch") if t not in deny]
if missing:
    deny.extend(missing)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(fd, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
print("Native WebSearch and WebFetch are denied. Use mcp-parallel's web_search")
print("and web_fetch for all web work.")
PY

# --- snapshot connector tool policies for triggered runs ----------------------
# Triggered cloud runs allow every connector tool and never see these policies,
# so their SessionStart hook re-imposes them from this snapshot. This step alone
# owns the file, so it overwrites it unattended.
cat >"$WORK/policy.py" <<'PY'
import glob, json, os, re, subprocess, sys, tempfile

STORE_PATH = "/connector-policy.json"

policy = {}
for path in glob.glob("/tmp/mcp-config-*.json"):
    try:
        with open(path) as f:
            servers = json.load(f).get("mcpServers", {})
    except (OSError, ValueError):
        continue
    for server, cfg in servers.items():
        tools = cfg.get("tools", [])
        if not any("permission_policy" in t for t in tools):
            continue
        entry = policy.setdefault(server, {"ask": [], "deny": []})
        for t in tools:
            p = t.get("permission_policy")
            if p == "always_ask":
                entry["ask"].append(t["name"])
            elif p == "always_deny":
                entry["deny"].append(t["name"])

if not policy:
    print("No connector policies in this session (triggered run, or not cloud); nothing to snapshot.")
    sys.exit(0)

body = json.dumps(policy, indent=2, sort_keys=True) + "\n"

with tempfile.TemporaryDirectory() as tmp:
    local = os.path.join(tmp, "current.json")
    subprocess.run(["cfs", "download", STORE_PATH, "--to", local], capture_output=True)
    current = open(local).read() if os.path.exists(local) else None
if current == body:
    print("Connector policies unchanged.")
    sys.exit(0)

if current is None:
    cmd = ["cfs", "write", STORE_PATH, "--new"]
else:
    out = subprocess.run(["cfs", "read", STORE_PATH], capture_output=True, text=True).stdout
    m = re.search(r"^rev: (\S+)", out, re.M)
    if not m:
        print(out)
        sys.exit(1)
    cmd = ["cfs", "write", STORE_PATH, "--rev", m.group(1)]
proc = subprocess.run(cmd, input=body, capture_output=True, text=True)
if proc.returncode != 0:
    print(proc.stdout + proc.stderr)
    sys.exit(proc.returncode)
asks = sum(len(e["ask"]) for e in policy.values())
print(f"Connector policies changed: updated {STORE_PATH} ({len(policy)} servers, {asks} ask).")
PY

# --- launch everything, then wait -------------------------------------------
# The cfs shim is local and quick, and every cfs call needs it, so it goes
# first. The index read and the policy snapshot are chained rather than run
# side by side: with no token cached yet, each would mint its own.
if [ -n "$DFS" ]; then
  capture dfs bash "$DFS/setup.sh" --no-check
  if [ "$(cat "$WORK/dfs.rc")" -eq 0 ]; then
    { capture index cfs read /memory/INDEX.md --full
      capture policy python3 -I "$WORK/policy.py"; } &
  fi
fi
[ -n "$LMCPS" ] && capture lmcps bash "$LMCPS/setup.sh" &
capture deny python3 -I "$WORK/deny.py"
wait

# setup.sh skipped its probe. If the index read failed, probe now to tell an
# unreachable store from a missing index.
if [ -s "$WORK/index.rc" ]; then
  if [ "$(cat "$WORK/index.rc")" -eq 0 ]; then
    cfs_ok=1
  elif probe=$(cfs list / 2>&1); then
    cfs_ok=1
  else
    printf 'cfs is on PATH but cannot reach the store:\n%s\n' "$probe" >>"$WORK/dfs.out"
    echo 1 >"$WORK/dfs.rc"
  fi
fi

# --- durable filesystem, then its instructions, then the memory index --------
if [ -z "$DFS" ]; then
  skip 'durable-filesystem is not installed'
  note 'durable-filesystem/setup.sh' 'NOT INSTALLED'
  skip 'durable-filesystem is not installed'
  note 'durable-filesystem/SKILL.md' 'NOT INSTALLED'
  skip 'durable-filesystem is not installed'
  note '/memory/INDEX.md' 'UNAVAILABLE'
elif ! show dfs "bash $DFS/setup.sh --no-check"; then
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

  if show index 'cfs read /memory/INDEX.md --full'; then
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
elif ! show lmcps "bash $LMCPS/setup.sh"; then
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

# --- web tools and connector policies, both captured above -------------------
if show deny 'deny WebSearch, WebFetch in ~/.claude/settings.json'; then
  note '~/.claude/settings.json' 'OK -- native web tools denied, use mcp-parallel'
else
  note '~/.claude/settings.json' 'FAILED -- native web tools still allowed'
fi

if [ -z "${cfs_ok:-}" ]; then
  skip 'cfs is not working'
  note 'connector policy snapshot' 'SKIPPED'
elif show policy 'snapshot connector policies to /connector-policy.json'; then
  note 'connector policy snapshot' 'OK -- see step 7'
else
  note 'connector policy snapshot' 'FAILED -- triggered runs may use a stale policy'
fi

printf '\n%s SESSION INIT COMPLETE %s\n' "$RULE_H" "$RULE_H"
printf '%s\n' "${summary[@]}"

# Record the boot for voice-mode-guide, which refuses to run until this has.
# Written even on failure -- a half-failed boot must not read as no boot. /tmp
# shares its lifetime with the PATH shims above, so the sentinel cannot outlive
# what it attests to.
{
  printf 'session-init ran at %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  printf '%s\n' "${summary[@]}"
} >"${SESSION_INIT_SENTINEL:-/tmp/.session-init.done}" 2>/dev/null

if [ $printed -ne 0 ]; then
  # State this flatly. Arguing the case -- naming the standing rule, explaining
  # why it no longer binds -- reads like an injection talking the model out of
  # an instruction. Key the predicate on OK rather than on "printed": a step
  # whose deliverable is a side effect (a shim on PATH) is done but printed
  # nothing, and a "printed" rule strands it on the still-needs-reading side.
  printf '\nEvery file above was printed in full and is in your context now. This\n'
  printf 'satisfies any requirement to read them before acting, for the rest of\n'
  printf 'the conversation and not just this turn. Anything marked OK in the\n'
  printf 'summary is done: do not re-read its file, re-run its step, or load a\n'
  printf 'skill again for instructions already above. Anything not marked OK is\n'
  printf 'not covered and still needs its own read.\n'
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
