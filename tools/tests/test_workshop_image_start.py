"""A sealed image accepts dynamic ownership once and preserves learner files."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

TOOLS=Path(__file__).resolve().parents[1];sys.path.insert(0,str(TOOLS))
import workshop_config as wc
spec=importlib.util.spec_from_file_location('image_start',wc.SPEC/'runtime/start_image.py')
start=importlib.util.module_from_spec(spec);spec.loader.exec_module(start)


class ImageStartTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        config=self.root/'etc/doris-workshop';config.mkdir(parents=True)
        (self.root/'var/lib/doris-workshop').mkdir(parents=True)
        browser=self.root/'opt/doris-courses/doris-course/common/doris_labkit/browser.py';browser.parent.mkdir(parents=True)
        shutil.copyfile(wc.REPO/'doris-course/common/doris_labkit/browser.py',browser)
        (config/'image-sealed').touch()
        runtime={'course_ref':'a'*40};(config/'runtime.json').write_text(json.dumps(runtime))
        self.runtime_hash=start.digest(config/'runtime.json')
        (config/'image-manifest.json').write_text(json.dumps({'course_ref':'a'*40,'runtime_sha256':self.runtime_hash}))
        self.manifest_hash=start.digest(config/'image-manifest.json')
        self.activity={'schema_version':1,'event_id':'new-event','learner_id':'s002','release_ref':'rta-v1',
                       'expires_at':'2030-01-01T00:00:00Z','ports':{'8030':'http://127.0.0.1:49000'}}

    def initialize(self,**kwargs):
        return start.initialize(self.activity,self.manifest_hash,self.runtime_hash,'a'*40,root=self.root,**kwargs)

    def test_new_identity_and_dynamic_browser_routes(self):
        report=self.initialize();self.assertTrue(report['source_had_no_activity'])
        config=self.root/'etc/doris-workshop'
        routes=json.loads((config/'browser-urls.json').read_text())
        self.assertEqual(routes['learner_id'],'s002');self.assertEqual(routes['ports'],self.activity['ports'])
        self.initialize(check=True)

    def test_rerun_preserves_exercises_but_refuses_ownership_changes(self):
        self.initialize();sentinel=self.root/'var/lib/doris-workshop/learner-work.txt';sentinel.write_text('saved exercise')
        self.initialize();self.assertEqual(sentinel.read_text(),'saved exercise')
        self.activity['learner_id']='s001'
        with self.assertRaisesRegex(ValueError,'cannot change ownership'):self.initialize()

    def test_baked_activity_and_wrong_versions_are_rejected(self):
        (self.root/'etc/doris-workshop/activity.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'baked'):self.initialize()
        with self.assertRaisesRegex(ValueError,'checksum'):
            start.initialize(self.activity,'0'*64,self.runtime_hash,'a'*40,root=self.root)

    def test_credentials_in_browser_url_are_rejected(self):
        self.activity['ports']={'8030':'https://user:secret@example.com'}
        with self.assertRaises(ValueError):self.initialize()

    def test_check_does_not_initialize_an_unprepared_host(self):
        with self.assertRaisesRegex(ValueError,'has not completed'):self.initialize(check=True)
        self.assertFalse((self.root/'etc/doris-workshop/activity.json').exists())


if __name__=='__main__':unittest.main()
