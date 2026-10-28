"""A trafilatura failure should fall back to the stdlib extractor."""
import sys, types, unittest

from engine.ingest import extract

HTML = (
    "<html><head><title>Hello Title Here</title></head><body><p>"
    + ("alpha beta gamma delta " * 20)
    + "</p></body></html>"
)


class _Fake(types.ModuleType):
    def __init__(self, name, extract_fn, meta_fn):
        super().__init__(name)
        self.extract = extract_fn
        self.extract_metadata = meta_fn


class ExtractFallbackTest(unittest.TestCase):
    def tearDown(self):
        sys.modules.pop("trafilatura", None)

    def test_parser_error_uses_the_stdlib_text(self):
        def boom(*_a, **_k):
            raise RuntimeError("parser blew up")

        sys.modules["trafilatura"] = _Fake("trafilatura", boom, boom)
        page = extract(HTML, "https://example.com/x")
        self.assertIn("alpha", page["text"])
        self.assertIn("stdlib", page["method"])
        self.assertIn("Hello", page["title"])

    def test_trafilatura_text_is_kept_when_it_returns(self):
        meta = types.SimpleNamespace(title="From Meta")
        sys.modules["trafilatura"] = _Fake(
            "trafilatura",
            lambda *_a, **_k: "word " * 50,
            lambda *_a, **_k: meta,
        )
        page = extract("<html></html>", "https://example.com/x")
        self.assertEqual(page["method"], "trafilatura")
        self.assertEqual(page["title"], "From Meta")
        self.assertGreaterEqual(len(page["text"].split()), 40)


if __name__ == "__main__":
    unittest.main()
