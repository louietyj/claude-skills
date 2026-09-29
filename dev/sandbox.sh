#!/bin/bash
# A local stand-in for claude.ai's code-execution sandbox, in Docker. Every skill
# in this repo runs only there, and Windows is a poor place to reproduce it.
#
#   dev/sandbox.sh up             start the container; idempotent
#   dev/sandbox.sh sh [command]   run a command in it, or open a shell
#   dev/sandbox.sh down           remove it, and everything installed in it
#
# Each skill is mounted read-only at /mnt/skills/user/<name>, where an uploaded
# skill lands, so the commands in its SKILL.md run verbatim. dev/work/ is mounted
# at /work for handing files in and out (a locally built binary, logs, HTML dumps).
#
# SANDBOX_LAYOUT=cowork mounts them where Cowork and cloud Claude Code sync them
# instead -- $HOME/.claude/skills/synced/<bucket>/<name>, with no /mnt/skills
# user tier and no /home/claude. Give it its own SANDBOX_NAME.
set -euo pipefail

NAME=${SANDBOX_NAME:-claude-sandbox}
IMAGE=${SANDBOX_IMAGE:-node:24-bookworm}
LAYOUT=${SANDBOX_LAYOUT:-chat}
case "$LAYOUT" in
  chat)   SKILLS=/mnt/skills/user; WORKDIR=/home/claude ;;
  cowork) SKILLS=/root/.claude/skills/synced/test-org_test-acct; WORKDIR=/root ;;
  *) printf 'SANDBOX_LAYOUT must be chat or cowork\n' >&2; exit 2 ;;
esac
REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK="$REPO/dev/work"

# Docker Desktop on Windows wants C:/... host paths, and Git Bash would otherwise
# rewrite every /mnt/... container path into a Windows one.
host_path() { if command -v cygpath >/dev/null; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
export MSYS_NO_PATHCONV=1

up() {
  if docker container inspect "$NAME" >/dev/null 2>&1; then
    docker start "$NAME" >/dev/null
    echo "$NAME already exists; started it"
    return
  fi

  mkdir -p "$WORK"
  mounts=(-v "$(host_path "$WORK"):/work")
  for skill in "$REPO"/*/; do
    [ -f "$skill/SKILL.md" ] || continue
    name=$(basename "$skill")
    mounts+=(-v "$(host_path "$skill"):$SKILLS/$name:ro")
  done

  # Chrome dies on Docker's default 64MB /dev/shm.
  docker run -d --name "$NAME" --shm-size=1g --label "sandbox.workdir=$WORKDIR" \
    "${mounts[@]}" "$IMAGE" sleep infinity >/dev/null
  [ "$LAYOUT" = chat ] && docker exec "$NAME" mkdir -p /home/claude

  # The chromium package is here for its shared libraries as much as the browser:
  # a downloaded Chromium (cloakbrowser, puppeteer) will not start without them.
  docker exec "$NAME" bash -c '
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
      chromium fonts-liberation >/dev/null
  '
  echo "$NAME is up; skills are under $SKILLS, dev/work is /work"
}

case "${1:-}" in
  up)
    up
    ;;
  sh)
    shift
    tty=(-i); [ -t 0 ] && [ -t 1 ] && tty=(-it)
    # Containers from before the label existed are all chat layout.
    wd=$(docker inspect -f '{{index .Config.Labels "sandbox.workdir"}}' "$NAME")
    wd=${wd:-/home/claude}
    if [ $# -eq 0 ]; then
      docker exec "${tty[@]}" -w "$wd" "$NAME" bash
    else
      docker exec "${tty[@]}" -w "$wd" "$NAME" bash -c "$*"
    fi
    ;;
  down)
    docker rm -f "$NAME" >/dev/null && echo "removed $NAME"
    ;;
  *)
    printf 'usage: dev/sandbox.sh up | sh [command] | down\n' >&2
    exit 2
    ;;
esac
