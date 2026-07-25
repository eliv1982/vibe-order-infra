// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api } from '../api/client';
import { getToken, saveToken } from '../api/tokenStorage';
import type {
  AdminRead,
  AdminSettingRead,
  ApplicationPriorityRead,
  AuthCheckResponse,
  PrioritizedApplicationList,
  TokenResponse,
} from '../api/types';
import { renderAdmin, shouldOfferRegistration } from './admin';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    api: {
      checkAuthStatus: vi.fn(),
      registerAdmin: vi.fn(),
      loginAdmin: vi.fn(),
      getCurrentAdmin: vi.fn(),
      getAllServices: vi.fn(),
      createService: vi.fn(),
      updateService: vi.fn(),
      deleteService: vi.fn(),
      getActiveServices: vi.fn(),
      createApplication: vi.fn(),
      createBehaviorMetric: vi.fn(),
      getPrioritizedApplications: vi.fn(),
    },
  };
});

function makeCheck(overrides: Partial<AuthCheckResponse> = {}): AuthCheckResponse {
  return { admin_exists: true, registration_allowed: false, ...overrides };
}

function makeAdmin(overrides: Partial<AdminRead> = {}): AdminRead {
  return {
    id: 1,
    username: 'admin',
    is_active: true,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function makeToken(overrides: Partial<TokenResponse> = {}): TokenResponse {
  return { access_token: 'a.jwt.token', token_type: 'bearer', expires_in: 1800, ...overrides };
}

/** Minimal fixture for the "Заявки" tab's own deferred-response tests below
 * — full field-by-field coverage of ApplicationPriorityRead lives in
 * adminApplications.test.ts; this file only needs enough to exercise
 * admin.ts's tab-switch lifecycle wiring. */
function makeApplicationItem(overrides: Partial<ApplicationPriorityRead> = {}): ApplicationPriorityRead {
  return {
    application: {
      id: 1,
      first_name: 'Иван',
      last_name: 'Петров',
      middle_name: null,
      contact_data: '+7 900 000-00-00',
      business_niche: 'Личный автомобиль',
      company_size: 'Седан',
      business_info: 'BMW X5',
      task_scope: 'Разовая услуга',
      requester_role: 'Владелец',
      business_size: 'Один автомобиль',
      need_scope: 'Полировка',
      deadline: 'В течение недели',
      task_type: 'Восстановление',
      interested_product: 'Полировка кузова',
      budget: '25000.00',
      preferred_contact_method: 'Телефон',
      preferred_contact_time: 'Утро',
      comment: null,
      created_at: '2026-01-10T09:00:00Z',
      updated_at: '2026-01-10T09:00:00Z',
    },
    priority_score: 55,
    priority_level: 'medium',
    priority_label: 'Средняя',
    reasons: [],
    recommended_action: 'Связаться сегодня',
    recommended_team: 'Специалист детейлинга',
    requires_personal_manager: false,
    ...overrides,
  };
}

function makeApplicationList(items: ApplicationPriorityRead[]): PrioritizedApplicationList {
  return { items, total: items.length, skip: 0, limit: 100 };
}

/** A controllable promise for deterministic "slow request resolves late"
 * tests — no arbitrary sleeps, the test decides exactly when it settles. */
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

/** Lets already-queued microtasks/macrotasks (e.g. a just-resolved deferred's
 * .then chain) run to completion before assertions. */
async function flush(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  vi.mocked(api.getAllServices).mockResolvedValue([]);
  vi.mocked(api.getPrioritizedApplications).mockResolvedValue({
    items: [],
    total: 0,
    skip: 0,
    limit: 100,
  });
});

afterEach(() => {
  sessionStorage.clear();
});

describe('shouldOfferRegistration', () => {
  it('is true only when no admin exists and registration is allowed', () => {
    expect(shouldOfferRegistration(makeCheck({ admin_exists: false, registration_allowed: true }))).toBe(
      true,
    );
  });

  it('is false once an admin exists, even if registration_allowed were somehow true', () => {
    expect(
      shouldOfferRegistration(makeCheck({ admin_exists: true, registration_allowed: false })),
    ).toBe(false);
  });
});

describe('renderAdmin — auth gate', () => {
  it('shows a loading state before checkAuthStatus resolves', () => {
    vi.mocked(api.checkAuthStatus).mockReturnValue(new Promise(() => {})); // never resolves

    const root = document.createElement('div');
    renderAdmin(root);

    expect(root.textContent).toContain('Проверяем авторизацию');
  });

  it('shows the registration form when no admin exists yet', async () => {
    vi.mocked(api.checkAuthStatus).mockResolvedValue(
      makeCheck({ admin_exists: false, registration_allowed: true }),
    );

    const root = document.createElement('div');
    renderAdmin(root);

    await vi.waitFor(() => expect(root.querySelector('#register-form')).not.toBeNull());
  });

  it('shows the login form when an admin exists and no token is stored', async () => {
    vi.mocked(api.checkAuthStatus).mockResolvedValue(makeCheck());

    const root = document.createElement('div');
    renderAdmin(root);

    await vi.waitFor(() => expect(root.querySelector('#login-form')).not.toBeNull());
    expect(api.getCurrentAdmin).not.toHaveBeenCalled();
  });

  it('shows the admin panel when a valid token is stored', async () => {
    saveToken('valid-token');
    vi.mocked(api.checkAuthStatus).mockResolvedValue(makeCheck());
    vi.mocked(api.getCurrentAdmin).mockResolvedValue(makeAdmin({ username: 'root' }));

    const root = document.createElement('div');
    renderAdmin(root);

    await vi.waitFor(() => expect(root.querySelector('#services-list')).not.toBeNull());
    expect(root.querySelector('#register-form')).toBeNull();
    expect(root.querySelector('#login-form')).toBeNull();
    expect(root.textContent).toContain('root');
  });

  it('clears an invalid/expired stored token and shows login with a notice', async () => {
    saveToken('stale-token');
    vi.mocked(api.checkAuthStatus).mockResolvedValue(makeCheck());
    vi.mocked(api.getCurrentAdmin).mockRejectedValue(
      new ApiError('Could not validate credentials', 401),
    );

    const root = document.createElement('div');
    renderAdmin(root);

    await vi.waitFor(() => expect(root.querySelector('#login-form')).not.toBeNull());
    expect(root.textContent).toContain('Сессия истекла');
    expect(getToken()).toBeNull();
  });

  it('shows a retry-able error on a network failure, without touching a stored token', async () => {
    saveToken('still-good-token');
    vi.mocked(api.checkAuthStatus).mockRejectedValue(new TypeError('Failed to fetch'));

    const root = document.createElement('div');
    renderAdmin(root);

    await vi.waitFor(() => expect(root.querySelector('#auth-check-retry')).not.toBeNull());
    expect(getToken()).toBe('still-good-token');
  });
});

describe('logout', () => {
  it('clears the token and returns to the login form', async () => {
    saveToken('valid-token');
    vi.mocked(api.checkAuthStatus).mockResolvedValue(makeCheck());
    vi.mocked(api.getCurrentAdmin).mockResolvedValue(makeAdmin());

    const root = document.createElement('div');
    renderAdmin(root);
    await vi.waitFor(() => expect(root.querySelector('#logout-button')).not.toBeNull());

    root.querySelector<HTMLButtonElement>('#logout-button')!.click();

    expect(getToken()).toBeNull();
    expect(root.querySelector('#login-form')).not.toBeNull();
  });
});

describe('register form', () => {
  async function renderRegister(root: HTMLElement): Promise<void> {
    vi.mocked(api.checkAuthStatus).mockResolvedValue(
      makeCheck({ admin_exists: false, registration_allowed: true }),
    );
    renderAdmin(root);
    await vi.waitFor(() => expect(root.querySelector('#register-form')).not.toBeNull());
  }

  function fillAndSubmit(
    root: HTMLElement,
    values: { username: string; password: string; confirm: string },
  ): void {
    const form = root.querySelector<HTMLFormElement>('#register-form')!;
    (form.querySelector('#register-username') as HTMLInputElement).value = values.username;
    (form.querySelector('#register-password') as HTMLInputElement).value = values.password;
    (form.querySelector('#register-password-confirm') as HTMLInputElement).value = values.confirm;
    form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
  }

  it('blocks submission when password and confirmation do not match, without calling the API', async () => {
    const root = document.createElement('div');
    await renderRegister(root);

    fillAndSubmit(root, { username: 'admin', password: 'StrongPassw0rd!', confirm: 'Different!' });

    expect(api.registerAdmin).not.toHaveBeenCalled();
  });

  it('on a 409 conflict, re-checks auth status and transitions to the login form', async () => {
    const root = document.createElement('div');
    await renderRegister(root);
    vi.mocked(api.registerAdmin).mockRejectedValue(new ApiError('conflict', 409));
    vi.mocked(api.checkAuthStatus).mockResolvedValue(makeCheck());

    fillAndSubmit(root, {
      username: 'admin',
      password: 'StrongPassw0rd!',
      confirm: 'StrongPassw0rd!',
    });

    await vi.waitFor(() => expect(root.querySelector('#login-form')).not.toBeNull());
    expect(api.checkAuthStatus).toHaveBeenCalledTimes(2); // initial + post-409 re-check
  });

  it('auto-logs in and shows the admin panel after a successful registration', async () => {
    const root = document.createElement('div');
    await renderRegister(root);
    vi.mocked(api.registerAdmin).mockResolvedValue(makeAdmin());
    vi.mocked(api.loginAdmin).mockResolvedValue(makeToken());
    vi.mocked(api.getCurrentAdmin).mockResolvedValue(makeAdmin());

    fillAndSubmit(root, {
      username: 'admin',
      password: 'StrongPassw0rd!',
      confirm: 'StrongPassw0rd!',
    });

    await vi.waitFor(() => expect(root.querySelector('#services-list')).not.toBeNull());
    expect(api.loginAdmin).toHaveBeenCalledWith({ username: 'admin', password: 'StrongPassw0rd!' });
    expect(getToken()).toBe('a.jwt.token');
  });
});

describe('login form', () => {
  async function renderLogin(root: HTMLElement): Promise<void> {
    vi.mocked(api.checkAuthStatus).mockResolvedValue(makeCheck());
    renderAdmin(root);
    await vi.waitFor(() => expect(root.querySelector('#login-form')).not.toBeNull());
  }

  function fillAndSubmit(root: HTMLElement, username: string, password: string): void {
    const form = root.querySelector<HTMLFormElement>('#login-form')!;
    (form.querySelector('#login-username') as HTMLInputElement).value = username;
    (form.querySelector('#login-password') as HTMLInputElement).value = password;
    form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
  }

  it('shows one fixed neutral message on a 401, regardless of the backend detail text', async () => {
    const root = document.createElement('div');
    await renderLogin(root);
    vi.mocked(api.loginAdmin).mockRejectedValue(
      new ApiError('some backend-specific technical detail', 401),
    );

    fillAndSubmit(root, 'nobody', 'wrong-password');

    await vi.waitFor(() => expect(root.textContent).toContain('Неверное имя пользователя или пароль'));
    expect(root.textContent).not.toContain('some backend-specific technical detail');
    expect(getToken()).toBeNull();
  });

  it('on success, stores the token, verifies via /me, then shows the admin panel', async () => {
    const root = document.createElement('div');
    await renderLogin(root);
    vi.mocked(api.loginAdmin).mockResolvedValue(makeToken());
    vi.mocked(api.getCurrentAdmin).mockResolvedValue(makeAdmin());

    fillAndSubmit(root, 'admin', 'StrongPassw0rd!');

    await vi.waitFor(() => expect(root.querySelector('#services-list')).not.toBeNull());
    expect(api.getCurrentAdmin).toHaveBeenCalledTimes(1);
  });
});

describe('authenticated CRUD panel', () => {
  async function renderPanel(root: HTMLElement, admin = makeAdmin()): Promise<void> {
    saveToken('valid-token');
    vi.mocked(api.checkAuthStatus).mockResolvedValue(makeCheck());
    vi.mocked(api.getCurrentAdmin).mockResolvedValue(admin);
    renderAdmin(root);
    await vi.waitFor(() => expect(root.querySelector('#services-list')).not.toBeNull());
  }

  it('sends create-service requests through the auth-aware client (createService is invoked)', async () => {
    const root = document.createElement('div');
    await renderPanel(root);
    vi.mocked(api.createService).mockResolvedValue({
      id: 1,
      service_name: 'X',
      budget_min: '1',
      budget_max: '2',
      description: null,
      is_active: true,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    });

    const form = root.querySelector<HTMLFormElement>('#create-service-form')!;
    (form.querySelector('[name="service_name"]') as HTMLInputElement).value = 'Полировка';
    (form.querySelector('[name="budget_min"]') as HTMLInputElement).value = '100';
    (form.querySelector('[name="budget_max"]') as HTMLInputElement).value = '200';
    form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));

    await vi.waitFor(() => expect(api.createService).toHaveBeenCalledTimes(1));
  });

  it('escapes a malicious admin username instead of rendering it as markup', async () => {
    const root = document.createElement('div');
    await renderPanel(root, makeAdmin({ username: '<script>alert(1)</script>' }));

    expect(root.innerHTML).not.toContain('<script>alert(1)</script>');
    expect(root.innerHTML).toContain('&lt;script&gt;alert(1)&lt;/script&gt;');
  });

  it('a 401 on a CRUD action clears the token and returns to the login form', async () => {
    vi.mocked(api.getAllServices).mockResolvedValue([
      {
        id: 1,
        service_name: 'Полировка',
        budget_min: '100',
        budget_max: '200',
        description: null,
        is_active: true,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
    ]);
    const root = document.createElement('div');
    await renderPanel(root);

    vi.mocked(api.deleteService).mockRejectedValue(
      new ApiError('Could not validate credentials', 401),
    );
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    await vi.waitFor(() =>
      expect(root.querySelector('button[data-action="delete"]')).not.toBeNull(),
    );
    root.querySelector<HTMLButtonElement>('button[data-action="delete"]')!.click();

    await vi.waitFor(() => expect(root.querySelector('#login-form')).not.toBeNull());
    expect(getToken()).toBeNull();
  });
});

describe('async generation guard', () => {
  it('a slow initial /auth/check does not repaint the UI after a newer renderAdmin() call', async () => {
    const firstCheck = createDeferred<AuthCheckResponse>();
    vi.mocked(api.checkAuthStatus).mockReturnValueOnce(firstCheck.promise);

    const root = document.createElement('div');
    renderAdmin(root); // generation 1 — stuck loading on a slow /auth/check
    expect(root.textContent).toContain('Проверяем авторизацию');

    vi.mocked(api.checkAuthStatus).mockResolvedValueOnce(makeCheck());
    renderAdmin(root); // generation 2 — admin exists, no token -> login form
    await vi.waitFor(() => expect(root.querySelector('#login-form')).not.toBeNull());

    // The stale generation-1 check now resolves as "no admin yet" — it must
    // not switch the already-current login view to the registration view.
    firstCheck.resolve(makeCheck({ admin_exists: false, registration_allowed: true }));
    await flush();

    expect(root.querySelector('#register-form')).toBeNull();
    expect(root.querySelector('#login-form')).not.toBeNull();
  });

  it('a slow stored-token /me check does not open the panel after a newer render (e.g. post-logout)', async () => {
    saveToken('token-from-generation-1');
    vi.mocked(api.checkAuthStatus).mockResolvedValue(makeCheck());
    const meDeferred = createDeferred<AdminRead>();
    vi.mocked(api.getCurrentAdmin).mockReturnValueOnce(meDeferred.promise);

    const root = document.createElement('div');
    renderAdmin(root); // generation 1 — awaiting /me for the stored token
    await vi.waitFor(() => expect(api.getCurrentAdmin).toHaveBeenCalledTimes(1));

    // Simulate the effect of a logout / navigation elsewhere: the token is
    // gone and a fresh render takes over while the old /me is still pending.
    sessionStorage.clear();
    renderAdmin(root); // generation 2
    await vi.waitFor(() => expect(root.querySelector('#login-form')).not.toBeNull());

    meDeferred.resolve(makeAdmin());
    await flush();

    expect(root.querySelector('#services-list')).toBeNull();
    expect(root.querySelector('#login-form')).not.toBeNull();
  });

  it('a slow in-flight CRUD request does not reopen the panel after clicking logout', async () => {
    saveToken('valid-token');
    vi.mocked(api.checkAuthStatus).mockResolvedValue(makeCheck());
    vi.mocked(api.getCurrentAdmin).mockResolvedValue(makeAdmin());
    vi.mocked(api.getAllServices).mockResolvedValue([
      {
        id: 1,
        service_name: 'Полировка',
        budget_min: '100',
        budget_max: '200',
        description: null,
        is_active: true,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
    ]);

    const root = document.createElement('div');
    renderAdmin(root);
    await vi.waitFor(() =>
      expect(root.querySelector('button[data-action="toggle-active"]')).not.toBeNull(),
    );

    const toggleDeferred = createDeferred<AdminSettingRead>();
    vi.mocked(api.updateService).mockReturnValueOnce(toggleDeferred.promise);
    root.querySelector<HTMLButtonElement>('button[data-action="toggle-active"]')!.click();
    await vi.waitFor(() => expect(api.updateService).toHaveBeenCalledTimes(1));

    root.querySelector<HTMLButtonElement>('#logout-button')!.click();
    expect(root.querySelector('#login-form')).not.toBeNull();

    toggleDeferred.resolve({
      id: 1,
      service_name: 'Полировка',
      budget_min: '100',
      budget_max: '200',
      description: null,
      is_active: false,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    });
    await flush();

    expect(root.querySelector('#services-list')).toBeNull();
    expect(root.querySelector('#login-form')).not.toBeNull();
  });

  it('two sequential renderAdmin() calls — the first one finishing later does not override the second', async () => {
    saveToken('valid-token');
    vi.mocked(api.checkAuthStatus).mockResolvedValue(makeCheck());
    const firstMe = createDeferred<AdminRead>();
    vi.mocked(api.getCurrentAdmin).mockReturnValueOnce(firstMe.promise);

    const root = document.createElement('div');
    renderAdmin(root); // generation 1
    await vi.waitFor(() => expect(api.getCurrentAdmin).toHaveBeenCalledTimes(1));

    vi.mocked(api.getCurrentAdmin).mockResolvedValueOnce(makeAdmin({ username: 'second-gen' }));
    renderAdmin(root); // generation 2
    await vi.waitFor(() => expect(root.textContent).toContain('second-gen'));

    firstMe.resolve(makeAdmin({ username: 'first-gen' }));
    await flush();

    expect(root.textContent).toContain('second-gen');
    expect(root.textContent).not.toContain('first-gen');
  });

  it('clicking Retry starts a new generation — a late resolution of the original failed check cannot repaint, and the retry itself decides the final UI', async () => {
    const firstCheck = createDeferred<AuthCheckResponse>();
    vi.mocked(api.checkAuthStatus).mockReturnValueOnce(firstCheck.promise);

    const root = document.createElement('div');
    renderAdmin(root); // generation 1
    firstCheck.reject(new TypeError('Failed to fetch'));
    await vi.waitFor(() => expect(root.querySelector('#auth-check-retry')).not.toBeNull());

    const retryButton = root.querySelector<HTMLButtonElement>('#auth-check-retry')!;
    const secondCheck = createDeferred<AuthCheckResponse>();
    vi.mocked(api.checkAuthStatus).mockReturnValueOnce(secondCheck.promise);

    retryButton.click(); // generation 2 — starts a fresh auth-check
    expect(api.checkAuthStatus).toHaveBeenCalledTimes(2);

    // A stale invocation of the very same (now-detached) retry handler must
    // not be able to kick off yet another auth-check under the old renderId.
    retryButton.click();
    expect(api.checkAuthStatus).toHaveBeenCalledTimes(2);

    // The new generation's own check now resolves — it alone decides the UI.
    secondCheck.resolve(makeCheck());
    await vi.waitFor(() => expect(root.querySelector('#login-form')).not.toBeNull());

    expect(root.querySelector('#auth-check-retry')).toBeNull();
  });

  it('a stale login-flow does not save the token or open the panel after a newer renderAdmin()', async () => {
    vi.mocked(api.checkAuthStatus).mockResolvedValue(makeCheck());
    const root = document.createElement('div');
    renderAdmin(root); // generation 1
    await vi.waitFor(() => expect(root.querySelector('#login-form')).not.toBeNull());

    const loginDeferred = createDeferred<TokenResponse>();
    vi.mocked(api.loginAdmin).mockReturnValueOnce(loginDeferred.promise);

    const form = root.querySelector<HTMLFormElement>('#login-form')!;
    (form.querySelector('#login-username') as HTMLInputElement).value = 'admin';
    (form.querySelector('#login-password') as HTMLInputElement).value = 'StrongPassw0rd!';
    form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
    await vi.waitFor(() => expect(api.loginAdmin).toHaveBeenCalledTimes(1));

    // A fresh renderAdmin() (e.g. navigated away and back) supersedes this
    // in-flight login before it ever gets a token back.
    renderAdmin(root); // generation 2
    await vi.waitFor(() => expect(root.querySelector('#login-form')).not.toBeNull());

    loginDeferred.resolve(makeToken());
    await flush();

    expect(getToken()).toBeNull();
    expect(root.querySelector('#services-list')).toBeNull();
    expect(root.querySelector('#login-form')).not.toBeNull();
  });

  it('a stale register-flow does not auto-login or repaint after a newer renderAdmin()', async () => {
    vi.mocked(api.checkAuthStatus).mockResolvedValue(
      makeCheck({ admin_exists: false, registration_allowed: true }),
    );
    const root = document.createElement('div');
    renderAdmin(root); // generation 1
    await vi.waitFor(() => expect(root.querySelector('#register-form')).not.toBeNull());

    const registerDeferred = createDeferred<AdminRead>();
    vi.mocked(api.registerAdmin).mockReturnValueOnce(registerDeferred.promise);

    const form = root.querySelector<HTMLFormElement>('#register-form')!;
    (form.querySelector('#register-username') as HTMLInputElement).value = 'admin';
    (form.querySelector('#register-password') as HTMLInputElement).value = 'StrongPassw0rd!';
    (form.querySelector('#register-password-confirm') as HTMLInputElement).value =
      'StrongPassw0rd!';
    form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
    await vi.waitFor(() => expect(api.registerAdmin).toHaveBeenCalledTimes(1));

    // A fresh renderAdmin() supersedes this in-flight registration — an
    // admin now exists per this newer check, so generation 2 shows login.
    vi.mocked(api.checkAuthStatus).mockResolvedValueOnce(makeCheck());
    renderAdmin(root); // generation 2
    await vi.waitFor(() => expect(root.querySelector('#login-form')).not.toBeNull());

    registerDeferred.resolve(makeAdmin());
    await flush();

    expect(api.loginAdmin).not.toHaveBeenCalled();
    expect(root.querySelector('#register-form')).toBeNull();
    expect(root.querySelector('#services-list')).toBeNull();
    expect(root.querySelector('#login-form')).not.toBeNull();
  });
});

describe('in-flight submit guard', () => {
  it('a repeated login submit while a request is in flight sends only one request', async () => {
    vi.mocked(api.checkAuthStatus).mockResolvedValue(makeCheck());
    const root = document.createElement('div');
    renderAdmin(root);
    await vi.waitFor(() => expect(root.querySelector('#login-form')).not.toBeNull());

    const loginDeferred = createDeferred<TokenResponse>();
    vi.mocked(api.loginAdmin).mockReturnValueOnce(loginDeferred.promise);

    const form = root.querySelector<HTMLFormElement>('#login-form')!;
    (form.querySelector('#login-username') as HTMLInputElement).value = 'admin';
    (form.querySelector('#login-password') as HTMLInputElement).value = 'StrongPassw0rd!';

    // Simulates rapid double-click / repeated Enter while the request is in flight.
    form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
    form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
    form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));

    expect(api.loginAdmin).toHaveBeenCalledTimes(1);
    expect(root.querySelector<HTMLButtonElement>('#login-submit')!.disabled).toBe(true);

    vi.mocked(api.getCurrentAdmin).mockResolvedValue(makeAdmin());
    loginDeferred.resolve(makeToken());
    await vi.waitFor(() => expect(root.querySelector('#services-list')).not.toBeNull());
  });

  it('after a login error, the form is submittable again and a fresh submit sends a new request', async () => {
    vi.mocked(api.checkAuthStatus).mockResolvedValue(makeCheck());
    const root = document.createElement('div');
    renderAdmin(root);
    await vi.waitFor(() => expect(root.querySelector('#login-form')).not.toBeNull());

    vi.mocked(api.loginAdmin).mockRejectedValueOnce(new ApiError('nope', 401));

    const form = root.querySelector<HTMLFormElement>('#login-form')!;
    (form.querySelector('#login-username') as HTMLInputElement).value = 'admin';
    (form.querySelector('#login-password') as HTMLInputElement).value = 'wrong';
    form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));

    await vi.waitFor(() =>
      expect(root.querySelector<HTMLButtonElement>('#login-submit')!.disabled).toBe(false),
    );

    vi.mocked(api.loginAdmin).mockResolvedValueOnce(makeToken());
    vi.mocked(api.getCurrentAdmin).mockResolvedValue(makeAdmin());
    form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));

    await vi.waitFor(() => expect(root.querySelector('#services-list')).not.toBeNull());
    expect(api.loginAdmin).toHaveBeenCalledTimes(2);
  });

  it('a repeated register submit while a request is in flight sends only one request', async () => {
    vi.mocked(api.checkAuthStatus).mockResolvedValue(
      makeCheck({ admin_exists: false, registration_allowed: true }),
    );
    const root = document.createElement('div');
    renderAdmin(root);
    await vi.waitFor(() => expect(root.querySelector('#register-form')).not.toBeNull());

    const registerDeferred = createDeferred<AdminRead>();
    vi.mocked(api.registerAdmin).mockReturnValueOnce(registerDeferred.promise);

    const form = root.querySelector<HTMLFormElement>('#register-form')!;
    (form.querySelector('#register-username') as HTMLInputElement).value = 'admin';
    (form.querySelector('#register-password') as HTMLInputElement).value = 'StrongPassw0rd!';
    (form.querySelector('#register-password-confirm') as HTMLInputElement).value =
      'StrongPassw0rd!';

    form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
    form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));

    expect(api.registerAdmin).toHaveBeenCalledTimes(1);
    expect(root.querySelector<HTMLButtonElement>('#register-submit')!.disabled).toBe(true);

    vi.mocked(api.loginAdmin).mockResolvedValue(makeToken());
    vi.mocked(api.getCurrentAdmin).mockResolvedValue(makeAdmin());
    registerDeferred.resolve(makeAdmin());

    await vi.waitFor(() => expect(root.querySelector('#services-list')).not.toBeNull());
  });

  it('after a register error, the form is submittable again and a fresh submit sends a second request', async () => {
    vi.mocked(api.checkAuthStatus).mockResolvedValue(
      makeCheck({ admin_exists: false, registration_allowed: true }),
    );
    const root = document.createElement('div');
    renderAdmin(root);
    await vi.waitFor(() => expect(root.querySelector('#register-form')).not.toBeNull());

    vi.mocked(api.registerAdmin).mockRejectedValueOnce(new ApiError('weak password', 422));

    const form = root.querySelector<HTMLFormElement>('#register-form')!;
    (form.querySelector('#register-username') as HTMLInputElement).value = 'admin';
    (form.querySelector('#register-password') as HTMLInputElement).value = 'StrongPassw0rd!';
    (form.querySelector('#register-password-confirm') as HTMLInputElement).value =
      'StrongPassw0rd!';
    form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));

    await vi.waitFor(() =>
      expect(root.querySelector<HTMLButtonElement>('#register-submit')!.disabled).toBe(false),
    );
    expect(api.registerAdmin).toHaveBeenCalledTimes(1);

    vi.mocked(api.registerAdmin).mockResolvedValueOnce(makeAdmin());
    vi.mocked(api.loginAdmin).mockResolvedValue(makeToken());
    vi.mocked(api.getCurrentAdmin).mockResolvedValue(makeAdmin());
    form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));

    await vi.waitFor(() => expect(root.querySelector('#services-list')).not.toBeNull());
    expect(api.registerAdmin).toHaveBeenCalledTimes(2);
  });
});

describe('section tabs (Услуги / Заявки)', () => {
  async function renderPanel(root: HTMLElement): Promise<void> {
    saveToken('valid-token');
    vi.mocked(api.checkAuthStatus).mockResolvedValue(makeCheck());
    vi.mocked(api.getCurrentAdmin).mockResolvedValue(makeAdmin());
    renderAdmin(root);
    await vi.waitFor(() => expect(root.querySelector('#services-list')).not.toBeNull());
  }

  it('defaults to the Услуги tab, with the Заявки panel present but hidden', async () => {
    const root = document.createElement('div');
    await renderPanel(root);

    expect(root.querySelector('#admin-tab-services')!.getAttribute('aria-selected')).toBe('true');
    expect(root.querySelector('#admin-tab-applications')!.getAttribute('aria-selected')).toBe(
      'false',
    );
    expect(root.querySelector<HTMLElement>('#admin-panel-services')!.hidden).toBe(false);
    expect(root.querySelector<HTMLElement>('#admin-panel-applications')!.hidden).toBe(true);
    expect(api.getPrioritizedApplications).not.toHaveBeenCalled();
  });

  it('switching to Заявки shows that panel, hides Услуги, and lazily loads applications', async () => {
    const root = document.createElement('div');
    await renderPanel(root);

    root.querySelector<HTMLButtonElement>('#admin-tab-applications')!.click();

    expect(root.querySelector<HTMLElement>('#admin-panel-services')!.hidden).toBe(true);
    expect(root.querySelector<HTMLElement>('#admin-panel-applications')!.hidden).toBe(false);
    expect(root.querySelector('#admin-tab-applications')!.getAttribute('aria-selected')).toBe(
      'true',
    );
    await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenCalledWith(0, 100));
  });

  it('logout and username remain available regardless of the active tab', async () => {
    const root = document.createElement('div');
    await renderPanel(root);
    root.querySelector<HTMLButtonElement>('#admin-tab-applications')!.click();
    await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(1));

    expect(root.querySelector('#logout-button')).not.toBeNull();
    expect(root.textContent).toContain('admin');

    root.querySelector<HTMLButtonElement>('#logout-button')!.click();
    expect(root.querySelector('#login-form')).not.toBeNull();
  });

  it('switching back to Услуги does not affect the CRUD list already loaded there', async () => {
    vi.mocked(api.getAllServices).mockResolvedValue([
      {
        id: 1,
        service_name: 'Полировка',
        budget_min: '100',
        budget_max: '200',
        description: null,
        is_active: true,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
    ]);
    const root = document.createElement('div');
    await renderPanel(root);
    await vi.waitFor(() => expect(root.querySelector('.setting-card')).not.toBeNull());

    root.querySelector<HTMLButtonElement>('#admin-tab-applications')!.click();
    root.querySelector<HTMLButtonElement>('#admin-tab-services')!.click();

    expect(root.querySelector<HTMLElement>('#admin-panel-services')!.hidden).toBe(false);
    expect(root.querySelector('.setting-card')).not.toBeNull();
  });

  describe('a late applications response after switching tabs', () => {
    it('does not paint the hidden Заявки panel; Услуги stays active; reopening Заявки issues a fresh request with current data', async () => {
      const root = document.createElement('div');
      await renderPanel(root);

      const deferred = createDeferred<PrioritizedApplicationList>();
      vi.mocked(api.getPrioritizedApplications).mockReturnValueOnce(deferred.promise);

      root.querySelector<HTMLButtonElement>('#admin-tab-applications')!.click(); // 1. open Заявки
      await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(1)); // 2. pending

      root.querySelector<HTMLButtonElement>('#admin-tab-services')!.click(); // 3. switch to Услуги

      deferred.resolve(makeApplicationList([makeApplicationItem()])); // 4. old promise resolves successfully, late
      await flush();

      // 5. applications DOM must not get cards
      expect(
        root.querySelector<HTMLElement>('#admin-panel-applications')!.querySelector('.application-card'),
      ).toBeNull();
      // 6. services panel remains active
      expect(root.querySelector<HTMLElement>('#admin-panel-services')!.hidden).toBe(false);
      expect(root.querySelector<HTMLElement>('#admin-panel-applications')!.hidden).toBe(true);

      // 7. reopening Заявки starts a new request and shows current data
      const freshItem = makeApplicationItem();
      vi.mocked(api.getPrioritizedApplications).mockResolvedValueOnce(
        makeApplicationList([{ ...freshItem, application: { ...freshItem.application, first_name: 'Свежая' } }]),
      );
      root.querySelector<HTMLButtonElement>('#admin-tab-applications')!.click();
      await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(2));
      await vi.waitFor(() =>
        expect(
          root.querySelector<HTMLElement>('#admin-panel-applications')!.querySelector('.application-card'),
        ).not.toBeNull(),
      );
      expect(root.querySelector<HTMLElement>('#admin-panel-applications')!.textContent).toContain('Свежая');
    });

    it('a rejection arriving after switching away does not write an error state into the hidden Заявки panel', async () => {
      const root = document.createElement('div');
      await renderPanel(root);

      const deferred = createDeferred<PrioritizedApplicationList>();
      vi.mocked(api.getPrioritizedApplications).mockReturnValueOnce(deferred.promise);

      root.querySelector<HTMLButtonElement>('#admin-tab-applications')!.click();
      await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(1));

      root.querySelector<HTMLButtonElement>('#admin-tab-services')!.click();

      deferred.reject(new TypeError('Failed to fetch'));
      await flush();

      expect(root.querySelector<HTMLElement>('#admin-panel-applications')!.textContent).not.toContain(
        'Не удалось загрузить',
      );
    });
  });

  describe('tab keyboard navigation', () => {
    it('ArrowRight moves focus and selection to the next tab, cyclically', async () => {
      const root = document.createElement('div');
      document.body.appendChild(root);
      await renderPanel(root);

      const servicesTab = root.querySelector<HTMLButtonElement>('#admin-tab-services')!;
      const applicationsTab = root.querySelector<HTMLButtonElement>('#admin-tab-applications')!;
      servicesTab.focus();

      servicesTab.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }),
      );
      expect(document.activeElement).toBe(applicationsTab);
      expect(applicationsTab.getAttribute('aria-selected')).toBe('true');
      expect(servicesTab.getAttribute('aria-selected')).toBe('false');
      expect(root.querySelector<HTMLElement>('#admin-panel-applications')!.hidden).toBe(false);
      await vi.waitFor(() => expect(api.getPrioritizedApplications).toHaveBeenCalledTimes(1));

      applicationsTab.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }),
      );
      expect(document.activeElement).toBe(servicesTab); // cyclic wrap
      expect(servicesTab.getAttribute('aria-selected')).toBe('true');

      document.body.removeChild(root);
    });

    it('ArrowLeft wraps backwards, and Home/End jump to the first/last tab', async () => {
      const root = document.createElement('div');
      document.body.appendChild(root);
      await renderPanel(root);

      const servicesTab = root.querySelector<HTMLButtonElement>('#admin-tab-services')!;
      const applicationsTab = root.querySelector<HTMLButtonElement>('#admin-tab-applications')!;

      servicesTab.focus();
      servicesTab.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true, cancelable: true }),
      );
      expect(document.activeElement).toBe(applicationsTab); // cyclic wrap backwards
      expect(applicationsTab.getAttribute('aria-selected')).toBe('true');

      applicationsTab.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Home', bubbles: true, cancelable: true }),
      );
      expect(document.activeElement).toBe(servicesTab);
      expect(servicesTab.getAttribute('aria-selected')).toBe('true');

      servicesTab.dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true, cancelable: true }));
      expect(document.activeElement).toBe(applicationsTab);
      expect(applicationsTab.getAttribute('aria-selected')).toBe('true');

      document.body.removeChild(root);
    });

    it('tabindex follows the selected tab (roving tabindex)', async () => {
      const root = document.createElement('div');
      await renderPanel(root);

      const servicesTab = root.querySelector<HTMLButtonElement>('#admin-tab-services')!;
      const applicationsTab = root.querySelector<HTMLButtonElement>('#admin-tab-applications')!;

      expect(servicesTab.tabIndex).toBe(0);
      expect(applicationsTab.tabIndex).toBe(-1);

      applicationsTab.click();

      expect(applicationsTab.tabIndex).toBe(0);
      expect(servicesTab.tabIndex).toBe(-1);
    });

    it('does not intercept Enter/Space, so native button activation keeps working alongside the mouse', async () => {
      const root = document.createElement('div');
      await renderPanel(root);
      const applicationsTab = root.querySelector<HTMLButtonElement>('#admin-tab-applications')!;

      const enterEvent = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
      applicationsTab.dispatchEvent(enterEvent);
      expect(enterEvent.defaultPrevented).toBe(false);

      const spaceEvent = new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true });
      applicationsTab.dispatchEvent(spaceEvent);
      expect(spaceEvent.defaultPrevented).toBe(false);

      // Click is what a real browser fires when Enter/Space activate a <button>.
      applicationsTab.click();
      expect(applicationsTab.getAttribute('aria-selected')).toBe('true');
      expect(root.querySelector<HTMLElement>('#admin-panel-applications')!.hidden).toBe(false);
    });
  });
});
