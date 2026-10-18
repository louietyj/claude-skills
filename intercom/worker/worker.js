// intercom relay: a phone or watch streams mic audio here, a Durable Object
// relays it to Soniox and assembles speaker turns, and Claude long-polls the
// turns from a sandbox that can be recycled without losing any, or streams
// them over /events. In a talk session Claude can also speak through the
// device (Soniox TTS). Deepgram does the optional full-file pass afterwards.
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
//   POST /session     create and arm a session, {mode: listen|talk} -> {id, event_key}
//   GET  /watch?id    long-poll for entries past ?since
//   POST /note?id     a short note for the device (vibrates the watch)
//   POST /speak?id    talk mode: say {text} through the device; blocks until played or interrupted
// Monitor (the session's event_key as ?k=):
//   GET  /events?id   websocket: news entries past ?since, then each new one
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
// Talk mode: Soniox's semantic endpointing at its most patient decides when a turn is over.
const TALK_ENDPOINT = { max_endpoint_delay_ms: 3000, endpoint_sensitivity: -1.0,
                        endpoint_latency_adjustment_level: 0 };
// 48 kHz mono from Soniox; the SFU adapter takes 48 kHz stereo.
const TTS_CONFIG = { model: "tts-rt-v2", language: "en", voice: "Hazel", audio_format: "pcm_s16le",
                     sample_rate: 48000, speed: 1.2, reduce_silence: true, return_timestamps: true };
const TTS_BYTES_PER_S = 96000;
const VOICE_TRACK = "claude";
const PUMP_MS = 50;
const VOICE_LEAD_S = 0.2;
const VOICE_CHUNK_BYTES = 9600;          // 100 ms mono; doubled to stereo it stays under the 32 KB packet cap
// SFU forwarding plus the page's ~500 ms jitter buffer: how far the ear trails the relay.
const VOICE_PLAYOUT_MS = 700;
// A paragraph per call: an interruption then costs at most one paragraph.
const SPEAK_MAX = 400;
const BARGE_IN_WORDS = 2;
const SPEAK_GEN_TIMEOUT_MS = 90_000;
const MODES = new Set(["listen", "talk"]);
// Claude's own notes and speech are logged but are not news.
const NOT_NEWS = new Set(["note", "said"]);
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

function randomKey() {
  return Array.from(crypto.getRandomValues(new Uint8Array(18)), (b) => b.toString(16).padStart(2, "0")).join("");
}

// The adapter's protobuf Packet with only `bytes payload = 5` set, carrying
// interleaved stereo s16le made by doubling each mono sample.
function voicePacket(mono) {
  const n = mono.length * 2;
  const len = [];
  for (let v = n; ; v >>>= 7) {
    if (v < 0x80) { len.push(v); break; }
    len.push((v & 0x7f) | 0x80);
  }
  const out = new Uint8Array(1 + len.length + n);
  out[0] = (5 << 3) | 2;
  out.set(len, 1);
  let o = 1 + len.length;
  for (let i = 0; i < mono.length; i += 2) {
    out[o] = out[o + 2] = mono[i];
    out[o + 1] = out[o + 3] = mono[i + 1];
    o += 4;
  }
  return out;
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
      const { label, mode } = await req.json();
      for (let tries = 0; tries < 50; tries++) {
        const id = Array.from(crypto.getRandomValues(new Uint8Array(4)),
                              (b) => ID_ALPHABET[b % ID_ALPHABET.length]).join("");
        if (await this.ctx.storage.get(`s:${id}`)) continue;
        await this.ctx.storage.put(`s:${id}`, { id, label, mode, state: "armed", at: now });
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
          out.push({ id: s.id, label: s.label, mode: s.mode ?? "listen", state: s.state,
                     age_s: Math.round(age / 1000) });
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
    this.events = new Set();     // Monitor sockets
    this.speaking = null;        // the speak call in flight
    this.voiceOut = null;        // the SFU adapter's socket: PCM in, Claude's voice track out
    this.voicePending = null;
    this.voiceSub = null;        // the page's SFU session, awaiting its answer
  }

  talk() { return this.meta?.mode === "talk"; }

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
    if (!NOT_NEWS.has(kind)) {
      this.meta.news_seq = e.n;
      this.broadcast(e);
    }
    await this.ctx.storage.put(`e:${pad(e.n)}`, e);
    await this.saveMeta();
    return e;
  }

  broadcast(e) {
    const frame = JSON.stringify(e);
    for (const ws of this.events) {
      try { ws.send(frame); } catch { this.events.delete(ws); }
    }
  }

  // --- turns -------------------------------------------------------------

  async flushTurn() {
    const cur = this.cur;
    this.cur = null;
    const text = cur?.text.trim();
    // A lone "." from noise would read to Claude as Louie saying something.
    if (text && /[\p{L}\p{N}]/u.test(text)) await this.addEntry("turn", { spk: cur.spk, t: Math.round(cur.t), text });
  }

  // Soniox sends each final token once and every non-final token on every
  // message; tokens carry their own spacing, and "<end>" marks an utterance's
  // end. A turn also closes at the first sentence end past TURN_SPLIT_S, or a
  // monologue would only be delivered once it stops. Talk mode doesn't split:
  // a turn is something Claude acts on, so it should be a whole thought.
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
      if (!this.talk() && at - this.cur.t >= TURN_SPLIT_S && /[.?!]$/.test(tok.text)) await this.flushTurn();
    }
    this.pending = pending ? { spk: Number(pendingSpk ?? 0) || 0, text: pending } : null;
    if (this.speaking?.bargeIn && !this.speaking.stopping) {
      const heard = (this.tail()?.text ?? "").split(/\s+/).filter(Boolean).length;
      if (heard >= BARGE_IN_WORDS) this.bargeIn("Louie spoke");
    }
  }

  // The open turn's finished words plus those still being recognised.
  tail() {
    const text = ((this.cur?.text ?? "") + (this.pending?.text ?? "")).trim();
    return text ? { spk: this.cur?.spk ?? this.pending.spk, text } : null;
  }

  // --- transcriber -----------------------------------------------------------

  sonioxHeaders() {
    return { Upgrade: "websocket", Authorization: `Bearer ${this.env.SONIOX_API_KEY}` };
  }

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
      const resp = await fetch(this.env.SONIOX_URL, { headers: this.sonioxHeaders() });
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
      model: SONIOX_MODEL, audio_format: "auto", language_hints: ["en"],
      enable_speaker_diarization: !this.talk(), enable_endpoint_detection: true,
      ...(this.talk() ? TALK_ENDPOINT : {}),
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
    if (this.speaking) await this.finishSpeak(this.speaking, "interrupted", `session ended: ${reason}`);
    await this.closeVoice();
    await this.closeTranscriber();          // its last finals land before the end entry
    await this.flushTurn();
    await this.flushArchive();
    this.meta.state = "ended";
    this.meta.ended_at = Date.now();
    this.meta.end_reason = reason;
    await this.addEntry("event", { text: `session ended: ${reason}` });
    this.toDevice({ type: "ended", reason });
    for (const ws of this.events) { try { ws.close(1000, "session ended"); } catch {} }
    this.events.clear();
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
      if (this.speaking) await this.finishSpeak(this.speaking, "interrupted", "the device disconnected");
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
    if (msg.type === "stop_speaking") return this.bargeIn("Louie tapped stop");
    if (msg.type === "voice_request" && this.talk()) return this.subscribeVoice();
    if (msg.type === "voice_answer" && this.voiceSub) return this.answerVoice(msg.sdp);
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

  // Monitor's view: every news entry after `since`, then each new one as it
  // happens. The input gate holds other events during the storage read, so
  // nothing lands between the replay and joining the live set.
  async acceptEvents(since) {
    const [client, server] = Object.values(new WebSocketPair());
    server.accept();
    for (const e of await this.entriesAfter(since, 100_000)) {
      if (!NOT_NEWS.has(e.kind)) server.send(JSON.stringify(e));
    }
    if (this.meta.state === "ended" || this.meta.state === "expired") {
      server.close(1000, `session ${this.meta.state}`);
    } else {
      this.events.add(server);
      const drop = () => this.events.delete(server);
      server.addEventListener("close", drop);
      server.addEventListener("error", drop);
    }
    return new Response(null, { status: 101, webSocket: client });
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
          mode: this.meta.mode ?? "listen",
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

  // --- voice track (talk mode) -------------------------------------------------------
  //
  // Claude's voice reaches the page as a WebRTC track through Cloudflare's SFU:
  // Chrome on Android only cancels echo of audio that arrives from a real peer
  // connection. The SFU's WebSocket adapter dials /tts-in on this object, and
  // the page subscribes to the track it publishes.

  async sfu(path, method, body) {
    const resp = await fetch(`${this.env.SFU_URL}/${this.env.REALTIME_APP_ID}${path}`, {
      method, headers: { Authorization: `Bearer ${this.env.REALTIME_APP_TOKEN}`, "content-type": "application/json" },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    const out = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(`SFU ${path} returned ${resp.status}: ${JSON.stringify(out).slice(0, 200)}`);
    return out;
  }

  // One publication per session, made when first needed and remade if its socket dies.
  async ensureVoice(forPage = false) {
    if (this.voiceOut) return this.meta.voice;
    if (this.voicePending) return this.voicePending;
    this.voicePending = (async () => {
      this.meta.voice_key = randomKey();
      await this.saveMeta();
      const endpoint = `${this.meta.origin.replace(/^http/, "ws")}/tts-in?id=${this.meta.id}&k=${this.meta.voice_key}`;
      const out = await this.sfu("/adapters/websocket/new", "POST",
        { tracks: [{ location: "local", trackName: VOICE_TRACK, endpoint, inputCodec: "pcm" }] });
      const t = out.tracks?.[0];
      if (!t?.sessionId || t.errorCode) throw new Error(`voice adapter: ${t?.errorCode ?? "no session"} ${t?.errorDescription ?? ""}`.trim());
      this.meta.voice = { adapterId: t.adapterId, sessionId: t.sessionId };
      await this.saveMeta();
      // A page subscribed to the previous publication would hear nothing.
      if (!forPage) this.toDevice({ type: "voice_restart" });
      return this.meta.voice;
    })();
    try { return await this.voicePending; } finally { this.voicePending = null; }
  }

  acceptVoiceIn() {
    const [client, server] = Object.values(new WebSocketPair());
    server.accept();
    this.voiceOut = server;
    const gone = () => { if (this.voiceOut === server) this.voiceOut = null; };
    server.addEventListener("close", gone);
    server.addEventListener("error", gone);
    return new Response(null, { status: 101, webSocket: client });
  }

  async closeVoice() {
    const v = this.meta?.voice;
    try { this.voiceOut?.close(1000); } catch {}
    this.voiceOut = null;
    if (v?.adapterId) {
      try { await this.sfu("/adapters/websocket/close", "POST", { tracks: [{ adapterId: v.adapterId }] }); } catch {}
    }
  }

  // The page asks for the track; the SFU offers it, and the page's answer goes back.
  async subscribeVoice() {
    try {
      const pub = await this.ensureVoice(true);
      const { sessionId } = await this.sfu("/sessions/new", "POST");
      const out = await this.sfu(`/sessions/${sessionId}/tracks/new`, "POST",
        { tracks: [{ location: "remote", sessionId: pub.sessionId, trackName: VOICE_TRACK }] });
      if (out.tracks?.[0]?.errorCode) throw new Error(`subscribe: ${out.tracks[0].errorCode}`);
      this.voiceSub = sessionId;
      this.toDevice({ type: "voice_offer", sdp: out.sessionDescription });
    } catch (e) {
      await this.addEntry("event", { text: `Claude's voice could not be connected (${e.message}); speak will fail` });
      this.toDevice({ type: "voice_failed", error: String(e.message).slice(0, 200) });
    }
  }

  async answerVoice(sdp) {
    try {
      await this.sfu(`/sessions/${this.voiceSub}/renegotiate`, "PUT", { sessionDescription: sdp });
    } catch (e) {
      await this.addEntry("event", { text: `Claude's voice could not be connected (${e.message}); speak will fail` });
    }
  }

  // --- speech (talk mode) ----------------------------------------------------------
  //
  // Soniox streams PCM with per-character timestamps. The relay paces it into
  // the voice track at real time, so its own clock says how far playback has
  // got: elapsed time since the first frame, less the delay through the SFU
  // and the page's jitter buffer. The timestamps turn that into words.

  async speak(body) {
    const text = String(body?.text ?? "").trim();
    if (!this.talk()) return json({ ok: false, error: "this is a listen session: Claude has no voice in it" }, 409);
    if (!text) return json({ ok: false, error: "nothing to say" }, 400);
    if (text.length > SPEAK_MAX) {
      return json({ ok: false, error: `${text.length} chars; ${SPEAK_MAX} at most per call` }, 413);
    }
    if (!this.live()) return json({ ok: false, error: `session is ${this.meta.state}` }, 409);
    if (this.speaking) return json({ ok: false, error: "already speaking" }, 409);
    if (!this.device) return json({ status: "not_delivered", spoken: "", unspoken: text });
    if (!this.voiceOut) {
      try { await this.ensureVoice(); } catch (e) { return json({ status: "error", error: e.message }, 502); }
      if (!this.voiceOut) return json({ status: "error", error: "the voice track is not connected" }, 502);
    }

    // Claude says which entry it last read; a turn after that means it would be
    // answering a moment that has passed, so hand the turn back instead.
    if (Number.isInteger(body?.since)) {
      const unseen = (await this.entriesAfter(body.since)).filter((e) => e.kind === "turn");
      if (unseen.length) {
        return json({ status: "not_spoken", reason: "Louie said something you haven't read", spoken: "",
                      unspoken: text, turns: unseen.map((e) => ({ n: e.n, text: e.text })) });
      }
    }
    // Never talk over Louie: his turn arrives as an event, and Claude may want to say something else.
    const open = this.tail();
    if (open) return json({ status: "not_spoken", reason: "Louie is talking", spoken: "", unspoken: text,
                            heard_so_far: open.text });

    let ws;
    try {
      const resp = await fetch(this.env.SONIOX_TTS_URL, { headers: this.sonioxHeaders() });
      ws = resp.webSocket;
      if (!ws) return json({ status: "error", error: `TTS refused the connection (HTTP ${resp.status})` }, 502);
    } catch (e) {
      return json({ status: "error", error: `TTS unreachable (${e})` }, 502);
    }
    ws.accept();
    const sid = crypto.randomUUID().slice(0, 8);
    const sp = this.speaking = {
      sid, ws, text, chars: [], ends: [], queue: [], sentBytes: 0, firstAt: null,
      genEnded: false, genDone: false, stopping: null, guard: null, pump: null, resolve: null,
      bargeIn: body?.barge_in !== false,
    };
    const done = new Promise((r) => { sp.resolve = r; });
    ws.addEventListener("message", (m) => this.onTts(sp, m.data));
    ws.addEventListener("close", () => { if (this.speaking === sp && !sp.genEnded) this.finishSpeak(sp, "error", "TTS closed early"); });
    ws.send(JSON.stringify({ ...TTS_CONFIG, stream_id: sid }));
    ws.send(JSON.stringify({ stream_id: sid, text, text_end: true }));
    this.toDevice({ type: "tts_start", sid });
    sp.pump = setInterval(() => this.pumpVoice(sp), PUMP_MS);
    sp.guard = setTimeout(() => this.finishSpeak(sp, "error", "TTS produced no end of audio"), SPEAK_GEN_TIMEOUT_MS);
    return json(await done);
  }

  onTts(sp, raw) {
    if (this.speaking !== sp) return;
    let m;
    try { m = JSON.parse(raw); } catch { return; }
    if (m.error_code) return this.finishSpeak(sp, "error", `TTS ${m.error_type ?? m.error_code}: ${m.error_message ?? ""}`.trim());
    if (m.audio && !sp.stopping) sp.queue.push(Uint8Array.from(atob(m.audio), (c) => c.charCodeAt(0)));
    const ts = m.timestamps;
    if (ts) {
      sp.chars.push(...ts.characters);
      sp.ends.push(...ts.character_end_times_seconds);
    }
    if (m.audio_end) sp.genEnded = true;
    if (m.terminated) {
      sp.genEnded = sp.genDone = true;
      try { sp.ws.close(1000); } catch {}
    }
  }

  // Keeps the voice track VOICE_LEAD_S ahead of real time and no further, so a
  // barge-in has little queued in the SFU to talk over.
  pumpVoice(sp) {
    if (this.speaking !== sp || sp.stopping) return;
    const now = Date.now();
    const sentS = () => sp.sentBytes / TTS_BYTES_PER_S;
    while (sp.queue.length && (sp.firstAt == null || sentS() < (now - sp.firstAt) / 1000 + VOICE_LEAD_S)) {
      let chunk = sp.queue[0];
      if (chunk.length > VOICE_CHUNK_BYTES) {
        sp.queue[0] = chunk.subarray(VOICE_CHUNK_BYTES);
        chunk = chunk.subarray(0, VOICE_CHUNK_BYTES);
      } else {
        sp.queue.shift();
      }
      if (chunk.length % 2) chunk = chunk.subarray(0, chunk.length - 1);   // a split sample: not worth carrying
      try { this.voiceOut?.send(voicePacket(chunk)); } catch {}
      sp.firstAt ??= now;
      sp.sentBytes += chunk.length;
    }
    if (sp.genEnded && !sp.queue.length && sp.firstAt != null) {
      const endsAt = sp.firstAt + sentS() * 1000 + this.playoutDelayMs();
      if (now >= endsAt) this.finishSpeak(sp, "done");
    }
  }

  playoutDelayMs() { return Number(this.env.VOICE_PLAYOUT_MS ?? VOICE_PLAYOUT_MS); }

  played(sp) {
    if (sp.firstAt == null) return 0;
    const s = (Date.now() - sp.firstAt - this.playoutDelayMs()) / 1000;
    return Math.max(0, Math.min(s, sp.sentBytes / TTS_BYTES_PER_S));
  }

  // The page mutes at once; what is still in the SFU and the jitter buffer goes unheard.
  bargeIn(reason) {
    const sp = this.speaking;
    if (!sp || sp.stopping) return;
    sp.stopping = reason;
    this.toDevice({ type: "tts_stop", sid: sp.sid });
    this.finishSpeak(sp, "interrupted", reason);
  }

  // Timestamps cover the original text, so the k-th timed word is the k-th
  // word Claude wrote. A word counts as heard once its audio has fully played;
  // words not timed yet were never played.
  splitSpoken(sp, played) {
    const wordEnds = [];
    let end = null;
    sp.chars.forEach((c, i) => {
      if (/\s/.test(c)) {
        if (end != null) wordEnds.push(end);
        end = null;
      } else {
        end = sp.ends[i];
      }
    });
    if (end != null) wordEnds.push(end);
    let heard = 0;
    while (heard < wordEnds.length && wordEnds[heard] <= played + 0.05) heard++;
    const words = sp.text.split(/\s+/).filter(Boolean);
    return { spoken: words.slice(0, heard).join(" "), unspoken: words.slice(heard).join(" ") };
  }

  async finishSpeak(sp, status, reason) {
    if (this.speaking !== sp) return;
    this.speaking = null;
    const played = this.played(sp);
    clearTimeout(sp.guard);
    clearInterval(sp.pump);
    if (!sp.genDone) { try { sp.ws.send(JSON.stringify({ stream_id: sp.sid, cancel: true })); } catch {} }
    try { sp.ws.close(1000); } catch {}
    if (status === "error") this.toDevice({ type: "tts_stop", sid: sp.sid });
    else if (status === "done") this.toDevice({ type: "tts_done", sid: sp.sid });
    const { spoken, unspoken } = status === "done" ? { spoken: sp.text, unspoken: "" }
      : status === "error" ? { spoken: "", unspoken: sp.text } : this.splitSpoken(sp, played);
    if (spoken) await this.addEntry("said", { text: spoken, interrupted: status !== "done" });
    sp.resolve({
      status, spoken, unspoken,
      ...(reason ? { reason } : {}),
      ...(status === "interrupted" ? { heard_so_far: this.tail()?.text ?? "" } : {}),
      audio_s: Math.round((sp.sentBytes / TTS_BYTES_PER_S) * 10) / 10,
    });
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
      // The mode is set here and nowhere else: a listen session can never speak.
      this.meta = {
        id: b.id, label: b.label ?? "", keyterms: b.keyterms ?? [], archive: !!b.archive,
        mode: b.mode, event_key: randomKey(), origin: b.origin,
        state: "armed", created_at: Date.now(), started_at: null, ended_at: null,
        end_reason: null, seq: 0, segs: [],
      };
      await this.saveMeta();
      await this.ctx.storage.setAlarm(Date.now() + ARMED_TTL_MS);
      return json({ id: b.id, state: "armed", mode: b.mode, event_key: this.meta.event_key });
    }

    if (!(await this.load())) return json({ error: "no such session" }, 404);

    if (path === "/device") {
      if (req.headers.get("Upgrade") !== "websocket") return json({ error: "expected a websocket" }, 426);
      if (this.meta.state !== "armed" && this.meta.state !== "live") {
        return json({ error: `session is ${this.meta.state}` }, 410);
      }
      return this.acceptDevice();
    }
    if (path === "/events") {
      if (req.headers.get("Upgrade") !== "websocket") return json({ error: "expected a websocket" }, 426);
      if (!this.meta.event_key || q.get("k") !== this.meta.event_key) return json({ error: "unauthorized" }, 401);
      return this.acceptEvents(Math.max(0, Number(q.get("since")) || 0));
    }
    if (path === "/tts-in") {
      if (req.headers.get("Upgrade") !== "websocket") return json({ error: "expected a websocket" }, 426);
      if (!this.meta.voice_key || q.get("k") !== this.meta.voice_key) return json({ error: "unauthorized" }, 401);
      return this.acceptVoiceIn();
    }
    if (path === "/speak") return this.speak(await req.json().catch(() => null));
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
const CLI_ROUTES = new Set(["/session", "/watch", "/note", "/speak", "/stop", "/transcript", "/status",
                            "/audio", "/pair"]);
// Gated inside the session by its own keys: a Monitor URL never carries the CLI
// token, and the SFU adapter never sees it.
const EVENT_ROUTES = new Set(["/events", "/tts-in"]);

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
        REALTIME_APP_ID: !!env.REALTIME_APP_ID, REALTIME_APP_TOKEN: !!env.REALTIME_APP_TOKEN,
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
    } else if (!EVENT_ROUTES.has(path)) {
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
      const mode = b.mode ?? "listen";
      if (!MODES.has(mode)) return json({ error: `mode must be listen or talk, not ${mode}` }, 400);
      const keyterms = (b.keyterms ?? []).map((t) => String(t).trim()).filter(Boolean).slice(0, 100);
      const claim = await (await registry.fetch("https://registry/claim", {
        method: "POST", body: JSON.stringify({ label: b.label ?? "", mode }) })).json();
      if (!claim.id) return json(claim, 500);
      const stub = env.SESSION.get(env.SESSION.idFromName(claim.id));
      return stub.fetch("https://session/create", { method: "POST",
        body: JSON.stringify({ id: claim.id, label: b.label ?? "", keyterms, archive: !!b.archive, mode,
                               origin: url.origin }) });
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
