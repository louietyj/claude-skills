#!/bin/bash
# One-touch headless-browser boot. Installs and configures pinchtab (real Chrome
# via CLI/HTTP), starts the server, mints a session -- and then prints pinchtab's
# own bundled instructions in full, so the caller's next tool call is a real
# browser command rather than another `cat`. Idempotent.
#
# Deliberately NOT `set -e`. The stages are independent and a partial boot is a
# useful outcome: a failed cloak install must not cost the caller a working
# ordinary browser, and the caller has to be able to see which half worked. Each
# stage records its own status and the summary at the end reports all of them.
set -uo pipefail

TOTAL=4
step=0
summary=()
failed=0
fatal=0
docs_repeat=0
DOCS_MARK=/tmp/.headless-browser-docs-printed
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
  printf '\n%s HEADLESS BROWSER READY %s\n' "$RULE_H" "$RULE_H"
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
  printf 'Do not cat it again, do not load the pinchtab skill, and do not re-run\n'
  printf 'any step above; this output is a transcript of work already done, not a\n'
  printf 'plan. The session exists: never create or export PINCHTAB_SESSION\n'
  printf 'yourself, whatever that SKILL.md says.\n'
  printf '\nNext: pinchtab nav <url> --block-images\n'

  if [ $failed -ne 0 ]; then
    printf '\nSome steps did not complete -- the summary above is the authority on\n'
    printf 'what is available. Say which part is down rather than working around it.\n'
  fi
  exit 0
}

CLOAK=${HEADLESS_BROWSER_CLOAK:-0}
for arg in "$@"; do
  case "$arg" in
    --cloak)    CLOAK=1 ;;
    --no-cloak) CLOAK=0 ;;
    *) printf 'usage: setup.sh [--cloak|--no-cloak]\n' >&2; exit 2 ;;
  esac
done

printf '%s HEADLESS BROWSER SETUP %s\n' "$RULE_H" "$RULE_H"
printf "What follows is a transcript of %d steps, ending with pinchtab's own\n" "$TOTAL"
printf 'instructions. Read all of it -- this is the only place those instructions\n'
printf 'get printed, and the corrections to them that follow the file matter.\n'
printf 'Then use the browser.\n'

SKILL_DIR=$(cd "$(dirname "$0")" && pwd)
NPM_ROOT="$(npm root -g)"
REAL="$NPM_ROOT/pinchtab/bin/pinchtab"
BIN_DIR="$(dirname "$(dirname "$NPM_ROOT")")/bin"
SHIM="$BIN_DIR/pinchtab"
SESSION_FILE="${PINCHTAB_SESSION_FILE:-/home/claude/.pinchtab-session}"
PINCHTAB_DOCS="$NPM_ROOT/pinchtab/skills/pinchtab"

# --- 1. the package ----------------------------------------------------------
begin 'install pinchtab'

# A shim from a previous run would open a session before the server exists, so
# drop it and drive setup through $REAL directly. It is reinstalled at the end.
[ -f "$SHIM" ] && [ ! -L "$SHIM" ] && rm -f "$SHIM" || true

# Check the package, not `command -v pinchtab`: the shim satisfies that test
# even when the package underneath it is missing.
if [ -x "$REAL" ]; then
  echo "already installed: $("$REAL" --version 2>/dev/null || echo unknown)"
  rc=0
else
  npm install -g pinchtab
  rc=$?
fi
end $rc
if [ $rc -ne 0 ] || [ ! -x "$REAL" ]; then
  note 'pinchtab install' 'FAILED -- nothing else can run'
  fatal=1
  finish
fi
note 'pinchtab install' "OK -- $REAL"

# --- 2. a browser for it to drive --------------------------------------------
begin 'find a browser runtime'
BROWSER_NOTE=''

# A patched Chromium that ordinary bot detection does not reject on
# fingerprint. Opt-in: it costs ~2.5 minutes on a cold sandbox, which is not a
# price every page should pay to be read.
if [ $CLOAK -eq 1 ]; then
  t0=$SECONDS

  # `npm install` is the expensive call here, and not because of the download:
  # measured at 215s in the sandbox to conclude "up to date, audited 9
  # packages" on an already-populated tree, against 1s for ensureBinary() to
  # hand back a cached binary. Registry round-trips, and the whole source of
  # the run-to-run variance. So every path below exists to not call it.
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
      # `npm ci` against the shipped lockfile, so npm downloads the 8 tarballs
      # and does no resolution at all. `npm install` off a bare `npm init` is
      # what cost 215s: no lockfile means a full solve, every run, forever.
      cp "$SKILL_DIR/cloak/package.json" "$SKILL_DIR/cloak/package-lock.json" . 2>/dev/null
      # Timed, and progress left on stderr: a silenced multi-minute step is
      # indistinguishable from a wedged one, and reads as a hang.
      timeout "${SETUP_TIMEOUT:-300}" npm ci --prefer-offline --no-fund --no-audit || npm_ok=0
      if [ $npm_ok -eq 0 ]; then
        # A pin ages: a yanked version or an npm too old for lockfileVersion 3
        # must not cost the caller cloak mode entirely.
        echo "npm ci failed -- retrying unpinned, which is the slow path" >&2
        npm_ok=1
        rm -f package-lock.json
        timeout "${SETUP_TIMEOUT:-300}" npm install --prefer-offline --no-fund --no-audit \
          cloakbrowser playwright-core || npm_ok=0
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
    "$REAL" config set browser.binary "$CLOAK_BIN" >/dev/null
    "$REAL" config set browser.cloak.fingerprintSeed "${CLOAK_SEED:-42069}" >/dev/null
    "$REAL" config set browser.cloak.platform "${CLOAK_PLATFORM:-windows}" >/dev/null
    "$REAL" config set browser.cloak.timezone "${CLOAK_TIMEZONE:-America/New_York}" >/dev/null
    "$REAL" config set browser.cloak.locale "${CLOAK_LOCALE:-en-US}" >/dev/null
    # Flipped LAST, and only once the binary is confirmed on disk: pointing
    # browsers.default at a runtime that isn't there breaks every later nav.
    "$REAL" config set browsers.default cloak >/dev/null
    echo "cloak runtime: $CLOAK_BIN"
    BROWSER_NOTE="OK -- cloakbrowser"
  else
    echo "cloakbrowser install failed; falling back to ordinary Chrome" >&2
    CLOAK=0
    BROWSER_NOTE='CLOAK FAILED -- fell back to plain Chrome'
    failed=1
  fi
fi

if [ $CLOAK -eq 0 ]; then
  # A previous run in this sandbox may have left browsers.default at cloak, and
  # a plain-Chrome run has to actually get plain Chrome. Best-effort: an older
  # pinchtab without the key just refuses the write.
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
end 0
note 'browser runtime' "$BROWSER_NOTE"

# --- 3. server, session shim, session ----------------------------------------
begin 'start server and mint a session'

"$REAL" server stop >/dev/null 2>&1 || true
"$REAL" server restart >/dev/null
sleep 2

# Session shim. Callers never manage PINCHTAB_SESSION: the shim resolves one on
# first use, reuses it across bash calls, and recreates it if it goes stale.
#
# Installed LAST and unconditionally, because `npm install -g` recreates the bin
# symlink and would otherwise wipe it. The `rm -f` is load-bearing: that entry is
# a SYMLINK into the package, so writing through it overwrites pinchtab's own
# entry point and bricks the install with no backup.
rm -f "$SHIM" "$SESSION_FILE"
cat > "$SHIM" <<SHIM_EOF
#!/bin/sh
# pinchtab shim -- installed by headless-browser/setup.sh. Do not edit in place;
# setup.sh rewrites it on every run.
REAL=$REAL
SESSION_FILE_DEFAULT=$SESSION_FILE
SHIM_EOF
cat >> "$SHIM" <<'SHIM_EOF'
SELF=$(readlink -f "$0" 2>/dev/null)
if [ ! -x "$REAL" ] || [ "$(readlink -f "$REAL" 2>/dev/null)" = "$SELF" ]; then
  echo "pinchtab: shim resolves to itself; run: npm install -g pinchtab --force" >&2
  exit 70
fi
F=${PINCHTAB_SESSION_FILE:-$SESSION_FILE_DEFAULT}

resolve() {
  unset PINCHTAB_SESSION            # a stale value makes `session create` fail
  if [ ! -s "$F" ]; then
    id=$("$REAL" session create --agent-id claude 2>/dev/null | tr -d '\n')
    case "$id" in
      ses_*) printf '%s' "$id" > "$F" ;;
      *)     rm -f "$F"; return 1 ;;
    esac
  fi
  PINCHTAB_SESSION=$(cat "$F" 2>/dev/null); export PINCHTAB_SESSION
}

# `session create` must run unscoped -- a session-scoped caller gets 403, which
# makes a hand-rolled `export PINCHTAB_SESSION=$(pinchtab session create ...)`
# capture an empty string and silently look like it worked.
case "${1:-}" in
  session) unset PINCHTAB_SESSION ;;
  *)       [ -n "${PINCHTAB_SESSION:-}" ] || resolve ;;
esac

# A caller may export a session by hand -- pinchtab's own bundled docs tell them
# to. Adopt it as the persistent one, so the NEXT bash call (which has no export)
# attaches to that same tab instead of minting a fresh, empty one. Without this,
# mixing the two styles silently queries a blank tab.
case "${PINCHTAB_SESSION:-}" in
  ses_*)
    [ "$(cat "$F" 2>/dev/null)" = "$PINCHTAB_SESSION" ] \
      || printf '%s' "$PINCHTAB_SESSION" > "$F"
    ;;
esac

E=$(mktemp)
"$REAL" "$@" 2>"$E"; rc=$?
# pinchtab exits 0 on a bad session, so the retry must never be gated on $rc
if grep -q 'bad_session' "$E"; then
  rm -f "$F"; resolve
  "$REAL" "$@" 2>"$E"; rc=$?
fi
cat "$E" >&2; rm -f "$E"
exit $rc
SHIM_EOF
chmod +x "$SHIM"

# Create the session now: makes the notice below true, and warms the first nav.
SID=$("$REAL" session create --agent-id claude 2>/dev/null | tr -d '\n' || true)
case "$SID" in
  ses_*) printf '%s' "$SID" > "$SESSION_FILE" ;;
  *)     rm -f "$SESSION_FILE" ;;   # shim will create one lazily on first use
esac

# Through the shim, not $REAL: this is the only thing that exercises the whole
# chain -- server, browser launch, session resolution -- and a misconfigured
# browser binary fails here or else on the caller's first real page.
if "$SHIM" nav https://example.com --block-images >/dev/null 2>&1; then
  echo "server up, session ${SID:-<lazy>}, test nav OK"
  end 0
  note 'server + session' 'OK -- session is already initialized'
else
  echo "server up, session ${SID:-<lazy>}, but the test nav FAILED" >&2
  end 1
  note 'server + session' 'FAILED -- the browser does not launch'
  fatal=1
fi

# --- 4. pinchtab's own instructions, in full ---------------------------------
begin "cat $PINCHTAB_DOCS/SKILL.md"
if [ $fatal -ne 0 ]; then
  printf 'SKIPPED -- pinchtab does not work; its instructions would be unusable.\n'
  end 1
  note 'pinchtab SKILL.md' 'SKIPPED'
  finish
fi

# The sandbox is per-conversation, so this marker means the file was printed to
# THIS caller. Escalating to --cloak is a second run, and ~20KB of instructions
# it already has is the most expensive thing the script could reprint.
if [ -f "$DOCS_MARK" ]; then
  printf 'SKIPPED -- already printed in full on an earlier run in this\n'
  printf 'conversation. Scroll back for it. If it is genuinely gone from your\n'
  printf 'context, `rm %s` and run this script again.\n' "$DOCS_MARK"
  docs_repeat=1
  end 0
  note 'pinchtab SKILL.md' 'OK -- printed on an earlier run'
elif cat "$PINCHTAB_DOCS/SKILL.md"; then
  touch "$DOCS_MARK"
  end 0
  note 'pinchtab SKILL.md' 'OK -- printed in full above'
else
  end 1
  note 'pinchtab SKILL.md' 'FAILED -- read it yourself before using pinchtab'
fi

# Corrections come after the file, not before it: they only make sense once its
# text is in context, and stating them first invites reading the file for the
# rule they already overrode.
printf '\n%s CORRECTIONS TO THE FILE ABOVE %s\n' "$RULE_H" "$RULE_H"
printf 'Three things in that file are wrong for this sandbox. Everything else in\n'
printf 'it applies exactly as written.\n'
printf '\n1. Its Core Workflow opens with\n'
printf '     export PINCHTAB_SESSION=$(pinchtab session create --agent-id ...)\n'
printf '   DO NOT RUN THAT. The session above already exists and a shim attaches\n'
printf '   every command to it, across separate bash calls. Creating one by hand\n'
printf '   gets a 403, captures an empty string, and silently drives a blank tab.\n'
printf '2. Never run `pinchtab skill update` or `pinchtab skill sync`. They write\n'
printf '   into other agent skill directories found on the machine.\n'
printf '3. Anything it says about picking a browser or profile is already settled\n'
printf '   by the steps above -- do not reconfigure `browser.binary`, the security\n'
printf '   gates, or `browsers.default`.\n'
printf '\nThe tab, its DOM and typed form values persist across bash calls, so a\n'
printf 'multi-step flow never replays earlier steps. If the shim has to remint a\n'
printf 'session you get a fresh empty tab: re-`nav` after seeing `no_current_tab`.\n'
printf '\nThat file links a references/ directory. It is NOT printed here; read a\n'
printf 'page from it only if you actually need it:\n'
for f in "$PINCHTAB_DOCS"/references/*; do
  [ -f "$f" ] && printf '  %s\n' "$f"
done

finish
