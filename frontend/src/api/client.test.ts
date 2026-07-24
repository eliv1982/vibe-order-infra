import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, extractErrorDetail, isUnauthorizedError, ApiError } from './client';
import type { ApplicationCreatePayload } from './types';

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

  it('POST /api/behavior-metrics', async () => {
    await api.createBehaviorMetric({ application_id: 1 });
    expect(fetchMock).toHaveBeenCalledWith('/api/behavior-metrics', expect.anything());
  });

  it('GET /api/auth/check', async () => {
    await api.checkAuthStatus();
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/check', expect.anything());
  });

  it('POST /api/auth/register', async () => {
    await api.registerAdmin({ username: 'admin', password: 'StrongPassw0rd!' });
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/register', expect.anything());
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
    await api.createBehaviorMetric({ application_id: 1 });
    await api.checkAuthStatus();
    await api.registerAdmin({ username: 'admin', password: 'StrongPassw0rd!' });
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
