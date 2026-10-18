// live-listen relay: a phone or watch streams mic audio here, a Durable Object
// relays it to Soniox and assembles speaker turns, and Claude long-polls the
// turns from a sandbox that can be recycled without losing any. Deepgram does
// the optional full-file pass afterwards.
//
// Soniox for live because on watch-mic audio Deepgram's streaming diarization
// put every word on one speaker, while Soniox split the same bytes correctly.
// Neither retains anything: Soniox real-time stores nothing; Deepgram gets
// mip_opt_out.
//
// Device (DEVICE_TOKEN as ?k=, kept by the page in localStorage):
//   GET  /            the page
//   POST /pair/claim  {code} -> the device token; the code is the only gate
//   GET  /armed       sessions waiting to start, and live ones to rejoin
//   GET  /device?id   websocket: audio in; notes, restarts and the end out
// Claude (CLI_TOKEN as ?token=):
//   POST /pair        a one-time 6-digit code for pairing a device
//   POST /session     create and arm a session -> {id}
//   GET  /watch?id    long-poll for entries past ?since
//   POST /note?id     a short note for the device (vibrates the watch)
//   POST /stop?id     end the session
//   GET  /transcript?id[&batch=1]
//   GET  /audio?id&seg  an archived segment's webm, until the batch pass deletes it
//   GET  /status?id
// Anyone:
//   GET  /health      which secrets are set, never their values
import PAGE from "./page.html";

const ID_ALPHABET = "ACDEFGHJKLMNPQRTUVWXY34679";  // no 0/O, 1/I, 2/Z, 5/S, 8/B
const ID_RE = /^[ACDEFGHJKLMNPQRTUVWXY34679]{4}$/;
const ARMED_TTL_MS = 15 * 60_000;
const DEVICE_GONE_MS = 5 * 60_000;
const KEEP_MS = 24 * 3600_000;
const KEEPALIVE_MS = 5000;
const ARCHIVE_ROW_BYTES = 120_000;   // ~30s of opus per row: rows written are capped per day
const NOTE_MAX = 120;
const TURN_SPLIT_S = 12;
const PAIR_TTL_MS = 5 * 60_000;
const PAIR_TRIES = 5;
const PAIR_FAILS_PER_HOUR = 20;
const WATCH_MAX_S = 590;
const MAX_ENTRIES = 400;
const PENDING_AUDIO_MAX = 200;      // chunks held while the transcriber's socket opens

const SONIOX_MODEL = "stt-rt-v5";
const BATCH_PARAMS = {
  model: "nova-3", diarize: "true", smart_format: "true", punctuate: "true",
  mip_opt_out: "true",
};

const json = (o, c = 200) =>
  new Response(JSON.stringify(o), { status: c, headers: { "content-type": "application/json" } });
const pad = (n) => String(n).padStart(6, "0");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function deepgramUrl(base, params, keyterms) {
  const u = new URL(base);
  for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v);
  for (const t of keyterms ?? []) u.searchParams.append("keyterm", t);
  return u.toString();
}

function concat(parts) {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.byteLength, 0));
  let off = 0;
  for (const p of parts) { out.set(p, off); off += p.byteLength; }
  return out;
}

function mmss(sec) {
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  const two = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${two(m)}:${two(r)}` : `${two(m)}:${two(r)}`;
}

// ---------------------------------------------------------------------------
// One instance ("main") indexes every session, so a single bookmark on the
// watch can find whatever Claude armed without being sent a URL.

export class Registry {
  constructor(ctx) { this.ctx = ctx; }

  async fetch(req) {
    const { pathname } = new URL(req.url);
    const now = Date.now();

    if (pathname === "/claim") {
      const { label } = await req.json();
      for (let tries = 0; tries < 50; tries++) {
        const id = Array.from(crypto.getRandomValues(new Uint8Array(4)),
                              (b) => ID_ALPHABET[b % ID_ALPHABET.length]).join("");
        if (await this.ctx.storage.get(`s:${id}`)) continue;
        await this.ctx.storage.put(`s:${id}`, { id, label, state: "armed", at: now });
        return json({ id });
      }
      return json({ error: "no free id" }, 500);
    }

    // One pairing code at a time: 6 digits, 5 minutes, 5 tries, and at most
    // PAIR_FAILS_PER_HOUR wrong guesses across all codes, so guessing is hopeless.
    if (pathname === "/pair/new") {
      const code = String(100000 + (crypto.getRandomValues(new Uint32Array(1))[0] % 900000));
      await this.ctx.storage.put("pair", { code, exp: now + PAIR_TTL_MS, tries: 0 });
      return json({ code, expires_s: PAIR_TTL_MS / 1000 });
    }

    if (pathname === "/pair/claim") {
      const { code } = await req.json().catch(() => ({}));
      const hour = Math.floor(now / 3600_000);
      const fails = (await this.ctx.storage.get("pairfails")) ?? { hour, n: 0 };
      if (fails.hour !== hour) { fails.hour = hour; fails.n = 0; }
      if (fails.n >= PAIR_FAILS_PER_HOUR) return json({ ok: false, error: "too many wrong codes; try again in an hour" }, 429);
      const p = await this.ctx.storage.get("pair");
      if (p && p.exp > now && String(code ?? "").trim() === p.code) {
        await this.ctx.storage.delete("pair");
        return json({ ok: true });
      }
      fails.n += 1;
      await this.ctx.storage.put("pairfails", fails);
      if (p) {
        p.tries += 1;
        if (p.tries >= PAIR_TRIES || p.exp <= now) await this.ctx.storage.delete("pair");
        else await this.ctx.storage.put("pair", p);
      }
      return json({ ok: false, error: p && p.exp > now ? "wrong code" : "no code waiting; ask Claude for a new one" }, 401);
    }

    if (pathname === "/put") {
      const s = await req.json();
      const prev = (await this.ctx.storage.get(`s:${s.id}`)) ?? {};
      await this.ctx.storage.put(`s:${s.id}`, { ...prev, ...s, at: now });
      return json({ ok: true });
    }

    if (pathname === "/list") {
      const out = [];
      for (const [key, s] of await this.ctx.storage.list({ prefix: "s:" })) {
        const age = now - s.at;
        if (s.state === "live" || (s.state === "armed" && age < ARMED_TTL_MS)) {
          out.push({ id: s.id, label: s.label, state: s.state, age_s: Math.round(age / 1000) });
        } else if (age > KEEP_MS) {
          await this.ctx.storage.delete(key);
        }
      }
      out.sort((a, b) => a.age_s - b.age_s);
      return json({ sessions: out });
    }
    return json({ error: "not found" }, 404);
  }
}

// ---------------------------------------------------------------------------
// One instance per session. Everything Claude reads is in storage as numbered
// entries, so an eviction, a redeploy or a dropped device costs at most the
// turn being spoken, never the transcript.

export class Session {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
    this.meta = null;
    // Device facts Claude reads (ui, hidden_since, disconnected_at) live in meta:
    // an object restart must not turn "gone 3 minutes" into "fine".
    this.device = null;          // current device websocket
    this.stt = null;             // current transcriber websocket
    this.sttOpening = false;
    this.quickFails = 0;
    this.pendingAudio = [];
    this.cur = null;             // turn being assembled: {spk, t, text}
    this.pending = null;         // words still being recognised, never stored
    this.lastAudioAt = 0;
    this.arch = null;            // {seg, part, chunks, bytes}
    this.keepalive = null;
  }

  async load() {
    if (this.meta) return this.meta;
    this.meta = (await this.ctx.storage.get("meta")) ?? null;
    // A fresh instance with a live session means the object restarted (a
    // deploy, or Cloudflare moving it): the device socket died with the old
    // one and no close handler ran to say so.
    if (this.meta?.state === "live" && !this.meta.disconnected_at) {
      this.meta.disconnected_at = Date.now();
      await this.addEntry("event", { text: "relay restarted; the device must reconnect (no audio until it does)" });
      await this.ctx.storage.setAlarm(Date.now() + DEVICE_GONE_MS);
    }
    return this.meta;
  }

  async saveMeta() { await this.ctx.storage.put("meta", this.meta); }

  elapsed() {
    return this.meta?.started_at ? (Date.now() - this.meta.started_at) / 1000 : 0;
  }

  live() { return this.meta?.state === "live"; }

  registry(path, body) {
    const stub = this.env.REGISTRY.get(this.env.REGISTRY.idFromName("main"));
    return stub.fetch(`https://registry${path}`, { method: "POST", body: JSON.stringify(body) });
  }

  toDevice(msg) {
    try { this.device?.send(JSON.stringify(msg)); } catch {}
  }

  async addEntry(kind, fields) {
    const e = { n: ++this.meta.seq, kind, t: Math.round(this.elapsed()), ...fields };
    // Claude's own notes are logged but are not news: they must not wake its watch.
    if (kind !== "note") this.meta.news_seq = e.n;
    await this.ctx.storage.put(`e:${pad(e.n)}`, e);
    await this.saveMeta();
    return e;
  }

  // --- turns -------------------------------------------------------------

  async flushTurn() {
    const cur = this.cur;
    this.cur = null;
    const text = cur?.text.trim();
    if (text) await this.addEntry("turn", { spk: cur.spk, t: Math.round(cur.t), text });
  }

  // Soniox sends each final token once and every non-final token on every
  // message; tokens carry their own spacing, and "<end>" marks an utterance's
  // end. A turn also closes at the first sentence end past TURN_SPLIT_S, or a
  // monologue would only be delivered once it stops.
  async onTranscript(raw, offset, conn) {
    let m;
    try { m = JSON.parse(raw); } catch { return; }
    if (m.error_code) {
      conn.error = `${m.error_code} ${m.error_message ?? ""}`.trim();
      return;
    }
    const tokens = m.tokens ?? [];
    if (tokens.length) this.quickFails = 0;
    let pending = "", pendingSpk = null;
    for (const tok of tokens) {
      if (!tok.is_final) {
        if (tok.text !== "<end>") { pending += tok.text; pendingSpk ??= tok.speaker; }
        continue;
      }
      if (tok.text === "<end>") { await this.flushTurn(); continue; }
      const spk = Number(tok.speaker ?? 0) || 0;
      if (this.cur && this.cur.spk !== spk) await this.flushTurn();
      const at = offset + (tok.start_ms ?? 0) / 1000;
      if (!this.cur) this.cur = { spk, t: at, text: "" };
      this.cur.text += tok.text;
      if (at - this.cur.t >= TURN_SPLIT_S && /[.?!]$/.test(tok.text)) await this.flushTurn();
    }
    this.pending = pending ? { spk: Number(pendingSpk ?? 0) || 0, text: pending } : null;
  }

  // The open turn's finished words plus those still being recognised.
  tail() {
    const text = ((this.cur?.text ?? "") + (this.pending?.text ?? "")).trim();
    return text ? { spk: this.cur?.spk ?? this.pending.spk, text } : null;
  }

  // --- transcriber -----------------------------------------------------------

  // A segment is one MediaRecorder run: one webm header, one transcriber
  // stream. A stream resumed mid-container is not reliably decodable, so every
  // reconnect on either side starts a new segment.
  async startSegment() {
    // Synchronous up to the first await: the header chunk arrives right behind
    // the `seg` message and must land in pendingAudio.
    this.closeTranscriber();
    this.pendingAudio = [];
    this.sttOpening = true;
    const seg = { n: this.meta.segs.length, start_s: this.elapsed() };
    this.meta.segs.push(seg);
    await this.saveMeta();

    let ws;
    try {
      const resp = await fetch(this.env.SONIOX_URL, { headers: { Upgrade: "websocket" } });
      ws = resp.webSocket;
      if (!ws) {
        this.sttOpening = false;
        await this.addEntry("event", { text: `transcriber refused the connection (HTTP ${resp.status}); no transcript until the device reconnects` });
        return;
      }
    } catch (e) {
      this.sttOpening = false;
      await this.addEntry("event", { text: `transcriber unreachable (${e}); no transcript until the device reconnects` });
      return;
    }
    ws.accept();
    const conn = { ws, opened: Date.now(), error: null };
    ws.send(JSON.stringify({
      api_key: this.env.SONIOX_API_KEY, model: SONIOX_MODEL, audio_format: "auto",
      language_hints: ["en"], enable_speaker_diarization: true, enable_endpoint_detection: true,
      ...(this.meta.keyterms.length ? { context: { terms: this.meta.keyterms } } : {}),
    }));
    this.stt = ws;
    this.sttOpening = false;
    ws.addEventListener("message", (m) => this.onTranscript(m.data, seg.start_s, conn));
    ws.addEventListener("close", (c) => this.onTranscriberClosed(conn, c.code));
    ws.addEventListener("error", () => this.onTranscriberClosed(conn, 1006));
    for (const chunk of this.pendingAudio) ws.send(chunk);
    this.pendingAudio = [];
    clearInterval(this.keepalive);
    this.keepalive = setInterval(() => {
      if (this.stt === ws && Date.now() - this.lastAudioAt > 3000) {
        try { ws.send(JSON.stringify({ type: "keepalive" })); } catch {}
      }
    }, KEEPALIVE_MS);
  }

  // An empty frame makes Soniox send its last finals and close; the old
  // listener still lands them. Resolves on close (or after 3s), for end().
  closeTranscriber() {
    const ws = this.stt;
    this.stt = null;
    clearInterval(this.keepalive);
    if (!ws) return Promise.resolve();
    return new Promise((resolve) => {
      const done = () => { clearTimeout(timer); resolve(); };
      const timer = setTimeout(() => { try { ws.close(1000); } catch {} resolve(); }, 3000);
      ws.addEventListener("close", done);
      try { ws.send(""); } catch { done(); }
    });
  }

  async onTranscriberClosed(conn, code) {
    if (this.stt !== conn.ws) return;        // one we closed on purpose
    this.stt = null;
    clearInterval(this.keepalive);
    await this.flushTurn();
    if (!this.live() || !this.device) return;
    const why = conn.error ?? `ws ${code}`;
    // An immediate failure (bad key) would recur on every restart; stop looping.
    if (Date.now() - conn.opened < 5000 && ++this.quickFails >= 3) {
      await this.addEntry("event", { text: `transcriber keeps failing (${why}); no transcript until the device reconnects` });
      return;
    }
    await this.addEntry("event", { text: `transcriber dropped (${why}); restarting the recorder` });
    this.toDevice({ type: "restart" });       // the page answers with a fresh segment
  }

  // --- audio and archive -----------------------------------------------------

  async onAudio(chunk) {
    this.lastAudioAt = Date.now();
    // Forward before any await, so chunks reach the transcriber in arrival order.
    if (this.stt) {
      try { this.stt.send(chunk); } catch {}
    } else if (this.sttOpening && this.pendingAudio.length < PENDING_AUDIO_MAX) {
      this.pendingAudio.push(chunk);
    }
    if (this.meta.archive) await this.archive(chunk);
  }

  async archive(chunk) {
    const seg = this.meta.segs.length - 1;
    if (seg < 0) return;
    if (this.arch && this.arch.seg !== seg) await this.flushArchive();
    if (!this.arch) this.arch = { seg, part: 0, chunks: [], bytes: 0 };
    this.arch.chunks.push(new Uint8Array(chunk));
    this.arch.bytes += chunk.byteLength;
    if (this.arch.bytes >= ARCHIVE_ROW_BYTES) await this.flushArchive(true);
  }

  async flushArchive(sameSegment = false) {
    const a = this.arch;
    if (!a || !a.bytes) { if (!sameSegment) this.arch = null; return; }
    await this.ctx.storage.put(`a:${pad(a.seg)}:${pad(a.part)}`, concat(a.chunks));
    this.arch = sameSegment ? { seg: a.seg, part: a.part + 1, chunks: [], bytes: 0 } : null;
  }

  // --- lifecycle -------------------------------------------------------------

  async end(reason) {
    if (!this.meta || this.meta.state === "ended") return;
    await this.closeTranscriber();          // its last finals land before the end entry
    await this.flushTurn();
    await this.flushArchive();
    this.meta.state = "ended";
    this.meta.ended_at = Date.now();
    this.meta.end_reason = reason;
    await this.addEntry("event", { text: `session ended: ${reason}` });
    this.toDevice({ type: "ended", reason });
    await this.registry("/put", { id: this.meta.id, state: "ended" });
    await this.ctx.storage.setAlarm(Date.now() + KEEP_MS);
  }

  async alarm() {
    if (!(await this.load())) return;
    const now = Date.now();
    if (this.meta.state === "armed" && now - this.meta.created_at >= ARMED_TTL_MS) {
      this.meta.state = "expired";
      await this.saveMeta();
      await this.registry("/put", { id: this.meta.id, state: "expired" });
      await this.ctx.storage.setAlarm(now + KEEP_MS);
    } else if (this.live() && !this.device && this.meta.disconnected_at &&
               now - this.meta.disconnected_at >= DEVICE_GONE_MS) {
      await this.end(`the device was gone for ${Math.round(DEVICE_GONE_MS / 60000)} minutes`);
    } else if ((this.meta.state === "ended" || this.meta.state === "expired") &&
               now - (this.meta.ended_at ?? this.meta.created_at) >= KEEP_MS) {
      await this.ctx.storage.deleteAll();
      this.meta = null;
    }
  }

  // --- device socket -----------------------------------------------------------

  async acceptDevice() {
    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    server.accept();

    const old = this.device;
    this.device = server;
    if (old) { try { old.close(4000, "replaced by a newer connection"); } catch {} }

    if (this.meta.state === "armed") {
      this.meta.state = "live";
      this.meta.started_at = Date.now();
      await this.saveMeta();
      await this.registry("/put", { id: this.meta.id, state: "live" });
      await this.addEntry("event", { text: "recording started" });
    } else if (this.meta.disconnected_at) {
      const gone = Math.round((Date.now() - this.meta.disconnected_at) / 1000);
      await this.addEntry("event", { text: `device reconnected after ${gone}s; nothing was heard in between` });
    }
    this.meta.disconnected_at = null;
    this.meta.hidden_since = null;
    await this.saveMeta();

    server.addEventListener("message", (m) => {
      if (this.device !== server) return;
      if (typeof m.data !== "string") return this.onAudio(m.data);
      let msg;
      try { msg = JSON.parse(m.data); } catch { return; }
      return this.onDeviceMessage(msg);
    });
    const gone = async () => {
      try { server.close(1000); } catch {}
      if (this.device !== server) return;
      this.device = null;
      this.closeTranscriber();
      await this.flushTurn();
      if (!this.live()) return;
      this.meta.disconnected_at = Date.now();
      await this.addEntry("event", { text: "device disconnected; no audio until it reconnects" });
      await this.ctx.storage.setAlarm(Date.now() + DEVICE_GONE_MS);
    };
    server.addEventListener("close", gone);
    server.addEventListener("error", gone);
    return new Response(null, { status: 101, webSocket: client });
  }

  async onDeviceMessage(msg) {
    if (msg.type === "hello") { this.meta.ui = msg.ui ?? null; return this.saveMeta(); }
    if (msg.type === "seg") return this.startSegment();
    if (msg.type === "stop") return this.end("stopped on the device");
    if (msg.type === "vis") {
      const who = this.meta.ui ?? "device";
      if (msg.state === "hidden" && this.meta.hidden_since == null) {
        this.meta.hidden_since = Date.now();
        await this.addEntry("event", { text: `${who} page left the screen: the mic is OFF (a watch keeps ~5s) until it is back` });
      } else if (msg.state === "visible" && this.meta.hidden_since != null) {
        const s = Math.round((Date.now() - this.meta.hidden_since) / 1000);
        this.meta.hidden_since = null;
        await this.addEntry("event", { text: `${who} page back on screen after ${s}s; nothing was heard in between` });
      }
    }
  }

  // --- what Claude reads ---------------------------------------------------------

  deviceStatus() {
    const now = Date.now();
    const s = (t) => (t ? Math.round((now - t) / 1000) : null);
    return {
      connected: !!this.device,
      ui: this.meta.ui ?? null,
      hidden_s: s(this.meta.hidden_since),
      disconnected_s: s(this.meta.disconnected_at),
      last_audio_s: s(this.lastAudioAt),
      transcriber: this.stt ? "open" : this.sttOpening ? "opening" : "closed",
    };
  }

  async entriesAfter(since, limit = MAX_ENTRIES) {
    const rows = await this.ctx.storage.list({ prefix: "e:", start: `e:${pad(since + 1)}`, limit });
    return [...rows.values()];
  }

  async watch(q, signal) {
    const since = Math.max(0, Number(q.get("since")) || 0);
    const interval = Math.max(0, Number(q.get("interval") ?? 10)) * 1000;
    const wait = Math.min(WATCH_MAX_S, Math.max(1, Number(q.get("wait")) || 90)) * 1000;
    const t0 = Date.now();
    const newsSeq = () => this.meta.news_seq ?? this.meta.seq;
    const backlog = newsSeq() > since;

    while (true) {
      const over = this.meta.state === "ended" || this.meta.state === "expired";
      const news = newsSeq() > since;
      const elapsed = Date.now() - t0;
      let event = null;
      if (over && !news) event = "ended";
      else if (news && (backlog || over || elapsed >= interval)) event = over ? "ended" : "transcript";
      else if (elapsed >= wait || signal?.aborted) event = "idle";
      if (event) {
        const entries = await this.entriesAfter(since);
        return json({
          event, id: this.meta.id, state: this.meta.state, label: this.meta.label,
          elapsed_s: Math.round(this.elapsed()),
          entries,
          last: entries.length ? entries.at(-1).n : since,
          more: entries.length ? entries.at(-1).n < this.meta.seq : false,
          tail: this.live() ? this.tail() : null,
          device: this.deviceStatus(),
          ...(over ? { end_reason: this.meta.end_reason ?? this.meta.state } : {}),
        });
      }
      await sleep(250);
    }
  }

  async note(body) {
    const text = String(body?.text ?? "").trim();
    if (!text) return json({ ok: false, error: "empty note" }, 400);
    if (text.length > NOTE_MAX) {
      return json({ ok: false, error: `note is ${text.length} chars; ${NOTE_MAX} at most` }, 400);
    }
    if (!this.live()) return json({ ok: false, error: `session is ${this.meta.state}` }, 409);
    const delivered = !!this.device;
    this.toDevice({ type: "note", text });
    const e = await this.addEntry("note", { text, delivered });
    return json({ ok: true, delivered, n: e.n });
  }

  async batch() {
    if (!this.meta.archive) return json({ error: "audio was not archived for this session (start --archive)" }, 400);
    const done = await this.ctx.storage.get("batch");
    if (done) return json(done);
    // Keep the part counter: resetting it mid-segment would overwrite part 0.
    if (this.live()) await this.flushArchive(true);

    const words = [];
    for (const seg of this.meta.segs) {
      const rows = await this.ctx.storage.list({ prefix: `a:${pad(seg.n)}:` });
      const parts = [...rows.values()];
      if (!parts.length) continue;
      const resp = await fetch(deepgramUrl(this.env.DEEPGRAM_URL, BATCH_PARAMS, this.meta.keyterms), {
        method: "POST",
        headers: { Authorization: `Token ${this.env.DEEPGRAM_API_KEY}`, "content-type": "audio/webm" },
        body: concat(parts),
      });
      if (!resp.ok) {
        return json({ error: `Deepgram batch returned ${resp.status}: ${(await resp.text()).slice(0, 300)}`, segment: seg.n }, 502);
      }
      const data = await resp.json();
      for (const w of data?.results?.channels?.[0]?.alternatives?.[0]?.words ?? []) {
        words.push({ seg: seg.n, spk: w.speaker ?? 0, t: seg.start_s + w.start,
                     text: w.punctuated_word ?? w.word });
      }
    }

    // Speaker numbers are per segment, so each segment opens its own blocks.
    const out = [];
    let last = null;
    for (const w of words) {
      const key = `${w.seg}:${w.spk}`;
      if (key !== last) {
        if (last !== null) out.push("");
        const tag = this.meta.segs.length > 1 ? `SPEAKER_${w.spk} seg${w.seg}` : `SPEAKER_${w.spk}`;
        out.push(`[${tag}] ${mmss(w.t)}`, w.text);
        last = key;
      } else {
        out[out.length - 1] += " " + w.text;
      }
    }
    const result = { id: this.meta.id, label: this.meta.label, segments: this.meta.segs.length,
                     transcript: out.join("\n") };
    if (!this.live()) {                        // while live, more audio is coming
      await this.ctx.storage.put("batch", result);
      const audioKeys = [...(await this.ctx.storage.list({ prefix: "a:" })).keys()];
      for (let i = 0; i < audioKeys.length; i += 128) await this.ctx.storage.delete(audioKeys.slice(i, i + 128));
      result.audio_deleted = true;
    }
    return json(result);
  }

  async fetch(req) {
    const url = new URL(req.url);
    const q = url.searchParams;
    const path = url.pathname;

    if (path === "/create") {
      const b = await req.json();
      this.meta = {
        id: b.id, label: b.label ?? "", keyterms: b.keyterms ?? [], archive: !!b.archive,
        state: "armed", created_at: Date.now(), started_at: null, ended_at: null,
        end_reason: null, seq: 0, segs: [],
      };
      await this.saveMeta();
      await this.ctx.storage.setAlarm(Date.now() + ARMED_TTL_MS);
      return json({ id: b.id, state: "armed" });
    }

    if (!(await this.load())) return json({ error: "no such session" }, 404);

    if (path === "/device") {
      if (req.headers.get("Upgrade") !== "websocket") return json({ error: "expected a websocket" }, 426);
      if (this.meta.state !== "armed" && this.meta.state !== "live") {
        return json({ error: `session is ${this.meta.state}` }, 410);
      }
      return this.acceptDevice();
    }
    if (path === "/watch") return this.watch(q, req.signal);
    if (path === "/note") return this.note(await req.json().catch(() => null));
    if (path === "/stop") {
      await this.end("stopped by Claude");
      return json({ ok: true, state: this.meta.state });
    }
    if (path === "/transcript") {
      if (q.get("batch")) return this.batch();
      const entries = await this.entriesAfter(0, 100_000);
      return json({ id: this.meta.id, label: this.meta.label, state: this.meta.state, entries });
    }
    if (path === "/audio") {
      if (!this.meta.archive) return json({ error: "audio was not archived for this session (start --archive)" }, 400);
      if (this.live()) await this.flushArchive(true);
      const seg = Math.max(0, Number(q.get("seg")) || 0);
      const parts = [...(await this.ctx.storage.list({ prefix: `a:${pad(seg)}:` })).values()];
      if (!parts.length) {
        const deleted = !!(await this.ctx.storage.get("batch"));
        return json({ error: deleted ? "audio already deleted by the batch pass" : `no audio for segment ${seg}` }, 404);
      }
      return new Response(concat(parts), { headers: { "content-type": "audio/webm" } });
    }
    if (path === "/status") {
      return json({ ...this.meta, device: this.deviceStatus(), elapsed_s: Math.round(this.elapsed()) });
    }
    return json({ error: "not found" }, 404);
  }
}

// ---------------------------------------------------------------------------

const DEVICE_ROUTES = new Set(["/armed", "/device"]);
const CLI_ROUTES = new Set(["/session", "/watch", "/note", "/stop", "/transcript", "/status", "/audio", "/pair"]);

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const path = url.pathname;
    const q = url.searchParams;

    if (path === "/" && req.method === "GET") {
      return new Response(PAGE, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
    }
    if (path === "/health") {
      return json({ ok: true, secrets: {
        CLI_TOKEN: !!env.CLI_TOKEN, DEVICE_TOKEN: !!env.DEVICE_TOKEN,
        SONIOX_API_KEY: !!env.SONIOX_API_KEY, DEEPGRAM_API_KEY: !!env.DEEPGRAM_API_KEY,
      } });
    }

    // The code is this route's only gate; the page keeps the token it returns.
    if (path === "/pair/claim" && req.method === "POST") {
      if (!env.DEVICE_TOKEN) return json({ ok: false, error: "relay has no DEVICE_TOKEN" }, 500);
      const registry = env.REGISTRY.get(env.REGISTRY.idFromName("main"));
      const body = await req.text();
      const out = await registry.fetch("https://registry/pair/claim", { method: "POST", body });
      const res = await out.json();
      return res.ok ? json({ ok: true, k: env.DEVICE_TOKEN }) : json(res, out.status);
    }

    // An unset secret closes its gate rather than opening it.
    if (DEVICE_ROUTES.has(path)) {
      if (!env.DEVICE_TOKEN || q.get("k") !== env.DEVICE_TOKEN) return json({ error: "unauthorized" }, 401);
    } else if (CLI_ROUTES.has(path)) {
      if (!env.CLI_TOKEN || q.get("token") !== env.CLI_TOKEN) return json({ error: "unauthorized" }, 401);
    } else {
      return json({ error: "not found" }, 404);
    }

    const registry = env.REGISTRY.get(env.REGISTRY.idFromName("main"));
    if (path === "/armed") return registry.fetch("https://registry/list");
    if (path === "/pair") {
      if (req.method !== "POST") return json({ error: "POST" }, 405);
      return registry.fetch("https://registry/pair/new", { method: "POST" });
    }

    if (path === "/session") {
      if (req.method !== "POST") return json({ error: "POST" }, 405);
      const b = await req.json().catch(() => ({}));
      const keyterms = (b.keyterms ?? []).map((t) => String(t).trim()).filter(Boolean).slice(0, 100);
      const claim = await (await registry.fetch("https://registry/claim", {
        method: "POST", body: JSON.stringify({ label: b.label ?? "" }) })).json();
      if (!claim.id) return json(claim, 500);
      const stub = env.SESSION.get(env.SESSION.idFromName(claim.id));
      return stub.fetch("https://session/create", { method: "POST",
        body: JSON.stringify({ id: claim.id, label: b.label ?? "", keyterms, archive: !!b.archive }) });
    }

    const id = (q.get("id") ?? "").toUpperCase();
    if (!ID_RE.test(id)) return json({ error: "need ?id= (4 characters)" }, 400);
    const stub = env.SESSION.get(env.SESSION.idFromName(id));
    if (req.headers.get("Upgrade") === "websocket") return stub.fetch(req);
    // Buffered: a streamed body the object never reads is still being pumped
    // after it responds, and workerd then fails the next request.
    const body = req.method === "POST" ? await req.text() : undefined;
    return stub.fetch(new Request(req.url, { method: req.method, body }));
  },
};
