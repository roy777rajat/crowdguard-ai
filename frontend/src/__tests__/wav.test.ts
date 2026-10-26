import { describe, expect, it } from 'vitest';
import { encodeWav16 } from '../audio/wav';

async function bytes(b: Blob): Promise<DataView> {
  return new DataView(await new Promise<ArrayBuffer>((res) => {
    const r = new FileReader();
    r.onload = () => res(r.result as ArrayBuffer);
    r.readAsArrayBuffer(b);
  }));
}

describe('encodeWav16', () => {
  it('writes a mono 16-bit 16 kHz PCM WAV header and clamps samples', async () => {
    const v = await bytes(encodeWav16(new Float32Array([0, 1, -1, 2, -2]), 16000));
    const tag = (o: number) => String.fromCharCode(v.getUint8(o), v.getUint8(o + 1), v.getUint8(o + 2), v.getUint8(o + 3));
    expect([tag(0), tag(8), tag(12), tag(36)]).toEqual(['RIFF', 'WAVE', 'fmt ', 'data']);
    expect(v.getUint16(20, true)).toBe(1);        // PCM
    expect(v.getUint16(22, true)).toBe(1);        // mono
    expect(v.getUint32(24, true)).toBe(16000);
    expect(v.getUint16(34, true)).toBe(16);
    expect(v.getUint32(40, true)).toBe(10);
    expect(v.byteLength).toBe(44 + 10);
    expect([v.getInt16(44, true), v.getInt16(46, true), v.getInt16(48, true)]).toEqual([0, 32767, -32768]);
    expect([v.getInt16(50, true), v.getInt16(52, true)]).toEqual([32767, -32768]);
  });
});
