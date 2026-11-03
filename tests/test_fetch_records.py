"""News fetch scripts skip a record that is not an object.

One bad Hacker News item, or a saved X file that is not a catalog,
must not abort the run.
"""
import importlib.util
import os
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    path = os.path.join(ROOT, "scripts", name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FetchRecordTest(unittest.TestCase):
    def test_x_build_skips_records_that_are_not_objects(self):
        mod = _load("fetch_x_news")
        self.assertEqual(mod.build([], None, "", 5), [])
        self.assertEqual(mod.build({"data": "abc"}, None, "", 5), [])
        news = {
            "data": [
                "nope",
                {
                    "id": "n1",
                    "name": "A story",
                    "contexts": {"topics": ["Technology"]},
                    "cluster_posts_results": [5, {"post_id": "p1"}],
                },
            ]
        }
        posts = {"data": "abc", "includes": {"users": ["nope"]}}
        stories = mod.build(news, posts, "Technology", 5)
        self.assertEqual([s["title"] for s in stories], ["A story"])
        self.assertEqual(stories[0]["x_id"], "n1")
        self.assertNotIn("top_post", stories[0])

    def test_hn_fetch_skips_a_non_object_item(self):
        mod = _load("fetch_hn")
        story = {
            "id": 2,
            "type": "story",
            "title": "A real story",
            "url": "https://example.com/a",
            "score": 3,
            "by": "ada",
            "time": 0,
            "descendants": 1,
        }

        def fake_get(path, timeout=10):
            if path.endswith("stories.json"):
                return [1, 2]
            if path == "item/1.json":
                return 5
            if path == "item/2.json":
                return story
            raise AssertionError(path)

        with patch.object(mod, "get", side_effect=fake_get):
            heads = mod.fetch(1, "top")
        self.assertEqual([h["title"] for h in heads], ["A real story"])
        self.assertEqual(heads[0]["hn_id"], 2)
        self.assertEqual(heads[0]["url"], "https://example.com/a")
