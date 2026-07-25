/**
 * "Статистика" (behavior analytics) section of the admin panel.
 *
 * Owns everything specific to the protected analytics overview: period
 * selection, loading, KPI cards, popular buttons, section activity, and
 * loading/error/empty states. Auth, the render generation guard, logout and
 * the other admin sections stay in admin.ts — this module never touches
 * sessionStorage/tokenStorage or the auth flow directly; a 401 is reported
 * upward via AnalyticsSectionHost so admin.ts remains the single place that
 * ends a session.
 *
 * Every number/label rendered comes straight from
 * GET /api/analytics/overview — nothing is recomputed on the frontend, and
 * no metric absent from the backend response (conversion rate, visitor
 * counts, a coordinate heatmap, ...) is ever fabricated here.
 *
 * Mounted once by admin.ts's tab switcher (mirrors adminApplications.ts's
 * mountAdminApplications); the returned controller's
 * activate()/deactivate()/dispose() then track whether the "Статистика" tab
 * is the one currently visible, independently of admin.ts's own auth/
 * session generation (see AnalyticsSectionHost).
 *
 * The pure formatting/rendering helpers below (formatCount, formatSeconds,
 * buttonAnalyticsListHtml, sectionAnalyticsListHtml, ...) are also imported
 * by adminApplications.ts to render one application's behavior detail in
 * its modal — the single source of truth for how these backend types are
 * displayed, so overview and detail never drift apart.
 */

import { api, isUnauthorizedError } from '../api/client';
import type { AnalyticsOverview, AnalyticsPeriod, ButtonAnalyticsItem, SectionAnalyticsItem } from '../api/types';
import { escapeHtml } from '../utils/html';

export interface AnalyticsSectionHost {
  /** Mirrors admin.ts's own render-generation guard — true while this
   * section's mount is still the one the admin panel is showing (false
   * once a newer renderAdmin()/logout/session-expiry has taken over). */
  isActive: () => boolean;
  /** admin.ts owns ending the session (the token is already cleared
   * centrally in api/client.ts on any 401) — this just asks it to show
   * the login view. */
  onSessionExpired: () => void;
}

/** Returned by mountAdminAnalytics — lets admin.ts's tab switcher tell this
 * section when the "Статистика" tab becomes the visible one (activate),
 * when the admin navigates away from it (deactivate), and when the whole
 * mount is being torn down for good (dispose). */
export interface AnalyticsSectionController {
  activate: () => void;
  deactivate: () => void;
  dispose: () => void;
}

interface AnalyticsState {
  period: AnalyticsPeriod;
  overview: AnalyticsOverview | null;
  loading: boolean;
  hasLoadError: boolean;
}

/**
 * Mount-level generation guard (mirrors adminApplications.ts's
 * appsGeneration/isCurrentAppsRender pair) — guards against a hypothetical
 * second mount into a still-live container superseding this one.
 */
let analyticsGeneration = 0;

function beginAnalyticsRender(): number {
  analyticsGeneration += 1;
  return analyticsGeneration;
}

function isCurrentAnalyticsRender(mountId: number): boolean {
  return mountId === analyticsGeneration;
}

// --- Formatting helpers (pure, unit tested) --------------------------------
// Every field on AnalyticsOverview/ApplicationBehaviorAnalytics is typed as
// number/string at compile time, but that's a compile-time promise only —
// these helpers treat the actual runtime value as `unknown` and degrade to
// a neutral display ("—") instead of ever rendering NaN/Infinity/undefined/
// null/"[object Object]".

const PERIOD_LABELS: Record<AnalyticsPeriod, string> = {
  day: '24 часа',
  week: '7 дней',
  month: '30 дней',
};

export function analyticsPeriodLabel(period: AnalyticsPeriod): string {
  return PERIOD_LABELS[period];
}

/** Pure — unit tested. Backend count fields are ints — only a finite,
 * non-negative *integer* is a valid count. A fractional runtime value
 * (1.5, 1.6, ...) is schema drift/malformed data, not a value to silently
 * round — it is rejected here rather than coerced. */
export function normalizeCount(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value) || !Number.isInteger(value) || value < 0) {
    return null;
  }
  return value;
}

/** Pure — unit tested. Integer display; never rounds — normalizeCount
 * already guarantees an integer or null, so a fractional/malformed count
 * degrades to "—" instead of being rounded into a misleadingly precise
 * whole number. */
export function formatCount(value: unknown): string {
  const normalized = normalizeCount(value);
  return normalized === null ? '—' : String(normalized);
}

/** Pure — unit tested. "42 сек" / "3 мин 12 сек" / "1 ч 5 мин"; 0 -> "0 сек";
 * null/malformed/negative/non-finite -> "—". */
export function formatSeconds(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return '—';
  const totalSeconds = Math.round(value);
  if (totalSeconds < 60) return `${totalSeconds} сек`;
  if (totalSeconds < 3600) {
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${minutes} мин ${seconds} сек`;
  }
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  return `${hours} ч ${minutes} мин`;
}

const decimalFormatter = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });

/** Pure — unit tested. Up to 2 decimals, trailing zeros trimmed by
 * Intl.NumberFormat itself (1.5 -> "1,5", 2 -> "2"); invalid -> "—". */
export function formatAverageReturns(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return '—';
  return decimalFormatter.format(value);
}

/** Pure — unit tested. Only a finite number is usable at all (for either the
 * bar or the text) — NaN/Infinity/non-number always degrade to null. */
export function normalizePercent(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return value;
}

/** Pure — unit tested. Clamped 0..100 for the visual bar width only;
 * malformed input renders a zero-width (not missing) bar. */
export function percentBarWidth(value: unknown): number {
  const normalized = normalizePercent(value);
  if (normalized === null) return 0;
  return Math.min(100, Math.max(0, normalized));
}

/** Pure — unit tested. Text form is NOT clamped (an out-of-range value is
 * still shown as-is, e.g. "150%") — only malformed input falls back to "—". */
export function formatPercent(value: unknown): string {
  const normalized = normalizePercent(value);
  return normalized === null ? '—' : `${decimalFormatter.format(normalized)}%`;
}

/** Pure — unit tested. Trims a button/section name; non-string or blank
 * input falls back to a neutral label instead of "[object Object]"/"". */
export function formatAnalyticsName(value: unknown, fallback = 'Без названия'): string {
  if (typeof value !== 'string') return fallback;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : fallback;
}

/**
 * Allowlist mapping from a collector `clicked_buttons[].name` wire
 * identifier (see frontend/src/pages/home.ts's `trackClick()` call sites)
 * to its Russian display label. Deliberately an exact-match lookup — a
 * button identifier not in this map is not a typo/variant of one that is,
 * so it is never merged (case-insensitively or otherwise) with a known
 * one; it is shown as its own (trimmed, escaped) text instead. */
const BUTTON_ANALYTICS_NAME_MAP: Readonly<Record<string, string>> = {
  hero_cta: 'Основная кнопка на главном экране',
  service_card: 'Выбор услуги',
  submit_application: 'Отправка заявки',
  change_service: 'Смена услуги',
};

/** Same allowlist convention as BUTTON_ANALYTICS_NAME_MAP, for a
 * `section_activity[].section` / `cursor_hover_data` key (see
 * frontend/src/pages/home.ts's `data-hover-section` attributes). */
const SECTION_ANALYTICS_NAME_MAP: Readonly<Record<string, string>> = {
  application_form: 'Форма заявки',
  services: 'Раздел услуг',
  hero: 'Главный экран',
};

/** Pure — unit tested. Wraps formatAnalyticsName with a "—" fallback (this
 * display context has no separate "Без названия"-style placeholder), then
 * maps a *known* collector button identifier to its Russian label. An
 * unrecognized-but-otherwise-safe string (already trimmed/normalized by
 * formatAnalyticsName) is shown as-is — never silently dropped, never
 * guessed into a known label. The wire value itself (item.name) is never
 * touched; only this rendered copy changes. */
export function formatButtonAnalyticsName(value: unknown): string {
  const normalized = formatAnalyticsName(value, '—');
  if (normalized === '—') return normalized;
  return BUTTON_ANALYTICS_NAME_MAP[normalized] ?? normalized;
}

/** Pure — unit tested. Section-name counterpart of formatButtonAnalyticsName. */
export function formatSectionAnalyticsName(value: unknown): string {
  const normalized = formatAnalyticsName(value, '—');
  if (normalized === '—') return normalized;
  return SECTION_ANALYTICS_NAME_MAP[normalized] ?? normalized;
}

const dateOnlyFormatter = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit',
  month: 'long',
  year: 'numeric',
});

const dateTimeFormatter = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit',
  month: 'long',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
});

function parseValidDate(value: unknown): Date | null {
  if (typeof value !== 'string') return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** Pure — unit tested. Never throws; invalid/malformed input -> "—". Always
 * formatted in the browser's local timezone (Intl.DateTimeFormat's default),
 * so every date shown in this admin panel is consistent with the others. */
export function formatAnalyticsDate(value: unknown): string {
  const date = parseValidDate(value);
  return date ? dateOnlyFormatter.format(date) : '—';
}

/** Pure — unit tested. Date + time, for a metric's recorded_at timestamp. */
export function formatAnalyticsDateTime(value: unknown): string {
  const date = parseValidDate(value);
  return date ? dateTimeFormatter.format(date) : '—';
}

/** Pure — unit tested. "17 июля 2026 – 24 июля 2026"; either bound invalid
 * -> "—" (never a half-formed range). */
export function formatPeriodRange(periodStart: unknown, periodEnd: unknown): string {
  const startText = formatAnalyticsDate(periodStart);
  const endText = formatAnalyticsDate(periodEnd);
  if (startText === '—' || endText === '—') return '—';
  return `${startText} – ${endText}`;
}

function isZeroCount(value: unknown): boolean {
  return typeof value === 'number' && Number.isFinite(value) && value === 0;
}

/** Pure — unit tested. True only when every count that could indicate any
 * activity in the period is confidently zero — not when a count is merely
 * malformed/unreadable (that renders as "—" instead, not as "empty"). */
export function isOverviewEmpty(overview: AnalyticsOverview): boolean {
  return (
    isZeroCount(overview.applications_count) &&
    isZeroCount(overview.metrics_count) &&
    isZeroCount(overview.total_return_count) &&
    isZeroCount(overview.total_button_clicks)
  );
}

// --- Shared list rendering (overview popular_buttons/section_activity, and
// reused by adminApplications.ts for one application's detail) -------------
// Order is never re-sorted here — every item is rendered in exactly the
// order the backend returned it in.

/** Exported for reuse by adminApplications.ts's application detail modal. */
export function buttonAnalyticsListHtml(items: ButtonAnalyticsItem[], emptyMessage: string): string {
  if (!Array.isArray(items) || items.length === 0) {
    return `<p class="admin-empty">${escapeHtml(emptyMessage)}</p>`;
  }
  const rows = items
    .map((item) => {
      const name = formatButtonAnalyticsName(item?.name);
      const count = formatCount(item?.count);
      const percentText = formatPercent(item?.share_percent);
      const barWidth = percentBarWidth(item?.share_percent);
      const barLabel = `${name}: ${count} кликов, ${percentText}`;
      return `
        <li class="analytics-bar-item">
          <div class="analytics-bar-row">
            <span class="analytics-bar-name">${escapeHtml(name)}</span>
            <span class="analytics-bar-meta">${escapeHtml(count)} · ${escapeHtml(percentText)}</span>
          </div>
          <div class="analytics-bar-track" role="img" aria-label="${escapeHtml(barLabel)}">
            <div class="analytics-bar-fill" style="width: ${barWidth}%"></div>
          </div>
        </li>
      `;
    })
    .join('');
  return `<ul class="analytics-bar-list">${rows}</ul>`;
}

/** Exported for reuse by adminApplications.ts's application detail modal.
 * average_duration_seconds is backend-defined as total duration / total
 * hover interactions for that section — labeled "Среднее время одного
 * наведения" accordingly, never as a heatmap or cursor-coordinate view. */
export function sectionAnalyticsListHtml(items: SectionAnalyticsItem[], emptyMessage: string): string {
  if (!Array.isArray(items) || items.length === 0) {
    return `<p class="admin-empty">${escapeHtml(emptyMessage)}</p>`;
  }
  const rows = items
    .map((item) => {
      const name = formatSectionAnalyticsName(item?.section);
      const totalDuration = formatSeconds(item?.total_duration_seconds);
      const averageDuration = formatSeconds(item?.average_duration_seconds);
      const interactions = formatCount(item?.interactions_count);
      const percentText = formatPercent(item?.share_percent);
      const barWidth = percentBarWidth(item?.share_percent);
      const barLabel = `${name}: ${percentText}`;
      return `
        <li class="analytics-bar-item">
          <div class="analytics-bar-row">
            <span class="analytics-bar-name">${escapeHtml(name)}</span>
            <span class="analytics-bar-meta">${escapeHtml(percentText)}</span>
          </div>
          <dl class="analytics-section-facts">
            <div><dt>Общее время</dt><dd>${escapeHtml(totalDuration)}</dd></div>
            <div><dt>Среднее время одного наведения</dt><dd>${escapeHtml(averageDuration)}</dd></div>
            <div><dt>Наведений</dt><dd>${escapeHtml(interactions)}</dd></div>
          </dl>
          <div class="analytics-bar-track" role="img" aria-label="${escapeHtml(barLabel)}">
            <div class="analytics-bar-fill" style="width: ${barWidth}%"></div>
          </div>
        </li>
      `;
    })
    .join('');
  return `<ul class="analytics-bar-list">${rows}</ul>`;
}

// --- KPI cards ---------------------------------------------------------

interface KpiCard {
  label: string;
  value: string;
}

function kpiCardHtml(card: KpiCard): string {
  return `
    <div class="analytics-kpi-card">
      <span class="analytics-kpi-label">${escapeHtml(card.label)}</span>
      <span class="analytics-kpi-value">${escapeHtml(card.value)}</span>
    </div>
  `;
}

function kpiGroupHtml(title: string, cards: KpiCard[]): string {
  return `
    <div class="analytics-kpi-group">
      <h4>${escapeHtml(title)}</h4>
      <div class="analytics-kpi-grid">
        ${cards.map(kpiCardHtml).join('')}
      </div>
    </div>
  `;
}

function kpiGroupsHtml(overview: AnalyticsOverview): string {
  return [
    kpiGroupHtml('Заявки', [
      { label: 'Заявки за период', value: formatCount(overview.applications_count) },
      { label: 'С поведенческими метриками', value: formatCount(overview.applications_with_metrics) },
      { label: 'Без поведенческих метрик', value: formatCount(overview.applications_without_metrics) },
      { label: 'Записей метрик', value: formatCount(overview.metrics_count) },
    ]),
    kpiGroupHtml('Время', [
      { label: 'Среднее время на странице', value: formatSeconds(overview.average_time_on_page_seconds) },
      { label: 'Медианное время на странице', value: formatSeconds(overview.median_time_on_page_seconds) },
    ]),
    kpiGroupHtml('Возвраты', [
      { label: 'Среднее число возвратов', value: formatAverageReturns(overview.average_return_count) },
      { label: 'Всего возвратов', value: formatCount(overview.total_return_count) },
    ]),
    kpiGroupHtml('Клики', [
      { label: 'Кликов по кнопкам', value: formatCount(overview.total_button_clicks) },
      { label: 'Уникальных кнопок', value: formatCount(overview.unique_clicked_buttons) },
    ]),
  ].join('');
}

// --- Shell / rendering ---------------------------------------------------

function shellTemplate(): string {
  return `
    <div class="admin-section-heading">
      <span class="eyebrow">Статистика</span>
      <h3>Поведенческая аналитика</h3>
    </div>

    <div class="analytics-toolbar">
      <div class="analytics-periods" role="group" aria-label="Период статистики">
        <button type="button" class="filter-chip" data-period="day" aria-pressed="false">24 часа</button>
        <button type="button" class="filter-chip" data-period="week" aria-pressed="true">7 дней</button>
        <button type="button" class="filter-chip" data-period="month" aria-pressed="false">30 дней</button>
      </div>
      <button type="button" class="btn btn-secondary btn-small" id="analytics-refresh">
        Обновить
      </button>
    </div>

    <p class="analytics-range" id="analytics-range"></p>
    <div id="analytics-status" role="status" aria-live="polite"></div>

    <div id="analytics-content" hidden>
      <div class="analytics-kpi-groups" id="analytics-kpi-groups"></div>

      <p class="analytics-caption">
        Статистика рассчитана по отправленным заявкам и связанным с ними поведенческим метрикам.
      </p>

      <p class="admin-empty" id="analytics-empty-note" hidden>За выбранный период данных пока нет.</p>

      <section class="analytics-section">
        <h4>Популярные действия</h4>
        <div id="analytics-buttons"></div>
      </section>

      <section class="analytics-section">
        <h4>Активность по секциям формы</h4>
        <div id="analytics-sections"></div>
      </section>
    </div>
  `;
}

function setControlsDisabled(container: HTMLElement, disabled: boolean): void {
  const refreshButton = container.querySelector<HTMLButtonElement>('#analytics-refresh');
  if (refreshButton) refreshButton.disabled = disabled;
}

function renderOverview(container: HTMLElement, state: AnalyticsState): void {
  const statusEl = container.querySelector<HTMLElement>('#analytics-status');
  const rangeEl = container.querySelector<HTMLElement>('#analytics-range');
  const contentEl = container.querySelector<HTMLElement>('#analytics-content');
  const kpiEl = container.querySelector<HTMLElement>('#analytics-kpi-groups');
  const emptyNoteEl = container.querySelector<HTMLElement>('#analytics-empty-note');
  const buttonsEl = container.querySelector<HTMLElement>('#analytics-buttons');
  const sectionsEl = container.querySelector<HTMLElement>('#analytics-sections');
  if (!statusEl || !rangeEl || !contentEl || !kpiEl || !emptyNoteEl || !buttonsEl || !sectionsEl) return;

  // Loading/error/no-overview all fall through to the same "drop the old
  // dataset" cleanup below — hidden alone would leave stale KPI/list text
  // sitting in the DOM (and in `.textContent`), which is exactly what the
  // stale-response guard exists to prevent.
  const clearContent = (): void => {
    contentEl.hidden = true;
    rangeEl.textContent = '';
    kpiEl.innerHTML = '';
    buttonsEl.innerHTML = '';
    sectionsEl.innerHTML = '';
    emptyNoteEl.hidden = true;
  };

  if (state.loading) {
    clearContent();
    statusEl.innerHTML = '<p class="admin-empty">Загружаем статистику…</p>';
    return;
  }

  if (state.hasLoadError) {
    clearContent();
    statusEl.innerHTML =
      '<div class="banner banner--error" role="alert">Не удалось загрузить статистику. Повторите попытку.</div>';
    return;
  }

  const overview = state.overview;
  if (!overview) {
    clearContent();
    statusEl.innerHTML = '';
    return;
  }

  statusEl.innerHTML = '';
  rangeEl.textContent = `Период: ${formatPeriodRange(overview.period_start, overview.period_end)}`;
  contentEl.hidden = false;
  kpiEl.innerHTML = kpiGroupsHtml(overview);
  emptyNoteEl.hidden = !isOverviewEmpty(overview);
  buttonsEl.innerHTML = buttonAnalyticsListHtml(
    overview.popular_buttons,
    'За выбранный период кликов по кнопкам не зафиксировано.',
  );
  sectionsEl.innerHTML = sectionAnalyticsListHtml(
    overview.section_activity,
    'За выбранный период активность по секциям не зафиксирована.',
  );
}

async function loadOverview(
  container: HTMLElement,
  host: AnalyticsSectionHost,
  state: AnalyticsState,
  activationId: number,
  isStillCurrent: (activationId: number) => boolean,
): Promise<void> {
  // Refuses to start a second overlapping request — see selectPeriod/refresh
  // below for how period switches vs. plain refreshes interact with this.
  if (state.loading) return;

  state.loading = true;
  state.hasLoadError = false;
  // Old data is dropped immediately, not just visually — a filter/period
  // change made while this request is pending can never resurrect it.
  state.overview = null;
  renderOverview(container, state);
  setControlsDisabled(container, true);

  try {
    const overview = await api.getAnalyticsOverview(state.period);
    if (!isStillCurrent(activationId)) return;
    state.overview = overview;
    state.loading = false;
    renderOverview(container, state);
  } catch (error) {
    if (!isStillCurrent(activationId)) return;
    state.loading = false;
    if (isUnauthorizedError(error)) {
      host.onSessionExpired();
      return;
    }
    state.hasLoadError = true;
    renderOverview(container, state);
  } finally {
    if (isStillCurrent(activationId)) {
      setControlsDisabled(container, false);
    }
  }
}

const ANALYTICS_PERIODS: readonly AnalyticsPeriod[] = ['day', 'week', 'month'];

function isAnalyticsPeriod(value: string | undefined): value is AnalyticsPeriod {
  return typeof value === 'string' && (ANALYTICS_PERIODS as readonly string[]).includes(value);
}

/**
 * Mounts the analytics section into `container`, wiring every listener
 * exactly once, and returns a controller for admin.ts's tab switcher to
 * drive as the admin moves between "Статистика" and the other tabs — see
 * adminApplications.ts's mountAdminApplications for the identical
 * activate()/deactivate()/dispose() contract this mirrors.
 */
export function mountAdminAnalytics(
  container: HTMLElement,
  host: AnalyticsSectionHost,
): AnalyticsSectionController {
  const mountId = beginAnalyticsRender();
  const state: AnalyticsState = {
    period: 'week',
    overview: null,
    loading: false,
    hasLoadError: false,
  };

  let tabActive = false;
  let currentActivationId = 0;

  function isStillCurrent(activationId: number): boolean {
    return activationId === currentActivationId && isCurrentAnalyticsRender(mountId) && host.isActive();
  }

  function isInteractive(): boolean {
    return tabActive && isCurrentAnalyticsRender(mountId) && host.isActive();
  }

  container.innerHTML = shellTemplate();

  const periodButtons = Array.from(container.querySelectorAll<HTMLButtonElement>('[data-period]'));

  function updatePeriodButtons(): void {
    periodButtons.forEach((button) => {
      button.setAttribute('aria-pressed', String(button.dataset.period === state.period));
    });
  }

  function startLoad(activationId: number): void {
    void loadOverview(container, host, state, activationId, isStillCurrent);
  }

  periodButtons.forEach((button) => {
    button.addEventListener('click', () => {
      if (!isInteractive()) return;
      const period = button.dataset.period;
      if (!isAnalyticsPeriod(period) || period === state.period) return;

      // A period switch invalidates any still-pending request from the
      // previous period — mirrors adminApplications.ts's deactivate(): bump
      // the activation id and reset the loading flag together, so the fresh
      // load below is never blocked by a stale in-flight request.
      state.period = period;
      state.loading = false;
      currentActivationId += 1;
      updatePeriodButtons();
      startLoad(currentActivationId);
    });
  });

  container.querySelector<HTMLButtonElement>('#analytics-refresh')?.addEventListener('click', () => {
    if (!isInteractive()) return;
    // Same activation, same period — loadOverview's own `if (state.loading)
    // return` guard dedupes rapid repeated clicks into at most one request.
    startLoad(currentActivationId);
  });

  function activate(): void {
    tabActive = true;
    currentActivationId += 1;
    const activationId = currentActivationId;
    startLoad(activationId);
  }

  function deactivate(): void {
    tabActive = false;
    currentActivationId += 1;
    state.loading = false;
  }

  function dispose(): void {
    tabActive = false;
    currentActivationId += 1;
    state.loading = false;
  }

  return { activate, deactivate, dispose };
}
