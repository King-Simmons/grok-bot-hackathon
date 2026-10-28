"""Concurrent synth jobs stay bounded, and finished jobs do not pile up."""
import os, tempfile, threading, time, unittest
from http.server import ThreadingHTTPServer
import http.client, json

_root = tempfile.mkdtemp(prefix="lofi-jobs-")
os.environ["LOFI_STATUS_DIR"] = os.path.join(_root, "status")
os.environ["LOFI_DATA_DIR"] = os.path.join(_root, "data")
os.makedirs(os.environ["LOFI_STATUS_DIR"], exist_ok=True)
os.makedirs(os.environ["LOFI_DATA_DIR"], exist_ok=True)

from engine.server import Handler  # noqa: E402


def _result(url):
    return {
        "mood": {
            "url": url, "page_summary": "s", "sentiment": "calm",
            "lofi_prompt": "p", "cover_art_prompt": "c",
            "musical_attributes": {
                "bpm": 72, "key": "A minor", "mood_tags": ["calm"], "primary_instruments": ["piano"],
            },
        },
        "track": {
            "file": "tracks/offline.mp3", "url": "/tracks/offline.mp3",
            "path": "/tmp/offline.mp3", "title": "Offline", "duration_s": 44.0,
        },
        "page": {"title": "Offline", "url": url},
        "analysis": {},
        "timing": {"track_cached": True, "total_s": 0.01},
    }


class JobLimitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.httpd.daemon_threads = True
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def post(self, path, obj):
        raw = json.dumps(obj).encode()
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("POST", path, body=raw, headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            body = json.loads(resp.read().decode())
            return resp.status, body
        finally:
            conn.close()

    def get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            body = json.loads(resp.read().decode())
            return resp.status, body
        finally:
            conn.close()

    def test_fifth_concurrent_synth_is_rejected(self):
        from unittest.mock import patch
        release = threading.Event()
        entered = []
        finished = []

        def fake(url, force=False, progress=None, vocals="none", script=None, agent="anonymous"):
            entered.append(url)
            release.wait(3)
            finished.append(url)
            return _result(url)

        def post_one(i, sink):
            sink.append(self.post("/api/synthesize", {"url": f"https://example.com/job/{i}", "async": True}))

        with patch("engine.pipeline.synthesize", fake):
            sinks = []
            threads = []
            for i in range(4):
                sink = []
                sinks.append(sink)
                thread = threading.Thread(target=post_one, args=(i, sink))
                thread.start()
                threads.append(thread)
            for _ in range(50):
                if len(entered) >= 4:
                    break
                time.sleep(0.02)
            status, body = self.post("/api/synthesize", {"url": "https://example.com/job/overflow", "async": True})
            release.set()
            for thread in threads:
                thread.join(3)
            for _ in range(50):
                if len(finished) >= 4:
                    break
                time.sleep(0.02)
        self.assertEqual(len(finished), 4)
        self.assertEqual(status, 429, body)
        self.assertIn("rendering", body["error"])
        self.assertNotIn("job_id", body)
        accepted = [item[0][0] for item in sinks if item]
        self.assertEqual(accepted, [202, 202, 202, 202])

    def test_fifth_concurrent_sync_synth_is_rejected(self):
        from unittest.mock import patch
        release = threading.Event()
        entered = []
        sinks = []

        def fake(url, force=False, progress=None, vocals="none", script=None, agent="anonymous"):
            entered.append(url)
            release.wait(3)
            return _result(url)

        def post_one(i, sink):
            sink.append(self.post("/api/synthesize", {"url": f"https://example.com/sync/{i}"}))

        with patch("engine.pipeline.synthesize", fake):
            threads = []
            try:
                for i in range(4):
                    sink = []
                    sinks.append(sink)
                    thread = threading.Thread(target=post_one, args=(i, sink))
                    thread.start()
                    threads.append(thread)
                for _ in range(50):
                    if len(entered) >= 4:
                        break
                    time.sleep(0.02)
                overflow = []
                extra = threading.Thread(target=post_one, args=("overflow", overflow))
                extra.start()
                threads.append(extra)
                for _ in range(25):
                    if overflow:
                        break
                    time.sleep(0.02)
            finally:
                release.set()
            for thread in threads:
                thread.join(3)
        self.assertTrue(overflow, "sync overflow request did not return")
        status, body = overflow[0]
        self.assertEqual(status, 429, body)
        accepted = [item[0][0] for item in sinks if item]
        self.assertEqual(accepted, [200, 200, 200, 200])

    def test_done_means_the_slot_is_free(self):
        """A poll that sees done can start another track. The slot is already free."""
        from unittest.mock import patch
        gate = threading.Event()
        holding = []

        def fake(url, force=False, progress=None, vocals="none", script=None, agent="anonymous"):
            return _result(url)

        def slow_note(*_args, **_kwargs):
            holding.append(1)
            gate.wait(3)

        ids = []
        with patch("engine.pipeline.synthesize", fake), patch("engine.server.note_synthesize", slow_note):
            try:
                for i in range(4):
                    status, body = self.post(
                        "/api/synthesize", {"url": f"https://example.com/order/{i}", "async": True})
                    self.assertEqual(status, 202, body)
                    ids.append(body["job_id"])
                for _ in range(50):
                    if len(holding) >= 4:
                        break
                    time.sleep(0.02)
                self.assertEqual(len(holding), 4)
                for jid in ids:
                    _code, job = self.get("/api/jobs/" + jid)
                    self.assertEqual(job.get("state"), "running", job)
                status, body = self.post(
                    "/api/synthesize", {"url": "https://example.com/order/busy", "async": True})
                self.assertEqual(status, 429, body)
            finally:
                gate.set()
            for jid in ids:
                for _ in range(50):
                    _code, job = self.get("/api/jobs/" + jid)
                    if job.get("state") == "done":
                        break
                    time.sleep(0.01)
                else:
                    self.fail(f"job stayed {job.get('state')}")
            status, body = self.post(
                "/api/synthesize", {"url": "https://example.com/order/after", "async": True})
            self.assertEqual(status, 202, body)
            for _ in range(50):
                _code, job = self.get("/api/jobs/" + body["job_id"])
                if job.get("state") == "done":
                    break
                time.sleep(0.01)
            self.assertEqual(job.get("state"), "done", job)

    def test_finished_jobs_past_the_cap_are_forgotten(self):
        from unittest.mock import patch

        def fake(url, force=False, progress=None, vocals="none", script=None, agent="anonymous"):
            return _result(url)

        ids = []
        with patch("engine.pipeline.synthesize", fake):
            for i in range(70):
                status, body = self.post("/api/synthesize", {"url": f"https://example.com/keep/{i}", "async": True})
                self.assertEqual(status, 202, body)
                ids.append(body["job_id"])
                for _ in range(40):
                    code, job = self.get("/api/jobs/" + body["job_id"])
                    if job.get("state") == "done":
                        break
                    time.sleep(0.01)
                else:
                    self.fail(f"job {i} did not finish: {job}")
        code, _job = self.get("/api/jobs/" + ids[0])
        self.assertEqual(code, 404)
        code, job = self.get("/api/jobs/" + ids[-1])
        self.assertEqual(code, 200)
        self.assertEqual(job["state"], "done")


if __name__ == "__main__":
    unittest.main()
