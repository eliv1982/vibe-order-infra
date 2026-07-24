/**
 * Admin JWT storage — sessionStorage only.
 *
 * Chosen over localStorage so a forgotten/left-open browser tab doesn't keep
 * the admin authenticated forever (sessionStorage clears when the tab/browser
 * closes); chosen over in-memory-only so a page reload during a normal
 * editing session doesn't force a re-login. There is no httpOnly-cookie
 * option this stage — the backend returns the token in JSON, not a cookie.
 */

const STORAGE_KEY = 'aurel_admin_token';

/** Only a non-empty (post-trim) string is a usable token — guards against a
 * runtime-invalid caller turning into the literal "null"/"undefined", and
 * against a stray empty/whitespace value ever reaching an Authorization
 * header. */
function normalizeToken(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

export function getToken(): string | null {
  const raw = sessionStorage.getItem(STORAGE_KEY);
  if (raw === null) return null;

  const normalized = normalizeToken(raw);
  if (normalized === null) {
    sessionStorage.removeItem(STORAGE_KEY);
    return null;
  }
  return normalized;
}

export function saveToken(token: string): void {
  const normalized = normalizeToken(token);
  if (normalized === null) {
    // A runtime-invalid value must not leave a previously-saved valid token
    // sitting in storage — treat it the same as an explicit logout.
    clearToken();
    return;
  }
  sessionStorage.setItem(STORAGE_KEY, normalized);
}

export function clearToken(): void {
  sessionStorage.removeItem(STORAGE_KEY);
}
