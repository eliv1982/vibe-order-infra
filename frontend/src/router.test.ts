import { describe, expect, it } from 'vitest';
import { resolveRoute } from './router';

describe('resolveRoute', () => {
  it('resolves the root path to home', () => {
    expect(resolveRoute('/')).toBe('home');
  });

  it('resolves /admin to admin', () => {
    expect(resolveRoute('/admin')).toBe('admin');
  });

  it('strips a trailing slash before matching /admin', () => {
    expect(resolveRoute('/admin/')).toBe('admin');
  });

  it.each(['/api', '/api/', '/api/applications', '/api/admin-settings/active', '/docs', '/docs/oauth2-redirect', '/openapi.json', '/redoc'])(
    'never disguises backend/technical path %s as the homepage',
    (pathname) => {
      expect(resolveRoute(pathname)).toBe('not-found');
    },
  );

  it('falls back to not-found for an unrecognized frontend path instead of silently rendering home', () => {
    expect(resolveRoute('/some-typo')).toBe('not-found');
  });
});
