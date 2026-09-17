# Provisioning

One-time setup. Nothing here runs per call; if a call is failing, check
`dialer health` first — it names which link is down.

## The consult queue

A Cloudflare Durable Object. A plain Worker cannot do this job: the held
`/consult` request and the `/answer` that releases it run as separate isolates
and would never see each other's state. `idFromName("main")` routes every
request to one instance, which is the whole point.

```bash
cd worker
npx wrangler deploy
npx wrangler secret put CONSULT_TOKEN     # any long alphanumeric string
npx wrangler secret put RETELL_API_KEY    # the one with a webhook badge in the dashboard
```

Keep the token alphanumeric. An earlier one containing `%`, `|`, `\` and `]`
cost an evening: every one of those is a shell or URL metacharacter.

`env.CONSULT_TOKEN` being unset does not open the gate — it closes it. `None !=
undefined` is true, so every authenticated route 401s for everyone until the
secret exists.

### Routes

| route | caller | auth |
|---|---|---|
| `POST /consult` | Retell | `X-Retell-Signature` |
| `POST /event` | Retell webhooks | `X-Retell-Signature` |
| `GET /poll` | Claude | `?token=` |
| `POST /answer` | Claude | `?token=` |
| `GET /pending` | Claude | `?token=` |
| `GET /call` | Claude | `?token=` |
| `GET /health` | anyone | none |

Retell signs both callbacks with an HMAC of the body keyed by the API key, so
the Worker needs its own copy of that key. Retell also documents a single
source IP, `100.20.5.228`, but promises nothing about it staying put; an
allowlist on it would fail every consult the day it moves.

## The agent

`agent/agent.json` holds the agent and its response engine as one spec, with ids
and the worker URL templated. Two Retell objects: the agent (voice, telephony,
behaviour) and the retell-llm (model, prompt, tools). The LLM is created first
because the agent references it by id.

**Do not create the agent by importing that file in the dashboard.** Import
always mints a *new* `agent_id`, which orphans `config.json` and leaves the
phone number pointing at the old one. Every change should go through
`PATCH /update-agent` and `PATCH /update-retell-llm`, which mutate in place.

The agent has never been published: its only version is the v0 draft, which
those PATCHes edit and every call runs. Publishing it (a dashboard button)
freezes v0 and sends future edits to a new draft, and which of the two calls
would then run is undocumented. Don't publish without checking a call's
`agent_version` afterwards.

The prompt is deliberately almost empty — `{{brief}}` plus the invariants. Three
dynamic variables are supplied per call (`opening`, `brief`, `call_purpose`), so
each call's instructions are written fresh rather than configured once.

Past 4,000 prompt tokens, Retell bills the whole call at tokens ÷ 4,000. The
count is every turn's full context: general prompt, tool descriptions, handbook
presets, transcript, tool results and any knowledge base retrievals, so a
knowledge base is no escape. A long call passes 4,000 on transcript alone,
which is why the invariants and tool descriptions are compressed and GUIDE.md
gates every brief on a compression pass. The handbook presets stay on
deliberately: Retell's tuned text, worth its tokens.

## A phone number

Buy one in the Retell dashboard (~$2/mo) or import a Twilio number, then put it
in `config.json` as `from_number`. Its default outbound agent does not matter —
`dispatch` sends `override_agent_id`.

## Storage and PII

Currently `data_storage_setting: "everything"` with `pii_config.categories: []`,
i.e. scrubbing off, so `get-call` returns `transcript_with_tool_calls`
unredacted.

This was a deliberate reversal. With scrubbing on, the `address` category ate
every personal name and `date_of_birth` ate ordinary calendar dates — a booking
confirmation came back reading *"a party of four under the name [address 2]"*,
which destroys the one artifact worth keeping. Scrubbing is applied before
storage, so there is no read-raw-then-scrub option.

If it is ever turned back on, `/event` captures Retell's webhook payload, which
predates the scrub — `dialer call <id>` reads it.

## The browser launcher (`dev/`)

Development only, and excluded from the packaged skill: a browser call needs the
user's own microphone, which the claude.ai sandbox cannot reach. Locally it lets
the loop be exercised without spending PSTN minutes.

```bash
dialer stage --purpose "…" --opening "…" --brief-file brief.md
dialer serve                      # then open http://localhost:8765/
```

The page creates the call when you click, not in advance: a registered web call
expires in about a minute, so a token minted when the message was written is
usually stale by the time anyone clicks it — which presents as a dead button.

`dev/webcall/retell-sdk.js` is a 560KB bundle (gitignored); rebuild with
`dev/webcall/build.sh`. The SDK's own UMD build externalises `livekit-client`
and `eventemitter3`, so loading it from a CDN means guessing global names.
