"""Offline pieces of scripts/smoke_topic.py.

The full script still hits the network for a web topic. This module does not.
"""
import json, os, tempfile, unittest

from engine import pipeline
from engine.server import topics_catalog
from engine.topic_web import BadSource, Cand, EmptyTopic, gather_pages, normalize_source, normalize_topic
from tests.server_case import ServerCase


def _fake_ingest(url):
    if "blocked.example" in url:
        return {
            "title": "Blocked Example Story", "url": url, "method": "url-words", "words": 4,
            "note": "The site refused the fetch, so this mood is from the words in the link.",
            "text": "blocked example story words",
        }
    return {
        "title": "Lo-fi music", "url": url, "method": "trafilatura", "words": 40,
        "text": " ".join(["lofi music"] * 20),
    }


class TopicOfflineTest(unittest.TestCase):
    def setUp(self):
        self._token = os.environ.pop("X_BEARER_TOKEN", None)

    def tearDown(self):
        if self._token is not None:
            os.environ["X_BEARER_TOKEN"] = self._token

    def test_gather_keeps_a_real_page_and_a_url_words_page(self):
        pages = gather_pages([
            Cand("https://en.wikipedia.org/wiki/Lofi", "Lo-fi music", "Wikipedia"),
            Cand("https://blocked.example/news/story", "Blocked Example Story", "News"),
        ], ingest_fn=_fake_ingest)
        methods = {p["method"] for p in pages}
        self.assertIn("trafilatura", methods)
        self.assertTrue(any(p.get("method") == "url-words" and p.get("note") for p in pages))

    def test_empty_topic_and_bad_source(self):
        for raw in ("", "   ", "???"):
            with self.assertRaises(EmptyTopic):
                normalize_topic(raw)
        with self.assertRaises(BadSource) as caught:
            normalize_source("fm")
        self.assertIn("web", str(caught.exception).lower())
        self.assertEqual(normalize_source(""), "web")
        self.assertEqual(normalize_source(None), "web")

    def test_x_cache_resolves_without_rendering(self):
        posts, source, _meta = pipeline.resolve_topic_posts("SpaceX")
        self.assertEqual(source, "x_topics.json")
        self.assertGreaterEqual(len(posts), 2)
        with self.assertRaises(pipeline.TopicNotFound):
            pipeline.resolve_topic_posts("zzz-not-a-cached-topic")

    def _with_topics(self, payload):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        previous = pipeline.TOPICS_FILE
        pipeline.TOPICS_FILE = path
        return path, previous

    def _restore_topics(self, path, previous):
        pipeline.TOPICS_FILE = previous
        os.remove(path)

    def test_a_list_file_is_an_empty_catalog(self):
        path, previous = self._with_topics(["not", "a", "catalog"])
        try:
            catalog = topics_catalog()
            self.assertEqual(catalog["topics"], [])
            self.assertEqual(pipeline.cached_topic("lofi"), None)
        finally:
            self._restore_topics(path, previous)

    def test_string_topic_entries_are_skipped(self):
        path, previous = self._with_topics({
            "topics": [
                "just a string",
                {"topic": "lofi", "aliases": ["chill"], "posts": ["soft rain tonight"]},
            ],
            "updated": "2026-01-01T00:00:00Z",
        })
        try:
            catalog = topics_catalog()
            self.assertEqual([row["topic"] for row in catalog["topics"]], ["lofi"])
            self.assertEqual(catalog["topics"][0]["post_count"], 1)
            self.assertNotIn("posts", catalog["topics"][0])
            self.assertEqual(catalog["updated"], "2026-01-01T00:00:00Z")
            self.assertEqual(pipeline.cached_topic("chill")["topic"], "lofi")
            self.assertEqual(pipeline.cached_topic("missing"), None)
        finally:
            self._restore_topics(path, previous)


class TopicsRouteTest(ServerCase):
    def test_get_topics_returns_an_empty_list_for_a_bad_file(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"topics": ["just a string"]}, f)
        previous = pipeline.TOPICS_FILE
        pipeline.TOPICS_FILE = path
        try:
            status, _loc, _raw, body = self.request("GET", "/api/topics")
        finally:
            pipeline.TOPICS_FILE = previous
            os.remove(path)
        self.assertEqual(status, 200)
        self.assertEqual(body["topics"], [])
