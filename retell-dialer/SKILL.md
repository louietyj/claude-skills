---
name: retell-dialer
description: "Place a real phone call on Louie's behalf and supervise it live — restaurant bookings, doctor's offices, clinics, utilities, anywhere the only channel is a phone line. A fast voice agent runs the conversation; when it hits something its brief does not cover it consults you mid-call and waits for your answer, and you can inject corrections it never asked for. Use whenever a task needs someone phoned. NOT fire-and-forget: you stay on the loop for the whole call, and you must have Louie's explicit go-ahead before dialling."
---

# retell-dialer

A Retell voice agent holds the conversation at human tempo. You are its
supervisor: it calls `consult_supervisor` when it needs something, that request
is held open by a Cloudflare Durable Object, and your answer is what it speaks.
You can also push context in unprompted, without it having asked.

You are slow — 6 to 13 seconds per round trip. The agent covers that with
"let me check on that" and the receptionist waits. **A human tolerates that
twice, maybe three times.** Everything else has to be anticipated before you
dial, which is why most of this file is about the brief rather than the API.

## Setup (once per conversation)

```bash
bash /mnt/skills/user/retell-dialer/setup.sh
```

Puts `dialer` on PATH and proves the consult queue answers. If it **fails,
stop and tell Louie** — do not place a call. Without the queue,
`consult_supervisor` times out with a stranger holding the line.

**Never re-derive the path or set a shell variable for it.** Shell state dies
with the bash call that set it. Just call `dialer`.

## Before you dial

### 1. Get permission. Every time.

Skills have no permission prompt — nothing stands between you and a real phone
ringing in someone's office. **Ask Louie and get an explicit yes before every
call**, and say who you are about to ring and what for. Approval for one call is
not approval for the next one.

### 2. Gather what the call will need

Read `memory/profile.md` — that is the floor, not the job. Then work out what
*this* call will be asked and go find it. A clinic wants date of birth, member
ID and the referring doctor; a restaurant wants a party size and a callback
number; a utility wants an account number.

**Ask Louie for anything you cannot find.** He would rather answer three
questions now than listen to you stall on the phone. PII is fine to request and
fine to put in the brief — it travels to the agent and into the transcript, both
of which come back to him.

When a call turns up something durable — a member ID, a doctor's name, a
pharmacy — offer to put it in `memory/profile.md` afterwards.

### 3. Settle how decisions get made

This is the part that actually determines whether the call works, and it is not
a data question. Facts are cheap to preload; *decisions* are what stall a call.
Before dialling, know:

- **What can it accept without asking?** Give a band, not a single answer —
  "any weekday morning before 11am in the next three weeks" travels much
  further than "Tuesday at 9". A band lets the agent handle three counter-offers
  without consulting once.
- **What are the hard nos?**
- **Can it commit, or must it come back to you?** If a booking is reversible,
  let it commit. If it is not, say so explicitly.
- **Will Louie be at the keyboard?** If he is, you can consult him mid-call and
  relay. If he is not, you are deciding alone and the band has to be wider.
- **Check the calendar** for anything time-bound, and include travel time.

## Placing and supervising a call

```bash
dialer health                          # queue, token, agent, number
dialer dispatch --to +1... --purpose "…" --opening "…" --brief-file brief.md
dialer watch --call-id <id> [--budget 200] [--interval 15] [--since N]
dialer answer <call_id> "your answer"  # answers AND resumes watching
dialer steer  <call_id> "context" [--speak-now]
dialer transcript <call_id>
```

`--opening` is the first thing spoken. Keep it to the announcement plus one
line of purpose; the agent-side invariants already fix the announcement wording.
`--purpose` is a short phrase used for call screening. `--brief-file` is the
whole prompt.

### The loop

`watch` blocks and returns on whichever comes first: a consult, roughly
`--interval` seconds of new dialogue, or the call ending. Then you act and call
it again. It consumes Retell's live-transcript websocket internally, so it can
return on the agent *mishandling* something as well as on it asking for help.

**When a consult comes back, answer it in your very next tool call.** Not after
a sentence of explanation — the receptionist is listening to silence for every
second you spend writing prose. `answer` resumes watching in the same process
precisely so there is no gap to fill. This rule cost three live calls to learn.

Answer with **authority, not instructions**: "anything 7 to 9pm works, later is
better; if Friday is full try Saturday" lets the agent negotiate several turns
by itself. "Say 8pm" buys you one turn and another consult.

`steer` needs nobody waiting — use it when `watch` shows the call drifting, or
when Louie tells you something new mid-call. It lands as an `injected` turn.
`--speak-now` makes the agent raise it immediately instead of waiting its turn.

When the call ends, `watch` returns the transcript with it. Read it, tell Louie
what was agreed, and offer to file anything durable to memory.

## Writing the brief

The brief becomes the agent's entire prompt. Everything not in it, the agent
does not know. See `references/brief.md` for a worked example; the shape is:

```
You are calling <who> to <what> for Louie Tan.

Details you may give freely:
- ...

What to accomplish:
- ...

Accept without asking: <the band>
Do not agree to: <the hard nos>

Anything outside the above — a cost, a policy, a form — is not yours to
decide. Ask consult_supervisor.
```

Two failure modes to write against. **Too thin** and the agent consults four
times and the call dies of stalling. **Too rigid** — one exact time, no
fallback — and the first counter-offer forces a consult. Aim for a band.

## Things that will bite you

- **An unset variable renders as a literal `{{brief}}`.** `dispatch` refuses
  empty ones, but that is the shape of the disaster: an agent on a live call
  with no instructions.
- **Retell's `get-call` transcript is post-call only.** Mid-call, the consult
  payload and `watch`'s websocket are the only sources. Do not ask the agent to
  narrate the conversation back to you — it is attached automatically.
- **A call still ringing is not a call that ended.** Handled, but if you see a
  `4004`, that is what it means.
- **Do not re-import the agent JSON in the dashboard.** It forks a new
  `agent_id` and silently orphans the config. Agent changes go through
  `/update-agent` and `/update-retell-llm`, which mutate in place.

## Calls you should not place

Anything where being a robot on the line is the problem, not the tempo:
emergency services, anything where a misunderstanding is expensive and
irreversible, or anyone who has said they do not want to be called. Say so
rather than dialling and hoping.
