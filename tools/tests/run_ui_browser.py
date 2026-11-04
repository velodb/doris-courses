"""Run the real local UI pages in Chromium, then remove all fixture credentials."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='workshop-ui-browser-') as temporary:
        private = Path(temporary)
        with (private / 'fixture.log').open('w') as stream:
            process = subprocess.Popen([sys.executable, str(ROOT / 'tools/tests/ui_browser_fixture.py'),
                '--directory', temporary], stdout=stream, stderr=stream)
            try:
                deadline = time.monotonic() + 40
                while not (private / 'ready.json').exists():
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('Local UI fixture did not start; inspect local dependencies')
                    time.sleep(.1)
                result = subprocess.run(['node', str(ROOT / 'ui/workshop/scripts/verify-integrated.cjs'),
                    str(private / 'ready.json')], env=os.environ.copy(), text=True, capture_output=True, timeout=150)
                output = result.stdout + result.stderr
                if args.log:
                    args.log.parent.mkdir(parents=True, exist_ok=True)
                    args.log.write_text(output)
                print(output)
                return result.returncode
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


if __name__ == '__main__':
    sys.exit(main())
