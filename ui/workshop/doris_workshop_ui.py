"""UI-only server-side rendering. Receives trusted routes and already-sanitized lesson HTML."""
from html import escape, unescape
from itertools import groupby
import json
from pathlib import Path
import re
from string import Template
from urllib.parse import urlsplit

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
    def __init__(self, *, asset_base, home, help_url=None, home_label='Workshops'):
        self.asset_base = asset_base.rstrip('/')
        self.home = home
        self.home_label = home_label
        self.help = help_url or home.rstrip('/') + '/help'
        self.copy = json.loads((ROOT / 'src/copy.json').read_text())
        self.services = json.loads((ROOT / 'src/services.json').read_text())

    def service_label(self, name):
        return self.services.get(name, {}).get('label', name)

    def service_role(self, name):
        return self.services.get(name, {}).get('role', 'extra')

    def template(self, name, **values):
        return Template((ROOT / 'src/templates' / (name + '.html')).read_text()).substitute(values)

    def page(self, title, body, *, identity='', navigation=()):
        nav = '<nav class="topnav" aria-label="Workshop navigation">' + ''.join(
            f'<a href="{href(url)}"' + (' aria-current="page"' if current else '') +
            (' target="_blank" rel="noopener"' if external else '') + f'>{text(label)}</a>'
            for label, url, current, external in navigation) + '</nav>' if navigation else ''
        return self.template('page', title=text(title), body=body, identity=identity,
            navigation=nav, asset_base=href(self.asset_base), home=href(self.home), help=href(self.help))

    def breadcrumb(self, label, course=None):
        middle = f'<a href="{href(course)}">Course outline</a><span aria-hidden="true">/</span>' if course else ''
        return f'<nav class="breadcrumb" aria-label="Breadcrumb"><a href="{href(self.home)}">{text(self.home_label)}</a><span aria-hidden="true">/</span>{middle}<span aria-current="page">{text(label)}</span></nav>'

    def activities(self, activities):
        if not activities:
            return self.state('no-activity')
        rows = ''.join(f'<article class="activity-row"><span class="number" aria-hidden="true">{i:02}</span>'
            f'<h2>{text(row["id"])}</h2><a class="primary" href="{href(row["href"])}">Sign in to workshop <span class="arrow" aria-hidden="true">→</span></a></article>'
            for i, row in enumerate(activities, 1))
        return self.page('Workshops', self.template('activities', activities=rows))

    def workspace(self, *, event, learner, course, tools, extra, logout):
        identity = f'<div class="identity"><span class="mono activity-id">{text(event)}</span><span class="mono learner">{text(learner)}</span><form method="post" action="{href(logout)}"><button class="signout" type="submit">Sign out of this entry</button></form></div>'
        rows = ''.join(f'<a class="tool-row" href="{href(row["href"])}"' +
            (' target="_blank" rel="noopener"' if row.get('external') else '') +
            f'><span class="tool-number" aria-hidden="true">{i:02}</span><h3>' +
            (f'<img class="jupyter" src="{href(self.asset_base)}/jupyter.svg" alt="Jupyter">' if row['id'] == 'jupyter' else '') +
            f'{text(row["title"])}</h3><span class="arrow" aria-hidden="true">↗</span></a>'
            for i, row in enumerate(tools, 2))
        extra_label = 'Multi-FE' if extra and all(self.service_role(row.get('id')) == 'fe' for row in extra) else 'More tools'
        extras = f'<div class="fe-links"><span class="muted">{extra_label}</span>' + ''.join(
            f'<a class="text-link" href="{href(row["href"])}" target="_blank" rel="noopener">{text(row["title"])} ↗</a>' for row in extra) + '</div>' if extra else ''
        body = self.template('workspace', breadcrumb=self.breadcrumb('My workspace'), learner=text(learner),
            course=href(course), tools=rows, extra=extras)
        return self.page('My workspace', body, identity=identity)

    def course(self, courses, *, course_url, notebooks, terminal):
        sections = []
        for course in courses:
            blocks = []
            numbered = list(enumerate(course['modules'], 1))
            grouped = bool(course.get('levels')) and all(isinstance(m.get('level'), int) for _, m in numbered)
            for level, modules in groupby(numbered, key=lambda item: item[1].get('level') if grouped else None):
                label = course.get('levels', {}).get(str(level), f'Level {level}') if level is not None else ''
                level_heading = f'<div class="section-heading"><h2>{text(label)}</h2><span class="muted">LEVEL {level}</span></div>' if label else ''
                rows = ''.join(f'<article class="module"><span class="module-number" aria-hidden="true">{i:02}</span><h3>{text(module["title"])}</h3><div class="actions">'
                    f'<a class="text-link" href="{href(module["lesson"])}">Read lesson →</a><a class="text-link" href="{href(module["lab"])}" target="_blank" rel="noopener">Open Lab ↗</a></div></article>'
                    for i, module in modules)
                blocks.append(f'<section class="level-block">{level_heading}{rows}</section>')
            heading = f'<div class="section-heading"><h2>{text(course["title"])}</h2></div>' if len(courses) > 1 else ''
            sections.append(f'<section class="course-outline">{heading}{"".join(blocks)}</section>')
        title = courses[0]['title'] if len(courses) == 1 else 'Courses & Labs'
        body = self.template('course', breadcrumb=self.breadcrumb('Course outline'), title=text(title),
            notebooks=href(notebooks), modules=''.join(sections))
        return self.page(title, body, navigation=self.course_nav(course_url, notebooks, terminal, 'course'))

    def lesson(self, title, html, *, course, notebooks, terminal, lab=None):
        # Only the caller's existing safe Markdown renderer may supply html.
        html = re.sub(r'(<table(?: [^>]+)?>)', r'<div class="scrollbox" tabindex="0" role="region" aria-label="Scrollable lesson table">\1', html).replace('</table>', '</table></div>')
        html = html.replace('<pre>', '<pre tabindex="0" role="region" aria-label="Scrollable code example">')
        contents = []
        for slug, title_html in re.findall(r'<h2 id="([^"]+)"[^>]*>(.*?)</h2>', html, re.S):
            label = unescape(re.sub('<[^>]+>', '', title_html))
            contents.append(f'<a href="#{text(slug)}">{text(label)}</a>')
        toc = '<details class="toc"><summary>In this lesson</summary><nav aria-label="On this page">' + ''.join(contents) + '</nav></details>' if contents else ''
        lab_html = f'<a class="primary" href="{href(lab)}" target="_blank" rel="noopener">Open Lab <span class="arrow" aria-hidden="true">↗</span></a><p>Opens in a new tab.</p>' if lab else ''
        body = self.template('lesson', breadcrumb=self.breadcrumb(title), title=text(title),
            content=html, course=href(course), lab=lab_html, contents=toc)
        return self.page(title, body, navigation=self.course_nav(course, notebooks, terminal, 'course'))

    def terminal(self, *, course, notebooks, terminal):
        body = self.template('terminal', breadcrumb=self.breadcrumb('Web Terminal'),
            notebooks=href(notebooks), course=href(course))
        return self.page('Web Terminal', body, navigation=self.course_nav(course, notebooks, terminal, 'terminal'))

    @staticmethod
    def course_nav(course, notebooks, terminal, current):
        return [('Course outline', course, current == 'course', False), ('Notebooks', notebooks, False, True),
            ('Web Terminal', terminal, current == 'terminal', False)]

    def state(self, key, *, destination=None):
        row = self.copy[key]
        destination = destination or self.home
        primary = self.home_label if row['destination'] == 'activity' and self.home_label != 'Workshops' else row['primary']
        detail = f'<div class="state-detail"><p>{text(row["detail"])}</p></div>' if row.get('detail') else ''
        body = self.template('state', label=text(row['label']), title=text(row['title']), body=text(row['body']),
            detail=detail, destination=href(destination), primary=text(primary))
        return self.page(row['title'], body)


def asset(name):
    files = {'logo.svg': ('assets/doris-trimmed.svg', 'image/svg+xml'),
        'jupyter.svg': ('assets/jupyter.svg', 'image/svg+xml'),
        'manrope.ttf': ('assets/manrope.ttf', 'font/ttf')}
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
