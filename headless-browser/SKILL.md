---
name: headless-browser
description: "Fetches a page when web_fetch didn't -- through a browser that ordinary bot detection does not turn away. One script, ~18s, no second step. Use it whenever web_fetch returned nothing, a consent/paywall/'enable JavaScript' stub, or less than the page should hold: a thin result is the trigger, not just an outright error, and a fetch that silently drops JS-rendered content looks exactly like a successful one, so check what came back against the search snippet that led you there. Go straight here, skipping web_fetch, for SPAs, dashboards, JS-rendered tables, infinite scroll, and anything interactive -- expanding 'show more', clicking through flows, forms, pagination. If you are about to call a page inaccessible, or answer a page-specific question from snippets instead of the page, stop and run this: you wanted to read that page for a reason and the reason has not gone away. It is cheap; do not talk yourself out of it. Also logs in to the user's own accounts when claude-in-chrome is unavailable."
---

# Headless Browser (pinchtab)

There is nothing else to read here. The skill is one script:

```bash
bash /mnt/skills/*/headless-browser/setup.sh
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

The glob is deliberate: an uploaded skill lands under `/mnt/skills/user/` or
`/mnt/skills/plugins/` depending on how it was installed, and hardcoding either
one breaks on the other.

## Then

```bash
pinchtab nav <url> --block-images
pinchtab text
```

Every command that can change the page (`nav`, `click`, `fill`, `press`,
`scroll`, ...) also saves a screenshot and prints its path. When the text or
snapshot doesn't add up -- a canvas, a map, refs pointing at the wrong thing --
view that image rather than untangling the snapshot, and click what you see
with `click --x --y`: image pixels are click coordinates, no scaling.

The session outlives the bash call, so multi-step flows work across calls --
nav in one, click in the next, read state in a third. The tab, its DOM state
and typed form values all persist; you never replay earlier steps. If the
session goes stale the shim recreates it, but that gives you a fresh tab with
no page loaded, so re-`nav` after seeing `no_current_tab`.

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
you see them, and coordinates read off them land short of the target.

## Captchas

Nothing to call: `nav` solves a challenge on its own when it detects one, at
~$0.001 and 20-60s, and waits for it. It prints a HINT saying so, and says
nothing on the ordinary pages that never trigger one. A solve that outlasts the
call keeps running in the background, and the HINT says so and gives the
command to check on it. Run exactly that. Do not act on the page or navigate
while it runs: a new navigation loads a fresh challenge over the one being
solved.

**Never click a captcha widget.** Solving injects a token and does not tick the
box, so a solved page still shows an unchecked "I'm not a robot" and `snap`
still lists it. That is done, not pending -- clicking it discards a solve you
already paid for. Just proceed: fill the form, press submit, read the page.

A failed solve leaves the tab usable and says so in the HINT; `POST /solve`
adds a per-attempt `history` naming the solver that refused and why.

`nav` covers reCAPTCHA (v2, v3, Enterprise, invisible), Turnstile, hCaptcha
(also inside an embedded form's iframe),
Arkose FunCaptcha, GeeTest v4, MTCaptcha, AWS WAF, Tencent, NetEase Yidun,
Yandex SmartCaptcha, Prosopo and Lemin. Tencent and Yidun are solved once their
puzzle is showing: after the click that opens it, or once it is scrolled into
view. People solve hCaptcha, FunCaptcha and the 2Captcha-only vendors, so those
take one to three minutes: expect the pending HINT and run its check again
rather than giving up. A solve is paid for once, and acting on the solved page
does not buy another.

AliExpress's "Please drag the slider to verify" page is passed on `nav` too, at
no cost: it drags the slider, and a refused drag is retried on a fresh one up
to three times. From this sandbox it returns every 15-20 item pages, and each
`nav` that lands on it passes it again. Its other form, "We need to check if you
are a robot" over an item page, is a paid reCAPTCHA solve on `nav`. "Sorry,
there was a problem accessing the page" has no slider, and nothing gets past
it.

Puzzles without a sitekey (drag the piece into the gap, turn the image upright,
pick the matching tiles, read a flickering GIF) are never solved on `nav`.
Look at the screenshot, name the parts with selectors, and hand them over:

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

## Logged-in accounts

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
