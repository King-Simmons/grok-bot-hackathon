"""The ffmpeg fallback must not leave a partial mp3 at the final path."""
import builtins, os, subprocess, sys, tempfile, unittest
from unittest.mock import patch

from engine.synth import render

ATTRS = {
    "bpm": 74, "key": "A minor", "mood_tags": ["analytical"],
    "primary_instruments": ["Rhodes", "upright bass", "dusty drums"],
}


_REAL_IMPORT = builtins.__import__


def _block_lameenc(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "lameenc":
        raise ImportError("lameenc blocked")
    return _REAL_IMPORT(name, globals, locals, fromlist, level)


class FfmpegEncodeTest(unittest.TestCase):
    def test_failed_ffmpeg_leaves_the_existing_mp3_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "track.mp3")
            with open(out, "wb") as handle:
                handle.write(b"OLD")
            seen = []

            def fake_run(cmd, check=False):
                dest = cmd[-1]
                seen.append(dest)
                with open(dest, "wb") as handle:
                    handle.write(b"PARTIAL")
                raise subprocess.CalledProcessError(1, cmd)

            sys.modules.pop("lameenc", None)
            with patch("builtins.__import__", _block_lameenc), patch("subprocess.run", fake_run):
                with self.assertRaises(subprocess.CalledProcessError):
                    render(ATTRS, seed=1, out_mp3=out)
            with open(out, "rb") as handle:
                self.assertEqual(handle.read(), b"OLD")
            self.assertTrue(seen)
            self.assertNotEqual(os.path.abspath(seen[0]), os.path.abspath(out))
            leftovers = [name for name in os.listdir(tmp) if name != "track.mp3"]
            self.assertEqual(leftovers, [])

    def test_successful_ffmpeg_replaces_the_mp3_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "track.mp3")
            with open(out, "wb") as handle:
                handle.write(b"OLD")

            def fake_run(cmd, check=False):
                dest = cmd[-1]
                self.assertNotEqual(os.path.abspath(dest), os.path.abspath(out))
                with open(dest, "wb") as handle:
                    handle.write(b"FULLMP3")
                return subprocess.CompletedProcess(cmd, 0)

            sys.modules.pop("lameenc", None)
            with patch("builtins.__import__", _block_lameenc), patch("subprocess.run", fake_run):
                info = render(ATTRS, seed=1, out_mp3=out)
            with open(out, "rb") as handle:
                self.assertEqual(handle.read(), b"FULLMP3")
            self.assertIn("encode_s", info)
            leftovers = [name for name in os.listdir(tmp) if name != "track.mp3"]
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
