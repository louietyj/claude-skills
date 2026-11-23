# End-to-end tests of the calling agent

Two rigs for trying a prompt or tool change on the real Retell agent before
it goes near a real phone. Both run against a **throwaway copy** of the live
agent, so the live one is never touched until `dialer agent-push`.

| | Voice (intercom → Retell) | Text (Retell Chat API) |
|---|---|---|
| Tests | everything, including what gets *spoken*: tool-call speech, `NO_RESPONSE_NEEDED`, double turns, timing | what the model decides: turn length, menus, silence choices |
| Cost | a normal call: ~$0.14/min Retell + Telnyx | $0.02 per agent message |
| Fidelity | the real voice pipeline | no TTS or turn-taking; menus can play out differently |

Use text to iterate on wording, voice to confirm. They disagree sometimes: a
speech menu question that came straight after a language prompt got silence on
voice and a keypress in text (2026-11-23).

## The copy

```bash
python rt.py clone        # copies the live LLM + agent; ids kept in test.json
python rt.py draft        # the checkout's agent/agent.json prompt + tools onto the copy
python rt.py vars harbor.vars.json   # brief, opening, other_party, call_purpose
...
python rt.py teardown     # unbinds and deletes the copy (and its chat agent)
```

`draft` reads `agent/agent.json`, so after editing `agent/prompt.md` re-inline
it first (the inlining half of `dialer agent-push`). Inbound calls skip
`dispatch`, so the copy's variables come from the LLM's
`default_dynamic_variables`, which is what `vars` sets.

## Voice: intercom plays the business

Intercom's relay dials any SIP URI, and Retell accepts inbound SIP for an
imported number at `sip:sip.retellai.com` with no credentials. So intercom
calls the Retell agent directly, without touching how the 669 number routes on
Telnyx (it goes to Louie's softphone):

```bash
python rt.py bind         # the copy answers inbound on the number
python exp.py harbor.json # plays the script, answers consults, prints Retell's transcript
python rt.py unbind       # straight after: anyone sending SIP to Retell for the number reaches it
```

A script is `{"lines": [[delay_s, "what they say"], ...], "answers": [[delay_s,
"consult answer"], ...], "tail_s": 15}`. Each line is spoken through intercom
after its delay, waiting for a gap if the agent is talking; consults are
answered in order. Needs intercom's `config.json` in the checkout.

The evidence, in order of trust:

- `transcript_with_tool_calls` (printed): what the agent actually *said*, with
  tool calls in place. A line there was spoken.
- The call's `public_log_url` (from `get-call`): one line per LLM turn,
  including `NO_RESPONSE_NEEDED returned by LLM`, latency and prompt size.
  The only way to see a silent choice.

Silence is ambiguous in the transcript alone: check the log.

## Text: Retell's Chat API

```bash
python chat.py setup                              # a chat agent on the copy's LLM
python chat.py run harbor.chat.json harbor.vars.json
python chat.py teardown
```

Each line of the JSON list is sent as their turn; the agent's messages and tool
calls come back, with an empty reply for `NO_RESPONSE_NEEDED`. Consults reach
the real queue and hold the turn open until answered with the dialer.

Better than a script: play the business turn by turn and react to what it
does, the way a real menu or receptionist would (re-prompt after silence,
"invalid option", a refusal). The agent only experiences turns, not time, so
for its decisions this is as good as a call:

```bash
python chat.py start harbor.vars.json
python chat.py say "Thanks for calling Harbor Car Rental, this is Sam."
python chat.py end
```

A fixed script can't do this. It plays on regardless of what the agent did, so
it checks mechanics (what's spoken, what's silent), not behaviour.

## What the 2026-11-23 runs established

- Text written alongside a tool call is spoken. With `speak_during_execution`
  on, Retell speaks that text and drops the execution message; it speaks the
  execution message only when the model wrote none. With it off, speech is
  entirely the model's choice.
- Text before a consult and the reply after its result are one response, spoken
  run together with no pause.
- Hard nos in a brief are read literally: "never make a reservation" kept the
  agent from answering a menu's "are you calling to book a new reservation?"
- Every turn sends the whole prompt and transcript: 5.5–6.7k tokens on these
  runs, past the 4k where Retell starts billing by size.
