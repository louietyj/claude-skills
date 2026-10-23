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
moment, or streams them into `Monitor` with `intercom follow`, the same
long-poll in a loop. Everything that matters lives in the object, so a recycle only pauses
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
| `bin/intercom.py` | the CLI: health, start, watch, follow, monitor-url, speak, set, note, stop, transcript, audio, status, pair, bookmark |
| `test_intercom.py` | offline CLI tests: `python test_intercom.py` |
| `worker/` | the relay (`worker.js`), the device page (`page.html`), `wrangler.toml` |
| `aec/` | the page's own echo canceller: `aec3.wasm` (WebRTC's AEC3), its wrapper `aec3.js`, `worklet.js`; rebuilt by `build.sh` from `wap.cc` |
| `worker/test_session.py` | relay tests against `wrangler dev` and the fakes |
| `dev/fake_stt.py` | Soniox-shaped live STT and TTS and Deepgram-shaped batch stand-ins, scripted by the test's audio frames |
| `dev/feed.py` | stands in for the phone: streams a file to the deployed relay |
| `dev/soniox_compare.py` | streams a file straight to Soniox, for comparing providers on identical bytes |
| `dev/make_sample.ps1` | two Windows voices act out a consultation -> `dev/sample.webm` |
| `dev/watch_shot.py` | adb screenshot of the watch with its round face drawn on, or of the phone |
| `dev/page_shot.py` | headless-Chrome screenshots of the page at watch and phone sizes, live and with settings open |

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

**The page must stay on screen: on the watch always, on the phone unless
Chrome has the flag below.** Measured with a throwaway probe on a Pixel Watch 3
(Samsung Internet 5.2) and a Pixel 11 Pro (stock Chrome 154):
- Watch: a crown press releases Wake Lock, and ~5s later the socket dies. The
  page reconnects by itself when reopened.
- Phone: a screen lock or app switch keeps the socket and the recorder alive
  but turns the mic off, so **silence keeps arriving**, indistinguishable from
  a quiet room.

The page therefore reports `visibilitychange` and reads its own mic, the relay
logs both, and `watch` names the device state on every return.

**A silenced mic delivers exact zeros.** Measured on the Pixel 11 Pro behind the
lock screen: without the flag below every sample was 0.0, with it a quiet room
read ~1e-5 even through noise suppression. So the page samples an analyser
on recorder chunks, at most every 500 ms (a hidden page's timers are throttled,
its recorder isn't), and reports `live`, `silent` (zeros for 3s) or `unknown` (the
context stopped or its buffer froze), repeating it every 5s. Only a hidden page
with no fresh `live` reading is logged as "mic is OFF", which covers the watch,
whose socket dies anyway, and pages cached from before the probe.

**Picture-in-picture keeps the phone's mic across app switches.** Chrome on
Android silences a background page's mic after ~5s (crbug 445356777) unless
the page has a picture-in-picture window, here a square canvas tile opened by
a button: Chrome needs a tap for it, and opening it on START collapsed the
page at once. The page reports itself visible while the tile is open, and
hidden if it is closed while away, which silences the mic again
(crbug 491996749). Screen lock still stops it. With Wake Lock
held, the watch stayed awake in ambient mode, wrist down.

**A Chrome feature flag keeps the phone's mic through screen lock.** Android
14+ lets a backgrounded app record only under a `microphone` foreground
service. Chrome's manifest declares one, but Chrome starts it only behind
`AndroidEnableBackgroundMediaCapturing`, which is off by default and not in
`chrome://flags` (see `MediaCaptureNotificationServiceImpl.java`). With it on, a
tab that opens the mic starts the service while still on screen, and the mic
survives screen lock and app switches without the PiP tile. Tested on the Pixel
11 Pro (Chrome 155), locked in a pocket, talking through earbuds. Louie's phone
has it, set up once over adb:

1. `chrome://flags/#enable-command-line-on-non-rooted-devices` → Enabled, relaunch.
2. Write the flag to Chrome's command-line file and restart Chrome:
   ```bash
   adb shell 'echo "_ --enable-features=AndroidEnableBackgroundMediaCapturing --enable-test-intents" > /data/local/tmp/chrome-command-line'
   adb shell am force-stop com.android.chrome
   ```
3. `chrome://version` should list the flag under "Command Line".

`--enable-test-intents` does nothing here but silence Chrome's "loading flags
from a command-line file" warning at start (`bad_flags_prompt.cc` skips it for
that switch; elsewhere it only lets `am` pass a voice-search result as one
string). A second warning, naming the step-1 flag, has no such exemption. It
goes away only if Chrome reads the file as Android's debug app
(`am set-debug-app --persistent com.android.chrome`, step 1 off), which needs
USB debugging left on: kept as is so adb can be turned off.

It survives reboots and Chrome updates. Clearing Chrome's data or resetting its
flags undoes step 1, a factory reset undoes both, and `adb shell rm
/data/local/tmp/chrome-command-line` undoes it on purpose (in Git Bash, set
`MSYS_NO_PATHCONV=1` or the path gets rewritten). The feature is experimental:
if the relay logs the mic going silent on lock again, check `chrome://version`
first.

**A near-silent chirp keeps Chrome from freezing the page.** With the mic
kept, Chrome still froze a hidden page 90s after it last made a sound (Blink's
`StopInBackground`: 30s "recently audible", then a minute): JavaScript, the
recorder and the AudioContext clock all stopped, while the socket stayed open
and the mic stayed held. Sound is the only exemption, so the page plays a
19 kHz tone at -49 dBFS (Chrome counts above -72 as audible) for a second every
20s, scheduled an hour ahead on the audio clock rather than by timers. Locked
and silent for 6.5 minutes, the page kept streaming. The relay also logs a
connected page that stops sending audio for 10s, which is what a frozen page
looks like from outside.

**No browser audio processing in listen mode.** `noiseSuppression` and
`autoGainControl` are off and the recorder runs at 64 kbps: both flatten the
differences between voices that diarization relies on. Talk mode turns all
three on, echo cancellation above all: without it Claude transcribes its own
voice from the speaker.

**Talk mode lets Soniox end turns, then waits 3 s of silence.** Its semantic
endpointing runs at its most patient (`max_endpoint_delay_ms` 3000,
sensitivity -1), and a monologue is not split. Even so it closed turns while
Louie paused to think, so the relay holds a turn until `TALK_HOLD_MS` passes
with no new words. An earlier merge window keyed off Soniox's punctuation, a
guess stacked on a guess; this one keys off silence alone. Claude answers in
seconds anyway, and whole thoughts matter more than quick turn-taking.

The wait is per session, 1-10 s: Louie sets it in the page's settings (⋯),
Claude with `intercom set --hold` or `start --hold`. Soniox's endpointing
stays fixed: it is set when its stream opens. The page remembers Louie's last
pick and offers it when a session starts, but never over a value set since,
so a rejoin keeps what was chosen. His change is logged as news, so Claude
knows why his turns arrive later.

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
the gate decides. Outside real Chrome the gate reads AEC3's output (below),
where echo never reaches -42 dB twice in a row, so it opens on 3 of 5 frames:
inside the relay's lookback, and an interruption keeps its first words.

**Outside real Chrome, the page cancels Claude's echo itself.** The watch's
browser hands echo cancellation to Wear OS, which gates rather than subtracts:
silence, then bursts of Claude at Louie's own level, several a minute, which
ducked Claude and got transcribed. The raw mic was fine: WebRTC's AEC3 on it
left echo at -55 to -80 dB and Louie untouched. So the page runs AEC3 in an
AudioWorklet on the raw mic, with Claude's track scaled by the duck volume as
the reference (it must be what the speaker plays); the output is `sendStream`.
The processed mic stays open, unsent, to keep Android in call mode. Real Chrome
keeps its own (AEC3 on desktop, the phone's on Android); `?aec=on|off`
overrides. The raw mic reads ~10 dB lower, hence its -42 dB floor. Level alone
isn't enough there: running water by the watch is as loud as Louie and ducked
Claude throughout, so a frame also needs WebRTC's VAD (on AEC3's output) to
call a fifth of it voice. That cut false ducks from 24% of the time to 2%;
noise suppression only halved them. Interrupting still takes ~2 s: the relay
waits for two transcribed words.

**`aec/aec3.wasm` is built here, not taken from npm.** The published wasm
builds of AEC3 are scalar and cost the watch ~500 ms of CPU per audio second,
~90% in the delay estimator, whose fast paths are SSE2/NEON. `build.sh` lets
meson take the NEON paths on wasm32 (Emscripten maps `arm_neon.h` onto wasm
SIMD), and `wap.cc` runs the delay estimator at 8x downsampling without
pre-echo detection: ~120 ms per audio second, same output. Live that is ~20% of
a core and ~9 mA (~7% of a session's draw), so the worklet runs AEC3 only
while Claude has been audible in the last 2 s.

**Pause is a deliberate disconnect.** The page tells the relay, then turns
off the mic, the recorder, Claude's voice and the socket; resuming reconnects
like a rejoin. The relay logs it as a pause rather than a drop, and gives it an
hour rather than five minutes before ending the session.

**Mute sends silence; it doesn't stop anything.** The recorder and the echo
gate read a clone of the mic track, and mute disables only the clone, so the
transcriber stream runs on (billed alike: Soniox charges for stream time, not
audio) and Claude keeps talking. The mic probe reads the original, so a muted
mic is never reported as silenced, and Claude isn't told about mute at all.

**Listen mode batches Monitor events.** Each event wakes Claude, and a
conversation has a turn every few seconds, so the relay holds listen-mode
turns for 10s and then sends them back to back as separate frames: Monitor
joins frames that arrive together into one notification, and its truncation
(around 500 characters) appeared to apply per frame, which one big frame
would hit far more often. Device news
flushes the batch at once; talk mode sends each turn as it closes.

**Monitor runs `follow`, not the event socket.** Monitor's websocket went deaf
after a few quiet minutes: on a 7-minute idle socket it delivered nothing, not
even the close, while a plain client on the same socket got the end frame. A
pause in the conversation silently ended Claude's listening. `follow` loops
`/watch` instead (gathering 10s in listen mode, as the stream batches), prints
each news entry as the same JSON line the stream would send, and exits at the
end. `/events` and `monitor-url` stay for other clients.

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
