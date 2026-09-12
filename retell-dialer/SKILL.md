---
name: retell-dialer
description: "Place a real phone call on Louie's behalf and supervise it live — restaurant bookings, doctor's offices, clinics, utilities, anywhere the only channel is a phone line. A fast voice agent runs the conversation; when it hits something its brief does not cover it consults you mid-call and waits for your answer, and you can inject corrections it never asked for. Use whenever a task needs someone phoned. NOT fire-and-forget: you stay on the loop for the whole call, and you must have Louie's explicit go-ahead before dialling."
---

# retell-dialer

A Retell voice agent holds the conversation at human tempo. It calls
`consult_supervisor` when it needs something and your answer is what it speaks;
you can also push context in unprompted.

**The number that governs everything here:** a consult reaches you in
milliseconds, but your answer is 6–13 seconds of silence on someone else's
phone. A receptionist tolerates that twice, maybe three times, before they get
suspicious or send you to voicemail. The consult is an emergency reserve, not
the mechanism — which is why most of this file is about the brief.

## Setup (once per conversation)

```bash
bash /mnt/skills/user/retell-dialer/setup.sh
```

Puts `dialer` on PATH and proves the consult queue answers. If it **fails, stop
and tell Louie** — do not place a call. Without the queue, `consult_supervisor`
times out with a stranger holding the line.

**Never re-derive the path or set a shell variable for it.** Shell state dies
with the bash call that set it. Just call `dialer`.

## Get permission. Every call.

Skills have no permission prompt — nothing stands between you and a real phone
ringing in someone's office. **Ask Louie and get an explicit yes before every
call**, saying who you are about to ring and what for. Approval for one call is
not approval for the next.

## Writing the brief

The brief becomes the agent's entire prompt: `{{brief}}`, then the invariants,
nothing else. Whatever is not in it, the agent does not know — and discovers it
does not know while someone is waiting.

**Be thorough past the point of feeling excessive.** This is the one place where
your instinct to be concise is actively wrong. A fact you preloaded and never
needed cost you nothing; a fact you left out costs six to thirteen seconds of
dead air and burns one of your two or three consults. Put in the tangential
stuff: the spelling of the name, the old address as well as the current one, the
insurance group number as well as the member ID, what was tried last time, who
they spoke to before, the reference number from the letter.

**The test is whether it was knowable before you dialled** — not whether it is a
fact. A member ID, a policy number, the spelling of a street: enumerable, so
they belong in the brief, and spending hold time on them is a planning failure
playing out on someone else's phone.

But plenty of answers are a *function of what the other side says* and could not
have been preloaded — whether the slot they just offered clears the calendar
once driving time is counted, whether a quoted price beats the one already in
hand. You hold the calendar, maps and memory; the agent holds none of them and
cannot be given all of them. Those lookups are exactly what the consult is for.
Reserve it for what their answer made necessary, and preload everything else.

Write it as whatever prose and bullets fit the call — it is a prompt, not a
form. What it has to carry:

- **Who is being called, and what done looks like** — enough to recognise
  success and stop pushing, failure and stop trying.
- **Every fact the other side might ask for**, per above — and which are free to
  volunteer versus only on request.
- **The band of acceptable outcomes**, plus the hard nos and whether it may
  commit to something irreversible.
- **An instruction to consult rather than decide, naming the categories** you
  expect (a cost, a policy, a form, a referral). Naming categories is what makes
  the tool fire on the question you did *not* anticipate; a brief listing only
  facts leaves the agent to judge what counts as out of scope, and it judges
  generously.

### The band is the whole trick

Facts are cheap. *Decisions* stall calls, so the brief carries decision
authority, not just data.

"Any weekday morning before 11am in the next three weeks, any doctor" absorbs
three counter-offers without a consult. "Tuesday 9am with Dr. Okafor" turns the
first one into a consult. Write the band wide enough that the likely
counter-offers fall inside it, and say what to do when they do not.

One input you can only get up front: **will Louie be at the keyboard?** If not,
you are deciding alone and the band has to be wider. Check his calendar for
anything time-bound, and include travel time.

### Where the facts come from

`memory/profile.md` is the floor, not the job — then work out what *this* call
will be asked and go find it. Ask Louie for whatever you cannot; he would rather
answer three questions now than listen to you stall on the phone. PII is fine to
request and to put in the brief: it goes to the agent and into the transcript,
both of which come back to him. Afterwards, offer to file anything durable the
call turned up — a member ID, a doctor's name — back to `profile.md`.

## Running the call

```bash
dialer health                          # queue, token, agent, number
dialer dispatch --to +1... --purpose "…" --opening "…" --brief-file brief.md
dialer watch --call-id <id> [--budget 200] [--interval 15] [--since N]
dialer answer <call_id> "your answer"  # answers AND resumes watching
dialer steer  <call_id> "context" [--speak-now]
dialer transcript <call_id>
```

`--opening` is the first thing spoken: the announcement plus one line of purpose
(the invariants fix the announcement wording). `--purpose` is a short phrase for
call screening.

`watch` blocks and returns on whichever comes first — a consult, roughly
`--interval` seconds of new dialogue, or the call ending. Then you act and call
it again. It consumes the live-transcript websocket internally, so it also
returns on the agent *mishandling* something it never thought to ask about.

**When a consult comes back, answer it in your very next tool call.** Not after
a sentence of explanation — every second you spend writing prose is silence on
the line. `answer` resumes watching in the same process precisely so there is no
gap to fill. This rule cost three live calls to learn.

Answer with **authority, not instructions** — a band, same as the brief. On a
live call one band answer carried four turns unaided: it declined the offered
slot, asked for a window, judged the counter-offer out of band *without
consulting again*, and fell back to a callback. "Say 8pm" buys one turn and
another consult.

`steer` needs nobody waiting on the far end — use it when `watch` shows the call
drifting, or when Louie tells you something new mid-call. It lands as an
`injected` turn; `--speak-now` makes the agent raise it immediately.

When the call ends, `watch` returns the transcript with it. Tell Louie what was
agreed.

### If Louie wants to listen or take over

Point him at **`dashboard.retellai.com/live-monitoring`**. Clicking the ongoing
call opens a panel with a live transcript and three controls: `Live Listen`,
`Take Over`, `End Call`. Listening is silent to the other side and reversible
(`Exit Live Listen`); taking over is confirm-gated and permanent. It reaches
Retell directly and needs nothing from you, so it works alongside this skill on
claude.ai. Offer it for the first few real calls.

**That page cannot send text.** Listen, seize, or hang up are the only options,
so `steer` is the only way to redirect a call without taking it — if he is
watching and wants a course correction, he has to ask you for it.

## Things that will bite you

- **An unset variable renders as a literal `{{brief}}`.** `dispatch` refuses
  empty ones, but that is the shape of the disaster: a live call with no
  instructions.
- **`get-call`'s transcript is post-call only.** Mid-call, the consult payload
  and `watch` are the only sources. Never ask the agent to narrate the
  conversation back to you — it is attached automatically.
- **A call still ringing is not a call that ended.** Handled, but a `4004` from
  the monitor socket means "not live yet", not "over".
- **A take-over is not a hangup.** If Louie seizes the call from the dashboard,
  Retell ends the *agent's* leg — he is still on the phone. `watch` returns
  `event: taken_over`, and the dashboard says outright "Transcription is paused
  while you're on the call": nothing after that moment is recorded anywhere.
  Ask him how it went; never report the truncated transcript as the outcome.
- **Never re-import the agent JSON in the dashboard.** It forks a new `agent_id`
  and silently orphans the config. Agent changes go through `/update-agent` and
  `/update-retell-llm`, which mutate in place.
