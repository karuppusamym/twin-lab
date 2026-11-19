// Parcel depot: a small single-page application written by hand for the code-map tests.
// Synthetic data only. A tiny router of its own, shaped like Vue Router's route table:
// nested routes, guards (beforeEnter), a lazy chunk (admin.js) the recorded flow never opens,
// a tab set, a menu, a dialog opened by code, a translation file, a list of values, and API
// calls of which several are never made in the recording.

const CONFIG = { apiBase: '/api', analyticsKey: 'AKIAQX7ZPLANTED0KEY9', reports: 'https://reports.depot.corp.internal/export' };

export const PARCEL_STATUSES = [
  { value: 'received', label: 'Received' },
  { value: 'sorted', label: 'Sorted' },
  { value: 'out', label: 'Out for delivery' },
  { value: 'delivered', label: 'Delivered' },
];

let texts = {};
function t(key) {
  return key.split('.').reduce((o, k) => (o || {})[k], texts) || key;
}

const api = {
  post: (url, body) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  put: (url, body) => fetch(url, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  delete: (url) => fetch(url, { method: 'DELETE' }),
};

// --- guards -------------------------------------------------------------------------------------

async function requireSession() {
  const r = await fetch('/api/session');
  if (!r.ok) {
    return navigate('/sign-in');
  }
  return true;
}

async function requireAdmin() {
  const r = await fetch('/api/session');
  const who = r.ok ? await r.json() : {};
  if (!who.admin) {
    return navigate('/parcels');
  }
  return true;
}

// --- components ---------------------------------------------------------------------------------

const Shell = {
  render() {
    return `<header id="topbar"><span class="brand">Parcel depot</span>
      <nav id="mainNav">
        <a id="navParcels" href="/parcels" data-link>${t('nav.parcels')}</a>
        <a id="navDepots" href="/depots" data-link>Depots</a>
        <a id="navAdmin" href="/admin/users" data-link>Admin</a>
      </nav>
      <div role="menu" id="userMenu">
        <a role="menuitem" id="menuHelp" href="/help" data-link>Help</a>
        <button role="menuitem" id="menuSignOut" type="button">Sign out</button>
      </div></header>
      <main id="outlet"></main>`;
  },
  mounted() {
    document.getElementById('menuSignOut').addEventListener('click', async () => {
      await api.delete('/api/session');
      navigate('/sign-in');
    });
  },
};

const SignIn = {
  render() {
    return `<h1>Sign in</h1><form id="signInForm"><label for="username">User name</label>
      <input id="username" name="username"><label for="password">Password</label>
      <input id="password" name="password" type="password">
      <button id="signIn" type="submit">Sign in</button><p id="signInError" class="error"></p></form>`;
  },
  mounted() {
    document.getElementById('signInForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      const r = await api.post('/api/session', {
        username: document.getElementById('username').value,
        password: document.getElementById('password').value,
      });
      if (r.ok) {
        navigate('/parcels');
      } else {
        document.getElementById('signInError').textContent = 'Wrong user name or password.';
      }
    });
  },
};

const ParcelList = {
  render() {
    const options = PARCEL_STATUSES.map((s) => `<option value="${s.value}">${s.label}</option>`).join('');
    return `<h1>Parcels</h1>
      <label for="statusFilter">Status</label><select id="statusFilter"><option value="">All</option>${options}</select>
      <button id="newParcel" type="button">${t('parcel.new')}</button>
      <table id="parcels"><thead><tr><th>Parcel</th><th>Recipient</th><th>Depot</th><th>Status</th></tr></thead><tbody></tbody></table>
      <dialog id="newParcelDialog" aria-labelledby="newParcelTitle">
        <h2 id="newParcelTitle">New parcel</h2>
        <label for="recipient">Recipient</label><input id="recipient">
        <button id="saveParcel" type="button">Save</button>
        <button id="cancelParcel" type="button">Cancel</button>
      </dialog>`;
  },
  async mounted() {
    const status = document.getElementById('statusFilter').value;
    const r = await fetch('/api/parcels?page=' + 1 + '&status=' + status);
    const data = await r.json();
    document.querySelector('#parcels tbody').innerHTML = data.items.map((p) =>
      `<tr><td><a class="parcel-link" href="/parcels/${p.id}/overview" data-link>${p.id}</a></td>` +
      `<td>${p.recipient}</td><td>${p.depot}</td><td>${p.status}</td></tr>`).join('');
    document.getElementById('newParcel').addEventListener('click', () => {
      document.getElementById('newParcelDialog').showModal();
    });
    document.getElementById('cancelParcel').addEventListener('click', () => {
      document.getElementById('newParcelDialog').close();
    });
    document.getElementById('saveParcel').addEventListener('click', async () => {
      await api.post('/api/parcels', { recipient: document.getElementById('recipient').value });
      document.getElementById('newParcelDialog').close();
    });
  },
};

const ParcelPage = {
  render(params) {
    return `<h1>Parcel <span id="parcelId">${params.id}</span></h1>
      <div role="tablist" id="parcelTabs">
        <a role="tab" id="tabOverview" href="/parcels/${params.id}/overview" data-link>Overview</a>
        <a role="tab" id="tabHistory" href="/parcels/${params.id}/history" data-link>History</a>
      </div>
      <section role="tabpanel" id="parcelPanel"></section>
      <button id="deleteParcel" type="button">Delete</button>`;
  },
  mounted(params) {
    document.getElementById('deleteParcel').addEventListener('click', async () => {
      if (confirm('Delete this parcel?')) {
        await api.delete(`/api/parcels/${params.id}`);
        navigate('/parcels');
      }
    });
  },
};

const ParcelOverview = {
  async load(params) {
    const r = await fetch(`/api/parcels/${params.id}`);
    const p = await r.json();
    return `<dl id="overview"><dt>Recipient</dt><dd>${p.recipient}</dd><dt>Depot</dt><dd>${p.depot}</dd></dl>`;
  },
};

const ParcelHistory = {
  async load(params) {
    const r = await fetch('/api/parcels/' + params.id + '/history');
    const rows = await r.json();
    return `<ol id="history">${rows.items.map((h) => `<li>${h.status}</li>`).join('')}</ol>`;
  },
};

const DepotList = {
  render() {
    return `<h1>Depots</h1><table id="depots"><tbody></tbody></table>`;
  },
  async mounted() {
    const r = await fetch('/api/depots');
    const rows = await r.json();
    document.querySelector('#depots tbody').innerHTML = rows.items.map((d) => `<tr><td>${d.name}</td></tr>`).join('');
  },
};

const HelpPage = {
  render() {
    return `<h1>Help</h1><p id="helpText">Ask the depot desk.</p><a id="exportLink" href="#" >Export</a>`;
  },
  mounted() {
    document.getElementById('exportLink').addEventListener('click', () => fetch(CONFIG.reports));
  },
};

const NotFound = {
  render() {
    return `<h1>Not found</h1><a id="home" href="/parcels" data-link>Back to the parcels</a>`;
  },
};

// --- the route table ----------------------------------------------------------------------------

const routes = [
  { path: '/sign-in', component: SignIn, meta: { title: 'Sign in - Parcel depot' } },
  { path: '/', redirect: '/parcels' },
  { path: '/parcels', component: ParcelList, beforeEnter: requireSession, meta: { title: 'Parcels - Parcel depot' } },
  {
    path: '/parcels/:id',
    component: ParcelPage,
    beforeEnter: requireSession,
    meta: { title: 'Parcel - Parcel depot' },
    children: [
      { path: 'overview', component: ParcelOverview },
      { path: 'history', component: ParcelHistory },
    ],
  },
  { path: '/depots', component: DepotList, beforeEnter: requireSession, meta: { title: 'Depots - Parcel depot' } },
  {
    path: '/admin',
    component: () => import('./admin.js').then((m) => m.AdminPage),
    beforeEnter: requireAdmin,
    meta: { title: 'Administration - Parcel depot' },
    children: [
      { path: 'users', component: () => import('./admin.js').then((m) => m.AdminUsers) },
      { path: 'settings', component: () => import('./admin.js').then((m) => m.AdminSettings) },
    ],
  },
  { path: '/help', component: HelpPage, meta: { title: 'Help - Parcel depot' } },
  { path: '/:pathMatch(.*)*', component: NotFound },
];

// --- the router ---------------------------------------------------------------------------------

function match(list, segs, parent) {
  for (const r of list) {
    const own = r.path.split('/').filter(Boolean);
    if (r.path.startsWith('/:pathMatch')) {
      return { chain: [...parent, r], params: {} };
    }
    if (own.length > segs.length) continue;
    const params = {};
    let ok = true;
    own.forEach((p, i) => {
      if (p.startsWith(':')) params[p.slice(1)] = segs[i];
      else if (p !== segs[i]) ok = false;
    });
    if (!ok) continue;
    const rest = segs.slice(own.length);
    if (!rest.length) return { chain: [...parent, r], params };
    if (r.children) {
      const inner = match(r.children, rest, [...parent, r]);
      if (inner) return { chain: inner.chain, params: { ...params, ...inner.params } };
    }
  }
  return null;
}

async function resolveComponent(c) {
  return typeof c === 'function' ? await c() : c;
}

async function show(path) {
  const hit = match(routes, path.split('/').filter(Boolean), []);
  const route = hit.chain[0];
  if (route.redirect) return navigate(route.redirect);
  for (const r of hit.chain) {
    if (r.beforeEnter && (await r.beforeEnter()) !== true) return;
  }
  document.title = (hit.chain.find((r) => r.meta) || { meta: { title: 'Parcel depot' } }).meta.title;
  const outlet = document.getElementById('outlet');
  const top = await resolveComponent(route.component);
  outlet.innerHTML = top.render ? top.render(hit.params) : '';
  if (top.mounted) await top.mounted(hit.params);
  const child = hit.chain[1];
  if (child) {
    const inner = await resolveComponent(child.component);
    const panel = document.getElementById('parcelPanel') || outlet;
    panel.innerHTML = inner.load ? await inner.load(hit.params) : inner.render(hit.params);
  }
}

function navigate(path) {
  history.pushState({}, '', path);
  return show(path);
}

function createRouter(options) {
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a[data-link]');
    if (a) {
      e.preventDefault();
      navigate(a.getAttribute('href'));
    }
  });
  window.addEventListener('popstate', () => show(location.pathname));
  return options;
}

async function start() {
  const r = await fetch('/static/i18n/en.json');
  texts = await r.json();
  document.getElementById('app').innerHTML = Shell.render();
  Shell.mounted();
  createRouter({ routes });
  await show(location.pathname);
}

start();
