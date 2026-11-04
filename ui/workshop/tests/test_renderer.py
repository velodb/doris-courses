"""UI presentation boundaries and portable packaging; no AWS service dependencies."""
import hashlib
import importlib.util
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import doris_workshop_ui as ui


class RendererTests(unittest.TestCase):
    def setUp(self):
        self.renderer = ui.Renderer(asset_base='/doris-courses/static', home='/doris-courses/')

    def test_hostile_labels_are_escaped_and_script_routes_are_rejected(self):
        html = self.renderer.activities([{'id': '<script>alert(1)</script>', 'href': '/activity/'}])
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        for bad in ('javascript:alert(1)', '//other.example.com/', 'http://other.example.com/', '/\\other.example.com/', '/bad\npath'):
            with self.subTest(url=bad), self.assertRaises(ValueError):
                self.renderer.activities([{'id': 'test', 'href': bad}])

    def test_pages_are_server_rendered_and_do_not_contain_preview_identity_or_script(self):
        html = self.renderer.workspace(event='class-a', learner='learner-a', course='https://own.example.com/workshop/',
            tools=[{'id': 'jupyter', 'title': 'Notebooks', 'href': 'https://own.example.com/lab', 'external': True}],
            extra=[], logout='/_workshop/logout')
        self.assertIn('learner-a', html)
        self.assertIn('https://own.example.com/lab', html)
        for absent in ('demo-rta', 's001', '<script', '<style', 'localStorage', 'first-login', 'fe2'):
            self.assertNotIn(absent, html)
        self.assertIn('method="post"', html)
        self.assertIn('rel="noopener"', html)

    def test_assets_are_a_fixed_map(self):
        for name in ('../src/copy.json', 'src/copy.json', 'portal.py', '/logo.svg'):
            with self.subTest(name=name), self.assertRaises(KeyError):
                ui.asset(name)
        css, mime = ui.asset('ui.css')
        self.assertEqual(mime, 'text/css')
        self.assertIn(b"url('manrope.ttf')", css)

    def test_runtime_archive_has_no_course_or_operator_content_and_is_deterministic(self):
        spec = importlib.util.spec_from_file_location('ui_package_test', ROOT / 'package.py')
        package = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(package)
        for offline in (False, True):
            blob = package.archive(offline=offline)
            self.assertEqual(hashlib.sha256(blob).digest(), hashlib.sha256(package.archive(offline=offline)).digest())
            with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
                names = archive.getnames()
                self.assertIn('doris_workshop_ui.py', names)
                self.assertEqual('assets/manrope.ttf' in names, offline)
                self.assertFalse(any('course.md' in name or 'prototype' in name or 'review/' in name or 'plan-doc' in name for name in names))

    def test_packaging_rejects_a_symlinked_source_directory_outside_ui(self):
        spec = importlib.util.spec_from_file_location('ui_package_symlink_test', ROOT / 'package.py')
        package = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(package)
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source, outside = base / 'ui', base / 'outside'
            source.mkdir()
            outside.mkdir()
            (outside / 'page.html').write_text('outside source')
            (source / 'templates').symlink_to(outside, target_is_directory=True)
            with mock.patch.object(package, 'ROOT', source), mock.patch.object(package, 'FILES', ('templates/page.html',)):
                with self.assertRaises(ValueError):
                    package.archive()


if __name__ == '__main__':
    unittest.main()
