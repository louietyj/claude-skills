---
name: voice-mode-guide
description: "Briefing to run in CHAT, in the turn before the user switches the Claude mobile app to voice. Voice replaces the injected context -- the skills index and bash_tool both disappear -- so this prints, into the transcript where it survives the toggle, what changes, how to reach each capability anyway, and the full skill index with triggers and paths. It boots the conversation too if that has not happened yet. Invoke it when the user says they are about to switch to voice, go hands-free, or start a call."
---

# voice-mode-guide

There is nothing to read here. The skill is one script:

```bash
bash /mnt/skills/*/voice-mode-guide/voice-mode-guide.sh
```

**DO NOT head/tail/grep this command's output.** Its entire purpose is to put
text into your context before the mode switch takes your context away;
truncating it defeats the skill.

Run it now, then answer. It prints the briefing and the skill index, and boots
the conversation first if `session-init` has not already run. It is idempotent:
a second run says so in a few lines rather than repeating itself.

The glob is deliberate: an uploaded skill lands under `/mnt/skills/user/` or
`/mnt/skills/plugins/` depending on how it was installed, and hardcoding
either one breaks on the other.

Best run from chat, before the switch. If the user asks for it from inside
voice, run it anyway -- through `code_execution` and `subprocess`, since
`bash_tool` is not registered there.
