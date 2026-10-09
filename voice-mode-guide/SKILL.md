---
name: voice-mode-guide
description: "Briefing to run in CHAT, in the turn before the user switches the Claude mobile app to voice. Voice is answered by a separate Claude with no preferences, skills, shell or durable memory, whose only bridge back is a handoff tool to the text side; it does read this conversation as it stood at the switch. So this prints, into the transcript, how the two agents work together, when and how voice should hand off, and a catalog of what the text side can do. Invoke it when the user says they are about to switch to voice, go hands-free or start a call, or just types \"voice\"."
---

# voice-mode-guide

There is nothing to read here. The skill is one script:

```bash
bash "$(ls -1dt /mnt/skills/*/voice-mode-guide ~/.claude/skills/synced/*/voice-mode-guide 2>/dev/null | head -1)/voice-mode-guide.sh"
```

**DO NOT head/tail/grep this command's output.** Its whole purpose is to put
text into the transcript for voice-Claude to read after the switch.

If session-init has not run in this conversation, the script says so and stops.
Run session-init as its own call, then this script again. Never both in one
call: the combined output would be replaced by a preview, and the preview is
all voice-Claude would see.

It is idempotent: a second run says so in a few lines.

**From inside voice it is useless.** Voice-Claude has no shell to run it, and
anything text-Claude prints during a handoff never reaches voice. If the user
asks for it while in voice, tell them to type "voice" in chat, then switch back.
