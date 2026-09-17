# retell-dialer

A Retell voice agent holds the conversation at human tempo. It calls
`consult_supervisor` when it needs something and acts on your answer;
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

## Weekdays come from the Gregorian calendar, never from your head

Google Calendar gives bare timestamps like `2026-09-15T00:00:00-07:00`, with
no day of the week, so it is tempting to work the weekday out. Under call
pressure that goes wrong: "Thursday, September 18th" went to a real rep in a
month where the 18th was a Friday. Take every weekday from the calendar setup
printed. For a month past it, run `dialer gregorian_calendar --from 2027-01`
first, every time, however sure you are. Mid-call, add the call id
(`dialer gregorian_calendar CALL_ID --from 2027-01`) and the agent is sent the
same calendar, so leave it out of your answer.

## Get permission. Every call.

Skills have no permission prompt — nothing stands between you and a real phone
ringing in someone's office. **Ask Louie and get an explicit yes before every
call**, saying who you are about to ring and what for. Approval for one call is
not approval for the next.

## Writing the brief

The brief is the agent's entire prompt. It opens with **who is being called and
what done looks like** — enough to recognise success and stop pushing, failure
and stop trying — then carries the three things above and nothing else: no
coaching on tone or telephone manner.

`dispatch` appends the same Gregorian calendar to every brief, so the agent has
it too. Leave it out of what you write.

**Take stock before you write.** Every fact about Louie that could come up,
every decision the call could force, every tool you hold. Then sort each into
the buckets below: what differs between them is whether it goes into the brief,
and how specifically.

### Facts: by how much of it fits in the brief

1. **Likely useful, enumerable — spell it out.** Be thorough past the point of
   feeling excessive; leaving things out is the wrong instinct here (the
   compression gate below cuts words, never facts). A fact
   you preloaded and never needed cost nothing; one you left out costs six to
   thirteen seconds of dead air and burns a consult. Put in the tangential
   stuff: the spelling of the name, the old address as well as the current one,
   the insurance group number as well as the member ID, the reference number
   from the letter, every slot that suits Louie in the next two weeks. Say which
   are free to volunteer and which only on request.
2. **Likely useful, impractical to enumerate — name it, with a trigger.** The
   rest of the calendar, every past claim, drive times from anywhere. Say where
   the written-down part ends and that a consult reaches past it: "Anything
   outside these slots, consult me and I'll check his calendar." The trigger is
   what makes the agent ask instead of declining.
3. **Unlikely to matter, but might — broad strokes.** A sentence or two on what
   a consult can resolve — something to look up, something to work out,
   something to put to Louie — and what you hold: his full profile, email and
   documents, maps, the web, Louie himself in chat. Categories, not contents.
   Without this, a question outside the brief looks like a dead end, and the
   agent guesses or refuses instead of asking.

### Decisions: by who may make them

1. **The agent may commit on the spot.**
2. **You may commit on the spot** — latitude Louie gave you that takes judgement
   or research the agent cannot do mid-call. "Accept if it's under $100 and
   better than anything else we could get" delegates a comparison to you.
3. **Only Louie may commit.**
4. **Pre-rejected** — the hard nos.

**The brief carries 1 and 4.** Anything between them is a consult, and you hold
the line between 2 and 3 — so the agent needs the categories that escalate (a
cost, a policy, a form, a referral), not which of the two each one lands in.
Naming categories is what makes the tool fire on the question you did *not*
anticipate. If the lines between the four are unclear, settle them with Louie
before dialling, not on the call.

**The agent's latitude is the whole trick.** Facts are cheap; *decisions* stall calls. "Any
weekday morning before 11am in the next three weeks, any doctor" absorbs three
counter-offers without a consult. "Tuesday 9am with Dr. Okafor" turns the first
one into a consult. Write it wide enough that the likely counter-offers fall
inside, and say whether the agent may commit to anything irreversible.

Do bucket 2's research before dialling where you can — your answer is dead air
on the line. And ask up front: **will Louie be at the keyboard?** If not, bucket
3 cannot be answered live. Move what you can into 2 with him now; the rest ends
in a callback.

### Tools: by whether they could matter

1. **Might be useful** — goes in with facts bucket 3, in broad strokes.
2. **No way it's relevant** — leave it out. If something genuinely tricky comes
   up the agent will consult you, and you are reading the transcript live a few
   seconds behind anyway. When you can resolve something it did not ask about —
   a clash with the calendar it had no way to see — make whatever tool calls it
   takes and `steer` it with the answer.

### Where the facts come from

`cfs read /memory/profile.md` (the durable-filesystem skill) is the floor, not
the job — then work out what *this* call will be asked and go find it. Ask Louie for whatever you cannot; he would rather
answer three questions now than listen to you stall on the phone. PII is fine to
request and to put in the brief: it goes to the agent and into the transcript,
both of which come back to him. Afterwards, offer to file anything durable the
call turned up back to `profile.md`.

### MANDATORY GATE: compress the brief before every dispatch

Retell tokens are expensive and the brief is re-sent on every turn, so every
token counts. **This is a compression task, not a summarisation task.** The
thoroughness asked for above is non-negotiable: every fact, band edge and
consult trigger survives. What goes is every word that is not carrying one.
There is no target length. Compact hard, as dense as it will go.

**Three steps, the draft and the final in separate messages. No exceptions,
however short the brief feels.**

1. **Write the draft to a markdown file with a tool call.** Complete and
   honest, not a strawman left loose for step 2 to find, but do not compress as
   you go: density is step 3's job.
2. **Then think.** A thinking block only opens at the start of a message, and a
   tool result is the only thing that starts one without handing the turn back
   to Louie. That is why the draft goes to a file and not into your reply.
   Compressing "in your head" is not this step: once words are written, the
   coherent next tokens agree with them, so a pass in the same breath comes out
   as small local edits and never finds the section that should not exist. In
   the thinking block, go through the draft against the list below and name
   every cut before making any, largest unit first: whole sections, then
   bullets, then sentences, then words. A final that comes out barely shorter
   started at word level.
3. **Make the cuts in that file**, editing or rewriting whichever they call
   for, then dispatch it.

Never reprint the draft into your reply or show a before/after.

Cut:

- **What the agent knows cold.** Ask what *you* have strong priors on: if you
  know it without being told, so does the agent. Verifying identity with the
  facts given, explaining why you are calling, getting a confirmation number
  before hanging up.
- **Anything said twice.** A fact given once does not come back in the
  decisions. A band implies its complement: "≤$45 accept, $45–60 consult" does
  not also need "don't accept $50 on the spot". Nothing the invariants already
  carry: announcing, not guessing, reading back.
- **Extra words in what stays.** If a point can be said in fewer words, rewrite
  it in fewer: no filler, no hedges, no qualifiers that change nothing.
- **Prose where a fragment works.** `acct 1234 56 789`, not "The account number
  is…". Glue sentences, "(see below)", headings that organise
  nothing.
- **Reasons the agent cannot act on.** Keep a *why* only when it changes what
  the agent does: "the only number to give; this line takes no inbound calls"
  stays.

## Running the call

```text
dialer health
    Queue, token, agent, webhook, number. setup.sh already ran it; run it
    again only to diagnose a failure.

dialer dispatch --to +16695550142 --opening "…" --purpose "…" --brief-file brief.md
    --to          E.164.
    --opening     Spoken verbatim the moment they pick up. Start with exactly
                  "Hello, I'm an AI assistant for Louie Tan. This call is recorded
                  and a transcription will be sent to him." then one sentence of
                  purpose.
    --purpose     A few words, given if a call-screening service asks what the
                  call is about.
    --brief-file  The brief as plain prose, in any file (or --brief "…"
                  inline). Files last the whole conversation; shell
                  variables do not.
    Prints {"call_id": "call_…", "call_status": "registered"}; that call_id is
    the CALL_ID below.
    If any call is already live it refuses instead, printing each one's
    call_id, number, start time and brief. One of them the call you were
    placing (an interrupted turn loses the tool call, not the call)? Watch
    it. None of them? It is another conversation's: leave it alone and
    dispatch again with --force.

dialer watch CALL_ID [--interval 15] [--budget 200] [--since N]
    Defaults as shown. Blocks, then prints one JSON object whose "event" is
    the first of:
      consult       Immediately, whatever the timers say. "question" is what
                    the agent asked, "consult_id" is what you answer, and
                    "transcript" is the call so far.
      transcript    A finished turn, once --interval seconds have passed.
                    "new" is the lines since --since.
      idle          --budget ran out and nobody spoke. "monitor": "no frames"
                    means the live transcript is not arriving; consults
                    still will.
      call_ended    With the full "transcript". A call nobody picked up has
                    "call_status": "not_connected", a null transcript and a
                    "disconnection_reason" such as dial_no_answer.
      taken_over    Louie took the call over; see below.
      error         Retell has no call with that id: a mistyped CALL_ID.
    consult, transcript and idle carry "hint", naming the --since to pass
    next; a consult's hint is the whole answer command. Act on what came
    back, then watch again.

    --interval N  Batching floor, not a wait: it never returns on silence,
                  it just stops watch returning once per word. Set it for
                  what the call is doing right now, and judge it afresh on
                  every watch, answer and steer:
                    15  negotiating, scheduling, anything you must follow
                        closely and react to fast.
                    30  the agent is working through its brief, a phone
                        menu, hold: slow going that needs little attention.
                  Every return spends one of your finite tool calls.
    --budget N    The cap for silence -- hold music, a long menu, "let me
                  check" -- where --interval never fires. Keep it at or under
                  200: the sandbox kills at 300s and discards all output.
    --since N     Where to resume; every return's hint carries the number.
                  "18 + 1 in progress" resumes at 18, so that turn comes back
                  finished rather than cut off. Lost it, or a watch was cut
                  off by an interruption? Watch again without it and you get
                  the whole call so far.

dialer answer CONSULT_ID [watch flags] <<'EOF'
dialer steer  CALL_ID [--speak-now] [watch flags] <<'EOF'
    The text goes on stdin through a quoted heredoc, closed by EOF alone on
    its own line. Never as an argument: the shell rewrites it first, and
    "$50" arrives as "0".
    Send, then keep watching in the same process: same returns, same flags.
    The next turn or two is what shows whether you were understood.
    If the agent asks something new before you answer, it queues: answering
    one consult returns the next straight away.

dialer hangup CALL_ID
    Drops the call at once and prints what `watch` prints when a call ends.
    Dire straits only; see below.

dialer transcript CALL_ID
    Post-call only; mid-call, use watch.

dialer journal
    Every dispatch, answer, steer and hangup, one JSON line each, written
    before the request goes out: {"at", "command", "call_id", "text"} (answer adds
    "consult_id"), followed by a line with its "status" (for dispatch, also
    the new "call_id"). A line
    with no status after it was cut off in flight and may still have landed;
    resend an answer if unsure, since a duplicate fails and shows the text
    that landed. See the rule below.

dialer gregorian_calendar [CALL_ID] [--from YYYY-MM] [--months 3]
    Every date with its weekday, one month per line:
      Sep 2026: 1T 2W 3R 4F 5S 6U 7M ...
    M T W R F S U are Monday to Sunday. Plain dates only: it knows nothing
    of Louie's Google Calendar.
    With a CALL_ID it also steers the same calendar to that call's agent,
    silently and without watching. Do not repeat it in an answer.
```

A call, start to finish (output trimmed):

```text
$ dialer dispatch --to +16695550142 --purpose "move a dental cleaning" \
    --opening "Hello, I'm an AI assistant for Louie Tan. This call is recorded and a transcription will be sent to him. I'm calling to move his Friday cleaning." \
    --brief-file brief.md
{"call_id": "call_8f2e", "call_status": "registered", "call_type": "phone_call"}

$ dialer watch call_8f2e
{"event": "transcript", "turns": "3 + 1 in progress",
 "new": ["agent: Hello, I'm an AI assistant for Louie Tan. ...",
         "user: Sure, can you spell his last name?",
         "agent: T-A-N.",
         "user: Okay, I've got  [...utterance may still be in progress]"],
 "hint": "continue with `--since 3` on watch, answer or steer"}

$ dialer watch call_8f2e --since 3
{"event": "consult", "consult_id": "call_8f2e:q7c1",
 "question": "They offered Tuesday 2pm. Accept?",
 "transcript": "...", "turns": "6 + 1 in progress",
 "hint": "answer with: dialer answer call_8f2e:q7c1 --since 6 <<'EOF', your text, then EOF alone on the last line"}

$ dialer answer call_8f2e:q7c1 --since 6 <<'EOF'
No afternoons. Any weekday before 11am next week.
EOF
{"event": "transcript", "turns": "9 + 1 in progress", "new": [...],
 "hint": "continue with `--since 9` on watch, answer or steer"}

        Louie, in chat: "Wednesday afternoon works too"

$ dialer journal
{"at": "...", "command": "answer", "call_id": "call_8f2e", "consult_id": "call_8f2e:q7c1", "text": "No afternoons. ..."}
{"at": "...", "command": "answer", "call_id": "call_8f2e", "consult_id": "call_8f2e:q7c1", "status": 200}

$ dialer steer call_8f2e --since 9 <<'EOF'
Louie says Wednesday afternoon also works.
EOF
{"event": "call_ended", "call_ended": true, "disconnection_reason": "agent_hangup",
 "transcript": ["agent: Hello, I'm an AI assistant for Louie Tan. ...", "...",
                "agent: Thanks so much, goodbye."]}
```

### After any interjection, read the journal first

**Any time a new user message arrives while a call is live, run `dialer
journal` before your next `answer`, `steer` or `dispatch` on that call. No
exceptions, whatever the transcript appears to show.**

A new message means a turn boundary, and a turn boundary is exactly where
claude.ai drops an in-flight tool call. This is a habit, not a diagnosis: do not
try to notice whether something is missing, because the interruption erases the
evidence you would notice it by. An answer you already sent looks identical to
one you never sent. It is one local read against your own ledger — cheap enough
to do on every resumed turn, including the many where nothing was lost.

This has already cost a real call: a dropped `answer` had told the office to
keep the original appointment, the supervisor sent a contradicting one, and the
receptionist asked "wait, do you want me to cancel it or not?"

It still comes first with a consult waiting.

**When a consult comes back, answer it straight away.** Run only what the
answer depends on first — the journal, a calendar read — and write no prose:
every second of it is silence on the line. `answer` resumes watching in the
same process precisely so there is no gap to fill. This rule cost three live
calls to learn. After 90 seconds unanswered the agent is told to promise a
callback rather than guess, and a late answer fails saying so.

Answer with **authority, not instructions** — a band, same as the brief. On a
live call one band answer carried four turns unaided: it declined the offered
slot, asked for a window, judged the counter-offer out of band *without
consulting again*, and fell back to a callback. "Say 8pm" buys one turn and
another consult.

Keep answers and steers info-dense: their text stays in the agent's context for
the rest of the call. There is no time to polish mid-call, so no second pass —
just no filler.

**Verify before you authorise.** Read the calendar, count the travel, ask Louie
in chat. This is the adversarial review, and it is the half most easily skipped
under time pressure — a band granted unchecked is how a conflicting slot got
booked.

`steer` injects context without the agent asking, then watches what the agent
does with it. Use it when Louie tells you something new, or when the agent is
genuinely going wrong — not to narrate what it can already hear. A live view
marks its last line `[...utterance may still be in progress]`: that sentence is
still being spoken and may end differently, so never steer on it — wait for
the next `watch`. One steer "corrected" a keypress that had been right. Every
line above it is final and safe to act on.

**Never use `--speak-now` unless you are in dire straits.** It cuts the agent
off mid-sentence to reply afresh. Without it the agent still reads the context
before its next reply — including one that would confirm something.

A steer only reaches a call in progress: not one still ringing, nor one over.

To end a call early, `steer` the agent to wrap up and hang up as soon as it
can; it has an end-call tool. **`dialer hangup` is for dire straits only** —
an agent hallucinating or ignoring steers. It drops the line mid-word, with no
goodbye, on someone who was talking to Louie's representative.

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
- **An interrupted tool call vanishes from your transcript.** It still ran, so
  never conclude "I did not do X" from X being absent — see the journal rule
  above. Answering a consult that was already answered fails and shows the
  text that was sent; `dispatch` refuses while a call is live.
- **Keypresses are inaudible, to everyone.** DTMF is out-of-band, so no one
  hears a tone; the digits appear only as `[TOOL press_digit]`. A far-end
  system wanting in-band tones drops them silently and the menu repeats — a
  menu looping twice is the signature, so stop pressing and find another route.
- **A take-over is not a hangup.** Retell ends the *agent's* leg while Louie
  stays on the phone, and transcription stops dead there. `watch` returns
  `event: taken_over`; ask him how it went rather than reporting the fragment.
