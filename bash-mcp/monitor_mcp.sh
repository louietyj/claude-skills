#!/usr/bin/env bash
# Poll one MCP tool and print a line only when the extracted value changes (or,
# with --match, on every poll whose value matches), so Monitor can stream it.
# Errors always print: silence means "no change", never "broken". Runs until
# killed, except that a call the claude.ai relay refuses for lack of approval
# stops it with exit 3, since retrying can't help.
#
#   monitor_mcp.sh <server> <tool> <json-args> [jq-filter] [interval-secs] [--match REGEX]
#
#   monitor_mcp.sh Interactive_Brokers_IBKR get_account_summary '{}' '.excess_liquidity' 300
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

server="${1:?server}"; tool="${2:?tool}"; args="${3:-}"; filter="${4:-.}"
[ -z "$args" ] && args='{}'
interval="${5:-30}"
match=""; [ "${6:-}" = "--match" ] && match="${7:?--match needs a regex}"

tmp=$(mktemp -d "${TMPDIR:-/tmp}/monitor-mcp.XXXX")
trap 'rm -rf "$tmp"' EXIT
prev=""; have_prev=""
while true; do
  python3 -I "$DIR/mcp_call.py" "$server" "$tool" "$args" >"$tmp/out" 2>"$tmp/err"
  rc=$?
  if [ "$rc" -eq 3 ]; then
    echo "STOPPED [$server]: $(head -c 300 "$tmp/err" | tr '\n' ' ')"
    exit 3
  elif [ "$rc" -ne 0 ]; then
    echo "ERROR [$server]: $(head -c 200 "$tmp/err" | tr '\n' ' ')"
  elif ! cur=$(jq -rc "$filter" "$tmp/out" 2>"$tmp/err"); then
    echo "ERROR [$server]: jq '$filter': $(head -c 200 "$tmp/err" | tr '\n' ' ')"
  elif [ -n "$match" ]; then
    printf '%s' "$cur" | grep -qE "$match" && echo "match [$server]: $cur"
  elif [ -z "$have_prev" ]; then
    echo "init [$server]: $cur"; prev="$cur"; have_prev=1
  elif [ "$cur" != "$prev" ]; then
    echo "changed [$server]: $prev -> $cur"; prev="$cur"
  fi
  sleep "$interval"
done
