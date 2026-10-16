import { newSalt, deriveVaultKey, encryptValue, decryptValue, generatedPassword, vaultHealth } from './vault.js';

const root = document.querySelector('#app');
const state = {
  setup: false, signedIn: false, tab: 'home', me: null, documents: [], expenses: [],
  reminders: [], reports: null, settings: null, draft: null, vaultKey: null, vaultItems: [], cloudFiles: {},
  message: '', error: '', busy: false
};
const tabs = [
  ['home', '⌂', 'Home'], ['ask', '✦', 'Ask / Add'], ['vault', '◇', 'Passwords'],
  ['documents', '▤', 'Documents'], ['expenses', '◉', 'Expenses'],
  ['calendar', '▦', 'Calendar'], ['reports', '▥', 'Reports'], ['settings', '⚙', 'Settings']
];

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, character =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
}

function money(minor, currency = 'INR') {
  try { return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(minor / 100); }
  catch { return `${currency} ${(minor / 100).toFixed(2)}`; }
}

function dateText(value) {
  if (!value) return 'Not set';
  return new Date(`${value}T12:00:00`).toLocaleDateString(undefined,
    { day: 'numeric', month: 'short', year: 'numeric' });
}

async function api(path, options = {}) {
  const headers = { 'X-HomeHub-Request': '1', ...options.headers };
  if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const response = await fetch(`/api${path}`, { credentials: 'include', ...options, headers });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try { detail = (await response.json()).detail || detail; } catch { /* use status */ }
    if (response.status === 401) { state.signedIn = false; state.vaultKey = null; render(); }
    throw new Error(detail);
  }
  const type = response.headers.get('content-type') || '';
  return type.includes('application/json') ? response.json() : response;
}

function showMessage(message, isError = false) {
  state.message = isError ? '' : message;
  state.error = isError ? message : '';
  render();
}

async function refresh() {
  const [me, documents, expenses, reminders, reports, settings] = await Promise.all([
    api('/me'), api('/documents'), api('/expenses'), api('/reminders'), api('/reports'), api('/settings')
  ]);
  Object.assign(state, { me, documents, expenses, reminders, reports, settings, signedIn: true });
  if (state.vaultKey) await loadVault();
  render();
}

async function loadVault() {
  const encrypted = await api('/vault/items');
  state.vaultItems = await Promise.all(encrypted.map(async row =>
    ({ id: row.id, ...await decryptValue(state.vaultKey, row.ciphertext) })));
}

function authView() {
  const first = !state.setup;
  return `<main class="auth-page"><section class="auth-card">
    <div class="brand brand-large"><span class="brand-mark">H</span><span>HomeHub</span></div>
    <p class="eyebrow">YOUR PRIVATE HOME SPACE</p>
    <h1>${first ? 'Set up your home hub' : 'Welcome back'}</h1>
    <p class="muted">${first ? 'Create the first account on the host machine.' : 'Sign in to your local hub.'}</p>
    <form id="auth-form" class="stack">
      ${first ? '<label>Host setup code<input name="setup_token" type="text" autocomplete="off" required /></label>' : ''}
      <label>Email<input name="email" type="email" autocomplete="username" required /></label>
      <label>Account password<input name="password" type="password" minlength="12" autocomplete="${first ? 'new-password' : 'current-password'}" required /></label>
      <button class="button primary" type="submit">${first ? 'Create account' : 'Sign in'}</button>
    </form><p class="fineprint">This account password signs you in. The password vault has a separate master password.</p>
  </section></main>`;
}

function shell(content) {
  return `<div class="layout"><aside class="sidebar">
    <div class="brand"><span class="brand-mark">H</span><span>HomeHub</span></div>
    <div class="side-caption">WORKSPACE</div><nav class="nav">
    ${tabs.map(([key, icon, title]) => `<button data-tab="${key}" class="nav-link ${state.tab === key ? 'active' : ''}"><span>${icon}</span>${title}</button>`).join('')}
    </nav><div class="sidebar-foot"><div class="online-dot"></div><span>Local workspace</span></div>
    </aside><div class="main-column"><header class="topbar"><div class="mobile-brand">HomeHub</div>
    <span class="topbar-address">${escapeHtml(state.me?.email || '')}</span>
    <button class="text-button" data-action="logout">Sign out</button></header>
    <main class="content">${state.message ? `<div class="notice success">${escapeHtml(state.message)}</div>` : ''}
    ${state.error ? `<div class="notice error">${escapeHtml(state.error)}</div>` : ''}${content}</main></div>
    <nav class="mobile-nav">${tabs.slice(0, 6).map(([key, icon, title]) =>
      `<button data-tab="${key}" class="${state.tab === key ? 'active' : ''}"><span>${icon}</span><small>${title.split(' ')[0]}</small></button>`).join('')}</nav></div>`;
}

function card(title, body, extra = '') {
  return `<section class="card"><div class="card-head"><h2>${title}</h2>${extra}</div>${body}</section>`;
}

function homeView() {
  const spend = state.expenses.filter(item => item.spent_on?.startsWith(new Date().toISOString().slice(0, 7)))
    .reduce((sum, item) => sum + item.amount_minor, 0);
  const next = state.reminders.filter(item => item.event_date >= new Date().toISOString().slice(0, 10)).slice(0, 4);
  return `<div class="hero"><div><p class="eyebrow">YOUR PERSONAL DASHBOARD</p><h1>Everything in one place.</h1>
    <p>Manage the important details of everyday life from your own network.</p></div>
    <button class="button light" data-tab="ask">✦ Ask or add something</button></div>
    <div class="metric-grid">
      <div class="metric"><span>Spent this month</span><strong>${money(spend)}</strong><small>Across ${state.expenses.length} saved expenses</small></div>
      <div class="metric"><span>Documents</span><strong>${state.documents.length}</strong><small>${state.documents.filter(d => d.expiry_date).length} with expiry dates</small></div>
      <div class="metric"><span>Upcoming reminders</span><strong>${next.length}</strong><small>From documents and manual entries</small></div>
      <div class="metric"><span>Vault items</span><strong>${state.reports?.vault_count || 0}</strong><small>Encrypted on your devices</small></div>
    </div><div class="two-column">
      ${card('Coming up', next.length ? `<div class="item-list">${next.map(r => `<div class="list-row"><div><strong>${escapeHtml(r.title)}</strong><small>${dateText(r.event_date)}</small></div><span class="pill">Reminder</span></div>`).join('')}</div>` : '<p class="empty">No upcoming reminders. Add one from the Calendar tab.</p>', '<button class="text-button" data-tab="calendar">View all →</button>')}
      ${card('Quick actions', `<div class="quick-grid"><button data-tab="expenses">＋ Add expense</button><button data-tab="documents">↑ Upload document</button><button data-tab="vault">◇ Save login</button><button data-tab="calendar">▦ Set reminder</button></div>`)}
    </div>`;
}

function askView() {
  const draft = state.draft;
  return `<div class="page-title"><p class="eyebrow">UNIFIED INTERFACE</p><h1>Ask or add anything</h1>
    <p>Describe an expense, reminder, document, password entry, or report. Review the result before saving.</p></div>
    ${card('What would you like to do?', `<form id="prompt-form" class="prompt-form"><textarea name="text" rows="3" placeholder="Paid ₹850 for petrol today by UPI..." required></textarea><div class="prompt-bottom"><span>Processed by your local Ollama model when configured</span><button class="button primary" type="submit">Interpret request →</button></div></form>
      <div class="example-prompts"><button data-example="Paid ₹850 for petrol today by UPI">Petrol expense</button><button data-example="Remind me to renew my PUC on 2027-02-15">PUC reminder</button><button data-example="Show my expense report">Expense report</button></div>`)}
    ${draft ? card('Review proposed action', `<div class="draft-kind">${escapeHtml(draft.kind)}</div>
      <dl class="draft-list">${Object.entries(draft).filter(([k, v]) => k !== 'kind' && v !== '' && v !== null).map(([k, v]) => `<div><dt>${escapeHtml(k.replaceAll('_', ' '))}</dt><dd>${escapeHtml(v)}</dd></div>`).join('')}</dl>
      <div class="actions"><button class="button primary" data-action="use-draft">Continue in ${escapeHtml(draft.kind)} tab</button><button class="button ghost" data-action="clear-draft">Discard</button></div>`): ''}`;
}

function vaultView() {
  if (!state.me?.vault_salt) return `<div class="page-title"><p class="eyebrow">ENCRYPTED VAULT</p><h1>Create your password vault</h1><p>Choose a strong master password. It cannot be recovered by the server.</p></div>
    ${card('Set master password', `<form id="vault-create-form" class="stack"><label>Master password<input type="password" name="master" minlength="14" required autocomplete="new-password" /></label><label>Confirm master password<input type="password" name="confirm" minlength="14" required autocomplete="new-password" /></label><button class="button primary">Create vault</button></form>`)}`;
  if (!state.vaultKey) return `<div class="page-title"><p class="eyebrow">ENCRYPTED VAULT</p><h1>Unlock your vault</h1><p>Decryption happens in this browser. The host stores encrypted records.</p></div>
    ${card('Master password', `<form id="vault-unlock-form" class="inline-form"><input name="master" type="password" placeholder="Enter master password" required autofocus /><button class="button primary">Unlock</button></form>`)}`;
  const health = vaultHealth(state.vaultItems);
  return `<div class="page-title row-between"><div><p class="eyebrow">ENCRYPTED VAULT</p><h1>Passwords</h1><p>Your private logins, available while this vault is unlocked.</p></div><button class="button ghost" data-action="lock-vault">Lock vault</button></div>
    <div class="metric-grid compact"><div class="metric"><span>Saved logins</span><strong>${health.total}</strong></div><div class="metric"><span>Reused passwords</span><strong>${health.reused}</strong></div><div class="metric"><span>Short passwords</span><strong>${health.short}</strong></div></div>
    <div class="two-column wide-left">${card('Saved logins', state.vaultItems.length ? `<div class="item-list">${state.vaultItems.map(item => `<div class="list-row"><div><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.username || item.url || '')}</small></div><button class="text-button" data-reveal="${item.id}">Open</button></div>`).join('')}</div>` : '<p class="empty">No logins yet.</p>')}
    ${card('Add login', `<form id="vault-item-form" class="stack"><label>Title<input name="title" required placeholder="Electricity account" /></label><label>Website<input name="url" type="url" placeholder="https://..." /></label><label>Username<input name="username" autocomplete="off" /></label><label>Password<div class="input-row"><input name="password" type="password" autocomplete="new-password" required /><button type="button" class="button ghost" data-action="generate-password">Generate</button></div></label><label>Notes<textarea name="notes" rows="2"></textarea></label><button class="button primary">Save encrypted login</button></form>`)}</div>`;
}

function documentsView() {
  return `<div class="page-title"><p class="eyebrow">DOCUMENT LIBRARY</p><h1>Documents</h1><p>Upload once, review dates, and sync to your configured drives.</p></div>
    ${card('Upload a document or bill', `<form id="document-form" class="inline-form"><input name="file" type="file" accept=".pdf,image/jpeg,image/png,image/webp" required /><select name="category"><option>General</option><option>Identity</option><option>Vehicle</option><option>Insurance</option><option>Receipts</option><option>Other</option></select><button class="button primary">Upload</button></form>`)}
    ${card('Cloud folders', `<div class="actions"><button class="button ghost" data-action="browse-cloud" data-provider="google">Browse Google Drive</button><button class="button ghost" data-action="browse-cloud" data-provider="onedrive">Browse OneDrive</button></div>
      ${Object.entries(state.cloudFiles).map(([provider, files]) => `<h3> ${provider === 'google' ? 'Google Drive' : 'OneDrive'} · HomeHub folder</h3>${files.length ? `<div class="item-list">${files.map(file => `<div class="list-row"><div><strong>${escapeHtml(file.name)}</strong><small>${escapeHtml(file.path)} · ${Math.round(file.size / 1024)} KB</small></div><button class="text-button" data-action="open-cloud-file" data-provider="${provider}" data-path="${escapeHtml(file.path)}">Open</button></div>`).join('')}</div>` : '<p class="empty">This folder is empty.</p>'}`).join('')}`)}
    ${card('Your files', state.documents.length ? `<div class="item-list">${state.documents.map(doc => `<div class="document-row"><div class="doc-icon">▤</div><div class="doc-info"><strong>${escapeHtml(doc.name)}</strong><small>${escapeHtml(doc.category)} · ${dateText(doc.created_at.slice(0, 10))} · ${escapeHtml(doc.processing_status)}</small><small>${Object.entries(doc.cloud || {}).map(([p, x]) => `${p}: ${x.status}`).join(' · ') || 'Local encrypted copy'}</small></div><div class="doc-actions"><button class="text-button" data-action="open-document" data-id="${doc.id}">Open</button>${doc.processing_status === 'error' ? `<button class="text-button" data-action="retry-document" data-id="${doc.id}">Retry</button>` : ''}</div>
      <form class="review-form" data-document="${doc.id}"><label>Type<input name="category" value="${escapeHtml(doc.category)}" /></label><label>Issue date<input name="issue_date" type="date" value="${escapeHtml(doc.issue_date || doc.extracted?.issue_date || '')}" /></label><label>Expiry date<input name="expiry_date" type="date" value="${escapeHtml(doc.expiry_date || doc.extracted?.expiry_date || '')}" /></label><label>Remind days before<input name="lead" type="number" min="0" max="3650" value="30" /></label><button class="button small">Confirm dates</button><small class="evidence">${escapeHtml(doc.extracted?.evidence || '')}</small></form></div>`).join('')}</div>` : '<p class="empty">No documents uploaded yet.</p>')}`;
}

function expensesView() {
  return `<div class="page-title"><p class="eyebrow">SPENDING JOURNAL</p><h1>Expenses</h1><p>Add an expense and its bill in a single form.</p></div>
    <div class="two-column wide-left">${card('Recent expenses', state.expenses.length ? `<div class="item-list">${state.expenses.map(item => `<div class="list-row"><div><strong>${escapeHtml(item.merchant || item.category)}</strong><small>${dateText(item.spent_on)} · ${escapeHtml(item.category)} · ${escapeHtml(item.payment_method)}</small></div><div class="right-align"><strong>${money(item.amount_minor, item.currency)}</strong>${item.receipt_document_id ? `<button class="text-button" data-action="open-document" data-id="${item.receipt_document_id}">Bill</button>` : ''}</div></div>`).join('')}</div>` : '<p class="empty">No expenses yet.</p>')}
    ${card('Add expense', `<form id="expense-form" class="stack"><div class="form-grid"><label>Amount<input name="amount" type="number" min="0.01" step="0.01" required /></label><label>Currency<input name="currency" value="INR" maxlength="3" required /></label></div><label>Merchant<input name="merchant" placeholder="Fuel station" /></label><div class="form-grid"><label>Category<select name="category"><option>Food</option><option>Groceries</option><option>Transport</option><option>Fuel</option><option>Bills</option><option>Health</option><option>Shopping</option><option>Travel</option><option>Other</option></select></label><label>Date<input name="spent_on" type="date" value="${new Date().toISOString().slice(0, 10)}" required /></label></div><label>Payment method<input name="payment_method" placeholder="UPI, card, cash..." /></label><label>Bill or receipt <span class="muted">(optional)</span><input name="bill" type="file" accept=".pdf,image/jpeg,image/png,image/webp" /></label><label>Note<textarea name="note" rows="2"></textarea></label><button class="button primary">Save expense</button></form>`)}</div>
    ${card('Monthly category budget', `<form id="budget-form" class="inline-form"><select name="category"><option>Food</option><option>Groceries</option><option>Transport</option><option>Fuel</option><option>Bills</option><option>Health</option><option>Shopping</option><option>Travel</option><option>Other</option></select><input name="monthly_limit" type="number" min="0.01" step="0.01" placeholder="Monthly limit" required /><input name="currency" value="INR" maxlength="3" /><button class="button">Set budget</button></form><p class="fineprint">Budget progress appears in Reports.</p>`)}`;
}

function calendarView() {
  return `<div class="page-title"><p class="eyebrow">DATES THAT MATTER</p><h1>Calendar & reminders</h1><p>Track upcoming dates, add a device calendar event, and send email reminders from the host.</p></div>
    ${card('Add reminder', `<form id="reminder-form" class="inline-form"><input name="title" placeholder="Renew vehicle PUC" required /><input name="event_date" type="date" required /><label class="mini-label">Days before<input name="lead_days" type="number" min="0" max="3650" value="30" /></label><label class="checkbox"><input name="email_enabled" type="checkbox" checked /> Email</label><button class="button primary">Save reminder</button></form>`)}
    ${card('Scheduled dates', state.reminders.length ? `<div class="item-list">${state.reminders.map(item => `<div class="list-row"><div><strong>${escapeHtml(item.title)}</strong><small>Event ${dateText(item.event_date)} · Email ${dateText(item.send_on)}${item.sent_at ? ' · Sent' : item.last_error ? ' · Delivery failed' : ''}</small></div><div class="actions"><button class="text-button" data-action="calendar-file" data-id="${item.id}">Add to device calendar</button><button class="text-button danger" data-action="delete-reminder" data-id="${item.id}">Delete</button></div></div>`).join('')}</div>` : '<p class="empty">No reminders scheduled.</p>')}`;
}

function reportsView() {
  const report = state.reports || {};
  const byCategory = report.expense_by_category || [];
  const maximum = Math.max(1, ...byCategory.map(x => x.total_minor));
  return `<div class="page-title row-between"><div><p class="eyebrow">INSIGHTS</p><h1>Reports</h1><p>Clear views for spending, documents, reminders, and vault health.</p></div><a class="button ghost" href="/api/expenses/export.csv">Export expenses CSV</a></div>
    <div class="two-column">${card('Expenses by category', byCategory.length ? `<div class="bars">${byCategory.map(item => `<div class="bar-row"><div><strong>${escapeHtml(item.category)}</strong><span>${money(item.total_minor, item.currency)}</span></div><div class="bar-track"><div style="width:${Math.round(item.total_minor / maximum * 100)}%"></div></div></div>`).join('')}</div>` : '<p class="empty">Add expenses to see trends.</p>')}
    ${card('Monthly spending', (report.expense_by_month || []).length ? `<div class="item-list">${report.expense_by_month.map(item => `<div class="list-row"><strong>${escapeHtml(item.month)}</strong><strong>${money(item.total_minor, item.currency)}</strong></div>`).join('')}</div>` : '<p class="empty">No monthly data yet.</p>')}</div>
    <div class="two-column">${card('Monthly budgets', (report.budgets || []).length ? `<div class="bars">${report.budgets.map(item => `<div class="bar-row"><div><strong>${escapeHtml(item.category)}</strong><span>${money(item.spent_minor, item.currency)} / ${money(item.monthly_limit_minor, item.currency)}</span></div><div class="bar-track"><div style="width:${Math.min(100, Math.round(item.spent_minor / item.monthly_limit_minor * 100))}%"></div></div></div>`).join('')}</div>` : '<p class="empty">Set a category budget in Expenses.</p>')}
    ${card('Top merchants', (report.expense_by_merchant || []).length ? `<div class="item-list">${report.expense_by_merchant.map(item => `<div class="list-row"><strong>${escapeHtml(item.merchant)}</strong><span>${money(item.total_minor, item.currency)}</span></div>`).join('')}</div>` : '<p class="empty">Merchant totals will appear here.</p>')}</div>
    <div class="two-column">${card('Document expiry', (report.documents_due || []).length ? `<div class="item-list">${report.documents_due.map(item => `<div class="list-row"><strong>${escapeHtml(item.name)}</strong><span>${dateText(item.expiry_date)}</span></div>`).join('')}</div><p class="fineprint">${report.documents_missing_expiry || 0} documents need expiry review.</p>` : `<p class="empty">No confirmed expiry dates yet.</p><p class="fineprint">${report.documents_missing_expiry || 0} documents need expiry review.</p>`)}
    ${card('Reminders & vault', `<div class="report-stats"><div><strong>${report.reminders?.total || 0}</strong><span>Total reminders</span></div><div><strong>${report.reminders?.overdue || 0}</strong><span>Past event dates</span></div><div><strong>${report.reminders?.failed || 0}</strong><span>Email failures</span></div><div><strong>${report.vault_count || 0}</strong><span>Vault entries</span></div></div><p class="fineprint">Detailed password health is calculated only while your vault is unlocked.</p>`)}</div>`;
}

function settingsView() {
  const s = state.settings || {};
  return `<div class="page-title"><p class="eyebrow">CONFIGURATION</p><h1>Settings</h1><p>Connection status for the services running on your host.</p></div>
    <div class="two-column">${card('Local intelligence', `<div class="setting-row"><span>Ollama model</span><strong>${escapeHtml(s.ollama_model || 'Not configured')}</strong></div><div class="setting-row"><span>Ollama service</span><strong>${s.ollama_online ? 'Online' : 'Unavailable'}</strong></div><p class="fineprint">Set HOMEHUB_OLLAMA_MODEL on the host. Prompts never go to a cloud AI provider.</p>`)}
    ${card('Connected services', `<div class="setting-row"><span>Google Drive</span><strong>${s.cloud?.google?.configured ? 'Configured' : 'Not configured'}</strong></div><div class="setting-row"><span>OneDrive</span><strong>${s.cloud?.onedrive?.configured ? 'Configured' : 'Not configured'}</strong></div><div class="setting-row"><span>rclone</span><strong>${s.rclone_installed ? 'Installed' : 'Not installed'}</strong></div><div class="setting-row"><span>Reminder email</span><strong>${s.smtp_configured ? 'Configured' : 'Not configured'}</strong></div><div class="setting-row"><span>Email schedule</span><strong>${escapeHtml(s.reminder_hour)}:00 ${escapeHtml(s.timezone || '')}</strong></div>`)}</div>
    ${card('Deployment', `<p>This app runs on one host and is reached from other devices on the same network. Configure a trusted HTTPS certificate and enter the host address on each device.</p><p class="fineprint">Cloud OAuth is configured on the host through rclone. Secrets are kept in the host's .env and rclone configuration, not in this page.</p>`)}`;
}

function render() {
  if (!state.signedIn) { root.innerHTML = authView(); return; }
  const views = { home: homeView, ask: askView, vault: vaultView, documents: documentsView,
    expenses: expensesView, calendar: calendarView, reports: reportsView, settings: settingsView };
  root.innerHTML = shell((views[state.tab] || homeView)());
}

function formValue(form, name) { return form.elements.namedItem(name)?.value?.trim() || ''; }

async function handleSubmit(event) {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  event.preventDefault();
  state.error = ''; state.message = '';
  const submit = form.querySelector('button[type=submit],button:not([type])');
  if (submit) submit.disabled = true;
  try {
    if (form.id === 'auth-form') {
      await api(state.setup ? '/login' : '/setup', { method: 'POST', body: JSON.stringify({ email: formValue(form, 'email'), password: formValue(form, 'password'),
        ...(state.setup ? {} : { setup_token: formValue(form, 'setup_token') }) }) });
      state.setup = true; await refresh(); return;
    }
    if (form.id === 'prompt-form') {
      state.draft = await api('/prompt', { method: 'POST', body: JSON.stringify({ text: formValue(form, 'text') }) });
      showMessage('Review the proposed action below.'); return;
    }
    if (form.id === 'vault-create-form') {
      if (formValue(form, 'master') !== formValue(form, 'confirm')) throw new Error('Master passwords do not match');
      const salt = newSalt(); const key = await deriveVaultKey(formValue(form, 'master'), salt);
      const check = await encryptValue(key, { check: 'homehub-vault-v1' });
      await api('/vault/config', { method: 'POST', body: JSON.stringify({ salt, check }) });
      state.vaultKey = key; state.me.vault_salt = salt; state.me.vault_check = check;
      showMessage('Vault created. Keep your master password safe.'); return;
    }
    if (form.id === 'vault-unlock-form') {
      const key = await deriveVaultKey(formValue(form, 'master'), state.me.vault_salt);
      try { const value = await decryptValue(key, state.me.vault_check); if (value.check !== 'homehub-vault-v1') throw new Error(); }
      catch { throw new Error('Incorrect master password'); }
      state.vaultKey = key; await loadVault(); showMessage('Vault unlocked.'); return;
    }
    if (form.id === 'vault-item-form') {
      const item = Object.fromEntries(['title', 'url', 'username', 'password', 'notes'].map(name => [name, formValue(form, name)]));
      await api('/vault/items', { method: 'POST', body: JSON.stringify({ ciphertext: await encryptValue(state.vaultKey, item) }) });
      await loadVault(); showMessage('Login saved and encrypted.'); return;
    }
    if (form.id === 'document-form') {
      const data = new FormData(form);
      await api('/documents', { method: 'POST', body: data });
      await refresh(); showMessage('Document uploaded. Extraction and cloud sync are processing.'); return;
    }
    if (form.classList.contains('review-form')) {
      await api(`/documents/${form.dataset.document}/review`, { method: 'POST', body: JSON.stringify({
        category: formValue(form, 'category'), issue_date: formValue(form, 'issue_date') || null,
        expiry_date: formValue(form, 'expiry_date') || null, remind_days_before: Number(formValue(form, 'lead'))
      }) });
      await refresh(); showMessage('Document dates confirmed and reminder updated.'); return;
    }
    if (form.id === 'expense-form') {
      let receipt_document_id = null;
      const bill = form.elements.namedItem('bill')?.files?.[0];
      if (bill) { const data = new FormData(); data.append('file', bill); data.append('category', 'Receipts');
        receipt_document_id = (await api('/documents', { method: 'POST', body: data })).id; }
      await api('/expenses', { method: 'POST', body: JSON.stringify({ amount: formValue(form, 'amount'),
        currency: formValue(form, 'currency'), category: formValue(form, 'category'), merchant: formValue(form, 'merchant'),
        spent_on: formValue(form, 'spent_on'), payment_method: formValue(form, 'payment_method'),
        note: formValue(form, 'note'), receipt_document_id }) });
      await refresh(); showMessage('Expense saved.'); return;
    }
    if (form.id === 'budget-form') {
      await api('/budgets', { method: 'POST', body: JSON.stringify({ category: formValue(form, 'category'),
        monthly_limit: formValue(form, 'monthly_limit'), currency: formValue(form, 'currency') }) });
      await refresh(); showMessage('Monthly budget saved.'); return;
    }
    if (form.id === 'reminder-form') {
      await api('/reminders', { method: 'POST', body: JSON.stringify({ title: formValue(form, 'title'),
        event_date: formValue(form, 'event_date'), lead_days: Number(formValue(form, 'lead_days')),
        email_enabled: form.elements.namedItem('email_enabled').checked }) });
      await refresh(); showMessage('Reminder scheduled.'); return;
    }
  } catch (error) { showMessage(error.message, true); }
  finally { if (submit) submit.disabled = false; }
}

async function handleClick(event) {
  const button = event.target.closest('button');
  if (!button) return;
  if (button.dataset.tab) { state.tab = button.dataset.tab; state.error = ''; state.message = ''; render(); return; }
  if (button.dataset.example) { state.tab = 'ask'; render(); document.querySelector('#prompt-form textarea').value = button.dataset.example; return; }
  const action = button.dataset.action;
  try {
    if (action === 'logout') { await api('/logout', { method: 'POST' }); state.signedIn = false; state.vaultKey = null; render(); }
    if (action === 'lock-vault') { state.vaultKey = null; state.vaultItems = []; render(); }
    if (action === 'generate-password') { const input = document.querySelector('#vault-item-form [name=password]'); input.value = generatedPassword(); input.type = 'text'; }
    if (action === 'clear-draft') { state.draft = null; render(); }
    if (action === 'use-draft') {
      const draft = state.draft;
      state.tab = ({ expense: 'expenses', reminder: 'calendar', document: 'documents', vault: 'vault', report: 'reports' })[draft.kind] || 'ask';
      render();
      if (draft.kind === 'expense') { const form = document.querySelector('#expense-form');
        for (const [field, value] of Object.entries({ amount: draft.amount, currency: draft.currency, category: draft.category,
          merchant: draft.merchant, spent_on: draft.date, payment_method: draft.payment_method, note: draft.note })) {
          if (form?.elements.namedItem(field) && value) form.elements.namedItem(field).value = value;
        } }
      if (draft.kind === 'reminder') { const form = document.querySelector('#reminder-form');
        if (form) { form.elements.namedItem('title').value = draft.title; form.elements.namedItem('event_date').value = draft.date || ''; } }
    }
    if (action === 'open-document') { window.open(`/api/documents/${button.dataset.id}/file`, '_blank', 'noopener'); }
    if (action === 'browse-cloud') {
      state.cloudFiles[button.dataset.provider] = await api(`/cloud/${button.dataset.provider}/files`);
      render();
    }
    if (action === 'open-cloud-file') {
      window.open(`/api/cloud/${button.dataset.provider}/file?path=${encodeURIComponent(button.dataset.path)}`, '_blank', 'noopener');
    }
    if (action === 'retry-document') { await api(`/documents/${button.dataset.id}/retry`, { method: 'POST' }); await refresh(); showMessage('Processing queued again.'); }
    if (action === 'calendar-file') { window.location.href = `/api/reminders/${button.dataset.id}/calendar.ics`; }
    if (action === 'delete-reminder') { if (!confirm('Delete this reminder?')) return;
      await api(`/reminders/${button.dataset.id}`, { method: 'DELETE' }); await refresh(); showMessage('Reminder deleted.'); }
    if (button.dataset.reveal) {
      const item = state.vaultItems.find(x => x.id === button.dataset.reveal);
      if (!item) return;
      const content = `Website: ${item.url || '—'}\nUsername: ${item.username || '—'}\nPassword: ${item.password || '—'}\nNotes: ${item.notes || '—'}`;
      alert(content);
    }
  } catch (error) { showMessage(error.message, true); }
}

root.addEventListener('submit', handleSubmit);
root.addEventListener('click', handleClick);

async function start() {
  try { const status = await api('/status'); state.setup = status.setup;
    if (status.setup) { try { await refresh(); } catch { render(); } } else render(); }
  catch (error) { root.innerHTML = `<main class="auth-page"><div class="auth-card"><h1>Cannot reach HomeHub</h1><p>${escapeHtml(error.message)}</p></div></main>`; }
}

start();
if ('serviceWorker' in navigator && window.isSecureContext) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}
