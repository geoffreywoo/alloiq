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

    def test_estimated_earnings_trades_must_carry_confirmation_metadata(self):
        payload = {
            "as_of": "2026-05-24",
            "session": "postmarket",
            "portfolio": {"by_symbol": [{"symbol": "AVGO", "weight": 0.5}, {"symbol": "MRVL", "weight": 0.5}]},
            "feature_matrix": {"feature_count": 2, "rows": [{"symbol": "AVGO"}, {"symbol": "MRVL"}]},
            "research_book": {"item_count": 2, "items": [{"symbol": "AVGO"}, {"symbol": "MRVL"}]},
            "portfolio_benchmark": {
                "primary_horizon": "3m",
                "primary_portfolio_return": 10,
                "primary_price_coverage_pct": 100,
                "horizon_returns": [{"key": "3m", "portfolio_return": 10, "price_coverage_pct": 100}],
                "sizing_plan": {
                    "target_count": 2,
                    "action_count": 2,
                    "target_total_weight": 0.13,
                    "limits": {"max_one_ticket_delta": 0.03, "max_daily_turnover": 0.08, "max_single_name_weight": 0.15},
                    "targets": [
                        {"symbol": "AVGO", "current_weight": 0.05, "model_target_weight": 0.08},
                        {"symbol": "MRVL", "current_weight": 0.06, "model_target_weight": 0.05},
                    ],
                    "rebalance_budget": {
                        "total_add_weight": 0.03,
                        "total_trim_weight": 0.01,
                        "cash_deployed_weight": 0.02,
                        "cash_raised_weight": 0.0,
                        "starting_cash_weight": 0.05,
                        "post_trade_cash_weight": 0.03,
                        "net_delta_weight": 0.02,
                        "max_cash_deploy_weight": 0.03,
                    },
                },
                "action_queue": [
                    {
                        "symbol": "AVGO",
                        "trade_action": "add",
                        "current_weight": 0.05,
                        "recommended_delta_weight": 0.03,
                        "post_action_weight": 0.08,
                        "trade_target_weight": 0.08,
                        "target_weight": 0.08,
                        "model_target_weight": 0.08,
                        "max_allowed_weight": 0.15,
                        "risk_flags": [],
                    },
                    {
                        "symbol": "MRVL",
                        "trade_action": "trim",
                        "current_weight": 0.06,
                        "recommended_delta_weight": -0.01,
                        "post_action_weight": 0.05,
                        "trade_target_weight": 0.05,
                        "target_weight": 0.05,
                        "model_target_weight": 0.05,
                        "max_allowed_weight": 0.15,
                        "risk_flags": [],
                    },
                ],
            },
            "approval_tickets": [
                {
                    "symbol": "AVGO",
                    "current_weight": 0.05,
                    "recommended_delta_weight": 0.03,
                    "post_action_weight": 0.08,
                    "trade_target_weight": 0.08,
                    "target_weight": 0.08,
                    "model_target_weight": 0.08,
                },
                {
                    "symbol": "MRVL",
                    "current_weight": 0.06,
                    "recommended_delta_weight": -0.01,
                    "post_action_weight": 0.05,
                    "trade_target_weight": 0.05,
                    "target_weight": 0.05,
                    "model_target_weight": 0.05,
                },
            ],
            "engine": {"feature_count": 2, "ranked_candidates": [{"symbol": "AVGO"}, {"symbol": "MRVL"}]},
            "earnings_events": [
                {
                    "symbol": "AVGO",
                    "event_type": "earnings",
                    "event_date": "2026-06-03",
                    "days_until": 8,
                    "source": "nasdaq_earnings_calendar",
                    "confirmed_or_estimated": "estimated",
                    "risk_window": "clear",
                },
                {
                    "symbol": "MRVL",
                    "event_type": "earnings",
                    "event_date": "2026-05-27",
                    "days_until": 1,
                    "source": "nasdaq_earnings_calendar",
                    "confirmed_or_estimated": "estimated",
                    "risk_window": "blackout",
                },
            ],
            "backtest": {"outcome_count": 0, "completed_outcome_count": 0, "pending_outcome_count": 0, "missing_price_count": 0, "outcomes": []},
        }

        audit = build_instrumentation_audit(payload)

        self.assertEqual(audit["status"], "attention")
        self.assertTrue(any(check["name"] == "AVGO_action_has_earnings_event_metadata" for check in audit["failures"]))
        self.assertTrue(any(check["name"] == "AVGO_estimated_earnings_trade_requires_confirmation_gate" for check in audit["failures"]))
        self.assertTrue(any(check["name"] == "AVGO_estimated_earnings_trade_has_confirmation_risk_flag" for check in audit["failures"]))
        self.assertTrue(any(check["name"] == "MRVL_action_has_earnings_event_metadata" for check in audit["failures"]))
        self.assertTrue(any(check["name"] == "MRVL_estimated_earnings_trade_requires_confirmation_gate" for check in audit["failures"]))
        self.assertTrue(any(check["name"] == "MRVL_estimated_earnings_trade_has_confirmation_risk_flag" for check in audit["failures"]))

        for action in payload["portfolio_benchmark"]["action_queue"]:
            is_avgo = action["symbol"] == "AVGO"
            action.update(
                {
                    "earnings_days_until": 8 if is_avgo else 1,
                    "earnings_event_date": "2026-06-03" if is_avgo else "2026-05-27",
                    "earnings_event_source": "nasdaq_earnings_calendar",
                    "earnings_confirmed_or_estimated": "estimated",
                    "earnings_risk_window": "clear" if is_avgo else "blackout",
                    "earnings_confirmation_required": True,
                    "risk_flags": ["earnings_confirmation_required"],
                }
            )

        audit = build_instrumentation_audit(payload)
        self.assertTrue(any(check["name"] == "approval_tickets_mirror_action_metadata" for check in audit["failures"]))

        for ticket in payload["approval_tickets"]:
            is_avgo = ticket["symbol"] == "AVGO"
            ticket.update(
                {
                    "earnings_days_until": 8 if is_avgo else 1,
                    "earnings_event_date": "2026-06-03" if is_avgo else "2026-05-27",
                    "earnings_event_source": "nasdaq_earnings_calendar",
                    "earnings_confirmed_or_estimated": "estimated",
                    "earnings_risk_window": "clear" if is_avgo else "blackout",
                    "earnings_confirmation_required": True,
                }
            )

        audit = build_instrumentation_audit(payload)

        self.assertEqual(audit["status"], "attention")
        self.assertTrue(
            any(check["name"] == "AVGO_approval_ticket_has_pending_earnings_confirmation_check" for check in audit["failures"])
        )
        self.assertTrue(
            any(check["name"] == "MRVL_approval_ticket_has_pending_earnings_confirmation_check" for check in audit["failures"])
        )

        for ticket in payload["approval_tickets"]:
            ticket["approval_checks"] = [
                {
                    "check": "approval_only_no_live_order",
                    "status": "passed",
                    "detail": "Ticket is approval-only; AlloIQ does not place live orders.",
                },
                {
                    "check": "sizing_weights_present",
                    "status": "passed",
                    "detail": "Current, delta, post-action, and target weights are present.",
                },
                {
                    "check": "earnings_date_confirmed",
                    "status": "pending",
                    "detail": "Confirm the estimated earnings date from a primary source before approving this ticket.",
                },
            ]
            ticket["approval_open_check_count"] = 1
            ticket["approval_gate_status"] = "blocked_until_confirmation"

        audit = build_instrumentation_audit(payload)

        self.assertEqual(audit["status"], "ok")

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
                    "targets": [{"symbol": "NVDA", "current_weight": 0.05, "model_target_weight": 0.05}],
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
                    "target_count": 1,
                    "action_count": 1,
                    "limits": {"max_one_ticket_delta": 0.03, "max_daily_turnover": 0.08, "max_single_name_weight": 0.15},
                    "targets": [{"symbol": "NVDA", "current_weight": 0.05, "model_target_weight": 0.05}],
                },
                "action_queue": [
                    {
                        "symbol": "NVDA",
                        "current_weight": 0.05,
                        "recommended_delta_weight": 0.0,
                        "post_action_weight": 0.05,
                        "trade_target_weight": 0.05,
                        "target_weight": 0.05,
                        "model_target_weight": 0.05,
                        "max_allowed_weight": 0.15,
                    }
                ],
            },
            "approval_tickets": [
                {
                    "symbol": "NVDA",
                    "current_weight": 0.05,
                    "recommended_delta_weight": 0.0,
                    "post_action_weight": 0.05,
                    "trade_target_weight": 0.05,
                    "target_weight": 0.05,
                    "model_target_weight": 0.05,
                }
            ],
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
        self.assertIn("action_queue_external_reliability_fields_present", failure_names)
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
            "external_provider_gap_count": 0,
            "external_provider_configuration_gap_count": 0,
            "external_provider_transient_gap_count": 0,
            "external_provider_stale_gap_count": 0,
            "external_provider_runtime_gap_count": 0,
            "external_provider_other_gap_count": 0,
            "external_provider_primary_gap_severity": "none",
            "external_provider_gap_severity_score": 0.0,
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
                    "target_count": 1,
                    "action_count": 1,
                    "limits": {"max_one_ticket_delta": 0.03, "max_daily_turnover": 0.08, "max_single_name_weight": 0.15},
                    "targets": [{"symbol": "NVDA", "current_weight": 0.05, "model_target_weight": 0.05}],
                },
                "action_queue": [
                    {
                        "symbol": "NVDA",
                        "current_weight": 0.05,
                        "recommended_delta_weight": 0.0,
                        "post_action_weight": 0.05,
                        "trade_target_weight": 0.05,
                        "target_weight": 0.05,
                        "model_target_weight": 0.05,
                        "max_allowed_weight": 0.15,
                        **external_reliability,
                    }
                ],
            },
            "approval_tickets": [
                {
                    "symbol": "NVDA",
                    "current_weight": 0.05,
                    "recommended_delta_weight": 0.0,
                    "post_action_weight": 0.05,
                    "trade_target_weight": 0.05,
                    "target_weight": 0.05,
                    "model_target_weight": 0.05,
                    **external_reliability,
                }
            ],
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

    def test_backtest_earnings_schema_groups_must_cover_outcomes(self):
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
            "backtest": {
                "version": "2026-05-recommendation-backtest-v4",
                "outcome_count": 1,
                "completed_outcome_count": 0,
                "pending_outcome_count": 1,
                "missing_price_count": 0,
                "outcomes": [
                    {
                        "status": "pending",
                        "symbol": "NVDA",
                        "horizon": "5d",
                        "due_date": "2026-06-02",
                        "earnings_event_status": "estimated",
                        "earnings_confirmation_bucket": "confirmation_required",
                        "earnings_confirmation_required": True,
                        "approval_required": True,
                        "approval_gate_status": "review_required",
                        "approval_open_check_count": 1,
                        "approval_blocking_checks": ["earnings_date_confirmed"],
                        "approval_blocker_bucket": "review_required",
                    }
                ],
            },
        }

        audit = build_instrumentation_audit(payload)
        failure_names = {row["name"] for row in audit["failures"]}

        self.assertEqual(audit["status"], "attention")
        self.assertIn("backtest_pending_earnings_event_status_groups_present", failure_names)
        self.assertIn("backtest_pending_earnings_risk_window_groups_present", failure_names)
        self.assertIn("backtest_pending_earnings_confirmation_bucket_groups_present", failure_names)
        self.assertIn("backtest_pending_approval_gate_status_groups_present", failure_names)
        self.assertIn("backtest_pending_approval_blocker_bucket_groups_present", failure_names)

        payload["backtest"].update(
            {
                "pending_by_earnings_event_status": [{"key": "estimated", "pending_count": 1}],
                "pending_by_earnings_risk_window": [{"key": "clear", "pending_count": 1}],
                "pending_by_earnings_confirmation_bucket": [{"key": "confirmation_required", "pending_count": 1}],
                "pending_by_approval_gate_status": [{"key": "review_required", "pending_count": 1}],
                "pending_by_approval_blocker_bucket": [{"key": "review_required", "pending_count": 1}],
                "pending_label_schedule": {
                    "pending_label_count": 1,
                    "pending_learning_label_count": 0,
                    "overdue_label_count": 0,
                    "overdue_learning_label_count": 0,
                    "due_window_counts": {"due_today": 0, "due_next_7d": 0, "due_next_30d": 1, "overdue": 0},
                    "learning_due_window_counts": {"due_today": 0, "due_next_7d": 0, "due_next_30d": 0, "overdue": 0},
                    "learning_due_dates": [],
                    "next_label": {"due_date": "2026-06-02", "days_until_due": 9, "horizon": "5d", "symbol": "NVDA", "due_count": 1},
                    "next_learning_label": {},
                    "next_label_due_date": "2026-06-02",
                    "next_learning_label_due_date": None,
                },
                "next_label_maturity": {"due_date": "2026-06-02", "days_until_due": 9, "horizon": "5d", "symbol": "NVDA", "due_count": 1},
                "next_learning_label_maturity": {},
                "next_label_maturity_date": "2026-06-02",
                "next_learning_label_maturity_date": None,
            }
        )

        audit = build_instrumentation_audit(payload)

        self.assertEqual(audit["status"], "ok")

    def test_approval_data_friction_schema_guards_payload_surfaces(self):
        label_schedule = {
            "pending_label_count": 1,
            "pending_learning_label_count": 0,
            "overdue_label_count": 0,
            "overdue_learning_label_count": 0,
            "due_window_counts": {"due_today": 0, "due_next_7d": 0, "due_next_30d": 1, "overdue": 0},
            "learning_due_window_counts": {"due_today": 0, "due_next_7d": 0, "due_next_30d": 0, "overdue": 0},
            "learning_due_dates": [],
            "next_label": {"due_date": "2026-06-02", "days_until_due": 9, "horizon": "5d", "symbol": "NVDA", "due_count": 1},
            "next_learning_label": {},
            "next_label_due_date": "2026-06-02",
            "next_learning_label_due_date": None,
        }
        payload = {
            "as_of": "2026-05-24",
            "session": "postmarket",
            "portfolio": {"by_symbol": [{"symbol": "NVDA", "weight": 1.0}]},
            "feature_matrix": {
                "version": "2026-05-ml-feature-matrix-v5",
                "feature_count": 1,
                "rows": [{"symbol": "NVDA"}],
            },
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
            "backtest": {
                "outcome_count": 1,
                "completed_outcome_count": 0,
                "pending_outcome_count": 1,
                "missing_price_count": 0,
                "outcomes": [
                    {
                        "status": "pending",
                        "symbol": "NVDA",
                        "horizon": "5d",
                        "due_date": "2026-06-02",
                        "external_feed_status": "limited",
                        "external_coverage_multiplier": 0.3333,
                        "approval_data_friction_bucket": "unknown",
                    }
                ],
                "pending_label_schedule": label_schedule,
                "next_label_maturity": label_schedule["next_label"],
                "next_learning_label_maturity": {},
                "next_label_maturity_date": "2026-06-02",
                "next_learning_label_maturity_date": None,
            },
        }

        audit = build_instrumentation_audit(payload)
        failure_names = {row["name"] for row in audit["failures"]}

        self.assertEqual(audit["status"], "attention")
        self.assertIn("feature_matrix_approval_data_friction_fields_present", failure_names)
        self.assertIn("research_book_approval_data_friction_fields_present", failure_names)
        self.assertIn("backtest_outcomes_approval_data_friction_fields_present", failure_names)
        self.assertIn("backtest_pending_approval_data_friction_unknown_context_count_zero", failure_names)
        self.assertIn("backtest_pending_approval_data_friction_groups_present", failure_names)

        friction = {
            "approval_data_friction_score": 23.33,
            "approval_data_friction_bucket": "external_review",
            "approval_data_friction_reasons": ["external_feed_reliability_review_required"],
        }
        payload["feature_matrix"]["rows"][0].update(friction)
        payload["research_book"]["items"][0].update({**friction, "approval_data_friction_penalty": 1.4})
        payload["backtest"]["outcomes"][0].update({**friction, "approval_data_friction_penalty": None})
        payload["backtest"]["pending_by_approval_data_friction_bucket"] = [{"key": "external_review", "pending_count": 1}]

        audit = build_instrumentation_audit(payload)

        self.assertEqual(audit["status"], "ok")

    def test_training_examples_preserve_approval_ticket_context(self):
        approval_check = {
            "check": "external_feed_reliability_reviewed",
            "status": "pending",
            "detail": "Review provider coverage before approval.",
        }
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
                    "target_count": 1,
                    "action_count": 1,
                    "limits": {"max_one_ticket_delta": 0.03, "max_daily_turnover": 0.08, "max_single_name_weight": 0.15},
                    "targets": [{"symbol": "NVDA", "current_weight": 0.05, "model_target_weight": 0.05}],
                },
                "action_queue": [
                    {
                        "symbol": "NVDA",
                        "current_weight": 0.05,
                        "recommended_delta_weight": 0.0,
                        "post_action_weight": 0.05,
                        "trade_target_weight": 0.05,
                        "target_weight": 0.05,
                        "model_target_weight": 0.05,
                        "max_allowed_weight": 0.15,
                    }
                ],
            },
            "approval_tickets": [
                {
                    "symbol": "NVDA",
                    "current_weight": 0.05,
                    "recommended_delta_weight": 0.0,
                    "post_action_weight": 0.05,
                    "trade_target_weight": 0.05,
                    "target_weight": 0.05,
                    "model_target_weight": 0.05,
                    "approval_required": True,
                    "approval_gate_status": "review_required",
                    "approval_open_check_count": 1,
                    "approval_checks": [
                        {
                            "check": "approval_only_no_live_order",
                            "status": "passed",
                            "detail": "Approval-only; no live order.",
                        },
                        approval_check,
                    ],
                }
            ],
            "recommendation_training_examples": [
                {
                    "example_id": "example-nvda",
                    "as_of": "2026-05-24",
                    "session": "postmarket",
                    "symbol": "NVDA",
                }
            ],
            "engine": {"feature_count": 1, "ranked_candidates": [{"symbol": "NVDA"}]},
            "backtest": {"outcome_count": 0, "completed_outcome_count": 0, "pending_outcome_count": 0, "missing_price_count": 0, "outcomes": []},
        }

        audit = build_instrumentation_audit(payload)
        failure_names = {row["name"] for row in audit["failures"]}

        self.assertEqual(audit["status"], "attention")
        self.assertIn("training_examples_approval_learning_fields_present", failure_names)
        self.assertIn("training_examples_mirror_approval_ticket_context", failure_names)

        payload["recommendation_training_examples"][0].update(
            {
                "approval_required": True,
                "approval_gate_status": "review_required",
                "approval_open_check_count": 1,
                "approval_blocking_checks": ["external_feed_reliability_reviewed"],
            }
        )

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

    def test_flags_approval_blocker_summary_that_does_not_match_rows(self):
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
            "backtest": {"outcome_count": 0, "completed_outcome_count": 0, "pending_outcome_count": 0, "missing_price_count": 0, "outcomes": []},
            "data_health": {
                "approval_blocker_summary": {
                    "status": "attention",
                    "total_source_blocker_count": 2,
                    "external_gap_ticket_count": 1,
                    "earnings_confirmation_ticket_count": 1,
                    "visible_blocker_row_count": 2,
                    "blocked_ticket_count": 1,
                    "blocked_symbols": ["NVDA"],
                    "open_check_count": 1,
                    "open_check_counts": {"external_feed_reliability_reviewed": 1},
                    "provider_gap_source_counts": {},
                    "confirmation_priority_counts": {},
                    "next_confirmation_deadline": "2026-05-25",
                    "next_confirmation_symbols": ["AVGO"],
                },
                "sources": [
                    {
                        "source": "external_signals",
                        "label": "External signals",
                        "status": "ok",
                        "approval_blocked_external_gap_count": 1,
                        "approval_blocked_external_gaps": [
                            {
                                "symbol": "NVDA",
                                "ticket_id": "ticket-nvda",
                                "approval_gate_status": "review_required",
                                "approval_open_check_count": 1,
                                "approval_blocking_checks": ["external_feed_reliability_reviewed"],
                                "provider_gap_count": 1,
                                "provider_gap_sources": ["alpha_vantage_news"],
                            }
                        ],
                    },
                    {
                        "source": "earnings",
                        "label": "Earnings calendar",
                        "status": "ok",
                        "approval_blocked_confirmation_gap_count": 1,
                        "approval_blocked_confirmation_gaps": [
                            {
                                "symbol": "MRVL",
                                "ticket_id": "ticket-mrvl",
                                "approval_gate_status": "blocked_until_confirmation",
                                "approval_open_check_count": 1,
                                "approval_blocking_checks": ["earnings_date_confirmed"],
                                "event_date": "2026-05-27",
                                "confirmation_deadline": "2026-05-24",
                                "confirmation_priority": "p0_blackout_confirmation",
                            }
                        ],
                    },
                ],
            },
        }

        audit = build_instrumentation_audit(payload)
        failure_names = {row["name"] for row in audit["failures"]}

        self.assertEqual(audit["status"], "attention")
        self.assertIn("data_health_approval_blocker_summary_blocked_ticket_count_matches_rows", failure_names)
        self.assertIn("data_health_approval_blocker_summary_open_check_counts_match_rows", failure_names)
        self.assertIn("data_health_approval_blocker_summary_provider_gap_counts_match_rows", failure_names)
        self.assertIn("data_health_approval_blocker_summary_confirmation_priority_counts_match_rows", failure_names)
        self.assertIn("data_health_approval_blocker_summary_next_confirmation_deadline_matches_rows", failure_names)

    def test_flags_audit_source_gaps_that_drop_approval_blocker_context(self):
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
            "backtest": {"outcome_count": 0, "completed_outcome_count": 0, "pending_outcome_count": 0, "missing_price_count": 0, "outcomes": []},
            "data_health": {
                "approval_blocker_summary": {
                    "status": "attention",
                    "total_source_blocker_count": 2,
                    "external_gap_ticket_count": 1,
                    "earnings_confirmation_ticket_count": 1,
                    "visible_blocker_row_count": 2,
                    "blocked_ticket_count": 2,
                    "blocked_symbols": ["MRVL", "NVDA"],
                    "open_check_count": 2,
                    "open_check_counts": {"earnings_date_confirmed": 1, "external_feed_reliability_reviewed": 1},
                    "provider_gap_source_counts": {"alpha_vantage_news": 1},
                    "confirmation_priority_counts": {"p0_blackout_confirmation": 1},
                    "next_confirmation_deadline": "2026-05-24",
                    "next_confirmation_symbols": ["MRVL"],
                },
                "sources": [
                    {
                        "source": "external_signals",
                        "label": "External signal feeds",
                        "status": "limited",
                        "approval_blocked_external_gap_count": 1,
                        "approval_blocked_external_gaps": [
                            {
                                "symbol": "NVDA",
                                "ticket_id": "ticket-nvda",
                                "approval_gate_status": "review_required",
                                "approval_open_check_count": 1,
                                "approval_blocking_checks": ["external_feed_reliability_reviewed"],
                                "provider_gap_count": 1,
                                "provider_gap_sources": ["alpha_vantage_news"],
                            }
                        ],
                    },
                    {
                        "source": "earnings",
                        "label": "Earnings calendar",
                        "status": "estimated",
                        "action_linked_confirmation_gap_count": 1,
                        "approval_blocked_confirmation_gap_count": 1,
                        "approval_blocked_confirmation_gaps": [
                            {
                                "symbol": "MRVL",
                                "ticket_id": "ticket-mrvl",
                                "approval_gate_status": "blocked_until_confirmation",
                                "approval_open_check_count": 1,
                                "approval_blocking_checks": ["earnings_date_confirmed"],
                                "event_date": "2026-05-27",
                                "confirmation_deadline": "2026-05-24",
                                "confirmation_priority": "p0_blackout_confirmation",
                            }
                        ],
                    },
                ],
            },
            "audit": {
                "data_gaps": [
                    {"area": "source", "label": "External signal feeds", "status": "limited"},
                    {"area": "source", "label": "Earnings calendar", "status": "estimated"},
                ]
            },
        }

        audit = build_instrumentation_audit(payload)
        failure_names = {row["name"] for row in audit["failures"]}

        self.assertEqual(audit["status"], "attention")
        self.assertIn("audit_source_gap_external_blocker_count_matches_data_health_external_signal_feeds", failure_names)
        self.assertIn("audit_source_gap_confirmation_blocker_count_matches_data_health_earnings_calendar", failure_names)
        self.assertIn("audit_source_gap_action_confirmation_count_matches_data_health_earnings_calendar", failure_names)

        payload["audit"]["data_gaps"][0].update(
            {
                "approval_blocked_external_gap_count": 1,
                "approval_blocked_external_gaps": payload["data_health"]["sources"][0]["approval_blocked_external_gaps"],
            }
        )
        payload["audit"]["data_gaps"][1].update(
            {
                "action_linked_confirmation_gap_count": 1,
                "approval_blocked_confirmation_gap_count": 1,
                "approval_blocked_confirmation_gaps": payload["data_health"]["sources"][1]["approval_blocked_confirmation_gaps"],
            }
        )

        audit = build_instrumentation_audit(payload)

        self.assertEqual(audit["status"], "ok")


if __name__ == "__main__":
    unittest.main()
