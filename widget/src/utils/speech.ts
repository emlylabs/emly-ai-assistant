/**
 * Browser Web Speech API helpers for the embeddable widget.
 *
 * STT (SpeechRecognition / webkitSpeechRecognition) and TTS
 * (speechSynthesis) stay entirely in the visitor's browser — nothing
 * is uploaded. Mic access needs a secure context (HTTPS or localhost).
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
  // Headings / emphasis / strikethrough markers
  text = text.replace(/^#{1,6}\s+/gm, '');
  text = text.replace(/(\*\*|__)(.*?)\1/g, '$2');
  text = text.replace(/(\*|_)(.*?)\1/g, '$2');
  text = text.replace(/~~(.*?)~~/g, '$1');
  // Bare URLs
  text = text.replace(/https?:\/\/\S+/g, ' ');
  // Collapse whitespace
  return text.replace(/\s+/g, ' ').trim();
}

function wordLengthAt(plain: string, charIndex: number): number {
  if (charIndex < 0 || charIndex >= plain.length) return 0;
  const rest = plain.slice(charIndex);
  const match = rest.match(/^\S+/);
  return match ? match[0].length : 1;
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
let activeUtterance: SpeechSynthesisUtterance | null = null;
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

function clearSpeechProgress(): void {
  progress = {
    speakId: null,
    plainText: '',
    charIndex: 0,
    charLength: 0,
    speaking: false,
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
    if (activeRecognition === recognition) {
      activeRecognition = null;
    }
    handlers.onEnd?.();
  };

  try {
    activeRecognition = recognition;
    recognition.start();
    return true;
  } catch (err) {
    activeRecognition = null;
    handlers.onError?.(err instanceof Error ? err.message : 'start-failed');
    return false;
  }
}

export function stopListening(): void {
  if (!activeRecognition) return;
  const rec = activeRecognition;
  activeRecognition = null;
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

export function isListening(): boolean {
  return activeRecognition !== null;
}

export function stopSpeaking(): void {
  activeUtterance = null;
  if (isSpeechSynthesisSupported()) {
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

  stopSpeaking();
  try {
    const utterance = new SpeechSynthesisUtterance(plain);
    utterance.lang = typeof navigator !== 'undefined' ? navigator.language : 'en-US';
    activeUtterance = utterance;

    progress = {
      speakId: options.speakId ?? null,
      plainText: plain,
      charIndex: 0,
      charLength: wordLengthAt(plain, 0),
      speaking: true,
    };
    emitProgress();

    utterance.onboundary = (event: SpeechSynthesisEvent) => {
      if (activeUtterance !== utterance) return;
      // Prefer word boundaries; some engines only fire sentence.
      if (event.name && event.name !== 'word' && event.name !== 'sentence') return;
      const charIndex = typeof event.charIndex === 'number' ? event.charIndex : 0;
      const reported =
        typeof (event as SpeechSynthesisEvent & { charLength?: number }).charLength === 'number'
          ? (event as SpeechSynthesisEvent & { charLength?: number }).charLength!
          : 0;
      const charLength = reported > 0 ? reported : wordLengthAt(plain, charIndex);
      progress = {
        ...progress,
        charIndex,
        charLength,
        speaking: true,
      };
      emitProgress();
    };

    const finish = () => {
      if (activeUtterance !== utterance) return;
      activeUtterance = null;
      clearSpeechProgress();
      options.onEnd?.();
    };

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
