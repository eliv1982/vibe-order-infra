// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/client';
import type {
  ApplicationBehaviorAnalytics,
  ApplicationPriorityRead,
  ApplicationRead,
  PrioritizedApplicationList,
} from '../api/types';
import { formatBudget } from '../utils/format';
import { formatAnalyticsDateTime } from './adminAnalytics';
import {
  buildPrioritizedQuery,
  formatApplicationBudget,
  formatApplicationDate,
  fullName,
  hasActiveCriteria,
  isKnownPriorityLevel,
  mountAdminApplications,
  normalizePriorityScore,
  normalizeReasonPoints,
  priorityLevelFullLabel,
  priorityLevelLabel,
  priorityScoreDisplay,
  reasonPointsDisplay,
  type ApplicationsSectionController,
  type ApplicationsSectionHost,
} from './adminApplications';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    api: {
      getPrioritizedApplications: vi.fn(),
      getApplicationBehaviorAnalytics: vi.fn(),
    },
  };
});

import { api } from '../api/client';

let nextId = 1;

function makeApplication(overrides: Partial<ApplicationRead> = {}): ApplicationRead {
  const id = overrides.id ?? nextId++;
  return {
    id,
    first_name: 'Иван',
    last_name: 'Петров',
    middle_name: null,
    contact_data: '+7 900 000-00-00',
    business_niche: 'Личный автомобиль',
    company_size: 'Седан или универсал',
    business_info: 'BMW X5, 2019, требуется полировка',
    task_scope: 'Разовая услуга',
    requester_role: 'Владелец автомобиля',
    business_size: 'Один автомобиль',
    need_scope: 'Хочу восстановить блеск кузова',
    deadline: 'В течение недели',
    task_type: 'Восстановление внешнего вида',
    service_id: 1,
    interested_product: 'Полировка кузова',
    budget: '25000.00',
    preferred_contact_method: 'Телефон',
    preferred_contact_time: 'Утро (9:00–12:00)',
    comment: null,
    created_at: '2026-01-10T09:00:00Z',
    updated_at: '2026-01-10T09:00:00Z',
    ...overrides,
  };
}

function makeItem(overrides: Partial<ApplicationPriorityRead> = {}): ApplicationPriorityRead {
  return {
    application: makeApplication(),
    priority_score: 55,
    priority_level: 'medium',
    priority_label: 'Средняя',
    reasons: [
      { code: 'deadline', points: 12, label: 'Срок записи: В течение недели' },
      { code: 'budget', points: 15, label: 'Бюджет от 25 000 ₽' },
    ],
    recommended_action: 'Связаться сегодня',
    recommended_team: 'Специалист детейлинга',
    requires_personal_manager: false,
    ...overrides,
  };
}

/** Bypasses makeItem's typed overrides on purpose — used only to build
 * malformed/hostile wire fixtures (e.g. priority_score as a string) that
 * TypeScript would otherwise reject, mirroring what an actual malformed
 * backend/proxy response would look like at runtime. */
function makeUnsafeItem(overrides: Record<string, unknown>): ApplicationPriorityRead {
  return { ...makeItem(), ...overrides } as unknown as ApplicationPriorityRead;
}

function makeList(items: ApplicationPriorityRead[]): PrioritizedApplicationList {
  return { items, total: items.length, skip: 0, limit: 100 };
}

/** Like makeList, but for a page that is part of a larger total - the shape
 * a real multi-page GET /applications/prioritized response has (see
 * PrioritizedApplicationList, api/types.ts). */
function makeListPage(
  items: ApplicationPriorityRead[],
  overrides: Partial<Pick<PrioritizedApplicationList, 'total' | 'skip'>> = {},
): PrioritizedApplicationList {
  return { items, total: items.length, skip: 0, limit: 100, ...overrides };
}

function makeAnalyticsDetail(overrides: Partial<ApplicationBehaviorAnalytics> = {}): ApplicationBehaviorAnalytics {
  return {
    application_id: 1,
    has_metrics: false,
    time_on_page_seconds: null,
    return_count: null,
    clicked_buttons: [],
    section_activity: [],
    total_button_clicks: 0,
    recorded_at: null,
    ...overrides,
  };
}

/** Bypasses ApplicationBehaviorAnalytics's typed fields on purpose — builds
 * a malformed/hostile wire fixture, mirroring makeUnsafeItem below. */
function makeUnsafeAnalyticsDetail(overrides: Record<string, unknown>): ApplicationBehaviorAnalytics {
  return { ...makeAnalyticsDetail(), has_metrics: true, ...overrides } as unknown as ApplicationBehaviorAnalytics;
}

function makeHost(overrides: Partial<ApplicationsSectionHost> = {}): ApplicationsSectionHost {
  return {
    isActive: () => true,
    onSessionExpired: vi.fn(),
    ...overrides,
  };
}

/** Mounts and immediately activates — the steady-state most tests care
 * about (equivalent to what admin.ts does the first time the admin opens
 * the "Заявки" tab). */
function mountAndActivate(
  container: HTMLElement,
  host: ApplicationsSectionHost,
): ApplicationsSectionController {
  const controller = mountAdminApplications(container, host);
  controller.activate();
  return controller;
}

async function renderWithItems(
  items: ApplicationPriorityRead[],
  host: ApplicationsSectionHost = makeHost(),
): Promise<HTMLElement> {
  vi.mocked(api.getPrioritizedApplications).mockResolvedValue(makeList(items));
  const container = document.createElement('div');
  mountAndActivate(container, host);
  await vi.waitFor(() => expect(container.querySelectorAll('.application-card').length).toBe(items.length));
  return container;
}

/** A controllable promise for deterministic "slow request resolves late"
 * tests, mirroring admin.test.ts's own helper. */
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
  nextId = 1;
  // Most tests don't care about the modal's lazily-loaded behavior detail —
  // give it a harmless default so opening the modal never leaves an
  // unhandled rejection lying around; tests that do care override this.
  vi.mocked(api.getApplicationBehaviorAnalytics).mockResolvedValue(makeAnalyticsDetail());
});

// --- API ---------------------------------------------------------------

describe('loading applications', () => {
  it('requests prioritized applications with skip=0, limit=100 on mount', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(makeList([]));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());

    await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenCalledWith(0, 100, {}));
  });

  it('a 401 calls host.onSessionExpired instead of rendering an error banner', async () => {
    vi.mocked(api.getPrioritizedApplications).mockRejectedValue(
      new ApiError('Could not validate credentials', 401),
    );
    const onSessionExpired = vi.fn();
    const container = document.createElement('div');
    mountAndActivate(container, makeHost({ onSessionExpired }));

    await vi.waitFor(() => expect(onSessionExpired).toHaveBeenCalledTimes(1));
    expect(container.textContent).not.toContain('Не удалось загрузить');
  });

  it('a non-401 failure shows a neutral error message and does not call onSessionExpired', async () => {
    vi.mocked(api.getPrioritizedApplications).mockRejectedValue(new TypeError('Failed to fetch'));
    const onSessionExpired = vi.fn();
    const container = document.createElement('div');
    mountAndActivate(container, makeHost({ onSessionExpired }));

    await vi.waitFor(() => expect(container.textContent).toContain('Не удалось загрузить'));
    expect(onSessionExpired).not.toHaveBeenCalled();
  });
});

// --- Pagination ------------------------------------------------------------
// Stage 4: the admin panel used to always request skip=0/limit=100 and
// never let the admin reach anything beyond the first 100 applications,
// even though the backend already returned enough (skip/total/limit) to
// page through the rest. These tests exercise the Prev/Next controls added
// for that (see renderPager/wirePager in adminApplications.ts).

describe('pagination', () => {
  it('shows the current range and total once a page loads', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(
      makeListPage([makeItem()], { total: 250, skip: 0 }),
    );
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());

    await vi.waitFor(() => expect(container.textContent).toContain('Заявки 1–1 из 250'));
  });

  it('disables "Назад" on the first page and enables "Далее" when more rows remain', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(
      makeListPage(Array.from({ length: 100 }, () => makeItem()), { total: 250, skip: 0 }),
    );
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());

    await vi.waitFor(() => expect(container.textContent).toContain('Заявки 1–100 из 250'));
    expect(container.querySelector<HTMLButtonElement>('#applications-prev')!.disabled).toBe(true);
    expect(container.querySelector<HTMLButtonElement>('#applications-next')!.disabled).toBe(false);
  });

  it('disables both Prev and Next when every row already fits on one page', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(makeList([makeItem(), makeItem()]));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());

    await vi.waitFor(() => expect(container.textContent).toContain('Заявки 1–2 из 2'));
    expect(container.querySelector<HTMLButtonElement>('#applications-prev')!.disabled).toBe(true);
    expect(container.querySelector<HTMLButtonElement>('#applications-next')!.disabled).toBe(true);
  });

  it('clicking "Далее" requests the next page with skip advanced by the page size', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(
      makeListPage(Array.from({ length: 100 }, () => makeItem()), { total: 250, skip: 0 }),
    );
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(container.textContent).toContain('Заявки 1–100 из 250'));

    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(
      makeListPage(Array.from({ length: 100 }, () => makeItem()), { total: 250, skip: 100 }),
    );
    container.querySelector<HTMLButtonElement>('#applications-next')!.click();

    await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenLastCalledWith(100, 100, {}));
    await vi.waitFor(() => expect(container.textContent).toContain('Заявки 101–200 из 250'));
    expect(container.querySelector<HTMLButtonElement>('#applications-prev')!.disabled).toBe(false);
  });

  it('clicking "Назад" from the second page returns to skip=0', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(
      makeListPage(Array.from({ length: 100 }, () => makeItem()), { total: 250, skip: 100 }),
    );
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(container.textContent).toContain('из 250'));

    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(
      makeListPage(Array.from({ length: 100 }, () => makeItem()), { total: 250, skip: 0 }),
    );
    container.querySelector<HTMLButtonElement>('#applications-prev')!.click();

    await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenLastCalledWith(0, 100, {}));
  });

  it('the pager is hidden while a page is loading', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(
      makeListPage(Array.from({ length: 100 }, () => makeItem()), { total: 250, skip: 0 }),
    );
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(container.textContent).toContain('из 250'));

    vi.mocked(api.getPrioritizedApplications).mockReturnValue(new Promise(() => {}));
    container.querySelector<HTMLButtonElement>('#applications-next')!.click();

    await vi.waitFor(() => expect(container.textContent).not.toContain('из 250'));
  });

  it('re-activating the tab (switching back to "Заявки") returns to the first page', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(
      makeListPage(Array.from({ length: 100 }, () => makeItem()), { total: 250, skip: 100 }),
    );
    const container = document.createElement('div');
    const controller = mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(container.textContent).toContain('из 250'));

    controller.deactivate();
    vi.mocked(api.getPrioritizedApplications).mockClear();
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(
      makeListPage(Array.from({ length: 100 }, () => makeItem()), { total: 250, skip: 0 }),
    );
    controller.activate();

    await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenCalledWith(0, 100, {}));
  });
});

// --- Rendering -----------------------------------------------------------

describe('rendering', () => {
  it('shows a loading state before the request resolves', () => {
    vi.mocked(api.getPrioritizedApplications).mockReturnValue(new Promise(() => {}));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());

    expect(container.textContent).toContain('Загружаем заявки');
  });

  it('shows an empty state when there are no applications', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(makeList([]));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());

    await vi.waitFor(() => expect(container.textContent).toContain('Заявок пока нет'));
  });

  it('renders cards in the exact backend-supplied order, without re-sorting locally', async () => {
    const items = [
      makeItem({
        application: makeApplication({ id: 1, first_name: 'Первый', last_name: '' }),
        priority_score: 10,
      }),
      makeItem({
        application: makeApplication({ id: 2, first_name: 'Второй', last_name: '' }),
        priority_score: 90,
      }),
      makeItem({
        application: makeApplication({ id: 3, first_name: 'Третий', last_name: '' }),
        priority_score: 50,
      }),
    ];
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(makeList(items));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());

    await vi.waitFor(() => expect(container.querySelectorAll('.application-card').length).toBe(3));
    const names = [...container.querySelectorAll<HTMLElement>('.application-card')].map(
      (card) => card.querySelector('h3')!.textContent,
    );
    expect(names).toEqual(['Первый', 'Второй', 'Третий']);
  });

  it('shows the Высокий/Средний/Стандартный priority label as text, not just a color, regardless of what backend priority_label says', async () => {
    const items = [
      // priority_label deliberately holds the old, no-longer-shown wording —
      // the badge must be driven entirely by priority_level (see
      // requirement 6 / priorityLevelLabel), never by this backend string.
      makeItem({ priority_level: 'hot', priority_label: 'Горячая' }),
      makeItem({ application: makeApplication(), priority_level: 'medium', priority_label: 'Средняя' }),
      makeItem({ application: makeApplication(), priority_level: 'low', priority_label: 'Низкая' }),
    ];
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(makeList(items));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());

    await vi.waitFor(() => expect(container.querySelectorAll('.application-card').length).toBe(3));
    expect(container.textContent).toContain('Высокий');
    expect(container.textContent).toContain('Средний');
    expect(container.textContent).toContain('Стандартный');
    // The old sales-lead-"temperature" wording must never appear in the UI.
    expect(container.textContent).not.toContain('Горячая');
    expect(container.textContent).not.toContain('Низкая');
  });

  it('shows Приоритет обработки context via the badge\'s accessible name (aria-label), not just the short badge text', async () => {
    const items = [
      makeItem({ application: makeApplication({ id: 1 }), priority_level: 'hot' }),
      makeItem({ application: makeApplication({ id: 2 }), priority_level: 'medium' }),
      makeItem({ application: makeApplication({ id: 3 }), priority_level: 'low' }),
    ];
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(makeList(items));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());

    await vi.waitFor(() => expect(container.querySelectorAll('.application-card').length).toBe(3));
    const badges = [...container.querySelectorAll<HTMLElement>('.priority-badge')];
    expect(badges.map((b) => b.getAttribute('aria-label'))).toEqual([
      'Высокий приоритет обработки',
      'Средний приоритет обработки',
      'Стандартный приоритет обработки',
    ]);
  });

  it('shows the score as "N/100"', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(
      makeList([makeItem({ priority_score: 82 })]),
    );
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());

    await vi.waitFor(() => expect(container.textContent).toContain('82/100'));
  });

  it('shows recommended_action and recommended_team', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(
      makeList([
        makeItem({
          recommended_action: 'Связаться в течение часа',
          recommended_team: 'Менеджер автопарков',
        }),
      ]),
    );
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());

    await vi.waitFor(() => expect(container.textContent).toContain('Связаться в течение часа'));
    expect(container.textContent).toContain('Менеджер автопарков');
  });

  it('shows a personal-manager indicator only when requires_personal_manager is true', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(
      makeList([
        makeItem({
          application: makeApplication({ id: 1 }),
          requires_personal_manager: true,
        }),
        makeItem({
          application: makeApplication({ id: 2 }),
          requires_personal_manager: false,
        }),
      ]),
    );
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());

    await vi.waitFor(() => expect(container.querySelectorAll('.application-card').length).toBe(2));
    const cards = [...container.querySelectorAll<HTMLElement>('.application-card')];
    expect(cards[0].querySelector('.application-card-pm-flag')).not.toBeNull();
    expect(cards[1].querySelector('.application-card-pm-flag')).toBeNull();
  });

  it('formats budget and date for display', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(
      makeList([
        makeItem({
          application: makeApplication({ budget: '150000.00', created_at: '2026-03-05T10:00:00Z' }),
        }),
      ]),
    );
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());

    await vi.waitFor(() => expect(container.querySelector('.application-card')).not.toBeNull());
    expect(container.textContent).toContain('150');
    expect(container.textContent).toContain('₽');
    expect(container.textContent).toContain(formatApplicationDate('2026-03-05T10:00:00Z'));
  });
});

// --- Filtering ---------------------------------------------------------
// Stage 4 correction: search/priority are no longer filtered client-side
// against whichever page happened to already be loaded (the independently
// audited defect - a match on a later backend page was invisible from page
// 1, and a real corpus-wide zero was indistinguishable from that). Both
// criteria are now sent to the backend as query params on every
// GET /applications/prioritized call (see buildPrioritizedQuery), and
// `state.items` is rendered as-is - whatever the backend already filtered
// and paginated.

/** Longer than adminApplications.ts's internal SEARCH_DEBOUNCE_MS (350ms,
 * not exported) - real timers, matching this file's existing async style
 * (see `flush` above), rather than introducing fake timers just for this
 * describe block. */
async function waitForSearchDebounce(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 400));
}

describe('buildPrioritizedQuery / hasActiveCriteria (pure)', () => {
  it('omits both params when there is no active criterion', () => {
    expect(buildPrioritizedQuery('all', '')).toEqual({});
    expect(buildPrioritizedQuery('all', '   ')).toEqual({});
    expect(hasActiveCriteria('all', '')).toBe(false);
    expect(hasActiveCriteria('all', '   ')).toBe(false);
  });

  it('sends a trimmed search and no priority when only search is active', () => {
    expect(buildPrioritizedQuery('all', '  Смирнова  ')).toEqual({ search: 'Смирнова' });
    expect(hasActiveCriteria('all', '  Смирнова  ')).toBe(true);
  });

  it('sends a priority level and no search when only a chip is active', () => {
    expect(buildPrioritizedQuery('hot', '')).toEqual({ priority: 'hot' });
    expect(hasActiveCriteria('hot', '')).toBe(true);
  });

  it('sends both when search and a priority chip are both active', () => {
    expect(buildPrioritizedQuery('medium', 'Кузнецов')).toEqual({
      search: 'Кузнецов',
      priority: 'medium',
    });
  });
});

describe('filtering — DOM', () => {
  it('filter chips read Все/Высокий/Средний/Стандартный, never the old hot/low wording', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(makeList([]));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(container.textContent).toContain('Заявок пока нет'));

    const filterTexts = [...container.querySelectorAll<HTMLElement>('.filter-chip')].map((btn) =>
      btn.textContent?.trim(),
    );
    expect(filterTexts).toEqual(['Все', 'Высокий', 'Средний', 'Стандартный']);
    expect(container.textContent).not.toContain('Горячие');
    expect(container.textContent).not.toContain('Низкие');
    expect(container.textContent).not.toContain('Средние');
  });

  it('clicking a priority chip resets to the first page and sends priority to the backend', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValueOnce(
      makeListPage(Array.from({ length: 100 }, () => makeItem()), { total: 250, skip: 100 }),
    );
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(container.textContent).toContain('из 250'));
    // Not on the first page any more (skip=100) - the chip click below must
    // still reset to skip=0, not just narrow whatever page is currently shown.
    container.querySelector<HTMLButtonElement>('#applications-next')!.click();
    await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(2));

    const hotItems = [
      makeItem({ application: makeApplication({ id: 1 }), priority_level: 'hot' }),
    ];
    vi.mocked(api.getPrioritizedApplications).mockResolvedValueOnce(makeList(hotItems));
    container.querySelector<HTMLButtonElement>('[data-filter="hot"]')!.click();

    await vi.waitFor(() =>
      expect(api.getPrioritizedApplications).toHaveBeenLastCalledWith(0, 100, { priority: 'hot' }),
    );
    await vi.waitFor(() => expect(container.querySelectorAll('.application-card').length).toBe(1));
  });

  it('clicking an already-active chip does not send a redundant request', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(makeList([]));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(1));

    container.querySelector<HTMLButtonElement>('[data-filter="all"]')!.click();
    await flush();
    expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(1);
  });

  it('typing in the search box debounces, resets to the first page, and sends search to the backend', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValueOnce(
      makeListPage(Array.from({ length: 100 }, () => makeItem()), { total: 250, skip: 100 }),
    );
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(container.textContent).toContain('из 250'));
    container.querySelector<HTMLButtonElement>('#applications-next')!.click();
    await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(2));

    const matches = [makeItem({ application: makeApplication({ id: 1, first_name: 'Анна' }) })];
    vi.mocked(api.getPrioritizedApplications).mockResolvedValueOnce(makeList(matches));
    const searchInput = container.querySelector<HTMLInputElement>('#applications-search')!;
    searchInput.value = 'Анна';
    searchInput.dispatchEvent(new Event('input', { bubbles: true }));

    // No request yet - still debouncing.
    expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(2);

    await waitForSearchDebounce();

    expect(api.getPrioritizedApplications).toHaveBeenLastCalledWith(0, 100, { search: 'Анна' });
    await vi.waitFor(() => expect(container.querySelectorAll('.application-card').length).toBe(1));
  });

  it('rapid typing sends exactly one debounced request, carrying only the final value', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(makeList([]));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(1));

    const searchInput = container.querySelector<HTMLInputElement>('#applications-search')!;
    for (const value of ['А', 'Ан', 'Анн', 'Анна']) {
      searchInput.value = value;
      searchInput.dispatchEvent(new Event('input', { bubbles: true }));
    }

    await waitForSearchDebounce();

    expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(2); // mount + exactly one debounced call
    expect(api.getPrioritizedApplications).toHaveBeenLastCalledWith(0, 100, { search: 'Анна' });
  });

  it('sends both search and priority together once both are set', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(makeList([]));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(1));

    container.querySelector<HTMLButtonElement>('[data-filter="medium"]')!.click();
    await vi.waitFor(() =>
      expect(api.getPrioritizedApplications).toHaveBeenLastCalledWith(0, 100, { priority: 'medium' }),
    );

    const searchInput = container.querySelector<HTMLInputElement>('#applications-search')!;
    searchInput.value = 'Кузнецов';
    searchInput.dispatchEvent(new Event('input', { bubbles: true }));
    await waitForSearchDebounce();

    expect(api.getPrioritizedApplications).toHaveBeenLastCalledWith(0, 100, {
      search: 'Кузнецов',
      priority: 'medium',
    });
  });

  it('a slower response for an older search value never overwrites a newer one', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValueOnce(makeList([]));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(1));

    const staleDeferred = createDeferred<PrioritizedApplicationList>();
    vi.mocked(api.getPrioritizedApplications).mockReturnValueOnce(staleDeferred.promise);
    const searchInput = container.querySelector<HTMLInputElement>('#applications-search')!;
    searchInput.value = 'Старый';
    searchInput.dispatchEvent(new Event('input', { bubbles: true }));
    await waitForSearchDebounce();
    await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(2));

    // A second, newer search fires (and resolves) before the stale one does.
    const freshItems = [makeItem({ application: makeApplication({ id: 9, first_name: 'Новый' }) })];
    vi.mocked(api.getPrioritizedApplications).mockResolvedValueOnce(makeList(freshItems));
    searchInput.value = 'Новый';
    searchInput.dispatchEvent(new Event('input', { bubbles: true }));
    await waitForSearchDebounce();
    await vi.waitFor(() => expect(container.querySelectorAll('.application-card').length).toBe(1));
    expect(container.textContent).toContain('Новый');

    // The stale "Старый" response now arrives late - must not replace the
    // already-rendered "Новый" result.
    staleDeferred.resolve(
      makeList([makeItem({ application: makeApplication({ id: 1, first_name: 'НеДолжноПоявиться' }) })]),
    );
    await flush();

    expect(container.textContent).toContain('Новый');
    expect(container.textContent).not.toContain('НеДолжноПоявиться');
  });

  it('empty state is "Ничего не найдено" only when the backend reports a genuine zero for active criteria', async () => {
    const items = [makeItem({ application: makeApplication({ id: 1, first_name: 'Анна' }) })];
    vi.mocked(api.getPrioritizedApplications).mockResolvedValueOnce(makeList(items));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(container.querySelectorAll('.application-card').length).toBe(1));
    expect(container.textContent).not.toContain('Ничего не найдено');

    // Backend reports zero matches for the active search - never a locally
    // computed "no card on this page" result.
    vi.mocked(api.getPrioritizedApplications).mockResolvedValueOnce(
      makeListPage([], { total: 0, skip: 0 }),
    );
    const searchInput = container.querySelector<HTMLInputElement>('#applications-search')!;
    searchInput.value = 'нет такого клиента';
    searchInput.dispatchEvent(new Event('input', { bubbles: true }));
    await waitForSearchDebounce();

    await vi.waitFor(() => expect(container.querySelectorAll('.application-card').length).toBe(0));
    expect(container.textContent).toContain('Ничего не найдено');
    expect(container.textContent).not.toContain('Заявок пока нет');
  });

  it('"Заявок пока нет" (not "Ничего не найдено") when there is no active criterion and the corpus is empty', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(makeListPage([], { total: 0, skip: 0 }));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());

    await vi.waitFor(() => expect(container.textContent).toContain('Заявок пока нет'));
    expect(container.textContent).not.toContain('Ничего не найдено');
  });

  it('shows the backend-reported filtered total, not a locally-counted subset', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValueOnce(makeList([]));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(1));

    // 17 total matches across the whole (250-row) corpus, only 1 of which
    // happens to be on this page - the reproduction of the independent
    // audit's exact scenario (17 matches across 101+ applications).
    vi.mocked(api.getPrioritizedApplications).mockResolvedValueOnce(
      makeListPage([makeItem({ application: makeApplication({ id: 1, first_name: 'Виктор' }) })], {
        total: 17,
        skip: 0,
      }),
    );
    const searchInput = container.querySelector<HTMLInputElement>('#applications-search')!;
    searchInput.value = 'Виктор';
    searchInput.dispatchEvent(new Event('input', { bubbles: true }));
    await waitForSearchDebounce();

    await vi.waitFor(() => expect(container.textContent).toContain('Найдено: 17'));
    expect(container.textContent).toContain('Заявки 1–1 из 17');
  });

  it('the pager preserves the active search/priority criteria across Next/Prev', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValueOnce(makeList([]));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(1));

    vi.mocked(api.getPrioritizedApplications).mockResolvedValueOnce(
      makeListPage(Array.from({ length: 100 }, () => makeItem()), { total: 150, skip: 0 }),
    );
    container.querySelector<HTMLButtonElement>('[data-filter="hot"]')!.click();
    await vi.waitFor(() =>
      expect(api.getPrioritizedApplications).toHaveBeenLastCalledWith(0, 100, { priority: 'hot' }),
    );

    vi.mocked(api.getPrioritizedApplications).mockResolvedValueOnce(
      makeListPage(Array.from({ length: 50 }, () => makeItem()), { total: 150, skip: 100 }),
    );
    container.querySelector<HTMLButtonElement>('#applications-next')!.click();

    await vi.waitFor(() =>
      expect(api.getPrioritizedApplications).toHaveBeenLastCalledWith(100, 100, { priority: 'hot' }),
    );
  });
});

// --- Modal -----------------------------------------------------------------

describe('detail modal', () => {
  it('opens the modal for the correct application', async () => {
    const secondApp = makeApplication({ id: 2, first_name: 'Борис', last_name: 'Кузнецов' });
    const items = [
      makeItem({ application: makeApplication({ id: 1, first_name: 'Анна', last_name: 'Смирнова' }) }),
      makeItem({ application: secondApp }),
    ];
    const container = await renderWithItems(items);

    const viewButtons = container.querySelectorAll<HTMLButtonElement>('[data-action="view"]');
    viewButtons[1]!.click();

    expect(container.querySelector('#application-modal-overlay')!.hasAttribute('hidden')).toBe(false);
    const modalBody = container.querySelector<HTMLElement>('#application-modal-body')!;
    expect(modalBody.textContent).toContain(fullName(secondApp));
    expect(modalBody.textContent).not.toContain(fullName(items[0].application));
  });

  it('shows the Клиент/Автомобиль/Обращение/Приоритет обработки blocks', async () => {
    const container = await renderWithItems([makeItem()]);
    container.querySelector<HTMLButtonElement>('[data-action="view"]')!.click();

    const headings = [...container.querySelectorAll('.modal-section h3')].map((h) => h.textContent);
    expect(headings).toEqual([
      'Клиент',
      'Автомобиль',
      'Обращение',
      'Приоритет обработки',
      'Поведение на странице',
    ]);
  });

  it('shows scoring reasons with their label and a signed points value', async () => {
    const container = await renderWithItems([
      makeItem({
        reasons: [{ code: 'deadline', points: 25, label: 'Срок записи: Как можно скорее' }],
      }),
    ]);
    container.querySelector<HTMLButtonElement>('[data-action="view"]')!.click();

    expect(container.textContent).toContain('Срок записи: Как можно скорее');
    expect(container.textContent).toContain('+25');
    // The internal rule code must never be shown to the user.
    expect(container.textContent).not.toContain('deadline');
  });

  it('closes via the close button and returns focus to the trigger', async () => {
    const container = await renderWithItems([makeItem()]);
    document.body.appendChild(container);
    const trigger = container.querySelector<HTMLButtonElement>('[data-action="view"]')!;
    trigger.click();
    expect(container.querySelector('#application-modal-overlay')!.hasAttribute('hidden')).toBe(false);

    container.querySelector<HTMLButtonElement>('#application-modal-close')!.click();

    expect(container.querySelector('#application-modal-overlay')!.hasAttribute('hidden')).toBe(true);
    expect(document.activeElement).toBe(trigger);
    document.body.removeChild(container);
  });

  it('closes on Escape', async () => {
    const container = await renderWithItems([makeItem()]);
    container.querySelector<HTMLButtonElement>('[data-action="view"]')!.click();
    expect(container.querySelector('#application-modal-overlay')!.hasAttribute('hidden')).toBe(false);

    container
      .querySelector('#application-modal-overlay')!
      .dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

    expect(container.querySelector('#application-modal-overlay')!.hasAttribute('hidden')).toBe(true);
  });

  it('escapes an XSS payload in applicant-supplied fields instead of executing/rendering it as markup', async () => {
    const payload = '<img src=x onerror="window.__pwned = true">';
    const container = await renderWithItems([
      makeItem({
        application: makeApplication({
          business_info: payload,
          need_scope: payload,
          comment: payload,
        }),
      }),
    ]);
    container.querySelector<HTMLButtonElement>('[data-action="view"]')!.click();

    const body = container.querySelector<HTMLElement>('#application-modal-body')!;
    expect(body.querySelector('img')).toBeNull();
    expect(body.innerHTML).toContain('&lt;img');
    expect((window as unknown as { __pwned?: boolean }).__pwned).toBeUndefined();
  });
});

// --- Runtime value normalization (pure) -----------------------------------
// priority_score/reason.points/priority_level are typed as number/
// PriorityLevel in api/types.ts, but that's a compile-time promise only —
// these helpers treat the actual runtime value as untrusted regardless of
// what TypeScript expects, per the audit finding.

describe('normalizePriorityScore / priorityScoreDisplay', () => {
  const validCases: Array<[unknown, number]> = [
    [82, 82],
    [0, 0],
    [100, 100],
    [-5, 0], // clamped up to the floor
    [150, 100], // clamped down to the ceiling
  ];

  it.each(validCases)('normalizes %p to %p', (input, expected) => {
    expect(normalizePriorityScore(input)).toBe(expected);
  });

  const malformedCases: unknown[] = [
    null,
    undefined,
    '82', // a numeric-looking string is still not trusted here
    Number.NaN,
    Number.POSITIVE_INFINITY,
    {},
    [],
    '"><img src=x onerror=alert(1)>',
  ];

  it.each(malformedCases)('rejects %p as null', (input) => {
    expect(normalizePriorityScore(input)).toBeNull();
  });

  const displayCases: Array<[unknown, string]> = [
    [82, '82/100'],
    [0, '0/100'],
    [-5, '0/100'],
    [150, '100/100'],
    [null, '—/100'],
    [undefined, '—/100'],
    ['82', '—/100'],
    [Number.NaN, '—/100'],
    [{}, '—/100'],
    [[], '—/100'],
    ['"><img src=x onerror=alert(1)>', '—/100'],
  ];

  it.each(displayCases)('priorityScoreDisplay(%p) -> %p', (input, expected) => {
    expect(priorityScoreDisplay(input)).toBe(expected);
  });
});

describe('normalizeReasonPoints / reasonPointsDisplay', () => {
  const displayCases: Array<[unknown, string]> = [
    [12, '+12'],
    [0, '+0'],
    [-5, '-5'],
    [null, '—'],
    [undefined, '—'],
    ['12', '—'],
    [Number.NaN, '—'],
    [{}, '—'],
    [[], '—'],
    ['"><svg onload=alert(1)>', '—'],
  ];

  it.each(displayCases)('reasonPointsDisplay(%p) -> %p', (input, expected) => {
    expect(reasonPointsDisplay(input)).toBe(expected);
  });

  it('normalizeReasonPoints only accepts a finite number', () => {
    expect(normalizeReasonPoints(12)).toBe(12);
    expect(normalizeReasonPoints('12')).toBeNull();
    expect(normalizeReasonPoints(Number.NaN)).toBeNull();
  });
});

describe('isKnownPriorityLevel', () => {
  const cases: Array<[unknown, boolean]> = [
    ['hot', true],
    ['medium', true],
    ['low', true],
    ['HOT', false],
    ['', false],
    [null, false],
    [undefined, false],
    [1, false],
    [{}, false],
    [[], false],
    ['hot" onclick="alert(1)"', false],
  ];

  it.each(cases)('isKnownPriorityLevel(%p) -> %p', (input, expected) => {
    expect(isKnownPriorityLevel(input)).toBe(expected);
  });
});

describe('priorityLevelLabel / priorityLevelFullLabel', () => {
  // Product terminology: scoring reflects a composite processing priority,
  // not sales-lead "temperature" — hot/medium/low must never surface as
  // "Горячая/Средняя/Низкая" anywhere in the UI, and an unrecognized level
  // falls back to the same neutral label the badge itself uses.
  const cases: Array<[unknown, string, string]> = [
    ['hot', 'Высокий', 'Высокий приоритет обработки'],
    ['medium', 'Средний', 'Средний приоритет обработки'],
    ['low', 'Стандартный', 'Стандартный приоритет обработки'],
    [null, 'Не определена', 'Не определена'],
    [undefined, 'Не определена', 'Не определена'],
    ['HOT', 'Не определена', 'Не определена'],
    ['hot" onclick="alert(1)"', 'Не определена', 'Не определена'],
    [{}, 'Не определена', 'Не определена'],
  ];

  it.each(cases)('priorityLevelLabel(%p) -> %p / priorityLevelFullLabel(%p) -> %p', (input, short, full) => {
    expect(priorityLevelLabel(input)).toBe(short);
    expect(priorityLevelFullLabel(input)).toBe(full);
  });

  it('never returns the old "Горячая"/"Низкая" wording for any input', () => {
    const allInputs = [...cases.map(([input]) => input), 'medium', 'low'];
    for (const input of allInputs) {
      expect(priorityLevelLabel(input)).not.toBe('Горячая');
      expect(priorityLevelLabel(input)).not.toBe('Низкая');
      expect(priorityLevelFullLabel(input)).not.toContain('Горячая');
      expect(priorityLevelFullLabel(input)).not.toContain('Низкая');
    }
  });
});

describe('formatApplicationBudget', () => {
  const cases: Array<[unknown, string]> = [
    [null, '—'],
    [undefined, '—'],
    [{}, '—'],
    [[], '—'],
    ['', '—'],
    ['   ', '—'],
    [Number.NaN, '—'],
    [Number.POSITIVE_INFINITY, '—'],
    ['не число', '—'],
    [true, '—'],
    [0, formatBudget(0)],
    ['0', formatBudget(0)],
    [25000, formatBudget(25000)],
    ['13000.00', formatBudget(13000)],
    ['150000', formatBudget(150000)],
  ];

  it.each(cases)('formatApplicationBudget(%p) -> %p', (input, expected) => {
    expect(formatApplicationBudget(input)).toBe(expected);
  });

  it('never renders "не число ₽", "NaN ₽", "undefined", "null" or "[object Object]"', () => {
    const forbidden = ['не число ₽', 'NaN ₽', 'undefined', 'null', '[object Object]'];
    const malformedInputs: unknown[] = [
      null,
      undefined,
      {},
      [],
      '',
      '   ',
      'не число',
      Number.NaN,
      Number.POSITIVE_INFINITY,
      true,
    ];
    for (const input of malformedInputs) {
      const result = formatApplicationBudget(input);
      forbidden.forEach((bad) => expect(result).not.toContain(bad));
    }
  });
});

// --- Runtime hardening — malformed/XSS wire values (DOM) --------------------

describe('runtime hardening — malformed/XSS wire values render safely', () => {
  function assertNoInjectedElements(container: HTMLElement): void {
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('svg')).toBeNull();
    expect(container.querySelectorAll('script').length).toBe(0);
    expect(
      container.querySelectorAll('[onclick],[onerror],[onload],[onfocus],[autofocus]').length,
    ).toBe(0);
  }

  async function renderUnsafe(item: ApplicationPriorityRead): Promise<HTMLElement> {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(makeList([item]));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(container.querySelectorAll('.application-card').length).toBe(1));
    return container;
  }

  it('a hostile priority_score renders "—/100" instead of injecting markup, and Просмотр still opens the right application', async () => {
    const payload = '"><img src=x onerror="window.__pwned_score = true">';
    const item = makeUnsafeItem({
      application: { ...makeApplication(), first_name: 'Score', last_name: 'Хардненинг' },
      priority_score: payload,
    });
    const container = await renderUnsafe(item);

    assertNoInjectedElements(container);
    expect(container.textContent).toContain('—/100');
    expect((window as unknown as { __pwned_score?: boolean }).__pwned_score).toBeUndefined();

    container.querySelector<HTMLButtonElement>('[data-action="view"]')!.click();
    const modalBody = container.querySelector<HTMLElement>('#application-modal-body')!;
    expect(modalBody.textContent).toContain('Хардненинг Score');
  });

  it('a hostile reason.points renders "—" instead of injecting markup', async () => {
    const payload = '"><svg onload="window.__pwned_points = true">';
    const item = makeUnsafeItem({
      reasons: [{ code: 'x', points: payload, label: 'Подозрительная причина' }],
    });
    const container = await renderUnsafe(item);
    container.querySelector<HTMLButtonElement>('[data-action="view"]')!.click();

    const modalBody = container.querySelector<HTMLElement>('#application-modal-body')!;
    assertNoInjectedElements(container);
    expect(modalBody.textContent).toContain('Подозрительная причина');
    expect(modalBody.querySelector('.reason-points')!.textContent).toBe('—');
    expect((window as unknown as { __pwned_points?: boolean }).__pwned_points).toBeUndefined();
  });

  it('a hostile application.id never reaches the DOM, and Просмотр still opens the right application', async () => {
    const payload = '"><button autofocus onfocus="window.__pwned_id = true">';
    const item = makeUnsafeItem({
      application: {
        ...makeApplication(),
        id: payload,
        first_name: 'ID',
        last_name: 'Хардненинг',
      },
    });
    const container = await renderUnsafe(item);

    assertNoInjectedElements(container);
    expect(container.innerHTML).not.toContain('onfocus');
    expect(container.innerHTML).not.toContain('autofocus');
    expect((window as unknown as { __pwned_id?: boolean }).__pwned_id).toBeUndefined();

    container.querySelector<HTMLButtonElement>('[data-action="view"]')!.click();
    const modalBody = container.querySelector<HTMLElement>('#application-modal-body')!;
    expect(modalBody.textContent).toContain('Хардненинг ID');
  });

  it('a hostile priority_level falls back to a neutral class and label instead of a raw CSS class / inline handler', async () => {
    const payload = 'hot" onclick="window.__pwned_level = true';
    const item = makeUnsafeItem({ priority_level: payload, priority_label: 'Горячая' });
    const container = await renderUnsafe(item);

    assertNoInjectedElements(container);
    const badge = container.querySelector<HTMLElement>('.priority-badge')!;
    expect(badge.className).toBe('priority-badge priority-badge--unknown');
    expect(badge.textContent).toBe('Не определена');
    expect(badge.getAttribute('aria-label')).toBe('Не определена');
    expect((window as unknown as { __pwned_level?: boolean }).__pwned_level).toBeUndefined();

    container.querySelector<HTMLButtonElement>('[data-action="view"]')!.click();
    expect(container.querySelector('#application-modal-overlay')!.hasAttribute('hidden')).toBe(false);
  });
});

// --- Lifecycle ---------------------------------------------------------------

describe('lifecycle', () => {
  it('a late response after the host becomes inactive does not paint the section', async () => {
    const deferred = createDeferred<PrioritizedApplicationList>();
    vi.mocked(api.getPrioritizedApplications).mockReturnValueOnce(deferred.promise);
    let active = true;
    const container = document.createElement('div');
    mountAndActivate(container, makeHost({ isActive: () => active }));
    await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(1));

    active = false; // e.g. logout / a fresh admin panel render happened
    deferred.resolve(makeList([makeItem()]));
    await flush();

    expect(container.querySelector('.application-card')).toBeNull();
  });

  it('a 401 arriving after the host became inactive does not call onSessionExpired again', async () => {
    const deferred = createDeferred<PrioritizedApplicationList>();
    vi.mocked(api.getPrioritizedApplications).mockReturnValueOnce(deferred.promise);
    let active = true;
    const onSessionExpired = vi.fn();
    const container = document.createElement('div');
    mountAndActivate(container, makeHost({ isActive: () => active, onSessionExpired }));
    await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(1));

    active = false;
    deferred.reject(new ApiError('Could not validate credentials', 401));
    await flush();

    expect(onSessionExpired).not.toHaveBeenCalled();
  });

  it('multiple rapid refresh clicks send only one additional request while one is in flight', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValueOnce(makeList([makeItem()]));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(container.querySelector('.application-card')).not.toBeNull());

    const deferred = createDeferred<PrioritizedApplicationList>();
    vi.mocked(api.getPrioritizedApplications).mockReturnValueOnce(deferred.promise);

    const refreshButton = container.querySelector<HTMLButtonElement>('#applications-refresh')!;
    refreshButton.click();
    refreshButton.click();
    refreshButton.click();

    expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(2); // initial mount + one refresh
    expect(refreshButton.disabled).toBe(true);

    deferred.resolve(makeList([makeItem()]));
    await flush();
    expect(refreshButton.disabled).toBe(false);
  });

  it('listeners from a superseded mount do not fire after a fresh mount into the same container', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(makeList([]));
    const container = document.createElement('div');

    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(container.textContent).toContain('Заявок пока нет'));
    const firstRefreshButton = container.querySelector<HTMLButtonElement>('#applications-refresh')!;

    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(container.textContent).toContain('Заявок пока нет'));

    // The old button node is detached from the live container now — clicking
    // the detached reference must not trigger another fetch.
    vi.mocked(api.getPrioritizedApplications).mockClear();
    firstRefreshButton.click();
    await flush();
    expect(api.getPrioritizedApplications).not.toHaveBeenCalled();

    // The new instance's own refresh button still works correctly.
    const currentRefreshButton = container.querySelector<HTMLButtonElement>('#applications-refresh')!;
    currentRefreshButton.click();
    await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(1));
  });
});

describe('lifecycle — activate() / deactivate() / dispose()', () => {
  it('deactivating during an in-flight load discards the late response; reactivating starts a fresh, successful load', async () => {
    const deferred = createDeferred<PrioritizedApplicationList>();
    vi.mocked(api.getPrioritizedApplications).mockReturnValueOnce(deferred.promise);
    const container = document.createElement('div');
    const controller = mountAdminApplications(container, makeHost());

    controller.activate(); // "opens" Заявки — request starts, stays pending
    await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(1));

    controller.deactivate(); // "switches" to Услуги while the old request is still pending

    deferred.resolve(makeList([makeItem()])); // the old, now-stale request finally resolves
    await flush();

    expect(container.querySelector('.application-card')).toBeNull();

    // Re-opening Заявки starts a brand-new request and renders its result.
    vi.mocked(api.getPrioritizedApplications).mockResolvedValueOnce(makeList([makeItem()]));
    controller.activate();
    await vi.waitFor(() => expect(container.querySelector('.application-card')).not.toBeNull());
    expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(2);
  });

  it('a rejection arriving after deactivate() does not write an error state into the hidden panel', async () => {
    const deferred = createDeferred<PrioritizedApplicationList>();
    vi.mocked(api.getPrioritizedApplications).mockReturnValueOnce(deferred.promise);
    const container = document.createElement('div');
    const controller = mountAdminApplications(container, makeHost());

    controller.activate();
    await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(1));

    controller.deactivate();
    deferred.reject(new TypeError('Failed to fetch'));
    await flush();

    expect(container.textContent).not.toContain('Не удалось загрузить');
  });

  it('deactivate() closes an open modal without moving focus back into the hidden panel', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(makeList([makeItem()]));
    const container = document.createElement('div');
    document.body.appendChild(container);
    const controller = mountAdminApplications(container, makeHost());
    controller.activate();
    await vi.waitFor(() => expect(container.querySelector('.application-card')).not.toBeNull());

    const trigger = container.querySelector<HTMLButtonElement>('[data-action="view"]')!;
    trigger.click();
    expect(container.querySelector('#application-modal-overlay')!.hasAttribute('hidden')).toBe(false);

    controller.deactivate();

    expect(container.querySelector('#application-modal-overlay')!.hasAttribute('hidden')).toBe(true);
    expect(document.activeElement).not.toBe(trigger);
    document.body.removeChild(container);
  });

  it('reactivating after deactivate() does not attach duplicate listeners (refresh still sends exactly one request)', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(makeList([]));
    const container = document.createElement('div');
    const controller = mountAdminApplications(container, makeHost());

    controller.activate();
    await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(1));
    controller.deactivate();
    controller.activate();
    await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(2));

    vi.mocked(api.getPrioritizedApplications).mockClear();
    const refreshDeferred = createDeferred<PrioritizedApplicationList>();
    vi.mocked(api.getPrioritizedApplications).mockReturnValueOnce(refreshDeferred.promise);
    container.querySelector<HTMLButtonElement>('#applications-refresh')!.click();
    expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(1);
    refreshDeferred.resolve(makeList([]));
    await flush();
  });

  it('dispose() invalidates a pending load and closes the modal without restoring focus', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValueOnce(makeList([makeItem()]));
    const container = document.createElement('div');
    document.body.appendChild(container);
    const controller = mountAdminApplications(container, makeHost());
    controller.activate();
    await vi.waitFor(() => expect(container.querySelectorAll('.application-card').length).toBe(1));

    const trigger = container.querySelector<HTMLButtonElement>('[data-action="view"]')!;
    trigger.click();
    expect(container.querySelector('#application-modal-overlay')!.hasAttribute('hidden')).toBe(false);

    const deferred = createDeferred<PrioritizedApplicationList>();
    vi.mocked(api.getPrioritizedApplications).mockReturnValueOnce(deferred.promise);
    controller.deactivate();
    controller.activate(); // simulates reopening the tab; request now pending
    await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(2));

    controller.dispose();
    deferred.resolve(makeList([makeItem(), makeItem(), makeItem()]));
    await flush();

    expect(container.querySelector('#application-modal-overlay')!.hasAttribute('hidden')).toBe(true);
    expect(document.activeElement).not.toBe(trigger);
    // The second activate() already cleared the grid to show a loading state;
    // dispose() then discarded the late 3-item response, so it never repaints.
    expect(container.querySelectorAll('.application-card').length).toBe(0);
    document.body.removeChild(container);
  });
});

// --- Stale dataset guard (filter/search must never resurrect an old set) ---

describe('stale dataset guard — a pending request can never overwrite a newer one', () => {
  it('a pending refresh is superseded by a priority-chip change started before it resolves', async () => {
    const setA = [makeItem({ application: makeApplication({ id: 1, first_name: 'Анна' }) })];
    vi.mocked(api.getPrioritizedApplications).mockResolvedValueOnce(makeList(setA));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(container.querySelectorAll('.application-card').length).toBe(1));

    // A refresh starts and stays pending (a deferred promise, never resolved yet).
    const staleDeferred = createDeferred<PrioritizedApplicationList>();
    vi.mocked(api.getPrioritizedApplications).mockReturnValueOnce(staleDeferred.promise);
    container.querySelector<HTMLButtonElement>('#applications-refresh')!.click();
    await vi.waitFor(() => expect(container.textContent).toContain('Загружаем заявки'));

    // Before it resolves, a priority-chip click starts a newer load — this
    // must supersede the pending refresh, not be blocked by it (see
    // loadApplications's loadGeneration guard).
    const hotItems = [
      makeItem({ application: makeApplication({ id: 2, first_name: 'Виктор' }), priority_level: 'hot' }),
    ];
    vi.mocked(api.getPrioritizedApplications).mockResolvedValueOnce(makeList(hotItems));
    container.querySelector<HTMLButtonElement>('[data-filter="hot"]')!.click();
    await vi.waitFor(() => expect(container.querySelectorAll('.application-card').length).toBe(1));
    expect(container.textContent).toContain('Виктор');

    // The stale, unfiltered refresh now resolves late — must not overwrite
    // the newer, filtered result already on screen.
    staleDeferred.resolve(makeList(setA));
    await flush();

    expect(container.textContent).toContain('Виктор');
    expect(container.textContent).not.toContain('Анна');
  });

  it('error refresh: a non-401 failure keeps the error state until the debounced search reload lands, then a successful result replaces it', async () => {
    const setA = [makeItem({ application: makeApplication({ id: 1, first_name: 'Анна' }) })];
    vi.mocked(api.getPrioritizedApplications).mockResolvedValueOnce(makeList(setA));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(container.querySelectorAll('.application-card').length).toBe(1));

    vi.mocked(api.getPrioritizedApplications).mockRejectedValueOnce(new TypeError('Failed to fetch'));
    container.querySelector<HTMLButtonElement>('#applications-refresh')!.click();
    await vi.waitFor(() => expect(container.textContent).toContain('Не удалось загрузить'));

    // Typing starts a debounce timer — the error state is untouched until
    // it actually fires a new request.
    const searchInput = container.querySelector<HTMLInputElement>('#applications-search')!;
    searchInput.value = 'виктор';
    searchInput.dispatchEvent(new Event('input', { bubbles: true }));
    expect(container.textContent).toContain('Не удалось загрузить');

    const setB = [makeItem({ application: makeApplication({ id: 2, first_name: 'Виктор' }) })];
    vi.mocked(api.getPrioritizedApplications).mockResolvedValueOnce(makeList(setB));
    await waitForSearchDebounce();

    await vi.waitFor(() => expect(container.querySelectorAll('.application-card').length).toBe(1));
    expect(container.textContent).toContain('Виктор');
    expect(container.textContent).not.toContain('Анна');
    expect(container.textContent).not.toContain('Не удалось загрузить');
  });
});

// --- Application behavior analytics (lazy-loaded modal detail) -----------

describe('application behavior analytics — modal detail', () => {
  it('shows a loading state immediately, before the detail request resolves', async () => {
    const deferred = createDeferred<ApplicationBehaviorAnalytics>();
    vi.mocked(api.getApplicationBehaviorAnalytics).mockReturnValueOnce(deferred.promise);
    const container = await renderWithItems([makeItem()]);

    container.querySelector<HTMLButtonElement>('[data-action="view"]')!.click();

    const analyticsEl = container.querySelector<HTMLElement>('#application-modal-analytics-content')!;
    expect(analyticsEl.textContent).toContain('Загружаем поведенческие метрики…');
  });

  it('requests the analytics for the opened application', async () => {
    const app = makeApplication({ id: 55 });
    const container = await renderWithItems([makeItem({ application: app })]);
    container.querySelector<HTMLButtonElement>('[data-action="view"]')!.click();

    await vi.waitFor(() => expect(api.getApplicationBehaviorAnalytics).toHaveBeenCalledWith(55));
  });

  it('shows the "no metrics" message when has_metrics is false', async () => {
    vi.mocked(api.getApplicationBehaviorAnalytics).mockResolvedValueOnce(makeAnalyticsDetail({ has_metrics: false }));
    const container = await renderWithItems([makeItem()]);
    container.querySelector<HTMLButtonElement>('[data-action="view"]')!.click();

    await vi.waitFor(() =>
      expect(container.querySelector('#application-modal-analytics-content')!.textContent).toContain(
        'Для этой заявки поведенческие метрики не записаны.',
      ),
    );
  });

  it('shows time on page, return count, click count, recorded_at, buttons and sections when metrics exist', async () => {
    const detail = makeAnalyticsDetail({
      has_metrics: true,
      time_on_page_seconds: 192,
      return_count: 2,
      total_button_clicks: 5,
      recorded_at: '2026-07-20T10:05:00Z',
      clicked_buttons: [{ name: 'Отправить', count: 5, share_percent: 100 }],
      section_activity: [
        { section: 'Контакты', total_duration_seconds: 60, average_duration_seconds: 30, interactions_count: 2, share_percent: 100 },
      ],
    });
    vi.mocked(api.getApplicationBehaviorAnalytics).mockResolvedValueOnce(detail);
    const container = await renderWithItems([makeItem()]);
    container.querySelector<HTMLButtonElement>('[data-action="view"]')!.click();

    const analyticsEl = container.querySelector<HTMLElement>('#application-modal-analytics-content')!;
    await vi.waitFor(() => expect(analyticsEl.textContent).toContain('3 мин 12 сек')); // time on page
    expect(analyticsEl.textContent).toContain('2'); // return count
    expect(analyticsEl.textContent).toContain('5'); // total button clicks
    expect(analyticsEl.textContent).toContain(formatAnalyticsDateTime('2026-07-20T10:05:00Z'));
    expect(analyticsEl.textContent).toContain('Отправить');
    expect(analyticsEl.textContent).toContain('Контакты');
    expect(analyticsEl.textContent).toContain('Среднее время одного наведения');
    // Overview-level KPIs (e.g. "applications_count") are never repeated here.
    expect(analyticsEl.textContent).not.toContain('Заявки за период');
    // Stage 4: return_count is a per-device localStorage visit-counter
    // snapshot at submission time (see adminAnalytics.ts's matching KPI
    // group), not a count of returns to this form.
    expect(analyticsEl.textContent).toContain('Счётчик визитов (устройство)');
    expect(analyticsEl.textContent).not.toContain('Возвратов к форме');
    // CSP (Stage 4): the width is applied via the CSSOM (.style.width),
    // which style-src does not block (unlike a style="" string written
    // into markup/innerHTML) - see applyBarWidths in adminAnalytics.ts.
    const fill = analyticsEl.querySelector<HTMLElement>('.analytics-bar-fill')!;
    expect(fill.style.width).toBe('100%');
    expect(fill.dataset.barWidth).toBe('100');
  });

  it('maps known collector button/section identifiers to Russian labels in the detail modal', async () => {
    const detail = makeAnalyticsDetail({
      has_metrics: true,
      clicked_buttons: [
        { name: 'hero_cta', count: 2, share_percent: 50 },
        { name: 'submit_application', count: 2, share_percent: 50 },
      ],
      section_activity: [
        { section: 'application_form', total_duration_seconds: 30, average_duration_seconds: 15, interactions_count: 2, share_percent: 100 },
      ],
    });
    vi.mocked(api.getApplicationBehaviorAnalytics).mockResolvedValueOnce(detail);
    const container = await renderWithItems([makeItem()]);
    container.querySelector<HTMLButtonElement>('[data-action="view"]')!.click();

    const analyticsEl = container.querySelector<HTMLElement>('#application-modal-analytics-content')!;
    await vi.waitFor(() => expect(analyticsEl.textContent).toContain('Основная кнопка на главном экране'));
    expect(analyticsEl.textContent).toContain('Отправка заявки');
    expect(analyticsEl.textContent).toContain('Форма заявки');
    // The technical wire identifiers must never leak into the visible text.
    expect(analyticsEl.textContent).not.toContain('hero_cta');
    expect(analyticsEl.textContent).not.toContain('submit_application');
    expect(analyticsEl.textContent).not.toContain('application_form');
  });

  it('shows a neutral error message and a working retry on a non-401 failure', async () => {
    vi.mocked(api.getApplicationBehaviorAnalytics).mockRejectedValueOnce(new TypeError('Failed to fetch'));
    const container = await renderWithItems([makeItem()]);
    container.querySelector<HTMLButtonElement>('[data-action="view"]')!.click();

    const analyticsEl = container.querySelector<HTMLElement>('#application-modal-analytics-content')!;
    await vi.waitFor(() => expect(analyticsEl.textContent).toContain('Не удалось загрузить поведенческие метрики.'));

    vi.mocked(api.getApplicationBehaviorAnalytics).mockResolvedValueOnce(makeAnalyticsDetail({ has_metrics: false }));
    analyticsEl.querySelector<HTMLButtonElement>('#application-modal-analytics-retry')!.click();

    await vi.waitFor(() =>
      expect(analyticsEl.textContent).toContain('Для этой заявки поведенческие метрики не записаны.'),
    );
  });

  it('a 401 calls host.onSessionExpired, not the generic error message', async () => {
    vi.mocked(api.getApplicationBehaviorAnalytics).mockRejectedValueOnce(new ApiError('nope', 401));
    const onSessionExpired = vi.fn();
    const container = await renderWithItems([makeItem()], makeHost({ onSessionExpired }));
    container.querySelector<HTMLButtonElement>('[data-action="view"]')!.click();

    await vi.waitFor(() => expect(onSessionExpired).toHaveBeenCalledTimes(1));
    expect(container.querySelector('#application-modal-analytics-content')!.textContent).not.toContain(
      'Не удалось загрузить',
    );
  });

  it('closing the modal before the response arrives discards it — no late paint', async () => {
    const deferred = createDeferred<ApplicationBehaviorAnalytics>();
    vi.mocked(api.getApplicationBehaviorAnalytics).mockReturnValueOnce(deferred.promise);
    const container = await renderWithItems([makeItem()]);
    container.querySelector<HTMLButtonElement>('[data-action="view"]')!.click();
    await vi.waitFor(() => expect(api.getApplicationBehaviorAnalytics).toHaveBeenCalledTimes(1));

    container.querySelector<HTMLButtonElement>('#application-modal-close')!.click();
    deferred.resolve(makeAnalyticsDetail({ has_metrics: true, time_on_page_seconds: 999 }));
    await flush();

    expect(container.querySelector('#application-modal-overlay')!.hasAttribute('hidden')).toBe(true);
    expect(container.querySelector('#application-modal-analytics-content')!.textContent).not.toContain(
      '999 сек',
    );
  });

  it('opening application A then B: a late response for A never overwrites B', async () => {
    const appA = makeApplication({ id: 1, first_name: 'Анна' });
    const appB = makeApplication({ id: 2, first_name: 'Борис' });
    const deferredA = createDeferred<ApplicationBehaviorAnalytics>();
    vi.mocked(api.getApplicationBehaviorAnalytics).mockReturnValueOnce(deferredA.promise);
    const container = await renderWithItems([makeItem({ application: appA }), makeItem({ application: appB })]);

    const viewButtons = container.querySelectorAll<HTMLButtonElement>('[data-action="view"]');
    viewButtons[0]!.click(); // open A — request pending
    await vi.waitFor(() => expect(api.getApplicationBehaviorAnalytics).toHaveBeenCalledWith(1));

    vi.mocked(api.getApplicationBehaviorAnalytics).mockResolvedValueOnce(
      makeAnalyticsDetail({ has_metrics: true, time_on_page_seconds: 42 }),
    );
    viewButtons[1]!.click(); // open B before A resolved
    await vi.waitFor(() =>
      expect(container.querySelector('#application-modal-analytics-content')!.textContent).toContain('42 сек'),
    );

    // A's stale response now arrives — must not overwrite B's content.
    deferredA.resolve(makeAnalyticsDetail({ has_metrics: true, time_on_page_seconds: 999 }));
    await flush();

    expect(container.querySelector('#application-modal-body')!.textContent).toContain('Борис');
    expect(container.querySelector('#application-modal-analytics-content')!.textContent).toContain('42 сек');
    expect(container.querySelector('#application-modal-analytics-content')!.textContent).not.toContain('999 сек');
  });

  it('deactivating the applications tab invalidates a pending detail request', async () => {
    const deferred = createDeferred<ApplicationBehaviorAnalytics>();
    vi.mocked(api.getApplicationBehaviorAnalytics).mockReturnValueOnce(deferred.promise);
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(makeList([makeItem()]));
    const container = document.createElement('div');
    const controller = mountAdminApplications(container, makeHost());
    controller.activate();
    await vi.waitFor(() => expect(container.querySelector('.application-card')).not.toBeNull());

    container.querySelector<HTMLButtonElement>('[data-action="view"]')!.click();
    await vi.waitFor(() => expect(api.getApplicationBehaviorAnalytics).toHaveBeenCalledTimes(1));

    controller.deactivate();
    deferred.resolve(makeAnalyticsDetail({ has_metrics: true, time_on_page_seconds: 999 }));
    await flush();

    expect(container.querySelector('#application-modal-overlay')!.hasAttribute('hidden')).toBe(true);
  });

  it('a malformed application id (hostile runtime value) fails gracefully instead of crashing', async () => {
    vi.mocked(api.getApplicationBehaviorAnalytics).mockRejectedValueOnce(new Error('Invalid application id'));
    const item = makeUnsafeItem({
      application: { ...makeApplication(), id: '"><img src=x onerror=alert(1)>' },
    });
    const container = await renderWithItems([item]);
    container.querySelector<HTMLButtonElement>('[data-action="view"]')!.click();

    await vi.waitFor(() =>
      expect(container.querySelector('#application-modal-analytics-content')!.textContent).toContain(
        'Не удалось загрузить поведенческие метрики.',
      ),
    );
    expect(container.querySelector('img')).toBeNull();
  });

  it('renders malformed runtime detail values safely (no NaN/undefined/[object Object])', async () => {
    const detail = makeUnsafeAnalyticsDetail({
      time_on_page_seconds: 'lots',
      return_count: Number.NaN,
      total_button_clicks: -1,
      clicked_buttons: [{ name: 123, count: 'many', share_percent: 'lots' }],
    });
    vi.mocked(api.getApplicationBehaviorAnalytics).mockResolvedValueOnce(detail);
    const container = await renderWithItems([makeItem()]);
    container.querySelector<HTMLButtonElement>('[data-action="view"]')!.click();

    const analyticsEl = container.querySelector<HTMLElement>('#application-modal-analytics-content')!;
    await vi.waitFor(() => expect(api.getApplicationBehaviorAnalytics).toHaveBeenCalledTimes(1));
    await flush();
    expect(analyticsEl.textContent).not.toContain('NaN');
    expect(analyticsEl.textContent).not.toContain('undefined');
    expect(analyticsEl.textContent).not.toContain('[object Object]');
    expect(analyticsEl.textContent).toContain('—'); // malformed count/time fall back to the dash
  });

  it('an XSS payload in a clicked button name renders as text, not markup', async () => {
    const payload = '<img src=x onerror="window.__pwned_detail_button = true">';
    const detail = makeAnalyticsDetail({
      has_metrics: true,
      clicked_buttons: [{ name: payload, count: 1, share_percent: 100 }],
    });
    vi.mocked(api.getApplicationBehaviorAnalytics).mockResolvedValueOnce(detail);
    const container = await renderWithItems([makeItem()]);
    container.querySelector<HTMLButtonElement>('[data-action="view"]')!.click();

    await vi.waitFor(() => expect(api.getApplicationBehaviorAnalytics).toHaveBeenCalledTimes(1));
    await flush();

    expect(container.querySelector('img')).toBeNull();
    expect((window as unknown as { __pwned_detail_button?: boolean }).__pwned_detail_button).toBeUndefined();
  });

  it('an XSS payload in a section name renders as text, not markup', async () => {
    const payload = '"><svg onload="window.__pwned_detail_section = true">';
    const detail = makeAnalyticsDetail({
      has_metrics: true,
      section_activity: [
        { section: payload, total_duration_seconds: 1, average_duration_seconds: 1, interactions_count: 1, share_percent: 100 },
      ],
    });
    vi.mocked(api.getApplicationBehaviorAnalytics).mockResolvedValueOnce(detail);
    const container = await renderWithItems([makeItem()]);
    container.querySelector<HTMLButtonElement>('[data-action="view"]')!.click();

    await vi.waitFor(() => expect(api.getApplicationBehaviorAnalytics).toHaveBeenCalledTimes(1));
    await flush();

    expect(container.querySelector('svg')).toBeNull();
    expect((window as unknown as { __pwned_detail_section?: boolean }).__pwned_detail_section).toBeUndefined();
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});
