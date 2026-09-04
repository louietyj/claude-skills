---
name: session-init
description: "One-touch conversation boot, run as the first action of every conversation. A single bash call that sets up the durable filesystem and the local MCP servers, prints the memory index, and prints both of those skills' instructions in full -- replacing five separate tool calls. The user's preferences invoke its script directly, so you normally never load this skill at all; if you are reading this, you have most likely not booted yet, and should run the command below now."
---

# session-init

There is nothing to read here. The skill is one script:

```bash
bash /mnt/skills/*/session-init/session-init.sh
```

**DO NOT head/tail/grep this command's output.** It prints instructions into
your context; truncating it silently costs you them.

Run it once, before answering anything. It prints everything you would
otherwise have gone to `durable-filesystem` and `local-mcps` for -- their setup
output, their complete instructions, and the memory index -- so neither of those
skills needs a separate invocation afterwards.

The glob is deliberate: an uploaded skill lands under `/mnt/skills/user/` or
`/mnt/skills/plugins/` depending on how it was installed, and hardcoding either
one breaks on the other.

Its output is a transcript of work already done. Do not re-run the commands it
shows or re-read the files it prints.

If a stage fails the script keeps going and says so in its closing summary --
one failure never costs you the other half of the boot. Report what is down
rather than re-running the script or routing around it.
