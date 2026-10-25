---
name: calendar-commute
description: "Add a drive event before one of Louie's calendar events, matching what Google Calendar's Maps panel writes when he clicks the location, enters a start point and hits Add to Calendar: same length, start, title and description. Use whenever he asks to add commute, drive or travel time before an event, including from a scheduled task that has picked the events."
---

# Calendar commute

The Maps panel in Google Calendar adds a drive event that ends when the event
starts and begins at its "Leave around" time. That time is the **upper end** of
Maps' "typically X–Y min" range, not the headline estimate. This skill
reproduces that event for one calendar event from the public Directions API. In
a controlled head-to-head against the panel's own request it matched 23 of 24
routes exactly; the one miss was one second past a rounding boundary.

Deciding which events need a drive is the caller's job. This skill takes one
event and adds its drive.

**A drive only ever leads to an event.** Never add a drive home, or any drive
away from an event, when there is no next event at the far end to make.

All the arithmetic runs in `commute`, never by hand in context. Once per
conversation, after `session-init` (it fetches the local-mcps config, which
holds the Maps key):

```bash
bash "$(ls -1dt /mnt/skills/*/calendar-commute ~/.claude/skills/synced/*/calendar-commute 2>/dev/null | head -1)/setup.sh"
```

That lookup is deliberate: claude.ai chat puts skills under `/mnt/skills/user/`
or `/mnt/skills/plugins/`, Cowork and cloud Claude Code under
`~/.claude/skills/synced/<bucket>/`, and hardcoding any one breaks the others.

It puts `commute` on PATH. **Never re-derive the path or set a shell variable
for it.** Just call `commute`.

## 1. Pick the origin

Use judgement. Often the origin is obvious or can be inferred: an earlier event
that day with a location he would drive from, or the request itself ("from
work"). His home and work addresses are in `/memory/profile.md`. When it
isn't clear, ask Louie rather than guess. In a scheduled run, where nobody may
be there to answer, skip the event and list it in the report with the question.

## 2. Decide on a buffer

**Default to no buffer.** Most events don't need one; a piano lesson or a lunch
date doesn't. Add one only when something calls for it: a flight, an event that
says "please arrive 15 minutes early", or Louie asking for one.

When there is a buffer, **it is always its own event. Never fold it into the
drive** by padding its length ("~31 min + buffer") or by ending the drive early
and leaving a gap. The day must stay contiguous:

| Event | Time |
|---|---|
| Drive from … to SFO | 1:00–1:45 PM |
| Airport Buffer | 1:45–3:45 PM |
| Flight to … (already on the calendar) | 3:45 PM – … |

- **Domestic flight:** 2 hours, titled `Airport Buffer`.
- **Anything else:** the time the event or Louie states.
- **No buffer:** there is no buffer event, and the drive ends at the event's
  start.

## 3. Compute the drive

```bash
commute drive --from "<origin>" --to "<event location, verbatim>" --arrive "<event start.dateTime>" \
  [--buffer <minutes> --buffer-title "Airport Buffer"]
```

Pass `--arrive` exactly as the event's `start.dateTime` gives it, offset
included. The script keeps its seconds, since the panel does.

With `--buffer`, the drive arrives at the event's start minus the buffer, and
the output adds a `buffer_event` that runs from there to the event's start.
Don't do that subtraction yourself.

It prints JSON. `create_event` holds the arguments for the Calendar connector's
`create_event`, ready to pass on as they are:

- **Title:** `Drive from {origin} to {destination}`. Each label defaults to the
  first comma-separated part of the location string, standing in for the
  panel's place name. Override it with `--from-label` / `--to-label` when that
  part isn't what Maps would show, such as a bare unit number. Home is labelled
  by its street line, as the panel does, unless Louie asks otherwise.
- **Description:** the route's summary, e.g. `US-101 N`.
- **Times:** local, no offset, as the connector wants. If the event's own time
  zone differs from Louie's calendar zone, also pass `timeZone` with the
  event's IANA zone.
- **Visibility:** private. No location, default reminders.

The rest of the output is for checking: what each location geocoded to
(`resolved`), the raw pessimistic seconds, and whether the iteration converged.
**Read `resolved` for both ends.** A location that geocodes to the wrong place
gives a confident wrong drive. If it looks wrong, or the lookup fails (a bare
store name often does), rerun with a fuller address or `lat,lng`, keeping the
event's own name with `--to-label`.

If the script stops because the drive would have to start in the past, there
is nothing to add.

## 4. Create it

Before creating anything, look at what is already on the calendar just before
the event:
- **A drive is already there, contiguous with the event** (directly, or
  through a buffer event): add nothing.
- **A drive or buffer is there but breaks the rule:** padded with buffer, a
  gap before the event, or a buffer missing. Show Louie the fix and replace it
  on his go-ahead, rather than adding a second drive beside it. Carry any
  notes in its description over to the new drive, after the route summary;
  deleting it must not lose them.
- Say so if the new drive would start before the previous event ends.

Then call `create_event` with the `create_event` arguments, and with
`buffer_event` if there is one, on the event's calendar. Tell Louie the drive's
length, leave time and route in one line.

## How the numbers are made

Treat this as fixed. It was reverse-engineered from the panel's own network
calls and client code, then checked head-to-head. Don't re-derive it or
"improve" it:

- **Pessimistic traffic.** The range's upper end is the Directions API with
  `traffic_model=pessimistic`. The panel's headline time is `best_guess`, and
  the event ignores it.
- **Arrive-by by iteration.** For driving, the API ignores traffic on an
  `arrival_time`, so the script iterates `departure = arrival − duration` to a
  fixed point. That agreed with the panel's own arrive-by in 21 of 23 cases.
- **Route.** It uses Maps' first route, the one Add to Calendar sits under.
- **Rounding.** It rounds the raw seconds up onto a grid that coarsens with
  length: 1 min under 10, 2 min under 30, 5 min under 90, else 10.
- **Coordinates, never names.** The script geocodes each location once and
  routes between coordinates. Passing names was the confound that made an
  earlier test look like a 25/54 failure.

The `lmcps` Maps tool can't do this, because `maps_directions` has no
`traffic_model`. So the script calls the API directly with the same key.
