"""Authenticated, read-only course navigation inside the existing Jupyter server."""

import mimetypes
from pathlib import Path
import posixpath
import re
import sys
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
    def initialize(self, plan, source):
        self.plan, self.source = plan, Path(source).resolve()
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


class IndexHandler(PortalHandler):
    @web.authenticated
    def get(self):
        courses = []
        for course in self.plan["courses"]:
            directory = course["course_dir"]
            modules = []
            for module in course.get("modules", []):
                lesson = content_url(self.base_url, directory + "/" + module["course"])
                lab = notebook_url(self.base_url, directory + "/" + module["lab"])
                modules.append({"title": module.get("title", module["id"]), "lesson": lesson, "lab": lab, "level": module.get("level")})
            courses.append({"title": course.get("title", directory.rsplit("/", 1)[-1]), "modules": modules, "levels": course.get("levels", {})})
        links = self.course_links()
        self.page(self.ui.course(courses, course_url=links["course"], notebooks=links["notebooks"], terminal=links["terminal"]))


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
            lab = next((notebook_url(self.base_url, course["course_dir"] + "/" + module["lab"])
                for course in self.plan["courses"] for module in course.get("modules", [])
                if path == course["course_dir"] + "/" + module["course"]), None)
            self.page(self.ui.lesson(title, render_markdown(source, path, self.base_url), lab=lab, **self.course_links()))
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


def register(server, plan, source):
    prefix = url_path_join(server.base_url, "workshop")
    options = {"plan": plan, "source": source}
    server.web_app.add_handlers(".*$", [
        (prefix + "/?", IndexHandler, options),
        (prefix + "/terminal", TerminalHandler, options),
        (prefix + "/help", HelpHandler, options),
        (prefix + "/static/([^/]+)", AssetHandler, options),
        (prefix + "/content/(.*)", ContentHandler, options),
    ])
