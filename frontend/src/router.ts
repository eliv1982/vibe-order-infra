/**
 * Minimal pathname-based SPA router — no external routing library.
 *
 * Distinguishes "/" (home) from "/admin". Nginx serves the built dist/ with
 * a SPA-fallback rule (try_files ... /index.html, see
 * nginx/conf.d/vibe.elivcloud.org.conf) so "/admin" also works on a hard
 * refresh — this router assumes that fallback exists but does not
 * configure it (Nginx is out of scope here).
 *
 * Backend/technical paths (/api/*, /docs, /openapi.json, /redoc) must
 * never be mistaken for the homepage — if the router is ever asked to
 * resolve one of these (e.g. a stray client-side navigate() call, or a
 * misconfigured static fallback serving index.html for them), it resolves
 * to 'not-found' rather than silently rendering the home page under a
 * backend URL. Any other unrecognized frontend path also falls through to
 * 'not-found'.
 */

export type Route = 'home' | 'admin' | 'not-found';

type RouteChangeHandler = (route: Route) => void;

function isBackendPath(pathname: string): boolean {
  return (
    pathname === '/api' ||
    pathname.startsWith('/api/') ||
    pathname === '/docs' ||
    pathname.startsWith('/docs/') ||
    pathname === '/openapi.json' ||
    pathname === '/redoc'
  );
}

/** Pure — unit tested in router.test.ts. */
export function resolveRoute(pathname: string): Route {
  const normalized = pathname.replace(/\/+$/, '') || '/';
  if (isBackendPath(normalized)) return 'not-found';
  if (normalized === '/admin') return 'admin';
  if (normalized === '/') return 'home';
  return 'not-found';
}

let currentHandler: RouteChangeHandler | null = null;

export function navigate(path: string): void {
  if (window.location.pathname !== path) {
    window.history.pushState({}, '', path);
  }
  currentHandler?.(resolveRoute(path));
}

export function startRouter(onRouteChange: RouteChangeHandler): void {
  currentHandler = onRouteChange;

  window.addEventListener('popstate', () => {
    currentHandler?.(resolveRoute(window.location.pathname));
  });

  // Intercept clicks on internal links marked with data-link so navigation
  // between "/" and "/admin" doesn't force a full page reload.
  document.addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const anchor = target.closest('a[data-link]');
    if (!(anchor instanceof HTMLAnchorElement)) return;

    const url = new URL(anchor.href, window.location.origin);
    if (url.origin !== window.location.origin) return;

    event.preventDefault();
    navigate(url.pathname);
  });

  onRouteChange(resolveRoute(window.location.pathname));
}
