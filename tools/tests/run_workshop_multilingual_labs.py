"""Execute both admitted Module 1 Lab copies with unchanged code and managed kernels.

Uses Docker and the course's public S3 dataset. Requires the named Lab resources
not to exist, and removes only resources created by this run.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from workshop_multilingual_fixture import InstalledFixture, ROOT
sys.path.insert(0, str(ROOT / 'workshop/aws/runtime'))
from multilingual import Content


def docker(*args, check=True):
    return subprocess.run(['docker', *args], capture_output=True, text=True, check=check, timeout=60)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    resources = [('container', 'doris-rta'), ('network', 'doris-rta'),
                 ('volume', 'doris-rta-fe-meta'), ('volume', 'doris-rta-be-storage')]
    for kind, name in resources:
        if docker(kind, 'inspect', name, check=False).returncode == 0:
            raise ValueError('Lab validation requires an unused doris-rta sandbox; existing resources were preserved')
    fixture = InstalledFixture(args.directory, args.package).prepare()
    import nbformat
    from nbclient import NotebookClient
    from jupyter_client import KernelManager
    from jupyter_client.kernelspec import KernelSpecManager
    adapter = Content(fixture.plan, fixture.layout.content, fixture.layout.workspace)
    lab = next(p for p in adapter.notebooks if '/lab' in p)
    rows, report = [], {'passed': False, 'scope': 'local-macos-arm64-real-labs', 'package_digest': fixture.plan['content']['package_digest'],
                        'languages': {}, 'unchanged_code': True, 'same_course_environment': True}
    try:
        for locale in ['en', 'kk-Cyrl']:
            copy_path = fixture.layout.workspace / adapter.copy(lab, locale)
            nb = nbformat.read(copy_path, as_version=4)
            original = nbformat.read(fixture.layout.content / lab, as_version=4)
            assert [(c.id, c.source) for c in nb.cells if c.cell_type == 'code'] == [(c.id, c.source) for c in original.cells if c.cell_type == 'code']
            specs = KernelSpecManager(kernel_dirs=[str(fixture.layout.kernels)], ensure_native_kernel=False)
            km = KernelManager(kernel_name='doris-rta', kernel_spec_manager=specs)
            client = NotebookClient(nb, km=km, timeout=1200, resources={'metadata': {'path': str(copy_path.parent)}})
            state = {'passed': False, 'cells': []}; report['languages'][locale] = state
            with client.setup_kernel():
                for index, cell in enumerate(nb.cells):
                    if cell.cell_type != 'code': continue
                    began = time.monotonic()
                    try:
                        client.execute_cell(cell, index)
                        errors = [o for o in cell.outputs if o.output_type == 'error']
                        assert not errors
                        state['cells'].append({'id': cell.id, 'passed': True, 'seconds': round(time.monotonic()-began, 2)})
                        print(locale, cell.id, 'passed', flush=True)
                    finally:
                        nbformat.write(nb, args.output / (locale + '-executed.ipynb'))
                        (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
            state['passed'] = True
            state['code_cells'] = len(state['cells'])
            state['sandbox_id'] = docker('inspect', '--format', '{{.Id}}', 'doris-rta').stdout.strip()
            state['baseline_rows'] = docker('exec', 'doris-rta', 'mysql', '-uroot', '-h127.0.0.1', '-P9030', '-N', '-e', 'SELECT COUNT(*) FROM doris_course.events').stdout.strip()
            assert state['baseline_rows'] == '10158080'
        assert report['languages']['en']['sandbox_id'] == report['languages']['kk-Cyrl']['sandbox_id']
        report['passed'] = True
        return 0
    finally:
        cleanup = []
        for kind, name in resources:
            result = docker(kind, 'rm', *(['-f'] if kind == 'container' else []), name, check=False)
            cleanup.append({'kind': kind, 'name': name, 'absent': docker(kind, 'inspect', name, check=False).returncode != 0})
        report['cleanup'] = cleanup
        (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    sys.exit(main())
