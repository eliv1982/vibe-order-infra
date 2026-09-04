/**
 * "Заявки" (client applications) section of the admin panel.
 *
 * Owns everything specific to browsing prioritized applications: loading,
 * filter/search, cards, and the detail modal. Auth, the render generation
 * guard, logout and the "Услуги" section stay in admin.ts — this module
 * never touches sessionStorage/tokenStorage or the auth flow directly; a
 * 401 is reported upward via ApplicationsSectionHost so admin.ts remains
 * the single place that ends a session.
 *
 * Score is never recomputed here — every number/label rendered comes
 * straight from GET /api/applications/prioritized, already sorted by the
 * backend.
 *
 * Stage 4 correction: the priority-level chips and the search box are both
 * sent to the backend as `priority`/`search` query params (see
 * api.getPrioritizedApplications) and applied there before pagination -
 * `state.items` is always exactly one already-filtered, already-paginated
 * page, never re-filtered client-side. Earlier Stage 4 filtered only
 * whatever page was already loaded (filterApplications/matchesSearch, since
 * removed) - correct on a single-page dataset, but presented as
 * application-wide while a match sitting on a later unfiltered page was
 * invisible from page 1, and a real corpus-wide zero was indistinguishable
 * from that. Changing either criterion resets to the first page (see
 * wireControls) and re-fetches; search is debounced (see
 * SEARCH_DEBOUNCE_MS) and, like every other trigger for a new page load,
 * goes through state.loadGeneration so a slow, now-stale response can never
 * overwrite a newer one (see loadApplications).
 *
 * Mounted once (see mountAdminApplications) by admin.ts's tab switcher; the
 * returned controller's activate()/deactivate()/dispose() then track
 * whether the "Заявки" tab is the one currently visible, independently of
 * admin.ts's own auth/session generation (see ApplicationsSectionHost).
 */

import { api, isUnauthorizedError, type PrioritizedApplicationsQuery } from '../api/client';
import type { ApplicationBehaviorAnalytics, ApplicationPriorityRead, ApplicationRead, PriorityLevel } from '../api/types';
import { escapeHtml } from '../utils/html';
import { formatBudget } from '../utils/format';
import {
  applyBarWidths,
  buttonAnalyticsListHtml,
  formatAnalyticsDateTime,
  formatCount,
  formatSeconds,
  sectionAnalyticsListHtml,
} from './adminAnalytics';

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

/** Fixed page size for GET /applications/prioritized — matches the previous
 * hardcoded default (see PrioritizedApplicationList's skip/limit/total
 * fields, api/types.ts) so a dataset that fits on one page renders exactly
 * as before; the difference is Stage 4's Prev/Next controls (see
 * renderPager) that now let the admin reach rows beyond it instead of the
 * list silently stopping at the first 100. */
const APPLICATIONS_PAGE_SIZE = 100;

/** Debounce delay for the search input (Stage 4 correction) — long enough
 * that a normal typing cadence sends one request per pause, not one per
 * keystroke, short enough that the result still feels immediate. Priority
 * chip clicks skip this entirely (see wireControls) — a single discrete
 * click is never rapid-fire the way typing is. */
const SEARCH_DEBOUNCE_MS = 350;

interface ApplicationsState {
  /** Exactly one backend-filtered, backend-paginated page — never
   * re-filtered client-side (see this module's docstring). The "Просмотр"
   * button's data-index refers directly into this array. */
  items: ApplicationPriorityRead[];
  /** Offset of `items`' first row within the full backend-ordered,
   * backend-filtered set (the `skip` GET /applications/prioritized was
   * last called with) — 0 is the first page. */
  skip: number;
  /** Total row count across every page of the *current* filter/search
   * criteria, as last reported by the backend (PrioritizedApplicationList.
   * total) — null before the first successful load. Used to size/enable
   * the pager and to tell a genuine corpus-wide zero apart from "still
   * loading"; never assumed stable across a reload (a concurrent admin/
   * applicant can change it, and it changes whenever the criteria do). */
  total: number | null;
  filterLevel: FilterLevel;
  searchQuery: string;
  loading: boolean;
  /** True only while the most recent load attempt ended in a (non-401)
   * error — distinct from `items` legitimately being empty, so renderList
   * knows not to paper over the error banner with "Заявок пока нет." if the
   * admin touches the filter/search while no successful dataset exists. */
  hasLoadError: boolean;
  lastFocusedTrigger: HTMLButtonElement | null;
  /** Bumped every time the modal opens for a (possibly different)
   * application, and whenever it closes — the in-flight behavior-analytics
   * detail request captures this value and only ever paints if it's still
   * current when the response arrives (see loadApplicationAnalytics). */
  modalGeneration: number;
  /** Guards against a second overlapping detail request for the same modal
   * generation (e.g. a rapid double-click on the "Повторить" retry button). */
  modalAnalyticsLoading: boolean;
  /** Bumped on every loadApplications() call, regardless of trigger (tab
   * activation, refresh, pager, or a filter/search change) — the response
   * only ever gets applied if it's still the latest generation when it
   * arrives, so a slow, now-stale response (e.g. an earlier search value)
   * can never overwrite a newer one, even if requests resolve out of
   * order. */
  loadGeneration: number;
  /** Pending debounce timer for a search-input change (see wireControls) —
   * cleared on deactivate()/dispose() so a stray reload can never fire
   * after the tab becomes inactive or the mount is torn down. */
  searchDebounceTimer: ReturnType<typeof setTimeout> | undefined;
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

/** Pure — unit tested. True while either criterion would narrow the result
 * below "every application" — drives the choice between "Заявок пока нет."
 * (a genuinely empty, unfiltered corpus) and "Ничего не найдено." (this
 * criteria combination has zero matches) in renderList, and the optional
 * "Найдено: N" line above the grid. */
export function hasActiveCriteria(filterLevel: FilterLevel, searchQuery: string): boolean {
  return filterLevel !== 'all' || searchQuery.trim() !== '';
}

/** Pure — unit tested. The exact query GET /applications/prioritized is
 * called with for a given filter/search state: blank/whitespace-only search
 * and the 'all' level both mean "no criterion", represented by simply
 * omitting that key (see api.getPrioritizedApplications) rather than
 * sending an empty string or the literal 'all'. */
export function buildPrioritizedQuery(
  filterLevel: FilterLevel,
  searchQuery: string,
): PrioritizedApplicationsQuery {
  const query: PrioritizedApplicationsQuery = {};
  const trimmedSearch = searchQuery.trim();
  if (trimmedSearch !== '') query.search = trimmedSearch;
  if (filterLevel !== 'all') query.priority = filterLevel;
  return query;
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

    <section class="modal-section">
      <h3>Поведение на странице</h3>
      <div id="application-modal-analytics-content" role="status" aria-live="polite">
        <p class="admin-empty">Загружаем поведенческие метрики…</p>
      </div>
    </section>
  `;
}

// --- Behavior analytics detail (lazy-loaded after the modal opens) --------
// Every number/label rendered here comes straight from
// GET /api/analytics/applications/{id} — reuses the same formatting/list
// helpers as the "Статистика" tab (see adminAnalytics.ts) so the two never
// drift apart, and never repeats the period-level overview KPIs.

// Stage 4 correction: detail.return_count is the value of a per-device
// localStorage visit counter at submission time (see adminAnalytics.ts's
// "Визиты с устройства" KPI group for the full explanation the label below
// deliberately mirrors), not a count of returns to this specific form.
function applicationAnalyticsDetailHtml(detail: ApplicationBehaviorAnalytics): string {
  if (!detail.has_metrics) {
    return '<p class="admin-empty">Для этой заявки поведенческие метрики не записаны.</p>';
  }

  const summaryHtml = `
    <dl>
      <div><dt>Время на странице</dt><dd>${escapeHtml(formatSeconds(detail.time_on_page_seconds))}</dd></div>
      <div><dt>Счётчик визитов (устройство)</dt><dd>${escapeHtml(formatCount(detail.return_count))}</dd></div>
      <div><dt>Кликов по кнопкам</dt><dd>${escapeHtml(formatCount(detail.total_button_clicks))}</dd></div>
      <div><dt>Метрика записана</dt><dd>${escapeHtml(formatAnalyticsDateTime(detail.recorded_at))}</dd></div>
    </dl>
  `;

  const buttonsHtml = buttonAnalyticsListHtml(detail.clicked_buttons, 'Кнопки не нажимались.');
  const sectionsHtml = sectionAnalyticsListHtml(
    detail.section_activity,
    'Активность по секциям не зафиксирована.',
  );

  return `
    ${summaryHtml}
    <h4>Нажатые кнопки</h4>
    ${buttonsHtml}
    <h4>Активность по секциям</h4>
    ${sectionsHtml}
  `;
}

function applicationAnalyticsErrorHtml(): string {
  return `
    <p class="admin-empty">Не удалось загрузить поведенческие метрики.</p>
    <button type="button" class="btn btn-secondary btn-small" id="application-modal-analytics-retry">
      Повторить
    </button>
  `;
}

/** Bumps the modal generation (invalidating any in-flight detail request)
 * and resets the in-flight flag together, so the very next load this modal
 * starts is never blocked by a stale request that will now be ignored when
 * it resolves — mirrors adminAnalytics.ts's period-switch invalidation. */
function bumpModalGeneration(state: ApplicationsState): number {
  state.modalGeneration += 1;
  state.modalAnalyticsLoading = false;
  return state.modalGeneration;
}

async function loadApplicationAnalytics(
  container: HTMLElement,
  host: ApplicationsSectionHost,
  state: ApplicationsState,
  applicationId: number,
  generation: number,
): Promise<void> {
  if (state.modalAnalyticsLoading) return;

  function isStillCurrent(): boolean {
    return generation === state.modalGeneration && host.isActive();
  }

  const contentEl = container.querySelector<HTMLElement>('#application-modal-analytics-content');
  if (!contentEl) return;

  state.modalAnalyticsLoading = true;
  contentEl.innerHTML = '<p class="admin-empty">Загружаем поведенческие метрики…</p>';

  try {
    const detail = await api.getApplicationBehaviorAnalytics(applicationId);
    if (!isStillCurrent()) return;
    contentEl.innerHTML = applicationAnalyticsDetailHtml(detail);
    applyBarWidths(contentEl);
  } catch (error) {
    if (!isStillCurrent()) return;
    if (isUnauthorizedError(error)) {
      host.onSessionExpired();
      return;
    }
    contentEl.innerHTML = applicationAnalyticsErrorHtml();
    contentEl
      .querySelector<HTMLButtonElement>('#application-modal-analytics-retry')
      ?.addEventListener('click', () => {
        if (!isStillCurrent()) return;
        void loadApplicationAnalytics(container, host, state, applicationId, generation);
      });
  } finally {
    if (generation === state.modalGeneration) {
      state.modalAnalyticsLoading = false;
    }
  }
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
        <input
          type="search"
          id="applications-search"
          placeholder="Имя, контакты, услуга, автомобиль…"
          maxlength="200"
        />
        <button type="button" class="btn btn-secondary btn-small" id="applications-refresh">
          Обновить
        </button>
      </div>
    </div>

    <div id="applications-status" role="status" aria-live="polite"></div>
    <div class="applications-grid" id="applications-list"></div>

    <div class="applications-pager" id="applications-pager" role="status" aria-live="polite"></div>

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
 * the old dataset along with it: `items` is cleared here, not just the DOM,
 * so the previous page/criteria's rows can never be resurrected onto the
 * screen while a new request for different criteria is in flight (see
 * renderList's loading/hasLoadError guard for the error banner's own
 * protection against being overwritten in turn).
 */
function setStatusMessage(container: HTMLElement, state: ApplicationsState, message: string): void {
  const statusEl = container.querySelector<HTMLElement>('#applications-status');
  const listEl = container.querySelector<HTMLElement>('#applications-list');
  state.items = [];
  if (statusEl) statusEl.innerHTML = `<p class="admin-empty">${escapeHtml(message)}</p>`;
  if (listEl) listEl.innerHTML = '';
}

function setRefreshDisabled(container: HTMLElement, disabled: boolean): void {
  const button = container.querySelector<HTMLButtonElement>('#applications-refresh');
  if (button) button.disabled = disabled;
}

/**
 * Prev/Next + "N–M из T" range — the backend already returns skip/limit/
 * total on every load (PrioritizedApplicationList, api/types.ts); before
 * Stage 4 the admin UI simply never asked for anything past the first
 * page. Rendered from `state.skip`/`state.total`/`state.items.length`
 * alone (never re-derives them from the DOM), and hidden entirely while
 * there is no successful load to page through yet (loading, error, or
 * before the first response — see loadApplications).
 */
function renderPager(container: HTMLElement, state: ApplicationsState): void {
  const pagerEl = container.querySelector<HTMLElement>('#applications-pager');
  if (!pagerEl) return;

  if (state.total === null || state.loading || state.hasLoadError) {
    pagerEl.innerHTML = '';
    return;
  }

  const from = state.total === 0 ? 0 : state.skip + 1;
  const to = state.skip + state.items.length;
  const hasPrev = state.skip > 0;
  const hasNext = state.skip + state.items.length < state.total;

  pagerEl.innerHTML = `
    <button type="button" class="btn btn-secondary btn-small" id="applications-prev" ${hasPrev ? '' : 'disabled'}>
      Назад
    </button>
    <span class="applications-pager-range">Заявки ${from}–${to} из ${state.total}</span>
    <button type="button" class="btn btn-secondary btn-small" id="applications-next" ${hasNext ? '' : 'disabled'}>
      Далее
    </button>
  `;
}

/**
 * Renders exactly what the last successful load returned — `state.items` is
 * already the backend-filtered, backend-paginated page, so this never
 * re-filters it. Only called right after a successful load (see
 * loadApplications), so `state.loading`/`state.hasLoadError` are always
 * false here in practice; the empty-vs-"Ничего не найдено" choice below
 * still has to happen somewhere, so it lives here rather than in
 * loadApplications itself.
 */
function renderList(container: HTMLElement, state: ApplicationsState): void {
  const statusEl = container.querySelector<HTMLElement>('#applications-status');
  const listEl = container.querySelector<HTMLElement>('#applications-list');
  if (!statusEl || !listEl) return;

  if (state.items.length === 0) {
    listEl.innerHTML = '';
    // A genuine corpus-wide zero for the *active* criteria (backend-
    // reported, see loadApplications) — "Ничего не найдено" only ever
    // reflects that, never a merely-empty current page of an otherwise
    // non-empty filtered result (impossible here: an empty page can only
    // happen at skip=0, since wirePager never lets skip advance past
    // state.total).
    statusEl.innerHTML = hasActiveCriteria(state.filterLevel, state.searchQuery)
      ? '<p class="admin-empty">Ничего не найдено. Попробуйте изменить фильтр или запрос.</p>'
      : '<p class="admin-empty">Заявок пока нет.</p>';
    return;
  }

  statusEl.innerHTML = hasActiveCriteria(state.filterLevel, state.searchQuery)
    ? `<p class="applications-count">Найдено: ${state.total ?? state.items.length}</p>`
    : '';
  listEl.innerHTML = state.items.map((item, index) => applicationCardTemplate(item, index)).join('');
}

async function loadApplications(
  container: HTMLElement,
  host: ApplicationsSectionHost,
  state: ApplicationsState,
  activationId: number,
  isStillCurrent: (activationId: number) => boolean,
): Promise<void> {
  // A fresh generation for this specific request — captured below and
  // compared again once the response arrives, so a response is only ever
  // applied if no newer load (a later criteria change, pager click, or
  // refresh) has started since. Deliberately does NOT refuse to start
  // while state.loading is already true: unlike the refresh button and the
  // pager (which each guard against redundant clicks themselves — see
  // wireControls/wirePager), a filter/search change must always be able to
  // supersede an in-flight request for the previous criteria, not be
  // silently dropped by it.
  state.loadGeneration += 1;
  const generation = state.loadGeneration;

  state.loading = true;
  // A fresh attempt supersedes any previous error — if it also ends in an
  // empty result, that's a legitimate "Заявок пока нет.", not a leftover
  // error state.
  state.hasLoadError = false;
  setStatusMessage(container, state, 'Загружаем заявки…');
  setRefreshDisabled(container, true);
  renderPager(container, state);

  function isStillTheCurrentLoad(): boolean {
    return generation === state.loadGeneration && isStillCurrent(activationId);
  }

  try {
    const response = await api.getPrioritizedApplications(
      state.skip,
      APPLICATIONS_PAGE_SIZE,
      buildPrioritizedQuery(state.filterLevel, state.searchQuery),
    );
    if (!isStillTheCurrentLoad()) return;
    state.items = response.items;
    // Reflects exactly what this response says, including `skip` — a
    // Prev/Next click already set state.skip before this call started (see
    // wirePager), but this keeps state.skip authoritative from the
    // backend's own echo rather than trusting the locally-computed value if
    // the two were ever to disagree.
    state.skip = response.skip;
    state.total = response.total;
    state.loading = false;
    renderList(container, state);
    renderPager(container, state);
  } catch (error) {
    if (!isStillTheCurrentLoad()) return;
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
    renderPager(container, state);
  } finally {
    if (isStillTheCurrentLoad()) {
      setRefreshDisabled(container, false);
    }
  }
}

function openModal(
  container: HTMLElement,
  state: ApplicationsState,
  host: ApplicationsSectionHost,
  item: ApplicationPriorityRead,
  trigger: HTMLButtonElement,
): void {
  const overlay = container.querySelector<HTMLElement>('#application-modal-overlay');
  const body = container.querySelector<HTMLElement>('#application-modal-body');
  const closeButton = container.querySelector<HTMLButtonElement>('#application-modal-close');
  if (!overlay || !body || !closeButton) return;

  // A fresh generation — invalidates whatever detail request the
  // previously-open application (if any) may have had in flight.
  const generation = bumpModalGeneration(state);

  body.innerHTML = applicationModalBodyTemplate(item);
  overlay.hidden = false;
  state.lastFocusedTrigger = trigger;
  closeButton.focus();

  void loadApplicationAnalytics(container, host, state, item.application.id, generation);
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
  // Invalidates any pending/in-flight behavior-analytics detail request for
  // the application that was just closed.
  bumpModalGeneration(state);
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
  host: ApplicationsSectionHost,
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
    if (!Number.isInteger(index) || index < 0 || index >= state.items.length) return;
    const item = state.items[index];
    if (!item) return;

    openModal(container, state, host, item, button);
  });
}

/**
 * Prev/Next clicks — delegated on the pager's static container (like
 * wireListDelegation above), since renderPager replaces its innerHTML on
 * every load and a listener bound directly to a button would be discarded
 * along with it. Ignored while a load is already in flight, or once the
 * clicked direction is no longer available (state.total may have changed
 * since the buttons were last rendered - e.g. a concurrent admin deleted a
 * row - so this re-checks the boundary itself rather than trusting the
 * (possibly now-stale) disabled attribute alone).
 */
function wirePager(
  container: HTMLElement,
  state: ApplicationsState,
  isInteractive: () => boolean,
  requestReload: () => void,
): void {
  const pagerEl = container.querySelector<HTMLElement>('#applications-pager');
  if (!pagerEl) return;

  pagerEl.addEventListener('click', (event) => {
    if (!isInteractive() || state.loading) return;
    const target = event.target;
    if (!(target instanceof Element)) return;

    if (target.closest('#applications-prev')) {
      if (state.skip <= 0) return;
      state.skip = Math.max(0, state.skip - APPLICATIONS_PAGE_SIZE);
      requestReload();
    } else if (target.closest('#applications-next')) {
      if (state.total === null || state.skip + state.items.length >= state.total) return;
      state.skip += APPLICATIONS_PAGE_SIZE;
      requestReload();
    }
  });
}

/** Clears any pending search-debounce timer without firing it — used
 * whenever a criteria change is about to trigger its own immediate reload
 * (so a stale debounced reload can't also fire moments later) and on
 * deactivate()/dispose() (so one can never fire once the tab is hidden or
 * the mount is gone). */
function clearSearchDebounce(state: ApplicationsState): void {
  if (state.searchDebounceTimer !== undefined) {
    clearTimeout(state.searchDebounceTimer);
    state.searchDebounceTimer = undefined;
  }
}

function wireControls(
  container: HTMLElement,
  state: ApplicationsState,
  host: ApplicationsSectionHost,
  isInteractive: () => boolean,
  requestReload: () => void,
): void {
  /** Shared by both criteria: back to the first page, then reload — used
   * directly by the (undebounced) priority chips, and by the search input
   * after its debounce timer fires. */
  function requestFilteredReload(): void {
    state.skip = 0;
    requestReload();
  }

  const filterButtons = container.querySelectorAll<HTMLButtonElement>('.filter-chip');
  filterButtons.forEach((button) => {
    button.addEventListener('click', () => {
      if (!isInteractive()) return;
      const level = button.dataset.filter as FilterLevel | undefined;
      if (!level || level === state.filterLevel) return;
      state.filterLevel = level;
      filterButtons.forEach((btn) => btn.setAttribute('aria-pressed', String(btn === button)));
      // A priority chip is a single discrete click, not rapid-fire typing —
      // no debounce, and any pending debounced search reload is superseded
      // by this one (both criteria are always sent together, see
      // buildPrioritizedQuery).
      clearSearchDebounce(state);
      requestFilteredReload();
    });
  });

  const searchInput = container.querySelector<HTMLInputElement>('#applications-search');
  searchInput?.addEventListener('input', () => {
    if (!isInteractive()) return;
    state.searchQuery = searchInput.value;
    clearSearchDebounce(state);
    state.searchDebounceTimer = setTimeout(() => {
      state.searchDebounceTimer = undefined;
      if (!isInteractive()) return;
      requestFilteredReload();
    }, SEARCH_DEBOUNCE_MS);
  });

  const refreshButton = container.querySelector<HTMLButtonElement>('#applications-refresh');
  refreshButton?.addEventListener('click', () => {
    // Explicit state.loading dedup (unlike a filter/search change, which
    // must always supersede an in-flight load — see loadApplications):
    // rapid repeat clicks on the same button while a request is already in
    // flight for the exact same criteria/page should not each queue their
    // own redundant request.
    if (!isInteractive() || state.loading) return;
    requestReload();
  });

  wireListDelegation(container, state, host, isInteractive);
  wirePager(container, state, isInteractive, requestReload);
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
    skip: 0,
    total: null,
    filterLevel: 'all',
    searchQuery: '',
    loading: false,
    hasLoadError: false,
    lastFocusedTrigger: null,
    modalGeneration: 0,
    modalAnalyticsLoading: false,
    loadGeneration: 0,
    searchDebounceTimer: undefined,
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
  wireControls(container, state, host, isInteractive, () => {
    void loadApplications(container, host, state, currentActivationId, isStillCurrent);
  });

  function activate(): void {
    tabActive = true;
    currentActivationId += 1;
    const activationId = currentActivationId;
    // Every (re-)activation starts back at the first page - mirrors the
    // pre-Stage-4 behavior of always requesting skip=0, and avoids showing
    // a Prev/Next state left over from whatever page the admin was on the
    // last time this tab was active.
    state.skip = 0;
    void loadApplications(container, host, state, activationId, isStillCurrent);
  }

  function deactivate(): void {
    tabActive = false;
    currentActivationId += 1;
    state.loading = false;
    clearSearchDebounce(state);
    closeModal(container, state, { restoreFocus: false });
  }

  function dispose(): void {
    tabActive = false;
    currentActivationId += 1;
    state.loading = false;
    clearSearchDebounce(state);
    closeModal(container, state, { restoreFocus: false });
  }

  return { activate, deactivate, dispose };
}
