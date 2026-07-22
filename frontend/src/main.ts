import '@fontsource/golos-text/400.css';
import '@fontsource/golos-text/500.css';
import '@fontsource/golos-text/600.css';
import '@fontsource/golos-text/700.css';
import './style.css';

import { startRouter } from './router';
import { renderHome } from './pages/home';
import { renderAdmin } from './pages/admin';
import { renderNotFound } from './pages/notFound';

const appRoot = document.getElementById('app');

if (!appRoot) {
  throw new Error('Root element #app not found');
}

startRouter((route) => {
  appRoot.innerHTML = '';
  if (route === 'admin') {
    renderAdmin(appRoot);
  } else if (route === 'home') {
    renderHome(appRoot);
  } else {
    renderNotFound(appRoot);
  }
});
