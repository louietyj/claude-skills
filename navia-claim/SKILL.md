---
name: navia-claim
description: "File Louie's Navia Health Care FSA reimbursement claims from receipts he sends (photos, screenshots, PDFs): agree every claim field with him up front, fill Navia's own claim form in a browser on a residential IP, verify three mandatory checkpoints against the agreed fields before Send, and read the claim back afterwards. Ships js/navia.js, read-only page helpers: duplicate check, portal-unchanged check, form-state check and read-back. Use whenever he sends a receipt or bill to claim from his FSA, says 'submit this to Navia', or asks what's on his FSA statement."
---

# navia-claim

You drive the browser; this skill gives you the route, the state to expect at
each step, and read-only page helpers. The claim always goes through Navia's
own form, never API writes: the checkpoint screens only exist there.

This is a skill, not a script. **Hard rules** are marked and followed exactly.
**Use judgment** items describe the outcome and format Louie wants, not a
formula: decide from the receipt, what he said and what you can look up, and
ask when it isn't clear. He approves every field, so propose with your
reasoning rather than apply a rule wrongly.

**Hard rule: never click "send claim to Navia" until checkpoints 1, 2 and 3
(section 5) have each passed: a full-size screenshot and `navia.formState`
both matching every agreed field, written out.** No exceptions for short
claims, retries or "I just looked at it".

## 0. Agree the fields with Louie

His conventions (2026-11-14):

- **One receipt, one claim, one item.** A receipt covering different services
  (Rx and over-the-counter) becomes a **separate claim per service**, each with
  only its own lines. Travel is always its own claim (0b).
- **Date (use judgment):** start and end are almost always the same day, the
  day on the receipt, even if he received it later. Take the most
  defensible-looking date: a big, bold printed date beats a small dispense date
  elsewhere. Tiebreak: dispense > order > delivery date.
- **For whom:** Louie Yi Jie Tan.
- **Provider:** the basic name: `Walmart Pharmacy`, `Grace Dental`,
  `C.Sharp Optometry`.
- **Amount:** what he paid for the claimed lines. Never bag fees or tips.
  **Delivery fees (use judgment):** claimable when the delivery was for the
  medical item; ask if it also brought non-medical things.
- **Comments: a terse "how we got this number".** Blank when the amount reads
  straight off the receipt. A subset: `Finasteride $24.80, Clindamycin $20.00`
  (item names verbatim when that's what identifies them). Tax re-applied:
  `Finasteride $10.00 * 1.09125 tax = $10.91`. No prose.
- **Tax multiplier (use judgment):** receipts don't always say what was taxed,
  and can come from another state. Divide the receipt's tax by what looks like
  the taxable subtotal, look up the local sales tax rate, and check the two
  agree and reproduce the tax line. If they don't, say so on the card.
- Eligibility and documentation are his calls: claim what he asks.

Send him this card, every field filled:

| Field | Example |
|---|---|
| Benefit | Health Care FSA (1/1/2026 - 12/31/2026), the year of service |
| Documents, in upload order | walmart-rx-2026-11-10.pdf (named for what it is: Navia's reviewer sees it) |
| Expense type | Prescription Drugs (Navia's types: `navia.benefits()`, or Prescription Drugs, Over-the-Counter Items, Over-The-Counter with Rx, General Healthcare, General Dental Care, General Vision Care, Counseling Services, Chiropractic, Massage Therapy, Orthodontia, Travel, Personal Protective Equipment, Feminine Care Product, Letter of Medical Necessity) |
| Service start / end | 11/10/2026 / 11/10/2026 |
| Provider | Walmart Pharmacy |
| For whom | Louie Yi Jie Tan |
| Amount | $44.80 |
| Comments | Finasteride $24.80, Clindamycin $20.00 |

His OK makes this card **the agreed fields**, checked character for character
from here on. Then file without asking again, unless he asked to see the
checkpoints first: then send them and wait for his go before Send.

## 0b. Travel (mileage) claims

A drive to an in-person visit (doctor, dentist, lab, optometrist) is claimed
again as **its own claim, expense type Travel**. **Whether there was a trip
(use judgment):** from the receipt and what he said; offer or ask when it isn't
obvious. A delivered order has none.

- **Route (use judgment):** home → provider → home by default; another start
  or end only when context says so. His home address is in
  `/memory/profile.md` (cfs store); never write it into this public repo.
- **Documents:** the Google Maps printout first, then every document of the
  base claim.
- **Rate:** the IRS medical mileage rate for the year of service (2026:
  20.5¢/mi); check the IRS figure for a new year.
- **Amount:** cents = round(miles × rate in cents), miles exactly as Google
  prints them.
- **Provider:** base provider + ` (Mileage)`, e.g. `Grace Dental (Mileage)`.
- **Comments:** `22.3mi * $0.205/mi = $4.57`.
- Date and for whom: as the base claim.

**Making the Maps printout** (tested in plain `pinchtab` 2026-11-14; Google
Maps needs no residential IP):

1. Open `https://www.google.com/maps/dir/<home>/<provider address>/<home>/data=!4m2!4m1!3e0`
   (`3e0` = driving, addresses `+`-joined). **Expect:** "Driving … N miles"
   and a "Details" button. Keep Google's default route.
2. "Details". **Expect:** "N min (N miles)" at the top, turn-by-turn steps,
   a Print button.
3. "Print" → "Print including maps". **Expect:** the print view: "<from> to
   <to>   Drive N miles, M min", a notes box, "Cancel" and "Print". Don't click
   its Print: that opens a system dialog.
4. Save the page with headers and footers, so it carries the date and the
   directions URL: `pdf -o <provider>-mileage-<date>.pdf --display-header-footer`
   (another browser: print-to-PDF of this view).
5. Open the PDF: page 1 shows the map, "Drive N miles" and the addresses; N is
   the comment's mileage.

## 1. Browser

Set it up yourself, with any browser tool that can click, type, screenshot and
evaluate JavaScript in the page (examples below are pinchtab batch steps):

- **A residential IP.** `webapi.naviabenefits.com` is Cloudflare-blocked from
  datacenter IPs (this sandbox, Browser Use `--country none`, VPNs): the login
  page loads, nothing after it works. Default: his home IP
  (`pinchtab-proxy start --proxy home`, or `--proxy socks5://127.0.0.1:1080`
  after the home-proxy skill's setup); fallback: `pinchtab-cloud` residential.
- **Images not blocked**, or the form's PDF previewer
  (`assets/js/pdf.worker.min.js`) fails and uploads never list.
- **One tab** on `https://app.naviabenefits.com/`. A reload or new tab starts
  a new claim session.

## 2. Log in

Get the login with request-credential (`naviabenefits.com`) and do steps 1–3
in one `run`. Type values as keystrokes: Navia's Angular form ignores filled
or pasted ones.

```
nav https://app.naviabenefits.com/#/login
expect "#Login_Password[type=password]"
type "#Login_Username" "$CRED_USERNAME"
type "#Login_Password" "$CRED_PASSWORD"
click "#login-btn"
wait --fn "document.body.innerText.includes('Verify Your Identity') || JSON.parse(sessionStorage.getItem('authorizationData')||'{}').token || [...document.querySelectorAll('messagebox')].some(e => e.innerText.trim())" --timeout-ms 30000
```

**Expect** one of:
- a `token` in `sessionStorage.authorizationData`: logged in (the device is
  remembered);
- "Verify Your Identity": pick the phone (the masked number, not the "@" one),
  "send code", **expect** "Enter verification code". The code arrives in
  about 10 s in his phone's notifications (mcp-android
  `android_notification_list`: TextNow, sender `40680`, "Code: NNNNNN"; email
  lags minutes). Type it into "Enter code", tick "Remember this device for
  future logins", "submit code". A new code voids the old one;
- a `<messagebox>` with text: login rejected. Stop and tell Louie.

**Then expect:** `#/pptHome`, his name top right, a `token` set.

## 3. Helpers and pre-checks

Inject `js/navia.js` (this skill's directory); it installs `window.navia`.
Reinject after any full page load. Async helpers also leave
`{done, ok, value | error}` in `navia.result`: start one, wait for
`navia.result.done` (the statement call can take 2 min), read the result.
`navia.formState` is synchronous and returns directly.

```bash
export NAVIA_JS="$(tr '\n' ' ' < "$(ls -1dt /mnt/skills/*/navia-claim ~/.claude/skills/synced/*/navia-claim 2>/dev/null | head -1)/js/navia.js")"
pinchtab-proxy execute-batch <<'EOF'
eval "$NAVIA_JS"
eval "navia.selfcheck(); 'started'"
wait --fn "navia.result && navia.result.done" --timeout-ms 120000
eval "JSON.stringify(navia.result)"
EOF
```

1. `navia.selfcheck()` → **expect** `{ok: true, problems: []}`: the portal
   still has the endpoints and form fields recorded 2026-11-13 (it moves the
   tab to `#/claimSub`). Problems: Navia changed its portal; you can still
   file by hand, but tell Louie the helpers may be off.
2. `navia.duplicates({date: '2026-11-10', amount: '44.80'})` → **expect**
   `possibleDuplicates: []`. A hit (same amount, same service date or posted
   within 45 days, claims and card swipes) goes to Louie first. **Use
   judgment** on plainly different visits: repeat trips cost the same mileage,
   so mention those on the card instead of stopping. Navia also rejects a
   same-date-same-amount item itself (`NVClaimDuplicate`).

Also: `navia.statement({date})` (claims, election, reimbursed),
`navia.benefits()`, and `navia.formState()` (section 4).

## 4. Fill the form

After every step: screenshot, compare with **Expect**, and where it says
**Assert**, run `JSON.stringify(navia.formState())` and check the named
fields. Anything else: stop, see "Getting unstuck".

1. Open `#/claimSub` (or "submit a claim" on the home page). **Expect:**
   "Submit a Claim", "add item to claim", "send claim to Navia".
   **Assert:** `screen: 'summary'`, `items: []`, `termsTicked: false`. Leftover
   items are a stale attempt: "cancel claim request" → "yes" first.
2. "add item to claim". **Expect:** the "Claim Items" dialog at "1 Upload
   Documents", "select the benefit...", "choose files", "File Count: 0".
3. Open "select the benefit..." and click "Health Care FSA" inside that open
   list (the name also appears elsewhere on the page). **Assert:**
   `screen: 'upload'`, `benefit: 'Health Care FSA'`, `planYear` the agreed
   year, e.g. `'01/01/2026 - 12/31/2026'`.
4. Per document, in order: "choose files", give it that one file
   (`upload <path>` fills the picker the click opened), and wait until its row
   is no longer loading before the next. **Assert:** `files` lists every
   document in order, each `loading: false`; `fileCount` matches.
5. **CHECKPOINT 1.**
6. "next". **Expect:** "2 Enter Claim Details", the document preview left,
   "Select a service...", empty boxes, "I'm finished". **Assert:**
   `screen: 'details'`, `selectedBenefit` the agreed benefit and year.
7. Open "Select a service..." and click the agreed type. **Assert:**
   `expenseType`.
8. Type into each box: start and end date (MM/DD/YYYY), Provider name, For
   whom, Amount (`44.80`; it shows `$44.80`), Comments. **Assert:** each value,
   and `rejectedByForm: []` (Navia marks e.g. an empty required box). Fix a
   box: click in, select all, type again.
9. **CHECKPOINT 2.**
10. "I'm finished" (never "add another claim"). **Expect:** a green "Claim
    added successfully" toast, the dialog closed. **Assert:**
    `screen: 'summary'`, exactly one item.
11. **CHECKPOINT 3.**
12. Tick "I have read and agree to Navia's terms and conditions", click "send
    claim to Navia" once. **Expect:** `#/claimReceipt?batchId=…`, "Success!
    Claim Submitted", the receipt with total and anticipated reimbursement
    date. Screenshot it.

## 5. Checkpoints

At each: a **full-size** screenshot (small print matters) and
`navia.formState({expected: <agreed fields>})`, e.g.

```
eval "JSON.stringify(navia.formState({expected: {documents: ['walmart-rx-2026-11-10.pdf'], benefit: 'Health Care FSA', expenseType: 'Prescription Drugs', fromDate: '2026-11-10', toDate: '2026-11-10', provider: 'Walmart Pharmacy', who: 'Louie Yi Jie Tan', amount: '44.80', comments: 'Finasteride $24.80, Clindamycin $20.00'}}))"
```

It returns `ok` and a `checks` list for that screen. Then write out every
field as `field | agreed | screenshot | formState | ✓/✗`. **Pass only if the
screenshot and formState both match the agreed value.** A ✗, an unreadable
value, a failed screenshot or `ok: false` means: fix it, capture again.

| | Screen | Must match |
|---|---|---|
| 1 | Upload Documents, files loaded | Benefit; plan year; every agreed file in order (Maps PDF first on travel), none loading, nothing extra. |
| 2 | Enter Claim Details, filled | Benefit and year; expense type; dates; provider; for whom; amount; the whole comment (scroll it if needed); nothing rejected. And the preview shows **this** claim's evidence: the receipt's date and total, or the Maps PDF's route and miles. |
| 3 | Claim summary | Exactly one item: dates, provider, amount; terms box unticked. |

## 6. Read back and report

On the receipt page (reinject `navia.js` if needed):
`navia.readback({expected: {amount: '44.80', fromDate: '2026-11-10',
provider: 'Walmart Pharmacy', who: 'Louie Yi Jie Tan', comments: '...'}})`
(batchId from the URL). **Expect** `ok: true`, `mismatches: []`, and a
"PENDING Online Claim" in `statementLines`. A mismatch goes to Louie verbatim.

Report the batch id, total, anticipated reimbursement date and the Pending
line, and send the three checkpoint screenshots plus the receipt page; save
them with the comparison tables under
`/drafts/navia-claims/claims/<date>-<provider>/` in the store. Then close the
browser: Navia's logout doesn't end the session.

## Getting unstuck

Look at the screenshot first, then `navia.formState()`.

- **Take over by hand** when a label click fails, matches the wrong element,
  or the page isn't at an Expect state: snapshot the controls
  (`snap --grep "<label words>"`), click by ref (fresh: refs die when the page
  changes) or at the point in the screenshot
  (`click --x X --y Y --unscale last-small`), **one action per batch**,
  screenshot after each, until you are back at an Expect state. Dropdown
  options are links; a stubborn dropdown opens from its arrow.
- **A value didn't stick** (`rejectedByForm`, or the box shows something else):
  click in, Ctrl+A, type again, click elsewhere, assert again.
- **Upload stuck loading / never lists:** the PDF previewer is blocked.
  Restart the browser without image or script blocking and start the claim
  again.
- **Wrong value on the item:** the pencil on its row → "next" → fix →
  "Update claim" → redo checkpoints 2 and 3. Or the trash icon → "yes" → add
  it again.
- **Messy state** (extra items, wrong document): "cancel claim request" →
  "yes" → step 1. Navia then shows a red "We were unable to complete your
  request": its own cancel bug (it leaves the unsubmitted upload behind),
  harmless. Nothing is submitted before Send.
- **Navia rejects the item** (e.g. a duplicate): stop, tell Louie.
- **Logged out mid-claim:** log in again, restart from step 1.
- **Nothing works after login, network status 0:** not a residential IP.
- **Unsure whether Send went through:** **never click Send again.**
  `navia.statement({date})`: look for "PENDING Online Claim" with the amount
  (or My Benefits → Health Care FSA). Refile only if it's absent and Louie
  agrees.
- **Portal changed** (selfcheck problems, a step that no longer matches):
  `REPAIR.md` has the API and the page hooks the helpers read.
