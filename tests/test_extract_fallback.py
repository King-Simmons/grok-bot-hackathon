"""A trafilatura failure should fall back to the stdlib extractor."""
import importlib.util, sys, types, unittest

_HAS_TRAFILATURA = importlib.util.find_spec("trafilatura") is not None

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
    def setUp(self):
        self._real_trafilatura = sys.modules.get("trafilatura")

    def tearDown(self):
        if self._real_trafilatura is None:
            sys.modules.pop("trafilatura", None)
        else:
            sys.modules["trafilatura"] = self._real_trafilatura

    @unittest.skipUnless(_HAS_TRAFILATURA, "trafilatura is not installed")
    def test_teardown_restores_the_real_module(self):
        import trafilatura
        real = sys.modules["trafilatura"]
        self._real_trafilatura = real
        sys.modules["trafilatura"] = _Fake("trafilatura", lambda *_a, **_k: "", lambda *_a, **_k: None)
        self.tearDown()
        self.assertIs(sys.modules.get("trafilatura"), real)
        self.setUp()

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

    def test_metadata_error_keeps_the_extracted_text(self):
        def boom(*_a, **_k):
            raise RuntimeError("metadata failed")

        sys.modules["trafilatura"] = _Fake("trafilatura", lambda *_a, **_k: "kept " * 50, boom)
        page = extract("<html><head><title>Page Title</title></head></html>", "https://example.com/x")
        self.assertEqual(page["method"], "trafilatura")
        self.assertGreaterEqual(len(page["text"].split()), 40)
        self.assertIn("kept", page["text"])
        self.assertIn("Page Title", page["title"])


if __name__ == "__main__":
    unittest.main()
