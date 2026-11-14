/* Read-only helpers for a signed-in app.naviabenefits.com tab; installs window.navia.
   Results also land in navia.result for eval tools that don't await. Single quotes
   and block comments only, so the file survives being joined onto one line. */
(() => {
  const API = 'https://webapi.naviabenefits.com/';
  const ENDPOINTS = {
    getTransId: 'api/common/trans/claim',
    getClaim: 'api/ppt/claims/benefits',
    validateClaim: 'api/ppt/claims/validate?transId=',
    uploadFile: 'api/ppt/docs/upload/files?transId=',
    deleteFile: 'api/ppt/docs/upload?transId=',
    saveAllClaims: 'api/ppt/claims/batch?transId=',
    claimReceipt: 'api/ppt/claims/receipt?id='
  };
  const FORM_FIELDS = ['expenseTypeId', 'isNoResid', 'isSecIns', 'ProcessResidual', 'depId'];

  const auth = () => {
    const a = JSON.parse(sessionStorage.getItem('authorizationData') || 'null');
    if (!a || !a.token) throw new Error('not logged in to Navia');
    return a;
  };

  const get = async (path) => {
    const a = auth();
    const r = await fetch(API + path, {credentials: 'include', cache: 'no-store', headers: {
      Accept: 'application/json, text/plain, */*', Authorization: 'bearer ' + a.token, 'X-NAVIA-CSRF-TOKEN': a.xnaviacsrfToken}});
    const text = await r.text();
    if (r.status !== 200) throw new Error(path.split('?')[0] + ' answered ' + r.status + ': ' + text.slice(0, 300));
    try { return JSON.parse(text); } catch (e) { return text; }
  };

  const toDate = (s) => {
    s = String(s || '').slice(0, 10);
    let m = s.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (m) return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
    m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
    if (m) return new Date(Date.UTC(+m[3], +m[1] - 1, +m[2]));
    throw new Error('bad date ' + JSON.stringify(s) + ': use YYYY-MM-DD');
  };
  const iso = (d) => d.toISOString().slice(0, 10);
  const us = (d) => iso(d).replace(/^(\d{4})-(\d{2})-(\d{2})$/, '$2/$3/$1');
  const days = (a, b) => Math.round(Math.abs(a - b) / 86400000);
  const covers = (start, end, d) => toDate(start) <= d && d <= toDate(end);

  const benefits = async () => {
    const b = await get(ENDPOINTS.getClaim);
    return (b.claimBenes || []).map((x) => ({
      name: x.benefitName, descr: x.descr, benefitTypeId: x.benefitTypeId,
      start: x.startDate.slice(0, 10), end: x.endDate.slice(0, 10), lastSubmitDate: (x.lastSubmitDate || '').slice(0, 10),
      election: x.annualElection, reimbursed: x.disburseToDate,
      expenseTypes: (x.expenseTypes || []).map((t) => t.descr)}));
  };

  /* The statement's planId lives in the portal's own session data, one entry per plan year. */
  const planId = (benefitTypeId, d) => {
    const ppt = JSON.parse(sessionStorage.getItem('pptResponse') || '[]');
    for (const group of [].concat(ppt)) for (const s of (group && group.benefitStmts) || []) {
      if (String(s.benefitTypeId) !== String(benefitTypeId) || !s.planId || !s.beginDate || !s.endDate) continue;
      if (covers(s.beginDate, s.endDate, d)) return s.planId;
    }
    throw new Error('no plan of benefit type ' + benefitTypeId + ' covers ' + iso(d) + ' (open the portal home page once, then retry)');
  };

  const statement = async ({date, benefit = 'Health Care FSA'} = {}) => {
    const d = date ? toDate(date) : new Date();
    const bene = (await benefits()).find((b) => (b.name || '').toLowerCase() === benefit.toLowerCase() && covers(b.start, b.end, d));
    if (!bene) throw new Error('no ' + benefit + ' covers ' + iso(d));
    const pid = decodeURIComponent(planId(bene.benefitTypeId, d));
    const st = await get('api/ppt/stmt/accttrans?planId=' + encodeURIComponent(pid) + '&benefitTypeId=' + bene.benefitTypeId);
    const claims = (st.transLines || []).filter((l) => l.claimAmount).map((l) => {
      const det = {}; for (const x of l.details || []) det[x.name] = x.descr;
      return {posted: (l.postedDate || '').slice(0, 10), amount: l.claimAmount, descr: l.descr || '', status: det['Status'] || '',
              serviceDates: det['Service Date(s)'] || '', provider: det['Provider'] || det['Merchant'] || '',
              expenseType: det['Expense Type'] || '', submitted: det['Submitted on'] || ''};
    });
    return {benefit: bene.descr, election: bene.election, reimbursed: bene.reimbursed, lastSubmitDate: bene.lastSubmitDate, claims};
  };

  /* Earlier claims or card swipes with the same amount, on the same service date or posted within 45 days. */
  const duplicates = async ({date, amount, benefit}) => {
    const d = toDate(date); const amt = Number(amount);
    const st = await statement({date, benefit});
    const hits = st.claims.filter((l) => Math.abs(l.amount - amt) < 0.005 &&
      (l.serviceDates.includes(us(d)) || (l.posted && days(toDate(l.posted), d) <= 45)));
    return {benefit: st.benefit, possibleDuplicates: hits};
  };

  /* Confirms the portal's claim code still uses the endpoints and form fields recorded 2026-11-13.
     Opens the claim page so its code is loaded. */
  const selfcheck = async () => {
    if (!location.hash.startsWith('#/claimSub')) location.hash = '#/claimSub';
    const table = {}; const fields = {}; const seen = new Set();
    for (let tries = 0; tries < 20 && Object.keys(table).length < Object.keys(ENDPOINTS).length; tries++) {
      const urls = performance.getEntriesByType('resource').map((e) => e.name)
        .filter((u) => /^https:\/\/app\.naviabenefits\.com\/.*\.js$/.test(u) && !seen.has(u));
      for (const u of urls) {
        seen.add(u);
        const t = await (await fetch(u)).text();
        for (const k of Object.keys(ENDPOINTS)) { const m = t.match(new RegExp('[,{]' + k + ':.([^\'`\x22]*).')); if (m) table[k] = m[1]; }
        for (const f of FORM_FIELDS) if (t.includes(f)) fields[f] = true;
      }
      await new Promise((r) => setTimeout(r, 500));
    }
    const problems = Object.keys(ENDPOINTS).filter((k) => table[k] !== ENDPOINTS[k])
      .map((k) => k + ': expected ' + ENDPOINTS[k] + ', portal has ' + (table[k] || 'nothing'))
      .concat(FORM_FIELDS.filter((f) => !fields[f]).map((f) => 'form field ' + f + ' is gone'));
    return {ok: problems.length === 0, problems};
  };

  /* After Send: what Navia recorded, compared with the agreed fields. batchId defaults to the receipt page's URL. */
  const readback = async ({expected, batchId} = {}) => {
    if (!batchId) {
      const m = location.hash.match(/batchId=([^&]+)/);
      if (!m) throw new Error('no batchId: pass one, or run this on the claim receipt page');
      batchId = decodeURIComponent(m[1]);
    }
    const receipt = await get(ENDPOINTS.claimReceipt + encodeURIComponent(batchId));
    const items = receipt.claimItems || [];
    const mismatches = [];
    if (expected) {
      if (items.length !== 1) mismatches.push('receipt lists ' + items.length + ' items, expected 1');
      const it = items[0] || {};
      if (Math.abs(Number(it.cost) - Number(expected.amount)) >= 0.005) mismatches.push('amount: agreed ' + expected.amount + ', recorded ' + it.cost);
      if ((it.date || '').slice(0, 10) !== iso(toDate(expected.fromDate))) mismatches.push('date: agreed ' + expected.fromDate + ', recorded ' + it.date);
      for (const [k, r] of [['provider', 'provider'], ['who', 'who'], ['comments', 'notes']])
        if (expected[k] !== undefined && String(it[r] || '').trim() !== String(expected[k]).trim())
          mismatches.push(k + ': agreed ' + JSON.stringify(expected[k]) + ', recorded ' + JSON.stringify(it[r]));
    }
    const on = expected ? expected.fromDate : (items[0] || {}).date;
    const st = await statement({date: on});
    const pending = st.claims.filter((l) => /pending/i.test(l.descr) && Math.abs(l.amount - Number(receipt.total)) < 0.005);
    if (!pending.length) mismatches.push('no Pending line for ' + receipt.total + ' on the statement yet');
    return {ok: mismatches.length === 0, batchId, total: receipt.total, submitted: receipt.submitted,
            nextReimbDate: (receipt.nextReimbDate || '').slice(0, 10), items, statementLines: pending, mismatches};
  };

  /* What the claim form shows right now, read from the page, as a second channel next to the
     checkpoint screenshots. Pass the agreed fields to get a per-field comparison. */
  const visible = (e) => !!(e && (e.offsetWidth || e.offsetHeight || e.getClientRects().length));
  const text = (e) => (e ? e.innerText.replace(/\s+/g, ' ').trim() : null);
  const money = (s) => Number(String(s || '').replace(/[$,\s]/g, ''));
  const formState = ({expected} = {}) => {
    const modal = [...document.querySelectorAll('.nv-modal')].find(visible);
    const toggles = modal ? [...modal.querySelectorAll('a.dropdown-toggle.selected-item')].filter(visible).map(text) : [];
    const byPh = (ph) => [...modal.querySelectorAll('input, textarea')].find((e) => e.placeholder === ph && visible(e));
    const field = (sel) => { const e = modal.querySelector(sel); return visible(e) ? e : null; };
    let state;
    if (modal && field('#fromDate')) {
      const inputs = {fromDate: field('#fromDate'), toDate: field('#toDate'), provider: field('#providerName'),
                      who: field('#forWhom'), amount: byPh('Amount'), comments: field('#claimComment')};
      state = {screen: 'details', selectedBenefit: (byPh('Selected Benefit') || {}).value, expenseType: toggles[0] || null,
               previewFile: text([...modal.querySelectorAll('span.file-name')].find(visible))};
      for (const [k, e] of Object.entries(inputs)) state[k] = e ? e.value : null;
      state.rejectedByForm = Object.entries(inputs).filter(([, e]) => e && e.classList.contains('ng-invalid')).map(([k]) => k);
    } else if (modal && visible(document.getElementById('fileDropRef'))) {
      const rows = [...modal.querySelectorAll('.list-row')].filter((r) => visible(r) && r.querySelector('.file-name-btn'));
      state = {screen: 'upload', benefit: toggles[0] || null, planYear: toggles[1] || null,
               fileCount: Number(((modal.innerText.match(/File Count:\s*(\d+)/) || [])[1]) || 0),
               files: rows.map((r) => ({name: text(r.querySelector('.file-name-btn')), size: ((text(r).match(/[\d.]+\s*[KMG]?B/) || [])[0]) || null,
                                        loading: /loading/i.test(text(r))}))};
    } else if (location.hash.startsWith('#/claimReceipt')) {
      state = {screen: 'receipt', batchId: decodeURIComponent((location.hash.match(/batchId=([^&]+)/) || [])[1] || '')};
    } else if (location.hash.startsWith('#/claimSub')) {
      const rows = [...document.querySelectorAll('.listview .list-row')].filter(visible)
        .map((r) => [...r.children].map(text)).filter((c) => c[0] !== 'Service Date');
      const terms = document.getElementById('agreeToTerms');
      state = {screen: 'summary', termsTicked: !!(terms && terms.checked),
               items: rows.map((c) => { const d = (c[0] || '').match(/\d{1,2}\/\d{1,2}\/\d{4}/g) || [];
                                        return {fromDate: d[0] || null, toDate: d[1] || d[0] || null, provider: c[1], amount: c[2]}; })};
    } else {
      state = {screen: 'other', hash: location.hash};
    }
    if (!expected) return state;

    const checks = [];
    const eq = (name, agreed, page, same) => checks.push({field: name, agreed, page, ok: same === undefined ? String(agreed).trim() === String(page == null ? '' : page).trim() : same});
    const sameDate = (a, b) => { try { return iso(toDate(a)) === iso(toDate(b)); } catch (e) { return false; } };
    const sameMoney = (a, b) => Math.abs(money(a) - money(b)) < 0.005;
    const docs = expected.documents || [];
    if (state.screen === 'upload') {
      eq('benefit', expected.benefit || 'Health Care FSA', state.benefit);
      const year = String(toDate(expected.fromDate).getUTCFullYear());
      eq('planYear', year, state.planYear, (state.planYear || '').includes(year));
      eq('fileCount', docs.length, state.fileCount, state.fileCount === docs.length && state.files.length === docs.length);
      docs.forEach((d, i) => eq('file ' + (i + 1), d, (state.files[i] || {}).name));
      eq('loading', 'none', state.files.filter((f) => f.loading).map((f) => f.name).join(', ') || 'none');
    } else if (state.screen === 'details') {
      eq('selectedBenefit', expected.benefit || 'Health Care FSA', state.selectedBenefit, (state.selectedBenefit || '').startsWith(expected.benefit || 'Health Care FSA'));
      eq('expenseType', expected.expenseType, state.expenseType);
      eq('fromDate', expected.fromDate, state.fromDate, sameDate(expected.fromDate, state.fromDate));
      eq('toDate', expected.toDate || expected.fromDate, state.toDate, sameDate(expected.toDate || expected.fromDate, state.toDate));
      eq('provider', expected.provider, state.provider);
      eq('who', expected.who, state.who);
      eq('amount', expected.amount, state.amount, sameMoney(expected.amount, state.amount));
      eq('comments', expected.comments || '', state.comments);
      if (docs.length) eq('previewFile', docs[0], state.previewFile, docs.includes(state.previewFile));
      eq('rejectedByForm', 'none', state.rejectedByForm.join(', ') || 'none');
    } else if (state.screen === 'summary') {
      const it = state.items[0] || {};
      eq('items', 1, state.items.length);
      eq('fromDate', expected.fromDate, it.fromDate, sameDate(expected.fromDate, it.fromDate));
      eq('toDate', expected.toDate || expected.fromDate, it.toDate, sameDate(expected.toDate || expected.fromDate, it.toDate));
      eq('provider', expected.provider, it.provider);
      eq('amount', expected.amount, it.amount, sameMoney(expected.amount, it.amount));
      eq('termsTicked', false, state.termsTicked, state.termsTicked === false);
    }
    return {...state, ok: checks.every((c) => c.ok), checks};
  };

  const wrap = (fn) => (args) => {
    window.navia.result = {done: false};
    return Promise.resolve().then(() => fn(args || {})).then(
      (value) => { window.navia.result = {done: true, ok: true, value}; return value; },
      (e) => { window.navia.result = {done: true, ok: false, error: String((e && e.message) || e)}; throw e; });
  };

  const wrapSync = (fn) => (args) => {
    try { const value = fn(args || {}); window.navia.result = {done: true, ok: true, value}; return value; }
    catch (e) { window.navia.result = {done: true, ok: false, error: String((e && e.message) || e)}; throw e; }
  };

  window.navia = {result: null, benefits: wrap(benefits), statement: wrap(statement), duplicates: wrap(duplicates),
                  selfcheck: wrap(selfcheck), readback: wrap(readback), formState: wrapSync(formState)};
  return 'navia ready: benefits, statement, duplicates, selfcheck, readback, formState';
})();
