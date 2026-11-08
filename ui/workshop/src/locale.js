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
  const notebookFragments = () => {
    for (const link of document.querySelectorAll('[data-notebook-fragment]')) {
      const destination = new URL(link.href, location.href);
      destination.hash = location.hash;
      link.href = destination.href;
    }
  };
  notebookFragments();
  window.addEventListener('hashchange', notebookFragments);
  const form = document.querySelector('.language-form');
  if (!form) return;
  const select = form.elements.locale;
  form.action = location.pathname + location.hash;
  // Keep non-locale routing parameters (e.g. module/section) on form submits.
  for (const [name, value] of current.searchParams) {
    if (name === 'locale') continue;
    const input = document.createElement('input');
    input.type = 'hidden'; input.name = name; input.value = value; form.append(input);
  }
  const switchLocale = value => {
    const locale = match(value);
    if (!locale) return;
    try { localStorage.setItem(ctx.key, locale); } catch (_) { storageFailed = true; }
    const destination = new URL(location.href);
    destination.searchParams.set('locale', locale);
    if (storageFailed) { try { sessionStorage.setItem(ctx.key + '.storage-error', '1'); } catch (_) {} }
    if (destination.href !== location.href) location.assign(destination.href);
  };
  form.addEventListener('submit', event => {
    event.preventDefault();
    switchLocale(select.value);
  });
  select.addEventListener('change', () => switchLocale(select.value));

  // Keep the server-rendered GET form as a no-script fallback. The enhanced
  // picker owns both the closed control and its menu, including keyboard use.
  const label = form.querySelector('label');
  const picker = document.createElement('details'); picker.className = 'language-picker';
  const trigger = document.createElement('summary'); trigger.className = 'language-trigger';
  trigger.id = select.id;
  trigger.setAttribute('aria-haspopup', 'menu'); trigger.setAttribute('aria-expanded', 'false');
  trigger.setAttribute('aria-controls', 'workshop-language-menu');
  trigger.setAttribute('aria-label', label.textContent + ': ' + select.selectedOptions[0].textContent);
  const name = document.createElement('span'); name.textContent = select.selectedOptions[0].textContent;
  const arrow = document.createElement('span'); arrow.className = 'language-chevron'; arrow.setAttribute('aria-hidden', 'true');
  trigger.append(name, arrow);
  const menu = document.createElement('div'); menu.id = 'workshop-language-menu'; menu.className = 'language-menu';
  menu.setAttribute('role', 'menu'); menu.setAttribute('aria-labelledby', trigger.id);
  const items = Array.from(select.options, option => {
    const link = document.createElement('a'); link.dataset.locale = option.value; link.lang = option.value;
    link.setAttribute('role', 'menuitemradio'); link.setAttribute('aria-checked', String(option.selected)); link.tabIndex = -1;
    const text = document.createElement('span'); text.textContent = option.textContent;
    const check = document.createElement('span'); check.className = 'language-check'; check.textContent = '✓'; check.setAttribute('aria-hidden', 'true');
    link.append(text, check);
    link.addEventListener('click', event => {
      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault(); picker.open = false; select.value = option.value;
      switchLocale(option.value); trigger.focus();
    });
    return link;
  });
  const updateLinks = () => items.forEach(link => {
    const destination = new URL(location.href); destination.searchParams.set('locale', link.dataset.locale); link.href = destination.href;
  });
  updateLinks(); window.addEventListener('hashchange', updateLinks);
  menu.append(...items); picker.append(trigger, menu);
  select.id += '-native'; label.htmlFor = select.id;
  form.insertBefore(picker, select); select.hidden = true;
  form.querySelector('button[type="submit"]').hidden = true;
  const closePicker = () => { picker.open = false; trigger.setAttribute('aria-expanded', 'false'); };
  const openPicker = index => { picker.open = true; trigger.setAttribute('aria-expanded', 'true'); items[index].focus(); };
  picker.addEventListener('toggle', () => trigger.setAttribute('aria-expanded', String(picker.open)));
  trigger.addEventListener('keydown', event => {
    if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) {
      event.preventDefault();
      if (picker.open && ['Enter', ' '].includes(event.key)) closePicker();
      else openPicker(event.key === 'ArrowUp' ? items.length - 1 : Math.max(0, items.findIndex(link => link.getAttribute('aria-checked') === 'true')));
    } else if (event.key === 'Escape') { event.preventDefault(); closePicker(); }
  });
  menu.addEventListener('keydown', event => {
    const index = items.indexOf(document.activeElement);
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
      items[next].focus();
    } else if (event.key === 'Escape') { event.preventDefault(); closePicker(); trigger.focus(); }
    else if (event.key === ' ') { event.preventDefault(); document.activeElement.click(); }
    else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const next = items.find(link => link.firstElementChild.textContent.toLowerCase().startsWith(event.key.toLowerCase()));
      if (next) { event.preventDefault(); next.focus(); }
    }
  });
  document.addEventListener('click', event => { if (!picker.contains(event.target)) closePicker(); });
  document.addEventListener('focusin', event => { if (!picker.contains(event.target)) closePicker(); });
  try {
    if (sessionStorage.getItem(ctx.key + '.storage-error')) {
      const notice = document.createElement('p'); notice.role = 'status'; notice.textContent = ctx.storageError;
      form.after(notice); sessionStorage.removeItem(ctx.key + '.storage-error');
    }
  } catch (_) { /* URL selection remains usable when browser storage is disabled. */ }
})();
