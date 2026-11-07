"""Authenticated course navigation and optional instance-local learning records."""

import json
import mimetypes
from pathlib import Path
import posixpath
import re
import sys
import sqlite3
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from jupyter_server.base.handlers import JupyterHandler
from jupyter_server.utils import url_path_join
from markdown_it import MarkdownIt
from tornado import web

UI_ROOT = Path(__file__).resolve().parent / "ui"
if not UI_ROOT.is_dir():
    UI_ROOT = Path(__file__).resolve().parents[3] / "ui/workshop"
sys.path.insert(0, str(UI_ROOT))
from doris_workshop_ui import Renderer, asset as ui_asset
from i18n import InstalledUI, LocaleUnavailable, with_locale


def content_url(base, path):
    return url_path_join(base, "workshop/content", quote(path, safe="/"))


def notebook_url(base, path):
    return url_path_join(base, "lab/tree", quote(path, safe="/"))


def rewrite_link(value, current, base, context=None):
    """Resolve only a Markdown link's URL, preserving its query and fragment."""
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or not parts.path or parts.path.startswith("/"):
        return value
    path = posixpath.normpath(posixpath.join(posixpath.dirname(current), unquote(parts.path)))
    route = notebook_url if path.endswith(".ipynb") else content_url
    result = urlunsplit(("", "", route(base, path), parts.query, parts.fragment))
    return context.url(result) if context else result


def render_markdown(text, current, base, context=None):
    renderer = MarkdownIt("commonmark", {"html": False}).enable("table")
    tokens = renderer.parse(text)
    used = {}
    heading_ids = context.heading_ids(current) if context else None
    heading_index = 0
    for index, token in enumerate(tokens):
        if token.type == "heading_open":
            slug = re.sub(r"[^\w\- ]", "", tokens[index + 1].content.lower()).replace(" ", "-")
            count = used.get(slug, 0)
            used[slug] = count + 1
            if heading_ids is not None:
                if heading_index >= len(heading_ids):
                    raise ValueError('Stable anchor count differs from lesson headings')
                token.attrSet('id', heading_ids[heading_index])
            else:
                token.attrSet("id", slug + (f"-{count}" if count else ""))
            heading_index += 1
    if heading_ids is not None and heading_index != len(heading_ids):
        raise ValueError('Stable anchor count differs from lesson headings')

    def rewrite(items):
        for token in items:
            alignment = token.attrGet("style")
            if token.tag in {"td", "th"} and alignment in {"text-align:left", "text-align:right", "text-align:center"}:
                token.attrs.pop("style", None)
                token.attrSet("class", "align-" + alignment.split(":")[1])
            for key in ("href", "src"):
                value = token.attrGet(key)
                if value:
                    token.attrSet(key, rewrite_link(value, current, base, context))
            if token.children:
                rewrite(token.children)
    rewrite(tokens)
    return renderer.renderer.render(tokens, renderer.options, {})


class PortalHandler(JupyterHandler):
    def initialize(self, plan, source, progress_db=None, installed_ui=None):
        self.plan, self.source = plan, Path(source).resolve()
        self.installed_ui = installed_ui or InstalledUI()
        self.progress = None
        self.modules = {}
        if progress_db is not None:
            sys.path.insert(0, str(self.source / 'doris-course/common'))
            from doris_labkit import progress
            self.progress = progress
            self.store = progress.Store(progress_db)
            self.modules = progress.registry(plan)
        self.ui = self.renderer(self.installed_ui.context())

    def renderer(self, context):
        return Renderer(context=context, current_url=self.request.path, asset_base=url_path_join(self.base_url, "workshop/static"),
            home=url_path_join(self.base_url, "workshop/"), help_url=url_path_join(self.base_url, "workshop/help"), home_label="Course outline")

    async def prepare(self):
        await super().prepare()
        if self._finished or not self.current_user or '/static/' in self.request.path:
            return
        if len(self.get_query_arguments('locale')) > 1:
            raise web.HTTPError(400)
        try:
            # The gateway fixes origin to the assigned learner instance. No URL
            # parameter can choose an identity or another preference namespace.
            context = self.installed_ui.context(explicit=self.get_query_argument('locale', None),
                browser=self.request.headers.get('Accept-Language', ''),
                activity=self.plan.get('event_id', ''), learner=self.plan.get('learner_id', ''),
                scope=self.request.host + self.base_url)
            self.ui = self.renderer(context)
        except LocaleUnavailable:
            self.set_status(406)
            self.page(self.ui.unavailable(language=True))

    def write_error(self, status_code, **kwargs):
        if hasattr(self, 'ui'):
            self.page(self.ui.unavailable())
        else:
            super().write_error(status_code, **kwargs)

    def page(self, html):
        self.set_header("Cache-Control", "no-store")
        self.set_header("Content-Security-Policy", "default-src 'none'; img-src 'self' https: data:; style-src 'self'; script-src 'self'; font-src 'self'; base-uri 'none'; frame-ancestors 'self'; form-action 'self'")
        self.set_header("Content-Type", "text/html; charset=utf-8")
        self.finish(html)

    def course_links(self):
        return {key: self.ui.context.url(value) for key, value in {"course": url_path_join(self.base_url, "workshop/"), "notebooks": url_path_join(self.base_url, "lab"),
            "terminal": url_path_join(self.base_url, "workshop/terminal")}.items()}

    def notebook(self, path, kind="notebook"):
        try:
            resolved, actual = self.ui.context.resolve_content(path, kind)
        except LocaleUnavailable:
            raise web.HTTPError(406)
        if actual not in self.installed_ui.messages:
            raise web.HTTPError(406)
        return with_locale(notebook_url(self.base_url, resolved), actual)

    def module_view(self, course, module, records):
        directory = course['course_dir']
        display = self.installed_ui.display('module', (course.get('slug'), module['id']), {'title': module['title']}, self.ui.context.locale)
        row = {**module, **display, 'quiz': None, 'lesson': content_url(self.base_url, directory + '/' + module['course']),
               'lab': self.notebook(directory + '/' + module['lab'])}
        quiz = str(Path(module['quiz']).with_suffix('.ipynb'))
        if quiz in course.get('notebooks', []):
            row['quiz'] = self.notebook(directory + '/' + quiz, 'quiz')
        key = course.get('slug'), module['id']
        if self.progress and records is not None and key in self.modules:
            row['progress'] = self.progress.view(self.modules[key], records.get(key))
            route = url_path_join(self.base_url, 'workshop/progress', *key)
            row.update(mark=route + '/mark', open_lab=route + '/lab')
        return row

    def xsrf_html(self):
        return self.xsrf_form_html()

    def progress_error(self, message=None, status=503):
        message = message or self.ui.t('progress.failed')
        self.set_status(status)
        self.page(self.ui.progress_page(action=url_path_join(self.base_url, 'workshop/progress/import'),
                                      xsrf=self.xsrf_html(), message=message, error=True))


class IndexHandler(PortalHandler):
    @web.authenticated
    def get(self):
        if self.ui.context.locale != 'en' and (not self.installed_ui.content or not self.installed_ui.labels):
            self.set_status(406)
            return self.page(self.ui.unavailable(language=True))
        courses = []
        records, progress_ui = None, None
        if self.progress:
            progress_ui = {'xsrf': self.xsrf_html(), 'export': url_path_join(self.base_url, 'workshop/progress/export'),
                           'import': url_path_join(self.base_url, 'workshop/progress/import')}
            try:
                records = self.store.records()
            except (OSError, sqlite3.Error, ValueError):
                progress_ui['error'] = True
        for course in self.plan["courses"]:
            modules = [self.module_view(course, module, records) for module in course.get('modules', [])]
            row = {'title': course.get('title', course['course_dir'].rsplit('/', 1)[-1]), 'modules': modules, 'levels': course.get('levels', {})}
            row.update(self.installed_ui.display('course', course.get('slug'), {'title': row['title'], 'levels': row['levels']}, self.ui.context.locale))
            row['optional'] = [{**self.installed_ui.display('optional', (course.get('slug'), part['path']), {'title': part['title']}, self.ui.context.locale), 'href': self.notebook(course['course_dir'] + '/' + part['path'])}
                               for part in course.get('optional', []) if part['path'] in course.get('notebooks', [])]
            if self.progress and records is not None:
                row['progress'] = self.progress.summary([m for (slug, _), m in self.modules.items() if slug == course['slug']], records)
            courses.append(row)
        links = self.course_links()
        self.page(self.ui.course(courses, course_url=links["course"], notebooks=links["notebooks"], terminal=links["terminal"], progress=progress_ui))


class ContentHandler(PortalHandler):
    @web.authenticated
    def get(self, path):
        try:
            resolved, actual = self.ui.context.resolve_content(path, 'lesson' if path.endswith('.md') else 'asset')
        except LocaleUnavailable:
            self.set_status(406)
            return self.page(self.ui.unavailable(language=True))
        if actual != self.ui.context.locale:
            return self.redirect(with_locale(content_url(self.base_url, path), actual))
        target = (self.source / resolved).resolve()
        allowed = any(target.is_relative_to((self.source / c["course_dir"]).resolve()) for c in self.plan["courses"])
        if not allowed or not target.is_file() or any(p.startswith(".") for p in Path(path).parts):
            raise web.HTTPError(404)
        if target.suffix == ".md":
            source = target.read_text()
            heading = re.match(r"^#\s+([^\n]+)\n", source)
            title = heading[1] if heading else target.stem
            source = source[heading.end():] if heading else source
            selected = next(((course, module) for course in self.plan['courses'] for module in course.get('modules', [])
                             if path == course['course_dir'] + '/' + module['course']), None)
            lab, row = None, None
            if selected:
                course, module = selected
                lab = self.notebook(course['course_dir'] + '/' + module['lab'])
                if self.progress:
                    try:
                        row = self.module_view(course, module, self.store.records())
                        if 'progress' not in row:
                            row = None
                    except LocaleUnavailable:
                        self.set_status(406)
                        return self.page(self.ui.unavailable(language=True))
                    except (OSError, sqlite3.Error, ValueError):
                        return self.progress_error()
            try:
                html = render_markdown(source, path, self.base_url, self.ui.context)
            except LocaleUnavailable:
                self.set_status(406)
                return self.page(self.ui.unavailable(language=True))
            self.page(self.ui.lesson(title, html, lab=lab,
                                     module=row, xsrf=self.xsrf_html() if row else '', **self.course_links()))
        elif target.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
            self.set_header("Content-Type", mimetypes.guess_type(str(target))[0])
            self.set_header("X-Content-Type-Options", "nosniff")
            # SVG is served as an image, never as an executable document.
            self.set_header("Content-Security-Policy", "default-src 'none'; sandbox")
            self.finish(target.read_bytes())
        elif target.suffix == ".ipynb" and any(path == c["course_dir"] + "/" + p
                                               for c in self.plan["courses"] for p in c["notebooks"]):
            self.redirect(self.notebook(path))
        else:
            raise web.HTTPError(404)


class TerminalHandler(PortalHandler):
    @web.authenticated
    def get(self):
        self.page(self.ui.terminal(**self.course_links()))


class AssetHandler(PortalHandler):
    @web.authenticated
    def get(self, name):
        try:
            data, mime = ui_asset(name)
        except KeyError:
            raise web.HTTPError(404)
        self.set_header("Content-Type", mime)
        self.set_header("X-Content-Type-Options", "nosniff")
        self.set_header("Cache-Control", "no-store")
        self.finish(data)

    head = get


class HelpHandler(PortalHandler):
    @web.authenticated
    def get(self):
        self.page(self.ui.state("help"))


class ProgressHandler(PortalHandler):
    def check_xsrf_cookie(self):
        # Jupyter normally exempts token-authenticated requests. The gateway
        # injects its token for every learner request, so require form XSRF here.
        web.RequestHandler.check_xsrf_cookie(self)
        origin = self.request.headers.get('Origin')
        if origin and origin not in {f'{self.request.protocol}://{self.request.host}', f'https://{self.request.host}'}:
            raise web.HTTPError(403, 'Cross-origin progress writes are not allowed.')

    def require_progress(self):
        if not self.progress:
            raise web.HTTPError(404)
        if len(self.request.body) > self.progress.MAX_BYTES * 4 + 65536:
            raise web.HTTPError(413, 'Progress files must be at most 1 MiB.')


class MarkHandler(ProgressHandler):
    @web.authenticated
    def post(self, slug, module_id, action):
        self.require_progress()
        module = self.modules.get((slug, module_id))
        if module is None:
            raise web.HTTPError(404)
        try:
            if action == 'lab':
                self.store.mark(module, 'lab_opened')
                destination = self.notebook(module['course_dir'] + '/' + module['lab'])
            else:
                field, value = self.get_body_argument('field'), self.get_body_argument('value')
                if field not in {'course_read', 'lab_declared_complete'} or value not in {'true', 'false'}:
                    raise self.progress.ProgressError('Invalid completion operation.')
                self.store.mark(module, field, value == 'true')
                destination = (content_url(self.base_url, module['course_dir'] + '/' + module['course'])
                               if self.get_body_argument('return', 'outline') == 'lesson' else self.course_links()['course'])
        except self.progress.ProgressError as exc:
            return self.progress_error(self.ui.t.error(exc), 400)
        except (OSError, sqlite3.Error, ValueError):
            return self.progress_error()
        self.redirect(self.ui.context.url(destination), status=303)


class ExportHandler(ProgressHandler):
    @web.authenticated
    def get(self):
        self.require_progress()
        try:
            version = self.get_query_argument('version', '2')
            if version not in {'1', '2'}:
                raise self.progress.ProgressError('Unsupported progress schema_version; expected 1 or 2.')
            data = self.store.export(version=int(version))
        except self.progress.ProgressError as exc:
            return self.progress_error(self.ui.t.error(exc), 400)
        except (OSError, sqlite3.Error, ValueError):
            return self.progress_error(self.ui.t('progress.download-failed'))
        self.set_header('Cache-Control', 'no-store')
        self.set_header('Content-Type', 'application/json')
        self.set_header('Content-Disposition', 'attachment; filename="doris-workshop-progress.json"')
        self.set_header('X-Content-Type-Options', 'nosniff')
        if version == '1':
            self.set_header('Warning', '299 - "v1 omits attempt, language and version history; retain the v2 export"')
        self.finish(data)


class ImportHandler(ProgressHandler):
    def render_import(self, **kwargs):
        self.page(self.ui.progress_page(action=url_path_join(self.base_url, 'workshop/progress/import'),
                                       xsrf=self.xsrf_html(), **kwargs))

    @web.authenticated
    def get(self):
        self.require_progress()
        self.render_import()

    @web.authenticated
    def post(self):
        self.require_progress()
        try:
            if self.get_body_argument('confirm', '') == 'yes':
                raw = self.get_body_argument('payload')
                document = self.progress.validate_document(raw)
                count = self.store.import_document(document, self.modules)
                self.render_import(message=self.ui.t.plural('import.saved', count))
            else:
                files = self.request.files.get('file', [])
                if len(files) != 1:
                    return self.progress_error(self.ui.t('import.choose'), 400)
                raw = files[0]['body']
                document = self.progress.validate_document(raw)
                rows = self.store.preview(document, self.modules)
                self.render_import(rows=rows, payload=json.dumps(document, separators=(',', ':')))
        except self.progress.ProgressError as exc:
            self.progress_error(self.ui.t.error(exc), 400)
        except (OSError, sqlite3.Error, ValueError):
            self.progress_error()


def register(server, plan, source, progress_db=None, installed_ui=None):
    prefix = url_path_join(server.base_url, "workshop")
    installed_ui = installed_ui or InstalledUI()
    options = {"plan": plan, "source": source, "progress_db": progress_db, "installed_ui": installed_ui}
    server.web_app.add_handlers(".*$", [
        (prefix + "/?", IndexHandler, options),
        (prefix + "/terminal", TerminalHandler, options),
        (prefix + "/help", HelpHandler, options),
        (prefix + "/static/([^/]+)", AssetHandler, options),
        (prefix + "/content/(.*)", ContentHandler, options),
        (prefix + "/progress/([a-z0-9-]+)/([a-z0-9-]+)/(mark|lab)", MarkHandler, options),
        (prefix + "/progress/export", ExportHandler, options),
        (prefix + "/progress/import", ImportHandler, options),
    ])
