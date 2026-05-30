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
    attach_data_health_approval_blocker_summary,
    build_approval_tickets,
    build_data_health,
    build_methodology,
    build_research_universe,
    build_weekly_research,
    generate_brief,
    render_backtest_summary,
    render_data_health,
    render_markdown,
    render_outcome_diagnostics,
    vanguard_staleness,
)


class ReportTests(unittest.TestCase):
    def test_approval_tickets_preserve_action_confirmation_and_external_metadata(self):
        tickets = build_approval_tickets(
            date(2026, 5, 26),
            "intraday",
            {"gross_exposure": 1_000_000},
            {
                "action_queue": [
                    {
                        "symbol": "AVGO",
                        "trade_action": "add",
                        "current_weight": 0.05,
                        "recommended_delta_weight": 0.03,
                        "post_action_weight": 0.08,
                        "target_weight": 0.08,
                        "model_target_weight": 0.08,
                        "earnings_days_until": 8,
                        "earnings_event_date": "2026-06-03",
                        "earnings_event_source": "nasdaq_earnings_calendar",
                        "earnings_confirmed_or_estimated": "estimated",
                        "earnings_risk_window": "clear",
                        "earnings_confirmation_required": True,
                        "external_feed_status": "limited",
                        "external_coverage_multiplier": 0.3333,
                        "external_provider_ok_ratio": 0.3333,
                        "coverage_adjusted_external_signal_score": 4.79,
                    }
                ]
            },
            [{"symbol": "AVGO", "bucket": "semis_networking_hbm", "last_price": 100.0}],
        )

        ticket = tickets[0]

        self.assertTrue(ticket["earnings_confirmation_required"])
        self.assertEqual(ticket["earnings_event_date"], "2026-06-03")
        self.assertEqual(ticket["earnings_confirmed_or_estimated"], "estimated")
        self.assertEqual(ticket["external_feed_status"], "limited")
        self.assertEqual(ticket["external_coverage_multiplier"], 0.3333)
        self.assertEqual(ticket["coverage_adjusted_external_signal_score"], 4.79)
        self.assertEqual(ticket["approval_gate_status"], "blocked_until_confirmation")
        self.assertEqual(ticket["approval_open_check_count"], 2)
        approval_checks = {check["check"]: check for check in ticket["approval_checks"]}
        self.assertEqual(approval_checks["earnings_date_confirmed"]["status"], "pending")
        self.assertEqual(approval_checks["external_feed_reliability_reviewed"]["status"], "pending")

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

    def test_research_universe_includes_manager_discovery_symbols(self):
        config = AppConfig(
            path=Path("config/invest.toml"),
            data={"watchlist": {"symbols": ["NVDA"]}},
        )

        universe = build_research_universe(
            config,
            {"by_symbol": []},
            {
                "focus_managers": [],
                "top_adds": [
                    {"symbol": "CEG", "bucket": "power_grid_gas_nuclear", "manager_count": 4, "delta_value": 800_000_000}
                ],
                "top_consensus": [
                    {"symbol": "VST", "bucket": "unmapped", "common_manager_count": 5, "common_value": 2_000_000_000}
                ],
            },
        )

        self.assertIn("NVDA", universe)
        self.assertIn("CEG", universe)
        self.assertIn("VST", universe)
        constrained = build_research_universe(
            config,
            {"by_symbol": []},
            {
                "focus_managers": [],
                "top_adds": [
                    {"symbol": "CEG", "bucket": "power_grid_gas_nuclear", "manager_count": 4, "delta_value": 800_000_000}
                ],
            },
            max_symbols=2,
        )
        self.assertEqual(constrained[0], "NVDA")
        self.assertIn("CEG", constrained)

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

    def test_data_health_render_includes_approval_blocker_summary(self):
        lines = []
        render_data_health(
            lines,
            {
                "data_health": {
                    "recommendation_posture": "reduced_confidence",
                    "summary": "Recommendations are constrained by data freshness.",
                    "approval_blocker_summary": {
                        "status": "attention",
                        "total_source_blocker_count": 7,
                        "external_gap_ticket_count": 5,
                        "earnings_confirmation_ticket_count": 2,
                        "blocked_ticket_count": 5,
                        "blocked_symbols": ["AVGO", "GOOG", "MRVL", "MU", "NVDA"],
                        "open_check_count": 11,
                        "open_check_counts": {
                            "external_feed_reliability_reviewed": 5,
                            "risk_flags_reviewed": 4,
                            "earnings_date_confirmed": 2,
                        },
                        "provider_gap_source_counts": {
                            "alpha_vantage_news": 5,
                            "eia_energy_power": 5,
                        },
                        "provider_gap_severity_counts": {
                            "configuration_required": 10,
                        },
                        "confirmation_priority_counts": {
                            "p0_blackout_confirmation": 1,
                            "p2_pre_risk_window_backfill": 1,
                        },
                        "next_confirmation_deadline": "2026-05-26",
                        "next_confirmation_symbols": ["MRVL"],
                    },
                    "sources": [
                        {
                            "source": "external_signals",
                            "label": "External signal feeds",
                            "status": "limited",
                            "detail": "2/6 providers ok.",
                            "approval_blocked_external_gaps": [
                                {
                                    "symbol": "AVGO",
                                    "approval_gate_status": "review_required",
                                    "trade_action": "add",
                                    "recommended_delta_weight": 0.03,
                                    "approval_open_check_count": 2,
                                    "provider_gap_sources": ["alpha_vantage_news", "eia_energy_power"],
                                    "provider_gap_severities": ["configuration_required"],
                                }
                            ],
                        }
                    ],
                }
            },
        )

        text = "\n".join(lines)

        self.assertIn("Approval blockers: 5 blocked tickets; 7 source blockers; 11 open checks", text)
        self.assertIn("symbols AVGO, GOOG, MRVL, MU, NVDA", text)
        self.assertIn("open checks external_feed_reliability_reviewed=5, risk_flags_reviewed=4, earnings_date_confirmed=2", text)
        self.assertIn("provider gaps alpha_vantage_news=5, eia_energy_power=5", text)
        self.assertIn("gap severities configuration_required=10", text)
        self.assertIn("next confirmation 2026-05-26 (MRVL)", text)
        self.assertIn(
            "Approval-blocked external tickets: AVGO (review_required; add +3.0%; 2 open checks; providers alpha_vantage_news, eia_energy_power; gap severities configuration required)",
            text,
        )

    def test_data_health_render_includes_confirmation_deadline_context(self):
        lines = []
        render_data_health(
            lines,
            {
                "data_health": {
                    "recommendation_posture": "reduced_confidence",
                    "summary": "Recommendations are constrained by data freshness.",
                    "sources": [
                        {
                            "source": "earnings",
                            "label": "Earnings calendar",
                            "status": "estimated",
                            "detail": "2 estimated forward dates.",
                            "confirmation_gaps": [
                                {
                                    "symbol": "MRVL",
                                    "event_date": "2026-05-27",
                                    "risk_window": "blackout",
                                    "confirmation_priority": "p0_blackout_confirmation",
                                    "confirmation_deadline": "2026-05-26",
                                    "days_to_confirmation_deadline": 0,
                                    "remediation": "Confirm the earnings date via company IR.",
                                },
                                {
                                    "symbol": "AVGO",
                                    "event_date": "2026-06-03",
                                    "risk_window": "clear",
                                    "confirmation_priority": "p2_pre_risk_window_backfill",
                                    "confirmation_deadline": "2026-05-27",
                                    "days_to_confirmation_deadline": 1,
                                    "remediation": "Backfill company IR/manual confirmation.",
                                },
                            ],
                            "approval_blocked_confirmation_gaps": [
                                {
                                    "symbol": "MRVL",
                                    "approval_gate_status": "blocked_until_confirmation",
                                    "trade_action": "trim",
                                    "recommended_delta_weight": -0.01,
                                    "approval_open_check_count": 3,
                                    "event_date": "2026-05-27",
                                    "confirmation_deadline": "2026-05-26",
                                    "days_to_confirmation_deadline": 0,
                                    "confirmation_priority": "p0_blackout_confirmation",
                                }
                            ],
                        }
                    ],
                }
            },
        )

        text = "\n".join(lines)

        self.assertIn(
            "MRVL 2026-05-27 (blackout, p0_blackout_confirmation, deadline 2026-05-26 today)",
            text,
        )
        self.assertIn(
            "AVGO 2026-06-03 (clear, p2_pre_risk_window_backfill, deadline 2026-05-27 in 1d)",
            text,
        )
        self.assertIn(
            "Approval-blocked confirmation tickets: MRVL (blocked_until_confirmation; trim -1.0%; 3 open checks; event 2026-05-27; deadline 2026-05-26 today; p0_blackout_confirmation)",
            text,
        )

    def test_report_data_health_summary_derived_from_approval_tickets(self):
        data_health = {
            "sources": [
                {
                    "source": "external_signals",
                    "provider_gaps": [
                        {"source": "alpha_vantage_news", "severity": "configuration_required"},
                        {"source": "gdelt_global_news", "severity": "transient_network"},
                    ],
                },
                {
                    "source": "earnings",
                    "confirmation_gaps": [
                        {
                            "symbol": "MRVL",
                            "confirmation_deadline": "2026-05-26",
                            "confirmation_priority": "p0_blackout_confirmation",
                        }
                    ],
                },
            ]
        }
        tickets = [
            {
                "ticket_id": "ticket-mrvl",
                "symbol": "MRVL",
                "trade_action": "trim",
                "recommended_delta_weight": -0.01,
                "approval_checks": [
                    {"check": "external_feed_reliability_reviewed", "status": "pending"},
                    {"check": "earnings_date_confirmed", "status": "pending"},
                    {"check": "risk_flags_reviewed", "status": "pending"},
                ],
            },
            {
                "ticket_id": "ticket-nvda",
                "symbol": "NVDA",
                "approval_checks": [
                    {"check": "external_feed_reliability_reviewed", "status": "pending"},
                ],
            },
        ]

        attach_data_health_approval_blocker_summary(data_health, tickets)

        summary = data_health["approval_blocker_summary"]
        self.assertEqual(summary["status"], "attention")
        self.assertEqual(summary["total_source_blocker_count"], 3)
        self.assertEqual(summary["external_gap_ticket_count"], 2)
        self.assertEqual(summary["earnings_confirmation_ticket_count"], 1)
        self.assertEqual(summary["blocked_ticket_count"], 2)
        self.assertEqual(summary["blocked_symbols"], ["MRVL", "NVDA"])
        self.assertEqual(
            summary["open_check_counts"],
            {"earnings_date_confirmed": 1, "external_feed_reliability_reviewed": 2, "risk_flags_reviewed": 1},
        )
        self.assertEqual(summary["provider_gap_source_counts"], {"alpha_vantage_news": 2, "gdelt_global_news": 2})
        self.assertEqual(summary["provider_gap_severity_counts"], {"configuration_required": 2, "transient_network": 2})
        self.assertEqual(summary["confirmation_priority_counts"], {"p0_blackout_confirmation": 1})
        self.assertEqual(summary["next_confirmation_deadline"], "2026-05-26")
        self.assertEqual(summary["next_confirmation_symbols"], ["MRVL"])
        external_source = next(row for row in data_health["sources"] if row["source"] == "external_signals")
        self.assertEqual(external_source["approval_blocked_external_gap_count"], 2)
        self.assertEqual(external_source["approval_blocked_external_gaps"][0]["ticket_id"], "ticket-mrvl")
        self.assertEqual(external_source["approval_blocked_external_gaps"][0]["trade_action"], "trim")
        self.assertEqual(external_source["approval_blocked_external_gaps"][0]["recommended_delta_weight"], -0.01)
        self.assertEqual(external_source["approval_blocked_external_gaps"][0]["provider_gap_count"], 2)
        self.assertEqual(
            external_source["approval_blocked_external_gaps"][0]["provider_gap_sources"],
            ["alpha_vantage_news", "gdelt_global_news"],
        )
        self.assertEqual(
            external_source["approval_blocked_external_gaps"][0]["provider_gap_severities"],
            ["configuration_required", "transient_network"],
        )
        earnings_source = next(row for row in data_health["sources"] if row["source"] == "earnings")
        self.assertEqual(earnings_source["action_linked_confirmation_gap_count"], 1)
        self.assertEqual(earnings_source["approval_blocked_confirmation_gap_count"], 1)
        self.assertEqual(earnings_source["approval_blocked_confirmation_gaps"][0]["ticket_id"], "ticket-mrvl")
        self.assertEqual(earnings_source["approval_blocked_confirmation_gaps"][0]["approval_gate_status"], "blocked_until_confirmation")
        self.assertTrue(earnings_source["confirmation_gaps"][0]["approval_ticket_linked"])

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
                    "approval_learning_readiness_projection": {
                        "pending_approval_label_count": 6,
                        "pending_approval_learning_label_count": 4,
                        "pending_approval_fast_label_count": 2,
                        "pending_approval_blocker_bucket_count": 2,
                        "pending_approval_blocker_buckets": [
                            {"key": "review_required", "pending_count": 4, "next_due_date": "2026-05-31"},
                            {"key": "blocked_until_confirmation", "pending_count": 2, "next_due_date": "2026-06-24"},
                        ],
                        "next_approval_label_due_date": "2026-05-31",
                        "next_approval_label_due_count": 4,
                        "next_approval_learning_label_due_date": "2026-06-24",
                        "next_approval_learning_label_due_count": 2,
                    },
                    "approval_data_friction_learning_readiness_projection": {
                        "pending_approval_data_friction_label_count": 6,
                        "pending_approval_data_friction_learning_label_count": 4,
                        "pending_approval_data_friction_fast_label_count": 2,
                        "pending_approval_data_friction_bucket_count": 2,
                        "pending_approval_data_friction_buckets": [
                            {"key": "external_review", "pending_count": 4, "next_due_date": "2026-05-31"},
                            {"key": "earnings_and_external_review", "pending_count": 2, "next_due_date": "2026-06-24"},
                        ],
                        "next_approval_data_friction_label_due_date": "2026-05-31",
                        "next_approval_data_friction_label_due_count": 4,
                        "next_approval_data_friction_learning_label_due_date": "2026-06-24",
                        "next_approval_data_friction_learning_label_due_count": 2,
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
        self.assertIn("Approval-gated label projection: 6 pending labels across 2 blocker buckets", md)
        self.assertIn("review required 4 labels next 2026-05-31", md)
        self.assertIn("blocked until confirmation 2 labels next 2026-06-24", md)
        self.assertIn("next learning-eligible approval label due 2026-06-24 adds 2 labels", md)
        self.assertIn("Approval data-friction label projection: 6 pending labels across 2 friction buckets", md)
        self.assertIn("external review 4 labels next 2026-05-31", md)
        self.assertIn("earnings and external review 2 labels next 2026-06-24", md)
        self.assertIn("next learning-eligible friction label due 2026-06-24 adds 2 labels", md)
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
                    "by_external_provider_gap_severity": [
                        {
                            "key": "configuration_required",
                            "completed_count": 2,
                            "mean_error": -9.0,
                            "mean_absolute_error": 9.0,
                            "underprediction_count": 0,
                            "overprediction_count": 2,
                        },
                    ],
                    "by_external_provider_gap_severity_exposure": [
                        {
                            "key": "configuration_required",
                            "completed_count": 2,
                            "mean_error": -9.0,
                            "mean_absolute_error": 9.0,
                            "underprediction_count": 0,
                            "overprediction_count": 2,
                        },
                        {
                            "key": "runtime_budget",
                            "completed_count": 2,
                            "mean_error": -7.0,
                            "mean_absolute_error": 7.0,
                            "underprediction_count": 0,
                            "overprediction_count": 2,
                        },
                    ],
                    "by_approval_data_friction_bucket": [
                        {
                            "key": "earnings_and_external_review",
                            "completed_count": 2,
                            "mean_error": -10.0,
                            "mean_absolute_error": 10.0,
                            "underprediction_count": 0,
                            "overprediction_count": 2,
                        },
                    ],
                    "by_earnings_confirmation_bucket": [
                        {
                            "key": "confirmation_required",
                            "completed_count": 1,
                            "mean_error": -6.0,
                            "mean_absolute_error": 6.0,
                            "underprediction_count": 0,
                            "overprediction_count": 1,
                        },
                    ],
                    "pending_by_earnings_confirmation_bucket": [
                        {"key": "no_event", "pending_count": 2, "next_due_date": "2026-06-02"},
                        {"key": "confirmation_required", "pending_count": 2, "next_due_date": "2026-06-25"},
                    ],
                    "pending_by_earnings_risk_window": [
                        {"key": "unknown", "pending_count": 2, "next_due_date": "2026-06-02"},
                        {"key": "blackout", "pending_count": 1, "next_due_date": "2026-06-25"},
                        {"key": "clear", "pending_count": 1, "next_due_date": "2026-06-25"},
                    ],
                    "pending_by_approval_blocker_bucket": [
                        {"key": "no_approval_context", "pending_count": 2, "next_due_date": "2026-06-02"},
                        {"key": "blocked_until_confirmation", "pending_count": 1, "next_due_date": "2026-06-25"},
                        {"key": "review_required", "pending_count": 2, "next_due_date": "2026-06-25"},
                    ],
                    "pending_by_approval_data_friction_bucket": [
                        {"key": "clear", "pending_count": 2, "next_due_date": "2026-06-02"},
                        {"key": "earnings_and_external_review", "pending_count": 3, "next_due_date": "2026-06-25"},
                    ],
                    "pending_by_external_provider_gap_severity": [
                        {"key": "unknown", "pending_count": 2, "next_due_date": "2026-06-02"},
                        {"key": "configuration_required", "pending_count": 3, "next_due_date": "2026-06-25"},
                    ],
                    "pending_by_external_provider_gap_severity_exposure": [
                        {"key": "configuration_required", "pending_count": 3, "next_due_date": "2026-06-25"},
                        {"key": "runtime_budget", "pending_count": 3, "next_due_date": "2026-06-25"},
                        {"key": "transient_network", "pending_count": 3, "next_due_date": "2026-06-25"},
                        {"key": "unknown", "pending_count": 2, "next_due_date": "2026-06-02"},
                    ],
                    "pending_external_provider_gap_severity_observation_summary": {
                        "pending_label_count": 5,
                        "observed_label_count": 3,
                        "unknown_label_count": 2,
                        "observed_ratio": 0.6,
                        "unknown_next_due_date": "2026-06-02",
                        "backfill_policy": "decision_time_only",
                    },
                    "pending_external_provider_gap_severity_observation_gap_count": 2,
                    "pending_external_provider_gap_severity_observation_gap_hidden_label_count": 0,
                    "pending_external_provider_gap_severity_observation_gap_queue": [
                        {"symbol": "AMD", "horizon": "5d", "due_date": "2026-06-02"},
                        {"symbol": "ASML", "horizon": "1m", "due_date": "2026-06-25"},
                    ],
                    "pending_external_provider_gap_severity_observation_gap_work_item_count": 2,
                    "pending_external_provider_gap_severity_observation_gap_visible_work_item_label_count": 2,
                    "pending_external_provider_gap_severity_observation_gap_hidden_work_item_label_count": 0,
                    "pending_external_provider_gap_severity_observation_gap_hidden_work_item_count": 0,
                    "pending_external_provider_gap_severity_observation_gap_hidden_calibration_work_item_count": 1,
                    "pending_external_provider_gap_severity_observation_gap_hidden_calibration_work_item_queue_limit": 8,
                    "pending_external_provider_gap_severity_observation_gap_hidden_calibration_work_item_queue": [
                        {
                            "external_provider_gap_severity_observation_work_item_id": "hidden-calibration-asml-1m",
                            "symbol": "ASML",
                            "horizon": "1m",
                            "as_of": "2026-05-24",
                            "session": "premarket",
                            "decision_time_report_json": "2026-05-24-premarket.json",
                            "decision_time_report_markdown": "2026-05-24-premarket.md",
                            "decision_time_report_json_available": True,
                            "decision_time_report_markdown_available": True,
                            "due_date": "2026-06-25",
                            "label_count": 1,
                            "candidate_backfill_status": "ready",
                            "candidate_source_section": "external_signals.source_statuses",
                            "candidate_backfill_policy": "decision_time_external_signals_provider_status_only",
                            "candidate_backfill_values": {
                                "external_provider_gap_count": 2,
                                "external_provider_configuration_gap_count": 1,
                                "external_provider_runtime_gap_count": 0,
                                "external_provider_stale_gap_count": 0,
                                "external_provider_transient_gap_count": 1,
                                "external_provider_other_gap_count": 0,
                                "external_provider_primary_gap_severity": "configuration_required",
                                "external_provider_gap_severity_score": 45.0,
                            },
                        }
                    ],
                    "pending_external_provider_gap_severity_observation_gap_hidden_calibration_report_batch_count": 1,
                    "pending_external_provider_gap_severity_observation_gap_hidden_calibration_report_batch_queue_limit": 8,
                    "pending_external_provider_gap_severity_observation_gap_hidden_calibration_report_batch_queue": [
                        {
                            "decision_time_report_json": "2026-05-24-premarket.json",
                            "decision_time_report_markdown": "2026-05-24-premarket.md",
                            "decision_time_report_json_available": True,
                            "decision_time_report_markdown_available": True,
                            "as_of": "2026-05-24",
                            "session": "premarket",
                            "label_count": 1,
                            "work_item_count": 1,
                            "due_date_count": 1,
                            "earliest_due_date": "2026-06-25",
                            "latest_due_date": "2026-06-25",
                            "horizons": ["1m"],
                            "symbols": ["ASML"],
                            "symbol_count": 1,
                            "candidate_backfill_status": "ready",
                            "candidate_source_section": "external_signals.source_statuses",
                            "candidate_backfill_policy": "decision_time_external_signals_provider_status_only",
                            "candidate_backfill_values": {
                                "external_provider_gap_count": 2,
                                "external_provider_configuration_gap_count": 1,
                                "external_provider_runtime_gap_count": 0,
                                "external_provider_stale_gap_count": 0,
                                "external_provider_transient_gap_count": 1,
                                "external_provider_other_gap_count": 0,
                                "external_provider_primary_gap_severity": "configuration_required",
                                "external_provider_gap_severity_score": 45.0,
                            },
                        }
                    ],
                    "pending_external_provider_gap_severity_observation_gap_hidden_calibration_backfill_record_count": 1,
                    "pending_external_provider_gap_severity_observation_gap_hidden_calibration_backfill_record_queue_limit": 8,
                    "pending_external_provider_gap_severity_observation_gap_hidden_calibration_backfill_record_queue": [
                        {
                            "external_provider_gap_severity_observation_backfill_record_id": "record-asml-1m",
                            "external_provider_gap_severity_observation_work_item_id": "hidden-calibration-asml-1m",
                            "candidate_apply_status": "ready",
                            "candidate_apply_policy": "update_matching_recommendation_training_examples_by_source_trial_id",
                            "target_section": "recommendation_training_examples",
                            "symbol": "ASML",
                            "horizon": "1m",
                            "decision_as_of": "2026-05-24",
                            "session": "premarket",
                            "due_date": "2026-06-25",
                            "source_report": "2026-05-24-premarket.json",
                            "source_report_available": True,
                            "source_outcome_ids": ["outcome-asml-1m"],
                            "source_trial_ids": ["trial-asml"],
                            "fields_to_backfill": [
                                "external_provider_gap_count",
                                "external_provider_configuration_gap_count",
                                "external_provider_runtime_gap_count",
                                "external_provider_stale_gap_count",
                                "external_provider_transient_gap_count",
                                "external_provider_other_gap_count",
                                "external_provider_primary_gap_severity",
                                "external_provider_gap_severity_score",
                            ],
                            "candidate_source_section": "external_signals.source_statuses",
                            "candidate_backfill_policy": "decision_time_external_signals_provider_status_only",
                            "candidate_backfill_values": {
                                "external_provider_gap_count": 2,
                                "external_provider_configuration_gap_count": 1,
                                "external_provider_runtime_gap_count": 0,
                                "external_provider_stale_gap_count": 0,
                                "external_provider_transient_gap_count": 1,
                                "external_provider_other_gap_count": 0,
                                "external_provider_primary_gap_severity": "configuration_required",
                                "external_provider_gap_severity_score": 45.0,
                            },
                        }
                    ],
                    "pending_external_provider_gap_severity_observation_gap_work_item_queue": [
                        {
                            "symbol": "AMD",
                            "horizon": "5d",
                            "as_of": "2026-05-24",
                            "session": "premarket",
                            "due_date": "2026-06-02",
                            "days_until_due": 9,
                            "due_window": "due_next_30d",
                            "label_count": 1,
                        },
                        {
                            "symbol": "ASML",
                            "horizon": "1m",
                            "as_of": "2026-05-24",
                            "session": "premarket",
                            "due_date": "2026-06-25",
                            "days_until_due": 32,
                            "due_window": "later",
                            "label_count": 1,
                        },
                    ],
                    "pending_external_provider_gap_severity_observation_gap_due_dates": [
                        {
                            "due_date": "2026-06-02",
                            "days_until_due": 9,
                            "due_window": "due_next_30d",
                            "label_count": 1,
                            "work_item_count": 1,
                            "visible_label_count": 1,
                            "visible_work_item_count": 1,
                            "hidden_label_count": 0,
                            "hidden_work_item_count": 0,
                            "cumulative_label_count": 1,
                            "cumulative_work_item_count": 1,
                            "cumulative_visible_label_count": 1,
                            "cumulative_visible_work_item_count": 1,
                            "cumulative_hidden_label_count": 0,
                            "cumulative_hidden_work_item_count": 0,
                            "horizons": ["5d"],
                            "symbols": ["AMD"],
                        },
                        {
                            "due_date": "2026-06-25",
                            "days_until_due": 32,
                            "due_window": "later",
                            "label_count": 1,
                            "work_item_count": 1,
                            "visible_label_count": 1,
                            "visible_work_item_count": 1,
                            "hidden_label_count": 0,
                            "hidden_work_item_count": 0,
                            "cumulative_label_count": 2,
                            "cumulative_work_item_count": 2,
                            "cumulative_visible_label_count": 2,
                            "cumulative_visible_work_item_count": 2,
                            "cumulative_hidden_label_count": 0,
                            "cumulative_hidden_work_item_count": 0,
                            "horizons": ["1m"],
                            "symbols": ["ASML"],
                        },
                    ],
                    "pending_external_provider_gap_severity_observation_gap_due_window_counts": [
                        {
                            "due_window": "due_next_30d",
                            "label_count": 1,
                            "work_item_count": 1,
                            "visible_label_count": 1,
                            "visible_work_item_count": 1,
                            "hidden_label_count": 0,
                            "hidden_work_item_count": 0,
                            "due_date_count": 1,
                            "earliest_due_date": "2026-06-02",
                            "latest_due_date": "2026-06-02",
                        },
                        {
                            "due_window": "later",
                            "label_count": 1,
                            "work_item_count": 1,
                            "visible_label_count": 1,
                            "visible_work_item_count": 1,
                            "hidden_label_count": 0,
                            "hidden_work_item_count": 0,
                            "due_date_count": 1,
                            "earliest_due_date": "2026-06-25",
                            "latest_due_date": "2026-06-25",
                        },
                    ],
                    "pending_external_provider_gap_severity_observation_gap_horizon_counts": [
                        {
                            "horizon": "5d",
                            "learning_role": "fast_check",
                            "label_count": 1,
                            "work_item_count": 1,
                            "visible_label_count": 1,
                            "visible_work_item_count": 1,
                            "hidden_label_count": 0,
                            "hidden_work_item_count": 0,
                            "due_date_count": 1,
                            "next_due_date": "2026-06-02",
                            "latest_due_date": "2026-06-02",
                            "days_until_next_due": 9,
                            "next_due_window": "due_next_30d",
                            "next_visible_due_date": "2026-06-02",
                            "latest_visible_due_date": "2026-06-02",
                            "days_until_next_visible_due": 9,
                            "next_visible_due_window": "due_next_30d",
                            "next_visible_due_label_count": 1,
                            "next_visible_due_work_item_count": 1,
                            "next_visible_due_horizons": ["5d"],
                            "next_hidden_due_label_count": 0,
                            "next_hidden_due_work_item_count": 0,
                            "next_hidden_due_horizons": [],
                        },
                        {
                            "horizon": "1m",
                            "learning_role": "calibration_label",
                            "label_count": 1,
                            "work_item_count": 1,
                            "visible_label_count": 1,
                            "visible_work_item_count": 1,
                            "hidden_label_count": 0,
                            "hidden_work_item_count": 0,
                            "due_date_count": 1,
                            "next_due_date": "2026-06-25",
                            "latest_due_date": "2026-06-25",
                            "days_until_next_due": 32,
                            "next_due_window": "later",
                            "next_visible_due_date": "2026-06-25",
                            "latest_visible_due_date": "2026-06-25",
                            "days_until_next_visible_due": 32,
                            "next_visible_due_window": "later",
                            "next_visible_due_label_count": 1,
                            "next_visible_due_work_item_count": 1,
                            "next_visible_due_horizons": ["1m"],
                            "next_hidden_due_label_count": 0,
                            "next_hidden_due_work_item_count": 0,
                            "next_hidden_due_horizons": [],
                        },
                    ],
                    "pending_external_provider_gap_severity_observation_gap_learning_role_counts": [
                        {
                            "learning_role": "fast_check",
                            "label_count": 1,
                            "work_item_count": 1,
                            "visible_label_count": 1,
                            "visible_work_item_count": 1,
                            "hidden_label_count": 0,
                            "hidden_work_item_count": 0,
                            "due_date_count": 1,
                            "horizon_count": 1,
                            "horizons": ["5d"],
                            "next_due_date": "2026-06-02",
                            "latest_due_date": "2026-06-02",
                            "days_until_next_due": 9,
                            "next_due_window": "due_next_30d",
                            "next_visible_due_date": "2026-06-02",
                            "latest_visible_due_date": "2026-06-02",
                            "days_until_next_visible_due": 9,
                            "next_visible_due_window": "due_next_30d",
                            "next_visible_due_label_count": 1,
                            "next_visible_due_work_item_count": 1,
                            "next_visible_due_horizons": ["5d"],
                            "next_hidden_due_label_count": 0,
                            "next_hidden_due_work_item_count": 0,
                            "next_hidden_due_horizons": [],
                            "visible_label_coverage_pct": 100.0,
                            "visible_work_item_coverage_pct": 100.0,
                            "queue_visibility_status": "fully_visible",
                        },
                        {
                            "learning_role": "calibration_label",
                            "label_count": 1,
                            "work_item_count": 1,
                            "visible_label_count": 1,
                            "visible_work_item_count": 1,
                            "hidden_label_count": 0,
                            "hidden_work_item_count": 0,
                            "due_date_count": 1,
                            "horizon_count": 1,
                            "horizons": ["1m"],
                            "next_due_date": "2026-06-25",
                            "latest_due_date": "2026-06-25",
                            "days_until_next_due": 32,
                            "next_due_window": "later",
                            "next_visible_due_date": "2026-06-25",
                            "latest_visible_due_date": "2026-06-25",
                            "days_until_next_visible_due": 32,
                            "next_visible_due_window": "later",
                            "next_visible_due_label_count": 1,
                            "next_visible_due_work_item_count": 1,
                            "next_visible_due_horizons": ["1m"],
                            "next_hidden_due_label_count": 0,
                            "next_hidden_due_work_item_count": 0,
                            "next_hidden_due_horizons": [],
                            "visible_label_coverage_pct": 100.0,
                            "visible_work_item_coverage_pct": 100.0,
                            "queue_visibility_status": "fully_visible",
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
        self.assertIn("External provider gap severity outcomes: configuration_required (2 labels, mean error -9.0", md)
        self.assertIn(
            "External provider gap severity exposure outcomes: configuration_required (2 labels, mean error -9.0",
            md,
        )
        self.assertIn("runtime_budget (2 labels, mean error -7.0", md)
        self.assertIn("Approval data-friction outcomes: earnings_and_external_review (2 labels, mean error -10.0", md)
        self.assertIn("Earnings confirmation outcomes: confirmation_required (1 labels, mean error -6.0", md)
        self.assertIn("Pending earnings label buckets: confirmation required 2 labels next 2026-06-25", md)
        self.assertIn("risk windows blackout 1 labels next 2026-06-25; clear 1 labels next 2026-06-25", md)
        self.assertIn("Pending approval label buckets: blocked until confirmation 1 labels next 2026-06-25", md)
        self.assertIn("review required 2 labels next 2026-06-25", md)
        self.assertIn("Pending approval data-friction labels: earnings and external review 3 labels next 2026-06-25", md)
        self.assertIn("Pending external provider gap severity labels: configuration required 3 labels next 2026-06-25", md)
        self.assertIn(
            "Pending external provider gap severity exposures: configuration required 3 labels next 2026-06-25; "
            "runtime budget 3 labels next 2026-06-25; transient network 3 labels next 2026-06-25",
            md,
        )
        self.assertIn(
            "Pending external provider gap severity observation: 3/5 labels observed (60.0%); "
            "2 unknown need decision_time_only backfill, next unknown due 2026-06-02",
            md,
        )
        self.assertIn(
            "Provider gap severity observation backfill queue: 2 labels / 2 work items missing severity context "
            "(2 visible work items covering 2 labels; 0 hidden work items covering 0 labels); "
            "AMD 5d due 2026-06-02 from 2026-05-24 premarket (1 labels), "
            "ASML 1m due 2026-06-25 from 2026-05-24 premarket (1 labels)",
            md,
        )
        self.assertIn(
            "Provider gap severity observation due dates: 2026-06-02 (in 9 days, due_next_30d): "
            "1 labels / 1 work items "
            "(1 visible work items covering 1 labels, 0 hidden work items covering 0 labels; "
            "cumulative 1 labels / 1 work items); "
            "2026-06-25 (in 32 days, later): 1 labels / 1 work items "
            "(1 visible work items covering 1 labels, 0 hidden work items covering 0 labels; "
            "cumulative 2 labels / 2 work items)",
            md,
        )
        self.assertIn(
            "Provider gap severity observation due windows: due next 30d: 1 labels / 1 work items "
            "across 1 due dates, 2026-06-02 to 2026-06-02 (1 visible labels, 0 hidden); "
            "later: 1 labels / 1 work items across 1 due dates, 2026-06-25 to 2026-06-25 "
            "(1 visible labels, 0 hidden)",
            md,
        )
        self.assertIn(
            "Provider gap severity observation horizons: 5d fast check: 1 labels / 1 work items, "
            "next 2026-06-02 (in 9 days, due_next_30d); 1 visible labels, 0 hidden; "
            "1m calibration label: 1 labels / 1 work items, next 2026-06-25 (in 32 days, later); "
            "1 visible labels, 0 hidden",
            md,
        )
        self.assertIn(
            "Provider gap severity observation learning roles: fast check: 1 labels / 1 work items "
            "across 1 horizons (5d), next 2026-06-02 (in 9 days, due_next_30d); "
            "1 visible labels, 0 hidden, 100.0% visible (fully visible); "
            "visible next 2026-06-02 (in 9 days, due_next_30d; 1 labels / 1 work items; horizons 5d); "
            "calibration label: 1 labels / 1 work items "
            "across 1 horizons (1m), next 2026-06-25 (in 32 days, later); "
            "1 visible labels, 0 hidden, 100.0% visible (fully visible); "
            "visible next 2026-06-25 (in 32 days, later; 1 labels / 1 work items; horizons 1m)",
            md,
        )
        self.assertIn(
            "Provider gap severity hidden calibration queue: 1 hidden calibration work items; "
            "ASML 1m due 2026-06-25 from 2026-05-24 premarket via 2026-05-24-premarket.json "
            "(1 labels); candidate configuration required (2 gaps)",
            md,
        )
        self.assertIn(
            "Provider gap severity hidden calibration report batches: 1 decision-time reports; "
            "2026-05-24-premarket.json: 1 work items / 1 labels due 2026-06-25; horizons 1m; "
            "1 symbols; candidate configuration required (2 gaps)",
            md,
        )
        self.assertIn(
            "Provider gap severity hidden calibration backfill records: 1 ready records; "
            "ASML 1m from 2026-05-24-premarket.json apply ready; candidate configuration required (2 gaps)",
            md,
        )
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
