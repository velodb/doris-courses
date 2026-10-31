import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from doris_labkit.browser import browser_url, browser_redirect, validate_routes, register_browser_port


class BrowserRoutesTests(unittest.TestCase):
    def setUp(self):
        self.routes = {"schema_version": 1, "event_id": "event-a", "learner_id": "learner-a",
                       "ports": {"18030": "https://learner.example.test/e/event-a/l/learner-a/fe",
                                 "28131": "http://127.0.0.1:38131"}}
        self.args = dict(routes=self.routes, event_id="event-a", learner_id="learner-a")

    def test_local_default_and_relative_links_are_unchanged(self):
        with patch.dict(os.environ, {}, clear=True):
            for url in ("http://localhost:18030", "../images/chart.png", "course.md?lang=en#heading"):
                self.assertEqual(browser_url(url), url)
                if not url.startswith("http"):
                    self.assertEqual(browser_url(url, **self.args), url)

    def test_exact_ports_paths_queries_and_websocket_schemes(self):
        self.assertEqual(browser_url("http://127.0.0.1:18030/api/profile?query_id=a%2Fb#text", **self.args),
                         "https://learner.example.test/e/event-a/l/learner-a/fe/api/profile?query_id=a%2Fb#text")
        self.assertEqual(browser_url("ws://localhost:18030/socket?x=1", **self.args),
                         "wss://learner.example.test/e/event-a/l/learner-a/fe/socket?x=1")
        self.assertEqual(browser_url("http://localhost:28131/path", **self.args), "http://127.0.0.1:38131/path")
        self.assertEqual(browser_url("https://docs.example.test/localhost:18030", **self.args),
                         "https://docs.example.test/localhost:18030")
        with self.assertRaises(ValueError):
            browser_url("http://localhost:18131/", **self.args)
        for path in ("/../bob", "/%2e%2e/bob", "/%252e%252e/bob", "/foo%5c..%5cbob"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                browser_url("http://localhost:18030" + path, **self.args)

    def test_relative_and_absolute_service_redirects(self):
        for location in ("/login?next=%2F", "http://localhost:18030/login?next=%2F"):
            self.assertEqual(browser_redirect(location, "http://localhost:18030/api/status", **self.args),
                             "https://learner.example.test/e/event-a/l/learner-a/fe/login?next=%2F")

    def test_ownership_and_unsafe_destinations_fail(self):
        with self.assertRaises(ValueError):
            browser_url("http://localhost:18030", **{**self.args, "learner_id": "learner-b"})
        with self.assertRaises(ValueError):
            browser_url("http://localhost:18030", **{**self.args, "event_id": "event-b"})
        for target in ("http://remote.example.test", "https://user:secret@example.test", "https://example.test?token=bad",
                       "https://example.test/../other", "javascript:alert(1)", "https://example.test/#fragment"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                validate_routes({**self.routes, "ports": {"18030": target}}, "event-a", "learner-a")

    def test_file_configuration_requires_explicit_matching_identity(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routes.json"
            path.write_text(json.dumps(self.routes))
            with patch.dict(os.environ, {"DORIS_BROWSER_ROUTES_FILE": str(path)}, clear=True):
                with self.assertRaises(ValueError):
                    browser_url("http://localhost:18030")
                with patch.dict(os.environ, {"DORIS_WORKSHOP_EVENT_ID": "event-a", "DORIS_WORKSHOP_LEARNER_ID": "learner-a"}):
                    self.assertIn("learner.example.test", browser_url("http://localhost:18030"))

    def test_named_service_keeps_browser_origin_stable_across_dynamic_port_changes(self):
        self.routes["services"] = {"dw-flink": "https://flink.example.test"}
        for port in (49123, 49234):
            self.assertEqual(browser_url(f"http://127.0.0.1:{port}/#/jobs?limit=1", service="dw-flink", **self.args),
                             "https://flink.example.test/#/jobs?limit=1")
        with self.assertRaises(ValueError):
            browser_url("http://127.0.0.1:49123", service="unassigned", **self.args)

    def test_dynamic_registry_is_atomic_and_bound_to_the_learner(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.routes["services"] = {"dw-flink": "https://flink.example.test"}
            (root / "routes.json").write_text(json.dumps(self.routes))
            env = {"DORIS_BROWSER_ROUTES_FILE": str(root / "routes.json"),
                   "DORIS_WORKSHOP_EVENT_ID": "event-a", "DORIS_WORKSHOP_LEARNER_ID": "learner-a",
                   "DORIS_WORKSHOP_PORT_REGISTRY": str(root / "ports.json")}
            with patch.dict(os.environ, env, clear=True):
                register_browser_port("dw-flink", 49123)
                register_browser_port("dw-flink", 49234)
                self.assertEqual(json.loads((root / "ports.json").read_text())["ports"], {"dw-flink": 49234})
                with self.assertRaises(ValueError):
                    register_browser_port("unassigned", 49234)
                record = json.loads((root / "ports.json").read_text())
                record["learner_id"] = "other"
                (root / "ports.json").write_text(json.dumps(record))
                with self.assertRaises(ValueError):
                    register_browser_port("dw-flink", 49345)


if __name__ == "__main__":
    unittest.main()
