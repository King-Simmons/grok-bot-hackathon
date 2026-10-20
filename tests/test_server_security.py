"""Security checks for the engine HTTP server. Stdlib only."""
import http.client, json, os, sys, threading, unittest
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.server import Handler, request_gate  # noqa: E402


class ServerSecurityTest(unittest.TestCase):
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
            return resp.status, resp.getheader("Location"), resp.read()
        finally:
            conn.close()

    def test_root_redirects_to_dashboard(self):
        status, location, body = self.request("GET", "/")
        self.assertEqual(status, 302)
        self.assertEqual(location, "/dashboard/")
        self.assertNotIn(b"Directory listing", body)
        self.assertNotIn(b".git", body)

    def test_dotfiles_and_repo_files_are_blocked(self):
        for path in ("/.git/config", "/engine/server.py", "/.env", "/dashboard/.env",
                     "/dashboard/../.git/config", "/%2e%2e/%2e%2e/.git/config", "/%2egit/config"):
            status, _loc, body = self.request("GET", path)
            self.assertIn(status, (403, 404), path)
            self.assertNotIn(b"[core]", body, path)
            self.assertNotIn(b"repositoryformatversion", body, path)
            parsed = json.loads(body)
            self.assertEqual(parsed["error"], "forbidden", path)
        status, _loc, body = self.request("HEAD", "/.git/config")
        self.assertEqual(status, 403)
        self.assertNotIn(b"[core]", body)

    def test_dashboard_and_samples_still_served(self):
        status, _loc, body = self.request("GET", "/dashboard/")
        self.assertEqual(status, 200)
        self.assertIn(b"Lofi News Radio", body)
        status, _loc, body = self.request("GET", "/samples/beat-001.mp3")
        self.assertEqual(status, 200)
        self.assertGreater(len(body), 1000)
        status, _loc, body = self.request("HEAD", "/samples/beat-001.mp3")
        self.assertEqual(status, 200)
        status, _loc, body = self.request("GET", "/samples/")
        self.assertEqual(status, 404)
        self.assertNotIn(b"Directory listing", body)

    def test_non_object_json_returns_400(self):
        for raw in (b"[]", b'"str"', b"123", b"null"):
            status, _loc, body = self.request(
                "POST", "/api/synthesize", body=raw,
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(status, 400, raw)
            parsed = json.loads(body)
            self.assertIn("error", parsed, raw)
            self.assertIn("JSON object", parsed["error"], raw)

    def test_local_dashboard_post_still_works(self):
        origin = f"http://127.0.0.1:{self.port}"
        status, _loc, body = self.request(
            "POST", "/api/synthesize", body=b"{}",
            headers={"Content-Type": "application/json", "Origin": origin},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "missing url")
        status, _loc, body = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        status, _loc, body = self.request("GET", "/api/topics")
        self.assertEqual(status, 200)
        self.assertIn("topics", json.loads(body))
        status, _loc, body = self.request(
            "POST", "/api/synthesize_topic", body=b"{}",
            headers={"Content-Type": "application/json", "Origin": origin},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body).get("error_kind"), "empty_topic")

    def test_bad_origin_and_host_rejected(self):
        status, _loc, body = self.request(
            "POST", "/api/synthesize", body=b"{}",
            headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"], "forbidden")
        status, _loc, body = self.request(
            "GET", "/api/mood?url=https://example.com",
            headers={"Host": f"rebind.evil.example:{self.port}"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"], "forbidden")

    def test_public_base_and_unclear_host_are_allowed(self):
        os.environ["LOFI_PUBLIC_BASE"] = "https://radio.example.com"
        try:
            status, _loc, body = self.request(
                "GET", "/api/health",
                headers={"Host": "radio.example.com", "Origin": "https://radio.example.com"},
            )
            self.assertEqual(status, 200, body)
            status, _loc, body = self.request(
                "GET", "/api/health",
                headers={"Host": "not a host:::"},
            )
            self.assertEqual(status, 200, body)
        finally:
            os.environ.pop("LOFI_PUBLIC_BASE", None)

    def test_gate_allows_null_origin(self):
        decision, _why = request_gate("127.0.0.1:53343", "null", {"127.0.0.1", "localhost"})
        self.assertEqual(decision, "allow-log")
        decision, why = request_gate("evil.example", None, {"127.0.0.1", "localhost"})
        self.assertEqual(decision, "deny")
        self.assertEqual(why, "host")


if __name__ == "__main__":
    unittest.main()
