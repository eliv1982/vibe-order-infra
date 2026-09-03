import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, extractErrorDetail, isUnauthorizedError, ApiError } from './client';
import type {
  AnalyticsOverview,
  AnalyticsPeriod,
  ApplicationBehaviorAnalytics,
  ApplicationCreatePayload,
  PrioritizedApplicationList,
} from './types';

vi.mock('./tokenStorage', () => ({
  getToken: vi.fn(),
  saveToken: vi.fn(),
  clearToken: vi.fn(),
}));

import { clearToken, getToken } from './tokenStorage';

describe('extractErrorDetail', () => {
  it('returns the string detail from an HTTPException-style body', () => {
    expect(extractErrorDetail({ detail: 'Application not found' }, 'fallback')).toBe(
      'Application not found',
    );
  });

  it('joins messages from a FastAPI validation-error array', () => {
    const body = {
      detail: [
        { msg: 'Field required', loc: ['body', 'first_name'] },
        { msg: 'Input should be greater than 0', loc: ['body', 'application_id'] },
      ],
    };
    expect(extractErrorDetail(body, 'fallback')).toBe(
      'Field required; Input should be greater than 0',
    );
  });

  it('falls back when detail is missing entirely', () => {
    expect(extractErrorDetail({}, 'fallback message')).toBe('fallback message');
  });

  it('falls back when body is not an object', () => {
    expect(extractErrorDetail(null, 'fallback message')).toBe('fallback message');
    expect(extractErrorDetail('oops', 'fallback message')).toBe('fallback message');
  });
});

describe('api request URLs (canonical paths — no trailing slash)', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({}),
    });
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('GET /api/admin-settings/active', async () => {
    await api.getActiveServices();
    expect(fetchMock).toHaveBeenCalledWith('/api/admin-settings/active', expect.anything());
  });

  it('GET /api/admin-settings', async () => {
    await api.getAllServices();
    expect(fetchMock).toHaveBeenCalledWith('/api/admin-settings', expect.anything());
  });

  it('POST /api/admin-settings', async () => {
    await api.createService({ service_name: 'Полировка', budget_min: 100, budget_max: 200 });
    expect(fetchMock).toHaveBeenCalledWith('/api/admin-settings', expect.anything());
  });

  it('PATCH /api/admin-settings/{id} — no trailing slash after the id', async () => {
    await api.updateService(42, { is_active: false });
    expect(fetchMock).toHaveBeenCalledWith('/api/admin-settings/42', expect.anything());
  });

  it('DELETE /api/admin-settings/{id} — no trailing slash after the id', async () => {
    await api.deleteService(7);
    expect(fetchMock).toHaveBeenCalledWith('/api/admin-settings/7', expect.anything());
  });

  it('POST /api/applications', async () => {
    await api.createApplication({} as unknown as ApplicationCreatePayload);
    expect(fetchMock).toHaveBeenCalledWith('/api/applications', expect.anything());
  });

  it('parses the behavior_metrics_capability field from the create-application response', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 201,
      json: async () => ({ id: 1, behavior_metrics_capability: 'one-time-token' }),
    });

    const result = await api.createApplication({} as unknown as ApplicationCreatePayload);
    expect(result.behavior_metrics_capability).toBe('one-time-token');
  });

  it('GET /api/applications/prioritized?skip=0&limit=100 by default', async () => {
    await api.getPrioritizedApplications();
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/applications/prioritized?skip=0&limit=100',
      expect.anything(),
    );
  });

  it('GET /api/applications/prioritized with custom skip/limit, safely encoded', async () => {
    await api.getPrioritizedApplications(20, 50);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/applications/prioritized?skip=20&limit=50',
      expect.anything(),
    );
  });

  it('parses the PrioritizedApplicationList response', async () => {
    const payload: PrioritizedApplicationList = { items: [], total: 0, skip: 0, limit: 100 };
    fetchMock.mockResolvedValueOnce({ ok: true, status: 200, json: async () => payload });

    await expect(api.getPrioritizedApplications()).resolves.toEqual(payload);
  });

  it('POST /api/behavior-metrics', async () => {
    await api.createBehaviorMetric({ application_id: 1, capability: 'tok' });
    expect(fetchMock).toHaveBeenCalledWith('/api/behavior-metrics', expect.anything());
  });

  it('GET /api/analytics/overview?period=day', async () => {
    await api.getAnalyticsOverview('day');
    expect(fetchMock).toHaveBeenCalledWith('/api/analytics/overview?period=day', expect.anything());
  });

  it('GET /api/analytics/overview?period=week', async () => {
    await api.getAnalyticsOverview('week');
    expect(fetchMock).toHaveBeenCalledWith('/api/analytics/overview?period=week', expect.anything());
  });

  it('GET /api/analytics/overview?period=month', async () => {
    await api.getAnalyticsOverview('month');
    expect(fetchMock).toHaveBeenCalledWith('/api/analytics/overview?period=month', expect.anything());
  });

  it('parses the AnalyticsOverview response', async () => {
    const payload: AnalyticsOverview = {
      period: 'week',
      period_start: '2026-07-17T00:00:00Z',
      period_end: '2026-07-24T00:00:00Z',
      applications_count: 10,
      metrics_count: 8,
      applications_with_metrics: 6,
      applications_without_metrics: 4,
      average_time_on_page_seconds: 42.5,
      median_time_on_page_seconds: 38,
      average_return_count: 1.25,
      total_return_count: 10,
      total_button_clicks: 55,
      unique_clicked_buttons: 4,
      popular_buttons: [{ name: 'Записаться', count: 30, share_percent: 54.5 }],
      section_activity: [
        {
          section: 'Контакты',
          total_duration_seconds: 120,
          average_duration_seconds: 12,
          interactions_count: 10,
          share_percent: 100,
        },
      ],
    };
    fetchMock.mockResolvedValueOnce({ ok: true, status: 200, json: async () => payload });

    await expect(api.getAnalyticsOverview('week')).resolves.toEqual(payload);
  });

  it('GET /api/analytics/applications/{id} — no trailing slash after the id', async () => {
    await api.getApplicationBehaviorAnalytics(42);
    expect(fetchMock).toHaveBeenCalledWith('/api/analytics/applications/42', expect.anything());
  });

  it('parses the ApplicationBehaviorAnalytics response', async () => {
    const payload: ApplicationBehaviorAnalytics = {
      application_id: 7,
      has_metrics: true,
      time_on_page_seconds: 88,
      return_count: 2,
      clicked_buttons: [{ name: 'Отправить', count: 3, share_percent: 100 }],
      section_activity: [],
      total_button_clicks: 3,
      recorded_at: '2026-07-20T10:00:00Z',
    };
    fetchMock.mockResolvedValueOnce({ ok: true, status: 200, json: async () => payload });

    await expect(api.getApplicationBehaviorAnalytics(7)).resolves.toEqual(payload);
  });

  it('rejects an invalid analytics period without calling fetch', async () => {
    await expect(
      api.getAnalyticsOverview('bogus' as unknown as AnalyticsPeriod),
    ).rejects.toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('rejects a non-positive-integer application id without calling fetch', async () => {
    await expect(api.getApplicationBehaviorAnalytics(-1)).rejects.toThrow();
    await expect(api.getApplicationBehaviorAnalytics(0)).rejects.toThrow();
    await expect(api.getApplicationBehaviorAnalytics(1.5)).rejects.toThrow();
    await expect(api.getApplicationBehaviorAnalytics(Number.NaN)).rejects.toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('GET /api/auth/check', async () => {
    await api.checkAuthStatus();
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/check', expect.anything());
  });

  it('POST /api/auth/login', async () => {
    await api.loginAdmin({ username: 'admin', password: 'StrongPassw0rd!' });
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/login', expect.anything());
  });

  it('GET /api/auth/me', async () => {
    await api.getCurrentAdmin();
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/me', expect.anything());
  });

  it('never calls any endpoint with a trailing slash', async () => {
    await api.getActiveServices();
    await api.getAllServices();
    await api.updateService(1, {});
    await api.deleteService(1);
    await api.getAnalyticsOverview('week');
    await api.getApplicationBehaviorAnalytics(1);

    for (const call of fetchMock.mock.calls) {
      const url = call[0] as string;
      expect(url.endsWith('/')).toBe(false);
    }
  });
});

describe('Authorization header — attached only to protected calls', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({}),
    });
    vi.stubGlobal('fetch', fetchMock);
    vi.mocked(getToken).mockReturnValue('a-stored-token');
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.mocked(getToken).mockReset();
  });

  function headersOf(call: unknown[]): Record<string, string> {
    const init = call[1] as RequestInit;
    return init.headers as Record<string, string>;
  }

  it('getCurrentAdmin sends Authorization: Bearer <token>', async () => {
    await api.getCurrentAdmin();
    expect(headersOf(fetchMock.mock.calls[0]).Authorization).toBe('Bearer a-stored-token');
  });

  it('getPrioritizedApplications sends Authorization: Bearer <token>', async () => {
    await api.getPrioritizedApplications();
    expect(headersOf(fetchMock.mock.calls[0]).Authorization).toBe('Bearer a-stored-token');
  });

  it('getAnalyticsOverview sends Authorization: Bearer <token>', async () => {
    await api.getAnalyticsOverview('week');
    expect(headersOf(fetchMock.mock.calls[0]).Authorization).toBe('Bearer a-stored-token');
  });

  it('getApplicationBehaviorAnalytics sends Authorization: Bearer <token>', async () => {
    await api.getApplicationBehaviorAnalytics(3);
    expect(headersOf(fetchMock.mock.calls[0]).Authorization).toBe('Bearer a-stored-token');
  });

  it('getAllServices/createService/updateService/deleteService all send Authorization', async () => {
    await api.getAllServices();
    await api.createService({ service_name: 'X', budget_min: 1, budget_max: 2 });
    await api.updateService(1, { is_active: true });
    await api.deleteService(1);

    for (const call of fetchMock.mock.calls) {
      expect(headersOf(call).Authorization).toBe('Bearer a-stored-token');
    }
  });

  it('public calls never send Authorization even when a token is stored', async () => {
    await api.getActiveServices();
    await api.createApplication({} as unknown as ApplicationCreatePayload);
    await api.createBehaviorMetric({ application_id: 1, capability: 'tok' });
    await api.checkAuthStatus();
    await api.loginAdmin({ username: 'admin', password: 'StrongPassw0rd!' });

    for (const call of fetchMock.mock.calls) {
      expect(headersOf(call).Authorization).toBeUndefined();
    }
  });

  it('a protected call sends no Authorization header when no token is stored', async () => {
    vi.mocked(getToken).mockReturnValue(null);
    await api.getAllServices();
    expect(headersOf(fetchMock.mock.calls[0]).Authorization).toBeUndefined();
  });

  it('does not send Authorization when the stored token is an empty string', async () => {
    vi.mocked(getToken).mockReturnValue('');
    await api.getAllServices();
    expect(headersOf(fetchMock.mock.calls[0]).Authorization).toBeUndefined();
  });

  it('does not send Authorization when the stored token is whitespace-only', async () => {
    vi.mocked(getToken).mockReturnValue('   ');
    await api.getAllServices();
    expect(headersOf(fetchMock.mock.calls[0]).Authorization).toBeUndefined();
  });

  it('never forms "Bearer null" even if getToken returns null at runtime', async () => {
    vi.mocked(getToken).mockReturnValue(null);
    await api.getAllServices();
    const auth = headersOf(fetchMock.mock.calls[0]).Authorization;
    expect(auth).toBeUndefined();
    expect(auth).not.toBe('Bearer null');
  });

  it('never forms "Bearer undefined" even if getToken misbehaves at runtime', async () => {
    vi.mocked(getToken).mockReturnValue(undefined as unknown as string | null);
    await api.getAllServices();
    const auth = headersOf(fetchMock.mock.calls[0]).Authorization;
    expect(auth).toBeUndefined();
    expect(auth).not.toBe('Bearer undefined');
  });
});

describe('401 handling — centralized token clearing', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.mocked(clearToken).mockReset();
    vi.mocked(getToken).mockReset();
  });

  it('a 401 on a protected call clears the stored token', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({ detail: 'Could not validate credentials' }),
      }),
    );

    await expect(api.getAllServices()).rejects.toThrow(ApiError);
    expect(clearToken).toHaveBeenCalledTimes(1);
  });

  it('a 401 on getPrioritizedApplications clears the stored token', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({ detail: 'Could not validate credentials' }),
      }),
    );

    await expect(api.getPrioritizedApplications()).rejects.toThrow(ApiError);
    expect(clearToken).toHaveBeenCalledTimes(1);
  });

  it('a 401 on getAnalyticsOverview clears the stored token', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({ detail: 'Could not validate credentials' }),
      }),
    );

    await expect(api.getAnalyticsOverview('week')).rejects.toThrow(ApiError);
    expect(clearToken).toHaveBeenCalledTimes(1);
  });

  it('a 404 on getApplicationBehaviorAnalytics does not clear the stored token', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        json: async () => ({ detail: 'Application not found' }),
      }),
    );

    await expect(api.getApplicationBehaviorAnalytics(999)).rejects.toThrow(ApiError);
    expect(clearToken).not.toHaveBeenCalled();
  });

  it('a 422 on getAnalyticsOverview does not clear the stored token', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: async () => ({ detail: [{ msg: 'Input should be a valid period', loc: ['query', 'period'] }] }),
      }),
    );

    await expect(api.getAnalyticsOverview('week')).rejects.toThrow(ApiError);
    expect(clearToken).not.toHaveBeenCalled();
  });

  it('a 500 on getApplicationBehaviorAnalytics does not clear the stored token', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: async () => ({ detail: 'Internal Server Error' }),
      }),
    );

    await expect(api.getApplicationBehaviorAnalytics(5)).rejects.toThrow(ApiError);
    expect(clearToken).not.toHaveBeenCalled();
  });

  it('a 401 on a public call never clears anything (there is nothing to clear)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({ detail: 'Incorrect username or password' }),
      }),
    );

    await expect(api.loginAdmin({ username: 'admin', password: 'wrong' })).rejects.toThrow(
      ApiError,
    );
    expect(clearToken).not.toHaveBeenCalled();
  });

  it('a network error on a protected call does not clear a valid stored token', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));

    await expect(api.getAllServices()).rejects.toThrow();
    expect(clearToken).not.toHaveBeenCalled();
  });
});

describe('isUnauthorizedError', () => {
  it('is true for a 401 ApiError', () => {
    expect(isUnauthorizedError(new ApiError('nope', 401))).toBe(true);
  });

  it('is false for other ApiError statuses', () => {
    expect(isUnauthorizedError(new ApiError('not found', 404))).toBe(false);
    expect(isUnauthorizedError(new ApiError('conflict', 409))).toBe(false);
  });

  it('is false for a non-ApiError (e.g. a network failure)', () => {
    expect(isUnauthorizedError(new TypeError('Failed to fetch'))).toBe(false);
    expect(isUnauthorizedError(new Error('boom'))).toBe(false);
    expect(isUnauthorizedError('not even an error')).toBe(false);
  });
});
