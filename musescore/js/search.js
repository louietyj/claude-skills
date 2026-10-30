// Evaluate (awaited) on a musescore.com/sheetmusic?... results page. Returns one JSON string:
// {ok, total, user, scores: [...]}, read from the page's own store.
(async () => {
  const st = window.UGAPP && UGAPP.store;
  const d = st && st.page && st.page.data;
  if (!d || !d.scores) return JSON.stringify({ ok: false, error: 'not a search page: window.UGAPP.store.page.data.scores is missing', url: location.href });
  const clean = (t) => (t || '').replace(/\[\/?b\]/g, '');
  return JSON.stringify({
    ok: true, url: location.href, total: d.pagination && d.pagination.totalCount,
    user: (st.user && st.user.username) || null,
    scores: d.scores.map((s) => ({
      id: s.id, title: clean(s.title), url: s.href, official: !!s.is_official, pdf_only: !!s.is_pdf,
      by: s.user && s.user.name, instrumentation: (s.instrumentations || []).map((x) => x.name).join(' / '),
      parts: (s.parts_names || []).join(' / '), pages: s.pages_count, duration: s.duration,
      key: s.key_signature, time: s.time_signature, rating: s.rating && s.rating.rating,
      votes: s.rating && s.rating.count, views: s.hits, purchased: !!s.is_purchased,
    })),
  });
})()
