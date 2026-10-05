#!/usr/bin/env bash
# Zip this session's transcript, tool results and sub-agent transcripts, after
# rendering each .jsonl to a claude-replay .html beside it (same name).
# Usage: export.sh [session.jsonl]   (default: the most recently written session)
set -uo pipefail

out=/mnt/user-data/outputs/transcript-export.zip
if [ $# -ge 1 ]; then
  p=$(realpath "$1")
else
  cd ~/.claude/projects || { echo "no ~/.claude/projects" >&2; exit 1; }
  # ./ prefix: project dir names start with '-'
  p=$(realpath "$(ls -1t ./*/*.jsonl | head -1)")
fi
d=${p%.jsonl}
cd "$(dirname "$p")" || exit 1

# Main transcript first, then sub-agents. Rendering is best effort: a failure
# leaves that .jsonl without an .html but never blocks the zip.
jsonls=("$p")
[ -d "$d/subagents" ] && while IFS= read -r f; do jsonls+=("$f"); done \
  < <(find "$d/subagents" -name '*.jsonl' | sort)

rendered=0 failed=0
for f in "${jsonls[@]}"; do
  if npx -y claude-replay@0.11.0 "$f" -o "${f%.jsonl}.html" \
       --title "$(basename "${f%.jsonl}")" >/dev/null 2>"${f%.jsonl}.replay.log"; then
    rendered=$((rendered + 1)); rm -f "${f%.jsonl}.replay.log"
  else
    failed=$((failed + 1)); echo "claude-replay failed on $f:" >&2; tail -5 "${f%.jsonl}.replay.log" >&2
    rm -f "${f%.jsonl}.replay.log"
  fi
done
msg="rendered $rendered of ${#jsonls[@]} transcript(s) to HTML"
[ "$failed" -gt 0 ] && msg+=", $failed failed (zipped without HTML)"
echo "$msg"

mkdir -p "$(dirname "$out")" && rm -f "$out"
items=("$(basename "$p")")
[ -f "${p%.jsonl}.html" ] && items+=("$(basename "${p%.jsonl}.html")")
[ -d "$d" ] && items+=("$(basename "$d")")
zip -qr "$out" "${items[@]}" && unzip -l "$out"
