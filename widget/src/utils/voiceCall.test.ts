import { describe, expect, it, vi } from 'vitest';
import { Downsampler, PcmQueue } from './voiceCall';

describe('Downsampler', () => {
  it('turns 48 kHz floats into 16 kHz 16-bit samples', () => {
    const d = new Downsampler(48000, 16000);
    const out = d.push(new Float32Array(4800).fill(0.5));
    expect(out.length).toBe(1600);
    // Averaging a constant keeps it.
    expect(out[0]).toBe(Math.trunc(0.5 * 0x7fff));
  });

  it('joins buffers without losing samples', () => {
    const d = new Downsampler(44100, 16000);
    let total = 0;
    for (let i = 0; i < 10; i += 1) total += d.push(new Float32Array(441)).length;
    // 4410 samples at 44.1 kHz is 100 ms: 1600 at 16 kHz, give or take one.
    expect(Math.abs(total - 1600)).toBeLessThanOrEqual(1);
  });

  it('clamps out-of-range input', () => {
    const out = new Downsampler(16000, 16000).push(Float32Array.from([2, -2]));
    expect(Array.from(out)).toEqual([0x7fff, -0x8000]);
  });
});

class FakeCtx {
  currentTime = 0;
  destination = {};
  starts: number[] = [];
  stops = 0;
  createBuffer(_channels: number, length: number, rate: number) {
    return { duration: length / rate, getChannelData: () => new Float32Array(length) };
  }
  createBufferSource() {
    return {
      buffer: null as unknown,
      onended: null as unknown,
      connect: () => {},
      start: (at: number) => {
        this.starts.push(at);
      },
      stop: vi.fn(() => {
        this.stops += 1;
      }),
    };
  }
}

describe('PcmQueue', () => {
  it('schedules chunks back to back and reports playing', () => {
    const ctx = new FakeCtx();
    const q = new PcmQueue(ctx as unknown as AudioContext);
    q.push(new ArrayBuffer(24000 * 2), 24000); // 1 s
    q.push(new ArrayBuffer(24000), 24000); // 0.5 s
    expect(ctx.starts[1]).toBeCloseTo(ctx.starts[0] + 1, 5);
    expect(q.playing()).toBe(true);
    ctx.currentTime = 2;
    expect(q.playing()).toBe(false);
  });

  it('keeps a sample split across chunks', () => {
    const ctx = new FakeCtx();
    const q = new PcmQueue(ctx as unknown as AudioContext);
    q.push(new ArrayBuffer(3), 24000);
    q.push(new ArrayBuffer(1), 24000);
    expect(ctx.starts.length).toBe(2); // 1 sample, then the joined 1 sample
  });

  it('clear() stops queued audio so an interruption is silent at once', () => {
    const ctx = new FakeCtx();
    const q = new PcmQueue(ctx as unknown as AudioContext);
    q.push(new ArrayBuffer(4800), 24000);
    q.push(new ArrayBuffer(4800), 24000);
    q.clear();
    expect(ctx.stops).toBe(2);
    expect(q.playing()).toBe(false);
  });
});
