# claude-listen

Claude listens to a live conversation through Louie's phone or Pixel Watch and
steers him in chat while it runs.

The device's browser streams mic audio to a Cloudflare Durable Object, which
relays it to Soniox (`stt-rt-v5`, speaker diarization, context terms) and
stores numbered entries: speaker turns, device events, notes sent. Claude
long-polls them with `listen watch` from a sandbox that can be recycled at any
moment. Everything that matters lives in the object, so a recycle only pauses
Claude. An optional full-file pass afterwards goes to Deepgram Nova-3.

Nothing is retained by either provider: Soniox real-time stores no audio or
text, and every Deepgram request carries `mip_opt_out=true`. Audio is kept on
the relay only for an `--archive` session, and deleted when its batch pass runs.

Replaces `meeting-listen` (an AgentCall meeting bot), which returned minutes of
speech per poll as one blob, credited every word to one speaker, and needed a
video call to join.

## Layout

| | |
|---|---|
| `SKILL.md` | thin skill body: just runs `setup.sh` |
| `GUIDE.md` | how to run a session; printed in full by `setup.sh` |
| `bin/listen.py` | the CLI: health, start, watch, note, stop, transcript, audio, status, pair, bookmark |
| `test_listen.py` | offline CLI tests: `python test_listen.py` |
| `worker/` | the relay (`worker.js`), the device page (`page.html`), `wrangler.toml` |
| `worker/test_session.py` | relay tests against `wrangler dev` and the fakes |
| `dev/fake_stt.py` | Soniox-shaped live and Deepgram-shaped batch stand-ins, scripted by the test's audio frames |
| `dev/feed.py` | stands in for the phone: streams a file to the deployed relay |
| `dev/soniox_compare.py` | streams a file straight to Soniox, for comparing providers on identical bytes |
| `dev/make_sample.ps1` | two Windows voices act out a consultation -> `dev/sample.webm` |
| `dev/watch_shot.py` | adb screenshot of the watch with its round face drawn on, or of the phone |

## Setup

```bash
cd worker
npx wrangler deploy
npx wrangler secret put CLI_TOKEN          # long alphanumeric
npx wrangler secret put DEVICE_TOKEN       # long alphanumeric, different
npx wrangler secret put SONIOX_API_KEY
npx wrangler secret put DEEPGRAM_API_KEY   # only for `transcript --batch`
```

`config.json` (gitignored; see `config.example.json`) holds `worker_url`,
`cli_token` and `device_token`. Then `python bin/listen.py health`.

**Devices:** bookmark the plain worker URL. The first visit asks for a pairing
code: `listen pair` issues one (6 digits, single use, 5 minutes, 5 tries, and 20
wrong guesses an hour across all codes). The page then keeps the device token
in localStorage and finds whatever Claude armed by itself. The layout sizes
itself to the screen; the phone also shows the running transcript.

**Never deploy while a session is live.** A deploy restarts every Durable
Object and drops its sockets. The page reconnects and the transcript
survives, but the seconds in between are lost.

## Packaging

```bash
python package.py     # -> claude-listen.zip, upload to claude.ai
```

The zip bundles `config.json`, so **the artefact is a credential**: the CLI
token reads any live conversation's transcript.

## Tests

```bash
python test_listen.py                 # offline, instant
python worker/test_session.py         # starts wrangler dev + fakes, ~2 min
```

End to end against the real providers, no phone:

```bash
python bin/listen.py start --label sample --term "Wooga Family Trust" --term trustee --archive
python dev/feed.py dev/sample.webm --stop      # in one shell
python bin/listen.py watch                     # in another, repeatedly
python bin/listen.py audio --out .             # before --batch, which deletes it
python bin/listen.py transcript --batch
```

## Design notes

**Soniox for live, because of the watch.** On a recording made through the
Pixel Watch 3's mic, Deepgram Nova-3's streaming diarization put every word on
one speaker, every time: unfiltered, low-passed at 7 kHz, and resampled to
16 kHz alike. Soniox split the same bytes correctly, as did Deepgram's own
full-file pass. On the phone's mic both providers did fine. The watch audio is
clear to a human ear; its spectrum is thin above 2 kHz with image energy at
8–12 kHz, which seems to be what Deepgram's streaming model cannot cluster.
Soniox is also about a quarter of the price.

**The page must stay on screen, on both devices.** Measured with a throwaway probe on a
Pixel Watch 3 (Samsung Internet 5.2) and a Pixel 11 Pro (Chrome 154):
- Watch: a crown press releases Wake Lock, and ~5s later the socket dies. The
  page reconnects by itself when reopened.
- Phone: a screen lock or app switch keeps the socket and the recorder alive
  but turns the mic off, so **silence keeps arriving**, indistinguishable from
  a quiet room.

The page therefore reports `visibilitychange`, the relay logs "mic is OFF"
entries, and `watch` names the device state on every return. With Wake Lock
held, the watch stayed awake in ambient mode, wrist down.

**No browser audio processing.** `noiseSuppression` and `autoGainControl` are
off and the recorder runs at 64 kbps: both flatten the differences between
voices that diarization relies on.

**One recorder run = one webm header = one transcriber stream** (a "segment").
A stream that resumes mid-container is not reliably decodable, so every
reconnect, on either side, starts a new segment: the relay tells the page to
`restart`, and the page answers with a fresh recorder. `startSegment` is
synchronous up to its first await, because the chunk carrying the header
arrives right behind the `seg` message. A transcriber that fails within 5s
three times running (a bad key) stops being restarted rather than looping.

**Device facts live in storage, not memory.** `ui`, `hidden_since` and
`disconnected_at` are in `meta`, and a fresh instance that finds a live session
logs "relay restarted": an object restart must not turn "gone three minutes"
into "fine". Locally, workerd restarted the object on every device disconnect,
which is how this surfaced.

**Bodies are buffered before forwarding to the object.** A streamed POST body
the object never reads is still being pumped after it responds, and workerd
then fails the *next* request.

**`watch` budget is 90s** because Cowork's bash tool kills at 120s by default
(configurable to 600s). The budget only caps silence; speech returns after the
`--interval` floor (10s), and a backlog returns at once. Claude's own notes are
logged but don't count as news. The cursor is written only after output is
printed, so a killed watch replays rather than skips.

**Free plan fits.** Per meeting hour: ~450 GB-s of 13,000/day, ~720 billed
requests of 100k/day (incoming WebSocket messages bill 20:1), ~720 rows
written. Archived audio is buffered to ~30s per row for the same reason.
