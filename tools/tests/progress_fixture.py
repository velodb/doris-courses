"""Two disposable local Jupyter installations for progress acceptance tests.

Uses real course identities/lessons/quizzes and lightweight Lab notebooks;
does not execute course Labs, contact AWS, or start Docker.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import workshop_config
import workshop_runtime


class ProgressFixture:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.workspace = self.directory / 'workspace'
        self.db = self.directory / 'progress/progress.sqlite3'
        self.process = None
        config = workshop_config.load_document(ROOT / 'workshop/aws/examples/event.example.yaml')
        config['course_ref'] = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
        config['course_profiles'] = ['rta-main', 'dw-main', 'obs-main']
        self.plan = {'course_ref': config['course_ref'], 'courses': workshop_runtime.course_selection(config), 'learning_progress': True}
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.directory / 'plan.json').write_text(json.dumps(self.plan))
        for course in self.plan['courses']:
            spec_dir = self.directory / 'kernels' / course['kernel']
            spec_dir.mkdir(parents=True, exist_ok=True)
            (spec_dir / 'kernel.json').write_text(json.dumps({'argv': [sys.executable, '-m', 'ipykernel_launcher', '-f', '{connection_file}'],
                'display_name': course['kernel'], 'language': 'python'}))
            first = course['modules'][0]
            quiz = str(Path(first['quiz']).with_suffix('.ipynb'))
            for path, code in [(first['lab'], '42'), (quiz,
                    'import os, sys\nfrom pathlib import Path\nsource = Path(os.environ["DORIS_WORKSHOP_PROGRESS_SOURCE"])\n'
                    'sys.path.insert(0, str(source / "doris-course/common"))\n'
                    'from doris_labkit.quiz import CourseQuiz\n'
                    f'quiz = CourseQuiz.from_yaml(source / {str(Path(course["course_dir"]) / first["quiz"])!r})\n_ = quiz.show()')]:
                target = self.workspace / course['course_dir'] / path
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    target.write_text(json.dumps({'nbformat': 4, 'nbformat_minor': 5,
                        'metadata': {'kernelspec': {'name': course['kernel'], 'display_name': course['kernel'], 'language': 'python'}},
                        'cells': [{'cell_type': 'code', 'id': 'progress-check', 'metadata': {}, 'source': code, 'outputs': [], 'execution_count': None}]}))

    def start(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        self.base = f'http://127.0.0.1:{self.port}'
        env = {**os.environ, 'JUPYTER_CONFIG_DIR': str(self.directory / 'config'),
               'JUPYTER_DATA_DIR': str(self.directory / 'data'), 'IPYTHONDIR': str(self.directory / 'ipython')}
        self.log = (self.directory / 'jupyter.log').open('a')
        self.process = subprocess.Popen([sys.executable, '-I', str(ROOT / 'workshop/aws/runtime/serve.py'),
            '--runtime-json', str(self.directory / 'plan.json'), '--source', str(ROOT),
            '--workspace', str(self.workspace), '--runtime-dir', str(self.directory / 'runtime'),
            '--kernel-dir', str(self.directory / 'kernels'), '--port', str(self.port)], stdout=self.log, stderr=self.log, env=env)
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError('Local progress Jupyter exited during startup.')
            token = self.directory / 'runtime/token'
            if token.exists():
                try:
                    request = urllib.request.Request(self.base + '/api/status', headers={'Authorization': 'token ' + token.read_text()})
                    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=1):
                        return self
                except OSError:
                    pass
            time.sleep(.1)
        raise RuntimeError('Local progress Jupyter did not become ready.')

    def credentials(self):
        return {'base': self.base, 'token': (self.directory / 'runtime/token').read_text(), 'workspace': str(self.workspace)}

    def stop(self):
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            self.process = None
            self.log.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    fixture = ProgressFixture(args.directory).start()
    try:
        ready = args.directory / 'ready.json'
        ready.write_text(json.dumps(fixture.credentials()))
        ready.chmod(0o600)
        while True:
            time.sleep(1)
    finally:
        fixture.stop()
