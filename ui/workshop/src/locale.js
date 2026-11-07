/* Preference belongs to an authenticated activity/learner; each tab pins its URL. */
(() => {
  'use strict';
  const node = document.querySelector('[data-locale-context]');
  if (!node) return;
  const ctx = JSON.parse(node.dataset.localeContext);
  const match = value => ctx.locales.find(row => [row.tag, ...row.aliases].some(x => x.toLowerCase() === String(value).toLowerCase()))?.tag;
  const browserMatch = value => {
    while (value) { const tag = match(value); if (tag) return tag; value = value.includes('-') ? value.slice(0, value.lastIndexOf('-')) : ''; }
  };
  const current = new URL(location.href);
  const explicit = current.searchParams.has('locale');
  let stored, storageFailed = false;
  try { stored = localStorage.getItem(ctx.key); } catch (_) { storageFailed = true; }
  const selected = explicit ? match(current.searchParams.get('locale')) :
    match(stored) || navigator.languages.map(browserMatch).find(Boolean) || ctx.default;
  if (selected && (current.searchParams.get('locale') !== selected)) {
    current.searchParams.set('locale', selected);
    location.replace(current.href);
    return;
  }
  const form = document.querySelector('.language-form');
  if (!form) return;
  form.action = location.pathname + location.hash;
  // Keep non-locale routing parameters (e.g. module/section) on form submits.
  for (const [name, value] of current.searchParams) {
    if (name === 'locale') continue;
    const input = document.createElement('input');
    input.type = 'hidden'; input.name = name; input.value = value; form.append(input);
  }
  form.addEventListener('submit', event => {
    event.preventDefault();
    const locale = match(form.elements.locale.value);
    if (!locale) return;
    try { localStorage.setItem(ctx.key, locale); } catch (_) { storageFailed = true; }
    const destination = new URL(location.href);
    destination.searchParams.set('locale', locale);
    if (storageFailed) { try { sessionStorage.setItem(ctx.key + '.storage-error', '1'); } catch (_) {} }
    location.assign(destination.href);
  });
  try {
    if (sessionStorage.getItem(ctx.key + '.storage-error')) {
      const notice = document.createElement('p'); notice.role = 'status'; notice.textContent = ctx.storageError;
      form.after(notice); sessionStorage.removeItem(ctx.key + '.storage-error');
    }
  } catch (_) { /* URL selection remains usable when browser storage is disabled. */ }
})();
