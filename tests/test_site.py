import json
import os
from pathlib import Path
import tempfile
import unittest

from invest.backtest import BACKTEST_VERSION
from invest.site import build_public_moves, build_site, sanitize_payload, stale_status


class SiteTests(unittest.TestCase):
    def test_build_site_uses_newest_same_day_report_by_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports_dir = root / "reports"
            reports_dir.mkdir()
            out_dir = root / "web"
            postmarket = minimal_report_payload("2026-05-24", "postmarket")
            premarket = minimal_report_payload("2026-05-24", "premarket")
            postmarket_path = reports_dir / "2026-05-24-postmarket.json"
            premarket_path = reports_dir / "2026-05-24-premarket.json"
            postmarket_path.write_text(
                json.dumps(postmarket),
                encoding="utf-8",
            )
            premarket_path.write_text(
                json.dumps(premarket),
                encoding="utf-8",
            )
            os.utime(postmarket_path, (1_779_400_000, 1_779_400_000))
            os.utime(premarket_path, (1_779_500_000, 1_779_500_000))

            result = build_site(reports_dir, out_dir=out_dir, privacy="public")

            latest = json.loads((out_dir / "data" / "latest.json").read_text(encoding="utf-8"))
            self.assertTrue(result["latest_report"].endswith("2026-05-24-premarket.json"))
            self.assertEqual(latest["site"]["source_report"], "2026-05-24-premarket.json")
            self.assertEqual(latest["site"]["report_session"], "premarket")

    def test_build_site_uses_midday_rank_when_report_mtime_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports_dir = root / "reports"
            reports_dir.mkdir()
            out_dir = root / "web"
            premarket_path = reports_dir / "2026-05-24-premarket.json"
            midday_path = reports_dir / "2026-05-24-midday.json"
            premarket_path.write_text(
                json.dumps(minimal_report_payload("2026-05-24", "premarket")),
                encoding="utf-8",
            )
            midday_path.write_text(
                json.dumps(minimal_report_payload("2026-05-24", "midday")),
                encoding="utf-8",
            )
            os.utime(premarket_path, (1_779_500_000, 1_779_500_000))
            os.utime(midday_path, (1_779_500_000, 1_779_500_000))

            result = build_site(reports_dir, out_dir=out_dir, privacy="public")

            latest = json.loads((out_dir / "data" / "latest.json").read_text(encoding="utf-8"))
            self.assertTrue(result["latest_report"].endswith("2026-05-24-midday.json"))
            self.assertEqual(latest["site"]["source_report"], "2026-05-24-midday.json")
            self.assertEqual(latest["site"]["report_session"], "midday")

    def test_public_payload_redacts_broker_data_and_adds_moves(self):
        payload = {
            "product": {"name": "Old", "domain": "old.example"},
            "positions": {"NVDA": 1000},
            "transactions": [{"symbol": "NVDA"}],
            "portfolio": {
                "position_count": 1,
                "symbol_count": 1,
                "gross_exposure": 1000,
                "net_exposure": 1000,
                "by_bucket": [{"bucket": "semis_networking_hbm", "weight": 1.0, "market_value": 1000}],
                "by_symbol": [{"symbol": "NVDA", "bucket": "semis_networking_hbm", "weight": 1.0, "market_value": 1000}],
            },
            "decision_cards": [
                {
                    "symbol": "NVDA",
                    "score": 50,
                    "bucket": "semis_networking_hbm",
                    "candidate": "research hold/add-on-dip candidate",
                    "portfolio_value": 1000,
                    "consensus_manager_count": 4,
                    "news_count": 2,
                    "put_value": 0,
                    "call_value": 0,
                    "consensus_value": 100000000,
                    "counterargument": "Risk.",
                    "falsifier": "Wrong.",
                }
            ],
            "macro": {"regime": "mixed macro tape"},
            "ideas": [],
            "data_health": {
                "recommendation_posture": "normal",
                "sources": [{"source": "broker_positions", "label": "Broker positions", "status": "ok", "detail": "1 row"}],
            },
            "methodology": {
                "version": "test",
                "updated_by_backend": True,
                "risk_and_sizing": {
                    "private_ticket_fields": ["estimated_notional", "estimated_shares"],
                    "approval_required": True,
                    "order_execution": "none",
                },
                "public_privacy": {"stripped_fields": ["quantity", "market_value"]},
            },
            "audit": {"source_freshness": [{"source": "broker_positions", "raw": {"account": "U123"}}]},
            "calendars": {
                "earnings": {"events": [{"symbol": "NVDA", "raw": {"account": "U123"}, "confidence": 1.0}]},
                "filings_13f": {"managers": [{"manager_key": "m1", "status": "pending"}]},
            },
            "engine": {
                "ranked_candidates": [{"symbol": "NVDA", "raw_json": {"account": "U123"}, "expected_return_rank_score": 55}],
                "optimizer": {"allocations": [{"symbol": "NVDA", "estimated_notional": 1000}]},
            },
            "feature_matrix": {
                "rows": [{"symbol": "NVDA", "current_weight": 0.1, "raw": {"account": "U123"}, "private_notes": "secret"}],
            },
            "research_book": {
                "items": [{"symbol": "NVDA", "risk_adjusted_expected_return": 20, "private_notes": "secret"}],
            },
            "outcome_diagnostics": {
                "current_training_example_count": 1,
                "raw": {"account": "U123"},
            },
            "backtest": {
                "trial_count": 1,
                "raw": {"account": "U123"},
                "outcomes": [{"symbol": "NVDA", "estimated_shares": 2, "decision_forward_return_pct": 10}],
            },
            "recommendation_training_examples": [{"symbol": "NVDA", "current_weight": 0.1}],
            "paper_portfolio": {
                "paper_trades": [{"symbol": "NVDA", "estimated_shares": 2, "target_weight": 0.11}],
            },
            "portfolio_valuation_private": {
                "current_value_total": 1000,
                "positions": [{"symbol": "NVDA", "current_value": 1000}],
            },
            "market_return_windows": {"NVDA": {"5d": 1.0, "3m": 5.0}},
            "approval_tickets": [
                {
                    "ticket_id": "abc",
                    "symbol": "NVDA",
                    "current_weight": 0.1,
                    "recommended_delta_weight": 0.01,
                    "target_weight": 0.11,
                    "estimated_notional": 1000,
                    "estimated_shares": 2,
                    "raw": {"account": "U123"},
                }
            ],
            "manager_radar": {
                "focus_managers": [
                    {
                        "manager_key": "altimeter",
                        "manager_name": "Altimeter",
                        "manager_tier": "tier_1",
                        "manager_group": "AI Thesis Core",
                        "total_common_value": 1000000,
                        "symbol_coverage_pct": 100,
                        "top_positions": [
                            {
                                "rank": 1,
                                "symbol": "NVDA",
                                "issuer": "NVIDIA CORP",
                                "bucket": "semis_networking_hbm",
                                "fund_weight": 1.0,
                                "portfolio_weight": 1.0,
                                "value": 1000000,
                                "entry_price_estimate": 100.0,
                                "current_price": 125.0,
                                "current_value_estimate": 1250000.0,
                                "valuation_confidence": "low",
                            }
                        ],
                        "positions": [
                            {
                                "rank": 1,
                                "symbol": "NVDA",
                                "issuer": "NVIDIA CORP",
                                "bucket": "semis_networking_hbm",
                                "fund_weight": 1.0,
                                "portfolio_weight": 1.0,
                                "value": 1000000,
                                "shares": 10,
                                "entry_price_estimate": 100.0,
                                "current_price": 125.0,
                                "current_value_estimate": 1250000.0,
                                "valuation_confidence": "low",
                            }
                        ],
                    },
                    {
                        "manager_key": "d1-capital",
                        "manager_name": "D1 Capital Partners L.P.",
                        "manager_tier": "tier_1",
                        "manager_group": "AI Thesis Core",
                        "top_positions": [
                            {
                                "rank": 1,
                                "symbol": "META",
                                "issuer": "META PLATFORMS INC",
                                "bucket": "frontier_ai_platforms",
                                "fund_weight": 1.0,
                                "portfolio_weight": 0.0,
                                "value": 1000000,
                            }
                        ],
                        "positions": [
                            {
                                "rank": 1,
                                "symbol": "META",
                                "issuer": "META PLATFORMS INC",
                                "bucket": "frontier_ai_platforms",
                                "fund_weight": 1.0,
                                "portfolio_weight": 0.0,
                                "value": 1000000,
                                "shares": 10,
                            }
                        ],
                    },
                ]
            },
        }

        public = sanitize_payload(payload)

        self.assertTrue(public["private_data_redacted"])
        self.assertEqual(public["positions"], {})
        self.assertEqual(public["transactions"], [])
        self.assertEqual(public["portfolio"]["value_basis"], "weights_only")
        self.assertEqual(public["portfolio"]["display_name"], "Geoffrey Woo Portfolio")
        self.assertEqual(public["portfolio"]["weight_basis"], "invested_equity_ex_cash")
        self.assertEqual(public["portfolio"]["by_symbol"][0]["weight"], 1.0)
        self.assertNotIn("market_value", public["portfolio"]["by_symbol"][0])
        self.assertNotIn("portfolio_value", public["decision_cards"][0])
        self.assertEqual(public["decision_cards"][0]["portfolio_weight"], 1.0)
        self.assertNotIn("total_common_value", public["manager_radar"]["focus_managers"][0])
        self.assertNotIn("value", public["manager_radar"]["focus_managers"][0]["top_positions"][0])
        self.assertEqual(public["manager_radar"]["focus_managers"][0]["positions"][0]["rank"], 1)
        self.assertNotIn("value", public["manager_radar"]["focus_managers"][0]["positions"][0])
        self.assertNotIn("shares", public["manager_radar"]["focus_managers"][0]["positions"][0])
        self.assertEqual(public["manager_radar"]["focus_manager_groups"][0]["key"], "tier_1")
        d1 = next(row for row in public["manager_radar"]["focus_managers"] if row["manager_key"] == "d1-capital")
        self.assertEqual(d1["manager_tier"], "tier_2")
        self.assertEqual(d1["manager_group"], "Manager Context Bench")
        self.assertNotIn("positions", d1)
        self.assertNotIn("D1", public["manager_radar"]["focus_manager_groups"][0]["description"])
        self.assertNotIn(
            "value",
            public["manager_radar"]["focus_manager_groups"][0]["managers"][0]["top_positions"][0],
        )
        self.assertEqual(public["product"]["domain"], "alloiq.com")
        self.assertEqual(public["anti_fund_growth"]["category"], "affiliated_private_fund")
        self.assertEqual(public["anti_fund_growth"]["marketing_url"], "https://antifund.com")
        self.assertIn("not a public-stock fund", public["anti_fund_growth"]["description"])
        self.assertEqual(public["anti_fund_growth"]["basis"], "mark_to_market_growth_book_weight")
        self.assertEqual(public["anti_fund_growth"]["basis_label"], "Mark-to-market weight")
        anti_positions = public["anti_fund_growth"]["positions"]
        self.assertAlmostEqual(sum(row["weight"] for row in anti_positions), 1.0, places=5)
        self.assertEqual(anti_positions[0]["company"], "OpenAI")
        self.assertAlmostEqual(anti_positions[0]["weight"], 0.402806, places=6)
        self.assertAlmostEqual(anti_positions[0]["cost_weight"], 0.367390, places=6)
        anthropic = next(row for row in anti_positions if row["company"] == "Anthropic")
        self.assertAlmostEqual(anthropic["weight"], 0.065421, places=6)
        self.assertAlmostEqual(anthropic["cost_weight"], 0.065421, places=6)
        self.assertEqual(anthropic["mark_basis"], "$900B private valuation model mark")
        anti_keys = set()

        def collect_keys(value):
            if isinstance(value, dict):
                anti_keys.update(value.keys())
                for child in value.values():
                    collect_keys(child)
            elif isinstance(value, list):
                for child in value:
                    collect_keys(child)

        collect_keys(public["anti_fund_growth"])
        self.assertFalse(
            {
                "account_details",
                "warehouse_details",
                "share_count",
                "shares",
                "pps",
                "fund_price_per_share",
                "original_price_per_share",
                "original_purchase_price",
                "fund_purchase_price",
                "mark_m",
                "cost_m",
                "market_value",
                "cost_basis",
            }
            & anti_keys
        )
        self.assertEqual(public["recommended_moves"][0]["action"], "Core position review")
        self.assertNotIn("estimated_notional", public["approval_tickets"][0])
        self.assertNotIn("estimated_shares", public["approval_tickets"][0])
        self.assertNotIn("raw", public["approval_tickets"][0])
        self.assertTrue(public["approval_tickets"][0]["approval_required"])
        self.assertTrue(public["methodology"]["updated_by_backend"])
        self.assertNotIn("estimated_notional", public["methodology"]["risk_and_sizing"])
        self.assertNotIn("estimated_notional", json.dumps(public["methodology"]))
        self.assertNotIn("estimated_shares", json.dumps(public["methodology"]))
        self.assertNotIn('"quantity"', json.dumps(public["methodology"]))
        self.assertNotIn("quantity", json.dumps(public["methodology"]))
        self.assertNotIn('"market_value"', json.dumps(public["methodology"]))
        self.assertEqual(public["data_health"]["sources"][0]["source"], "position_snapshot")
        self.assertNotIn("raw", json.dumps(public["audit"]))
        self.assertNotIn("account", json.dumps(public["calendars"]))
        self.assertNotIn("raw_json", json.dumps(public["engine"]))
        self.assertNotIn("private_notes", json.dumps(public["feature_matrix"]))
        self.assertNotIn("private_notes", json.dumps(public["research_book"]))
        self.assertNotIn("estimated_shares", json.dumps(public["backtest"]))
        self.assertNotIn("account", json.dumps(public["backtest"]))
        self.assertNotIn("recommendation_training_examples", public)
        self.assertNotIn("estimated_shares", json.dumps(public["paper_portfolio"]))
        self.assertNotIn("portfolio_valuation_private", public)
        self.assertNotIn("market_return_windows", public)
        self.assertEqual(
            public["manager_radar"]["focus_managers"][0]["top_positions"][0]["current_value_estimate"],
            1250000.0,
        )
        self.assertNotIn("shares", public["manager_radar"]["focus_managers"][0]["positions"][0])

    def test_public_portfolio_uses_ex_cash_weights(self):
        public = sanitize_payload(
            {
                "portfolio": {
                    "cash_weight": 0.2,
                    "equity_weight": 0.8,
                    "weight_basis": "total_portfolio_including_cash",
                    "comparison_weight_basis": "invested_equity_ex_cash",
                    "by_bucket": [
                        {"bucket": "semis_networking_hbm", "weight": 0.8, "ex_cash_weight": 1.0},
                        {"bucket": "cash_reserves", "weight": 0.2, "ex_cash_weight": 0.0},
                    ],
                    "by_symbol": [
                        {"symbol": "NVDA", "bucket": "semis_networking_hbm", "weight": 0.8, "ex_cash_weight": 1.0},
                        {"symbol": "CASH", "bucket": "cash_reserves", "asset_class": "cash", "is_cash": True, "weight": 0.2},
                    ],
                },
                "manager_radar": {},
                "portfolio_benchmark": {},
                "decision_cards": [{"symbol": "NVDA", "candidate": "research", "score": 50}],
                "macro": {},
                "ideas": [],
            }
        )

        self.assertEqual(public["portfolio"]["weight_basis"], "invested_equity_ex_cash")
        self.assertEqual(public["portfolio"]["cash_weight"], 0.2)
        self.assertEqual(public["portfolio"]["by_symbol"], [{"symbol": "NVDA", "bucket": "semis_networking_hbm", "asset_class": "equity", "is_cash": False, "weight": 1.0, "total_weight": 0.8}])
        self.assertEqual(public["portfolio"]["by_bucket"], [{"bucket": "semis_networking_hbm", "weight": 1.0, "total_weight": 0.8}])
        self.assertEqual(public["decision_cards"][0]["portfolio_weight"], 1.0)

    def test_public_portfolio_derives_ex_cash_weights_when_source_has_total_weights_only(self):
        public = sanitize_payload(
            {
                "portfolio": {
                    "cash_weight": 0.2,
                    "equity_weight": 0.8,
                    "weight_basis": "total_portfolio_including_cash",
                    "by_bucket": [
                        {"bucket": "semis_networking_hbm", "weight": 0.4},
                        {"bucket": "frontier_ai_platforms", "weight": 0.4},
                        {"bucket": "cash_reserves", "weight": 0.2},
                    ],
                    "by_symbol": [
                        {"symbol": "NVDA", "bucket": "semis_networking_hbm", "weight": 0.4},
                        {"symbol": "GOOG", "bucket": "frontier_ai_platforms", "weight": 0.4},
                        {"symbol": "CASH", "bucket": "cash_reserves", "asset_class": "cash", "is_cash": True, "weight": 0.2},
                    ],
                },
                "manager_radar": {},
                "portfolio_benchmark": {},
                "decision_cards": [
                    {"symbol": "NVDA", "candidate": "research", "score": 50},
                    {"symbol": "GOOG", "candidate": "research", "score": 45},
                ],
                "macro": {},
                "ideas": [],
            }
        )

        self.assertEqual(sum(row["weight"] for row in public["portfolio"]["by_symbol"]), 1.0)
        self.assertEqual(public["portfolio"]["by_symbol"][0]["weight"], 0.5)
        self.assertEqual(public["portfolio"]["by_symbol"][0]["total_weight"], 0.4)
        self.assertEqual(public["portfolio"]["by_bucket"][1]["weight"], 0.5)
        self.assertEqual(public["decision_cards"][0]["portfolio_weight"], 0.5)

    def test_public_moves_call_out_put_heavy_names(self):
        moves = build_public_moves(
            [
                {
                    "symbol": "AMD",
                    "score": 40,
                    "bucket": "semis_networking_hbm",
                    "portfolio_weight": 0,
                    "consensus_manager_count": 3,
                    "news_count": 0,
                    "put_value": 100000000,
                    "call_value": 0,
                    "counterargument": "Risk.",
                    "falsifier": "Wrong.",
                }
            ],
            {"regime": "risk-on AI acceleration"},
        )

        self.assertEqual(moves[0]["action"], "Risk watch")
        self.assertEqual(moves[0]["posture"], "Cautious")

    def test_public_moves_use_portfolio_weights_for_owned_risk_budget(self):
        moves = build_public_moves(
            [
                {
                    "symbol": "NVDA",
                    "score": 40,
                    "bucket": "semis_networking_hbm",
                    "portfolio_weight": 0.05,
                    "consensus_manager_count": 3,
                    "news_count": 0,
                    "put_value": 100000000,
                    "call_value": 0,
                    "counterargument": "Risk.",
                    "falsifier": "Wrong.",
                }
            ],
            {"regime": "risk-on AI acceleration"},
            {"by_bucket": [{"bucket": "semis_networking_hbm", "weight": 0.3}]},
        )

        self.assertEqual(moves[0]["action"], "Hold with risk budget")
        self.assertEqual(moves[0]["portfolio_weight"], 0.05)

    def test_public_payload_sanitizes_weekly_research(self):
        payload = {
            "portfolio": {},
            "manager_radar": {},
            "portfolio_benchmark": {},
            "decision_cards": [],
            "macro": {},
            "ideas": [],
            "weekly_research": {
                "title": "Weekly",
                "market_value": 1000,
                "ideas": [
                    {
                        "symbol": "NVDA",
                        "setup": "Study.",
                        "portfolio_value": 1000,
                        "quantity": 12,
                        "broker": "ibkr",
                        "portfolio_weight": 0.1,
                    }
                ],
            },
        }

        public = sanitize_payload(payload)

        self.assertNotIn("market_value", public["weekly_research"])
        self.assertNotIn("portfolio_value", public["weekly_research"]["ideas"][0])
        self.assertNotIn("quantity", public["weekly_research"]["ideas"][0])
        self.assertNotIn("broker", public["weekly_research"]["ideas"][0])
        self.assertEqual(public["weekly_research"]["ideas"][0]["portfolio_weight"], 0.1)

    def test_stale_status_marks_daily_report_as_stale_when_as_of_lags_market_date(self):
        status = stale_status("premarket", "2026-05-26T13:00:00Z", "2026-05-24")

        self.assertTrue(status["is_stale_at_build"])
        self.assertEqual(status["status"], "stale")
        self.assertEqual(status["report_age_days"], 2)
        self.assertEqual(status["expected_report_as_of"], "2026-05-26")
        self.assertEqual(status["max_report_age_days"], 0)
        self.assertIn("report_as_of 2026-05-24 is before expected report date 2026-05-26", status["reason"])

    def test_stale_status_allows_daily_report_on_market_holiday(self):
        status = stale_status("premarket", "2026-05-25T13:00:00Z", "2026-05-24")

        self.assertFalse(status["is_stale_at_build"])
        self.assertEqual(status["status"], "fresh")
        self.assertEqual(status["market_date_at_build"], "2026-05-25")
        self.assertEqual(status["expected_report_as_of"], "2026-05-22")
        self.assertEqual(status["report_lag_days"], 0)

    def test_stale_status_uses_eastern_market_date_for_utc_builds(self):
        status = stale_status("postmarket", "2026-05-25T01:00:00Z", "2026-05-24")

        self.assertFalse(status["is_stale_at_build"])
        self.assertEqual(status["status"], "fresh")
        self.assertEqual(status["market_date_at_build"], "2026-05-24")
        self.assertEqual(status["expected_report_as_of"], "2026-05-22")
        self.assertEqual(status["report_age_days"], 0)

    def test_intraday_stale_status_uses_short_freshness_window(self):
        status = stale_status("intraday", "2026-05-26T17:00:00Z", "2026-05-26")

        self.assertEqual(status["max_age_hours"], 2)

    def test_public_payload_adds_default_methodology(self):
        public = sanitize_payload(
            {
                "portfolio": {"position_count": 0, "by_bucket": [], "by_symbol": []},
                "manager_radar": {},
                "portfolio_benchmark": {"action_queue": []},
                "signal_synthesis": {"confirmed_card_count": 2},
                "decision_cards": [],
                "macro": {},
                "ideas": [],
            }
        )

        self.assertEqual(public["methodology"]["current_run"]["confirmed_card_count"], 2)
        self.assertEqual(public["methodology"]["risk_and_sizing"]["order_execution"], "none")
        self.assertEqual(public["methodology"]["public_privacy"]["mode"], "weights_only")
        self.assertEqual(public["engine"]["objective"], "maximize_expected_3_12m_forward_return")
        self.assertEqual(public["paper_portfolio"]["live_order_execution"], "disabled")
        self.assertEqual(public["calendars"]["earnings"]["source_quality"], "limited")
        self.assertIn("engine_health", public["audit"])

    def test_public_payload_backfills_learning_readiness_projection_from_backtest(self):
        public = sanitize_payload(
            {
                "as_of": "2026-05-24",
                "session": "postmarket",
                "portfolio": {"position_count": 1, "by_symbol": [{"symbol": "NVDA", "weight": 1.0}]},
                "feature_matrix": {"rows": []},
                "research_book": {"items": []},
                "portfolio_benchmark": {
                    "action_queue": [
                        {
                            "symbol": "MRVL",
                            "trade_action": "trim",
                            "recommended_delta_weight": -0.01,
                            "risk_flags": ["earnings_confirmation_required"],
                            "earnings_confirmation_required": True,
                        }
                    ]
                },
                "approval_tickets": [],
                "engine": {
                    "feature_count": 0,
                    "ranked_candidates": [],
                    "learning": {
                        "status": "baseline_fallback",
                        "minimum_required": 20,
                        "message": "Insufficient completed outcomes.",
                    },
                },
                "outcome_diagnostics": {
                    "label_maturity": {
                        "completed_long_horizon_count": 18,
                        "minimum_long_horizon_required": 20,
                        "additional_long_horizon_needed": 2,
                    },
                },
                "backtest": {
                    "outcome_count": 3,
                    "completed_outcome_count": 0,
                    "pending_outcome_count": 3,
                    "missing_price_count": 0,
                    "outcomes": [
                        {"symbol": "NVDA", "horizon": "1m", "status": "pending", "due_date": "2026-06-24"},
                        {"symbol": "AMD", "horizon": "1m", "status": "pending", "due_date": "2026-06-24"},
                        {"symbol": "AVGO", "horizon": "3m", "status": "pending", "due_date": "2026-08-24"},
                    ],
                },
                "data_health": {"sources": []},
                "audit": {
                    "overall_status": "attention",
                    "data_gaps": [
                        {
                            "area": "engine",
                            "label": "Learning reranker",
                            "status": "baseline_fallback",
                            "detail": "Old generic detail.",
                        }
                    ],
                },
                "manager_radar": {},
                "decision_cards": [],
                "macro": {},
                "ideas": [],
            }
        )

        diagnostics = public["outcome_diagnostics"]
        self.assertEqual(diagnostics["pending_label_schedule"]["pending_learning_label_count"], 3)
        self.assertEqual(diagnostics["pending_label_schedule"]["next_learning_label_due_date"], "2026-06-24")
        self.assertEqual(diagnostics["learning_readiness_projection"]["next_learning_label_due_count"], 2)
        self.assertTrue(diagnostics["learning_readiness_projection"]["learning_ready_after_next_learning_label"])
        self.assertEqual(diagnostics["learning_readiness_projection"]["estimated_learning_ready_date"], "2026-06-24")
        learning_gap = next(row for row in public["audit"]["data_gaps"] if row["label"] == "Learning reranker")
        self.assertNotIn("Old generic detail", learning_gap["detail"])
        self.assertIn("Next learning-label projection: 20/20 labels after 2026-06-24", learning_gap["detail"])
        self.assertIn("Estimated learning-ready date: 2026-06-24", learning_gap["detail"])

    def test_public_payload_backfills_label_maturity_from_backtest(self):
        public = sanitize_payload(
            {
                "as_of": "2026-05-24",
                "session": "postmarket",
                "portfolio": {"position_count": 1, "by_symbol": [{"symbol": "NVDA", "weight": 1.0}]},
                "feature_matrix": {"rows": []},
                "research_book": {"items": []},
                "portfolio_benchmark": {"action_queue": []},
                "approval_tickets": [],
                "engine": {
                    "feature_count": 0,
                    "ranked_candidates": [],
                    "learning": {
                        "status": "baseline_fallback",
                        "minimum_required": 20,
                        "message": "Insufficient completed outcomes.",
                    },
                },
                "outcome_diagnostics": {},
                "backtest": {
                    "outcome_count": 2,
                    "completed_outcome_count": 0,
                    "pending_outcome_count": 2,
                    "missing_price_count": 0,
                    "outcomes": [
                        {
                            "symbol": "NVDA",
                            "horizon": "1m",
                            "status": "pending",
                            "as_of": "2026-05-23",
                            "due_date": "2026-06-23",
                            "trade_action": "add",
                            "direction": 1,
                            "coverage_adjusted_external_signal_score": 5.0,
                            "external_feed_status": "limited",
                            "external_coverage_multiplier": 0.25,
                        },
                        {"symbol": "AMD", "horizon": "3m", "status": "pending", "as_of": "2026-05-23", "due_date": "2026-08-23"},
                    ],
                },
                "data_health": {"sources": []},
                "audit": {"overall_status": "attention", "data_gaps": []},
                "manager_radar": {},
                "decision_cards": [],
                "macro": {},
                "ideas": [],
            }
        )

        diagnostics = public["outcome_diagnostics"]
        self.assertEqual(diagnostics["label_maturity"]["completed_long_horizon_count"], 0)
        self.assertEqual(diagnostics["label_maturity"]["pending_outcome_count"], 2)
        self.assertEqual(
            diagnostics["horizon_label_counts"],
            [
                {"horizon": "1m", "completed_count": 0, "pending_count": 1, "missing_price_count": 0},
                {"horizon": "3m", "completed_count": 0, "pending_count": 1, "missing_price_count": 0},
            ],
        )
        self.assertEqual(diagnostics["pending_label_schedule"]["next_learning_label_due_date"], "2026-06-25")
        self.assertEqual(diagnostics["learning_readiness_projection"]["next_learning_label_due_count"], 1)
        external_projection = diagnostics["external_learning_readiness_projection"]
        self.assertEqual(external_projection["pending_external_learning_label_count"], 1)
        self.assertEqual(external_projection["next_external_learning_label_due_date"], "2026-06-25")
        self.assertEqual(external_projection["projected_external_long_horizon_count_all_scheduled"], 1)
        self.assertEqual(external_projection["projected_external_additional_needed_all_scheduled"], 19)
        self.assertFalse(external_projection["external_learning_ready_with_scheduled_pending_labels"])
        pending_status = {row["key"]: row for row in public["backtest"]["pending_by_external_feed_status"]}
        self.assertEqual(pending_status["limited"]["pending_count"], 1)
        self.assertEqual(pending_status["unknown"]["pending_count"], 1)
        pending_coverage = {row["key"]: row for row in public["backtest"]["pending_by_external_coverage"]}
        self.assertEqual(pending_coverage["thin_coverage"]["pending_count"], 1)
        self.assertEqual(pending_coverage["unknown"]["pending_count"], 1)
        self.assertEqual(public["backtest"]["pending_external_coverage_gap_count"], 1)
        self.assertEqual(public["backtest"]["pending_external_coverage_gap_queue"][0]["symbol"], "AMD")
        self.assertTrue(public["backtest"]["pending_external_coverage_gap_queue"][0]["external_coverage_gap_id"])
        self.assertIn("no external observation", public["backtest"]["pending_external_coverage_gap_queue"][0]["external_coverage_gap_reason"])
        self.assertEqual(public["backtest"]["pending_external_coverage_gap_queue"][0]["external_coverage_backfill_policy"], "decision_time_only")
        self.assertIn("do not use later", public["backtest"]["pending_external_coverage_gap_queue"][0]["external_coverage_gap_action"])
        self.assertEqual(
            public["backtest"]["pending_external_coverage_gap_queue"][0]["external_coverage_acceptance_checks"][0]["check"],
            "external_feed_status_present",
        )
        self.assertEqual(public["backtest"]["pending_external_coverage_gap_plan"]["candidate_gap_count"], 1)
        self.assertEqual(public["backtest"]["pending_external_coverage_gap_plan"]["priority_gap_count"], 1)
        self.assertEqual(public["backtest"]["pending_external_coverage_gap_plan"]["priority_acceptance_check_count"], 4)
        self.assertEqual(public["backtest"]["pending_external_coverage_gap_plan"]["priority_open_acceptance_check_count"], 4)
        self.assertEqual(public["backtest"]["pending_external_coverage_gap_plan"]["priority_symbols"], ["AMD"])
        self.assertEqual(public["backtest"]["pending_external_coverage_gap_plan"]["projected_external_long_horizon_count_after_priority_backfill"], 2)
        self.assertEqual(public["backtest"]["pending_external_coverage_gap_plan"]["projected_external_additional_needed_after_priority_backfill"], 18)
        self.assertEqual(
            public["backtest"]["pending_external_coverage_gap_plan"]["priority_rows"][0]["required_external_observation_date"],
            "2026-05-23",
        )
        learning_gap = next(row for row in public["audit"]["data_gaps"] if row["label"] == "Learning reranker")
        self.assertIn("External coverage priority backfill", learning_gap["detail"])
        self.assertIn(public["backtest"]["pending_external_coverage_gap_plan"]["priority_rows"][0]["external_coverage_gap_id"], learning_gap["detail"])
        pending_alignment = {row["key"]: row for row in public["backtest"]["pending_by_external_alignment"]}
        self.assertEqual(pending_alignment["aligned"]["pending_count"], 1)
        self.assertEqual(pending_alignment["unknown"]["pending_count"], 1)
        self.assertEqual(public["backtest"]["pending_external_alignment_due_dates"][0]["due_count"], 1)
        self.assertEqual(public["backtest"]["pending_external_alignment_due_dates"][0]["aligned_count"], 1)
        self.assertEqual(public["backtest"]["pending_external_alignment_watchlist"][0]["symbol"], "NVDA")
        self.assertEqual(public["backtest"]["pending_external_alignment_watchlist"][0]["external_alignment"], "aligned")
        self.assertIn("confirmation sample", public["backtest"]["pending_external_alignment_watchlist"][0]["external_alignment_review_reason"])

    def test_public_payload_backfills_pending_external_alignment_review_queue(self):
        public = sanitize_payload(
            {
                "as_of": "2026-05-23",
                "session": "postmarket",
                "portfolio": {"position_count": 1, "by_symbol": [{"symbol": "NVDA", "weight": 1.0}]},
                "feature_matrix": {"rows": []},
                "research_book": {"items": []},
                "portfolio_benchmark": {"action_queue": []},
                "approval_tickets": [],
                "engine": {"feature_count": 0, "ranked_candidates": []},
                "outcome_diagnostics": {},
                "backtest": {
                    "outcome_count": 5,
                    "completed_outcome_count": 0,
                    "pending_outcome_count": 5,
                    "missing_price_count": 0,
                    "outcomes": [
                        {
                            "symbol": "GOOG",
                            "horizon": "5d",
                            "status": "pending",
                            "as_of": "2026-05-23",
                            "due_date": "2026-06-02",
                            "outcome_id": "outcome-goog-conflict-a",
                            "trial_id": "trial-goog-conflict-a",
                            "session": "postmarket",
                            "trade_action": "trim",
                            "recommended_delta_weight": -0.01,
                            "coverage_adjusted_external_signal_score": 4.0,
                        },
                        {
                            "symbol": "GOOG",
                            "horizon": "5d",
                            "status": "pending",
                            "as_of": "2026-05-23",
                            "due_date": "2026-06-02",
                            "trade_action": "trim",
                            "recommended_delta_weight": -0.01,
                            "coverage_adjusted_external_signal_score": 5.0,
                        },
                        {
                            "symbol": "NVDA",
                            "horizon": "5d",
                            "status": "pending",
                            "as_of": "2026-05-23",
                            "due_date": "2026-06-02",
                            "outcome_id": "outcome-nvda-engine",
                            "trial_id": "trial-nvda-engine",
                            "session": "postmarket",
                            "trade_action": "watch",
                            "recommended_delta_weight": 0,
                            "risk_adjusted_expected_return": 30,
                            "coverage_adjusted_external_signal_score": 6.0,
                        },
                        {
                            "symbol": "AVGO",
                            "horizon": "5d",
                            "status": "pending",
                            "as_of": "2026-05-23",
                            "due_date": "2026-06-02",
                            "trade_action": "add",
                            "recommended_delta_weight": 0.01,
                            "coverage_adjusted_external_signal_score": 5.0,
                        },
                        {
                            "symbol": "AMD",
                            "horizon": "5d",
                            "status": "pending",
                            "as_of": "2026-05-23",
                            "due_date": "2026-06-02",
                            "trade_action": "add",
                            "recommended_delta_weight": 0.01,
                        },
                    ],
                },
                "data_health": {"sources": []},
                "audit": {"overall_status": "attention", "data_gaps": []},
                "manager_radar": {},
                "decision_cards": [],
                "macro": {},
                "ideas": [],
            }
        )

        self.assertEqual(public["backtest"]["pending_external_alignment_review_count"], 3)
        self.assertEqual(public["backtest"]["pending_external_alignment_review_item_count"], 2)
        self.assertEqual(public["backtest"]["pending_external_alignment_review_queue_limit"], 12)
        self.assertEqual(public["backtest"]["pending_external_alignment_review_hidden_item_count"], 0)
        acceptance = public["backtest"]["pending_external_alignment_review_acceptance_summary"]
        self.assertEqual(acceptance["label_count"], 3)
        self.assertEqual(acceptance["work_item_count"], 2)
        self.assertEqual(acceptance["check_count"], 8)
        self.assertEqual(acceptance["open_check_count"], 2)
        self.assertEqual(acceptance["open_label_count"], 3)
        self.assertEqual(acceptance["open_check_counts"], {"matured_label_available": 2})
        self.assertEqual(acceptance["metadata_ready_work_item_count"], 2)
        self.assertEqual(acceptance["next_open_check_due_date"], "2026-06-02")
        self.assertEqual(acceptance["next_open_check_due_open_check_count"], 2)
        self.assertEqual(acceptance["next_open_check_due_label_count"], 3)
        self.assertEqual(acceptance["next_open_check_due_work_item_count"], 2)
        self.assertEqual(acceptance["next_open_check_due_visible_work_item_count"], 2)
        self.assertEqual(acceptance["next_open_check_due_hidden_work_item_count"], 0)
        self.assertTrue(acceptance["next_open_check_due_fully_visible"])
        self.assertEqual(acceptance["next_open_check_due_symbols"], ["GOOG", "NVDA"])
        self.assertEqual(acceptance["next_open_check_due_horizons"], ["5d"])
        conflict_action = "When the label matures, compare realized direction with the external signal before changing external-signal trust."
        missed_action = "When the label matures, test whether the external signal should have promoted a directional size or timing change."
        self.assertEqual(
            acceptance["next_open_check_due_focus_counts"],
            {
                "external_disagreement": {"label_count": 2, "work_item_count": 1},
                "missed_external_signal": {"label_count": 1, "work_item_count": 1},
            },
        )
        self.assertEqual(
            acceptance["next_open_check_due_learning_action_counts"],
            {
                conflict_action: {"label_count": 2, "work_item_count": 1},
                missed_action: {"label_count": 1, "work_item_count": 1},
            },
        )
        self.assertEqual(
            acceptance["next_open_check_due_measurement_missing_field_counts"],
            {"risk_adjusted_expected_return": {"label_count": 2, "work_item_count": 1}},
        )
        self.assertEqual(
            acceptance["open_check_due_dates"],
            [
                {
                    "due_date": "2026-06-02",
                    "open_check_count": 2,
                    "label_count": 3,
                    "work_item_count": 2,
                    "symbols": ["GOOG", "NVDA"],
                    "horizons": ["5d"],
                    "focus_counts": {
                        "external_disagreement": {"label_count": 2, "work_item_count": 1},
                        "missed_external_signal": {"label_count": 1, "work_item_count": 1},
                    },
                    "learning_action_counts": {
                        conflict_action: {"label_count": 2, "work_item_count": 1},
                        missed_action: {"label_count": 1, "work_item_count": 1},
                    },
                    "measurement_missing_field_counts": {
                        "risk_adjusted_expected_return": {"label_count": 2, "work_item_count": 1},
                    },
                    "check_counts": {"matured_label_available": 2},
                }
            ],
        )
        due_dates = public["backtest"]["pending_external_alignment_review_due_dates"]
        self.assertEqual(due_dates[0]["label_count"], 3)
        self.assertEqual(due_dates[0]["work_item_count"], 2)
        self.assertEqual(due_dates[0]["focus_counts"]["external_disagreement"], {"label_count": 2, "work_item_count": 1})
        self.assertEqual(due_dates[0]["focus_counts"]["missed_external_signal"], {"label_count": 1, "work_item_count": 1})
        self.assertEqual(public["backtest"]["pending_external_alignment_measurement_gap_label_count"], 2)
        self.assertEqual(public["backtest"]["pending_external_alignment_measurement_gap_item_count"], 1)
        self.assertEqual(public["backtest"]["pending_external_alignment_measurement_gap_hidden_item_count"], 0)
        gap_plan = public["backtest"]["pending_external_alignment_measurement_gap_plan"]
        self.assertEqual(gap_plan["next_due_date"], "2026-06-02")
        self.assertEqual(
            gap_plan["next_due_field_counts"],
            {"risk_adjusted_expected_return": {"label_count": 2, "work_item_count": 1}},
        )
        gap_queue = public["backtest"]["pending_external_alignment_measurement_gap_queue"]
        self.assertEqual([row["symbol"] for row in gap_queue], ["GOOG"])
        self.assertTrue(gap_queue[0]["external_alignment_measurement_gap_id"])
        self.assertEqual(gap_queue[0]["external_alignment_measurement_missing_fields"], ["risk_adjusted_expected_return"])
        self.assertEqual(gap_queue[0]["external_alignment_measurement_missing_label_count"], 2)
        self.assertIn("do not use later", gap_queue[0]["external_alignment_measurement_gap_action"])
        self.assertEqual(gap_queue[0]["external_alignment_measurement_backfill_policy"], "decision_time_only")
        queue = public["backtest"]["pending_external_alignment_review_queue"]
        self.assertEqual([row["symbol"] for row in queue], ["GOOG", "NVDA"])
        self.assertTrue(queue[0]["external_alignment_review_id"])
        self.assertNotEqual(queue[0]["external_alignment_review_id"], queue[1]["external_alignment_review_id"])
        self.assertEqual(queue[0]["external_alignment_review_version"], "2026-05-external-alignment-review-v1")
        self.assertEqual(queue[0]["external_alignment"], "conflict")
        self.assertEqual(queue[0]["external_alignment_review_label_count"], 2)
        self.assertEqual(queue[0]["external_alignment_review_focus"], "external_disagreement")
        self.assertIn("external disagreement", queue[0]["external_alignment_review_priority_reason"])
        self.assertIn("compare realized direction", queue[0]["external_alignment_review_learning_action"])
        self.assertEqual(queue[0]["external_alignment_review_measurement_plan"]["engine_direction"], "negative")
        self.assertEqual(queue[0]["external_alignment_review_measurement_plan"]["external_signal_direction"], "positive")
        self.assertEqual(queue[0]["external_alignment_review_measurement_plan"]["coverage_adjusted_external_signal_score"], 4.0)
        self.assertIn("risk_adjusted_expected_return", queue[0]["external_alignment_review_measurement_plan"]["missing_measurement_fields"])
        self.assertIn("expected missing", queue[0]["external_alignment_review_measurement_plan"]["summary"])
        self.assertEqual(queue[0]["external_alignment_review_open_check_count"], 1)
        self.assertEqual(
            [check["status"] for check in queue[0]["external_alignment_review_acceptance_checks"]],
            ["passed", "passed", "passed", "pending"],
        )
        self.assertEqual(queue[0]["source_outcome_id"], "outcome-goog-conflict-a")
        self.assertEqual(queue[0]["source_trial_id"], "trial-goog-conflict-a")
        self.assertEqual(queue[0]["session"], "postmarket")
        self.assertIn("disagrees", queue[0]["external_alignment_review_reason"])
        self.assertEqual(queue[1]["external_alignment_review_focus"], "missed_external_signal")
        self.assertEqual(queue[1]["source_outcome_id"], "outcome-nvda-engine")
        self.assertEqual(queue[1]["source_trial_id"], "trial-nvda-engine")

    def test_public_payload_refreshes_stale_external_coverage_gap_plan(self):
        public = sanitize_payload(
            {
                "as_of": "2026-05-24",
                "session": "postmarket",
                "portfolio": {"position_count": 1, "by_symbol": [{"symbol": "NVDA", "weight": 1.0}]},
                "feature_matrix": {"rows": []},
                "research_book": {"items": []},
                "portfolio_benchmark": {"action_queue": []},
                "approval_tickets": [],
                "engine": {"feature_count": 0, "ranked_candidates": []},
                "outcome_diagnostics": {},
                "backtest": {
                    "outcome_count": 1,
                    "completed_outcome_count": 0,
                    "pending_outcome_count": 1,
                    "missing_price_count": 0,
                    "pending_external_coverage_gap_count": 1,
                    "pending_external_coverage_gap_queue": [{"symbol": "NVDA"}],
                    "pending_external_coverage_gap_plan": {
                        "candidate_gap_count": 1,
                        "priority_gap_count": 1,
                        "residual_gap_count": 0,
                    },
                    "outcomes": [
                        {
                            "symbol": "NVDA",
                            "horizon": "1m",
                            "status": "pending",
                            "as_of": "2026-05-23",
                            "due_date": "2026-06-23",
                            "trade_action": "add",
                            "direction": 1,
                            "coverage_adjusted_external_signal_score": 5.0,
                            "external_signal_score": 20.0,
                            "external_feed_status": "limited",
                            "external_coverage_multiplier": 0.25,
                            "external_signal_count": 2,
                            "external_source_count": 1,
                        }
                    ],
                },
                "data_health": {"sources": []},
                "audit": {"overall_status": "attention", "data_gaps": []},
                "manager_radar": {},
                "decision_cards": [],
                "macro": {},
                "ideas": [],
            }
        )

        self.assertEqual(public["backtest"]["pending_external_coverage_gap_count"], 0)
        self.assertEqual(public["backtest"]["pending_external_coverage_gap_queue"], [])
        self.assertEqual(public["backtest"]["pending_external_coverage_gap_plan"]["candidate_gap_count"], 0)
        self.assertEqual(public["backtest"]["pending_external_coverage_gap_plan"]["priority_gap_count"], 0)
        self.assertEqual(public["backtest"]["pending_external_coverage_gap_plan"]["residual_gap_count"], 0)

    def test_public_payload_recomputes_earnings_health_date_counts(self):
        public = sanitize_payload(
            {
                "as_of": "2026-05-24",
                "session": "postmarket",
                "portfolio": {"position_count": 1, "by_symbol": [{"symbol": "NVDA", "weight": 1.0}]},
                "feature_matrix": {"rows": []},
                "research_book": {"items": []},
                "portfolio_benchmark": {
                    "action_queue": [
                        {
                            "symbol": "MRVL",
                            "trade_action": "trim",
                            "recommended_delta_weight": -0.01,
                            "risk_flags": ["earnings_confirmation_required"],
                            "earnings_confirmation_required": True,
                        }
                    ]
                },
                "approval_tickets": [
                    {
                        "ticket_id": "ticket-mrvl",
                        "symbol": "MRVL",
                        "trade_action": "trim",
                        "recommended_delta_weight": -0.01,
                        "earnings_confirmation_required": True,
                        "risk_flags": ["earnings_confirmation_required"],
                        "approval_gate_status": "blocked_until_confirmation",
                        "approval_open_check_count": 2,
                        "approval_checks": [
                            {"check": "approval_only_no_live_order", "status": "passed", "detail": "No live order."},
                            {"check": "earnings_date_confirmed", "status": "pending", "detail": "Confirm date."},
                            {"check": "risk_flags_reviewed", "status": "pending", "detail": "Review flags."},
                        ],
                    }
                ],
                "engine": {"feature_count": 0, "ranked_candidates": []},
                "outcome_diagnostics": {},
                "backtest": {"outcome_count": 0, "completed_outcome_count": 0, "pending_outcome_count": 0, "missing_price_count": 0, "outcomes": []},
                "earnings_events": [
                    {
                        "symbol": "MRVL",
                        "event_date": "2026-05-27",
                        "days_until": 3,
                        "event_type": "earnings",
                        "source": "nasdaq_earnings_calendar",
                        "confirmed_or_estimated": "estimated",
                    },
                    {
                        "symbol": "NVDA",
                        "event_type": "earnings_catalyst",
                        "source": "news",
                        "confirmed_or_estimated": "estimated",
                    },
                ],
                "calendars": {
                    "earnings": {
                        "event_count": 2,
                        "confirmed_count": 0,
                        "estimated_count": 2,
                        "source_quality": "estimated",
                        "events": [],
                    }
                },
                "data_health": {
                    "sources": [
                        {
                            "source": "earnings",
                            "label": "Earnings calendar",
                            "status": "estimated",
                            "detail": "2 events; 2 forward date candidates; 0 confirmed, 2 estimated.",
                        }
                    ]
                },
                "audit": {"overall_status": "attention", "data_gaps": []},
                "manager_radar": {},
                "decision_cards": [],
                "macro": {},
                "ideas": [],
            }
        )

        earnings = public["calendars"]["earnings"]
        self.assertEqual(earnings["provider_date_count"], 1)
        self.assertEqual(earnings["estimated_count"], 1)
        self.assertEqual(earnings["catalyst_marker_count"], 1)
        self.assertEqual(earnings["confirmation_gap_count"], 1)
        self.assertEqual(earnings["action_linked_confirmation_gap_count"], 1)
        self.assertEqual(earnings["confirmation_gaps"][0]["symbol"], "MRVL")
        self.assertTrue(earnings["confirmation_gaps"][0]["action_linked"])
        self.assertEqual(earnings["confirmation_gaps"][0]["trade_action"], "trim")
        self.assertEqual(earnings["confirmation_gaps"][0]["recommended_delta_weight"], -0.01)
        self.assertTrue(earnings["confirmation_gaps"][0]["action_confirmation_required"])
        self.assertTrue(earnings["confirmation_gaps"][0]["approval_ticket_linked"])
        self.assertEqual(earnings["confirmation_gaps"][0]["ticket_id"], "ticket-mrvl")
        self.assertEqual(earnings["confirmation_gaps"][0]["approval_gate_status"], "blocked_until_confirmation")
        self.assertEqual(earnings["confirmation_gaps"][0]["approval_open_check_count"], 2)
        self.assertEqual(
            earnings["confirmation_gaps"][0]["approval_blocking_checks"],
            ["earnings_date_confirmed", "risk_flags_reviewed"],
        )
        self.assertEqual(earnings["approval_blocked_confirmation_gap_count"], 1)
        self.assertEqual(earnings["approval_blocked_confirmation_gaps"][0]["ticket_id"], "ticket-mrvl")
        self.assertIn("current trim ticket", earnings["confirmation_gaps"][0]["remediation"])
        source = next(row for row in public["data_health"]["sources"] if row["source"] == "earnings")
        self.assertIn("1 forward date candidates; 0 confirmed, 1 estimated; 1 catalyst markers", source["detail"])
        self.assertEqual(source["confirmation_gap_count"], 1)
        self.assertEqual(source["action_linked_confirmation_gap_count"], 1)
        self.assertEqual(source["approval_blocked_confirmation_gap_count"], 1)
        self.assertEqual(source["confirmation_gaps"][0]["symbol"], "MRVL")
        self.assertTrue(source["confirmation_gaps"][0]["action_linked"])
        self.assertEqual(source["approval_blocked_confirmation_gaps"][0]["approval_gate_status"], "blocked_until_confirmation")
        summary = public["data_health"]["approval_blocker_summary"]
        self.assertEqual(summary["status"], "attention")
        self.assertEqual(summary["total_source_blocker_count"], 1)
        self.assertEqual(summary["earnings_confirmation_ticket_count"], 1)
        self.assertEqual(summary["external_gap_ticket_count"], 0)
        self.assertEqual(summary["blocked_ticket_count"], 1)
        self.assertEqual(summary["blocked_symbols"], ["MRVL"])
        self.assertEqual(summary["open_check_counts"], {"earnings_date_confirmed": 1, "risk_flags_reviewed": 1})
        self.assertEqual(
            summary["next_confirmation_deadline"],
            source["approval_blocked_confirmation_gaps"][0]["confirmation_deadline"],
        )
        self.assertEqual(summary["next_confirmation_symbols"], ["MRVL"])
        gap = next(row for row in public["audit"]["data_gaps"] if row["label"] == "Earnings calendar")
        self.assertIn("1 catalyst markers", gap["detail"])
        self.assertEqual(gap["confirmation_gap_count"], 1)
        self.assertEqual(gap["action_linked_confirmation_gap_count"], 1)
        self.assertEqual(gap["approval_blocked_confirmation_gap_count"], 1)

    def test_public_payload_recomputes_stale_backtest_due_dates_from_trading_calendar(self):
        public = sanitize_payload(
            {
                "as_of": "2026-05-24",
                "session": "postmarket",
                "portfolio": {"position_count": 1, "by_symbol": [{"symbol": "NVDA", "weight": 1.0}]},
                "feature_matrix": {"rows": []},
                "research_book": {"items": []},
                "portfolio_benchmark": {"action_queue": []},
                "approval_tickets": [],
                "engine": {
                    "feature_count": 0,
                    "ranked_candidates": [],
                    "learning": {
                        "status": "baseline_fallback",
                        "minimum_required": 20,
                        "message": "Insufficient completed outcomes.",
                    },
                },
                "outcome_diagnostics": {
                    "label_maturity": {
                        "completed_long_horizon_count": 18,
                        "minimum_long_horizon_required": 20,
                        "additional_long_horizon_needed": 2,
                    },
                    "pending_label_schedule": {"next_learning_label_due_date": "2026-06-23"},
                    "learning_readiness_projection": {"estimated_learning_ready_date": "2026-06-23"},
                },
                "backtest": {
                    "outcome_count": 4,
                    "completed_outcome_count": 0,
                    "pending_outcome_count": 4,
                    "missing_price_count": 0,
                    "recent_pending": [
                        {"symbol": "NVDA", "horizon": "5d", "status": "pending", "as_of": "2026-05-23", "due_date": "2026-05-30"},
                    ],
                    "outcomes": [
                        {"symbol": "NVDA", "horizon": "5d", "status": "pending", "as_of": "2026-05-23", "due_date": "2026-05-30"},
                        {"symbol": "NVDA", "horizon": "1m", "status": "pending", "as_of": "2026-05-23", "due_date": "2026-06-23"},
                        {"symbol": "AMD", "horizon": "1m", "status": "pending", "as_of": "2026-05-23", "due_date": "2026-06-23"},
                        {"symbol": "AVGO", "horizon": "3m", "status": "pending", "as_of": "2026-05-23", "due_date": "2026-08-23"},
                    ],
                },
                "data_health": {"sources": []},
                "audit": {
                    "overall_status": "attention",
                    "data_gaps": [
                        {
                            "area": "engine",
                            "label": "Learning reranker",
                            "status": "baseline_fallback",
                            "detail": "Old calendar due-date detail.",
                        }
                    ],
                },
                "manager_radar": {},
                "decision_cards": [],
                "macro": {},
                "ideas": [],
            }
        )

        outcomes = public["backtest"]["outcomes"]
        nvda_five_day = next(row for row in outcomes if row["symbol"] == "NVDA" and row["horizon"] == "5d")
        nvda_one_month = next(row for row in outcomes if row["symbol"] == "NVDA" and row["horizon"] == "1m")
        self.assertEqual(nvda_five_day["due_date"], "2026-06-02")
        self.assertEqual(nvda_one_month["due_date"], "2026-06-25")
        self.assertEqual(public["backtest"]["recent_pending"][0]["due_date"], "2026-06-02")
        self.assertEqual(public["backtest"]["due_date_policy"], "xnys_trading_days")
        self.assertEqual(public["backtest"]["due_date_policy_version"], BACKTEST_VERSION)
        diagnostics = public["outcome_diagnostics"]
        self.assertEqual(diagnostics["pending_label_schedule"]["next_label_due_date"], "2026-06-02")
        self.assertEqual(diagnostics["pending_label_schedule"]["next_learning_label_due_date"], "2026-06-25")
        self.assertEqual(diagnostics["learning_readiness_projection"]["estimated_learning_ready_date"], "2026-06-25")
        learning_gap = next(row for row in public["audit"]["data_gaps"] if row["label"] == "Learning reranker")
        self.assertIn("2026-06-25", learning_gap["detail"])

    def test_public_payload_backfills_external_reliability_and_recomputes_audit(self):
        payload = {
            "as_of": "2026-05-24",
            "session": "postmarket",
            "portfolio": {"position_count": 1, "by_symbol": [{"symbol": "NVDA", "weight": 1.0}]},
            "feature_matrix": {
                "feature_count": 1,
                "rows": [
                    {
                        "symbol": "NVDA",
                        "external_signal_score": 20,
                        "external_signal_count": 4,
                        "external_source_count": 3,
                    }
                ],
            },
            "research_book": {"item_count": 1, "items": [{"symbol": "NVDA"}]},
            "portfolio_benchmark": {
                "primary_horizon": "3m",
                "primary_portfolio_return": 10,
                "primary_price_coverage_pct": 100,
                "horizon_returns": [{"key": "3m", "portfolio_return": 10, "price_coverage_pct": 100}],
                "sizing_plan": {
                    "target_count": 1,
                    "action_count": 1,
                    "target_total_weight": 0.08,
                    "limits": {"max_one_ticket_delta": 0.03, "max_daily_turnover": 0.08, "max_single_name_weight": 0.15},
                    "targets": [{"symbol": "NVDA", "current_weight": 0.05, "model_target_weight": 0.08}],
                    "rebalance_budget": {
                        "total_add_weight": 0.03,
                        "total_trim_weight": 0.0,
                        "cash_deployed_weight": 0.03,
                        "cash_raised_weight": 0.0,
                        "starting_cash_weight": 0.10,
                        "post_trade_cash_weight": 0.07,
                        "net_delta_weight": 0.03,
                        "max_cash_deploy_weight": 0.03,
                    },
                },
                "action_queue": [
                    {
                        "symbol": "NVDA",
                        "trade_action": "add",
                        "current_weight": 0.05,
                        "recommended_delta_weight": 0.03,
                        "post_action_weight": 0.08,
                        "trade_target_weight": 0.08,
                        "target_weight": 0.08,
                        "model_target_weight": 0.08,
                        "max_allowed_weight": 0.15,
                        "risk_adjusted_expected_return": 22,
                        "confidence": 70,
                        "catalyst_clock": "fresh catalyst",
                        "company_reason": "company clears bar",
                        "sector_reason": "sector supports",
                        "tertiary_signal_summary": "external feeds partially degraded",
                        "company_add_eligible": True,
                        "funding_source": "funded_by_cash_reserve",
                        "external_signal_score": 20,
                        "coverage_adjusted_external_signal_score": 10,
                        "external_coverage_multiplier": 0.5,
                        "external_feed_status": "limited",
                        "external_provider_count": 2,
                        "external_provider_ok_count": 1,
                        "external_provider_ok_ratio": 0.5,
                        "external_signal_count": 4,
                        "external_source_count": 3,
                    }
                ],
            },
            "approval_tickets": [
                {
                    "ticket_id": "ticket-nvda",
                    "symbol": "NVDA",
                    "trade_action": "add",
                    "current_weight": 0.05,
                    "recommended_delta_weight": 0.03,
                    "post_action_weight": 0.08,
                    "trade_target_weight": 0.08,
                    "target_weight": 0.08,
                    "model_target_weight": 0.08,
                    "external_signal_score": 20,
                    "coverage_adjusted_external_signal_score": 10,
                    "external_coverage_multiplier": 0.5,
                    "external_feed_status": "limited",
                    "external_provider_count": 2,
                    "external_provider_ok_count": 1,
                    "external_provider_ok_ratio": 0.5,
                    "external_signal_count": 4,
                    "external_source_count": 3,
                    "approval_gate_status": "review_required",
                    "approval_open_check_count": 1,
                    "approval_checks": [
                        {"check": "approval_only_no_live_order", "status": "passed", "detail": "No live order."},
                        {"check": "external_feed_reliability_reviewed", "status": "pending", "detail": "Review providers."},
                    ],
                }
            ],
            "engine": {
                "feature_count": 1,
                "ranked_candidates": [{"symbol": "NVDA", "external_signal_score": 20}],
            },
            "external_signals": {
                "status": "ok",
                "provider_count": 2,
                "signal_count": 4,
                "source_statuses": [
                    {"source": "sec_company_data", "status": "ok", "signal_count": 4},
                    {"source": "alpha_vantage_news", "label": "Alpha Vantage news", "status": "limited", "detail": "API key missing.", "signal_count": 0},
                ],
                "by_symbol": {
                    "NVDA": {
                        "external_signal_score": 20,
                        "signal_count": 4,
                        "source_count": 3,
                    }
                },
            },
            "backtest": {"outcome_count": 0, "completed_outcome_count": 0, "pending_outcome_count": 0, "missing_price_count": 0, "outcomes": []},
            "data_health": {
                "recommendation_posture": "normal",
                "weak_source_count": 0,
                "sources": [
                    {
                        "source": "external_signals",
                        "label": "External signal feeds",
                        "status": "ok",
                        "detail": "Old report said ok.",
                    }
                ],
            },
            "instrumentation_audit": {"version": "2026-05-number-wiring-audit-v1", "status": "ok", "failure_count": 0},
            "audit": {"overall_status": "ok", "data_gaps": []},
            "manager_radar": {},
            "decision_cards": [],
            "macro": {},
            "ideas": [],
        }

        public = sanitize_payload(payload)
        feature = public["feature_matrix"]["rows"][0]
        engine = public["engine"]["ranked_candidates"][0]

        self.assertEqual(public["external_signals"]["provider_status_counts"], {"ok": 1, "limited": 1})
        self.assertEqual(public["external_signals"]["status"], "limited")
        self.assertEqual(public["external_signals"]["provider_ok_count"], 1)
        self.assertEqual(public["external_signals"]["provider_ok_ratio"], 0.5)
        self.assertEqual(public["external_signals"]["provider_gap_count"], 1)
        self.assertEqual(public["external_signals"]["provider_gaps"][0]["source"], "alpha_vantage_news")
        self.assertEqual(public["external_signals"]["provider_gaps"][0]["severity"], "configuration_required")
        external_health = next(row for row in public["data_health"]["sources"] if row["source"] == "external_signals")
        self.assertEqual(external_health["status"], "limited")
        self.assertIn("Alpha Vantage news: API key missing", external_health["detail"])
        self.assertEqual(external_health["provider_gap_count"], 1)
        self.assertEqual(external_health["provider_gaps"][0]["source"], "alpha_vantage_news")
        self.assertEqual(external_health["provider_gaps"][0]["severity"], "configuration_required")
        self.assertIn("API key", external_health["provider_gaps"][0]["remediation"])
        self.assertEqual(external_health["approval_blocked_external_gap_count"], 1)
        self.assertEqual(external_health["approval_blocked_external_gaps"][0]["symbol"], "NVDA")
        self.assertEqual(external_health["approval_blocked_external_gaps"][0]["ticket_id"], "ticket-nvda")
        self.assertEqual(
            external_health["approval_blocked_external_gaps"][0]["approval_blocking_checks"],
            ["external_feed_reliability_reviewed"],
        )
        self.assertEqual(external_health["approval_blocked_external_gaps"][0]["provider_gap_sources"], ["alpha_vantage_news"])
        self.assertEqual(
            external_health["approval_blocked_external_gaps"][0]["provider_gap_severities"],
            ["configuration_required"],
        )
        self.assertEqual(public["data_health"]["weak_source_count"], 1)
        self.assertEqual(public["data_health"]["recommendation_posture"], "reduced_confidence")
        summary = public["data_health"]["approval_blocker_summary"]
        self.assertEqual(summary["status"], "attention")
        self.assertEqual(summary["total_source_blocker_count"], 1)
        self.assertEqual(summary["external_gap_ticket_count"], 1)
        self.assertEqual(summary["earnings_confirmation_ticket_count"], 0)
        self.assertEqual(summary["blocked_ticket_count"], 1)
        self.assertEqual(summary["blocked_symbols"], ["NVDA"])
        self.assertEqual(summary["open_check_counts"], {"external_feed_reliability_reviewed": 1})
        self.assertEqual(summary["provider_gap_source_counts"], {"alpha_vantage_news": 1})
        self.assertEqual(summary["provider_gap_severity_counts"], {"configuration_required": 1})
        self.assertEqual(feature["external_coverage_multiplier"], 0.5)
        self.assertEqual(feature["coverage_adjusted_external_signal_score"], 10.0)
        self.assertEqual(feature["external_feed_status"], "limited")
        self.assertEqual(engine["coverage_adjusted_external_signal_score"], 10.0)
        self.assertEqual(public["instrumentation_audit"]["status"], "ok")
        self.assertEqual(public["audit"]["instrumentation_health"]["failure_count"], 0)
        audit_freshness = next(row for row in public["audit"]["source_freshness"] if row["source"] == "external_signals")
        self.assertEqual(audit_freshness["status"], "limited")
        self.assertIn("Alpha Vantage news: API key missing", audit_freshness["detail"])
        self.assertEqual(audit_freshness["provider_gaps"][0]["source"], "alpha_vantage_news")
        self.assertEqual(audit_freshness["provider_gaps"][0]["severity"], "configuration_required")
        self.assertEqual(audit_freshness["approval_blocked_external_gap_count"], 1)
        gap = next(row for row in public["audit"]["data_gaps"] if row["label"] == "External signal feeds")
        self.assertEqual(gap["status"], "limited")
        self.assertIn("Alpha Vantage news: API key missing", gap["detail"])
        self.assertEqual(gap["approval_blocked_external_gap_count"], 1)
        self.assertEqual(gap["approval_blocked_external_gaps"][0]["ticket_id"], "ticket-nvda")

    def test_backtest_page_wires_external_feed_calibration_tables(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "web" / "backtest.html").read_text(encoding="utf-8")
        script = (root / "web" / "backtest.js").read_text(encoding="utf-8")

        for element_id in [
            "externalStatusTable",
            "externalCoverageTable",
            "externalCoverageGapPlan",
            "externalCoverageGapQueue",
            "externalAlignmentTable",
            "externalAlignmentDueDates",
            "externalAlignmentReviewQueue",
            "externalAlignmentWatchlist",
        ]:
            self.assertIn(f'id="{element_id}"', html)
            self.assertIn(f'"{element_id}"', script)

        self.assertIn("renderExternalGroup(", script)
        self.assertIn("renderAlignmentDueDates(", script)
        self.assertIn("renderAlignmentReviewQueue(", script)
        self.assertIn("alignmentReviewDueDateRow(", script)
        self.assertIn("alignmentReviewRow(", script)
        self.assertIn("renderOutcomes(", script)
        self.assertIn("by_external_feed_status", script)
        self.assertIn("by_external_coverage", script)
        self.assertIn("by_external_alignment", script)
        self.assertIn("pending_by_external_feed_status", script)
        self.assertIn("pending_by_external_coverage", script)
        self.assertIn("pending_external_coverage_gap_plan", script)
        self.assertIn("priority_acceptance_check_count", script)
        self.assertIn("priority_open_acceptance_check_count", script)
        self.assertIn("projected_external_long_horizon_count_after_priority_backfill", script)
        self.assertIn("external_learning_ready_after_priority_backfill", script)
        self.assertIn("pending_external_coverage_gap_queue", script)
        self.assertIn("pending_by_external_alignment", script)
        self.assertIn("pending_external_alignment_due_dates", script)
        self.assertIn("pending_external_alignment_review_due_dates", script)
        self.assertIn("pending_external_alignment_review_queue", script)
        self.assertIn("pending_external_alignment_review_count", script)
        self.assertIn("pending_external_alignment_review_item_count", script)
        self.assertIn("pending_external_alignment_review_hidden_item_count", script)
        self.assertIn("pending_external_alignment_review_acceptance_summary", script)
        self.assertIn("pending_external_alignment_measurement_gap_plan", script)
        self.assertIn("pending_external_alignment_measurement_gap_queue", script)
        self.assertIn("next_open_check_due_date", script)
        self.assertIn("next_open_check_due_symbols", script)
        self.assertIn("next_open_check_due_horizons", script)
        self.assertIn("next_open_check_due_focus_counts", script)
        self.assertIn("next_open_check_due_measurement_missing_field_counts", script)
        self.assertIn("next_open_check_due_visible_work_item_count", script)
        self.assertIn("next_open_check_due_hidden_work_item_count", script)
        self.assertIn("external_alignment_review_focus", script)
        self.assertIn("external_alignment_review_label_count", script)
        self.assertIn("external_alignment_review_id", script)
        self.assertIn("external_alignment_review_priority_reason", script)
        self.assertIn("external_alignment_review_learning_action", script)
        self.assertIn("external_alignment_review_measurement_plan", script)
        self.assertIn("external_alignment_review_acceptance_checks", script)
        self.assertIn("external_alignment_review_open_check_count", script)
        self.assertIn("source_outcome_id", script)
        self.assertIn("source_trial_id", script)
        self.assertIn("pending_external_alignment_watchlist", script)
        self.assertIn("external_alignment_review_reason", script)
        self.assertIn("external_coverage_gap_id", script)
        self.assertIn("external_coverage_gap_action", script)
        self.assertIn("external_coverage_acceptance_checks", script)
        self.assertIn("external_coverage_gap_reason", script)
        self.assertIn("No external feed status labels yet.", script)
        self.assertIn("No external coverage labels yet.", script)
        self.assertIn("No priority coverage gaps.", script)
        self.assertIn("No external coverage gaps blocking learning.", script)
        self.assertIn("No external alignment labels yet.", script)
        self.assertIn("No pending external alignment due dates.", script)
        self.assertIn("No non-confirming external alignment reviews.", script)
        self.assertIn("Showing", script)
        self.assertIn("No pending external alignment examples.", script)

    def test_manager_return_labels_explain_horizon(self):
        root = Path(__file__).resolve().parents[1]
        app_script = (root / "web" / "app.js").read_text(encoding="utf-8")
        core_script = (root / "web" / "ai-thesis-core.js").read_text(encoding="utf-8")

        self.assertIn("Est. ${horizonLabel(proxy.horizon)} return", app_script)
        self.assertIn("Trailing ${horizon} 13F long-book public-price proxy", app_script)
        self.assertIn("not realized fund performance or a forward return", app_script)
        self.assertIn("Est. Since-entry Return", core_script)
        self.assertIn("Current px vs inferred 13F entry", core_script)
        self.assertIn("since_entry_est_return_pct", core_script)

    def test_public_payload_sanitizes_llm_review_request_fields(self):
        payload = minimal_report_payload("2026-05-24", "postmarket")
        payload["llm_review"] = {
            "status": "ok",
            "mode": "shadow",
            "model": "gpt-4o-mini",
            "raw_prompt": "private prompt",
            "request_payload": {"api_key": "secret"},
            "prompt_text": "private prompt text",
            "reviews": [
                {
                    "symbol": "NVDA",
                    "thesis_quality": "mixed",
                    "decision_usefulness_score": 75,
                    "review_required": True,
                    "confidence": 0.8,
                    "evidence_gaps": ["Refresh margin evidence."],
                    "contradictions": [],
                    "stale_assumptions": [],
                    "risk_questions": [],
                }
            ],
        }

        public = sanitize_payload(payload)
        text = json.dumps(public["llm_review"])

        self.assertEqual(public["llm_review"]["status"], "ok")
        self.assertIn("reviews", public["llm_review"])
        self.assertNotIn("raw_prompt", text)
        self.assertNotIn("request_payload", text)
        self.assertNotIn("api_key", text)
        self.assertNotIn("prompt_text", text)


def minimal_report_payload(as_of: str, session: str) -> dict:
    return {
        "as_of": as_of,
        "session": session,
        "portfolio": {"position_count": 0, "by_symbol": [], "by_bucket": []},
        "feature_matrix": {"feature_count": 0, "rows": []},
        "research_book": {"item_count": 0, "items": []},
        "portfolio_benchmark": {"action_queue": []},
        "approval_tickets": [],
        "engine": {"feature_count": 0, "ranked_candidates": []},
        "outcome_diagnostics": {},
        "backtest": {
            "outcome_count": 0,
            "completed_outcome_count": 0,
            "pending_outcome_count": 0,
            "missing_price_count": 0,
            "outcomes": [],
        },
        "data_health": {"recommendation_posture": "normal", "weak_source_count": 0, "sources": []},
        "audit": {"overall_status": "ok", "data_gaps": []},
        "manager_radar": {},
        "decision_cards": [],
        "macro": {},
        "ideas": [],
    }


if __name__ == "__main__":
    unittest.main()
