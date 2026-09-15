# retell-dialer

Place a real phone call on Louie's behalf and supervise it live.

A Retell voice agent holds the conversation at human tempo. When it hits
something its brief does not cover it calls `consult_supervisor`, which POSTs to
a Cloudflare Durable Object that holds the HTTP request open; Claude polls that
queue and answers, and the released response is what the agent speaks. Claude
can also push context in unprompted, and watch the transcript as it is spoken.

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
| `test_dialer.py` | offline regression tests — `python test_dialer.py` |
| `worker/` | the Durable Object queue (`wrangler deploy`) |
| `worker/test_queue.py` | queue tests against `wrangler dev` — see its docstring |
| `agent/agent.json` | the Retell agent + response engine, ids templated out |
| `dev/webcall/` | localhost launcher, for exercising the loop without PSTN |
| `references/provisioning.md` | deploying the Worker, the agent, PII settings |

## Setup

`config.json` (gitignored — see `config.example.json`) holds the Retell API key,
the agent and LLM ids, the worker URL and its token. Then:

```bash
dialer health     # queue, token, agent, webhook, number
python test_dialer.py
```

`references/provisioning.md` covers deploying the Worker and creating the agent.

## Packaging

```bash
python package.py     # -> retell-dialer.zip, upload to claude.ai
```

The zip bundles `config.json`, so **the artefact is a credential**: anyone
holding it can phone people as Louie. It is gitignored for the same reason.

`dev/`, `worker/` and the tests are excluded — a browser call needs the user's
own microphone, which the sandbox cannot reach, and the Worker is deployed from
a checkout.

## Design notes

Most of this is shaped by failures on real calls. The tests are named for the
symptom rather than the function, so the suite reads as a list of them.

**The supervisor's transcript is not a record of what it did.** claude.ai drops
an in-flight tool call when a turn is interrupted, so an answer that was sent
can vanish from the sender's own context. This cost two calls — the second time
producing a receptionist asking "wait, do you want me to cancel it or not?"
`dialer journal` writes every dispatch, answer and steer to disk *before* the
request, which survives the turn; the skill makes reading it unconditional after
any interjection, because detecting the drop from the inside is impossible by
construction.

**Three things are structural rather than advisory**, each having cost a call:
`answer` and `steer` resume watching in the same process — two commands invite
prose between them, and that prose is dead air on a stranger's phone; every
request is built by urllib rather than curl, because `curl -G` folds the body into the query string
and sends none; and both `watch` and `poll` check Retell's call status
themselves, because neither the queue nor the monitor socket can tell "still
talking" from "hung up twenty seconds ago".

**The live transcript is lossy and lagging.** The monitor streams an utterance
as it is spoken, so `watch` labels its last line
`[...utterance may still be in progress]` — a steer written against a
half-received sentence once "corrected" a keypress that had been right.

**The agent is Sonnet 5 with a phone line.** It does not need telling how to
behave on a call; it needs Louie's facts, the authority to decide for him, and a
supervisor willing to check its judgement. Most of SKILL.md follows from that,
and so does most of what was deleted from it.
