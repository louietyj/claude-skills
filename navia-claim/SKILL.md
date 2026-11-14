---
name: navia-claim
description: "File Louie's Navia Health Care FSA reimbursement claims from receipts he sends (photos, screenshots, PDFs): agree every claim field with him up front, fill Navia's own claim form in a browser on a residential IP, verify three mandatory screenshots against the agreed fields before Send, and read the claim back afterwards. Ships js/navia.js, read-only page helpers for the duplicate check, a portal-unchanged check and the read-back. Use whenever he sends a receipt or bill to claim from his FSA, says 'submit this to Navia', or asks what's on his FSA statement."
---

# navia-claim

You drive the browser; this skill gives you the route, the state to expect at
every step, and read-only page helpers. The claim itself always goes through
Navia's own form, never through API writes, because the three checkpoint
screens only exist there.

This is a skill, not a script. Two kinds of instruction follow:

- **Hard rules**, marked as such, which you follow exactly.
- **Use judgment** items: decide from the receipt, what Louie said, and what
  you can look up, and ask him when the answer isn't clear. They describe the
  outcome and the format he wants, not a formula; don't turn them into one.
  He approves every field anyway, so a sensible proposal with your reasoning
  beats a rigid rule applied wrongly.

**Hard rule: never click "send claim to Navia" until you have captured
checkpoints 1, 2 and 3 below as full-size screenshots and matched every field
on them against the agreed fields, in writing.** No exceptions for short
claims, retries or "I just looked at it".

## 0. Agree the fields with Louie (before the browser)

Louie's conventions (from him, 2026-11-14):

- **One receipt, one claim, one item.** The exception is a receipt covering
  different services (Rx and over-the-counter on one Walmart receipt): a
  **separate claim per service**, each claiming only its own lines. Travel is
  always its own claim (below).
- **Date (use judgment):** start and end are almost always the same day, the
  day on the receipt, even if he received it later. Pick the most
  defensible-looking date on the receipt: a big, bold printed date wins over a
  small dispense date elsewhere on the page. As a tiebreak, dispense date >
  order date > delivery date.
- **For whom:** Louie Yi Jie Tan.
- **Provider:** the basic name: `Walmart Pharmacy`, `Grace Dental`,
  `C.Sharp Optometry`.
- **Amount:** what he paid for the claimed lines. Bag fees and tips are never
  claimed. **Delivery fees (use judgment):** claimable when the delivery was
  for the medical item; ask if the same delivery also brought non-medical
  things.
- **Comments: a brief "how we got this number", nothing more.** Blank when
  the amount reads straight off the receipt. A subset of lines:
  `Finasteride $24.80, Clindamycin $20.00` (copy the item names verbatim when
  that's what identifies them). Tax re-applied: `Finasteride $10.00 * 1.09125
  tax = $10.91`. No prose. The format is fixed; the numbers are not:
- **Tax multiplier (use judgment):** receipts don't always say what was taxed
  or at what rate, and they can come from another state or county. Combine
  what you have: divide the receipt's tax by what looks like the taxable
  subtotal, look up the sales tax rate where the store is, and make sure the
  two agree and reproduce the receipt's tax line. If they don't, say so on the
  card.
- Eligibility and documentation are his calls: claim what he asks.

Read the receipt and send him this card, every field filled:

| Field | Example | Notes |
|---|---|---|
| Benefit | Health Care FSA (1/1/2026 - 12/31/2026) | Plan year = the year of service. |
| Documents | walmart-rx-2026-11-10.pdf | Name each file for what it is; Navia's reviewer sees the name. PDF or image. In upload order. |
| Expense type | Prescription Drugs | Navia's list: Prescription Drugs, Over-the-Counter Items, Over-The-Counter with Rx, General Healthcare, General Dental Care, General Vision Care, Counseling Services, Chiropractic, Massage Therapy, Orthodontia, Travel, Personal Protective Equipment, Feminine Care Product, Letter of Medical Necessity. |
| Service start / end | 11/10/2026 / 11/10/2026 | |
| Provider | Walmart Pharmacy | |
| For whom | Louie Yi Jie Tan | |
| Amount | $44.80 | |
| Comments | Finasteride $24.80, Clindamycin $20.00 | |

Wait for his OK or edits. This card is **the agreed fields**: everything
below is checked against it, character for character. Then, by default, you
file without asking again; if he asked to see the screenshots first, send the
three checkpoints and wait for his go before Send.

## 0b. Travel (mileage) claims

A drive to an in-person visit (doctor, dentist, lab, optometrist) is claimed
again **as its own claim**, expense type **Travel**. **Whether there was a
trip (use judgment):** from the receipt and what Louie said; offer the travel
claim, or ask, when it isn't obvious. A delivered order has none.

- **Route (use judgment):** home → the provider → home by default. Louie's home
  address is in his memory profile (`/memory/profile.md` in the cfs store);
  never write it into this public repo. Another start or end (work, another
  stop) only when context says so; ask if unsure.
- **Evidence: a Google Maps printout, first**, then every document of the base
  claim. Name it `<provider>-mileage-<date>.pdf`. See "Making the Maps
  printout" below.
- **Rate:** the IRS medical mileage rate for the year of service (2026:
  20.5¢/mi). Check the IRS's figure for a new year before using it.
- **Amount:** cents = round(miles × rate in cents), with the miles exactly as
  Google prints them.
- **Provider:** the base claim's provider + ` (Mileage)`: `Grace Dental (Mileage)`.
- **Comments:** `22.3mi * $0.205/mi = $4.57`.
- Date, for whom: as the base claim.

Example: a 22.3 mi round trip to Grace Dental in 2026 is `Grace Dental
(Mileage)`, `22.3mi * $0.205/mi = $4.57`, documents
`grace-dental-mileage-2026-03-17.pdf` then the dental receipt. Checkpoint 1
also confirms the Maps PDF is listed first.

### Making the Maps printout

Tested in pinchtab 2026-11-14 (plain `pinchtab` works; Google Maps doesn't
need the residential IP). Google's print view is part of the page, so save
the page as a PDF yourself rather than using its Print button, which opens a
system print dialog.

1. Open `https://www.google.com/maps/dir/<home>/<provider address>/<home>/data=!4m2!4m1!3e0`
   (`3e0` = driving; addresses `+`-joined). **Expect:** a "Driving … N miles"
   route option and a "Details" button. Use Google's default (fastest) route.
2. Click "Details". **Expect:** "N min (N miles)" at the top of the panel, the
   turn-by-turn list, and Send / Share / Print buttons.
3. Click "Print", then "Print including maps". **Expect:** the print view:
   "<from> to <to>   Drive N miles, M min" across the top, a notes box,
   "Cancel" and "Print" buttons. Don't click its Print.
4. Save the page as a PDF with headers and footers on, so it carries the date
   and the directions URL like his past printouts:
   ```
   pdf -o <provider>-mileage-<date>.pdf --display-header-footer
   ```
   (Any other browser: its print-to-PDF of this view.)
5. Open the PDF and check page 1: the map, "Drive N miles", the start, stop
   and end addresses. N is the mileage in the comment.

## 1. Browser

Set it up yourself with whatever browser skill you have. Requirements:

- **A residential IP.** `webapi.naviabenefits.com` is Cloudflare-blocked from
  datacenter IPs (this sandbox, Browser Use `--country none`, VPNs): the login
  page loads but nothing after it works. Louie's home IP is the default
  (`pinchtab-proxy start --proxy home`); Browser Use residential
  (`pinchtab-cloud`) if the home relay is down.
- **Images not blocked.** The form previews documents with
  `assets/js/pdf.worker.min.js`; with images or scripts blocked the preview
  fails and the uploaded file never appears in the list.
- One tab on `https://app.naviabenefits.com/`. Work in it; a reload or new tab
  starts a new claim session.

Examples below are pinchtab batch steps. Any tool that can click, type, take
screenshots and evaluate JavaScript in the page works the same way.

## 2. Log in

Get the login with request-credential (`naviabenefits.com`), all of this in
one `run`:

1. Open `https://app.naviabenefits.com/#/login`.
   **Expect:** a visible password field `#Login_Password[type=password]`.
2. Type (keystrokes, not a pasted/filled value: Angular ignores those) the
   username into `#Login_Username`, the password into `#Login_Password`; click
   `#login-btn`.
3. **Expect within 30 s, one of:**
   - `sessionStorage.authorizationData` has a `token`: logged in (the browser
     remembers the device). Go to step 5.
   - The page says "Verify Your Identity": step 4.
   - A `<messagebox>` with text: the login was rejected. Stop and tell Louie.

```
nav https://app.naviabenefits.com/#/login
expect "#Login_Password[type=password]"
type "#Login_Username" "$CRED_USERNAME"
type "#Login_Password" "$CRED_PASSWORD"
click "#login-btn"
wait --fn "document.body.innerText.includes('Verify Your Identity') || JSON.parse(sessionStorage.getItem('authorizationData')||'{}').token || [...document.querySelectorAll('messagebox')].some(e => e.innerText.trim())" --timeout-ms 30000
```

4. Code: choose the phone radio (the masked number, not the one with "@"), click
   "send code". **Expect:** "Enter verification code". Read the code from his
   phone's notifications (mcp-android `android_notification_list`, TextNow,
   sender `40680`, "Code: NNNNNN"; it arrives in about 10 s; email lags
   minutes). Type it into the "Enter code" box, tick "Remember this device for
   future logins", click "submit code". A new code request voids the old one.
5. **Expect:** URL `#/pptHome`, his name and employer top right,
   `authorizationData.token` set.

## 3. Load the helpers and run the pre-checks

Inject `js/navia.js` (in this skill's directory) into the tab. It installs
`window.navia`; reinject after any full page load. Each helper returns a
promise **and** leaves `{done, ok, value | error}` in `navia.result`, so with
an eval that doesn't await, start it, wait for `navia.result.done`, then read
`navia.result`. The statement call can take over a minute: wait up to 2 min.

```bash
export NAVIA_JS="$(tr '\n' ' ' < "$(ls -1dt /mnt/skills/*/navia-claim ~/.claude/skills/synced/*/navia-claim 2>/dev/null | head -1)/js/navia.js")"
pinchtab-proxy execute-batch <<'EOF'
eval "$NAVIA_JS"
eval "navia.selfcheck(); 'started'"
wait --fn "navia.result && navia.result.done" --timeout-ms 120000
eval "JSON.stringify(navia.result)"
EOF
```

1. `navia.selfcheck()` → **expect** `{ok: true, problems: []}`. It reads the
   portal's code and confirms the endpoints and form fields recorded
   2026-11-13 (it moves the tab to `#/claimSub`). Problems mean Navia changed
   its portal: still fine to file by hand, but the read-back may not work and
   this skill needs updating; tell Louie.
2. `navia.duplicates({date: '2026-11-10', amount: '44.80'})` → **expect**
   `possibleDuplicates: []`. Any hit (same amount, same service date or posted
   within 45 days, claims and card swipes alike) goes to Louie before you go
   on. **Use judgment** for hits that are plainly a different visit: repeat
   trips to one provider cost the same mileage, so a travel hit with a
   different service date is expected; mention it on the card rather than
   stopping. Navia's form also rejects a same-date-same-amount claim itself
   (`NVClaimDuplicate`).

Also there: `navia.statement({date})` (claims, election, reimbursed) and
`navia.benefits()` (benefits, plan dates, expense types).

## 4. Fill the form

Take a screenshot after every step and compare it with **Expect** before the
next one. Anything else means stop and see "Getting unstuck".

1. Open `https://app.naviabenefits.com/#/claimSub` (or "submit a claim" on
   the home page). **Expect:** "Submit a Claim"; "Claim Items" with **no
   rows**; "add item to claim"; the terms box unticked. Rows already there are
   a stale attempt: "cancel claim request" → "yes" first.
2. Click "add item to claim". **Expect:** a "Claim Items" dialog, "1 Upload
   Documents", a "select the benefit..." dropdown, "drag and drop or choose
   files", "Uploaded Files … File Count: 0".
3. Open "select the benefit..." and click "Health Care FSA" in its list (the
   options are links; "Health Care FSA" also appears elsewhere on the page, so
   use the open list's own entry). **Expect:** the benefit box reads "Health
   Care FSA" and a plan-year box beside it reads the agreed year, e.g.
   "01/01/2026 - 12/31/2026"; "plan dates" and "available balance" links.
4. For each agreed document, in the agreed order: click "choose files" and
   give it that one file (`upload <path>` fills the picker that click opened),
   and wait until its row's Actions column shows a trash icon, not "loading",
   before the next. **Expect:** one row per document, in that order, with
   names and sizes; "File Count" equal to the number of documents.
5. **CHECKPOINT 1** (see section 5).
6. Click "next". **Expect:** "2 Enter Claim Details"; the document preview on
   the left; "Selected Benefit" reading the agreed benefit and year; "Select a
   service..."; empty date, Provider name, For whom, Amount, Comments boxes;
   buttons "cancel", "back to documents", "I'm finished", "add another claim".
7. Open "Select a service..." and click the agreed expense type. **Expect:**
   the box shows it; "Benefit eligibility date" and "Anticipated
   reimbursement date" appear.
8. Type (keystrokes) into each box: Service start date and Service end date as
   MM/DD/YYYY, Provider name, For whom, Amount as `44.80` (it reformats to
   `$44.80`), Comments. **Expect:** every box shows the typed value
   (`value <selector>` or reading the inputs in the page confirms what the
   screenshot shows). To fix one: click in, select all, type again.
9. **CHECKPOINT 2.**
10. Click "I'm finished". **Expect:** a green "Claim added successfully" toast,
    the dialog closed, the Claim Items table with exactly one row. Never
    "add another claim": one item per claim.
11. **CHECKPOINT 3.**
12. Tick "I have read and agree to Navia's terms and conditions", click "send
    claim to Navia". Once. **Expect:** URL `#/claimReceipt?batchId=…`, "Success!
    Claim Submitted", "Online Claim Submission Receipt" with the anticipated
    reimbursement date, the claim total and "Claimed Items". Screenshot it.

## 5. The checkpoints

At each one: a **full-size** screenshot (small print matters), then write out
every field as `field | agreed | on screen | ✓/✗`. Any ✗, an unreadable value,
or a failed screenshot means do not proceed: fix it (Getting unstuck) and
capture the checkpoint again.

| | Screen | Must match the agreed fields |
|---|---|---|
| 1 | Upload Documents, files loaded | Benefit; plan year; every agreed file, in the agreed order (Maps PDF first on a travel claim), none "loading", nothing extra; File Count. |
| 2 | Enter Claim Details, filled | Selected Benefit + year; expense type; start and end dates; provider; for whom; amount; the whole comment (scroll the box if it wraps out of view). And the preview on the left shows **this** claim's evidence: the receipt's date and total, or on a travel claim the Maps PDF's route and miles. |
| 3 | Claim summary | Exactly one row; its service dates, provider and amount; nothing else in the list; terms box still unticked. |

## 6. Read back and report

On the receipt page, reinject `navia.js` if needed and run
`navia.readback({expected: {amount: '44.80', fromDate: '2026-11-10', provider:
'Walmart Pharmacy', who: 'Louie Yi Jie Tan', comments: '...'}})` (the batchId
comes from the page URL). **Expect** `ok: true`, `mismatches: []`, and a
`statementLines` entry "PENDING Online Claim" for the total. A mismatch goes to
Louie verbatim.

Report to Louie: the batch id, total, anticipated reimbursement date, the
Pending line, and the three checkpoint screenshots plus the receipt page
(send them, and save them with the comparison tables under
`/drafts/navia-claims/claims/<date>-<provider>/` in the store). Then close the
browser: Navia's logout doesn't end the session server-side.

## Getting unstuck

Always look at the screenshot first; most stalls are visible.

- **Take over by hand.** When a label click fails or the page isn't what the
  recipe expects, drive it like a person: snapshot the interactive elements
  (`snap --grep "<label words>"`), click by ref, or click the point you see in
  the screenshot (`click --x X --y Y --unscale last-small`), **one action per
  batch**, screenshot after each, until the page is back at an Expect state.
- **"selector is ambiguous" / wrong element**: use the ref from a fresh snap
  of the open dropdown or dialog, or coordinates. Refs die on page changes.
- **A typed value didn't stick or reverted**: click into the box, select all
  (Ctrl+A), type again, click elsewhere, re-read the value.
- **Dropdown won't open**: click its arrow, not the text; the options are links.
- **Upload row stuck on "loading"** or the file never lists: the PDF previewer
  is blocked. Restart the browser without image/script blocking, reload, and
  start the claim again (a reload makes a new session; old uploads are
  abandoned).
- **A wrong value on the item**: pencil icon on its row → "next" past the
  documents → fix → "Update claim" → redo checkpoints 2 and 3. Or the trash
  icon → "yes" and add it again.
- **Anything messy** (extra rows, wrong document, confused state): "cancel
  claim request" → "yes" → start again at step 1. Nothing is submitted before
  Send.
- **Navia rejects the item** with a message (e.g. a duplicate): stop, tell
  Louie.
- **Logged out mid-claim** (redirected to the login page, helpers say "not
  logged in"): log in again and restart the claim from step 1.
- **Nothing loads after login / status 0 network errors**: the browser isn't
  on a residential IP.
- **Not sure Send went through** (timeout, blank page, error after the
  click): **never click Send again.** Run `navia.statement({date})` and look
  for a "PENDING Online Claim" line with the amount; open "My Benefits" →
  Health Care FSA if the helpers can't run. Only if it is absent and Louie
  agrees, file again.

## How it works, for repairs

Navia's portal is an Angular app at `app.naviabenefits.com` talking to
`webapi.naviabenefits.com` with `Authorization: bearer <token>` and
`X-NAVIA-CSRF-TOKEN`, both from `sessionStorage.authorizationData` (never print
it). Rejected requests come back without CORS headers, so they show as network
failures in the page. Recorded 2026-11-13 from the form's own traffic:

- `GET api/common/trans/claim` → the claim session's `transId`. A reload starts a new one.
- `GET api/ppt/claims/benefits` → benefits, plan dates, `lastSubmitDate`, expense types.
- `POST api/ppt/docs/upload/files?transId=` (multipart `files`) on choosing a file; `DELETE api/ppt/docs/upload?transId=&fileName=` from the trash icon. After a rejected validate Navia has already dropped the uploads.
- `POST api/ppt/claims/validate?transId=` per item ("I'm finished" / "Update claim"): `{id, age, amount: "44.80", benefitId, comments, depId, expenseTypeId, fromDate: "MM/DD/YYYY", toDate, provider, who, isNoResid, isOffset, isSecIns, ProcessResidual}` → 200 empty or 400 `[{errCode, errMsg}]`.
- `POST api/ppt/claims/batch?transId=` on Send: the array of those items (`id` 1-based) → the `batchId` string; the page then routes to `#/claimReceipt?batchId=<encoded again>`.
- `GET api/ppt/claims/receipt?id=<batchId, URL-encoded again>` → what Navia recorded.
- `GET api/ppt/stmt/accttrans?planId=&benefitTypeId=` → the statement; `planId` from `sessionStorage.pptResponse[].benefitStmts[]` (one per plan year, with `beginDate`/`endDate`).

To see what changed after a portal update, file one claim by hand with the
browser's network capture on and compare the `/validate` and `/batch` bodies,
then update `ENDPOINTS`/`FORM_FIELDS` in `js/navia.js` and the recipe above.
