/**
 * Single fetch wrapper for the backend API.
 *
 * - Same-origin requests only: paths are relative ("/api/..."), never a
 *   hardcoded host/IP — Nginx proxies /api/ to the backend in production,
 *   so this works unmodified behind any domain and needs no CORS config.
 * - Canonical endpoint paths have no trailing slash (the backend's
 *   collection routes are registered as "" under their router prefix,
 *   e.g. POST /api/applications — a trailing slash would 307-redirect).
 */

import type {
  AdminLoginPayload,
  AdminRead,
  AdminSettingCreatePayload,
  AdminSettingRead,
  AdminSettingUpdatePayload,
  AnalyticsOverview,
  AnalyticsPeriod,
  ApplicationBehaviorAnalytics,
  ApplicationCreatePayload,
  ApplicationCreateRead,
  AuthCheckResponse,
  BehaviorMetricCreatePayload,
  BehaviorMetricRead,
  PrioritizedApplicationList,
  TokenResponse,
} from './types';
import { clearToken, getToken } from './tokenStorage';

const API_PREFIX = '/api';

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/** True for a 401 from a Bearer-protected call — the caller should treat this
 * as "not authenticated (any more)", not as a generic request failure. */
export function isUnauthorizedError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

interface FastApiValidationErrorItem {
  msg?: unknown;
}

/** Pure function, unit tested in api/client.test.ts — no fetch involved. */
export function extractErrorDetail(body: unknown, fallback: string): string {
  if (!body || typeof body !== 'object' || !('detail' in body)) {
    return fallback;
  }

  const detail = (body as { detail: unknown }).detail;

  if (typeof detail === 'string' && detail.trim().length > 0) {
    return detail;
  }

  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item as FastApiValidationErrorItem)?.msg)
      .filter((msg): msg is string => typeof msg === 'string' && msg.length > 0);
    if (messages.length > 0) {
      return messages.join('; ');
    }
  }

  return fallback;
}

interface RequestOpts {
  /** Attach `Authorization: Bearer <token>` when a token is stored, and
   * treat a 401 response as "clear the stored token" — opt-in per call so
   * public endpoints never send a token they don't need. */
  auth?: boolean;
}

/** Defense-in-depth beyond tokenStorage's own guarantee: never let a
 * runtime-invalid getToken() result (null/undefined/empty/whitespace) reach
 * an Authorization header as "Bearer null"/"Bearer undefined"/"Bearer ". */
function hasUsableToken(token: string | null): token is string {
  return typeof token === 'string' && token.trim().length > 0;
}

const ANALYTICS_PERIODS: readonly AnalyticsPeriod[] = ['day', 'week', 'month'];

/** Frontend allowlist — never forwards an arbitrary string as the `period`
 * query param, regardless of what a caller (or a compromised caller) passes
 * in at runtime despite the AnalyticsPeriod compile-time type. */
function isValidAnalyticsPeriod(value: unknown): value is AnalyticsPeriod {
  return typeof value === 'string' && (ANALYTICS_PERIODS as readonly string[]).includes(value);
}

/** application_id is a path segment, not a query param — validated before
 * the URL is even built so a malformed/hostile id can never end up baked
 * into the request path. */
function isPositiveIntegerId(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0;
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  opts: RequestOpts = {},
): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'application/json',
    ...(options.headers as Record<string, string> | undefined),
  };

  if (opts.auth) {
    const token = getToken();
    if (hasUsableToken(token)) {
      headers.Authorization = `Bearer ${token}`;
    }
  }

  const response = await fetch(`${API_PREFIX}${path}`, { ...options, headers });

  if (!response.ok) {
    if (opts.auth && response.status === 401) {
      // Centralized here so every auth-required call self-heals stale
      // storage, regardless of which one happened to hit the 401 first.
      clearToken();
    }

    const fallback = `Запрос завершился с ошибкой (${response.status})`;
    let detail = fallback;
    try {
      const body: unknown = await response.json();
      detail = extractErrorDetail(body, fallback);
    } catch {
      // Response body wasn't JSON (or was empty) — keep the generic message.
    }
    throw new ApiError(detail, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

const get = <T>(path: string, opts?: RequestOpts) => request<T>(path, { method: 'GET' }, opts);
const post = <T>(path: string, body: unknown, opts?: RequestOpts) =>
  request<T>(path, { method: 'POST', body: JSON.stringify(body) }, opts);
const patch = <T>(path: string, body: unknown, opts?: RequestOpts) =>
  request<T>(path, { method: 'PATCH', body: JSON.stringify(body) }, opts);
const del = (path: string, opts?: RequestOpts) => request<void>(path, { method: 'DELETE' }, opts);

export const api = {
  getActiveServices: () => get<AdminSettingRead[]>('/admin-settings/active'),
  getAllServices: () => get<AdminSettingRead[]>('/admin-settings', { auth: true }),
  createService: (payload: AdminSettingCreatePayload) =>
    post<AdminSettingRead>('/admin-settings', payload, { auth: true }),
  updateService: (id: number, payload: AdminSettingUpdatePayload) =>
    patch<AdminSettingRead>(`/admin-settings/${id}`, payload, { auth: true }),
  deleteService: (id: number) => del(`/admin-settings/${id}`, { auth: true }),

  createApplication: (payload: ApplicationCreatePayload) =>
    post<ApplicationCreateRead>('/applications', payload),
  getPrioritizedApplications: (skip = 0, limit = 100) => {
    const params = new URLSearchParams({ skip: String(skip), limit: String(limit) });
    return get<PrioritizedApplicationList>(`/applications/prioritized?${params.toString()}`, {
      auth: true,
    });
  },

  createBehaviorMetric: (payload: BehaviorMetricCreatePayload) =>
    post<BehaviorMetricRead>('/behavior-metrics', payload),

  getAnalyticsOverview: (period: AnalyticsPeriod) => {
    if (!isValidAnalyticsPeriod(period)) {
      return Promise.reject(new Error('Invalid analytics period'));
    }
    const params = new URLSearchParams({ period });
    return get<AnalyticsOverview>(`/analytics/overview?${params.toString()}`, { auth: true });
  },
  getApplicationBehaviorAnalytics: (applicationId: number) => {
    if (!isPositiveIntegerId(applicationId)) {
      return Promise.reject(new Error('Invalid application id'));
    }
    return get<ApplicationBehaviorAnalytics>(`/analytics/applications/${applicationId}`, {
      auth: true,
    });
  },

  checkAuthStatus: () => get<AuthCheckResponse>('/auth/check'),
  loginAdmin: (payload: AdminLoginPayload) => post<TokenResponse>('/auth/login', payload),
  getCurrentAdmin: () => get<AdminRead>('/auth/me', { auth: true }),
};
