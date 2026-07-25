import { api, ApiError, isUnauthorizedError } from '../api/client';
import { clearToken, getToken, saveToken } from '../api/tokenStorage';
import type {
  AdminRead,
  AdminSettingCreatePayload,
  AdminSettingRead,
  AdminSettingUpdatePayload,
  AuthCheckResponse,
} from '../api/types';
import { escapeHtml } from '../utils/html';
import { formatBudget } from '../utils/format';
import { mountAdminApplications } from './adminApplications';
import type { ApplicationsSectionController } from './adminApplications';

interface AdminState {
  services: AdminSettingRead[];
  editingId: number | null;
}

/** Holds the (lazily-created) "Заявки" controller so admin.ts's
 * session-ending transitions (logout, 401 session expiry) can dispose of
 * it — see wireLogoutButton/wireTabs. */
interface ApplicationsControllerRef {
  current: ApplicationsSectionController | null;
}

/**
 * Generation guard for async render flows: every renderAdmin() call (and
 * every explicit session-ending transition — logout, a 401-forced session
 * expiry) starts a new generation. Each async flow captures the renderId it
 * was started under and re-checks isCurrentRender() after every await and
 * before every DOM mutation, so a late-arriving response from a superseded
 * flow can no longer paint over newer UI, re-open the panel, or resurrect a
 * cleared token.
 */
let renderGeneration = 0;

function beginRender(): number {
  renderGeneration += 1;
  return renderGeneration;
}

function isCurrentRender(renderId: number): boolean {
  return renderId === renderGeneration;
}

export function renderAdmin(root: HTMLElement): void {
  const renderId = beginRender();
  void runAuthGate(root, renderId);
}

/** Pure — unit tested. Registration is only offered while no admin exists yet. */
export function shouldOfferRegistration(check: AuthCheckResponse): boolean {
  return !check.admin_exists && check.registration_allowed;
}

const SESSION_EXPIRED_NOTICE = 'Сессия истекла — войдите снова.';

async function runAuthGate(root: HTMLElement, renderId: number): Promise<void> {
  root.innerHTML = loadingTemplate();

  let check: AuthCheckResponse;
  try {
    check = await api.checkAuthStatus();
  } catch {
    if (!isCurrentRender(renderId)) return;
    renderAuthCheckError(root, renderId);
    return;
  }
  if (!isCurrentRender(renderId)) return;

  if (shouldOfferRegistration(check)) {
    renderRegisterView(root, renderId);
    return;
  }

  if (!getToken()) {
    renderLoginView(root, renderId);
    return;
  }

  try {
    const admin = await api.getCurrentAdmin();
    if (!isCurrentRender(renderId)) return;
    renderAuthenticatedView(root, renderId, admin);
  } catch (error) {
    if (!isCurrentRender(renderId)) return;
    if (isUnauthorizedError(error)) {
      // Also cleared centrally inside client.ts's request() on the same
      // 401 — calling it again here is a harmless no-op, and keeps this
      // "the token was rejected" outcome self-contained/verifiable on its
      // own rather than depending on that other call having happened.
      handleSessionExpired(root, renderId);
    } else {
      renderAuthCheckError(root, renderId);
    }
  }
}

function pageHeaderTemplate(): string {
  return `
    <header class="site-header">
      <a class="brand" href="/" data-link>
        <span class="brand-mark">AUREL</span><span>Detailing</span>
      </a>
      <nav class="site-nav" aria-label="Основная навигация">
        <a href="/" data-link>На главную</a>
      </nav>
    </header>
  `;
}

function loadingTemplate(): string {
  return `
    <div class="page container admin-page admin-auth-page">
      ${pageHeaderTemplate()}
      <div class="card form-card admin-auth-card">
        <p class="admin-empty">Проверяем авторизацию…</p>
      </div>
    </div>
  `;
}

function authCheckErrorTemplate(): string {
  return `
    <div class="page container admin-page admin-auth-page">
      ${pageHeaderTemplate()}
      <div class="card form-card admin-auth-card">
        <div class="banner banner--error" role="alert">
          Не удалось проверить авторизацию. Проверьте соединение и попробуйте снова.
        </div>
        <button type="button" class="btn btn-primary" id="auth-check-retry">Повторить</button>
      </div>
    </div>
  `;
}

function renderAuthCheckError(root: HTMLElement, renderId: number): void {
  if (!isCurrentRender(renderId)) return;
  root.innerHTML = authCheckErrorTemplate();
  root.querySelector<HTMLButtonElement>('#auth-check-retry')?.addEventListener('click', () => {
    if (!isCurrentRender(renderId)) return;
    // Retry starts a fresh generation rather than reusing renderId — this
    // detached handler (and this renderId) is now permanently stale, so it
    // can never re-trigger the auth flow a second time, and nothing tied to
    // the old generation can act after this point.
    const newRenderId = beginRender();
    void runAuthGate(root, newRenderId);
  });
}

function noticeBannerHtml(notice: string | undefined): string {
  return notice ? `<div class="banner banner--notice">${escapeHtml(notice)}</div>` : '';
}

function registerTemplate(notice?: string): string {
  return `
    <div class="page container admin-page admin-auth-page">
      ${pageHeaderTemplate()}
      <div class="card form-card admin-auth-card">
        <span class="eyebrow">Первый запуск</span>
        <h2>Создайте учётную запись администратора</h2>
        <div id="auth-banner" role="status" aria-live="polite">${noticeBannerHtml(notice)}</div>
        <form id="register-form" novalidate>
          <div class="field">
            <label for="register-username">Имя пользователя</label>
            <input
              type="text"
              id="register-username"
              name="username"
              required
              minlength="3"
              maxlength="150"
              autocomplete="username"
            />
          </div>
          <div class="field">
            <label for="register-password">Пароль</label>
            <input
              type="password"
              id="register-password"
              name="password"
              required
              minlength="8"
              maxlength="256"
              autocomplete="new-password"
            />
          </div>
          <div class="field">
            <label for="register-password-confirm">Повторите пароль</label>
            <input
              type="password"
              id="register-password-confirm"
              name="password_confirm"
              required
              minlength="8"
              maxlength="256"
              autocomplete="new-password"
            />
          </div>
          <button class="btn btn-primary" type="submit" id="register-submit">
            Зарегистрироваться
          </button>
        </form>
      </div>
    </div>
  `;
}

function renderRegisterView(root: HTMLElement, renderId: number, notice?: string): void {
  if (!isCurrentRender(renderId)) return;
  root.innerHTML = registerTemplate(notice);
  wireRegisterForm(root, renderId);
}

function wireRegisterForm(root: HTMLElement, renderId: number): void {
  const form = root.querySelector<HTMLFormElement>('#register-form');
  const submitBtn = root.querySelector<HTMLButtonElement>('#register-submit');
  const bannerEl = root.querySelector<HTMLElement>('#auth-banner');
  if (!form || !submitBtn || !bannerEl) return;

  let inFlight = false;

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    if (!isCurrentRender(renderId) || inFlight) return;
    inFlight = true;
    void handleRegister(root, renderId, form, submitBtn, bannerEl).finally(() => {
      inFlight = false;
    });
  });
}

/** Mirrors syncBudgetRangeValidity's pattern: a native custom-validity check
 * run right before form.reportValidity(), not on every keystroke. */
function syncPasswordConfirmValidity(form: HTMLFormElement): void {
  const passwordInput = form.querySelector<HTMLInputElement>('[name="password"]');
  const confirmInput = form.querySelector<HTMLInputElement>('[name="password_confirm"]');
  if (!passwordInput || !confirmInput) return;

  confirmInput.setCustomValidity(
    passwordInput.value === confirmInput.value ? '' : 'Пароли не совпадают.',
  );
}

async function handleRegister(
  root: HTMLElement,
  renderId: number,
  form: HTMLFormElement,
  submitBtn: HTMLButtonElement,
  bannerEl: HTMLElement,
): Promise<void> {
  bannerEl.innerHTML = '';

  syncPasswordConfirmValidity(form);
  if (!form.reportValidity()) return;

  const formData = new FormData(form);
  // Username: trimmed (consistent with the backend's own normalization) but
  // never lowercased here — the backend case-folds for storage/comparison.
  const username = String(formData.get('username') ?? '').trim();
  // Password: never trimmed, anywhere in this pipeline.
  const password = String(formData.get('password') ?? '');

  submitBtn.disabled = true;
  submitBtn.textContent = 'Создаём…';

  try {
    await api.registerAdmin({ username, password });
  } catch (error) {
    if (!isCurrentRender(renderId)) return;
    if (error instanceof ApiError && error.status === 409) {
      // Someone else already registered (race) — re-check canonical state
      // per the required flow, then always land on the login view.
      try {
        await api.checkAuthStatus();
      } catch {
        // Ignore — we're going to the login view regardless of this result.
      }
      if (!isCurrentRender(renderId)) return;
      renderLoginView(root, renderId, 'Администратор уже зарегистрирован. Пожалуйста, войдите.');
      return;
    }

    const message =
      error instanceof ApiError ? error.message : 'Не удалось создать администратора.';
    bannerEl.innerHTML = `<div class="banner banner--error">${escapeHtml(message)}</div>`;
    submitBtn.disabled = false;
    submitBtn.textContent = 'Зарегистрироваться';
    return;
  }
  if (!isCurrentRender(renderId)) return;

  // Auto-login with the credentials just submitted (still only in memory),
  // then verify via /me before showing the panel — simpler and more
  // reliable than asking the admin to retype everything on a second screen.
  try {
    const tokenResponse = await api.loginAdmin({ username, password });
    if (!isCurrentRender(renderId)) return;
    saveToken(tokenResponse.access_token);
    const admin = await api.getCurrentAdmin();
    if (!isCurrentRender(renderId)) return;
    renderAuthenticatedView(root, renderId, admin);
  } catch {
    if (!isCurrentRender(renderId)) return;
    renderLoginView(root, renderId, 'Регистрация прошла успешно — войдите, используя указанные данные.');
  }
}

const LOGIN_NEUTRAL_ERROR = 'Неверное имя пользователя или пароль.';

function loginTemplate(notice?: string): string {
  return `
    <div class="page container admin-page admin-auth-page">
      ${pageHeaderTemplate()}
      <div class="card form-card admin-auth-card">
        <span class="eyebrow">Вход</span>
        <h2>Вход в административную панель</h2>
        <div id="auth-banner" role="status" aria-live="polite">${noticeBannerHtml(notice)}</div>
        <form id="login-form" novalidate>
          <div class="field">
            <label for="login-username">Имя пользователя</label>
            <input type="text" id="login-username" name="username" required autocomplete="username" />
          </div>
          <div class="field">
            <label for="login-password">Пароль</label>
            <input
              type="password"
              id="login-password"
              name="password"
              required
              autocomplete="current-password"
            />
          </div>
          <button class="btn btn-primary" type="submit" id="login-submit">Войти</button>
        </form>
      </div>
    </div>
  `;
}

function renderLoginView(root: HTMLElement, renderId: number, notice?: string): void {
  if (!isCurrentRender(renderId)) return;
  root.innerHTML = loginTemplate(notice);
  wireLoginForm(root, renderId);
}

function wireLoginForm(root: HTMLElement, renderId: number): void {
  const form = root.querySelector<HTMLFormElement>('#login-form');
  const submitBtn = root.querySelector<HTMLButtonElement>('#login-submit');
  const bannerEl = root.querySelector<HTMLElement>('#auth-banner');
  if (!form || !submitBtn || !bannerEl) return;

  let inFlight = false;

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    if (!isCurrentRender(renderId) || inFlight) return;
    inFlight = true;
    void handleLogin(root, renderId, form, submitBtn, bannerEl).finally(() => {
      inFlight = false;
    });
  });
}

async function handleLogin(
  root: HTMLElement,
  renderId: number,
  form: HTMLFormElement,
  submitBtn: HTMLButtonElement,
  bannerEl: HTMLElement,
): Promise<void> {
  bannerEl.innerHTML = '';
  if (!form.reportValidity()) return;

  const formData = new FormData(form);
  const username = String(formData.get('username') ?? '').trim();
  const password = String(formData.get('password') ?? ''); // never trimmed

  submitBtn.disabled = true;
  submitBtn.textContent = 'Входим…';

  let tokenValue: string;
  try {
    const tokenResponse = await api.loginAdmin({ username, password });
    tokenValue = tokenResponse.access_token;
  } catch (error) {
    if (!isCurrentRender(renderId)) return;
    // Unknown username and wrong password must look identical to the user —
    // never surface the backend's response body text for this case.
    const message =
      error instanceof ApiError && error.status === 401
        ? LOGIN_NEUTRAL_ERROR
        : 'Не удалось выполнить вход. Попробуйте ещё раз.';
    bannerEl.innerHTML = `<div class="banner banner--error">${escapeHtml(message)}</div>`;
    submitBtn.disabled = false;
    submitBtn.textContent = 'Войти';
    return;
  }

  if (!isCurrentRender(renderId)) return;
  saveToken(tokenValue);

  try {
    const admin = await api.getCurrentAdmin();
    if (!isCurrentRender(renderId)) return;
    renderAuthenticatedView(root, renderId, admin);
  } catch {
    if (!isCurrentRender(renderId)) return;
    clearToken();
    bannerEl.innerHTML = `<div class="banner banner--error">${escapeHtml(
      'Не удалось подтвердить сессию. Попробуйте войти снова.',
    )}</div>`;
    submitBtn.disabled = false;
    submitBtn.textContent = 'Войти';
  }
}

function handleSessionExpired(root: HTMLElement, renderId: number): void {
  if (!isCurrentRender(renderId)) return;
  // Also cleared centrally inside client.ts's request() — see runAuthGate's
  // matching comment for why calling it again here is intentional.
  clearToken();
  // A forced session expiry ends this render generation outright — any other
  // in-flight request still tied to the old (now-unauthenticated) session
  // must be unable to act, regardless of what the new DOM happens to contain.
  const newRenderId = beginRender();
  renderLoginView(root, newRenderId, SESSION_EXPIRED_NOTICE);
}

function servicesSectionTemplate(): string {
  return `
    <div class="admin-section-heading">
      <span class="eyebrow">Услуги</span>
      <h3>Управление услугами</h3>
    </div>

    <div class="card form-card admin-create-card">
      <h3>Добавить услугу</h3>
      <div id="create-banner" role="status" aria-live="polite"></div>
      <form id="create-service-form" novalidate>
        <div class="field-grid">
          <div class="field">
            <label for="new-service-name">Название услуги</label>
            <input type="text" id="new-service-name" name="service_name" required maxlength="255" />
          </div>
          <div class="field">
            <label for="new-budget-min">Бюджет от, ₽</label>
            <input type="number" id="new-budget-min" name="budget_min" required min="0" step="1" />
          </div>
          <div class="field">
            <label for="new-budget-max">Бюджет до, ₽</label>
            <input type="number" id="new-budget-max" name="budget_max" required min="0" step="1" />
          </div>
          <div class="field checkbox-row field--align-end">
            <input type="checkbox" id="new-is-active" name="is_active" checked />
            <label for="new-is-active">Активна сразу</label>
          </div>
        </div>
        <div class="field">
          <label for="new-description">Описание <span class="hint">(необязательно)</span></label>
          <textarea id="new-description" name="description"></textarea>
        </div>
        <button class="btn btn-primary" type="submit" id="create-service-submit">Добавить услугу</button>
      </form>
    </div>

    <div id="services-list-status" role="status" aria-live="polite"></div>
    <div class="admin-grid" id="services-list"></div>
  `;
}

function adminPanelTemplate(admin: AdminRead): string {
  return `
    <div class="page container admin-page">
      ${pageHeaderTemplate()}

      <div class="admin-header">
        <div>
          <span class="eyebrow">Административная панель</span>
          <h2>AUREL Detailing</h2>
        </div>
        <div class="admin-session">
          <span>Вы вошли как <strong>${escapeHtml(admin.username)}</strong></span>
          <button type="button" class="btn btn-secondary btn-small" id="logout-button">
            Выйти
          </button>
        </div>
      </div>

      <nav class="admin-tabs" role="tablist" aria-label="Разделы административной панели">
        <button
          type="button"
          class="admin-tab"
          id="admin-tab-services"
          data-tab="services"
          role="tab"
          aria-selected="true"
          aria-controls="admin-panel-services"
        >
          Услуги
        </button>
        <button
          type="button"
          class="admin-tab"
          id="admin-tab-applications"
          data-tab="applications"
          role="tab"
          aria-selected="false"
          aria-controls="admin-panel-applications"
        >
          Заявки
        </button>
      </nav>

      <section id="admin-panel-services" role="tabpanel" aria-labelledby="admin-tab-services">
        ${servicesSectionTemplate()}
      </section>

      <section id="admin-panel-applications" role="tabpanel" aria-labelledby="admin-tab-applications" hidden></section>
    </div>
  `;
}

/** Exported for testing (see pages/render.test.ts) — must escape user-editable service_name/description. */
export function viewCardTemplate(service: AdminSettingRead): string {
  return `
    <article class="card setting-card" data-id="${service.id}">
      <div class="setting-card-top">
        <h3>${escapeHtml(service.service_name)}</h3>
        <span class="status-pill ${service.is_active ? 'status-pill--active' : 'status-pill--inactive'}">
          ${service.is_active ? 'Активна' : 'Неактивна'}
        </span>
      </div>
      ${service.description ? `<p class="setting-card-desc">${escapeHtml(service.description)}</p>` : ''}
      <p class="setting-card-budget">
        ${formatBudget(Number(service.budget_min))} – ${formatBudget(Number(service.budget_max))}
      </p>
      <div class="setting-card-actions">
        <button type="button" class="btn btn-secondary btn-small" data-action="edit" data-id="${service.id}">
          Изменить
        </button>
        <button type="button" class="btn btn-secondary btn-small" data-action="toggle-active" data-id="${service.id}">
          ${service.is_active ? 'Сделать неактивной' : 'Сделать активной'}
        </button>
        <button type="button" class="btn btn-danger btn-small" data-action="delete" data-id="${service.id}">
          Удалить
        </button>
      </div>
    </article>
  `;
}

function editCardTemplate(service: AdminSettingRead): string {
  const id = service.id;
  return `
    <article class="card setting-card" data-id="${id}">
      <form data-edit-form data-id="${id}">
        <div class="field">
          <label for="edit-name-${id}">Название услуги</label>
          <input
            type="text"
            id="edit-name-${id}"
            name="service_name"
            required
            maxlength="255"
            value="${escapeHtml(service.service_name)}"
          />
        </div>
        <div class="field-grid">
          <div class="field">
            <label for="edit-min-${id}">Бюджет от, ₽</label>
            <input type="number" id="edit-min-${id}" name="budget_min" required min="0" step="1" value="${service.budget_min}" />
          </div>
          <div class="field">
            <label for="edit-max-${id}">Бюджет до, ₽</label>
            <input type="number" id="edit-max-${id}" name="budget_max" required min="0" step="1" value="${service.budget_max}" />
          </div>
          <div class="field checkbox-row field--align-end">
            <input type="checkbox" id="edit-active-${id}" name="is_active" ${service.is_active ? 'checked' : ''} />
            <label for="edit-active-${id}">Активна</label>
          </div>
        </div>
        <div class="field">
          <label for="edit-desc-${id}">Описание <span class="hint">(необязательно)</span></label>
          <textarea id="edit-desc-${id}" name="description">${service.description ? escapeHtml(service.description) : ''}</textarea>
        </div>
        <div id="edit-banner-${id}" role="status" aria-live="polite"></div>
        <div class="setting-card-actions">
          <button type="submit" class="btn btn-primary btn-small">Сохранить</button>
          <button type="button" class="btn btn-secondary btn-small" data-action="cancel-edit">Отмена</button>
        </div>
      </form>
    </article>
  `;
}

/**
 * Client-side mirror of the backend's budget_min <= budget_max rule (see
 * AdminSettingCreate/AdminSettingUpdate validators) — surfaces the error as
 * a native browser validation message on budget_max, so form.reportValidity()
 * blocks submission before the request ever reaches the backend. The
 * backend validator remains authoritative regardless.
 */
function syncBudgetRangeValidity(form: HTMLFormElement): void {
  const minInput = form.querySelector<HTMLInputElement>('[name="budget_min"]');
  const maxInput = form.querySelector<HTMLInputElement>('[name="budget_max"]');
  if (!minInput || !maxInput) return;

  const isRangeValid = Number(minInput.value) <= Number(maxInput.value);
  maxInput.setCustomValidity(
    isRangeValid ? '' : 'Бюджет «до» должен быть больше или равен бюджету «от».',
  );
}

function renderAuthenticatedView(root: HTMLElement, renderId: number, admin: AdminRead): void {
  if (!isCurrentRender(renderId)) return;
  root.innerHTML = adminPanelTemplate(admin);

  const state: AdminState = { services: [], editingId: null };
  const applicationsControllerRef: ApplicationsControllerRef = { current: null };

  wireLogoutButton(root, renderId, applicationsControllerRef);
  wireCreateForm(root, renderId, state);
  wireListContainer(root, renderId, state);
  void loadServices(root, renderId, state);
  wireTabs(root, renderId, applicationsControllerRef);
}

type AdminTab = 'services' | 'applications';

/**
 * Wires the Услуги/Заявки tab switcher. Switching tabs never touches
 * innerHTML for the whole panel (no full re-render, no page reload) — it
 * just toggles `hidden` on the two <section>s, so logout/username in the
 * shared header above stay put regardless of which tab is active. The
 * applications section is mounted lazily, once, on its first activation;
 * every subsequent switch drives the same controller's activate()/
 * deactivate() instead of re-mounting, so a response that arrives after
 * the admin has switched away from "Заявки" can never repaint that
 * (now-hidden) panel — see adminApplications.ts's mountAdminApplications.
 *
 * Also implements the standard WAI-ARIA tabs keyboard pattern: ArrowRight/
 * ArrowLeft cycle through tabs, Home/End jump to the first/last, and the
 * tab that receives focus is immediately selected (Enter/Space and mouse
 * clicks keep working via the native <button> click behavior — the keydown
 * handler only ever intercepts the four navigation keys above).
 */
function wireTabs(
  root: HTMLElement,
  renderId: number,
  controllerRef: ApplicationsControllerRef,
): void {
  const tabButtons = Array.from(root.querySelectorAll<HTMLButtonElement>('.admin-tab'));
  const servicesPanel = root.querySelector<HTMLElement>('#admin-panel-services');
  const applicationsPanel = root.querySelector<HTMLElement>('#admin-panel-applications');
  if (!servicesPanel || !applicationsPanel) return;

  let activeTab: AdminTab = 'services';

  // Arrow functions (not hoisted `function` declarations) so TypeScript
  // keeps servicesPanel/applicationsPanel narrowed to HTMLElement inside
  // them, per the null-check above.
  const syncTabIndexes = (): void => {
    tabButtons.forEach((btn) => {
      btn.tabIndex = btn.dataset.tab === activeTab ? 0 : -1;
    });
  };
  syncTabIndexes();

  const selectTab = (tab: AdminTab): void => {
    if (!isCurrentRender(renderId) || tab === activeTab) return;
    activeTab = tab;

    tabButtons.forEach((btn) => btn.setAttribute('aria-selected', String(btn.dataset.tab === tab)));
    syncTabIndexes();
    servicesPanel.hidden = tab !== 'services';
    applicationsPanel.hidden = tab !== 'applications';

    if (tab === 'applications') {
      if (!controllerRef.current) {
        controllerRef.current = mountAdminApplications(applicationsPanel, {
          isActive: () => isCurrentRender(renderId),
          onSessionExpired: () => {
            controllerRef.current?.dispose();
            handleSessionExpired(root, renderId);
          },
        });
      }
      controllerRef.current.activate();
    } else {
      controllerRef.current?.deactivate();
    }
  };

  tabButtons.forEach((button) => {
    button.addEventListener('click', () => {
      const tab = button.dataset.tab as AdminTab | undefined;
      if (!tab) return;
      selectTab(tab);
    });

    button.addEventListener('keydown', (event) => {
      if (!isCurrentRender(renderId)) return;

      const currentIndex = tabButtons.indexOf(button);
      let targetIndex: number;
      switch (event.key) {
        case 'ArrowRight':
          targetIndex = (currentIndex + 1) % tabButtons.length;
          break;
        case 'ArrowLeft':
          targetIndex = (currentIndex - 1 + tabButtons.length) % tabButtons.length;
          break;
        case 'Home':
          targetIndex = 0;
          break;
        case 'End':
          targetIndex = tabButtons.length - 1;
          break;
        default:
          return;
      }

      event.preventDefault();
      const targetButton = tabButtons[targetIndex];
      const targetTab = targetButton.dataset.tab as AdminTab | undefined;
      if (!targetTab) return;
      selectTab(targetTab);
      targetButton.focus();
    });
  });
}

function wireLogoutButton(
  root: HTMLElement,
  renderId: number,
  controllerRef: ApplicationsControllerRef,
): void {
  root.querySelector<HTMLButtonElement>('#logout-button')?.addEventListener('click', () => {
    if (!isCurrentRender(renderId)) return;
    controllerRef.current?.dispose();
    clearToken();
    // Logout ends this render generation outright — see handleSessionExpired
    // for why any other in-flight request from this session must be unable
    // to act afterwards (e.g. restore the token or repaint the panel).
    const newRenderId = beginRender();
    renderLoginView(root, newRenderId);
  });
}

async function loadServices(root: HTMLElement, renderId: number, state: AdminState): Promise<void> {
  const statusEl = root.querySelector<HTMLElement>('#services-list-status');
  if (!statusEl) return;

  statusEl.innerHTML = '<p class="admin-empty">Загружаем услуги…</p>';
  try {
    const services = await api.getAllServices();
    if (!isCurrentRender(renderId)) return;
    state.services = services;
  } catch (error) {
    if (!isCurrentRender(renderId)) return;
    if (isUnauthorizedError(error)) {
      handleSessionExpired(root, renderId);
      return;
    }
    console.warn('Failed to load admin settings', error);
    statusEl.innerHTML = '<p class="admin-empty">Не удалось загрузить список услуг.</p>';
    return;
  }

  renderList(root, state);
}

function renderList(root: HTMLElement, state: AdminState): void {
  const listEl = root.querySelector<HTMLElement>('#services-list');
  const statusEl = root.querySelector<HTMLElement>('#services-list-status');
  if (!listEl || !statusEl) return;

  if (state.services.length === 0) {
    listEl.innerHTML = '';
    statusEl.innerHTML = '<p class="admin-empty">Услуг пока нет — добавьте первую выше.</p>';
    return;
  }

  statusEl.innerHTML = '';
  listEl.innerHTML = state.services
    .map((service) =>
      state.editingId === service.id ? editCardTemplate(service) : viewCardTemplate(service),
    )
    .join('');
}

function wireCreateForm(root: HTMLElement, renderId: number, state: AdminState): void {
  const form = root.querySelector<HTMLFormElement>('#create-service-form');
  const submitBtn = root.querySelector<HTMLButtonElement>('#create-service-submit');
  const bannerEl = root.querySelector<HTMLElement>('#create-banner');
  if (!form || !submitBtn || !bannerEl) return;

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    if (!isCurrentRender(renderId)) return;
    void handleCreate(root, renderId, form, submitBtn, bannerEl, state);
  });
}

async function handleCreate(
  root: HTMLElement,
  renderId: number,
  form: HTMLFormElement,
  submitBtn: HTMLButtonElement,
  bannerEl: HTMLElement,
  state: AdminState,
): Promise<void> {
  bannerEl.innerHTML = '';

  syncBudgetRangeValidity(form);
  if (!form.reportValidity()) return;

  const formData = new FormData(form);
  const description = String(formData.get('description') ?? '').trim();

  const payload: AdminSettingCreatePayload = {
    service_name: String(formData.get('service_name') ?? '').trim(),
    budget_min: Number(formData.get('budget_min')),
    budget_max: Number(formData.get('budget_max')),
    description: description.length > 0 ? description : null,
    is_active: formData.get('is_active') === 'on',
  };

  submitBtn.disabled = true;
  submitBtn.textContent = 'Добавляем…';

  try {
    await api.createService(payload);
    if (!isCurrentRender(renderId)) return;
    form.reset();
    await loadServices(root, renderId, state);
  } catch (error) {
    if (!isCurrentRender(renderId)) return;
    if (isUnauthorizedError(error)) {
      handleSessionExpired(root, renderId);
      return;
    }
    const message = error instanceof ApiError ? error.message : 'Не удалось создать услугу.';
    bannerEl.innerHTML = `<div class="banner banner--error">${escapeHtml(message)}</div>`;
  } finally {
    if (isCurrentRender(renderId)) {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Добавить услугу';
    }
  }
}

function wireListContainer(root: HTMLElement, renderId: number, state: AdminState): void {
  const listEl = root.querySelector<HTMLElement>('#services-list');
  if (!listEl) return;

  listEl.addEventListener('click', (event) => {
    if (!isCurrentRender(renderId)) return;
    const target = event.target;
    if (!(target instanceof Element)) return;
    const button = target.closest('button[data-action]');
    if (!(button instanceof HTMLButtonElement)) return;

    const action = button.dataset.action;
    const idAttr = button.dataset.id ?? button.closest<HTMLElement>('[data-id]')?.dataset.id;
    const id = Number(idAttr);
    if (!idAttr || Number.isNaN(id)) return;

    if (action === 'edit') {
      state.editingId = id;
      renderList(root, state);
    } else if (action === 'cancel-edit') {
      state.editingId = null;
      renderList(root, state);
    } else if (action === 'delete') {
      void handleDelete(root, renderId, state, id);
    } else if (action === 'toggle-active') {
      void handleToggleActive(root, renderId, state, id);
    }
  });

  listEl.addEventListener('submit', (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute('data-edit-form')) return;
    event.preventDefault();
    if (!isCurrentRender(renderId)) return;
    const id = Number(form.dataset.id);
    void handleSaveEdit(root, renderId, state, form, id);
  });
}

async function handleDelete(root: HTMLElement, renderId: number, state: AdminState, id: number): Promise<void> {
  const service = state.services.find((item) => item.id === id);
  const label = service ? `«${service.service_name}»` : 'эту услугу';
  if (!window.confirm(`Удалить услугу ${label}? Это действие необратимо.`)) return;

  try {
    await api.deleteService(id);
    if (!isCurrentRender(renderId)) return;
    await loadServices(root, renderId, state);
  } catch (error) {
    if (!isCurrentRender(renderId)) return;
    if (isUnauthorizedError(error)) {
      handleSessionExpired(root, renderId);
      return;
    }
    console.warn('Failed to delete admin setting', error);
    window.alert(error instanceof ApiError ? error.message : 'Не удалось удалить услугу.');
  }
}

async function handleToggleActive(
  root: HTMLElement,
  renderId: number,
  state: AdminState,
  id: number,
): Promise<void> {
  const service = state.services.find((item) => item.id === id);
  if (!service) return;

  try {
    await api.updateService(id, { is_active: !service.is_active });
    if (!isCurrentRender(renderId)) return;
    await loadServices(root, renderId, state);
  } catch (error) {
    if (!isCurrentRender(renderId)) return;
    if (isUnauthorizedError(error)) {
      handleSessionExpired(root, renderId);
      return;
    }
    console.warn('Failed to toggle admin setting', error);
    window.alert(error instanceof ApiError ? error.message : 'Не удалось изменить статус услуги.');
  }
}

async function handleSaveEdit(
  root: HTMLElement,
  renderId: number,
  state: AdminState,
  form: HTMLFormElement,
  id: number,
): Promise<void> {
  const bannerEl = root.querySelector<HTMLElement>(`#edit-banner-${id}`);
  if (bannerEl) bannerEl.innerHTML = '';

  syncBudgetRangeValidity(form);
  if (!form.reportValidity()) return;

  const formData = new FormData(form);
  const description = String(formData.get('description') ?? '').trim();

  const payload: AdminSettingUpdatePayload = {
    service_name: String(formData.get('service_name') ?? '').trim(),
    budget_min: Number(formData.get('budget_min')),
    budget_max: Number(formData.get('budget_max')),
    description: description.length > 0 ? description : null,
    is_active: formData.get('is_active') === 'on',
  };

  try {
    await api.updateService(id, payload);
    if (!isCurrentRender(renderId)) return;
    state.editingId = null;
    await loadServices(root, renderId, state);
  } catch (error) {
    if (!isCurrentRender(renderId)) return;
    if (isUnauthorizedError(error)) {
      handleSessionExpired(root, renderId);
      return;
    }
    const message = error instanceof ApiError ? error.message : 'Не удалось сохранить изменения.';
    if (bannerEl) bannerEl.innerHTML = `<div class="banner banner--error">${escapeHtml(message)}</div>`;
  }
}
