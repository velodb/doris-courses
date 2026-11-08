"""One-time migration of an admitted translation package to editable course files.

This is migration tooling, not a builder input or an export/import authoring API.
Normal authoring uses the resulting Markdown, Notebook and YAML files directly.
"""

import argparse
from collections import Counter, defaultdict
from html import escape, unescape
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import subprocess
import tempfile
from urllib.parse import unquote, urlsplit, urlunsplit

from markdown_it import MarkdownIt
import yaml


ROOT = Path(__file__).resolve().parents[1]
LOCALE = 'kk-Cyrl'
PREFIX = f'doris-course/{LOCALE}'
COURSES = ('01-real-time-analytics', '02-data-warehousing', '03-observability')
PARSER = MarkdownIt('commonmark')
IMAGES = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def text_of(cell):
    return ''.join(cell['source']) if isinstance(cell['source'], list) else cell['source']


def yaml_bytes(document):
    return yaml.safe_dump(document, allow_unicode=True, sort_keys=False, width=100).encode()


def slug(title):
    return re.sub(r'[^\w\- ]', '', title.lower()).replace(' ', '-')


def heading_rows(text):
    tokens = PARSER.parse(text)
    return [(token.map[0], tokens[i + 1].content) for i, token in enumerate(tokens)
            if token.type == 'heading_open']


def heading_aliases(text, counts):
    result = []
    for line, title in heading_rows(text):
        name = slug(title)
        index = counts[name]
        counts[name] += 1
        result.append((line, name + (f'-{index}' if index else '')))
    return result


def anchored(source, target, unit_anchor, counts):
    """Materialize old identities once; future edits need no English alignment."""
    aliases = heading_aliases(source, counts)
    target_headings = heading_rows(target)
    if len(aliases) != len(target_headings):
        raise ValueError('Migration input has a different heading count')
    existing = set(re.findall(r'\bid=["\']([^"\']+)["\']', target))
    lines = target.splitlines(keepends=True)
    for (_, alias), (line, _) in reversed(list(zip(aliases, target_headings))):
        if alias not in existing:
            lines.insert(line, f'<a id="{alias}"></a>\n\n')
            existing.add(alias)
    if unit_anchor:
        if unit_anchor in existing:
            raise ValueError('Migration input already contains its unit anchor')
        lines.insert(0, f'<a id="{unit_anchor}"></a>\n\n')
    return ''.join(lines)


def protected_spans(text):
    """Keep examples as examples when relocating actual Markdown/HTML links."""
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    spans = [(offsets[t.map[0]], offsets[t.map[1]]) for t in PARSER.parse(text)
             if t.type in {'fence', 'code_block'}]
    spans += [(m.start(), m.end()) for m in re.finditer(r'(`+)([^`]*?)\1', text)]
    return spans


def relocated_url(value, original):
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or not parts.path or parts.path.startswith('/'):
        return value
    old_target = posixpath.normpath(posixpath.join(posixpath.dirname(original), unquote(parts.path)))
    new_source = original.replace('doris-course/', PREFIX + '/', 1)
    if any(old_target == f'doris-course/{c}' or old_target.startswith(f'doris-course/{c}/') for c in COURSES):
        new_target = old_target.replace('doris-course/', PREFIX + '/', 1)
    else:
        # Common code, repository documentation and untranslated AI stay outside
        # the localized tree. Never manufacture a localized missing destination.
        new_target = old_target
    relative = posixpath.relpath(new_target, posixpath.dirname(new_source))
    return urlunsplit(('', '', relative, parts.query, parts.fragment))


def relocate_links(text, original):
    spans = protected_spans(text)

    def allowed(start):
        return not any(a <= start < b for a, b in spans)

    changes = []
    for match in re.finditer(r'!?\[[^\]\n]*\]\(([^\s)]+)\)', text):
        if allowed(match.start()):
            changes.append((match.start(1), match.end(1), relocated_url(match[1], original)))
    for tag in re.finditer(r'<(?:img|a)\b[^>]*>', text, re.I):
        if not allowed(tag.start()):
            continue
        for attr in re.finditer(r'\b(?:src|href)\s*=\s*(["\'])(.*?)\1', tag[0], re.I):
            start, end = tag.start() + attr.start(2), tag.start() + attr.end(2)
            old = unescape(attr[2])
            new = relocated_url(old, original)
            if new != old:
                changes.append((start, end, escape(new, quote=True)))
    for start, end, value in sorted(changes, reverse=True):
        text = text[:start] + value + text[end:]
    if PurePosixPath(original).name == 'README.md':
        # These are setup instructions, not Notebook code or public dataset URLs.
        text = text.replace('`../common`', '`../../common`')
        for course in COURSES:
            text = text.replace(f'cd doris-course/{course}', f'cd {PREFIX}/{course}')
    return text


def ordered_like(original, translated):
    if isinstance(original, dict):
        if set(original) != set(translated):
            raise ValueError('Translated metadata changed its schema')
        return {key: ordered_like(value, translated[key]) for key, value in original.items()}
    if isinstance(original, list):
        if len(original) != len(translated):
            raise ValueError('Translated metadata changed its list length')
        return [ordered_like(a, b) for a, b in zip(original, translated)]
    return translated


def frozen_inputs(package, repo=ROOT):
    tree = Path(package).resolve() / 'tree'
    manifest = json.loads((tree / 'package.v2.json').read_text())
    if manifest['package_state'] != 'release' or LOCALE not in manifest['available_locales']:
        raise ValueError('Migration requires an admitted complete Kazakh release package')
    for row in manifest['snapshot']['files']:
        data = (tree / row['path']).read_bytes()
        if len(data) != row['size'] or digest(data) != row['sha256']:
            raise ValueError('Migration package file changed: ' + row['path'])
    ref = manifest['snapshot']['source_ref']
    listed = subprocess.run(['git', 'ls-tree', '-r', '--full-tree', ref,
                             *(f'doris-course/{c}' for c in COURSES)], cwd=repo,
                            capture_output=True, text=True, check=True).stdout
    records = {}
    for line in listed.splitlines():
        info, name = line.split('\t', 1)
        mode, kind, blob = info.split()
        if kind != 'blob' or mode not in {'100644', '100755'}:
            raise ValueError('Migration accepts regular tracked source files only: ' + name)
        source_file = tree / name
        # Admitted teaching packages omit dotfiles such as .gitignore. They are
        # still tracked course sources and must be copied from the pinned tree.
        data = source_file.read_bytes() if source_file.is_file() else subprocess.run(
            ['git', 'show', f'{ref}:{name}'], cwd=repo, capture_output=True, check=True).stdout
        if not name.endswith('.ipynb') and hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() != blob:
            raise ValueError('Frozen source bytes differ from the pinned Git source: ' + name)
        records[name] = {'data': data, 'mode': int(mode[-3:], 8)}
    if {PurePosixPath(p).parts[1] for p in records} != set(COURSES):
        raise ValueError('Pinned source must contain all three complete courses')
    source_map = json.loads((tree / 'locales/source-map.json').read_text())['units']
    units = defaultdict(list)
    for unit in source_map:
        if unit['source_path'] in records:
            units[unit['source_path']].append(unit)
    translation = next(t for t in manifest['translations'] if t['locale'] == LOCALE)
    targets = {unit['unit_id']: unit['target_text'] for unit in translation['units']}
    anchors = {row['unit_id']: row['anchor'] for row in json.loads((tree / 'indexes/anchors.json').read_text())}
    return tree, manifest, records, units, targets, anchors


def materialize_file(name, frozen, units, targets, anchors, tree):
    original = frozen['data']
    localized = tree / 'rendered' / LOCALE / name
    if name.endswith('/course.yaml'):
        translated = json.loads(localized.with_name('course.json').read_text())
        translated = ordered_like(yaml.safe_load(original), translated)
        translated['language'] = LOCALE
        return yaml_bytes(translated)
    if name.endswith('.md'):
        source = original.decode()
        rows = [u for u in units if u['locator']['kind'] == 'markdown-block']
        rows.sort(key=lambda u: source.index(u['source_text']))
        if not rows or ''.join(u['source_text'] for u in rows) != source:
            raise ValueError('Markdown migration must cover the complete frozen file: ' + name)
        if ''.join(targets[u['id']] for u in rows) != localized.read_text():
            raise ValueError('Rendered translation does not match its exact migration units: ' + name)
        counts = Counter()
        text = ''.join(anchored(u['source_text'], targets[u['id']], anchors[u['id']], counts) for u in rows)
        return (relocate_links(text, name).rstrip('\n') + '\n').encode()
    if name.endswith('.ipynb'):
        source = json.loads(original)
        translated = json.loads(localized.read_text())
        if [(c['id'], c['cell_type']) for c in source['cells']] != [(c['id'], c['cell_type']) for c in translated['cells']]:
            raise ValueError('Notebook cell identity changed: ' + name)
        by_cell = {u['locator']['value']: u for u in units if u['locator']['kind'] == 'notebook-cell'}
        counts = Counter()
        for before, cell in zip(source['cells'], translated['cells']):
            if cell['cell_type'] == 'code':
                if text_of(before) != text_of(cell):
                    raise ValueError('Notebook code changed: ' + name)
                continue
            if cell['cell_type'] != 'markdown':
                continue
            unit = by_cell.get(cell['id'])
            if text_of(cell).strip() and unit is None:
                raise ValueError('Markdown cell lacks its historical identity: ' + name)
            text = anchored(text_of(before), text_of(cell), anchors[unit['id']] if unit else None, counts)
            cell['source'] = relocate_links(text, name).splitlines(keepends=True)
        return (json.dumps(translated, ensure_ascii=False, indent=1) + '\n').encode()
    if name.endswith('.yaml') and PurePosixPath(name).name.startswith('quiz'):
        # The remaining translated course files are the native Quiz YAML banks.
        return localized.read_bytes()
    if name.endswith('/requirements.txt'):
        return original.replace(b'-e ../common', b'-e ../../common')
    return original


class HtmlLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        self.urls += [value for key, value in attrs if key in {'src', 'href'} and value is not None]


def links(text):
    result = []

    def walk(tokens):
        for token in tokens:
            if token.type in {'link_open', 'image'}:
                result.append(token.attrGet('href' if token.type == 'link_open' else 'src'))
            if token.type in {'html_block', 'html_inline'}:
                html = HtmlLinks()
                html.feed(token.content)
                result.extend(html.urls)
            if token.children:
                walk(token.children)
    walk(PARSER.parse(text))
    return result


def document_text(path):
    if path.suffix == '.ipynb':
        return '\n'.join(text_of(c) for c in json.loads(path.read_text())['cells'] if c['cell_type'] == 'markdown')
    return path.read_text()


def fragment_ids(text):
    return set(re.findall(r'\bid=["\']([^"\']+)["\']', text)) | {name for _, name in heading_aliases(text, Counter())}


def quiz_identity(document):
    return [(q['id'], q['type'], tuple(o['id'] for o in q['options']), q['answer']) for q in document['questions']]


def clear_pointer(document, pointer):
    parts = [p.replace('~1', '/').replace('~0', '~') for p in pointer.lstrip('/').split('/')]
    current = document
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    if isinstance(current, list):
        current[int(parts[-1])] = None
    else:
        current[parts[-1]] = None


def audit_source_tree(output, package, repo=ROOT):
    """Read-only, repeatable migration parity and native-link verification."""
    output, repo = Path(output).resolve(), Path(repo).resolve()
    tree, manifest, records, units, targets, anchors = frozen_inputs(package, repo)
    expected = {p.removeprefix('doris-course/') for p in records}
    actual = {p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()}
    if actual != expected | {'catalog.yaml', 'ui.yaml', 'README.md'}:
        raise ValueError('Localized tree must exactly mirror every tracked course file plus its three root files')
    counts = Counter()
    for original, frozen in records.items():
        path = output / original.removeprefix('doris-course/')
        data = path.read_bytes()
        wanted = materialize_file(original, frozen, units[original], targets, anchors, tree)
        if data != wanted:
            raise ValueError('Migration parity failed: ' + str(path))
        counts['tracked_course_files'] += 1
        if path.suffix in IMAGES:
            if data != frozen['data']:
                raise ValueError('Source image pixels changed: ' + str(path))
            counts['unchanged_images'] += 1
        if path.suffix == '.ipynb':
            counts['notebooks'] += 1
            old = json.loads(frozen['data'])
            new = json.loads(data)
            for a, b in zip(old['cells'], new['cells']):
                if (a['id'], a['cell_type']) != (b['id'], b['cell_type']):
                    raise ValueError('Notebook identity changed')
                if a['cell_type'] == 'code' and text_of(a) != text_of(b):
                    raise ValueError('Notebook code changed')
                counts['unchanged_code_cells'] += a['cell_type'] == 'code'
        if path.suffix == '.yaml' and path.name.startswith('quiz'):
            before, after = yaml.safe_load(frozen['data']), yaml.safe_load(data)
            if quiz_identity(before) != quiz_identity(after):
                raise ValueError('Quiz question/option/answer identity changed')
            counts['quiz_banks'] += 1
        if path.suffix not in {'.md', '.ipynb'}:
            continue
        text = document_text(path)
        explicit = re.findall(r'\bid=["\']([^"\']+)["\']', text)
        if len(explicit) != len(set(explicit)):
            raise ValueError('Duplicate explicit anchor: ' + str(path))
        old_texts = [frozen['data'].decode()] if path.suffix == '.md' else [
            text_of(c) for c in json.loads(frozen['data'])['cells'] if c['cell_type'] == 'markdown']
        alias_counts = Counter()
        for old_text in old_texts:
            for _, alias in heading_aliases(old_text, alias_counts):
                if alias not in explicit:
                    raise ValueError('Missing saved English heading alias: ' + str(path) + '#' + alias)
                counts['saved_english_heading_aliases'] += 1
        for unit in units[original]:
            if unit['locator']['kind'] in {'markdown-block', 'notebook-cell'}:
                if anchors[unit['id']] not in explicit:
                    raise ValueError('Missing historical unit anchor: ' + unit['id'])
                counts['historical_unit_anchors'] += 1
        for url in links(text):
            parts = urlsplit(url)
            if parts.scheme or parts.netloc or parts.path.startswith('/'):
                continue
            canonical_source = PREFIX + '/' + path.relative_to(output).as_posix()
            canonical_target = posixpath.normpath(posixpath.join(posixpath.dirname(canonical_source), unquote(parts.path))) if parts.path else canonical_source
            target = output / canonical_target.removeprefix(PREFIX + '/') if canonical_target.startswith(PREFIX + '/') else repo / canonical_target
            if not target.is_file():
                raise ValueError(f'Broken relative link: {path}: {url}')
            if parts.fragment and target.suffix in {'.md', '.ipynb'} and unquote(parts.fragment) not in fragment_ids(document_text(target)):
                raise ValueError(f'Broken relative fragment: {path}: {url}')
            counts['relative_links_and_fragments'] += 1
        counts['markdown_readers'] += path.suffix == '.md'
    catalog = yaml.safe_load((output / 'catalog.yaml').read_text())
    if catalog['courses'] != [f'{PREFIX}/{c}/course.yaml' for c in COURSES] or catalog['programs']:
        raise ValueError('Localized catalog must select only the three available courses without program credit')
    for course in COURSES:
        name = f'doris-course/{course}/course.yaml'
        metadata = yaml.safe_load((output / course / 'course.yaml').read_text())
        if metadata['language'] != LOCALE:
            raise ValueError('Localized metadata needs its actual source language')
        original = yaml.safe_load(records[name]['data'])
        comparable = json.loads(json.dumps(metadata))
        original['language'] = LOCALE
        for unit in units[name]:
            if unit['locator']['kind'] == 'json-pointer':
                clear_pointer(original, unit['locator']['value'])
                clear_pointer(comparable, unit['locator']['value'])
        if original != comparable:
            raise ValueError('Course technical metadata, identities or credit policy changed: ' + course)
        for relative in [metadata['lab_setup']['requirements'], metadata['lab_setup']['guide']]:
            if not (output / course / relative).is_file():
                raise ValueError('Missing native course setup file: ' + relative)
        for module in metadata['modules']:
            for key in ('course', 'lab', 'quiz'):
                if not (output / course / module[key]).is_file():
                    raise ValueError('Missing native module file: ' + module[key])
            for part in module.get('optional', []):
                if not (output / course / part['path']).is_file():
                    raise ValueError('Missing native optional file: ' + part['path'])
        requirements = (output / course / 'requirements.txt').read_text()
        if '-e ../../common\n' not in requirements or '-e ../common\n' in requirements:
            raise ValueError('Course must use the single existing shared common package')
        counts['primary_modules'] += len(metadata['modules'])
        counts['optional_assets'] += sum(len(m.get('optional', [])) for m in metadata['modules'])
    ui = yaml.safe_load((output / 'ui.yaml').read_text())
    translated_ui = json.loads((tree / f'rendered/{LOCALE}/locales/ui.en.json').read_text())['messages']
    if ui != translated_ui or not all(isinstance(k, str) and isinstance(v, str) for k, v in ui.items()):
        raise ValueError('ui.yaml must contain only the original translated message mapping')
    english_ui = json.loads((tree / 'locales/ui.en.json').read_text())['messages']
    placeholders = lambda value: Counter(re.findall(r'\{[A-Za-z_][A-Za-z0-9_.]*\}', value))
    if set(ui) != set(english_ui) or any(placeholders(ui[k]) != placeholders(english_ui[k]) for k in ui):
        raise ValueError('UI message placeholders changed')
    counts['ui_messages'] = len(ui)
    return {'passed': True, 'source_ref': manifest['snapshot']['source_ref'], 'source_locale': LOCALE,
            'source_package_digest': manifest['snapshot']['package_digest'], **dict(counts),
            'shared_common': 'doris-course/common (not duplicated)',
            'editing_source': 'native Markdown, Notebook and YAML; no overlay required'}


README = '''# Kazakh course sources

Edit these course files directly. Each course has its own Markdown lessons, Jupyter
Notebooks, Quiz YAML, course manifest, helper code, requirements, environments,
data, tests and images. The three course directories mirror the English course
layout. `catalog.yaml` selects these three courses using repository-relative paths.
`ui.yaml` is a plain message-key to text mapping.

There is no translated AI course here. The catalog declares no program completion
qualification; the complete program still requires the English catalog's fourth
course. Links to untranslated AI material explicitly lead to its English source.

Keep the repository together. Course requirements install the single shared
`doris-course/common` package through `-e ../../common`; each course has its own
virtual environment. Notebook code cells and Quiz question, option and answer IDs
retain their original identity. Course helper paths continue to discover their
own course root.

Historical `unit-*` anchors and English heading aliases are ordinary HTML anchors
saved in Markdown and Notebook Markdown cells. Preserve these IDs when editing a
section so existing links keep working. New authoring and rendering need no
English paragraph matching, heading-count matching, or translation JSON overlay.

Existing English PNG pixels are retained. Kazakh alt text, captions and complete
adjacent label mappings explain the diagrams; the image pixels were not translated.
The text was translated and self-checked by the session LLM, with technical checks,
and has not received native human language review.

The one-time migration tool is `tools/migrate_course_translations.py`. It consumes
the historical admitted package, writes a **new** output directory, and refuses to
overwrite an existing source tree. `--verify-only` reads and audits a migration
without writing. The tool and the historical package are not build dependencies
and are not an export/import editing workflow.

To replay the historical migration into a new directory:

```bash
python tools/migrate_course_translations.py \\
  --package /path/to/historical-package --output /tmp/new-kk-Cyrl-source
python tools/migrate_course_translations.py \\
  --package /path/to/historical-package --output /tmp/new-kk-Cyrl-source --verify-only
python -m unittest discover -s tools/tests -p test_migrate_course_translations.py -v
```

Use an environment with `tools/requirements.txt`. Historical package replay tests
run when that package is available, or when `DORIS_MIGRATION_PACKAGE` points to it.
The native source checks and normal course authoring need no historical package.
'''


def migrate(package, output, repo=ROOT):
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError('Migration output already exists; existing course sources are never overwritten')
    tree, manifest, records, units, targets, anchors = frozen_inputs(package, repo)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.course-migration-', dir=output.parent))
    try:
        for name, frozen in records.items():
            target = staging / name.removeprefix('doris-course/')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(materialize_file(name, frozen, units[name], targets, anchors, tree))
            target.chmod(frozen['mode'])
        catalog = json.loads((tree / f'rendered/{LOCALE}/catalog.json').read_text())
        catalog['courses'] = [f'{PREFIX}/{c}/course.yaml' for c in COURSES]
        catalog['programs'] = []
        (staging / 'catalog.yaml').write_bytes(yaml_bytes(catalog))
        ui = json.loads((tree / f'rendered/{LOCALE}/locales/ui.en.json').read_text())['messages']
        (staging / 'ui.yaml').write_bytes(yaml_bytes(ui))
        (staging / 'README.md').write_text(README)
        report = audit_source_tree(staging, package, repo)
        staging.rename(output)
        return report
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New locale source directory; never overwritten')
    parser.add_argument('--repo', type=Path, default=ROOT)
    parser.add_argument('--verify-only', action='store_true', help='Read-only migration parity audit of an existing output')
    args = parser.parse_args(argv)
    if args.verify_only:
        report = audit_source_tree(args.output, args.package, args.repo)
    else:
        report = migrate(args.package, args.output, args.repo)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
