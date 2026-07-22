import { describe, expect, it } from 'vitest';
import { isServiceIdInList, serviceCardTemplate } from './home';
import { viewCardTemplate } from './admin';
import type { AdminSettingRead } from '../api/types';

function makeService(overrides: Partial<AdminSettingRead> = {}): AdminSettingRead {
  return {
    id: 1,
    service_name: 'Полировка кузова',
    budget_min: '1000.00',
    budget_max: '5000.00',
    description: 'Ручная полировка',
    is_active: true,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

describe('serviceCardTemplate (home) — rendered XSS safety', () => {
  it('escapes a malicious service_name instead of rendering it as markup', () => {
    const html = serviceCardTemplate(
      makeService({ service_name: '<img src=x onerror="alert(1)">' }),
    );
    expect(html).not.toContain('<img src=x onerror="alert(1)">');
    expect(html).toContain('&lt;img src=x onerror=&quot;alert(1)&quot;&gt;');
  });
});

describe('viewCardTemplate (admin) — rendered XSS safety', () => {
  it('escapes a malicious service_name and description instead of rendering them as markup', () => {
    const html = viewCardTemplate(
      makeService({
        service_name: '<script>alert(1)</script>',
        description: '<img src=x onerror="alert(2)">',
      }),
    );
    expect(html).not.toContain('<script>alert(1)</script>');
    expect(html).not.toContain('<img src=x onerror="alert(2)">');
    expect(html).toContain('&lt;script&gt;alert(1)&lt;/script&gt;');
    expect(html).toContain('&lt;img src=x onerror=&quot;alert(2)&quot;&gt;');
  });
});

describe('isServiceIdInList', () => {
  const activeServices = [makeService({ id: 1 }), makeService({ id: 2 })];

  it('returns true when the selected service is present (still active)', () => {
    expect(isServiceIdInList(activeServices, 1)).toBe(true);
  });

  it('returns false when the selected service id is missing — deleted or deactivated', () => {
    expect(isServiceIdInList(activeServices, 999)).toBe(false);
  });

  it('returns false for an empty active list', () => {
    expect(isServiceIdInList([], 1)).toBe(false);
  });
});
