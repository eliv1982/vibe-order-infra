/**
 * Idempotency-Key generation for POST /applications (Stage 1B correction —
 * see backend/app/routes/applications.py's `_IDEMPOTENCY_KEY_RE`).
 *
 * The backend only enforces a minimum length/charset — it can't distinguish
 * a truly random client-supplied key from a short-but-format-valid,
 * low-entropy one, which is exactly why a successful replay is never
 * treated as authorization to mint or recover a behavior-metrics capability
 * (see backend/app/crud/application.py::create_application_idempotent's
 * module-level design note): the Idempotency-Key is not a secret, only a
 * de-duplication handle. crypto.randomUUID() (122 bits, 36 characters)
 * used to back this; the backend's 43-character floor (the length
 * secrets.token_urlsafe(32), a 256-bit token, always produces) exists to
 * keep unrelated legitimate keys from colliding with each other, not to
 * make this value hard to guess. This mirrors that server-side, byte for
 * byte: 32 cryptographically random bytes, base64url-encoded without
 * padding.
 */
export function generateIdempotencyKey(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  let binary = '';
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
