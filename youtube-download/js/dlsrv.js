// Evaluated (awaited) on the dlsrv embed page; `ytdl js` fills in __REQUEST__.
// Returns a JSON string: {url, filename} or {error, ...}. SKILL.md documents the API.
(async () => {
  const req = __REQUEST__;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const done = (o) => JSON.stringify(o);

  if (location.host !== "embed.dlsrv.online") {
    return done({ error: "wrong_page", page: location.href });
  }

  // The page solves its Cap.js proof-of-work on load and stores the session.
  let token = null;
  for (let i = 0; i < 60 && !(token = sessionStorage.getItem("__session")); i++) await sleep(500);
  if (!token) return done({ error: "no_session", hint: "the page never got a session token" });

  const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
  const call = async (method, path, body) => {
    const r = await fetch(path, { method, headers, body: body && JSON.stringify(body), cache: "no-store" });
    const text = await r.text();
    let json = null;
    try { json = JSON.parse(text); } catch {}
    return { status: r.status, json, text: text.slice(0, 300) };
  };

  const touch = await call("POST", "/api/session/touch", { videoId: req.videoId });
  if (touch.status !== 200) return done({ error: "touch_failed", status: touch.status, body: touch.text });

  const path = ["mp3", "m4a", "opus"].includes(req.format) ? "/api/download/mp3" : "/api/download/mp4";
  const request = async (body) => {
    let res;
    for (let attempt = 0; attempt < 4; attempt++) {
      await sleep(1500);
      res = await call("POST", path, body);
      if (!(res.status === 403 && /not ready/i.test(res.text))) break;
    }
    return res;
  };
  // "Request failed with status code N" is the backend's own fetch from YouTube failing.
  const refused = (res) => done({
    error: res.status === 429 ? "rate_limited"
      : /Request failed with status code/i.test(res.text) ? "upstream_refused" : "download_refused",
    status: res.status, body: res.text,
  });

  // Probe: each link's filename names what would be served; 2160 gets the video's best.
  if (req.probe) {
    const out = {};
    for (const quality of ["2160", "1080"]) {
      const res = await request({ ...req, probe: undefined, quality });
      if (res.status !== 200 || res.json?.error) return refused(res);
      const m = /\((\d+p), (\w+)\)\.\w+$/.exec(res.json?.filename || "");
      out[quality === "2160" ? "best" : "at_1080"] = m ? `${m[1]} ${m[2]}` : res.json?.filename;
    }
    return done(out);
  }

  const res = await request(req);
  if (res.status !== 200 || res.json?.error) return refused(res);

  // The page's v3 path answers with a job to poll instead of a URL; handle both.
  let result = res.json;
  if (!result.url && result.job) {
    const start = Date.now();
    while (Date.now() - start < 300000) {
      await sleep(2000);
      const p = await call("GET", `/api/v3/progress?job=${encodeURIComponent(result.job)}`);
      if (p.status === 429) continue;
      if (p.status !== 200) return done({ error: "progress_failed", status: p.status, body: p.text });
      if (p.json?.done) {
        if (!p.json.url) return done({ error: "conversion_failed", body: p.text });
        result = { ...result, url: p.json.url };
        break;
      }
    }
  }
  if (!result.url) return done({ error: "no_url", body: res.text });
  return done({ url: result.url, filename: result.filename || null, status: result.status || null });
})()
