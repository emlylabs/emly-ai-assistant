/**
 * Client for a realtime voice call (`/widget/{bot}/voice/ws`), served by
 * the backend's self-hosted Pipecat pipeline (`services/voice_call.py`).
 *
 * - Mic: captured with Web Audio, downsampled to 16 kHz 16-bit PCM and sent
 *   as binary messages. Echo cancellation keeps the bot's own voice out.
 * - Bot voice: 24 kHz 16-bit PCM binary messages, played gaplessly as they
 *   arrive. `{"type":"interrupt"}` (the visitor talked over the bot) drops
 *   whatever is still queued.
 * - Chat: JSON events carry live captions, the visitor's final transcript
 *   and the bot's streamed answer, so the call shows up as chat bubbles.
 *
 * The widget token goes in the first message, not the URL (URLs end up in
 * access logs), and a rejected token is retried once with a fresh one.
 */
import { ensureWidgetIdentity, markTokenStale } from './widgetAuth';

const MIC_RATE = 16000;
const DEFAULT_OUT_RATE = 24000;
/** ~43 ms at 48 kHz. */
const CAPTURE_BUFFER = 2048;
const READY_TIMEOUT_MS = 15000;
/** Small lead so the first chunk isn't scheduled in the past. */
const PLAYBACK_LEAD_S = 0.05;

export type VoiceCallState = 'connecting' | 'listening' | 'speaking';

export type VoiceCallReply = {
  text: string;
  messageId: number | null;
  citations: unknown[];
  error: boolean;
};

export type VoiceCallEvents = {
  onState?: (state: VoiceCallState) => void;
  /** Live caption while the visitor talks. */
  onUserInterim?: (text: string) => void;
  /** The visitor finished a turn. */
  onUserTranscript?: (turn: number, text: string) => void;
  /** A piece of the bot's answer, raw (may contain `<internal_code>`). */
  onBotText?: (turn: number, delta: string) => void;
  onBotDone?: (turn: number, reply: VoiceCallReply) => void;
  /** The call ended. `reason` is null when the visitor hung up. */
  onEnd?: (reason: string | null) => void;
};

type ServerEvent = {
  type?: string;
  code?: string;
  text?: string;
  turn?: number;
  message_id?: number | null;
  citations?: unknown[];
  error?: boolean;
};

type AudioContextCtor = new () => AudioContext;

function audioContextCtor(): AudioContextCtor | null {
  if (typeof window === 'undefined') return null;
  const w = window as Window & { AudioContext?: AudioContextCtor; webkitAudioContext?: AudioContextCtor };
  return w.AudioContext || w.webkitAudioContext || null;
}

export type MicDevice = { id: string; label: string };

/** Microphones the browser can see. Labels appear once mic access is granted. */
export async function listMicrophones(): Promise<MicDevice[]> {
  if (typeof navigator === 'undefined' || !navigator.mediaDevices?.enumerateDevices) return [];
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    return devices
      .filter((d) => d.kind === 'audioinput' && d.deviceId)
      .map((d, i) => ({ id: d.deviceId, label: d.label || `Microphone ${i + 1}` }));
  } catch {
    return [];
  }
}

const MIC_CONSTRAINTS = { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 };

export function isVoiceCallSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    window.isSecureContext === true &&
    typeof WebSocket !== 'undefined' &&
    !!navigator.mediaDevices?.getUserMedia &&
    audioContextCtor() !== null
  );
}

/** Float samples at `inRate` → 16-bit PCM at `outRate`, by averaging. Keeps
 * the fractional position between calls so buffers join without clicks. */
export class Downsampler {
  private readonly ratio: number;
  private pos = 0;
  private pending: number[] = [];

  constructor(inRate: number, outRate: number) {
    this.ratio = inRate / outRate;
  }

  push(input: Float32Array): Int16Array {
    for (let i = 0; i < input.length; i += 1) this.pending.push(input[i]);
    const out: number[] = [];
    while (this.pos + this.ratio <= this.pending.length) {
      const start = Math.floor(this.pos);
      const end = Math.min(this.pending.length, Math.floor(this.pos + this.ratio));
      let sum = 0;
      for (let j = start; j < end; j += 1) sum += this.pending[j];
      const sample = end > start ? sum / (end - start) : this.pending[start];
      const clamped = Math.max(-1, Math.min(1, sample));
      out.push(clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff);
      this.pos += this.ratio;
    }
    const consumed = Math.floor(this.pos);
    this.pending = this.pending.slice(consumed);
    this.pos -= consumed;
    return Int16Array.from(out);
  }
}

/** Gapless PCM playback that can be cleared on interruption. */
export class PcmQueue {
  private readonly ctx: AudioContext;
  private nextStart = 0;
  private carry: Uint8Array | null = null;
  private readonly sources = new Set<AudioBufferSourceNode>();
  private readonly output: AudioNode;

  constructor(ctx: AudioContext, output?: AudioNode) {
    this.ctx = ctx;
    this.output = output ?? ctx.destination;
  }

  push(chunk: ArrayBuffer, sampleRate: number): void {
    let bytes = new Uint8Array(chunk);
    if (this.carry) {
      const joined = new Uint8Array(this.carry.length + bytes.length);
      joined.set(this.carry);
      joined.set(bytes, this.carry.length);
      bytes = joined;
      this.carry = null;
    }
    const usable = bytes.length - (bytes.length % 2);
    if (usable < bytes.length) this.carry = bytes.slice(usable);
    if (usable === 0) return;
    const count = usable / 2;
    const view = new DataView(bytes.buffer, bytes.byteOffset, usable);
    const buffer = this.ctx.createBuffer(1, count, sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < count; i += 1) data[i] = view.getInt16(i * 2, true) / 32768;
    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.output);
    const startAt = Math.max(this.nextStart, this.ctx.currentTime + PLAYBACK_LEAD_S);
    source.start(startAt);
    this.nextStart = startAt + buffer.duration;
    this.sources.add(source);
    source.onended = () => {
      this.sources.delete(source);
    };
  }

  /** True while queued audio is still playing. */
  playing(): boolean {
    return this.nextStart > this.ctx.currentTime;
  }

  clear(): void {
    this.sources.forEach((source) => {
      try {
        source.onended = null;
        source.stop();
      } catch {
        // already stopped
      }
    });
    this.sources.clear();
    this.carry = null;
    this.nextStart = 0;
  }
}

/** RMS loudness 0..1 of what an analyser hears right now. */
function levelOf(analyser: AnalyserNode | null, scratch: Float32Array | null): number {
  if (!analyser || !scratch) return 0;
  analyser.getFloatTimeDomainData(scratch);
  let sum = 0;
  for (let i = 0; i < scratch.length; i += 1) sum += scratch[i] * scratch[i];
  // Speech RMS rarely passes ~0.3; scale so normal speech fills the meter.
  return Math.min(1, Math.sqrt(sum / scratch.length) * 4);
}

export class VoiceCall {
  private readonly url: string;
  private readonly sessionId: string;
  private readonly lang: string | null;
  private readonly events: VoiceCallEvents;
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private ws: WebSocket | null = null;
  private capture: { source: MediaStreamAudioSourceNode; node: ScriptProcessorNode; sink: GainNode } | null = null;
  private player: PcmQueue | null = null;
  private micAnalyser: AnalyserNode | null = null;
  private botAnalyser: AnalyserNode | null = null;
  private micScratch: Float32Array | null = null;
  private botScratch: Float32Array | null = null;
  private muted = false;
  private stateTimer: ReturnType<typeof setInterval> | null = null;
  private state: VoiceCallState = 'connecting';
  private ended = false;
  private outRate = DEFAULT_OUT_RATE;

  constructor(url: string, opts: { sessionId: string; lang?: string | null }, events: VoiceCallEvents) {
    this.url = url;
    this.sessionId = opts.sessionId;
    this.lang = opts.lang ?? null;
    this.events = events;
  }

  /** Call from the click that starts the call: audio needs a gesture. */
  start(): void {
    const Ctor = audioContextCtor();
    if (!Ctor || !this.url) {
      this.finish('unsupported');
      return;
    }
    try {
      this.ctx = new Ctor();
      // Inside the click, so the browser lets audio play.
      void this.ctx.resume().catch(() => {});
    } catch {
      this.finish('unsupported');
      return;
    }
    this.setState('connecting');
    void this.connect();
  }

  hangup(): void {
    this.finish(null);
  }

  /** Send a typed message into the call; it is answered like speech. */
  sendText(text: string): boolean {
    const ws = this.ws;
    const trimmed = text.trim();
    if (!trimmed || !ws || ws.readyState !== WebSocket.OPEN || this.state === 'connecting') return false;
    // A typed message interrupts the bot, so stop its audio here too.
    this.player?.clear();
    ws.send(JSON.stringify({ type: 'text', text: trimmed }));
    return true;
  }

  /** The microphone in use, if known. */
  currentMicrophone(): string | null {
    return this.stream?.getAudioTracks()[0]?.getSettings().deviceId ?? null;
  }

  /** Switch microphones mid-call without dropping the call. */
  async setMicrophone(deviceId: string): Promise<boolean> {
    const ctx = this.ctx;
    if (!ctx || this.ended || !this.capture) return false;
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { ...MIC_CONSTRAINTS, deviceId: { exact: deviceId } },
      });
    } catch {
      return false;
    }
    if (this.ended || !this.capture) {
      stream.getTracks().forEach((track) => track.stop());
      return false;
    }
    const source = ctx.createMediaStreamSource(stream);
    source.connect(this.capture.node);
    if (this.micAnalyser) source.connect(this.micAnalyser);
    try {
      this.capture.source.disconnect();
    } catch {
      // ignore
    }
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = stream;
    this.capture.source = source;
    stream.getAudioTracks().forEach((track) => {
      track.enabled = !this.muted;
    });
    return true;
  }

  /** Muted: the mic sends silence, so the bot hears nothing. */
  setMuted(muted: boolean): void {
    this.muted = muted;
    this.stream?.getAudioTracks().forEach((track) => {
      track.enabled = !muted;
    });
  }

  isMuted(): boolean {
    return this.muted;
  }

  /** Live loudness for the call screen's visualizer. */
  levels(): { mic: number; bot: number } {
    return {
      mic: this.muted ? 0 : levelOf(this.micAnalyser, this.micScratch),
      bot: levelOf(this.botAnalyser, this.botScratch),
    };
  }

  private async connect(): Promise<void> {
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: MIC_CONSTRAINTS });
    } catch (err) {
      const name = (err as { name?: string } | null)?.name;
      this.finish(name === 'NotAllowedError' ? 'not-allowed' : name === 'NotFoundError' ? 'audio-capture' : 'start-failed');
      return;
    }
    if (this.ended) {
      this.releaseMic();
      return;
    }
    try {
      await this.openSocket();
    } catch (err) {
      const code = err instanceof Error ? err.message : 'connect_failed';
      if (code === 'widget_token_invalid' && !this.ended) {
        markTokenStale();
        try {
          await this.openSocket();
          return;
        } catch (retryErr) {
          this.finish(retryErr instanceof Error ? retryErr.message : 'connect_failed');
          return;
        }
      }
      this.finish(code);
    }
  }

  /** Resolves once the server says `ready`; rejects with the error code. */
  private openSocket(): Promise<void> {
    return new Promise((resolve, reject) => {
      void ensureWidgetIdentity().then(
        ({ token, userId }) => {
          if (this.ended) {
            resolve();
            return;
          }
          let ws: WebSocket;
          try {
            ws = new WebSocket(this.url);
          } catch {
            reject(new Error('connect_failed'));
            return;
          }
          ws.binaryType = 'arraybuffer';
          this.ws = ws;
          let ready = false;
          let errorCode: string | null = null;
          const timer = setTimeout(() => {
            if (!ready) {
              ws.onclose = null;
              try {
                ws.close();
              } catch {
                // ignore
              }
              reject(new Error('ready_timeout'));
            }
          }, READY_TIMEOUT_MS);

          ws.onopen = () => {
            ws.send(JSON.stringify({
              type: 'auth',
              token,
              user_id: userId,
              session_id: this.sessionId,
              ...(this.lang ? { lang: this.lang } : {}),
            }));
          };
          ws.onmessage = (event: MessageEvent) => {
            if (typeof event.data !== 'string') {
              if (ready) this.player?.push(event.data as ArrayBuffer, this.outRate);
              return;
            }
            let msg: ServerEvent;
            try {
              msg = JSON.parse(event.data) as ServerEvent;
            } catch {
              return;
            }
            if (msg.type === 'error') {
              errorCode = String(msg.code || 'error');
              return;
            }
            if (msg.type === 'ready' && !ready) {
              ready = true;
              clearTimeout(timer);
              this.beginAudio();
              resolve();
              return;
            }
            if (ready) this.handleEvent(msg);
          };
          ws.onclose = () => {
            clearTimeout(timer);
            if (!ready) {
              reject(new Error(errorCode || 'connect_failed'));
              return;
            }
            this.finish(errorCode || 'disconnected');
          };
        },
        () => reject(new Error('widget_init_failed')),
      );
    });
  }

  private beginAudio(): void {
    const ctx = this.ctx;
    const stream = this.stream;
    if (!ctx || !stream || this.ended) return;
    const botAnalyser = ctx.createAnalyser();
    botAnalyser.fftSize = 512;
    botAnalyser.connect(ctx.destination);
    this.botAnalyser = botAnalyser;
    this.botScratch = new Float32Array(botAnalyser.fftSize);
    this.player = new PcmQueue(ctx, botAnalyser);
    const source = ctx.createMediaStreamSource(stream);
    const micAnalyser = ctx.createAnalyser();
    micAnalyser.fftSize = 512;
    source.connect(micAnalyser);
    this.micAnalyser = micAnalyser;
    this.micScratch = new Float32Array(micAnalyser.fftSize);
    // ScriptProcessor rather than an AudioWorklet: worklets load from a
    // URL, which host pages' CSP often blocks.
    const node = ctx.createScriptProcessor(CAPTURE_BUFFER, 1, 1);
    const sink = ctx.createGain();
    sink.gain.value = 0; // the processor must reach the output to run; keep it silent
    const downsampler = new Downsampler(ctx.sampleRate, MIC_RATE);
    node.onaudioprocess = (event: AudioProcessingEvent) => {
      const ws = this.ws;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      const pcm = downsampler.push(event.inputBuffer.getChannelData(0));
      if (pcm.length) ws.send(pcm.buffer);
    };
    source.connect(node);
    node.connect(sink);
    sink.connect(ctx.destination);
    this.capture = { source, node, sink };
    this.setState('listening');
    this.stateTimer = setInterval(() => {
      if (this.ended || !this.player) return;
      this.setState(this.player.playing() ? 'speaking' : 'listening');
    }, 150);
  }

  private handleEvent(msg: ServerEvent): void {
    const turn = typeof msg.turn === 'number' ? msg.turn : 0;
    switch (msg.type) {
      case 'interrupt':
        this.player?.clear();
        this.setState('listening');
        break;
      case 'user_interim':
        this.events.onUserInterim?.(String(msg.text || ''));
        break;
      case 'user_transcript':
        this.events.onUserTranscript?.(turn, String(msg.text || ''));
        break;
      case 'bot_text':
        this.events.onBotText?.(turn, String(msg.text || ''));
        break;
      case 'bot_done':
        this.events.onBotDone?.(turn, {
          text: String(msg.text || ''),
          messageId: typeof msg.message_id === 'number' ? msg.message_id : null,
          citations: Array.isArray(msg.citations) ? msg.citations : [],
          error: Boolean(msg.error),
        });
        break;
      default:
        break;
    }
  }

  private setState(next: VoiceCallState): void {
    if (this.state === next) return;
    this.state = next;
    this.events.onState?.(next);
  }

  private releaseMic(): void {
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
  }

  private finish(reason: string | null): void {
    if (this.ended) return;
    this.ended = true;
    if (this.stateTimer !== null) clearInterval(this.stateTimer);
    this.stateTimer = null;
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onclose = null;
      ws.onmessage = null;
      try {
        ws.close(1000);
      } catch {
        // ignore
      }
    }
    if (this.capture) {
      try {
        this.capture.node.onaudioprocess = null;
        this.capture.source.disconnect();
        this.capture.node.disconnect();
        this.capture.sink.disconnect();
      } catch {
        // ignore
      }
      this.capture = null;
    }
    this.player?.clear();
    this.player = null;
    this.micAnalyser = null;
    this.botAnalyser = null;
    this.releaseMic();
    const ctx = this.ctx;
    this.ctx = null;
    void ctx?.close().catch(() => {});
    this.events.onEnd?.(reason);
  }
}
