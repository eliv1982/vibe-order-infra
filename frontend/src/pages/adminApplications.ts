/**
 * "Заявки" (client applications) section of the admin panel.
 *
 * Owns everything specific to browsing prioritized applications: loading,
 * local filter/search, cards, and the detail modal. Auth, the render
 * generation guard, logout and the "Услуги" section stay in admin.ts —
 * this module never touches sessionStorage/tokenStorage or the auth flow
 * directly; a 401 is reported upward via ApplicationsSectionHost so admin.ts
 * remains the single place that ends a session.
 *
 * Score is never recomputed here — every number/label rendered comes
 * straight from GET /api/applications/prioritized, already sorted by the
 * backend.
 *
 * Mounted once (see mountAdminApplications) by admin.ts's tab switcher; the
 * returned controller's activate()/deactivate()/dispose() then track
 * whether the "Заявки" tab is the one currently visible, independently of
 * admin.ts's own auth/session generation (see ApplicationsSectionHost).
 */

import { api, isUnauthorizedError } from '../api/client';
import type { ApplicationPriorityRead, ApplicationRead, PriorityLevel } from '../api/types';
import { escapeHtml } from '../utils/html';
import { formatBudget } from '../utils/format';

export interface ApplicationsSectionHost {
  /** Mirrors admin.ts's own render-generation guard — true while this
   * section's mount is still the one the admin panel is showing (false
   * once a newer renderAdmin()/logout/session-expiry has taken over). */
  isActive: () => boolean;
  /** admin.ts owns ending the session (the token is already cleared
   * centrally in api/client.ts on any 401) — this just asks it to show
   * the login view. */
  onSessionExpired: () => void;
}

/** Returned by mountAdminApplications — lets admin.ts's tab switcher tell
 * this section when the "Заявки" tab becomes the visible one (activate),
 * when the admin navigates away from it (deactivate), and when the whole
 * mount is being torn down for good (dispose). */
export interface ApplicationsSectionController {
  activate: () => void;
  deactivate: () => void;
  dispose: () => void;
}

type FilterLevel = 'all' | PriorityLevel;

interface ApplicationsState {
  items: ApplicationPriorityRead[];
  /** Exactly what's currently rendered in #applications-list, in render
   * order — the "Просмотр" button's data-index refers into this array, not
   * into `items`, so filtering/searching can never desync the two. */
  visibleItems: ApplicationPriorityRead[];
  filterLevel: FilterLevel;
  searchQuery: string;
  loading: boolean;
  /** True only while the most recent load attempt ended in a (non-401)
   * error — distinct from `items` legitimately being empty, so renderList
   * knows not to paper over the error banner with "Заявок пока нет." if the
   * admin touches the filter/search while no successful dataset exists. */
  hasLoadError: boolean;
  lastFocusedTrigger: HTMLButtonElement | null;
}

/**
 * Mount-level generation guard (mirrors admin.ts's renderGeneration/
 * isCurrentRender pair) — guards against a hypothetical second mount into
 * a still-live container superseding this one. Kept separate from
 * host.isActive() so this module doesn't need to know anything about
 * admin.ts's internals beyond the injected host.
 */
let appsGeneration = 0;

function beginAppsRender(): number {
  appsGeneration += 1;
  return appsGeneration;
}

function isCurrentAppsRender(mountId: number): boolean {
  return mountId === appsGeneration;
}

export function fullName(app: ApplicationRead): string {
  return [app.last_name, app.first_name, app.middle_name]
    .filter((part): part is string => typeof part === 'string' && part.trim().length > 0)
    .join(' ');
}

const applicationDateFormatter = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
});

/** Pure — unit tested. Falls back to the raw string on an unparseable date
 * rather than throwing or showing "Invalid Date". */
export function formatApplicationDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return applicationDateFormatter.format(date);
}

function matchesSearch(item: ApplicationPriorityRead, normalizedQuery: string): boolean {
  if (!normalizedQuery) return true;
  const app = item.application;
  const haystack = [
    app.first_name,
    app.last_name,
    app.contact_data,
    app.interested_product,
    app.business_info,
  ]
    .join(' ')
    .toLowerCase();
  return haystack.includes(normalizedQuery);
}

/** Pure — unit tested. Preserves the backend-supplied order (Array.filter
 * never reorders); never touches priority_score/level itself. */
export function filterApplications(
  items: ApplicationPriorityRead[],
  filterLevel: FilterLevel,
  searchQuery: string,
): ApplicationPriorityRead[] {
  const normalizedQuery = searchQuery.trim().toLowerCase();
  return items.filter((item) => {
    if (filterLevel !== 'all' && item.priority_level !== filterLevel) return false;
    return matchesSearch(item, normalizedQuery);
  });
}

// --- Runtime value hardening ---------------------------------------------
// The wire types in api/types.ts declare priority_score/reason.points as
// `number` and priority_level as the PriorityLevel union, but those are
// compile-time promises only — a malformed or hostile backend/proxy
// response is still valid JS at runtime. Everything below treats those
// fields as `unknown` and normalizes them before they ever reach a
// template string, so a malformed payload degrades to a neutral display
// instead of ever being interpolated into HTML.

const KNOWN_PRIORITY_LEVELS: readonly PriorityLevel[] = ['hot', 'medium', 'low'];
const FALLBACK_PRIORITY_LABEL = 'Не определена';

/**
 * Frontend-owned priority terminology (product decision: scoring reflects a
 * composite processing priority, not sales-lead "temperature" or urgency
 * alone) — the UI never shows "Горячая/Средняя/Низкая". Backend's own
 * priority_label (still present on the wire, see api/types.ts) is
 * intentionally not read anywhere in this module; the visible label and
 * its accessible full form are both derived solely from the validated
 * priority_level enum via these allowlist maps, so this display never
 * depends on — or has to change alongside — the backend's Russian string.
 */
const PRIORITY_LEVEL_LABELS: Record<PriorityLevel, string> = {
  hot: 'Высокий',
  medium: 'Средний',
  low: 'Стандартный',
};

const PRIORITY_LEVEL_FULL_LABELS: Record<PriorityLevel, string> = {
  hot: 'Высокий приоритет обработки',
  medium: 'Средний приоритет обработки',
  low: 'Стандартный приоритет обработки',
};

/** Pure — unit tested. */
export function isKnownPriorityLevel(value: unknown): value is PriorityLevel {
  return typeof value === 'string' && (KNOWN_PRIORITY_LEVELS as readonly string[]).includes(value);
}

/** Pure — unit tested. Short badge/filter text for a validated priority
 * level; unknown/malformed input falls back to the same neutral label the
 * badge itself uses. */
export function priorityLevelLabel(value: unknown): string {
  return isKnownPriorityLevel(value) ? PRIORITY_LEVEL_LABELS[value] : FALLBACK_PRIORITY_LABEL;
}

/** Pure — unit tested. Full "N приоритет обработки" form for accessible
 * text (aria-label) and contexts with room for the whole phrase. */
export function priorityLevelFullLabel(value: unknown): string {
  return isKnownPriorityLevel(value) ? PRIORITY_LEVEL_FULL_LABELS[value] : FALLBACK_PRIORITY_LABEL;
}

/** Pure — unit tested. Clamps a runtime score into [0, 100]; anything that
 * isn't actually a finite number (a hostile string, null, an object, ...)
 * normalizes to null so callers fall back to a neutral display. */
export function normalizePriorityScore(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return Math.min(100, Math.max(0, value));
}

/** Pure — unit tested. */
export function priorityScoreDisplay(value: unknown): string {
  const normalized = normalizePriorityScore(value);
  return normalized === null ? '—/100' : `${normalized}/100`;
}

/** Pure — unit tested. Only a finite number is a valid points value. */
export function normalizeReasonPoints(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** Pure — unit tested. */
export function reasonPointsDisplay(value: unknown): string {
  const normalized = normalizeReasonPoints(value);
  if (normalized === null) return '—';
  return normalized >= 0 ? `+${normalized}` : `${normalized}`;
}

/** Pure — unit tested. Defensive local wrapper around utils/format's
 * formatBudget: `application.budget` arrives as a Decimal-as-string, but at
 * runtime it can't be trusted to actually be one. Rejects everything that
 * isn't a finite number (or a string that parses to one) instead of ever
 * rendering "NaN ₽" / "undefined ₽" / "[object Object]". Keeps the
 * project's existing ru-RU currency formatting (see utils/format.ts). */
export function formatApplicationBudget(value: unknown): string {
  if (typeof value !== 'number' && typeof value !== 'string') return '—';
  if (typeof value === 'string' && value.trim() === '') return '—';
  const numeric = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(numeric)) return '—';
  return formatBudget(numeric);
}

// --- Templates --------------------------------------------------------
// Every applicant-supplied string is escaped, and so is every backend
// label/recommendation string (recommended_action, recommended_team,
// reason.label) — none of it is trusted markup. reason.code is
// intentionally never rendered (internal-only). Backend's priority_label
// is never rendered either — the badge's text/accessible name come from
// the frontend-owned PRIORITY_LEVEL_LABELS/PRIORITY_LEVEL_FULL_LABELS maps
// above, keyed off the validated priority_level enum.
//
// priority_score/reason.points/priority_level are run through the
// normalizers above before display, and application.id is never
// interpolated into HTML/data-* at all — the "Просмотр" button is bound to
// its item via a frontend-generated array index instead (see `index`
// params below and wireListDelegation).

function priorityBadgeHtml(item: ApplicationPriorityRead): string {
  const known = isKnownPriorityLevel(item.priority_level);
  const levelClass = known ? item.priority_level : 'unknown';
  const label = priorityLevelLabel(item.priority_level);
  const fullLabel = priorityLevelFullLabel(item.priority_level);
  return `<span class="priority-badge priority-badge--${levelClass}" aria-label="${escapeHtml(
    fullLabel,
  )}">${escapeHtml(label)}</span>`;
}

function applicationCardTemplate(item: ApplicationPriorityRead, index: number): string {
  const app = item.application;
  return `
    <article class="card application-card" data-index="${index}">
      <div class="application-card-top">
        <div>
          <h3>${escapeHtml(fullName(app))}</h3>
          <p class="application-card-service">${escapeHtml(app.interested_product)}</p>
        </div>
        ${priorityBadgeHtml(item)}
      </div>

      <dl class="application-card-facts">
        <div><dt>Бюджет</dt><dd>${escapeHtml(formatApplicationBudget(app.budget))}</dd></div>
        <div><dt>Дата заявки</dt><dd>${escapeHtml(formatApplicationDate(app.created_at))}</dd></div>
        <div><dt>Желаемый срок записи</dt><dd>${escapeHtml(app.deadline)}</dd></div>
        <div><dt>Тип обращения</dt><dd>${escapeHtml(app.task_type)}</dd></div>
      </dl>

      <div class="application-card-priority">
        <span class="application-card-score">${priorityScoreDisplay(item.priority_score)}</span>
        <p class="application-card-action"><strong>Действие:</strong> ${escapeHtml(item.recommended_action)}</p>
        <p class="application-card-team"><strong>Команда:</strong> ${escapeHtml(item.recommended_team)}</p>
        ${
          item.requires_personal_manager
            ? '<p class="application-card-pm-flag">Нужен персональный менеджер</p>'
            : ''
        }
      </div>

      <div class="application-card-actions">
        <button type="button" class="btn btn-secondary btn-small" data-action="view" data-index="${index}">
          Просмотр
        </button>
      </div>
    </article>
  `;
}

function reasonsListHtml(item: ApplicationPriorityRead): string {
  if (item.reasons.length === 0) {
    return '<p class="admin-empty">Причины не рассчитаны.</p>';
  }
  const rows = item.reasons
    .map(
      (reason) => `
        <li>
          <span class="reason-label">${escapeHtml(reason.label)}</span>
          <span class="reason-points">${reasonPointsDisplay(reason.points)}</span>
        </li>
      `,
    )
    .join('');
  return `<ul class="reasons-list">${rows}</ul>`;
}

function applicationModalBodyTemplate(item: ApplicationPriorityRead): string {
  const app = item.application;
  return `
    <h2 id="application-modal-title">Заявка — ${escapeHtml(fullName(app))}</h2>

    <section class="modal-section">
      <h3>Клиент</h3>
      <dl>
        <div><dt>ФИО</dt><dd>${escapeHtml(fullName(app))}</dd></div>
        <div><dt>Контакты</dt><dd>${escapeHtml(app.contact_data)}</dd></div>
        <div><dt>Способ связи</dt><dd>${escapeHtml(app.preferred_contact_method)}</dd></div>
        <div><dt>Удобное время</dt><dd>${escapeHtml(app.preferred_contact_time)}</dd></div>
      </dl>
    </section>

    <section class="modal-section">
      <h3>Автомобиль</h3>
      <dl>
        <div><dt>Использование автомобиля</dt><dd>${escapeHtml(app.business_niche)}</dd></div>
        <div><dt>Класс автомобиля</dt><dd>${escapeHtml(app.company_size)}</dd></div>
        <div><dt>Количество автомобилей</dt><dd>${escapeHtml(app.business_size)}</dd></div>
        <div><dt>Кто обращается</dt><dd>${escapeHtml(app.requester_role)}</dd></div>
        <div><dt>Автомобиль и состояние</dt><dd>${escapeHtml(app.business_info)}</dd></div>
      </dl>
    </section>

    <section class="modal-section">
      <h3>Обращение</h3>
      <dl>
        <div><dt>Услуга</dt><dd>${escapeHtml(app.interested_product)}</dd></div>
        <div><dt>Бюджет</dt><dd>${escapeHtml(formatApplicationBudget(app.budget))}</dd></div>
        <div><dt>Формат обслуживания</dt><dd>${escapeHtml(app.task_scope)}</dd></div>
        <div><dt>Тип обращения</dt><dd>${escapeHtml(app.task_type)}</dd></div>
        <div><dt>Желаемый срок записи</dt><dd>${escapeHtml(app.deadline)}</dd></div>
        <div><dt>Пожелания и проблема</dt><dd>${escapeHtml(app.need_scope)}</dd></div>
        <div><dt>Комментарий</dt><dd>${app.comment ? escapeHtml(app.comment) : '—'}</dd></div>
        <div><dt>Дата создания</dt><dd>${escapeHtml(formatApplicationDate(app.created_at))}</dd></div>
      </dl>
    </section>

    <section class="modal-section">
      <h3>Приоритет обработки</h3>
      <p class="application-card-score">${priorityScoreDisplay(item.priority_score)} — ${priorityBadgeHtml(item)}</p>
      ${reasonsListHtml(item)}
      <p><strong>Рекомендуемое действие:</strong> ${escapeHtml(item.recommended_action)}</p>
      <p><strong>Рекомендуемая команда:</strong> ${escapeHtml(item.recommended_team)}</p>
      <p><strong>Персональный менеджер:</strong> ${
        item.requires_personal_manager ? 'Нужен' : 'Не требуется'
      }</p>
    </section>
  `;
}

function shellTemplate(): string {
  return `
    <div class="applications-toolbar">
      <div class="applications-filters" role="group" aria-label="Фильтр заявок по приоритету обработки">
        <button type="button" class="filter-chip" data-filter="all" aria-pressed="true">Все</button>
        <button type="button" class="filter-chip" data-filter="hot" aria-pressed="false">Высокий</button>
        <button type="button" class="filter-chip" data-filter="medium" aria-pressed="false">Средний</button>
        <button type="button" class="filter-chip" data-filter="low" aria-pressed="false">Стандартный</button>
      </div>
      <div class="applications-search-row">
        <label class="sr-only" for="applications-search">Поиск по заявкам</label>
        <input type="search" id="applications-search" placeholder="Имя, контакты, услуга, автомобиль…" />
        <button type="button" class="btn btn-secondary btn-small" id="applications-refresh">
          Обновить
        </button>
      </div>
    </div>

    <div id="applications-status" role="status" aria-live="polite"></div>
    <div class="applications-grid" id="applications-list"></div>

    <div class="modal-overlay" id="application-modal-overlay" hidden>
      <div
        class="modal-window"
        role="dialog"
        aria-modal="true"
        aria-labelledby="application-modal-title"
        id="application-modal"
      >
        <button
          type="button"
          class="modal-close"
          id="application-modal-close"
          aria-label="Закрыть окно заявки"
        >
          ✕
        </button>
        <div id="application-modal-body"></div>
      </div>
    </div>
  `;
}

// --- Rendering ----------------------------------------------------------

/**
 * Shows a transient status message (loading/error) and — crucially — drops
 * the old dataset along with it: `items`/`visibleItems` are cleared here,
 * not just the DOM, so a filter/search change made while this message is
 * showing (loadApplications's loading/error paths, both of which route
 * through here) can never re-filter a stale, previously-loaded set back
 * onto the screen (see renderList's loading/hasLoadError guard for the
 * error banner's own protection against being overwritten in turn).
 */
function setStatusMessage(container: HTMLElement, state: ApplicationsState, message: string): void {
  const statusEl = container.querySelector<HTMLElement>('#applications-status');
  const listEl = container.querySelector<HTMLElement>('#applications-list');
  state.items = [];
  state.visibleItems = [];
  if (statusEl) statusEl.innerHTML = `<p class="admin-empty">${escapeHtml(message)}</p>`;
  if (listEl) listEl.innerHTML = '';
}

function setRefreshDisabled(container: HTMLElement, disabled: boolean): void {
  const button = container.querySelector<HTMLButtonElement>('#applications-refresh');
  if (button) button.disabled = disabled;
}

function renderList(container: HTMLElement, state: ApplicationsState): void {
  const statusEl = container.querySelector<HTMLElement>('#applications-status');
  const listEl = container.querySelector<HTMLElement>('#applications-list');
  if (!statusEl || !listEl) return;

  if (state.items.length === 0) {
    state.visibleItems = [];
    // items is empty because a load is currently in flight or the last one
    // failed (see loadApplications/setStatusMessage) — that status message
    // already owns the (already-empty) list; a filter/search change must
    // not paint over it with "Заявок пока нет.".
    if (state.loading || state.hasLoadError) return;
    listEl.innerHTML = '';
    statusEl.innerHTML = '<p class="admin-empty">Заявок пока нет.</p>';
    return;
  }

  const filtered = filterApplications(state.items, state.filterLevel, state.searchQuery);
  state.visibleItems = filtered;

  if (filtered.length === 0) {
    listEl.innerHTML = '';
    statusEl.innerHTML =
      '<p class="admin-empty">Ничего не найдено. Попробуйте изменить фильтр или запрос.</p>';
    return;
  }

  statusEl.innerHTML = `<p class="applications-count">Найдено: ${filtered.length} из ${state.items.length}</p>`;
  listEl.innerHTML = filtered.map((item, index) => applicationCardTemplate(item, index)).join('');
}

async function loadApplications(
  container: HTMLElement,
  host: ApplicationsSectionHost,
  state: ApplicationsState,
  activationId: number,
  isStillCurrent: (activationId: number) => boolean,
): Promise<void> {
  // Refuses to start a second overlapping request — this alone guarantees
  // at most one in-flight fetch per activation, so a stale response can
  // never race a newer one within the same activation.
  if (state.loading) return;

  state.loading = true;
  // A fresh attempt supersedes any previous error — if it also ends in an
  // empty result, that's a legitimate "Заявок пока нет.", not a leftover
  // error state.
  state.hasLoadError = false;
  setStatusMessage(container, state, 'Загружаем заявки…');
  setRefreshDisabled(container, true);

  try {
    const response = await api.getPrioritizedApplications(0, 100);
    if (!isStillCurrent(activationId)) return;
    state.items = response.items;
    state.loading = false;
    renderList(container, state);
  } catch (error) {
    if (!isStillCurrent(activationId)) return;
    state.loading = false;
    if (isUnauthorizedError(error)) {
      host.onSessionExpired();
      return;
    }
    // Neutral message regardless of the underlying failure (network error,
    // 404/422/500, ...) — none of those should end the session, and the
    // backend's raw error text is not shown for a background list load.
    state.hasLoadError = true;
    setStatusMessage(
      container,
      state,
      'Не удалось загрузить заявки. Нажмите «Обновить», чтобы попробовать снова.',
    );
  } finally {
    if (isStillCurrent(activationId)) {
      setRefreshDisabled(container, false);
    }
  }
}

function openModal(
  container: HTMLElement,
  state: ApplicationsState,
  item: ApplicationPriorityRead,
  trigger: HTMLButtonElement,
): void {
  const overlay = container.querySelector<HTMLElement>('#application-modal-overlay');
  const body = container.querySelector<HTMLElement>('#application-modal-body');
  const closeButton = container.querySelector<HTMLButtonElement>('#application-modal-close');
  if (!overlay || !body || !closeButton) return;

  body.innerHTML = applicationModalBodyTemplate(item);
  overlay.hidden = false;
  state.lastFocusedTrigger = trigger;
  closeButton.focus();
}

/** restoreFocus is false when closing as a side effect of the tab becoming
 * inactive (deactivate/dispose) — the trigger button lives in a panel
 * that's about to be hidden, so focusing it would move focus into
 * invisible content. */
function closeModal(
  container: HTMLElement,
  state: ApplicationsState,
  { restoreFocus }: { restoreFocus: boolean } = { restoreFocus: true },
): void {
  const overlay = container.querySelector<HTMLElement>('#application-modal-overlay');
  if (!overlay || overlay.hidden) return;
  overlay.hidden = true;
  const trigger = state.lastFocusedTrigger;
  state.lastFocusedTrigger = null;
  if (restoreFocus) trigger?.focus();
}

function wireModal(container: HTMLElement, state: ApplicationsState): void {
  const overlay = container.querySelector<HTMLElement>('#application-modal-overlay');
  const closeButton = container.querySelector<HTMLButtonElement>('#application-modal-close');
  if (!overlay || !closeButton) return;

  closeButton.addEventListener('click', () => closeModal(container, state));

  // Backdrop click closes — but only when the click actually lands on the
  // overlay itself, not somewhere inside the modal window.
  overlay.addEventListener('click', (event) => {
    if (event.target === overlay) closeModal(container, state);
  });

  overlay.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeModal(container, state);
  });
}

function wireListDelegation(
  container: HTMLElement,
  state: ApplicationsState,
  isInteractive: () => boolean,
): void {
  const listEl = container.querySelector<HTMLElement>('#applications-list');
  if (!listEl) return;

  listEl.addEventListener('click', (event) => {
    if (!isInteractive()) return;
    const target = event.target;
    if (!(target instanceof Element)) return;
    const button = target.closest('button[data-action="view"]');
    if (!(button instanceof HTMLButtonElement)) return;

    // The card is bound to its item via a frontend-generated array index —
    // never via backend-supplied application.id — so a malformed/hostile
    // id can never end up driving a lookup, let alone the DOM.
    const index = Number(button.dataset.index);
    if (!Number.isInteger(index) || index < 0 || index >= state.visibleItems.length) return;
    const item = state.visibleItems[index];
    if (!item) return;

    openModal(container, state, item, button);
  });
}

function wireControls(
  container: HTMLElement,
  state: ApplicationsState,
  isInteractive: () => boolean,
  requestReload: () => void,
): void {
  const filterButtons = container.querySelectorAll<HTMLButtonElement>('.filter-chip');
  filterButtons.forEach((button) => {
    button.addEventListener('click', () => {
      if (!isInteractive()) return;
      const level = button.dataset.filter as FilterLevel | undefined;
      if (!level) return;
      state.filterLevel = level;
      filterButtons.forEach((btn) => btn.setAttribute('aria-pressed', String(btn === button)));
      renderList(container, state);
    });
  });

  const searchInput = container.querySelector<HTMLInputElement>('#applications-search');
  searchInput?.addEventListener('input', () => {
    if (!isInteractive()) return;
    state.searchQuery = searchInput.value;
    renderList(container, state);
  });

  const refreshButton = container.querySelector<HTMLButtonElement>('#applications-refresh');
  refreshButton?.addEventListener('click', () => {
    if (!isInteractive()) return;
    requestReload();
  });

  wireListDelegation(container, state, isInteractive);
  wireModal(container, state);
}

/**
 * Mounts the applications section into `container`, wiring every listener
 * exactly once, and returns a controller for admin.ts's tab switcher to
 * drive as the admin moves between "Услуги" and "Заявки":
 *
 * - activate(): call when the "Заявки" tab becomes the visible one. Starts
 *   a fresh load under a new local activation id.
 * - deactivate(): call when the admin switches away. Bumps the activation
 *   id (so any still-in-flight request from the old activation is a no-op
 *   when it resolves/rejects), resets the loading flag (so a future
 *   activate() isn't blocked by a request that's being ignored anyway),
 *   and closes the modal without moving focus into the now-hidden panel.
 * - dispose(): call when the mount itself is going away for good (logout,
 *   session expiry, a fresh admin panel render). Same invalidation as
 *   deactivate(); this module registers no document-level listeners, so
 *   there is nothing else to remove.
 *
 * Every listener wired here is scoped to elements inside `container`, so a
 * full admin-panel re-render (which replaces root's innerHTML) discards
 * them together with the DOM regardless of dispose() having been called.
 */
export function mountAdminApplications(
  container: HTMLElement,
  host: ApplicationsSectionHost,
): ApplicationsSectionController {
  const mountId = beginAppsRender();
  const state: ApplicationsState = {
    items: [],
    visibleItems: [],
    filterLevel: 'all',
    searchQuery: '',
    loading: false,
    hasLoadError: false,
    lastFocusedTrigger: null,
  };

  let tabActive = false;
  let currentActivationId = 0;

  function isStillCurrent(activationId: number): boolean {
    return activationId === currentActivationId && isCurrentAppsRender(mountId) && host.isActive();
  }

  function isInteractive(): boolean {
    return tabActive && isCurrentAppsRender(mountId) && host.isActive();
  }

  container.innerHTML = shellTemplate();
  wireControls(container, state, isInteractive, () => {
    void loadApplications(container, host, state, currentActivationId, isStillCurrent);
  });

  function activate(): void {
    tabActive = true;
    currentActivationId += 1;
    const activationId = currentActivationId;
    void loadApplications(container, host, state, activationId, isStillCurrent);
  }

  function deactivate(): void {
    tabActive = false;
    currentActivationId += 1;
    state.loading = false;
    closeModal(container, state, { restoreFocus: false });
  }

  function dispose(): void {
    tabActive = false;
    currentActivationId += 1;
    state.loading = false;
    closeModal(container, state, { restoreFocus: false });
  }

  return { activate, deactivate, dispose };
}
