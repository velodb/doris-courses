"""Execute the selected course, including explicitly selected optional teaching paths."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

DW_PROFILES = {'dw-main', 'dw-kafka', 'dw-cdc', 'dw-bi', 'dw-backup', 'dw-extensions'}
DW_OPTIONALS = {
    'dw-extensions': [(3, 'level1/extensions/extension23_physical_design.ipynb'),
                      (5, 'level1/extensions/extension45_files_and_group_commit.ipynb'),
                      (7, 'level1/extensions/extension67_schema_and_delete.ipynb')],
    'dw-kafka': [(5, 'level1/module05-ingestion/optional05_kafka_routine_load.ipynb')],
    'dw-cdc': [(5, 'level1/module05-ingestion/optional05_flink_mysql_cdc.ipynb')],
}
CONTINUOUS_QUIZ = 'level1/module05-ingestion/quiz05_continuous_ingestion.ipynb'


def selection_plan(course):
    """One exact ordered execution list; an unimplemented profile fails closed."""
    profiles = set(course['profiles'])
    rta = course['kernel'] == 'doris-rta' and profiles == {'rta-main'}
    dw = course['kernel'] == 'doris-dw' and 'dw-main' in profiles and profiles <= DW_PROFILES
    if not (rta or dw):
        raise ValueError('course validation: unsupported profile selection')
    expected_modules = [f'module-{i}' for i in range(1, 11 if rta else 15)]
    if [m['id'] for m in course['modules']] != expected_modules:
        raise ValueError('course validation: incomplete or reordered main path')
    steps = []
    for number, module in enumerate(course['modules'], 1):
        flags = []
        if dw and number == 11 and 'dw-bi' in profiles:
            flags = ['BUILD_DASHBOARD']
        if dw and number == 13 and 'dw-backup' in profiles:
            flags = ['RUN_BACKUP_DRILL']
        steps.append({'module': module['id'], 'kind': 'lab', 'notebook': module['lab'], 'opt_ins': flags})
        steps.append({'module': module['id'], 'kind': 'quiz', 'notebook': str(Path(module['quiz']).with_suffix('.ipynb')), 'opt_ins': []})
        # Run each extension after its prerequisites. Streaming comes last at
        # Module 5, then restore the ordinary sandbox before returning to main.
        for profile in ('dw-extensions', 'dw-kafka', 'dw-cdc'):
            for after, path in DW_OPTIONALS.get(profile, []) if profile in profiles else []:
                if number == after:
                    steps.append({'module': module['id'], 'kind': 'optional', 'notebook': path, 'opt_ins': []})
        if dw and number == 5 and profiles & {'dw-kafka', 'dw-cdc'}:
            steps.append({'module': module['id'], 'kind': 'optional-quiz', 'notebook': CONTINUOUS_QUIZ, 'opt_ins': []})
    if set(course['notebooks']) != {s['notebook'] for s in steps}:
        raise ValueError('course validation: selected Notebooks do not match the execution plan')
    return steps


def enable_opt_ins(notebook, flags):
    """Change only the documented booleans in the private execution copy."""
    enabled = []
    for flag in flags:
        matches = []
        for cell in notebook.cells:
            if cell.cell_type != 'code':
                continue
            expression = re.compile(r'^' + re.escape(flag) + r' = False$', re.MULTILINE)
            if expression.search(cell.source):
                matches.append((cell, expression))
        if len(matches) != 1:
            raise ValueError('course validation: documented opt-in is missing or ambiguous')
        cell, expression = matches[0]
        cell.source = expression.sub(flag + ' = True', cell.source)
        enabled.append(flag)
    return enabled


def valid_selection_report(report, steps, profiles, source_notebooks):
    """Require every selected file and cell, with its original source hash."""
    rows = report.get('notebooks', [])
    if report.get('course_profiles') != sorted(profiles) or len(rows) != len(steps):
        return False
    modules = [{'module': step['module'], 'passed': True} for step in steps if step['kind'] == 'lab']
    if report.get('modules') != modules:
        return False
    for row, step in zip(rows, steps):
        source = source_notebooks[step['notebook']]
        code_count = sum(c['cell_type'] == 'code' for c in json.loads(source)['cells'])
        if (any(row.get(k) != v for k, v in step.items()) or row.get('passed') is not True or
                row.get('source_sha256') != hashlib.sha256(source).hexdigest() or
                row.get('code_cells') != code_count or row.get('errors') != 0 or
                row.get('enabled_opt_ins') != step['opt_ins']):
            return False
    return (report.get('notebook_count') == len(rows) and
            report.get('code_cells') == sum(r['code_cells'] for r in rows))


def main():
    import nbformat
    from nbclient import NotebookClient
    expected = json.loads(Path(sys.argv[1]).read_text())
    root = Path(expected['course_output_dir'])
    root.mkdir(parents=True, exist_ok=True)
    runtime = json.loads(Path('/etc/doris-workshop/runtime.json').read_text())
    activity = json.loads(Path('/etc/doris-workshop/activity.json').read_text())
    if (os.geteuid() == 0 or runtime['course_ref'] != expected['course_ref'] or
            activity['event_id'] != expected['event_id'] or activity['learner_id'] != expected['learner_id'] or
            len(runtime['courses']) != 1):
        raise ValueError('course validation: runtime or activity mismatch')
    os.environ.update(JUPYTER_PATH='/usr/local/share/doris-workshop/jupyter',
                      DORIS_WORKSHOP_EVENT_ID=activity['event_id'],
                      DORIS_WORKSHOP_LEARNER_ID=activity['learner_id'],
                      DORIS_BROWSER_ROUTES_FILE='/etc/doris-workshop/browser-urls.json',
                      DORIS_WORKSHOP_PORT_REGISTRY='/run/doris-workshop/service-ports.json')
    browser_env = Path('/etc/doris-workshop/browser.env')
    if browser_env.exists():
        for line in browser_env.read_text().splitlines():
            key, value = line.split('=', 1)
            if (not re.fullmatch(r'(?:DORIS_|DW_)[A-Z0-9_]+', key) or
                    not re.fullmatch(r'[a-zA-Z0-9:/._-]+', value) or key in {
                        'DORIS_WORKSHOP_EVENT_ID', 'DORIS_WORKSHOP_LEARNER_ID', 'DORIS_BROWSER_ROUTES_FILE'}):
                raise ValueError('course validation: invalid browser environment')
            os.environ[key] = value
    course = runtime['courses'][0]
    steps = selection_plan(course)
    if sorted(course['profiles']) != sorted(expected['course_profiles']):
        raise ValueError('course validation: profile binding mismatch')
    source = Path('/opt/doris-courses') / course['course_dir']
    workspace = Path('/var/lib/doris-workshop/workspace') / course['course_dir']
    report = {'schema_version': 2, 'kind': 'doris-workshop-course-validation',
              'image_id': expected['ami_id'], 'instance_id': expected['instance_id'],
              'course_ref': runtime['course_ref'], 'course_profiles': sorted(course['profiles']),
              'started_at': datetime.now(timezone.utc).isoformat(), 'modules': [], 'notebooks': [],
              'notebook_count': 0, 'code_cells': 0, 'documented_transitions': [], 'done': False, 'passed': False}
    timings = []

    def save():
        temp = root / 'progress.tmp'
        temp.write_text(json.dumps(report, indent=2) + '\n')
        temp.replace(root / 'progress.json')
        (root / 'cell-timings.json').write_text(json.dumps(timings, indent=2) + '\n')

    def transition(step):
        if course['kernel'] == 'doris-rta' and step['kind'] == 'lab':
            if step['module'] == 'module-9':
                subprocess.run(['docker', 'stop', 'doris-rta'], check=True, capture_output=True)
                report['documented_transitions'].append('Stopped single-node sandbox before Lab 9, as required by the course.')
            if step['module'] == 'module-10':
                subprocess.run(['docker', 'compose', '-f', str(source / 'level3/module09-sharding-replication/compose.multinode.yml'), 'stop'], check=True, capture_output=True)
                report['documented_transitions'].append('Stopped Module 9 before Lab 10, preserving its volumes as the course directs.')
        if course['kernel'] == 'doris-dw' and step['kind'] == 'optional-quiz':
            # Labs 5A/5B leave their own resources intact. The documented return
            # to Lab 1 removes the memory overlay and restores prior networks.
            code = ('from dw_course.docker_runtime import prepare_environment; '
                    'prepare_environment(start=True)')
            subprocess.run([str(source / '.venv/bin/python'), '-c', code], cwd=source, check=True, capture_output=True)
            report['documented_transitions'].append('Restored the ordinary Doris sandbox and its existing course networks after streaming Labs.')

    def execute(step):
        path = workspace / step['notebook']
        original = (source / step['notebook']).read_bytes()
        nb = nbformat.read(path, as_version=4)
        # The working copy differs only in its managed kernelspec before execution.
        pinned = nbformat.reads(original.decode(), as_version=4)
        if [c.source for c in nb.cells] != [c.source for c in pinned.cells]:
            raise ValueError('course validation: working Notebook differs from pinned source')
        row = {**step, 'source_sha256': hashlib.sha256(original).hexdigest(),
               'code_cells': 0, 'errors': 0, 'enabled_opt_ins': [], 'passed': False}
        try:
            row['enabled_opt_ins'] = enable_opt_ins(nb, step['opt_ins'])
            client = NotebookClient(nb, timeout=3600, kernel_name=course['kernel'], resources={'metadata': {'path': str(path.parent)}})
            with client.setup_kernel():
                for index, cell in enumerate(nb.cells):
                    if cell.cell_type != 'code':
                        continue
                    began = time.monotonic()
                    try:
                        client.execute_cell(cell, index)
                    finally:
                        errors = sum(o.get('output_type') == 'error' for o in cell.get('outputs', []))
                        row['code_cells'] += 1
                        row['errors'] += errors
                        timings.append({'notebook': step['notebook'], 'cell': cell.get('id'),
                                        'seconds': round(time.monotonic() - began, 2), 'errors': errors})
                        save()
            row['passed'] = row['errors'] == 0
        except Exception:
            # Outputs may embed passwords or connection strings. Executed files
            # stay private; the SSM response contains only this bounded summary.
            row['error'] = {'type': 'cell-execution-failed', 'message': 'Inspect the private validation Notebook.'}
        finally:
            target = root / step['notebook']
            target.parent.mkdir(parents=True, exist_ok=True)
            nbformat.write(nb, target)
        return row

    save()
    try:
        for step in steps:
            transition(step)
            row = execute(step)
            report['notebooks'].append(row)
            report['notebook_count'] += 1
            report['code_cells'] += row['code_cells']
            if step['kind'] == 'lab':
                report['modules'].append({'module': step['module'], 'passed': row['passed']})
            elif not row['passed']:
                report['modules'][-1]['passed'] = False
            save()
            print(json.dumps({'notebook': step['notebook'], 'passed': row['passed']}), flush=True)
            if not row['passed']:
                break
        originals = {step['notebook']: (source / step['notebook']).read_bytes() for step in steps}
        report['passed'] = valid_selection_report(report, steps, course['profiles'], originals)
    except Exception:
        report['error'] = {'type': 'course-transition-failed', 'message': 'Inspect the disposable validation host.'}
    report['done'] = True
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    save()
    print('COURSE_REPORT=' + json.dumps(report, separators=(',', ':')), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
