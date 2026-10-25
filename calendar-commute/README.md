# calendar-commute

Adds a drive event before one calendar event, reproducing what Google
Calendar's Maps panel writes when you click the event's location, enter a start
point and hit Add to Calendar. The length, start time, title and description
all match. Claude picks the origin and creates the event through the Google
Calendar connector; `commute` does the routing and rounding. Choosing which
events need a drive belongs to the caller, such as a scheduled task.

```
SKILL.md                  the workflow Claude follows; judgement calls stay here
setup.sh                  puts `commute` on PATH, finding the skill in either layout
bin/commute.py            geocode, pessimistic arrive-by, grid rounding (stdlib only)
test_commute.py           offline tests, no network
package.py                builds calendar-commute.zip for upload
```

It depends on `local-mcps` for the key in its `mcp-google-maps` config, and on
the Google Calendar connector. It holds no credentials, so the zip is safe to
share.

## How the panel works

These findings were reverse-engineered on 2026-10-16 from the panel's network
traffic and client JavaScript, then checked head-to-head against its own
request.

- **The panel is an iframe.** It loads `www.google.com/maps/companion/` and
  fetches directions with one batchexecute RPC, `NBA0yb`. The request carries
  `[type, null, epoch]`, where type 1 is arrive-by and 0 is depart-at.
- **Per route, the response holds one summary block:**

  ```
  [[1320,"22 min"], null, 2, [975,"16 min"], [960, 2100, "16–35 min"], [1320], null, null,
   [1792261500, "America/Los_Angeles", "11:25 AM", -25200, 1792261500]]
  ```

  That is the typical time, the time without traffic, the range, and the leave
  time. The leave time is the arrival minus the range's upper end. That held in
  191 of 191 rows; for trips under about 10 minutes the server sends no leave
  time at all.
- **The server does all the rounding.** Every displayed duration is rounded up
  onto a grid: 1 min under 10, 2 min under 30, 5 min under 90, 10 min above.
  The displayed values seen were 5–9, 10–28 even, 30–85 in fives, then tens.
- **The client builds the event in one function** (`cXa` in the `m=directions`
  module):
  - Length is the range's upper end if there is one, else the typical time,
    else the no-traffic time.
  - Arrive-by: start at the leave time if the server sent one, else at arrival
    minus the length. Either way the event ends at the arrival.
  - Title: `Drive from {first stop} to {last stop}`, or `Walk from`, `Bike
    from`, `Take transit from`, with `Travel from` as the fallback.
  - Stop label: the place name, else the typed text, else `Your location`,
    else lat,lng.
  - Description: the route summary.
  - Also sets visibility private and a source of `Google Maps directions`, then
    posts the event straight to the primary calendar.

`NBA0yb` needs the logged-in browser session and its XSRF token, so a scheduled
job can't call it. `commute` gets the same numbers from the public Directions
API instead.

## Design

- **Upper end = pessimistic.** The range's upper end matches the Directions API
  with `traffic_model=pessimistic`, rounded up on the grid. The headline time
  matches `best_guess` the same way.
- **Arrive-by through a fixed point.** For driving, the API ignores traffic on
  an `arrival_time`, so the script iterates `departure = arrival − duration`.
  It converges in two to three calls, and agreed with the panel's own arrive-by
  in 21 of 23 cases (the other two were one step apart).
- **Ceil on raw seconds.** Rounding to a whole minute first scored 20/24, and
  flooring scored 14/24. Ceiling the raw seconds scored 23/24.
- **Coordinates, never names.** The first head-to-head scored 25/54, but the
  test was flawed. The panel's request was given a place name with the
  coordinates, and Google geocoded the name 1–2 km away. With the same
  `lat,lng` on both sides it scored 23/24 for the upper end and 24/24 for the
  typical time, and in depart-at mode at the same instant 23/23 and 22/23. So
  the script geocodes each location once and routes between coordinates.
- **The one miss is a boundary.** The API's pessimistic value was 3,001 s,
  which rounds up to 55, while the panel showed 50. It is pinned in the tests
  so that a "fix" has to account for the 23 it would break.
- **The route is Maps' first.** The first call picks it, and later iterations
  follow it by summary and distance (±1.5%) even if the API reorders its
  alternatives.
- **Labels are approximate.** The panel uses Maps' place header ("Example
  Dental", "100 Main St Suite 2"), which the public geocoder doesn't return
  byte for byte (it gives "Ste 2", for example). The first comma-separated part
  of the calendar location is the closest stand-in, and SKILL.md says when to
  override it.
- **Drives lead to events.** No drive home or away from an event unless a next
  event is waiting at the other end.
- **No buffer by default; when there is one, it's a separate event.** Only
  something that calls for it (a flight, "arrive 15 minutes early") gets one,
  and it sits between the drive and the event so the three are contiguous.
  Padding the drive hides the buffer and breaks its match with Maps; a gap
  leaves the time unaccounted for. `--buffer` computes both events.
- **Judgement stays in SKILL.md.** Picking the origin (infer it, or ask),
  sizing a buffer and checking what is already on the calendar live there.
  The script only does arithmetic.
- **One event, driving only.** Scanning a calendar for events that need a drive
  is left to the caller. Walking, cycling and transit aren't modelled; a
  transit event isn't rounded and uses the schedule's times instead.

## Testing

```
python test_commute.py    # offline, no network
```

For a live check, `commute drive` needs the Maps key. Set `COMMUTE_MAPS_KEY`, or
`LMCPS_HOME` pointing at a directory with local-mcps' `config.json`.
`dev/sandbox.sh` covers both skill layouts.
