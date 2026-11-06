"""Loopback-only browser acceptance fixture with real Jupyter and simulated ALB auth.

No AWS access or course containers. The fixture control routes never ship in a gateway.
"""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import time

from aiohttp import web
import test_workshop_access as fixture


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--soak-seconds', type=int, default=0)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='workshop-recovery-') as tmp:
        root = Path(tmp)
        for name in ('workspace', 'runtime', 'kernels/fixture', 'config', 'data'):
            (root / name).mkdir(parents=True)
        (root / 'kernels/fixture/kernel.json').write_text(json.dumps({'argv': [sys.executable, '-m', 'ipykernel_launcher', '-f', '{connection_file}'], 'display_name': 'Fixture', 'language': 'python'}))
        (root / 'plan.json').write_text(json.dumps({'course_ref': '0' * 40, 'courses': [{'kernel': 'fixture', 'course_dir': 'doris-course/01-real-time-analytics', 'notebooks': [], 'modules': []}]}))
        (root / 'workspace/idle.ipynb').write_text(json.dumps({'nbformat': 4, 'nbformat_minor': 5,
            'metadata': {'kernelspec': {'name': 'fixture', 'display_name': 'Fixture', 'language': 'python'}},
            'cells': [{'id': 'idle-fixture', 'cell_type': 'code', 'metadata': {}, 'source': 'print("fixture")', 'outputs': [], 'execution_count': None}]}))
        with socket.socket() as reserve:
            reserve.bind(('127.0.0.1', 0))
            port = reserve.getsockname()[1]
        runtime = fixture.access.wc.REPO / 'workshop/aws/runtime'
        code = ('import sys,runpy;sys.path.insert(0,' + repr(str(runtime)) + ');import serve;'
            'original=serve.make_config\n'
            'def config(*a,**k):\n c=original(*a,**k);c.LabApp.expose_app_in_browser=True;return c\n'
            'serve.make_config=config\n'
            'runpy.run_path(' + repr(str(fixture.access.OVERLAY / 'jupyter_gateway.py')) + ',run_name="__main__")')
        env = {**os.environ, 'JUPYTER_CONFIG_DIR': str(root / 'config'), 'JUPYTER_DATA_DIR': str(root / 'data'), 'IPYTHONDIR': str(root / 'ipython')}
        log = (args.output / 'jupyter.log').open('w')
        jupyter = subprocess.Popen([sys.executable, '-I', '-c', code, '--runtime-json', str(root / 'plan.json'),
            '--workspace', str(root / 'workspace'), '--runtime-dir', str(root / 'runtime'), '--kernel-dir', str(root / 'kernels'),
            '--source', str(fixture.access.wc.REPO), '--port', str(port)], env=env, stdout=log, stderr=log, start_new_session=True)
        runner = None
        try:
            for _ in range(150):
                if jupyter.poll() is not None:
                    raise RuntimeError('Fixture Jupyter exited; inspect private test log')
                if (root / 'runtime/token').exists():
                    try:
                        reader, writer = await asyncio.open_connection('127.0.0.1', port)
                        writer.close(); await writer.wait_closed(); break
                    except OSError:
                        pass
                await asyncio.sleep(0.1)
            else:
                raise TimeoutError('Fixture Jupyter did not listen')
            auth = {'allowed': True, 'mode': 401, 'ended': False, 'jwt_seconds': 8, 'session_until': float('inf'), 'requests': []}

            @web.middleware
            async def alb(request, handler):
                if request.path == '/_fixture/control':
                    values = await request.json()
                    for key in ('allowed', 'mode', 'ended', 'jwt_seconds'):
                        if key in values: auth[key] = values[key]
                    if 'session_seconds' in values: auth['session_until'] = time.time() + values['session_seconds']
                    return web.json_response({'ok': True})
                if request.path == '/_fixture/stats':
                    return web.json_response({'requests': auth['requests']})
                if request.path == '/_workshop/reauth':
                    auth.update(allowed=True, session_until=float('inf'))
                if auth['ended']:
                    return web.Response(status=403, headers={'X-Workshop-Access': 'expired'})
                if not auth['allowed'] or time.time() >= auth['session_until']:
                    if auth['mode'] == 302:
                        return web.Response(status=302, headers={'Location': '/fixture-sign-in'})
                    return web.Response(status=auth['mode'])
                if request.method not in ('GET', 'HEAD'):
                    auth['requests'].append({'method': request.method, 'path': request.path})
                headers = {**request.headers, 'Host': fixture.HOST,
                    'X-Amzn-Oidc-Data': fixture.token(headers={'exp': time.time() + auth['jwt_seconds']})}
                if 'Origin' in headers:
                    headers['Origin'] = 'https://' + fixture.HOST
                return await handler(request.clone(headers=headers))

            config = fixture.config(port)
            config['expires_at'] = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime('%Y-%m-%dT%H:%M:%SZ')
            app = fixture.gateway.application(config, verifier=fixture.LocalVerifier(config, None), token_file=root / 'runtime/token')
            app.middlewares.append(alb)
            cert, key = root / 'cert.pem', root / 'key.pem'
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key),
                '-out', str(cert), '-days', '1', '-subj', '/CN=localhost'], check=True, capture_output=True)
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); tls.load_cert_chain(cert, key)
            runner = web.AppRunner(app, access_log=None); await runner.setup()
            site = web.TCPSite(runner, '127.0.0.1', 0, ssl_context=tls); await site.start()
            url = 'https://localhost:' + str(site._server.sockets[0].getsockname()[1])
            print('FIXTURE_READY ' + url, flush=True)
            process = await asyncio.create_subprocess_exec('node', str(Path(__file__).with_name('test_workshop_session_recovery.cjs')),
                url, str(args.output.resolve()), str(args.soak_seconds))
            status = await process.wait()
            if status:
                raise RuntimeError('Browser recovery acceptance failed')
        finally:
            if runner: await runner.cleanup()
            os.killpg(jupyter.pid, signal.SIGTERM)
            await asyncio.to_thread(jupyter.wait, 10)
            log.close()


if __name__ == '__main__':
    asyncio.run(main())
