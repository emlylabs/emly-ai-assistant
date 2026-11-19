/**
 * Browser Web Speech API helpers for the embeddable widget.
 *
 * Voice is processed by the visitor's browser vendor (e.g. Google for
 * Chrome, Microsoft for Edge). Audio does not pass through Emly servers.
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

/** Strip markdown / URLs so TTS does not read markup aloud. */
export function plainTextForSpeech(input: string): string {
  let text = input ?? '';
  // Fenced code blocks
  text = text.replace(/```[\s\S]*?```/g, ' ');
  // Inline code
  text = text.replace(/`([^`]+)`/g, '$1');
  // Images ![alt](url) → alt
  text = text.replace(/!\[([^\]]*)\]\([^)]+\)/g, '$1');
  // Links [label](url) → label
  text = text.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1');
  // Headings / emphasis / strikethrough markers.
  // `*` and `_` count as emphasis only when they sit on a word boundary
  // and touch the word, so `file_name` and `2 * 3 * 4` stay intact.
  text = text.replace(/^#{1,6}\s+/gm, '');
  const boundary = '(?:^|[\\s([{"\'])';
  const end = '(?=$|[\\s)\\].,!?:;"\'])';
  text = text.replace(new RegExp(`(${boundary})(\\*\\*|__)(\\S(?:.*?\\S)?)\\2${end}`, 'g'), '$1$3');
  text = text.replace(new RegExp(`(${boundary})(\\*|_)(\\S(?:.*?\\S)?)\\2${end}`, 'g'), '$1$3');
  text = text.replace(/~~(.*?)~~/g, '$1');
  // Bare URLs are spoken as the word "link", with any trailing punctuation kept.
  text = text.replace(/https?:\/\/[^\s]+/g, (url) => {
    const core = url.replace(/[.,!?:;)]+$/g, '');
    return `link${url.slice(core.length)}`;
  });
  // Collapse whitespace
  return text.replace(/\s+/g, ' ').trim();
}

type WordSpan = { start: number; end: number };

function buildWordSpans(plain: string): WordSpan[] {
  const spans: WordSpan[] = [];
  const re = /\S+/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(plain)) !== null) {
    spans.push({ start: match.index, end: match.index + match[0].length });
  }
  return spans;
}

/** Rough spoken duration — Chrome often skips `onboundary`, so we
 * drive the highlight from elapsed time with this estimate. */
function estimateDurationMs(plain: string, rate: number): number {
  const words = plain.split(/\s+/).filter(Boolean).length || 1;
  const wpm = 165 * (rate > 0 ? rate : 1);
  return Math.max(800, (words / wpm) * 60 * 1000);
}

export function wordIndexForChar(spans: WordSpan[], charIndex: number): number {
  if (spans.length === 0) return 0;
  for (let i = 0; i < spans.length; i += 1) {
    if (charIndex >= spans[i].start && charIndex < spans[i].end) return i;
  }
  for (let i = spans.length - 1; i >= 0; i -= 1) {
    if (charIndex >= spans[i].start) return i;
  }
  return 0;
}

export type SpeechProgress = {
  speakId: string | null;
  plainText: string;
  charIndex: number;
  charLength: number;
  speaking: boolean;
};

type SpeakOptions = {
  /** Correlates progress with a chat message bubble. */
  speakId?: string;
  onEnd?: () => void;
};

let activeRecognition: SpeechRecognitionLike | null = null;
let listening = false;
const listeningListeners = new Set<(on: boolean) => void>();
let activeUtterance: SpeechSynthesisUtterance | null = null;
let progressTimer: number | null = null;
let progress: SpeechProgress = {
  speakId: null,
  plainText: '',
  charIndex: 0,
  charLength: 0,
  speaking: false,
};
const progressListeners = new Set<(next: SpeechProgress) => void>();

function emitProgress(): void {
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

function stopProgressTimer(): void {
  if (progressTimer !== null) {
    window.clearInterval(progressTimer);
    progressTimer = null;
  }
}

function clearSpeechProgress(): void {
  stopProgressTimer();
  progress = {
    speakId: null,
    plainText: '',
    charIndex: 0,
    charLength: 0,
    speaking: false,
  };
  emitProgress();
}

function applyWordHighlight(
  speakId: string | null,
  plain: string,
  span: WordSpan,
): void {
  const nextIndex = span.start;
  const nextLength = Math.max(1, span.end - span.start);
  if (
    progress.speaking &&
    progress.speakId === speakId &&
    progress.charIndex === nextIndex &&
    progress.charLength === nextLength
  ) {
    return;
  }
  progress = {
    speakId,
    plainText: plain,
    charIndex: nextIndex,
    charLength: nextLength,
    speaking: true,
  };
  emitProgress();
}

/** Subscribe to TTS progress (word highlight). Returns unsubscribe. */
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
  lang?: string;
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
  recognition.lang = handlers.lang || (typeof navigator !== 'undefined' ? navigator.language : 'en-US');

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
  } catch (err) {
    activeRecognition = null;
    emitListening(false);
    handlers.onError?.(err instanceof Error ? err.message : 'start-failed');
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

export function stopSpeaking(): void {
  const hadUtterance = activeUtterance !== null;
  activeUtterance = null;
  stopProgressTimer();
  // Cancel only speech this widget started. The widget is mounted in the
  // host page, so an unconditional cancel would cut off the host's audio.
  if (hadUtterance && isSpeechSynthesisSupported()) {
    try {
      window.speechSynthesis.cancel();
    } catch {
      // ignore
    }
  }
  if (progress.speaking || progress.speakId) {
    clearSpeechProgress();
  }
}

export function speak(text: string, options: SpeakOptions = {}): boolean {
  if (!isSpeechSynthesisSupported()) return false;
  const plain = plainTextForSpeech(text);
  if (!plain) return false;

  // The mic would otherwise transcribe the reply from the speakers and
  // send it as the visitor's next message.
  stopListening();
  stopSpeaking();
  try {
    const utterance = new SpeechSynthesisUtterance(plain);
    utterance.lang = typeof navigator !== 'undefined' ? navigator.language : 'en-US';
    activeUtterance = utterance;

    const speakId = options.speakId ?? null;
    const words = buildWordSpans(plain);
    const durationMs = estimateDurationMs(plain, utterance.rate || 1);
    // Stay at 0 until `onstart`. Highlighting from speak() runs ahead of
    // the voice, and Chrome often never fires `onend` if we cancel first.
    let startedAt = 0;
    const queuedAt = performance.now();

    progress = {
      speakId,
      plainText: plain,
      charIndex: 0,
      charLength: 0,
      speaking: true,
    };
    emitProgress();

    const markStarted = () => {
      if (activeUtterance !== utterance || startedAt) return;
      startedAt = performance.now();
    };

    utterance.onstart = () => {
      markStarted();
    };

    // Chrome/Edge on many Linux setups fire `onboundary` once (or never).
    // Drive karaoke highlight from elapsed time; snap the clock when a
    // real boundary arrives so it stays aligned with the voice.
    utterance.onboundary = (event: SpeechSynthesisEvent) => {
      if (activeUtterance !== utterance || words.length === 0) return;
      if (event.name && event.name !== 'word' && event.name !== 'sentence') return;
      const charIndex = typeof event.charIndex === 'number' ? event.charIndex : 0;
      const wi = wordIndexForChar(words, charIndex);
      const span = words[wi];
      if (!startedAt) {
        const targetT = (wi + 0.5) / words.length;
        startedAt = performance.now() - targetT * durationMs;
      }
      applyWordHighlight(speakId, plain, span);
    };

    const finish = () => {
      if (activeUtterance !== utterance) return;
      activeUtterance = null;
      clearSpeechProgress();
      options.onEnd?.();
    };

    progressTimer = window.setInterval(() => {
      if (activeUtterance !== utterance || words.length === 0) return;
      if (typeof window !== 'undefined' && window.speechSynthesis.paused) return;
      const synthesis = window.speechSynthesis;
      if (!startedAt) {
        const waited = performance.now() - queuedAt;
        if (waited > durationMs + 2000 && !synthesis.speaking && !synthesis.pending) {
          finish();
        }
        return;
      }
      const elapsed = performance.now() - startedAt;
      // `onend` does not fire for a cancelled utterance, a network-voice
      // cutoff, or when voices are not loaded yet. Stop the highlight then.
      if (elapsed > durationMs * 1.5 + 2000 && !synthesis.speaking) {
        finish();
        return;
      }
      const t = Math.min(0.999, Math.max(0, elapsed / durationMs));
      const wi = Math.min(words.length - 1, Math.floor(t * words.length));
      applyWordHighlight(speakId, plain, words[wi]);
    }, 50);

    utterance.onend = finish;
    utterance.onerror = finish;

    window.speechSynthesis.speak(utterance);
    return true;
  } catch {
    activeUtterance = null;
    clearSpeechProgress();
    return false;
  }
}

export function isSpeaking(): boolean {
  if (!isSpeechSynthesisSupported()) return false;
  return window.speechSynthesis.speaking || progress.speaking;
}

/** Stop mic + TTS — use when the user leaves the chat surface. */
export function silenceVoice(): void {
  stopListening();
  stopSpeaking();
}
