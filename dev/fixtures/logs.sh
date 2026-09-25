#!/bin/bash
# prints autosolver log lines from all instances
T=$(node -p 'require("/root/.pinchtab/config.json").server.token')
for id in $(curl -s -H "Authorization: Bearer $T" localhost:9867/instances | grep -oE 'inst_[a-z0-9]+' | sort -u); do
  curl -s -H "Authorization: Bearer $T" localhost:9867/instances/$id/logs
done | grep -E 'autosolver' | tail -${1:-15} | cut -c1-400
