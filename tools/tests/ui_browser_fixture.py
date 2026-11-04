"""Loopback-only UI fixture: real portal and Jupyter handlers, test-signed OIDC claims."""
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

from aiohttp import web
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/tests'))
import test_workshop_portal as fixture


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


async def run(directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    course = yaml.safe_load((ROOT / 'doris-course/01-real-time-analytics/course.yaml').read_text())
    plan = {'courses': [{'course_dir': 'doris-course/01-real-time-analytics', 'kernel': 'doris-rta',
        'title': course['title'], 'modules': course['modules'], 'levels': course.get('levels', {})}], 'course_ref': '0' * 40}
    (directory / 'plan.json').write_text(json.dumps(plan))
    (directory / 'workspace').mkdir(exist_ok=True)
    config = fixture.config()
    app = fixture.portal.application(config, verifiers={event: fixture.LocalVerifier({**activity,
        'region': config['region'], 'alb_arn': config['alb_arn']}, None)
        for event, activity in config['activities'].items()})
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    portal_port = site._server.sockets[0].getsockname()[1]
    port = free_port()
    env = {**os.environ, 'JUPYTER_CONFIG_DIR': str(directory / 'jupyter-config'),
        'JUPYTER_DATA_DIR': str(directory / 'jupyter-data'), 'BROWSER': '/usr/bin/true'}
    with (directory / 'jupyter.log').open('w') as log:
        process = subprocess.Popen([sys.executable, '-I', str(ROOT / 'workshop/aws/runtime/serve.py'),
            '--runtime-json', str(directory / 'plan.json'), '--workspace', str(directory / 'workspace'),
            '--runtime-dir', str(directory / 'runtime'), '--kernel-dir', str(directory / 'kernels'),
            '--source', str(ROOT), '--port', str(port)], stdout=log, stderr=log, env=env)
        try:
            deadline = time.monotonic() + 30
            token = directory / 'runtime/token'
            while not token.exists():
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('Local Jupyter fixture did not start')
                await asyncio.sleep(.1)
            while True:
                try:
                    _, writer = await asyncio.open_connection('127.0.0.1', port)
                    writer.close()
                    await writer.wait_closed()
                    break
                except ConnectionRefusedError:
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('Local Jupyter fixture did not listen')
                    await asyncio.sleep(.1)
            ready = {'portal_port': portal_port, 'course_port': port, 'jupyter_token': token.read_text(),
                'oidc': {who: fixture.token(who, headers={'exp': time.time() + 600})
                    for who in ('subject-alice', 'subject-bob', 'unassigned')}}
            path = directory / 'ready.json'
            path.write_text(json.dumps(ready))
            path.chmod(0o600)
            stop = asyncio.Event()
            for sig in (signal.SIGINT, signal.SIGTERM):
                asyncio.get_running_loop().add_signal_handler(sig, stop.set)
            await stop.wait()
        finally:
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, 10)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait)
            await runner.cleanup()
            (directory / 'ready.json').unlink(missing_ok=True)
            for name in ('token', 'cookie-secret'):
                (directory / 'runtime' / name).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.directory))


if __name__ == '__main__':
    main()
