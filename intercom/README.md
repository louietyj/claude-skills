# intercom

Claude listens through Louie's phone or Pixel Watch, in one of two modes fixed
when the session starts. **Listen**: Claude follows a live conversation and
steers him in chat. **Talk**: Louie and Claude talk hands-free while Claude
keeps working; Claude speaks through the device with Soniox TTS (`tts-rt-v2`,
voice Hazel). The relay refuses speech in a listen session, and the device
shows the mode on the room's button, so Louie's tap on Web or Phone is the
consent.

The device's browser streams mic audio to a Cloudflare Durable Object, which
relays it to Soniox (`stt-rt-v5`, speaker diarization, context terms; in talk
mode, through Krisp's voice isolation first) and
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
| `GATE.md` | the gate agent's prompt; `intercom gate start` fills in GUIDE.md's "Steering or collaborating" and the brief |
| `bin/intercom.py` | the CLI: health, start, watch, follow, gate, wake-next, brief, monitor-url, speak, set, call, hangup, note, stop, transcript, audio, status, pair, bookmark |
| `test_intercom.py` | offline CLI tests: `python test_intercom.py` |
| `worker/` | the relay (`worker.js`), the device page (`page.html`), `wrangler.toml` |
| `aec/` | the page's own echo canceller: `aec3.wasm` (WebRTC's AEC3), its wrapper `aec3.js`, `worklet.js`; rebuilt by `build.sh` from `wap.cc` |
| `opus/` | the relay's webm/opus decoder for voice isolation: `opus.wasm` (libopus's decoder), its wrapper and webm demuxer `opus.js`; rebuilt by `build.sh` |
| `worker/test_session.py` | relay tests against `wrangler dev` and the fakes; `chrome-opus.webm` is Chrome's recorder output for them |
| `dev/fake_stt.py` | Soniox-shaped live STT and TTS, Deepgram-shaped batch and Krisp-shaped voice isolation stand-ins, scripted by the test's audio frames |
| `dev/telnyx_api.py` | Telnyx's API with the key in `dev/telnyx.key` (gitignored), for setting up the phone line |
| `dev/fake_telnyx.py` | Telnyx-shaped call API and signed webhooks, driven by tests or by `dev/phone.html`, the fake handset it serves at `/phone` |
| `dev/feed.py` | stands in for the phone: streams a file to the deployed relay |
| `dev/soniox_compare.py` | streams a file straight to Soniox, for comparing providers on identical bytes |
| `dev/make_sample.ps1` | two Windows voices act out a consultation -> `dev/sample.webm` |
| `dev/watch_shot.py` | adb screenshot of the watch with its round face drawn on, or of the phone |
| `dev/page_shot.py` | headless-Chrome screenshots of the page at watch and phone sizes, live and with settings open |
| `dev/page_resume.py` | drops the real page's socket in headless Chromium (closed, silent, too long) and checks no audio is lost |

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
npx wrangler secret put KRISP_API_KEY      # talk mode's voice isolation (developers.krisp.ai)
```

The phone line (optional) needs a Telnyx account: a Call Control application
whose webhook is `<worker>/telnyx`, a number assigned to it, and an outbound
voice profile with tight limits. Then:

```bash
npx wrangler secret put TELNYX_API_KEY
npx wrangler secret put TELNYX_PUBLIC_KEY       # the account's webhook-signing key
npx wrangler secret put TELNYX_CONNECTION_ID    # the Call Control application's id
npx wrangler secret put TELNYX_FROM             # the Telnyx number, E.164
```

No number to call is configured anywhere: `intercom call --to` and the page
pass one each time (the repo is public). `intercom health` says whether the
phone line is set up.

`config.json` (gitignored; see `config.example.json`) holds `worker_url`,
`cli_token`, `device_token` and optionally `keyterms_url`, the standing list
`intercom start --default-terms` fetches. Then `python bin/intercom.py health`.

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
python dev/page_resume.py             # the page itself, headless; CHROME=<path> for a preinstalled one
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
a button: Chrome needs a tap for it, and opening it as the mic started collapsed the
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

**Talk mode hears only Louie: Krisp's voice isolation.** Music, chatter and
other voices around him went into the transcript as his. Noise suppression
(the browser's, or a phone's) keeps any speech, sung lyrics included, so the
relay runs Krisp's hosted Voice Isolation API (`VIVA-tel`, $0.003/min) on
every source: a page's webm is decoded in the object (libopus's decoder,
built to wasm by `opus/build.sh`, ~0.5 ms per 250 ms chunk), the phone line's
L16 is already PCM. 30 ms frames go to Krisp and come back one for one, in
order, so Soniox's clock is unchanged; Soniox then gets PCM. Anything Krisp
can't take (a refused token, a dropped socket, a 3 s stall) goes to Soniox
unfiltered, and an event says so. Telnyx sells the same Krisp model on the
phone leg alone, at $0.005/min. Listen mode never isolates: it would keep only
the nearest voice of a conversation. Session tokens last 5 hours; whether an
open stream outlives its token is untested.

**A turn carries clues, not a verdict.** Isolation leaves leaks: a loud PA
announcement while Louie is silent is the only voice there, so it comes
through turned down but audible, and gets transcribed. Each turn therefore
stores Soniox's mean token confidence and, on isolated audio, how far Krisp
turned its words down: each frame's power before and after Krisp is logged,
and the words' timestamps index it. On the first real test (a commute centre's
PA), leaked announcement words dropped 17-70 dB while Louie's moved under
±8 dB; a garbled leak dropped only 3-7 dB but came back at confidence
0.16-0.46. Confidence alone misses a clear announcement (0.95+). Claude
weighs both, with the content; the relay drops nothing.

**Talk mode lets Soniox end turns, then can wait for silence.** Its semantic
endpointing runs at its most patient (`max_endpoint_delay_ms` 3000,
sensitivity -1), and a monologue is not split. Even so it closes turns while
Louie pauses to think, so the relay can hold a turn until a turn wait passes
with no new words. An earlier merge window keyed off Soniox's punctuation, a
guess stacked on a guess; this one keys off silence alone.

The wait is per session, 0-10 s, and 0 by default (`TALK_HOLD_MS`): Louie
prefers quick replies, and raises it when he needs time to think. He sets it
in half seconds in the page's settings (⋯), Claude with `intercom set --hold`
or `start --hold`. Soniox's endpointing stays fixed: it is set when its stream
opens. The page remembers Louie's last pick and offers it when a session
starts, but never over a value set since, so a rejoin keeps what was chosen.
His changes are logged as settings, not news: each tap of the stepper would
otherwise wake Claude.

**Claude's voice goes through Cloudflare's Realtime SFU.** Chrome on Android
only cancels echo of audio that arrives from a real peer connection: Web Audio
playback, and even an on-page WebRTC loopback, came back through the phone's
mic word for word. The relay creates a WebSocket adapter that dials `/tts-in`,
paces Soniox's 48 kHz PCM into it at real time (as stereo, in protobuf
packets), and the page subscribes to the published track over WebRTC and plays
it through `<audio>` with a ~500 ms jitter buffer. That connection takes a few
seconds after the page takes the audio, and audio sent before it is up is simply lost, so `speak`
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

**`speak` has no length cap.** A 400-character cap was tried, to push
answers into short spoken paragraphs. Claude just split the same answer into
more calls, chained in one command, so the refusal only cost a tool call. The
Bash call's 120s timeout already bounds what one command can say.

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

**A room has screens, and at most one audio holder.** Any number of pages
can be in a session (a "room"), each with its own socket: they all get notes
and room state, and any can take the audio, leave it, or stop the room. One
screen at most holds the audio: only its chunks reach the transcriber, and
only it gets Claude's voice and the transcriber's restarts. Taking the audio
tells the old holder to let go; a holder that drops takes the audio back when
it reconnects, unless another screen has it by then. Joining brings no audio:
the page offers Web and Phone when nothing holds it, and keeps the move behind
the ⋯ sheet otherwise, so a second screen never grabs the mic by accident.
Pause is leaving audio: the screen stays, everything audio stops, and the room
ends after an hour without audio, or five minutes with no screen at all.

**The phone line is just another audio holder.** Mobile data drops on some
of Louie's routes even at full bars, where a voice call (VoLTE has a reserved
bearer, then falls back to the old voice network) holds up. So the relay can
have Telnyx call him: `/v2/calls` with a 20 s ring (shorter than voicemail
takes to answer), a webhook back to `/telnyx`, and a media stream back to
`/phone`. Both are checked against Telnyx's Ed25519 signature; the stream also
carries a per-call key. Once the stream starts, the call holds the audio like
a screen would: with bidirectional L16 asked for, the call's audio arrives as
L16 too and goes to Soniox raw, and Claude's voice goes back as L16 at the
rate the stream's `start` names, paced at real time, each chunk followed by a
mark. Telnyx's L16 is little-endian: network order played as static on the
first real call. Its rate is 8 kHz unless the dial asks otherwise (16 kHz audio
on an 8 kHz stream played at half speed), so the dial asks for 16 kHz. That
alone isn't HD: without `preferred_codecs`, Telnyx offers the carrier only
G.711 (speech cut at 3.8 kHz); with AMR-WB first, a call to a mobile negotiates
AMR-WB (speech to 7 kHz). Telnyx's HD reaches only AT&T, T-Mobile and Verizon
phones directly; a number that forwards through another carrier stays G.711. Telnyx
returns marks as the audio plays, so the relay knows exactly what Louie heard,
and barge-in is `clear` (marks it flushes back don't count). The handset
cancels echo itself, so the page's echo gate doesn't apply. A screen taking
the audio, or leaving it, hangs up; nothing redials, since a drop and a hang-up
can't reliably be told apart. A call counts as presence, so it can open a room
no screen has, and keep it. Only the number's last four digits are stored.

**Mute sends silence; it doesn't stop anything.** The recorder and the echo
gate read a clone of the mic track, and mute disables only the clone, so the
transcriber stream runs on (billed alike: Soniox charges for stream time, not
audio) and Claude keeps talking. The mic probe reads the original, so a muted
mic is never reported as silenced, and Claude isn't told about mute at all.

**Listen mode batches Monitor events.** Each event wakes Claude, and a
conversation has a turn every few seconds, so the relay holds listen-mode
turns for 10s and then sends them back to back as separate frames: Monitor
joins frames that arrive together into one notification, and its truncation
applies per line, which one big frame would hit far more often. Device news
flushes the batch at once; talk mode sends each turn as it closes.

**`follow` wraps long entries.** Monitor cuts each stdout line at ~400
characters (`...(truncated)`, not configurable), and every long talk-mode turn
then cost an `intercom transcript` call before Claude could answer. Lines
printed within 200 ms arrive whole in one notification, so `follow` wraps an
entry at word boundaries into lines of at most 380, continuations prefixed
`<n>+ `.

**Monitor runs `follow`, not the event socket.** Monitor's websocket went deaf
after a few quiet minutes: on a 7-minute idle socket it delivered nothing, not
even the close, while a plain client on the same socket got the end frame. A
pause in the conversation silently ended Claude's listening. `follow` loops
`/watch` instead, prints each news entry as one compact line, `51 [07:20 S1]
text` (JSON took ~1.8x the characters, every one of them re-read on each later
wake), and exits at the end. `/events` and `monitor-url` stay for other clients.

**Listen mode under Monitor has a gate.** Every Monitor event wakes Claude, and
a wake re-reads the whole conversation from cache, so a quiet one costs about as
much as a busy one. On a 3-hour collaborating call (Opus, 10s batches), 302 of
595 calls were a bare `.`, and they were 55% of its 154M cache-read tokens. So
`follow` holds listen-mode turns, and a Haiku subagent running `intercom gate`
says which batches the main agent would act on. Deterministic rules don't wait
for it: a turn naming Claude, device news, `wake-next`, and a 30 s ceiling on
the oldest undelivered line. Replayed against that call (Haiku answering each of
its 523 batches, scored against what Opus actually did), Opus woke 226 times
instead of 480; 63% of its actions arrived on the spot, 80% within 15 s, all
within 30 s. Rules alone woke it 166 times but put only 22% on the spot. What
made the gate work:
- It sees the main agent's posts (read from its JSONL), so it can tell the room
  reading out an answer from a new question. Without them it woke Opus twice as
  often and drifted towards "no" as its context grew.
- Its question line sits after every batch. How the threshold is worded (40%,
  "think briefly") made no measurable difference.
- Half its "false" wakes were one batch early: Opus waited for the point. Hence
  `wake-next`, which only Opus can know to ask for.

The relay is unchanged. `follow` is its only reader, and the two agents meet in
files under `~/.intercom/gate-<id>/`, one writer each (spool and delivery cursor
from `follow`; answers, gate state and the `wake` flag from the gate;
`wake-next` and the brief from the main agent). A gate that ends its turn
notifies the main agent, which is how a respawn is asked for: past 100K tokens
Haiku's input costs 5x, so each gate is told `RESPAWN` after reading up to
~35K, and the next starts from the whole conversation so far, compact. That
fits easily: the call above was ~40K tokens of transcript and posts, while most
of a long-running gate's context was its own per-call framing. Polling the
relay every 2 s instead of long-polling for 10 s costs ~1,800 requests a meeting
hour, well inside the free plan.

**`speak` is compare-and-swap.** Claude passes the last entry it read; a newer
turn from Louie, or one still in progress, means nothing is played and the
turn comes back instead, so Claude never answers a moment that has passed.

**`speak` survives a lost reply.** It blocks for the whole utterance, often
30s or more with nothing on the wire, and claude.ai's sandbox proxy
sometimes closed such a request: the speech played in full, Cloudflare logged
the client leaving after 31s, and the CLI said `not spoken`. Other calls as
long went through, so it is no fixed timeout. Now each call carries an id: a
CLI that loses the connection asks again under it, and the relay hands back
the same call's outcome, waiting on one still speaking or reading one that
finished (kept in storage, the last 20), so nothing is said twice. While it
speaks, the relay sends a space every 5s ahead of the JSON, in case quiet is
what the proxy objects to. A `done` comes back as just its status and length:
the text was Claude's own.

**A dropped page resumes.** A socket drop (home Wi-Fi blipped twice in an hour,
2s each; walking means switching networks) used to cost the audio in the gap,
a new transcriber stream, and Claude being told. Now the page keeps
recording through it and keeps its last 15s of chunks; the relay keeps the
transcriber's stream open for 15s and counts the chunks it got. When the
page's new socket names the same recorder run, the relay says how many
arrived, the page resends the rest, and the stream carries on, its clock
intact, since the recorder never stopped. Claude isn't told (a quiet `net`
entry), and a `speak` meanwhile waits for the page. Past the grace, or after an
object restart, it's a drop as before. Measured on the providers: Krisp
returned a 30s burst in about a second; Soniox takes a backlog but works
through it at about real time, so after a 6s gap turns ran up to ~5s late for
~30s. Hence a short grace rather than a long one. To notice a socket that died
without closing (a network switch), the relay pings every screen every 2s
and the page opens a new socket after 6s without one, or at once when the
browser reports a different kind of network; a device's newer socket replaces
its older one, so the swap needs no drop. Claude's voice is asked for again if
its connection fails, three times.

**One recorder run = one webm header = one transcriber stream** (a "segment").
A stream that resumes mid-container is not reliably decodable, so every
reconnect, on either side, starts a new segment unless the page resumes (above):
the relay tells the page to
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

**Free plan fits.** Per meeting hour: ~450 GB-s of 13,000/day, ~2,500 billed
requests of 100k/day (incoming WebSocket messages bill 20:1, and the gated
`follow` polls every 2 s), ~720 rows written. Archived audio is buffered to ~30s per row for the same reason.
