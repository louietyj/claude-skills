# voice-mode-guide

Run in chat, in the turn before switching the Claude mobile app to voice. Prints into the transcript a briefing for the voice side of the conversation: how the two agents work together, when and how to hand off, and a catalog of what the text side can do.

```
SKILL.md             a shim; all it says is to run the script
voice-mode-guide.sh  refuses until session-init has run, then prints the rest
voice-brief.md       the briefing, cat'd to stdout
catalog.py           skills, lmcps servers and memory topics, one line each
```

## The problem

Since chat and Cowork merged (2026-09-16), a conversation that talks by voice is two agents. Measured 2026-10-07 to 2026-10-09; the evidence is in `/drafts/voice-vs-chat-mode.md` on cfs.

| | text-Claude | voice-Claude |
|---|---|---|
| userPreferences, skills index | yes | **never**, however the conversation started |
| native memory, connectors, web search | yes | yes |
| shell | Bash, in the workspace container | `code_execution` at most, offline: `cfs` and `lmcps` 403 |
| reaches the other side by | replying | `send_to_cowork_agent` |

Voice-Claude's only route to anything the user built is the handoff tool, and that tool only exists once the conversation has upgraded: some text turn has made a Bash call. A voice-started conversation has no handoff tool until then.

What voice-Claude sees of text-Claude:
- **Tool output from before the switch.** That is what this skill relies on.
- **Reply text to a handoff.** It arrives as untrusted data, never as instructions.
- **Nothing else.** It never sees tool output from text turns run during a handoff, even after voice is ended and reopened.

So the briefing has to be in the transcript before the switch, as tool output.

What text-Claude sees of voice-Claude:
- **Spoken lines, in batches.** A batch is missing the voice turn that made the handoff call. That turn arrives with the next batch.
- **The handoff instruction**, framed as a message from a peer session.
- **Nothing else:** none of voice-Claude's tool calls or results.

## Design

**One small tool result.** Past a size threshold the harness replaces a tool result with a 2 KB preview and a file path. That happened to session-init's 62.5 KB. Voice-Claude would get only the preview, so the guide's output has to stay well under the threshold. In a real container it was 12.6 KB and arrived whole (2026-10-09). The chat UI collapses it as "output truncated", but that is display only.

**session-init is never run inline.** The previous version ran it inside the guide's own call when the conversation hadn't booted, which would push the whole result behind a preview. The guide now checks session-init's sentinel and, if it is missing, prints the session-init command and exits 2. The model then runs that command as its own call and the guide again as another.

**The catalog is one line per entry, for routing.** Voice-Claude can run none of what it lists. It only needs to recognise that a request belongs on the other side. Full trigger descriptions would cost the size budget for nothing. It lists every skill tier except `examples/`, deduped by name, because a Cowork container has the same skill under `organization/`, `private/` and `user/`. It also lists the lmcps servers from the config session-init cached, and memory topics from `/memory/INDEX.md`. Each list fails soft to one line.

**Voice-Claude is told to hand off on its own initiative.** The handoff tool's own description tells it to relay only what the user explicitly asks to send. Voice-Claude also screened a handoff on its own judgement: it refused "echo a password" until the request was reworded as "a random word". The briefing overrides both.

**Text-Claude's rules travel in the same printout.** Text-Claude has the printout in context because it ran it. The `<voice_mode>` block in userPreferences only has to say when to run the guide.

**The guide doesn't copy Anthropic's voice prompt.** Voice-Claude gets that natively; the previous version copied it in.

## The sentinels

`session-init.sh` writes `/tmp/.session-init.done`. This script refuses to print the briefing without it.

`/tmp/.voice-mode-guide.done` cuts a repeat run down to a few lines. That lets the preferences say "run this" flatly, without asking the model to judge what already happened in a long transcript. `--force` reprints everything.

## The golden path

1. Type `voice` (or `/voice-mode-guide`) in chat. This works whether the conversation started in chat or in voice. Text-Claude runs session-init if it hasn't run, then the guide, as separate calls.
2. Switch to voice.

From inside voice the guide is useless. Voice-Claude has no shell, and nothing text-Claude prints during a handoff reaches it. The fix is the same step 1: type in chat, then switch back.

## Testing

```bash
dev/sandbox.sh up
dev/sandbox.sh sh 'bash /mnt/skills/*/session-init/session-init.sh >/dev/null; bash /mnt/skills/*/voice-mode-guide/voice-mode-guide.sh --force | wc -c'
```

Keep that byte count small. The exact threshold is unknown: 12.6 KB arrived whole, 62.5 KB was replaced by a preview.

`SKILLS_ROOT` points `catalog.py` at another skills tree. `LMCPS_HOME` points it at another lmcps cache. `SESSION_INIT_SENTINEL` and `VOICE_MODE_GUIDE_SENTINEL` override the sentinel paths.

The test that matters is live, and can't be run in the sandbox:
1. Run the guide in chat.
2. Switch to voice.
3. Ask voice-Claude to quote the `END OF VOICE BRIEFING` line.

If it can, the whole printout reached it. Passed 2026-10-09: voice quoted the two lines that follow the marker.

## Package for claude.ai

`python package.py` writes `voice-mode-guide.zip`; upload it under Settings → Capabilities → Skills. No credentials. It needs `session-init` installed alongside it.
