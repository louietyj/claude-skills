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
the capability gates, starts the server, proves the chain works with a test
nav -- and prints pinchtab's own instructions in full, written for this setup.
Nothing to `cat` afterwards and no other skill to load. Idempotent -- re-run it
rather than debugging whether it ran.

Its output is a transcript of work already done. Do not re-run the commands it
shows, do not re-read what it prints, and never create or export
`PINCHTAB_SESSION`: pinchtab keeps one session per agent across bash calls.

The lookup is deliberate: claude.ai chat puts skills under `/mnt/skills/user/`
or `/mnt/skills/plugins/`, Cowork and cloud Claude Code under
`~/.claude/skills/synced/<bucket>/`, and hardcoding any one breaks the others.

## Then

```bash
pinchtab execute-batch <<'EOF'
nav <url> --block-images
EOF
```

and view `latest-small.jpg` in the same message. Every browser step is a line
of a batch, as pinchtab's instructions describe.

`pinchtab` is this sandbox's own browser, also callable as `pinchtab-local`.
If setup.sh's summary lists "Louie's Chrome", there is a second one,
`pinchtab-windows`: his real Chrome on his laptop, logged in to his accounts.
If it lists "cloud browser", there is `pinchtab-cloud`: a hosted browser on a
residential IP, for sites that ban this sandbox. All take the same batches,
with their own screenshot directories; see their sections below before
touching either.

The tab outlives the bash call: its page, DOM state and typed form values
persist, so you never replay earlier steps. If the session goes stale pinchtab
makes a new one, but that is a fresh tab with no page loaded, so `nav` again
after `no_current_tab`.

## Subagents for involved navigation

If you have a subagent tool, hand the navigation to a subagent. This is the
default, not an option -- above all when you are Opus. A single page fetch you
can run yourself; clicking through a site -- shop search results, filters,
product listings, booking or checkout flows -- is where an agent burns 30 tool
calls and fills its context with screenshots and snaps. Delegated, only the
findings come back, and you keep the last mile.

Pick the model by whether a failure would be loud or silent:

- **Haiku at xhigh effort** (`model: "haiku"`, `effort: "xhigh"`) when you can
  check "done" from what it returns: a page state it reached, saved page text
  or screenshots, or the browser handed back. If it falls short, you can see.
- **Sonnet** (`model: "sonnet"`) when the result is a judgment or a claim to
  have covered everything -- cheapest, all of them, the official one -- or
  the flow is long and full of form state, or it submits or buys anything.
  There, an agent that gave up early returns a partial answer that looks
  complete.

Where you can, rewrite the brief so the task fits Haiku: "find the cheapest
SFO-ANC flight in November" invites a silently partial answer; "load the
results for these four dates and save each page's text" is checkable, and the
comparison stays with you. Name the stopping point as a page state, not a goal.

Never spawn Haiku without `effort`: at its default, medium, it gives up at the
first failed click and drops rules from long instructions. If it comes back
blocked or partial, give the task to Sonnet along with what Haiku tried
rather than retrying Haiku.

The most common case: "Find xyz's official booking site, go through its
booking flow, and stop at the pricing page." Picking the official site is a
judgment and the flow is full of form state, so it goes to Sonnet. The pricing
page is the part you care about, so it comes back to you for the last mile.

Spawn them whenever the need shows up, before or after you have browsed
yourself. Give each a name no other subagent in this conversation has had
(letters, digits, `-`, `_`), and start its prompt with this, verbatim apart
from the name:

```
Before anything else, run this and read ALL of its output -- do not head,
tail or grep it. It sets up your own tab in a shared browser and teaches
you how to drive it, including a prefix you must put on every Bash call:

bash "$(ls -1dt /mnt/skills/*/web-browser ~/.claude/skills/synced/*/web-browser 2>/dev/null | head -1)/setup.sh" --subagent <name>

Report what you saw on the page, not only what `text` said.
If the result is more than a few lines of data, don't copy it into your
reply: write it to a file (on the page with the answer, the batch step
`text > /tmp/<name>-result.txt`; screenshots are fine too) and return the
paths with a short summary.
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

**2. Work in your own tab.** Your first step is `nav <url> --new-tab`; later
steps follow that tab. A bare `nav` without `--new-tab` drives the tab HE is
looking at. When he points you at one of his ("this tab", "the tab I'm on"),
take it:

```bash
pinchtab-windows active-tab            # his tabs, the one he's focused first
pinchtab-windows execute-batch <<'EOF'
snap --tab <id>
EOF
```

and pass `--tab <id>` on every step for his tab.

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

## Cloud browser (pinchtab-cloud)

This sandbox browses from a datacenter IP, and some sites ban those outright
whatever the fingerprint: Yelp (DataDome) and anything behind Akamai's "Access
Denied". `pinchtab-cloud` is a Browser Use cloud browser on a residential IP,
driven like `pinchtab`. It costs money ($5 per GB, $0.02 an hour), so use it
only after `pinchtab` came back blocked, never as the default.

```bash
# Starts one if none is running (US IP).
pinchtab-cloud execute-batch <<'EOF'
nav <url> --block-images
text
EOF
```

- **Don't stop it.** Not at the end of a task, not at the end of your turn:
  the user's follow-up wants the same tabs. It ends by itself an hour after it
  started, and idling costs next to nothing (about 2 cents an hour). A command
  after that starts a fresh one, and says so: nav again. `pinchtab-cloud stop`
  is only for when you are certain nothing follows, such as the last step of
  an unattended scheduled task.
- **Keep `--block-images` on.** Traffic is the cost: a page with images is
  several MB, a few cents each.
- `pinchtab-cloud start --country gb` for another country.
- **A first look may catch the bot check**: "Verifying the device..." on
  Yelp passes by itself in a few seconds. Look again before calling it blocked.
- Captchas are solved by pinchtab as usual, and screenshots, `--unscale` and
  subagents (`PINCHTAB_AGENT`, own tab with `nav --new-tab`) work as for
  `pinchtab`. There is one cloud browser per sandbox.

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
2. **Credentials: request-credential, the whole login in one `run`.** It gets
   the login from Louie's password manager and hands it to the script as
   `$CRED_USERNAME`/`$CRED_PASSWORD`, redacts the script's output, then deletes
   the login. pinchtab keeps it on the same tab, and the cookies outlive the
   run.
   ```bash
   request-credential run rt-... -- sh -c '
     # Screenshots cannot be redacted, and text fields echo val= in snaps.
     # The batch expands $CRED_* itself; its status lines show each step as written.
     pinchtab execute-batch --snap <<"EOF"
   expect "#password[type=password]"
   fill "#email" "$CRED_USERNAME"
   fill "#password" "$CRED_PASSWORD"
   click "#sign-in"
   EOF
   '
   ```
   Before asking, find each step's selectors without the password (snap,
   `eval` over the inputs' names and types): many logins put the password on
   a second page after the username. Check the login worked inside the same
   `run`: a snap in a later call is not redacted. A subagent exports
   `PINCHTAB_AGENT` before `run`; it is passed on.
   Only for a login his password manager does not have, fall back to
   request-text: `PW=$(request-text wait rt-... | jq -j .password)`, then a
   batch with the same `expect` and `fill "#password" "$PW"` (export it, since
   the batch reads the environment), then `request-text close`. Never to a file
   or the store.
3. **Email and SMS MFA: just do it.** Credentials imply permission to read
   the codes from Gmail, or from his text messages through mcp-android. For
   TOTP or push, ask the user.
4. **Screenshots over refs.** A misclick here acts as the user. Unless the
   target is unmistakable, check the screenshot, click with `--x --y`, and
   verify the next screenshot. Never reuse refs across a page change.

## Notes

- The browser, profile and security gates are settled by setup.sh. Do not
  reconfigure `browser.binary`, `browsers.default` or the security gates
  (`trustLoopbackProxy` included: it is set to match the egress proxy here).
- Leave the server running when you're done. Follow-up questions often need
  the browser again, and the sandbox is disposable anyway.
