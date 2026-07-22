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
  AdminSettingCreatePayload,
  AdminSettingRead,
  AdminSettingUpdatePayload,
  ApplicationCreatePayload,
  ApplicationRead,
  BehaviorMetricCreatePayload,
  BehaviorMetricRead,
} from './types';

const API_PREFIX = '/api';

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
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

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      ...(options.headers ?? {}),
    },
  });

  if (!response.ok) {
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

const get = <T>(path: string) => request<T>(path, { method: 'GET' });
const post = <T>(path: string, body: unknown) =>
  request<T>(path, { method: 'POST', body: JSON.stringify(body) });
const patch = <T>(path: string, body: unknown) =>
  request<T>(path, { method: 'PATCH', body: JSON.stringify(body) });
const del = (path: string) => request<void>(path, { method: 'DELETE' });

export const api = {
  getActiveServices: () => get<AdminSettingRead[]>('/admin-settings/active'),
  getAllServices: () => get<AdminSettingRead[]>('/admin-settings'),
  createService: (payload: AdminSettingCreatePayload) =>
    post<AdminSettingRead>('/admin-settings', payload),
  updateService: (id: number, payload: AdminSettingUpdatePayload) =>
    patch<AdminSettingRead>(`/admin-settings/${id}`, payload),
  deleteService: (id: number) => del(`/admin-settings/${id}`),

  createApplication: (payload: ApplicationCreatePayload) =>
    post<ApplicationRead>('/applications', payload),

  createBehaviorMetric: (payload: BehaviorMetricCreatePayload) =>
    post<BehaviorMetricRead>('/behavior-metrics', payload),
};
