#!/bin/bash
# Re-pin the cloakbrowser dependency solve: run it, eyeball the diff, commit.
# `--package-lock-only` resolves without installing or downloading a browser.
#
# Monthly is plenty. A stale pin installs an older cloakbrowser and an older
# patched Chromium, which still works, and setup.sh falls back to an unpinned
# install if the pin stops resolving -- so drift costs time, never the feature.
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
