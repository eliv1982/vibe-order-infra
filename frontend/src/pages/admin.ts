import { api, ApiError } from '../api/client';
import type {
  AdminSettingCreatePayload,
  AdminSettingRead,
  AdminSettingUpdatePayload,
} from '../api/types';
import { escapeHtml } from '../utils/html';
import { formatBudget } from '../utils/format';

interface AdminState {
  services: AdminSettingRead[];
  editingId: number | null;
}

export function renderAdmin(root: HTMLElement): void {
  root.innerHTML = adminTemplate();

  const state: AdminState = { services: [], editingId: null };

  wireCreateForm(root, state);
  wireListContainer(root, state);
  void loadServices(root, state);
}

function adminTemplate(): string {
  return `
    <div class="page container admin-page">
      <header class="site-header">
        <a class="brand" href="/" data-link>
          <span class="brand-mark">AUREL</span><span>Detailing</span>
        </a>
        <nav class="site-nav" aria-label="Основная навигация">
          <a href="/" data-link>На главную</a>
        </nav>
      </header>

      <div class="banner banner--notice admin-disclaimer" role="alert">
        <span aria-hidden="true">⚠️</span>
        <div>
          <strong>Учебная административная панель</strong>
          Авторизация ещё не подключена — любой, кто откроет /admin, может изменять услуги.
          Проверка прав доступа будет добавлена на следующем этапе. Эту страницу нельзя считать защищённой.
        </div>
      </div>

      <div class="admin-header">
        <div>
          <span class="eyebrow">Услуги</span>
          <h2>Управление услугами</h2>
        </div>
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

async function loadServices(root: HTMLElement, state: AdminState): Promise<void> {
  const statusEl = root.querySelector<HTMLElement>('#services-list-status');
  if (!statusEl) return;

  statusEl.innerHTML = '<p class="admin-empty">Загружаем услуги…</p>';
  try {
    state.services = await api.getAllServices();
  } catch (error) {
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

function wireCreateForm(root: HTMLElement, state: AdminState): void {
  const form = root.querySelector<HTMLFormElement>('#create-service-form');
  const submitBtn = root.querySelector<HTMLButtonElement>('#create-service-submit');
  const bannerEl = root.querySelector<HTMLElement>('#create-banner');
  if (!form || !submitBtn || !bannerEl) return;

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    void handleCreate(root, form, submitBtn, bannerEl, state);
  });
}

async function handleCreate(
  root: HTMLElement,
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
    form.reset();
    await loadServices(root, state);
  } catch (error) {
    const message = error instanceof ApiError ? error.message : 'Не удалось создать услугу.';
    bannerEl.innerHTML = `<div class="banner banner--error">${escapeHtml(message)}</div>`;
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = 'Добавить услугу';
  }
}

function wireListContainer(root: HTMLElement, state: AdminState): void {
  const listEl = root.querySelector<HTMLElement>('#services-list');
  if (!listEl) return;

  listEl.addEventListener('click', (event) => {
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
      void handleDelete(root, state, id);
    } else if (action === 'toggle-active') {
      void handleToggleActive(root, state, id);
    }
  });

  listEl.addEventListener('submit', (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute('data-edit-form')) return;
    event.preventDefault();
    const id = Number(form.dataset.id);
    void handleSaveEdit(root, state, form, id);
  });
}

async function handleDelete(root: HTMLElement, state: AdminState, id: number): Promise<void> {
  const service = state.services.find((item) => item.id === id);
  const label = service ? `«${service.service_name}»` : 'эту услугу';
  if (!window.confirm(`Удалить услугу ${label}? Это действие необратимо.`)) return;

  try {
    await api.deleteService(id);
    await loadServices(root, state);
  } catch (error) {
    console.warn('Failed to delete admin setting', error);
    window.alert(error instanceof ApiError ? error.message : 'Не удалось удалить услугу.');
  }
}

async function handleToggleActive(root: HTMLElement, state: AdminState, id: number): Promise<void> {
  const service = state.services.find((item) => item.id === id);
  if (!service) return;

  try {
    await api.updateService(id, { is_active: !service.is_active });
    await loadServices(root, state);
  } catch (error) {
    console.warn('Failed to toggle admin setting', error);
    window.alert(error instanceof ApiError ? error.message : 'Не удалось изменить статус услуги.');
  }
}

async function handleSaveEdit(
  root: HTMLElement,
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
    state.editingId = null;
    await loadServices(root, state);
  } catch (error) {
    const message = error instanceof ApiError ? error.message : 'Не удалось сохранить изменения.';
    if (bannerEl) bannerEl.innerHTML = `<div class="banner banner--error">${escapeHtml(message)}</div>`;
  }
}
