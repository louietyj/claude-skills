#!/bin/bash
# Rebuild retell-sdk.js. The SDK's own UMD build externalises livekit-client and
# eventemitter3, so loading it from a CDN means guessing at global names; this
# bundles all three into one file instead.
set -euo pipefail
cd "$(dirname "$0")"
tmp=$(mktemp -d)
cp entry.js "$tmp/"
cd "$tmp"
npm init -y >/dev/null
npm install --no-fund --no-audit retell-client-js-sdk@3.0.1 esbuild
npx esbuild entry.js --bundle --format=iife --global-name=Retell --minify \
  --outfile="$OLDPWD/retell-sdk.js"
echo "rebuilt $OLDPWD/retell-sdk.js"
