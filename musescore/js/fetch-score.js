// Evaluate (awaited) on a musescore.com score page. Returns one JSON string:
// {ok, meta, files: {name: base64}, problems}. How each step works, and what to
// check when MuseScore changes it, is in SKILL.md under "How MuseScore serves a score".
(async () => {
  // md5 (RFC 1321) over an ASCII string, for the jmuse Authorization token.
  const md5 = (s) => {
    const k = [], r = [7, 12, 17, 22, 5, 9, 14, 20, 4, 11, 16, 23, 6, 10, 15, 21];
    for (let i = 0; i < 64; i++) k[i] = Math.floor(Math.abs(Math.sin(i + 1)) * 2 ** 32) >>> 0;
    const n = ((s.length + 8) >> 6) + 1, w = new Array(n * 16).fill(0);
    for (let i = 0; i < s.length; i++) w[i >> 2] |= s.charCodeAt(i) << ((i % 4) * 8);
    w[s.length >> 2] |= 0x80 << ((s.length % 4) * 8);
    w[n * 16 - 2] = s.length * 8;
    let [a0, b0, c0, d0] = [0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476];
    for (let j = 0; j < n * 16; j += 16) {
      let [a, b, c, d] = [a0, b0, c0, d0];
      for (let i = 0; i < 64; i++) {
        let f, g;
        if (i < 16) { f = (b & c) | (~b & d); g = i; }
        else if (i < 32) { f = (d & b) | (~d & c); g = (5 * i + 1) % 16; }
        else if (i < 48) { f = b ^ c ^ d; g = (3 * i + 5) % 16; }
        else { f = c ^ (b | ~d); g = (7 * i) % 16; }
        const t = d; d = c; c = b;
        const x = (a + f + k[i] + w[j + g]) | 0, sh = r[(i >> 4) * 4 + (i % 4)];
        b = (b + ((x << sh) | (x >>> (32 - sh)))) | 0; a = t;
      }
      a0 = (a0 + a) | 0; b0 = (b0 + b) | 0; c0 = (c0 + c) | 0; d0 = (d0 + d) | 0;
    }
    return [a0, b0, c0, d0].map((v) => [0, 8, 16, 24].map((q) => ((v >>> q) & 255).toString(16).padStart(2, '0')).join('')).join('');
  };
  const sleep = (ms) => new Promise((z) => setTimeout(z, ms));
  const hashOf = (u) => ((u || '').match(/scoredata\/g\/([0-9a-f]{40})\//) || [])[1] || null;
  const fail = (error, extra = {}) => JSON.stringify({ ok: false, error, ...extra });

  const st = window.UGAPP && UGAPP.store;
  const s = st && st.page && st.page.data && st.page.data.score;
  if (!s) return fail('not a score page: window.UGAPP.store.page.data.score is missing');
  const u = st.user || {};
  const meta = {
    id: s.id, title: s.title, pages: s.pages_count, pdf_only: !!s.is_pdf, official: !!s.is_official,
    purchased: !!s.is_purchased, parts: s.parts_names, instrumentation: (s.instrumentations || []).map((x) => x.name),
    keysig: s.keysig, measures: s.measures, duration: s.duration, page_url: location.href,
    user: { id: u.id || 0, name: u.username || null, pro: !!u.isPro, pro_plus: !!u.isProPlus },
  };

  // Page 1: the static URL the page preloads. og:image is a different render, under a different hash.
  const re0 = /\/static\/musescore\/scoredata\/g\/[0-9a-f]{40}\/score_0\.(svg|png)/;
  const cands = [...document.querySelectorAll('link[as=image]')].map((l) => l.href)
    .concat([...document.images].map((i) => i.src), performance.getEntriesByType('resource').map((e) => e.name));
  const page0 = (cands.find((x) => re0.test(x) && /score_0\.svg/.test(x)) || cands.find((x) => re0.test(x)) || '').split('@')[0];
  const h0 = hashOf(page0);
  if (!h0) return fail('no static score_0 URL on the page (no <link rel=preload as=image>; viewer not loaded?)', { meta });
  meta.hash = h0;

  // jmuse token suffix, read from the site's own bundle: md5(id + type + index + SUFFIX).slice(0, 4).
  const bundles = [...document.head.innerHTML.matchAll(/href=["'](https:\/\/musescore\.com\/static\/public\/build\/[^"']+\.js)["']/g)].map((m) => m[1]);
  const suffixes = [];
  for (const b of bundles) {
    try {
      const t = await (await fetch(b)).text();
      for (const m of t.matchAll(/"([^"]+)"\)\.substr\(0,4\)/g)) if (!suffixes.includes(m[1])) suffixes.push(m[1]);
    } catch (e) {}
  }
  if (!suffixes.includes('111010')) suffixes.push('111010'); // last known value
  meta.suffixes = suffixes;

  const jmuse = async (type, index, suffix) => {
    let out = { status: 0 };
    for (let a = 0; a < 4; a++) {
      try {
        const r = await fetch(`/api/jmuse?id=${s.id}&type=${type}&index=${index}`, { headers: { Authorization: md5(`${s.id}${type}${index}${suffix}`).slice(0, 4) } });
        let j = null; try { j = await r.json(); } catch (e) {}
        out = { status: r.status, url: (j && j.info && j.info.url) || null, error: (j && j.error && j.error.message) || null };
      } catch (e) { out = { status: 0, error: String(e) }; }
      if (out.status && out.status !== 429 && out.status < 500) break;
      await sleep(800 * (a + 1));
    }
    return out;
  };
  const wanted = [];
  for (let i = 1; i < s.pages_count; i++) wanted.push(['img', i]);
  if (!s.is_pdf) wanted.push(['midi', 0]);

  // Find the suffix the server accepts, probing with the first wanted file.
  let suffix = null, probe = null;
  if (wanted.length) {
    for (const sx of suffixes) {
      probe = await jmuse(wanted[0][0], wanted[0][1], sx);
      if (probe.status === 200) { suffix = sx; break; }
    }
    if (!suffix) return fail(`jmuse refused ${wanted[0][0]} ${wanted[0][1]} with every token suffix (last: HTTP ${probe.status} ${probe.error || ''})`, { meta, needs_login: probe.status === 401 || probe.status === 403 || probe.status === 404 });
  }
  meta.auth_suffix = suffix;

  const urls = { ['score_0.' + page0.match(/score_0\.(svg|png)/)[1]]: page0 };
  const problems = [], wrong = [];
  for (const [type, index] of wanted) {
    const v = await jmuse(type, index, suffix);
    if (v.status !== 200 || !v.url) { problems.push(`${type}${index}: HTTP ${v.status} ${v.error || ''}`.trim()); continue; }
    if (hashOf(v.url) !== h0) { wrong.push(`${type}${index}: served from ${hashOf(v.url)}, page 1 is ${h0}`); continue; }
    const ext = v.url.split('?')[0].match(/\.(\w+)$/)[1];
    urls[type === 'midi' ? 'score.mid' : `score_${index}.${ext}`] = v.url;
  }
  if (wrong.length) return fail('MuseScore served files from a different score than page 1 (decoy or wrong score); nothing returned', { meta, wrong });
  if (!s.is_pdf) {
    const sp = performance.getEntriesByType('resource').map((e) => e.name).find((n) => hashOf(n) === h0 && /space\.jsonp/.test(n));
    urls['space.jsonp'] = sp || `https://musescore.com/static/musescore/scoredata/g/${h0}/space.jsonp`;
  }

  const files = {};
  for (const [name, url] of Object.entries(urls)) {
    let r = null, err = '';
    for (let a = 0; a < 4; a++) {
      try { r = await fetch(url, { cache: 'no-store' }); if (r.ok) break; err = 'HTTP ' + r.status; }
      catch (e) { err = String(e); }
      await sleep(800 * (a + 1));
    }
    if (!r || !r.ok) { problems.push(`${name}: ${err}`); continue; }
    const b = new Uint8Array(await r.arrayBuffer());
    let bin = '';
    for (let i = 0; i < b.length; i += 8192) bin += String.fromCharCode(...b.subarray(i, i + 8192));
    files[name] = btoa(bin);
  }
  return JSON.stringify({ ok: true, meta, files, problems });
})()
