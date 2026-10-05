---
name: "transcript-export"
description: "Export this session's JSONL transcript, tool results and sub-agent transcripts as a zip and present it, with every transcript also rendered to a browsable HTML replay. Use for /transcript-export or when asked to export the conversation."
---

# transcript-export

Claude Code-style session files live in `~/.claude/projects/<project>/`: `<session-id>.jsonl` is the main transcript. Beside it is a `<session-id>/` directory holding `tool-results/` (oversized tool outputs) and `subagents/` (sub-agent transcripts, if any were spawned).

The script renders the main transcript and every sub-agent `.jsonl` to HTML with [claude-replay](https://github.com/es617/claude-replay), writing `<name>.html` beside each `<name>.jsonl`, then zips the lot to `/mnt/user-data/outputs/transcript-export.zip`. Run it as is:

```bash
bash "$(ls -1dt /mnt/skills/*/transcript-export ~/.claude/skills/synced/*/transcript-export 2>/dev/null | head -1)/export.sh"
```

Rendering needs `npx` and the npm registry. If a render fails, the script prints why and zips that transcript without its HTML. It never skips the zip.

Then call SendUserFile on `/mnt/user-data/outputs/transcript-export.zip` with `display: attach`. Reply in one or two lines:
- it is a snapshot as of now, so turns after the export are not included;
- each `.html` opens in a browser as a replay; ⏭ (skip to end) shows the whole conversation at once;
- mention if no `subagents/` entries appeared (none were spawned), and name any transcript that failed to render.
