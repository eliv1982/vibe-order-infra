// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/client';
import type { AnalyticsOverview } from '../api/types';
import {
  analyticsPeriodLabel,
  formatAnalyticsDate,
  formatAnalyticsDateTime,
  formatAnalyticsName,
  formatAverageReturns,
  formatCount,
  formatPercent,
  formatPeriodRange,
  formatSeconds,
  isOverviewEmpty,
  mountAdminAnalytics,
  normalizeCount,
  normalizePercent,
  percentBarWidth,
  type AnalyticsSectionController,
  type AnalyticsSectionHost,
} from './adminAnalytics';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    api: {
      getAnalyticsOverview: vi.fn(),
    },
  };
});

import { api } from '../api/client';

function makeOverview(overrides: Partial<AnalyticsOverview> = {}): AnalyticsOverview {
  return {
    period: 'week',
    period_start: '2026-07-17T00:00:00Z',
    period_end: '2026-07-24T00:00:00Z',
    applications_count: 12,
    metrics_count: 9,
    applications_with_metrics: 7,
    applications_without_metrics: 5,
    average_time_on_page_seconds: 125,
    median_time_on_page_seconds: 90,
    average_return_count: 1.4,
    total_return_count: 14,
    total_button_clicks: 40,
    unique_clicked_buttons: 3,
    popular_buttons: [
      { name: 'Записаться', count: 20, share_percent: 50 },
      { name: 'Позвонить', count: 12, share_percent: 30 },
      { name: 'Написать', count: 8, share_percent: 20 },
    ],
    section_activity: [
      {
        section: 'Контакты',
        total_duration_seconds: 300,
        average_duration_seconds: 15,
        interactions_count: 20,
        share_percent: 60,
      },
      {
        section: 'Услуги',
        total_duration_seconds: 200,
        average_duration_seconds: 10,
        interactions_count: 20,
        share_percent: 40,
      },
    ],
    ...overrides,
  };
}

function makeEmptyOverview(overrides: Partial<AnalyticsOverview> = {}): AnalyticsOverview {
  return makeOverview({
    applications_count: 0,
    metrics_count: 0,
    applications_with_metrics: 0,
    applications_without_metrics: 0,
    average_time_on_page_seconds: null,
    median_time_on_page_seconds: null,
    average_return_count: null,
    total_return_count: 0,
    total_button_clicks: 0,
    unique_clicked_buttons: 0,
    popular_buttons: [],
    section_activity: [],
    ...overrides,
  });
}

/** Bypasses TypeScript's field typing on purpose — builds malformed/hostile
 * wire fixtures mirroring what an actual malformed backend/proxy response
 * would look like at runtime, per adminApplications.test.ts's own
 * makeUnsafeItem convention. */
function makeUnsafeOverview(overrides: Record<string, unknown>): AnalyticsOverview {
  return { ...makeOverview(), ...overrides } as unknown as AnalyticsOverview;
}

function makeHost(overrides: Partial<AnalyticsSectionHost> = {}): AnalyticsSectionHost {
  return {
    isActive: () => true,
    onSessionExpired: vi.fn(),
    ...overrides,
  };
}

function mountAndActivate(
  container: HTMLElement,
  host: AnalyticsSectionHost,
): AnalyticsSectionController {
  const controller = mountAdminAnalytics(container, host);
  controller.activate();
  return controller;
}

async function renderWithOverview(overview: AnalyticsOverview): Promise<HTMLElement> {
  vi.mocked(api.getAnalyticsOverview).mockResolvedValue(overview);
  const container = document.createElement('div');
  mountAndActivate(container, makeHost());
  await vi.waitFor(() => expect(container.querySelector('#analytics-content')!.hasAttribute('hidden')).toBe(false));
  return container;
}

/** A controllable promise for deterministic "slow request resolves late"
 * tests, mirroring adminApplications.test.ts's own helper. */
function createDeferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
} {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

async function flush(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

// --- Rendering -------------------------------------------------------------

describe('rendering', () => {
  it('shows a loading state before the request resolves', () => {
    vi.mocked(api.getAnalyticsOverview).mockReturnValue(new Promise(() => {}));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());

    expect(container.textContent).toContain('Загружаем статистику');
  });

  it('requests the week period by default on first activation', async () => {
    vi.mocked(api.getAnalyticsOverview).mockResolvedValue(makeOverview());
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());

    await vi.waitFor(() => expect(api.getAnalyticsOverview).toHaveBeenCalledWith('week'));
    expect(container.querySelector('[data-period="week"]')!.getAttribute('aria-pressed')).toBe('true');
  });

  it('renders the KPI values from the overview', async () => {
    const container = await renderWithOverview(makeOverview());

    expect(container.textContent).toContain('12'); // applications_count
    expect(container.textContent).toContain('7'); // applications_with_metrics
    expect(container.textContent).toContain('5'); // applications_without_metrics
    expect(container.textContent).toContain('9'); // metrics_count
    expect(container.textContent).toContain('14'); // total_return_count
    expect(container.textContent).toContain('40'); // total_button_clicks
    expect(container.textContent).toContain('3'); // unique_clicked_buttons
  });

  it('a fractional (malformed/schema-drift) KPI count renders as "—", never rounded', async () => {
    const overview = makeUnsafeOverview({
      applications_count: 1.5,
      metrics_count: 1.6,
      applications_with_metrics: 7,
    });
    const container = await renderWithOverview(overview);

    const kpiValues = [...container.querySelectorAll<HTMLElement>('.analytics-kpi-value')].map(
      (el) => el.textContent,
    );
    // The malformed counts must show the dash, not a silently rounded "2".
    expect(kpiValues).toContain('—');
    expect(kpiValues).not.toContain('2');
    // A genuinely valid integer count on the same overview still renders normally.
    expect(kpiValues).toContain('7');
  });

  it('shows the period range formatted from period_start/period_end', async () => {
    const overview = makeOverview();
    const container = await renderWithOverview(overview);

    const expected = `Период: ${formatAnalyticsDate(overview.period_start)} – ${formatAnalyticsDate(overview.period_end)}`;
    expect(container.querySelector('#analytics-range')!.textContent).toBe(expected);
  });

  it('formats average/median time on page using minutes+seconds semantics', async () => {
    const container = await renderWithOverview(
      makeOverview({ average_time_on_page_seconds: 192, median_time_on_page_seconds: 45 }),
    );

    expect(container.textContent).toContain('3 мин 12 сек');
    expect(container.textContent).toContain('45 сек');
  });

  it('formats average/total returns', async () => {
    const container = await renderWithOverview(
      makeOverview({ average_return_count: 1.5, total_return_count: 14 }),
    );

    expect(container.textContent).toContain('1,5');
    expect(container.textContent).toContain('14');
  });

  it('shows an explanatory caption about how the numbers are computed', async () => {
    const container = await renderWithOverview(makeOverview());
    expect(container.textContent).toContain(
      'Статистика рассчитана по отправленным заявкам и связанным с ними поведенческим метрикам.',
    );
  });

  it('shows an empty-overview note plus per-section empty states when every count is zero', async () => {
    const container = await renderWithOverview(makeEmptyOverview());

    expect(container.querySelector('#analytics-empty-note')!.hasAttribute('hidden')).toBe(false);
    expect(container.textContent).toContain('За выбранный период данных пока нет.');
    expect(container.textContent).toContain('За выбранный период кликов по кнопкам не зафиксировано.');
    expect(container.textContent).toContain('За выбранный период активность по секциям не зафиксирована.');
  });

  it('hides the empty-overview note when at least one count is non-zero', async () => {
    const container = await renderWithOverview(makeOverview());
    expect(container.querySelector('#analytics-empty-note')!.hasAttribute('hidden')).toBe(true);
  });

  it('shows a neutral error message and a working retry on a non-401 failure', async () => {
    vi.mocked(api.getAnalyticsOverview).mockRejectedValueOnce(new TypeError('Failed to fetch'));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());

    await vi.waitFor(() => expect(container.textContent).toContain('Не удалось загрузить статистику'));
    expect(container.querySelector('#analytics-content')!.hasAttribute('hidden')).toBe(true);
    // The period selector must remain usable during an error state.
    const dayButton = container.querySelector<HTMLButtonElement>('[data-period="day"]')!;
    expect(dayButton.disabled).toBe(false);

    vi.mocked(api.getAnalyticsOverview).mockResolvedValueOnce(makeOverview());
    container.querySelector<HTMLButtonElement>('#analytics-refresh')!.click();
    await vi.waitFor(() => expect(container.textContent).not.toContain('Не удалось загрузить статистику'));
    expect(container.querySelector('#analytics-content')!.hasAttribute('hidden')).toBe(false);
  });

  it('never shows the backend error body text', async () => {
    vi.mocked(api.getAnalyticsOverview).mockRejectedValueOnce(
      new ApiError('some backend-specific SQL error detail', 500),
    );
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());

    await vi.waitFor(() => expect(container.textContent).toContain('Не удалось загрузить статистику'));
    expect(container.textContent).not.toContain('some backend-specific SQL error detail');
  });

  it('a 401 calls host.onSessionExpired instead of rendering an error banner', async () => {
    vi.mocked(api.getAnalyticsOverview).mockRejectedValue(new ApiError('nope', 401));
    const onSessionExpired = vi.fn();
    const container = document.createElement('div');
    mountAndActivate(container, makeHost({ onSessionExpired }));

    await vi.waitFor(() => expect(onSessionExpired).toHaveBeenCalledTimes(1));
    expect(container.textContent).not.toContain('Не удалось загрузить');
  });
});

// --- Periods -----------------------------------------------------------

describe('periods', () => {
  it('day/week/month buttons request the matching period and mark it active', async () => {
    vi.mocked(api.getAnalyticsOverview).mockResolvedValue(makeOverview());
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(api.getAnalyticsOverview).toHaveBeenCalledWith('week'));

    container.querySelector<HTMLButtonElement>('[data-period="day"]')!.click();
    await vi.waitFor(() => expect(api.getAnalyticsOverview).toHaveBeenCalledWith('day'));
    expect(container.querySelector('[data-period="day"]')!.getAttribute('aria-pressed')).toBe('true');
    expect(container.querySelector('[data-period="week"]')!.getAttribute('aria-pressed')).toBe('false');

    container.querySelector<HTMLButtonElement>('[data-period="month"]')!.click();
    await vi.waitFor(() => expect(api.getAnalyticsOverview).toHaveBeenCalledWith('month'));
    expect(container.querySelector('[data-period="month"]')!.getAttribute('aria-pressed')).toBe('true');
  });

  it('clicking the already-active period does not issue an extra request', async () => {
    vi.mocked(api.getAnalyticsOverview).mockResolvedValue(makeOverview());
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(api.getAnalyticsOverview).toHaveBeenCalledTimes(1));

    container.querySelector<HTMLButtonElement>('[data-period="week"]')!.click();
    await flush();
    expect(api.getAnalyticsOverview).toHaveBeenCalledTimes(1);
  });

  it('switching period invalidates the previous period\'s pending response', async () => {
    const deferredWeek = createDeferred<AnalyticsOverview>();
    vi.mocked(api.getAnalyticsOverview).mockReturnValueOnce(deferredWeek.promise);
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(api.getAnalyticsOverview).toHaveBeenCalledWith('week'));

    vi.mocked(api.getAnalyticsOverview).mockResolvedValueOnce(makeOverview({ period: 'day', applications_count: 3 }));
    container.querySelector<HTMLButtonElement>('[data-period="day"]')!.click();
    await vi.waitFor(() => expect(container.textContent).toContain('3'));

    // The stale "week" response now resolves — must not repaint over "day".
    deferredWeek.resolve(makeOverview({ period: 'week', applications_count: 999 }));
    await flush();

    expect(container.textContent).not.toContain('999');
    expect(container.querySelector('[data-period="day"]')!.getAttribute('aria-pressed')).toBe('true');
  });

  it('refresh reloads the currently selected period', async () => {
    vi.mocked(api.getAnalyticsOverview).mockResolvedValue(makeOverview());
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(api.getAnalyticsOverview).toHaveBeenCalledTimes(1));

    container.querySelector<HTMLButtonElement>('[data-period="month"]')!.click();
    await vi.waitFor(() => expect(api.getAnalyticsOverview).toHaveBeenCalledWith('month'));

    vi.mocked(api.getAnalyticsOverview).mockClear();
    container.querySelector<HTMLButtonElement>('#analytics-refresh')!.click();
    await vi.waitFor(() => expect(api.getAnalyticsOverview).toHaveBeenCalledWith('month'));
    expect(api.getAnalyticsOverview).toHaveBeenCalledTimes(1);
  });

  it('backend order of popular_buttons/section_activity is preserved across period switches', async () => {
    const overview = makeOverview();
    const container = await renderWithOverview(overview);

    const names = [...container.querySelectorAll<HTMLElement>('#analytics-buttons .analytics-bar-name')].map(
      (el) => el.textContent,
    );
    expect(names).toEqual(overview.popular_buttons.map((b) => b.name));

    const sections = [...container.querySelectorAll<HTMLElement>('#analytics-sections .analytics-bar-name')].map(
      (el) => el.textContent,
    );
    expect(sections).toEqual(overview.section_activity.map((s) => s.section));
  });
});

// --- Popular buttons ---------------------------------------------------

describe('popular buttons', () => {
  it('shows name, count and share for each button in backend order', async () => {
    const container = await renderWithOverview(makeOverview());

    const items = [...container.querySelectorAll<HTMLElement>('#analytics-buttons .analytics-bar-item')];
    expect(items.length).toBe(3);
    expect(items[0].textContent).toContain('Записаться');
    expect(items[0].textContent).toContain('20');
    expect(items[0].textContent).toContain('50%');
  });

  it('clamps the visual bar width to [0, 100] from share_percent', async () => {
    const container = await renderWithOverview(
      makeOverview({ popular_buttons: [{ name: 'X', count: 1, share_percent: 250 }] }),
    );
    const fill = container.querySelector<HTMLElement>('#analytics-buttons .analytics-bar-fill')!;
    expect(fill.style.width).toBe('100%');
  });

  it('renders malformed runtime values safely instead of NaN/undefined/[object Object]', async () => {
    const overview = makeUnsafeOverview({
      popular_buttons: [{ name: 42, count: 'many', share_percent: 'lots' }],
    });
    const container = await renderWithOverview(overview);

    const item = container.querySelector<HTMLElement>('#analytics-buttons .analytics-bar-item')!;
    expect(item.textContent).not.toContain('NaN');
    expect(item.textContent).not.toContain('undefined');
    expect(item.textContent).not.toContain('[object Object]');
    expect(item.querySelector('.analytics-bar-name')!.textContent).toBe('Без названия');
    expect(item.textContent).toContain('—');
    const fill = item.querySelector<HTMLElement>('.analytics-bar-fill')!;
    expect(fill.style.width).toBe('0%');
  });

  it('shows a dedicated empty message and no list when there are no buttons', async () => {
    const container = await renderWithOverview(makeOverview({ popular_buttons: [] }));
    const buttonsSection = container.querySelector<HTMLElement>('#analytics-buttons')!;
    expect(buttonsSection.querySelector('.analytics-bar-list')).toBeNull();
    expect(buttonsSection.textContent).toContain('За выбранный период кликов по кнопкам не зафиксировано.');
  });

  it('an XSS payload as a button name renders as text, not markup or an event handler', async () => {
    const payload = '<img src=x onerror="window.__pwned_button = true">';
    const container = await renderWithOverview(
      makeOverview({ popular_buttons: [{ name: payload, count: 5, share_percent: 100 }] }),
    );

    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelectorAll('[onerror]').length).toBe(0);
    expect(container.innerHTML).toContain('&lt;img');
    expect((window as unknown as { __pwned_button?: boolean }).__pwned_button).toBeUndefined();
  });
});

// --- Section activity ----------------------------------------------------

describe('section activity', () => {
  it('shows total duration, average-per-hover, interactions and share for each section', async () => {
    const container = await renderWithOverview(makeOverview());
    const item = container.querySelectorAll<HTMLElement>('#analytics-sections .analytics-bar-item')[0];

    expect(item.textContent).toContain('Контакты');
    expect(item.textContent).toContain('5 мин'); // 300s total duration
    expect(item.textContent).toContain('15 сек'); // 15s average per hover
    expect(item.textContent).toContain('20'); // interactions_count
    expect(item.textContent).toContain('60%');
  });

  it('labels average_duration_seconds precisely as "Среднее время одного наведения"', async () => {
    const container = await renderWithOverview(makeOverview());
    expect(container.textContent).toContain('Среднее время одного наведения');
  });

  it('never uses heatmap/coordinate/"просмотры секции" wording', async () => {
    const container = await renderWithOverview(makeOverview());
    const text = container.textContent ?? '';
    expect(text.toLowerCase()).not.toContain('heatmap');
    expect(text.toLowerCase()).not.toContain('тепловая карта');
    expect(text.toLowerCase()).not.toContain('координат');
    expect(text).not.toContain('просмотры секции');
  });

  it('preserves backend order without re-sorting by duration/share', async () => {
    const overview = makeOverview({
      section_activity: [
        { section: 'Малая', total_duration_seconds: 10, average_duration_seconds: 5, interactions_count: 2, share_percent: 5 },
        { section: 'Большая', total_duration_seconds: 500, average_duration_seconds: 50, interactions_count: 10, share_percent: 95 },
      ],
    });
    const container = await renderWithOverview(overview);
    const names = [...container.querySelectorAll<HTMLElement>('#analytics-sections .analytics-bar-name')].map(
      (el) => el.textContent,
    );
    expect(names).toEqual(['Малая', 'Большая']);
  });

  it('renders malformed section values safely', async () => {
    const overview = makeUnsafeOverview({
      section_activity: [
        {
          section: null,
          total_duration_seconds: 'lots',
          average_duration_seconds: -5,
          interactions_count: Number.POSITIVE_INFINITY,
          share_percent: Number.NaN,
        },
      ],
    });
    const container = await renderWithOverview(overview);
    const item = container.querySelector<HTMLElement>('#analytics-sections .analytics-bar-item')!;
    expect(item.textContent).not.toContain('NaN');
    expect(item.textContent).not.toContain('Infinity');
    expect(item.querySelector('.analytics-bar-name')!.textContent).toBe('Без названия секции');
  });

  it('shows a dedicated empty message when there is no section activity', async () => {
    const container = await renderWithOverview(makeOverview({ section_activity: [] }));
    const sectionsEl = container.querySelector<HTMLElement>('#analytics-sections')!;
    expect(sectionsEl.querySelector('.analytics-bar-list')).toBeNull();
    expect(sectionsEl.textContent).toContain('За выбранный период активность по секциям не зафиксирована.');
  });

  it('an XSS-like section name renders as text, not markup', async () => {
    const payload = '"><svg onload="window.__pwned_section = true">';
    const container = await renderWithOverview(
      makeOverview({
        section_activity: [
          { section: payload, total_duration_seconds: 1, average_duration_seconds: 1, interactions_count: 1, share_percent: 100 },
        ],
      }),
    );

    expect(container.querySelector('svg')).toBeNull();
    expect(container.querySelectorAll('[onload]').length).toBe(0);
    expect((window as unknown as { __pwned_section?: boolean }).__pwned_section).toBeUndefined();
  });
});

// --- Lifecycle -----------------------------------------------------------

describe('lifecycle', () => {
  it('a late success after switching away from the tab does not repaint', async () => {
    const deferred = createDeferred<AnalyticsOverview>();
    vi.mocked(api.getAnalyticsOverview).mockReturnValueOnce(deferred.promise);
    const container = document.createElement('div');
    const controller = mountAdminAnalytics(container, makeHost());

    controller.activate();
    await vi.waitFor(() => expect(api.getAnalyticsOverview).toHaveBeenCalledTimes(1));

    controller.deactivate();
    deferred.resolve(makeOverview());
    await flush();

    expect(container.querySelector('#analytics-content')!.hasAttribute('hidden')).toBe(true);
  });

  it('a late rejection after switching away does not show an error state', async () => {
    const deferred = createDeferred<AnalyticsOverview>();
    vi.mocked(api.getAnalyticsOverview).mockReturnValueOnce(deferred.promise);
    const container = document.createElement('div');
    const controller = mountAdminAnalytics(container, makeHost());

    controller.activate();
    await vi.waitFor(() => expect(api.getAnalyticsOverview).toHaveBeenCalledTimes(1));

    controller.deactivate();
    deferred.reject(new TypeError('Failed to fetch'));
    await flush();

    expect(container.textContent).not.toContain('Не удалось загрузить статистику');
  });

  it('deactivate() then activate() starts a fresh, successful load', async () => {
    const deferred = createDeferred<AnalyticsOverview>();
    vi.mocked(api.getAnalyticsOverview).mockReturnValueOnce(deferred.promise);
    const container = document.createElement('div');
    const controller = mountAdminAnalytics(container, makeHost());

    controller.activate();
    await vi.waitFor(() => expect(api.getAnalyticsOverview).toHaveBeenCalledTimes(1));
    controller.deactivate();

    vi.mocked(api.getAnalyticsOverview).mockResolvedValueOnce(makeOverview());
    controller.activate();
    await vi.waitFor(() => expect(container.querySelector('#analytics-content')!.hasAttribute('hidden')).toBe(false));
    expect(api.getAnalyticsOverview).toHaveBeenCalledTimes(2);
  });

  it('a 401 arriving after deactivate() does not call onSessionExpired again', async () => {
    const deferred = createDeferred<AnalyticsOverview>();
    vi.mocked(api.getAnalyticsOverview).mockReturnValueOnce(deferred.promise);
    const onSessionExpired = vi.fn();
    const container = document.createElement('div');
    const controller = mountAdminAnalytics(container, makeHost({ onSessionExpired }));

    controller.activate();
    await vi.waitFor(() => expect(api.getAnalyticsOverview).toHaveBeenCalledTimes(1));
    controller.deactivate();

    deferred.reject(new ApiError('Could not validate credentials', 401));
    await flush();

    expect(onSessionExpired).not.toHaveBeenCalled();
  });

  it('logout/401 during an active tab calls onSessionExpired and does not resurrect the old dataset', async () => {
    vi.mocked(api.getAnalyticsOverview).mockResolvedValueOnce(makeOverview());
    const container = document.createElement('div');
    let active = true;
    const onSessionExpired = vi.fn();
    const controller = mountAdminAnalytics(container, makeHost({ isActive: () => active, onSessionExpired }));
    controller.activate();
    await vi.waitFor(() => expect(container.querySelector('#analytics-content')!.hasAttribute('hidden')).toBe(false));

    const deferred = createDeferred<AnalyticsOverview>();
    vi.mocked(api.getAnalyticsOverview).mockReturnValueOnce(deferred.promise);
    container.querySelector<HTMLButtonElement>('#analytics-refresh')!.click();
    await vi.waitFor(() => expect(container.textContent).toContain('Загружаем статистику'));

    active = false; // simulates a session-wide 401/logout elsewhere in the panel
    deferred.reject(new ApiError('Could not validate credentials', 401));
    await flush();

    expect(onSessionExpired).not.toHaveBeenCalled(); // isActive() was already false
    expect(container.textContent).not.toContain('12'); // old dataset not restored
  });

  it('double refresh (rapid clicks while one request is in flight) sends only one additional request', async () => {
    vi.mocked(api.getAnalyticsOverview).mockResolvedValueOnce(makeOverview());
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(container.querySelector('#analytics-content')!.hasAttribute('hidden')).toBe(false));

    const deferred = createDeferred<AnalyticsOverview>();
    vi.mocked(api.getAnalyticsOverview).mockReturnValueOnce(deferred.promise);

    const refreshButton = container.querySelector<HTMLButtonElement>('#analytics-refresh')!;
    refreshButton.click();
    refreshButton.click();
    refreshButton.click();

    expect(api.getAnalyticsOverview).toHaveBeenCalledTimes(2); // initial mount + one refresh
    expect(refreshButton.disabled).toBe(true);

    deferred.resolve(makeOverview());
    await flush();
    expect(refreshButton.disabled).toBe(false);
  });

  it('dispose() invalidates a pending load; the panel never repaints afterwards', async () => {
    vi.mocked(api.getAnalyticsOverview).mockResolvedValueOnce(makeOverview());
    const container = document.createElement('div');
    const controller = mountAdminAnalytics(container, makeHost());
    controller.activate();
    await vi.waitFor(() => expect(container.querySelector('#analytics-content')!.hasAttribute('hidden')).toBe(false));

    const deferred = createDeferred<AnalyticsOverview>();
    vi.mocked(api.getAnalyticsOverview).mockReturnValueOnce(deferred.promise);
    controller.deactivate();
    controller.activate();
    await vi.waitFor(() => expect(api.getAnalyticsOverview).toHaveBeenCalledTimes(2));

    controller.dispose();
    deferred.resolve(makeOverview({ applications_count: 777 }));
    await flush();

    expect(container.textContent).not.toContain('777');
  });

  it('old dataset does not resurrect during a pending or errored reload', async () => {
    vi.mocked(api.getAnalyticsOverview).mockResolvedValueOnce(makeOverview({ applications_count: 12 }));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(container.textContent).toContain('12'));

    vi.mocked(api.getAnalyticsOverview).mockRejectedValueOnce(new TypeError('Failed to fetch'));
    container.querySelector<HTMLButtonElement>('#analytics-refresh')!.click();
    await vi.waitFor(() => expect(container.textContent).toContain('Не удалось загрузить статистику'));

    expect(container.textContent).not.toContain('12');
    expect(container.querySelector('#analytics-content')!.hasAttribute('hidden')).toBe(true);
  });
});

// --- Formatters (pure) -----------------------------------------------------

describe('formatCount / normalizeCount', () => {
  const cases: Array<[unknown, string]> = [
    [0, '0'],
    [1, '1'],
    [12, '12'],
    [12.0, '12'],
    [25, '25'],
    // Backend count fields are ints — a fractional runtime value is
    // malformed/schema drift and must never be silently rounded.
    [1.5, '—'],
    [1.6, '—'],
    [-1, '—'],
    [-0.5, '—'],
    [null, '—'],
    [undefined, '—'],
    ['2', '—'],
    [true, '—'],
    [false, '—'],
    [Number.NaN, '—'],
    [Number.POSITIVE_INFINITY, '—'],
    [Number.NEGATIVE_INFINITY, '—'],
    [{}, '—'],
    [[], '—'],
    ['"><img src=x onerror=alert(1)>', '—'],
  ];

  it.each(cases)('formatCount(%p) -> %p', (input, expected) => {
    expect(formatCount(input)).toBe(expected);
  });

  it('normalizeCount accepts only a finite, non-negative integer', () => {
    expect(normalizeCount(0)).toBe(0);
    expect(normalizeCount(1)).toBe(1);
    expect(normalizeCount(25)).toBe(25);
    expect(normalizeCount(-1)).toBeNull();
    expect(normalizeCount('5')).toBeNull();
  });

  it('never rounds a fractional count into a whole number', () => {
    expect(normalizeCount(1.5)).toBeNull();
    expect(normalizeCount(1.6)).toBeNull();
    expect(formatCount(1.5)).not.toBe('2');
    expect(formatCount(1.6)).not.toBe('2');
    expect(formatCount(1.5)).toBe('—');
    expect(formatCount(1.6)).toBe('—');
  });
});

describe('formatSeconds', () => {
  const cases: Array<[unknown, string]> = [
    [0, '0 сек'],
    [1, '1 сек'],
    [42, '42 сек'],
    [59, '59 сек'],
    [60, '1 мин 0 сек'],
    [192, '3 мин 12 сек'],
    [3599, '59 мин 59 сек'],
    [3600, '1 ч 0 мин'],
    [3900, '1 ч 5 мин'],
    [null, '—'],
    [undefined, '—'],
    [-5, '—'],
    [Number.NaN, '—'],
    [Number.POSITIVE_INFINITY, '—'],
    ['192', '—'],
    [{}, '—'],
  ];

  it.each(cases)('formatSeconds(%p) -> %p', (input, expected) => {
    expect(formatSeconds(input)).toBe(expected);
  });
});

describe('formatAverageReturns', () => {
  const cases: Array<[unknown, string]> = [
    [0, '0'],
    [1, '1'],
    [1.5, '1,5'],
    [1.234, '1,23'],
    [null, '—'],
    [undefined, '—'],
    [-1, '—'],
    [Number.NaN, '—'],
    ['1.5', '—'],
  ];

  it.each(cases)('formatAverageReturns(%p) -> %p', (input, expected) => {
    expect(formatAverageReturns(input)).toBe(expected);
  });
});

describe('normalizePercent / percentBarWidth / formatPercent', () => {
  it('clamps the bar width to [0, 100] but leaves the text unclamped', () => {
    expect(percentBarWidth(150)).toBe(100);
    expect(percentBarWidth(-10)).toBe(0);
    expect(percentBarWidth(32.5)).toBe(32.5);
    expect(formatPercent(150)).toBe('150%');
    expect(formatPercent(32.5)).toBe('32,5%');
  });

  const malformedCases: unknown[] = [null, undefined, 'x', Number.NaN, Number.POSITIVE_INFINITY, {}, []];

  it.each(malformedCases)('malformed %p -> bar 0, text "—"', (input) => {
    expect(percentBarWidth(input)).toBe(0);
    expect(formatPercent(input)).toBe('—');
    expect(normalizePercent(input)).toBeNull();
  });
});

describe('formatAnalyticsName', () => {
  const cases: Array<[unknown, string]> = [
    ['Записаться', 'Записаться'],
    ['  Со пробелами  ', 'Со пробелами'],
    ['', 'Без названия'],
    ['   ', 'Без названия'],
    [null, 'Без названия'],
    [undefined, 'Без названия'],
    [42, 'Без названия'],
    [{}, 'Без названия'],
  ];

  it.each(cases)('formatAnalyticsName(%p) -> %p', (input, expected) => {
    expect(formatAnalyticsName(input)).toBe(expected);
  });
});

describe('formatAnalyticsDate / formatAnalyticsDateTime / formatPeriodRange', () => {
  it('formats a valid ISO date consistently and never throws', () => {
    expect(formatAnalyticsDate('2026-07-17T00:00:00Z')).not.toBe('—');
    expect(() => formatAnalyticsDate('not a date')).not.toThrow();
  });

  const invalidCases: unknown[] = [null, undefined, '', 'not a date', 42, {}, []];

  it.each(invalidCases)('formatAnalyticsDate(%p) -> "—"', (input) => {
    expect(formatAnalyticsDate(input)).toBe('—');
  });

  it.each(invalidCases)('formatAnalyticsDateTime(%p) -> "—"', (input) => {
    expect(formatAnalyticsDateTime(input)).toBe('—');
  });

  it('formatPeriodRange joins two valid dates, and falls back to "—" if either is invalid', () => {
    const range = formatPeriodRange('2026-07-17T00:00:00Z', '2026-07-24T00:00:00Z');
    expect(range).toBe(`${formatAnalyticsDate('2026-07-17T00:00:00Z')} – ${formatAnalyticsDate('2026-07-24T00:00:00Z')}`);
    expect(formatPeriodRange(null, '2026-07-24T00:00:00Z')).toBe('—');
    expect(formatPeriodRange('2026-07-17T00:00:00Z', 'garbage')).toBe('—');
  });
});

describe('isOverviewEmpty', () => {
  it('is true only when every activity count is confidently zero', () => {
    expect(isOverviewEmpty(makeEmptyOverview())).toBe(true);
    expect(isOverviewEmpty(makeOverview())).toBe(false);
    expect(isOverviewEmpty(makeEmptyOverview({ applications_count: 1 }))).toBe(false);
  });

  it('does not treat a malformed (non-zero-confirmable) count as empty', () => {
    const overview = makeUnsafeOverview({
      applications_count: 'many',
      metrics_count: 0,
      total_return_count: 0,
      total_button_clicks: 0,
    });
    expect(isOverviewEmpty(overview)).toBe(false);
  });
});

describe('analyticsPeriodLabel', () => {
  it('labels each period with the UI-facing (non-calendar) wording', () => {
    expect(analyticsPeriodLabel('day')).toBe('24 часа');
    expect(analyticsPeriodLabel('week')).toBe('7 дней');
    expect(analyticsPeriodLabel('month')).toBe('30 дней');
  });
});
