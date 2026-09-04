import json
import tempfile
import unittest
from pathlib import Path

from web.adapters import TrackerAdapter


class TrackerAdapterTests(unittest.TestCase):
    def test_snapshot_maps_existing_tracker_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "outputs").mkdir()
            (root / "config").mkdir()
            (root / "work").mkdir()
            status = {"last_scan": 1, "last_scan_iso": "x", "universe": 10, "active_directions": 4,
                      "new_signals": [{"strategy":"enhanced","symbol":"BTCUSDT","side":"做多","entry":100,"stop":98,"target":104,"risk_pct":.015}],
                      "positions": [], "trades": [{"net_r": 2}, {"net_r": -1}], "cost_model":"costs"}
            (root / "outputs" / "paper_tracker_status.json").write_text(json.dumps(status), encoding="utf-8")
            (root / "config" / "trading_scanner_config.json").write_text('{"telegram_enabled": false}', encoding="utf-8")
            result = TrackerAdapter(root).snapshot()
            self.assertEqual(result["signals"][0]["grade"], "A")
            self.assertEqual(result["signals"][0]["score"], 90)
            self.assertEqual(result["signals"][0]["score_parts"]["reward_risk"], 20)
            self.assertEqual(result["signals"][0]["reward_risk"], 2.0)
            self.assertEqual(result["metrics"]["expectancy_r"], .5)
            self.assertEqual(result["metrics"]["win_rate"], 50.0)
            self.assertFalse(result["telegram"]["enabled"])


if __name__ == "__main__":
    unittest.main()
