---
name: intercom
description: "Louie's phone or Pixel Watch as Claude's ears, and optionally its voice. Listen mode: read a real-time speaker-labelled transcript of a live conversation a few seconds behind and steer Louie in chat. Use whenever Louie asks Claude to listen in, sit in on, coach him through, or take notes on an in-person conversation, meeting, appointment or negotiation, including a Zoom/Meet/Teams call (the phone just listens to the room). Talk mode: a hands-free spoken conversation with Claude while it keeps working, for when Louie says let's talk, talk me through it, or he's walking, driving or away from the keyboard. NOT for placing phone calls (retell-dialer)."
---

# intercom

There is nothing else to read here. The skill is one script:

```bash
bash "$(ls -1dt /mnt/skills/*/intercom ~/.claude/skills/synced/*/intercom 2>/dev/null | head -1)/setup.sh"
```

**DO NOT head/tail/grep this command's output.** It prints the guide into your
context; truncating it silently costs you the guide.

Run it before anything else. It puts `intercom` on PATH, proves the relay is
reachable with the right token, then prints the guide in full. Do not `view` or
`cat` GUIDE.md yourself; setup already put it in your context. If setup
**fails, stop and tell Louie**.

Its output is a transcript of work already done: do not re-run it this
conversation.

The lookup is deliberate: claude.ai chat puts skills under `/mnt/skills/user/`
or `/mnt/skills/plugins/`, Cowork and cloud Claude Code under
`~/.claude/skills/synced/<bucket>/`, and hardcoding any one breaks the others.
Never re-derive the path or set a shell variable for it. Just call `intercom`.
