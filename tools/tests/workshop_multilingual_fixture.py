"""Two isolated local learners using a real installed bilingual runtime bundle."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'workshop/aws/runtime'), str(ROOT / 'ui/workshop')]
import workshop_config as wc
import workshop_content
import workshop_runtime
import host


class InstalledFixture:
    def __init__(self, directory, package, learner='alice'):
        self.directory, self.package, self.learner = Path(directory), Path(package), learner
        self.layout = host.Layout(self.directory / 'host')
        self.process = None
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); self.port = sock.getsockname()[1]

    def prepare(self):
        self.directory.mkdir(parents=True)
        binding = workshop_content.admit(self.package)
        config = wc.load_document(wc.SPEC / 'examples/event.example.yaml')
        config.update(schema_version=2, course_ref=binding['source_ref'], course_profiles=['rta-module-1'], content=binding)
        path = self.directory / 'config.json'; path.write_text(json.dumps(config))
        bundle = self.directory / 'bundle'
        self.plan = workshop_runtime.create_bundle(path, bundle, content_package=self.package)
        host.verify_bundle(bundle)
        files = host.install_source(bundle, self.plan, self.layout)
        host.install_content(bundle, self.plan, self.layout)
        self.layout.helpers.mkdir(parents=True)
        for name in ('kernel.py', 'multilingual.py', 'host.py', 'portal.py', 'serve.py'):
            shutil.copyfile(bundle / name, self.layout.helpers / name)
        host.install_ui(bundle, self.layout)
        host.materialize(self.plan, files, self.layout)
        host.atomic_json(self.layout.config / 'runtime.json', self.plan, 0o644)
        for course in self.plan['courses']:
            (self.layout.source / course['course_dir'] / '.venv').symlink_to(ROOT / course['course_dir'] / '.venv', target_is_directory=True)
        return self

    def start(self):
        self.log = (self.directory / 'jupyter.log').open('a')
        env = {**os.environ, 'JUPYTER_CONFIG_DIR': str(self.directory / 'config'), 'JUPYTER_DATA_DIR': str(self.directory / 'data'),
               'DORIS_WORKSHOP_EVENT_ID': 'ml05-local', 'DORIS_WORKSHOP_LEARNER_ID': self.learner}
        self.process = subprocess.Popen([sys.executable, str(self.layout.helpers / 'serve.py'),
            '--runtime-json', str(self.layout.config / 'runtime.json'), '--workspace', str(self.layout.workspace),
            '--source', str(self.layout.source), '--content-root', str(self.layout.content),
            '--runtime-dir', str(self.directory / 'runtime'), '--kernel-dir', str(self.layout.kernels), '--port', str(self.port)],
            env=env, stdout=self.log, stderr=self.log)
        import check
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError('Installed Jupyter failed; inspect fixture log')
            if check.jupyter_ready(self.directory / 'runtime/token', f'http://127.0.0.1:{self.port}/api/status'):
                return
            time.sleep(.15)
        raise RuntimeError('Installed Jupyter did not become ready')

    def credentials(self):
        return {'port': self.port, 'token': (self.directory / 'runtime/token').read_text(),
                'workspace': str(self.layout.workspace), 'source': str(self.layout.source),
                'content': str(self.layout.content), 'plan': self.plan, 'learner': self.learner}

    def stop(self):
        if self.process:
            self.process.terminate()
            try: self.process.wait(timeout=10)
            except subprocess.TimeoutExpired: self.process.kill(); self.process.wait()
            self.process = None; self.log.close()
