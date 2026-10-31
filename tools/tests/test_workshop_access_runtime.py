"""Real loopback Jupyter behind the gateway; no AWS or Docker required."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
import uuid

import test_workshop_access as fixture
from aiohttp.test_utils import TestClient, TestServer

RUNTIME = fixture.access.wc.REPO / "workshop/aws/runtime"


@unittest.skipUnless(importlib.util.find_spec("jupyterlab"), "Install runtime/test-requirements.txt for real Jupyter tests")
class JupyterGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_jupyter_reader_save_kernel_and_terminal_sockets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("workspace", "runtime", "kernels/fixture", "config", "data"):
                (root / name).mkdir(parents=True)
            plan = {"course_ref": "0" * 40, "courses": [{"kernel": "fixture", "course_dir": "doris-course/01-real-time-analytics", "notebooks": [], "modules": []}]}
            (root / "plan.json").write_text(json.dumps(plan))
            (root / "kernels/fixture/kernel.json").write_text(json.dumps({"argv": [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"], "display_name": "Fixture", "language": "python"}))
            with socket.socket() as reserve:
                reserve.bind(("127.0.0.1", 0))
                port = reserve.getsockname()[1]
            code = f'import sys, runpy; sys.path.insert(0, {str(RUNTIME)!r}); runpy.run_path({str(fixture.access.OVERLAY / "jupyter_gateway.py")!r}, run_name="__main__")'
            env = {**os.environ, "JUPYTER_CONFIG_DIR": str(root / "config"), "JUPYTER_DATA_DIR": str(root / "data"), "IPYTHONDIR": str(root / "ipython")}
            with (root / "server.log").open("w") as log:
                process = subprocess.Popen([sys.executable, "-I", "-c", code,
                    "--runtime-json", str(root / "plan.json"), "--workspace", str(root / "workspace"),
                    "--runtime-dir", str(root / "runtime"), "--kernel-dir", str(root / "kernels"),
                    "--source", str(fixture.access.wc.REPO), "--port", str(port)], env=env, stdout=log, stderr=log)
                client = None
                try:
                    for _ in range(150):
                        if process.poll() is not None:
                            self.fail((root / "server.log").read_text())
                        if (root / "runtime/token").exists():
                            try:
                                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                                writer.close()
                                await writer.wait_closed()
                                break
                            except OSError:
                                pass
                        await asyncio.sleep(0.1)
                    else:
                        self.fail("Real Jupyter did not listen before timeout")
                    config = fixture.config(port)
                    app = fixture.gateway.application(config, verifier=fixture.LocalVerifier(config, None), token_file=root / "runtime/token")
                    client = TestClient(TestServer(app))
                    await client.start_server()
                    headers = {"Host": fixture.HOST, "Origin": "https://" + fixture.HOST, "X-Amzn-Oidc-Data": fixture.token()}
                    response = await client.get("/workshop/", headers=headers)
                    self.assertEqual(response.status, 200, await response.text())
                    response = await client.get("/lab", headers=headers)
                    self.assertEqual(response.status, 200)
                    self.assertIn("JupyterLab", await response.text())
                    response = await client.put("/api/contents/saved.txt", headers=headers,
                        json={"type": "file", "format": "text", "content": "saved-through-gateway"})
                    self.assertEqual(response.status, 201, await response.text())
                    self.assertEqual((root / "workspace/saved.txt").read_text(), "saved-through-gateway")
                    response = await client.post("/api/kernels", headers=headers, json={"name": "fixture"})
                    self.assertEqual(response.status, 201, await response.text())
                    kernel = (await response.json())["id"]
                    session_id = uuid.uuid4().hex
                    ws = await client.ws_connect(f"/api/kernels/{kernel}/channels?session_id={session_id}", headers=headers)
                    await ws.send_json({"channel": "shell", "header": {"msg_id": uuid.uuid4().hex,
                        "username": "fixture", "session": session_id, "msg_type": "execute_request", "version": "5.3"},
                        "parent_header": {}, "metadata": {}, "content": {"code": "7 * 6", "silent": False,
                            "store_history": False, "user_expressions": {}, "allow_stdin": False, "stop_on_error": True}})
                    async with asyncio.timeout(20):
                        while True:
                            message = await ws.receive_json()
                            if message.get("msg_type") == "execute_result":
                                self.assertEqual(message["content"]["data"]["text/plain"], "42")
                                break
                    await ws.close()
                    response = await client.post("/api/terminals", headers=headers)
                    self.assertEqual(response.status, 200, await response.text())
                    terminal = (await response.json())["name"]
                    ws = await client.ws_connect(f"/terminals/websocket/{terminal}", headers=headers)
                    await ws.send_json(["stdin", "printf 'gateway-%s\\n' terminal-ok\n"])
                    output = ""
                    async with asyncio.timeout(10):
                        while "gateway-terminal-ok" not in output:
                            message = await ws.receive_json()
                            if message[0] == "stdout":
                                output += message[1]
                    await ws.close()
                    self.assertEqual((await client.delete(f"/api/kernels/{kernel}", headers=headers)).status, 204)
                    self.assertEqual((await client.delete(f"/api/terminals/{terminal}", headers=headers)).status, 204)
                    bad = {**headers, "X-Amzn-Oidc-Data": fixture.token("subject-bob")}
                    self.assertEqual((await client.get("/api/contents", headers=bad)).status, 403)
                finally:
                    if client:
                        await client.close()
                    process.terminate()
                    try:
                        await asyncio.to_thread(process.wait, 10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        await asyncio.to_thread(process.wait, 5)
                self.assertNotIn((root / "runtime/token").read_text(), (root / "server.log").read_text())
