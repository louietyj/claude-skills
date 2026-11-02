# archive-today troubleshooting

Read only the section the failure named. Fix the script, `bin/archive_today.py`, rather
than driving archive.today by hand: the fix belongs in
[louietyj/claude-skills](https://github.com/louietyj/claude-skills) under `archive-today/`.
Tell Louie what broke and what you changed.

## How it works

1. `GET https://archive.ph/newest/<url>`. A 302 goes to the snapshot, a 404 means none.
   A **429 with a reCAPTCHA** is the "One more step" page.
2. The captcha page is explicit reCAPTCHA v2. Its sitekey sits in the inline JS as
   `'sitekey': '...'`. The callback POSTs to `/cdn-cgi/l/chk_captcha` with:
   - `response=<token>`
   - `location=<page url>`
   - `xden=<constant, A50 as of 2026-11>`
   - `fp=<JSON>`

   `fp` is a ThumbmarkJS fingerprint. When that script fails to load, the page sends
   `{"error":true,"stage":"resource",...}` instead, and the server accepts it. The
   script sends that fallback.
3. The token comes from CapSolver (`ReCaptchaV2TaskProxyLess`), or 2Captcha
   (`RecaptchaV2TaskProxyless`) if CapSolver fails. Then the POST. A 200 response sets
   `cf_clearance`, which is kept in `~/.cache/archive-today/cookies.txt`.
4. Submitting is `GET /submit/?url=<urlencoded>`. It answers 200 with
   `Refresh: 0;url=https://archive.ph/wip/<id>`. Poll `/wip/<id>`: it answers 200 while
   capturing, then 302 to `/<id>` when done.
   - If the URL was archived recently, it answers 302 to `/<id>/again?url=...` instead.
     That is the "save it again?" page for the existing snapshot. Its save button
     resubmits with `anyway=1`, which is `--fresh`.
   - archive.today normalizes the URL before matching (`?foo=1` on an NYT URL still hit
     the existing snapshot).
5. Text comes from trafilatura on the snapshot HTML.

The same User-Agent has to be used throughout. The clearance embeds the solving IP.
The sandbox's egress IP changes per connection across a /24, and clearances have died
within ~10 minutes, so a dead clearance is normal: the script just solves again.

## connection

Every retry of one request failed. archive.today resets roughly 40% of TLS handshakes
from the sandbox's egress (2026-11-02: archive.ph/.li/.vn failed 3-4 of 8, while other
hosts through the same proxy failed 0 of 8), so `request()` retries 8 times with backoff.
- Run it again once. A streak of 8 failures does happen.
- Check that the host answers at all: `curl -sS -o /dev/null -w '%{http_code}\n' https://archive.ph/`.
  A 429 is fine (that's the captcha). A curl exit 35 or 56 on every try means the
  host is unreachable from here.
- In cloud Claude Code, `curl -sS "$HTTPS_PROXY/__agentproxy/status"` lists recent relay
  failures by host.
- If archive.ph alone is dead, try another mirror: change `HOST` to `https://archive.li`
  or `https://archive.vn`. Clearances are per domain, so it solves again.

## captcha-page-changed

The 429 page no longer has something step 2 relies on. The page is saved at
`~/.cache/archive-today/captcha-page.html`; the error names what's missing.
- Find the callback that POSTs the token (search for `xhr.open`, `fetch(` or `chk_`).
  Read which fields it sends and where.
- New sitekey location, `xden` value or field name: update the regexes in
  `clear_captcha()`.
- A different captcha vendor (hCaptcha, Turnstile): the solver task type changes.
  CapSolver no longer does hCaptcha, but 2Captcha does (`HCaptchaTaskProxyless`).
- If the server stops accepting the fallback `fp`, look at what ThumbmarkJS would
  produce. That's a much bigger job: tell Louie before starting it.

## captcha-rejected

`chk_captcha` didn't answer 200, or the captcha came back straight after a 200.
- Once may be a bad token: rerun.
- Every time: the server now checks something the script doesn't send. Compare against
  a real browser solve with the web-browser skill (pinchtab: `nav` to the page, then
  `solve`). Look at that request's body and headers, and at the cookies it carries
  before and after.

## solver

Every solver failed; the error carries each one's reason.
- `ERROR_KEY_DOES_NOT_EXIST` / `ERROR_ZERO_BALANCE`: a key or balance problem. Tell
  Louie; he funds and rotates the keys, which live in the repo's `keys/`.
- `no key`: the zip was built without that key file.
- Timeouts: the provider is slow, so rerun. If one provider keeps failing, the other is
  still tried.

## unexpected-response

`/newest/`, `/submit/` or `/wip/` answered with a status or shape not seen before.
The error carries the status and the start of the body.
- A redirect to a different archive.today domain: update `HOST`.
- `/submit/` with no `Refresh` header and no "Loading" page: look for a form or token.
  Until 2026 the homepage form carried a hidden `submitid`, and that may be back.
- A 200 "Loading" page with no wip id means a capture of that URL is already in flight.
  The script handles that by polling `/newest/`.

## Bad captures

Not a script failure: the snapshot exists but holds the wrong page. In the 2026-11-02
test, 3 of 10 hard-paywalled snapshots were bad: an FT "Security Verification" page,
an Economist `ERR_PROXY_CONNECTION_FAILED`, and The Information's stub. A later
snapshot of the same Information article was complete. See SKILL.md for what to do.
