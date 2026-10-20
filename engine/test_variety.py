"""Same seed renders the same audio. Ten seeds of one mood use ten progressions.

Length stays on the original formula (target 38 s, 2 s tail, 32 kHz).
"""
import unittest

from engine.synth import SR, choose, progression_pool, render

MOODS = {
    "analytical": {"bpm": 74, "key": "A minor", "mood_tags": ["analytical", "focused"],
                   "primary_instruments": ["Rhodes", "felt piano", "upright bass", "dusty drums", "vinyl crackle"]},
    "calm": {"bpm": 70, "key": "F major", "mood_tags": ["calm", "cozy"],
             "primary_instruments": ["felt piano", "nylon guitar", "upright bass", "dusty drums", "rain"]},
    "energetic": {"bpm": 88, "key": "G minor", "mood_tags": ["energetic", "bouncy"],
                  "primary_instruments": ["Rhodes", "vibraphone", "sub bass", "dusty drums"]},
    "dark": {"bpm": 66, "key": "C minor", "mood_tags": ["dark", "moody"],
             "primary_instruments": ["warm pad", "felt piano", "sub bass", "dusty drums", "vinyl crackle"]},
    "melancholic": {"bpm": 68, "key": "E minor", "mood_tags": ["melancholic", "nostalgic"],
                    "primary_instruments": ["felt piano", "warm pad", "upright bass", "vinyl crackle"]},
    "upbeat": {"bpm": 84, "key": "C major", "mood_tags": ["upbeat", "warm"],
               "primary_instruments": ["Rhodes", "nylon guitar", "vibraphone", "dusty drums"]},
}


def legacy_duration(bpm, target_s=38.0):
    bar = 4 * (60 / float(bpm))
    nbars = int(max(9, min(16, round(target_s / bar) + 1)))
    return round(int((nbars * bar + 2.0) * SR) / SR, 1)


class VarietyTest(unittest.TestCase):
    def test_pools_cover_ten_seeds(self):
        for mood in MOODS:
            for mode in ("minor", "major"):
                self.assertGreaterEqual(len(progression_pool(mode, mood)), 10, f"{mood} {mode}")

    def test_ten_seeds_distinct_progressions_and_voices(self):
        for mood, attrs in MOODS.items():
            picked = [choose(attrs, seed=s) for s in range(10)]
            prog = [p["progression"] for p in picked]
            self.assertEqual(len(set(prog)), 10, f"{mood} progressions collided: {prog}")
            self.assertGreaterEqual(len({p["chord_voice"] for p in picked}), 3, mood)
            self.assertGreaterEqual(len({p["lead_voice"] for p in picked}), 2, mood)
            self.assertEqual(choose(attrs, seed=3), picked[3])

    def test_duration_matches_the_old_formula(self):
        for bpm in (66, 74, 88):
            info = render({"bpm": bpm, "key": "A minor", "mood_tags": ["analytical"],
                           "primary_instruments": ["Rhodes", "upright bass", "dusty drums"]}, seed=1)
            self.assertEqual(info["duration_s"], legacy_duration(bpm))
            self.assertLess(info["render_s"], 4.0)

    def test_same_seed_is_identical_audio(self):
        attrs = MOODS["analytical"]
        a = render(attrs, seed=4)
        b = render(attrs, seed=4)
        c = render(attrs, seed=5)
        self.assertEqual(a["audio_sha1"], b["audio_sha1"])
        self.assertEqual(a["progression"], b["progression"])
        self.assertEqual(a["chord_voice"], b["chord_voice"])
        self.assertEqual(a["duration_s"], b["duration_s"])
        self.assertNotEqual(a["audio_sha1"], c["audio_sha1"])
        self.assertEqual(a["duration_s"], legacy_duration(attrs["bpm"]))
        self.assertLess(a["render_s"], 4.0)


if __name__ == "__main__":
    unittest.main()
