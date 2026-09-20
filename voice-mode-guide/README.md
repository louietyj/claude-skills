# voice-mode-guide

Run in chat, in the turn before switching the Claude mobile app to voice. Prints — into the transcript, where it survives the toggle — what is about to change, how to reach each capability anyway, and the skill index that voice would otherwise take away.

```
SKILL.md             a shim; all it says is to run the script
voice-mode-guide.sh  boot if needed, then the briefing, then the index
voice-brief.md       the briefing text, cat'd to stdout
skill-index.py       rebuilds <available_skills> from /mnt/skills
```

Everything load-bearing is printed by the script rather than written in `SKILL.md`: the injected context is replaced on the toggle, the conversation is not, and it is not certain which side of that line a skill body lands on.

## The problem

Toggling between typed and spoken input swaps the whole injected context. Measured 2026-09-19/20 across sessions that toggled several times:

| | chat | voice |
|---|---|---|
| `bash_tool` | registered | **not registered** |
| `<available_skills>` | present | **absent** |
| system prompt | standard | voice-specific (brevity, TTS) |
| `code_execution`, connectors, `tool_search`, `conversation_search`, `memory_*` | present | present |

The input modality is irrelevant to the model — speech-to-text produces tokens either way. This is a product-layer difference: a different system prompt and a different tool registry per mode.

Nothing about the machine changes, so `bash_tool`'s absence is fully covered by `subprocess.run(..., shell=True)` through `code_execution`, and a `session-init` that ran in chat leaves `cfs` and `lmcps` working after the switch. What doesn't survive is the *announcement*: `/mnt/skills/` is byte-identical in both modes, but a skill fires because its trigger description is in context, and in voice none of them are. A filename alone is useless — `hostc` conveys nothing about when to reach for it.

## The tier rule

`<available_skills>` is exactly `public/` + `user/` + `plugins/`, omitting `/mnt/skills/examples/` wholesale — 44 on disk, none announced. Verified by asking a chat session to list its skills without looking at the filesystem, then diffing against `find /mnt/skills`.

`skill-index.py` applies the same rule. This mirrors chat rather than improving on it: Claude wouldn't reach for `slack-gif-creator` in chat, so announcing it in voice is a divergence, not a fix. The count of omitted examples is still printed, with the path, so a task that genuinely needs one isn't blocked by a silent filter.

There is no allowlist and no per-skill judgement anywhere in this skill. Skills whose final step is presenting a file or rendering an artifact do lose their delivery surface in voice — no screen to tap — but that is stated once in the briefing as a constraint and left to the model to apply.

## The sentinels

There is no boot process on claude.ai, only a preference asking for `session-init` on the first turn, which may or may not have been followed. So `session-init.sh` writes `/tmp/.session-init.done` — a timestamp plus its stage summary — and this script reads it. The sentinel rather than a `cfs`-on-PATH probe, because it records *which* stages passed, so a half-failed boot reports as half-failed; and its lifetime is the VM's, exactly the lifetime of the shims it attests to.

On a miss the script runs `session-init.sh` itself, bracketing the output with "this has run, do not run it again" both before and after — several hundred lines separate them, and a redundant re-run costs both setup scripts and two full `SKILL.md` prints.

A second sentinel, `/tmp/.voice-mode-guide.done`, short-circuits a repeat run to a few lines. That exists so the preferences can say *run this* flatly; the conditional phrasing would ask a model that has just lost its context to judge what happened earlier in it. `--force` reprints everything.

## Recovering from inside voice

Preferences survive a chat→voice toggle, so they carry a fallback for when the toggle happened before the guide ran. Two triggers, deliberately:

- **`claude_behavior` describing you as a voice-based conversational agent.** Quoted from a live voice session (2026-09-20), alongside TTS output, two-sentence / fifty-word guidance, and no lists or tables. Fires at the top of the session, before anything has gone wrong.
- **`bash_tool` coming back "not registered".** Reactive, but keyed on a measured registry difference rather than on prompt text.

Both, because in the same session that quoted `claude_behavior` the model first reported the block as *absent* and only found it on a careful second look. The block is reliably present; introspection about it is not. The second trigger needs no introspection — it fires at the exact moment of the original failure: reaching for `bash_tool`, getting "not registered", and reporting it as missing rather than routing around it.

## Testing

```bash
dev/sandbox.sh up
dev/sandbox.sh sh 'bash /mnt/skills/*/voice-mode-guide/voice-mode-guide.sh'
```

The container only mounts skills at `/mnt/skills/user/`, so exercising the tier split means staging the others by hand:

```bash
docker exec claude-sandbox mkdir -p /mnt/skills/plugins /mnt/skills/public /mnt/skills/examples
docker cp voice-mode-guide claude-sandbox:/mnt/skills/plugins/voice-mode-guide
docker exec claude-sandbox rm -f /tmp/.session-init.done /tmp/.voice-mode-guide.done
```

`SKILLS_ROOT` points `skill-index.py` at another tree, the quick way to test frontmatter parsing against synthetic files. `SESSION_INIT_SENTINEL` and `VOICE_MODE_GUIDE_SENTINEL` override the sentinel paths.

## Package for claude.ai

`python package.py` writes `voice-mode-guide.zip`; upload it under Settings → Capabilities → Skills. No credentials. It needs `session-init` installed alongside it, and reports that rather than failing if it isn't.

## Limits

**A conversation cold-started in voice is unsalvageable by anything in this repo.** No preferences, no skills index, no `bash_tool` — so no trigger can fire, because every self-healing path here rides on the preferences block, and that is exactly what a cold start does not get. Recovery needs the user to narrate the whole bootstrap aloud: the path, the read, the run, and the `subprocess` workaround.

The workaround is unchanged: start in chat, run this, then toggle.
