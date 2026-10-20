"""Single-range static responses from Handler. Run: python3 -m unittest engine.test_range"""
import os, threading, unittest, urllib.error, urllib.request
from http.server import ThreadingHTTPServer

from engine.server import ROOT, Handler

SAMPLE = "/samples/beat-001.mp3"


def _h(headers, name):
    for k, v in headers.items():
        if k.lower() == name.lower():
            return v
    return ""


class RangeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def get(self, path, headers=None):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), headers=headers or {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def test_plain_200(self):
        code, hdrs, body = self.get(SAMPLE)
        self.assertEqual(code, 200)
        self.assertEqual(_h(hdrs, "Accept-Ranges"), "bytes")
        self.assertEqual(_h(hdrs, "Content-Range"), "")
        self.assertEqual(len(body), os.path.getsize(os.path.join(ROOT, SAMPLE.lstrip("/"))))

    def test_partial_and_unsatisfiable(self):
        disk = os.path.join(ROOT, SAMPLE.lstrip("/"))
        size = os.path.getsize(disk)
        with open(disk, "rb") as f:
            expect = f.read(100)
        code, hdrs, body = self.get(SAMPLE, {"Range": "bytes=0-99"})
        self.assertEqual(code, 206)
        self.assertEqual(_h(hdrs, "Content-Range"), "bytes 0-99/%d" % size)
        self.assertEqual(body, expect)
        code, hdrs, body = self.get(SAMPLE, {"Range": "bytes=10-19"})
        self.assertEqual(body, expect[10:20])
        self.assertEqual(_h(hdrs, "Content-Range"), "bytes 10-19/%d" % size)
        code, hdrs, body = self.get(SAMPLE, {"Range": "bytes=0-"})
        self.assertEqual(code, 206)
        self.assertEqual(len(body), size)
        self.assertEqual(_h(hdrs, "Content-Range"), "bytes 0-%d/%d" % (size - 1, size))
        code, hdrs, body = self.get(SAMPLE, {"Range": "bytes=-8"})
        self.assertEqual(code, 206)
        self.assertEqual(len(body), 8)
        self.assertTrue(_h(hdrs, "Content-Range").endswith("/%d" % size))
        code, hdrs, _ = self.get(SAMPLE, {"Range": "bytes=%d-%d" % (size, size + 5)})
        self.assertEqual(code, 416)
        self.assertEqual(_h(hdrs, "Content-Range"), "bytes */%d" % size)
        code, _, _ = self.get(SAMPLE, {"Range": "bytes=0-1,2-3"})
        self.assertEqual(code, 416)
        code, hdrs, body = self.get("/api/health", {"Range": "bytes=0-1"})
        self.assertEqual(code, 200)
        self.assertIn(b'"ok"', body)


if __name__ == "__main__":
    unittest.main()
