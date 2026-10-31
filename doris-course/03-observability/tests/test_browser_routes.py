import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from doris_course import ObservabilityLab


class BrowserTests(unittest.TestCase):
    def test_ui_origins_are_separate_from_collector_and_api_endpoints(self):
        services = {"obs-" + name: "https://alice-" + name + ".example.test" for name in ("fe", "grafana", "demo", "mailpit", "litefuse")}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "routes.json"
            path.write_text(json.dumps({"schema_version": 1, "event_id": "event", "learner_id": "alice", "ports": {}, "services": services}))
            with patch.dict(os.environ, {"DORIS_BROWSER_ROUTES_FILE": str(path), "DORIS_WORKSHOP_EVENT_ID": "event", "DORIS_WORKSHOP_LEARNER_ID": "alice"}):
                lab = object.__new__(ObservabilityLab)
                self.assertEqual(lab.fe_browser_url, services["obs-fe"])
                for name in ("grafana", "demo", "mailpit", "litefuse"):
                    self.assertTrue(getattr(lab, name + "_url").startswith("http://127.0.0.1:"))
                    self.assertEqual(getattr(lab, name + "_browser_url"), services["obs-" + name])
                self.assertTrue(lab.otlp_http_url.startswith("http://127.0.0.1:"))
                with patch.object(lab, "_http_status", return_value=200) as read:
                    lab._storefront_status()
                    read.assert_called_once_with(lab.demo_url + "/")
