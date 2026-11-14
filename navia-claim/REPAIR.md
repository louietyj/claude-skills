# navia-claim: how it works, for repairs

Read this when `navia.selfcheck()` reports problems or a recipe step no longer
matches the page. Filing a claim doesn't need it.

Navia's portal is an Angular app at `app.naviabenefits.com` calling
`webapi.naviabenefits.com` with `Authorization: bearer <token>` and
`X-NAVIA-CSRF-TOKEN`, both from `sessionStorage.authorizationData` (never
print it). Rejected requests lack CORS headers, so the page sees network
failures. The form's own traffic, recorded 2026-11-13:

- `GET api/common/trans/claim` → the claim session's `transId` (new per page load).
- `GET api/ppt/claims/benefits` → benefits, plan dates, `lastSubmitDate`, expense types.
- `POST api/ppt/docs/upload/files?transId=` (multipart `files`) on choosing a file; `DELETE api/ppt/docs/upload?transId=&fileName=` from the trash icon.
- `POST api/ppt/claims/validate?transId=` per item ("I'm finished", "Update claim"): `{id, age, amount: "44.80", benefitId, comments, depId, expenseTypeId, fromDate: "MM/DD/YYYY", toDate, provider, who, isNoResid, isOffset, isSecIns, ProcessResidual}` → 200 empty, or 400 `[{errCode, errMsg}]`.
- `POST api/ppt/claims/batch?transId=` on Send: those items as an array (`id` 1-based) → the `batchId`; the page routes to `#/claimReceipt?batchId=<encoded again>`.
- `GET api/ppt/claims/receipt?id=<batchId, encoded again>` → what Navia recorded.
- `GET api/ppt/stmt/accttrans?planId=&benefitTypeId=` → the statement; `planId` from `sessionStorage.pptResponse[].benefitStmts[]` (per plan year, `beginDate`/`endDate`).

`formState` reads these page hooks (2026-11-14): the dialog `.nv-modal`;
dropdown selections `a.dropdown-toggle.selected-item`; upload step
`#fileDropRef`, file rows `.list-row` with `.file-name-btn`, "File Count: N";
details `#fromDate`, `#toDate`, `#providerName`, `#forWhom`, `#claimComment`,
inputs with placeholder "Amount" / "Selected Benefit", preview name
`span.file-name`, Angular's `ng-invalid`; summary rows `.listview .list-row`
(columns: dates, provider, amount), terms `#agreeToTerms`.

After a portal update: file one claim by hand with network capture on, compare
the `/validate` and `/batch` bodies and the hooks above, then update
`ENDPOINTS`, `FORM_FIELDS` and `formState` in `js/navia.js` and the recipe in
`SKILL.md`.
