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
suspicious or send you to voicemail.

## What you add, and what you don't

The agent is Sonnet 5 with a phone line. **Anything you would know cold, it
knows cold** — how to talk to a receptionist, that a menu is a menu, when to
stop pushing. Telling it those things burns a turn and muddies its context.

Three things it does not have, and supplying them is the job:

- **Louie's facts.** It knows nothing about him beyond the brief.
- **Authority.** It cannot know what Louie would accept.
- **Your reach.** Calendar, maps, memory, the web — and Louie himself in chat.

Then one thing beyond supplying: **be an adversarial reviewer of its
decisions.** It is deciding fast, on partial information, with someone waiting.
You are slower, far better informed, and under no social pressure. Assume its
judgement is good and check it anyway, hardest on anything irreversible.

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

The brief is the agent's entire prompt. It carries the three things above and
nothing else — no coaching on tone or telephone manner.

**Be thorough past the point of feeling excessive**, on facts specifically —
this is where your instinct to be concise is wrong. A fact you preloaded and
never needed cost nothing; one you left out costs six to thirteen seconds of
dead air and burns a consult. Put in the tangential stuff: the spelling of the
name, the old address as well as the current one, the insurance group number as
well as the member ID, the reference number from the letter.

The test is whether it was knowable before you dialled. Everything enumerable
goes in; what the consult is for is the answer that depends on what they say —
whether the slot just offered clears the calendar once driving time is counted.

What it has to carry:

- **Who is being called, and what done looks like** — enough to recognise
  success and stop pushing, failure and stop trying.
- **Every fact the other side might ask for**, and which are free to volunteer
  versus only on request.
- **The band of acceptable outcomes**, the hard nos, and whether it may commit
  to something irreversible.
- **The categories that should escalate** — a cost, a policy, a form, a
  referral. Naming categories is what makes the tool fire on the question you
  did *not* anticipate.

### The band is the whole trick

Facts are cheap. *Decisions* stall calls, so the brief carries decision
authority, not just data.

"Any weekday morning before 11am in the next three weeks, any doctor" absorbs
three counter-offers without a consult. "Tuesday 9am with Dr. Okafor" turns the
first one into a consult. Write the band wide enough that the likely
counter-offers fall inside it, and say what to do when they do not.

One input you can only get up front: **will Louie be at the keyboard?** If not,
you are deciding alone and the band has to be wider.

### Where the facts come from

`memory/profile.md` is the floor, not the job — then work out what *this* call
will be asked and go find it. Ask Louie for whatever you cannot; he would rather
answer three questions now than listen to you stall on the phone. PII is fine to
request and to put in the brief: it goes to the agent and into the transcript,
both of which come back to him. Afterwards, offer to file anything durable the
call turned up back to `profile.md`.

## Running the call

```bash
dialer health                          # queue, token, agent, number
dialer dispatch --to +1... --purpose "…" --opening "…" --brief-file brief.md
dialer watch --call-id <id> [--budget 200] [--interval 15] [--since N]
dialer answer <call_id> "your answer"  # answers AND resumes watching
dialer steer  <call_id> "context" [--speak-now]
dialer transcript <call_id>
dialer journal                         # what you actually did, after an interrupt
```

`--opening` is the first thing spoken: the announcement plus one line of purpose
(the invariants fix the announcement wording). `--purpose` is a short phrase for
call screening.

`watch` blocks and returns on whichever comes first — a consult, roughly
`--interval` seconds of new dialogue, or the call ending. Then you act and call
it again.

**When a consult comes back, answer it in your very next tool call.** Not after
a sentence of explanation — every second you spend writing prose is silence on
the line. `answer` resumes watching in the same process precisely so there is no
gap to fill. This rule cost three live calls to learn.

Answer with **authority, not instructions** — a band, same as the brief. On a
live call one band answer carried four turns unaided: it declined the offered
slot, asked for a window, judged the counter-offer out of band *without
consulting again*, and fell back to a callback. "Say 8pm" buys one turn and
another consult.

**Verify before you authorise.** Read the calendar, count the travel, ask Louie
in chat. This is the adversarial review, and it is the half most easily skipped
under time pressure — a band granted unchecked is how a conflicting slot got
booked.

`steer` injects context without the agent asking. Use it when Louie tells you
something new, or when the agent is genuinely going wrong — not to narrate what
it can already hear. And remember you are reading a lagging, truncated copy:
`watch` cuts utterances mid-word, and one steer "corrected" a keypress that had
been right. Wait for the next `watch` rather than act on a partial line.

**Avoid `--speak-now` unless it absolutely cannot wait.** It makes the agent
talk over whoever is mid-sentence. Without it the context lands on the next
natural turn, which is almost always soon enough.

When the call ends, `watch` returns the transcript with it. Tell Louie what was
agreed.

### If Louie wants to listen or take over

Point him at **`dashboard.retellai.com/live-monitoring`**. Clicking the ongoing
call opens a panel with a live transcript and three controls: `Live Listen`,
`Take Over`, `End Call`. Listening is silent to the other side and reversible;
taking over is confirm-gated and permanent. It reaches Retell directly, so it
works alongside this skill on claude.ai.

**That page cannot send text**, so `steer` is the only way to redirect a call
without seizing it — if he is watching and wants a course correction, he has to
ask you.

## Things that will bite you

- **An unset variable renders as a literal `{{brief}}`** — a live call with no
  instructions. `dispatch` refuses empty ones.
- **`get-call`'s transcript is post-call only.** Mid-call, the consult payload
  carries it automatically and `watch` streams it.
- **A `4004` from the monitor socket means "not live yet", not "over"** — a
  ringing call closes with it.
- **An interrupted tool call vanishes from your transcript.** It still ran, so
  never conclude "I did not do X" from X being absent. **Run `dialer journal`
  after any interruption** — it records every dispatch, answer and steer before
  sending, and survives the turn. `answer` also returns the text already
  delivered, and `dispatch` refuses while a call is live.
- **Keypresses are inaudible, to everyone.** DTMF is out-of-band, so no one
  hears a tone; the digits appear only as `[TOOL press_digit]`. A far-end
  system wanting in-band tones drops them silently and the menu repeats — a
  menu looping twice is the signature, so stop pressing and find another route.
- **A take-over is not a hangup.** Retell ends the *agent's* leg while Louie
  stays on the phone, and transcription stops dead there. `watch` returns
  `event: taken_over`; ask him how it went rather than reporting the fragment.
- **Never re-import the agent JSON in the dashboard** — it forks a new
  `agent_id` and orphans the config. Use `/update-agent` and
  `/update-retell-llm`, which mutate in place.
