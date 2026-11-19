import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  micErrorMessage,
  plainTextForSpeech,
  resolveVoiceLang,
  speak,
  startListening,
  stopListening,
  stopSpeaking,
  subscribeListening,
  subscribeSpeechProgress,
  type SpeechProgress,
} from './speech';

describe('plainTextForSpeech', () => {
  it('keeps underscores inside words', () => {
    expect(plainTextForSpeech('Set `user_id` and file_name')).toBe('Set user_id and file_name');
  });

  it('keeps spaced asterisks used as multiplication', () => {
    expect(plainTextForSpeech('2 * 3 * 4')).toBe('2 * 3 * 4');
  });

  it('speaks a bare URL as the word link', () => {
    expect(plainTextForSpeech('See https://example.com/leave for details.')).toBe(
      'See link for details.',
    );
  });

  it('still strips emphasis that touches the word', () => {
    expect(plainTextForSpeech('Say *hello* and **bold** now')).toBe('Say hello and bold now');
    expect(plainTextForSpeech('_italic_')).toBe('italic');
  });

  it('keeps single-character emphasis spans separate', () => {
    expect(plainTextForSpeech('*a* and *b*')).toBe('a and b');
    expect(plainTextForSpeech('**a** and **b**')).toBe('a and b');
    expect(plainTextForSpeech('_a_ and _b_')).toBe('a and b');
  });

  it('handles parentheses inside link and bare URLs', () => {
    expect(plainTextForSpeech('[portal](https://x.y/a_(b))')).toBe('portal');
    expect(plainTextForSpeech('See [portal](https://x.y/a_(b)).')).toBe('See portal.');
    expect(plainTextForSpeech('https://x.y/a_(b).')).toBe('link.');
    expect(plainTextForSpeech('(see https://x.y/a)')).toBe('(see link)');
  });

  it('drops tables, lists, quotes, rules, HTML and footnotes', () => {
    const md = [
      '> Note this',
      '',
      '| Form | Fee |',
      '| --- | :---: |',
      '| I-90 | $465 |',
      '',
      '---',
      '- first',
      '* second',
      '+ third',
      'Line<br>break with <b>bold</b>[^1]',
      '',
      '[^1]: The source.',
    ].join('\n');
    const out = plainTextForSpeech(md);
    expect(out).toBe(
      'Note this Form, Fee I-90, $465 first second third Line break with bold The source.',
    );
    expect(out).not.toMatch(/[|*>#<]|---|\[\^/);
  });

  it('keeps ordered list numbers', () => {
    expect(plainTextForSpeech('1. Bring ID\n2. Sign')).toBe('1. Bring ID 2. Sign');
  });
});

describe('micErrorMessage', () => {
  it('maps known codes to a message', () => {
    expect(micErrorMessage('not-allowed')).toBe('Microphone permission was blocked.');
    expect(micErrorMessage('service-not-allowed')).toBe(
      'Voice input is turned off or blocked in this browser.',
    );
    expect(micErrorMessage('audio-capture')).toBe('No microphone was found.');
    expect(micErrorMessage('network')).toBe('Voice input needs a network connection.');
    expect(micErrorMessage('language-not-supported')).toBe(
      "Voice input doesn't support this language.",
    );
  });

  it('gives a generic message for start failures and unknown codes', () => {
    const generic = "Voice input couldn't start. Please type your message.";
    expect(micErrorMessage('start-failed')).toBe(generic);
    expect(micErrorMessage('unsupported')).toBe(generic);
    expect(micErrorMessage('something-new')).toBe(generic);
  });

  it('stays quiet for benign ends', () => {
    expect(micErrorMessage('aborted')).toBeNull();
    expect(micErrorMessage('no-speech')).toBeNull();
  });
});

describe('resolveVoiceLang', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('prefers the bot language, then the browser, then en-US', () => {
    vi.stubGlobal('navigator', { language: 'fr-FR' });
    expect(resolveVoiceLang('es-MX')).toBe('es-MX');
    expect(resolveVoiceLang('  ')).toBe('fr-FR');
    expect(resolveVoiceLang(null)).toBe('fr-FR');
    vi.stubGlobal('navigator', { language: '' });
    expect(resolveVoiceLang(undefined)).toBe('en-US');
  });
});

// ---- Browser fakes -------------------------------------------------------

class FakeUtterance {
  text: string;
  lang = '';
  rate = 1;
  onstart: (() => void) | null = null;
  onend: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(text: string) {
    this.text = text;
  }
}

type FakeSynthesis = {
  speaking: boolean;
  paused: boolean;
  pending: boolean;
  speak: ReturnType<typeof vi.fn>;
  cancel: ReturnType<typeof vi.fn>;
};

class FakeRecognition {
  static instances: FakeRecognition[] = [];
  static throwOnStart = false;
  continuous = false;
  interimResults = false;
  lang = '';
  onresult: unknown = null;
  onerror: ((event: { error?: string }) => void) | null = null;
  onend: (() => void) | null = null;
  start = vi.fn(() => {
    if (FakeRecognition.throwOnStart) throw new Error('InvalidStateError: already started');
  });
  stop = vi.fn();
  abort = vi.fn();
  constructor() {
    FakeRecognition.instances.push(this);
  }
}

let synthesis: FakeSynthesis;

function installBrowser(): void {
  synthesis = {
    speaking: false,
    paused: false,
    pending: false,
    speak: vi.fn(),
    cancel: vi.fn(),
  };
  vi.stubGlobal('window', {
    speechSynthesis: synthesis,
    SpeechRecognition: FakeRecognition,
    // Wrappers so the faked timers are looked up at call time.
    setInterval: (fn: () => void, ms: number) => setInterval(fn, ms),
    clearInterval: (id: ReturnType<typeof setInterval>) => clearInterval(id),
  });
  vi.stubGlobal('SpeechSynthesisUtterance', FakeUtterance);
  vi.stubGlobal('navigator', { language: 'en-GB' });
}

function lastUtterance(): FakeUtterance {
  const calls = synthesis.speak.mock.calls;
  return calls[calls.length - 1][0] as FakeUtterance;
}

describe('speak watchdog', () => {
  // "hello world" → 2 words → 800 ms budget. Hard cap = 800 * 2 + 5000.
  const HARD_CAP_MS = 6600;
  let updates: SpeechProgress[];
  let unsubscribe: () => void;

  beforeEach(() => {
    vi.useFakeTimers({
      toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout', 'performance'],
    });
    installBrowser();
    updates = [];
    unsubscribe = subscribeSpeechProgress((next) => updates.push(next));
  });

  afterEach(() => {
    stopSpeaking();
    unsubscribe();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  const latest = () => updates[updates.length - 1];

  it('marks the bubble as speaking and uses the configured language', () => {
    expect(speak('hello world', { speakId: 'm1', lang: 'es-MX' })).toBe(true);
    expect(latest()).toEqual({ speakId: 'm1', speaking: true });
    expect(lastUtterance().lang).toBe('es-MX');
  });

  it('falls back to the browser language', () => {
    speak('hello world', { speakId: 'm1' });
    expect(lastUtterance().lang).toBe('en-GB');
  });

  it('finishes on a normal onend', () => {
    const onEnd = vi.fn();
    speak('hello world', { speakId: 'm1', onEnd });
    lastUtterance().onstart?.();
    lastUtterance().onend?.();
    expect(onEnd).toHaveBeenCalledTimes(1);
    expect(latest()).toEqual({ speakId: null, speaking: false });
    expect(vi.getTimerCount()).toBe(0);
  });

  it('ends when Chrome leaves speaking stuck on true', () => {
    const onEnd = vi.fn();
    speak('hello world', { speakId: 'm1', onEnd });
    synthesis.speaking = true;
    lastUtterance().onstart?.();

    vi.advanceTimersByTime(HARD_CAP_MS - 500);
    expect(onEnd).not.toHaveBeenCalled();
    expect(latest().speaking).toBe(true);

    vi.advanceTimersByTime(1000);
    expect(synthesis.cancel).toHaveBeenCalled();
    expect(onEnd).toHaveBeenCalledTimes(1);
    expect(latest()).toEqual({ speakId: null, speaking: false });
    expect(vi.getTimerCount()).toBe(0);
  });

  it('ends when speech stays paused', () => {
    const onEnd = vi.fn();
    speak('hello world', { speakId: 'm1', onEnd });
    synthesis.speaking = true;
    synthesis.paused = true;
    lastUtterance().onstart?.();

    vi.advanceTimersByTime(HARD_CAP_MS + 500);
    expect(synthesis.cancel).toHaveBeenCalled();
    expect(onEnd).toHaveBeenCalledTimes(1);
    expect(latest().speaking).toBe(false);
  });

  it('ends when speech never starts but stays pending', () => {
    const onEnd = vi.fn();
    speak('hello world', { speakId: 'm1', onEnd });
    synthesis.pending = true;

    vi.advanceTimersByTime(HARD_CAP_MS + 500);
    expect(synthesis.cancel).toHaveBeenCalled();
    expect(onEnd).toHaveBeenCalledTimes(1);
    expect(latest().speaking).toBe(false);
  });

  it('ends quickly when speech never starts and nothing is queued', () => {
    const onEnd = vi.fn();
    speak('hello world', { speakId: 'm1', onEnd });

    vi.advanceTimersByTime(3000);
    expect(onEnd).toHaveBeenCalledTimes(1);
    expect(latest().speaking).toBe(false);
  });

  it('caps the start wait at 8 s for a long reply', () => {
    const onEnd = vi.fn();
    speak('word '.repeat(500), { speakId: 'm1', onEnd });

    vi.advanceTimersByTime(7500);
    expect(onEnd).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1000);
    expect(onEnd).toHaveBeenCalledTimes(1);
  });

  it('stopSpeaking cancels and clears the bubble', () => {
    speak('hello world', { speakId: 'm1' });
    stopSpeaking();
    expect(synthesis.cancel).toHaveBeenCalled();
    expect(latest()).toEqual({ speakId: null, speaking: false });
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe('listening state', () => {
  let states: boolean[];
  let unsubscribe: () => void;

  beforeEach(() => {
    installBrowser();
    FakeRecognition.instances = [];
    FakeRecognition.throwOnStart = false;
    states = [];
    unsubscribe = subscribeListening((on) => states.push(on));
  });

  afterEach(() => {
    stopListening();
    unsubscribe();
    vi.unstubAllGlobals();
  });

  it('turns on when started and off when the recognizer ends', () => {
    const onEnd = vi.fn();
    expect(startListening({ onEnd })).toBe(true);
    expect(states).toEqual([false, true]);

    FakeRecognition.instances[0].onend?.();
    expect(states).toEqual([false, true, false]);
    expect(onEnd).toHaveBeenCalledTimes(1);
  });

  it('ignores a replaced recognizer ending late', () => {
    startListening({});
    const first = FakeRecognition.instances[0];
    startListening({});
    expect(states[states.length - 1]).toBe(true);

    first.onend?.();
    expect(states[states.length - 1]).toBe(true);

    FakeRecognition.instances[1].onend?.();
    expect(states[states.length - 1]).toBe(false);
  });

  it('turns off on stopListening', () => {
    startListening({});
    stopListening();
    expect(states).toEqual([false, true, false]);
    expect(FakeRecognition.instances[0].stop).toHaveBeenCalled();
  });

  it('reports start-failed when start() throws', () => {
    FakeRecognition.throwOnStart = true;
    const onError = vi.fn();
    expect(startListening({ onError })).toBe(false);
    expect(onError).toHaveBeenCalledWith('start-failed');
    expect(states[states.length - 1]).toBe(false);
  });

  it('surfaces real errors but not benign ones', () => {
    const onError = vi.fn();
    startListening({ onError });
    const rec = FakeRecognition.instances[0];
    rec.onerror?.({ error: 'no-speech' });
    rec.onerror?.({ error: 'aborted' });
    rec.onerror?.({ error: 'service-not-allowed' });
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenCalledWith('service-not-allowed');
  });

  it('uses the bot language, else the browser language', () => {
    startListening({ lang: 'es-MX' });
    expect(FakeRecognition.instances[0].lang).toBe('es-MX');
    startListening({});
    expect(FakeRecognition.instances[1].lang).toBe('en-GB');
  });
});
