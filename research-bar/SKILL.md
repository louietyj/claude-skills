---
name: research-bar
description: "The minimum bar Louie expects whenever you research something for him: recommending, finding, comparing or vetting anything (a restaurant, a product to buy, a contractor, salon or other service provider, a deal or subscription) or reading what people say online about a topic. Load it before the first search, including for casual asks like 'any good pho nearby?', 'where should I get X done?', 'which one should I buy?' or 'what do people think of Y?'. Not for a single fact with one right answer (an opening time, a spec, a price you're simply quoting)."
---

# research-bar

When Louie asks you to find, recommend, compare or research something, research
it **the way he would himself**. He is a chronic over-researcher. He hands the
work to you to save his time, not to skip the looking: go to the same places he
would, take in what is there, and bring him the picture. You can usually get
the same data faster than he can, because you can script and scrape and he has
two eyeballs.

**Read `/memory/research-bar.md` first** (with `cfs read`). It holds the calibrations
for specific areas (shopping, local services, discourse...), the cases already
worked through with him, and what he looks at. What follows is the general bar.

## The food chain

Star ratings, rankings and "top 10" lists are inputs to your judgment. Your
judgment is an input to his, and the decision is always his. So you are not on
the hook for the outcome. You **are** on the hook for the legwork: did you take
in everything relevant?

"I have three names with ratings" is not done. Done is: you would be confident
committing to this yourself, having seen what Louie would have looked at.

## How it goes

1. **Why and what.** Work out why he is asking, what problem it solves, and
   exactly what he is buying or deciding, from context and memory first. Never
   fill the gap from a default: "a haircut" can be a trim, colour or balayage,
   and which one changes every later step. If it is still unclear and the answer
   would change the research, ask, with every question in one message. Don't
   invent questions to look thorough.
2. **What would Louie look at, and how do you get it?** List what he would
   check, then find the cheapest way to get the same data at breadth.
3. **Draw the ladder.** Unless the ask is tiny, lay out the rungs, **including
   the ones you think he'd skip**. Mark where you guess he'd stop, and give
   your take. He marks each rung do, skim or skip, and you start. If he's away
   or it's urgent, go to your mark and say that's what you did.
4. **Execute, then bring him a picture:** what you found, your ranked picks (a
   few, not one), the calls left for him, and anything relevant he never
   mentioned. Say what you read first-hand versus what a subagent, snippet or
   aggregator told you, and label estimates as estimates.

## A rough floor

This is the floor, not the ceiling. None of it is binding; use judgement.

- **Breadth before depth.** Go wide before you narrow: all his usual sites, more
  than three candidates, past the nearest ones. A place he already uses is one
  candidate, not a reason to stop looking. Breadth is where you have fallen
  short before, more often than rigor.
- **Strike disqualifiers early.** A hard requirement (the right sensor type, an
  active licence, a clean disciplinary record) gets checked before anything is
  shortlisted, not after.
- **Read the primary material.** Menus and price lists rather than `$$` symbols.
  Listing pages rather than search cards. Photos, noting how recent they are:
  you are a vision model, and user photos often hold the menu or the real
  product. Actual review text, sorted by newest, lowest and most helpful where
  the tool allows, looking for a common theme in the negatives. A 4.8 whose bad
  reviews all say the same thing is worth knowing.
- **Reddit by default**, whenever there might be something there. `mcp-reddit`
  runs through `lmcps`, so it is cheap: about ten queries in one bash loop,
  written to a file, and then you read what's worth reading. If nothing is there,
  drop it. People swearing by something is strong organic signal. Include history
  and discourse when they bear on the question ("is there a pattern to their
  Black Friday deals?").

  ```bash
  for q in "query one" "query two" "query three"; do
    printf '\n### %s\n' "$q"
    lmcps call mcp-reddit search_reddit "$(jq -nc --arg q "$q" '{query: $q, limit: 25}')"
  done > "$SCRATCH/reddit.txt" 2>&1
  ```
  (`$SCRATCH` is your scratchpad.) `get_post_details` reads a thread's comments.
- **Cheap breadth is always worth it.** If you know a tool or API that he didn't
  think of and you can loop it in one bash call, query it. A miss costs one call.

## The plan is a floor too

Follow leads that come up mid-research without asking (another site, a question
that just surfaced), as long as the cost stays in proportion. Check in only
before a jump in scale (three subagents becoming twenty), or before anything
that needs his login: ask first, and if he's away, use the request-credential
skill. Contacting anyone (an email, a contact form, a call) is out of scope:
offer it, and expect him to say no.
