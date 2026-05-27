import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from invest.config import AppConfig
from invest.pipeline import extract_pipeline_result_json, run_pipeline


class PipelineTests(unittest.TestCase):
    def test_weekly_pipeline_runs_refresh_report_build_and_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = AppConfig(
                path=Path("config/invest.toml"),
                data={
                    "reports": {"directory": str(Path(tmp) / "reports")},
                    "database": {"path": str(Path(tmp) / "invest.db")},
                    "managers": [{"key": "situational-awareness", "name": "Situational Awareness LP", "primary": True}],
                },
            )
            with (
                patch("invest.pipeline.refresh_filings", return_value={"stored": 2}) as filings,
                patch("invest.pipeline.sync_brokers", return_value={"imported": 3}) as brokers,
                patch("invest.pipeline.generate_brief", return_value=(Path(tmp) / "weekly.md", Path(tmp) / "weekly.json")) as brief,
                patch("invest.pipeline.build_site", return_value={"out_dir": str(Path(tmp) / "web")}) as site,
                patch("invest.pipeline.assert_public_assets_safe") as scan,
                patch("invest.pipeline.assert_public_snapshot_quality") as quality,
            ):
                result = run_pipeline(None, config, "weekly", out_dir=Path(tmp) / "web", force=True)

            self.assertEqual(result["status"], "ran")
            filings.assert_called_once()
            brokers.assert_called_once()
            brief.assert_called_once_with(None, config, "weekly")
            site.assert_called_once()
            scan.assert_called_once_with(Path(tmp) / "web")
            quality.assert_called_once_with(Path(tmp) / "web")

    def test_scheduled_duplicate_window_skips_without_side_effects(self):
        config = AppConfig(path=Path("config/invest.toml"), data={})
        with (
            patch("invest.pipeline.refresh_filings") as filings,
            patch("invest.pipeline.sync_brokers") as brokers,
            patch("invest.pipeline.generate_brief") as brief,
            patch("invest.pipeline.build_site") as site,
        ):
            result = run_pipeline(
                None,
                config,
                "premarket",
                scheduled_at="2026-07-06T13:00:00Z",
            )

        self.assertEqual(result["status"], "skipped")
        filings.assert_not_called()
        brokers.assert_not_called()
        brief.assert_not_called()
        site.assert_not_called()

    def test_midday_pipeline_generates_midday_brief(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = AppConfig(
                path=Path("config/invest.toml"),
                data={"reports": {"directory": str(Path(tmp) / "reports")}},
            )
            with (
                patch("invest.pipeline.refresh_filings", return_value={"stored": 1}),
                patch("invest.pipeline.sync_brokers", return_value={"imported": 1}),
                patch("invest.pipeline.generate_brief", return_value=(Path(tmp) / "midday.md", Path(tmp) / "midday.json")) as brief,
                patch("invest.pipeline.build_site", return_value={"out_dir": str(Path(tmp) / "web")}),
                patch("invest.pipeline.assert_public_assets_safe"),
                patch("invest.pipeline.assert_public_snapshot_quality"),
            ):
                result = run_pipeline(None, config, "midday", out_dir=Path(tmp) / "web", force=True)

        self.assertEqual(result["status"], "ran")
        brief.assert_called_once_with(None, config, "midday")

    def test_intraday_pipeline_reuses_stored_broker_positions(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = AppConfig(
                path=Path("config/invest.toml"),
                data={"reports": {"directory": str(Path(tmp) / "reports")}},
            )
            with (
                patch("invest.pipeline.refresh_filings", return_value={"stored": 1}),
                patch("invest.pipeline.sync_brokers") as brokers,
                patch("invest.pipeline.generate_brief", return_value=(Path(tmp) / "intraday.md", Path(tmp) / "intraday.json")) as brief,
                patch("invest.pipeline.build_site", return_value={"out_dir": str(Path(tmp) / "web")}),
                patch("invest.pipeline.assert_public_assets_safe"),
                patch("invest.pipeline.assert_public_snapshot_quality"),
            ):
                result = run_pipeline(None, config, "intraday", out_dir=Path(tmp) / "web", force=True)

        self.assertEqual(result["status"], "ran")
        self.assertEqual(result["brokers"]["status"], "not_run")
        brokers.assert_not_called()
        brief.assert_called_once_with(None, config, "intraday")

    def test_public_pipeline_defers_publish_for_shrunken_portfolio_after_broker_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports_dir = root / "reports"
            out_dir = root / "web"
            reports_dir.mkdir()
            (out_dir / "data").mkdir(parents=True)
            previous_payload = {
                "portfolio": {
                    "position_count": 12,
                    "symbol_count": 8,
                    "by_symbol": [{"symbol": "NVDA", "weight": 0.2}],
                }
            }
            new_payload = {
                "as_of": "2026-05-24",
                "session": "premarket",
                "portfolio": {"position_count": 3, "symbol_count": 3},
            }
            latest = out_dir / "data" / "latest.json"
            report_json = reports_dir / "2026-05-24-premarket.json"
            latest.write_text(json.dumps(previous_payload), encoding="utf-8")
            report_json.write_text(json.dumps(new_payload), encoding="utf-8")
            config = AppConfig(
                path=Path("config/invest.toml"),
                data={"reports": {"directory": str(reports_dir)}},
            )
            with (
                patch("invest.pipeline.refresh_filings", return_value={"stored": 0}),
                patch(
                    "invest.pipeline.sync_brokers",
                    return_value={"imported": 0, "details": {"ibkr": {"status": "failed"}}},
                ),
                patch("invest.pipeline.generate_brief", return_value=(reports_dir / "brief.md", report_json)),
                patch("invest.pipeline.build_site") as site,
            ):
                result = run_pipeline(None, config, "premarket", out_dir=out_dir, force=True)

            self.assertEqual(result["status"], "deferred")
            self.assertIn("Refusing to publish public snapshot", result["reason"])
            site.assert_not_called()

    def test_extract_pipeline_result_json_ignores_nested_status_objects(self):
        text = """
Stored 13F 0000000000-26-000001 with 12 holdings
{"status": "synced", "report_id": 123}
{
  "brokers": {"details": {"ibkr": {"status": "failed"}}},
  "kind": "postmarket",
  "privacy": "public",
  "reason": "Refusing to publish public snapshot because broker sync failed/skipped.",
  "schedule": {"should_run": true},
  "status": "deferred",
  "warehouse": {"status": "synced"}
}
{"status": "synced", "rows": 0}
"""

        result = extract_pipeline_result_json(text)

        self.assertEqual(result["status"], "deferred")
        self.assertIn("Refusing to publish public snapshot", result["reason"])

    def test_extract_pipeline_result_json_requires_top_level_pipeline_shape(self):
        with self.assertRaises(ValueError):
            extract_pipeline_result_json('{"status": "synced"}')


if __name__ == "__main__":
    unittest.main()
