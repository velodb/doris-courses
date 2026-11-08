#!/usr/bin/env python3
"""Check typed UI copy and its consumer inventory without starting a service."""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ui/workshop'))
from i18n import Messages

INVENTORY = ROOT / 'locales/ui.inventory.json'
PATHS = ['ui/workshop/doris_workshop_ui.py', 'ui/workshop/src/copy.json',
         'ui/workshop/service_guidance.py', 'ui/workshop/i18n.py', 'ui/workshop/src/locale.js',
         'workshop/aws/runtime/portal.py', 'workshop/aws/access/portal.py', 'workshop/aws/access/gateway.py',
         'workshop/aws/access/session_recovery.js', 'doris-course/common/doris_labkit/quiz.py', 'doris-course/common/doris_labkit/messages.py',
         *[p.relative_to(ROOT).as_posix() for p in sorted((ROOT / 'ui/workshop/src/templates').glob('*.html'))]]


def inventory():
    messages = Messages()
    texts = {path: (ROOT / path).read_text() for path in PATHS}
    rows = []
    for key in sorted(messages.source):
        refs = [path for path, value in texts.items() if key in value or
                (path.endswith('.html') and '${msg_' + key.replace('.', '_').replace('-', '_') + '}' in value)]
        if key.startswith('service.single.') or key.startswith('service.multi.'):
            refs.append('ui/workshop/service_guidance.py')
        if key.startswith('progress.error.'):
            refs += ['doris-course/common/doris_labkit/progress.py', 'doris-course/common/doris_labkit/messages.py']
        for base, forms in messages.contract['plurals'].items():
            if key in forms.values():
                refs += [path for path, value in texts.items() if base in value]
        if not refs:
            raise ValueError('Unaccounted UI message: ' + key)
        rows.append({'key': key, 'parameters': messages.contract['parameters'][key], 'consumers': sorted(set(refs))})
    # Typoed literal keys must fail instead of being silently unused.
    for path, value in texts.items():
        for key in re.findall(r'''["']((?:notebook|quiz|recovery|template|state|locale|progress|import|nav|workspace|activity|course|lesson|service)\.[a-z][a-z0-9_.-]*[a-z0-9])["']''', value):
            if key in messages.contract['plurals'] or key.endswith(('.js', '.json', '.yaml')):
                continue  # Machine file names, not message references.
            if key not in messages.source:
                raise ValueError('Unknown UI message in ' + path + ': ' + key)
    # Templates contain slots, numbers and arrows; all literal learner text is in
    # the canonical dictionary. HTML semantics and machine CSS classes stay put.
    from html.parser import HTMLParser
    class CheckTemplate(HTMLParser):
        def handle_data(self, value):
            value = re.sub(r'\$\{?[a-zA-Z_][a-zA-Z_0-9]*\}?', '', value)
            if re.search(r'[a-zA-Z]', value):
                raise ValueError('Literal template copy: ' + value.strip())
    for path, value in texts.items():
        if path.endswith('.html'):
            CheckTemplate().feed(value)
    return {'version': 1, 'source': 'locales/ui.en.json', 'contract': 'locales/ui.contract.json', 'messages': rows,
            'boundaries': {'offline_design_preview': 'English review tool; not installed in learner runtime',
                'machine_diagnostics': 'Exceptions, logs, API denial bodies and third-party applications retain machine semantics',
                'progress_errors': 'Only exact static allowlisted diagnostics become translated learner copy; unknown errors use a generic message',
                'content': 'Lesson text, module titles, questions and image descriptions come from admitted content adapters'}}


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    value = inventory()
    if args.write:
        INVENTORY.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    elif json.loads(INVENTORY.read_text()) != value:
        raise ValueError('UI inventory is stale; review and run --write')
    print(json.dumps({'messages': len(value['messages']), 'parameterized': sum(bool(row['parameters']) for row in value['messages']), 'ok': True}))


if __name__ == '__main__':
    main()
