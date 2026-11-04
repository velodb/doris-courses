"""Build the approved A UI preview. Read course sources, never modify or duplicate them."""
from pathlib import Path
import base64
import hashlib
from html import escape
import json
import mimetypes
import re
import subprocess
from urllib.parse import unquote, urlsplit

import yaml
from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
COURSE = REPO / 'doris-course/01-real-time-analytics'
manifest = yaml.safe_load((COURSE / 'course.yaml').read_text())
renderer = MarkdownIt('commonmark', {'html': False}).enable('table')
image_cache = {}


def embed(path):
    path = path.resolve()
    if path not in image_cache:
        if not path.is_relative_to(REPO):
            raise ValueError('Preview assets must belong to this repository')
        mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        image_cache[path] = f'data:{mime};base64,' + base64.b64encode(path.read_bytes()).decode()
    return image_cache[path]


module_paths = [(COURSE / module['course']).resolve() for module in manifest['modules']]
lab_paths = [(COURSE / module['lab']).resolve() for module in manifest['modules']]


def lesson(module):
    source = COURSE / module['course']
    tokens = renderer.parse(source.read_text())
    # The UI owns the page heading; the lesson owns all subsequent content.
    if tokens[0].type == 'heading_open' and tokens[0].tag == 'h1':
        tokens = tokens[3:]
    toc, used = [], {}
    for i, token in enumerate(tokens):
        if token.type == 'heading_open':
            title = tokens[i + 1].content
            slug = re.sub(r'[^\w\- ]', '', title.lower()).replace(' ', '-')
            count = used.get(slug, 0)
            used[slug] = count + 1
            slug += f'-{count}' if count else ''
            token.attrSet('id', slug)
            token.attrSet('tabindex', '-1')
            if token.tag == 'h2':
                toc.append({'id': slug, 'title': title})

    def rewrite(items):
        for token in items:
            if token.type == 'image':
                src = token.attrGet('src')
                if urlsplit(src).scheme:
                    raise ValueError('Offline preview cannot load a remote course image')
                token.attrSet('src', embed(source.parent / unquote(src)))
                token.attrSet('loading', 'lazy')
            if token.type == 'link_open':
                href = token.attrGet('href')
                parts = urlsplit(href)
                target = (source.parent / unquote(parts.path)).resolve() if parts.path and not parts.scheme else None
                if not parts.path and parts.fragment:
                    token.attrSet('href', '#')
                    token.attrSet('data-section', parts.fragment)
                elif target in module_paths:
                    token.attrSet('href', f'#lesson/{module_paths.index(target) + 1}')
                    token.attrSet('data-route', f'lesson/{module_paths.index(target) + 1}')
                elif target == (COURSE / 'README.md').resolve():
                    token.attrSet('href', '#course')
                    token.attrSet('data-route', 'course')
                else:
                    label = f'Lab {lab_paths.index(target)+1}' if target in lab_paths else 'Course reference' if parts.scheme else Path(parts.path).name
                    token.attrSet('href', '#external')
                    token.attrSet('data-route', 'external')
                    token.attrSet('data-tool', label)
                    token.attrSet('data-target', href if parts.scheme else str(target.relative_to(REPO)) if target and target.is_relative_to(REPO) else href)
            if token.children:
                rewrite(token.children)
    rewrite(tokens)
    return {
        'title': module['title'], 'summary': module['summary'], 'level': module['level'],
        'id': module['id'], 'lesson_path': module['course'], 'lab_path': module['lab'],
        'html': renderer.renderer.render(tokens, renderer.options, {}), 'toc': toc,
        'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
    }


data = {
    'schema_version': 1,
    'mode': 'design-preview',
    'activity': {'id': 'demo-rta', 'course_title': manifest['title'], 'profile': 'rta-main'},
    'assignment': {'learner_id': 's001', 'service_ids': ['jupyter', 'doris', 'fe1', 'fe2', 'fe3']},
    'course': {'title': manifest['title'], 'description': manifest['description'], 'modules': [lesson(m) for m in manifest['modules']]},
    'assets': {'logo': embed(ROOT / 'assets/doris-trimmed.svg'), 'jupyter': embed(ROOT / 'assets/jupyter.svg')},
}
font = embed(ROOT / 'assets/manrope.ttf')
css = f"@font-face{{font-family:Manrope;src:url('{font}') format('truetype');font-weight:200 800;font-display:swap}}\n" + (ROOT / 'src/styles.css').read_text()
script = (ROOT / 'src/prototype.js').read_text()
copy = json.loads((ROOT / 'src/copy.json').read_text())
payload = json.dumps({'data': data, 'copy': copy}, ensure_ascii=False).replace('<', '\\u003c')
license_text = (ROOT / 'assets/manrope-OFL.txt').read_text()
html = f'''<!doctype html>
<!-- A: user-selected Lab Manual. Design only; demo-rta / s001 are examples.
No live authentication or resource access. Course sources are read at build time.
Only visual density and font size preferences may be saved locally.
Font license: {license_text} -->
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none';img-src data:;font-src data:;style-src 'unsafe-inline';script-src 'unsafe-inline';connect-src 'none';form-action 'none';base-uri 'none'">
<title>Apache Doris Workshop / A</title><style>{css}</style></head><body><div id="app"></div>
<div class="sr-only" id="announcement" role="status" aria-live="polite"></div>
<noscript>This offline design preview needs JavaScript to switch pages.</noscript>
<script id="preview-input" type="application/json">{payload}</script><script>{script}</script></body></html>'''
(ROOT / 'dist').mkdir(exist_ok=True)
(ROOT / 'dist/prototype.html').write_text(html)
components = (ROOT / 'src/components.html').read_text()
(ROOT / 'dist/components.html').write_text(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none';font-src data:;style-src 'unsafe-inline';img-src data:;base-uri 'none';form-action 'none'"><title>A / UI components</title><style>{css}</style></head><body>{components}</body></html>''')
(ROOT / 'review/selected/source-manifest.json').write_text(json.dumps({
    'source_ref': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
    'course_manifest': str((COURSE / 'course.yaml').relative_to(REPO)),
    'modules': [{'id': m['id'], 'source': m['lesson_path'], 'sha256': m['sha256']} for m in data['course']['modules']],
    'embedded_images': len(image_cache) - 3,
    'output': 'dist/prototype.html', 'output_sha256': hashlib.sha256(html.encode()).hexdigest(),
}, indent=2) + '\n')
print(f'Built A: {len(data["course"]["modules"])} real lessons, {len(image_cache)-3} course images, {len(html):,} bytes')
