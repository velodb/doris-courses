#!/usr/bin/env python3
"""Admit a versioned teaching package and print its exact Workshop binding."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

import content_contract_v2 as contract
import workshop_config as wc

_spec = importlib.util.spec_from_file_location('workshop_content_runtime', wc.SPEC / 'runtime/multilingual.py')
consumer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(consumer)


def admit(directory, config=None):
    directory = Path(directory).resolve()
    tree = directory / 'tree'
    entries = [name for name in ('package.v2.json', 'package.v3.json') if (tree / name).is_file()]
    if len(entries) != 1:
        raise wc.ConfigError('content: exactly one supported package manifest is required')
    entry = entries[0]
    if entry == 'package.v3.json':
        import content_files_contract
        content_files_contract.validate_package(tree)
    else:
        contract.validate_package(tree)
    package = json.loads((tree / entry).read_text())
    value = consumer.binding(package)
    consumer.validate_binding(value)
    if config is not None and (config.get('schema_version') != 2 or config['content'] != value
                               or config['course_ref'] != value['source_ref'] or config['course_profiles'] != value['profiles']):
        raise wc.ConfigError('content: package, language set, profile or fingerprints differ from config')
    consumer.verify_tree(tree, value)
    installed_contract = json.loads((wc.REPO / 'locales/ui.contract.json').read_text())
    if json.loads((tree / 'locales/ui.contract.json').read_text()) != installed_contract:
        raise wc.ConfigError('content: UI ABI differs from the installed consumer; rebuild the complete package')
    # The archive must represent this exact admitted tree, without extra entries.
    import tarfile
    expected = {r['path']: r for r in package['snapshot']['files']}
    expected[entry] = {'sha256': consumer.digest((tree / entry).read_bytes())}
    with tarfile.open(directory / 'teaching.tar.gz') as stream:
        seen = set()
        for member in stream.getmembers():
            if not member.isfile() or member.name not in expected or member.name in seen:
                raise wc.ConfigError('content: archive entries differ from admitted tree')
            seen.add(member.name)
            if consumer.digest(stream.extractfile(member).read()) != expected[member.name]['sha256']:
                raise wc.ConfigError('content: archive file differs from admitted tree')
        if seen != set(expected):
            raise wc.ConfigError('content: incomplete teaching archive')
    return value


def ui_from_bundle(bundle, expected):
    """Project messages only, from the exact bound private runtime archive."""
    import tarfile
    import hashlib
    bundle = Path(bundle)
    plan = wc.load_document(bundle / 'runtime.json')
    consumer.validate_plan(plan)
    if plan.get('content') != expected:
        raise wc.ConfigError('content: entry runtime binding differs from activity')
    archive = bundle / 'teaching.tar.gz'
    if hashlib.sha256(archive.read_bytes()).hexdigest() != plan['sha256']['teaching.tar.gz']:
        raise wc.ConfigError('content: entry archive changed')
    dictionaries = {}
    with tarfile.open(archive) as stream:
        package = json.load(stream.extractfile(consumer.package_name(expected)))
        if consumer.binding(package) != expected:
            raise wc.ConfigError('content: entry package differs')
        inventory = {r['path']: r for r in package['snapshot']['files']}
        for tag in expected['available_locales']:
            name = ('' if tag == 'en' else 'rendered/' + tag + '/') + 'locales/ui.en.json'
            raw = stream.extractfile(name).read()
            if consumer.digest(raw) != inventory[name]['sha256']:
                raise wc.ConfigError('content: entry message hash differs')
            dictionaries[tag] = json.loads(raw)['messages']
    sys.path.insert(0, str(wc.REPO / 'ui/workshop'))
    from i18n import InstalledUI
    return InstalledUI(dictionaries, default=expected['default_locale']).snapshot(expected['package_digest'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        value = admit(args.package)
        if args.output:
            wc.write_manifest(args.output, value)
        else:
            print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
