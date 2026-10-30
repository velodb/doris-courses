import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workshop_urls as urls


class RouteGenerationTests(unittest.TestCase):
    def test_routes_bind_an_explicit_assignment(self):
        config = {"event_id": "event-a", "learner_ids": ["alice", "bob"]}
        ports = {"18030": "http://127.0.0.1:28030"}
        first = urls.build_routes(config, "alice", ports)
        second = urls.build_routes(config, "bob", ports)
        self.assertEqual(first["learner_id"], "alice")
        self.assertEqual(second["learner_id"], "bob")
        with self.assertRaises(urls.wc.ConfigError):
            urls.build_routes(config, "unassigned", ports)
        with self.assertRaises(ValueError):
            urls.browser.browser_url("http://localhost:18030", routes=first, event_id="event-a", learner_id="bob")
