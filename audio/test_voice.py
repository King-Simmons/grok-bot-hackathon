"""Smoke tests for the Chatterbox voice module. No model download.

    python3 -m unittest audio.test_voice
"""
import importlib, inspect, json, os, sys, unittest
from unittest import mock

# Importing the voice module must not pull torch or the model.
for _name in ("torch", "chatterbox", "chatterbox.tts", "audio.voice"):
    sys.modules.pop(_name, None)
import audio.voice as voice  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _has(name):
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


class ContractTests(unittest.TestCase):
    def test_import_does_not_load_the_model(self):
        self.assertNotIn("torch", sys.modules)
        self.assertNotIn("chatterbox", sys.modules)
        self.assertNotIn("chatterbox.tts", sys.modules)

    def test_signature_is_the_stable_contract(self):
        sig = inspect.signature(voice.synthesize_speech)
        self.assertEqual(list(sig.parameters), ["text", "exaggeration", "cfg_weight", "speed", "out_path", "lofi"])
        self.assertEqual(sig.parameters["exaggeration"].default, 0.5)
        self.assertEqual(sig.parameters["cfg_weight"].default, 0.5)
        self.assertEqual(sig.parameters["speed"].default, 1.0)
        self.assertIsNone(sig.parameters["out_path"].default)
        self.assertEqual(sig.parameters["lofi"].kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIs(sig.parameters["lofi"].default, False)

    def test_presets_match_the_listening_pass(self):
        self.assertEqual(voice.PRESETS["news"], {"exaggeration": 0.7, "cfg_weight": 0.3, "speed": 0.9})
        self.assertEqual(voice.PRESETS["dj"], {"exaggeration": 0.5, "cfg_weight": 0.5, "speed": 0.9})

    def test_resolve_controls_lets_explicit_numbers_win(self):
        self.assertEqual(voice.resolve_controls(), (0.5, 0.5, 1.0))
        self.assertEqual(voice.resolve_controls(preset="news"), (0.7, 0.3, 0.9))
        self.assertEqual(voice.resolve_controls(speed=1.0, preset="news"), (0.7, 0.3, 1.0))
        with self.assertRaises(ValueError):
            voice.resolve_controls(preset="opera")
        with self.assertRaises(ValueError):
            voice.resolve_controls(speed=4)

    def test_missing_package_raises_and_does_not_import_torch(self):
        sys.modules.pop("torch", None)
        with self.assertRaises(voice.VoiceUnavailable) as caught:
            voice.synthesize_speech("Hello from the news desk.")
        message = str(caught.exception)
        self.assertIn("requirements-voice", message)
        self.assertIn("3.11", message)
        self.assertIn("setuptools", message)
        self.assertNotIn("torch", sys.modules)
        self.assertNotIn("chatterbox", sys.modules)
        self.assertFalse(voice.available())

    def test_python_313_and_setuptools_81_are_blocked(self):
        with mock.patch.object(sys, "version_info", (3, 13, 1)):
            self.assertIn("3.13", voice._python_blocker())
        with mock.patch.object(sys, "version_info", (3, 11, 9)):
            self.assertIsNone(voice._python_blocker())
        for version, blocked in (("80.9.0", False), ("81.0.0", True), ("82.1", True)):
            fake = type("Setuptools", (), {"__version__": version})()
            with mock.patch.dict(sys.modules, {"setuptools": fake}):
                message = voice._setuptools_blocker()
            if blocked:
                self.assertIn("setuptools", message)
            else:
                self.assertIsNone(message)

    def test_split_text_rejoins_and_stays_short(self):
        short = "Good evening. You're listening to News Talk."
        self.assertEqual(voice.split_text(short), [short])
        long = " ".join(f"Sentence {i} is part of the briefing." for i in range(40))
        chunks = voice.split_text(long)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= voice.MAX_CHUNK for chunk in chunks))
        self.assertEqual(" ".join(chunks), long)
        with self.assertRaises(ValueError):
            voice.synthesize_speech("word " * 5000)

    def test_docs_name_the_contract(self):
        with open(os.path.join(ROOT, "docs", "VOICE.md"), encoding="utf-8") as handle:
            doc = handle.read()
        self.assertIn("synthesize_speech(text, exaggeration, cfg_weight, speed, out_path)", doc)
        self.assertIn("Python 3.11", doc)
        self.assertIn("setuptools", doc)
        self.assertIn("about **8 seconds**", doc)
        self.assertIn("about **2 seconds of compute per 1 second of audio**", doc)
        self.assertIn("PerTh", doc)
        self.assertIn("consent", doc.lower())
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as handle:
            readme = handle.read()
        self.assertIn("docs/VOICE.md", readme)

    def test_generated_audio_is_gitignored(self):
        with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("tracks/", text)
        self.assertIn("*.wav", text)
        self.assertIn("*.safetensors", text)
        path = voice._cache_path("hello", 0.5, 0.5, 1.0, False)
        self.assertTrue(path.endswith(".wav"))
        self.assertIn(os.sep + "tracks" + os.sep, path)


class DspTests(unittest.TestCase):
    def test_speed_one_does_not_call_ffmpeg(self):
        if not _has("numpy"):
            self.skipTest("numpy not installed")
        import numpy as np
        audio = np.linspace(-0.2, 0.2, 1600, dtype=np.float32)
        with mock.patch("subprocess.run", side_effect=AssertionError("ffmpeg should not run")):
            out, sr = voice._apply_speed(audio, 16000, 1.0)
        self.assertEqual(sr, 16000)
        self.assertEqual(len(out), len(audio))

    def test_speed_stretches_with_atempo(self):
        if not _has("numpy") or not shutil_which():
            self.skipTest("needs numpy and ffmpeg")
        import numpy as np
        sr = 16000
        audio = (0.2 * np.sin(2 * np.pi * 220 * np.arange(sr) / sr)).astype(np.float32)
        out, out_sr = voice._apply_speed(audio, sr, 0.5)
        self.assertEqual(out_sr, sr)
        self.assertAlmostEqual(len(out) / sr, 2.0, delta=0.15)

    def test_lofi_chain_cuts_the_high_end(self):
        if not (_has("numpy") and _has("scipy")):
            self.skipTest("needs numpy and scipy")
        import numpy as np
        sr = 24000
        t = np.arange(sr) / sr
        low = np.sin(2 * np.pi * 1000 * t)
        high = np.sin(2 * np.pi * 6000 * t)
        mixed = (0.5 * low + 0.5 * high).astype(np.float64)
        out, out_sr = voice._apply_lofi(mixed, sr)
        self.assertEqual(out_sr, 44100)
        mono = out.mean(axis=1) if getattr(out, "ndim", 1) == 2 else out

        def band(x, rate, f0):
            spec = np.abs(np.fft.rfft(x))
            freqs = np.fft.rfftfreq(len(x), 1 / rate)
            mask = (freqs > f0 - 150) & (freqs < f0 + 150)
            return float(spec[mask].mean()) + 1e-12

        before = band(mixed, sr, 6000) / band(mixed, sr, 1000)
        after = band(mono, out_sr, 6000) / band(mono, out_sr, 1000)
        self.assertLess(after, before * 0.35)

    def test_vocal_chain_imports_without_pyloudnorm(self):
        if not _has("scipy"):
            self.skipTest("scipy not installed")
        import builtins
        real = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name == "pyloudnorm" or name.startswith("pyloudnorm."):
                raise ImportError("blocked")
            return real(name, *args, **kwargs)

        sys.modules.pop("audio.lofi_vocals", None)
        builtins.__import__ = guarded
        try:
            import audio.lofi_vocals as vocals
            self.assertTrue(callable(vocals.lofi_chain))
            self.assertEqual(vocals.atempo_chain(0.9), "atempo=0.900000")
        finally:
            builtins.__import__ = real


class ApiTests(unittest.TestCase):
    def test_speak_degrades_and_synthesize_still_validates(self):
        from http.server import ThreadingHTTPServer
        import threading, urllib.error, urllib.request
        from engine.server import Handler

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health") as response:
                health = json.loads(response.read().decode())
            self.assertTrue(health["ok"])
            self.assertFalse(health["chatterbox"])

            def post(path, payload):
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}{path}",
                    data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json"},
                )
                try:
                    urllib.request.urlopen(request)
                except urllib.error.HTTPError as exc:
                    return exc.code, json.loads(exc.read().decode())
                self.fail(f"{path} should have failed")

            code, body = post("/api/speak", {"text": "Hello from the news desk.", "preset": "news"})
            self.assertEqual(code, 503)
            self.assertIn("chatterbox", body["error"].lower())
            self.assertFalse(body["chatterbox"])

            code, body = post("/api/speak", {})
            self.assertEqual(code, 400)
            self.assertIn("text", body["error"])

            code, body = post("/api/speak", {"text": "Hello", "preset": "opera"})
            self.assertEqual(code, 400)

            code, body = post("/api/synthesize", {})
            self.assertEqual(code, 400)
            self.assertIn("url", body["error"])

            code, body = post("/api/nope", {"text": "hi"})
            self.assertEqual(code, 404)
        finally:
            server.shutdown()
            server.server_close()


def shutil_which():
    import shutil
    return shutil.which("ffmpeg")


if __name__ == "__main__":
    unittest.main()
