# intercom

Claude listens through Louie's phone or Pixel Watch, in one of two modes fixed
when the session starts. **Listen**: Claude follows a live conversation and
steers him in chat. **Talk**: Louie and Claude talk hands-free while Claude
keeps working; Claude speaks through the device with Soniox TTS (`tts-rt-v2`,
voice Hazel). The relay refuses speech in a listen session, and the device
shows the mode on its START button, so Louie's tap is the consent.

The device's browser streams mic audio to a Cloudflare Durable Object, which
relays it to Soniox (`stt-rt-v5`, speaker diarization, context terms) and
stores numbered entries: speaker turns, device events, notes sent. Claude
long-polls them with `intercom watch` from a sandbox that can be recycled at any
moment, or, in Claude Code, streams them with `Monitor` over `/events` (each
session has its own key for this, so the CLI token never lands in a Monitor
URL). Everything that matters lives in the object, so a recycle only pauses
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
| `bin/intercom.py` | the CLI: health, start, watch, monitor-url, speak, note, stop, transcript, audio, status, pair, bookmark |
| `test_intercom.py` | offline CLI tests: `python test_intercom.py` |
| `worker/` | the relay (`worker.js`), the device page (`page.html`), `wrangler.toml` |
| `worker/test_session.py` | relay tests against `wrangler dev` and the fakes |
| `dev/fake_stt.py` | Soniox-shaped live STT and TTS and Deepgram-shaped batch stand-ins, scripted by the test's audio frames |
| `dev/feed.py` | stands in for the phone: streams a file to the deployed relay |
| `dev/soniox_compare.py` | streams a file straight to Soniox, for comparing providers on identical bytes |
| `dev/make_sample.ps1` | two Windows voices act out a consultation -> `dev/sample.webm` |
| `dev/watch_shot.py` | adb screenshot of the watch with its round face drawn on, or of the phone |

## Setup

The relay is deployed as the `claude-intercom` Worker (skill names can't
contain "claude", Worker names can), and that name is in its URL.

```bash
cd worker
npx wrangler deploy
npx wrangler secret put CLI_TOKEN          # long alphanumeric
npx wrangler secret put DEVICE_TOKEN       # long alphanumeric, different
npx wrangler secret put SONIOX_API_KEY
npx wrangler secret put DEEPGRAM_API_KEY   # only for `transcript --batch`
```

`config.json` (gitignored; see `config.example.json`) holds `worker_url`,
`cli_token` and `device_token`. Then `python bin/intercom.py health`.

**Devices:** bookmark the plain worker URL. The first visit asks for a pairing
code: `intercom pair` issues one (6 digits, single use, 5 minutes, 5 tries, and 20
wrong guesses an hour across all codes). The page then keeps the device token
in localStorage and finds whatever Claude armed by itself. The layout sizes
itself to the screen; the phone also shows the running transcript.

**Never deploy while a session is live.** A deploy restarts every Durable
Object and drops its sockets. The page reconnects and the transcript
survives, but the seconds in between are lost.

## Packaging

```bash
python package.py     # -> intercom.zip, upload to claude.ai
```

The zip bundles `config.json`, so **the artefact is a credential**: the CLI
token reads any live conversation's transcript.

## Tests

```bash
python test_intercom.py                 # offline, instant
python worker/test_session.py         # starts wrangler dev + fakes, ~2 min
```

End to end against the real providers, no phone:

```bash
python bin/intercom.py start --label sample --term "Wooga Family Trust" --term trustee --archive
python dev/feed.py dev/sample.webm --stop      # in one shell
python bin/intercom.py watch                     # in another, repeatedly
python bin/intercom.py audio --out .             # before --batch, which deletes it
python bin/intercom.py transcript --batch
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
entries, and `watch` names the device state on every return.

**Picture-in-picture keeps the phone's mic across app switches.** Chrome on
Android silences a background page's mic after ~5s (crbug 445356777) unless
the page has a picture-in-picture window, here a square canvas tile opened by
a button: Chrome needs a tap for it, and opening it on START collapsed the
page at once. The page reports itself visible while the tile is open, and
hidden if it is closed while away, which silences the mic again
(crbug 491996749). Screen lock still stops it. With Wake Lock
held, the watch stayed awake in ambient mode, wrist down.

**No browser audio processing in listen mode.** `noiseSuppression` and
`autoGainControl` are off and the recorder runs at 64 kbps: both flatten the
differences between voices that diarization relies on. Talk mode turns all
three on, echo cancellation above all: without it Claude transcribes its own
voice from the speaker.

**Talk mode lets Soniox end turns.** Its semantic endpointing runs at its most
patient (`max_endpoint_delay_ms` 3000, sensitivity -1), and a monologue is not
split. A fixed merge window on top was tried and dropped: it keyed off
Soniox's punctuation, a guess stacked on a guess.

**Claude's voice goes through Cloudflare's Realtime SFU.** Chrome on Android
only cancels echo of audio that arrives from a real peer connection: Web Audio
playback, and even an on-page WebRTC loopback, came back through the phone's
mic word for word. The relay creates a WebSocket adapter that dials `/tts-in`,
paces Soniox's 48 kHz PCM into it at real time (as stereo, in protobuf
packets), and the page subscribes to the published track over WebRTC and plays
it through `<audio>` with a ~500 ms jitter buffer. That connection takes a few
seconds after START, and audio sent before it is up is simply lost, so `speak`
waits (up to 10s) for the page to say it is connected and playing.

**The page's `AudioContext` must open after the mic.** On a Pixel, Chrome
leaves echo cancellation to the phone, which only cancels audio on a call
(voice-communication) stream. The echo-cancelled mic puts Android in call
mode; a context created before that opens a *media* stream, Chrome plays
Claude's voice through it, and the echo comes back at full level while the
track still reports `echoCancellation: true`. It depended on timing, so it
looked intermittent. `dumpsys audio` shows Chrome's playback `usage`.

**The relay decides barge-in and keeps the clock.** While Claude speaks, two
words of Louie's live transcript (or a pause) make the relay stop feeding the
track and tell the page to mute. Playback position is time since the first
frame less the SFU-plus-jitter delay (`VOICE_PLAYOUT_MS`), and Soniox's
character timestamps turn that into the words he heard.

**`speak` takes at most 400 characters.** That isn't a technical limit:
calls chained in one command lose and report the same as one long call
would. It's there because a hard refusal reshapes an answer into short
spoken paragraphs where a guideline alone doesn't.

**While Claude speaks, only sustained sound counts.** The watch's own echo
canceller leaves loud but brief bursts, while Louie's speech holds 70-100% of
frames above -35 dB. The page's gate opens when 12 of the last 20 frames (two
seconds) clear -35 dB, and sends only the open and close; the relay drops any
word said during Claude's speech (plus an echo tail) unless the gate was open
at that word's audio time. Peak loudness alone could not separate them (echo
spikes reached Louie's level), and a one-second window let echo bursts and
wrist noise through. So interrupting takes real talking. When Claude is silent
every word counts. Meanwhile the page ducks Claude to a quarter volume after
200 ms of sound, as claude.ai's voice mode does, so Louie can be heard while
the gate decides.

**Pause is a deliberate disconnect.** The page tells the relay, then turns
off the mic, the recorder, Claude's voice and the socket; resuming reconnects
like a rejoin. The relay logs it as a pause rather than a drop, and gives it an
hour rather than five minutes before ending the session.

**Listen mode batches Monitor events.** Each event wakes Claude, and a
conversation has a turn every few seconds, so the relay holds listen-mode
turns for 10s and then sends them back to back as separate frames: Monitor
joins frames that arrive together into one notification, and its truncation
(around 500 characters) appeared to apply per frame, which one big frame
would hit far more often. Device news
flushes the batch at once; talk mode sends each turn as it closes.

**`speak` is compare-and-swap.** Claude passes the last entry it read; a newer
turn from Louie, or one still in progress, means nothing is played and the
turn comes back instead, so Claude never answers a moment that has passed.

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
