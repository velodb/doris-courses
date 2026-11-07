"""UI-only server-side rendering. Receives trusted routes and already-sanitized lesson HTML."""
from html import escape, unescape
from itertools import groupby
import json
from pathlib import Path
import re
from string import Template
from urllib.parse import urlsplit
import service_guidance
from i18n import InstalledUI, with_locale

ROOT = Path(__file__).resolve().parent


def text(value):
    return escape(str(value), quote=True)


def href(value):
    value = str(value)
    parts = urlsplit(value)
    if any(ord(c) < 32 or c == '\\' for c in value) or parts.username or parts.password:
        raise ValueError('Invalid UI route')
    if parts.scheme:
        if parts.scheme != 'https' or not parts.hostname:
            raise ValueError('UI routes require HTTPS')
    elif parts.netloc or not parts.path.startswith('/'):
        raise ValueError('UI routes require a same-origin absolute path')
    return text(value)


class Renderer:
    def __init__(self, *, asset_base, home, help_url=None, home_label='Workshops', context=None, current_url=None):
        self.context = context or InstalledUI().context()
        self.t = self.context.messages
        self.m = self.t.html
        self.current_url = current_url or home
        self.asset_base = asset_base.rstrip('/')
        self.home = home
        self.home_label = self.t('nav.outline') if home_label == 'Course outline' else self.t('template.activities.text-1') if home_label == 'Workshops' else home_label
        self.help = help_url or home.rstrip('/') + '/help'
        self.copy = json.loads((ROOT / 'src/copy.json').read_text())
        for state, row in self.copy.items():
            for field in row:
                key = f'state.{state}.{field}'
                if key in self.t.messages:
                    row[field] = self.t(key)
        self.services = json.loads((ROOT / 'src/services.json').read_text())

    def href(self, value):
        parts = urlsplit(str(value))
        if (parts.path == '/' or parts.path.startswith(('/workshop', '/lab', '/doris-courses'))) and '/static' not in parts.path:
            value = self.context.url(str(value))
        return href(value)

    def language_picker(self):
        options = ''.join(f'<option value="{text(tag)}"' + (' selected' if tag == self.context.locale else '') + f'>{text(row["display_name"])}</option>'
                          for tag, row in self.context.installed.registry.items())
        return (f'<aside class="language-area"><form class="language-form" method="get" action="{href(self.current_url)}">'
                f'<label for="workshop-language">{self.m("locale.label")}</label><select name="locale" id="workshop-language">{options}</select>'
                f'<button type="submit">{self.m("locale.apply")}</button></form>'
                f'<details><summary>{self.m("locale.label")}</summary><p>{self.m("locale.boundary")}</p></details></aside>')

    def service_label(self, name):
        return self.t('service.single.label') if name == 'rta-fe' else self.services.get(name, {}).get('label', name)

    def service_role(self, name):
        return self.services.get(name, {}).get('role', 'extra')

    def template(self, name, **values):
        messages = {'msg_' + key.replace('.', '_').replace('-', '_'): self.m(key) for key in self.t.messages if key.startswith('template.' + name + '.')}
        return Template((ROOT / 'src/templates' / (name + '.html')).read_text()).substitute(**messages, **values)

    def page(self, title, body, *, identity='', navigation=()):
        nav = f'<nav class="topnav" aria-label="{self.m("nav.workshop")}">' + ''.join(
            f'<a href="{self.href(url)}"' + (' aria-current="page"' if current else '') +
            (' target="_blank" rel="noopener"' if external else '') + f'>{text(label)}</a>'
            for label, url, current, external in navigation) + '</nav>' if navigation else ''
        return self.template('page', title=text(title), body=body, identity=identity,
            navigation=nav, asset_base=href(self.asset_base), home=self.href(self.home), help=self.href(self.help),
            locale=text(self.context.locale), direction=text(self.context.direction), language=self.language_picker(),
            locale_context=text(json.dumps(self.context.client(), ensure_ascii=False)))

    def breadcrumb(self, label, course=None):
        middle = f'<a href="{self.href(course)}">{self.m("nav.outline")}</a><span aria-hidden="true">/</span>' if course else ''
        return f'<nav class="breadcrumb" aria-label="{self.m("nav.breadcrumb")}"><a href="{self.href(self.home)}">{text(self.home_label)}</a><span aria-hidden="true">/</span>{middle}<span aria-current="page">{text(label)}</span></nav>'

    def activities(self, activities):
        if not activities:
            return self.state('no-activity')
        rows = ''.join(f'<article class="activity-row"><span class="number" aria-hidden="true">{i:02}</span>'
            f'<h2>{text(row["id"])}</h2><a class="primary" href="{self.href(row["href"])}">{self.m("activity.signin")} <span class="arrow" aria-hidden="true">→</span></a></article>'
            for i, row in enumerate(activities, 1))
        return self.page(self.t('template.activities.text-1'), self.template('activities', activities=rows))

    def workspace(self, *, event, learner, course, tools, extra, logout):
        identity = f'<div class="identity"><span class="mono activity-id">{text(event)}</span><span class="mono learner">{text(learner)}</span><form method="post" action="{self.href(logout)}"><button class="signout" type="submit">{self.m("workspace.signout")}</button></form></div>'
        rows = ''.join(f'<a class="tool-row" href="{self.href(row["href"])}"' +
            (' target="_blank" rel="noopener"' if row.get('external') else '') +
            f'><span class="tool-number" aria-hidden="true">{i:02}</span><div><h3>' +
            (f'<img class="jupyter" src="{href(self.asset_base)}/jupyter.svg" alt="Jupyter">' if row['id'] == 'jupyter' else '') +
            f'{text(row["title"])}</h3>' +
            (f'<p>{text(service_guidance.details(row["id"], messages=self.t)["description"])}</p>' if service_guidance.details(row['id'], messages=self.t).get('description') else '') +
            '</div><span class="arrow" aria-hidden="true">↗</span></a>'
            for i, row in enumerate(tools, 2))
        extra_label = 'Multi-FE' if extra and all(self.service_role(row.get('id')) == 'fe' for row in extra) else self.t('workspace.more')
        extras = f'<div class="fe-links"><span class="muted">{text(extra_label)}</span>' + ''.join(
            f'<a class="text-link" href="{self.href(row["href"])}" target="_blank" rel="noopener">{text(row["title"])} ↗</a>' for row in extra) + '</div>' if extra else ''
        if any(row.get('id') in {'rta-fe1', 'rta-fe2', 'rta-fe3'} for row in extra):
            guide = service_guidance.details('rta-multi-fe', messages=self.t)
            extras += (f'<div class="service-guidance"><p>{text(guide["description"])}</p>'
                f'<a class="text-link" href="{self.href(service_guidance.lab_url(course, guide["lab"]))}" target="_blank" rel="noopener">{text(guide["lab_label"])} ↗</a></div>')
        body = self.template('workspace', breadcrumb=self.breadcrumb(self.t('template.workspace.text-1')), assigned=self.m('workspace.assigned', learner=learner),
            course=self.href(course), tools=rows, extra=extras)
        return self.page(self.t('template.workspace.text-1'), body, identity=identity)

    def form(self, action, xsrf, label, fields=None, *, disabled=False, style='text-link', new_tab=False):
        inputs = ''.join(f'<input type="hidden" name="{text(k)}" value="{text(v)}">' for k, v in (fields or {}).items())
        target = ' target="_blank" rel="noopener"' if new_tab else ''
        return (f'<form class="progress-form" method="post" action="{self.href(action)}"{target}>{xsrf}{inputs}'
                f'<button class="{text(style)}" type="submit"' + (' disabled' if disabled else '') + f'>{text(label)}</button></form>')

    def progress_controls(self, module, xsrf, *, lesson=False):
        state = module['progress']
        flags = [('course_read', self.t('progress.lesson')), ('lab_declared_complete', self.t('progress.lab')), ('quiz_passed', self.t('progress.quiz'))]
        badges = ''.join(f'<span class="credit {"earned" if state[k] else ""}">{text(label)}: {self.m("progress.done" if state[k] else "progress.todo")}</span>' for k, label in flags)
        status = f'<div class="credit-status">{badges}<strong>{self.m("progress.complete") if state["complete"] else str(state["parts"]) + " / 3"}</strong></div>'
        if not module.get('required', True):
            status += f'<p>{self.m("progress.optional")}</p>'
        if state['content_updated']:
            status += f'<p class="content-updated">{self.m("progress.updated")}</p>'
        if state['best_total']:
            status += f'<p>{self.m("progress.best", correct=state["best_correct"], total=state["best_total"])}</p>'
        field = 'course_read' if lesson else 'lab_declared_complete'
        label = self.t(('progress.undo-read' if state[field] else 'progress.mark-read') if lesson else ('progress.undo-lab' if state[field] else 'progress.mark-lab'))
        status += self.form(module['mark'], xsrf, label, {'field': field, 'value': 'false' if state[field] else 'true', 'return': 'lesson' if lesson else 'outline'},
                            disabled=not lesson and not state['lab_opened'])
        if not lesson and not state['lab_opened']:
            status += f'<p>{self.m("progress.open-first")}</p>'
        return status

    def course(self, courses, *, course_url, notebooks, terminal, progress=None):
        sections = []
        for course in courses:
            blocks = []
            numbered = list(enumerate(course['modules'], 1))
            grouped = bool(course.get('levels')) and all(isinstance(m.get('level'), int) for _, m in numbered)
            for level, modules in groupby(numbered, key=lambda item: item[1].get('level') if grouped else None):
                label = course.get('levels', {}).get(str(level), self.t('course.level', level=level) if level is not None else '') if level is not None else ''
                level_heading = f'<div class="section-heading"><h2>{text(label)}</h2><span class="muted">{self.m("course.level-label", level=level)}</span></div>' if label else ''
                rows = ''
                for i, module in modules:
                    lab = (self.form(module['open_lab'], progress['xsrf'], self.t('course.open-lab'), new_tab=True) if module.get('progress') is not None else
                           f'<a class="text-link" href="{self.href(module["lab"])}" target="_blank" rel="noopener">{self.m("course.open-lab")}</a>')
                    quiz = f'<a class="text-link" href="{self.href(module["quiz"])}" target="_blank" rel="noopener">{self.m("course.open-quiz")}</a>' if module.get('quiz') else ''
                    controls = '<div class="module-progress">' + self.progress_controls(module, progress['xsrf']) + '</div>' if module.get('progress') is not None else ''
                    rows += (f'<article class="module"><span class="module-number" aria-hidden="true">{i:02}</span><h3>{text(module["title"])}</h3><div class="actions">'
                             f'<a class="text-link" href="{self.href(module["lesson"])}">{self.m("course.read")}</a>{lab}{quiz}</div>{controls}</article>')
                blocks.append(f'<section class="level-block">{level_heading}{rows}</section>')
            heading = f'<div class="section-heading"><h2>{text(course["title"])}</h2></div>' if len(courses) > 1 else ''
            overall = ''
            if course.get('progress') is not None:
                score = course['progress']
                label = f'{score["percent"]:.1f}%' if score['total'] else self.t('progress.na')
                overall = (f'<div class="progress-panel"><h2>{self.m("progress.heading", score=label)}</h2>'
                           f'<p>{self.m("progress.summary", complete=score["complete"], modules=score["modules"], parts=score["parts"], total=score["total"])}</p>'
                           + (f'<progress aria-label="{self.m("progress.label")}" max="{score["total"]}" value="{score["parts"]}"></progress>' if score['total'] else '') + '</div>')
            optional = ''
            if course.get('optional'):
                links = ''.join(f'<li><a href="{self.href(part["href"])}" target="_blank" rel="noopener">{text(part["title"])}</a></li>' for part in course['optional'])
                optional = f'<section class="progress-panel"><h2>{self.m("course.optional")}</h2><p>{self.m("course.optional-detail")}</p><ul>' + links + '</ul></section>'
            sections.append(f'<section class="course-outline">{heading}{overall}{"".join(blocks)}{optional}</section>')
        title = courses[0]['title'] if len(courses) == 1 else self.t('template.workspace.text-3')
        body = self.template('course', breadcrumb=self.breadcrumb(self.t('nav.outline')), title=text(title),
            notebooks=self.href(notebooks), modules=''.join(sections), progress=self.progress_links(progress))
        return self.page(title, body, navigation=self.course_nav(course_url, notebooks, terminal, 'course'))

    def lesson(self, title, html, *, course, notebooks, terminal, lab=None, module=None, xsrf=''):
        # Only the caller's existing safe Markdown renderer may supply html.
        html = re.sub(r'(<table(?: [^>]+)?>)', lambda match: f'<div class="scrollbox" tabindex="0" role="region" aria-label="{self.m("lesson.table")}">' + match[0], html).replace('</table>', '</table></div>')
        html = html.replace('<pre>', f'<pre tabindex="0" role="region" aria-label="{self.m("lesson.code")}">')
        contents = []
        for slug, title_html in re.findall(r'<h2 id="([^"]+)"[^>]*>(.*?)</h2>', html, re.S):
            label = unescape(re.sub('<[^>]+>', '', title_html))
            contents.append(f'<a href="#{text(slug)}">{text(label)}</a>')
        toc = f'<details class="toc"><summary>{self.m("lesson.contents")}</summary><nav aria-label="{self.m("lesson.on-page")}">' + ''.join(contents) + '</nav></details>' if contents else ''
        lab_html = f'<a class="primary" href="{self.href(lab)}" target="_blank" rel="noopener">{self.m("lesson.open-lab")} <span class="arrow" aria-hidden="true">↗</span></a><p>{self.m("lesson.new-tab")}</p>' if lab else ''
        controls = ''
        if module is not None:
            lab_html = self.form(module['open_lab'], xsrf, self.t('course.open-lab'), style='primary', new_tab=True)
            lab_html += f'<a class="text-link" href="{self.href(module["quiz"])}" target="_blank" rel="noopener">{self.m("course.open-quiz")}</a>' if module.get('quiz') else ''
            controls = f'<section class="progress-panel" aria-label="{self.m("progress.module")}">' + self.progress_controls(module, xsrf, lesson=True) + '</section>'
        body = self.template('lesson', breadcrumb=self.breadcrumb(title), title=text(title),
            content=html, course=self.href(course), lab=lab_html, contents=toc, progress=controls)
        return self.page(title, body, navigation=self.course_nav(course, notebooks, terminal, 'course'))

    def progress_links(self, progress):
        if not progress:
            return ''
        if progress.get('error'):
            return f'<p class="progress-alert" role="alert">{self.m("progress.unavailable")}</p>'
        return (f'<section class="progress-panel"><h2>{self.m("progress.record")}</h2><p>{self.m("progress.instructions")}</p>'
                f'<div class="actions"><a class="text-link" href="{self.href(progress["export"])}">{self.m("progress.download")}</a>'
                f'<a class="text-link" href="{self.href(progress["import"])}">{self.m("template.progress.text-1")}</a>'
                f'<a class="text-link" href="{self.href(self.home)}">{self.m("progress.refresh")}</a></div>'
                f'<details class="progress-notes"><summary>{self.m("progress.saving")}</summary><p>{self.m("progress.transfer")}</p></details></section>')

    def progress_page(self, *, action, xsrf, rows=None, payload='', message='', error=False):
        if rows is None:
            form = (f'<form class="progress-upload" method="post" enctype="multipart/form-data" action="{self.href(action)}">{xsrf}'
                    f'<label for="progress-file">{self.m("import.file")}</label><input id="progress-file" type="file" name="file" accept="application/json,.json" required>'
                    f'<button class="primary" type="submit">{self.m("import.preview")}</button></form>')
        else:
            items = ''
            for row in rows:
                r = row['record']
                state = self.t('import.match' if row['matched'] else 'import.skip')
                if row['content_updated']:
                    state += self.t('import.updated')
                state += ' ' + self.t('import.history', attempts=row.get('attempt_count', 0), legacy=row.get('legacy_count', 0))
                after = row['result']
                outcome = (' · '.join(f'{label}: {self.t("progress.done" if after[key] else "progress.todo")}' for key, label in
                           [('course_read', self.t('progress.lesson')), ('lab_declared_complete', self.t('progress.lab')), ('quiz_passed', self.t('progress.quiz'))])
                           + self.t('import.best', correct=after["best_correct"], total=after["best_total"])) if after else self.t('import.skipped')
                items += (f'<tr><td>{text(r["course_slug"])} / {text(r["module_id"])}</td><td>{text(state)}</td>'
                          f'<td>{text(outcome)}</td><td>{text(r["source_ref"][:12])}<br>{text(r["updated_at"])}</td></tr>')
            form = (f'<div class="scrollbox" tabindex="0" role="region" aria-label="{self.m("import.matches")}"><table class="progress-table"><thead><tr>'
                    + ''.join(f'<th>{self.m(key)}</th>' for key in ('import.module', 'import.action', 'import.after', 'import.source'))
                    + '</tr></thead><tbody>' + items + '</tbody></table></div>'
                    + f'<p>{self.m("import.merge")}</p>'
                    + self.form(action, xsrf, self.t('import.confirm'), {'payload': payload, 'confirm': 'yes'}, style='primary'))
        notice = f'<p class="progress-alert" role="{"alert" if error else "status"}">{text(message)}</p>' if message else ''
        body = self.template('progress', breadcrumb=self.breadcrumb(self.t('template.progress.text-1')), form=form, message=notice, home=self.href(self.home))
        return self.page(self.t('template.progress.text-1'), body)

    def terminal(self, *, course, notebooks, terminal):
        body = self.template('terminal', breadcrumb=self.breadcrumb(self.t('nav.terminal')),
            notebooks=self.href(notebooks), course=self.href(course))
        return self.page(self.t('nav.terminal'), body, navigation=self.course_nav(course, notebooks, terminal, 'terminal'))

    def course_nav(self, course, notebooks, terminal, current):
        return [(self.t('nav.outline'), course, current == 'course', False), (self.t('nav.notebooks'), notebooks, False, True),
            (self.t('nav.terminal'), terminal, current == 'terminal', False)]

    def unavailable(self, *, language=False):
        body = f'<section class="state"><h1>{self.m("error.title")}</h1><p>{self.m("locale.unavailable" if language else "error.body")}</p><a href="{href(with_locale(self.current_url, "en"))}">{self.m("locale.english")}</a></section>'
        return self.page(self.t('error.title'), body)

    def state(self, key, *, destination=None):
        row = self.copy[key]
        destination = destination or self.home
        primary = self.home_label if row['destination'] == 'activity' and self.home_label != self.t('template.activities.text-1') else row['primary']
        detail = f'<div class="state-detail"><p>{text(row["detail"])}</p></div>' if row.get('detail') else ''
        body = self.template('state', label=text(row['label']), title=text(row['title']), body=text(row['body']),
            detail=detail, destination=self.href(destination), primary=text(primary))
        return self.page(row['title'], body)


def asset(name):
    files = {'logo.svg': ('assets/doris-trimmed.svg', 'image/svg+xml'),
        'jupyter.svg': ('assets/jupyter.svg', 'image/svg+xml'),
        'manrope.ttf': ('assets/manrope.ttf', 'font/ttf')}
    if name == 'locale.js':
        return (ROOT / 'src/locale.js').read_bytes(), 'text/javascript'
    if name == 'ui.css':
        css = "@font-face{font-family:Manrope;src:url('manrope.ttf') format('truetype');font-weight:200 800;font-display:swap}\n"
        css += (ROOT / 'src/styles.css').read_text()
        css += '\n.signout{background:none;border:0;padding:8px 0;color:var(--link);text-decoration:underline;font:inherit;font-size:14px;cursor:pointer}.identity form{margin:0}.tool-row h3{margin:0}\n'
        css += '.article pre{max-width:100%}\n'
        css += '.article .align-left{text-align:left}.article .align-right{text-align:right}.article .align-center{text-align:center}\n'
        return css.encode(), 'text/css'
    if name not in files:
        raise KeyError(name)
    path, mime = files[name]
    return (ROOT / path).read_bytes(), mime
