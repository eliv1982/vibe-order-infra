/** Shown for backend/technical paths (/api/*, /docs, /openapi.json) and
 * any other unrecognized frontend path — see router.ts's 'not-found' route. */
export function renderNotFound(root: HTMLElement): void {
  root.innerHTML = `
    <div class="page container not-found">
      <h1>Страница не найдена</h1>
      <p>Похоже, такой страницы не существует.</p>
      <a class="btn btn-primary" href="/" data-link>На главную</a>
    </div>
  `;
}
