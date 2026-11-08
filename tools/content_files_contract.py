"""Independent admission of authored course files, without translation overlays."""
from pathlib import Path, PurePosixPath
import copy
import re
import string
import posixpath
import yaml
from urllib.parse import unquote, urlsplit
from html.parser import HTMLParser

import content_contract_v2 as c

CONTRACT_VERSION = '3.0.0'
KIND = 'doris-course-files'
ABI = 'doris-workshop-content-3.0'
CAPABILITIES = ['course-files-v1', 'notebook-copies-v1', 'quiz-presentations-v2']
TRANSFORMS = {'identity', 'notebook-markdown-style-v1', 'yaml-to-json-v1', 'ui-yaml-to-json-v1'}
read_json, safe_path, sha256, canonical, digest = c.read_json, c.safe_path, c.sha256, c.canonical, c.digest


def load(data):
    if any(isinstance(token, (yaml.AliasToken, yaml.AnchorToken)) for token in yaml.scan(data)):
        c.fail('structured course YAML cannot contain aliases or anchors')
    return c.bounded(c.v1.load(data.decode('utf-8')))


def grading(bank):
    return {'passing_score_percent': bank['passing_score_percent'], 'questions': sorted([
        {'id': q['id'], 'type': q['type'], 'option_ids': sorted(o['id'] for o in q['options']),
         'answer': q['answer']} for q in bank['questions']], key=lambda q: q['id'])}


def validate_bank(bank):
    c.shape(bank, 'authoring_quiz')
    c.index_by(bank['questions'], 'id', 'Quiz question')
    for question in bank['questions']:
        options = c.index_by(question['options'], 'id', 'Quiz option')
        if question['answer'] not in options:
            c.fail('Quiz answer is not an option ID')


def policy(course):
    return {k: course[k] for k in ('slug', 'status', 'completion', 'certificate')} | {
        'modules': [{k: m[k] for k in ('id', 'status', 'required', 'requirements')} for m in course['modules']]}


def presentation(bank):
    return digest({'title': bank['title'], 'description': bank['description'], 'questions': [
        {key: question[key] for key in ('id', 'objective', 'prompt', 'options')}
        for question in bank['questions']]})


def semantic_token(path, source_ref):
    # Public identity derives from a Git version, never from private answers.
    return 'quiz.' + sha256(('doris-course-files:' + source_ref + ':' + path).encode())[:32]


def project(raw, transformation):
    if transformation not in TRANSFORMS:
        c.fail('unsupported file projection')
    if transformation == 'identity':
        return raw
    if transformation == 'notebook-markdown-style-v1':
        from markdown_it import MarkdownIt
        notebook = read_json(raw)
        original = copy.deepcopy(notebook)
        for cell in notebook['cells']:
            if cell['cell_type'] != 'markdown':
                continue
            text = ''.join(cell['source']) if isinstance(cell['source'], list) else cell['source']
            offsets = [0]
            for line in text.splitlines(keepends=True):
                offsets.append(offsets[-1] + len(line))
            protected = [(offsets[t.map[0]], offsets[t.map[1]]) for t in MarkdownIt().parse(text)
                         if t.type in {'fence', 'code_block'}]
            protected.extend((m.start(), m.end()) for m in re.finditer(r'(`+)(.*?)\1', text, re.S))
            chunks, end = [], 0
            for match in re.finditer(r'<[A-Za-z][^>]*>', text):
                if any(a <= match.start() < b for a, b in protected):
                    continue
                chunks.extend([text[end:match.start()], re.sub(
                    r"\sstyle\s*=\s*(?:\"[^\"]*\"|'[^']*')", '', match.group(), flags=re.I)])
                end = match.end()
            chunks.append(text[end:])
            clean = ''.join(chunks)
            if clean != text:
                cell['source'] = clean.splitlines(keepends=True)
        return raw if notebook == original else canonical(notebook) + b'\n'
    value = load(raw)
    if transformation == 'ui-yaml-to-json-v1':
        value = {'kind': 'doris-ui-messages', 'messages': value}
    return canonical(value) + b'\n'


def package_digest(package):
    value = copy.deepcopy(package)
    value['snapshot'].pop('package_digest', None)
    return digest(value)


def locale_digest(package, locale):
    return digest([a for a in package['assets'] if a['locale'] in {locale, 'shared'}])


def structure(course):
    return [{k: (m.get(k) if k != 'optional' else [
        {name: value for name, value in part.items() if name != 'title'} for part in m.get('optional', [])])
             for k in ('id', 'level', 'order', 'course', 'lab', 'quiz', 'optional')} for m in course['modules']]


def local_references(text, path):
    from markdown_it import MarkdownIt
    values = []

    class Html(HTMLParser):
        def handle_starttag(self, tag, attrs):
            values.extend(v for k, v in attrs if k in {'src', 'href'} and v)

    def walk(tokens):
        for token in tokens:
            if token.type in {'html_inline', 'html_block'}:
                Html().feed(token.content)
            if token.type in {'image', 'link_open'}:
                values.append(token.attrGet('src' if token.type == 'image' else 'href'))
            if token.children:
                walk(token.children)
    walk(MarkdownIt().parse(text))
    for value in values:
        parsed = urlsplit(value)
        if not parsed.scheme and not parsed.netloc and parsed.path and not parsed.path.startswith('/'):
            yield posixpath.normpath(posixpath.join(posixpath.dirname(path), unquote(parsed.path)))


def validate_package(root, *, source=None, expected_ref=None, expected_digest=None):
    root = Path(root).resolve()
    package = read_json(safe_path(root, 'package.v3.json').read_bytes())
    fields = {'schema_version', 'contract_version', 'kind', 'purpose', 'package_state', 'catalog',
              'courses', 'course_sources', 'default_locale', 'available_locales', 'locale_digests',
              'profiles', 'selected_profiles', 'assets', 'quizzes', 'snapshot'}
    if (set(package) != fields or type(package['schema_version']) is not int or package['schema_version'] != 3
            or package['contract_version'] != CONTRACT_VERSION or package['kind'] != KIND
            or package['purpose'] != 'teaching' or package['package_state'] != 'release'):
        c.fail('unsupported course-files package')
    snapshot = package['snapshot']
    if (set(snapshot) != {'source_ref', 'builder_version', 'files', 'package_digest'}
            or not re.fullmatch(r'[0-9a-f]{40}', snapshot['source_ref'])
            or not re.fullmatch(r'course-files-1\.[0-9a-f]{64}', snapshot['builder_version'])):
        c.fail('invalid course-files source/builder binding')
    locales = package['available_locales']
    if (not isinstance(locales, list) or not locales or locales[0] != 'en' or len(set(locales)) != len(locales)
            or any(tag not in {r['tag'] for r in c.REGISTRY['locales']} for tag in locales)
            or package['default_locale'] not in locales or set(package['locale_digests']) != set(locales)):
        c.fail('invalid installed language set')
    inventory = c.index_by(snapshot['files'], 'path', 'file')
    if len(inventory) > 10000 or sum(r['size'] for r in inventory.values()) > c.MAX_TOTAL_BYTES:
        c.fail('course-files package exceeds limits')
    files = {}
    for path, record in inventory.items():
        if set(record) != {'path', 'size', 'sha256'} or type(record['size']) is not int or not 0 <= record['size'] <= c.MAX_FILE_BYTES:
            c.fail('invalid course file record')
        raw = safe_path(root, path).read_bytes()
        if len(raw) != record['size'] or sha256(raw) != record['sha256']:
            c.fail('course file hash differs: ' + path)
        files[path] = raw
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if not p.is_dir()}
    if any(p.is_symlink() for p in root.rglob('*')) or actual != set(files) | {'package.v3.json'}:
        c.fail('unexpected course package files or symlinks')
    inputs = read_json(files['indexes/source-inputs.json'])
    if set(inputs) != {'source_ref', 'inputs', 'projections'} or inputs['source_ref'] != snapshot['source_ref']:
        c.fail('source-input provenance differs')
    sources = c.index_by(inputs['inputs'], 'path', 'source input')
    projected = set()
    projection_rows = {}
    for row in inputs['projections']:
        if set(row) != {'source', 'path', 'transformation'} or row['source'] not in sources or row['path'] in projected:
            c.fail('invalid or duplicate course projection')
        projected.add(row['path'])
        projection_rows[row['path']] = row
        if row['source'] not in files or row['path'] not in files:
            c.fail('projection lacks raw source or output')
        if project(files[row['source']], row['transformation']) != files[row['path']]:
            c.fail('authored course projection differs: ' + row['path'])
    for path, record in sources.items():
        if (set(record) != {'path', 'size', 'sha256'} or type(record['size']) is not int
                or not 0 <= record['size'] <= c.MAX_FILE_BYTES
                or not re.fullmatch(r'[0-9a-f]{64}', record['sha256'])):
            c.fail('invalid raw source input record')
        raw = files.get(path)
        if raw is None or len(raw) != record['size'] or sha256(raw) != record['sha256']:
            c.fail('raw course source differs: ' + path)
        if source is not None and safe_path(source, path).read_bytes() != raw:
            c.fail('independent Git source differs: ' + path)
    config = load(files['locales/course-files.yaml'])
    if config.get('schema_version') != 1 or set(package['course_sources']) != set(locales):
        c.fail('invalid course-files configuration')
    if package['catalog'] != config['locales']['en']['catalog']:
        c.fail('canonical catalog source differs')
    native_courses = {}
    for tag in locales:
        settings = config['locales'][tag]
        native_catalog = load(files[settings['catalog']]); c.shape(native_catalog, 'catalog')
        native_courses[tag] = {}
        for path in native_catalog['courses']:
            document = load(files[path])
            native_courses[tag][document['slug']] = str(PurePosixPath(path).parent)
        if native_courses[tag] != package['course_sources'][tag]:
            c.fail('language course roots differ from native catalog')
    canonical_roots = native_courses['en']
    builder = 'tools/course_files_producer.py'
    if snapshot['builder_version'] != 'course-files-1.' + sha256(files[builder]):
        c.fail('builder provenance differs')
    assets = c.index_by(package['assets'], 'id', 'asset')
    by_path = {}
    for asset in assets.values():
        required = {'id', 'kind', 'locale', 'file', 'source_asset_hash', 'source_path', 'source_file'}
        if (set(asset) != required or asset['locale'] not in {*locales, 'shared'}
                or asset['kind'] not in {'lesson', 'notebook', 'quiz', 'metadata', 'ui', 'image', 'code', 'data'}
                or asset['source_file'] not in sources or asset['file'] != inventory.get(asset['file']['path'])
                or asset['file']['path'] in by_path):
            c.fail('invalid course asset')
        by_path[asset['file']['path']] = asset
        tag, logical = asset['locale'], asset['source_path']
        if logical == 'locales/ui.en.json' and tag != 'shared':
            physical = config['locales'][tag]['ui']
            transform = 'identity' if physical.endswith('.json') else 'ui-yaml-to-json-v1'
            expected_kind = 'ui'
        elif logical == 'catalog.json' and tag != 'shared':
            physical = config['locales'][tag]['catalog']
            transform, expected_kind = 'yaml-to-json-v1', 'metadata'
        elif logical == 'locales/ui.contract.json' or logical.startswith('doris-course/common/'):
            if tag != 'shared':
                c.fail('common infrastructure must stay shared')
            physical, transform, expected_kind = logical, 'identity', None
        else:
            slug = next((slug for slug, base in canonical_roots.items() if logical.startswith(base + '/')), None)
            if slug is None or tag == 'shared' or slug not in native_courses[tag]:
                c.fail('asset is outside its language course roots')
            relative = logical[len(canonical_roots[slug]) + 1:]
            physical = native_courses[tag][slug] + '/' + ('course.yaml' if relative == 'course.json' else relative)
            expected_kind = 'metadata' if relative == 'course.json' else None
            transform = ('yaml-to-json-v1' if expected_kind else
                         'notebook-markdown-style-v1' if logical.endswith('.ipynb') else 'identity')
        expected_path = logical if tag in {'en', 'shared'} and transform == 'identity' else f'rendered/{tag}/{logical}'
        if (asset['source_file'] != physical or asset['file']['path'] != expected_path
                or projection_rows.get(expected_path) != {'source': physical, 'path': expected_path, 'transformation': transform}
                or (expected_kind and asset['kind'] != expected_kind)):
            c.fail('asset projection differs from its native language source')
        raw = files[asset['file']['path']]
        suffix = Path(asset['source_path']).suffix.lower()
        expected_kind = ('data' if tag == 'shared' and suffix == '.md' else
                         'notebook' if suffix == '.ipynb' else 'lesson' if suffix == '.md'
                         else 'image' if suffix in {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'}
                         else 'code' if suffix in {'.py', '.sh', '.sql'}
                         else 'quiz' if suffix in {'.yaml', '.yml'} and Path(asset['source_path']).name.startswith('quiz') else None)
        if expected_kind and asset['kind'] != expected_kind:
            c.fail('course asset kind differs from its file type: ' + asset['source_path'])
        if asset['kind'] == 'image' and Path(asset['source_path']).suffix.lower() == '.svg':
            c.safe_svg(raw)
        if asset['kind'] == 'lesson':
            c.safe_markdown(raw.decode(), asset['source_file'], files)
        if asset['kind'] == 'notebook':
            notebook = read_json(raw)
            ids = set()
            if notebook.get('nbformat') != 4 or not isinstance(notebook.get('cells'), list):
                c.fail('invalid course Notebook')
            for cell in notebook['cells']:
                if not isinstance(cell.get('id'), str) or cell['id'] in ids:
                    c.fail('missing or duplicate Notebook cell ID')
                ids.add(cell['id'])
                text = ''.join(cell['source']) if isinstance(cell['source'], list) else cell['source']
                if cell['cell_type'] == 'code':
                    if cell.get('outputs') or cell.get('execution_count') is not None:
                        c.fail('course Notebook contains outputs')
                elif cell['cell_type'] == 'markdown':
                    c.safe_markdown(text, asset['source_file'], files)
                else:
                    c.fail('unsupported Notebook cell')
    index = read_json(files['indexes/assets.json'])
    if set(index) != {'assets', 'links', 'downloads', 'closure_paths', 'shared_images'} or index['assets'] != package['assets']:
        c.fail('asset index differs')
    linked = set()
    for row in index['links']:
        asset = by_path.get(row['path'])
        key = row['locale'], row['source_path']
        if (set(row) != {'path', 'locale', 'source_path', 'source_file'} or key in linked or not asset
                or any(row[k] != asset[k] for k in ('locale', 'source_path', 'source_file'))):
            c.fail('ambiguous or invalid locale asset link')
        linked.add(key)
    if len(linked) != len(assets):
        c.fail('missing locale asset links')
    for row in index['downloads']:
        asset = assets.get(row['asset_id'])
        if (not asset or row != {'asset_id': asset['id'], 'path': asset['file']['path'],
                               'sha256': asset['file']['sha256'], 'delivery': 'immutable-artifact',
                               'source_ref': snapshot['source_ref']}):
            c.fail('invalid download binding')
    if (len(index['downloads']) != len(assets)
            or {r['asset_id'] for r in index['downloads']} != set(assets)):
        c.fail('incomplete or duplicate download index')
    ui_contract = read_json(files['locales/ui.contract.json'])
    for tag in locales:
        ui_asset = next(a for a in assets.values() if a['kind'] == 'ui' and a['locale'] == tag)
        messages = read_json(files[ui_asset['file']['path']])['messages']
        if set(messages) != set(ui_contract['parameters']):
            c.fail('UI message keys differ from installed contract')
        for key, value in messages.items():
            if not isinstance(value, str) or not value:
                c.fail('empty or non-text UI message')
            try:
                fields = [name for _, name, spec, conversion in string.Formatter().parse(value) if name is not None]
            except ValueError as error:
                c.fail('invalid UI template: ' + str(error))
            if set(fields) != set(ui_contract['parameters'][key]):
                c.fail('UI placeholders differ: ' + key)
    catalog = load(files[package['catalog']]); c.shape(catalog, 'catalog')
    courses = c.index_by(package['courses'], 'slug', 'course')
    if [b['manifest'] for b in courses.values()] != catalog['courses']:
        c.fail('course bindings differ from canonical catalog')
    course_documents = {}
    for slug, binding in courses.items():
        if set(binding) != {'slug', 'manifest', 'policy_revision'}:
            c.fail('unknown course binding fields')
        canonical_course = load(files[binding['manifest']]); c.shape(canonical_course, 'course')
        c.index_by(canonical_course['modules'], 'id', 'course module')
        if canonical_course['slug'] != slug:
            c.fail('course identity differs')
        course_documents[slug] = canonical_course
        meaning = policy(canonical_course)
        old = config['legacy_revisions']['policies'].get(slug)
        expected_revision = (old['revision'] if old and old.get('payload_digest') == digest(meaning)
                             else 'policy.' + digest(meaning)[:32])
        if binding['policy_revision'] != expected_revision:
            c.fail('course policy revision differs from authored identity')
        for tag in locales:
            physical = package['course_sources'].get(tag, {}).get(slug)
            if not physical:
                continue
            actual_course = load(files[physical + '/course.yaml'])
            checked_course = copy.deepcopy(actual_course); checked_course['language'] = 'en'; c.shape(checked_course, 'course')
            c.index_by(actual_course['modules'], 'id', 'locale course module')
            if (actual_course.get('language') != tag or policy(actual_course) != policy(canonical_course)
                    or structure(actual_course) != structure(canonical_course)):
                c.fail('locale course identity/completion policy differs: ' + slug)
            base = str(PurePosixPath(binding['manifest']).parent)
            output = f'rendered/{tag}/{base}/course.json'
            if read_json(files[output]) != actual_course:
                c.fail('course metadata projection differs')
    quizzes = c.index_by(package['quizzes'], 'id', 'quiz')
    declared_quizzes = set()
    for slug, course in course_documents.items():
        base = str(PurePosixPath(courses[slug]['manifest']).parent)
        for module in course['modules']:
            if module.get('quiz'):
                declared_quizzes.add(base + '/' + module['quiz'])
            declared_quizzes.update(base + '/' + p['quiz'] for p in module.get('optional', []) if p.get('quiz'))
    if {q['source_path'] for q in quizzes.values()} != declared_quizzes:
        c.fail('registered Quiz banks differ from course declarations')
    for asset in assets.values():
        if asset['source_path'] in declared_quizzes and asset['kind'] != 'quiz':
            c.fail('registered Quiz bank cannot be a public data attachment')
    for quiz in quizzes.values():
        if set(quiz) != {'id', 'course_slug', 'module_id', 'source_path', 'semantic_revision', 'semantic_source_ref',
                         'passing_score_percent', 'questions', 'presentations'}:
            c.fail('unknown Quiz descriptor fields')
        bank = load(files[quiz['source_path']]); validate_bank(bank)
        meaning = grading(bank)
        ordered = lambda document: [{'id': q['id'], 'type': q['type'], 'option_ids': [o['id'] for o in q['options']],
                                      'answer': q['answer']} for q in document['questions']]
        machine = {'passing_score_percent': bank['passing_score_percent'], 'questions': ordered(bank)}
        old = config['legacy_revisions']['quizzes'].get(quiz['source_path'])
        if old and old.get('grading_digest') == digest(machine):
            expected_revision = old['revision']
            if quiz['semantic_source_ref'] is not None:
                c.fail('legacy Quiz identity has a forged source origin')
        else:
            origin = quiz['semantic_source_ref']
            if not isinstance(origin, str) or not re.fullmatch(r'[0-9a-f]{40}', origin):
                c.fail('new Quiz identity requires its grading-change Git origin')
            expected_revision = semantic_token(quiz['source_path'], origin)
        if (quiz['id'] != 'q.' + sha256(quiz['source_path'].encode())[:32]
                or quiz['passing_score_percent'] != bank['passing_score_percent']
                or quiz['questions'] != ordered(bank) or quiz['semantic_revision'] != expected_revision):
            c.fail('Quiz grading identity differs')
        seen = set()
        for row in quiz['presentations']:
            if set(row) != {'locale', 'presentation_revision', 'unit_ids'} or row['unit_ids'] != []:
                c.fail('invalid file-based Quiz presentation fields')
            tag = row['locale']
            if tag in seen or tag not in locales:
                c.fail('duplicate Quiz presentation')
            seen.add(tag)
            asset = next((a for a in assets.values() if a['source_path'] == quiz['source_path'] and a['locale'] == tag), None)
            if not asset or asset['kind'] != 'quiz':
                c.fail('Quiz presentation file missing')
            local = load(files[asset['file']['path']]); validate_bank(local)
            if (grading(local) != meaning or ordered(local) != ordered(bank)
                    or row['presentation_revision'] != presentation(local)):
                c.fail('locale Quiz changes grading or display binding')
    profiles = c.index_by(package['profiles'], 'id', 'profile')
    if not package['selected_profiles'] or list(profiles) != package['selected_profiles']:
        c.fail('profile selection/order differs')
    for profile in profiles.values():
        if set(profile) != {'id', 'modules', 'asset_ids', 'quiz_ids'} or len(set(profile['asset_ids'])) != len(profile['asset_ids']):
            c.fail('invalid course profile')
        if set(profile['asset_ids']) - set(assets) or set(profile['quiz_ids']) - set(quizzes):
            c.fail('profile references unknown assets or quizzes')
        definition = next((p for p in config['profiles'] if p['id'] == profile['id']), None)
        if not definition or profile['modules'] != [{k: row[k] for k in ('course_slug', 'module_id')} for row in definition['modules']]:
            c.fail('profile does not contain its declared complete module selection')
        for key in profile['modules']:
            course = course_documents[key['course_slug']]
            module = next(m for m in course['modules'] if m['id'] == key['module_id'])
            base = str(PurePosixPath(courses[key['course_slug']]['manifest']).parent)
            for tag in locales:
                if key['course_slug'] not in package['course_sources'].get(tag, {}):
                    c.fail('selected course unavailable in locale')
                for field in ('course', 'lab', 'quiz'):
                    path = base + '/' + module[field]
                    asset = next((a for a in assets.values() if a['source_path'] == path and a['locale'] == tag), None)
                    if not asset or asset['id'] not in profile['asset_ids']:
                        c.fail('primary course file omitted from profile')
                shell = str(PurePosixPath(base + '/' + module['quiz']).with_suffix('.ipynb'))
                if shell in files:
                    item = next((a for a in assets.values() if a['source_path'] == shell and a['locale'] == tag), None)
                    if not item or item['id'] not in profile['asset_ids']:
                        c.fail('primary Quiz Notebook shell omitted from profile')
                quiz = next(q for q in quizzes.values() if q['source_path'] == base + '/' + module['quiz'])
                if quiz['id'] not in profile['quiz_ids'] or tag not in {r['locale'] for r in quiz['presentations']}:
                    c.fail('primary Quiz omitted from profile')
                chosen = next(r for r in definition['modules'] if r['course_slug'] == key['course_slug'] and r['module_id'] == key['module_id'])
                parts = {p['path']: p for p in module.get('optional', [])}
                for relative in chosen['optional_paths']:
                    if relative not in parts:
                        c.fail('profile selects unknown optional material')
                    part = parts[relative]
                    required = [relative]
                    if part.get('quiz'):
                        required.extend([part['quiz'], str(PurePosixPath(part['quiz']).with_suffix('.ipynb'))])
                        optional_quiz = next(q for q in quizzes.values() if q['source_path'] == base + '/' + part['quiz'])
                        if optional_quiz['id'] not in profile['quiz_ids']:
                            c.fail('optional Quiz omitted from profile')
                        if tag not in {r['locale'] for r in optional_quiz['presentations']}:
                            c.fail('optional Quiz language presentation missing')
                    for relative_path in required:
                        item = next((a for a in assets.values() if a['source_path'] == base + '/' + relative_path and a['locale'] == tag), None)
                        if not item or item['id'] not in profile['asset_ids']:
                            c.fail('optional file omitted from profile')
        for aid in profile['asset_ids']:
            asset = assets[aid]
            texts = []
            if asset['kind'] == 'lesson':
                texts = [files[asset['file']['path']].decode()]
            elif asset['kind'] == 'notebook':
                texts = [''.join(cell['source']) for cell in read_json(files[asset['file']['path']])['cells'] if cell['cell_type'] == 'markdown']
            for text in texts:
                for referenced in local_references(text, asset['source_file']):
                    dependency = next((a for a in assets.values() if a['source_file'] == referenced), None)
                    if dependency and dependency['kind'] in {'image', 'code', 'data'} and dependency['id'] not in profile['asset_ids']:
                        c.fail('profile omits referenced resource: ' + referenced)
    for tag in locales:
        if locale_digest(package, tag) != package['locale_digests'][tag]:
            c.fail('locale digest differs')
    if package_digest(package) != snapshot['package_digest']:
        c.fail('course package digest differs')
    if expected_ref is not None and snapshot['source_ref'] != expected_ref:
        c.fail('unexpected course source SHA')
    if expected_digest is not None and snapshot['package_digest'] != expected_digest:
        c.fail('unexpected course package digest')
    return {'contract_version': CONTRACT_VERSION, 'available_locales': locales,
            'selected_profiles': package['selected_profiles'], 'package_digest': snapshot['package_digest'],
            'coverage': {tag: {'missing_units': [], 'missing_assets': [], 'missing_quizzes': []} for tag in locales}}
