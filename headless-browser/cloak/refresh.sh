#!/bin/bash
# Re-pin the cloakbrowser dependency solve. Run from anywhere:
#
#     bash headless-browser/cloak/refresh.sh
#
# `--package-lock-only` resolves and writes package-lock.json without
# installing or downloading a browser, so this is a few seconds on any machine
# with a fast link to the registry. It is the whole maintenance story for the
# pin: run it, eyeball the diff, commit.
#
# Cadence is undemanding. A stale pin installs an older cloakbrowser, which
# fetches an older patched Chromium and still works; the version only matters
# when a site's detection has moved on. setup.sh falls back to an unpinned
# install if the pin ever stops resolving, so drift costs time, never the
# feature. Monthly, or when a page cloak used to get through starts failing.
set -euo pipefail

cd "$(dirname "$0")"

before=$(cat package-lock.json 2>/dev/null || true)
npm install --package-lock-only --no-audit --no-fund
after=$(cat package-lock.json)

if [ "$before" = "$after" ]; then
  echo "pin unchanged: $(node -p "require('./package-lock.json').packages['node_modules/cloakbrowser'].version")"
  exit 0
fi

echo "pin updated to cloakbrowser $(node -p "require('./package-lock.json').packages['node_modules/cloakbrowser'].version")"
echo "review with: git diff -- headless-browser/cloak/"
