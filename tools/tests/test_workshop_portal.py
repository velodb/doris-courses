import copy
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workshop_access as access
sys.path.insert(0, str(access.OVERLAY))
import portal
import gateway
import jwt
from aiohttp import ClientSession
from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives.asymmetric import ec

DOMAIN = "workshops.example.com"
ARN = "arn:aws:elasticloadbalancing:us-east-1:000000000000:loadbalancer/app/test/0123456789abcdef"
KID = "00000000-0000-4000-8000-000000000000"
KEY = ec.generate_private_key(ec.SECP256R1())


def inputs(event="class-a", domain=DOMAIN):
    c = {"event_id": event, "aws_account_id": "000000000000", "region": "us-east-1", "domain": domain,
        "learner_ids": ["alice", "bob"], "course_profiles": ["rta-main"], "expires_at": "2099-01-01T00:00:00Z",
        "owner_id": "operator", "aws_profile": "offline-test"}
    a = {"account_id": c["aws_account_id"], "region": c["region"], "domain": domain, "alb_arn": ARN,
        "portal": {"instance_id": "i-00000000000000009", "listener_priority": 100}}
    identity = {"event_id": event, "issuer": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_" + event,
        "client_id": event.replace("-", ""), "login_origin": "https://dws-" + event + ".auth.us-east-1.amazoncognito.com"}
    subjects = {"alice": ["subject-alice"], "bob": ["subject-bob"]}
    plan = access.routes(c, a, {"alice": "i-00000000000000001", "bob": "i-00000000000000002"})
    return plan, identity, subjects


def config(domain=DOMAIN):
    return access.portal_config(*[list(items) for items in zip(inputs(domain=domain), inputs("class-b", domain))])


def token(subject="subject-alice", event="class-a", headers=None, key=KEY):
    activity = config()["activities"][event]
    signed = {"kid": KID, "signer": ARN, "iss": activity["issuer"], "client": activity["client_id"], "exp": time.time() + 60}
    signed.update(headers or {})
    return jwt.encode({"sub": subject}, key, algorithm="ES256", headers=signed)


class LocalVerifier(gateway.Verifier):
    async def key(self, kid):
        if kid != KID:
            raise gateway.Denied()
        return KEY.public_key()


class PortalPlanningTests(unittest.TestCase):
    def test_empty_registry_validates_global_identity_without_placeholder_assignments(self):
        c = {**config(), "activities": {}}
        portal.validate_config(c)
        for field, value in (("region", "invalid"), ("region", "us-west-2"),
                             ("alb_arn", "arn:aws:elasticloadbalancing:us-east-1:000000000000:listener/app/test/id"),
                             ("activities", [])):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                portal.validate_config({**c, field: value})

    def test_partner_and_company_have_same_paths_and_separate_service_origins(self):
        for domain in (DOMAIN, "partner-a." + DOMAIN):
            c = config(domain)
            portal.validate_config(c)
            self.assertEqual(c["domain"], domain)
            assignment = c["activities"]["class-a"]["assignments"]["subject-alice"]
            self.assertEqual(assignment["services"]["jupyter"], "https://class-a-alice-jupyter." + domain)
            self.assertFalse(any(secret in json.dumps(c) for secret in ("password", "client_secret", "subject-charlie")))

    def test_assignment_files_cannot_cross_namespace_event_or_subject_ownership(self):
        p, i, s = inputs()
        for modified in ({**i, "event_id": "wrong"},):
            with self.assertRaises(ValueError):
                access.portal_config([p], [modified], [s])
        with self.assertRaises(ValueError):
            access.portal_config([p, p], [i, i], [s, s])
        with self.assertRaises(ValueError):
            access.portal_config([p], [i], [{"alice": ["shared"], "bob": ["shared"]}])
        other = inputs("class-b", "partner-a." + DOMAIN)
        with self.assertRaises(ValueError):
            access.portal_config([p, other[0]], [i, other[1]], [s, other[2]])
        with self.assertRaises(ValueError):
            access.portal_config([p], [], [s])

    def test_untrusted_redirects_and_secret_fields_are_rejected(self):
        c = config()
        for url in ("https://evil.example.com", "//evil.example.com", "https://class-a-bob-jupyter." + DOMAIN,
                    "https://class-a-alice-jupyter." + DOMAIN + "/?next=evil"):
            broken = copy.deepcopy(c)
            broken["activities"]["class-a"]["assignments"]["subject-alice"]["services"]["jupyter"] = url
            with self.assertRaises(ValueError):
                portal.validate_config(broken)
        c["client_secret"] = "forbidden"
        with self.assertRaises(ValueError):
            portal.validate_config(c)

    def test_portal_priority_and_host_allocation_cannot_overlap_learners(self):
        p, _, _ = inputs()
        self.assertEqual(p["terraform"]["portal"]["priority"], 1030)
        c = {"event_id": "event", "aws_account_id": "000000000000", "region": "us-east-1", "domain": DOMAIN,
            "learner_ids": ["alice"], "course_profiles": ["rta-main"], "expires_at": "2099-01-01T00:00:00Z",
            "owner_id": "operator", "aws_profile": "offline-test"}
        a = p["terraform"]["access"]
        with self.assertRaises(ValueError):
            access.routes(c, a, {"alice": a["portal"]["instance_id"]})
        with self.assertRaises(ValueError):
            access.routes(c, a, {"alice": "i-00000000000000001"}, priority_base=100)

    def test_portal_bundle_is_separate_and_hash_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "portal"
            access.bundle(target, portal=True)
            self.assertEqual(set(json.loads((target / "overlay.json").read_text())["sha256"]), access.PORTAL_FILES)
            self.assertNotIn("install_gateway.sh", {path.name for path in target.iterdir()})

    def test_public_cli_writes_private_runtime_config_and_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            arguments = []
            for name, value in zip(("plan", "identity", "subjects"), inputs()):
                path = root / (name + ".json")
                path.write_text(json.dumps(value))
                arguments.extend(["--" + name, str(path)])
            output = root / "portal.json"
            command = [sys.executable, str(access.wc.REPO / "tools/workshop_access.py")]
            process = subprocess.run(command + ["portal-config", *arguments, "--output", str(output)], capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            portal.validate_config(json.loads(output.read_text()))
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("subject-alice", process.stdout)
            process = subprocess.run(command + ["portal-bundle", "--output-dir", str(root / "bundle")], capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual((root / "bundle").stat().st_mode & 0o777, 0o700)


class PortalHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = config()
        self.client = await self.start(self.config)

    async def start(self, c):
        verifiers = {event: LocalVerifier({**activity, "alb_arn": c["alb_arn"], "region": c["region"]}, None)
            for event, activity in c["activities"].items()}
        client = TestClient(TestServer(portal.application(c, verifiers=verifiers)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        return client

    def headers(self, signed=None, event="class-a", **extra):
        return {"Host": event + "-entry." + DOMAIN, "X-Amzn-Oidc-Data": signed or token(event=event), **extra}

    async def test_empty_portal_serves_only_public_index_and_health(self):
        client = await self.start({**self.config, "activities": {}})
        response = await client.get(portal.PATH, headers={"Host": DOMAIN})
        self.assertEqual(response.status, 200)
        body = await response.text()
        self.assertIn("No activities are available", body)
        self.assertNotIn("href=", body)
        for host, path in (("class-a-entry." + DOMAIN, portal.PATH), (DOMAIN, "/api/contents")):
            response = await client.get(path, headers={"Host": host})
            self.assertEqual(response.status, 403)
        response = await client.get("/healthz", headers={"Host": DOMAIN})
        self.assertEqual(response.status, 200)

    async def test_public_index_contains_activity_links_without_assignments(self):
        response = await self.client.get("/doris-courses/", headers={"Host": DOMAIN})
        self.assertEqual(response.status, 200)
        html = await response.text()
        self.assertIn("https://class-a-entry." + DOMAIN + "/doris-courses/", html)
        self.assertNotIn("alice", html)
        self.assertNotIn("subject-", html)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

    async def test_valid_signature_shows_only_the_callers_assigned_links(self):
        for subject, own, other in (("subject-alice", "alice", "bob"), ("subject-bob", "bob", "alice")):
            response = await self.client.get(portal.PATH, headers=self.headers(token(subject)))
            self.assertEqual(response.status, 200)
            html = await response.text()
            self.assertIn(f"https://class-a-{own}-jupyter.{DOMAIN}/workshop/", html)
            self.assertNotIn(other, html)
            self.assertNotIn("subject-", html)

    async def test_missing_forged_unknown_subject_and_cross_activity_tokens_are_denied(self):
        cases = [(None, 401), ("forged", 401), (token("unassigned"), 403), (token(event="class-b"), 401),
            (token(headers={"signer": ARN + "other"}), 401), (token(headers={"exp": time.time() - 1}), 401),
            (token(key=ec.generate_private_key(ec.SECP256R1())), 401)]
        for signed, status in cases:
            headers = self.headers(signed) if signed else {"Host": "class-a-entry." + DOMAIN, "X-Amzn-Oidc-Identity": "subject-alice"}
            response = await self.client.get(portal.PATH, headers=headers)
            self.assertEqual(response.status, status)
            self.assertNotIn("alice", await response.text())

    async def test_unknown_hosts_paths_queries_writes_and_websockets_are_denied(self):
        for host, path in ((DOMAIN, "/"), (DOMAIN, "/another-workshop/"), ("evil.example.com", portal.PATH),
                ("class-a-entry." + DOMAIN, "/api/contents"), ("class-a-entry." + DOMAIN, "/doris-courses/?next=https://evil.example.com")):
            response = await self.client.get(path, headers=self.headers(Host=host))
            self.assertIn(response.status, (400, 403))
        response = await self.client.post(portal.PATH, headers=self.headers())
        self.assertEqual(response.status, 405)
        async with ClientSession() as session:
            with self.assertRaises(__import__("aiohttp").WSServerHandshakeError):
                await session.ws_connect(self.client.make_url(portal.PATH), headers=self.headers())

    async def test_expired_activity_disappears_and_cached_authentication_cannot_enter(self):
        c = copy.deepcopy(self.config)
        c["activities"]["class-a"]["expires_at"] = "2000-01-01T00:00:00Z"
        client = await self.start(c)
        response = await client.get(portal.PATH, headers={"Host": DOMAIN})
        self.assertNotIn("class-a", await response.text())
        response = await client.get(portal.PATH, headers=self.headers())
        self.assertEqual(response.status, 403)

    async def test_logout_requires_signed_identity_and_exact_origin(self):
        for method, origin, status in (("POST", "https://evil.example.com", 403), ("GET", None, 403),
                ("POST", "https://class-a-entry." + DOMAIN, 303)):
            headers = self.headers()
            if origin:
                headers["Origin"] = origin
            response = await self.client.request(method, "/_workshop/logout", headers=headers, allow_redirects=False)
            self.assertEqual(response.status, status)
            if status == 303:
                self.assertTrue(response.headers["Location"].startswith(self.config["activities"]["class-a"]["login_origin"] + "/logout?"))
                self.assertEqual(len(response.headers.getall("Set-Cookie")), 6)
        response = await self.client.get("/_workshop/logged-out", headers={"Host": "class-a-entry." + DOMAIN})
        self.assertEqual(response.status, 200)

    async def test_logout_form_policy_allows_only_its_own_cognito_redirect(self):
        for event, activity in self.config["activities"].items():
            response = await self.client.get(portal.PATH, headers=self.headers(event=event))
            self.assertEqual(response.status, 200)
            policy = response.headers["Content-Security-Policy"]
            self.assertEqual(policy.split("form-action ")[1], "'self' " + activity["login_origin"] + "/logout")
            response = await self.client.post("/_workshop/logout", headers=self.headers(
                event=event, Origin=f"https://{event}-entry.{DOMAIN}"), allow_redirects=False)
            self.assertEqual(response.status, 303)
            self.assertTrue(response.headers["Location"].startswith(activity["login_origin"] + "/logout?"))
        response = await self.client.get(portal.PATH, headers={"Host": DOMAIN})
        self.assertEqual(response.headers["Content-Security-Policy"].split("form-action ")[1], "'self'")

    async def test_partner_namespace_uses_identical_entry_path(self):
        domain = "partner-a." + DOMAIN
        client = await self.start(config(domain))
        response = await client.get(portal.PATH, headers={"Host": domain})
        self.assertIn("https://class-a-entry." + domain + portal.PATH, await response.text())
        response = await client.get(portal.PATH, headers=self.headers(Host="class-a-entry." + domain))
        self.assertEqual(response.status, 200)
        self.assertIn("class-a-alice-jupyter." + domain, await response.text())


if __name__ == "__main__":
    unittest.main()
