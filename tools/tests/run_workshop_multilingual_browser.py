"""Run real installed-package Jupyter checks for two local learners, without AWS."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from workshop_multilingual_fixture import InstalledFixture, ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--keep', type=Path, help='Keep disposable fixture for separate real Lab execution; stop servers before return')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix='ml05-installed-browser-') if args.keep is None else None
    base = args.keep or Path(temporary.name)
    a, b = InstalledFixture(base / 'a', args.package, 'alice'), InstalledFixture(base / 'b', args.package, 'bob')
    ready = base / 'ready.json'
    try:
        a.prepare().start(); b.prepare().start()
        sys.path.insert(0, str(ROOT / 'doris-course/common'))
        from doris_labkit.quizbank import display_order, load_quiz
        from doris_labkit.messages import Messages
        module = a.plan['courses'][0]['modules'][0]
        quizpath = a.plan['courses'][0]['course_dir'] + '/' + module['quiz']
        banks, messages = {}, {}
        for tag in ('en', 'kk-Cyrl'):
            path = a.layout.content / (('rendered/' + tag + '/') if tag != 'en' else '') / quizpath
            banks[tag] = [q.option_text(q.answer) for q in display_order(load_quiz(path, localized=tag != 'en')).questions]
            ui = a.layout.content / (('rendered/' + tag + '/') if tag != 'en' else '') / 'locales/ui.en.json'
            messages[tag] = json.loads(ui.read_text())['messages']
        ready.write_text(json.dumps({'a': a.credentials(), 'b': b.credentials(), 'correct': banks, 'messages': messages})); ready.chmod(0o600)
        result = subprocess.run(['node', str(ROOT / 'ui/workshop/scripts/verify-installed-multilingual.cjs'), str(ready), str(args.output.resolve())],
            env=os.environ.copy(), capture_output=True, text=True, timeout=300)
        (args.output / 'browser.log').write_text(result.stdout + result.stderr)
        print(result.stdout + result.stderr)
        return result.returncode
    finally:
        a.stop(); b.stop(); ready.unlink(missing_ok=True)
        (args.output / 'cleanup.json').write_text(json.dumps({'servers_stopped': True, 'credentials_removed': True,
            'fixture_retained_for_lab': bool(args.keep), 'no_cloud_resources': True}) + '\n')
        if temporary:
            for p in base.rglob('*'):
                if p.is_dir() and not p.is_symlink(): p.chmod(0o755)
            temporary.cleanup()


if __name__ == '__main__':
    sys.exit(main())
