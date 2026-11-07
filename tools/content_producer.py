"""Deterministic, offline producer for the pinned multilingual teaching contract.

Only this installed code executes. Git content, overlays and configuration are
read as data. No source hook, Lab, dependency installer or translator is run.
"""
from __future__ import annotations

import ast
import copy
import gzip
from html.parser import HTMLParser
import io
import posixpath
import importlib.util
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
from urllib.parse import urlsplit
import uuid

from markdown_it import MarkdownIt
import yaml

import content_contract_v2 as c

VERSION = 'ml02-1'
CONFIG = 'locales/producer.json'
MAP = 'locales/source-map.json'
GLOSSARY = 'locales/glossary.json'
UI = 'locales/ui.en.json'
UI_CONTRACT = 'locales/ui.contract.json'
MESSAGE_ENGINE = 'doris-course/common/doris_labkit/messages.py'
_spec = importlib.util.spec_from_file_location('producer_messages', Path(__file__).resolve().parents[1] / MESSAGE_ENGINE)
_messages = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_messages)
IMAGE = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'}
PARSER = MarkdownIt('commonmark').enable('table')


def validate_messages(locale, source, target, contract):
    try:
        _messages.Messages(locale, source=source, messages=target, contract=contract)
    except (ValueError, KeyError, TypeError) as error:
        c.fail('invalid UI messages/placeholder contract: ' + str(error))


def checked_path(path):
    if not isinstance(path, str) or not c.PATH.fullmatch(path) or path.startswith('/'):
        c.fail(f'non-canonical producer path: {path}')
    if any(part in {'.', '..'} for part in path.split('/')):
        c.fail(f'escaping producer path: {path}')
    return path


def json_bytes(value):
    return c.canonical(value) + b'\n'


class GitSource:
    """Read an exact commit, without checking it out or consulting dirty files."""
    def __init__(self, repo, ref):
        self.repo = Path(repo).resolve()
        if not re.fullmatch('[0-9a-f]{40}', ref):
            c.fail('source-ref must be a full 40-character commit SHA')
        actual = self.git('rev-parse', '--verify', ref + '^{commit}').decode().strip()
        if actual != ref:
            c.fail('source-ref is not the selected commit')
        self.ref = ref
        self.entries = {}
        for row in self.git('ls-tree', '-rz', ref).split(b'\0'):
            if not row:
                continue
            meta, path = row.split(b'\t', 1)
            mode, kind, blob = meta.decode().split()
            self.entries[path.decode()] = (mode, kind, blob)
        self.cache = {}

    def git(self, *args):
        result = subprocess.run(['git', '-C', str(self.repo), *args], capture_output=True)
        if result.returncode:
            c.fail(result.stderr.decode(errors='replace').strip())
        return result.stdout

    def paths(self):
        return sorted(self.entries)

    def read(self, path):
        checked_path(path)
        if path not in self.entries:
            c.fail(f'file is absent from selected Git commit: {path}')
        mode, kind, blob = self.entries[path]
        if mode not in {'100644', '100755'} or kind != 'blob':
            c.fail(f'non-regular Git content: {path}')
        if path not in self.cache:
            size = int(self.git('cat-file', '-s', blob))
            if size > c.MAX_FILE_BYTES:
                c.fail(f'file exceeds 16 MiB: {path}')
            self.cache[path] = self.git('cat-file', 'blob', blob)
        return self.cache[path]


def verify_revision_history(source):
    """A revision cannot be rebound to different meaning anywhere in this SHA's history."""
    seen = {}
    commits = source.git('log', '--format=%H', source.ref, '--', CONFIG).decode().splitlines()
    for commit in commits:
        row = source.git('ls-tree', '-z', commit, '--', CONFIG)
        if not row:  # A deletion retains the earlier binding in history.
            continue
        blob = row.split(b'\t', 1)[0].decode().split()[2]
        if int(source.git('cat-file', '-s', blob)) > c.MAX_FILE_BYTES:
            c.fail('historical producer config exceeds size limit')
        config = c.read_json(source.git('cat-file', 'blob', blob))
        validate_config(config)
        for domain in ('policy_revisions', 'quiz_revisions'):
            for identity, binding in config[domain].items():
                key = domain, identity, binding['revision']
                if key in seen and seen[key] != binding['payload_digest']:
                    c.fail('immutable revision reused with different meaning in Git history: ' + binding['revision'])
                seen[key] = binding['payload_digest']


def load(data, path):
    if path.endswith(('.yaml', '.yml')):
        if any(isinstance(t, (yaml.AliasToken, yaml.AnchorToken)) for t in yaml.scan(data)):
            c.fail('YAML aliases and anchors are not producer data')
        return c.bounded(c.v1.load(data.decode()))
    return c.read_json(data)


def strip_legacy_styles(text):
    """Remove HTML style attributes while preserving fenced/inline code bytes."""
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    protected = [(offsets[t.map[0]], offsets[t.map[1]]) for t in PARSER.parse(text)
                 if t.type in {'fence', 'code_block'}]
    protected.extend((m.start(), m.end()) for m in re.finditer(r'(`+)(.*?)\1', text, re.S))
    chunks, end = [], 0
    for match in re.finditer(r'<[A-Za-z][^>]*>', text):
        if any(a <= match.start() < b for a, b in protected):
            continue
        tag = re.sub(r"\sstyle\s*=\s*(?:\"[^\"]*\"|'[^']*')", '', match.group(), flags=re.I)
        chunks.extend([text[end:match.start()], tag]); end = match.end()
    chunks.append(text[end:])
    return ''.join(chunks)


def normalized_notebook(data):
    """Drop legacy style attributes in Markdown only; all other fields survive.

    The contract still rejects every other unsafe HTML attribute/element. Raw
    Git byte hashes and this named transform are retained in source-inputs.json.
    """
    notebook = load(data, 'notebook.ipynb')
    original = copy.deepcopy(notebook)
    for cell in notebook['cells']:
        if cell['cell_type'] == 'markdown':
            source = ''.join(cell['source']) if isinstance(cell['source'], list) else cell['source']
            # A narrowly scoped legacy transform, never a general HTML sanitizer.
            source = strip_legacy_styles(source)
            if source != (''.join(cell['source']) if isinstance(cell['source'], list) else cell['source']):
                cell['source'] = source.splitlines(keepends=True)
        elif cell['cell_type'] == 'code':
            if cell.get('outputs') or cell.get('execution_count') is not None:
                c.fail('source Notebooks require cleared outputs and execution counts')
    return data if notebook == original else json_bytes(notebook)


def course_index(source):
    catalog = load(source.read('catalog.yaml'), 'catalog.yaml')
    c.shape(catalog, 'catalog')
    courses = {}
    for path in catalog['courses']:
        checked_path(path)
        course = load(source.read(path), path)
        c.shape(course, 'course')
        if course['slug'] in courses:
            c.fail('duplicate course identity')
        courses[course['slug']] = (path, course)
    return catalog, courses


def learning_paths(source, courses):
    """Discover teaching files independently of the hand-maintained source map."""
    paths = set()
    for manifest, course in courses.values():
        base = posixpath.dirname(manifest)
        paths.add(base + '/' + course['lab_setup']['guide'])
        for path in source.paths():
            if path.startswith(base + '/level') and path.endswith(('.md', '.ipynb')):
                paths.add(path)
        for module in course['modules']:
            for path in [module.get(k) for k in ('course', 'lab', 'quiz')] + [
                    p.get(k) for p in module.get('optional', []) for k in ('path', 'quiz')]:
                if path:
                    paths.add(base + '/' + path)
    return sorted(paths)


def scope_for(path, courses):
    for slug, (manifest, course) in courses.items():
        base = posixpath.dirname(manifest)
        if path == manifest or path.startswith(base + '/'):
            if path in {manifest, base + '/' + course['lab_setup']['guide']}:
                return {'course_slug': slug, 'module_id': None}
            for module in course['modules']:
                owned = [module.get(k) for k in ('course', 'lab', 'quiz')] + [
                    part.get(k) for part in module.get('optional', []) for k in ('path', 'quiz')]
                if path in {base + '/' + p for p in owned if p}:
                    return {'course_slug': slug, 'module_id': module['id']}
            for module in course['modules']:
                lesson = module.get('course')
                if lesson and path.startswith(posixpath.dirname(base + '/' + lesson) + '/'):
                    return {'course_slug': slug, 'module_id': module['id']}
            return {'course_slug': slug, 'module_id': None}
    return {'course_slug': None, 'module_id': None}


def markdown_blocks(text):
    """Partition by sections, retaining every byte, with unique text selectors.

    Repeated headings are fine when their sections differ. Duplicate sections
    are merged with a neighbour until unambiguous. A later split/merge requires
    explicit author mapping and is never guessed from an array position.
    """
    lines = text.splitlines(keepends=True)
    starts = sorted({0, *[t.map[0] for t in PARSER.parse(text) if t.type == 'heading_open']})
    blocks = [''.join(lines[a:b]) for a, b in zip(starts, starts[1:] + [len(lines)])]
    blocks = [b for b in blocks if b]
    i = 0
    while i < len(blocks):
        if text.count(blocks[i]) != 1:
            if len(blocks) == 1:
                c.fail('ambiguous Markdown structure')
            start = i if i + 1 < len(blocks) else i - 1
            blocks[start:start + 2] = [''.join(blocks[start:start + 2])]
            i = max(0, start - 1)
        else:
            i += 1
    return blocks


def candidates(source):
    """Return all allowed leaves, with semantic handles for structured arrays."""
    catalog, courses = course_index(source)
    result = []

    def add(path, kind, locator, text, handle):
        if not text:
            return
        result.append({'handle': path + '::' + handle, 'scope': scope_for(path, courses),
                       'source_path': path, 'locator': {'kind': kind, 'value': locator},
                       'source_text': text, 'source_hash': c.sha256(text.encode())})

    def leaf(path, pointer, text, handle=None):
        add(path, 'json-pointer', pointer, text, handle or pointer)

    for key in ('title', 'description'):
        leaf('catalog.yaml', '/' + key, catalog[key])
    for i, program in enumerate(catalog['programs']):
        leaf('catalog.yaml', f'/programs/{i}/title', program['title'], f'program.{program["id"]}.title')
    for manifest, course in courses.values():
        for key in ('title', 'short_title', 'description'):
            leaf(manifest, '/' + key, course[key])
        leaf(manifest, '/presentation/badge_label', course['presentation']['badge_label'])
        for level, text in course['levels'].items():
            leaf(manifest, '/levels/' + str(level), text)
        for i, module in enumerate(course['modules']):
            for key in ('title', 'summary'):
                leaf(manifest, f'/modules/{i}/{key}', module[key], f'module.{module["id"]}.{key}')
            for j, part in enumerate(module.get('optional', [])):
                leaf(manifest, f'/modules/{i}/optional/{j}/title', part['title'],
                     f'module.{module["id"]}.optional.{part["path"]}.title')
    for path in learning_paths(source, courses):
        data = source.read(path)
        if path.endswith('.md'):
            for i, block in enumerate(markdown_blocks(data.decode())):
                # Temporary candidate handle for explicit reconciliation only.
                add(path, 'markdown-block', 'unassigned', block, f'block-{i + 1}')
        elif path.endswith('.ipynb'):
            notebook = load(normalized_notebook(data), path)
            ids = [cell.get('id') for cell in notebook['cells']]
            if not all(ids) or len(ids) != len(set(ids)):
                c.fail(f'Notebook cell IDs must be present and unique: {path}')
            for cell in notebook['cells']:
                if cell['cell_type'] == 'markdown':
                    text = ''.join(cell['source']) if isinstance(cell['source'], list) else cell['source']
                    add(path, 'notebook-cell', cell['id'], text, 'cell.' + cell['id'])
        else:
            bank = load(data, path)
            c.shape(bank, 'authoring_quiz')
            for key in ('title', 'description'):
                leaf(path, '/' + key, bank[key])
            for i, question in enumerate(bank['questions']):
                for key in ('objective', 'prompt', 'explanation'):
                    leaf(path, f'/questions/{i}/{key}', question[key], f'question.{question["id"]}.{key}')
                for j, option in enumerate(question['options']):
                    leaf(path, f'/questions/{i}/options/{j}/text', option['text'],
                         f'question.{question["id"]}.option.{option["id"]}')
    ui = load(source.read(UI), UI)
    c.shape(ui, 'ui_messages')
    validate_messages('en', ui['messages'], ui['messages'], load(source.read(UI_CONTRACT), UI_CONTRACT))
    for key, text in ui['messages'].items():
        leaf(UI, '/messages/' + key, text)
    return result


def reconcile(source, previous=None, bindings=None, *, allow_new=False, retire=()):
    """Preserve IDs; edits/moves of Markdown require an explicit ID -> handle.

    Initial IDs are assigned UUIDs once, then committed. Content hashes and
    positions never become identities. Structured handles use permanent IDs.
    """
    current = candidates(source)
    by_handle = c.index_by(current, 'handle', 'candidate handle')
    bindings = bindings or {}
    old_units = previous['units'] if previous else []
    if previous:
        c.shape(previous, 'source_map')
    old = c.index_by(old_units, 'id', 'stable unit ID')
    if set(bindings) - set(old) or set(retire) - set(old) or set(bindings) & set(retire):
        c.fail('unknown or conflicting explicit unit mapping')
    # Movement or edits never reuse IDs by positional inference. Structured
    # candidates expose permanent semantic handles for explicit rebindings.
    claimed, output = set(), []
    for uid, unit in old.items():
        if uid in retire:
            continue
        if uid in bindings:
            matches = [by_handle[bindings[uid]]] if bindings[uid] in by_handle else []
        else:
            matches = [v for v in current if v['source_path'] == unit['source_path']
                       and v['locator']['kind'] == unit['locator']['kind']
                       and (v['source_text'] == unit['source_text'] if unit['locator']['kind'] == 'markdown-block'
                            else v['locator']['value'] == unit['locator']['value']
                            and v['source_text'] == unit['source_text'])]
        if len(matches) != 1 or matches[0]['handle'] in claimed:
            c.fail(f'explicit rebind/retire required for {uid}; inspect candidates')
        value = copy.deepcopy(matches[0]); claimed.add(value.pop('handle'))
        value['id'] = uid
        if value['locator']['kind'] == 'markdown-block':
            value['locator']['value'] = unit['locator']['value']
        output.append(value)
    for value in current:
        if value['handle'] in claimed:
            continue
        if previous and not allow_new:
            c.fail(f'unregistered source unit: {value["handle"]}; explicitly accept new units')
        value = copy.deepcopy(value); value.pop('handle')
        value['id'] = 'u.' + uuid.uuid4().hex
        if value['locator']['kind'] == 'markdown-block':
            value['locator']['value'] = 'section.' + uuid.uuid4().hex
        output.append(value)
    result = {'schema_version': 2, 'contract_version': '2.0.0', 'kind': 'doris-source-map',
              'units': sorted(output, key=lambda u: u['id'])}
    c.shape(result, 'source_map')
    return result


def complete_map(source, source_map):
    c.shape(source_map, 'source_map')
    c.index_by(source_map['units'], 'id', 'stable unit ID')
    expected = candidates(source)
    # Match the full independently discovered partition, including its scope.
    def key(v):
        return (v['source_path'], v['locator']['kind'],
                '' if v['locator']['kind'] == 'markdown-block' else v['locator']['value'],
                v['source_text'], v['source_hash'], c.digest(v['scope']))
    actual = [key(u) for u in source_map['units']]
    if len(actual) != len(set(actual)) or set(actual) != {key(v) for v in expected}:
        c.fail('source-map is incomplete or stale; run explicit reconciliation')
    return source_map['units']


def policy_payload(course):
    return {key: course[key] for key in ('slug', 'status', 'completion', 'certificate')} | {
        'modules': [{k: m[k] for k in ('id', 'status', 'required', 'requirements')}
                    for m in course['modules']]}


def quiz_payload(bank):
    return {'passing_score_percent': bank['passing_score_percent'], 'questions': [
        {k: q[k] for k in ('id', 'type', 'objective', 'prompt', 'options', 'answer', 'explanation')}
        for q in bank['questions']]}


def checked_revision(binding, value):
    if set(binding) != {'revision', 'payload_digest'} or binding['payload_digest'] != c.digest(value):
        c.fail('policy/quiz meaning changed; assign a new immutable revision explicitly')
    return binding['revision']


def validate_config(config):
    fields = {'version', 'profiles', 'policy_revisions', 'quiz_revisions', 'shared_images', 'glossary_version'}
    if set(config) != fields or (type(config['version']) is not int or config['version'] != 1) or not config['glossary_version']:
        c.fail('unsupported or malformed producer config')
    c.index_by(config['profiles'], 'id', 'producer profile')
    for profile in config['profiles']:
        if set(profile) != {'id', 'modules'} or not profile['modules']:
            c.fail('invalid producer profile')
        seen = set()
        for module in profile['modules']:
            if set(module) != {'course_slug', 'module_id', 'optional_paths'}:
                c.fail('invalid profile module selection')
            key = module['course_slug'], module['module_id']
            if key in seen or len(module['optional_paths']) != len(set(module['optional_paths'])):
                c.fail('duplicate profile selection')
            seen.add(key)
    for path, review in config['shared_images'].items():
        checked_path(path)
        if set(review) != {'record', 'rationale'} or not all(review.values()):
            c.fail('shared images require explicit treatment evidence')


def links(text):
    values = []
    class Html(HTMLParser):
        def handle_starttag(self, tag, attrs):
            values.extend(v for k, v in attrs if k in {'src', 'href'} and v)
    def walk(tokens):
        for token in tokens:
            if token.type in {'link_open', 'image'}:
                values.append(token.attrGet('href' if token.type == 'link_open' else 'src'))
            if token.type in {'html_block', 'html_inline'}:
                Html().feed(token.content)
            if token.children:
                walk(token.children)
    walk(PARSER.parse(text))
    return values


def relative_target(path, url):
    parsed = urlsplit(url)
    if parsed.scheme or parsed.netloc or parsed.path.startswith('/') or not parsed.path:
        return None
    target = posixpath.normpath(posixpath.join(posixpath.dirname(path), parsed.path))
    checked_path(target)
    return target


def texts(data, path):
    if path.endswith('.md'):
        return [data.decode()]
    if path.endswith('.ipynb'):
        return [''.join(cell['source']) if isinstance(cell['source'], list) else cell['source']
                for cell in load(data, path)['cells'] if cell['cell_type'] == 'markdown']
    return []


def quiz_shells(files, teaching, courses):
    """Bind Notebook shells by static YAML string references, without execution."""
    banks = {path for path in teaching if path.endswith(('.yaml', '.yml'))}
    result = {}
    for path in sorted(p for p in teaching if p.endswith('.ipynb')):
        notebook = load(files[path], path)
        code = '\n'.join(''.join(cell['source']) if isinstance(cell['source'], list) else cell['source']
                         for cell in notebook['cells'] if cell['cell_type'] == 'code')
        if 'from_yaml' not in code:
            continue
        tree = ast.parse(code)
        if not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'from_yaml'
                   for n in ast.walk(tree)):
            continue
        scope = scope_for(path, courses)
        base = posixpath.dirname(courses[scope['course_slug']][0])
        values = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        matches = [bank for bank in banks if bank.startswith(base + '/') and values.intersection(
            {bank, bank.removeprefix(base + '/'), posixpath.basename(bank)})]
        if len(matches) != 1:
            c.fail('Quiz Notebook requires one unambiguous static bank reference: ' + path)
        result.setdefault(matches[0], []).append(path)
    return result


def file_record(path, data):
    return {'path': path, 'size': len(data), 'sha256': c.sha256(data)}


def asset_id(path):
    # A file-addressed asset ID, unlike persistent translation unit identities.
    return 'a.' + c.sha256(path.encode())[:32]


def build_tree(source, selected, locales=('en',), *, state='release', default='en'):
    config = load(source.read(CONFIG), CONFIG); validate_config(config)
    if isinstance(source, GitSource):
        verify_revision_history(source)
    glossary = load(source.read(GLOSSARY), GLOSSARY)
    if (set(glossary) != {'version', 'source_locale', 'target_locale', 'entries'}
            or glossary['version'] != config['glossary_version'] or glossary['source_locale'] != 'en'
            or glossary['target_locale'] not in {item['tag'] for item in c.REGISTRY['locales']}):
        c.fail('glossary is malformed or differs from its pinned version')
    for term in glossary['entries']:
        if set(term) != {'source', 'target', 'rule'} or not all(isinstance(v, str) and v for v in term.values()):
            c.fail('invalid glossary term')
    source_map = load(source.read(MAP), MAP)
    units = complete_map(source, source_map)
    catalog, courses = course_index(source)
    profiles_config = c.index_by(config['profiles'], 'id', 'producer profile')
    if not selected or len(selected) != len(set(selected)) or set(selected) - set(profiles_config):
        c.fail('select unique, known producer profiles')
    if not locales or len(locales) != len(set(locales)) or locales[0] != 'en':
        c.fail('locale set must be unique and start with en')
    for tag in locales:
        if tag not in {item['tag'] for item in c.REGISTRY['locales']}:
            c.fail(f'unknown canonical locale: {tag}')
    if default not in locales or state not in {'release', 'preview'}:
        c.fail('invalid package state/default locale')
    files, inputs = {}, {}
    def include(path):
        checked_path(path)
        if path in files:
            return
        data = source.read(path)
        inputs[path] = file_record(path, data)
        if len(inputs) > 10000 or sum(r['size'] for r in inputs.values()) > c.MAX_TOTAL_BYTES:
            c.fail('producer input exceeds frozen file count/size limits')
        files[path] = normalized_notebook(data) if path.endswith('.ipynb') else data
    include('catalog.yaml')
    for path in source.paths():
        if path.startswith('doris-course/') and c.PATH.fullmatch(path):
            include(path)
    for path in (CONFIG, MAP, UI, GLOSSARY, UI_CONTRACT):
        include(path)
    # Freeze the actual builder and admission implementation as provenance data.
    for path in (MESSAGE_ENGINE, 'tools/content_producer.py', 'tools/build_content_package.py',
                 'tools/content_contract_v2.py', 'tools/check_content_contract.py',
                 'tools/requirements.txt', 'contracts/v1/schema.json', 'contracts/v2/schema.json',
                 'contracts/v2/locales.json', 'contracts/v2/registry.schema.json'):
        include(path)
        installed = Path(__file__).resolve().parents[1] / path
        if installed.read_bytes() != files[path]:
            c.fail('selected source does not match the installed pinned builder/interface: ' + path)
    pending = list(files)
    visited = set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        for text in texts(files[path], path):
            for url in links(text):
                target = relative_target(path, url)
                if target and target not in files:
                    include(target); pending.append(target)
    teaching = set(learning_paths(source, courses))
    metadata_paths = {'catalog.yaml', *(p for p, _ in courses.values())}
    assets = []
    for path, data in sorted(files.items()):
        suffix = Path(path).suffix.lower()
        if path in metadata_paths:
            # Metadata is rendered into data-only JSON, leaving v1 YAML intact.
            rendered = 'rendered/en/' + path.rsplit('.', 1)[0] + '.json'
            files[rendered] = json_bytes(load(data, path))
            assets.append({'id': asset_id(path), 'kind': 'metadata', 'locale': 'en',
                           'file': file_record(rendered, files[rendered]), 'source_asset_hash': None})
            continue
        if path in teaching:
            kind = {'.md': 'lesson', '.ipynb': 'notebook', '.yaml': 'quiz', '.yml': 'quiz'}[suffix]
            locale = 'en'
        elif path == UI:
            kind, locale = 'ui', 'en'
        elif path == UI_CONTRACT:
            kind, locale = 'data', 'shared'
        elif suffix in IMAGE:
            kind = 'image'; locale = 'shared' if path in config['shared_images'] else 'en'
        elif path.startswith('doris-course/') and suffix not in {'.md', '.ipynb'}:
            kind, locale = ('code' if suffix in {'.py', '.sh', '.sql'} else 'data'), 'shared'
        else:
            continue
        assets.append({'id': asset_id(path), 'kind': kind, 'locale': locale,
                       'file': file_record(path, data), 'source_asset_hash': None})
    asset_paths = {path: asset_id(path) for path in files if path not in metadata_paths}
    asset_paths.update({path: asset_id(path) for path in metadata_paths})
    by_source = {}
    for unit in units:
        by_source.setdefault(unit['source_path'], []).append(unit)
    quizzes = []
    for path in sorted(p for p in teaching if p.endswith(('.yaml', '.yml'))):
        bank = load(files[path], path); scope = scope_for(path, courses)
        if path not in config['quiz_revisions']:
            c.fail(f'missing immutable quiz revision: {path}')
        quiz = {'id': 'q.' + c.sha256(path.encode())[:32], **scope, 'source_path': path,
                'semantic_revision': checked_revision(config['quiz_revisions'][path], quiz_payload(bank)),
                'passing_score_percent': bank['passing_score_percent'], 'questions': [
                    {'id': q['id'], 'type': q['type'], 'option_ids': [o['id'] for o in q['options']],
                     'answer': q['answer']} for q in bank['questions']], 'presentations': []}
        ids = [u['id'] for u in by_source[path] if not u['locator']['value'].endswith('/explanation')]
        quiz['presentations'].append({'locale': 'en', 'presentation_revision': '0' * 64, 'unit_ids': ids})
        quizzes.append(quiz)
    shells = quiz_shells(files, teaching, courses)
    profiles = []
    closure_paths = set()
    for pid in selected:
        selection = profiles_config[pid]
        needed = {'catalog.yaml', UI, UI_CONTRACT}
        keys, allowed_optional = [], set()
        for chosen in selection['modules']:
            slug, mid = chosen['course_slug'], chosen['module_id']
            if slug not in courses:
                c.fail('profile references unknown course')
            manifest, course = courses[slug]; base = posixpath.dirname(manifest)
            module = next((m for m in course['modules'] if m['id'] == mid), None)
            if course['status'] not in {'published', 'preview'} or not module or module['status'] != 'published':
                c.fail('profile cannot expose draft/planned/retired content')
            keys.append({'course_slug': slug, 'module_id': mid})
            needed.update({manifest, base + '/' + course['lab_setup']['guide']})
            needed.update(a['file']['path'] for a in assets if a['locale'] == 'shared'
                          and a['file']['path'].startswith((base + '/', 'doris-course/common/')))
            for field in ('course', 'lab', 'quiz'):
                if module.get(field):
                    needed.add(base + '/' + module[field])
            # Quiz notebook shells are first-class teaching content.
            optionals = {p['path']: p for p in module.get('optional', [])}
            if set(chosen['optional_paths']) - set(optionals):
                c.fail('profile references unknown optional work')
            for path in chosen['optional_paths']:
                part = optionals[path]
                needed.add(base + '/' + path); allowed_optional.add(base + '/' + path)
                if part.get('quiz'):
                    needed.add(base + '/' + part['quiz']); allowed_optional.add(base + '/' + part['quiz'])
        needed.update(shell for bank, paths in shells.items() if bank in needed for shell in paths)
        # Links to other lessons are navigation; images and downloads are closure.
        pending = list(needed)
        while pending:
            path = pending.pop()
            for text in texts(files[path], path):
                for url in links(text):
                    target = relative_target(path, url)
                    if target and target not in needed and target not in teaching and target not in metadata_paths:
                        needed.add(target); pending.append(target)
        closure_paths.update(needed)
        selected_keys = {(k['course_slug'], k['module_id']) for k in keys}
        required_units = []
        for unit in units:
            path, scope = unit['source_path'], unit['scope']
            if path not in needed:
                continue
            # Course manifest global copy + selected module/optional labels only.
            if path in metadata_paths and scope['module_id'] is None and path != 'catalog.yaml':
                pointer = unit['locator']['value']
                match = re.match(r'/modules/(\d+)(?:/optional/(\d+))?', pointer)
                if match:
                    course = courses[scope['course_slug']][1]
                    module = course['modules'][int(match[1])]
                    if (course['slug'], module['id']) not in selected_keys:
                        continue
                    if match[2] and (posixpath.dirname(path) + '/' + module['optional'][int(match[2])]['path']) not in allowed_optional:
                        continue
            required_units.append(unit['id'])
        profiles.append({'id': pid, 'modules': keys, 'unit_ids': required_units,
                         'asset_ids': [a['id'] for a in assets if any(
                             asset_paths.get(path) == a['id'] for path in needed)],
                         'quiz_ids': [q['id'] for q in quizzes if q['source_path'] in needed]})
    bindings = [{'slug': slug, 'manifest': path,
                 'policy_revision': checked_revision(config['policy_revisions'][slug], policy_payload(course))}
                for slug, (path, course) in courses.items()]
    translations, locale_reports = [], {}
    required = set().union(*(set(p['unit_ids']) for p in profiles))
    unit_index = c.index_by(units, 'id', 'unit')
    for tag in locales[1:]:
        overlay_path = f'locales/{tag}/overlay.json'
        if overlay_path in source.paths():
            include(overlay_path)
            overlay = load(files[overlay_path], overlay_path); c.shape(overlay, 'locale_overlay')
            if overlay['locale'] != tag:
                c.fail('overlay locale differs from its directory')
            entries = c.index_by(overlay['units'], 'unit_id', 'translation unit')
        else:
            entries = {}
        stale, missing, unchecked = [], [], []
        for uid, entry in entries.items():
            if uid not in unit_index:
                c.fail(f'unknown overlay unit: {uid}')
            if entry['provenance']['glossary_version'] != config['glossary_version']:
                c.fail('overlay glossary differs from pinned producer glossary')
            if entry['source_hash'] != unit_index[uid]['source_hash']:
                stale.append(uid)
        for uid in sorted(required):
            if uid not in entries:
                missing.append(uid)
            elif entries[uid]['state'] not in {'llm_checked', 'reviewed'}:
                unchecked.append(uid)
        if stale:
            c.fail(f'stale overlay {tag}: ' + ','.join(stale))
        if state == 'release' and (missing or unchecked):
            c.fail(f'release locale {tag} has missing/unchecked units: missing={len(missing)}, unchecked={len(unchecked)}')
        translations.append({'locale': tag, 'units': list(entries.values())})
        locale_reports[tag] = {'required': len(required), 'missing': missing, 'stale': stale, 'unchecked': unchecked}
        # Generate complete files only, retaining their course-relative layout.
        for asset in list(assets):
            path = asset['file']['path']
            original_path = next((p for p in metadata_paths if asset_id(p) == asset['id']), path)
            file_units = by_source.get(original_path, [])
            if original_path in metadata_paths:
                file_units = [u for u in file_units if u['id'] in required]
            if asset['locale'] != 'en' or not file_units or any(u['id'] not in entries for u in file_units):
                continue
            target_path = f'rendered/{tag}/' + path.removeprefix('rendered/en/')
            data = apply_units(files[path], original_path, file_units, entries)
            if original_path == UI:
                validate_messages(tag, load(files[UI], UI)['messages'], load(data, UI)['messages'], load(files[UI_CONTRACT], UI_CONTRACT))
            files[target_path] = data
            assets.append({'id': asset['id'] + '.' + tag.lower(), 'kind': asset['kind'], 'locale': tag,
                           'file': file_record(target_path, data), 'source_asset_hash': asset['file']['sha256']})
        replacements = f'locales/{tag}/assets.json'
        if replacements in source.paths():
            include(replacements)
            replacement_map = load(files[replacements], replacements)
            if not isinstance(replacement_map, dict):
                c.fail('asset replacement input must map source image paths to frozen image paths')
            for original, replacement in replacement_map.items():
                checked_path(original); checked_path(replacement)
                asset = next((a for a in assets if a['id'] == asset_id(original) and a['kind'] == 'image'), None)
                if not asset or asset['locale'] != 'en' or Path(replacement).suffix.lower() not in IMAGE:
                    c.fail('replacement must bind an English image of a supported format')
                include(replacement)
                target_path = f'rendered/{tag}/' + original
                files[target_path] = files[replacement]
                assets.append({'id': asset['id'] + '.' + tag.lower(), 'kind': 'image', 'locale': tag,
                    'file': file_record(target_path, files[target_path]), 'source_asset_hash': asset['file']['sha256']})
        # Preserve source-relative links physically. The link index marks each
        # untranslated navigation target as English; consumers must use that
        # locale explicitly when routing, never expose it under a target URL.
        pending = [p for p in files if p.startswith(f'rendered/{tag}/')]
        visited = set()
        while pending:
            path = pending.pop()
            if path in visited:
                continue
            visited.add(path)
            for text in texts(files[path], path):
                for url in links(text):
                    target = relative_target(path, url)
                    if target and target not in files:
                        prefix = f'rendered/{tag}/'
                        if not target.startswith(prefix) or target[len(prefix):] not in files:
                            c.fail('localized link cannot resolve a frozen source: ' + str(target))
                        original = target[len(prefix):]
                        files[target] = files[original]
                        original_asset = next((a for a in assets if a['file']['path'] == original
                                               and a['locale'] in {'en', 'shared'}), None)
                        if original_asset:
                            assets.append({**original_asset, 'id': asset_id(target),
                                           'file': file_record(target, files[target])})
                        pending.append(target)
        for quiz in quizzes:
            ids = quiz['presentations'][0]['unit_ids']
            if all(uid in entries for uid in ids):
                quiz['presentations'].append({'locale': tag, 'presentation_revision': '0' * 64, 'unit_ids': ids})
    overlays = {o['locale']: {t['unit_id']: t for t in o['units']} for o in translations}
    for quiz in quizzes:
        for presentation in quiz['presentations']:
            presentation['presentation_revision'] = c.presentation_digest(quiz, presentation['locale'], unit_index, overlays)
    # Data-only indexes bind source provenance, stable anchors and immutable downloads.
    files['indexes/source-inputs.json'] = json_bytes({'source_ref': source.ref, 'inputs': [inputs[p] for p in sorted(inputs)],
        'notebook_transform': 'strip-markdown-style-attributes-v1', 'builder_version': VERSION})
    anchors = [{'unit_id': u['id'], 'source_path': u['source_path'], 'anchor': 'unit-' + u['id'],
                'locator': u['locator']} for u in units if u['locator']['kind'] in {'markdown-block', 'notebook-cell'}]
    files['indexes/anchors.json'] = json_bytes(anchors)
    files['indexes/coverage.json'] = json_bytes({'definition': 'all independently extracted units in selected profile closure',
        'required_units': len(required), 'locales': {'en': {'required': len(required), 'missing': [], 'stale': [], 'unchecked': []}, **locale_reports}})
    files['indexes/assets.json'] = json_bytes({'assets': assets, 'links': [
        {'path': path, 'locale': next((a['locale'] for a in assets if a['file']['path'] == path), 'en'),
         'source_path': path.split('/', 2)[2]} for path in sorted(files) if path.startswith('rendered/')], 'downloads': [
        {'asset_id': a['id'], 'path': a['file']['path'], 'sha256': a['file']['sha256'],
         'delivery': 'immutable-artifact', 'source_ref': source.ref} for a in assets],
         'closure_paths': sorted(closure_paths), 'shared_images': config['shared_images']})
    package = {'schema_version': 2, 'contract_version': '2.0.0', 'kind': 'doris-content-package',
        'purpose': 'teaching', 'package_state': state, 'catalog': 'catalog.yaml', 'courses': bindings,
        'variants': [{'locale': tag, 'publication_state': 'published' if tag == 'en' or state == 'release' else 'draft'} for tag in locales],
        'default_locale': default, 'available_locales': list(locales) if state == 'release' else ['en'],
        'locale_digests': {}, 'profiles': profiles, 'selected_profiles': selected, 'source_units': units,
        'translations': translations, 'assets': assets, 'quizzes': quizzes,
        'snapshot': {'source_ref': source.ref, 'source_schema_digest': c.sha256(c.v1.SCHEMA.read_bytes()),
            'contract_digest': c.sha256((c.CONTRACT / 'schema.json').read_bytes()), 'registry_version': c.REGISTRY['registry_version'],
            'registry_digest': c.sha256((c.CONTRACT / 'locales.json').read_bytes()),
            'builder_version': VERSION + '.' + c.sha256(source.read('tools/content_producer.py')),
            'files': [file_record(path, data) for path, data in sorted(files.items())],
            'translation_digest': '', 'teaching_digest': '', 'package_digest': ''}}
    public = public_projection(package, files)
    files['indexes/projections.json'] = json_bytes({'public_files': [file_record(p, d) for p, d in sorted(public.items())],
        'public_digest': c.digest([file_record(p, d) for p, d in sorted(public.items())]),
        'private_projection': 'package.v2.json; never serve the teaching tree or its grading digests'})
    package['snapshot']['files'] = [file_record(p, d) for p, d in sorted(files.items())]
    package = c.seal(package)
    files['package.v2.json'] = json_bytes(package)
    return files, package


def public_projection(package, files):
    """Allowlisted projection: no source inventory, answers or explanations."""
    public = {}
    required_quizzes = set().union(*(set(p['quiz_ids']) for p in package['profiles']))
    for quiz in package['quizzes']:
        if quiz['id'] not in required_quizzes:
            continue
        original = load(files[quiz['source_path']], quiz['source_path'])
        units = [u for u in package['source_units'] if u['source_path'] == quiz['source_path']]
        for tag in package['available_locales']:
            bank = original
            if tag != 'en':
                entries = next(o['units'] for o in package['translations'] if o['locale'] == tag)
                bank = load(apply_units(files[quiz['source_path']], quiz['source_path'], units,
                            {t['unit_id']: t for t in entries}), quiz['source_path'])
            presentation = next(p for p in quiz['presentations'] if p['locale'] == tag)
            value = {'schema_version': 2, 'kind': 'doris-public-quiz', 'id': quiz['id'], 'locale': tag,
                     'semantic_revision': quiz['semantic_revision'],
                     'presentation_revision': presentation['presentation_revision'],
                     'questions': [{k: q[k] for k in ('id', 'type', 'objective', 'prompt', 'options')}
                                   for q in bank['questions']]}
            c.shape(value, 'public_quiz')
            public[f'quizzes/{tag}/{quiz["id"]}.json'] = json_bytes(value)
    public['index.json'] = json_bytes({'schema_version': 2, 'kind': 'doris-public-content-index',
        'source_ref': package['snapshot']['source_ref'], 'available_locales': package['available_locales'],
        'default_locale': package['default_locale'], 'selected_profiles': package['selected_profiles'],
        'quizzes': [file_record(p, d) for p, d in sorted(public.items())]})
    return public


def apply_units(data, source_path, units, entries):
    suffix = Path(source_path).suffix
    if suffix == '.md':
        text = data.decode()
        # Replace by original spans in one pass, never cascading target text.
        spans = sorted((text.index(u['source_text']), len(u['source_text']), entries[u['id']]['target_text']) for u in units)
        end, chunks = 0, []
        for start, length, target in spans:
            if start < end:
                c.fail('overlapping Markdown units')
            chunks.extend([text[end:start], target]); end = start + length
        chunks.append(text[end:])
        return ''.join(chunks).encode()
    document = load(data, 'data.json' if suffix in {'.yaml', '.yml'} and data.lstrip().startswith(b'{') else source_path)
    for unit in units:
        value = entries[unit['id']]['target_text']
        if unit['locator']['kind'] == 'notebook-cell':
            cell = next(cell for cell in document['cells'] if cell['id'] == unit['locator']['value'])
            cell['source'] = value.splitlines(keepends=True) if isinstance(cell['source'], list) else value
        else:
            parts = [k.replace('~1', '/').replace('~0', '~') for k in unit['locator']['value'][1:].split('/')]
            node = document
            for key in parts[:-1]:
                node = node[int(key)] if isinstance(node, list) else node[key]
            node[parts[-1]] = value
    if suffix in {'.yaml', '.yml'} and not data.lstrip().startswith(b'{'):
        return yaml.safe_dump(document, sort_keys=False, allow_unicode=True).encode()
    return json_bytes(document)


def write_tree(files, root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    for path, data in sorted(files.items()):
        checked_path(path)
        target = root / path; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data); target.chmod(0o644)


def archive_bytes(files):
    stream = io.BytesIO()
    with gzip.GzipFile(fileobj=stream, mode='wb', filename='', mtime=0, compresslevel=9) as compressed:
        with tarfile.open(fileobj=compressed, mode='w', format=tarfile.USTAR_FORMAT) as archive:
            for path, data in sorted(files.items()):
                info = tarfile.TarInfo(path); info.size = len(data); info.mode = 0o644
                info.uid = info.gid = info.mtime = 0; info.uname = info.gname = ''
                archive.addfile(info, io.BytesIO(data))
    return stream.getvalue()


def build(source, output, profiles, locales=('en',), *, state='release', default='en'):
    output = Path(output).resolve()
    if output.exists():
        c.fail('output already exists; choose a new directory')
    files, package = build_tree(source, profiles, locales, state=state, default=default)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.content-build-', dir=output.parent) as temp:
        root = Path(temp) / 'tree'; write_tree(files, root)
        report = c.validate_package(root)
        public = public_projection(package, files)
        write_tree(public, Path(temp) / 'public')
        public_archive = archive_bytes(public)
        (Path(temp) / 'public.tar.gz').write_bytes(public_archive)
        artifact = archive_bytes(files)
        (Path(temp) / 'teaching.tar.gz').write_bytes(artifact)
        (Path(temp) / 'build-report.json').write_bytes(json_bytes({**report, 'archive_sha256': c.sha256(artifact),
            'archive_size': len(artifact), 'public_archive_sha256': c.sha256(public_archive),
            'public_digest': c.digest([file_record(p, d) for p, d in sorted(public.items())]), 'source_ref': source.ref, 'builder_version': package['snapshot']['builder_version']}))
        # Publish only a complete admitted result. A failed build leaves no output.
        Path(temp).rename(output)
    return load((output / 'build-report.json').read_bytes(), 'report.json')
