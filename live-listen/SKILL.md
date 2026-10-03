---
name: live-listen
description: "Listen in on a live conversation through Louie's phone or Pixel Watch as the microphone: read a real-time speaker-labelled transcript a few seconds behind and steer Louie in chat while it runs. Use whenever Louie asks Claude to listen in, sit in on, coach him through, or take notes on an in-person conversation, meeting, appointment or negotiation, including a Zoom/Meet/Teams call (the phone just listens to the room). NOT for placing phone calls (retell-dialer)."
---

# live-listen

There is nothing else to read here. The skill is one script:

```bash
bash "$(ls -1dt /mnt/skills/*/live-listen ~/.claude/skills/synced/*/live-listen 2>/dev/null | head -1)/setup.sh"
```

**DO NOT head/tail/grep this command's output.** It prints the guide into your
context; truncating it silently costs you the guide.

Run it before anything else. It puts `listen` on PATH, proves the relay is
reachable with the right token, then prints the guide in full. Do not `view` or
`cat` GUIDE.md yourself; setup already put it in your context. If setup
**fails, stop and tell Louie**.

Its output is a transcript of work already done: do not re-run it this
conversation.

The lookup is deliberate: claude.ai chat puts skills under `/mnt/skills/user/`
or `/mnt/skills/plugins/`, Cowork and cloud Claude Code under
`~/.claude/skills/synced/<bucket>/`, and hardcoding any one breaks the others.
Never re-derive the path or set a shell variable for it. Just call `listen`.
