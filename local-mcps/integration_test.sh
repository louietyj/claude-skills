#!/bin/bash
# Live tests against two real servers -- one npx, one uvx -- covering what the
# offline suite deliberately cannot: that `npx -y <pkg>` and `uvx <pkg>` are
# actually spawnable, that a cold start completes inside the timeout, and that
# real servers' framing survives the handshake.
#
#   bash integration_test.sh
#
# Needs node/npx and uv/uvx on PATH and network access to the npm and PyPI
# registries. First run pays ~20s per server for the download; later runs in the
# same environment hit the package cache.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# Run each candidate rather than just resolving it. Windows ships a `python3`
# App Execution Alias that satisfies `command -v` and then refuses to run.
LMCPS=""
for candidate in python3 python; do
  if "$candidate" -c 'import sys' >/dev/null 2>&1; then LMCPS="$candidate"; break; fi
done
[ -n "$LMCPS" ] || { echo "no working python interpreter found" >&2; exit 1; }
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

export LMCPS_HOME="$WORK/home"
export LMCPS_CONFIG="$WORK/config.json"
cat > "$LMCPS_CONFIG" <<'EOF'
{"mcpServers": {
  "everything": {
    "command": "npx", "args": ["-y", "@modelcontextprotocol/server-everything"],
    "description": "reference server: echo, arithmetic, resources, sampling"},
  "time": {
    "command": "uvx", "args": ["mcp-server-time", "--local-timezone", "Europe/London"],
    "description": "current time and timezone conversion"}
}}
EOF

pass=0 fail=0
run() { $LMCPS "$HERE/bin/lmcps.py" --timeout 240 "$@"; }

check() {  # check <name> <expected-substring> <command...>
  local name="$1" want="$2"; shift 2
  local got; got="$("$@" 2>&1)"
  if [[ "$got" == *"$want"* ]]; then
    echo "ok   $name"; pass=$((pass + 1))
  else
    echo "FAIL $name"; echo "     wanted: $want"; echo "     got:    ${got:0:400}"
    fail=$((fail + 1))
  fi
}

check "servers lists both"        "everything"        run servers
check "servers needs no spawn"    "timezone"          run servers

check "npx tools/list"            "get-sum"           run tools everything
check "npx tools/call"            "42"                run call everything get-sum '{"a": 17, "b": 25}'
check "npx echoes text content"   "hello"             run call everything echo '{"message": "hello"}'
check "npx --schema"              '"inputSchema"'     run tools everything --schema get-sum

check "uvx tools/list"            "get_current_time"  run tools time
check "uvx tools/call"            "Asia/Singapore"    run call time get_current_time '{"timezone": "Asia/Singapore"}'
check "uvx --schema"              "IANA timezone"     run tools time --schema get_current_time

# `index` against real servers: the fake cannot show that two cold npx/uvx
# spawns both survive one build, which is the whole job the cron does.
CATALOG="$WORK/catalog.json"
run index --out "$CATALOG" --built-by "integration-test" >/dev/null 2>&1
check "index wrote a catalog"        '"builtBy": "integration-test"' cat "$CATALOG"
check "index found the npx server"   "get-sum"          cat "$CATALOG"
check "index found the uvx server"   "get_current_time" cat "$CATALOG"
if grep -q inputSchema "$CATALOG"; then
  echo "FAIL index carries no schemas"; fail=$((fail + 1))
else
  echo "ok   index carries no schemas"; pass=$((pass + 1))
fi

# A dead server must not cost a live one its entry, and must not fail the build.
python - "$LMCPS_CONFIG" <<'PY'
import json, sys
p = sys.argv[1]
cfg = json.load(open(p))
cfg["mcpServers"]["broken"] = {"command": "definitely-not-a-real-binary",
                               "description": "cannot start"}
json.dump(cfg, open(p, "w"), indent=2)
PY
run index --out "$WORK/catalog2.json" --previous "$CATALOG" >/dev/null 2>&1
check "a broken server is flagged"     "definitely-not-a-real-binary" \
      cat "$WORK/catalog2.json"
check "a live server survives it"      "get-sum"  cat "$WORK/catalog2.json"

# TomTom is the motivating case for the HTTP path and the only way to exercise a
# non-allowlisted auth header against a real server. Opt-in: it needs a key.
if [ -n "${TOMTOM_KEY:-}" ]; then
  python - "$LMCPS_CONFIG" <<'PY'
import json, sys
p = sys.argv[1]
cfg = json.load(open(p))
cfg["mcpServers"]["tomtom"] = {
    "type": "http", "url": "https://mcp.tomtom.com/maps",
    "headers": {"tomtom-api-key": "${TOMTOM_KEY}"},
    "description": "geocoding, routing, live traffic"}
json.dump(cfg, open(p, "w"), indent=2)
PY
  check "tomtom header auth"   "tomtom-geocode"  run tools tomtom
  check "tomtom geocode"       "51.50"           run call tomtom tomtom-geocode \
        '{"query": "10 Downing Street, London"}'
else
  echo "skip TomTom checks (set TOMTOM_KEY to run them)"
fi

# A tool that fails in-band returns a perfectly good JSON-RPC response; it must
# still be an error here, or a failed call reads as a successful one. Assert on
# the message as well as the status -- any broken invocation exits nonzero, so
# the status alone would pass for the wrong reason.
out="$(run call everything get-structured-content '{"city": "Atlantis"}' 2>&1)"; rc=$?
if [ "$rc" -ne 0 ] && [[ "$out" == *"get-structured-content"* ]]; then
  echo "ok   in-band tool failure exits nonzero"; pass=$((pass + 1))
else
  echo "FAIL in-band tool failure exits nonzero (rc=$rc): ${out:0:300}"; fail=$((fail + 1))
fi

# The second tools/list must be served from cache. Break the config first, so a
# cache hit is the only way it can still succeed.
echo '{"mcpServers": {"time": {"command": "definitely-not-a-real-binary"}}}' > "$LMCPS_CONFIG"
check "tools cached for the conversation" "get_current_time" run tools time
check "--refresh bypasses the cache"      "command not found" run tools time --refresh

echo "---"
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
