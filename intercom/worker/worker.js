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
//   GET  /device?id   websocket, one per screen in the room: controls, and audio from
//                     the one holding it, in; room state, notes and the end out
// Claude (CLI_TOKEN as ?token=):
//   POST /pair        a one-time 6-digit code for pairing a device
//   POST /session     create and arm a session, {mode: listen|talk, isolate} -> {id, event_key}
//   GET  /watch?id    long-poll for entries past ?since
//   POST /note?id     a short note for the device (vibrates the watch)
//   POST /speak?id    talk mode: say {text} through the device; blocks until played or interrupted
//   POST /set?id      talk mode: {hold_s}, the silence that closes Louie's turn
//   POST /call?id     {to}: Telnyx rings that number for the room's phone line
//   POST /hangup?id   end the phone line
// Monitor (the session's event_key as ?k=):
//   GET  /events?id   websocket: news entries past ?since, then each new one
//   POST /stop?id     end the session
//   GET  /transcript?id[&batch=1]
//   GET  /audio?id&seg  an archived segment's webm, until the batch pass deletes it
//   GET  /status?id
// Anyone:
//   GET  /health      which secrets are set, never their values
// Telnyx (its Ed25519 signature):
//   POST /telnyx      call webhooks; client_state names the session
//   GET  /aec/*       the page's own echo canceller (../aec): wasm, wrapper, worklet
import PAGE from "./page.html";
import AEC3_WASM from "../aec/aec3.wasm";
import AEC3_JS from "../aec/aec3.js";
import AEC3_WORKLET from "../aec/worklet.js";
import OPUS_WASM from "../opus/opus.wasm";
import { WebmOpusDecoder } from "../opus/opus.js";

const AEC_FILES = {
  "/aec/aec3.wasm": [AEC3_WASM, "application/wasm"],
  "/aec/aec3.js": [AEC3_JS, "text/javascript"],
  "/aec/worklet.js": [AEC3_WORKLET, "text/javascript"],
};

const ID_ALPHABET = "ACDEFGHJKLMNPQRTUVWXY34679";  // no 0/O, 1/I, 2/Z, 5/S, 8/B
const ID_RE = /^[ACDEFGHJKLMNPQRTUVWXY34679]{4}$/;
const ARMED_TTL_MS = 15 * 60_000;
const DEVICE_GONE_MS = 5 * 60_000;
const RESUME_GRACE_MS = 60_000;          // the page buffers this much of its recording
const SCREEN_PING_MS = 2000;             // a page that hears nothing for 6s assumes its socket is dead
const LISTEN_BATCH_MS = 10_000;          // like watch's --interval
const NO_AUDIO_MAX_MS = 60 * 60_000;
// Why speech stopped, as `speak` reports it, when the audio left its holder.
const SPEAK_CUT_BY = { left: "Louie left audio", dropped: "the screen holding the audio disconnected",
                       "call ended": "the phone line ended" };
const TELNYX_API_URL = "https://api.telnyx.com";
const E164_RE = /^\+[1-9]\d{6,14}$/;
const RING_S = 20;                       // rings out before a voicemail would answer
// Telnyx's media_format encodings, as Soniox's raw-audio settings. Its L16 is
// little-endian (see toCall).
const PHONE_FORMATS = {
  PCMU: { audio_format: "mulaw", sample_rate: 8000, num_channels: 1 },
  PCMA: { audio_format: "alaw", sample_rate: 8000, num_channels: 1 },
  L16: { audio_format: "pcm_s16le", sample_rate: 16000, num_channels: 1 },
};
const WEBHOOK_SKEW_S = 300;              // older signed webhooks are refused as replays
const KEEP_MS = 24 * 3600_000;
const KEEPALIVE_MS = 5000;
const MIC_STALE_MS = 12_000;      // the page repeats its mic reading every ~5s
const AUDIO_STALL_MS = 10_000;    // the recorder sends a chunk every 250 ms
const ARCHIVE_ROW_BYTES = 120_000;   // ~30s of opus per row: rows written are capped per day
const NOTE_MAX = 120;
const TURN_SPLIT_S = 12;
const PAIR_TTL_MS = 5 * 60_000;
const PAIR_TRIES = 5;
const PAIR_FAILS_PER_HOUR = 20;
const WATCH_MAX_S = 590;
const MAX_ENTRIES = 400;
const PENDING_AUDIO_MAX = 200;      // chunks held while the transcriber's socket opens

// Talk mode's voice isolation (Krisp): 16-bit PCM in 30 ms frames, at 8 or 16 kHz.
const KRISP_API_URL = "https://api.developers.krisp.ai";
const KRISP_URL = "https://streaming.krisp.ai/voice-isolation";
const KRISP_TOKEN_MIN = 300;        // the longest session token Krisp issues
const ISO_RATE = 16000;             // a page's audio is decoded to this
const ISO_FRAME_MS = 30;
// A frame Krisp hasn't returned by then means it has stalled or never connected.
const ISO_STALL_MS = 3000;
const ISO_POWER_FRAMES = 4000;      // 2 minutes of power log: words are final within seconds

const SONIOX_MODEL = "stt-rt-v5";
// Talk mode: Soniox's semantic endpointing at its most patient decides when a turn is over.
const TALK_ENDPOINT = { max_endpoint_delay_ms: 3000, endpoint_sensitivity: -1.0,
                        endpoint_latency_adjustment_level: 0 };
// It can still end a turn while Louie gathers his thoughts, so a turn can be
// held open until this much silence follows. Louie prefers quick replies by
// default and raises it (the page's settings, or `intercom set`) for slow thinking.
const TALK_HOLD_MS = 0;
const HOLD_MIN_MS = 0;         // Soniox's end of utterance alone
const HOLD_MAX_MS = 10_000;
// 48 kHz mono from Soniox; the SFU adapter takes 48 kHz stereo.
const TTS_CONFIG = { model: "tts-rt-v2", language: "en", voice: "Hazel", audio_format: "pcm_s16le",
                     sample_rate: 48000, speed: 1.3, reduce_silence: true, return_timestamps: true };
// On a call, Soniox renders at the rate the stream's `start` names: Telnyx plays
// L16 at that rate (8 kHz unless asked; 16 kHz audio sent to an 8 kHz stream
// came out at half speed). The relay asks for 16 kHz, and the dial prefers
// wideband codecs, for HD voice where the carrier takes it. If no mark comes
// back, the line is taken to have played its audio this much later.
const PHONE_RATE = 16000;
const MARK_GRACE_MS = 2000;
const VOICE_TRACK = "claude";
const PUMP_MS = 50;
const VOICE_LEAD_S = 0.2;
// SFU forwarding plus the page's ~500 ms jitter buffer: how far the ear trails the relay.
const VOICE_PLAYOUT_MS = 700;
// The page's WebRTC connection takes a few seconds after START; audio fed before it is up is lost.
const VOICE_READY_WAIT_MS = 10_000;
const BARGE_IN_WORDS = 2;
const SPEAK_GEN_TIMEOUT_MS = 90_000;
const SPEAK_KEEPALIVE_MS = 5000;
const SPEAK_KEPT = 20;                   // results kept for a CLI whose reply was lost
const MODES = new Set(["listen", "talk"]);
// While Claude speaks, a word counts only if the page's sustained-energy gate
// was open when it was said: echo residue comes in brief spikes, Louie's
// speech is sustained. The gate reports late (it needs most of two seconds of
// loud frames), so its open edge is pulled back; words are matched by audio time.
const GATE_LAG_S = 1.5;
const GATE_MARGIN_S = 0.3;
// Echo of the last words still in flight after the relay stops feeding.
const ECHO_TAIL_S = 1.5;
const SPANS_MAX = 50;
// Claude's own notes and speech, and Louie's settings, are logged but are not news.
const NOT_NEWS = new Set(["note", "said", "setting", "net"]);
const BATCH_PARAMS = {
  model: "nova-3", diarize: "true", smart_format: "true", punctuate: "true",
  mip_opt_out: "true",
};

const json = (o, c = 200) =>
  new Response(JSON.stringify(o), { status: c, headers: { "content-type": "application/json" } });
const pad = (n) => String(n).padStart(6, "0");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// What a page saw when its socket went quiet: ", wifi rtt 50ms 10Mb/s hidden 12 KB unsent".
function netNote(m) {
  const parts = [m.net, m.rtt != null && `rtt ${m.rtt}ms`, m.down != null && `${m.down}Mb/s`,
                 m.vis === "hidden" && "hidden", m.unsent && `${Math.round(m.unsent / 1024)} KB unsent`].filter(Boolean);
  return parts.length ? `, ${parts.join(" ")}`.slice(0, 120) : "";
}

// A speak holds its request open for the whole utterance, and a proxy may drop
// one that stays silent that long: spaces go out ahead of the JSON.
function heldJson(promise) {
  const { readable, writable } = new TransformStream();
  const w = writable.getWriter(), enc = new TextEncoder();
  const tick = setInterval(() => w.write(enc.encode(" ")).catch(() => {}), SPEAK_KEEPALIVE_MS);
  promise.catch((e) => ({ status: "error", error: String(e?.message ?? e) }))
    .then((out) => w.write(enc.encode(JSON.stringify(out))))
    .finally(() => { clearInterval(tick); w.close().catch(() => {}); });
  return new Response(readable, { headers: { "content-type": "application/json" } });
}

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

// A phone segment's raw audio as a WAV file.
const WAV_FORMATS = { mulaw: [7, 8], alaw: [6, 8], pcm_s16le: [1, 16] };
function wav(raw, data) {
  const [tag, bits] = WAV_FORMATS[raw.audio_format];
  const head = new DataView(new ArrayBuffer(44));
  const ascii = (at, s) => [...s].forEach((c, i) => head.setUint8(at + i, c.charCodeAt(0)));
  const ch = raw.num_channels ?? 1, rate = raw.sample_rate;
  ascii(0, "RIFF"); head.setUint32(4, 36 + data.length, true); ascii(8, "WAVE");
  ascii(12, "fmt "); head.setUint32(16, 16, true); head.setUint16(20, tag, true); head.setUint16(22, ch, true);
  head.setUint32(24, rate, true); head.setUint32(28, rate * ch * bits / 8, true);
  head.setUint16(32, ch * bits / 8, true); head.setUint16(34, bits, true);
  ascii(36, "data"); head.setUint32(40, data.length, true);
  return concat([new Uint8Array(head.buffer), data]);
}

function meanSquare(frame) {
  const s = new Int16Array(frame.buffer, frame.byteOffset, frame.byteLength >> 1);
  let sum = 0;
  for (let i = 0; i < s.length; i++) sum += s[i] * s[i];
  return s.length ? sum / s.length : 0;
}

// Krisp keeps the voice nearest the mic and drops music, chatter and noise.
// Frames come back one for one and in order, so the transcriber's clock is
// unchanged. Whatever Krisp can't take (no token, a dropped socket, a stall)
// goes to the transcriber unfiltered instead: the transcript never stops.
// Each frame's power before and after is logged, so a word's timestamps say
// how much Krisp turned it down: the voice it keeps passes nearly unchanged.
class Isolation {
  constructor({ url, token, rate, send, report }) {
    Object.assign(this, { rate, send, report });
    this.frameBytes = rate * ISO_FRAME_MS / 1000 * 2;
    this.carry = new Uint8Array(0);
    this.waiting = [];           // frames not yet returned, sent or not: {frame, at}
    this.ws = null;
    this.ready = false;
    this.off = false;
    this.rawPower = [];          // per frame, from `base` on
    this.isoPower = [];          // ...for the frames the transcriber has had
    this.base = 0;
    this.connect(url, token).catch((e) => this.fail(e.message ?? String(e)));
  }

  async connect(url, token) {
    const key = await token();
    const resp = await fetch(url, { headers: { Upgrade: "websocket", Authorization: `api-key ${key}` } });
    const ws = resp.webSocket;
    if (!ws) throw new Error(`Krisp refused the connection (HTTP ${resp.status})`);
    ws.accept();
    if (this.off) { try { ws.close(1000); } catch {} return; }   // closed while connecting
    this.ws = ws;
    ws.addEventListener("message", (m) => this.onMessage(m.data));
    ws.addEventListener("close", (c) => this.fail(`Krisp closed the stream: ${c.code}${c.reason ? ` ${c.reason}` : ""}`));
    ws.addEventListener("error", () => this.fail("Krisp's stream failed"));
    ws.send(JSON.stringify({ model: "VIVA-tel", sample_rate: this.rate, suppression_level: 100 }));
  }

  onMessage(data) {
    if (this.off) return;
    if (typeof data !== "string") {
      if (this.waiting.shift()) this.pass(new Uint8Array(data));
      return;
    }
    let m;
    try { m = JSON.parse(data); } catch { return; }
    if (m.type === "ready") {
      this.ready = true;
      for (const w of this.waiting) this.ws.send(w.frame);
    } else if (m.type === "error") {
      this.fail(`Krisp: ${m.error?.code ?? "error"}`);
    }
  }

  // Still cut into frames once Krisp is off, so the power log keeps the transcriber's clock.
  push(pcm) {
    const b = concat([this.carry, pcm]);
    let at = 0;
    for (; at + this.frameBytes <= b.length; at += this.frameBytes) {
      const frame = b.slice(at, at + this.frameBytes);
      this.rawPower.push(meanSquare(frame));
      if (this.off) { this.pass(frame); continue; }
      this.waiting.push({ frame, at: Date.now() });
      if (this.ready) this.ws.send(frame);
    }
    this.carry = b.slice(at);
    if (this.rawPower.length > ISO_POWER_FRAMES * 2) {
      this.rawPower.splice(0, ISO_POWER_FRAMES);
      this.isoPower.splice(0, ISO_POWER_FRAMES);
      this.base += ISO_POWER_FRAMES;
    }
    if (this.waiting.length && Date.now() - this.waiting[0].at > ISO_STALL_MS) {
      this.fail(`Krisp returned nothing for ${ISO_STALL_MS / 1000}s`);
    }
  }

  pass(frame) {
    this.isoPower.push(meanSquare(frame));
    this.send(frame);
  }

  // Summed power before and after Krisp over [startMs, endMs) of the transcriber's stream.
  power(startMs, endMs) {
    let raw = 0, iso = 0;
    const last = Math.min(Math.ceil(endMs / ISO_FRAME_MS), this.base + this.isoPower.length);
    for (let f = Math.max(Math.floor(startMs / ISO_FRAME_MS), this.base); f < last; f++) {
      raw += this.rawPower[f - this.base];
      iso += this.isoPower[f - this.base];
    }
    return { raw, iso };
  }

  fail(why) {
    if (this.off) return;
    this.stop();
    this.report(why);
  }

  // Off from here on: what Krisp still holds goes out as it was.
  stop() {
    if (this.off) return;
    this.off = true;
    for (const w of this.waiting) this.pass(w.frame);
    this.waiting = [];
    try { this.ws?.close(1000); } catch {}
  }

  close() {
    this.stop();
    if (this.carry.length) this.send(this.carry);
    this.carry = new Uint8Array(0);
  }
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
    // Room facts Claude reads (hidden_since, no_audio_since, absent_since) live in
    // meta: an object restart must not turn "gone 3 minutes" into "fine".
    this.screens = new Map();    // every page in the room: websocket -> {ui}
    this.holder = null;          // the screen whose mic is the room's audio, or the phone line
    this.phone = null;           // Telnyx's media socket for the room's call
    this.phoneFormat = null;     // Soniox's raw-audio settings for it
    this.stt = null;             // current transcriber websocket
    this.sttOpening = false;
    this.quickFails = 0;
    this.pendingAudio = [];
    this.iso = null;             // talk mode: the segment's Isolation, if it has one
    this.decoder = null;         // ...and a page's webm decoded to PCM for it
    this.krisp = null;           // Krisp session token: {key, until}
    this.isoUnset = false;       // said once that the relay has no Krisp key
    this.cur = null;             // turn being assembled: {spk, t, text, gap}
    this.holdTimer = null;       // talk mode: delivers cur after TALK_HOLD_MS of silence
    this.pending = null;         // words still being recognised, never stored
    this.lastAudioAt = 0;
    this.audioStalledAt = null;  // the last chunk before the page stopped sending
    this.mic = null;            // the page's latest mic reading: {state, at}
    this.arch = null;            // {seg, part, chunks, bytes}
    this.keepalive = null;
    this.events = new Set();     // Monitor sockets
    this.speaking = null;        // the speak call in flight
    this.speakCalls = new Map(); // call id -> its outcome, for a CLI that lost the reply and asks again
    this.grace = null;           // a holding page that dropped and may resume: {ws, dev, ui, at, timer}
    this.segRec = null;          // the holding page's recorder run, and how many of its chunks arrived
    this.segChunks = 0;
    this.resuming = null;        // a rejoined page, until it says whether it could resend
    this.pinger = null;
    this.voiceOut = null;        // the SFU adapter's socket: PCM in, Claude's voice track out
    this.voicePending = null;
    this.voiceSub = null;        // the page's SFU session, awaiting its answer
    this.voiceReady = false;     // the page says it is connected and playing
    this.speechSpans = [];       // [start, end|null] in session seconds: when Claude's voice could echo
    this.loudSpans = [];         // [start, end] when the page's sustained-energy gate was open
    this.loudFrom = null;
  }

  talk() { return this.meta?.mode === "talk"; }

  async load() {
    if (this.meta) return this.meta;
    this.meta = (await this.ctx.storage.get("meta")) ?? null;
    // A page that dropped just before a restart can't resume: the stream died with the object.
    const g = this.meta?.state === "live" && this.meta.grace;
    if (g) {
      delete this.meta.grace;
      Object.assign(this.meta, { no_audio_since: g.at, no_audio_why: "dropped", no_audio_from: g.ui,
                                 audio_since: null });
      const s = Math.round((Date.now() - g.at) / 1000);
      await this.addEntry("event", { text: `${g.ui} holding the audio disconnected ${s}s ago; no audio until it reconnects` });
      await this.armTimer();
    }
    // A fresh instance with a live session means the object restarted (a
    // deploy, or Cloudflare moving it): the screens' sockets died with the old
    // one and no close handler ran to say so.
    if (this.meta?.state === "live" && !this.meta.absent_since) {
      const now = Date.now();
      this.meta.absent_since = now;
      if (!this.meta.no_audio_since) Object.assign(this.meta, { no_audio_since: now, no_audio_why: "dropped" });
      await this.addEntry("event", { text: "relay restarted; screens must reconnect (no audio until they do)" });
      await this.armTimer();
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

  // "line" for the call, so it is never confused with Louie's phone's browser.
  ui(ws) { return ws && ws === this.phone ? "line" : this.screens.get(ws)?.ui ?? "device"; }

  // Audio plumbing (transcriber restarts, Claude's voice) concerns only a holding page.
  toHolder(msg) {
    if (this.holder === this.phone) return;
    try { this.holder?.send(JSON.stringify(msg)); } catch {}
  }

  toScreens(msg) {
    const frame = JSON.stringify(msg);
    for (const ws of this.screens.keys()) { try { ws.send(frame); } catch {} }
  }

  // Each screen learns where the audio is, and whether it is itself.
  // `since` is how long the audio has been where it is, in the relay's clock:
  // pages count from it with the offset `now` gives them.
  sendRoom() {
    const audio = this.holder ? this.ui(this.holder) : null;
    const call = this.meta.call ? { state: this.meta.call.state, up_at: this.meta.call.up_at ?? null } : null;
    const since = this.meta.audio_since ?? null, now = Date.now();
    for (const ws of this.screens.keys()) {
      try { ws.send(JSON.stringify({ type: "room", audio, mine: ws === this.holder, call, since, now })); } catch {}
    }
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

  // Each Monitor event wakes Claude, and a conversation has a turn every few
  // seconds, so listen-mode turns are held and then sent back to back: Monitor
  // delivers frames that close together as one notification, and each frame
  // keeps its own length budget. Device news and talk turns go at once.
  broadcast(e) {
    if (!this.talk() && e.kind === "turn") {
      (this.held ??= []).push(e);
      this.heldTimer ??= setTimeout(() => this.flushHeld(), Number(this.env.LISTEN_BATCH_MS ?? LISTEN_BATCH_MS));
      return;
    }
    this.flushHeld();
    this.sendEvent(e);
  }

  flushHeld() {
    clearTimeout(this.heldTimer);
    this.heldTimer = null;
    const held = this.held ?? [];
    this.held = [];
    for (const e of held) this.sendEvent(e);
  }

  sendEvent(e) {
    const frame = JSON.stringify(e);
    for (const ws of this.events) {
      try { ws.send(frame); } catch { this.events.delete(ws); }
    }
  }

  // --- turns -------------------------------------------------------------

  async flushTurn() {
    clearTimeout(this.holdTimer);
    this.holdTimer = null;
    const cur = this.cur;
    this.cur = null;
    const text = cur?.text.trim();
    // A lone "." from noise would read to Claude as Louie saying something.
    if (!text || !/[\p{L}\p{N}]/u.test(text)) return;
    // Clues to whether this was Louie, for Claude to weigh: Soniox's mean confidence, and how
    // far isolation turned the words down (near 0 for the voice it keeps).
    const scores = {};
    if (cur.toks) scores.conf = Math.round(cur.conf / cur.toks * 100) / 100;
    if (cur.raw > 0) scores.drop_db = Math.min(99, Math.round(10 * Math.log10(cur.raw / Math.max(cur.iso, 1e-9))));
    await this.addEntry("turn", { spk: cur.spk, t: Math.round(cur.t), text, ...scores });
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
      const at = offset + (tok.start_ms ?? 0) / 1000;
      if (tok.text !== "<end>" && this.isEcho(at)) continue;
      // Any speech while a talk turn is held pushes its delivery back.
      if (this.holdTimer && tok.text !== "<end>") this.holdTurn();
      if (!tok.is_final) {
        if (tok.text !== "<end>") { pending += tok.text; pendingSpk ??= tok.speaker; }
        continue;
      }
      if (tok.text === "<end>") {
        if (this.talk() && this.cur && this.holdMs() > 0) { this.cur.gap = true; this.holdTurn(); }
        else await this.flushTurn();
        continue;
      }
      const spk = Number(tok.speaker ?? 0) || 0;
      if (this.cur && this.cur.spk !== spk) await this.flushTurn();
      if (!this.cur) this.cur = { spk, t: at, text: "", conf: 0, toks: 0, raw: 0, iso: 0 };
      // Soniox spaces words within an utterance, not across one.
      if (this.cur.gap && !/^\s/.test(tok.text)) this.cur.text += " ";
      this.cur.gap = false;
      this.cur.text += tok.text;
      if (tok.confidence != null) { this.cur.conf += tok.confidence; this.cur.toks++; }
      if (conn.iso && tok.end_ms != null) {
        const p = conn.iso.power(tok.start_ms ?? 0, tok.end_ms);
        this.cur.raw += p.raw;
        this.cur.iso += p.iso;
      }
      if (!this.talk() && at - this.cur.t >= TURN_SPLIT_S && /[.?!]$/.test(tok.text)) await this.flushTurn();
    }
    this.pending = pending ? { spk: Number(pendingSpk ?? 0) || 0, text: pending } : null;
    if (this.speaking?.bargeIn && !this.speaking.stopping) {
      const heard = (this.tail()?.text ?? "").split(/\s+/).filter(Boolean).length;
      if (heard >= BARGE_IN_WORDS) this.bargeIn("Louie spoke");
    }
  }

  holdTurn() {
    clearTimeout(this.holdTimer);
    this.holdTimer = setTimeout(() => this.flushTurn().catch(() => {}), this.holdMs());
  }

  holdMs() { return this.meta.hold_ms ?? Number(this.env.TALK_HOLD_MS ?? TALK_HOLD_MS); }

  // Louie's change is logged, not news: each tap of the stepper would wake Claude.
  async setHold(ms, byLouie) {
    if (!this.talk()) return { ok: false, error: "a listen session has no turn wait" };
    if (!Number.isFinite(ms) || ms < HOLD_MIN_MS || ms > HOLD_MAX_MS) {
      return { ok: false, error: `the turn wait must be ${HOLD_MIN_MS / 1000}-${HOLD_MAX_MS / 1000}s` };
    }
    this.meta.hold_ms = Math.round(ms);
    const s = this.meta.hold_ms / 1000;
    const when = s ? `after ${s}s of silence` : "as soon as the transcriber hears him finish";
    if (byLouie) await this.addEntry("setting", { text: `Louie set the turn wait to ${s}s: his turns reach you ${when}` });
    else await this.saveMeta();
    this.toScreens({ type: "settings", hold_ms: this.meta.hold_ms });
    return { ok: true, hold_s: s };
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
  // `format` is Soniox's for raw audio (the phone line); a page sends webm, which Soniox detects.
  // Isolated audio reaches Soniox as PCM either way.
  async startSegment(format = { audio_format: "auto" }) {
    // Synchronous up to the first await: the header chunk arrives right behind
    // the `seg` message and must land in pendingAudio.
    this.closeTranscriber();
    this.pendingAudio = [];
    this.sttOpening = true;
    const seg = { n: this.meta.segs.length, start_s: this.elapsed(),
                  ...(format.audio_format !== "auto" ? { raw: format } : {}) };
    this.meta.segs.push(seg);
    const rate = this.isolationRate(format);
    let iso = null;
    if (rate) {
      if (format.audio_format === "auto") this.decoder = new WebmOpusDecoder(OPUS_WASM, rate);
      iso = this.iso = new Isolation({
        url: this.env.KRISP_URL ?? KRISP_URL, token: () => this.krispToken(), rate,
        send: (frame) => this.toTranscriber(frame),
        report: (why) => this.addEntry("event", {
          text: `voice isolation off until the audio restarts (${why}); the transcriber hears unfiltered audio` }).catch(() => {}),
      });
      format = { audio_format: "pcm_s16le", sample_rate: rate, num_channels: 1 };
    }
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
    const conn = { ws, opened: Date.now(), error: null, iso };
    ws.send(JSON.stringify({
      model: SONIOX_MODEL, ...format, language_hints: ["en"],
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
      this.checkAudioStall();
    }, KEEPALIVE_MS);
  }

  // Talk mode's isolation runs on 16-bit PCM: a page's webm is decoded to it,
  // and the phone line's L16 already is. Other call codecs go as they are.
  isolationRate(format) {
    if (!this.talk() || !this.meta.isolate) return 0;
    if (!this.env.KRISP_API_KEY) {
      if (!this.isoUnset) {
        this.isoUnset = true;
        this.addEntry("event", { text: "voice isolation is unavailable (the relay has no KRISP_API_KEY); the transcriber hears unfiltered audio" }).catch(() => {});
      }
      return 0;
    }
    if (format.audio_format === "auto") return ISO_RATE;
    if (format.audio_format === "pcm_s16le" && [8000, 16000].includes(format.sample_rate)) return format.sample_rate;
    return 0;
  }

  // Session tokens are reused while they have a while left.
  async krispToken() {
    const now = Date.now();
    if (this.krisp && this.krisp.until - now > 10 * 60_000) return this.krisp.key;
    const resp = await fetch(`${this.env.KRISP_API_URL ?? KRISP_API_URL}/v2/sdk/tokens/session`, {
      method: "POST",
      headers: { Authorization: `api-key ${this.env.KRISP_API_KEY}`, "content-type": "application/json" },
      body: JSON.stringify({ usage_type: "viva", expiration_ttl: KRISP_TOKEN_MIN }),
    });
    const body = await resp.json().catch(() => ({}));
    if (!body.data?.session_key) throw new Error(`Krisp refused a token: ${body.error_code ?? `HTTP ${resp.status}`}`);
    this.krisp = { key: body.data.session_key, until: now + KRISP_TOKEN_MIN * 60_000 };
    return this.krisp.key;
  }

  toTranscriber(chunk) {
    if (this.stt) {
      try { this.stt.send(chunk); } catch {}
    } else if (this.sttOpening && this.pendingAudio.length < PENDING_AUDIO_MAX) {
      this.pendingAudio.push(chunk);
    }
  }

  // An empty frame makes Soniox send its last finals and close; the old
  // listener still lands them. Resolves on close (or after 3s), for end().
  closeTranscriber() {
    this.iso?.close();                  // the frames Krisp still holds, before the end of stream
    this.iso = null;
    this.decoder?.free();
    this.decoder = null;
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
    if (!this.live() || !this.holder) return;
    const why = conn.error ?? `ws ${code}`;
    // An immediate failure (bad key) would recur on every restart; stop looping.
    if (Date.now() - conn.opened < 5000 && ++this.quickFails >= 3) {
      await this.addEntry("event", { text: `transcriber keeps failing (${why}); no transcript until the device reconnects` });
      return;
    }
    await this.addEntry("event", { text: `transcriber dropped (${why}); restarting the recorder` });
    // Raw call audio can resume anywhere; a page answers with a fresh webm segment.
    if (this.holder === this.phone) await this.startSegment(this.phoneFormat);
    else this.toHolder({ type: "restart" });
  }

  // --- audio and archive -----------------------------------------------------

  // A connected page that stops sending is otherwise silent news: Chrome froze a
  // hidden page with its socket still open and its mic still held.
  checkAudioStall() {
    if (this.audioStalledAt || !this.holder || this.grace || !this.live() || !this.lastAudioAt) return;
    // A page that can resend what it recorded only matters once the grace is spent.
    const after = this.segRec != null ? this.graceMs() : AUDIO_STALL_MS;
    if (Date.now() - this.lastAudioAt < after) return;
    this.audioStalledAt = this.lastAudioAt;
    const who = this.ui(this.holder);
    this.addEntry("event", { text: `${who} stopped sending audio, with its socket still open: nothing is heard until it resumes` }).catch(() => {});
  }

  async onAudio(chunk) {
    this.lastAudioAt = Date.now();
    // Forward before any await, so chunks reach the transcriber in arrival order.
    if (this.iso) this.isolate(chunk);
    else this.toTranscriber(chunk);
    if (this.audioStalledAt) {
      const s = Math.round((this.lastAudioAt - this.audioStalledAt) / 1000);
      this.audioStalledAt = null;
      const who = this.ui(this.holder);
      this.addEntry("event", { text: `${who} audio is back after ${s}s; nothing was heard in between` }).catch(() => {});
    }
    if (this.meta.archive) await this.archive(chunk);
  }

  isolate(chunk) {
    if (!this.decoder) return this.iso.push(new Uint8Array(chunk));
    if (this.decoder.failed) return;          // until the fresh recorder's segment
    let pcm;
    try {
      pcm = this.decoder.push(chunk);
    } catch (e) {
      // A webm stream that can't be followed can't be resumed: a fresh recorder starts one.
      this.decoder.failed = true;
      this.addEntry("event", { text: `couldn't decode the page's audio (${e.message}); restarting the recorder` }).catch(() => {});
      return this.toHolder({ type: "restart" });
    }
    if (pcm.length) this.iso.push(new Uint8Array(pcm.buffer));
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
    if (this.grace) { clearTimeout(this.grace.timer); this.grace = null; }
    if (this.speaking) await this.finishSpeak(this.speaking, "interrupted", `session ended: ${reason}`);
    await this.closeVoice();
    await this.closeTranscriber();          // its last finals land before the end entry
    await this.flushTurn();
    await this.flushArchive();
    this.meta.state = "ended";
    this.meta.ended_at = Date.now();
    this.meta.end_reason = reason;
    await this.addEntry("event", { text: `session ended: ${reason}` });
    this.toScreens({ type: "ended", reason });
    for (const ws of this.events) { try { ws.close(1000, "session ended"); } catch {} }
    this.events.clear();
    await this.registry("/put", { id: this.meta.id, state: "ended" });
    await this.ctx.storage.setAlarm(Date.now() + KEEP_MS);
  }

  async alarm() {
    if (!(await this.load())) return;
    const now = Date.now();
    if (this.grace && now >= this.grace.at + this.graceMs()) await this.expireGrace();
    if (this.meta.state === "armed" && now - this.meta.created_at >= ARMED_TTL_MS) {
      this.meta.state = "expired";
      await this.saveMeta();
      await this.registry("/put", { id: this.meta.id, state: "expired" });
      await this.ctx.storage.setAlarm(now + KEEP_MS);
    } else if (this.live() && this.meta.absent_since && !this.meta.call && now - this.meta.absent_since >= DEVICE_GONE_MS) {
      await this.end(`no screen was open for ${Math.round(DEVICE_GONE_MS / 60000)} minutes`);
    } else if (this.live() && this.meta.no_audio_since && now - this.meta.no_audio_since >= NO_AUDIO_MAX_MS) {
      await this.end(`no audio for ${Math.round(NO_AUDIO_MAX_MS / 60000)} minutes`);
    } else if ((this.meta.state === "ended" || this.meta.state === "expired") &&
               now - (this.meta.ended_at ?? this.meta.created_at) >= KEEP_MS) {
      await this.ctx.storage.deleteAll();
      this.meta = null;
    } else if (this.live()) {
      await this.armTimer();
    }
  }

  // A room with no screen open ends after minutes; one Louie is in without audio
  // (he left it, or the screen holding it dropped) gets an hour.
  async armTimer() {
    const due = [this.meta.absent_since && this.meta.absent_since + DEVICE_GONE_MS,
                 this.meta.no_audio_since && this.meta.no_audio_since + NO_AUDIO_MAX_MS,
                 this.meta.grace && this.meta.grace.at + this.graceMs()].filter(Boolean);
    if (due.length) await this.ctx.storage.setAlarm(Math.min(...due));
  }

  // --- screens and the audio holder ----------------------------------------------
  //
  // Any number of pages can be in the room: each sees it and controls it. At
  // most one holds the audio; its mic is what the transcriber hears and
  // Claude's voice plays on it. Moving the audio ends the old holder's stream.

  async setHolder(ws, why) {
    const old = this.holder;
    if (old === ws) return;
    if (this.grace) { clearTimeout(this.grace.timer); this.grace = null; }
    delete this.meta.grace;
    this.segRec = null;
    this.segChunks = 0;
    this.resuming = null;
    let closing = null;
    if (old) {
      this.holder = null;            // nothing more of its audio is taken
      this.voiceReady = false;
      if (this.speaking) await this.finishSpeak(this.speaking, "interrupted", SPEAK_CUT_BY[why] ?? why);
      // The old stream's last words are still being recognised: they land on the
      // open turn, which closes once they have (at the end), not before.
      closing = this.closeTranscriber();
      if (old !== this.phone) { try { old.send(JSON.stringify({ type: "release" })); } catch {} }
    }
    this.holder = ws;
    this.meta.audio_since = ws ? Date.now() : null;
    this.mic = null;
    this.audioStalledAt = null;
    this.lastAudioAt = 0;
    Object.assign(this.meta, { hidden_since: null, mic_silent_since: null,
                               no_audio_since: ws ? null : Date.now(), no_audio_why: ws ? null : why,
                               no_audio_from: old && !ws ? this.ui(old) : null });
    await this.saveMeta();
    await this.armTimer();
    this.sendRoom();
    // Not awaited: the new holder's audio must not wait on the old stream.
    if (closing) closing.then(() => this.flushTurn()).catch(() => {});
  }

  // A screen asks for the audio, by tapping "This device" or by reconnecting
  // with the mic it held. Claude is told how long nothing was heard.
  async takeAudio(ws) {
    const old = this.holder;
    const gap = this.meta.no_audio_since ? Math.round((Date.now() - this.meta.no_audio_since) / 1000) : 0;
    const { no_audio_why: why, no_audio_from: from } = this.meta;
    const who = this.ui(ws);
    await this.setHolder(ws, `the audio moved to the ${who}`);
    let text;
    if (old) text = `audio moved from the ${this.ui(old)} to the ${who}`;
    else if (why === "unpicked") text = `recording started on the ${who}`;
    else if (why === "left") text = `Louie turned audio back on, on the ${who}, after ${gap}s; nothing was heard in between`;
    else if (from === who) text = `${who} reconnected after ${gap}s; nothing was heard in between`;
    else text = `audio back on the ${who} after ${gap}s; nothing was heard in between`;
    await this.addEntry("event", { text });
    // The call's audio has moved to a screen: the call itself has no further use.
    if (old && old === this.phone) await this.hangUp();
  }

  async leaveAudio() {
    if (!this.holder) return;
    const wasPhone = this.holder === this.phone;
    await this.setHolder(null, "left");
    await this.addEntry("event", { text: `Louie left audio: nothing is heard${this.talk() ? ", and you can't speak," : ""} until he turns it back on` });
    if (wasPhone) await this.hangUp();
  }

  // --- the phone line ----------------------------------------------------------------
  //
  // Telnyx rings a number (Louie's, passed in each time: none is kept) and its
  // webhooks report the call's progress. The number's last digits are all that
  // is stored.

  async telnyx(path, body) {
    const resp = await fetch(`${this.env.TELNYX_API_URL ?? TELNYX_API_URL}${path}`, {
      method: "POST", headers: { Authorization: `Bearer ${this.env.TELNYX_API_KEY}`, "content-type": "application/json" },
      body: JSON.stringify(body ?? {}),
    });
    const out = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(`Telnyx ${path} returned ${resp.status}: ${JSON.stringify(out.errors ?? out).slice(0, 200)}`);
    return out.data ?? {};
  }

  // Data may be dead when the call is wanted, so it can open a room no screen has.
  async goLive() {
    if (this.meta.state !== "armed") return;
    Object.assign(this.meta, { state: "live", started_at: Date.now(), no_audio_since: Date.now(),
                               no_audio_why: "unpicked" });
    await this.registry("/put", { id: this.meta.id, state: "live" });
    await this.armTimer();
  }

  async dial(to, byLouie) {
    if (this.meta.state !== "armed" && !this.live()) return { ok: false, error: `session is ${this.meta.state}` };
    if (!this.env.TELNYX_API_KEY || !this.env.TELNYX_CONNECTION_ID || !this.env.TELNYX_FROM) {
      return { ok: false, error: "the relay has no phone line set up" };
    }
    if (!E164_RE.test(String(to ?? ""))) return { ok: false, error: "the number must be E.164, like +14155550123" };
    if (this.meta.call) return { ok: false, error: `a call is already ${this.meta.call.state}` };
    const key = randomKey();
    const origin = this.meta.origin;
    let data;
    try {
      data = await this.telnyx("/v2/calls", {
        connection_id: this.env.TELNYX_CONNECTION_ID, from: this.env.TELNYX_FROM, to,
        timeout_secs: RING_S, preferred_codecs: "AMR-WB,G722,PCMU",
        webhook_url: `${origin}/telnyx`, client_state: btoa(JSON.stringify({ id: this.meta.id })),
        stream_url: `${origin.replace(/^http/, "ws")}/phone?id=${this.meta.id}&k=${key}`,
        stream_track: "inbound_track", stream_bidirectional_mode: "rtp", stream_bidirectional_codec: "L16",
        stream_bidirectional_sampling_rate: PHONE_RATE,
      });
    } catch (e) {
      await this.addEntry("event", { text: `could not call Louie's phone: ${e.message}` });
      return { ok: false, error: e.message };
    }
    await this.goLive();
    this.meta.call = { state: "dialing", ccid: data.call_control_id, key, to4: to.slice(-4), at: Date.now() };
    await this.addEntry("event", { text: `${byLouie ? "Louie asked for a call: " : ""}calling his phone (…${to.slice(-4)})` });
    this.sendRoom();
    return { ok: true, state: "dialing" };
  }

  async hangUp() {
    const call = this.meta.call;
    if (!call) return { ok: false, error: "no call" };
    try { await this.telnyx(`/v2/calls/${encodeURIComponent(call.ccid)}/actions/hangup`); }
    catch (e) { return { ok: false, error: e.message }; }
    return { ok: true };
  }

  // Webhooks for any other call (an old one, a retry) are ignored.
  async onTelnyx(event) {
    const type = event?.event_type, p = event?.payload ?? {};
    const call = this.meta.call;
    if (!call || p.call_control_id !== call.ccid) return;
    if (type === "call.initiated" && call.state === "dialing") {
      call.state = "ringing";
      await this.saveMeta();
    } else if (type === "call.answered") {
      call.state = "up";
      call.up_at = Date.now();
      await this.addEntry("event", { text: "phone line connected" });
    } else if (type === "call.hangup") {
      this.meta.call = null;
      if (!this.screens.size) this.meta.absent_since = Date.now();
      const how = [p.hangup_cause, p.hangup_source && `by the ${p.hangup_source}`].filter(Boolean).join(", ");
      const what = call.state === "up" ? `phone line ended after ${Math.round((Date.now() - call.up_at) / 1000)}s`
                                       : "the call to Louie's phone was not answered";
      await this.addEntry("event", { text: `${what}${how ? ` (${how})` : ""}` });
      await this.armTimer();
    } else {
      return;
    }
    this.sendRoom();
  }

  // Telnyx's media stream for the call. Once it starts, the line holds the
  // room's audio, and whatever held it lets go.
  acceptPhone() {
    const [client, server] = Object.values(new WebSocketPair());
    server.accept();
    server.addEventListener("message", (m) => this.onPhoneFrame(server, m.data).catch(() => {}));
    const gone = async () => {
      if (this.phone !== server) return;
      if (this.holder === server) await this.setHolder(null, "call ended");
      this.phone = null;
    };
    server.addEventListener("close", gone);
    server.addEventListener("error", gone);
    return new Response(null, { status: 101, webSocket: client });
  }

  async onPhoneFrame(ws, raw) {
    let m;
    try { m = JSON.parse(raw); } catch { return; }
    if (m.event === "start") {
      const f = m.start?.media_format ?? {};
      this.phone = ws;
      this.phoneFormat = { ...(PHONE_FORMATS[f.encoding] ?? PHONE_FORMATS.PCMU),
                           ...(f.sample_rate ? { sample_rate: Number(f.sample_rate) } : {}) };
      await this.takeAudio(ws);
      await this.startSegment(this.phoneFormat);
    } else if (m.event === "media" && ws === this.holder && (m.media?.track ?? "inbound") === "inbound") {
      await this.onAudio(Uint8Array.from(atob(m.media.payload ?? ""), (c) => c.charCodeAt(0)));
    } else if (m.event === "mark") {
      this.onMark(m.mark?.name);
    } else if (m.event === "stop") {
      if (ws === this.holder) await this.setHolder(null, "call ended");
      try { ws.close(1000); } catch {}
    }
  }

  async acceptDevice() {
    const [client, server] = Object.values(new WebSocketPair());
    server.accept();
    this.screens.set(server, { ui: null });
    await this.goLive();
    this.meta.absent_since = null;
    await this.saveMeta();
    await this.armTimer();

    // A socket that died without closing looks open to the page too: it reconnects when these stop.
    this.pinger ??= setInterval(() => {
      if (!this.screens.size) { clearInterval(this.pinger); this.pinger = null; return; }
      this.toScreens({ type: "ping" });
    }, SCREEN_PING_MS);

    server.addEventListener("message", (m) => {
      if (!this.screens.has(server)) return;
      if (typeof m.data !== "string") {
        if (server !== this.holder) return;
        this.segChunks++;
        return this.onAudio(m.data);
      }
      let msg;
      try { msg = JSON.parse(m.data); } catch { return; }
      return this.onDeviceMessage(server, msg);
    });
    const gone = () => { try { server.close(1000); } catch {} return this.dropScreen(server); };
    server.addEventListener("close", gone);
    server.addEventListener("error", gone);
    return new Response(null, { status: 101, webSocket: client });
  }

  async dropScreen(server, replaced = false) {
    if (!this.screens.has(server)) return;
    const who = this.ui(server);
    // The page closes soon after a stop, often before ending is done: not a drop.
    if (this.live() && !this.stopping && server === this.holder) {
      if (this.segRec != null && this.screens.get(server)?.dev) await this.beginGrace(server, replaced);
      else {
        await this.setHolder(null, "dropped");        // while it still has a name
        await this.addEntry("event", { text: `${who} holding the audio disconnected; no audio until it reconnects` });
      }
    }
    this.screens.delete(server);
    if (this.live() && !this.stopping && !this.screens.size && !this.meta.call) {
      this.meta.absent_since = Date.now();
      await this.saveMeta();
      await this.armTimer();
    }
  }

  // A page that drops while holding the audio keeps recording, and its next
  // socket can resume the transcriber's stream (rejoin). Claude hears of the
  // drop only if the grace runs out. Speech is cut when the socket died, as the
  // voice track rarely survives that, but not when a newer socket replaced a
  // live one. In meta too: after a restart, load() reports it.
  async beginGrace(server, replaced = false) {
    const g = { ws: server, dev: this.screens.get(server).dev, ui: this.ui(server), at: Date.now() };
    g.timer = setTimeout(() => this.expireGrace().catch(() => {}), this.graceMs());
    this.grace = g;
    this.meta.grace = { at: g.at, ui: g.ui };
    await this.saveMeta();
    await this.armTimer();
    if (replaced) return;
    this.voiceReady = false;
    if (this.speaking) await this.finishSpeak(this.speaking, "interrupted", SPEAK_CUT_BY.dropped);
  }

  graceMs() { return Number(this.env.RESUME_GRACE_MS ?? RESUME_GRACE_MS); }

  async expireGrace(quiet = false) {
    const g = this.grace;
    if (!g) return;
    clearTimeout(g.timer);
    this.grace = null;
    delete this.meta.grace;
    if (this.holder !== g.ws) return this.saveMeta();
    await this.setHolder(null, "dropped");
    // The dead socket has left the room, so its name comes from the grace; takeAudio reports the whole gap.
    Object.assign(this.meta, { no_audio_since: g.at, no_audio_from: g.ui });
    await this.saveMeta();
    await this.armTimer();
    if (!quiet) {
      const s = Math.round((Date.now() - g.at) / 1000);
      await this.addEntry("event", { text: `${g.ui} holding the audio disconnected ${s}s ago; no audio until it reconnects` });
    }
  }

  // A holding page's new socket, naming the recorder run it kept going.
  async rejoin(ws, msg) {
    const g = this.grace;
    if (g && g.dev === msg.dev) {
      if (msg.rec === this.segRec && (this.stt || this.sttOpening)) {
        clearTimeout(g.timer);
        this.grace = null;
        delete this.meta.grace;
        this.holder = ws;
        // The page answers `resumed`, or a fresh `seg` if it no longer has the chunks.
        this.resuming = { ui: g.ui, at: g.at, told: this.audioStalledAt != null,
                          why: msg.why ? `${String(msg.why).slice(0, 60)}${netNote(msg.at ?? {})}` : null };
        this.audioStalledAt = null;
        try { ws.send(JSON.stringify({ type: "resume", have: this.segChunks })); } catch {}
        return this.sendRoom();
      }
      await this.expireGrace(true);
    }
    if (this.holder) {
      try { ws.send(JSON.stringify({ type: "release" })); } catch {}
      return this.sendRoom();
    }
    await this.takeAudio(ws);
    try { ws.send(JSON.stringify({ type: "resume", have: -1 })); } catch {}   // a fresh segment
  }

  // Room controls come from any screen; audio plumbing only from the holder.
  async onDeviceMessage(ws, msg) {
    if (msg.type === "hello") {
      Object.assign(this.screens.get(ws), { ui: msg.ui ?? null, dev: msg.dev ?? null });
      // A device that left its page (a back swipe, a reload) may never have
      // closed the old socket: its newest page replaces it.
      for (const [other, info] of this.screens) {
        if (other === ws || !msg.dev || info.dev !== msg.dev) continue;
        try { other.close(4001, "replaced by this device's newer page"); } catch {}
        await this.dropScreen(other, true);        // a holder's starts a grace this page can resume
      }
      if (this.talk()) {
        // The device's remembered wait seeds a session nobody has set yet, so a
        // rejoin never undoes what Claude or Louie chose since.
        const pref = msg.hold_ms == null ? NaN : Number(msg.hold_ms);
        const seeded = this.meta.hold_ms == null && Number.isFinite(pref) && pref !== this.holdMs() &&
                       (await this.setHold(pref, true)).ok;
        if (!seeded) { try { ws.send(JSON.stringify({ type: "settings", hold_ms: this.holdMs() })); } catch {} }
      }
      // The latest note, without the buzz: it was news when it was sent.
      const last = this.meta.last_note;
      if (last) { try { ws.send(JSON.stringify({ type: "note", text: last.text, age_ms: Date.now() - last.at })); } catch {} }
      if (msg.holding && msg.rec != null) return this.rejoin(ws, msg);
      // A screen that held the mic before a drop takes it back, unless another has it now.
      if (msg.holding && !this.holder) return this.takeAudio(ws);
      if (msg.holding) { try { ws.send(JSON.stringify({ type: "release" })); } catch {} }
      return this.sendRoom();
    }
    if (msg.type === "take") return this.takeAudio(ws);
    if (msg.type === "leave") return this.leaveAudio();
    if (msg.type === "call") {
      const out = await this.dial(msg.to, true);
      if (!out.ok) { try { ws.send(JSON.stringify({ type: "call_failed", error: out.error })); } catch {} }
      return;
    }
    if (msg.type === "hangup") return this.hangUp();
    if (msg.type === "gap") return this.addEntry("net", { text: `${this.ui(ws)} heard nothing from the relay for ${(Number(msg.ms) / 1000).toFixed(1)}s${netNote(msg)}` });
    if (msg.type === "set") return this.setHold(Number(msg.hold_ms), true);
    if (msg.type === "stop") { this.stopping = true; return this.end("stopped on the device"); }
    if (ws !== this.holder) return;
    if (msg.type === "resumed" || msg.type === "seg") {
      const r = this.resuming;
      this.resuming = null;
      let started = null;
      if (msg.type === "seg") {
        this.segRec = msg.rec ?? null;
        this.segChunks = 0;
        started = this.startSegment();     // before any await: the header chunk is right behind
      }
      if (r) {
        const s = Math.round((Date.now() - r.at) / 1000);
        const why = r.why ? ` (${r.why})` : "";
        // A stall Claude was told about gets its answer as news; otherwise a blip stays quiet.
        await this.addEntry(started || r.told ? "event" : "net", { text: started
          ? `${r.ui} reconnected${why} after ${s}s, too late to resend: what was said in between is lost`
          : `${r.ui} reconnected${why} after ${s}s and resent what it recorded meanwhile: nothing was lost` });
      }
      return started;
    }
    if (msg.type === "loud") return this.onLoud(!!msg.on);
    if (msg.type === "voice_request" && this.talk()) return this.subscribeVoice();
    if (msg.type === "voice_ready") { this.voiceReady = true; return; }
    if (msg.type === "voice_answer" && this.voiceSub) return this.answerVoice(msg.sdp);
    if (msg.type === "mic") return this.onMic(msg.state);
    // Off screen with the mic still hearing (a locked phone, an app switch) is
    // not news: Claude hears of it only if the mic goes silent (onMic).
    if (msg.type === "vis") {
      const who = this.ui(ws);
      if (msg.state === "hidden" && this.meta.hidden_since == null) {
        this.meta.hidden_since = Date.now();
        this.meta.hidden_heard = this.micHears();
        if (this.meta.hidden_heard) return this.saveMeta();
        await this.addEntry("event", { text: `${who} page left the screen: the mic is OFF (a watch keeps ~5s) until it is back` });
      } else if (msg.state === "visible" && this.meta.hidden_since != null) {
        const s = Math.round((Date.now() - this.meta.hidden_since) / 1000);
        this.meta.hidden_since = null;
        if (this.meta.hidden_heard) return this.saveMeta();
        await this.addEntry("event", { text: `${who} page back on screen after ${s}s; nothing was heard in between` });
      }
    }
  }

  // The page's own reading of its input: off screen no longer settles it (README: the Chrome flag).
  async onMic(state) {
    this.mic = { state, at: Date.now() };
    const who = this.ui(this.holder);
    if (state === "silent" && this.meta.mic_silent_since == null) {
      this.meta.mic_silent_since = Date.now();
      const where = this.meta.hidden_since != null ? " with its page off screen" : "";
      await this.addEntry("event", { text: `${who} mic went silent${where}: nothing is heard until it is back` });
    } else if (state === "live" && this.meta.mic_silent_since != null) {
      const s = Math.round((Date.now() - this.meta.mic_silent_since) / 1000);
      this.meta.mic_silent_since = null;
      await this.addEntry("event", { text: `${who} mic is back after ${s}s silent; nothing was heard in between` });
    }
  }

  micHears() {
    return this.mic?.state === "live" && Date.now() - this.mic.at < MIC_STALE_MS;
  }

  // --- echo gate (talk mode) ---------------------------------------------------------

  onLoud(on) {
    const t = this.elapsed();
    if (on && this.loudFrom == null) this.loudFrom = t - GATE_LAG_S;
    else if (!on && this.loudFrom != null) {
      this.loudSpans.push([this.loudFrom, t]);
      if (this.loudSpans.length > SPANS_MAX) this.loudSpans.shift();
      this.loudFrom = null;
    }
  }

  // A word said while Claude's voice was playing, without sustained energy behind it, is echo.
  isEcho(at) {
    // On a call the handset cancels Claude's echo; the gate reads a page's mic.
    if (!this.talk() || this.holder === this.phone) return false;
    const speaking = this.speechSpans.some(([a, b]) => at >= a && (b == null || at <= b));
    if (!speaking) return false;
    const m = GATE_MARGIN_S;
    if (this.loudFrom != null && at >= this.loudFrom - m) return false;
    return !this.loudSpans.some(([a, b]) => at >= a - m && at <= b + m);
  }

  // --- what Claude reads ---------------------------------------------------------

  deviceStatus() {
    const now = Date.now();
    const s = (t) => (t ? Math.round((now - t) / 1000) : null);
    return {
      screens: [...this.screens.values()].map((v) => v.ui ?? "device"),
      audio: this.holder ? this.ui(this.holder) : null,
      reconnecting_s: this.grace ? s(this.grace.at) : null,
      no_audio_s: s(this.meta.no_audio_since),
      no_audio_why: this.meta.no_audio_why ?? null,
      absent_s: s(this.meta.absent_since),
      hidden_s: s(this.meta.hidden_since),
      mic: this.micHears() ? "live" : this.meta.mic_silent_since ? "silent" : "unknown",
      mic_silent_s: s(this.meta.mic_silent_since),
      call: this.meta.call ? { state: this.meta.call.state, to: `…${this.meta.call.to4}`,
                               s: s(this.meta.call.up_at ?? this.meta.call.at) } : null,
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
    const delivered = this.screens.size > 0;
    this.toScreens({ type: "note", text });
    this.meta.last_note = { text, at: Date.now() };     // for screens that join later
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
      if (!forPage) { this.voiceReady = false; this.toHolder({ type: "voice_restart" }); }
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
    this.voiceReady = false;
    try {
      const pub = await this.ensureVoice(true);
      const { sessionId } = await this.sfu("/sessions/new", "POST");
      const out = await this.sfu(`/sessions/${sessionId}/tracks/new`, "POST",
        { tracks: [{ location: "remote", sessionId: pub.sessionId, trackName: VOICE_TRACK }] });
      if (out.tracks?.[0]?.errorCode) throw new Error(`subscribe: ${out.tracks[0].errorCode}`);
      this.voiceSub = sessionId;
      this.toHolder({ type: "voice_offer", sdp: out.sessionDescription });
    } catch (e) {
      await this.addEntry("event", { text: `Claude's voice could not be connected (${e.message}); speak will fail` });
      this.toHolder({ type: "voice_failed", error: String(e.message).slice(0, 200) });
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

  // A CLI whose reply was lost asks again under the same call id and gets that
  // call's outcome, waiting if it is still speaking: never a second utterance.
  async speak(body) {
    const rid = typeof body?.rid === "string" && body.rid ? body.rid.slice(0, 64) : null;
    if (rid && this.speakCalls.has(rid)) return heldJson(this.speakCalls.get(rid));
    if (rid) {
      const kept = await this.ctx.storage.get(`speak:${rid}`);
      if (kept?.status === "started") {
        return json({ status: "interrupted", reason: "the relay restarted while speaking", spoken: "",
                      unspoken: String(body?.text ?? "").trim() });
      }
      if (kept) return json(kept);
    }
    const call = this.speakOutcome(body, rid);
    if (!rid) return call.then(([out, code]) => json(out, code));
    const outcome = call.then(([out]) => out);
    this.speakCalls.set(rid, outcome);
    call.finally(() => this.speakCalls.delete(rid)).catch(() => {});
    // A refusal answers at once with its own status; speech is held open with keepalives.
    const quick = await Promise.race([call, sleep(1000).then(() => null)]);
    return quick ? json(...quick) : heldJson(outcome);
  }

  async rememberSpeak(rid, out) {
    await this.ctx.storage.put(`speak:${rid}`, out);
    if (out.status !== "started") return;
    const kept = this.meta.speak_ids ??= [];
    kept.push(rid);
    if (kept.length > SPEAK_KEPT) await this.ctx.storage.delete(`speak:${kept.shift()}`);
    await this.saveMeta();
  }

  // [result, HTTP status]
  async speakOutcome(body, rid) {
    const text = String(body?.text ?? "").trim();
    if (!this.talk()) return [{ ok: false, error: "this is a listen session: Claude has no voice in it" }, 409];
    if (!text) return [{ ok: false, error: "nothing to say" }, 400];
    if (!this.live()) return [{ ok: false, error: `session is ${this.meta.state}` }, 409];
    if (this.speaking) return [{ ok: false, error: "already speaking" }, 409];
    if (!this.holder) {
      return [{ status: "not_delivered", reason: SPEAK_CUT_BY[this.meta.no_audio_why] ?? "nothing in the room has audio",
                spoken: "", unspoken: text }, 200];
    }
    // A call plays whatever reaches it; a page needs its voice track up first.
    // A page reconnecting after a blip still holds the audio, so this waits for it too.
    const onPhone = this.holder === this.phone;
    if (!onPhone) {
      if (!this.voiceOut) {
        try { await this.ensureVoice(); } catch (e) { return [{ status: "error", error: e.message }, 502]; }
        if (!this.voiceOut) return [{ status: "error", error: "the voice track is not connected" }, 502];
      }
      const waitUntil = Date.now() + Number(this.env.VOICE_READY_WAIT_MS ?? VOICE_READY_WAIT_MS);
      while (!this.voiceReady && this.holder && Date.now() < waitUntil) await new Promise((r) => setTimeout(r, 100));
      if (!this.voiceReady) {
        return [{ status: "not_delivered", reason: this.holder ? "Claude's voice is not connected on the device"
                  : SPEAK_CUT_BY[this.meta.no_audio_why] ?? "nothing in the room has audio", spoken: "", unspoken: text }, 200];
      }
    }
    if (this.speaking) return [{ ok: false, error: "already speaking" }, 409];

    // Claude says which entry it last read; a turn after that means it would be
    // answering a moment that has passed, so hand the turn back instead.
    if (Number.isInteger(body?.since)) {
      const unseen = (await this.entriesAfter(body.since)).filter((e) => e.kind === "turn");
      if (unseen.length) {
        return [{ status: "not_spoken", reason: "Louie said something you haven't read", spoken: "",
                  unspoken: text, turns: unseen.map((e) => ({ n: e.n, text: e.text })) }, 200];
      }
    }
    // Never talk over Louie: his turn arrives as an event, and Claude may want to say something else.
    const open = this.tail();
    if (open) return [{ status: "not_spoken", reason: "Louie is talking", spoken: "", unspoken: text,
                        heard_so_far: open.text }, 200];

    let ws;
    try {
      const resp = await fetch(this.env.SONIOX_TTS_URL, { headers: this.sonioxHeaders() });
      ws = resp.webSocket;
      if (!ws) return [{ status: "error", error: `TTS refused the connection (HTTP ${resp.status})` }, 502];
    } catch (e) {
      return [{ status: "error", error: `TTS unreachable (${e})` }, 502];
    }
    ws.accept();
    if (this.speaking) { try { ws.close(1000); } catch {} return [{ ok: false, error: "already speaking" }, 409]; }
    const sid = crypto.randomUUID().slice(0, 8);
    const rate = onPhone ? this.phoneFormat?.sample_rate ?? 8000 : TTS_CONFIG.sample_rate;
    const sp = this.speaking = {
      sid, rid, ws, text, chars: [], ends: [], queue: [], sentBytes: 0, firstAt: null,
      genEnded: false, genDone: false, stopping: null, guard: null, pump: null, resolve: null,
      bargeIn: body?.barge_in !== false,
      // A call reports, by marks, how much of the audio has played. Chunks are
      // 100 ms: at 48 kHz, doubled to stereo, under the SFU's 32 KB packet cap.
      phone: onPhone ? this.phone : null, markedBytes: 0, bps: rate * 2, chunk: rate * 2 / 10,
    };
    const done = new Promise((r) => { sp.resolve = r; });
    ws.addEventListener("message", (m) => this.onTts(sp, m.data));
    ws.addEventListener("close", () => { if (this.speaking === sp && !sp.genEnded) this.finishSpeak(sp, "error", "TTS closed early"); });
    ws.send(JSON.stringify({ ...TTS_CONFIG, sample_rate: rate, stream_id: sid }));
    ws.send(JSON.stringify({ stream_id: sid, text, text_end: true }));
    this.toHolder({ type: "tts_start", sid });
    sp.span = [this.elapsed(), null];
    this.speechSpans.push(sp.span);
    if (this.speechSpans.length > SPANS_MAX) this.speechSpans.shift();
    sp.pump = setInterval(() => this.pumpVoice(sp), PUMP_MS);
    sp.guard = setTimeout(() => this.finishSpeak(sp, "error", "TTS produced no end of audio"), SPEAK_GEN_TIMEOUT_MS);
    if (rid) await this.rememberSpeak(rid, { status: "started" });
    return [await done, 200];
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

  // Keeps the voice VOICE_LEAD_S ahead of real time and no further, so a
  // barge-in has little queued in the SFU (or the call) to talk over.
  pumpVoice(sp) {
    if (this.speaking !== sp || sp.stopping) return;
    const now = Date.now();
    const sentS = () => sp.sentBytes / sp.bps;
    while (sp.queue.length && (sp.firstAt == null || sentS() < (now - sp.firstAt) / 1000 + VOICE_LEAD_S)) {
      let chunk = sp.queue[0];
      if (chunk.length > sp.chunk) {
        sp.queue[0] = chunk.subarray(sp.chunk);
        chunk = chunk.subarray(0, sp.chunk);
      } else {
        sp.queue.shift();
      }
      if (chunk.length % 2) chunk = chunk.subarray(0, chunk.length - 1);   // a split sample: not worth carrying
      sp.firstAt ??= now;
      sp.sentBytes += chunk.length;
      if (sp.phone) this.toCall(sp, chunk);
      else { try { this.voiceOut?.send(voicePacket(chunk)); } catch {} }
    }
    if (sp.genEnded && !sp.queue.length && sp.firstAt != null) {
      const endsAt = sp.firstAt + sentS() * 1000 + this.playoutDelayMs() + (sp.phone ? MARK_GRACE_MS : 0);
      if ((sp.phone && sp.markedBytes >= sp.sentBytes) || now >= endsAt) this.finishSpeak(sp, "done");
    }
  }

  // Each chunk, as L16, is followed by a mark naming how far the audio reaches;
  // Telnyx returns the mark once that much has played. Telnyx's L16 is
  // little-endian, as Soniox renders it: network order played as static.
  toCall(sp, chunk) {
    let bin = "";
    for (let i = 0; i < chunk.length; i += 0x8000) bin += String.fromCharCode(...chunk.subarray(i, i + 0x8000));
    try {
      sp.phone.send(JSON.stringify({ event: "media", media: { payload: btoa(bin) } }));
      sp.phone.send(JSON.stringify({ event: "mark", mark: { name: `${sp.sid}:${sp.sentBytes}` } }));
    } catch {}
  }

  // Marks returned after a `clear` were never played, so they count only until then.
  onMark(name) {
    const sp = this.speaking;
    const [sid, bytes] = String(name ?? "").split(":");
    if (!sp?.phone || sp.stopping || sid !== sp.sid) return;
    sp.markedBytes = Math.max(sp.markedBytes, Number(bytes) || 0);
  }

  playoutDelayMs() { return Number(this.env.VOICE_PLAYOUT_MS ?? VOICE_PLAYOUT_MS); }

  played(sp) {
    if (sp.phone) return sp.markedBytes / sp.bps;
    if (sp.firstAt == null) return 0;
    const s = (Date.now() - sp.firstAt - this.playoutDelayMs()) / 1000;
    return Math.max(0, Math.min(s, sp.sentBytes / sp.bps));
  }

  // The page mutes at once, and a call drops its queue; what was still in
  // flight goes unheard.
  bargeIn(reason) {
    const sp = this.speaking;
    if (!sp || sp.stopping) return;
    sp.stopping = reason;
    this.stopVoice(sp);
    this.finishSpeak(sp, "interrupted", reason);
  }

  stopVoice(sp) {
    if (sp.phone) { try { sp.phone.send(JSON.stringify({ event: "clear" })); } catch {} }
    else this.toHolder({ type: "tts_stop", sid: sp.sid });
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
    if (sp.span) sp.span[1] = this.elapsed() + ECHO_TAIL_S;
    clearTimeout(sp.guard);
    clearInterval(sp.pump);
    if (!sp.genDone) { try { sp.ws.send(JSON.stringify({ stream_id: sp.sid, cancel: true })); } catch {} }
    try { sp.ws.close(1000); } catch {}
    if (status === "error") this.stopVoice(sp);
    else if (status === "done") this.toHolder({ type: "tts_done", sid: sp.sid });
    const { spoken, unspoken } = status === "done" ? { spoken: sp.text, unspoken: "" }
      : status === "error" ? { spoken: "", unspoken: sp.text } : this.splitSpoken(sp, played);
    if (spoken) await this.addEntry("said", { text: spoken, interrupted: status !== "done" });
    const out = {
      status, spoken, unspoken,
      ...(reason ? { reason } : {}),
      ...(status === "interrupted" ? { heard_so_far: this.tail()?.text ?? "" } : {}),
      audio_s: Math.round((sp.sentBytes / sp.bps) * 10) / 10,
    };
    if (sp.rid) await this.rememberSpeak(sp.rid, out).catch(() => {});
    sp.resolve(out);
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
        headers: { Authorization: `Token ${this.env.DEEPGRAM_API_KEY}`, "content-type": seg.raw ? "audio/wav" : "audio/webm" },
        body: seg.raw ? wav(seg.raw, concat(parts)) : concat(parts),
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
        mode: b.mode, isolate: !!b.isolate, event_key: randomKey(), origin: b.origin, hold_ms: b.hold_ms ?? null,
        state: "armed", created_at: Date.now(), started_at: null, ended_at: null,
        end_reason: null, seq: 0, news_seq: 0, segs: [],
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
    if (path === "/phone") {
      if (req.headers.get("Upgrade") !== "websocket") return json({ error: "expected a websocket" }, 426);
      if (!this.meta.call?.key || q.get("k") !== this.meta.call.key) return json({ error: "unauthorized" }, 401);
      return this.acceptPhone();
    }
    if (path === "/tts-in") {
      if (req.headers.get("Upgrade") !== "websocket") return json({ error: "expected a websocket" }, 426);
      if (!this.meta.voice_key || q.get("k") !== this.meta.voice_key) return json({ error: "unauthorized" }, 401);
      return this.acceptVoiceIn();
    }
    if (path === "/speak") return this.speak(await req.json().catch(() => null));
    if (path === "/set") {
      const b = await req.json().catch(() => ({}));
      const out = await this.setHold(Number(b?.hold_s) * 1000, false);
      return json(out, out.ok ? 200 : 400);
    }
    if (path === "/call") {
      const out = await this.dial((await req.json().catch(() => ({})))?.to, false);
      return json(out, out.ok ? 200 : 400);
    }
    if (path === "/hangup") {
      const out = await this.hangUp();
      return json(out, out.ok ? 200 : 400);
    }
    if (path === "/telnyx-event") {
      await this.onTelnyx(await req.json());
      return json({ ok: true });
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
      const raw = this.meta.segs.find((s) => s.n === seg)?.raw;
      return raw ? new Response(wav(raw, concat(parts)), { headers: { "content-type": "audio/wav" } })
                 : new Response(concat(parts), { headers: { "content-type": "audio/webm" } });
    }
    if (path === "/status") {
      return json({ ...this.meta, device: this.deviceStatus(), elapsed_s: Math.round(this.elapsed()),
                    ...(this.talk() ? { hold_s: this.holdMs() / 1000 } : {}) });
    }
    return json({ error: "not found" }, 404);
  }
}

// ---------------------------------------------------------------------------

const DEVICE_ROUTES = new Set(["/armed", "/device"]);
const CLI_ROUTES = new Set(["/session", "/watch", "/note", "/speak", "/set", "/call", "/hangup", "/stop",
                            "/transcript", "/status", "/audio", "/pair"]);

// Telnyx signs `{timestamp}|{raw body}` (a webhook) or `{timestamp}|{stream_url}`
// (its media socket) with the account's Ed25519 key. An unset public key refuses
// everything, like the relay's other gates.
async function telnyxSigned(env, req, signed) {
  const sig = req.headers.get("telnyx-signature-ed25519"), ts = req.headers.get("telnyx-timestamp");
  if (!env.TELNYX_PUBLIC_KEY || !sig || !/^\d+$/.test(ts ?? "")) return false;
  if (Math.abs(Date.now() / 1000 - Number(ts)) > WEBHOOK_SKEW_S) return false;
  const bytes = (b64) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  try {
    const key = await crypto.subtle.importKey("raw", bytes(env.TELNYX_PUBLIC_KEY), { name: "Ed25519" }, false, ["verify"]);
    return await crypto.subtle.verify({ name: "Ed25519" }, key, bytes(sig), new TextEncoder().encode(`${ts}|${signed}`));
  } catch {
    return false;
  }
}
// Gated inside the session by its own keys: a Monitor URL never carries the CLI
// token, and the SFU adapter never sees it.
const EVENT_ROUTES = new Set(["/events", "/tts-in", "/phone"]);

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const path = url.pathname;
    const q = url.searchParams;

    if (path === "/" && req.method === "GET") {
      return new Response(PAGE, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
    }
    if (AEC_FILES[path] && req.method === "GET") {
      const [body, type] = AEC_FILES[path];
      return new Response(body, { headers: { "content-type": type, "cache-control": "no-cache" } });
    }
    if (path === "/health") {
      return json({ ok: true, secrets: {
        CLI_TOKEN: !!env.CLI_TOKEN, DEVICE_TOKEN: !!env.DEVICE_TOKEN,
        SONIOX_API_KEY: !!env.SONIOX_API_KEY, DEEPGRAM_API_KEY: !!env.DEEPGRAM_API_KEY,
        REALTIME_APP_ID: !!env.REALTIME_APP_ID, REALTIME_APP_TOKEN: !!env.REALTIME_APP_TOKEN,
      }, phone_line: {
        TELNYX_API_KEY: !!env.TELNYX_API_KEY, TELNYX_PUBLIC_KEY: !!env.TELNYX_PUBLIC_KEY,
        TELNYX_CONNECTION_ID: !!env.TELNYX_CONNECTION_ID, TELNYX_FROM: !!env.TELNYX_FROM,
      }, voice_isolation: { KRISP_API_KEY: !!env.KRISP_API_KEY } });
    }

    // Telnyx's webhooks: the signature is the gate, and client_state names the session.
    if (path === "/telnyx" && req.method === "POST") {
      const body = await req.text();
      if (!(await telnyxSigned(env, req, body))) return json({ error: "bad signature" }, 401);
      let event, sid;
      try {
        event = JSON.parse(body).data;
        sid = JSON.parse(atob(event.payload.client_state)).id;
      } catch {
        return json({ ok: true });                 // not one of ours
      }
      if (!ID_RE.test(sid ?? "")) return json({ ok: true });
      const stub = env.SESSION.get(env.SESSION.idFromName(sid));
      await stub.fetch("https://session/telnyx-event", { method: "POST", body: JSON.stringify(event) });
      return json({ ok: true });                   // Telnyx retries anything else
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
      let hold_ms = null;
      if (b.hold_s != null) {
        hold_ms = Math.round(Number(b.hold_s) * 1000);
        if (mode !== "talk") return json({ error: "a listen session has no turn wait" }, 400);
        if (!(hold_ms >= HOLD_MIN_MS && hold_ms <= HOLD_MAX_MS)) {
          return json({ error: `the turn wait must be ${HOLD_MIN_MS / 1000}-${HOLD_MAX_MS / 1000}s` }, 400);
        }
      }
      // Isolation keeps one voice: listening needs everyone's.
      if (b.isolate && mode !== "talk") return json({ error: "voice isolation is for talk sessions only" }, 400);
      const keyterms = (b.keyterms ?? []).map((t) => String(t).trim()).filter(Boolean).slice(0, 100);
      const claim = await (await registry.fetch("https://registry/claim", {
        method: "POST", body: JSON.stringify({ label: b.label ?? "", mode }) })).json();
      if (!claim.id) return json(claim, 500);
      const stub = env.SESSION.get(env.SESSION.idFromName(claim.id));
      return stub.fetch("https://session/create", { method: "POST",
        body: JSON.stringify({ id: claim.id, label: b.label ?? "", keyterms, archive: !!b.archive, mode,
                               isolate: !!b.isolate, hold_ms, origin: url.origin }) });
    }

    // The stream URL as Telnyx was given it, which a Worker sees with an http scheme.
    if (path === "/phone" && !(await telnyxSigned(env, req, req.url.replace(/^http/, "ws")))) {
      return json({ error: "bad signature" }, 401);
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
