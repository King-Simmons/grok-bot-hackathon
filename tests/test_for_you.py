"""Offline For You checks. X and xAI are fakes. No audio is rendered."""
import json, os, tempfile, threading, time, unittest
from http.server import ThreadingHTTPServer

from engine import for_you
from engine.pipeline import TopicNotFound
from engine.server import Handler


def _render(topic, vocals, agent, progress):
    progress("rendering", 40)
    if topic == "missing pages":
        raise TopicNotFound('I couldn\'t find public pages about "missing pages".')
    return {
        "track": {"url": "/tracks/web-%s.mp3" % topic.split()[0].lower(), "file": "tracks/x.mp3",
                  "title": topic, "duration_s": 44.0},
        "timing": {"track_cached": False},
        "topic": {"x_reads": 0},
    }


class ForYouTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._env = {key: os.environ.get(key) for key in
                     ("X_CLIENT_ID", "X_CLIENT_SECRET", "XAI_API_KEY", "LOFI_X_HOME")}
        os.environ.pop("X_CLIENT_ID", None)
        os.environ.pop("X_CLIENT_SECRET", None)
        os.environ.pop("XAI_API_KEY", None)
        os.environ["LOFI_X_HOME"] = self.tmp.name
        for_you.reset_runtime()
        for_you.use_home(self.tmp.name)
        for_you.RENDER = _render
        for_you.DISCOVER = lambda topic: []
        self.calls = []

        def transport(method, url, headers, body):
            self.calls.append((method, url.split("?", 1)[0]))
            raise for_you.TransportError(599)

        for_you.TRANSPORT = transport

    def tearDown(self):
        for_you.reset_runtime()
        for_you.use_home(None)
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def test_typed_topics_skip_x_and_finish_when_one_item_fails(self):
        interests, note = for_you.rank(["City council", "Space telescopes"], None)
        self.assertEqual([row.topic for row in interests], ["City council", "Space telescopes"])
        self.assertIsNone(note)
        batch = for_you.Batch(topics=["City council", "missing pages"], summary=False, topics_given=True)
        view = batch.run(agent="test")
        self.assertEqual(view["state"], "done")
        self.assertEqual(view["connection"], "disconnected")
        self.assertIn("not connected", view["message"])
        self.assertEqual(view["result"]["made"], 1)
        self.assertEqual(view["result"]["failed"], 1)
        self.assertEqual(view["items"][0]["state"], "done")
        self.assertEqual(view["items"][0]["track_url"], "/tracks/web-city.mp3")
        self.assertEqual(view["items"][1]["state"], "failed")
        self.assertEqual(view["items"][1]["error_kind"], "topic_not_found")
        self.assertEqual(self.calls, [])
        self.assertFalse(view["summary"])

    def test_preview_does_not_render(self):
        called = []
        for_you.RENDER = lambda *args: called.append(args)
        view = for_you.preview(["City council"], topics_given=True)
        self.assertEqual(called, [])
        self.assertEqual(view["interests"][0]["topic"], "City council")
        self.assertEqual(view["connection"], "disconnected")

    def test_four_typed_topics_do_not_call_grok(self):
        called = []
        for_you.RANKER = lambda text: called.append(text) or '{"interests":[]}'
        topics = ["one", "two", "three", "four"]
        interests, note = for_you.rank(topics, {"hashtags": ("starship",), "keywords": (),
                                                "followed": (), "liked_accounts": ()})
        self.assertEqual([row.topic for row in interests], topics)
        self.assertEqual(called, [])
        self.assertIsNone(note)

    def test_grok_keeps_typed_topics_and_drops_inventions(self):
        for_you.RANKER = lambda text: json.dumps({"interests": [
            {"topic": "starship", "because": "hashtag starship"},
            {"topic": "totally new fad", "because": "no signal"},
        ]})
        signals = {"followed": (), "liked_accounts": (), "hashtags": ("starship",),
                   "keywords": (), "likes_read": 1, "following_read": 0}
        interests, note = for_you.rank(["City council"], signals)
        topics = [row.topic for row in interests]
        self.assertEqual(topics[0], "City council")
        self.assertIn("starship", [topic.lower() for topic in topics])
        self.assertFalse(any("fad" in topic.lower() for topic in topics))
        self.assertIsNone(note)

    def test_grok_failure_uses_the_heuristic(self):
        def boom(_text):
            raise RuntimeError("down")
        for_you.RANKER = boom
        signals = {"followed": ("SpaceX",), "liked_accounts": (), "hashtags": ("starship",),
                   "keywords": (("rocket", 3),), "likes_read": 2, "following_read": 1}
        interests, note = for_you.rank(["City council"], signals)
        self.assertEqual(interests[0].because, "you typed this")
        self.assertTrue(any(row.topic == "starship" for row in interests))
        self.assertIn("Grok was unavailable", note)

    def test_refused_likes_still_render_typed_topics(self):
        for_you._write_json("x-user.json", {
            "access_token": "present", "refresh_token": "present", "expires_at": time.time() + 3600,
            "username": "king", "user_id": "1", "link": "unprobed", "refused": [], "unreachable": False,
        })

        def transport(method, url, headers, body):
            self.calls.append(url.split("?", 1)[0])
            if url.endswith("/users/me?user.fields=username") or "/users/me?" in url:
                return 200, {"data": {"id": "1", "username": "king"}}
            if "liked_tweets" in url:
                raise for_you.TransportError(403)
            if "following" in url:
                return 200, {"data": [{"username": "SpaceX", "description": "rockets"}]}
            raise for_you.TransportError(500)

        for_you.TRANSPORT = transport
        view = for_you.Batch(topics=["City council"], summary=False, topics_given=True).run(agent="test")
        self.assertEqual(view["state"], "done")
        self.assertEqual(view["connection"], "degraded")
        self.assertIn("likes", view["message"])
        self.assertEqual(view["items"][0]["state"], "done")
        self.assertEqual(view["items"][0]["because"], "you typed this")
        status = for_you.public_status()
        self.assertNotIn("present", json.dumps(status))
        self.assertEqual(status["username"], "king")

    def test_broad_interest_uses_the_news_snapshot_title(self):
        path = os.path.join(self.tmp.name, "news.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"stories": [{
                "title": "AI coding agents rewrite the review",
                "topics": ["AI", "Technology"],
                "url": "https://x.com/i/trending/1",
            }]}, f)
        for_you.NEWS_PATH = path
        matched = for_you.match_interest(for_you.Interest("AI", "you typed this"))
        self.assertEqual(matched["talk_topic"], "AI coding agents rewrite the review")
        self.assertEqual(matched["story_origin"], "x_news")
        self.assertEqual(matched["story_url"], "https://x.com/i/trending/1")

    def test_login_state_is_single_use_and_loopback_only(self):
        os.environ["X_CLIENT_ID"] = "cid"
        os.environ["X_CLIENT_SECRET"] = "secret"
        url = for_you.start_login(53343)
        self.assertIn("code_challenge=", url)
        self.assertIn("code_challenge_method=S256", url)
        self.assertIn("redirect_uri=http%3A%2F%2F127.0.0.1%3A53343%2Fapi%2Ffor-you%2Fx%2Fcallback", url)
        self.assertIn("like.read", url)
        state = url.split("state=", 1)[1].split("&", 1)[0]
        with self.assertRaises(for_you.ForYouRejected) as raised:
            for_you.finish_login("code", state, "evil.example", ("127.0.0.1", 9))
        self.assertEqual(raised.exception.error_kind, "oauth_peer")
        self.assertIn(state, for_you._PENDING)
        with self.assertRaises(for_you.ForYouRejected) as raised:
            for_you.finish_login("code", state, "127.0.0.1:53343", ("8.8.8.8", 9))
        self.assertEqual(raised.exception.error_kind, "oauth_peer")

        def transport(method, url, headers, body):
            self.assertNotIn("secret", str(headers.get("User-Agent", "")))
            return 200, {"access_token": "aaa", "refresh_token": "bbb", "expires_in": 1000}

        for_you.TRANSPORT = transport
        for_you.finish_login("code", state, "127.0.0.1:53343", ("127.0.0.1", 9))
        status = for_you.public_status()
        self.assertEqual(status["connection"], "unprobed")
        self.assertNotIn("aaa", json.dumps(status))
        self.assertIn("X is connected", status["message"])
        with self.assertRaises(for_you.ForYouRejected) as raised:
            for_you.finish_login("code", state, "127.0.0.1:53343", ("127.0.0.1", 9))
        self.assertEqual(raised.exception.error_kind, "oauth_state")
        self.assertNotIn("code", str(raised.exception))

    def test_request_line_drops_the_callback_query(self):
        line = 'GET /api/for-you/x/callback?code=secret&state=abc HTTP/1.1'
        clean = for_you.redact_requestline(line)
        self.assertNotIn("secret", clean)
        self.assertNotIn("state", clean)
        self.assertIn("/api/for-you/x/callback", clean)
        self.assertEqual(for_you.redact_requestline('GET /api/health HTTP/1.1'), 'GET /api/health HTTP/1.1')


class ForYouHttpTest(unittest.TestCase):
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

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._env = {key: os.environ.get(key) for key in ("X_CLIENT_ID", "X_CLIENT_SECRET", "XAI_API_KEY")}
        os.environ.pop("X_CLIENT_ID", None)
        os.environ.pop("X_CLIENT_SECRET", None)
        os.environ.pop("XAI_API_KEY", None)
        for_you.reset_runtime()
        for_you.use_home(self.tmp.name)
        for_you.RENDER = _render
        for_you.DISCOVER = lambda topic: []
        for_you.TRANSPORT = lambda *args: (_ for _ in ()).throw(for_you.TransportError(599))

    def tearDown(self):
        for_you.reset_runtime()
        for_you.use_home(None)
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            return resp.status, resp.getheader("Location"), resp.read()
        finally:
            conn.close()

    def test_status_and_empty_batch(self):
        status, _loc, body = self.request("GET", "/api/for-you")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["connection"], "disconnected")
        self.assertIn("topics you type", payload["message"])
        status, _loc, body = self.request(
            "POST", "/api/for-you/batch", body=b'{"topics":[]}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error_kind"], "empty_batch")

    def test_batch_poll_and_join(self):
        gate = threading.Event()
        started = threading.Event()

        def slow(topic, vocals, agent, progress):
            started.set()
            self.assertTrue(gate.wait(2))
            return _render(topic, vocals, agent, progress)

        for_you.RENDER = slow
        status, _loc, body = self.request(
            "POST", "/api/for-you/batch",
            body=b'{"topics":["City council"],"summary":false}',
            headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1:%d" % self.port},
        )
        self.assertEqual(status, 202, body)
        first = json.loads(body)
        self.assertFalse(first["joined"])
        self.assertTrue(started.wait(2))
        status, _loc, body = self.request(
            "POST", "/api/for-you/batch",
            body=b'{"topics":["City council"],"summary":false}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 202)
        self.assertTrue(json.loads(body)["joined"])
        self.assertEqual(json.loads(body)["job_id"], first["job_id"])
        gate.set()
        deadline = time.time() + 3
        view = {}
        while time.time() < deadline:
            status, _loc, body = self.request("GET", first["poll"])
            view = json.loads(body)
            if view.get("state") == "done":
                break
            time.sleep(0.05)
        self.assertEqual(view["state"], "done")
        self.assertEqual(view["items"][0]["state"], "done")
        self.assertTrue(view["items"][0]["track_url"].startswith("/tracks/"))

    def test_vocals_field_is_rejected(self):
        status, _loc, body = self.request(
            "POST", "/api/for-you/batch",
            body=b'{"topics":["City council"],"vocals":"song"}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error_kind"], "bad_vocals")

    def test_connect_and_callback(self):
        status, _loc, body = self.request("GET", "/api/for-you/connect")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error_kind"], "oauth_unconfigured")
        os.environ["X_CLIENT_ID"] = "cid"
        os.environ["X_CLIENT_SECRET"] = "secret"
        status, location, body = self.request("GET", "/api/for-you/connect")
        self.assertEqual(status, 302)
        self.assertIn("twitter.com/i/oauth2/authorize", location)
        self.assertIn("127.0.0.1", location)
        self.assertNotIn("secret", location)
        status, location, body = self.request("GET", "/api/for-you/x/callback?code=abc&state=nope")
        self.assertEqual(status, 302)
        self.assertEqual(location, "/dashboard/#for-you")
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "x-user.json")))
        status, _loc, body = self.request(
            "GET", "/api/for-you/x/callback?code=abc&state=nope",
            headers={"Host": "evil.example"},
        )
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
