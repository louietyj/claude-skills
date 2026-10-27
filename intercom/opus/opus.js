// A page's MediaRecorder webm/opus, chunk by chunk, to 16-bit PCM, with
// libopus's decoder (opus.wasm, from build.sh). The relay passes the module
// in: Workers can't compile wasm at runtime.

// libc's stdio comes along; the decoder never writes anything.
const WASI_STUBS = { fd_close: () => 0, fd_write: () => 0, fd_seek: () => 0 };
const MAX_PACKET = 4000;          // opus's own ceiling for one packet
const MAX_FRAME_MS = 120;

// Elements to descend into rather than skip: Segment, Cluster, BlockGroup.
// Chrome writes the first two with unknown size, so they can't be skipped anyway.
const MASTER = new Set([0x18538067, 0x1f43b675, 0xa0]);
const BLOCK = new Set([0xa3, 0xa1]);          // SimpleBlock, Block

// An EBML variable-length integer: ids keep their length marker, sizes drop it.
function vint(buf, pos, isId) {
  if (pos >= buf.length) return null;
  const first = buf[pos];
  let len = 1, mask = 0x80;
  while (len <= 8 && !(first & mask)) { len++; mask >>= 1; }
  if (len > 8 || pos + len > buf.length) return null;
  let v = isId ? first : first & (mask - 1);
  let allOnes = (first & (mask - 1)) === mask - 1;
  for (let i = 1; i < len; i++) { v = v * 256 + buf[pos + i]; if (buf[pos + i] !== 0xff) allOnes = false; }
  return { v, len, unknown: !isId && allOnes };
}

// Opus packets out of a webm byte stream that arrives in arbitrary pieces.
class WebmDemuxer {
  constructor() { this.buf = new Uint8Array(0); this.skip = 0; }

  push(chunk) {
    const b = new Uint8Array(this.buf.length + chunk.byteLength);
    b.set(this.buf);
    b.set(new Uint8Array(chunk), this.buf.length);
    const packets = [];
    let pos = Math.min(this.skip, b.length);
    this.skip -= pos;
    while (true) {
      const id = vint(b, pos, true); if (!id) break;
      const size = vint(b, pos + id.len, false); if (!size) break;
      const body = pos + id.len + size.len;
      if (MASTER.has(id.v)) { pos = body; continue; }
      if (size.unknown) throw new Error(`webm element 0x${id.v.toString(16)} has unknown size`);
      if (BLOCK.has(id.v)) {
        if (body + size.v > b.length) break;
        const track = vint(b, body, false);
        const flags = b[body + track.len + 2];
        // Track number, a 2-byte timecode, flags; Chrome never laces opus.
        if (!(flags & 0x06)) packets.push(b.subarray(body + track.len + 3, body + size.v));
        pos = body + size.v;
        continue;
      }
      if (body + size.v > b.length) { this.skip = body + size.v - b.length; pos = b.length; break; }
      pos = body + size.v;
    }
    this.buf = b.slice(pos);
    return packets;
  }
}

export class WebmOpusDecoder {
  constructor(module, rate) {
    this.x = new WebAssembly.Instance(module, { wasi_snapshot_preview1: WASI_STUBS }).exports;
    this.x._initialize();
    this.maxSamples = rate * MAX_FRAME_MS / 1000;
    const err = this.x.malloc(4);
    this.dec = this.x.opus_decoder_create(rate, 1, err);
    const code = new Int32Array(this.x.memory.buffer, err, 1)[0];
    this.x.free(err);
    if (code) throw new Error(`opus_decoder_create failed (${code})`);
    this.inPtr = this.x.malloc(MAX_PACKET);
    this.outPtr = this.x.malloc(this.maxSamples * 2);
    this.demux = new WebmDemuxer();
  }

  // Every whole packet in what has arrived so far, as one run of samples.
  push(chunk) {
    const out = [];
    let total = 0;
    for (const packet of this.demux.push(chunk)) {
      if (packet.length > MAX_PACKET) continue;
      new Uint8Array(this.x.memory.buffer, this.inPtr, packet.length).set(packet);
      const n = this.x.opus_decode(this.dec, this.inPtr, packet.length, this.outPtr, this.maxSamples, 0);
      if (n <= 0) continue;           // a corrupt packet: drop it, keep the stream
      out.push(new Int16Array(this.x.memory.buffer, this.outPtr, n).slice());
      total += n;
    }
    const pcm = new Int16Array(total);
    let at = 0;
    for (const run of out) { pcm.set(run, at); at += run.length; }
    return pcm;
  }

  free() {
    if (!this.dec) return;
    this.x.opus_decoder_destroy(this.dec);
    this.x.free(this.inPtr);
    this.x.free(this.outPtr);
    this.dec = 0;
  }
}
