import { describe, expect, it } from 'vitest';
import { plainTextForSpeech, wordIndexForChar } from './speech';

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
});

describe('wordIndexForChar', () => {
  const spans = [
    { start: 0, end: 3 },
    { start: 4, end: 7 },
  ];

  it('returns the word that contains the character', () => {
    expect(wordIndexForChar(spans, 0)).toBe(0);
    expect(wordIndexForChar(spans, 5)).toBe(1);
  });

  it('uses the latest word at or before a character past the end', () => {
    expect(wordIndexForChar(spans, 100)).toBe(1);
  });

  it('returns 0 when there are no words', () => {
    expect(wordIndexForChar([], 3)).toBe(0);
  });
});
