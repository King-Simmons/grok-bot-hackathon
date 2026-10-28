"""A damaged usage totals file must not take down GET /api/usage."""
import json, os, tempfile, unittest
from datetime import datetime
from zoneinfo import ZoneInfo

_root = tempfile.mkdtemp(prefix="lofi-usage-")
os.environ["LOFI_DATA_DIR"] = os.path.join(_root, "data")
os.makedirs(os.environ["LOFI_DATA_DIR"], exist_ok=True)

from engine import usage  # noqa: E402


class UsageTotalsTest(unittest.TestCase):
    def test_summary_ignores_a_day_bucket_that_is_not_an_object(self):
        today = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
        os.makedirs(usage.DATA_DIR, exist_ok=True)
        with open(usage.TOTALS_PATH, "w", encoding="utf-8") as handle:
            json.dump({
                "all_time": {"calls": 3, "errors": 0, "tracks": 1, "audio_s": 10, "x_reads": 0},
                "days": {today: 5},
                "hours": {},
                "agents": {},
            }, handle)
        body = usage.summary()
        self.assertEqual(body["all_time"]["calls"], 3)
        self.assertEqual(body["today"]["calls"], 0)
        self.assertEqual(len(body["hours"]), 24)
        usage.record("dashboard", "synthesize", True, 0.1, tracks=1)
        body = usage.summary()
        self.assertGreaterEqual(body["all_time"]["calls"], 4)
        self.assertGreaterEqual(body["today"]["calls"], 1)

    def _write(self, payload):
        os.makedirs(usage.DATA_DIR, exist_ok=True)
        with open(usage.TOTALS_PATH, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)

    def test_non_numeric_calls_count_as_zero(self):
        today = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
        self._write({
            "all_time": {"calls": "abc", "errors": 0, "tracks": 0, "audio_s": "nope", "x_reads": 0},
            "days": {today: {"calls": "abc", "errors": 0, "tracks": 0, "audio_s": 0, "x_reads": 0, "by": {}}},
            "hours": {},
            "agents": {"dash": {"calls": "nope", "errors": 0, "tracks": 0, "audio_s": 0, "x_reads": 0}},
        })
        body = usage.summary()
        self.assertEqual(body["all_time"]["calls"], 0)
        self.assertEqual(body["all_time"]["audio_s"], 0)
        self.assertEqual(body["today"]["calls"], 0)
        self.assertEqual(body["agents"][0]["calls"], 0)
        usage.record("dashboard", "synthesize", True, 0.1, tracks=1)
        body = usage.summary()
        self.assertGreaterEqual(body["all_time"]["calls"], 1)
        self.assertGreaterEqual(body["today"]["calls"], 1)

    def test_non_object_by_is_ignored(self):
        today = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
        self._write({
            "all_time": {"calls": 2, "errors": 0, "tracks": 0, "audio_s": 0, "x_reads": 0},
            "days": {today: {"calls": 2, "errors": 0, "tracks": 0, "audio_s": 0, "x_reads": 0, "by": [1, 2]}},
            "hours": {},
            "agents": {},
        })
        body = usage.summary()
        self.assertEqual(body["today"]["calls"], 2)
        self.assertEqual(body["agents_today"], [])
        self.assertEqual(body["all_time"]["calls"], 2)
        usage.record("dashboard", "synthesize", True, 0.1, tracks=1)
        body = usage.summary()
        self.assertGreaterEqual(body["today"]["calls"], 3)
        self.assertEqual(body["agents_today"][0]["agent"], "dashboard")

    def test_infinite_and_negative_counters_stay_json_safe(self):
        today = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
        self._write({
            "all_time": {
                "calls": 1e400, "errors": -2, "tracks": 1, "audio_s": float("nan"), "x_reads": -1,
            },
            "days": {today: {
                "calls": -5, "errors": 0, "tracks": 0, "audio_s": float("inf"), "x_reads": 0, "by": {},
            }},
            "hours": {},
            "agents": {},
        })
        body = usage.summary()
        self.assertEqual(body["all_time"]["calls"], 0)
        self.assertEqual(body["all_time"]["errors"], 0)
        self.assertEqual(body["all_time"]["x_reads"], 0)
        self.assertEqual(body["all_time"]["audio_s"], 0.0)
        self.assertEqual(body["today"]["calls"], 0)
        self.assertEqual(body["today"]["audio_s"], 0.0)
        self.assertEqual(usage._int_map({"a": 1e400, "b": 2, "c": -3}), {"b": 2, "c": 0})
        dumped = json.dumps(body)
        self.assertNotIn("NaN", dumped)
        self.assertNotIn("Infinity", dumped)

    def test_one_bad_hour_does_not_drop_all_time(self):
        self._write({
            "all_time": {"calls": 7, "errors": 0, "tracks": 0, "audio_s": 1.5, "x_reads": 0},
            "days": {},
            "hours": {"2020-01-01T00": 1e400},
            "agents": {},
        })
        body = usage.summary()
        self.assertEqual(body["all_time"]["calls"], 7)
        self.assertEqual(body["all_time"]["audio_s"], 1.5)


if __name__ == "__main__":
    unittest.main()
