"""Content-v2 runtime admission, immutable templates and persistent working copies.

This module uses only the standard library before the host environments exist.
Full content-contract admission runs in the operator builder and server startup.
"""
import copy
import hashlib
from html import escape
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
import tempfile
from urllib.parse import quote, urlsplit, urlunsplit, unquote
import posixpath

ABI = 'doris-workshop-content-2.1'
CAPABILITIES = ['content-v2', 'notebook-copies-v1', 'quiz-presentations-v2']
PROFILE = 'rta-module-1'
COURSE = 'doris-course/01-real-time-analytics'


def profile_registry():
    # The new runtime ships the operator's allowlist as a hashed payload; local
    # operator imports read that same source. Old frozen bundles use old code.
    location = Path(__file__).with_name('profiles.v2.json')
    if not location.is_file():
        location = Path(__file__).resolve().parent.parent / 'profiles.v2.json'
    return json.loads(location.read_text())['profiles']


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def notebook_html_images(text, rewrite, parser):
    """Rewrite actual HTML image tags, keeping fenced/inline code examples intact."""
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    protected = [(offsets[t.map[0]], offsets[t.map[1]]) for t in parser.parse(text)
                 if t.type in {'fence', 'code_block'}]
    protected.extend((m.start(), m.end()) for m in re.finditer(r'(`+)(.*?)\1', text, re.S))
    replacements = []

    class Images(HTMLParser):
        def handle_starttag(self, tag, attrs):
            self.image(tag, attrs, False)

        def handle_startendtag(self, tag, attrs):
            self.image(tag, attrs, True)

        def image(self, tag, attrs, closed):
            start = offsets[self.getpos()[0] - 1] + self.getpos()[1]
            if tag != 'img' or any(a <= start < b for a, b in protected):
                return
            if not any(name == 'src' and value is not None for name, value in attrs):
                return
            rendered = []
            for name, value in attrs:
                if value is None:
                    rendered.append(name)
                else:
                    rendered.append(name + '="' + escape(rewrite(value) if name == 'src' else value, quote=True) + '"')
            replacement = '<img ' + ' '.join(rendered) + (' />' if closed else '>')
            replacements.append((start, start + len(self.get_starttag_text()), replacement))

    Images(convert_charrefs=False).feed(text)
    for start, end, replacement in reversed(replacements):
        text = text[:start] + replacement + text[end:]
    return text


def safe(root, name):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]+(?:[./][A-Za-z0-9_-]+)*', name):
        raise ValueError('content: invalid relative path')
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or str(path) != name:
        raise ValueError('content: unsafe relative path')
    root = Path(root).resolve()
    target = root / name
    for parent in (target, *target.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError('content: links are not allowed')
    if not target.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError('content: path outside installation')
    return target


def read(root, name):
    return json.loads(safe(root, name).read_bytes())


def fingerprint(package):
    """Bind every shared code/data input as well as dependency manifests."""
    shared = {a['file']['path']: a['file']['sha256'] for a in package['assets']
              if a['locale'] == 'shared' and a['kind'] in {'code', 'data'}}
    dependencies = {p: h for p, h in shared.items() if Path(p).name in
                    {'requirements.txt', 'pyproject.toml', 'uv.lock', 'setup.py', 'setup.cfg'}}
    return digest(canonical(shared)), digest(canonical(dependencies))


def binding(package):
    code, dependencies = fingerprint(package)
    return {'schema_version': 1, 'abi': ABI, 'capabilities': CAPABILITIES,
            'source_ref': package['snapshot']['source_ref'],
            'package_digest': package['snapshot']['package_digest'],
            'default_locale': package['default_locale'], 'available_locales': package['available_locales'],
            'locale_digests': package['locale_digests'], 'profiles': package['selected_profiles'],
            'code_digest': code, 'dependency_digest': dependencies}


def validate_binding(value):
    keys = {'schema_version', 'abi', 'capabilities', 'source_ref', 'package_digest', 'default_locale',
            'available_locales', 'locale_digests', 'profiles', 'code_digest', 'dependency_digest'}
    if not isinstance(value, dict) or set(value) != keys or type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ValueError('content: unsupported binding version')
    locales = value['available_locales']
    profiles = value['profiles']
    registry = profile_registry()
    if (not isinstance(profiles, list) or not profiles or len(profiles) != len(set(profiles))
            or any(tag not in registry for tag in profiles)
            or len({registry[tag]['kernel'] for tag in profiles}) != len(profiles)):
        raise ValueError('content: unknown, duplicate or overlapping profiles')
    if (value['abi'] != ABI or value['capabilities'] != CAPABILITIES
            or not isinstance(locales, list) or locales != ['en', 'kk-Cyrl']
            or value['default_locale'] not in locales or set(value['locale_digests']) != set(locales)):
        raise ValueError('content: unsupported ABI, profile, capability or language set')
    if not re.fullmatch('[0-9a-f]{40}', value['source_ref']):
        raise ValueError('content: invalid source revision')
    for checksum in [value[k] for k in ('package_digest', 'code_digest', 'dependency_digest')] + list(value['locale_digests'].values()):
        if not isinstance(checksum, str) or not re.fullmatch('[0-9a-f]{64}', checksum):
            raise ValueError('content: invalid fingerprint')


def validate_plan(plan):
    version = plan.get('schema_version')
    if type(version) is not int or version not in (1, 2):
        raise ValueError('runtime: unsupported version')
    if version == 1:
        if 'content' in plan or 'capabilities' in plan:
            raise ValueError('runtime: v1 cannot declare multilingual capabilities')
        return
    validate_binding(plan['content'])
    registry = profile_registry()
    selected = {registry[p]['kernel']: p for p in plan['content']['profiles']}
    if (plan.get('capabilities') != CAPABILITIES or plan['course_ref'] != plan['content']['source_ref']
            or [course['kernel'] for course in plan['courses']] != list(selected)):
        raise ValueError('runtime: incompatible content binding')
    seen = set()
    for course in plan['courses']:
        kernel = course['kernel']
        if kernel not in selected or kernel in seen:
            raise ValueError('runtime: unknown or duplicate course')
        seen.add(kernel)
        profile = registry[selected[kernel]]
        if (course['profiles'] != [selected[kernel]] or course['course_dir'] != profile['course_dir']
                or [m['id'] for m in course['modules']] != profile['module_ids']
                or not {p['path'] for p in course.get('optional', [])}.issubset(profile['optional_paths'])
                or course.get('extensions') != any('/extensions/' in p for p in profile['optional_paths'])):
            raise ValueError('runtime: selection differs from its admitted profile')


def verify_tree(root, expected, *, full=False, source=None):
    validate_binding(expected)
    package = read(root, 'package.v2.json')
    if package['package_state'] != 'release' or binding(package) != expected:
        raise ValueError('content: package binding differs; use an explicit update on a fresh installation')
    files = package['snapshot']['files']
    paths = set()
    for record in files:
        name = record['path']
        if name in paths:
            raise ValueError('content: duplicate file')
        paths.add(name)
        target = safe(root, name)
        if target.stat().st_size != record['size'] or digest(target.read_bytes()) != record['sha256']:
            raise ValueError('content: file hash mismatch: ' + name)
    actual = {p.relative_to(root).as_posix() for p in Path(root).rglob('*') if not p.is_dir()}
    if any(p.is_symlink() for p in Path(root).rglob('*')):
        raise ValueError('content: unexpected symlink')
    if actual != paths | {'package.v2.json'}:
        raise ValueError('content: unexpected installation files')
    if full:
        # Source tooling is frozen in source.tar and checked against the pinned
        # builder at bundle creation. It is never imported from the teaching tree.
        import sys
        sys.path.insert(0, str(Path(source) / 'tools'))
        from content_contract_v2 import validate_package
        validate_package(root)
    return package


def unpack(archive, destination, expected):
    """Validate in staging, then publish once. Never repair a changed tree in place."""
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        return verify_tree(destination, expected)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.chmod(0o755)
    import shutil
    temporary = Path(tempfile.mkdtemp(prefix='.content-', dir=destination.parent))
    try:
        with tarfile.open(archive) as stream:
            members = stream.getmembers()
            seen, total = set(), 0
            for member in members:
                safe(temporary, member.name)
                if not member.isfile() or member.name in seen or member.size > 16 * 1024 * 1024:
                    raise ValueError('content: invalid archive entry')
                seen.add(member.name)
                total += member.size
            if total > 128 * 1024 * 1024:
                raise ValueError('content: archive too large')
            stream.extractall(temporary, filter='data')
        package = verify_tree(temporary, expected)
        for path in temporary.rglob('*'):
            path.chmod(0o555 if path.is_dir() else 0o444)
        temporary.chmod(0o555)
        os.rename(temporary, destination)
        return package
    finally:
        if temporary.exists():
            temporary.chmod(0o755)
            for p in temporary.rglob('*'):
                if p.is_dir():
                    p.chmod(0o755)
            shutil.rmtree(temporary)


class Content:
    def __init__(self, plan, root, workspace):
        validate_plan(plan)
        self.plan, self.root, self.workspace = plan, Path(root), Path(workspace)
        self.package = verify_tree(self.root, plan['content'])
        self.assets = read(self.root, 'indexes/assets.json')
        self.inventory = {r['path']: r for r in self.package['snapshot']['files']}
        selected = [p for p in self.package['profiles'] if p['id'] in plan['content']['profiles']]
        selected_assets = {a for p in selected for a in p['asset_ids']}
        source_asset_ids = {a['file']['path']: a['id'] for a in self.package['assets'] if a['locale'] in {'en', 'shared'}}
        selected_keys = {(m['course_slug'], m['module_id']) for p in selected for m in p['modules']}
        if set(plan['content']['profiles']) != {p['id'] for p in selected}:
            raise ValueError('content: selected profile is absent')
        self.selected_quizzes = {q for p in selected for q in p['quiz_ids']}
        navigation_paths = {r['source_path'] for r in self.assets['links']}
        selected_files = {a['file']['path'] for a in self.package['assets'] if a['id'] in selected_assets}
        selected_paths = selected_files | {r['source_path'] for r in self.assets['links']
                                          if r['locale'] == 'en' and r['path'] in selected_files}
        selected_paths.update(q['source_path'] for q in self.package['quizzes'] if q['id'] in self.selected_quizzes)
        self.links = {locale: {} for locale in plan['content']['available_locales']}
        # A package can carry extra checked translations for other profiles.
        # Only this selection may expose them; outside navigation stays English.
        for row in self.assets['links']:
            for locale in self.links:
                if row['locale'] == locale and (locale == 'en' or row['source_path'] in selected_paths):
                    self.links[locale][row['source_path']] = (row['path'], row['locale'])
        for locale in self.links:
            for row in self.assets['links']:
                if row['locale'] == 'en':
                    self.links[locale].setdefault(row['source_path'], (row['path'], 'en'))
        for asset in self.package['assets']:
            if asset['id'] not in selected_assets:
                # Extra checked translations must not expand this selection.
                # Keep their original English lessons as explicit navigation.
                if asset.get('kind') == 'lesson' and asset['locale'] == 'en':
                    path = asset['file']['path']
                    if path in navigation_paths:
                        for locale in self.links:
                            self.links[locale].setdefault(path, (path, 'en'))
                continue
            if asset['locale'] == 'en' and not asset['file']['path'].startswith('rendered/'):
                self.links['en'].setdefault(asset['file']['path'], (asset['file']['path'], 'en'))
            if asset['locale'] == 'shared':
                for locale in self.links:
                    self.links[locale].setdefault(asset['file']['path'], (asset['file']['path'], locale))
        self.notebooks = {c['course_dir'] + '/' + p: c for c in plan['courses'] for p in c['notebooks']}
        self.allowed_paths = {path for links in self.links.values() for path, _ in links.values()}
        expected_notebooks, actual_keys = set(), set()
        for course in plan['courses']:
            metadata = read(self.root, f'rendered/en/{course["course_dir"]}/course.json')
            if course['slug'] != metadata['slug']:
                raise ValueError('content: course identity differs')
            expected_optional = []
            for installed in course['modules']:
                key = course['slug'], installed['id']; actual_keys.add(key)
                module = next(m for m in metadata['modules'] if m['id'] == installed['id'])
                if key not in selected_keys or any(installed[k] != module[k] for k in ('course', 'lab', 'quiz')):
                    raise ValueError('content: runtime paths differ from the admitted profile')
                expected_notebooks.add(course['course_dir'] + '/' + module['lab'])
                shell = course['course_dir'] + '/' + str(Path(module['quiz']).with_suffix('.ipynb'))
                if shell in self.inventory:
                    expected_notebooks.add(shell)
                for part in module.get('optional', []):
                    path = course['course_dir'] + '/' + part['path']
                    if source_asset_ids.get(path) in selected_assets:
                        expected_optional.append(part)
                        if path.endswith('.ipynb'):
                            expected_notebooks.add(path)
                        if part.get('quiz'):
                            shell = course['course_dir'] + '/' + str(Path(part['quiz']).with_suffix('.ipynb'))
                            if shell in self.inventory:
                                expected_notebooks.add(shell)
            if course.get('optional', []) != expected_optional:
                raise ValueError('content: optional selection differs from admitted assets')
        if actual_keys != selected_keys or set(self.notebooks) != expected_notebooks:
            raise ValueError('content: runtime Notebook selection differs from admitted assets')
        self.units = {u['id']: u for u in self.package['source_units']}
        self.anchor_rows = {r['unit_id']: r for r in read(self.root, 'indexes/anchors.json')}

    def resolve(self, path, kind, locale):
        from i18n import LocaleUnavailable
        if locale not in self.links or (kind in {'notebook', 'quiz'} and path not in self.notebooks):
            raise LocaleUnavailable('Content is outside the selected profile')
        try:
            relative, actual = self.links[locale][path]
            target = safe(self.root, relative)
            if digest(target.read_bytes()) != self.inventory[relative]['sha256']:
                raise ValueError('content: installed file changed')
            return relative, actual
        except KeyError as error:
            raise LocaleUnavailable('Content is unavailable') from error

    def ui(self):
        from i18n import InstalledUI
        dictionaries = {}
        for locale in self.links:
            path, actual = self.resolve('locales/ui.en.json', 'asset', locale)
            if actual != locale:
                raise ValueError('content: missing UI language')
            dictionaries[locale] = read(self.root, path)['messages']
        return InstalledUI(dictionaries, default=self.plan['content']['default_locale'], content=self.resolve,
                           labels=self.labels, anchors=self.anchors)

    def labels(self, kind, identity, values, locale):
        slug = identity if kind == 'course' else identity[0]
        course = next(c for c in self.plan['courses'] if c['slug'] == slug)
        document = read(self.root, f'rendered/{locale}/{course["course_dir"]}/course.json')
        if kind == 'course':
            selected = document
        elif kind == 'module':
            selected = next(m for m in document['modules'] if m['id'] == identity[1])
        elif kind == 'optional':
            selected = next(p for m in document['modules'] for p in m.get('optional', []) if p['path'] == identity[1])
        else:
            raise ValueError('content: unknown display kind')
        return {key: selected[key] for key in values}

    def headings(self, path):
        # Frozen section units occur in source order, independently of UUID order.
        source = safe(self.root, path).read_text()
        rows = [u for u in self.units.values() if u['source_path'] == path and u['locator']['kind'] == 'markdown-block'
                and re.match(r'^#{1,6} ', u['source_text'])]
        rows.sort(key=lambda u: source.index(u['source_text']))
        return rows

    def anchors(self, path, locale):
        return [self.anchor_rows[u['id']]['anchor'] for u in self.headings(path)][1:]

    def aliases(self, path):
        used, result = {}, []
        for unit in self.headings(path):
            title = unit['source_text'].splitlines()[0].lstrip('# ').lower()
            slug = re.sub(r'[^\w\- ]', '', title).replace(' ', '-')
            count = used.get(slug, 0); used[slug] = count + 1
            result.append(slug + (f'-{count}' if count else ''))
        return result[1:]

    def work_path(self, path, locale):
        if path not in self.notebooks or locale not in self.links:
            raise ValueError('content: Notebook is not selected')
        return f'work/{locale}/{path}'

    def copy(self, path, locale, base='/'):
        """Create once with an atomic no-replace link, including concurrent tabs."""
        relative, actual = self.resolve(path, 'notebook', locale)
        if actual != locale:
            raise ValueError('content: no translated Notebook template')
        name = self.work_path(path, locale)
        target = safe(self.workspace, name)
        if target.exists():
            if not target.is_file():
                raise ValueError('content: invalid working copy')
            return name
        notebook = read(self.root, relative)
        notebook.setdefault('metadata', {})['kernelspec'] = {
            'name': self.notebooks[path]['kernel'], 'display_name': self.notebooks[path]['kernel'], 'language': 'python'}
        from markdown_it import MarkdownIt
        parser = MarkdownIt('commonmark')
        english = {c['id']: c for c in read(self.root, path)['cells'] if c['cell_type'] == 'markdown'}
        cell_units = {u['locator']['value']: u for u in self.units.values()
                      if u['source_path'] == path and u['locator']['kind'] == 'notebook-cell'}
        used_slugs = {}
        def url(value):
            parts = urlsplit(value)
            if parts.scheme or parts.netloc or not parts.path or parts.path.startswith('/'):
                return value
            destination = posixpath.normpath(posixpath.join(posixpath.dirname(path), unquote(parts.path)))
            route = 'notebook' if destination.endswith('.ipynb') else 'content'
            if not destination.endswith(('.ipynb', '.md', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp')):
                selected, actual = self.resolve(destination, 'asset', locale)
                record = next(r for r in self.assets['downloads'] if r['path'] == selected)
                query = parts.query + ('&' if parts.query else '') + 'locale=' + actual
                return urlunsplit(('', '', base.rstrip('/') + '/workshop/artifact/' + record['sha256'] + '/' + quote(selected, safe='/'), query, parts.fragment))
            query = parts.query + ('&' if parts.query else '') + 'locale=' + locale
            return urlunsplit(('', '', base.rstrip('/') + '/workshop/' + route + '/' + quote(destination, safe='/'), query, parts.fragment))
        for cell in notebook['cells']:
            if cell['cell_type'] == 'markdown':
                text = ''.join(cell['source'])
                text = re.sub(r'(!?\[[^\]\n]*\]\()([^\s)]+)(\))', lambda m: m[1] + url(m[2]) + m[3], text)
                text = notebook_html_images(text, url, parser)
                source_tokens = parser.parse(''.join(english[cell['id']]['source']))
                target_tokens = parser.parse(text)
                source_titles = [source_tokens[i + 1].content for i, t in enumerate(source_tokens) if t.type == 'heading_open']
                target_lines = [t.map[0] for t in target_tokens if t.type == 'heading_open']
                if len(source_titles) != len(target_lines):
                    raise ValueError('content: Notebook heading count differs')
                insertions = []
                for title, line in zip(source_titles, target_lines):
                    slug = re.sub(r'[^\w\- ]', '', title.lower()).replace(' ', '-')
                    count = used_slugs.get(slug, 0); used_slugs[slug] = count + 1
                    insertions.append((line, slug + (f'-{count}' if count else '')))
                lines = text.splitlines(keepends=True)
                for line, alias in reversed(insertions):
                    lines.insert(line, '<a id="' + alias + '"></a>\n\n')
                unit = cell_units.get(cell['id'])
                if unit:
                    lines.insert(0, '<a id="' + self.anchor_rows[unit['id']]['anchor'] + '"></a>\n\n')
                text = ''.join(lines)
                cell['source'] = text
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(canonical(notebook) + b'\n')
                stream.flush(); os.fsync(stream.fileno())
            temporary.chmod(0o600)
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        return name

    def enrich(self):
        plan = copy.deepcopy(self.plan)
        for course in plan['courses']:
            for module in course['modules']:
                quiz = next(q for q in self.package['quizzes'] if q['source_path'] == course['course_dir'] + '/' + module['quiz'] and q['id'] in self.selected_quizzes)
                module['quiz_binding'] = quiz
                module['policy_revision'] = next(c['policy_revision'] for c in self.package['courses'] if c['slug'] == course['slug'])
                module['quiz_presentations'] = []
                for locale in self.links:
                    path, actual = self.resolve(quiz['source_path'], 'asset', locale)
                    if actual != locale:
                        raise ValueError('content: Quiz language is missing')
                    module['quiz_presentations'].append({'locale': locale, 'path': path, 'source_digest': self.inventory[path]['sha256']})
            for part in course.get('optional', []):
                if not part.get('quiz'):
                    continue
                quiz = next(q for q in self.package['quizzes'] if q['source_path'] == course['course_dir'] + '/' + part['quiz'] and q['id'] in self.selected_quizzes)
                part['quiz_binding'] = quiz
                part['policy_revision'] = next(c['policy_revision'] for c in self.package['courses'] if c['slug'] == course['slug'])
                part['quiz_presentations'] = []
                for locale in self.links:
                    path, actual = self.resolve(quiz['source_path'], 'asset', locale)
                    if actual != locale:
                        raise ValueError('content: optional Quiz language is missing')
                    part['quiz_presentations'].append({'locale': locale, 'path': path, 'source_digest': self.inventory[path]['sha256']})
        return plan


def configure_quiz(content, locale):
    """Bind unchanged Notebook calls to admitted presentations in this kernel."""
    from doris_labkit.quiz import CourseQuiz
    from doris_labkit.quizbank import load_quiz
    from doris_labkit.quizsession import Presentation
    original = CourseQuiz.from_yaml.__func__
    context = content.ui().context(explicit=locale)
    def installed(cls, path, **kwargs):
        path = Path(path).resolve()
        course_root = Path(os.environ['DORIS_WORKSHOP_SHARED_SOURCE']).resolve()
        relative = path.relative_to(course_root).as_posix()
        quiz = next((q for q in content.package['quizzes'] if q['source_path'] == relative and q['id'] in content.selected_quizzes), None)
        if quiz is None:
            raise ValueError('content: Quiz is outside the installed profile')
        selected, actual = content.resolve(relative, 'asset', locale)
        target = content.root / selected
        policy = next(c['policy_revision'] for c in content.package['courses'] if c['slug'] == quiz['course_slug'])
        presentation = Presentation.admitted(load_quiz(target, localized=True), quiz, locale=actual,
            policy_revision=policy, source_digest=digest(target.read_bytes()))
        return original(cls, target, presentation=presentation, messages=context.messages)
    CourseQuiz.from_yaml = classmethod(installed)
