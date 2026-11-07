"""Verify progress in real local Jupyter pages, then remove all test environments."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from progress_fixture import ProgressFixture, ROOT
sys.path.insert(0, str(ROOT / 'doris-course/common'))
from doris_labkit.quizbank import display_order, load_quiz


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--bilingual', action='store_true', help='Use a synthetic bilingual Quiz adapter')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='workshop-progress-browser-') as temporary:
        base = Path(temporary)
        fixture_type = ProgressFixture
        if args.bilingual:
            from quiz_progress_fixture import QuizProgressFixture
            fixture_type = QuizProgressFixture
        a, b = fixture_type(base / 'a'), fixture_type(base / 'b')
        try:
            a.start()
            b.start()
            for phase in ('interactive', 'restart'):
                if phase == 'restart':
                    a.stop()
                    b.stop()
                    a.start()
                    b.start()
                module = next(c for c in a.plan['courses'] if c['slug'] == 'real-time-analytics')['modules'][0]
                bank = display_order(load_quiz(ROOT / 'doris-course/01-real-time-analytics' / module['quiz']))
                ready = base / 'ready.json'
                ready.write_text(json.dumps({'a': a.credentials(), 'b': b.credentials(),
                    'bilingual': args.bilingual,
                    'correct': [q.option_text(q.answer) for q in bank.questions],
                    'wrong': [next(o.text for o in q.options if o.option_id != q.answer) for q in bank.questions]}))
                ready.chmod(0o600)
                run = subprocess.run(['node', str(ROOT / 'ui/workshop/scripts/verify-progress.cjs'),
                    str(ready), str(args.output.resolve()), phase], env=os.environ.copy(), text=True, capture_output=True, timeout=240)
                (args.output / f'{phase}-browser.txt').write_text(run.stdout + run.stderr)
                print(run.stdout + run.stderr)
                if run.returncode:
                    return run.returncode
        finally:
            a.stop()
            b.stop()
        (args.output / 'cleanup.json').write_text(json.dumps({'processes_stopped': True, 'temporary_environments_removed': True}) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
