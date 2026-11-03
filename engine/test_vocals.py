"""Vocals stay off unless asked. These tests never load Chatterbox."""
import os, subprocess, sys, tempfile, unittest
import numpy as np
import scipy.io.wavfile as wf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import vocals  # noqa: E402


MOOD = {"url": "https://example.com", "musical_attributes": {
    "bpm": 72, "key": "A minor", "mood_tags": ["calm", "warm"], "primary_instruments": ["piano"]}}
PAGE = {"title": "Example Domain", "url": "https://example.com"}
ANALYSIS = {"topics": ["example", "domain", "illustrative"]}


class ScriptTests(unittest.TestCase):
    def test_default_is_off(self):
        self.assertFalse(vocals.wants(None))
        self.assertFalse(vocals.wants("none"))
        self.assertFalse(vocals.wants(""))
        self.assertFalse(vocals.wants("nope"))
        self.assertTrue(vocals.wants("summary"))
        self.assertTrue(vocals.wants("song"))

    def test_summary_and_song_are_short_and_original(self):
        summary, source = vocals.build_lines("summary", MOOD, PAGE, ANALYSIS, None)
        self.assertEqual(source, "generated")
        self.assertLessEqual(len(summary), 4)
        self.assertTrue(any("BPM" in line for line in summary))
        song, _ = vocals.build_lines("song", MOOD, PAGE, ANALYSIS, None)
        self.assertEqual(len(song), 4)
        self.assertTrue(any("example" in line.lower() for line in song))

    def test_caller_script_wins_and_long_text_is_clipped(self):
        lines, source = vocals.build_lines("summary", MOOD, PAGE, ANALYSIS, "Hello there.\nSecond line")
        self.assertEqual(source, "caller")
        self.assertEqual(lines[0], "Hello there.")
        clipped, source = vocals.build_lines("song", MOOD, PAGE, ANALYSIS, "word " * 500)
        self.assertEqual(source, "caller")
        self.assertLessEqual(sum(len(line) for line in clipped), 900)

    def test_presets(self):
        calm = vocals.preset_for(["calm"], "summary")
        self.assertEqual((calm["exaggeration"], calm["cfg_weight"], calm["temperature"]), (0.5, 0.5, 0.7))
        hot = vocals.preset_for(["energetic"], "summary")
        self.assertEqual((hot["exaggeration"], hot["cfg_weight"]), (0.7, 0.3))
        calm_song = vocals.preset_for(["analytical"], "song")
        self.assertEqual(calm_song["name"], "dj")
        other_song = vocals.preset_for(["focused"], "song")
        self.assertEqual((other_song["exaggeration"], other_song["cfg_weight"]), (0.7, 0.3))

    def test_bar_grid(self):
        self.assertEqual(vocals.plan_starts(120, [0.35, 0.35]), [2.0, 6.0])

    def test_missing_chatterbox_keeps_the_beat_and_says_why(self):
        result = {"mood": MOOD, "track": {"url": "/tracks/a.mp3", "path": "/tmp/a.mp3", "title": "A"}}
        same = vocals.apply(result, "none", None, "anonymous", "a")
        self.assertIs(same, result)
        vocals._API = type("Missing", (), {"available": staticmethod(lambda: False)})()
        try:
            fallen = vocals.apply(result, "summary", None, "anonymous", "a")
        finally:
            vocals._API = None
        self.assertIsNot(fallen, result)
        self.assertEqual(fallen["track"], result["track"])
        self.assertEqual(fallen["mood"], result["mood"])
        self.assertEqual(fallen["vocals"]["state"], "instrumental")
        self.assertEqual(fallen["vocals"]["mode"], "summary")
        self.assertIn("instrumental", fallen["vocals"]["reason"].lower())

    def test_helper_import_does_not_load_torch(self):
        code = "import sys; sys.path.insert(0, 'scripts'); import chatterbox_voice; assert 'torch' not in sys.modules; assert 'chatterbox' not in sys.modules"
        subprocess.check_call([sys.executable, "-c", code], cwd=ROOT)


class MixTests(unittest.TestCase):
    def test_beat_ducks_under_the_vocal(self):
        from audio.vocal_mix import mix_lines
        sr = 44100
        n = sr * 2
        beat = (0.2 * np.sin(2 * np.pi * 110 * np.arange(n) / sr)).astype(np.float32)
        vocal = np.zeros(n, dtype=np.float32)
        vocal[:sr] = 0.4 * np.sin(2 * np.pi * 220 * np.arange(sr) / sr)
        with tempfile.TemporaryDirectory() as tmp:
            beat_path = os.path.join(tmp, "beat.wav")
            vocal_path = os.path.join(tmp, "vocal.wav")
            out = os.path.join(tmp, "mix.mp3")
            stem = os.path.join(tmp, "stem.mp3")
            wf.write(beat_path, sr, np.stack([beat, beat], axis=1))
            wf.write(vocal_path, sr, vocal)
            info = mix_lines(beat_path, [{"path": vocal_path, "start_s": 0.0}], out, stem_mp3=stem)
            self.assertLess(info["duck_ratio"], 0.95)
            self.assertGreater(os.path.getsize(out), 1000)
            self.assertGreater(os.path.getsize(stem), 1000)
            self.assertGreater(info["duration_s"], 1)


if __name__ == "__main__":
    unittest.main()
