// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { clearToken, getToken, saveToken } from './tokenStorage';

const STORAGE_KEY = 'aurel_admin_token';

afterEach(() => {
  sessionStorage.clear();
  localStorage.clear();
});

describe('tokenStorage', () => {
  it('returns null before anything is saved', () => {
    expect(getToken()).toBeNull();
  });

  it('save then get roundtrips the token', () => {
    saveToken('a.jwt.token');
    expect(getToken()).toBe('a.jwt.token');
  });

  it('trims external whitespace before saving', () => {
    saveToken('  a.jwt.token  ');
    expect(getToken()).toBe('a.jwt.token');
    expect(sessionStorage.getItem(STORAGE_KEY)).toBe('a.jwt.token');
  });

  it('rejects an empty string — nothing is saved', () => {
    saveToken('');
    expect(getToken()).toBeNull();
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it('rejects a whitespace-only string — nothing is saved', () => {
    saveToken('   ');
    expect(getToken()).toBeNull();
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it('a runtime null value is not turned into the string "null"', () => {
    saveToken(null as unknown as string);
    expect(getToken()).toBeNull();
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it('a runtime undefined value is not turned into the string "undefined"', () => {
    saveToken(undefined as unknown as string);
    expect(getToken()).toBeNull();
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it('a non-string runtime value is rejected', () => {
    saveToken(12345 as unknown as string);
    expect(getToken()).toBeNull();
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it('a whitespace-only value already in storage is cleaned up by getToken()', () => {
    sessionStorage.setItem(STORAGE_KEY, '   ');
    expect(getToken()).toBeNull();
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it('clear removes the token', () => {
    saveToken('a.jwt.token');
    clearToken();
    expect(getToken()).toBeNull();
  });

  it('clear on an already-empty store is a harmless no-op', () => {
    expect(() => clearToken()).not.toThrow();
    expect(getToken()).toBeNull();
  });

  it('never writes the token to localStorage', () => {
    saveToken('a.jwt.token');
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it.each<[string, unknown]>([
    ['null', null],
    ['undefined', undefined],
    ['an empty string', ''],
    ['a whitespace-only string', '   '],
    ['a number', 12345],
    ['an object', { not: 'a string' }],
  ])(
    'a previously-saved valid token is cleared when saveToken() is called with %s',
    (_label, invalidValue) => {
      saveToken('a.jwt.token');
      expect(getToken()).toBe('a.jwt.token');

      saveToken(invalidValue as unknown as string);

      expect(getToken()).toBeNull();
      expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
    },
  );
});
