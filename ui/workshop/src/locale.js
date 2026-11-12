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
      switchLocale(option.value); trigger.focus({preventScroll:true});
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

/* Multi-course navigation enhances the rendered outline without replacing any
 * course links, progress forms, working copies or learning records. */
(() => {
  'use strict';
  const main = document.querySelector('main.main');
  const courses = main ? Array.from(main.children).filter(node => node.matches('.course-outline')) : [];
  if (courses.length < 2) return;
  const headings = courses.map(course => course.querySelector(':scope > .section-heading h2'));
  if (headings.some(heading => !heading)) return;

  const anchor = (node, preferred) => {
    if (!node.id) {
      let id = preferred, suffix = 1;
      while (document.getElementById(id)) id = preferred + '-' + suffix++;
      node.id = id;
    }
    return node.id;
  };
  const label = main.querySelector('h1').textContent;
  const shell = document.createElement('div'); shell.className = 'course-navigation-layout';
  const sidebar = document.createElement('nav'); sidebar.className = 'course-sidebar';
  sidebar.setAttribute('aria-label', label);
  const caption = document.createElement('p'); caption.className = 'course-sidebar-caption'; caption.textContent = label;
  const list = document.createElement('ol'); list.className = 'course-switcher';
  const levelList = document.createElement('div'); levelList.className = 'course-levels';
  const content = document.createElement('div'); content.className = 'course-navigation-content';
  const links = [], groups = [];
  const makeLink = (title, target, className) => {
    const link = document.createElement('a'); link.className = className;
    link.textContent = title; link.setAttribute('href', '#' + target.id);
    return link;
  };
  courses[0].before(shell);
  shell.append(sidebar, content); sidebar.append(caption, list, levelList);
  courses.forEach((course, index) => {
    // Order is frozen within an installed activity and identical across locales.
    const id = anchor(course, 'workshop-course-' + (index + 1));
    const heading = headings[index]; heading.tabIndex = -1;
    const link = makeLink(heading.textContent, course, 'course-switcher-link');
    const item = document.createElement('li'); item.append(link); list.append(item); links.push(link);
    const group = document.createElement('div'); group.className = 'course-level-group'; group.hidden = index !== 0;
    group.setAttribute('aria-label', heading.textContent);
    for (const [number, level] of Array.from(course.querySelectorAll(':scope > .level-block')).entries()) {
      const title = level.querySelector('.section-heading h2');
      if (!title) continue;
      anchor(level, id + '-level-' + (number + 1)); title.tabIndex = -1;
      const levelLabel = level.querySelector('.section-heading .muted');
      group.append(makeLink((levelLabel ? levelLabel.textContent + ' · ' : '') + title.textContent, level, 'course-level-link'));
    }
    groups.push(group); levelList.append(group); content.append(course);
  });
  document.body.classList.add('course-navigation-page');

  // Keep the original transfer controls intact, within reach at the page head.
  const record = main.querySelector(':scope > .progress-panel');
  const pageHead = main.querySelector('.page-head');
  if (record && pageHead && record.querySelector('h2')) {
    const tools = document.createElement('details'); tools.className = 'course-progress-tools';
    const summary = document.createElement('summary'); summary.textContent = record.querySelector('h2').textContent;
    tools.append(summary, record); pageHead.append(tools);
    document.addEventListener('click', event => { if (!tools.contains(event.target)) tools.open = false; });
    tools.addEventListener('keydown', event => { if (event.key === 'Escape') { tools.open = false; summary.focus(); } });
  }

  let active = -1, pending = false;
  const current = index => {
    if (active === index) return;
    active = index;
    links.forEach((link, i) => {
      if (i === index) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
      groups[i].hidden = i !== index;
    });
    // Locale links already preserve the fragment; keep the visible course in
    // the URL even after manual scrolling, without adding history entries.
    const hash = location.hash.slice(1);
    const belongsHere = hash === courses[index].id || hash.startsWith(courses[index].id + '-level-');
    if (!belongsHere && (!hash || courses.some(course => hash === course.id || hash.startsWith(course.id + '-level-')))) {
      const destination = new URL(location.href); destination.hash = courses[index].id;
      history.replaceState(history.state, '', destination);
    }
  };
  const offset = () => {
    const topnav = document.querySelector('.topnav');
    return (topnav ? topnav.getBoundingClientRect().height : 0) +
      (window.matchMedia('(max-width:700px)').matches ? sidebar.getBoundingClientRect().height : 0) + 24;
  };
  const jump = (target, focus) => {
    const course = target.closest('.course-outline');
    const index = courses.indexOf(course);
    if (index < 0) return;
    current(index);
    const heading = target.matches('.course-outline') ? headings[index] : target.querySelector('.section-heading h2');
    if (focus && heading) heading.focus({preventScroll:true});
    window.scrollTo({top:Math.max(0, target.getBoundingClientRect().top + window.scrollY - offset()), behavior:'instant'});
  };
  const fragment = () => {
    let id;
    try { id = decodeURIComponent(location.hash.slice(1)); } catch (_) { return null; }
    const target = document.getElementById(id);
    return target && content.contains(target) && target.matches('.course-outline,.level-block') ? target : null;
  };
  sidebar.addEventListener('click', event => {
    const link = event.target.closest('a');
    if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const target = document.getElementById(link.hash.slice(1));
    if (!target || !content.contains(target)) return;
    event.preventDefault();
    const destination = new URL(location.href); destination.hash = target.id;
    if (destination.href !== location.href) history.pushState(null, '', destination);
    jump(target, true);
  });
  const track = () => {
    pending = false;
    const boundary = offset() + 16;
    let index = -1;
    courses.forEach((course, i) => { if (course.getBoundingClientRect().top <= boundary) index = i; });
    // Visiting the header to change language must not reset a selected course.
    if (index >= 0) current(index);
    else if (active < 0) current(0);
  };
  window.addEventListener('scroll', () => {
    if (!pending) { pending = true; requestAnimationFrame(track); }
  }, {passive:true});
  window.addEventListener('hashchange', () => { const target = fragment(); if (target) jump(target, true); });
  const initial = fragment();
  if (initial) {
    // Wait for the layout font before restoring a deep link on reload/switch.
    Promise.resolve(document.fonts ? document.fonts.ready : null).then(() => jump(initial, false));
  } else track();
})();
