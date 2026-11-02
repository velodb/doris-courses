import asyncio
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workshop_access as access
import workshop_accounts as accounts
sys.path.insert(0, str(access.OVERLAY))
import gateway
import jwt
from aiohttp import ClientSession, WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives.asymmetric import ec

ARN = "arn:aws:elasticloadbalancing:us-east-1:000000000000:loadbalancer/app/test/0123456789abcdef"
KID = "00000000-0000-4000-8000-000000000000"
HOST = "event-alice-jupyter.workshops.example.com"


def config(port=18888):
    return {"schema_version": 1, "event_id": "event", "learner_id": "alice", "region": "us-east-1",
        "alb_arn": ARN, "issuer": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_test",
        "client_id": "exampleclient", "subjects": ["subject-alice"], "expires_at": "2099-01-01T00:00:00Z",
        "login_origin": "https://dws-event.auth.us-east-1.amazoncognito.com",
        "services": {HOST: {"port": port, "jupyter": True, "redirect_origins": []}}}


KEY = ec.generate_private_key(ec.SECP256R1())


def token(subject="subject-alice", *, headers=None, signing_key=KEY):
    c = config()
    header = {"kid": KID, "signer": ARN, "iss": c["issuer"], "client": c["client_id"], "exp": time.time() + 60}
    header.update(headers or {})
    return jwt.encode({"sub": subject}, signing_key, algorithm="ES256", headers=header)


class LocalVerifier(gateway.Verifier):
    async def key(self, kid):
        if kid != KID:
            raise gateway.Denied()
        return KEY.public_key()


class IdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_signed_alb_header_expiry_is_validated(self):
        v = LocalVerifier(config(), None)
        self.assertGreater(await v.verify(token()), time.time())
        for expiry in (time.time() - 1, None, "invalid", float("inf"), True):
            with self.subTest(expiry=expiry), self.assertRaises(gateway.Denied):
                await v.verify(token(headers={"exp": expiry}))

    async def test_wrong_signer_issuer_client_subject_key_and_signature_are_denied(self):
        v = LocalVerifier(config(), None)
        for header in ({"signer": ARN + "bad"}, {"iss": "https://other.example.com"},
                       {"client": "other"}, {"kid": "../../metadata"}):
            with self.subTest(header=header), self.assertRaises(gateway.Denied):
                await v.verify(token(headers=header))
        for t in (token("subject-bob"), token(signing_key=ec.generate_private_key(ec.SECP256R1())), "forged", "x" * 17000):
            with self.assertRaises(gateway.Denied):
                await v.verify(t)

    async def test_key_endpoint_cannot_be_selected_by_the_caller(self):
        v = gateway.Verifier(config(), None)
        for kid in ("http://127.0.0.1", "../../latest/meta-data", "", None):
            with self.assertRaises(gateway.Denied):
                await v.key(kid)


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []

        async def upstream(request):
            self.requests.append((request.raw_path, dict(request.headers), await request.read()))
            if request.path == "/redirect":
                return web.Response(status=302, headers={"Location": f"http://127.0.0.1:{self.server.port}/deep?a=1#frag"})
            if request.path == "/cookie":
                return web.Response(headers=[("Set-Cookie", "app=one; Domain=example.com; Path=/; HttpOnly"),
                                             ("Set-Cookie", "second=two; Path=/")])
            if request.path == "/socket":
                ws = web.WebSocketResponse(protocols=("v1.kernel.websocket.jupyter.org",))
                await ws.prepare(request)
                async for message in ws:
                    if message.type == WSMsgType.TEXT:
                        await ws.send_str(message.data)
                    elif message.type == WSMsgType.BINARY:
                        await ws.send_bytes(message.data)
                return ws
            return web.Response(body=b"backend", headers={"Content-Type": "text/plain"})

        upstream_app = web.Application()
        upstream_app.router.add_route("*", "/{tail:.*}", upstream)
        self.server = TestServer(upstream_app)
        await self.server.start_server()
        self.c = config(self.server.port)
        self.temp = tempfile.TemporaryDirectory()
        self.token_file = Path(self.temp.name) / "token"
        self.token_file.write_text("local-jupyter-secret")
        self.client = TestClient(TestServer(gateway.application(self.c, verifier=LocalVerifier(self.c, None), token_file=self.token_file)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.close()
        self.temp.cleanup()

    def headers(self, **extra):
        return {"Host": HOST, "X-Amzn-Oidc-Data": token(), **extra}

    async def test_anonymous_forged_other_owner_and_unknown_host_never_reach_backend(self):
        for headers, status in (({"Host": HOST}, 401), (self.headers(**{"X-Amzn-Oidc-Data": "forged"}), 401),
            (self.headers(**{"X-Amzn-Oidc-Data": token("subject-bob")}), 403),
            (self.headers(Host="event-bob-jupyter.workshops.example.com"), 403)):
            for path in ("/workshop/", "/api/contents", "/api/kernels/one/channels", "/terminals/websocket/1"):
                response = await self.client.get(path, headers=headers)
                self.assertEqual(response.status, status)
        self.assertEqual(self.requests, [])

    async def test_proxy_preserves_raw_path_and_strips_auth_and_spoofed_proxy_headers(self):
        response = await self.client.get("/deep%20link?q=%2F", headers=self.headers(**{
            "X-Amzn-Oidc-AccessToken": "global-secret", "X-Forwarded-Host": "bob.example.com",
            "X-Forwarded-Proto": "http", "Forwarded": "host=bob", "Remote-User": "bob",
            "Cookie": "DorisAuthone=secret; app=ok", "authorization": "Bearer attacker"}))
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.read(), b"backend")
        path, headers, _ = self.requests[-1]
        self.assertEqual(path, "/deep%20link?q=/")  # aiohttp normalizes the test URL before sending.
        lower = {k.lower(): v for k, v in headers.items()}
        self.assertFalse(any(k.startswith("x-amzn-") for k in lower))
        self.assertEqual(lower["authorization"], "token local-jupyter-secret")
        self.assertEqual(lower["x-forwarded-host"], HOST)
        self.assertNotIn("forwarded", lower)
        self.assertNotIn("remote-user", lower)
        self.assertEqual(lower["cookie"], "app=ok")

    async def test_write_requires_canonical_origin_and_preserves_body(self):
        for origin in (None, "https://bob.example.com", "null"):
            headers = self.headers()
            if origin:
                headers["Origin"] = origin
            response = await self.client.post("/api/contents", headers=headers, data=b"private")
            self.assertEqual(response.status, 403)
        response = await self.client.post("/api/contents", headers=self.headers(Origin="https://" + HOST), data=b"saved")
        self.assertEqual(response.status, 200)
        self.assertEqual(self.requests[-1][2], b"saved")

    async def test_cookies_remain_separate_host_only_and_secure(self):
        response = await self.client.get("/cookie", headers=self.headers())
        values = response.headers.getall("Set-Cookie")
        self.assertEqual(len(values), 2)
        self.assertTrue(all("Secure" in v and "Domain=" not in v and "SameSite=Lax" in v for v in values))
        self.assertIn("HttpOnly", values[0])

    async def test_redirects_are_parsed_and_keep_deep_link_query_and_fragment(self):
        response = await self.client.get("/redirect", headers=self.headers(), allow_redirects=False)
        self.assertEqual(response.headers["Location"], f"https://{HOST}/deep?a=1#frag")

    async def test_unavailable_service_is_503_and_recovers(self):
        await self.server.close()
        response = await self.client.get("/", headers=self.headers())
        self.assertEqual(response.status, 503)
        self.assertIn("Start the course service", await response.text())

    async def test_websocket_text_binary_subprotocol_and_ownership(self):
        for header in (self.headers(Origin="https://bob.example.com"),
                       self.headers(Origin="https://" + HOST, **{"X-Amzn-Oidc-Data": token("subject-bob")})):
            async with ClientSession() as session:
                with self.assertRaises(__import__("aiohttp").WSServerHandshakeError):
                    await session.ws_connect(self.client.make_url("/socket"), headers=header)
        ws = await self.client.ws_connect("/socket", headers=self.headers(Origin="https://" + HOST), protocols=("v1.kernel.websocket.jupyter.org",))
        self.assertEqual(ws.protocol, "v1.kernel.websocket.jupyter.org")
        await ws.send_str("hello")
        self.assertEqual((await ws.receive()).data, "hello")
        await ws.send_bytes(b"binary")
        self.assertEqual((await ws.receive()).data, b"binary")
        await ws.close()

    async def test_socket_closes_at_signed_identity_expiry(self):
        ws = await self.client.ws_connect("/socket", headers=self.headers(Origin="https://" + HOST,
                **{"X-Amzn-Oidc-Data": token(headers={"exp": time.time() + 0.3})}))
        message = await asyncio.wait_for(ws.receive(), 2)
        self.assertEqual(message.type, WSMsgType.CLOSE)

    async def test_logout_clears_sharded_service_cookie_and_redirects_to_cognito(self):
        response = await self.client.post("/_workshop/logout", headers=self.headers(Origin="https://" + HOST), allow_redirects=False)
        self.assertEqual(response.status, 303)
        self.assertTrue(response.headers["Location"].startswith(self.c["login_origin"] + "/logout?"))
        self.assertEqual(len(response.headers.getall("Set-Cookie")), 6)
        self.assertEqual(self.requests, [])

    async def test_dynamic_service_fails_closed_and_follows_port_changes_without_restart(self):
        registry = Path(self.temp.name) / "service-ports.json"
        config = copy.deepcopy(self.c)
        config["services"][HOST].update(dynamic_port="dw-flink", jupyter=False)
        client = TestClient(TestServer(gateway.application(config, verifier=LocalVerifier(config, None), registry_file=registry)))
        await client.start_server()
        other = TestServer(web.Application())
        async def alternate(request):
            return web.Response(text="new-port")
        other.app.router.add_get("/", alternate)
        await other.start_server()
        try:
            response = await client.get("/", headers=self.headers())
            self.assertEqual(response.status, 503)
            for learner, port, status, body in (("bob", self.server.port, 503, None),
                    ("alice", self.server.port, 200, "backend"), ("alice", other.port, 200, "new-port")):
                registry.write_text(json.dumps({"schema_version": 1, "event_id": "event", "learner_id": learner, "ports": {"dw-flink": port}}))
                response = await client.get("/", headers=self.headers())
                self.assertEqual(response.status, status)
                if body:
                    self.assertEqual(await response.text(), body)
        finally:
            await client.close()
            await other.close()


class PlanningTests(unittest.TestCase):
    def inputs(self):
        c = {"event_id": "event", "aws_account_id": "000000000000", "region": "us-east-1", "domain": "workshops.example.com",
             "learner_ids": ["alice", "bob"], "course_profiles": ["rta-main"], "expires_at": "2099-01-01T00:00:00Z", "owner_id": "operator", "aws_profile": "offline-test"}
        a = {"account_id": c["aws_account_id"], "region": c["region"], "domain": c["domain"], "alb_arn": ARN}
        return c, a, {"alice": "i-00000000000000001", "bob": "i-00000000000000002"}

    def test_separate_dns_identity_is_bound_to_shared_dns_account(self):
        c, a, instances = self.inputs()
        a["dns_account_id"] = "111111111111"
        for dns in ({"account_id": a["dns_account_id"], "profile": "dns-operator"},
                    {"account_id": a["dns_account_id"], "role_arn": "arn:aws:iam::111111111111:role/workshop-dns"}):
            with self.subTest(dns=dns):
                plan = access.routes(c, a, instances, dns=dns)
                self.assertEqual(plan["terraform"]["dns"], dns)
                self.assertEqual(plan["terraform"]["expected_account_id"], c["aws_account_id"])
                self.assertEqual(plan["terraform"]["aws_profile"], c["aws_profile"])
        invalid = (None, {}, {"account_id": "222222222222", "profile": "other"},
            {"account_id": a["dns_account_id"], "profile": ""},
            {"account_id": a["dns_account_id"], "profile": "dns", "role_arn": "arn:aws:iam::111111111111:role/dns"},
            {"account_id": a["dns_account_id"], "role_arn": "arn:aws:iam::222222222222:role/dns"},
            {"account_id": a["dns_account_id"], "profile": "dns", "secret": "forbidden"})
        for dns in invalid:
            with self.subTest(dns=dns), self.assertRaises(ValueError):
                access.routes(c, a, instances, dns=dns)

    def test_routes_are_per_learner_port_overrides_are_explicit_and_roster_order_is_irrelevant(self):
        c, a, instances = self.inputs()
        plan = access.routes(c, a, instances, overrides={"alice": {"rta-fe": 28030}})
        self.assertIn("28030", plan["browser_routes"]["alice"])
        self.assertIn("18030", plan["browser_routes"]["bob"])
        c["learner_ids"].reverse()
        self.assertEqual(plan, access.routes(c, a, instances, overrides={"alice": {"rta-fe": 28030}}))

    def test_gateway_files_cannot_reassign_one_subject_to_two_learners(self):
        c, a, instances = self.inputs()
        plan = access.routes(c, a, instances)
        identity = {k: config()[k] for k in ("event_id", "issuer", "client_id", "login_origin")}
        with self.assertRaises(ValueError):
            access.gateway_configs(plan, identity, {"alice": ["one"], "bob": ["one"]})
        configs = access.gateway_configs(plan, identity, {"alice": ["one"], "bob": ["two"]})
        self.assertNotIn("two", json.dumps(configs["alice"]))
        self.assertNotIn("client_secret", json.dumps(configs))

    def test_selected_service_catalog_generates_public_urls_and_explicit_host_ports(self):
        c, a, instances = self.inputs()
        c["course_profiles"] = ["rta-main", "rta-metabase", "dw-main", "dw-bi", "dw-cdc", "obs-main", "ai-functions"]
        plan = access.routes(c, a, instances)
        browser = access.browser_routes(plan, "alice")
        self.assertEqual(len(browser["services"]), 15)
        self.assertEqual(len(plan["terraform"]["routes"]), 30)
        env = access.host_environment(plan, "alice")
        self.assertEqual(env["DW_BI_PUBLIC_URL"], browser["services"]["dw-superset"])
        self.assertEqual(env["DORIS_OBS_GRAFANA_PORT"], "23000")
        self.assertEqual(plan["services"]["alice"][browser["services"]["dw-flink"].removeprefix("https://")]["dynamic_port"], "dw-flink")
        self.assertTrue(all("bob" not in value for value in env.values()))

    def test_overlay_binds_exact_files_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "overlay"
            access.bundle(directory)
            manifest = json.loads((directory / "overlay.json").read_text())
            for name, digest in manifest["sha256"].items():
                self.assertEqual(hashlib.sha256((directory / name).read_bytes()).hexdigest(), digest)
            with self.assertRaises(ValueError):
                access.bundle(directory)

    def test_config_rejects_shared_secrets_and_unmapped_ports(self):
        c = config()
        c["client_secret"] = "must-stay-in-control-plane"
        with self.assertRaises(ValueError):
            gateway.validate_config(c)
        c = config(8080)
        with self.assertRaises(ValueError):
            gateway.validate_config(c)

    def test_temporary_accounts_are_owned_silent_and_credentials_remain_private(self):
        c, _, _ = self.inputs()
        identity = {"event_id": "event", "user_pool_id": "us-east-1_test", "issuer": config()["issuer"]}
        calls = []

        def call(config, operation, parameters, **kwargs):
            calls.append((operation, parameters))
            if operation == "get-caller-identity":
                return {"Account": c["aws_account_id"]}
            if operation == "list-tags-for-resource":
                return {"Tags": {"Project": "doris-workshop", "Event": "event", "Scope": "activity-access", "Owner": "operator"}}
            self.assertEqual(parameters["MessageAction"], "SUPPRESS")
            self.assertGreaterEqual(len(parameters["TemporaryPassword"]), 14)
            return {"User": {"Username": parameters["Username"], "UserStatus": "FORCE_CHANGE_PASSWORD",
                "Attributes": [{"Name": "sub", "Value": "subject-" + parameters["Username"]}]}}

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "accounts"
            subjects = accounts.create(c, identity, output, call=call)
            self.assertEqual(subjects["alice"], ["subject-alice"])
            self.assertEqual((output / "alice.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            self.assertNotIn("temporary_password", (output / "subjects.json").read_text())
        self.assertEqual([op for op, _ in calls].count("admin-create-user"), 2)

    def test_accounts_refuse_wrong_pool_ownership_before_mutation(self):
        c, _, _ = self.inputs()
        identity = {"event_id": "event", "user_pool_id": "us-east-1_test", "issuer": config()["issuer"]}

        def call(config, operation, parameters, **kwargs):
            if operation == "get-caller-identity":
                return {"Account": c["aws_account_id"]}
            self.assertEqual(operation, "list-tags-for-resource")
            return {"Tags": {"Event": "other"}}

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                accounts.create(c, identity, Path(tmp) / "accounts", call=call)


if __name__ == "__main__":
    unittest.main()
