# retell-dialer

Place a real phone call on Louie's behalf and supervise it live.

A Retell voice agent holds the conversation at human tempo. When it hits
something its brief does not cover it calls `consult_supervisor`, which POSTs to
a Cloudflare Durable Object that holds the HTTP request open; Claude polls that
queue and answers, and the released response is what the agent speaks. Claude
can also push context in unprompted, without the agent having asked.

Measured on live calls: a consult reaches Claude in **under 250ms**, and the
answer is being spoken **6–13 seconds** later. That is slow enough that a
receptionist notices and fast enough that they wait — twice, maybe three times.
Everything else has to be anticipated in the brief.

Nobody sells this assembled. The vendor MCP servers expose "start a call" and
"read the transcript afterwards", which is the fire-and-forget shape this exists
to avoid.

## Layout

| | |
|---|---|
| `SKILL.md` | the skill body — how to run a call |
| `bin/dialer.py` | the CLI: health, dispatch, watch, answer, steer, transcript, journal |
| `bin/ws.py` | stdlib RFC 6455 client for Retell's live-transcript socket |
| `worker/` | the Durable Object queue (`wrangler deploy`) |
| `agent/agent.json` | the Retell agent + response engine, ids templated out |
| `dev/webcall/` | localhost launcher, for exercising the loop without PSTN |
| `references/provisioning.md` | deploying the Worker, the agent, PII settings |

## Setup

`config.json` (gitignored — see `config.example.json`) holds the Retell API key,
the agent and LLM ids, the worker URL and its token. Then:

```bash
dialer health     # queue, token, agent, number
```

`references/provisioning.md` covers deploying the Worker and creating the agent.

## Packaging

```bash
python package.py     # -> retell-dialer.zip, upload to claude.ai
```

The zip bundles `config.json`, so **the artefact is a credential**: anyone
holding it can phone people as Louie. It is gitignored for the same reason.

`dev/` and `worker/` are excluded — a browser call needs the user's own
microphone, which the sandbox cannot reach, and the Worker is deployed from a
checkout.

## Design notes

Three things are structural rather than advisory, because each cost a live call
when it was left to the caller:

- **`answer` resumes watching in the same process.** Two commands invite prose
  between them, and that prose is dead air on a stranger's phone.
- **Requests are built by urllib, never curl.** `curl -G` folds the body into
  the query string and sends none; the Worker threw on the empty `req.json()`
  and three calls hung.
- **`watch` races the consult queue against the live transcript and call
  status.** The queue alone cannot distinguish "still talking" from "hung up
  twenty seconds ago", and it never fires at all for the failure that matters
  most — the agent mishandling something it does not know it is mishandling.

The agent prompt is deliberately almost empty: `{{brief}}` plus five invariants.
Everything else is supplied per call as a dynamic variable, so Claude writes the
whole prompt fresh each time rather than filling in a form.
