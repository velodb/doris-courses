"""Real Jupyter form/XSRF boundaries, durable saves and environment transfer."""
import http.cookiejar
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request

from progress_fixture import ProgressFixture, ROOT


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ProgressHTTPTests(unittest.TestCase):
    def test_authenticated_mutations_migration_failure_and_restart(self):
        with tempfile.TemporaryDirectory(prefix='workshop-progress-http-') as temporary:
            fixture = ProgressFixture(Path(temporary) / 'a')
            self.addCleanup(fixture.stop)
            fixture.start()
            cookies = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(cookies), NoRedirect())
            def request(path, fields=None, *, auth=True, xsrf=True, origin=None, raw=None, mime=None):
                headers = {'Authorization': 'token ' + fixture.credentials()['token']} if auth else {}
                if origin:
                    headers['Origin'] = origin
                if fields is not None:
                    if xsrf:
                        fields = {**fields, '_xsrf': next(c.value for c in cookies if c.name == '_xsrf')}
                    raw = urllib.parse.urlencode(fields).encode()
                    headers['Content-Type'] = 'application/x-www-form-urlencoded'
                if mime:
                    headers['Content-Type'] = mime
                req = urllib.request.Request(fixture.base + path, data=raw, headers=headers)
                try:
                    selected_opener = opener if auth else urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
                    with selected_opener.open(req, timeout=10) as response:
                        return response.status, response.read(), response.headers
                except urllib.error.HTTPError as error:
                    return error.code, error.read(), error.headers
            outline = '/workshop/'
            mark = '/workshop/progress/real-time-analytics/module-1/mark'
            status, body, _ = request(outline)
            self.assertEqual(status, 200, body)
            self.assertIn(b'Course progress: 0.0%', body)
            self.assertNotEqual(request('/workshop/progress/export', auth=False)[0], 200)
            self.assertEqual(request(mark, {'field': 'course_read', 'value': 'true'}, xsrf=False)[0], 403)
            self.assertEqual(request(mark, {'field': 'course_read', 'value': 'true'}, origin='https://other.invalid')[0], 403)
            self.assertEqual(request(mark, {'field': 'lab_declared_complete', 'value': 'true'})[0], 400)
            self.assertEqual(request(mark, {'field': 'course_read', 'value': 'true'})[0], 303)
            self.assertEqual(request('/workshop/progress/real-time-analytics/module-1/lab', {})[0], 303)
            self.assertEqual(request(mark, {'field': 'lab_declared_complete', 'value': 'true'})[0], 303)
            self.assertEqual(request(mark.replace('/module-1/', '/module-999/'), {'field': 'course_read', 'value': 'true'})[0], 404)
            status, exported, headers = request('/workshop/progress/export')
            self.assertEqual(status, 200)
            self.assertIn('attachment', headers['Content-Disposition'])
            document = json.loads(exported)
            self.assertEqual(document['schema_version'], 2)
            self.assertEqual(document['attempts'], [])
            v1_status, v1_exported, v1_headers = request('/workshop/progress/export?version=1')
            self.assertEqual(v1_status, 200)
            self.assertEqual(json.loads(v1_exported)['schema_version'], 1)
            self.assertIn('omits attempt', v1_headers['Warning'])
            self.assertEqual(request('/workshop/progress/export?version=3')[0], 400)
            self.assertTrue(document['records'][0]['lab_declared_complete'])
            self.assertNotIn('token', exported.decode())
            # A database write failure must be visible and must not grant credit.
            with sqlite3.connect(fixture.db) as connection:
                connection.execute("CREATE TRIGGER fail_write BEFORE INSERT ON progress BEGIN SELECT RAISE(ABORT,'injected'); END")
            status, body, _ = request(mark, {'field': 'course_read', 'value': 'false'})
            self.assertEqual(status, 503)
            self.assertIn(b'Progress was not saved', body)
            self.assertTrue(json.loads(request('/workshop/progress/export')[1])['records'][0]['course_read'])
            with sqlite3.connect(fixture.db) as connection:
                connection.execute('DROP TRIGGER fail_write')
            # A Jupyter restart rotates credentials, while the disk record survives.
            old_token = fixture.credentials()['token']
            fixture.stop()
            fixture.start()
            self.assertNotEqual(old_token, fixture.credentials()['token'])
            self.assertEqual(json.loads(request('/workshop/progress/export')[1])['records'], document['records'])
            # A second environment starts empty and never reads A implicitly.
            fixture.stop()
            fixture = ProgressFixture(Path(temporary) / 'b')
            self.addCleanup(fixture.stop)
            fixture.start()
            cookies.clear()
            self.assertEqual(json.loads(request('/workshop/progress/export')[1])['records'], [])
            request(outline)
            boundary = 'workshop-progress-boundary'
            raw = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="../../other.json"\r\nContent-Type: application/json\r\n\r\n'.encode()
                   + exported + f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="_xsrf"\r\n\r\n'.encode()
                   + next(c.value for c in cookies if c.name == '_xsrf').encode() + f'\r\n--{boundary}--\r\n'.encode())
            status, body, _ = request('/workshop/progress/import', raw=raw, mime=f'multipart/form-data; boundary={boundary}')
            self.assertEqual(status, 200, body)
            self.assertIn(b'Confirm import', body)
            self.assertEqual(json.loads(request('/workshop/progress/export')[1])['records'], [])
            self.assertEqual(request('/workshop/progress/import', {'payload': exported.decode(), 'confirm': 'yes'})[0], 200)
            self.assertTrue(json.loads(request('/workshop/progress/export')[1])['records'][0]['course_read'])
            self.assertEqual(request('/workshop/progress/import', {'payload': '{"schema_version":2}', 'confirm': 'yes'})[0], 400)
            self.assertFalse((Path(temporary) / 'other.json').exists())
            fixture.stop()


if __name__ == '__main__':
    unittest.main()
