import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, extractErrorDetail } from './client';
import type { ApplicationCreatePayload } from './types';

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
