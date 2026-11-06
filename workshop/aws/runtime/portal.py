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


def content_url(base, path):
    return url_path_join(base, "workshop/content", quote(path, safe="/"))


def notebook_url(base, path):
    return url_path_join(base, "lab/tree", quote(path, safe="/"))


def rewrite_link(value, current, base):
    """Resolve only a Markdown link's URL, preserving its query and fragment."""
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or not parts.path or parts.path.startswith("/"):
        return value
    path = posixpath.normpath(posixpath.join(posixpath.dirname(current), unquote(parts.path)))
    route = notebook_url if path.endswith(".ipynb") else content_url
    return urlunsplit(("", "", route(base, path), parts.query, parts.fragment))


def render_markdown(text, current, base):
    renderer = MarkdownIt("commonmark", {"html": False}).enable("table")
    tokens = renderer.parse(text)
    used = {}
    for index, token in enumerate(tokens):
        if token.type == "heading_open":
            slug = re.sub(r"[^\w\- ]", "", tokens[index + 1].content.lower()).replace(" ", "-")
            count = used.get(slug, 0)
            used[slug] = count + 1
            token.attrSet("id", slug + (f"-{count}" if count else ""))

    def rewrite(items):
        for token in items:
            alignment = token.attrGet("style")
            if token.tag in {"td", "th"} and alignment in {"text-align:left", "text-align:right", "text-align:center"}:
                token.attrs.pop("style", None)
                token.attrSet("class", "align-" + alignment.split(":")[1])
            for key in ("href", "src"):
                value = token.attrGet(key)
                if value:
                    token.attrSet(key, rewrite_link(value, current, base))
            if token.children:
                rewrite(token.children)
    rewrite(tokens)
    return renderer.renderer.render(tokens, renderer.options, {})


class PortalHandler(JupyterHandler):
    def initialize(self, plan, source, progress_db=None):
        self.plan, self.source = plan, Path(source).resolve()
        self.progress = None
        self.modules = {}
        if progress_db is not None:
            sys.path.insert(0, str(self.source / 'doris-course/common'))
            from doris_labkit import progress
            self.progress = progress
            self.store = progress.Store(progress_db)
            self.modules = progress.registry(plan)
        self.ui = Renderer(asset_base=url_path_join(self.base_url, "workshop/static"),
            home=url_path_join(self.base_url, "workshop/"), help_url=url_path_join(self.base_url, "workshop/help"), home_label="Course outline")

    def page(self, html):
        self.set_header("Cache-Control", "no-store")
        self.set_header("Content-Security-Policy", "default-src 'none'; img-src 'self' https: data:; style-src 'self'; font-src 'self'; base-uri 'none'; frame-ancestors 'self'; form-action 'self'")
        self.set_header("Content-Type", "text/html; charset=utf-8")
        self.finish(html)

    def course_links(self):
        return {"course": url_path_join(self.base_url, "workshop/"), "notebooks": url_path_join(self.base_url, "lab"),
            "terminal": url_path_join(self.base_url, "workshop/terminal")}

    def module_view(self, course, module, records):
        directory = course['course_dir']
        row = {**module, 'lesson': content_url(self.base_url, directory + '/' + module['course']),
               'lab': notebook_url(self.base_url, directory + '/' + module['lab'])}
        quiz = str(Path(module['quiz']).with_suffix('.ipynb'))
        if quiz in course.get('notebooks', []):
            row['quiz'] = notebook_url(self.base_url, directory + '/' + quiz)
        key = course.get('slug'), module['id']
        if self.progress and records is not None and key in self.modules:
            row['progress'] = self.progress.view(self.modules[key], records.get(key))
            route = url_path_join(self.base_url, 'workshop/progress', *key)
            row.update(mark=route + '/mark', open_lab=route + '/lab')
        return row

    def xsrf_html(self):
        return self.xsrf_form_html()

    def progress_error(self, message='Progress was not saved. Your existing record is unchanged. Reload and try again.', status=503):
        self.set_status(status)
        self.page(self.ui.progress_page(action=url_path_join(self.base_url, 'workshop/progress/import'),
                                      xsrf=self.xsrf_html(), message=message, error=True))


class IndexHandler(PortalHandler):
    @web.authenticated
    def get(self):
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
            row['optional'] = [{'title': part['title'], 'href': notebook_url(self.base_url, course['course_dir'] + '/' + part['path'])}
                               for part in course.get('optional', []) if part['path'] in course.get('notebooks', [])]
            if self.progress and records is not None:
                row['progress'] = self.progress.summary([m for (slug, _), m in self.modules.items() if slug == course['slug']], records)
            courses.append(row)
        links = self.course_links()
        self.page(self.ui.course(courses, course_url=links["course"], notebooks=links["notebooks"], terminal=links["terminal"], progress=progress_ui))


class ContentHandler(PortalHandler):
    @web.authenticated
    def get(self, path):
        target = (self.source / path).resolve()
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
                lab = notebook_url(self.base_url, course['course_dir'] + '/' + module['lab'])
                if self.progress:
                    try:
                        row = self.module_view(course, module, self.store.records())
                        if 'progress' not in row:
                            row = None
                    except (OSError, sqlite3.Error, ValueError):
                        return self.progress_error()
            self.page(self.ui.lesson(title, render_markdown(source, path, self.base_url), lab=lab,
                                     module=row, xsrf=self.xsrf_html() if row else '', **self.course_links()))
        elif target.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
            self.set_header("Content-Type", mimetypes.guess_type(str(target))[0])
            self.set_header("X-Content-Type-Options", "nosniff")
            # SVG is served as an image, never as an executable document.
            self.set_header("Content-Security-Policy", "default-src 'none'; sandbox")
            self.finish(target.read_bytes())
        elif target.suffix == ".ipynb" and any(path == c["course_dir"] + "/" + p
                                               for c in self.plan["courses"] for p in c["notebooks"]):
            self.redirect(notebook_url(self.base_url, path))
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
                destination = notebook_url(self.base_url, module['course_dir'] + '/' + module['lab'])
            else:
                field, value = self.get_body_argument('field'), self.get_body_argument('value')
                if field not in {'course_read', 'lab_declared_complete'} or value not in {'true', 'false'}:
                    raise self.progress.ProgressError('Invalid completion operation.')
                self.store.mark(module, field, value == 'true')
                destination = (content_url(self.base_url, module['course_dir'] + '/' + module['course'])
                               if self.get_body_argument('return', 'outline') == 'lesson' else self.course_links()['course'])
        except self.progress.ProgressError as exc:
            return self.progress_error(str(exc), 400)
        except (OSError, sqlite3.Error, ValueError):
            return self.progress_error()
        self.redirect(destination, status=303)


class ExportHandler(ProgressHandler):
    @web.authenticated
    def get(self):
        self.require_progress()
        try:
            data = self.store.export()
        except (OSError, sqlite3.Error, ValueError):
            return self.progress_error('The progress file could not be downloaded. Your record has not been reset.')
        self.set_header('Cache-Control', 'no-store')
        self.set_header('Content-Type', 'application/json')
        self.set_header('Content-Disposition', 'attachment; filename="doris-workshop-progress.json"')
        self.set_header('X-Content-Type-Options', 'nosniff')
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
                self.render_import(message=f'Import saved for {count} matching modules. Unknown or unselected modules were skipped. Return to the outline to see your progress.')
            else:
                files = self.request.files.get('file', [])
                if len(files) != 1:
                    raise self.progress.ProgressError('Choose one progress JSON file.')
                raw = files[0]['body']
                document = self.progress.validate_document(raw)
                rows = self.store.preview(document, self.modules)
                self.render_import(rows=rows, payload=json.dumps(document, separators=(',', ':')))
        except self.progress.ProgressError as exc:
            self.progress_error(str(exc), 400)
        except (OSError, sqlite3.Error, ValueError):
            self.progress_error()


def register(server, plan, source, progress_db=None):
    prefix = url_path_join(server.base_url, "workshop")
    options = {"plan": plan, "source": source, "progress_db": progress_db}
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
