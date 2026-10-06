---
name: retell-dialer
description: "Place a real phone call on Louie's behalf and supervise it live — restaurant bookings, doctor's offices, clinics, utilities, anywhere the only channel is a phone line. A fast voice agent runs the conversation; when it hits something its brief does not cover it consults you mid-call and waits for your answer, and you can inject corrections it never asked for. Use whenever a task needs someone phoned. NOT fire-and-forget: you stay on the loop for the whole call, and you must have Louie's explicit approval of the brief and the numbers it will dial before dialling."
---

# retell-dialer

There is nothing else to read here. The skill is one script:

```bash
bash "$(ls -1dt /mnt/skills/*/retell-dialer ~/.claude/skills/synced/*/retell-dialer 2>/dev/null | head -1)/setup.sh"
```

**DO NOT head/tail/grep this command's output.** It prints the guide into your
context; truncating it silently costs you the guide.

Run it before anything else, including drafting a brief. It puts `dialer` on
PATH, proves the consult queue works, prints a Gregorian calendar, then prints
the guide in full: writing the brief, running the call, and what has gone wrong
on real ones. Do not `view` or `cat` GUIDE.md yourself; setup already put it in
your context. If setup **fails, stop and tell Louie** — do not place a call.
Without the queue, `consult_supervisor` times out with a stranger holding the
line.

Its output is a transcript of work already done: do not re-run it, or anything
it shows, this conversation.

The lookup is deliberate: claude.ai chat puts skills under `/mnt/skills/user/`
or `/mnt/skills/plugins/`, Cowork and cloud Claude Code under
`~/.claude/skills/synced/<bucket>/`, and hardcoding any one breaks the others.

**Never re-derive the path or set a shell variable for it.** Shell state dies
with the bash call that set it. Just call `dialer`.
