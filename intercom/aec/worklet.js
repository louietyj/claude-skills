// AEC3 on the raw mic (input channel 0) against Claude's voice as played
// (channel 1, already scaled by the duck volume). Output: the cleaned mic.
// While cancelling, posts the share of each 100 ms that WebRTC's VAD calls
// voice: steady noise (running water) is as loud as Louie but not voice.
import { instantiate } from "./aec3.js";

// AEC3 costs the watch as much with a silent reference as with Claude talking,
// so it runs only from Claude's first sound until this long after the last:
// the echo tail, and audio still in the jitter buffer.
const HOLD_S = 2;
const AUDIBLE = 1e-4;   // -80 dBFS: a muted or idle track is exact zeros

registerProcessor("aec3", class extends AudioWorkletProcessor {
  constructor({ processorOptions }) {
    super();
    this.aec = instantiate(processorOptions.module).create({ sampleRate, vad: 3 });
    this.voiced = this.counted = 0;
    this.cancelling = false;
    this.silence = new Float32Array(128);
    // The mic also runs through a delay line as long as the canceller's own, so
    // switching between them doesn't skip or repeat audio.
    this.line = new Float32Array(this.aec.latencySamples);
    this.at = 0;
    this.quiet = Infinity;   // samples since the reference was last audible
  }

  process([input], [[out]]) {
    const mic = input[0], ref = input[1];
    if (!mic) return true;
    if (ref && ref.some((v) => v > AUDIBLE || v < -AUDIBLE)) this.quiet = 0;
    else this.quiet += mic.length;
    const cancel = this.quiet < HOLD_S * sampleRate;
    for (let i = 0; i < mic.length; i++) {
      const delayed = this.line[this.at];
      this.line[this.at] = mic[i];
      this.at = (this.at + 1) % this.line.length;
      if (!cancel) out[i] = delayed;
    }
    if (!cancel) {
      if (this.cancelling) this.port.postMessage(null);
      this.cancelling = false;
      this.voiced = this.counted = 0;
      return true;
    }
    this.cancelling = true;
    this.aec.cancel(mic, ref || this.silence, out);
    if (this.aec.stats().voice) this.voiced += mic.length;
    this.counted += mic.length;
    if (this.counted >= sampleRate / 10) {
      this.port.postMessage(this.voiced / this.counted);
      this.voiced = this.counted = 0;
    }
    return true;
  }
});
