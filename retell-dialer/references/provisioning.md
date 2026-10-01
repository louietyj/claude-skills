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

The prompt is ours end to end: a role key naming `{{other_party}}`, speech and
style rules, caller-side examples, then `{{brief}}` and the invariants. Four
dynamic variables are supplied per call (`other_party`, `opening`, `brief`,
`call_purpose`), so each call's instructions are written fresh. Edit it in
`agent/prompt.md`, then `dialer agent-push`: it inlines the file into
`agent.json` (title and HTML comments stripped), PATCHes the model, prompt,
tools and handbook toggles, and fails if the live config then differs from the
spec. A test fails while the two files disagree.

Every `handbook_config` preset is off. They are written for an agent that *is*
the business answering customers — persona, examples, empathy lines, intake-style
name confirmation — and with them on, the agent kept answering as the business
it had called ("who can you transfer you to"). The parts worth keeping (speech
normalization, NATO spelling, filler words, hard ceilings) are copied into the
prompt, reworded where they assumed the business side.
[`retell-handbook.md`](retell-handbook.md) has the presets' text.

Retell also adds a current date and time block to the newest user turn
("Current Time Awareness"), and no setting turns it off. On short turns the
agent took it for the other party's words ("that message looks like a block of
calendar text") until the prompt described it.

The model stays on `claude-5-sonnet`. On `claude-5.5-sonnet` the agent spoke its
reasoning aloud ("The transcript shows only 'Sure'… I'll repeat the intro") and
dismissed short turns as system text; the same prompt on Sonnet 5 did neither.
Sonnet 5.5 cannot fully disable thinking (`disabled` is a 400; the lowest
setting is `between_tools`), and Retell doesn't say what it sends instead.
Retest before upgrading.

The agent lets the other side speak first (`start_speaker: user`, speaking
itself after 3s of silence). On a fixed timer it talked over "Wooga Korean
Barbecue" and the host hung up. The price is that the first reply is generated
rather than pre-synthesised, 2–4s after their greeting. `reminder_max_count: 0`
because Retell's default nudge after 10s of silence had the agent disclose into
hold music, then skip the disclosure for the human who came back.
`press_digit` has `speak_after_execution: false` for the same reason: with it on,
Retell asks the model for a line after every keypress, and the menu hears it as
input. `end_call` has it off too: with it on, the agent spoke a line ("I'll wrap
up here") after hanging up.

The voice is `retell-Rita`: brisk, with a little line noise that reads as a real
caller. The slower, warmer voices suit an agent answering a helpline, not a
customer calling one. Platform voices also cost less than the same persona on
ElevenLabs.

Past 4,000 prompt tokens, Retell bills the whole call at tokens ÷ 4,000. The
count is every turn's full context: general prompt, tool descriptions, handbook
presets, transcript, tool results and any knowledge base retrievals, so a
knowledge base is no escape. A long call passes 4,000 on transcript alone,
which is why the invariants and tool descriptions are compressed and GUIDE.md
gates every brief on a compression pass.

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
