# Recovering from a bad write

Reference for the `durable-filesystem` skill. Its SKILL.md carries `history`
and `restore` for the common case; this is the rest.


Every file keeps 30 days of revisions, so a bad write is a rollback, not a loss:

```bash
cfs history /memory/hawaii.md               # revisions, newest first
cfs read /memory/hawaii.md --rev 0165931f   # the full older version
cfs restore /memory/hawaii.md --rev 0165931f
```

Restoring adds a new revision rather than erasing anything, so it is itself reversible — use it instead of rebuilding a damaged file by hand.

`diff` shows a diff only when it is small enough to take in at a glance (~20 changed lines, under 5% of the file); past that it returns the current file, and `--force` overrides. Revision history follows the *path*, so a file deleted and recreated under the same name inherits the old one's revisions.

