from contextlib import ExitStack
from datetime import date, datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from invest.config import AppConfig
from invest.reports import (
    alias_matches,
    build_data_health,
    build_methodology,
    build_weekly_research,
    generate_brief,
    render_backtest_summary,
    render_markdown,
    render_outcome_diagnostics,
    vanguard_staleness,
)


class ReportTests(unittest.TestCase):
    def test_report_renders_cited_news_and_public_weights_disclaimer(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            config = AppConfig(
                path=Path("config/invest.toml"),
                data={
                    "reports": {"directory": str(tmp)},
                    "vanguard": {"enabled": True},
                    "managers": [{"key": "situational-awareness", "name": "Situational Awareness LP", "primary": True}],
                },
            )
            payload = {
                "as_of": "2026-05-08",
                "session": "premarket",
                "stale_vanguard": {"is_stale": True, "last_import": None},
                "decision_cards": [
                    {
                        "symbol": "BE",
                        "candidate": "research add candidate",
                        "score": 42.0,
                        "bucket": "power_grid_gas_nuclear",
                        "last_price": 10.0,
                        "five_day_pct": 5.0,
                        "filing_value": 875505000.0,
                        "news_count": 2,
                        "counterargument": "Execution risk.",
                        "falsifier": "Demand breaks.",
                    }
                ],
                "transactions": [],
                "latest_filing": {
                    "form": "13F-HR",
                    "accession_number": "0002045724-26-000002",
                    "url": "https://www.sec.gov/example",
                    "filing_date": "2026-02-11",
                    "report_date": "2025-12-31",
                },
                "news": [
                    {
                        "title": "AI power demand headline",
                        "url": "https://example.com/news",
                        "source": "Example",
                        "published_at": "2026-05-08T10:00:00",
                    }
                ],
            }

            md = render_markdown(payload, config)

            self.assertIn("Public weights, public filings, daily AI markets signals", md)
            self.assertIn("[AI power demand headline](https://example.com/news)", md)
            self.assertIn("Vanguard import status: stale or missing", md)

    def test_report_omits_vanguard_warning_when_disabled(self):
        config = AppConfig(
            path=Path("config/invest.toml"),
            data={
                "vanguard": {"enabled": False},
                "managers": [{"key": "situational-awareness", "name": "Situational Awareness LP", "primary": True}],
            },
        )
        payload = {
            "as_of": "2026-05-08",
            "session": "premarket",
            "stale_vanguard": None,
            "portfolio": {"position_count": 0},
            "macro": {},
            "manager_radar": {},
            "ideas": [],
            "decision_cards": [],
            "transactions": [],
            "latest_filing": None,
            "news": [],
        }

        md = render_markdown(payload, config)

        self.assertNotIn("Vanguard import status", md)
        self.assertIn("Import IBKR Flex positions first", md)

    def test_ticker_alias_matching_avoids_common_false_positives(self):
        self.assertFalse(alias_matches("GOOGLE NEWS MOMENTUM", "GOOGL"))
        self.assertFalse(alias_matches("AI MOMENTUM BUILDS", "MU"))
        self.assertTrue(alias_matches("MICRON RALLIES ON HBM DEMAND", "MICRON"))
        self.assertTrue(alias_matches("COREWEAVE SIGNS AI DEAL", "COREWEAVE"))

    def test_vanguard_staleness_handles_timezone_aware_imports(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(
                """
                CREATE TABLE imports (
                    source TEXT NOT NULL,
                    path TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    row_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                "INSERT INTO imports (source, path, imported_at, row_count) VALUES (?, ?, ?, ?)",
                ("vanguard", "positions.csv", "2026-05-24T12:00:00+00:00", 10),
            )

            status = vanguard_staleness(conn, 2, now=datetime(2026, 5, 25, 12, tzinfo=timezone.utc))

            self.assertFalse(status["is_stale"])
            self.assertEqual(status["last_import"], "2026-05-24T12:00:00+00:00")
        finally:
            conn.close()

    def test_weekly_research_payload_contains_expanded_ideas(self):
        from datetime import date

        weekly = build_weekly_research(
            date(2026, 5, 24),
            [
                {
                    "symbol": "NVDA",
                    "type": "owned add/trim review",
                    "bucket": "semis_networking_hbm",
                    "score": 50,
                    "setup": "Owned leader needs review.",
                    "evidence": "score 50; 3 signal families.",
                    "trigger": "Backlog confirms.",
                    "risk": "Valuation.",
                    "falsifier": "Margins break.",
                    "signal_families": ["manager", "news", "price"],
                }
            ],
            [
                {
                    "symbol": "NVDA",
                    "bucket": "semis_networking_hbm",
                    "score": 50,
                    "signal_families": ["manager", "news", "price"],
                }
            ],
            {
                "action_queue": [
                    {
                        "symbol": "NVDA",
                        "action": "Add +1.0% on confirmation.",
                        "trade_action": "add",
                        "portfolio_weight": 0.05,
                        "recommended_delta_weight": 0.01,
                        "target_weight": 0.06,
                    }
                ]
            },
            {"regime": "mixed macro tape"},
        )

        self.assertEqual(weekly["title"], "Weekly Idea Research")
        self.assertEqual(weekly["ideas"][0]["symbol"], "NVDA")
        self.assertEqual(weekly["ideas"][0]["trade_action"], "add")
        self.assertTrue(weekly["ideas"][0]["research_questions"])

    def test_methodology_reflects_backend_inputs_and_approval_boundary(self):
        config = AppConfig(
            path=Path("config/invest.toml"),
            data={
                "watchlist": {"symbols": ["NVDA", "GOOGL"]},
                "news": {"queries": ["AI capex"]},
                "risk": {"max_single_name_weight": 0.12, "max_one_ticket_delta": 0.02},
                "managers": [{"key": "situational-awareness", "name": "Situational Awareness LP", "primary": True}],
            },
        )

        methodology = build_methodology(
            config,
            "premarket",
            {"recommendation_posture": "normal", "sources": [{"source": "prices", "status": "ok"}]},
            {"confirmed_card_count": 2, "dominant_families": [{"family": "manager", "count": 2}]},
            {"action_queue": [{"risk_flags": ["ticket_delta_cap"]}]},
            [{"symbol": "NVDA", "score_components": {"manager": 10, "catalyst": 4}}],
            [{"symbol": "NVDA"}],
            [{"symbol": "NVDA"}],
        )

        self.assertTrue(methodology["updated_by_backend"])
        self.assertEqual(methodology["pipeline"]["configured_inputs"]["watchlist_symbol_count"], 2)
        self.assertEqual(methodology["risk_and_sizing"]["limits"]["max_single_name_weight"], 0.12)
        self.assertEqual(methodology["risk_and_sizing"]["order_execution"], "none")
        self.assertIn("ticket_delta_cap", methodology["risk_and_sizing"]["constraint_flags_observed"])

    def test_estimated_earnings_calendar_reduces_data_confidence(self):
        health = build_data_health(
            portfolio={"position_count": 1},
            manager_radar={"stored_latest_count": 1, "manager_count": 1},
            recent_news=[{"title": "AI capex"}],
            prices={"NVDA": {"last": 100}},
            earnings_events=[
                {
                    "symbol": "NVDA",
                    "event_type": "earnings",
                    "source": "alpha_vantage_earnings_calendar",
                    "confirmed_or_estimated": "estimated",
                },
                {
                    "symbol": "NVDA",
                    "event_type": "earnings_catalyst",
                    "source": "news",
                    "confirmed_or_estimated": "estimated",
                }
            ],
            stale_vanguard=None,
            filing_result_count=False,
            broker_result_count=1,
        )

        earnings = next(row for row in health["sources"] if row["source"] == "earnings")
        self.assertEqual(earnings["status"], "estimated")
        self.assertIn("1 forward date candidates; 0 confirmed, 1 estimated; 1 catalyst markers", earnings["detail"])
        self.assertEqual(health["weak_source_count"], 1)
        self.assertEqual(health["recommendation_posture"], "reduced_confidence")

    def test_partial_external_signal_provider_coverage_reduces_data_confidence(self):
        health = build_data_health(
            portfolio={"position_count": 1},
            manager_radar={"stored_latest_count": 1, "manager_count": 1},
            recent_news=[{"title": "AI capex"}],
            prices={"NVDA": {"last": 100}},
            earnings_events=[
                {
                    "symbol": "NVDA",
                    "event_type": "earnings",
                    "source": "manual",
                    "confirmed_or_estimated": "confirmed",
                }
            ],
            stale_vanguard=None,
            filing_result_count=False,
            broker_result_count=1,
            external_signals={
                "status": "ok",
                "provider_count": 3,
                "signal_count": 12,
                "source_statuses": [
                    {"source": "sec_company_data", "label": "SEC company facts", "status": "ok", "detail": "12 parsed."},
                    {"source": "alpha_vantage_news", "label": "Alpha Vantage news", "status": "limited", "detail": "API key missing."},
                    {"source": "finra_short_interest", "label": "FINRA short interest", "status": "limited", "detail": "Runtime budget exhausted."},
                ],
            },
        )

        external = next(row for row in health["sources"] if row["source"] == "external_signals")
        self.assertEqual(external["status"], "limited")
        self.assertIn("1/3 providers ok, 2 limited", external["detail"])
        self.assertIn("Alpha Vantage news: API key missing", external["detail"])
        self.assertIn("FINRA short interest: Runtime budget exhausted", external["detail"])
        self.assertEqual(health["weak_source_count"], 1)
        self.assertEqual(health["recommendation_posture"], "reduced_confidence")

    def test_outcome_diagnostics_render_learning_readiness(self):
        lines = []
        render_outcome_diagnostics(
            lines,
            {
                "outcome_diagnostics": {
                    "status": "awaiting_forward_returns",
                    "current_training_example_count": 4,
                    "completed_outcome_count": 1,
                    "pending_outcome_count": 8,
                    "label_maturity": {
                        "learning_ready": False,
                        "completed_long_horizon_count": 1,
                        "minimum_long_horizon_required": 20,
                        "additional_long_horizon_needed": 19,
                        "pending_outcome_count": 8,
                    },
                    "learning_readiness_projection": {
                        "minimum_long_horizon_required": 20,
                        "projected_long_horizon_count_30d": 1,
                        "projected_additional_needed_30d": 19,
                        "next_learning_label_due_date": "2026-06-24",
                        "next_learning_label_due_count": 4,
                        "projected_long_horizon_count_next_learning_label": 5,
                        "estimated_learning_ready_date": "2026-08-24",
                        "estimated_learning_ready_projected_count": 20,
                        "learning_ready_with_scheduled_pending_labels": True,
                    },
                    "external_learning_readiness_projection": {
                        "minimum_external_long_horizon_required": 20,
                        "projected_external_long_horizon_count_all_scheduled": 5,
                        "projected_external_additional_needed_all_scheduled": 15,
                        "next_external_learning_label_due_date": "2026-06-24",
                        "next_external_learning_label_due_count": 2,
                        "next_external_fast_label_due_date": "2026-05-31",
                        "next_external_fast_label_due_count": 2,
                        "external_fast_labels_due_next_30d": 2,
                        "external_learning_ready_with_scheduled_pending_labels": False,
                    },
                    "horizon_label_counts": [
                        {"horizon": "5d", "completed_count": 1, "pending_count": 4, "missing_price_count": 0},
                        {"horizon": "1m", "completed_count": 1, "pending_count": 4, "missing_price_count": 0},
                    ],
                    "pending_label_schedule": {
                        "next_label": {"horizon": "5d", "due_date": "2026-05-31", "days_until_due": 7, "due_count": 4},
                        "next_learning_label": {"horizon": "1m", "due_date": "2026-06-24", "days_until_due": 31, "due_count": 4},
                        "due_window_counts": {"due_next_7d": 4, "due_next_30d": 4},
                        "learning_due_window_counts": {"due_next_7d": 0, "due_next_30d": 0},
                    },
                    "calibration": {
                        "status": "available",
                        "message": "Tracking.",
                        "mean_error": -2.0,
                        "mean_absolute_error": 4.0,
                        "underprediction_count": 1,
                        "overprediction_count": 2,
                        "sample_count": 2,
                        "minimum_calibration_samples": 20,
                        "additional_samples_needed": 18,
                        "calibration_ready": False,
                    },
                }
            },
        )

        md = "\n".join(lines)
        self.assertIn("Learning readiness: not ready", md)
        self.assertIn("1/20 required 1-12 month labels completed", md)
        self.assertIn("Learning unlock projection: 1/20 labels after 30-day due window; 19 more still needed", md)
        self.assertIn("next learning due 2026-06-24 adds 4 labels -> 5/20", md)
        self.assertIn("estimated ready 2026-08-24 at 20/20", md)
        self.assertIn("External-signal learning projection: 5/20 externally covered labels", md)
        self.assertIn("queued external labels do not yet cover the readiness threshold", md)
        self.assertIn("External-signal fast check: 2 5-day labels due 2026-05-31", md)
        self.assertIn("5d: 1 complete / 4 pending / 0 missing", md)
        self.assertIn("next learning-eligible label 1m due 2026-06-24, in 31 days", md)
        self.assertIn("all labels: 4 due within 7 days, 4 due within 30 days", md)
        self.assertIn("learning labels: 0 due within 7 days, 0 due within 30 days", md)
        self.assertIn("mean absolute error 4.0", md)
        self.assertIn("samples 2/20; 18 more before recalibration", md)
        self.assertIn("underpredicted 1; overpredicted 2", md)

    def test_backtest_summary_renders_calibration_error_shape(self):
        lines = []
        render_backtest_summary(
            lines,
            {
                "backtest": {
                    "status": "tracking",
                    "trial_count": 2,
                    "completed_outcome_count": 2,
                    "pending_outcome_count": 0,
                    "horizons": [{"horizon": "1m", "hit_rate": 0.5}],
                    "calibration": {
                        "status": "available",
                        "mean_error": -2.0,
                        "mean_absolute_error": 4.0,
                        "underprediction_count": 1,
                        "overprediction_count": 1,
                        "sample_count": 2,
                        "minimum_calibration_samples": 20,
                        "additional_samples_needed": 18,
                        "calibration_ready": False,
                        "priority_bucket": {
                            "key": "high_expected",
                            "completed_count": 1,
                            "mean_error": -12.0,
                            "mean_absolute_error": 12.0,
                            "bias": "overprediction",
                        },
                        "buckets": [
                            {
                                "key": "mid_expected",
                                "completed_count": 2,
                                "mean_error": 1.0,
                                "mean_absolute_error": 8.0,
                                "underprediction_count": 1,
                                "overprediction_count": 1,
                            },
                            {
                                "key": "high_expected",
                                "completed_count": 1,
                                "mean_error": -12.0,
                                "mean_absolute_error": 12.0,
                                "underprediction_count": 0,
                                "overprediction_count": 1,
                            },
                        ],
                    },
                    "by_external_feed_status": [
                        {
                            "key": "limited",
                            "completed_count": 2,
                            "mean_error": -12.0,
                            "mean_absolute_error": 12.0,
                            "underprediction_count": 0,
                            "overprediction_count": 2,
                        },
                        {
                            "key": "unknown",
                            "completed_count": 1,
                            "mean_error": 1.0,
                            "mean_absolute_error": 1.0,
                            "underprediction_count": 1,
                            "overprediction_count": 0,
                        },
                    ],
                    "by_external_coverage": [
                        {
                            "key": "thin_coverage",
                            "completed_count": 2,
                            "mean_error": -12.0,
                            "mean_absolute_error": 12.0,
                            "underprediction_count": 0,
                            "overprediction_count": 2,
                        },
                    ],
                    "by_external_alignment": [
                        {
                            "key": "conflict",
                            "completed_count": 1,
                            "mean_error": -8.0,
                            "mean_absolute_error": 8.0,
                            "underprediction_count": 0,
                            "overprediction_count": 1,
                        },
                    ],
                    "pending_external_alignment_due_dates": [
                        {"due_date": "2026-06-02", "due_count": 4, "conflict_count": 1, "aligned_count": 1}
                    ],
                    "pending_external_coverage_gap_count": 2,
                    "pending_external_coverage_gap_plan": {
                        "additional_external_coverage_needed": 2,
                        "candidate_gap_count": 2,
                        "minimum_external_long_horizon_required": 20,
                        "priority_gap_count": 2,
                        "priority_acceptance_check_count": 2,
                        "priority_open_acceptance_check_count": 2,
                        "projected_external_long_horizon_count_after_priority_backfill": 20,
                        "external_learning_ready_after_priority_backfill": True,
                        "priority_rows": [
                            {
                                "external_coverage_gap_id": "gap-amd-1m",
                                "symbol": "AMD",
                                "horizon": "1m",
                                "due_date": "2026-06-25",
                                "external_coverage_acceptance_checks": [{"check": "external_feed_status_present"}],
                            },
                            {"external_coverage_gap_id": "gap-asml-1m", "symbol": "ASML", "horizon": "1m", "due_date": "2026-06-25"},
                        ],
                    },
                    "pending_external_coverage_gap_queue": [
                        {"symbol": "AMD", "horizon": "1m", "due_date": "2026-06-25"},
                        {"symbol": "ASML", "horizon": "1m", "due_date": "2026-06-25"},
                    ],
                }
            },
        )

        md = "\n".join(lines)
        self.assertIn("Expected-vs-realized: available", md)
        self.assertIn("mean error -2.0", md)
        self.assertIn("mean absolute error 4.0", md)
        self.assertIn("samples 2/20; 18 more before recalibration", md)
        self.assertIn("underpredicted 1; overpredicted 1", md)
        self.assertIn("Calibration bands: mid_expected (2 labels, mean error 1.0", md)
        self.assertIn("high_expected (1 labels, mean error -12.0", md)
        self.assertIn("Calibration priority: high_expected has highest absolute error 12.0", md)
        self.assertIn("overprediction bias", md)
        self.assertIn("External feed status outcomes: limited (2 labels, mean error -12.0", md)
        self.assertIn("unknown (1 labels, mean error 1.0", md)
        self.assertIn("External coverage outcomes: thin_coverage (2 labels, mean error -12.0", md)
        self.assertIn("External alignment outcomes: conflict (1 labels, mean error -8.0", md)
        self.assertIn("Pending external alignment due dates: 2026-06-02: 4 labels (1 conflict, 1 aligned)", md)
        self.assertIn("External coverage gap priority: 2 labels needed", md)
        self.assertIn("gap-amd-1m", md)
        self.assertIn("backfill policy decision_time_only", md)
        self.assertIn("2/2 acceptance checks open", md)
        self.assertIn("projected 20/20 external labels, ready", md)
        self.assertIn("External coverage gap queue: 2 long-horizon labels missing external coverage", md)
        self.assertIn("AMD 1m due 2026-06-25", md)

    def test_generate_brief_feeds_completed_backtest_labels_into_engine_learning(self):
        completed = {
            "status": "complete",
            "outcome_id": "outcome-1",
            "trial_id": "trial-1",
            "symbol": "NVDA",
            "horizon": "1m",
            "as_of": "2026-05-01",
            "decision_forward_return_pct": 12.0,
            "raw_forward_return_pct": 12.0,
            "risk_adjusted_expected_return": 10.0,
            "signal_families": ["manager"],
            "bucket": "semis_networking_hbm",
            "trade_action": "add",
        }
        with tempfile.TemporaryDirectory() as tmp:
            config = AppConfig(
                path=Path("config/invest.toml"),
                data={
                    "reports": {"directory": tmp},
                    "vanguard": {"enabled": False},
                    "managers": [{"key": "situational-awareness", "name": "Situational Awareness LP", "primary": True}],
                    "thesis_buckets": [{"key": "semis_networking_hbm", "symbols": ["NVDA"]}],
                },
            )
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            try:
                with ExitStack() as stack:
                    for mock in [
                        patch("invest.reports.fetch_many", return_value=[]),
                        patch("invest.db.insert_news"),
                        patch("invest.reports.latest_manager_filing", return_value={"form": "13F-HR"}),
                        patch("invest.reports.latest_filing_values", return_value={}),
                        patch("invest.reports.fetch_daily_prices", return_value={"NVDA": {"last": 100}}),
                        patch("invest.reports.build_portfolio_exposure", return_value={"position_count": 1, "by_symbol": [{"symbol": "NVDA", "weight": 0.08}], "by_bucket": []}),
                        patch("invest.reports.portfolio_values_by_symbol", return_value={"NVDA": 1000}),
                        patch("invest.reports.build_manager_radar", return_value={"by_symbol": {}, "stored_latest_count": 1, "manager_count": 1}),
                        patch("invest.reports.build_research_universe", return_value=["NVDA"]),
                        patch("invest.reports.fetch_return_windows", return_value={}),
                        patch("invest.reports.manager_valuation_symbols", return_value=[]),
                        patch("invest.reports.build_manager_valuation_snapshot", return_value={}),
                        patch("invest.reports.attach_manager_valuations", side_effect=lambda radar, valuation: radar),
                        patch("invest.reports.build_portfolio_valuation_snapshot", return_value={}),
                        patch("invest.reports.transactions_since", return_value=[]),
                        patch("invest.reports.latest_news", return_value=[]),
                        patch("invest.reports.build_news_event_signals", return_value={}),
                        patch("invest.reports.build_earnings_events", return_value=[]),
                        patch("invest.reports.build_fred_macro_snapshot", return_value={}),
                        patch("invest.reports.build_macro_dashboard", return_value={}),
                        patch("invest.reports.build_external_signal_snapshot", return_value={}),
                        patch("invest.reports.build_decision_cards", return_value=[{"symbol": "NVDA", "bucket": "semis_networking_hbm"}]),
                        patch("invest.reports.build_idea_book", return_value=[]),
                        patch("invest.reports.top_catalyst_signals", return_value=[]),
                        patch("invest.reports.build_signal_synthesis", return_value={}),
                        patch("invest.reports.build_underwriting_layers", return_value={"company_underwriting": {}, "sector_underwriting": {}}),
                        patch("invest.reports.build_feature_matrix", return_value={"rows": [{"symbol": "NVDA", "signal_families": ["manager"]}]}),
                        patch("invest.reports.build_research_book", return_value={"items": [{"symbol": "NVDA", "risk_adjusted_expected_return": 10}]}),
                        patch("invest.reports.build_portfolio_benchmark", return_value={"action_queue": []}),
                        patch("invest.reports.build_approval_tickets", return_value=[{"ticket_id": "t1", "symbol": "NVDA", "trade_action": "add", "recommended_delta_weight": 0.01, "target_weight": 0.09}]),
                        patch("invest.reports.build_backtest_summary", return_value={"outcomes": [completed], "completed_outcome_count": 1, "pending_outcome_count": 0}),
                        patch("invest.reports.build_methodology", return_value={}),
                        patch("invest.reports.build_audit_snapshot", return_value={"overall_status": "ok", "data_gaps": []}),
                        patch("invest.reports.build_recommendation_explanations", return_value=[]),
                        patch("invest.reports.build_review_queue", return_value=[]),
                        patch("invest.reports.build_instrumentation_audit", return_value={"status": "ok", "check_count": 0, "failure_count": 0}),
                        patch("invest.reports.render_markdown", return_value="# ok"),
                    ]:
                        stack.enter_context(mock)
                    engine = stack.enter_context(patch("invest.reports.build_engine_snapshot", return_value={"version": "engine", "learning": {}, "feature_count": 1}))
                    paper = stack.enter_context(patch("invest.reports.build_paper_portfolio", return_value={"metrics": {}}))
                    generate_brief(conn, config, "postmarket", as_of=date(2026, 5, 24))
            finally:
                conn.close()

        history = engine.call_args.kwargs["outcome_history"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["horizon"], "1m")
        self.assertEqual(history[0]["forward_return_pct"], 12.0)
        self.assertEqual(paper.call_args.args[5], history)


if __name__ == "__main__":
    unittest.main()
