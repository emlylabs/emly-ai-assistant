/**
 * Voice helpers for the embeddable widget.
 *
 * - STT (mic): browser Web Speech API (default) or Deepgram via the
 *   backend STT endpoint.
 * - TTS: browser ``speechSynthesis`` (default) or Deepgram via the
 *   backend ``/widget/{bot}/tts`` endpoint (API key stays server-side).
 *
 * Browser voice is processed by the visitor's browser vendor (e.g. Google
 * for Chrome, Microsoft for Edge) and does not pass through Emly servers.
 * Mic access needs a secure context (HTTPS or localhost).
 *
 * iOS Safari only speaks when `speak()` runs inside a user gesture, so
 * automatic read-aloud after a voice question can fail silently there.
 * The speaker button on a reply still works, because that click is a gesture.
 */

type SpeechRecognitionLike = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: { error?: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
};

type SpeechRecognitionEventLike = {
  resultIndex: number;
  results: ArrayLike<{
    isFinal: boolean;
    0: { transcript: string };
  }>;
};

type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

function getSpeechRecognitionCtor(): SpeechRecognitionCtor | null {
  if (typeof window === 'undefined') return null;
  const w = window as Window & {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition || w.webkitSpeechRecognition || null;
}

export function isSpeechRecognitionSupported(): boolean {
  return getSpeechRecognitionCtor() !== null;
}

export function isSpeechSynthesisSupported(): boolean {
  return typeof window !== 'undefined' && typeof window.speechSynthesis !== 'undefined';
}

/** The mic API exists on plain HTTP, but browsers refuse to start it there. */
export function isSecureVoiceContext(): boolean {
  return typeof window !== 'undefined' && window.isSecureContext === true;
}

/** The bot's configured voice language, else the browser's, else en-US. */
export function resolveVoiceLang(override?: string | null): string {
  const configured = override?.trim();
  if (configured) return configured;
  if (typeof navigator !== 'undefined' && navigator.language) return navigator.language;
  return 'en-US';
}

/** A message for the visitor, or null when the error needs no message. */
export function micErrorMessage(code: string): string | null {
  switch (code) {
    case 'aborted':
    case 'no-speech':
      return null;
    case 'not-allowed':
      return 'Microphone permission was blocked.';
    case 'service-not-allowed':
      return 'Voice input is turned off or blocked in this browser.';
    case 'audio-capture':
      return 'No microphone was found.';
    case 'network':
      return 'Voice input needs a network connection.';
    case 'language-not-supported':
      return "Voice input doesn't support this language.";
    default:
      return "Voice input couldn't start. Please type your message.";
  }
}

/** Drop a URL's trailing punctuation, keeping a `)` that closes a `(` in the URL. */
function splitUrlTrailer(url: string): [string, string] {
  let end = url.length;
  while (end > 0) {
    const ch = url[end - 1];
    if ('.,!?:;'.includes(ch)) {
      end -= 1;
      continue;
    }
    if (ch === ')') {
      const core = url.slice(0, end);
      const opens = (core.match(/\(/g) || []).length;
      const closes = (core.match(/\)/g) || []).length;
      if (closes > opens) {
        end -= 1;
        continue;
      }
    }
    break;
  }
  return [url.slice(0, end), url.slice(end)];
}

/** Strip markdown / URLs so TTS does not read markup aloud. */
export function plainTextForSpeech(input: string): string {
  let text = input ?? '';
  // Fenced code blocks
  text = text.replace(/```[\s\S]*?```/g, ' ');
  // Inline code
  text = text.replace(/`([^`]+)`/g, '$1');
  // Link targets may hold one level of balanced parens, e.g. `a_(b)`.
  const target = '\\((?:[^()\\s]|\\([^()\\s]*\\))+\\)';
  // Images ![alt](url) → alt
  text = text.replace(new RegExp(`!\\[([^\\]]*)\\]${target}`, 'g'), '$1');
  // Footnote definitions lose their marker; inline refs are dropped.
  text = text.replace(/^[ \t]*\[\^[^\]]+\]:[ \t]*/gm, '');
  text = text.replace(/\[\^[^\]]+\]/g, '');
  // Links [label](url) → label
  text = text.replace(new RegExp(`\\[([^\\]]+)\\]${target}`, 'g'), '$1');
  // HTML tags
  text = text.replace(/<\/?[A-Za-z][^>]*>/g, ' ');
  // Line-level markup. Runs before emphasis so `* item` and `***` are
  // not read as emphasis markers.
  // Table delimiter rows: | --- | :---: |
  text = text.replace(/^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(\|[ \t]*:?-{3,}:?[ \t]*)+\|?[ \t]*$/gm, '');
  // Horizontal rules: ---, ***, ___ (also spaced).
  text = text.replace(/^[ \t]*([-*_])([ \t]*\1){2,}[ \t]*$/gm, '');
  // Table rows: outer pipes dropped, inner pipes become pauses.
  text = text.replace(/^[ \t]*\|(.*)\|[ \t]*$/gm, '$1');
  text = text.replace(/[ \t]*\|[ \t]*/g, ', ');
  // Blockquotes
  text = text.replace(/^[ \t]*(>[ \t]?)+/gm, '');
  // Bullet markers
  text = text.replace(/^[ \t]*[-*+][ \t]+/gm, '');
  // Headings / emphasis / strikethrough markers.
  // `*` and `_` count as emphasis only when they sit on a word boundary
  // and touch the word, so `file_name` and `2 * 3 * 4` stay intact.
  // The lazy `??` keeps `*a* and *b*` as two spans, not one.
  text = text.replace(/^#{1,6}\s+/gm, '');
  const boundary = '(?:^|[\\s([{"\'])';
  const end = '(?=$|[\\s)\\].,!?:;"\'])';
  text = text.replace(new RegExp(`(${boundary})(\\*\\*|__)(\\S(?:.*?\\S)??)\\2${end}`, 'gm'), '$1$3');
  text = text.replace(new RegExp(`(${boundary})(\\*|_)(\\S(?:.*?\\S)??)\\2${end}`, 'gm'), '$1$3');
  text = text.replace(/~~(.*?)~~/g, '$1');
  // Bare URLs are spoken as the word "link", with any trailing punctuation kept.
  text = text.replace(/https?:\/\/[^\s]+/g, (url) => `link${splitUrlTrailer(url)[1]}`);
  // Collapse whitespace
  return text.replace(/\s+/g, ' ').trim();
}

/** Rough spoken duration. The watchdog uses it as its time budget,
 * because `onend` does not always fire. */
// Chinese characters and Japanese kana/kanji. Each is about one syllable,
// and voices read them at roughly 4–8 per second.
const CJK_CHAR = /[぀-ヿ㐀-䶿一-鿿豈-﫿]/g;

function estimateDurationMs(plain: string, rate: number): number {
  // Chinese, Japanese and Thai have no spaces between words, so a space
  // count alone would treat a long reply as one word.
  // - Each CJK character counts as half a word (5.5 per second at
  //   165 wpm), so the 2x cap still covers speech at 2.75 per second.
  // - Other text counts as its words, or one word per 4 characters,
  //   whichever is more. This covers Thai.
  const cjk = (plain.match(CJK_CHAR) || []).length;
  const rest = plain.replace(CJK_CHAR, ' ');
  const spaced = rest.split(/\s+/).filter(Boolean).length;
  const restChars = rest.replace(/\s+/g, '').length;
  const words = Math.max(cjk * 0.5 + Math.max(spaced, Math.ceil(restChars / 4)), 1);
  const wpm = 165 * (rate > 0 ? rate : 1);
  return Math.max(800, (words / wpm) * 60 * 1000);
}

export type SpeechProgress = {
  speakId: string | null;
  speaking: boolean;
};

export type TtsProvider = 'browser' | 'deepgram';
export type SttProvider = 'browser' | 'deepgram';

type SpeakOptions = {
  /** Correlates progress with a chat message bubble. */
  speakId?: string;
  /** BCP-47 tag; falls back to the browser language. */
  lang?: string | null;
  onEnd?: () => void;
  /** Default ``browser``. ``deepgram`` needs ``fetchAudio``. */
  provider?: TtsProvider;
  /** POST plain text to the backend and return an audio blob (MP3). */
  fetchAudio?: (plain: string) => Promise<Blob>;
};

const WATCHDOG_INTERVAL_MS = 250;
/** Longest wait for a queued utterance that never starts. */
const MAX_START_WAIT_MS = 8000;

let activeRecognition: SpeechRecognitionLike | null = null;
let listening = false;
const listeningListeners = new Set<(on: boolean) => void>();
let activeUtterance: SpeechSynthesisUtterance | null = null;
let activeAudio: HTMLAudioElement | null = null;
let activeObjectUrl: string | null = null;
/** Bumped on every speak/stop so a slow Deepgram fetch can tell it was superseded. */
let speakGeneration = 0;
let watchdogTimer: number | null = null;
let progress: SpeechProgress = { speakId: null, speaking: false };

const SENTENCE_END = /[.!?।](?=\s|$)/;

/** Pull finished sentences out of a growing plain-text reply.
 * Short fragments stay buffered so "Hi." does not become its own clip.
 * A long stretch with no punctuation is cut early so speech can start. */
export function takeReadySentences(
  plain: string,
  from: number,
  final: boolean,
): { pieces: string[]; consumed: number } {
  const pieces: string[] = [];
  let start = Math.max(0, from);
  let i = start;

  const commit = (endExclusive: number) => {
    const piece = plain.slice(start, endExclusive).trim();
    if (piece) pieces.push(piece);
    let next = endExclusive;
    while (next < plain.length && /\s/.test(plain[next])) next += 1;
    start = next;
    i = next;
  };

  while (i < plain.length) {
    const rel = plain.slice(i).search(SENTENCE_END);
    if (rel === -1) break;
    const end = i + rel + 1;
    let next = end;
    while (next < plain.length && /\s/.test(plain[next])) next += 1;
    const piece = plain.slice(start, end).trim();
    const words = piece.split(/\s+/).filter(Boolean).length;
    const longEnough = piece.length >= 24 || words >= 5;
    if (!longEnough) {
      if (next >= plain.length) break;
      i = next;
      continue;
    }
    commit(end);
  }

  if (!final && plain.length - start >= 180) {
    const window = plain.slice(start, Math.min(plain.length, start + 160));
    let rel = window.lastIndexOf(',');
    if (rel < 40) rel = window.lastIndexOf(' ');
    if (rel >= 40) commit(start + rel);
  }

  if (final && start < plain.length) {
    const piece = plain.slice(start).trim();
    if (piece) pieces.push(piece);
    start = plain.length;
  }
  return { pieces, consumed: start };
}

type SpeechClip = {
  text: string;
  blobPromise: Promise<Blob | null> | null;
};

type SpeechStream = {
  generation: number;
  speakId: string | null;
  lang?: string | null;
  provider: TtsProvider;
  fetchAudio?: (plain: string) => Promise<Blob>;
  accepted: string;
  consumed: number;
  clips: SpeechClip[];
  nextPlay: number;
  closed: boolean;
  pumping: boolean;
  notify: (() => void) | null;
  cancelClip: (() => void) | null;
};

let speechStream: SpeechStream | null = null;
const progressListeners = new Set<(next: SpeechProgress) => void>();

function setProgress(next: SpeechProgress): void {
  progress = next;
  const snapshot = { ...progress };
  progressListeners.forEach((listener) => listener(snapshot));
}

function emitListening(next: boolean): void {
  if (listening === next) return;
  listening = next;
  listeningListeners.forEach((listener) => listener(next));
}

/** `speech.ts` owns whether the mic is on. Returns unsubscribe. */
export function subscribeListening(listener: (on: boolean) => void): () => void {
  listeningListeners.add(listener);
  listener(listening);
  return () => {
    listeningListeners.delete(listener);
  };
}

function stopWatchdog(): void {
  if (watchdogTimer !== null) {
    window.clearInterval(watchdogTimer);
    watchdogTimer = null;
  }
}

function clearSpeechProgress(): void {
  stopWatchdog();
  setProgress({ speakId: null, speaking: false });
}

/** Subscribe to which bubble is speaking. Returns unsubscribe. */
export function subscribeSpeechProgress(
  listener: (next: SpeechProgress) => void,
): () => void {
  progressListeners.add(listener);
  listener({ ...progress });
  return () => {
    progressListeners.delete(listener);
  };
}

export type ListenHandlers = {
  onInterim?: (transcript: string) => void;
  onFinal?: (transcript: string) => void;
  onEnd?: () => void;
  onError?: (error: string) => void;
  /** BCP-47 tag; falls back to the browser language. */
  lang?: string | null;
};

export function startListening(handlers: ListenHandlers): boolean {
  const Ctor = getSpeechRecognitionCtor();
  if (!Ctor) {
    handlers.onError?.('unsupported');
    return false;
  }

  stopListening();
  stopSpeaking();

  const recognition = new Ctor();
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.lang = resolveVoiceLang(handlers.lang);

  recognition.onresult = (event) => {
    let interim = '';
    let finalText = '';
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const result = event.results[i];
      const piece = result[0]?.transcript ?? '';
      if (result.isFinal) {
        finalText += piece;
      } else {
        interim += piece;
      }
    }
    if (interim) handlers.onInterim?.(interim);
    if (finalText.trim()) handlers.onFinal?.(finalText.trim());
  };

  recognition.onerror = (event) => {
    const code = event?.error || 'error';
    // "aborted" / "no-speech" are common benign ends — surface others.
    if (code !== 'aborted' && code !== 'no-speech') {
      handlers.onError?.(code);
    }
  };

  recognition.onend = () => {
    // A replaced recognizer also fires `onend`. Ignore it so a quick
    // stop/start cannot report "not listening" for the new session.
    if (activeRecognition !== recognition) return;
    activeRecognition = null;
    emitListening(false);
    handlers.onEnd?.();
  };

  try {
    activeRecognition = recognition;
    recognition.start();
    emitListening(true);
    return true;
  } catch {
    activeRecognition = null;
    emitListening(false);
    // A stable code, so the footer can show a message for it.
    handlers.onError?.('start-failed');
    return false;
  }
}

export function stopListening(): void {
  if (!activeRecognition) return;
  const rec = activeRecognition;
  activeRecognition = null;
  emitListening(false);
  try {
    rec.onresult = null;
    rec.onerror = null;
    rec.stop();
  } catch {
    try {
      rec.abort();
    } catch {
      // ignore
    }
  }
}

function cancelSynthesis(): void {
  try {
    window.speechSynthesis.cancel();
  } catch {
    // ignore
  }
}

function stopActiveAudio(): void {
  if (activeAudio) {
    try {
      activeAudio.onended = null;
      activeAudio.onerror = null;
      activeAudio.pause();
      activeAudio.removeAttribute('src');
      activeAudio.load();
    } catch {
      // ignore
    }
    activeAudio = null;
  }
  if (activeObjectUrl) {
    try {
      URL.revokeObjectURL(activeObjectUrl);
    } catch {
      // ignore
    }
    activeObjectUrl = null;
  }
}

export function stopSpeaking(): void {
  speakGeneration += 1;
  const current = speechStream;
  speechStream = null;
  current?.cancelClip?.();
  const hadUtterance = activeUtterance !== null;
  activeUtterance = null;
  stopWatchdog();
  stopActiveAudio();
  // Cancel only speech this widget started. The widget is mounted in the
  // host page, so an unconditional cancel would cut off the host's audio.
  if (hadUtterance && isSpeechSynthesisSupported()) {
    cancelSynthesis();
  }
  if (progress.speaking || progress.speakId) {
    clearSpeechProgress();
  }
}

/** Speak one utterance under the watchdog. `onDone` runs once: on end,
 * on error, or when the watchdog gives up. */
function queueUtterance(plain: string, lang: string | null | undefined, onDone: () => void): void {
  const utterance = new SpeechSynthesisUtterance(plain);
  utterance.lang = resolveVoiceLang(lang);
  activeUtterance = utterance;

  const durationMs = estimateDurationMs(plain, utterance.rate || 1);
  // Past this, end no matter what the browser reports. Covers Chrome's
  // stuck `speaking` after its ~15 s network-voice cutoff, speech left
  // paused, and speech that never starts.
  const hardCapMs = durationMs * 2 + 5000;
  const startWaitMs = Math.min(durationMs + 2000, MAX_START_WAIT_MS);
  let startedAt = 0;
  const queuedAt = performance.now();

  utterance.onstart = () => {
    if (activeUtterance !== utterance || startedAt) return;
    startedAt = performance.now();
  };

  const finish = () => {
    if (activeUtterance !== utterance) return;
    activeUtterance = null;
    stopWatchdog();
    onDone();
  };

  // Watchdog only: `onend` does not fire for a cancelled utterance, a
  // network-voice cutoff, or when voices are not loaded yet.
  stopWatchdog();
  watchdogTimer = window.setInterval(() => {
    if (activeUtterance !== utterance) return;
    const synthesis = window.speechSynthesis;
    const now = performance.now();
    if (now - queuedAt > hardCapMs) {
      cancelSynthesis();
      finish();
      return;
    }
    if (synthesis.paused) return;
    if (!startedAt) {
      if (now - queuedAt > startWaitMs && !synthesis.speaking && !synthesis.pending) {
        finish();
      }
      return;
    }
    if (now - startedAt > durationMs * 1.5 + 2000 && !synthesis.speaking) {
      finish();
    }
  }, WATCHDOG_INTERVAL_MS);

  utterance.onend = finish;
  utterance.onerror = finish;

  window.speechSynthesis.speak(utterance);
}

function speakBrowser(plain: string, options: SpeakOptions): boolean {
  if (!isSpeechSynthesisSupported()) return false;
  try {
    setProgress({ speakId: options.speakId ?? null, speaking: true });
    queueUtterance(plain, options.lang, () => {
      clearSpeechProgress();
      options.onEnd?.();
    });
    return true;
  } catch {
    activeUtterance = null;
    clearSpeechProgress();
    return false;
  }
}

async function speakDeepgram(plain: string, options: SpeakOptions): Promise<void> {
  const fetchAudio = options.fetchAudio;
  if (!fetchAudio) {
    speakBrowser(plain, options);
    return;
  }

  const generation = speakGeneration;
  const speakId = options.speakId ?? null;
  setProgress({ speakId, speaking: true });

  try {
    const blob = await fetchAudio(plain);
    // A newer speak/stop may have started while we awaited.
    if (generation !== speakGeneration) return;

    stopActiveAudio();
    const objectUrl = URL.createObjectURL(blob);
    activeObjectUrl = objectUrl;
    const audio = new Audio(objectUrl);
    activeAudio = audio;

    const finish = () => {
      if (activeAudio !== audio) return;
      stopActiveAudio();
      clearSpeechProgress();
      options.onEnd?.();
    };
    audio.onended = finish;
    audio.onerror = finish;

    await audio.play();
  } catch (err) {
    console.warn('Deepgram TTS failed; falling back to browser speech', err);
    if (generation === speakGeneration) {
      stopActiveAudio();
      if (!speakBrowser(plain, options)) clearSpeechProgress();
    }
  }
}

function wakeStream(s: SpeechStream): void {
  const notify = s.notify;
  s.notify = null;
  notify?.();
}

function ensureClipPrefetch(s: SpeechStream): void {
  if (s.provider !== 'deepgram' || !s.fetchAudio) return;
  const fetchAudio = s.fetchAudio;
  const last = Math.min(s.clips.length, s.nextPlay + 2);
  for (let i = s.nextPlay; i < last; i += 1) {
    const clip = s.clips[i];
    if (!clip.blobPromise) {
      clip.blobPromise = fetchAudio(clip.text).catch(() => null);
    }
  }
}

/** Resolves when the clip ends, fails, or the stream is cancelled. */
function playClip(s: SpeechStream, start: (done: () => void) => void): Promise<void> {
  return new Promise((resolve) => {
    let settled = false;
    const done = () => {
      if (settled) return;
      settled = true;
      if (s.cancelClip === done) s.cancelClip = null;
      resolve();
    };
    if (s.generation !== speakGeneration) {
      done();
      return;
    }
    s.cancelClip = done;
    try {
      start(done);
    } catch {
      done();
    }
  });
}

function playBrowserClip(s: SpeechStream, clip: SpeechClip): Promise<void> {
  if (!isSpeechSynthesisSupported()) return Promise.resolve();
  return playClip(s, (done) => queueUtterance(clip.text, s.lang, done));
}

function playBlobClip(s: SpeechStream, blob: Blob): Promise<void> {
  return playClip(s, (done) => {
    stopActiveAudio();
    const objectUrl = URL.createObjectURL(blob);
    activeObjectUrl = objectUrl;
    const audio = new Audio(objectUrl);
    activeAudio = audio;
    audio.onended = done;
    audio.onerror = done;
    audio.play().catch(done);
  });
}

async function pumpSpeechStream(s: SpeechStream): Promise<void> {
  if (s.pumping) return;
  s.pumping = true;
  try {
    while (s.generation === speakGeneration) {
      if (s.nextPlay >= s.clips.length) {
        if (s.closed) break;
        await new Promise<void>((resolve) => {
          s.notify = resolve;
        });
        continue;
      }
      const clip = s.clips[s.nextPlay];
      ensureClipPrefetch(s);
      try {
        if (s.provider === 'deepgram' && clip.blobPromise) {
          const blob = await clip.blobPromise;
          if (s.generation !== speakGeneration) return;
          if (blob) await playBlobClip(s, blob);
          else await playBrowserClip(s, clip);
        } else {
          await playBrowserClip(s, clip);
        }
      } catch {
        // Skip a clip that failed to play and keep the rest of the reply.
      }
      if (s.generation !== speakGeneration) return;
      s.nextPlay += 1;
      ensureClipPrefetch(s);
    }
  } finally {
    s.pumping = false;
    if (
      speechStream === s &&
      s.generation === speakGeneration &&
      s.closed &&
      s.nextPlay >= s.clips.length
    ) {
      speechStream = null;
      stopActiveAudio();
      clearSpeechProgress();
    }
  }
}

/** Start a sentence queue. The first finished sentence plays while later
 * ones are still being written. Deepgram clips are fetched one ahead. */
export function beginSpeechStream(options: SpeakOptions = {}): void {
  // The mic would otherwise transcribe the reply from the speakers.
  stopListening();
  stopSpeaking();
  const speakId = options.speakId ?? null;
  speechStream = {
    generation: speakGeneration,
    speakId,
    lang: options.lang,
    provider: options.provider === 'deepgram' ? 'deepgram' : 'browser',
    fetchAudio: options.fetchAudio,
    accepted: '',
    consumed: 0,
    clips: [],
    nextPlay: 0,
    closed: false,
    pumping: false,
    notify: null,
    cancelClip: null,
  };
  setProgress({ speakId, speaking: true });
}

/** Feed the full plain reply so far. Pass `final` when the answer is done
 * so the trailing fragment is spoken too. */
export function feedSpeechStream(fullPlain: string, final = false): void {
  const s = speechStream;
  if (!s || s.generation !== speakGeneration) return;
  const plain = (fullPlain || '').replace(/\s+/g, ' ').trim();
  // A rewritten prefix (e.g. a markdown construct resolved differently)
  // cannot be re-spoken; stop taking text and let the queue drain.
  if (!plain || (s.accepted && !plain.startsWith(s.accepted))) {
    if (final) {
      s.closed = true;
      wakeStream(s);
      void pumpSpeechStream(s);
    }
    return;
  }
  const { pieces, consumed } = takeReadySentences(plain, s.consumed, final);
  s.consumed = consumed;
  s.accepted = plain.slice(0, consumed);
  for (const piece of pieces) {
    s.clips.push({ text: piece, blobPromise: null });
  }
  ensureClipPrefetch(s);
  if (final) s.closed = true;
  wakeStream(s);
  void pumpSpeechStream(s);
}

export function speak(text: string, options: SpeakOptions = {}): boolean {
  const provider = options.provider === 'deepgram' ? 'deepgram' : 'browser';
  if (!canSpeak(provider)) return false;
  const plain = plainTextForSpeech(text);
  if (!plain) return false;

  // The mic would otherwise transcribe the reply from the speakers and
  // send it as the visitor's next message.
  stopListening();
  stopSpeaking();
  if (provider === 'deepgram') {
    void speakDeepgram(plain, options);
    return true;
  }
  return speakBrowser(plain, options);
}

export function isSpeaking(): boolean {
  if (activeAudio && !activeAudio.paused) return true;
  if (isSpeechSynthesisSupported() && window.speechSynthesis.speaking) return true;
  return progress.speaking;
}

/** True when any TTS path can run (browser synthesis and/or Deepgram fetch). */
export function canSpeak(provider: TtsProvider = 'browser'): boolean {
  if (provider === 'deepgram') return typeof Audio !== 'undefined';
  return isSpeechSynthesisSupported();
}

/** Stop mic + TTS — use when the user leaves the chat surface. */
export function silenceVoice(): void {
  stopListening();
  stopSpeaking();
}
