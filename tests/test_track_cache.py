"""A bad sidecar is a cache miss for URL tracks and for topic tracks."""
import os, shutil, tempfile, unittest
from unittest.mock import patch

_root = tempfile.mkdtemp(prefix="lofi-cache-")
os.environ["LOFI_STATUS_DIR"] = os.path.join(_root, "status")
os.environ["LOFI_DATA_DIR"] = os.path.join(_root, "data")
os.makedirs(os.environ["LOFI_STATUS_DIR"], exist_ok=True)
os.makedirs(os.environ["LOFI_DATA_DIR"], exist_ok=True)

from engine import pipeline  # noqa: E402

TEXT = "Lo fi nights in the city. Rain on the window. Keep the volume low tonight."


def _fake_render(attrs, seed=0, out_mp3=None, target_s=38.0, mood_hint=None):
    os.makedirs(os.path.dirname(out_mp3), exist_ok=True)
    with open(out_mp3, "wb") as handle:
        handle.write(b"ID3fake")
    return {"duration_s": 1.0, "render_s": 0.0, "encode_s": 0.0}


def _fake_page(url):
    return {
        "title": "Repro", "text": TEXT, "url": url, "requested_url": url,
        "words": 12, "method": "test", "truncated": False, "description": "",
    }


class TrackCacheTest(unittest.TestCase):
    def tearDown(self):
        tracks = os.path.join(pipeline.ROOT, "tracks")
        if os.path.isdir(tracks):
            for name in os.listdir(tracks):
                if name.startswith(("text-", "url-")) and "repro" in name:
                    os.remove(os.path.join(tracks, name))

    def test_topic_track_with_a_bad_sidecar_is_rendered_again(self):
        with patch("engine.synth.render", _fake_render):
            first = pipeline.synthesize_text(
                "repro topic sidecar", TEXT, "https://example.com/repro-topic",
                kind="text", report_status=False)
            side = first["track"]["path"][:-4] + ".json"
            with open(side, "w", encoding="utf-8") as handle:
                handle.write("{not json")
            second = pipeline.synthesize_text(
                "repro topic sidecar", TEXT, "https://example.com/repro-topic",
                kind="text", report_status=False)
        self.assertFalse(second["timing"]["track_cached"])
        with open(side, encoding="utf-8") as handle:
            self.assertIn("title", handle.read())

    def test_url_track_with_a_bad_sidecar_is_rendered_again(self):
        url = "https://example.com/repro-url-sidecar"
        with patch("engine.synth.render", _fake_render), patch("engine.pipeline.ingest", _fake_page):
            first = pipeline.synthesize(url, report_status=False)
            side = first["track"]["path"][:-4] + ".json"
            with open(side, "w", encoding="utf-8") as handle:
                handle.write("{not json")
            second = pipeline.synthesize(url, report_status=False)
        self.assertFalse(second["timing"]["track_cached"])

    def test_mood_cache_drops_urls_it_cannot_keep(self):
        calls = []

        def fake(url):
            calls.append(url)
            return _fake_page(url)

        pipeline._mood_cache.clear()
        try:
            with patch("engine.pipeline.ingest", fake):
                for i in range(300):
                    pipeline.mood_for(f"https://cache.example/{i}")
                pipeline.mood_for("https://cache.example/0")
                pipeline.mood_for("https://cache.example/299")
        finally:
            pipeline._mood_cache.clear()
        self.assertGreaterEqual(calls.count("https://cache.example/0"), 2)
        self.assertEqual(calls.count("https://cache.example/299"), 1)


if __name__ == "__main__":
    unittest.main()
