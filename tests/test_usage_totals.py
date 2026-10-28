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


if __name__ == "__main__":
    unittest.main()
