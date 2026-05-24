from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from invest.config import AppConfig
from invest.pipeline import run_pipeline


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


if __name__ == "__main__":
    unittest.main()
