#!/bin/bash
# One-touch web-browser boot. Installs and configures pinchtab (real Chrome
# via CLI/HTTP), starts the server, proves a page loads -- and then prints
# pinchtab's own instructions in full, so the caller's next tool call is a real
# browser command rather than another `cat`. Idempotent.
#
# Screenshots, sessions, --unscale and execute-batch live in the fork's binary,
# switched on by cli.json. What stays here is install and configuration, and
# thin wrappers for the two remote tiers (Louie's Chrome, the cloud browser).
#
# Deliberately NOT `set -e`. The stages are independent and a partial boot is
# useful: a failed cloak install must not cost the caller a working browser.
# Each stage records its own status and the closing summary reports all of them.
set -uo pipefail

TOTAL=4
step=0
summary=()
failed=0
fatal=0
docs_repeat=0
DOCS_MARK=/tmp/.web-browser-docs-printed
DOTS='.........................................'
RULE_H='════════'
RULE_S='────────'

note() {  # $1 = label, $2 = status. Called after the step number is incremented.
  summary+=("$(printf '  [%d/%d] %s %s %s' "$step" "$TOTAL" "$1" "${DOTS:${#1}}" "$2")")
}

begin() {  # $1 = what this step does, as displayed
  step=$((step + 1))
  printf '\n%s[%d/%d] %s\n' "$RULE_S" "$step" "$TOTAL" "$1"
}

end() {  # $1 = exit status of the step body
  printf '%s[%d/%d] end (exit %d)\n' "$RULE_S" "$step" "$TOTAL" "$1"
  [ "$1" -eq 0 ] || failed=1
}

# Every exit path runs this, so a boot that dies at step 1 still prints a summary.
finish() {
  printf '\n%s WEB BROWSER READY %s\n' "$RULE_H" "$RULE_H"
  printf '%s\n' "${summary[@]}"

  if [ $fatal -ne 0 ]; then
    printf '\npinchtab is not usable. Do not re-run this script and do not try to\n'
    printf 'drive the browser anyway -- tell the user which step failed. Fall back\n'
    printf 'to web_fetch or another fetch tool for the page you wanted.\n'
    exit 1
  fi

  printf '\npinchtab is live. Its SKILL.md '
  if [ $docs_repeat -ne 0 ]; then
    printf 'went into your context on an earlier run\nin this conversation'
  else
    printf 'is printed above IN FULL and is in your\ncontext now'
  fi
  printf ' -- for the rest of the conversation, not just this turn.\n'
  printf 'Do not run `pinchtab docs` again, and do not re-run any step above; this\n'
  printf 'output is a transcript of work already done, not a plan. Sessions are\n'
  printf 'handled for you: never create or export PINCHTAB_SESSION yourself.\n'

  if [ -n "$AGENT" ]; then
    printf '\n%s YOU ARE BROWSER AGENT %s %s\n' "$RULE_H" "$AGENT" "$RULE_H"
    printf 'Other agents share this browser. Start EVERY Bash call with\n\n'
    printf '  export PINCHTAB_AGENT=%s;\n\n' "$AGENT"
    printf 'Shell state does not carry over between calls, and a pinchtab command\n'
    printf 'without it drives another agent'"'"'s tab, silently. The examples above\n'
    printf 'all omit it; add it anyway. Your latest screenshot:\n'
    printf '  %s/latest-small.jpg\n' "$SHOT_DIR"
    printf 'If you re-run setup.sh, pass --subagent %s again: without it, it\n' "$AGENT"
    printf 'takes over the tab of the agent that spawned you.\n'
  else
    printf '\nYour latest screenshot is always %s/latest-small.jpg\n' "$SHOT_DIR"
  fi

  if [ -x "$WIN_SHIM" ]; then
    printf '\nLouie'"'"'s own logged-in Chrome is `pinchtab-windows`, after he approves a\n'
    printf 'grant -- see "Louie'"'"'s Chrome" in SKILL.md before using it. Its shots:\n'
    printf '  %s/latest-small.jpg\n' "$WIN_SHOT_DIR"
  fi
  if [ -x "$CLOUD_SHIM" ]; then
    printf '\nA cloud browser on a residential IP is `pinchtab-cloud`, for a site that\n'
    printf 'bans this sandbox outright -- see "Cloud browser" in SKILL.md; it bills.\n'
    printf 'Its shots: %s/latest-small.jpg\n' "$CLOUD_SHOT_DIR"
  fi

  # Last, because it is what an agent reads right before its first command.
  prefix=${AGENT:+export PINCHTAB_AGENT=$AGENT; }
  printf '\nNext -- every browser step is a line of a batch, even a single one, and\n'
  printf 'you view latest-small.jpg in the same message:\n\n'
  printf "%spinchtab execute-batch <<'EOF'\n" "$prefix"
  printf 'nav <url> --block-images\nEOF\n'

  if [ $failed -ne 0 ]; then
    printf '\nSome steps did not complete -- the summary above is the authority on\n'
    printf 'what is available. Say which part is down rather than working around it.\n'
  fi
  exit 0
}

usage() {
  printf 'usage: setup.sh [--cloak|--no-cloak] [--subagent <name>]\n' >&2
  exit 2
}

# Simultaneous runs take turns, so the second attaches to what the first set up.
# -o, because the server daemon this starts would otherwise inherit the lock and
# hold it forever.
if [ -z "${HEADLESS_BROWSER_LOCKED:-}" ] && command -v flock >/dev/null; then
  HEADLESS_BROWSER_LOCKED=1 exec flock -o /tmp/.web-browser-lock bash "$0" "$@"
fi

CLOAK=${HEADLESS_BROWSER_CLOAK:-1}
RECONFIGURE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --cloak)       CLOAK=1 RECONFIGURE=1 ;;
    --no-cloak)    CLOAK=0 RECONFIGURE=1 ;;
    --subagent=?*) PINCHTAB_AGENT=${1#*=} ;;
    --subagent)    [ -n "${2:-}" ] || usage; PINCHTAB_AGENT=$2; shift ;;
    *)             usage ;;
  esac
  shift
done
# pinchtab reads the name from the environment for this agent's tab and shots.
[ -n "${PINCHTAB_AGENT:-}" ] && export PINCHTAB_AGENT

printf '%s WEB BROWSER SETUP %s\n' "$RULE_H" "$RULE_H"
printf "What follows is a transcript of %d steps, ending with pinchtab's own\n" "$TOTAL"
printf 'instructions. Read all of it -- this is the only place those instructions\n'
printf 'get printed. Then use the browser.\n'

SKILL_DIR=$(cd "$(dirname "$0")" && pwd)
NPM_ROOT="$(npm root -g)"
REAL="$NPM_ROOT/pinchtab/bin/pinchtab"
BIN_DIR="$(dirname "$(dirname "$NPM_ROOT")")/bin"
SHIM="$BIN_DIR/pinchtab"
PINCHTAB_DOCS="$NPM_ROOT/pinchtab/skills/pinchtab"
READY_MARK=/tmp/.web-browser-ready

# Agents browsing side by side name themselves with PINCHTAB_AGENT, for a tab,
# session, shot directory and ledger each. pinchtab derives the same paths:
# <name>-shots/<agent>/ and /tmp/<name>-shots-unseen-<agent> for each tier.
AGENT=${PINCHTAB_AGENT:-}
case "$AGENT" in
  *[!A-Za-z0-9_-]*) printf 'a subagent name may hold only letters, digits, - and _\n' >&2; exit 2 ;;
esac
SHOT_DIR=${PINCHTAB_SHOT_DIR:-$HOME/pinchtab-shots}${AGENT:+/$AGENT}
SHOT_LEDGER=/tmp/pinchtab-shots-unseen${AGENT:+-$AGENT}
DOCS_MARK=$DOCS_MARK${AGENT:+-$AGENT}

# Louie's own Chrome, reached through his laptop's gate; installed only when the
# skill ships windows.json (host and gate key).
WINDOWS_JSON=$SKILL_DIR/windows.json
WINDOWS_PROTOCOL=1   # PROTOCOL in windows-gate's pinchtab_windows.py
WIN_SHIM=$BIN_DIR/pinchtab-windows
WIN_CONF=/tmp/.pinchtab-windows.conf
WIN_HEADERS=/tmp/.pinchtab-windows-headers
WIN_SHOT_DIR=${PINCHTAB_SHOT_DIR:-$HOME/pinchtab-windows-shots}${AGENT:+/$AGENT}

# A Browser Use cloud browser on a residential IP, for a site that bans this
# sandbox's datacenter IP outright; installed only when there is a key.
CLOUD_KEY=${BROWSER_USE_API_KEY:-$(cat "$SKILL_DIR/browser-use.key" 2>/dev/null | tr -d '\r\n')}
CLOUD_SHIM=$BIN_DIR/pinchtab-cloud
CLOUD_DIR=/tmp/.pinchtab-cloud
CLOUD_PORT=9870
CLOUD_SHOT_DIR=${PINCHTAB_SHOT_DIR:-$HOME/pinchtab-cloud-shots}${AGENT:+/$AGENT}

setup_cloud() {
  if [ -z "$CLOUD_KEY" ]; then
    note 'cloud browser' 'not configured -- no browser-use.key in the skill'
    return
  fi
  mkdir -p "$CLOUD_DIR" && chmod 700 "$CLOUD_DIR"
  printf '%s' "$CLOUD_KEY" > "$CLOUD_DIR/key"
  # The bridge gets its own config: its own state dir, so it never touches the
  # local server's profile, and trust in Browser Use's in-cluster proxy, which
  # every page of that browser comes back from (10.0.x.x).
  if ! python3 - "$("$REAL" config path)" "$CLOUD_DIR" <<'PY'
import json, sys
src, d = sys.argv[1:]
c = json.load(open(src))
c.setdefault("server", {})["stateDir"] = d + "/state"
sec = c.setdefault("security", {})
sec["trustedProxyCIDRs"] = ["10.0.0.0/8"]
sec["attach"] = {**sec.get("attach", {}), "enabled": True,
                 "allowHosts": ["*.cdp.browser-use.com"], "allowSchemes": ["https", "wss"]}
json.dump(c, open(d + "/config.json", "w"), indent=2)
open(d + "/token", "w").write(c["server"]["token"])
PY
  then
    note 'cloud browser' 'FAILED -- could not write its bridge config'
    return
  fi
  note 'cloud browser' "OK -- pinchtab-cloud, Browser Use key ...${CLOUD_KEY: -4}; use it only when pinchtab is blocked"
}

setup_windows() {
  if [ ! -f "$WINDOWS_JSON" ]; then
    note "Louie's Chrome" 'not configured -- no windows.json in the skill'
    return
  fi
  # The shim reads both files on every call; neither is ever printed.
  if ! python3 - "$WINDOWS_JSON" "$WIN_CONF" "$WIN_HEADERS" <<'PY'
import json, os, sys
src, conf, headers = sys.argv[1:]
c = json.load(open(src))
host = c["host"].strip().rstrip("/")
base = host if host.startswith(("https://", "http://")) else "https://" + host  # http: dev only
os.umask(0o077)
with open(headers, "w") as f:
    f.write(f"X-Gate-Key: {c['gate_key']}\n")
with open(conf, "w") as f:
    f.write(f"WIN_BASE='{base}'\n")
PY
  then
    note "Louie's Chrome" 'FAILED -- windows.json is unreadable'
    return
  fi
  local base status
  base=$(. "$WIN_CONF"; printf '%s' "$WIN_BASE")
  status=$(curl -sS -m 15 -H @"$WIN_HEADERS" "$base/gate/status" 2>&1)
  case "$status" in
    *"\"protocol\": $WINDOWS_PROTOCOL"*) note "Louie's Chrome" 'OK -- pinchtab-windows, his laptop answers' ;;
    *'"protocol"'*) note "Louie's Chrome" "MISMATCH -- his gate speaks another protocol: $status" ;;
    *) note "Louie's Chrome" 'UNREACHABLE now -- laptop asleep or gate down; pinchtab-windows will say so' ;;
  esac
}

# --- 4. pinchtab's own instructions, in full ---------------------------------
print_docs() {
  begin 'pinchtab docs'
  if [ $fatal -ne 0 ]; then
    printf 'SKIPPED -- pinchtab does not work; its instructions would be unusable.\n'
    end 1
    note 'pinchtab SKILL.md' 'SKIPPED'
    finish
  fi

  # The sandbox is per-conversation, so this marker means the file was printed to
  # THIS caller. Escalating to --cloak is a second run, and instructions it
  # already has are the most expensive thing the script could reprint.
  if [ -f "$DOCS_MARK" ]; then
    printf 'SKIPPED -- already printed in full on an earlier run in this\n'
    printf 'conversation. Scroll back for it. If it is genuinely gone from your\n'
    printf 'context, `rm %s` and run this script again.\n' "$DOCS_MARK"
    docs_repeat=1
    end 0
    note 'pinchtab SKILL.md' 'OK -- printed on an earlier run'
  elif "$SHIM" docs 2>/dev/null; then
    # The fork's own docs, from the binary that runs them.
    touch "$DOCS_MARK"
    end 0
    note 'pinchtab SKILL.md' 'OK -- printed in full above'
  else
    # The npm build: no execute-batch, no screenshots. Its upstream docs at least
    # describe the commands it does have.
    cat "$PINCHTAB_DOCS/SKILL.md"
    printf '\nThis is the upstream build: no execute-batch and no automatic\n'
    printf 'screenshots. Run commands one at a time and take `screenshot` yourself.\n'
    end 1
    note 'pinchtab SKILL.md' 'DEGRADED -- upstream docs; the fork did not install'
  fi

  # A subagent never loaded this skill, so it gets the skill's own SKILL.md too:
  # from "## Then" on, minus the section addressed to whoever spawns subagents.
  if [ -n "$AGENT" ] && [ $docs_repeat -eq 0 ]; then
    printf '\n%s THIS SKILL'"'"'S OWN SKILL.md (web-browser) %s\n' "$RULE_H" "$RULE_H"
    printf 'Where it and the file above disagree, this one wins.\n\n'
    awk '/^## /{keep = !/^## Subagents/} /^## Then/{on = 1} on && keep' "$SKILL_DIR/SKILL.md"
  fi
}

open_session() {
  # The whole chain -- server, browser launch, the agent's session (pinchtab
  # creates it on first use) and its screenshot -- because a misconfigured
  # browser binary fails here or else on the caller's first real page. The title
  # too: a nav that lands on Chrome's own error page (a dead proxy) exits 0.
  NAV_OUT=$("$SHIM" nav https://example.com --block-images 2>&1); nav_rc=$?
  NAV_TITLE=$("$SHIM" title 2>/dev/null)
  if [ $nav_rc -eq 0 ] && [ "$NAV_TITLE" = "Example Domain" ]; then
    rm -f "$SHOT_LEDGER"   # the caller's first `shots` shouldn't list example.com
    touch "$READY_MARK"
    echo "server up, session ready, test nav OK"
    end 0
    note 'server + session' 'OK -- session is already initialized'
  else
    echo "server up, but the test nav FAILED:" >&2
    grep '^Error' <<<"$NAV_OUT" >&2
    echo "page title: '$NAV_TITLE' (expected 'Example Domain')" >&2
    end 1
    note 'server + session' 'FAILED -- the browser cannot load a page'
    fatal=1
  fi
}

# --- attach: joining a browser this sandbox already runs ----------------------
# The full setup restarts the server, closing every other agent's tab mid-flow,
# so once one has passed its test nav, later runs only open their own session.
# `docs` exists only in a fork build this script linked in, so an older install
# (a shell shim, or npm's launcher) gets the full setup.
if [ $RECONFIGURE -eq 0 ] && [ -f "$READY_MARK" ] && [ -L "$SHIM" ] && "$SHIM" docs >/dev/null 2>&1; then
  TOTAL=2
  begin "open a session${AGENT:+ for agent $AGENT} in the running browser"
  open_session
  if [ $fatal -eq 0 ]; then
    print_docs
    finish
  fi
  # A browser that can't load a page has no tabs worth keeping.
  printf '\nThe running browser failed its test nav; setting it up from scratch.\n'
  summary=() step=0 failed=0 fatal=0 TOTAL=4
fi
rm -f "$READY_MARK"

# --- 1. the package ----------------------------------------------------------
begin 'install pinchtab'

# A shell shim from an older run of this script would open a session before the
# server exists, so drop it and drive setup through $REAL directly. `pinchtab` is
# linked to the binary at the end.
[ -f "$SHIM" ] && [ ! -L "$SHIM" ] && rm -f "$SHIM" || true

# Check the package, not `command -v pinchtab`: the shim satisfies that test
# even when the package underneath it is missing.
if [ -x "$REAL" ]; then
  echo "already installed: $("$REAL" --version 2>/dev/null || echo unknown)"
  rc=0
else
  # pinchtab's postinstall copies its skill into ~/.claude/skills/ if that exists,
  # which in Cowork and cloud Claude Code is a live skill competing with this one.
  PLANTED="$HOME/.claude/skills/pinchtab"
  [ -e "$PLANTED" ] && had_planted=1 || had_planted=0
  npm install -g pinchtab
  rc=$?
  [ $had_planted -eq 0 ] && rm -rf "$PLANTED"
fi
end $rc
if [ $rc -ne 0 ] || [ ! -x "$REAL" ]; then
  note 'pinchtab install' 'FAILED -- nothing else can run'
  fatal=1
  finish
fi

# Swap in our fork's build over the one npm just fetched. Upstream ships the
# capsolver solver as an unimplemented stub, so captcha pages get detected and
# then abandoned; the fork implements it and fixes the detection and reporting
# bugs around it. Only the binary is replaced -- the npm package's bundled docs
# and layout, which later steps read, stay as they are.
FORK_BIN_URL=${PINCHTAB_FORK_BIN_URL:-https://github.com/louietyj/pinchtab/releases/latest/download/pinchtab-linux-amd64}
FORK_NOTE='upstream npm build'
if [ "${PINCHTAB_USE_FORK:-1}" = "1" ]; then
  MANAGED=$(find "$NPM_ROOT/pinchtab/.managed-bin" -type f -name 'pinchtab-linux-amd64' 2>/dev/null | head -1)
  if [ -z "$MANAGED" ]; then
    echo "no managed binary to replace; staying on the npm build" >&2
  elif curl -fsSL --max-time "${SETUP_TIMEOUT:-300}" -o /tmp/pinchtab-fork "$FORK_BIN_URL" \
       && [ -s /tmp/pinchtab-fork ]; then
    # Replace by rename so a half-written download can never become the binary.
    chmod +x /tmp/pinchtab-fork && mv -f /tmp/pinchtab-fork "$MANAGED"
    echo "fork build installed: $MANAGED"
    FORK_NOTE="fork build ($("$REAL" --version 2>/dev/null || echo unknown))"
  else
    echo "fork build download failed; continuing on the npm build -- captcha" >&2
    echo "solving will not work, everything else will" >&2
    rm -f /tmp/pinchtab-fork
    failed=1
    FORK_NOTE='FORK DOWNLOAD FAILED -- no captcha solving'
  fi
fi
note 'pinchtab install' "OK -- $FORK_NOTE"

# --- 2. a browser for it to drive --------------------------------------------
begin 'find a browser runtime'
BROWSER_NOTE=''
# Step 3 restarts it anyway; stopped now, the config writes below don't each
# print "restart it to apply".
"$REAL" server stop >/dev/null 2>&1 || true

# A patched Chromium that ordinary bot detection does not reject on
# fingerprint. Default at 17.5s cold against 6.6s for plain Chrome, ~12s of
# which is the download; it was only ever expensive because npm re-solved.
if [ $CLOAK -eq 1 ]; then
  t0=$SECONDS

  # npm is the expensive call, not the download: 215s to conclude "up to date"
  # on a complete tree, against 1s for ensureBinary() to return the cached
  # binary. Every branch below exists to avoid reaching npm.
  for c in /root/.cloakbrowser/*/chrome "$HOME"/.cloakbrowser/*/chrome; do
    [ -x "$c" ] && { CLOAK_BIN=$c; break; }
  done

  if [ -n "${CLOAK_BIN:-}" ]; then
    echo "cloakbrowser binary already downloaded in this sandbox"
  else
    mkdir -p /tmp/pinchtab-cloak && cd /tmp/pinchtab-cloak
    npm_ok=1
    if [ -d node_modules/cloakbrowser ]; then
      echo "cloakbrowser package already installed; skipping npm"
    else
      echo "installing cloakbrowser: npm, then a few hundred MB of patched"
      echo "Chromium, cached in this sandbox afterwards. Budget ~${SETUP_TIMEOUT:-300}s; past"
      echo "that this step gives up and the boot continues on plain Chrome."
      echo "It is not hung."
      # `npm ci` fetches the 7 pinned tarballs and resolves nothing. Without a
      # lockfile npm re-solves the tree on every run -- that was the 215s.
      cp "$SKILL_DIR/cloak/package.json" "$SKILL_DIR/cloak/package-lock.json" . 2>/dev/null
      # Timed, and progress left on stderr: a silenced multi-minute step is
      # indistinguishable from a wedged one, and reads as a hang.
      timeout "${SETUP_TIMEOUT:-300}" npm ci --prefer-offline --no-fund --no-audit || npm_ok=0
      if [ $npm_ok -eq 0 ]; then
        # A yanked version or an npm too old for lockfileVersion 3 should cost
        # time, not cloak mode.
        echo "npm ci failed -- retrying unpinned, which is the slow path" >&2
        npm_ok=1
        rm -f package-lock.json
        timeout "${SETUP_TIMEOUT:-300}" npm install --prefer-offline --no-fund --no-audit \
          cloakbrowser || npm_ok=0
      fi
      echo "npm: $((SECONDS - t0))s elapsed"
    fi
    if [ $npm_ok -eq 1 ]; then
      # binaryInfo()'s shape is not contractual, so take whichever string field
      # names an existing file rather than assuming a key.
      CLOAK_BIN=$(timeout "${SETUP_TIMEOUT:-300}" node -e '
        import("cloakbrowser").then(async m => {
          await m.ensureBinary();
          const info = m.binaryInfo() || {};
          const fs = require("fs");
          for (const v of Object.values(info))
            if (typeof v === "string" && fs.existsSync(v) && fs.statSync(v).isFile())
              return console.log(v);
          process.exit(1);
        }).catch(e => { console.error(String(e)); process.exit(1); });
      ' | tail -1)
    fi
  fi
  echo "cloak stage: $((SECONDS - t0))s elapsed"
  if [ -n "${CLOAK_BIN:-}" ] && [ -x "$CLOAK_BIN" ]; then
    # A fixed seed makes every sandbox the same device on a different IP. Random per
    # sandbox, kept across re-runs so one conversation stays one device to a site.
    SEED_FILE=/tmp/.web-browser-seed
    CLOAK_SEED=${CLOAK_SEED:-$(cat "$SEED_FILE" 2>/dev/null)}
    CLOAK_SEED=${CLOAK_SEED:-$(( (RANDOM << 15 | RANDOM) % 90000 + 10000 ))}
    printf '%s' "$CLOAK_SEED" > "$SEED_FILE"
    "$REAL" config set browser.binary "$CLOAK_BIN" >/dev/null
    "$REAL" config set browser.cloak.fingerprintSeed "$CLOAK_SEED" >/dev/null
    "$REAL" config set browser.cloak.platform "${CLOAK_PLATFORM:-windows}" >/dev/null
    "$REAL" config set browser.cloak.timezone "${CLOAK_TIMEZONE:-America/New_York}" >/dev/null
    "$REAL" config set browser.cloak.locale "${CLOAK_LOCALE:-en-US}" >/dev/null
    # Fixed, not random: past ~1.15MP claude.ai shrinks screenshots and clicks
    # read off them land short (see README). Older builds lack the key.
    "$REAL" config set browser.cloak.windowSize "${CLOAK_WINDOW:-1440x900}" >/dev/null 2>&1 || true
    # Flipped LAST, and only once the binary is confirmed on disk: pointing
    # browsers.default at a runtime that isn't there breaks every later nav.
    "$REAL" config set browsers.default cloak >/dev/null
    echo "cloak runtime: $CLOAK_BIN"
    BROWSER_NOTE="OK -- cloakbrowser, seed $CLOAK_SEED"
  else
    echo "cloakbrowser install failed; falling back to ordinary Chrome" >&2
    CLOAK=0
    BROWSER_NOTE='CLOAK FAILED -- fell back to plain Chrome'
    failed=1
  fi
fi

if [ $CLOAK -eq 0 ]; then
  # A previous cloak run in this sandbox left browsers.default set, and
  # --no-cloak has to mean it. Best-effort: an older pinchtab refuses the write.
  "$REAL" config set browsers.default chrome >/dev/null 2>&1 || true

  # Most sandboxes ship a Chromium. If not, pull puppeteer's bundled build.
  if "$REAL" doctor 2>&1 | grep -qi "OK.*chrome_present"; then
    echo "system Chrome found"
  else
    echo "no system Chrome; pulling puppeteer's build (also a few minutes cold)"
    mkdir -p /tmp/pinchtab-chrome && cd /tmp/pinchtab-chrome
    npm init -y >/dev/null 2>&1
    timeout "${SETUP_TIMEOUT:-300}" npm install --no-fund --no-audit puppeteer
    CHROME_BIN=$(find /tmp/pinchtab-chrome /root/.cache/puppeteer "$HOME/.cache/puppeteer" \
      -type f -path "*chrome-linux64/chrome" 2>/dev/null | head -1)
    [ -n "$CHROME_BIN" ] && "$REAL" config set browser.binary "$CHROME_BIN" >/dev/null
    echo "downloaded Chrome: ${CHROME_BIN:-none found}"
  fi
  [ -n "$BROWSER_NOTE" ] || BROWSER_NOTE='OK -- plain Chrome'
fi

# Open up pinchtab's capability gates. These are Chrome features, gated by
# default for people running pinchtab on their own machine; in a disposable
# sandbox there's nothing for them to expose.
"$REAL" security down >/dev/null
for k in allowClipboard allowStateExport allowFileScheme; do
  "$REAL" config set "security.$k" true >/dev/null
done
"$REAL" config set security.allowedDomains '*' >/dev/null

# Cowork routes all egress through a proxy on 127.0.0.1, so every page comes
# back from a loopback IP, which pinchtab's SSRF guard 403s unless told the
# proxy is trusted. `security down` leaves this one alone.
LOOPBACK_PROXY=false
for p in "${HTTPS_PROXY:-}" "${https_proxy:-}" "${HTTP_PROXY:-}" "${http_proxy:-}" \
         "${ALL_PROXY:-}" "${all_proxy:-}"; do
  case "$p" in *://127.*|*://localhost*|*://\[::1\]*) LOOPBACK_PROXY=true ;; esac
done
"$REAL" config set security.trustLoopbackProxy "$LOOPBACK_PROXY" >/dev/null

# Captcha solving. Keys are never committed: each comes from the environment, or
# from an untracked key file beside this script. CapSolver solves reCAPTCHA,
# Turnstile, AWS WAF, GeeTest and MTCaptcha; 2Captcha is the only one left that
# solves hCaptcha and FunCaptcha, and backs CapSolver up on the rest. With neither
# key cloak's fingerprint is all that carries a page -- enough for risk-scored
# anti-bot (Cloudflare, DataDome) but not for a site that gates every visitor
# behind a captcha, archive.today being the common one.
SOLVER_NOTE='no key -- captcha solving off'
CAPSOLVER_KEY=${CAPSOLVER_API_KEY:-}
[ -n "$CAPSOLVER_KEY" ] || CAPSOLVER_KEY=$(cat "$SKILL_DIR/capsolver.key" 2>/dev/null | tr -d '\r\n')
TWOCAPTCHA_KEY=${TWOCAPTCHA_API_KEY:-}
[ -n "$TWOCAPTCHA_KEY" ] || TWOCAPTCHA_KEY=$(cat "$SKILL_DIR/2captcha.key" 2>/dev/null | tr -d '\r\n')
SOLVERS='' KEYS_NOTE=''
if [ -n "$CAPSOLVER_KEY" ]; then
  "$REAL" config set autoSolver.external.capsolverKey "$CAPSOLVER_KEY" >/dev/null 2>&1
  SOLVERS=capsolver, KEYS_NOTE="capsolver ...${CAPSOLVER_KEY: -4}"
fi
if [ -n "$TWOCAPTCHA_KEY" ]; then
  "$REAL" config set autoSolver.external.twoCaptchaKey "$TWOCAPTCHA_KEY" >/dev/null 2>&1
  SOLVERS=${SOLVERS}twocaptcha, KEYS_NOTE="${KEYS_NOTE:+$KEYS_NOTE, }2captcha ...${TWOCAPTCHA_KEY: -4}"
fi
if [ -n "$SOLVERS" ]; then
  "$REAL" config set autoSolver.enabled true >/dev/null
  "$REAL" config set autoSolver.solvers "${SOLVERS}cloudflare,nocaptcha,semantic,jschallenge" >/dev/null
  "$REAL" config set autoSolver.maxAttempts 3 >/dev/null
  # A reCAPTCHA image challenge routinely runs past 60s. The 30s default kills
  # the poll after CapSolver has already been paid for the solve.
  "$REAL" config set autoSolver.solverTimeoutSec 150 >/dev/null
  # A failed solve otherwise parks the tab in paused_handoff and 409s every
  # later action. There is no operator here to hand off to, so that leaves the
  # caller wedged on a page it could still read.
  "$REAL" config set autoSolver.handoffOnFailure false >/dev/null 2>&1
  # Otherwise nav returns while the solve is still running and says nothing about
  # it: the caller acts on an unsolved page, or succeeds later by luck and has no
  # idea a solve happened. Awaiting only costs time on a page carrying a
  # challenge, which is unusable until it is solved anyway.
  "$REAL" config set autoSolver.awaitOnNavigate true >/dev/null 2>&1
  SOLVER_NOTE="OK -- keys: $KEYS_NOTE"
fi
end 0
note 'browser runtime' "$BROWSER_NOTE"
note 'captcha solver' "$SOLVER_NOTE"

# --- 3. server, the pinchtab link and the remote-tier wrappers --------------
begin 'start server and mint a session'

"$REAL" server stop >/dev/null 2>&1 || true
"$REAL" server restart >/dev/null
sleep 2

# npm's bin/pinchtab is a Node launcher that exits 0 whatever the binary
# returned, so `pinchtab` is linked straight to the binary and callers get real
# exit codes.
GO_BIN=$(find "$NPM_ROOT/pinchtab/.managed-bin" -type f -name 'pinchtab-linux-amd64' 2>/dev/null | head -1)
[ -x "$GO_BIN" ] || GO_BIN=$REAL

# Screenshots after every page-changing command, and one agent session per
# PINCHTAB_AGENT kept across bash calls (recreated if it goes stale).
python3 - "$(dirname "$("$REAL" config path)")/cli.json" <<'PY'
import json, sys
json.dump({"autoshot": True, "stickySession": True}, open(sys.argv[1], "w"))
PY

# Linked LAST and unconditionally, because `npm install -g` recreates the bin
# symlink and would otherwise wipe it. The `rm -f` is load-bearing: that entry is
# a SYMLINK into the package, so writing through it would overwrite pinchtab's
# own entry point and brick the install with no backup.
rm -f "$SHIM"
ln -s "$GO_BIN" "$SHIM"
ln -sf "$GO_BIN" "$BIN_DIR/pinchtab-local"

# The remote tiers, `pinchtab-windows` and `pinchtab-cloud`: one body, two
# headers. Each reaches its browser through its own server and token, then runs
# the same binary, which keeps its shots apart by PINCHTAB_NAME.
wrapper_header() {  # $1 tier, $2 command name
  cat <<SHIM_EOF
#!/bin/sh
# $2 -- installed by web-browser/setup.sh. Do not edit in place;
# setup.sh rewrites it on every run.
REAL=$GO_BIN
TIER=$1
NAME=$2
WIN_CONF=$WIN_CONF
WIN_HEADERS=$WIN_HEADERS
CLOUD_DIR=$CLOUD_DIR
CLOUD_PORT=$CLOUD_PORT
SHIM_EOF
}
SHIM_BODY=$(mktemp)
cat > "$SHIM_BODY" <<'SHIM_EOF'
A=${PINCHTAB_AGENT:-}
PINCHTAB_NAME=$NAME; export PINCHTAB_NAME

json() { python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get(sys.argv[1]) or '')" "$1" 2>/dev/null; }

# A Browser Use cloud browser on a residential IP, through a bridge in this
# sandbox. Any command starts one when none is running. It ends itself
# CLOUD_MINUTES after it started: Browser Use can't extend a browser, and a
# timer here would not survive the sandbox being suspended between turns.
# Idle time costs $0.02/hour; traffic is the real cost.
if [ "$TIER" = cloud ]; then
  [ -s "$CLOUD_DIR/key" ] || { echo "$NAME: not set up -- the skill has no browser-use.key" >&2; exit 1; }
  export PINCHTAB_HINTS=off
  B=$CLOUD_DIR/browser.json
  END=$CLOUD_DIR/ends-at
  MINUTES=${PINCHTAB_CLOUD_MINUTES:-60}
  API=https://api.browser-use.com/api/v4/browsers
  TOKEN=$(cat "$CLOUD_DIR/token")
  api() { curl -sS -m 60 -H "X-Browser-Use-API-Key: $(cat "$CLOUD_DIR/key")" -H 'Content-Type: application/json' "$@"; }
  bridge_up() { curl -sf -m 5 -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$CLOUD_PORT/health" >/dev/null; }
  attach() {  # the bridge, to the browser in $B
    for p in $(pgrep -f "^$REAL bridge --cdp-attach"); do kill "$p" 2>/dev/null; done
    PINCHTAB_CONFIG=$CLOUD_DIR/config.json setsid nohup "$REAL" bridge --cdp-attach "$(json cdpUrl < "$B")" \
      --port "$CLOUD_PORT" > "$CLOUD_DIR/bridge.log" 2>&1 < /dev/null &
    i=0
    until bridge_up; do
      i=$((i + 1))
      [ $i -ge 30 ] && return 1
      sleep 1
    done
  }
  stop_cloud() {
    for p in $(pgrep -f "^$REAL bridge --cdp-attach"); do kill "$p" 2>/dev/null; done
    rm -f "$END"
    [ -s "$B" ] || return 0
    api -X PATCH "$API/$(json id < "$B")" --data '{"action":"stop"}' | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    cost = float(d.get("proxyCost") or 0) + float(d.get("browserCost") or 0)
    print("Stopped. Billed so far: %.1f MB of residential traffic, $%.3f in all (the meter lags; the dashboard has the final figure)." % (float(d.get("proxyUsedMb") or 0), cost))
except Exception:
    print("Stop sent; Browser Use did not report the usage.")'
    rm -f "$B"
  }
  start_cloud() {  # $1 country
    stop_cloud > /dev/null
    # 1440x900 like the local browser, so its shots stay under claude.ai's
    # shrink limit. Its own captcha solver is off: pinchtab's runs as usual.
    api -X POST "$API" --data "{\"proxyCountryCode\":\"$1\",\"timeout\":$MINUTES,\"browserScreenWidth\":1440,\"browserScreenHeight\":900,\"solveCaptchas\":false}" > "$B"
    if [ -z "$(json cdpUrl < "$B")" ]; then
      echo "$NAME: Browser Use refused: $(cat "$B")" >&2
      rm -f "$B"
      return 1
    fi
    # A minute early, so a command never lands on a browser about to end.
    echo $(( $(date +%s) + MINUTES * 60 - 60 )) > "$END"
    printf '%s' "$1" > "$CLOUD_DIR/country"
    if ! attach; then
      echo "$NAME: the bridge never attached. Its log:" >&2
      tail -5 "$CLOUD_DIR/bridge.log" >&2
      stop_cloud >&2
      return 1
    fi
    echo "$NAME: started a cloud browser on a $1 residential IP, ending by itself in $MINUTES minutes. A new browser has no page loaded: nav first." >&2
  }
  # A live browser whose bridge died with a suspended sandbox is re-attached,
  # tabs and all; an ended or lost one is replaced.
  ensure() {
    if [ -s "$B" ] && [ "$(date +%s)" -lt "$(cat "$END" 2>/dev/null || echo 0)" ]; then
      bridge_up || attach || start_cloud "$(cat "$CLOUD_DIR/country" 2>/dev/null || echo us)"
    else
      start_cloud "$(cat "$CLOUD_DIR/country" 2>/dev/null || echo us)"
    fi
  }
  case "${1:-}" in
    start)
      country=us
      [ "${2:-}" = --country ] && country=${3:?usage: $NAME start [--country us]}
      if [ "$country" = "$(cat "$CLOUD_DIR/country" 2>/dev/null)" ]; then ensure; else start_cloud "$country"; fi
      exit $? ;;
    stop)
      stop_cloud
      exit 0 ;;
  esac
  ensure || exit 1
  unset PINCHTAB_SESSION
  PINCHTAB_SERVER=http://127.0.0.1:$CLOUD_PORT PINCHTAB_TOKEN=$TOKEN
  XDG_STATE_HOME=/tmp/pinchtab-cloud-state${A:+-$A}
  export PINCHTAB_SERVER PINCHTAB_TOKEN XDG_STATE_HOME
fi

# Louie's Chrome: a bridge on his laptop behind a gate that wants a grant he
# approved. Bridges have no sessions; the CLI's current-tab file tracks the tab.
if [ "$TIER" = windows ]; then
  # shellcheck source=/dev/null
  . "$WIN_CONF" 2>/dev/null || { echo "$NAME: not set up -- the skill has no windows.json" >&2; exit 1; }
  # Advisories only, such as "no agent session", which a bridge can never have;
  # occurrence hints (captchas, stale refs) still print.
  export PINCHTAB_HINTS=off
  G=/tmp/.pinchtab-windows-grant
  RQ=/tmp/.pinchtab-windows-request
  gate() { curl -sS -m 75 -H @"$WIN_HEADERS" "$@"; }
  case "${1:-}" in
    grant)
      case "${2:-}" in
        --wait)
          rid=$(cat "$RQ" 2>/dev/null)
          [ -n "$rid" ] || { echo "$NAME: no request pending; run: $NAME grant \"<purpose>\"" >&2; exit 1; }
          # Under claude.ai's 300 s cap on one bash call, in polls under
          # Cloudflare's 100 s limit on one request.
          end=$(( $(date +%s) + 240 ))
          while :; do
            resp=$(gate "$WIN_BASE/grant/$rid?wait=55")
            case "$(printf '%s' "$resp" | json state)" in
              pending)
                [ "$(date +%s)" -lt "$end" ] && continue
                echo "$NAME: still waiting for Louie after 4 minutes. Ask him, then run $NAME grant --wait again." >&2
                exit 1 ;;
              approved)
                printf '%s' "$resp" | json token > "$G"; chmod 600 "$G"; rm -f "$RQ"
                bridge=$(printf '%s' "$resp" | json bridge_version)
                mine=$("$REAL" --version 2>/dev/null | awk '{print $NF}')
                echo "Approved. $NAME now drives Louie's Chrome until the grant goes idle for 30 minutes."
                [ "$bridge" = "$mine" ] || echo "WARNING: his bridge runs pinchtab $bridge, this sandbox $mine; commands may misbehave. Tell Louie."
                exit 0 ;;
              denied)
                rm -f "$RQ"
                echo "$NAME: Louie denied the request, or left it unanswered for 2 minutes. Don't ask again unless he says to." >&2
                exit 1 ;;
              error)
                rm -f "$RQ"
                echo "$NAME: approved, but his laptop could not attach to Chrome: $(printf '%s' "$resp" | json message)" >&2
                exit 1 ;;
              *)
                echo "$NAME: unexpected answer from the gate: $resp" >&2
                exit 1 ;;
            esac
          done ;;
        ''|-*)
          echo "usage: $NAME grant \"<what you need Louie's browser for>\" | $NAME grant --wait" >&2
          exit 2 ;;
        *)
          code=$(python3 -c 'import secrets; print("".join(secrets.choice("ACDEFHJKLMNPRTUVWXY34679") for _ in range(4)))')
          body=$(python3 -c 'import json,sys; print(json.dumps({"purpose": sys.argv[1], "code": sys.argv[2], "agent": sys.argv[3]}))' \
            "$2" "$code" "${PINCHTAB_AGENT:-claude}")
          resp=$(gate -H 'Content-Type: application/json' --data "$body" "$WIN_BASE/grant")
          rid=$(printf '%s' "$resp" | json request_id)
          if [ -z "$rid" ]; then
            echo "$NAME: the request was refused: ${resp:-no answer -- is Louie's laptop awake?}" >&2
            exit 1
          fi
          printf '%s' "$rid" > "$RQ"
          echo "Requested. Approval code: $code"
          echo "Tell Louie now, in chat: \"approve code $code on your laptop\"."
          echo "Then run: $NAME grant --wait"
          exit 0 ;;
      esac ;;
    release)
      [ -s "$G" ] && gate -X DELETE -H "Authorization: Bearer $(cat "$G")" "$WIN_BASE/grant" >/dev/null
      rm -f "$G"
      echo "Released; $NAME needs a new grant from here on."
      exit 0 ;;
    active-tab)
      [ -s "$G" ] || { echo "$NAME: no grant; run: $NAME grant \"<purpose>\"" >&2; exit 1; }
      gate -H "Authorization: Bearer $(cat "$G")" "$WIN_BASE/active-tab" | python3 -c '
import json, sys
d = json.load(sys.stdin)
if "tabs" not in d:
    sys.exit(d.get("message") or str(d))
for t in d["tabs"]:
    mark = "FOCUSED" if t["focused"] else "visible" if t["visible"] else "-"
    print("%-8s %s  %s  %s" % (mark, t["id"], t["title"][:60], t["url"][:80]))'
      exit $? ;;
  esac
  [ -s "$G" ] || { echo "$NAME: no grant. Run: $NAME grant \"<what you need Louie's browser for>\"" >&2; exit 1; }
  unset PINCHTAB_SESSION
  PINCHTAB_SERVER=$WIN_BASE PINCHTAB_TOKEN=$(cat "$G")
  PINCHTAB_HEADERS=$(cat "$WIN_HEADERS")
  # The CLI's current-tab cache, one per agent so subagents don't share a tab.
  XDG_STATE_HOME=/tmp/pinchtab-windows-state${A:+-$A}
  export PINCHTAB_SERVER PINCHTAB_TOKEN PINCHTAB_HEADERS XDG_STATE_HOME
fi

[ "$TIER" = windows ] || exec "$REAL" "$@"
# The grant ran out, was released, or Louie revoked it. A batch reports a step's
# error on stdout, so both streams are checked.
O=$(mktemp) E=$(mktemp)
trap 'rm -f "$O" "$E"' EXIT
"$REAL" "$@" >"$O" 2>"$E"; rc=$?
cat "$O"; cat "$E" >&2
if grep -qE 'grant_(required|expired)' "$O" "$E"; then
  rm -f "$G"
  echo "$NAME: the grant is gone (idle 30 min, released, or Louie revoked it). Ask again: $NAME grant \"<purpose>\"" >&2
fi
exit $rc
SHIM_EOF
rm -f "$WIN_SHIM"
if [ -f "$WINDOWS_JSON" ]; then
  { wrapper_header windows pinchtab-windows; cat "$SHIM_BODY"; } > "$WIN_SHIM"
  chmod +x "$WIN_SHIM"
fi
rm -f "$CLOUD_SHIM"
if [ -n "$CLOUD_KEY" ]; then
  { wrapper_header cloud pinchtab-cloud; cat "$SHIM_BODY"; } > "$CLOUD_SHIM"
  chmod +x "$CLOUD_SHIM"
fi
rm -f "$SHIM_BODY"

open_session
setup_windows
setup_cloud
print_docs
finish
