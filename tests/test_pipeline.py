import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from invest.brokers.ibkr import FlexError
from invest.config import AppConfig
from invest.pipeline import extract_pipeline_result_json, run_pipeline, sync_ibkr


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

    def test_public_pipeline_uses_previous_public_portfolio_after_broker_failure_regression(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports_dir = root / "reports"
            out_dir = root / "web"
            reports_dir.mkdir()
            (out_dir / "data").mkdir(parents=True)
            previous_payload = {
                "site": {"source_report": "previous.json", "built_at": "2026-05-23T12:00:00Z"},
                "portfolio": {
                    "position_count": 2,
                    "symbol_count": 2,
                    "security_symbol_count": 2,
                    "cash_weight": 0.2,
                    "equity_weight": 0.8,
                    "weight_basis": "invested_equity_ex_cash",
                    "total_weight_basis": "total_portfolio_including_cash",
                    "by_symbol": [
                        {"symbol": "NVDA", "bucket": "semis_networking_hbm", "weight": 0.75, "total_weight": 0.6},
                        {"symbol": "MU", "bucket": "semis_networking_hbm", "weight": 0.25, "total_weight": 0.2},
                    ],
                    "by_bucket": [
                        {"bucket": "semis_networking_hbm", "weight": 1.0, "total_weight": 0.8},
                    ],
                },
            }
            new_payload = {
                "as_of": "2026-05-24",
                "session": "premarket",
                "portfolio": {"position_count": 1, "symbol_count": 1},
                "portfolio_benchmark": {
                    "primary_horizon": "3m",
                    "primary_portfolio_return": 999.0,
                    "horizon_returns": [{"key": "3m", "label": "3M", "portfolio_return": 999.0, "price_coverage_pct": 100.0}],
                    "performance_components": [
                        {"symbol": "TSM", "weight": 1.0, "five_day_pct": 99.0, "contribution_pct": 99.0}
                    ],
                    "sizing_plan": {
                        "rebalance_budget": {
                            "starting_cash_weight": 0.1,
                            "total_add_weight": 0.03,
                            "total_trim_weight": 0.0,
                            "net_delta_weight": 0.03,
                            "cash_deployed_weight": 0.03,
                            "cash_raised_weight": 0.0,
                            "post_trade_cash_weight": 0.07,
                        },
                    },
                    "action_queue": [
                        {
                            "symbol": "MU",
                            "portfolio_weight": 0.0,
                            "current_weight": 0.0,
                            "recommended_delta_weight": 0.03,
                            "post_action_weight": 0.03,
                            "target_weight": 0.03,
                            "trade_target_weight": 0.03,
                            "trade_action": "add",
                            "model_target_weight": 0.03,
                            "max_allowed_weight": 0.15,
                        },
                        {
                            "symbol": "TSM",
                            "portfolio_weight": 0.0,
                            "current_weight": 0.0,
                            "recommended_delta_weight": 0.03,
                            "post_action_weight": 0.03,
                            "target_weight": 0.03,
                            "trade_target_weight": 0.03,
                            "trade_action": "add",
                        },
                    ],
                },
                "market_return_windows": {
                    "NVDA": {"5d": 4.0, "1m": 8.0, "3m": 12.0},
                    "MU": {"5d": 2.0, "1m": 6.0, "3m": 10.0},
                    "TSM": {"5d": 99.0, "1m": 99.0, "3m": 99.0},
                    "SPY": {"5d": 1.0, "1m": 2.0, "3m": 3.0},
                },
                "data_health": {
                    "recommendation_posture": "normal",
                    "summary": "ok",
                    "sources": [
                        {
                            "source": "broker_positions",
                            "label": "Position snapshot",
                            "status": "ok",
                            "detail": "1 current broker/account rows available",
                        }
                    ],
                    "weak_source_count": 0,
                },
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
                    return_value={
                        "imported": 0,
                        "details": {
                            "ibkr": {
                                "status": "failed",
                                "error": "IBKR Flex request failed: try again shortly.",
                                "attempts": 6,
                                "wait_seconds": 10.0,
                            }
                        },
                    },
                ),
                patch("invest.pipeline.generate_brief", return_value=(reports_dir / "brief.md", report_json)),
                patch("invest.pipeline.build_site", return_value={"out_dir": str(out_dir)}) as site,
                patch("invest.pipeline.assert_public_assets_safe"),
                patch("invest.pipeline.assert_public_snapshot_quality"),
            ):
                result = run_pipeline(None, config, "premarket", out_dir=out_dir, force=True)

            self.assertEqual(result["status"], "ran")
            self.assertEqual(result["portfolio_fallback"]["status"], "used_previous_public_portfolio")
            self.assertNotIn("IBKR", result["portfolio_fallback"]["reason"])
            site.assert_called_once()
            updated_payload = json.loads(report_json.read_text(encoding="utf-8"))
            self.assertEqual(updated_payload["portfolio"]["symbol_count"], 2)
            self.assertEqual(updated_payload["portfolio"]["by_symbol"][1]["symbol"], "MU")
            self.assertEqual(updated_payload["portfolio"]["by_symbol"][1]["comparison_weight"], 0.25)
            self.assertEqual(updated_payload["portfolio_benchmark"]["action_queue"][0]["portfolio_weight"], 0.25)
            self.assertEqual(updated_payload["portfolio_benchmark"]["action_queue"][0]["recommended_delta_weight"], 0.0)
            self.assertEqual(updated_payload["portfolio_benchmark"]["action_queue"][0]["post_action_weight"], 0.25)
            self.assertEqual(updated_payload["portfolio_benchmark"]["action_queue"][0]["trade_action"], "hold")
            self.assertEqual(updated_payload["portfolio_benchmark"]["action_queue"][1]["portfolio_weight"], 0.0)
            self.assertEqual(updated_payload["portfolio_benchmark"]["action_queue"][1]["recommended_delta_weight"], 0.0)
            self.assertEqual(updated_payload["portfolio_benchmark"]["action_queue"][1]["post_action_weight"], 0.0)
            self.assertEqual(updated_payload["portfolio_benchmark"]["action_queue"][1]["trade_action"], "hold")
            self.assertEqual(updated_payload["portfolio_benchmark"]["portfolio_return_5d"], 3.5)
            self.assertEqual(updated_payload["portfolio_benchmark"]["primary_horizon"], "3m")
            self.assertEqual(updated_payload["portfolio_benchmark"]["primary_portfolio_return"], 11.5)
            self.assertEqual(updated_payload["portfolio_benchmark"]["performance_universe"]["portfolio_symbol_count"], 2)
            self.assertTrue(
                updated_payload["portfolio_benchmark"]["performance_universe"]["recomputed_after_portfolio_fallback"]
            )
            self.assertEqual(
                {row["symbol"] for row in updated_payload["portfolio_benchmark"]["performance_components"]},
                {"NVDA", "MU"},
            )
            self.assertFalse(
                any(row["symbol"] == "TSM" for row in updated_payload["portfolio_benchmark"]["performance_components"])
            )
            budget = updated_payload["portfolio_benchmark"]["sizing_plan"]["rebalance_budget"]
            self.assertEqual(budget["starting_cash_weight"], 0.2)
            self.assertEqual(budget["total_add_weight"], 0.0)
            self.assertEqual(budget["cash_deployed_weight"], 0.0)
            self.assertEqual(budget["post_trade_cash_weight"], 0.2)
            self.assertEqual(updated_payload["data_health"]["sources"][0]["source"], "portfolio_fallback")
            self.assertEqual(updated_payload["data_health"]["sources"][1]["source"], "broker_positions")
            self.assertEqual(updated_payload["data_health"]["sources"][1]["status"], "stale")
            self.assertIn("previous public snapshot", updated_payload["data_health"]["sources"][1]["detail"])
            self.assertEqual(updated_payload["data_health"]["recommendation_posture"], "reduced_confidence")
            self.assertNotIn("IBKR", json.dumps(updated_payload["portfolio_fallback"]))

    def test_public_pipeline_uses_previous_public_portfolio_after_suspicious_successful_shrink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports_dir = root / "reports"
            out_dir = root / "web"
            reports_dir.mkdir()
            (out_dir / "data").mkdir(parents=True)
            previous_symbols = [f"SYM{index}" for index in range(12)]
            previous_payload = {
                "site": {"source_report": "previous.json", "built_at": "2026-05-26T15:53:36Z"},
                "portfolio": {
                    "position_count": 12,
                    "symbol_count": 12,
                    "security_symbol_count": 12,
                    "cash_weight": 0.0,
                    "equity_weight": 1.0,
                    "weight_basis": "invested_equity_ex_cash",
                    "total_weight_basis": "total_portfolio_including_cash",
                    "by_symbol": [
                        {"symbol": symbol, "bucket": "semis_networking_hbm", "weight": round(1 / 12, 6)}
                        for symbol in previous_symbols
                    ],
                    "by_bucket": [{"bucket": "semis_networking_hbm", "weight": 1.0}],
                },
            }
            new_payload = {
                "as_of": "2026-05-27",
                "session": "market_close",
                "portfolio": {
                    "position_count": 3,
                    "symbol_count": 3,
                    "by_symbol": [
                        {"symbol": "NVDA", "bucket": "semis_networking_hbm", "weight": 0.5},
                        {"symbol": "TSM", "bucket": "semis_networking_hbm", "weight": 0.3},
                        {"symbol": "AMZN", "bucket": "frontier_ai_platforms", "weight": 0.2},
                    ],
                },
                "portfolio_benchmark": {"action_queue": [], "sizing_plan": {"rebalance_budget": {}}},
                "data_health": {
                    "recommendation_posture": "normal",
                    "summary": "ok",
                    "sources": [
                        {
                            "source": "position_snapshot",
                            "label": "Position snapshot",
                            "status": "ok",
                            "detail": "3 current broker/account rows available",
                        }
                    ],
                    "weak_source_count": 0,
                },
            }
            latest = out_dir / "data" / "latest.json"
            report_json = reports_dir / "2026-05-27-market_close.json"
            latest.write_text(json.dumps(previous_payload), encoding="utf-8")
            report_json.write_text(json.dumps(new_payload), encoding="utf-8")
            config = AppConfig(
                path=Path("config/invest.toml"),
                data={"reports": {"directory": str(reports_dir)}},
            )
            with (
                patch("invest.pipeline.refresh_filings", return_value={"stored": 0}),
                patch("invest.pipeline.sync_brokers", return_value={"imported": 3, "details": {"ibkr": {"status": "ok"}}}),
                patch("invest.pipeline.generate_brief", return_value=(reports_dir / "brief.md", report_json)),
                patch("invest.pipeline.build_site", return_value={"out_dir": str(out_dir)}) as site,
                patch("invest.pipeline.assert_public_assets_safe"),
                patch("invest.pipeline.assert_public_snapshot_quality"),
            ):
                result = run_pipeline(None, config, "market_close", out_dir=out_dir, force=True)

            self.assertEqual(result["status"], "ran")
            self.assertEqual(result["portfolio_fallback"]["status"], "used_previous_public_portfolio")
            self.assertIn("looks incomplete", result["portfolio_fallback"]["regression_reason"])
            site.assert_called_once()
            updated_payload = json.loads(report_json.read_text(encoding="utf-8"))
            self.assertEqual(updated_payload["portfolio"]["symbol_count"], 12)
            self.assertEqual(updated_payload["portfolio"]["by_symbol"][0]["symbol"], "SYM0")
            self.assertEqual(updated_payload["data_health"]["sources"][0]["source"], "portfolio_fallback")
            self.assertEqual(updated_payload["data_health"]["sources"][1]["source"], "position_snapshot")
            self.assertEqual(updated_payload["data_health"]["sources"][1]["status"], "stale")
            self.assertIn("previous public snapshot", updated_payload["data_health"]["sources"][1]["detail"])

    def test_public_pipeline_uses_previous_manager_radar_after_refresh_regression(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports_dir = root / "reports"
            out_dir = root / "web"
            reports_dir.mkdir()
            (out_dir / "data").mkdir(parents=True)
            previous_payload = {
                "site": {"source_report": "previous.json", "built_at": "2026-05-26T15:53:36Z"},
                "manager_radar": {
                    "stored_latest_count": 2,
                    "manager_count": 2,
                    "focus_managers": [
                        {
                            "manager_key": "altimeter",
                            "manager_name": "Altimeter",
                            "manager_tier": "tier_1",
                            "status": "ok",
                            "positions": [{"rank": 1, "symbol": "NVDA", "fund_weight": 0.4}],
                            "top_positions": [{"rank": 1, "symbol": "NVDA", "fund_weight": 0.4}],
                        }
                    ],
                },
            }
            new_payload = {
                "as_of": "2026-05-27",
                "session": "postmarket",
                "portfolio": {"position_count": 1, "symbol_count": 1},
                "manager_radar": {
                    "stored_latest_count": 0,
                    "manager_count": 2,
                    "focus_managers": [
                        {
                            "manager_key": "altimeter",
                            "manager_name": "Altimeter",
                            "manager_tier": "tier_1",
                            "status": "missing_latest_filing",
                            "positions": [],
                            "top_positions": [],
                        }
                    ],
                },
                "data_health": {"sources": [], "summary": "ok", "recommendation_posture": "normal"},
            }
            latest = out_dir / "data" / "latest.json"
            report_json = reports_dir / "2026-05-27-postmarket.json"
            latest.write_text(json.dumps(previous_payload), encoding="utf-8")
            report_json.write_text(json.dumps(new_payload), encoding="utf-8")
            config = AppConfig(
                path=Path("config/invest.toml"),
                data={"reports": {"directory": str(reports_dir)}},
            )
            with (
                patch("invest.pipeline.refresh_filings", return_value={"stored": 0}),
                patch("invest.pipeline.generate_brief", return_value=(reports_dir / "brief.md", report_json)),
                patch("invest.pipeline.build_site", return_value={"out_dir": str(out_dir)}),
                patch("invest.pipeline.assert_public_assets_safe"),
                patch("invest.pipeline.assert_public_snapshot_quality"),
            ):
                result = run_pipeline(None, config, "intraday", out_dir=out_dir, force=True)

            self.assertEqual(result["status"], "ran")
            self.assertEqual(result["manager_radar_fallback"]["status"], "used_previous_public_manager_radar")
            updated_payload = json.loads(report_json.read_text(encoding="utf-8"))
            focus = updated_payload["manager_radar"]["focus_managers"][0]
            self.assertEqual(focus["status"], "ok")
            self.assertEqual(focus["positions"][0]["symbol"], "NVDA")
            self.assertEqual(updated_payload["data_health"]["sources"][0]["source"], "manager_radar_fallback")
            self.assertEqual(updated_payload["data_health"]["recommendation_posture"], "reduced_confidence")

    def test_sync_ibkr_uses_bounded_retry_configuration(self):
        config = AppConfig(path=Path("config/invest.toml"), data={"ibkr": {"raw_directory": "raw"}})
        with (
            patch.dict(os.environ, {
                "IBKR_FLEX_TOKEN": "token",
                "IBKR_FLEX_ACTIVITY_QUERY_ID": "query",
                "IBKR_FLEX_ATTEMPTS": "3",
                "IBKR_FLEX_WAIT_SECONDS": "0.5",
            }),
            patch("invest.pipeline.fetch_flex_statement", side_effect=FlexError("try again", retryable=True)) as fetch,
        ):
            imported, detail = sync_ibkr(None, config)

        self.assertEqual(imported, 0)
        self.assertEqual(detail["status"], "failed")
        self.assertEqual(detail["attempts"], 3)
        self.assertEqual(detail["wait_seconds"], 0.5)
        fetch.assert_called_once_with(
            "token",
            "query",
            Path("raw"),
            attempts=3,
            wait_seconds=0.5,
        )

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
