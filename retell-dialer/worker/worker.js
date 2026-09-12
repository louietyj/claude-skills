// Consult queue as a Durable Object — one addressable instance, so the held
// /consult request and the /answer that releases it always meet.
//
//   POST /consult   <- Retell. Held open until answered or punted.
//   POST /event     <- Retell webhooks. Captures the call as Retell saw it.
//   GET  /poll      <- Claude. Long-polls, returns a pending question.
//   POST /answer    <- Claude. Releases the held /consult.
//   GET  /pending   <- Claude. Non-blocking peek.
//   GET  /call      <- Claude. The captured webhook payload for one call_id.

const PUNT_MS = 90_000;
const POLL_MS = 220_000;
const CALL_TTL_MS = 6 * 60 * 60 * 1000;
const PUNT = "Tell them you'll confirm and call back shortly. Do not guess.";
const json = (o, c = 200) =>
  new Response(JSON.stringify(o), { status: c, headers: { "content-type": "application/json" } });

export class ConsultQueue {
  constructor(ctx) {
    this.ctx = ctx;
    this.pending = new Map();   // call_id -> {q, transcript, at, resolve, timer}
  }

  async fetch(req) {
    const { pathname, searchParams } = new URL(req.url);
    const body = async () => { try { return await req.json(); } catch { return null; } };

    if (pathname === "/consult") {
      const b = await body();
      if (!b) return json({ error: "consult: body was not JSON" }, 400);
      const id = b?.call?.call_id ?? `anon-${Date.now()}`;

      // Idempotency: Retell may retry (max_retry) and re-POST the same consult.
      // Retire the previous entry for this call_id so a retry never orphans a
      // socket. Delivery is idempotent: /poll may hand out the same question
      // more than once, and only /answer or the punt removes it.
      const prev = this.pending.get(id);
      if (prev) {
        clearTimeout(prev.timer);
        this.pending.delete(id);
        try { prev.resolve(json(PUNT)); } catch {}
      }

      return new Promise(resolve => {
        const timer = setTimeout(() => {
          if (this.pending.has(id)) { this.pending.delete(id); resolve(json(PUNT)); }
        }, PUNT_MS);
        this.pending.set(id, {
          q: b?.args?.question ?? "(no question)",
          transcript: b?.call?.transcript ?? null,
          at: prev?.at ?? Date.now(),
          resolve, timer,
        });
      });
    }

    if (pathname === "/poll") {
      // `?wait=` lets the client end a window on its own terms. Without it the
      // only way to stop short of POLL_MS is to abandon the socket, which
      // leaves a loop spinning here until it notices — the exact shape of the
      // orphaned-poll bug. A caller that wants to check call status between
      // windows should ask for a short wait, not time out on us.
      const asked = Number(searchParams.get("wait"));
      const waitMs = Number.isFinite(asked) && asked > 0
        ? Math.min(asked * 1000, POLL_MS)
        : POLL_MS;
      const deadline = Date.now() + waitMs;
      while (Date.now() < deadline) {
        // Bail the moment the client goes away, so an abandoned loop can never
        // outlive its caller and consume a question nobody receives.
        if (req.signal?.aborted) return json({ pending: null });
        const oldest = [...this.pending].sort((a, b) => a[1].at - b[1].at)[0];
        if (oldest) {
          const [id, p] = oldest;
          return json({ call_id: id, question: p.q, waiting_ms: Date.now() - p.at, transcript: p.transcript });
        }
        await new Promise(r => setTimeout(r, 250));
      }
      return json({ pending: null });
    }

    if (pathname === "/answer") {
      const b = await body();
      const call_id = b?.call_id ?? searchParams.get("call_id");
      const text    = b?.text    ?? searchParams.get("text");
      if (!call_id || !text) {
        return json({ error: "answer: need call_id and text in a JSON body (or as query params)" }, 400);
      }
      const p = this.pending.get(call_id);
      if (!p) return json({ ok: false, error: "no such pending consult" }, 404);
      clearTimeout(p.timer);
      this.pending.delete(call_id);
      p.resolve(json(text));
      return json({ ok: true, waited_ms: Date.now() - p.at });
    }

    // Retell's webhooks, captured verbatim. Two jobs: a hangup releases any
    // consult still held for that call, instead of leaving the tool to time
    // out; and the payload is kept so Claude can read the call as Retell sent
    // it, which is the only copy that exists before post-call PII scrubbing
    // rewrites the stored one.
    if (pathname === "/event") {
      const b = await body();
      if (!b) return json({ error: "event: body was not JSON" }, 400);
      const id = b?.call?.call_id;
      const event = b?.event ?? "unknown";
      if (!id) return json({ ok: false, error: "no call_id" }, 400);

      if (event === "call_ended" || event === "call_analyzed") {
        const held = this.pending.get(id);
        if (held) {
          clearTimeout(held.timer);
          this.pending.delete(id);
          try { held.resolve(json(PUNT)); } catch {}
        }
      }
      // Keyed by event as well as call: call_ended and call_analyzed carry
      // different things, and the later one must not overwrite the earlier.
      await this.ctx.storage.put(`call:${id}:${event}`,
        { at: Date.now(), payload: b });
      return json({ ok: true, event, call_id: id });
    }

    if (pathname === "/call") {
      const id = searchParams.get("call_id");
      if (!id) return json({ error: "call: need call_id" }, 400);
      const found = await this.ctx.storage.list({ prefix: `call:${id}:` });
      const now = Date.now();
      const events = {};
      for (const [key, value] of found) {
        if (now - value.at > CALL_TTL_MS) { await this.ctx.storage.delete(key); continue; }
        events[key.split(":").pop()] = value.payload;
      }
      return json({ call_id: id, events: Object.keys(events), payloads: events });
    }

    if (pathname === "/pending") {
      return json({
        count: this.pending.size,
        items: [...this.pending].map(([id, p]) =>
          ({ call_id: id, question: p.q, waiting_ms: Date.now() - p.at })),
      });
    }

    return json({ error: "not found" }, 404);
  }
}

export default {
  async fetch(req, env) {
    const { pathname, searchParams } = new URL(req.url);
    if (pathname === "/health") return json({ ok: true });

    // Retell's own callbacks cannot carry a query param, so they are the only
    // unauthenticated routes; everything Claude uses needs the shared secret.
    const fromRetell = pathname === "/consult" || pathname === "/event";
    if (!fromRetell && searchParams.get("token") !== env.CONSULT_TOKEN) {
      return json({ error: "unauthorized" }, 401);
    }
    // Single named instance — all traffic lands on the same object.
    return env.QUEUE.get(env.QUEUE.idFromName("main")).fetch(req);
  },
};
