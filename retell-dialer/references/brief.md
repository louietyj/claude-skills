# Writing a brief

The brief is the agent's entire prompt — `{{brief}}` followed by the invariants,
and nothing else. Whatever is not in it, the agent does not know.

## A worked example

This one was used on a live clinic call. It produced two consults in two
minutes, both genuinely outside its scope, and the agent handled everything else
alone.

```
You are calling Bayview Family Medicine to book a routine annual physical for
Louie Tan, an existing patient.

Patient details you may give freely:
- Louie Tan — T-A-N. Date of birth 4 March 1989.
- Member ID BX-4471902, Blue Shield PPO.
- Callback number 555-0142.

What to book:
- A routine annual physical, roughly 30-45 minutes.
- A weekday morning, any time before 11am, within the next three weeks.
- Dr. Okafor preferred.

Accept without asking: any weekday slot before 11am in the next three weeks,
with any doctor if Okafor is unavailable.

Keep it brief and warm. Anything outside the above — a different time of day, a
cost, a policy, a form, a referral, a pharmacy — is not yours to decide. Ask
consult_supervisor.
```

The last paragraph is doing real work. Naming the *categories* that should
escalate is what makes the tool fire on an unanticipated question; a brief that
only lists facts leaves the agent to decide what counts as out of scope, and it
decides generously.

## The band is the whole trick

Facts are cheap — preload them and they cost nothing. Decisions are what stall a
call, so the brief has to carry decision authority, not just data.

On the call above the agent was offered a slot, then a second slot, then asked
about a doctor substitution. It consulted on none of them, because
"any weekday slot before 11am, any doctor" covered all three. Compare a brief
saying "Tuesday 9am with Dr. Okafor": every one of those turns becomes a consult,
and three consults is a call that dies of stalling.

Write the band wide enough that the *likely* counter-offers fall inside it.

## What belongs in the opening

`--opening` is spoken before anything else. The announcement wording is fixed by
the invariants, so the opening should be that sentence plus one line of purpose:

```
Hello, I'm an AI assistant for Louie Tan. This call is being transcribed and
sent to him. I'm calling to book an annual physical.
```

If you expect an IVR, keep it shorter still — it is being said to a machine, and
the invariants make the agent re-announce to the human who eventually picks up.

## Answering a consult

Answer with authority, not a script. The agent is a competent negotiator given
room:

> Do not take 5 PM — that's far too early. Ask what they have from 7 PM onward;
> anything 7:00 to 9:00 works, and later is better than earlier. If the whole
> evening from 7 is genuinely full this Friday, ask about Saturday at the same
> time instead. Only if both are impossible, say you'll check with Louie and
> call back.

That single answer carried four turns of negotiation: it declined 5pm, asked for
a window, correctly judged an offered 10pm as outside the band *without
consulting again*, tried Saturday, and fell back to the callback when told they
were closed weekends.

An answer of "say 7pm" would have bought one turn and produced another consult.

## Refusing to guess

The invariants forbid guessing, but the brief should say what to do instead when
you can predict the gap. "If they ask for a pharmacy, say Louie will confirm at
the visit" is better than letting it consult, because it costs no hold time.

Reserve the consult for what you genuinely could not have anticipated.
