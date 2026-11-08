"""Build independent language course trees into an immutable teaching release."""
import copy
import importlib.util
from pathlib import Path, PurePosixPath
import posixpath
import tempfile

import content_files_contract as f
import content_producer as legacy

CONFIG = 'locales/course-files.yaml'
UI = 'locales/ui.en.json'
UI_CONTRACT = 'locales/ui.contract.json'


def record(path, data):
    return {'path': path, 'size': len(data), 'sha256': f.sha256(data)}


def identity(path):
    return 'a.' + f.sha256(path.encode())[:32]


def data(value):
    return f.canonical(value) + b'\n'


def revision(kind, value, old=None, digest_key='payload_digest'):
    value_digest = f.digest(value)
    return old['revision'] if old and old.get(digest_key) == value_digest else kind + '.' + value_digest[:32]


def ordered_grading(bank):
    return {'passing_score_percent': bank['passing_score_percent'], 'questions': [
        {'id': q['id'], 'type': q['type'], 'option_ids': [o['id'] for o in q['options']], 'answer': q['answer']}
        for q in bank['questions']]}


def grading_origin(source, path, meaning):
    origin = source.ref
    if not hasattr(source, 'git'):
        return origin  # Explicit synthetic source fixture, never a Git release.
    for commit in source.git('log', '--format=%H', source.ref, '--', path).decode().splitlines():
        try:
            previous = f.load(source.git('show', commit + ':' + path))
            if ordered_grading(previous) != meaning:
                break
        except (ValueError, KeyError, TypeError):
            break
        origin = commit
    return origin


def build_tree(source, selected, locales, default='en'):
    for name in ('course_files_producer.py', 'content_files_contract.py'):
        if source.read('tools/' + name) != Path(__file__).with_name(name).read_bytes():
            raise ValueError('source commit does not match the installed course-files builder: ' + name)
    config = f.load(source.read(CONFIG))
    if (set(config) != {'schema_version', 'locales', 'profiles', 'legacy_revisions'} or config['schema_version'] != 1
            or not locales or locales[0] != 'en' or len(set(locales)) != len(locales) or default not in locales):
        raise ValueError('invalid course-file configuration or languages')
    if any(tag not in config['locales'] for tag in locales):
        raise ValueError('unregistered course-file locale')
    all_paths = set(source.paths())
    raw, inputs, projections, assets, links = {}, {}, [], [], []

    def include(path):
        legacy.checked_path(path)
        if path not in raw:
            raw[path] = source.read(path)
            inputs[path] = record(path, raw[path])
        return raw[path]

    def output(path, physical, transform='identity'):
        value = f.project(include(physical), transform)
        if path == physical and value != raw[path]:
            raise ValueError('projection cannot replace a raw source')
        raw[path] = value
        projections.append({'source': physical, 'path': path, 'transformation': transform})
        return value

    catalogs, courses, course_sources = {}, {}, {}
    for tag in locales:
        settings = config['locales'][tag]
        catalog = f.load(include(settings['catalog'])); f.c.shape(catalog, 'catalog')
        catalogs[tag] = catalog
        courses[tag], course_sources[tag] = {}, {}
        for manifest in catalog['courses']:
            doc = f.load(include(manifest))
            check = copy.deepcopy(doc); check['language'] = 'en'; f.c.shape(check, 'course')
            if doc['language'] != tag or doc['slug'] in courses[tag]:
                raise ValueError('invalid or duplicate locale course identity')
            root = posixpath.dirname(manifest)
            if not root.startswith(settings['root'] + '/'):
                raise ValueError('course outside its declared language root')
            courses[tag][doc['slug']] = (manifest, doc)
            course_sources[tag][doc['slug']] = root
    english = courses['en']
    for tag in locales[1:]:
        for slug, (_, doc) in courses[tag].items():
            if slug not in english or f.policy(doc) != f.policy(english[slug][1]):
                raise ValueError('locale course changes stable identity/completion policy: ' + slug)
            original = english[slug][1]
            paths = lambda value: [{k: m.get(k) for k in ('id', 'level', 'order', 'course', 'lab', 'quiz', 'optional')}
                                   for m in value['modules']]
            a, b = paths(original), paths(doc)
            for values in (a, b):
                for module in values:
                    module['optional'] = [{k: v for k, v in part.items() if k != 'title'} for part in module['optional'] or []]
            if a != b:
                raise ValueError('locale module identity/asset paths differ: ' + slug)
    # Freeze every original/locale course file as raw provenance, including code,
    # data and environments. No source map, overlay, batch or LLM check is read.
    roots = {root for by_slug in course_sources.values() for root in by_slug.values()} | {'doris-course/common'}
    for path in sorted(all_paths):
        if legacy.c.PATH.fullmatch(path) and any(path.startswith(root + '/') for root in roots):
            include(path)
    for path in (CONFIG, UI, UI_CONTRACT, 'tools/course_files_producer.py', 'tools/content_files_contract.py',
                 'contracts/v2/schema.json', 'contracts/v2/locales.json', 'contracts/v1/schema.json'):
        include(path)
    canonical_roots = {slug: posixpath.dirname(row[0]) for slug, row in english.items()}
    teaching, quiz_owners = {}, {}
    for slug, (manifest, course) in english.items():
        base = canonical_roots[slug]
        types = {base + '/' + course['lab_setup']['guide']: 'lesson'}
        for path in all_paths:
            if path.startswith(base + '/level') and path.endswith(('.md', '.ipynb')):
                types[path] = 'notebook' if path.endswith('.ipynb') else 'lesson'
        for module in course['modules']:
            for relative in [module.get('quiz'), *(p.get('quiz') for p in module.get('optional', []))]:
                if relative:
                    path = base + '/' + relative; types[path] = 'quiz'; quiz_owners[path] = (slug, module['id'])
        teaching.update(types)
    asset_lookup = {}

    def add_asset(path, physical, tag, kind, transform='identity'):
        # English notebooks keep canonical raw bytes for source links; their
        # normalized installed template is generated under rendered/en.
        target = path if tag in {'en', 'shared'} and transform == 'identity' else 'rendered/' + tag + '/' + path
        value = output(target, physical, transform)
        aid = identity(path) + ('.' + tag.lower() if tag not in {'en', 'shared'} else '')
        if (tag, path) in asset_lookup:
            raise ValueError('duplicate locale asset')
        asset = {'id': aid, 'kind': kind, 'locale': tag, 'file': record(target, value),
                 'source_asset_hash': None, 'source_path': path, 'source_file': physical}
        assets.append(asset); asset_lookup[tag, path] = asset
        links.append({'path': target, 'locale': tag, 'source_path': path, 'source_file': physical})

    # Metadata uses the same normalized JSON projection as the legacy consumers.
    for tag in locales:
        for slug, (manifest, doc) in courses[tag].items():
            base = canonical_roots[slug]
            add_asset(base + '/course.json', manifest, tag, 'metadata', 'yaml-to-json-v1')
            physical_root = course_sources[tag][slug]
            for physical in sorted(p for p in all_paths if p.startswith(physical_root + '/') and p != manifest and legacy.c.PATH.fullmatch(p)):
                canonical_path = base + physical[len(physical_root):]
                suffix = Path(physical).suffix.lower()
                if canonical_path in teaching:
                    kind = teaching[canonical_path]
                elif suffix == '.md':
                    kind = 'lesson'
                elif suffix == '.ipynb':
                    kind = 'notebook'
                elif suffix in legacy.IMAGE:
                    kind = 'image'
                elif suffix in {'.yaml', '.yml'} and Path(physical).name.startswith('quiz'):
                    # Unregistered practice banks remain private too.
                    kind = 'quiz'
                else:
                    kind = 'code' if suffix in {'.py', '.sh', '.sql'} else 'data'
                transform = 'notebook-markdown-style-v1' if kind == 'notebook' else 'identity'
                add_asset(canonical_path, physical, tag, kind, transform)
        settings = config['locales'][tag]
        ui_path = settings['ui']
        add_asset(UI, ui_path, tag, 'ui', 'identity' if ui_path.endswith('.json') else 'ui-yaml-to-json-v1')
        add_asset('catalog.json', settings['catalog'], tag, 'metadata', 'yaml-to-json-v1')
    for physical in sorted(p for p in all_paths if p.startswith('doris-course/common/')):
        kind = 'code' if Path(physical).suffix.lower() in {'.py', '.sh', '.sql'} else 'data'
        add_asset(physical, physical, 'shared', kind)
    add_asset(UI_CONTRACT, UI_CONTRACT, 'shared', 'data')
    # All relative links are checked against their actual authoring paths. Pull
    # any explicitly referenced repository files into the private frozen tree.
    pending = list(raw)
    visited = set()
    while pending:
        physical = pending.pop()
        if physical in visited:
            continue
        visited.add(physical)
        if physical not in inputs:
            continue
        for text in legacy.texts(raw[physical], physical):
            for url in legacy.links(text):
                target = legacy.relative_target(physical, url)
                if target and target not in raw:
                    include(target); pending.append(target)
    quizzes = []
    old_revisions = config['legacy_revisions']
    for path, (slug, mid) in sorted(quiz_owners.items()):
        bank = f.load(raw[path]); f.c.shape(bank, 'authoring_quiz')
        meaning = f.grading(bank)
        machine = ordered_grading(bank)
        old = old_revisions['quizzes'].get(path)
        preserved = old and old.get('grading_digest') == f.digest(machine)
        origin = None if preserved else grading_origin(source, path, machine)
        descriptor = {'id': 'q.' + f.sha256(path.encode())[:32], 'course_slug': slug, 'module_id': mid,
                      'source_path': path, 'semantic_revision': old['revision'] if preserved else f.semantic_token(path, origin),
                      'semantic_source_ref': origin,
                      'passing_score_percent': bank['passing_score_percent'], 'questions': [
                          {'id': q['id'], 'type': q['type'], 'option_ids': [o['id'] for o in q['options']],
                           'answer': q['answer']} for q in bank['questions']], 'presentations': []}
        for tag in locales:
            asset = asset_lookup.get((tag, path))
            if asset:
                local = f.load(raw[asset['file']['path']]); f.c.shape(local, 'authoring_quiz')
                if f.grading(local) != meaning:
                    raise ValueError('locale Quiz changes stable grading identity: ' + path)
                descriptor['presentations'].append({'locale': tag, 'presentation_revision': f.presentation(local), 'unit_ids': []})
        quizzes.append(descriptor)
    # Validate UI placeholders directly; this is app copy, never teaching units.
    spec = importlib.util.spec_from_file_location('course_file_messages', Path(__file__).resolve().parents[1] / 'doris-course/common/doris_labkit/messages.py')
    messages = importlib.util.module_from_spec(spec); spec.loader.exec_module(messages)
    source_ui = f.read_json(raw[asset_lookup['en', UI]['file']['path']])['messages']
    for tag in locales:
        target_ui = f.read_json(raw[asset_lookup[tag, UI]['file']['path']])['messages']
        messages.Messages(tag, source=source_ui, messages=target_ui, contract=f.read_json(raw[UI_CONTRACT]))
    definitions = {p['id']: p for p in config['profiles']}
    if len(selected) != len(set(selected)) or any(p not in definitions for p in selected):
        raise ValueError('unknown or duplicate course profile')
    profiles = []
    for name in selected:
        chosen = definitions[name]
        wanted, keys = {UI, UI_CONTRACT, 'catalog.json'}, []
        for selection in chosen['modules']:
            slug, mid = selection['course_slug'], selection['module_id']
            base = canonical_roots[slug]; doc = english[slug][1]
            module = next(m for m in doc['modules'] if m['id'] == mid)
            if module['status'] != 'published':
                raise ValueError('profile selects unavailable module')
            if any(slug not in courses[tag] for tag in locales):
                raise ValueError('selected course missing in requested language')
            keys.append({'course_slug': slug, 'module_id': mid})
            wanted.update({base + '/course.json', base + '/' + doc['lab_setup']['guide']})
            wanted.update(base + '/' + module[k] for k in ('course', 'lab', 'quiz'))
            wanted.add(str(PurePosixPath(base + '/' + module['quiz']).with_suffix('.ipynb')))
            parts = {p['path']: p for p in module.get('optional', [])}
            for path in selection['optional_paths']:
                part = parts[path]; wanted.add(base + '/' + path)
                if part.get('quiz'):
                    wanted.add(base + '/' + part['quiz'])
                    wanted.add(str(PurePosixPath(base + '/' + part['quiz']).with_suffix('.ipynb')))
            wanted.update(a['source_path'] for a in assets if a['source_path'].startswith((base + '/', 'doris-course/common/'))
                          and a['kind'] in {'code', 'data'})
        # Asset references in each selected locale independently extend closure,
        # so a new Kazakh-only image/attachment needs no English counterpart.
        todo = [a for a in assets if a['source_path'] in wanted]
        visited_assets = set()
        while todo:
            asset = todo.pop()
            if asset['id'] in visited_assets:
                continue
            visited_assets.add(asset['id'])
            for text in legacy.texts(raw[asset['file']['path']], asset['source_path']):
                for url in legacy.links(text):
                    physical = legacy.relative_target(asset['source_file'], url)
                    target = next((a for a in assets if a['source_file'] == physical), None)
                    if target and target['kind'] in {'image', 'code', 'data'}:
                        wanted.add(target['source_path']); todo.append(target)
        profiles.append({'id': name, 'modules': keys,
                         'asset_ids': [a['id'] for a in assets if a['source_path'] in wanted],
                         'quiz_ids': [q['id'] for q in quizzes if q['source_path'] in wanted]})
    raw['indexes/anchors.json'] = data([])
    raw['indexes/assets.json'] = data({'assets': assets, 'links': links,
        'downloads': [{'asset_id': a['id'], 'path': a['file']['path'], 'sha256': a['file']['sha256'],
                       'delivery': 'immutable-artifact', 'source_ref': source.ref} for a in assets],
        'closure_paths': sorted({a['source_path'] for a in assets if any(a['id'] in p['asset_ids'] for p in profiles)}),
        'shared_images': {}})
    raw['indexes/source-inputs.json'] = data({'source_ref': source.ref, 'inputs': [inputs[p] for p in sorted(inputs)], 'projections': projections})
    bindings = [{'slug': slug, 'manifest': row[0], 'policy_revision': revision('policy', f.policy(row[1]), old_revisions['policies'].get(slug))}
                for slug, row in english.items()]
    package = {'schema_version': 3, 'contract_version': f.CONTRACT_VERSION, 'kind': f.KIND, 'purpose': 'teaching',
               'package_state': 'release', 'catalog': config['locales']['en']['catalog'], 'courses': bindings,
               'course_sources': course_sources, 'default_locale': default, 'available_locales': locales,
               'locale_digests': {}, 'profiles': profiles, 'selected_profiles': selected, 'assets': assets, 'quizzes': quizzes,
               'snapshot': {'source_ref': source.ref, 'builder_version': 'course-files-1.' + f.sha256(raw['tools/course_files_producer.py']),
                            'files': [record(p, raw[p]) for p in sorted(raw)], 'package_digest': ''}}
    package['locale_digests'] = {tag: f.locale_digest(package, tag) for tag in locales}
    package['snapshot']['package_digest'] = f.package_digest(package)
    raw['package.v3.json'] = data(package)
    return raw, package


def build(source, output, profiles, locales, default='en'):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('output exists; choose a fresh immutable destination')
    files, package = build_tree(source, profiles, locales, default)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.course-files-', dir=output.parent) as temporary:
        base = Path(temporary); tree = base / 'tree'; legacy.write_tree(files, tree)
        report = f.validate_package(tree)
        teaching = legacy.archive_bytes(files)
        (base / 'teaching.tar.gz').write_bytes(teaching)
        report.update(source_ref=source.ref, archive_sha256=f.sha256(teaching), archive_size=len(teaching))
        (base / 'build-report.json').write_bytes(data(report))
        base.rename(output)
    return report
