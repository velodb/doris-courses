"""Run synthetic bilingual acceptance and a full process/browser-session restart."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, required=True)
    output = parser.parse_args().output.resolve(); output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='workshop-locales-') as temporary:
        directory = Path(temporary)
        for phase in ('interactive', 'restart'):
            with (directory / 'fixture.log').open('a') as log:
                process = subprocess.Popen([sys.executable, str(ROOT / 'tools/tests/locale_browser_fixture.py'), '--directory', temporary], stdout=log, stderr=log)
                try:
                    deadline = time.monotonic() + 40
                    while not (directory / 'ready.json').exists():
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError('Local locale fixture failed; inspect private fixture log')
                        time.sleep(.1)
                    run = subprocess.run(['node', str(ROOT / 'ui/workshop/scripts/verify-i18n.cjs'), str(directory / 'ready.json'), str(output), phase],
                        text=True, capture_output=True, env=os.environ.copy(), timeout=150)
                    (output / (phase + '.log')).write_text(run.stdout + run.stderr)
                    print(run.stdout + run.stderr)
                    if run.returncode:
                        # No credentials leave the disposable private directory.
                        (output / 'fixture-failure.log').write_text((directory / 'runtime.log').read_text())
                        return run.returncode
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait()
    (output / 'cleanup.json').write_text(json.dumps({'processes_stopped': True, 'temporary_source_and_credentials_removed': True}) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
