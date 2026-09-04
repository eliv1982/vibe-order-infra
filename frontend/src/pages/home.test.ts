// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AdminSettingRead, ApplicationCreateRead } from '../api/types';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    api: {
      getActiveServices: vi.fn(),
      createApplication: vi.fn(),
      createBehaviorMetric: vi.fn(),
    },
  };
});

import { api } from '../api/client';
import { renderHome } from './home';

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

function makeApplicationCreateRead(
  overrides: Partial<ApplicationCreateRead> = {},
): ApplicationCreateRead {
  return {
    id: 42,
    first_name: 'Иван',
    last_name: 'Петров',
    middle_name: null,
    contact_data: '+7 900 000-00-00',
    business_niche: 'Личный автомобиль',
    company_size: 'Седан или универсал',
    business_info: 'BMW X5, 2019',
    task_scope: 'Разовая услуга',
    requester_role: 'Владелец автомобиля',
    business_size: 'Один автомобиль',
    need_scope: 'Хочу восстановить блеск кузова',
    deadline: 'В течение недели',
    task_type: 'Восстановление внешнего вида',
    service_id: 1,
    interested_product: 'Полировка кузова',
    budget: '2500.00',
    preferred_contact_method: 'Телефон',
    preferred_contact_time: 'Утро (9:00–12:00)',
    comment: null,
    created_at: '2026-01-10T09:00:00Z',
    updated_at: '2026-01-10T09:00:00Z',
    behavior_metrics_capability: null,
    ...overrides,
  };
}

function setValue(root: HTMLElement, id: string, value: string): void {
  const el = root.querySelector<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(`#${id}`);
  if (!el) throw new Error(`field #${id} not found`);
  el.value = value;
}

function checkRadio(root: HTMLElement, name: string, value: string): void {
  const el = root.querySelector<HTMLInputElement>(`input[name="${name}"][value="${value}"]`);
  if (!el) throw new Error(`radio ${name}=${value} not found`);
  el.checked = true;
}

/** Selects the (only, mocked) active service and fills every required field
 * with a value the backend's closed option sets actually accept (see
 * options.ts) — mirrors what a real user filling out the form would submit. */
async function fillOutValidApplication(root: HTMLElement): Promise<void> {
  await vi.waitFor(() => expect(root.querySelector('.service-card')).not.toBeNull());
  const serviceRadio = root.querySelector<HTMLInputElement>('input[name="selected_service"]')!;
  serviceRadio.click();
  // jsdom's .click() flips `checked` but does not reliably fire the native
  // 'change' event synchronously - dispatch it explicitly so the listener
  // that calls selectService()/reveals the form actually runs.
  serviceRadio.dispatchEvent(new Event('change', { bubbles: true }));

  setValue(root, 'first_name', 'Иван');
  setValue(root, 'last_name', 'Петров');
  setValue(root, 'contact_data', '+7 900 000-00-00');
  setValue(root, 'business_niche', 'Личный автомобиль');
  setValue(root, 'company_size', 'Седан или универсал');
  setValue(root, 'business_size', 'Один автомобиль');
  setValue(root, 'requester_role', 'Владелец автомобиля');
  setValue(root, 'business_info', 'BMW X5, 2019 год');
  setValue(root, 'task_scope', 'Разовая услуга');
  setValue(root, 'task_type', 'Восстановление внешнего вида');
  setValue(root, 'deadline', 'В течение недели');
  setValue(root, 'need_scope', 'Хочу восстановить блеск кузова');
  checkRadio(root, 'preferred_contact_method', 'Телефон');
  setValue(root, 'preferred_contact_time', 'Утро (9:00–12:00)');
}

function submitForm(root: HTMLElement): void {
  const form = root.querySelector<HTMLFormElement>('#application-form-el')!;
  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
}

async function flush(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('handleSubmit — idempotent-replay null capability (Stage 1B correction)', () => {
  it('treats a successful response with behavior_metrics_capability: null as a successful submission, without sending behavior metrics', async () => {
    vi.mocked(api.getActiveServices).mockResolvedValue([makeService()]);
    vi.mocked(api.createApplication).mockResolvedValue(
      makeApplicationCreateRead({ behavior_metrics_capability: null }),
    );

    const root = document.createElement('div');
    renderHome(root);
    await fillOutValidApplication(root);

    submitForm(root);
    await vi.waitFor(() => expect(api.createApplication).toHaveBeenCalledTimes(1));
    await flush();

    // Success banner shown, form hidden — a null capability is not an error.
    expect(root.querySelector('.banner--success')).not.toBeNull();
    const formEl = root.querySelector<HTMLFormElement>('#application-form-el')!;
    expect(formEl.hidden).toBe(true);

    // No behavior-metrics call was made with a null/undefined capability —
    // sendBehaviorMetrics must never be invoked at all in this case.
    expect(api.createBehaviorMetric).not.toHaveBeenCalled();
  });

  it('does not crash and does not create a second application when the capability is null', async () => {
    vi.mocked(api.getActiveServices).mockResolvedValue([makeService()]);
    vi.mocked(api.createApplication).mockResolvedValue(
      makeApplicationCreateRead({ behavior_metrics_capability: null }),
    );

    const root = document.createElement('div');
    renderHome(root);
    await fillOutValidApplication(root);

    submitForm(root);
    await vi.waitFor(() => expect(api.createApplication).toHaveBeenCalledTimes(1));
    await flush();

    // A null capability alone must never trigger a retry/duplicate submit.
    expect(api.createApplication).toHaveBeenCalledTimes(1);
  });

  it('still sends behavior metrics when a real (non-null) capability is returned — contrast case', async () => {
    vi.mocked(api.getActiveServices).mockResolvedValue([makeService()]);
    vi.mocked(api.createApplication).mockResolvedValue(
      makeApplicationCreateRead({ id: 99, behavior_metrics_capability: 'real-one-time-token' }),
    );
    vi.mocked(api.createBehaviorMetric).mockResolvedValue({
      id: 1,
      application_id: 99,
      time_on_page: 0,
      clicked_buttons: [],
      cursor_hover_data: {},
      return_count: 1,
      created_at: '2026-01-10T09:00:00Z',
      updated_at: '2026-01-10T09:00:00Z',
    });

    const root = document.createElement('div');
    renderHome(root);
    await fillOutValidApplication(root);

    submitForm(root);
    await vi.waitFor(() => expect(api.createApplication).toHaveBeenCalledTimes(1));
    await vi.waitFor(() => expect(api.createBehaviorMetric).toHaveBeenCalledTimes(1));

    expect(api.createBehaviorMetric).toHaveBeenCalledWith(
      expect.objectContaining({ application_id: 99, capability: 'real-one-time-token' }),
    );
  });
});
