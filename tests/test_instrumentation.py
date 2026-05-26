import unittest

from invest.instrumentation import build_instrumentation_audit


class InstrumentationAuditTests(unittest.TestCase):
    def test_flags_target_weight_that_is_not_next_trade_target(self):
        payload = {
            "as_of": "2026-05-24",
            "session": "postmarket",
            "portfolio": {"by_symbol": [{"symbol": "NVDA", "weight": 0.5}, {"symbol": "AMD", "weight": 0.5}]},
            "feature_matrix": {"feature_count": 2, "rows": [{"symbol": "NVDA"}, {"symbol": "AMD"}]},
            "research_book": {"item_count": 2, "items": [{"symbol": "NVDA"}, {"symbol": "AMD"}]},
            "portfolio_benchmark": {
                "primary_horizon": "3m",
                "primary_portfolio_return": 10,
                "primary_price_coverage_pct": 100,
                "horizon_returns": [{"key": "3m", "portfolio_return": 10, "price_coverage_pct": 100}],
                "sizing_plan": {
                    "target_count": 2,
                    "action_count": 2,
                    "limits": {"max_one_ticket_delta": 0.03, "max_daily_turnover": 0.08, "max_single_name_weight": 0.15},
                    "targets": [
                        {"symbol": "NVDA", "current_weight": 0.05, "model_target_weight": 0.15},
                        {"symbol": "AMD", "current_weight": 0.10, "model_target_weight": 0.0},
                    ],
                    "rebalance_budget": {
                        "total_add_weight": 0.03,
                        "total_trim_weight": 0.03,
                        "cash_deployed_weight": 0.0,
                        "cash_raised_weight": 0.0,
                        "starting_cash_weight": 0.0,
                        "post_trade_cash_weight": 0.0,
                        "net_delta_weight": 0.0,
                    },
                },
                "action_queue": [
                    {
                        "symbol": "NVDA",
                        "current_weight": 0.05,
                        "recommended_delta_weight": 0.03,
                        "post_action_weight": 0.08,
                        "trade_target_weight": 0.08,
                        "target_weight": 0.15,
                        "model_target_weight": 0.15,
                        "max_allowed_weight": 0.15,
                    },
                    {
                        "symbol": "AMD",
                        "current_weight": 0.10,
                        "recommended_delta_weight": -0.03,
                        "post_action_weight": 0.07,
                        "trade_target_weight": 0.07,
                        "target_weight": 0.07,
                        "model_target_weight": 0.0,
                        "max_allowed_weight": 0.15,
                    }
                ],
            },
            "approval_tickets": [
                {
                    "symbol": "NVDA",
                    "current_weight": 0.05,
                    "recommended_delta_weight": 0.03,
                    "post_action_weight": 0.08,
                    "trade_target_weight": 0.08,
                    "target_weight": 0.15,
                    "model_target_weight": 0.15,
                }
            ],
            "engine": {"feature_count": 1, "ranked_candidates": [{"symbol": "NVDA"}]},
            "backtest": {"outcome_count": 0, "completed_outcome_count": 0, "pending_outcome_count": 0, "missing_price_count": 0, "outcomes": []},
        }

        audit = build_instrumentation_audit(payload)

        self.assertEqual(audit["status"], "attention")
        self.assertTrue(any(check["name"] == "NVDA_target_is_trade_target" for check in audit["failures"]))

    def test_accepts_consistent_action_and_model_targets(self):
        payload = {
            "as_of": "2026-05-24",
            "session": "postmarket",
            "portfolio": {"by_symbol": [{"symbol": "NVDA", "weight": 0.5}, {"symbol": "AMD", "weight": 0.5}]},
            "feature_matrix": {"feature_count": 2, "rows": [{"symbol": "NVDA"}, {"symbol": "AMD"}]},
            "research_book": {"item_count": 2, "items": [{"symbol": "NVDA"}, {"symbol": "AMD"}]},
            "portfolio_benchmark": {
                "primary_horizon": "3m",
                "primary_portfolio_return": 10,
                "primary_price_coverage_pct": 100,
                "horizon_returns": [{"key": "3m", "portfolio_return": 10, "price_coverage_pct": 100}],
                "sizing_plan": {
                    "target_count": 2,
                    "action_count": 2,
                    "limits": {"max_one_ticket_delta": 0.03, "max_daily_turnover": 0.08, "max_single_name_weight": 0.15},
                    "targets": [
                        {"symbol": "NVDA", "current_weight": 0.05, "model_target_weight": 0.15},
                        {"symbol": "AMD", "current_weight": 0.10, "model_target_weight": 0.0},
                    ],
                    "rebalance_budget": {
                        "total_add_weight": 0.03,
                        "total_trim_weight": 0.03,
                        "cash_deployed_weight": 0.0,
                        "cash_raised_weight": 0.0,
                        "starting_cash_weight": 0.0,
                        "post_trade_cash_weight": 0.0,
                        "net_delta_weight": 0.0,
                    },
                },
                "action_queue": [
                    {
                        "symbol": "NVDA",
                        "current_weight": 0.05,
                        "recommended_delta_weight": 0.03,
                        "post_action_weight": 0.08,
                        "trade_target_weight": 0.08,
                        "target_weight": 0.08,
                        "model_target_weight": 0.15,
                        "max_allowed_weight": 0.15,
                    },
                    {
                        "symbol": "AMD",
                        "current_weight": 0.10,
                        "recommended_delta_weight": -0.03,
                        "post_action_weight": 0.07,
                        "trade_target_weight": 0.07,
                        "target_weight": 0.07,
                        "model_target_weight": 0.0,
                        "max_allowed_weight": 0.15,
                    }
                ],
            },
            "approval_tickets": [
                {
                    "symbol": "NVDA",
                    "current_weight": 0.05,
                    "recommended_delta_weight": 0.03,
                    "post_action_weight": 0.08,
                    "trade_target_weight": 0.08,
                    "target_weight": 0.08,
                    "model_target_weight": 0.15,
                },
                {
                    "symbol": "AMD",
                    "current_weight": 0.10,
                    "recommended_delta_weight": -0.03,
                    "post_action_weight": 0.07,
                    "trade_target_weight": 0.07,
                    "target_weight": 0.07,
                    "model_target_weight": 0.0,
                }
            ],
            "engine": {"feature_count": 2, "ranked_candidates": [{"symbol": "NVDA"}, {"symbol": "AMD"}]},
            "backtest": {"outcome_count": 0, "completed_outcome_count": 0, "pending_outcome_count": 0, "missing_price_count": 0, "outcomes": []},
        }

        audit = build_instrumentation_audit(payload)

        self.assertEqual(audit["status"], "ok")
        self.assertFalse(audit["failures"])

    def test_bottom_up_actions_must_trace_expected_return_and_funding(self):
        payload = {
            "as_of": "2026-05-24",
            "session": "postmarket",
            "portfolio": {"by_symbol": [{"symbol": "NVDA", "weight": 1.0}]},
            "company_underwriting": {"item_count": 1, "items": [{"symbol": "NVDA", "company_underwriting_score": 75}]},
            "sector_underwriting": {"item_count": 1, "items": [{"bucket": "semis_networking_hbm", "sector_setup_score": 70}]},
            "feature_matrix": {"feature_count": 1, "rows": [{"symbol": "NVDA"}]},
            "research_book": {"item_count": 1, "items": [{"symbol": "NVDA", "risk_adjusted_expected_return": 22}]},
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
                        "portfolio_weight": 0.05,
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
                        "tertiary_signal_summary": "13F confirms",
                        "company_add_eligible": True,
                        "funding_source": "funded_by_cash_reserve",
                    }
                ],
            },
            "approval_tickets": [
                {
                    "symbol": "NVDA",
                    "current_weight": 0.05,
                    "recommended_delta_weight": 0.03,
                    "post_action_weight": 0.08,
                    "trade_target_weight": 0.08,
                    "target_weight": 0.08,
                    "model_target_weight": 0.08,
                }
            ],
            "engine": {"feature_count": 1, "ranked_candidates": [{"symbol": "NVDA"}]},
            "backtest": {"outcome_count": 0, "completed_outcome_count": 0, "pending_outcome_count": 0, "missing_price_count": 0, "outcomes": []},
        }

        audit = build_instrumentation_audit(payload)

        self.assertEqual(audit["status"], "ok")

    def test_flags_stale_external_signal_and_backtest_schema(self):
        payload = {
            "as_of": "2026-05-24",
            "session": "postmarket",
            "portfolio": {"by_symbol": [{"symbol": "NVDA", "weight": 1.0}]},
            "feature_matrix": {"feature_count": 1, "rows": [{"symbol": "NVDA"}]},
            "research_book": {"item_count": 1, "items": [{"symbol": "NVDA"}]},
            "portfolio_benchmark": {
                "primary_horizon": "3m",
                "primary_portfolio_return": 10,
                "primary_price_coverage_pct": 100,
                "horizon_returns": [{"key": "3m", "portfolio_return": 10, "price_coverage_pct": 100}],
                "sizing_plan": {
                    "target_count": 0,
                    "action_count": 0,
                    "limits": {"max_one_ticket_delta": 0.03, "max_daily_turnover": 0.08, "max_single_name_weight": 0.15},
                    "targets": [],
                },
                "action_queue": [],
            },
            "approval_tickets": [],
            "engine": {"feature_count": 1, "ranked_candidates": [{"symbol": "NVDA"}]},
            "external_signals": {
                "status": "ok",
                "provider_count": 6,
                "signal_count": 20,
            },
            "backtest": {
                "outcome_count": 1,
                "completed_outcome_count": 1,
                "pending_outcome_count": 0,
                "missing_price_count": 0,
                "outcomes": [{"status": "complete", "symbol": "NVDA"}],
            },
        }

        audit = build_instrumentation_audit(payload)
        failure_names = {row["name"] for row in audit["failures"]}

        self.assertEqual(audit["status"], "attention")
        self.assertIn("external_signals_provider_ok_count_present", failure_names)
        self.assertIn("external_signals_provider_ok_ratio_present", failure_names)
        self.assertIn("external_signals_provider_status_counts_present", failure_names)
        self.assertIn("feature_matrix_external_reliability_fields_present", failure_names)
        self.assertIn("engine_external_reliability_fields_present", failure_names)
        self.assertIn("backtest_external_feed_status_groups_present", failure_names)
        self.assertIn("backtest_external_coverage_groups_present", failure_names)

    def test_accepts_external_signal_and_backtest_schema_fields(self):
        external_reliability = {
            "external_signal_score": 20,
            "coverage_adjusted_external_signal_score": 5,
            "external_coverage_multiplier": 0.25,
            "external_feed_status": "limited",
            "external_provider_count": 6,
            "external_provider_ok_count": 2,
            "external_provider_ok_ratio": 0.3333,
            "external_signal_count": 4,
            "external_source_count": 3,
        }
        payload = {
            "as_of": "2026-05-24",
            "session": "postmarket",
            "portfolio": {"by_symbol": [{"symbol": "NVDA", "weight": 1.0}]},
            "feature_matrix": {"feature_count": 1, "rows": [{"symbol": "NVDA", **external_reliability}]},
            "research_book": {"item_count": 1, "items": [{"symbol": "NVDA"}]},
            "portfolio_benchmark": {
                "primary_horizon": "3m",
                "primary_portfolio_return": 10,
                "primary_price_coverage_pct": 100,
                "horizon_returns": [{"key": "3m", "portfolio_return": 10, "price_coverage_pct": 100}],
                "sizing_plan": {
                    "target_count": 0,
                    "action_count": 0,
                    "limits": {"max_one_ticket_delta": 0.03, "max_daily_turnover": 0.08, "max_single_name_weight": 0.15},
                    "targets": [],
                },
                "action_queue": [],
            },
            "approval_tickets": [],
            "engine": {"feature_count": 1, "ranked_candidates": [{"symbol": "NVDA", **external_reliability}]},
            "external_signals": {
                "status": "limited",
                "provider_count": 6,
                "provider_ok_count": 2,
                "provider_ok_ratio": 0.3333,
                "provider_status_counts": {"ok": 2, "limited": 4},
                "signal_count": 20,
            },
            "backtest": {
                "outcome_count": 1,
                "completed_outcome_count": 1,
                "pending_outcome_count": 0,
                "missing_price_count": 0,
                "outcomes": [{"status": "complete", "symbol": "NVDA"}],
                "by_external_feed_status": [{"key": "limited", "completed_count": 1}],
                "by_external_coverage": [{"key": "thin_coverage", "completed_count": 1}],
            },
        }

        audit = build_instrumentation_audit(payload)

        self.assertEqual(audit["status"], "ok")

    def test_flags_engine_external_reliability_that_does_not_trace_to_features(self):
        feature_reliability = {
            "external_signal_score": 20,
            "coverage_adjusted_external_signal_score": 5,
            "external_coverage_multiplier": 0.25,
            "external_feed_status": "limited",
            "external_provider_count": 6,
            "external_provider_ok_count": 2,
            "external_provider_ok_ratio": 0.3333,
            "external_signal_count": 4,
            "external_source_count": 3,
        }
        engine_reliability = dict(feature_reliability, coverage_adjusted_external_signal_score=20)
        payload = {
            "as_of": "2026-05-24",
            "session": "postmarket",
            "portfolio": {"by_symbol": [{"symbol": "NVDA", "weight": 1.0}]},
            "feature_matrix": {"feature_count": 1, "rows": [{"symbol": "NVDA", **feature_reliability}]},
            "research_book": {"item_count": 1, "items": [{"symbol": "NVDA"}]},
            "portfolio_benchmark": {
                "primary_horizon": "3m",
                "primary_portfolio_return": 10,
                "primary_price_coverage_pct": 100,
                "horizon_returns": [{"key": "3m", "portfolio_return": 10, "price_coverage_pct": 100}],
                "sizing_plan": {
                    "target_count": 0,
                    "action_count": 0,
                    "limits": {"max_one_ticket_delta": 0.03, "max_daily_turnover": 0.08, "max_single_name_weight": 0.15},
                    "targets": [],
                },
                "action_queue": [],
            },
            "approval_tickets": [],
            "engine": {"feature_count": 1, "ranked_candidates": [{"symbol": "NVDA", **engine_reliability}]},
            "external_signals": {
                "status": "limited",
                "provider_count": 6,
                "provider_ok_count": 2,
                "provider_ok_ratio": 0.3333,
                "provider_status_counts": {"ok": 2, "limited": 4},
                "signal_count": 20,
            },
            "backtest": {"outcome_count": 0, "completed_outcome_count": 0, "pending_outcome_count": 0, "missing_price_count": 0, "outcomes": []},
        }

        audit = build_instrumentation_audit(payload)

        self.assertEqual(audit["status"], "attention")
        self.assertTrue(any(row["name"] == "engine_external_reliability_mirrors_feature_matrix" for row in audit["failures"]))


if __name__ == "__main__":
    unittest.main()
