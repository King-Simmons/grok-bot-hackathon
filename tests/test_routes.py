"""Offline HTTP checks for health, jobs, usage, agents, and topic errors.

These do not fetch the network. A patched pipeline stands in for a render.
"""
import os, time
from unittest.mock import patch

from tests.server_case import ServerCase

MOOD = {
    "url": "https://example.com/offline",
    "page_summary": "A short page.",
    "sentiment": "calm, late-night",
    "lofi_prompt": "soft piano",
    "cover_art_prompt": "a desk lamp",
    "musical_attributes": {
        "bpm": 72, "key": "A minor", "mood_tags": ["calm"], "primary_instruments": ["piano"],
    },
}


def _fake_track(url):
    return {
        "mood": dict(MOOD, url=url),
        "track": {
            "file": "tracks/offline.mp3", "url": "/tracks/offline.mp3",
            "path": "/tmp/offline.mp3", "title": "Offline", "duration_s": 44.0,
        },
        "page": {"title": "Offline", "url": url, "words": 12, "method": "test"},
        "analysis": {},
        "timing": {"track_cached": True, "total_s": 0.01},
    }


class RouteTest(ServerCase):
    def test_health_and_unknown_api(self):
        status, _loc, _raw, body = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        status, _loc, _raw, body = self.request("GET", "/api/nope")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "unknown endpoint")

    def test_missing_job_is_404(self):
        status, _loc, _raw, body = self.request("GET", "/api/jobs/not-a-real-job")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "no such job")

    def test_usage_reads_when_the_log_is_empty(self):
        status, _loc, _raw, body = self.request("GET", "/api/usage")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["hours"]), 24)
        self.assertIn("calls", body["all_time"])
        self.assertIn("calls", body["today"])

    def test_topics_catalog_defaults_to_web(self):
        status, _loc, _raw, body = self.request("GET", "/api/topics")
        self.assertEqual(status, 200)
        self.assertEqual(body["default_source"], "web")
        self.assertIn("web", body["sources"])
        self.assertIn("x", body["sources"])
        self.assertTrue(body["topics"])

    def test_topic_errors_stay_typed(self):
        os.environ.pop("X_BEARER_TOKEN", None)
        status, _loc, _raw, body = self.post_json("/api/synthesize_topic", {"topic": "???"})
        self.assertEqual(status, 400)
        self.assertEqual(body["error_kind"], "empty_topic")
        status, _loc, _raw, body = self.post_json(
            "/api/synthesize_topic", {"topic": "lofi", "source": "fm"})
        self.assertEqual(status, 400)
        self.assertEqual(body["error_kind"], "bad_source")
        status, _loc, _raw, body = self.post_json(
            "/api/synthesize_topic", {"topic": "zzz-not-a-cached-topic", "source": "x"})
        self.assertEqual(status, 404)
        self.assertEqual(body["error_kind"], "topic_not_found")
        status, _loc, _raw, body = self.post_json(
            "/api/synthesize_topic", {"topic": "東京", "source": "x"})
        self.assertEqual(status, 404)
        self.assertEqual(body["error_kind"], "topic_not_found")
        status, _loc, _raw, body = self.post_json(
            "/api/synthesize_topic", {"topic": "lofi", "posts": "nope", "source": "x"})
        self.assertEqual(status, 400)
        self.assertIn("posts", body["error"])

    def test_new_agent_needs_owner_and_task(self):
        status, _loc, _raw, body = self.post_json(
            "/api/agents", {"id": "route-test-bot", "state": "idle"})
        self.assertEqual(status, 400)
        self.assertIn("owner", body["error"])

    def test_async_job_reaches_done_without_a_render(self):
        def fake(url, force=False, progress=None, vocals="none", script=None, agent="anonymous"):
            if progress:
                progress("rendering", 90)
            return _fake_track(url)

        with patch("engine.pipeline.synthesize", fake):
            status, _loc, _raw, body = self.post_json(
                "/api/synthesize", {"url": "https://example.com/offline", "async": True})
        self.assertEqual(status, 202)
        self.assertTrue(body["job_id"])
        seen = None
        for _ in range(20):
            status, _loc, _raw, seen = self.request("GET", "/api/jobs/" + body["job_id"])
            if seen and seen.get("state") in ("done", "error"):
                break
            time.sleep(0.05)
        self.assertEqual(status, 200)
        self.assertEqual(seen["state"], "done")
        self.assertEqual(seen["result"]["track"]["url"], "/tracks/offline.mp3")
