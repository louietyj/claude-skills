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
const OUTCOME_NOTES = {
  answered: "Already answered with the text above. If you do not remember sending it, " +
            "you probably did, in a turn that was interrupted -- claude.ai drops aborted " +
            "tool calls from the transcript. To change course now, use `steer`.",
  timed_out: "Nobody answered within 90s, so the agent was told to promise a callback. " +
             "If it still matters, use `steer`.",
  call_ended: "The call ended before this was answered.",
};
const json = (o, c = 200) =>
  new Response(JSON.stringify(o), { status: c, headers: { "content-type": "application/json" } });

export class ConsultQueue {
  constructor(ctx) {
    this.ctx = ctx;
    this.pending = new Map();   // consult_id -> {call_id, q, transcript, at, resolve, timer}
  }

  // Every consult ends exactly one way -- answered, timed out, or its call
  // ended -- and the outcome is kept, so an answer that arrives too late is
  // told what happened rather than handed a bare 404.
  async settle(consultId, outcome, reply, text) {
    const p = this.pending.get(consultId);
    if (!p) return null;
    clearTimeout(p.timer);
    this.pending.delete(consultId);
    try { p.resolve(json(reply)); } catch {}
    await this.ctx.storage.put(`consult:${consultId}`,
      { question: p.q, outcome, ...(text ? { text } : {}), at: new Date().toISOString() });
    return p;
  }

  queuedFor(callId) {
    return [...this.pending]
      .filter(([, p]) => p.call_id === callId)
      .sort((a, b) => a[1].at - b[1].at);
  }

  async fetch(req) {
    const { pathname, searchParams } = new URL(req.url);
    const body = async () => { try { return await req.json(); } catch { return null; } };

    if (pathname === "/consult") {
      const b = await body();
      if (!b) return json({ error: "consult: body was not JSON" }, 400);
      const callId = b?.call?.call_id ?? `anon-${Date.now()}`;
      const q = b?.args?.question ?? "(no question)";

      // Retell retries a consult (max_retry) by re-POSTing the same question,
      // and the retry takes over the original's slot rather than queueing a
      // duplicate. A different question is a second consult and queues behind
      // the first: replacing it would stall a question nobody ever saw.
      // Delivery is idempotent -- /poll hands out the oldest until it settles.
      const retry = this.queuedFor(callId).find(([, p]) => p.q === q);
      const consultId = retry ? retry[0] : `${callId}:q${crypto.randomUUID().slice(0, 4)}`;
      if (retry) {
        clearTimeout(retry[1].timer);
        try { retry[1].resolve(json(PUNT)); } catch {}
      }

      return new Promise(resolve => {
        const timer = setTimeout(() => this.settle(consultId, "timed_out", PUNT), PUNT_MS);
        this.pending.set(consultId, {
          call_id: callId, q,
          transcript: b?.call?.transcript ?? null,
          at: retry ? retry[1].at : Date.now(),
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
      const callId = searchParams.get("call_id");
      const deadline = Date.now() + waitMs;
      while (Date.now() < deadline) {
        // Bail the moment the client goes away, so an abandoned loop can never
        // outlive its caller and consume a question nobody receives.
        if (req.signal?.aborted) return json({ pending: null });
        const oldest = [...this.pending]
          .filter(([, p]) => !callId || p.call_id === callId)
          .sort((a, b) => a[1].at - b[1].at)[0];
        if (oldest) {
          const [consultId, p] = oldest;
          return json({
            consult_id: consultId, call_id: p.call_id, question: p.q,
            waiting_ms: Date.now() - p.at, transcript: p.transcript,
            queued_behind: this.queuedFor(p.call_id).length - 1,
          });
        }
        await new Promise(r => setTimeout(r, 250));
      }
      return json({ pending: null });
    }

    if (pathname === "/answer") {
      const b = await body();
      const text = b?.text;
      let consultId = b?.consult_id;
      if (!text || !(consultId || b?.call_id)) {
        return json({ error: "answer: need consult_id and text in a JSON body" }, 400);
      }
      // A client that predates consult ids names only the call. That can only
      // be resolved while exactly one consult is waiting on it.
      if (!consultId) {
        const queued = this.queuedFor(b.call_id);
        if (queued.length > 1) {
          return json({ ok: false, error: "several consults are waiting; answer by consult_id",
                        pending: queued.map(([id, p]) => ({ consult_id: id, question: p.q })) }, 409);
        }
        consultId = queued[0]?.[0] ?? `${b.call_id}:none`;
      }

      const p = await this.settle(consultId, "answered", text, text);
      if (!p) {
        // On claude.ai the likeliest reason for "already answered" is the caller
        // itself, in a turn it can no longer see: an interrupted turn has its
        // tool calls stripped from the transcript. Hand back what actually
        // happened, rather than leaving it to conclude something else answered.
        const prior = await this.ctx.storage.get(`consult:${consultId}`);
        const callId = consultId.split(":")[0];
        return json({
          ok: false,
          error: "no such pending consult",
          ...(prior ? { prior, note: OUTCOME_NOTES[prior.outcome] } : {}),
          pending: this.queuedFor(callId).map(([id, w]) => ({ consult_id: id, question: w.q })),
        }, 404);
      }
      return json({ ok: true, waited_ms: Date.now() - p.at,
                    queued_behind: this.queuedFor(p.call_id).length });
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
        for (const [consultId] of this.queuedFor(id)) {
          await this.settle(consultId, "call_ended", PUNT);
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
          ({ consult_id: id, call_id: p.call_id, question: p.q, waiting_ms: Date.now() - p.at })),
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
