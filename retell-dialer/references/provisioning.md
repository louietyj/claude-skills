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
```

Keep the token alphanumeric. An earlier one containing `%`, `|`, `\` and `]`
cost an evening: every one of those is a shell or URL metacharacter.

`env.CONSULT_TOKEN` being unset does not open the gate — it closes it. `None !=
undefined` is true, so every authenticated route 401s for everyone until the
secret exists.

### Routes

| route | caller | auth |
|---|---|---|
| `POST /consult` | Retell | none — the tool config cannot send query params |
| `POST /event` | Retell webhooks | none, same reason |
| `GET /poll` | Claude | `?token=` |
| `POST /answer` | Claude | `?token=` |
| `GET /pending` | Claude | `?token=` |
| `GET /call` | Claude | `?token=` |
| `GET /health` | anyone | none |

`/consult` being unauthenticated means **the worker URL is itself a secret** —
anyone who knows it can inject consults. It lives in `config.json`, which is
gitignored, and is templated out of `agent/agent.json`.

## The agent

`agent/agent.json` holds the agent and its response engine as one spec, with ids
and the worker URL templated. Two Retell objects: the agent (voice, telephony,
behaviour) and the retell-llm (model, prompt, tools). The LLM is created first
because the agent references it by id.

**Do not create the agent by importing that file in the dashboard.** Import
always mints a *new* `agent_id`, which orphans `config.json` and leaves the
phone number pointing at the old one. Every change should go through
`PATCH /update-agent` and `PATCH /update-retell-llm`, which mutate in place.

The prompt is deliberately almost empty — `{{brief}}` plus the invariants. Three
dynamic variables are supplied per call (`opening`, `brief`, `call_purpose`), so
each call's instructions are written fresh rather than configured once.

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
