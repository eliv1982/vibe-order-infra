// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/client';
import type { ApplicationPriorityRead, ApplicationRead, PrioritizedApplicationList } from '../api/types';
import { formatBudget } from '../utils/format';
import {
  filterApplications,
  formatApplicationBudget,
  formatApplicationDate,
  fullName,
  isKnownPriorityLevel,
  mountAdminApplications,
  normalizePriorityScore,
  normalizeReasonPoints,
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

async function renderWithItems(items: ApplicationPriorityRead[]): Promise<HTMLElement> {
  vi.mocked(api.getPrioritizedApplications).mockResolvedValue(makeList(items));
  const container = document.createElement('div');
  mountAndActivate(container, makeHost());
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
});

// --- API ---------------------------------------------------------------

describe('loading applications', () => {
  it('requests prioritized applications with skip=0, limit=100 on mount', async () => {
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(makeList([]));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());

    await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenCalledWith(0, 100));
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

  it('shows the hot/medium/low priority label as text, not just a color', async () => {
    const items = [
      makeItem({ priority_level: 'hot', priority_label: 'Горячая' }),
      makeItem({ application: makeApplication(), priority_level: 'medium', priority_label: 'Средняя' }),
      makeItem({ application: makeApplication(), priority_level: 'low', priority_label: 'Низкая' }),
    ];
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(makeList(items));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());

    await vi.waitFor(() => expect(container.querySelectorAll('.application-card').length).toBe(3));
    expect(container.textContent).toContain('Горячая');
    expect(container.textContent).toContain('Средняя');
    expect(container.textContent).toContain('Низкая');
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

// --- Filtering -------------------------------------------------------------

describe('filterApplications (pure)', () => {
  it('preserves the original backend order within a level filter', () => {
    const items = [
      makeItem({ application: makeApplication({ id: 1 }), priority_level: 'hot' }),
      makeItem({ application: makeApplication({ id: 2 }), priority_level: 'medium' }),
      makeItem({ application: makeApplication({ id: 3 }), priority_level: 'hot' }),
    ];
    const result = filterApplications(items, 'hot', '');
    expect(result.map((item) => item.application.id)).toEqual([1, 3]);
  });

  it('filters by free-text search across name/contact/service/vehicle info', () => {
    const items = [
      makeItem({ application: makeApplication({ id: 1, first_name: 'Анна', last_name: 'Смирнова' }) }),
      makeItem({ application: makeApplication({ id: 2, first_name: 'Борис', last_name: 'Кузнецов' }) }),
    ];
    const result = filterApplications(items, 'all', 'смирнова');
    expect(result.map((item) => item.application.id)).toEqual([1]);
  });

  it('is case-insensitive and trims the query', () => {
    const items = [makeItem({ application: makeApplication({ id: 1, interested_product: 'Химчистка салона' }) })];
    expect(filterApplications(items, 'all', '  ХИМЧИСТКА  ').map((i) => i.application.id)).toEqual([1]);
  });

  it('returns an empty array when nothing matches', () => {
    const items = [makeItem()];
    expect(filterApplications(items, 'all', 'совершенно несуществующий запрос')).toEqual([]);
  });
});

describe('filtering — DOM', () => {
  it('clicking a level filter chip shows only matching cards', async () => {
    const items = [
      makeItem({ application: makeApplication({ id: 1 }), priority_level: 'hot', priority_label: 'Горячая' }),
      makeItem({
        application: makeApplication({ id: 2 }),
        priority_level: 'low',
        priority_label: 'Низкая',
      }),
    ];
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(makeList(items));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(container.querySelectorAll('.application-card').length).toBe(2));

    container.querySelector<HTMLButtonElement>('[data-filter="hot"]')!.click();

    const cards = container.querySelectorAll<HTMLElement>('.application-card');
    expect(cards.length).toBe(1);
    expect(cards[0].textContent).toContain('Горячая');
  });

  it('typing in the search box filters the visible list', async () => {
    const items = [
      makeItem({ application: makeApplication({ id: 1, first_name: 'Анна' }) }),
      makeItem({ application: makeApplication({ id: 2, first_name: 'Борис' }) }),
    ];
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(makeList(items));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(container.querySelectorAll('.application-card').length).toBe(2));

    const searchInput = container.querySelector<HTMLInputElement>('#applications-search')!;
    searchInput.value = 'Анна';
    searchInput.dispatchEvent(new Event('input', { bubbles: true }));

    const cards = container.querySelectorAll<HTMLElement>('.application-card');
    expect(cards.length).toBe(1);
    expect(cards[0].textContent).toContain('Анна');
  });

  it('shows a distinct empty-search-result state and a found count otherwise', async () => {
    const items = [makeItem({ application: makeApplication({ id: 1, first_name: 'Анна' }) })];
    vi.mocked(api.getPrioritizedApplications).mockResolvedValue(makeList(items));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(container.querySelectorAll('.application-card').length).toBe(1));
    expect(container.textContent).toContain('Найдено: 1 из 1');

    const searchInput = container.querySelector<HTMLInputElement>('#applications-search')!;
    searchInput.value = 'нет такого клиента';
    searchInput.dispatchEvent(new Event('input', { bubbles: true }));

    expect(container.querySelectorAll('.application-card').length).toBe(0);
    expect(container.textContent).toContain('Ничего не найдено');
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

  it('shows the Клиент/Автомобиль/Обращение/Приоритет blocks', async () => {
    const container = await renderWithItems([makeItem()]);
    container.querySelector<HTMLButtonElement>('[data-action="view"]')!.click();

    const headings = [...container.querySelectorAll('.modal-section h3')].map((h) => h.textContent);
    expect(headings).toEqual(['Клиент', 'Автомобиль', 'Обращение', 'Приоритет']);
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

describe('stale dataset guard — filter/search during a pending or failed refresh', () => {
  it('pending refresh: changing filter/search while set A is being replaced never resurrects set A, and the change applies only to set B once it arrives', async () => {
    const setA = [
      makeItem({ application: makeApplication({ id: 1, first_name: 'Анна' }), priority_level: 'hot' }),
      makeItem({ application: makeApplication({ id: 2, first_name: 'Борис' }), priority_level: 'low' }),
    ];
    // 1. successfully load set A
    vi.mocked(api.getPrioritizedApplications).mockResolvedValueOnce(makeList(setA));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(container.querySelectorAll('.application-card').length).toBe(2));

    // 2. start a refresh with a deferred promise — stays pending
    const deferred = createDeferred<PrioritizedApplicationList>();
    vi.mocked(api.getPrioritizedApplications).mockReturnValueOnce(deferred.promise);
    container.querySelector<HTMLButtonElement>('#applications-refresh')!.click();
    await vi.waitFor(() => expect(container.textContent).toContain('Загружаем заявки'));

    // 3. change filter and search while the refresh is still pending
    container.querySelector<HTMLButtonElement>('[data-filter="hot"]')!.click();
    const searchInput = container.querySelector<HTMLInputElement>('#applications-search')!;
    searchInput.value = 'виктор';
    searchInput.dispatchEvent(new Event('input', { bubbles: true }));

    // 4. set A's cards must not reappear
    expect(container.querySelectorAll('.application-card').length).toBe(0);
    expect(container.textContent).not.toContain('Анна');
    expect(container.textContent).not.toContain('Борис');

    // 5. finish the refresh with set B
    const setB = [
      makeItem({ application: makeApplication({ id: 3, first_name: 'Виктор' }), priority_level: 'hot' }),
      makeItem({ application: makeApplication({ id: 4, first_name: 'Галина' }), priority_level: 'medium' }),
    ];
    deferred.resolve(makeList(setB));
    await flush();

    // 6. the filter ('hot') + search ('виктор') set in step 3 apply only to set B
    const cards = container.querySelectorAll<HTMLElement>('.application-card');
    expect(cards.length).toBe(1);
    expect(container.textContent).toContain('Виктор');
    expect(container.textContent).not.toContain('Галина'); // excluded: not 'hot'
    expect(container.textContent).not.toContain('Анна');
    expect(container.textContent).not.toContain('Борис');
  });

  it('error refresh: a non-401 failure keeps the error state after a filter/search change, and a successful retry shows only the new set', async () => {
    const setA = [makeItem({ application: makeApplication({ id: 1, first_name: 'Анна' }) })];
    // 1. successfully load set A
    vi.mocked(api.getPrioritizedApplications).mockResolvedValueOnce(makeList(setA));
    const container = document.createElement('div');
    mountAndActivate(container, makeHost());
    await vi.waitFor(() => expect(container.querySelectorAll('.application-card').length).toBe(1));

    // 2. a new refresh completes with a non-401 error
    vi.mocked(api.getPrioritizedApplications).mockRejectedValueOnce(new TypeError('Failed to fetch'));
    container.querySelector<HTMLButtonElement>('#applications-refresh')!.click();
    await vi.waitFor(() => expect(container.textContent).toContain('Не удалось загрузить'));

    // 3. change search after the error
    const searchInput = container.querySelector<HTMLInputElement>('#applications-search')!;
    searchInput.value = 'виктор';
    searchInput.dispatchEvent(new Event('input', { bubbles: true }));

    // 4. set A's cards must not reappear
    expect(container.querySelectorAll('.application-card').length).toBe(0);
    expect(container.textContent).not.toContain('Анна');
    // 5. the error state remains (not silently replaced by "Заявок пока нет.")
    expect(container.textContent).toContain('Не удалось загрузить');
    expect(container.textContent).not.toContain('Заявок пока нет');

    // 6. retry with a successful set B shows only B (matching the search set in step 3)
    const setB = [makeItem({ application: makeApplication({ id: 2, first_name: 'Виктор' }) })];
    vi.mocked(api.getPrioritizedApplications).mockResolvedValueOnce(makeList(setB));
    container.querySelector<HTMLButtonElement>('#applications-refresh')!.click();
    await vi.waitFor(() => expect(container.querySelectorAll('.application-card').length).toBe(1));
    expect(container.textContent).toContain('Виктор');
    expect(container.textContent).not.toContain('Анна');
    expect(container.textContent).not.toContain('Не удалось загрузить');
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});
