// AEC3 on the raw mic (input channel 0) against Claude's voice as played
// (channel 1, already scaled by the duck volume). Output: the cleaned mic.
import { instantiate } from "./aec3.js";

// AEC3 costs the watch as much with a silent reference as with Claude talking,
// so it runs only from Claude's first sound until this long after the last:
// the echo tail, and audio still in the jitter buffer.
const HOLD_S = 2;
const AUDIBLE = 1e-4;   // -80 dBFS: a muted or idle track is exact zeros

registerProcessor("aec3", class extends AudioWorkletProcessor {
  constructor({ processorOptions }) {
    super();
    this.aec = instantiate(processorOptions.module).create({ sampleRate });
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
    if (cancel) this.aec.cancel(mic, ref || this.silence, out);
    return true;
  }
});
