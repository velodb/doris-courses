import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from dw_course import bi, streaming
from dw_course.runtime import WarehouseLab


class BrowserTests(unittest.TestCase):
    def test_ui_links_use_protected_origins_while_superset_api_stays_internal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "routes.json"
            path.write_text(json.dumps({"schema_version": 1, "event_id": "event", "learner_id": "alice", "ports": {},
                "services": {"dw-fe": "https://alice-fe.example.test", "dw-superset": "https://alice-bi.example.test", "dw-flink": "https://alice-flink.example.test"}}))
            with patch.dict(os.environ, {"DORIS_BROWSER_ROUTES_FILE": str(path), "DORIS_WORKSHOP_EVENT_ID": "event",
                    "DORIS_WORKSHOP_LEARNER_ID": "alice"}), patch.object(bi.requests, "Session") as factory:
                factory.return_value.request.return_value.json.side_effect = [{"access_token": "fixture"}, {"result": "csrf"}]
                api = bi.SupersetAPI()
                self.assertTrue(all(call.args[1].startswith("http://127.0.0.1:") for call in api.session.request.call_args_list))
                self.assertEqual(bi.superset_browser_url(), "https://alice-bi.example.test")
                lab = object.__new__(WarehouseLab)
                self.assertEqual(lab.profile_url("query/a"), "https://alice-fe.example.test/QueryProfile/query%2Fa")
                with patch.object(streaming, "REST", "http://127.0.0.1:49234"):
                    self.assertEqual(streaming.flink_browser_url(), "https://alice-flink.example.test")

    def test_flink_port_discovery_registers_the_actual_port(self):
        with patch.object(streaming, "compose", return_value="127.0.0.1:49234\n"), patch.object(streaming, "register_browser_port") as register:
            self.assertEqual(streaming.configure_streaming_port(), 49234)
            register.assert_called_once_with("dw-flink", 49234)
            self.assertEqual(streaming.REST, "http://127.0.0.1:49234")
