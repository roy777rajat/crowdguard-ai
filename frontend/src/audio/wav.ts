/** Browser audio -> the exact WAV format Muse Voice Transcribe accepts: RIFF/WAVE, mono, 16-bit PCM, 16 kHz. */

export const TARGET_RATE = 16000;

export function encodeWav16(samples: Float32Array, sampleRate: number = TARGET_RATE): Blob {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const str = (o: number, s: string) => {
    for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i));
  };
  str(0, 'RIFF');
  v.setUint32(4, 36 + samples.length * 2, true);
  str(8, 'WAVE');
  str(12, 'fmt ');
  v.setUint32(16, 16, true); // PCM chunk size
  v.setUint16(20, 1, true); // PCM
  v.setUint16(22, 1, true); // mono
  v.setUint32(24, sampleRate, true);
  v.setUint32(28, sampleRate * 2, true); // byte rate
  v.setUint16(32, 2, true); // block align
  v.setUint16(34, 16, true); // bits per sample
  str(36, 'data');
  v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buf], { type: 'audio/wav' });
}

type Ctor = typeof AudioContext;

/** Decode whatever MediaRecorder produced (webm/ogg/mp4), downmix and resample to 16 kHz mono, encode as WAV. */
export async function toWav16k(recording: Blob): Promise<Blob> {
  const AC: Ctor | undefined = window.AudioContext ?? (window as unknown as { webkitAudioContext?: Ctor }).webkitAudioContext;
  if (!AC || typeof OfflineAudioContext === 'undefined') throw new Error('This browser cannot convert audio.');
  const ctx = new AC();
  try {
    const decoded = await ctx.decodeAudioData(await recording.arrayBuffer());
    const frames = Math.max(1, Math.ceil(decoded.duration * TARGET_RATE));
    const off = new OfflineAudioContext(1, frames, TARGET_RATE); // 1 channel: the graph downmixes for us
    const src = off.createBufferSource();
    src.buffer = decoded;
    src.connect(off.destination);
    src.start();
    const rendered = await off.startRendering();
    // Half a second of silence before and after: recognisers can clip words spoken right at the edges.
    const pad = Math.round(TARGET_RATE * 0.5);
    const data = rendered.getChannelData(0);
    const out = new Float32Array(data.length + pad * 2);
    out.set(data, pad);
    return encodeWav16(out, TARGET_RATE);
  } finally {
    void ctx.close();
  }
}
