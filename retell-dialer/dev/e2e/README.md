# End-to-end tests of the calling agent

Two rigs for trying a prompt or tool change on the real Retell agent before
it goes near a real phone. Both run against a **throwaway copy** of the live
agent, so the live one is never touched until `dialer agent-push`.

| | Voice (intercom → Retell) | Text (Retell Chat API) |
|---|---|---|
| Tests | everything, including what gets *spoken*: tool-call speech, `NO_RESPONSE_NEEDED`, double turns, timing | what the model decides: turn length, menus, silence choices |
| Cost | ~11¢/min on Terra (~17¢ on Sonnet 5), plus Telnyx | 1¢ per agent message on Terra (2¢ Sonnet), plus ~2¢ per chat |
| Fidelity | the real voice pipeline | no TTS or turn-taking; menus can play out differently |

Use text to iterate on wording, voice to confirm. They disagree sometimes: a
speech menu question that came straight after a language prompt got silence on
voice and a keypress in text (2026-11-23).

## What it costs

This is Louie's own money: Retell auto-recharges his card $11 at a time.
On 2026-11-23 a day of testing cost about $16 (16 calls, $6.20; 110 chats,
$9.88) and fired the recharge twice in 80 minutes, enough for his bank to flag
it as a double charge. Chats feel free, and they aren't: they made up most of
that day's bill.

Per-unit rates, from that day's bills:

- **Voice call:** 7¢/min for Retell's engine (5.5¢) and TTS (1.5¢), plus the
  model (3.2¢/min Terra, 6.4¢/min Sonnet 5), plus a token surcharge once the
  prompt passes 4k tokens, which it always does here (see the last bullet
  below). The surcharge scales with the model's price and grows as the
  transcript does: 0.3–1.3¢/min on Terra, 2.4–6.3¢/min on Sonnet. In total
  that's 10.5–11.5¢/min on Terra and 16–20¢/min on Sonnet 5, plus a 0.3¢
  surcharge on calls under 10s. A 2-minute test call costs 20–35¢; the
  longest one (9.6 min on Sonnet) cost $1.90.
- **Chat:** 1¢ per agent message on Terra, 2¢ on Sonnet 5/5.5, 1.1¢ on
  `tell-1`. Every chat that ends adds 2.08¢ for Retell's post-chat analysis.
  A 15-turn roleplay costs 17–32¢.

Before a batch, say roughly what it will cost. Stop and ask Louie before
anything past about $5 in one sitting. To see what you've spent, sum
`call_cost.combined_cost` from `POST /v3/list-calls` and
`chat_cost.combined_cost` from `POST /v3/list-chats` (`{"limit": 1000}`). Both
are in **cents**.

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
