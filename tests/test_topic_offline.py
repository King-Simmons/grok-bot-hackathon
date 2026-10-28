"""Offline pieces of scripts/smoke_topic.py.

The full script still hits the network for a web topic. This module does not.
"""
import os, unittest

from engine import pipeline
from engine.topic_web import BadSource, Cand, EmptyTopic, gather_pages, normalize_source, normalize_topic


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
