import { api, ApiError } from '../api/client';
import type { AdminSettingRead, ApplicationCreatePayload } from '../api/types';
import {
  buildBehaviorMetricPayload,
  initBehaviorTracking,
  trackClick,
  trackHoverEnd,
  trackHoverStart,
} from '../metrics/behaviorMetrics';
import {
  BUSINESS_SIZE_OPTIONS,
  COMPANY_SIZE_OPTIONS,
  DEADLINE_OPTIONS,
  PREFERRED_CONTACT_METHOD_OPTIONS,
  PREFERRED_CONTACT_TIME_OPTIONS,
  REQUESTER_ROLE_OPTIONS,
  TASK_SCOPE_OPTIONS,
  TASK_TYPE_OPTIONS,
} from '../options';
import { escapeHtml } from '../utils/html';
import { clampToRange, computeInitialBudget, formatBudget, pickBudgetStep } from '../utils/format';

interface HomeState {
  selectedService: AdminSettingRead | null;
  selectedBudget: number;
  createdApplicationId: number | null;
}

export function renderHome(root: HTMLElement): void {
  initBehaviorTracking();
  root.innerHTML = homeTemplate();

  const state: HomeState = {
    selectedService: null,
    selectedBudget: 0,
    createdApplicationId: null,
  };

  wireHeroSection(root);
  wireHoverTracking(root);
  wireApplicationForm(root, state);
  void loadServices(root, state);
}

function homeTemplate(): string {
  return `
    <div class="page">
      <div class="blob-field" aria-hidden="true">
        <div class="blob blob--one"></div>
        <div class="blob blob--two"></div>
        <div class="blob blob--three"></div>
      </div>

      <header class="site-header container">
        <a class="brand" href="/">
          <span class="brand-mark">AUREL</span><span>Detailing</span>
        </a>
        <nav class="site-nav" aria-label="Основная навигация">
          <a href="#services">Услуги</a>
          <a href="#application-form">Заявка</a>
          <a href="/admin" data-link>Админ</a>
        </nav>
      </header>

      <section class="hero container" data-hover-section="hero">
        <div class="hero-content">
          <span class="eyebrow">Премиальный автомобильный детейлинг</span>
          <h1>Забота о вашем автомобиле — на уровне произведения искусства</h1>
          <p>
            Ручная полировка, керамическая защита кузова и салонный детейлинг
            для тех, кто ценит совершенство в каждой детали.
          </p>
          <div class="hero-actions">
            <a class="btn btn-primary" href="#application-form" data-track="hero_cta">Оставить заявку</a>
            <a class="btn btn-secondary" href="#services">Смотреть услуги</a>
          </div>
        </div>
      </section>

      <section class="section container" id="services" data-hover-section="services">
        <div class="section-heading">
          <span class="eyebrow">Услуги</span>
          <h2>Выберите услугу</h2>
          <p>Актуальный перечень и бюджет по каждой услуге — обновляется нашей командой.</p>
        </div>

        <div id="services-status" role="status" aria-live="polite"></div>

        <fieldset id="services-fieldset" hidden>
          <legend class="sr-only">Доступные услуги</legend>
          <div class="services-grid" id="services-grid"></div>
        </fieldset>

        <div class="card service-detail" id="service-detail" hidden></div>
      </section>

      <section class="section container" id="application-form" data-hover-section="application_form">
        <div class="section-heading">
          <span class="eyebrow">Заявка</span>
          <h2>Расскажите нам о задаче</h2>
          <p>Заполните форму — мы свяжемся с вами в выбранное время удобным способом.</p>
        </div>

        <div class="card form-card">
          <div id="form-banner" role="status" aria-live="polite"></div>
          <form id="application-form-el" novalidate>
            ${applicationFormFieldsTemplate()}
            <button class="btn btn-primary" type="submit" id="submit-application">
              Отправить заявку
            </button>
          </form>
        </div>
      </section>

      <footer class="site-footer container">
        <div class="footer-inner">
          <span>© AUREL Detailing — учебный проект.</span>
          <div class="footer-links">
            <a href="#services">Услуги</a>
            <a href="#application-form">Заявка</a>
            <a href="/admin" data-link>Административная панель</a>
          </div>
        </div>
      </footer>
    </div>
  `;
}

/**
 * maxLength is required (not defaulted) so every call site states the
 * limit explicitly — it must match the backend Pydantic schema's
 * max_length for that field (see backend/app/schemas/application.py),
 * not an arbitrary frontend guess.
 */
function textField(
  id: string,
  label: string,
  opts: { maxLength: number; required?: boolean; placeholder?: string },
): string {
  const { required = true, placeholder = '', maxLength } = opts;
  return `
    <div class="field">
      <label for="${id}">${label}${required ? '' : ' <span class="hint">(необязательно)</span>'}</label>
      <input
        type="text"
        id="${id}"
        name="${id}"
        ${required ? 'required' : ''}
        maxlength="${maxLength}"
        placeholder="${escapeHtml(placeholder)}"
      />
    </div>
  `;
}

function textareaField(
  id: string,
  label: string,
  opts: { required?: boolean; placeholder?: string } = {},
): string {
  const { required = true, placeholder = '' } = opts;
  return `
    <div class="field">
      <label for="${id}">${label}${required ? '' : ' <span class="hint">(необязательно)</span>'}</label>
      <textarea
        id="${id}"
        name="${id}"
        ${required ? 'required' : ''}
        placeholder="${escapeHtml(placeholder)}"
      ></textarea>
    </div>
  `;
}

function selectField(id: string, label: string, options: string[]): string {
  return `
    <div class="field">
      <label for="${id}">${label}</label>
      <select id="${id}" name="${id}" required>
        <option value="" disabled selected>Выберите вариант</option>
        ${options.map((opt) => `<option value="${escapeHtml(opt)}">${escapeHtml(opt)}</option>`).join('')}
      </select>
    </div>
  `;
}

function radioChipGroup(name: string, label: string, options: string[]): string {
  return `
    <div class="field">
      <span>${label}</span>
      <div class="radio-row" role="radiogroup" aria-label="${escapeHtml(label)}">
        ${options
          .map(
            (opt) => `
          <label class="radio-chip">
            <input type="radio" name="${name}" value="${escapeHtml(opt)}" required />
            <span>${escapeHtml(opt)}</span>
          </label>
        `,
          )
          .join('')}
      </div>
    </div>
  `;
}

function applicationFormFieldsTemplate(): string {
  return `
    <fieldset>
      <legend>Контактные данные</legend>
      <div class="field-grid">
        ${textField('first_name', 'Имя', { maxLength: 100 })}
        ${textField('last_name', 'Фамилия', { maxLength: 100 })}
        ${textField('middle_name', 'Отчество', { required: false, maxLength: 100 })}
        ${textField('contact_data', 'Телефон, email или Telegram', {
          maxLength: 255,
          placeholder: '+7 900 000-00-00 или @username',
        })}
      </div>
    </fieldset>

    <fieldset>
      <legend>О вашем бизнесе</legend>
      <div class="field-grid">
        ${textField('business_niche', 'Сфера деятельности', {
          maxLength: 255,
          placeholder: 'Например, автопарк такси, автосалон',
        })}
        ${selectField('company_size', 'Размер компании', COMPANY_SIZE_OPTIONS)}
        ${selectField('business_size', 'Масштаб бизнеса', BUSINESS_SIZE_OPTIONS)}
        ${selectField('requester_role', 'Ваша роль', REQUESTER_ROLE_OPTIONS)}
      </div>
      ${textareaField('business_info', 'Коротко о вашем бизнесе')}
    </fieldset>

    <fieldset>
      <legend>Детали задачи</legend>
      <div class="field-grid">
        ${selectField('task_scope', 'Формат сотрудничества', TASK_SCOPE_OPTIONS)}
        ${selectField('task_type', 'Тип задачи', TASK_TYPE_OPTIONS)}
        ${selectField('deadline', 'Срок выполнения', DEADLINE_OPTIONS)}
      </div>
      ${textareaField('need_scope', 'Что именно нужно сделать')}
    </fieldset>

    <fieldset>
      <legend>Как с вами связаться</legend>
      ${radioChipGroup(
        'preferred_contact_method',
        'Предпочитаемый способ связи',
        PREFERRED_CONTACT_METHOD_OPTIONS,
      )}
      ${selectField('preferred_contact_time', 'Удобное время', PREFERRED_CONTACT_TIME_OPTIONS)}
    </fieldset>

    <fieldset>
      <legend>Комментарий</legend>
      ${textareaField('comment', 'Комментарий', {
        required: false,
        placeholder: 'Любые дополнительные пожелания',
      })}
    </fieldset>
  `;
}

function wireHeroSection(root: HTMLElement): void {
  root.querySelector('[data-track="hero_cta"]')?.addEventListener('click', () => {
    trackClick('hero_cta');
  });
}

function wireHoverTracking(root: HTMLElement): void {
  root.querySelectorAll<HTMLElement>('[data-hover-section]').forEach((el) => {
    const section = el.dataset.hoverSection;
    if (!section) return;
    el.addEventListener('mouseenter', () => trackHoverStart(section));
    el.addEventListener('mouseleave', () => trackHoverEnd(section));
  });
}

/** Exported for testing (see pages/render.test.ts) — must escape user-editable service_name/description. */
export function serviceCardTemplate(service: AdminSettingRead): string {
  return `
    <label class="service-card">
      <input type="radio" name="selected_service" value="${service.id}" required />
      <span class="service-card-body">
        <h3>${escapeHtml(service.service_name)}</h3>
        <p>${formatBudget(Number(service.budget_min))} – ${formatBudget(Number(service.budget_max))}</p>
      </span>
    </label>
  `;
}

/** Pure — unit tested in pages/render.test.ts. Used both to render the
 * grid and to re-check the selected service right before submit. */
export function isServiceIdInList(services: AdminSettingRead[], serviceId: number): boolean {
  return services.some((service) => service.id === serviceId);
}

async function loadServices(root: HTMLElement, state: HomeState): Promise<void> {
  const statusEl = root.querySelector<HTMLElement>('#services-status');
  const fieldsetEl = root.querySelector<HTMLFieldSetElement>('#services-fieldset');
  const gridEl = root.querySelector<HTMLElement>('#services-grid');
  if (!statusEl || !fieldsetEl || !gridEl) return;

  statusEl.innerHTML = '<p class="service-empty">Загружаем услуги…</p>';

  let services: AdminSettingRead[];
  try {
    services = await api.getActiveServices();
  } catch (error) {
    console.warn('Failed to load active services', error);
    statusEl.innerHTML =
      '<p class="service-error">Не удалось загрузить список услуг. Обновите страницу или свяжитесь с нами напрямую.</p>';
    return;
  }

  if (services.length === 0) {
    statusEl.innerHTML =
      '<p class="service-empty">Услуги временно не опубликованы. Свяжитесь с нами напрямую.</p>';
    return;
  }

  statusEl.innerHTML = '';
  fieldsetEl.hidden = false;
  gridEl.innerHTML = services.map(serviceCardTemplate).join('');

  gridEl.querySelectorAll<HTMLInputElement>('input[type="radio"]').forEach((input) => {
    input.addEventListener('change', () => {
      const service = services.find((item) => item.id === Number(input.value));
      if (!service) return;
      trackClick('service_card');
      selectService(root, state, service);
    });
  });
}

function selectService(root: HTMLElement, state: HomeState, service: AdminSettingRead): void {
  state.selectedService = service;

  const min = Number(service.budget_min);
  const max = Number(service.budget_max);
  const step = pickBudgetStep(min, max);
  const initial = computeInitialBudget(min, max, step);
  state.selectedBudget = initial;

  const detailEl = root.querySelector<HTMLElement>('#service-detail');
  if (!detailEl) return;

  detailEl.hidden = false;
  detailEl.innerHTML = `
    <h3>${escapeHtml(service.service_name)}</h3>
    ${service.description ? `<p class="service-detail-desc">${escapeHtml(service.description)}</p>` : ''}
    <div class="budget-range-label">
      <span>${formatBudget(min)}</span>
      <span>${formatBudget(max)}</span>
    </div>
    <label class="sr-only" for="budget-slider">Желаемый бюджет</label>
    <input
      type="range"
      class="budget-slider"
      id="budget-slider"
      min="${min}"
      max="${max}"
      step="${step}"
      value="${initial}"
      ${min === max ? 'disabled' : ''}
    />
    <p>Ваш бюджет: <span class="budget-value" id="budget-value">${formatBudget(initial)}</span></p>
  `;

  const slider = detailEl.querySelector<HTMLInputElement>('#budget-slider');
  const valueEl = detailEl.querySelector<HTMLElement>('#budget-value');
  slider?.addEventListener('input', () => {
    // Defensive clamp: the range input already constrains itself to
    // [min, max], but state.selectedBudget must never drift outside it.
    const value = clampToRange(Number(slider.value), min, max);
    state.selectedBudget = value;
    if (valueEl) valueEl.textContent = formatBudget(value);
  });
}

function wireApplicationForm(root: HTMLElement, state: HomeState): void {
  const form = root.querySelector<HTMLFormElement>('#application-form-el');
  const submitBtn = root.querySelector<HTMLButtonElement>('#submit-application');
  const bannerEl = root.querySelector<HTMLElement>('#form-banner');
  if (!form || !submitBtn || !bannerEl) return;

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    trackClick('submit_application');
    void handleSubmit(root, form, submitBtn, bannerEl, state);
  });
}

async function handleSubmit(
  root: HTMLElement,
  form: HTMLFormElement,
  submitBtn: HTMLButtonElement,
  bannerEl: HTMLElement,
  state: HomeState,
): Promise<void> {
  bannerEl.innerHTML = '';

  const selectedService = state.selectedService;
  if (!selectedService) {
    bannerEl.innerHTML =
      '<div class="banner banner--error">Пожалуйста, выберите услугу перед отправкой заявки.</div>';
    document.getElementById('services')?.scrollIntoView({ behavior: 'smooth' });
    return;
  }

  if (!form.reportValidity()) {
    return;
  }

  submitBtn.disabled = true;
  submitBtn.textContent = 'Проверяем услугу…';

  // Lightweight re-check against the same /active endpoint the grid was
  // built from: the service could have been deactivated or deleted in
  // /admin between page load and this submit.
  let isStillActive: boolean;
  try {
    const activeServices = await api.getActiveServices();
    isStillActive = isServiceIdInList(activeServices, selectedService.id);
  } catch (error) {
    console.warn('Failed to re-verify selected service before submit', error);
    isStillActive = false;
  }

  if (!isStillActive) {
    submitBtn.disabled = false;
    submitBtn.textContent = 'Отправить заявку';
    bannerEl.innerHTML =
      '<div class="banner banner--error">Выбранная услуга больше недоступна. Пожалуйста, выберите услугу заново.</div>';
    state.selectedService = null;
    const detailEl = root.querySelector<HTMLElement>('#service-detail');
    if (detailEl) detailEl.hidden = true;
    await loadServices(root, state);
    document.getElementById('services')?.scrollIntoView({ behavior: 'smooth' });
    return;
  }

  submitBtn.textContent = 'Отправляем…';

  const formData = new FormData(form);
  const getValue = (name: string): string => String(formData.get(name) ?? '').trim();
  const getOptionalValue = (name: string): string | null => {
    const value = getValue(name);
    return value.length > 0 ? value : null;
  };

  const min = Number(selectedService.budget_min);
  const max = Number(selectedService.budget_max);

  const payload: ApplicationCreatePayload = {
    first_name: getValue('first_name'),
    last_name: getValue('last_name'),
    middle_name: getOptionalValue('middle_name'),
    contact_data: getValue('contact_data'),
    business_niche: getValue('business_niche'),
    company_size: getValue('company_size'),
    business_info: getValue('business_info'),
    task_scope: getValue('task_scope'),
    requester_role: getValue('requester_role'),
    business_size: getValue('business_size'),
    need_scope: getValue('need_scope'),
    deadline: getValue('deadline'),
    task_type: getValue('task_type'),
    interested_product: selectedService.service_name,
    // Final defensive clamp — the value sent to the backend must never
    // fall outside the service's own [budget_min, budget_max].
    budget: clampToRange(state.selectedBudget, min, max),
    preferred_contact_method: getValue('preferred_contact_method'),
    preferred_contact_time: getValue('preferred_contact_time'),
    comment: getOptionalValue('comment'),
  };

  try {
    const application = await api.createApplication(payload);
    state.createdApplicationId = application.id;

    form.hidden = true;
    bannerEl.innerHTML =
      '<div class="banner banner--success">Заявка отправлена! Мы свяжемся с вами в ближайшее время.</div>';

    void sendBehaviorMetrics(application.id);
  } catch (error) {
    const message =
      error instanceof ApiError ? error.message : 'Не удалось отправить заявку. Попробуйте ещё раз.';
    bannerEl.innerHTML = `<div class="banner banner--error">${escapeHtml(message)}</div>`;
    submitBtn.disabled = false;
    submitBtn.textContent = 'Отправить заявку';
  }
}

async function sendBehaviorMetrics(applicationId: number): Promise<void> {
  try {
    await api.createBehaviorMetric(buildBehaviorMetricPayload(applicationId));
  } catch (error) {
    // Metrics are best-effort: a failure here must never affect the
    // already-successful Application — surface it only for developers.
    console.warn('Failed to save behavior metrics', error);
  }
}
