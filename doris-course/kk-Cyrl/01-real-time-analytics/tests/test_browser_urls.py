import os
import unittest
from unittest.mock import patch

from doris_course import DorisLab


class LabBrowserTests(unittest.TestCase):
    def test_browser_output_leaves_internal_requests_unchanged(self):
        lab = object.__new__(DorisLab)
        lab._profile_http_host, lab._profile_http_port = "127.0.0.1", 18131
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(lab.fe_browser_url, "http://127.0.0.1:18131")
        with patch("doris_course.doris_client.browser_url", return_value="https://learner.example.test/fe") as translate, \
                patch("doris_course.doris_client.display") as display:
            self.assertEqual(lab.show_browser_link(lab.fe_http_url, "Open <FE>"), "https://learner.example.test/fe")
            self.assertIn("Open &lt;FE&gt;", display.call_args.args[0].data)
            self.assertEqual(lab.fe_http_url, "http://127.0.0.1:18131")
            self.assertIn("http://127.0.0.1:18131/api/profile", lab.profile_command("query-id"))
            translate.assert_called_once_with("http://127.0.0.1:18131")
