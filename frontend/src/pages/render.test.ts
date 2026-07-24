import { describe, expect, it } from 'vitest';
import {
  applicationSummaryTemplate,
  homeTemplate,
  isServiceIdInList,
  serviceCardTemplate,
} from './home';
import { viewCardTemplate } from './admin';
import type { AdminSettingRead } from '../api/types';
import { formatBudget } from '../utils/format';

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

describe('serviceCardTemplate — full name/description rendering', () => {
  it('renders a long service name in full, without a truncation class', () => {
    const longName =
      'Комплексная керамическая защита кузова, дисков и салона автомобиля премиум-класса';
    const html = serviceCardTemplate(makeService({ service_name: longName }));
    expect(html).toContain(longName);
    expect(html).not.toMatch(/line-clamp|truncate|text-overflow/);
  });

  it('renders the service description on the card, in full', () => {
    const longDesc =
      'Ручная многоэтапная полировка с нанесением защитного керамического слоя и полировкой фар';
    const html = serviceCardTemplate(makeService({ description: longDesc }));
    expect(html).toContain(longDesc);
  });
});

describe('applicationSummaryTemplate', () => {
  const service = makeService({
    service_name: 'Керамическое покрытие',
    description: 'Защита кузова на 2 года',
  });

  it('shows the selected service name, description, budget, and a way back to the services section', () => {
    const html = applicationSummaryTemplate(service, 250_000);
    expect(html).toContain('Керамическое покрытие');
    expect(html).toContain('Защита кузова на 2 года');
    expect(html).toContain(formatBudget(250_000));
    expect(html).toContain('data-action="change-service"');
  });

  it('updates the displayed budget when the budget changes', () => {
    const cheaper = applicationSummaryTemplate(service, 100_000);
    const pricier = applicationSummaryTemplate(service, 300_000);
    expect(cheaper).not.toBe(pricier);
    expect(cheaper).toContain(formatBudget(100_000));
    expect(pricier).toContain(formatBudget(300_000));
  });

  it('prompts the user to choose a service first, instead of looking like an independent form', () => {
    const html = applicationSummaryTemplate(null, 0);
    expect(html).toContain('выберите услугу');
    expect(html).not.toContain('data-action="change-service"');
  });
});

describe('homeTemplate — public navigation', () => {
  it('never links to /admin from the public client page', () => {
    expect(homeTemplate()).not.toContain('/admin');
  });
});

describe('homeTemplate — form card hidden until a service is selected', () => {
  it('renders the form card with the hidden attribute, positioned after the application summary', () => {
    const html = homeTemplate();

    expect(html).toMatch(/<div class="card form-card" id="form-card" hidden>/);

    const summaryIndex = html.indexOf('id="application-summary"');
    const formCardIndex = html.indexOf('id="form-card"');
    expect(summaryIndex).toBeGreaterThan(-1);
    expect(formCardIndex).toBeGreaterThan(summaryIndex);
  });
});

describe('homeTemplate — AUREL Detailing wording, not IT/freelance/generic-business wording', () => {
  // These phrases came from a leftover IT-services/freelance-project intake
  // template and were replaced with car detailing wording (see options.ts
  // and applicationFormFieldsTemplate() in home.ts). None of them — as
  // form option values or as visible labels/legends — must resurface.
  const bannedPhrases = [
    'Разработка с нуля',
    'Доработка существующего',
    'Пробный проект',
    'Постоянное сотрудничество',
    'Размер компании',
    'Ниша бизнеса',
    'Сфера деятельности',
    'Объем задачи',
    'Объём задачи',
    'Роль заполняющего',
  ];

  it.each(bannedPhrases)('does not contain the leftover IT/business phrase %s', (phrase) => {
    expect(homeTemplate()).not.toContain(phrase);
  });
});

describe('homeTemplate — business_niche field (vehicle usage)', () => {
  // Scoped to just the business_niche <select> block (not the full
  // template, not exact attribute order) so this stays robust to
  // unrelated markup/reordering changes elsewhere in the form.
  it('renders business_niche as a required select with AUREL-relevant vehicle-usage options', () => {
    const html = homeTemplate();

    const selectMatch = html.match(/<select\b[^>]*id="business_niche"[^>]*>[\s\S]*?<\/select>/);
    expect(selectMatch).not.toBeNull();

    const selectHtml = selectMatch![0];
    expect(selectHtml).toContain('id="business_niche"');
    expect(selectHtml).toContain('name="business_niche"');
    expect(selectHtml).toMatch(/\brequired\b/);

    for (const option of ['Личный автомобиль', 'Автопарк компании', 'Автосалон или дилер']) {
      expect(selectHtml).toContain(`<option value="${option}">${option}</option>`);
    }
  });
});
