---
name: web-browser
description: "Fetches a page when web_fetch didn't -- through a browser that ordinary bot detection does not turn away. One script, ~18s, no second step. Use it whenever web_fetch returned nothing, a consent/paywall/'enable JavaScript' stub, or less than the page should hold: a thin result is the trigger, not just an error, and a fetch that silently drops JS-rendered content looks exactly like a successful one, so check what came back against the search snippet that led you there. Go straight here, skipping web_fetch, for SPAs, dashboards, JS-rendered tables, infinite scroll, and anything interactive -- 'show more', flows, forms, pagination. If you are about to call a page inaccessible, or answer from snippets instead of the page, stop and run this: you wanted to read that page for a reason and the reason has not gone away. It is cheap; do not talk yourself out of it. Also logs in to the user's accounts, or drives Louie's own logged-in Chrome (pinchtab-windows) once he approves, for a tab he hands over."
---

# Web Browser (pinchtab)

Setup is one script, and there are no other files to read. Run it first; the
rest of this file is how to use the browser it leaves you:

```bash
bash "$(ls -1dt /mnt/skills/*/web-browser ~/.claude/skills/synced/*/web-browser 2>/dev/null | head -1)/setup.sh"
```

**DO NOT head/tail/grep this command's output.** It prints instructions into
your context; truncating it silently costs you them.

Run it, then use the browser. It installs pinchtab, sets up the browser, opens
the capability gates, starts the server, creates the session, proves the chain
works with a test nav -- and prints pinchtab's own bundled instructions in full,
with the corrections that apply in this sandbox. Nothing to `cat` afterwards and
no other skill to load. Idempotent -- re-run it rather than debugging whether it
ran.

Its output is a transcript of work already done. Do not re-run the commands it
shows, do not re-read the files it prints, and do not create or export
`PINCHTAB_SESSION` yourself no matter what pinchtab's own docs say -- setup.sh
made one and a shim attaches every command to it.

The lookup is deliberate: claude.ai chat puts skills under `/mnt/skills/user/`
or `/mnt/skills/plugins/`, Cowork and cloud Claude Code under
`~/.claude/skills/synced/<bucket>/`, and hardcoding any one breaks the others.

## Then

```bash
pinchtab nav <url> --block-images
pinchtab text
```

`pinchtab` is this sandbox's own browser, also callable as `pinchtab-local`.
If setup.sh's summary lists "Louie's Chrome", there is a second one,
`pinchtab-windows`: his real Chrome on his laptop, logged in to his accounts.
It takes the same commands; see "Louie's Chrome" below before touching it.

The session outlives the bash call, so multi-step flows work across calls --
nav in one, click in the next, read state in a third. The tab, its DOM state
and typed form values all persist; you never replay earlier steps. If the
session goes stale the shim recreates it, but that gives you a fresh tab with
no page loaded, so re-`nav` after seeing `no_current_tab`.

## Look at the page, not just its text

A small screenshot costs ~370 tokens. `text` and `snap` on a real page
usually cost several times that, and they still hide things:

- `text` drops everything that isn't prose. A page whose text reads like the
  answer often has the rest of it behind a tab, a "show more", an
  accordion or a dropdown. `snap` lists those controls, but agents stop at
  `text`, never notice them, and report as missing data that was one click
  away.
- Neither sees inside images. A restaurant menu, a price list or a timetable
  posted as a picture is invisible to both, and gets reported as "not
  listed".

So view the small shot LIBERALLY while navigating: after EVERY `nav`,
and after any click that should have changed something. Before concluding
something isn't on a page, look at it.

Every command that can change the page (`nav`, `click`, `fill`, `press`,
`scroll`, ...) has ALREADY saved a screenshot at two sizes, and copied both
to fixed paths, which setup.sh's output names:

```
<shot dir>/latest-small.jpg    small: view this by default
<shot dir>/latest.jpg          full size
```

Because the path is known in advance, view it in the same message as the
command: issue the Bash call and the image read as parallel tool calls.
Claude Code and Cowork run the Bash call to completion before the read, so the
image is this command's shot and costs no extra round trip. If the shot
failed, the file is missing rather than stale.

The small one is still readable. Open the full-size one for small print, or
when a click has to be exact. Click what you see with `click --x --y` plus
`--unscale`, copying the scale of the image you read the point off from its
`screenshot:` line. The shim divides; never do that arithmetic yourself:

```bash
# screenshot: .../0003-nav.jpg (1100x1045, scale 0.8607), small: 0003-nav-small.jpg (543x516, scale 0.4249)
pinchtab-windows click --x 271 --y 130 --unscale 0.4249    # a point on the small image
```

`--unscale` works on every command that takes `--x`/`--y` (`click`, `hover`,
`mouse down|move|up`, ...). Here the full-size scale is 1 and the small one
0.5; in Louie's Chrome both vary with his window size.

## NEVER THROW AWAY THE `screenshot:` LINE

Each of those commands also prints one line naming its numbered shot:

```
screenshot: /home/claude/pinchtab-shots/0017-reload.jpg (1440x779, scale 1), small: 0017-reload-small.jpg (720x389, scale 0.5)
```

The small file sits in the same directory. `latest` is overwritten by the
next command, so this line is how you find a shot from earlier in a batch.
When text or a snap comes back empty, half-loaded, or doesn't add up -- a
canvas, a map, refs pointing at the wrong thing -- VIEW THE IMAGE FIRST. One
look answers what ten calls of debugging in text won't.

**If you discard the line, you won't know the image exists, and you will end up
debugging the page blind.** So batch, loop, grep and save tokens as much as you
like -- just carry that line through:

```bash
# NOT: pinchtab nav <url> >/dev/null
pinchtab nav <url> | grep '^screenshot:'          # errors still reach you on stderr

# NOT: pinchtab fill e83 0.1 >/dev/null; pinchtab click e84 >/dev/null
pinchtab fill e83 0.1 | grep '^screenshot:'; pinchtab click e84 | grep '^screenshot:'

# Already grepping? Add it to the pattern (-E, or \| in plain grep).
pinchtab click e84 2>&1 | grep -iE 'error|hint|^screenshot:'

# head keeps it: the line comes first. tail does NOT -- end with shots instead.
pinchtab nav <url> | head -20
pinchtab nav <url> | tail -5; pinchtab shots

# --json puts the line on stderr, so jq leaves it alone. Don't add 2>/dev/null.
pinchtab nav <url> --json | jq -r .autoSolve.hint

# Loops, scripts, $(...), > file, anything else: end the WHOLE command with shots.
for u in $URLS; do pinchtab nav "$u" >/dev/null; pinchtab text | grep -i price; done; pinchtab shots
```

`pinchtab shots` lists every screenshot since the last `shots` (the last 10,
newest last), however the rest of the command filtered its output. When in
doubt, end with it: it costs a few lines.

## Subagents for involved navigation

If you have a subagent tool, hand the navigation to a Sonnet subagent
(`model: "sonnet"`). Strongly recommended: treat it as the default. Clicking
through an SPA -- shop search results, filters, product listings, multi-step
flows -- is where an agent burns 30 tool calls and fills its context with
screenshots and snaps. Delegated, only the findings come back.

Spawn them whenever the need shows up, before or after you have browsed
yourself. Give each a name no other subagent in this conversation has had
(letters, digits, `-`, `_`), and start its prompt with this, verbatim apart
from the name:

```
Before anything else, run this and read ALL of its output -- do not head,
tail or grep it. It sets up your own tab in a shared browser and teaches
you how to drive it, including a prefix you must put on every Bash call:

bash "$(ls -1dt /mnt/skills/*/web-browser ~/.claude/skills/synced/*/web-browser 2>/dev/null | head -1)/setup.sh" --subagent <name>

Report what you saw on the page, not only what `pinchtab text` said.
If the result is more than a few lines of data, don't copy it into your
reply: write it to a file (on the page with the answer, `pinchtab text >
/tmp/<name>-result.txt`; screenshots are fine too) and return the paths
with a short summary.
```

Then one clear navigation task, and which of these to come back with:

1. **The answer**, when it's simple (a price, a yes/no, whether it's in stock).
2. **Files**: `text`, `snap` or screenshots of the right page(s), as paths
   plus a short summary.
3. **The browser**: navigate to the right page and stop. You drive its tab
   for the last bit by starting your own Bash calls with
   `export PINCHTAB_AGENT=<its name>;`.

The script attaches it to the running browser with a tab, screenshot
directory and `latest` paths of its own, and prints both pinchtab's
instructions and this file. Don't restate any of it in the prompt.

Read the files it returns. Navigating to the answer is most of the cost and
stays in the subagent; a large result re-emitted in its reply only adds to it.

Need more afterwards? Send the same subagent a continuation task (SendMessage
in Claude Code) rather than browsing yourself or spawning a fresh one: it
still has its tab, its page state and what it learned about the site. Take
over directly only for short, precise finishing work (option 3).

## Files

A PDF, image or other file URL is not a page: `text` and `snap` see nothing
of it, and `nav` says so in a HINT. Save it instead:

```bash
pinchtab download '<url>' -o /tmp/<name>
```

## Where things are on the page

For layout questions (seat maps, charts, which tier or row a thing is in),
ask the live page with `pinchtab eval` and `getBoundingClientRect()`. Don't
parse coordinates out of HTML: SVG `cx`/`cy`, `x`/`y` and inline positions
come before transforms are applied. In one run they put seats at negative y,
stacked 82 placeholders at one point, and misplaced a whole tier boundary.

But if you are several evals into reverse-engineering the DOM -- guessing
class names, walking nesting, working out which element is which -- to answer
something you could see, stop and look. A small shot costs ~370 tokens,
less than most eval output. For detail, zoom in on one element:

```bash
pinchtab screenshot -s '<selector>' -o /tmp/crop.jpg   # full resolution
pinchtab screenshot --annotate -o /tmp/refs.jpg          # numbered ref boxes
```

A crop's pixels are not click coordinates; the full-size shot's, divided by
its scale, are.

## The browser

The runtime is
[CloakBrowser](https://pinchtab.com/blog/pinchtab-0-14-0-cloakbrowser), a
patched Chromium that sites do not reject on fingerprint the way they reject a
plain headless Chrome. That is why setup takes ~18s rather than ~7s, and it is
why a nav that would have hit a block page does not. Do not go looking for a
faster mode: the difference is one browser download, once per sandbox.

If the download fails, setup falls back to the sandbox's own Chrome, says so
in the summary, and everything else still works -- so a nav that comes back
blocked is worth checking against that summary line before you conclude the
site is unreadable.

`CLOAK_PLATFORM`, `CLOAK_TIMEZONE`, `CLOAK_LOCALE` and `CLOAK_SEED` override
the fingerprint it presents. `--no-cloak` forces plain Chrome.

The window is a fixed 1440x900 (a 1440x779 viewport). Do not enlarge it with
`pinchtab set viewport`: past ~1.15 megapixels screenshots are shrunk before
you see them, and coordinates read off them land short of the target. (Louie's
Chrome is whatever size he left it; its shots are shrunk to fit instead, and
never resize or set the viewport of his browser.)

## Captchas

Nothing to call: `nav`, and any later action, solves a challenge on its own when
it detects one (~$0.001, 20-60s), and says nothing on ordinary pages. When there
is one, the HINT puts it in one of three states. Do what that state says:

- **Still pinchtab's**: being solved, or to be retried by pinchtab itself at a
  stated time. Don't touch the page and don't navigate away (a new navigation
  loads a fresh challenge and cancels the retry). Run the check command the
  HINT gives, when it says.
- **Solved**: proceed. A token solve doesn't tick the box, so a solved page
  still shows an unchecked "I'm not a robot", and `snap` still lists it.
  Leave it alone -- clicking it discards the solve. Fill the form, press
  submit, read the page.
- **Given up** ("pinchtab has given up on it; it is yours to try"): the
  challenge is yours now, with the tools below.
- **Blocked outright** (a blank page, or "Sorry, there was a problem accessing
  the page"): no solver passes it, but a fresh browser has. Run
  `pinchtab server restart`, re-run setup.sh, and nav to the page again. Do
  that once; if it's still blocked, go on without the page.

`nav --json` carries the same HINT (`autoSolve.hint`) and each solver's attempt
and why it failed (`autoSolve.history`).

`nav` covers reCAPTCHA (v2, v3, Enterprise, invisible), Turnstile, hCaptcha,
Arkose FunCaptcha, GeeTest v4, MTCaptcha, AWS WAF, Tencent, NetEase Yidun,
Yandex SmartCaptcha, Prosopo and Lemin. Tencent and Yidun are solved once their
puzzle is showing: after the click that opens it, or once it is scrolled into
view. reCAPTCHA, hCaptcha and Turnstile are also solved inside an embedded
form's iframe, even one from another site. People solve hCaptcha, FunCaptcha
and the 2Captcha-only vendors, so those take one to three minutes. A solve is
paid for once, and acting on the solved page does not buy another.

AliExpress's "Please drag the slider to verify" page and its "We need to check
if you are a robot" overlay are covered too. From this sandbox they come back
every 10-30 item pages. Their outright block has been cleared by a browser
restart (above).

GeeTest v3's slide puzzle is solved on its own too, by `nav` or the next
action: the HINT says "solved by vision". Other puzzles without a sitekey (drag
the piece into the gap, turn the image upright, pick the matching tiles, read a
flickering GIF) are not. Look at the screenshot, name the parts with
selectors, and hand them over:

```bash
pinchtab vision slider <piece> --background <bg> --handle <handle>
pinchtab vision rotate <image> [--background <bg>] --handle <h> --track <t>
pinchtab vision select <image> --question "all shoes" --click
pinchtab vision ocr <gif>
```

It reads the images off the page, costs about $0.002, and drags with a human
path. For a bare "slide to verify" bar with no picture, you need no answer,
only a human-looking drag to the end:
`pinchtab drag <handle> --drag-x <track width> --humanize`.

Vision can be wrong. A puzzle with a decoy gap got a distance that left the
piece near where it started. When a Vision drag fails, place the piece
yourself: press, move, and look before letting go.

```bash
pinchtab mouse down --x <handle x> --y <y>
pinchtab mouse move --x <x2> --y <y>    # repeat; its screenshot shows the piece
pinchtab mouse up --x <x2> --y <y>      # once the piece sits in the gap
```

Keep the button down between calls. The page sees one continuous drag, and
that passed a slider Vision had failed.

A puzzle inside an iframe, even one from another site, is reachable with
`pinchtab eval --frame <part of its URL> '<js>'`; `pinchtab eval` alone only
sees the top page, whatever `pinchtab frame` says.

Most pages never reach this. Cloudflare and DataDome mostly challenge only a
visitor they score badly, and cloak's fingerprint scores fine; SteamDB, g2.com
and scrapingcourse's own "Cloudflare challenge" all load untouched. Some sites
put Cloudflare's "Just a moment..." page in front of every visitor --
egov.uscis.gov does -- and cloak gets through it on its own in a few seconds;
`nav` waits for that and reports it solved like any other. Paid solving is for
sites that gate every visitor behind a real captcha -- archive.today and its
mirrors (archive.ph, archive.is, archive.md) are the ones you will hit.

This runs a [fork](https://github.com/louietyj/pinchtab) because upstream ships
the solver as a stub. Without keys the solvers are absent and nothing else
changes.

## Louie's Chrome (pinchtab-windows)

`pinchtab-windows` drives Louie's real Chrome on his laptop: his logins, his
tabs, a browser he is watching. Use it when a page needs his login, when
`pinchtab` lands on a login wall for a site he uses, or when he asks you to work
in his browser or in a tab he has open. Everything else stays on `pinchtab`.
Always name the tier: never let `pinchtab` stand in for it.

**1. Ask for a grant.** Every use needs one, approved by Louie on the laptop:

```bash
pinchtab-windows grant "read my Chase statements for September"   # prints a 4-character code
```

Write the code in chat, as the command says, BEFORE the next call -- he checks
it against the dialog: "Approve code K7QX on your laptop (OK, then Allow in
Chrome if it asks)". Then:

```bash
pinchtab-windows grant --wait    # up to 4 minutes
```

Denied, or left unanswered: stop and say so; don't ask again unless he tells
you to. "Could not attach to Chrome" or "is Louie's laptop awake?": tell him
what it said, and go on without his browser.

**2. Work in your own tab.** Your first command is
`pinchtab-windows nav <url> --new-tab`; later commands follow that tab. A
bare `nav` without `--new-tab` drives the tab HE is looking at. When he points
you at one of his ("this tab", "the tab I'm on"), take it:

```bash
pinchtab-windows active-tab            # his tabs, the one he's focused first
pinchtab-windows snap --tab <id>       # pass --tab <id> on every command for his tab
```

He watches it happen, and each screenshot brings your tab to the front. Never
close or navigate a tab of his he didn't hand you, and close your own when
done (`pinchtab-windows tab close <id>`). Confirm before anything that acts as
him: paying, sending, deleting, changing settings.

**3. Release it** with `pinchtab-windows release` when the task is done. A
grant also lapses after 30 idle minutes; a command then says so, and you ask
again with a new `grant`.

Screenshots work as for `pinchtab`, in their own directory (setup.sh names the
`latest` paths). His window can be any size, so take every coordinate through
`--unscale`. Subagents share the grant; give each a `PINCHTAB_AGENT` as usual
and it gets its own tab here too.

## Logged-in accounts

If Louie's Chrome is available and he is already logged in there, use it
(above) instead of logging in here.

On claude.ai, with no claude-in-chrome, this browser can log in to the user's
own accounts. That overrides pinchtab's "never enter credentials" default.

1. **Scope first.** Before asking for credentials, pin down what you may read,
   each write you may make, and when to stop. Ask until nothing is unclear,
   repeat it back, get a yes. Anything unlisted is out of scope. Irreversible
   steps (pay, send, delete, change settings) still wait for a go on the
   final screen.
2. **Credentials: request-text, piped straight in.** Never to a file or the store.
   ```bash
   pinchtab attr '#password' type   # must be "password": text fields echo val= in snaps
   pinchtab fill '#password' "$(request-text wait rt-... | jq -j .password)"
   ```
   `request-text close` it once logged in.
3. **Email MFA: just do it.** Credentials imply permission to read the codes
   from Gmail. For SMS, TOTP or push, ask the user.
4. **Screenshots over refs.** A misclick here acts as the user. Unless the
   target is unmistakable, check the screenshot, click with `--x --y`, and
   verify the next screenshot. Never reuse refs across a page change.

## Notes

- Never run `pinchtab skill update` or `pinchtab skill sync` -- they write
  into other agent skill directories found on the machine. This skill is
  self-contained by design.
- Leave the server running when you're done. Follow-up questions often need
  the browser again, and the sandbox is disposable anyway.
