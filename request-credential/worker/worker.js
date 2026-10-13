// request-credential relay: Claude files a request for a site's login, this
// pushes an opaque request id to the paired phone over FCM, and the phone's
// cred-bridge app fetches the details here. The credential itself never passes
// through: the app seals it to the request-text link Claude opened, whose key
// exists only in Claude's sandbox.
//
// Claude (Authorization: Bearer CLI_TOKEN):
//   POST  /pair                a one-time 6-digit code for pairing the phone
//   POST  /request             {site, reason, url, ttl_s?} -> {rid}; pushes it
//   GET   /request/:rid        {status, site, ...}; never the url
//   PATCH /request/:rid        {url}: request-text reconnected under a new one
// Device (Authorization: Bearer DEVICE_TOKEN):
//   POST  /pair/claim          {code, fcm_token} -> {k}; the code is the only gate
//   PUT   /device              {fcm_token} after FCM rotates it
//   GET   /device/request/:rid[?seen=opened]  {site, reason, url, expires_at, status};
//                              marks delivered, or opened when the screen asks
//   POST  /device/request/:rid/{declined|sent}
// Anyone:
//   GET   /health              which secrets are set, never their values

const PAIR_TTL_MS = 5 * 60_000;
const PAIR_TRIES = 5;
const PAIR_FAILS_PER_HOUR = 20;
const TTL_DEFAULT_S = 600;
const TTL_MAX_S = 1800;
const KEEP_MS = 24 * 3600_000;
const REASON_MAX = 200;
const URL_MAX = 500;
const SITE_RE = /^(?=.{3,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/;
const RID_RE = /^[a-f0-9]{16}$/;
const FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging";

const json = (o, c = 200) =>
  new Response(JSON.stringify(o), { status: c, headers: { "content-type": "application/json" } });

const b64u = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes)))
  .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

// The states a request moves through before it ends, in order. They lapse to
// expired; the final states (sent, declined, undelivered) stick.
const OPEN = ["pending", "delivered", "opened"];

function statusOf(r, now) {
  return OPEN.includes(r.status) && now >= r.exp ? "expired" : r.status;
}

// ---------------------------------------------------------------------------
// FCM HTTP v1, authenticated as the service account in FCM_SA_JSON.

async function accessToken(env, cache) {
  const now = Math.floor(Date.now() / 1000);
  if (cache.token && cache.exp - 300 > now) return cache.token;

  const sa = JSON.parse(env.FCM_SA_JSON);
  const tokenUrl = env.OAUTH_TOKEN_URL || sa.token_uri;
  const enc = new TextEncoder();
  const head = b64u(enc.encode(JSON.stringify({ alg: "RS256", typ: "JWT" })));
  const claims = b64u(enc.encode(JSON.stringify({
    iss: sa.client_email, scope: FCM_SCOPE, aud: tokenUrl, iat: now, exp: now + 3600 })));
  const pem = sa.private_key.replace(/-----[^-]+-----/g, "").replace(/\s+/g, "");
  const der = Uint8Array.from(atob(pem), (c) => c.charCodeAt(0));
  const key = await crypto.subtle.importKey("pkcs8", der,
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, enc.encode(`${head}.${claims}`));

  const res = await fetch(tokenUrl, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer",
      assertion: `${head}.${claims}.${b64u(sig)}`,
    }),
  });
  if (!res.ok) throw new Error(`OAuth token exchange: HTTP ${res.status} ${(await res.text()).slice(0, 300)}`);
  const out = await res.json();
  cache.token = out.access_token;
  cache.exp = now + (out.expires_in ?? 3600);
  return cache.token;
}

// -> null on success, else a short reason Claude can relay.
async function push(env, cache, fcmToken, rid, ttlS) {
  const sa = JSON.parse(env.FCM_SA_JSON);
  const base = env.FCM_URL || "https://fcm.googleapis.com";
  const res = await fetch(`${base}/v1/projects/${sa.project_id}/messages:send`, {
    method: "POST",
    headers: { authorization: `Bearer ${await accessToken(env, cache)}`, "content-type": "application/json" },
    body: JSON.stringify({ message: {
      token: fcmToken,
      data: { rid },
      android: { priority: "high", ttl: `${ttlS}s` },
    } }),
  });
  if (res.ok) return null;
  const body = await res.text();
  if (res.status === 404 || body.includes("UNREGISTERED")) {
    return "the phone's push registration is stale; open the cred-bridge app once to refresh it";
  }
  return `FCM: HTTP ${res.status} ${body.slice(0, 300)}`;
}

// ---------------------------------------------------------------------------
// One instance holds everything: the paired device, the pairing code, and the
// requests, keyed r:<rid>.

export class Hub {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
    this.oauth = {};
  }

  async fetch(req) {
    const { pathname } = new URL(req.url);
    const body = req.method === "GET" ? {} : await req.json().catch(() => ({}));
    const now = Date.now();
    const st = this.ctx.storage;

    // One pairing code at a time: 6 digits, 5 minutes, 5 tries, and at most
    // PAIR_FAILS_PER_HOUR wrong guesses across all codes, so guessing is hopeless.
    if (pathname === "/pair/new") {
      const code = String(100000 + (crypto.getRandomValues(new Uint32Array(1))[0] % 900000));
      await st.put("pair", { code, exp: now + PAIR_TTL_MS, tries: 0 });
      return json({ code, expires_s: PAIR_TTL_MS / 1000 });
    }

    if (pathname === "/pair/claim") {
      const hour = Math.floor(now / 3600_000);
      const fails = (await st.get("pairfails")) ?? { hour, n: 0 };
      if (fails.hour !== hour) { fails.hour = hour; fails.n = 0; }
      if (fails.n >= PAIR_FAILS_PER_HOUR) return json({ ok: false, error: "too many wrong codes; try again in an hour" }, 429);
      const p = await st.get("pair");
      if (p && p.exp > now && String(body.code ?? "").trim() === p.code) {
        if (!body.fcm_token) return json({ ok: false, error: "missing fcm_token" }, 400);
        await st.delete("pair");
        await st.put("device", { fcm_token: String(body.fcm_token), paired: now });
        return json({ ok: true });
      }
      fails.n += 1;
      await st.put("pairfails", fails);
      if (p) {
        p.tries += 1;
        if (p.tries >= PAIR_TRIES || p.exp <= now) await st.delete("pair");
        else await st.put("pair", p);
      }
      return json({ ok: false, error: p && p.exp > now ? "wrong code" : "no code waiting; ask Claude for a new one" }, 401);
    }

    if (pathname === "/device") {
      if (!body.fcm_token) return json({ error: "missing fcm_token" }, 400);
      const d = await st.get("device");
      if (!d) return json({ error: "not paired" }, 409);
      await st.put("device", { ...d, fcm_token: String(body.fcm_token) });
      return json({ ok: true });
    }

    if (pathname === "/request/new") {
      const site = String(body.site ?? "").trim().toLowerCase();
      const reason = String(body.reason ?? "").trim();
      const url = String(body.url ?? "");
      const ttlS = Math.min(Math.max(Number(body.ttl_s) || TTL_DEFAULT_S, 30), TTL_MAX_S);
      if (!SITE_RE.test(site)) return json({ error: "site must be a bare domain like chase.com" }, 400);
      if (!reason || reason.length > REASON_MAX) return json({ error: `reason must be 1-${REASON_MAX} characters` }, 400);
      if (!/^https?:\/\//.test(url) || url.length > URL_MAX) return json({ error: "url must be the request-text link" }, 400);
      const d = await st.get("device");
      if (!d) return json({ error: "no phone paired; run `request-credential pair`" }, 409);

      for (const [key, r] of await st.list({ prefix: "r:" })) {
        if (now - r.created > KEEP_MS) await st.delete(key);
      }
      const rid = [...crypto.getRandomValues(new Uint8Array(8))].map((b) => b.toString(16).padStart(2, "0")).join("");
      const r = { rid, site, reason, url, status: "pending", created: now, exp: now + ttlS * 1000 };
      await st.put(`r:${rid}`, r);

      const failed = await push(this.env, this.oauth, d.fcm_token, rid, ttlS).catch((e) => String(e.message ?? e));
      if (failed) {
        await st.put(`r:${rid}`, { ...r, status: "undelivered" });
        return json({ error: failed, rid }, 502);
      }
      return json({ rid, expires_at: r.exp });
    }

    const m = pathname.match(/^\/(cli|device)\/([a-f0-9]+)(?:\/(\w+))?$/);
    if (!m || !RID_RE.test(m[2])) return json({ error: "not found" }, 404);
    const [, who, rid, action] = m;
    const r = await st.get(`r:${rid}`);
    if (!r) return json({ error: "no such request" }, 404);
    const status = statusOf(r, now);

    if (who === "cli") {
      if (action === "url") {
        if (!OPEN.includes(status)) return json({ error: `request is ${status}` }, 409);
        const url = String(body.url ?? "");
        if (!/^https?:\/\//.test(url) || url.length > URL_MAX) return json({ error: "url must be the request-text link" }, 400);
        await st.put(`r:${rid}`, { ...r, url });
        return json({ ok: true });
      }
      return json({ rid, site: r.site, reason: r.reason, status, created: r.created, expires_at: r.exp,
        ...(r.ended ? { ended: r.ended } : {}) });
    }

    if (!action) {
      // The push handler fetches as "delivered", the request screen as "opened"; never backwards.
      const seen = new URL(req.url).searchParams.get("seen") === "opened" ? "opened" : "delivered";
      let shown = status;
      if (OPEN.includes(status) && OPEN.indexOf(seen) > OPEN.indexOf(status)) {
        shown = seen;
        await st.put(`r:${rid}`, { ...r, status: seen });
      }
      return json({ rid, site: r.site, reason: r.reason, url: r.url, expires_at: r.exp, status: shown });
    }
    if (action === "declined" || action === "sent") {
      if (!OPEN.includes(status)) return json({ error: `request is ${status}` }, 409);
      await st.put(`r:${rid}`, { ...r, status: action, ended: now });
      return json({ ok: true });
    }
    return json({ error: "not found" }, 404);
  }
}

// ---------------------------------------------------------------------------

function bearer(req) {
  return (req.headers.get("authorization") ?? "").replace(/^Bearer\s+/i, "");
}

export default {
  async fetch(req, env) {
    const path = new URL(req.url).pathname.replace(/\/+$/, "") || "/";
    const hub = env.HUB.get(env.HUB.idFromName("main"));
    const forward = async (to, method = req.method) => hub.fetch(`https://hub${to}`, {
      method, body: method === "GET" ? undefined : await req.text(),
    });

    if (path === "/health") {
      return json({ ok: true, secrets: {
        CLI_TOKEN: !!env.CLI_TOKEN, DEVICE_TOKEN: !!env.DEVICE_TOKEN, FCM_SA_JSON: !!env.FCM_SA_JSON,
      } });
    }

    // The code is this route's only gate; the app keeps the token it returns.
    if (path === "/pair/claim" && req.method === "POST") {
      if (!env.DEVICE_TOKEN) return json({ ok: false, error: "relay has no DEVICE_TOKEN" }, 500);
      const out = await forward("/pair/claim");
      const res = await out.json();
      return res.ok ? json({ ok: true, k: env.DEVICE_TOKEN }) : json(res, out.status);
    }

    // An unset secret closes its gate rather than opening it.
    const token = bearer(req);
    const isDevice = path === "/device" || path.startsWith("/device/");
    const secret = isDevice ? env.DEVICE_TOKEN : env.CLI_TOKEN;
    if (!secret || token !== secret) return json({ error: "unauthorized" }, 401);

    if (path === "/pair" && req.method === "POST") return forward("/pair/new");
    if (path === "/request" && req.method === "POST") {
      if (!env.FCM_SA_JSON) return json({ error: "relay has no FCM_SA_JSON" }, 500);
      return forward("/request/new");
    }
    if (path === "/device" && req.method === "PUT") return forward("/device", "POST");

    let m = path.match(/^\/request\/([^/]+)$/);
    if (m && req.method === "GET") return forward(`/cli/${m[1]}`);
    if (m && req.method === "PATCH") return forward(`/cli/${m[1]}/url`, "POST");

    m = path.match(/^\/device\/request\/([^/]+)$/);
    if (m && req.method === "GET") return forward(`/device/${m[1]}${new URL(req.url).search}`);
    m = path.match(/^\/device\/request\/([^/]+)\/(declined|sent)$/);
    if (m && req.method === "POST") return forward(`/device/${m[1]}/${m[2]}`);

    return json({ error: "not found" }, 404);
  },
};
