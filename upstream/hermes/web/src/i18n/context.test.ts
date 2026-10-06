import { describe, expect, it } from 'vitest';
import { initialLocale } from './context';

describe('initial locale', () => {
  it('uses the document default without overwriting a saved preference', () => {
    expect(initialLocale(null, 'ko')).toBe('ko');
    expect(initialLocale('en', 'ko')).toBe('en');
    expect(initialLocale('unknown', 'ko')).toBe('ko');
    expect(initialLocale(null, 'unknown')).toBe('en');
  });
});
