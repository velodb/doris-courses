"""Loopback-only synthetic bilingual portal, Jupyter reader and recovery gateway."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from multilingual_fixture import ROOT, installed
from aiohttp import web

BASE = 'doris-course/01-real-time-analytics/'


def content(path, kind, locale):
    if path.endswith('mirror.md'):
        return path, 'en'
    if path.endswith('lesson.md') or path.endswith('image.svg'):
        return path.replace('lesson.md', locale + '-lesson.md').replace('image.svg', locale + '-image.svg'), locale
    return path, locale


def labels(kind, identity, values, locale):
    return {key: ('ӘҒҚҢӨҰҮҺІ · ' + value if isinstance(value, str) else value) for key, value in values.items()}


def runtime_main(arguments):
    sys.path.insert(0, str(ROOT / 'workshop/aws/runtime'))
    import portal as runtime_portal
    import serve
    original = runtime_portal.register
    runtime_portal.register = lambda *a, **kw: original(*a, **kw, installed_ui=installed(content=content, labels=labels, anchors=lambda path, locale: ['section.fixture'] if path.endswith('lesson.md') else []))
    serve.main(arguments)


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


async def run(directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    source = directory / 'source'
    course = source / BASE
    course.mkdir(parents=True, exist_ok=True)
    common = source / 'doris-course/common'
    if not common.exists():
        common.symlink_to(ROOT / 'doris-course/common', target_is_directory=True)
    for locale in ('en', 'kk-Cyrl'):
        (course / (locale + '-lesson.md')).write_text(f'# {locale} fixture lesson\n\n## ' + ('Stable section' if locale == 'en' else 'Өзгерген сынақ тақырыбы') + f'\n\n{locale} ӘҒҚҢӨҰҮҺІ text.\n\n![{locale} image description](image.svg)\n\n[English mirror](mirror.md)\n\n[Lab](lab.ipynb?view=one#cell)\n')
        (course / (locale + '-image.svg')).write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="30"><text x="0" y="20">' + locale + '</text></svg>')
    (course / 'mirror.md').write_text('# English navigation mirror\n\nEnglish only.\n')
    # Canonical identity paths remain present; the consumer selects display files.
    (course / 'lesson.md').write_text((course / 'en-lesson.md').read_text())
    plan = {'course_ref': 'a' * 40, 'learning_progress': True, 'event_id': 'class-a', 'learner_id': 'alice',
        'courses': [{'slug': 'real-time-analytics', 'status': 'published', 'course_dir': BASE.rstrip('/'), 'kernel': 'python3',
        'title': 'Synthetic fixture course', 'modules': [{'id': 'module-1', 'title': 'Synthetic module', 'status': 'published', 'required': True,
        'course': 'lesson.md', 'lab': 'lab.ipynb', 'quiz': 'quiz.yaml', 'content_version': 'b' * 64}], 'notebooks': ['lab.ipynb', 'quiz.ipynb']}]}
    (directory / 'plan.json').write_text(json.dumps(plan))
    (directory / 'workspace').mkdir(exist_ok=True)
    import test_workshop_portal as fixture
    config = fixture.config()
    portal = fixture.portal.application(config, installed_ui=installed(), verifiers={event: fixture.LocalVerifier({**activity,
        'region': config['region'], 'alb_arn': config['alb_arn']}, None) for event, activity in config['activities'].items()})
    runner = web.AppRunner(portal); await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0); await site.start()
    portal_port = site._server.sockets[0].getsockname()[1]
    port = free_port()
    env = {**os.environ, 'JUPYTER_CONFIG_DIR': str(directory / 'config'), 'JUPYTER_DATA_DIR': str(directory / 'data')}
    with (directory / 'runtime.log').open('a') as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__)), '--runtime',
            '--runtime-json', str(directory / 'plan.json'), '--source', str(source), '--workspace', str(directory / 'workspace'),
            '--runtime-dir', str(directory / 'runtime'), '--kernel-dir', str(directory / 'kernels'), '--port', str(port)], env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 35
            while True:
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('Synthetic locale Jupyter fixture did not start')
                try:
                    _, writer = await asyncio.open_connection('127.0.0.1', port)
                    writer.close(); await writer.wait_closed(); break
                except ConnectionRefusedError:
                    await asyncio.sleep(.1)
            import test_workshop_access as gateway_fixture
            c = gateway_fixture.config(port)
            app = gateway_fixture.gateway.application(c, verifier=gateway_fixture.LocalVerifier(c, None),
                token_file=directory / 'runtime/token', installed_ui=installed())
            gateway_runner = web.AppRunner(app); await gateway_runner.setup()
            gateway_site = web.TCPSite(gateway_runner, '127.0.0.1', 0); await gateway_site.start()
            ready = {'portal_port': portal_port, 'course_port': port, 'gateway_port': gateway_site._server.sockets[0].getsockname()[1],
                'jupyter_token': (directory / 'runtime/token').read_text(),
                'gateway_host': gateway_fixture.HOST, 'gateway_oidc': gateway_fixture.token(headers={'exp': time.time() + 600}),
                'oidc': {who: fixture.token(who, headers={'exp': time.time() + 600}) for who in ('subject-alice', 'subject-bob')}}
            path = directory / 'ready.json'; path.write_text(json.dumps(ready)); path.chmod(0o600)
            stop = asyncio.Event()
            for sig in (signal.SIGINT, signal.SIGTERM):
                asyncio.get_running_loop().add_signal_handler(sig, stop.set)
            await stop.wait()
            await gateway_runner.cleanup()
        finally:
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, 10)
            except subprocess.TimeoutExpired:
                process.kill(); await asyncio.to_thread(process.wait)
            await runner.cleanup()
            (directory / 'ready.json').unlink(missing_ok=True)


if __name__ == '__main__':
    if '--runtime' in sys.argv:
        sys.argv.remove('--runtime')
        runtime_main(sys.argv[1:])
    else:
        parser = argparse.ArgumentParser(); parser.add_argument('--directory', type=Path, required=True)
        asyncio.run(run(parser.parse_args().directory))
