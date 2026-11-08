#!/usr/bin/env python3
"""Validate attributed LLM batches and atomically extend a locale overlay.

This tool never translates text or rewrites existing translation records.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import os
import re

import content_contract_v2 as contract


def sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def merge(source_map, base, fragments, files, *, source_ref):
    units = contract.index_by(source_map['units'], 'id', 'source unit')
    existing = contract.index_by(base['units'], 'unit_id', 'existing translation')
    result = dict(existing)
    seen = set(existing)
    accepted, errors, pending = [], [], []
    for path, fragment in fragments:
        contract.shape(fragment, 'locale_overlay')
        if fragment['locale'] != base['locale']:
            raise ValueError('Batch locale differs from the base overlay: ' + str(path))
        for entry in fragment['units']:
            uid = entry['unit_id']
            if uid in seen:
                raise ValueError('A batch would replace or duplicate a translation: ' + uid)
            seen.add(uid)
            source = units.get(uid)
            problems = []
            if not source or entry['source_hash'] != source['source_hash']:
                problems.append('unknown or stale source')
            if entry['target_hash'] != sha(entry['target_text']):
                problems.append('target hash differs')
            check = entry['checks']['llm_self_check']
            waiting = not check and entry['state'] == 'translated'
            if not waiting and (not check or check['method'] != 'current-session-llm'
                    or check['source_hash'] != entry['source_hash']
                    or check['target_hash'] != entry['target_hash']):
                problems.append('missing exact source/target LLM comparison')
            if source and not waiting:
                left = Counter(contract.protected_markup(source['source_text']))
                right = Counter(contract.protected_markup(entry['target_text']))
                if left != right:
                    problems.append({'protected_missing': list((left-right).elements()),
                                     'protected_added': list((right-left).elements())})
                try:
                    contract.safe_markdown(entry['target_text'], source['source_path'], files)
                except ValueError as error:
                    problems.append(str(error))
            if problems:
                errors.append({'batch': str(path), 'unit_id': uid, 'problems': problems})
                continue
            if waiting:
                pending.append(uid)
                continue
            value = json.loads(json.dumps(entry))
            value['checks']['technical_admission'] = {
                'method': 'technical-validator', 'actor': 'tools/merge_locale_batches.py',
                'record': 'Actual unit/source/target hashes, exact protected code/URLs/HTML attributes/placeholders and safe Markdown checked against source ' + source_ref + '. No native human review implied.',
                'source_hash': entry['source_hash'], 'target_hash': entry['target_hash'],
            }
            value['state'] = 'llm_checked'
            result[uid] = value
            accepted.append(uid)
    return {**base, 'units': [result[uid] for uid in sorted(result)]}, accepted, errors, pending


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-map', type=Path, default=Path('locales/source-map.json'))
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--batches', type=Path, required=True)
    parser.add_argument('--source-ref', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--allow-pending', action='store_true', help='Merge only completed checks; pending units stay absent, so release coverage still fails.')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if not re.fullmatch('[0-9a-f]{40}', args.source_ref):
        raise ValueError('Choose a full immutable Git source SHA.')
    selected_map = json.loads(subprocess.check_output(['git', '-C', str(root), 'show', args.source_ref + ':locales/source-map.json']))
    if json.loads(args.source_map.read_text()) != selected_map:
        raise ValueError('Source map differs from the selected Git commit.')
    paths = subprocess.check_output(['git', '-C', str(root), 'ls-tree', '-r', '--name-only', args.source_ref], text=True).splitlines()
    documents = {name: None for name in paths}
    fragments = [(p, json.loads(p.read_text())) for p in sorted(args.batches.rglob('*.json'))]
    base = json.loads(args.base.read_text())
    output, accepted, errors, pending = merge(json.loads(args.source_map.read_text()), base, fragments, documents, source_ref=args.source_ref)
    report = {'source_ref': args.source_ref, 'fragments': len(fragments), 'existing_unchanged': len(base['units']),
              'accepted_new': len(accepted), 'accepted_ids': accepted, 'pending_self_check': pending,
              'errors': errors, 'passed': not errors, 'complete': not errors and not pending}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if errors or (pending and not args.allow_pending):
        print(json.dumps({'passed': False, 'invalid_units': len(errors), 'pending_self_check': len(pending), 'report': str(args.report)}))
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=args.output.parent, encoding='utf-8', delete=False) as file:
        json.dump(output, file, ensure_ascii=False, indent=2); file.write('\n')
        temporary = file.name
    os.replace(temporary, args.output)
    print(json.dumps({'passed': True, 'accepted_new': len(accepted), 'total': len(output['units'])}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
