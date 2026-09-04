import { describe, expect, it } from 'vitest';
import { generateIdempotencyKey } from './idempotencyKey';

// Must match backend/app/routes/applications.py's _IDEMPOTENCY_KEY_RE
// exactly: [A-Za-z0-9_-]{43,128}.
const BACKEND_CONTRACT_RE = /^[A-Za-z0-9_-]{43,128}$/;

describe('generateIdempotencyKey', () => {
  it('matches the backend charset/length contract', () => {
    expect(generateIdempotencyKey()).toMatch(BACKEND_CONTRACT_RE);
  });

  it('produces exactly 43 characters (32 random bytes, base64url, unpadded)', () => {
    expect(generateIdempotencyKey()).toHaveLength(43);
  });

  it('never contains base64 padding or the standard (non-URL-safe) alphabet characters', () => {
    const key = generateIdempotencyKey();
    expect(key).not.toContain('=');
    expect(key).not.toContain('+');
    expect(key).not.toContain('/');
  });

  it('generates distinct keys across calls', () => {
    const keys = new Set(Array.from({ length: 20 }, () => generateIdempotencyKey()));
    expect(keys.size).toBe(20);
  });
});
