"""In-process engine server for offline route tests.

New cases live in tests/test_*.py. Subclass ServerCase, or copy the
request helper. scripts/test_all.sh discovers this package.
"""
import http.client, json, threading, unittest
from http.server import ThreadingHTTPServer

from engine.server import Handler


class ServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.httpd.daemon_threads = True
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            raw = resp.read()
            parsed = None
            if raw:
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = None
            return resp.status, resp.getheader("Location"), raw, parsed
        finally:
            conn.close()

    def post_json(self, path, obj, headers=None):
        extra = {"Content-Type": "application/json"}
        if headers:
            extra.update(headers)
        return self.request(method="POST", path=path, body=json.dumps(obj).encode(), headers=extra)
