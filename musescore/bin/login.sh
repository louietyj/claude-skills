#!/bin/sh
# Run only through `request-credential run <id> -- sh <this file>`, which supplies
# $CRED_USERNAME/$CRED_PASSWORD and redacts the output.
set -e
[ -n "$CRED_PASSWORD" ] || { echo "no credential: run me through request-credential run" >&2; exit 1; }
# Plain nav, never --block-images: blocking images also breaks msfetch's page fetches.
pinchtab execute-batch <<"B"
nav https://musescore.com/user/login
expect "#password[type=password]"
type "#username" "$CRED_USERNAME"
type "#password" "$CRED_PASSWORD"
click "button[name=op]" --wait-nav
wait --url "**/dashboard*"
url
B
