"""Authenticated, read-only course navigation inside the existing Jupyter server."""

from html import escape
import mimetypes
from pathlib import Path
import posixpath
import re
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from jupyter_server.base.handlers import JupyterHandler
from jupyter_server.utils import url_path_join
from markdown_it import MarkdownIt
from tornado import web


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

    def page(self, title, body):
        base = self.base_url
        self.set_header("Cache-Control", "no-store")
        self.set_header("Content-Security-Policy", "default-src 'self'; img-src 'self' https: data:; style-src 'unsafe-inline'; frame-ancestors 'self'")
        self.set_header("Content-Type", "text/html; charset=utf-8")
        self.finish('<!doctype html><html lang="en"><meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width,initial-scale=1">'
                    f'<title>{escape(title)}</title><style>'
                    'body{max-width:960px;margin:32px auto;padding:0 24px;font:17px/1.65 system-ui;color:#172b4d}'
                    'a{color:#1558b0}nav{display:flex;gap:24px;border-bottom:1px solid #ddd;padding-bottom:16px}'
                    'img{max-width:100%;height:auto}pre{overflow:auto;background:#f3f5f7;padding:16px}'
                    'table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:8px}'
                    'button{font:inherit;padding:8px 16px}code{overflow-wrap:anywhere}'
                    '</style><nav>'
                    f'<a href="{escape(url_path_join(base, "workshop/"))}">Course home</a>'
                    f'<a href="{escape(url_path_join(base, "lab"))}">Notebooks &amp; downloads</a>'
                    f'<a href="{escape(url_path_join(base, "workshop/terminal"))}">Web Terminal</a></nav>'
                    f'<main><h1>{escape(title)}</h1>{body}</main></html>')


class IndexHandler(PortalHandler):
    @web.authenticated
    def get(self):
        body = '<p>Read a lesson, then open its Lab. Save or download your files in JupyterLab.</p>'
        for course in self.plan["courses"]:
            directory = course["course_dir"]
            body += f'<h2>{escape(course.get("title", directory.rsplit("/", 1)[-1]))}</h2><p>Kernel: <code>{escape(course["kernel"])}</code></p><ol>'
            for module in course.get("modules", []):
                lesson = content_url(self.base_url, directory + "/" + module["course"])
                lab = notebook_url(self.base_url, directory + "/" + module["lab"])
                body += f'<li><a href="{escape(lesson)}">{escape(module.get("title", module["id"]))}</a> · <a href="{escape(lab)}">Open Lab</a></li>'
            body += '</ol>'
        body += f'<p>Course version: <code>{escape(self.plan["course_ref"])}</code></p>'
        self.page("Apache Doris workshop", body)


class ContentHandler(PortalHandler):
    @web.authenticated
    def get(self, path):
        target = (self.source / path).resolve()
        allowed = any(target.is_relative_to((self.source / c["course_dir"]).resolve()) for c in self.plan["courses"])
        if not allowed or not target.is_file() or any(p.startswith(".") for p in Path(path).parts):
            raise web.HTTPError(404)
        if target.suffix == ".md":
            self.page(target.stem, render_markdown(target.read_text(), path, self.base_url))
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
        self.page("Web Terminal", '<p>Open a shell on your workshop instance.</p>'
                  '<form method="post">' + self.xsrf_form_html() + '<button>Open terminal</button></form>')

    @web.authenticated
    def post(self):
        terminal = self.settings["terminal_manager"].create()
        self.redirect(url_path_join(self.base_url, "lab/terminals", terminal["name"]))


def register(server, plan, source):
    prefix = url_path_join(server.base_url, "workshop")
    options = {"plan": plan, "source": source}
    server.web_app.add_handlers(".*$", [
        (prefix + "/?", IndexHandler, options),
        (prefix + "/terminal", TerminalHandler, options),
        (prefix + "/content/(.*)", ContentHandler, options),
    ])
