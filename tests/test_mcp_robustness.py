"""Offline MCP failure checks. A fake app server, no network, and no engine.

Run from the repo root:

    python3 -m unittest tests.test_mcp_robustness

The file is self-contained. A unittest discover of tests/ picks it up, which is
what the shared CI script looks for once that workflow is on main.
"""
import asyncio, importlib.util, json, os, socket, sys, threading, time, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_DIR = os.path.join(ROOT, "mcp")

MOOD = {
    "url": "https://example.com/story",
    "page_summary": "from-server",
    "sentiment": "calm reflective",
    "lofi_prompt": "soft piano",
    "musical_attributes": {
        "bpm": 72,
        "key": "D minor",
        "primary_instruments": ["piano"],
        "mood_tags": ["calm"],
    },
    "cover_art_prompt": "rain on a window",
}
TRACK = {
    "file": "tracks/from-server.mp3",
    "url": "/tracks/from-server.mp3",
    "path": "/tmp/from-server.mp3",
    "title": "From Server",
    "duration_s": 44,
}
WEB_TOPIC = {
    "topic": "lofi",
    "note": "from pages",
    "requested_source": "web",
    "source": "web",
    "pages": [{
        "title": "Lo-fi",
        "url": "https://example.com/story",
        "via": "Wikipedia",
        "method": "text",
        "words": 12,
    }],
    "page_count": 1,
}
X_TOPIC = {
    "topic": "SpaceX",
    "note": "mood from 3 posts",
    "requested_source": "x",
    "source": "x_topics.json",
    "post_count": 3,
    "x_search_url": "https://x.com/search?q=SpaceX",
    "x_reads": 0,
}


def _load_server():
    """Load the MCP module without letting ./mcp shadow the installed SDK."""
    mcp_dir = os.path.abspath(MCP_DIR)
    kept = []
    for entry in sys.path:
        if os.path.abspath(entry or os.getcwd()) == mcp_dir:
            continue
        kept.append(entry)
    sys.path[:] = kept
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    path = os.path.join(MCP_DIR, "lofi_mcp_server.py")
    spec = importlib.util.spec_from_file_location("lofi_mcp_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _closed_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class _State:
    def __init__(self, mode):
        self.mode = mode
        self.hits = 0
        self.release = threading.Event()
        self.seen = threading.Event()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def _raw(self, code, payload, content_type):
        if not isinstance(payload, bytes):
            payload = payload.encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, code, obj):
        self._raw(code, json.dumps(obj).encode(), "application/json")

    def _read(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            return self.rfile.read(n)
        return b""

    def _success(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/mood":
            body = dict(MOOD)
            body["_meta"] = {"page": {"title": "ignored"}}
            return body
        if path == "/api/synthesize":
            return {"mood": MOOD, "track": dict(TRACK), "timing": {"total_s": 1}, "vocals": {"state": "ready"}}
        if path == "/api/synthesize_topic":
            topic = dict(X_TOPIC) if self.server.state.mode == "x" else dict(WEB_TOPIC)
            return {"mood": MOOD, "track": dict(TRACK), "timing": {"total_s": 1}, "topic": topic}
        if path == "/api/usage":
            return {"all_time": {"calls": 7}, "today": {"calls": 1}, "hours": [0]}
        if path == "/api/agents" and self.command == "GET":
            return {"bots": [{"id": "from-server"}], "count": 1, "stale_after_s": 1800}
        if path == "/api/agents":
            return {"id": "ok-bot", "presence": "online", "state": "running", "task": "from-server", "owner": "King"}
        return {"ok": True}

    def _handle(self):
        self._read()
        state = self.server.state
        state.hits += 1
        path = self.path.split("?", 1)[0]
        mode = state.mode
        if mode == "timeout":
            state.seen.set()
            state.release.wait(5)
            self._json(200, {"ok": True})
            return
        if mode == "http_error":
            self._json(500, {"error": "boom"})
            return
        if mode == "http_html":
            self._raw(502, b"<html>down</html>", "text/html")
            return
        if mode == "bad_body":
            self._raw(200, b"not-json", "text/plain")
            return
        if mode in ("long", "lost", "job_error", "job_done"):
            if path.startswith("/api/jobs/"):
                if mode == "lost":
                    self._json(404, {"error": "no such job"})
                    return
                if mode == "job_error":
                    self._json(200, {"id": "job1", "state": "error", "error": "render failed"})
                    return
                if mode == "job_done":
                    self._json(200, {"id": "job1", "state": "done", "result": self._success()})
                    return
                self._json(200, {"id": "job1", "state": "running", "progress": 10})
                return
            self._json(202, {"job_id": "job1", "poll": "/api/jobs/job1"})
            return
        if mode == "topic_missing":
            self._json(404, {"error": 'No posts for "zzz".', "error_kind": "topic_not_found"})
            return
        self._json(200, self._success())

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()


class McpRobustnessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_server()

    def setUp(self):
        keys = (
            "LOFI_PUBLIC_BASE", "LOFI_MCP_TRANSPORT", "LOFI_MCP_HTTP_TIMEOUT",
            "LOFI_MCP_JOB_WAIT", "LOFI_MCP_POLL_INTERVAL",
        )
        self._saved = {key: os.environ.get(key) for key in keys}
        os.environ["LOFI_MCP_TRANSPORT"] = "http"
        os.environ["LOFI_MCP_HTTP_TIMEOUT"] = "0.4"
        os.environ["LOFI_MCP_JOB_WAIT"] = "0.45"
        os.environ["LOFI_MCP_POLL_INTERVAL"] = "0.05"
        os.environ["LOFI_PUBLIC_BASE"] = "http://127.0.0.1:%d" % _closed_port()

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _serve(self, mode):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        httpd.state = _State(mode)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.state.release.set)
        self.addCleanup(httpd.shutdown)
        self.addCleanup(httpd.server_close)
        base = "http://127.0.0.1:%d" % httpd.server_address[1]
        os.environ["LOFI_PUBLIC_BASE"] = base
        return httpd.state

    def _run(self, coro):
        return asyncio.run(coro)

    def _call(self, name, bad=False):
        mod = self.mod
        if name == "synthesize_url":
            if bad:
                return self._run(mod.synthesize_url("   "))
            return self._run(mod.synthesize_url("https://example.com/story"))
        if name == "get_mood_analysis":
            if bad:
                return self._run(mod.get_mood_analysis(""))
            return self._run(mod.get_mood_analysis("https://example.com/story"))
        if name == "synthesize_topic":
            if bad:
                return self._run(mod.synthesize_topic("lofi", posts="nope"))
            return self._run(mod.synthesize_topic("lofi"))
        if name == "report_status":
            if bad:
                return self._run(mod.report_status(id="Bad Id", state="running", task="Work"))
            return self._run(mod.report_status(id="ok-bot", state="running", task="Checking", owner="King"))
        if name == "list_agents":
            if bad:
                os.environ["LOFI_PUBLIC_BASE"] = "ftp://not-a-server"
            return self._run(mod.list_agents())
        if name == "get_usage":
            if bad:
                os.environ["LOFI_PUBLIC_BASE"] = ""
            return self._run(mod.get_usage())
        raise AssertionError(name)

    def _assert_error(self, result, kind):
        self.assertIsInstance(result, dict)
        self.assertIs(result.get("ok"), False)
        self.assertEqual(result.get("error_kind"), kind)
        self.assertTrue(isinstance(result.get("error"), str) and result["error"].strip())

    def test_bad_args_do_not_call_the_server(self):
        names = (
            "synthesize_url", "get_mood_analysis", "synthesize_topic",
            "report_status", "list_agents", "get_usage",
        )
        for name in names:
            with self.subTest(tool=name):
                state = self._serve("ok")
                result = self._call(name, bad=True)
                self._assert_error(result, "bad_args")
                self.assertEqual(state.hits, 0)

    def test_topic_validation_is_a_tool_error(self):
        """Empty topics and unknown sources stay raisable messages."""
        from mcp.server.mcpserver.exceptions import ToolError
        state = self._serve("ok")
        with self.assertRaises(ToolError) as empty:
            self._run(self.mod.synthesize_topic("   "))
        self.assertIn("topic", str(empty.exception).lower())
        with self.assertRaises(ToolError) as bad:
            self._run(self.mod.synthesize_topic("lofi", source="fm"))
        self.assertIn("web", str(bad.exception).lower())
        self.assertEqual(state.hits, 0)

    def test_server_down(self):
        os.environ["LOFI_PUBLIC_BASE"] = "http://127.0.0.1:%d" % _closed_port()
        for name in ("synthesize_url", "get_mood_analysis", "synthesize_topic", "report_status", "list_agents", "get_usage"):
            with self.subTest(tool=name):
                result = self._call(name)
                self._assert_error(result, "server_down")

    def test_timeout(self):
        for name in ("synthesize_url", "get_mood_analysis", "synthesize_topic", "report_status", "list_agents", "get_usage"):
            with self.subTest(tool=name):
                state = self._serve("timeout")
                t0 = time.monotonic()
                result = self._call(name)
                elapsed = time.monotonic() - t0
                self._assert_error(result, "timeout")
                self.assertLess(elapsed, 3.0)
                self.assertTrue(state.seen.is_set())

    def test_http_error(self):
        for name in ("synthesize_url", "get_mood_analysis", "synthesize_topic", "report_status", "list_agents", "get_usage"):
            with self.subTest(tool=name):
                self._serve("http_error")
                result = self._call(name)
                self._assert_error(result, "http_error")
                self.assertEqual(result.get("http_status"), 500)
                self.assertIn("boom", result["error"])

    def test_http_error_body_is_not_json(self):
        for name in ("synthesize_url", "get_mood_analysis", "synthesize_topic", "report_status", "list_agents", "get_usage"):
            with self.subTest(tool=name):
                self._serve("http_html")
                result = self._call(name)
                self._assert_error(result, "http_error")
                self.assertEqual(result.get("http_status"), 502)

    def test_success_body_is_not_json(self):
        for name in ("synthesize_url", "get_mood_analysis", "synthesize_topic", "report_status", "list_agents", "get_usage"):
            with self.subTest(tool=name):
                self._serve("bad_body")
                result = self._call(name)
                self._assert_error(result, "bad_response")

    def test_long_job_returns_still_running(self):
        for name in ("synthesize_url", "get_mood_analysis", "synthesize_topic", "report_status", "list_agents", "get_usage"):
            with self.subTest(tool=name):
                t0 = time.monotonic()
                self._serve("long")
                result = self._call(name)
                elapsed = time.monotonic() - t0
                self._assert_error(result, "still_running")
                self.assertEqual(result.get("status"), "still_running")
                self.assertEqual(result.get("job_id"), "job1")
                self.assertLess(elapsed, 3.0)

    def test_job_disappears(self):
        for name in ("synthesize_url", "get_mood_analysis", "synthesize_topic", "report_status", "list_agents", "get_usage"):
            with self.subTest(tool=name):
                self._serve("lost")
                result = self._call(name)
                self._assert_error(result, "job_lost")
                self.assertEqual(result.get("job_id"), "job1")

    def test_job_fails(self):
        for name in ("synthesize_url", "get_mood_analysis", "synthesize_topic", "report_status", "list_agents", "get_usage"):
            with self.subTest(tool=name):
                self._serve("job_error")
                result = self._call(name)
                self._assert_error(result, "job_error")
                self.assertIn("render failed", result["error"])
                self.assertEqual(result.get("job_id"), "job1")

    def test_finished_job_is_the_tool_result(self):
        self._serve("job_done")
        result = self._call("synthesize_url")
        self.assertEqual(result["sentiment"], "calm reflective")
        self.assertEqual(result["track_path"], "/tmp/from-server.mp3")
        self.assertEqual(result["track_url"], os.environ["LOFI_PUBLIC_BASE"] + "/tracks/from-server.mp3")
        self.assertNotIn("error_kind", result)

        self._serve("job_done")
        mood = self._call("get_mood_analysis")
        self.assertEqual(mood["page_summary"], "from-server")
        self.assertNotIn("_meta", mood)

        self._serve("job_done")
        topic = self._call("synthesize_topic")
        self.assertEqual(topic["topic"], "lofi")
        self.assertEqual(topic["source"], "web")
        self.assertEqual(topic["page_count"], 1)
        self.assertEqual(topic["pages"][0]["via"], "Wikipedia")

        self._serve("job_done")
        agents = self._call("list_agents")
        self.assertEqual(agents["bots"][0]["id"], "from-server")

        self._serve("job_done")
        usage = self._call("get_usage")
        self.assertEqual(usage["all_time"]["calls"], 7)

        self._serve("job_done")
        rec = self._call("report_status")
        self.assertEqual(rec["id"], "ok-bot")
        self.assertEqual(rec["task"], "from-server")

    def test_direct_success_shape(self):
        base_state = self._serve("ok")
        result = self._call("synthesize_url")
        self.assertEqual(result["musical_attributes"]["bpm"], 72)
        self.assertEqual(result["track_file"], "tracks/from-server.mp3")
        self.assertEqual(result["vocals"]["state"], "ready")
        self.assertTrue(result["track_url"].startswith(os.environ["LOFI_PUBLIC_BASE"]))
        self.assertGreaterEqual(base_state.hits, 1)

        self._serve("ok")
        mood = self._call("get_mood_analysis")
        self.assertEqual(mood["sentiment"], "calm reflective")
        self.assertNotIn("_meta", mood)

        self._serve("x")
        topic = self._run(self.mod.synthesize_topic("SpaceX", source="x"))
        self.assertEqual(topic["what_people_are_saying"], "from-server")
        self.assertEqual(topic["post_count"], 3)
        self.assertEqual(topic["post_source"], "x_topics.json")
        self.assertEqual(topic["x_search_url"], "https://x.com/search?q=SpaceX")
        self.assertEqual(topic["source"], "x")

    def test_unknown_topic_is_a_tool_error(self):
        from mcp.server.mcpserver.exceptions import ToolError
        self._serve("topic_missing")
        with self.assertRaises(ToolError) as caught:
            self._call("synthesize_topic")
        self.assertIn("posts", str(caught.exception).lower())

    def test_auto_uses_the_app_server_when_it_is_up(self):
        os.environ["LOFI_MCP_TRANSPORT"] = "auto"
        self._serve("ok")
        result = self._call("get_usage")
        self.assertEqual(result["all_time"]["calls"], 7)
        self.assertNotIn("error_kind", result)

    def test_bad_timeout_setting_does_not_crash(self):
        os.environ["LOFI_MCP_HTTP_TIMEOUT"] = "nope"
        self._serve("http_error")
        result = self._call("get_usage")
        self._assert_error(result, "http_error")


if __name__ == "__main__":
    unittest.main()
