import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from invest.config import AppConfig
from invest.pipeline import PortfolioSnapshotRegression, run_pipeline


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
            ):
                result = run_pipeline(None, config, "weekly", out_dir=Path(tmp) / "web", force=True)

            self.assertEqual(result["status"], "ran")
            filings.assert_called_once()
            brokers.assert_called_once()
            brief.assert_called_once_with(None, config, "weekly")
            site.assert_called_once()
            scan.assert_called_once_with(Path(tmp) / "web")

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

    def test_public_pipeline_refuses_to_publish_shrunken_portfolio_after_broker_failure(self):
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
                with self.assertRaises(PortfolioSnapshotRegression):
                    run_pipeline(None, config, "premarket", out_dir=out_dir, force=True)

            site.assert_not_called()


if __name__ == "__main__":
    unittest.main()
