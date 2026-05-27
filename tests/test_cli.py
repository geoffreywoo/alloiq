import io
import json
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from invest.backtest import trials_from_payload_actions
from invest.cli import (
    apply_coverage_gap_candidate_backfills,
    apply_measurement_gap_candidate_backfills,
    apply_provider_gap_severity_candidate_backfills,
    attach_recovery_external_signals,
    build_coverage_gap_plan_export,
    build_external_alignment_review_plan_export,
    build_measurement_gap_plan_export,
    build_provider_gap_severity_backfill_export,
    command_sources,
    format_external_alignment_review_plan_export,
    format_backtest_summary,
    format_measurement_gap_plan_export,
    format_provider_gap_severity_backfill_export,
    main,
    materialize_recovery_feature_skeleton,
    materialize_recovery_training_examples,
    refresh_report_backtest,
)
from invest.config import AppConfig


class CoverageGapPlanCliTests(unittest.TestCase):
    def test_sources_check_accepts_historical_as_of_symbols_and_outfile(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "external-signals.json"
            config = AppConfig(
                path=Path(tmp) / "invest.toml",
                data={"watchlist": {"symbols": ["NVDA", "MSFT"]}},
            )
            args = SimpleNamespace(
                sources_command="check",
                as_of="2026-05-23",
                symbols="be, GOOG, BE",
                out=str(out_path),
            )
            snapshot = {
                "version": "test",
                "as_of": "2026-05-23",
                "symbols": ["BE", "GOOG"],
                "signal_count": 0,
            }
            stdout = io.StringIO()

            with patch("invest.external_signals.build_external_signal_snapshot", return_value=snapshot) as build:
                with redirect_stdout(stdout):
                    exit_code = command_sources(args, config)

            self.assertEqual(exit_code, 0)
            build.assert_called_once_with(config, date(2026, 5, 23), ["BE", "GOOG"])
            self.assertEqual(json.loads(stdout.getvalue()), snapshot)
            self.assertEqual(json.loads(out_path.read_text(encoding="utf-8")), snapshot)

    def test_sources_check_rejects_invalid_as_of(self):
        config = AppConfig(path=Path("test.toml"), data={"watchlist": {"symbols": ["NVDA"]}})
        args = SimpleNamespace(sources_command="check", as_of="not-a-date", symbols="", out="")
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = command_sources(args, config)

        self.assertEqual(exit_code, 2)
        self.assertIn("Invalid --as-of date", stdout.getvalue())

    def test_refresh_report_backtest_updates_stale_external_gap_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports_dir = Path(tmp) / "reports"
            recovered = training_example("NVDA", "2026-05-23", external=True)
            current = training_example("MSFT", "2026-05-25", external=True)
            write_report(
                reports_dir / "2026-05-23-postmarket.json",
                {
                    "as_of": "2026-05-23",
                    "session": "postmarket",
                    "recommendation_training_examples": [recovered],
                },
            )
            latest_path = reports_dir / "2026-05-25-premarket.json"
            write_report(
                latest_path,
                {
                    "as_of": "2026-05-25",
                    "session": "premarket",
                    "recommendation_training_examples": [current],
                    "outcome_diagnostics": {"status": "stale"},
                    "backtest": {
                        "pending_external_coverage_gap_count": 8,
                        "pending_external_coverage_gap_plan": {
                            "observed_external_long_horizon_label_count": 0,
                            "residual_gap_count": 8,
                        },
                    },
                },
            )

            result = refresh_report_backtest(reports_dir, latest_path, price_history={})

            self.assertEqual(result["status"], "refreshed")
            self.assertEqual(result["before_external_gap_count"], 8)
            self.assertEqual(result["after_external_gap_count"], 0)
            self.assertEqual(result["before_residual_gap_count"], 8)
            self.assertEqual(result["after_residual_gap_count"], 0)
            self.assertEqual(result["after_observed_external_long_horizon_label_count"], 8)
            updated = json.loads(latest_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["backtest"]["pending_external_coverage_gap_count"], 0)
            self.assertEqual(updated["backtest"]["pending_external_coverage_gap_plan"]["residual_gap_count"], 0)
            self.assertEqual(updated["outcome_diagnostics"]["external_learning_readiness_projection"]["pending_external_learning_label_count"], 8)

    def test_format_backtest_summary_surfaces_label_maturity_schedule(self):
        result = {
            "status": "awaiting_matured_outcomes",
            "as_of": "2026-05-25",
            "trial_count": 2,
            "completed_outcome_count": 0,
            "pending_outcome_count": 3,
            "outcomes": [
                {"symbol": "NVDA", "horizon": "5d", "status": "pending", "due_date": "2026-06-02"},
                {"symbol": "MSFT", "horizon": "1m", "status": "pending", "due_date": "2026-06-25"},
                {"symbol": "GOOG", "horizon": "1m", "status": "pending", "due_date": "2026-06-25"},
            ],
            "pending_external_alignment_due_dates": [
                {
                    "due_date": "2026-06-25",
                    "horizons": ["1m"],
                    "due_count": 2,
                    "aligned_count": 1,
                    "conflict_count": 1,
                    "symbols": ["MSFT", "GOOG"],
                }
            ],
            "pending_by_earnings_confirmation_bucket": [
                {"key": "no_event", "pending_count": 1, "next_due_date": "2026-06-02"},
                {"key": "confirmation_required", "pending_count": 2, "next_due_date": "2026-06-25"},
            ],
            "pending_by_earnings_risk_window": [
                {"key": "unknown", "pending_count": 1, "next_due_date": "2026-06-02"},
                {"key": "blackout", "pending_count": 1, "next_due_date": "2026-06-25"},
                {"key": "clear", "pending_count": 1, "next_due_date": "2026-06-25"},
            ],
            "pending_by_approval_blocker_bucket": [
                {"key": "no_approval_context", "pending_count": 1, "next_due_date": "2026-06-02"},
                {"key": "blocked_until_confirmation", "pending_count": 1, "next_due_date": "2026-06-25"},
                {"key": "review_required", "pending_count": 2, "next_due_date": "2026-06-25"},
            ],
            "pending_external_alignment_review_count": 2,
            "pending_external_alignment_review_item_count": 1,
            "pending_external_alignment_review_hidden_item_count": 1,
            "pending_external_alignment_review_acceptance_summary": {
                "label_count": 2,
                "work_item_count": 1,
                "check_count": 2,
                "open_check_count": 1,
                "open_label_count": 2,
                "metadata_ready_work_item_count": 1,
                "open_check_counts": {"matured_label_available": 1},
                "next_open_check_due_date": "2026-06-25",
                "next_open_check_due_open_check_count": 1,
                "next_open_check_due_label_count": 2,
                "next_open_check_due_work_item_count": 1,
                "next_open_check_due_visible_work_item_count": 1,
                "next_open_check_due_hidden_work_item_count": 0,
                "next_open_check_due_fully_visible": True,
                "next_open_check_due_symbols": ["MSFT"],
                "next_open_check_due_horizons": ["1m"],
                "next_open_check_due_focus_counts": {
                    "external_disagreement": {"label_count": 2, "work_item_count": 1},
                },
                "next_open_check_due_learning_action_counts": {
                    "Compare realized direction with the external signal.": {"label_count": 2, "work_item_count": 1},
                },
                "next_open_check_due_measurement_missing_field_counts": {
                    "risk_adjusted_expected_return": {"label_count": 2, "work_item_count": 1},
                },
                "open_check_due_dates": [
                    {
                        "due_date": "2026-06-25",
                        "open_check_count": 1,
                        "label_count": 2,
                        "work_item_count": 1,
                        "symbols": ["MSFT"],
                        "horizons": ["1m"],
                        "focus_counts": {
                            "external_disagreement": {"label_count": 2, "work_item_count": 1},
                        },
                        "learning_action_counts": {
                            "Compare realized direction with the external signal.": {"label_count": 2, "work_item_count": 1},
                        },
                        "measurement_missing_field_counts": {
                            "risk_adjusted_expected_return": {"label_count": 2, "work_item_count": 1},
                        },
                        "check_counts": {"matured_label_available": 1},
                    }
                ],
            },
            "pending_external_alignment_review_due_dates": [
                {
                    "due_date": "2026-06-25",
                    "label_count": 2,
                    "work_item_count": 1,
                    "focus_counts": {
                        "external_disagreement": {"label_count": 1, "work_item_count": 1},
                        "missed_external_signal": {"label_count": 1, "work_item_count": 0},
                    },
                }
            ],
            "pending_external_alignment_review_queue": [
                {
                    "external_alignment_review_id": "review-msft-1m-conflict",
                    "symbol": "MSFT",
                    "horizon": "1m",
                    "due_date": "2026-06-25",
                    "external_alignment": "conflict",
                    "external_alignment_review_focus": "external_disagreement",
                    "external_alignment_review_label_count": 2,
                    "external_alignment_review_learning_action": "Compare realized direction with the external signal.",
                    "external_alignment_review_measurement_plan": {
                        "summary": "engine negative; external positive score 5; expected missing",
                        "missing_measurement_fields": ["risk_adjusted_expected_return"],
                    },
                    "external_alignment_review_acceptance_checks": [
                        {"check": "source_trace_present", "status": "passed"},
                        {"check": "matured_label_available", "status": "pending"},
                    ],
                    "external_alignment_review_open_check_count": 1,
                    "source_outcome_id": "outcome-msft-1m",
                }
            ],
            "pending_external_alignment_measurement_gap_plan": {
                "label_count": 2,
                "work_item_count": 1,
                "hidden_work_item_count": 0,
                "priority_acceptance_check_count": 2,
                "priority_open_acceptance_check_count": 2,
                "next_due_date": "2026-06-25",
                "next_due_field_counts": {
                    "risk_adjusted_expected_return": {"label_count": 2, "work_item_count": 1},
                },
                "priority_symbols": ["MSFT"],
            },
            "horizons": [
                {
                    "horizon": "5d",
                    "completed_count": 0,
                    "hit_rate": None,
                    "average_decision_return": None,
                }
            ],
        }

        summary = format_backtest_summary(result)

        self.assertIn("- Next label maturity: 2026-06-02 (5d, 1 label, in 8 days)", summary)
        self.assertIn("- Next learning label maturity: 2026-06-25 (1m, 2 labels, in 31 days)", summary)
        self.assertIn(
            "- Next external alignment label: 2026-06-25 (1m, 2 labels, 1 aligned, 1 conflict, symbols MSFT, GOOG)",
            summary,
        )
        self.assertIn(
            "- Pending earnings label buckets: confirmation required 2 labels next 2026-06-25 | "
            "risk windows blackout 1 label next 2026-06-25; clear 1 label next 2026-06-25",
            summary,
        )
        self.assertIn(
            "- Pending approval label buckets: blocked until confirmation 1 label next 2026-06-25; "
            "review required 2 labels next 2026-06-25",
            summary,
        )
        self.assertIn("- External alignment review queue: 2 non-confirming labels / 1 work item, 1 hidden", summary)
        self.assertIn(
            "- Review acceptance: 1/2 checks open across 2 labels; metadata-ready 1/1 work item; "
            "blockers matured_label_available=1; next blocker due 2026-06-25 "
            "(2 labels / 1 work item, 1m, symbols MSFT, focus external disagreement 2 labels/1 work item, "
            "queue 1/1 visible)",
            summary,
        )
        self.assertIn("- Next review bucket: 2026-06-25 (2 labels / 1 work item)", summary)
        self.assertIn(
            "- Next review focus: external disagreement 1 label/1 work item; "
            "missed external signal 1 label/0 work items",
            summary,
        )
        self.assertIn(
            "- Next review action: Compare realized direction with the external signal. (2 labels/1 work item)",
            summary,
        )
        self.assertIn(
            "- Next review missing measurements: risk adjusted expected return 2 labels/1 work item",
            summary,
        )
        self.assertIn(
            "- Measurement backfill queue: 2 labels / 1 work item, 2/2 priority checks open, "
            "next 2026-06-25 risk adjusted expected return 2 labels/1 work item, symbols MSFT",
            summary,
        )
        self.assertIn(
            "- Next review item: review-msft-1m-conflict MSFT 1m due 2026-06-25 "
            "(external disagreement, 2 labels, source outcome-, 1/2 open checks, "
            "engine negative; external positive score 5; expected missing, "
            "Compare realized direction with the external signal.)",
            summary,
        )

    def test_builds_external_alignment_review_plan_from_public_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            write_latest(web_dir, external_alignment_review_snapshot())

            export = build_external_alignment_review_plan_export(web_dir)
            text = format_external_alignment_review_plan_export(export)

            self.assertEqual(export["version"], "2026-05-external-alignment-review-plan-export-v1")
            self.assertEqual(export["status"], "ready")
            self.assertEqual(export["source_report"], "2026-05-25-premarket.json")
            self.assertEqual(export["label_count"], 2)
            self.assertEqual(export["work_item_count"], 1)
            self.assertEqual(export["visible_work_item_count"], 1)
            self.assertEqual(export["open_acceptance_check_count"], 1)
            self.assertEqual(export["open_acceptance_check_counts"], {"matured_label_available": 1})
            self.assertEqual(export["next_due_date"], "2026-06-02")
            self.assertEqual(export["next_due_focus_counts"], {"external_disagreement": {"label_count": 2, "work_item_count": 1}})
            self.assertEqual(export["review_bottleneck"], "awaiting_label_maturity")
            self.assertEqual(
                export["maturity_test_target_counts"],
                {"external_signal_trust_vs_engine_direction": 1},
            )
            self.assertEqual(
                export["maturity_test_primary_metric_counts"],
                {"decision_forward_return_pct": 1},
            )
            self.assertEqual(export["maturity_test_status_counts"], {"blocked": 1})
            self.assertEqual(export["maturity_test_blocker_counts"], {"matured_label_available": 1})
            self.assertEqual(export["maturity_test_result_counts"], {})
            self.assertEqual(export["priority_rows"][0]["external_alignment_review_id"], "review-goog-5d")
            self.assertEqual(export["priority_rows"][0]["measurement_summary"], "engine negative; external positive score 4; expected 9.41")
            self.assertEqual(
                export["priority_rows"][0]["review_question"],
                "When the label matures, did realized direction validate the engine or the external signal?",
            )
            maturity_plan = export["priority_rows"][0]["maturity_test_plan"]
            self.assertEqual(maturity_plan["version"], "2026-05-external-alignment-maturity-test-v1")
            self.assertEqual(maturity_plan["primary_metric"], "decision_forward_return_pct")
            self.assertEqual(maturity_plan["calibration_target"], "external_signal_trust_vs_engine_direction")
            self.assertEqual(maturity_plan["decision_rules"][0]["outcome"], "engine_validated")
            self.assertEqual(export["priority_rows"][0]["maturity_test_status"], "blocked")
            self.assertEqual(export["priority_rows"][0]["maturity_test_blockers"], ["matured_label_available"])
            self.assertIsNone(export["priority_rows"][0]["maturity_test_result"])
            self.assertIn("Review queue: 2 labels / 1 work items", text)
            self.assertIn("Review bottleneck: awaiting_label_maturity", text)
            self.assertIn("Blockers: matured_label_available=1", text)
            self.assertIn("Maturity test targets: external_signal_trust_vs_engine_direction=1", text)
            self.assertIn("Maturity test status: blocked=1", text)
            self.assertIn("Maturity test blockers: matured_label_available=1", text)
            self.assertIn("GOOG 5d: review-goog-5d", text)

    def test_external_alignment_review_plan_classifies_completed_maturity_test(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            payload = external_alignment_review_snapshot()
            row = payload["backtest"]["pending_external_alignment_review_queue"][0]
            row.update(
                {
                    "status": "complete",
                    "decision_forward_return_pct": 3.2,
                    "raw_forward_return_pct": -3.2,
                    "hit": True,
                    "expected_vs_realized_error": -6.21,
                }
            )
            row["external_alignment_review_acceptance_checks"][-1]["status"] = "passed"
            row["external_alignment_review_open_check_count"] = 0
            payload["backtest"]["pending_external_alignment_review_acceptance_summary"].update(
                {
                    "open_check_count": 0,
                    "open_check_counts": {},
                }
            )
            write_latest(web_dir, payload)

            export = build_external_alignment_review_plan_export(web_dir)

            result = export["priority_rows"][0]["maturity_test_result"]
            self.assertEqual(export["review_bottleneck"], "ready_for_review")
            self.assertEqual(export["maturity_test_status_counts"], {"classified": 1})
            self.assertEqual(export["maturity_test_blocker_counts"], {})
            self.assertEqual(export["maturity_test_result_counts"], {"engine_validated": 1})
            self.assertEqual(export["priority_rows"][0]["maturity_test_status"], "classified")
            self.assertEqual(export["priority_rows"][0]["maturity_test_blockers"], [])
            self.assertEqual(result["outcome"], "engine_validated")
            self.assertEqual(result["primary_metric"], "decision_forward_return_pct")
            self.assertEqual(result["primary_metric_value"], 3.2)
            self.assertEqual(result["calibration_target"], "external_signal_trust_vs_engine_direction")
            self.assertIn("Do not increase trust", result["learning_update"])

    def test_external_alignment_review_plan_cli_prints_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            write_latest(web_dir, external_alignment_review_snapshot())
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(["external-alignment-review-plan", "--web-dir", str(web_dir)])

            self.assertEqual(exit_code, 0)
            export = json.loads(stdout.getvalue())
            self.assertEqual(export["status"], "ready")
            self.assertEqual(export["review_bottleneck"], "awaiting_label_maturity")

    def test_builds_priority_backfill_checklist_from_public_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            write_latest(web_dir, coverage_gap_snapshot())

            export = build_coverage_gap_plan_export(web_dir)

            self.assertEqual(export["version"], "2026-05-external-coverage-gap-plan-export-v2")
            self.assertEqual(export["status"], "ready")
            self.assertEqual(export["source_report"], "2026-05-25-premarket.json")
            self.assertEqual(export["priority_gap_count"], 1)
            self.assertEqual(export["priority_acceptance_check_count"], 2)
            self.assertEqual(export["priority_open_acceptance_check_count"], 2)
            self.assertTrue(export["external_learning_ready_after_priority_backfill"])
            self.assertEqual(export["projected_external_learning_ready_date_after_priority_backfill"], "2026-06-25")
            self.assertEqual(export["priority_rows"][0]["external_coverage_gap_id"], "gap-goog-1m")
            self.assertEqual(export["priority_rows"][0]["source_outcome_id"], "outcome-goog-1m")
            self.assertEqual(export["priority_rows"][0]["external_coverage_backfill_policy"], "decision_time_only")
            self.assertEqual(export["priority_rows"][0]["external_coverage_acceptance_checks"][0]["status"], "pending")
            self.assertEqual(export["backfill_items"][0]["external_coverage_gap_id"], "gap-goog-1m")
            self.assertEqual(export["backfill_items"][0]["source_report"], "2026-05-25-premarket.json")
            self.assertEqual(export["backfill_items"][0]["decision_as_of"], "2026-05-24")
            self.assertEqual(
                export["backfill_items"][0]["fields_to_backfill"],
                ["external_feed_status", "external_coverage_multiplier", "coverage_adjusted_external_signal_score"],
            )
            self.assertEqual(export["backfill_items"][0]["open_acceptance_check_count"], 2)
            self.assertEqual(export["backfill_items"][0]["open_acceptance_checks"][0]["check"], "external_feed_status_present")

    def test_builds_measurement_gap_backfill_checklist_from_public_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            reports_dir = Path(tmp) / "reports"
            write_latest(web_dir, measurement_gap_snapshot())
            write_report(
                reports_dir / "2026-05-23-postmarket.json",
                {
                    "as_of": "2026-05-23",
                    "session": "postmarket",
                    "recommendation_training_examples": [
                        {
                            "example_id": "trial-goog-5d",
                            "symbol": "GOOG",
                            "risk_adjusted_expected_return": 6.2,
                        }
                    ],
                },
            )

            export = build_measurement_gap_plan_export(web_dir)
            resolved = build_measurement_gap_plan_export(
                web_dir,
                reports_dir=reports_dir,
                resolve_candidates=True,
            )
            text = format_measurement_gap_plan_export(resolved)

            self.assertEqual(export["version"], "2026-05-external-alignment-measurement-gap-plan-export-v1")
            self.assertEqual(export["status"], "ready")
            self.assertEqual(export["source_report"], "2026-05-25-premarket.json")
            self.assertEqual(export["label_count"], 2)
            self.assertEqual(export["work_item_count"], 1)
            self.assertEqual(export["hidden_work_item_count"], 0)
            self.assertEqual(export["next_due_date"], "2026-06-02")
            self.assertEqual(
                export["next_due_field_counts"],
                {"risk_adjusted_expected_return": {"label_count": 2, "work_item_count": 1}},
            )
            self.assertEqual(export["priority_rows"][0]["external_alignment_measurement_gap_id"], "measurement-gap-goog-5d")
            self.assertEqual(export["priority_rows"][0]["external_alignment_review_id"], "review-goog-5d")
            self.assertEqual(export["backfill_items"][0]["fields_to_backfill"], ["risk_adjusted_expected_return"])
            self.assertEqual(export["backfill_items"][0]["source_report"], "2026-05-25-premarket.json")
            self.assertEqual(export["backfill_items"][0]["decision_as_of"], "2026-05-23")
            self.assertEqual(export["backfill_items"][0]["open_acceptance_check_count"], 2)
            self.assertEqual(resolved["candidate_resolution"]["status"], "eligible_reports")
            self.assertEqual(resolved["candidate_resolution"]["candidate_ready_count"], 1)
            self.assertEqual(resolved["candidate_resolution"]["candidate_apply_ready_count"], 1)
            self.assertEqual(resolved["candidate_resolution"]["candidate_acceptance_passed_count"], 2)
            self.assertEqual(resolved["candidate_resolution"]["candidate_missing_required_field_counts"], {})
            self.assertEqual(resolved["candidate_resolution"]["candidate_failed_acceptance_check_counts"], {})
            self.assertEqual(
                resolved["candidate_resolution"]["candidate_source_section_counts"],
                {"recommendation_training_examples": 1},
            )
            self.assertEqual(resolved["backfill_items"][0]["candidate_source_section"], "recommendation_training_examples")
            self.assertEqual(resolved["backfill_items"][0]["candidate_backfill_values"], {"risk_adjusted_expected_return": 6.2})
            self.assertIn("Measurement gaps: 2 labels / 1 work items", text)
            self.assertIn("Candidate ready: 1 / 1", text)
            self.assertIn("GOOG 5d: measurement-gap-goog-5d", text)

    def test_measurement_gap_candidate_resolution_reports_missing_required_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            reports_dir = Path(tmp) / "reports"
            write_latest(web_dir, measurement_gap_snapshot())
            write_report(
                reports_dir / "2026-05-23-postmarket.json",
                {
                    "as_of": "2026-05-23",
                    "session": "postmarket",
                    "recommendation_training_examples": [
                        {
                            "example_id": "trial-goog-5d",
                            "symbol": "GOOG",
                            "risk_adjusted_expected_return": None,
                        }
                    ],
                },
            )

            export = build_measurement_gap_plan_export(
                web_dir,
                reports_dir=reports_dir,
                resolve_candidates=True,
            )
            text = format_measurement_gap_plan_export(export)

            self.assertEqual(export["candidate_resolution"]["candidate_ready_count"], 0)
            self.assertEqual(
                export["candidate_resolution"]["candidate_missing_required_field_counts"],
                {"risk_adjusted_expected_return": 1},
            )
            self.assertEqual(
                export["candidate_resolution"]["candidate_failed_acceptance_check_counts"],
                {"risk_adjusted_expected_return_present": 1},
            )
            self.assertIn("Candidate blockers: missing risk_adjusted_expected_return=1", text)
            self.assertIn("failed checks risk_adjusted_expected_return_present=1", text)

    def test_measurement_gap_candidate_resolution_derives_expected_return_from_feature_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            reports_dir = Path(tmp) / "reports"
            write_latest(web_dir, measurement_gap_snapshot())
            write_report(
                reports_dir / "2026-05-23-postmarket.json",
                {
                    "as_of": "2026-05-23",
                    "session": "postmarket",
                    "recommendation_training_examples": [
                        {
                            "example_id": "trial-goog-5d",
                            "symbol": "GOOG",
                            "risk_adjusted_expected_return": None,
                        }
                    ],
                    "feature_matrix": {
                        "version": "test-feature-matrix",
                        "rows": [
                            {
                                "symbol": "GOOG",
                                "bucket": "frontier_ai_platforms",
                                "current_weight": 0.1,
                                "company_underwriting_score": 60,
                                "sector_setup_score": 55,
                                "evidence_quality": 60,
                                "timing_score": 50,
                                "drawdown_risk": 40,
                                "valuation_support": 55,
                            }
                        ],
                    },
                },
            )

            export = build_measurement_gap_plan_export(
                web_dir,
                reports_dir=reports_dir,
                resolve_candidates=True,
            )
            text = format_measurement_gap_plan_export(export)

            self.assertEqual(export["candidate_resolution"]["candidate_ready_count"], 1)
            self.assertEqual(export["candidate_resolution"]["candidate_apply_ready_count"], 1)
            self.assertEqual(
                export["candidate_resolution"]["candidate_source_section_counts"],
                {"derived_feature_matrix.research_item": 1},
            )
            self.assertEqual(
                export["candidate_resolution"]["candidate_derivation_counts"],
                {"research_item_from_feature_matrix": 1},
            )
            self.assertEqual(export["backfill_items"][0]["candidate_source_section"], "derived_feature_matrix.research_item")
            self.assertEqual(export["backfill_items"][0]["candidate_derivation_policy"], "decision_time_feature_matrix_only")
            self.assertEqual(export["backfill_items"][0]["candidate_feature_matrix_version"], "test-feature-matrix")
            self.assertEqual(export["backfill_items"][0]["candidate_backfill_values"], {"risk_adjusted_expected_return": 21.72})
            self.assertIn("Candidate derivations: research_item_from_feature_matrix=1", text)

    def test_applies_measurement_gap_candidate_to_matching_training_example(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            reports_dir = Path(tmp) / "reports"
            write_latest(web_dir, measurement_gap_snapshot())
            report_path = reports_dir / "2026-05-23-postmarket.json"
            write_report(
                report_path,
                {
                    "as_of": "2026-05-23",
                    "session": "postmarket",
                    "recommendation_training_examples": [
                        {
                            "example_id": "trial-goog-5d",
                            "symbol": "GOOG",
                            "risk_adjusted_expected_return": None,
                        }
                    ],
                    "feature_matrix": {
                        "version": "test-feature-matrix",
                        "rows": [
                            {
                                "symbol": "GOOG",
                                "bucket": "frontier_ai_platforms",
                                "current_weight": 0.1,
                                "company_underwriting_score": 60,
                                "sector_setup_score": 55,
                                "evidence_quality": 60,
                                "timing_score": 50,
                                "drawdown_risk": 40,
                                "valuation_support": 55,
                            }
                        ],
                    },
                },
            )
            export = build_measurement_gap_plan_export(
                web_dir,
                reports_dir=reports_dir,
                resolve_candidates=True,
            )

            result = apply_measurement_gap_candidate_backfills(export["backfill_items"])

            self.assertEqual(result["status"], "applied")
            self.assertEqual(result["applied_item_count"], 1)
            self.assertEqual(result["field_update_count"], 1)
            updated = json.loads(report_path.read_text(encoding="utf-8"))
            example = updated["recommendation_training_examples"][0]
            self.assertEqual(example["risk_adjusted_expected_return"], 21.72)
            self.assertEqual(
                example["external_alignment_measurement_backfills"][0]["external_alignment_measurement_gap_id"],
                "measurement-gap-goog-5d",
            )
            self.assertEqual(
                example["external_alignment_measurement_backfills"][0]["candidate_derivation"],
                "research_item_from_feature_matrix",
            )

    def test_applies_provider_gap_severity_backfill_records_to_matching_training_examples(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            reports_dir = Path(tmp) / "reports"
            write_latest(web_dir, provider_gap_severity_backfill_snapshot())
            report_path = reports_dir / "2026-05-24-postmarket.json"
            write_report(
                report_path,
                {
                    "as_of": "2026-05-24",
                    "session": "postmarket",
                    "recommendation_training_examples": [
                        {
                            "example_id": "trial-goog-1m",
                            "symbol": "GOOG",
                            "external_provider_gap_count": None,
                            "external_provider_primary_gap_severity": None,
                        }
                    ],
                },
            )
            export = build_provider_gap_severity_backfill_export(web_dir, reports_dir=reports_dir)
            text = format_provider_gap_severity_backfill_export(export)

            result = apply_provider_gap_severity_candidate_backfills(export["backfill_items"])

            self.assertEqual(export["version"], "2026-05-external-provider-gap-severity-backfill-plan-export-v1")
            self.assertEqual(export["candidate_apply_ready_count"], 1)
            self.assertIn("GOOG 1m: ready configuration_required", text)
            self.assertEqual(result["status"], "applied")
            self.assertEqual(result["applied_item_count"], 1)
            self.assertEqual(result["applied_example_count"], 1)
            self.assertEqual(result["field_update_count"], 8)
            updated = json.loads(report_path.read_text(encoding="utf-8"))
            example = updated["recommendation_training_examples"][0]
            self.assertEqual(example["external_provider_gap_count"], 4)
            self.assertEqual(example["external_provider_configuration_gap_count"], 2)
            self.assertEqual(example["external_provider_runtime_gap_count"], 1)
            self.assertEqual(example["external_provider_stale_gap_count"], 1)
            self.assertEqual(example["external_provider_transient_gap_count"], 0)
            self.assertEqual(example["external_provider_other_gap_count"], 0)
            self.assertEqual(example["external_provider_primary_gap_severity"], "configuration_required")
            self.assertEqual(example["external_provider_gap_severity_score"], 53.33)
            self.assertEqual(
                example["external_provider_gap_severity_backfills"][0][
                    "external_provider_gap_severity_observation_backfill_record_id"
                ],
                "provider-gap-record-goog-1m",
            )

    def test_provider_gap_severity_apply_refuses_blocked_candidates(self):
        with self.assertRaises(RuntimeError):
            apply_provider_gap_severity_candidate_backfills([{"symbol": "GOOG", "candidate_apply_status": "blocked"}])

    def test_provider_gap_severity_cli_can_apply_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            reports_dir = Path(tmp) / "reports"
            write_latest(web_dir, provider_gap_severity_backfill_snapshot())
            report_path = reports_dir / "2026-05-24-postmarket.json"
            write_report(
                report_path,
                {
                    "as_of": "2026-05-24",
                    "session": "postmarket",
                    "recommendation_training_examples": [
                        {"example_id": "trial-goog-1m", "symbol": "GOOG"}
                    ],
                },
            )
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "provider-gap-severity-backfill-plan",
                        "--web-dir",
                        str(web_dir),
                        "--reports-dir",
                        str(reports_dir),
                        "--apply-candidates",
                    ]
                )

            self.assertEqual(exit_code, 0)
            export = json.loads(stdout.getvalue())
            self.assertEqual(export["apply_result"]["field_update_count"], 8)
            updated = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(
                updated["recommendation_training_examples"][0]["external_provider_primary_gap_severity"],
                "configuration_required",
            )

    def test_resolves_candidate_values_from_decision_time_source_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            web_dir = root / "web"
            reports_dir = root / "reports"
            write_latest(web_dir, coverage_gap_snapshot())
            write_report(
                reports_dir / "2026-05-25-premarket.json",
                {
                    "as_of": "2026-05-24",
                    "feature_matrix": {
                        "rows": [
                            {
                                "symbol": "GOOG",
                                "external_feed_status": "limited",
                                "external_coverage_multiplier": 0.3333,
                                "coverage_adjusted_external_signal_score": 0.0,
                            }
                        ]
                    }
                },
            )

            export = build_coverage_gap_plan_export(web_dir, reports_dir=reports_dir, resolve_candidates=True)

            self.assertEqual(export["candidate_resolution"]["status"], "source_report")
            self.assertEqual(export["candidate_resolution"]["candidate_ready_count"], 1)
            self.assertEqual(export["candidate_resolution"]["candidate_apply_ready_count"], 1)
            self.assertEqual(export["candidate_resolution"]["candidate_apply_blocked_count"], 0)
            self.assertEqual(export["candidate_resolution"]["candidate_acceptance_check_count"], 2)
            self.assertEqual(export["candidate_resolution"]["candidate_acceptance_passed_count"], 2)
            self.assertEqual(export["candidate_resolution"]["candidate_acceptance_failed_count"], 0)
            item = export["backfill_items"][0]
            self.assertEqual(item["candidate_resolution_status"], "ready")
            self.assertEqual(item["candidate_apply_status"], "ready")
            self.assertEqual(item["candidate_source"], str(reports_dir / "2026-05-25-premarket.json"))
            self.assertEqual(item["candidate_source_as_of"], "2026-05-24")
            self.assertTrue(item["candidate_symbol_found"])
            self.assertEqual(item["candidate_missing_required_fields"], [])
            self.assertEqual(item["candidate_backfill_values"]["external_feed_status"], "limited")
            self.assertEqual(item["candidate_backfill_values"]["external_coverage_multiplier"], 0.3333)
            self.assertEqual(item["candidate_backfill_values"]["coverage_adjusted_external_signal_score"], 0.0)
            self.assertEqual(item["candidate_acceptance_status_counts"], {"passed": 2})
            self.assertEqual([check["status"] for check in item["candidate_acceptance_checks"]], ["passed", "passed"])

    def test_blocks_apply_when_source_report_is_after_required_decision_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            web_dir = root / "web"
            reports_dir = root / "reports"
            write_latest(web_dir, coverage_gap_snapshot())
            write_report(
                reports_dir / "2026-05-25-premarket.json",
                {
                    "as_of": "2026-05-25",
                    "feature_matrix": {
                        "rows": [
                            {
                                "symbol": "GOOG",
                                "external_feed_status": "limited",
                                "external_coverage_multiplier": 0.3333,
                                "coverage_adjusted_external_signal_score": 0.0,
                            }
                        ]
                    },
                },
            )

            export = build_coverage_gap_plan_export(web_dir, reports_dir=reports_dir, resolve_candidates=True)

            self.assertEqual(export["candidate_resolution"]["candidate_ready_count"], 1)
            self.assertEqual(export["candidate_resolution"]["candidate_apply_ready_count"], 0)
            self.assertEqual(export["candidate_resolution"]["candidate_apply_blocked_count"], 1)
            self.assertEqual(export["candidate_resolution"]["candidate_acceptance_failed_count"], 1)
            item = export["backfill_items"][0]
            self.assertEqual(item["candidate_resolution_status"], "ready")
            self.assertEqual(item["candidate_apply_status"], "blocked")
            self.assertEqual(item["candidate_acceptance_status_counts"], {"failed": 1, "passed": 1})
            failed = [check for check in item["candidate_acceptance_checks"] if check["status"] == "failed"]
            self.assertEqual(failed[0]["check"], "decision_time_only")

    def test_eligible_report_policy_uses_date_safe_report_instead_of_future_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            web_dir = root / "web"
            reports_dir = root / "reports"
            write_latest(web_dir, coverage_gap_snapshot())
            write_report(
                reports_dir / "2026-05-24-postmarket.json",
                {
                    "as_of": "2026-05-24",
                    "session": "postmarket",
                    "feature_matrix": {
                        "rows": [
                            {
                                "symbol": "GOOG",
                                "external_feed_status": "limited",
                                "external_coverage_multiplier": 0.25,
                                "coverage_adjusted_external_signal_score": 0.0,
                            }
                        ]
                    },
                },
            )
            write_report(
                reports_dir / "2026-05-25-premarket.json",
                {
                    "as_of": "2026-05-25",
                    "session": "premarket",
                    "feature_matrix": {
                        "rows": [
                            {
                                "symbol": "GOOG",
                                "external_feed_status": "ok",
                                "external_coverage_multiplier": 1.0,
                                "coverage_adjusted_external_signal_score": 20.0,
                            }
                        ]
                    },
                },
            )

            export = build_coverage_gap_plan_export(
                web_dir,
                reports_dir=reports_dir,
                resolve_candidates=True,
                candidate_source="eligible-reports",
            )

            self.assertEqual(export["candidate_resolution"]["status"], "eligible_reports")
            self.assertEqual(export["candidate_resolution"]["candidate_report_count"], 2)
            self.assertEqual(export["candidate_resolution"]["candidate_apply_ready_count"], 1)
            self.assertEqual(export["candidate_resolution"]["candidate_report_search_reason_counts"], {"matched": 1})
            item = export["backfill_items"][0]
            self.assertEqual(item["candidate_source_policy"], "eligible-reports")
            self.assertEqual(item["candidate_report_search_status"], "eligible_report")
            self.assertEqual(item["candidate_report_search_reason"], "matched")
            self.assertEqual(item["candidate_eligible_date_report_count"], 1)
            self.assertEqual(item["candidate_symbol_report_count"], 2)
            self.assertEqual(item["candidate_symbol_eligible_report_count"], 1)
            self.assertEqual(item["candidate_source"], str(reports_dir / "2026-05-24-postmarket.json"))
            self.assertEqual(item["candidate_apply_status"], "ready")
            self.assertEqual(item["candidate_backfill_values"]["external_feed_status"], "limited")

    def test_eligible_report_policy_explains_missing_decision_time_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            web_dir = root / "web"
            reports_dir = root / "reports"
            payload = coverage_gap_snapshot()
            row = payload["backtest"]["pending_external_coverage_gap_plan"]["priority_rows"][0]
            row["as_of"] = "2026-05-23"
            row["required_external_observation_date"] = "2026-05-23"
            write_latest(web_dir, payload)
            write_report(
                reports_dir / "2026-05-24-premarket.json",
                {
                    "as_of": "2026-05-24",
                    "session": "premarket",
                    "feature_matrix": {
                        "rows": [
                            {
                                "symbol": "GOOG",
                                "external_feed_status": "limited",
                                "external_coverage_multiplier": 0.25,
                                "coverage_adjusted_external_signal_score": 0.0,
                            }
                        ]
                    },
                },
            )

            export = build_coverage_gap_plan_export(
                web_dir,
                reports_dir=reports_dir,
                resolve_candidates=True,
                candidate_source="eligible-reports",
            )

            self.assertEqual(
                export["candidate_resolution"]["candidate_report_search_reason_counts"],
                {"no_reports_on_or_before_required_date": 1},
            )
            self.assertEqual(export["candidate_resolution"]["candidate_apply_ready_count"], 0)
            item = export["backfill_items"][0]
            self.assertEqual(item["candidate_report_search_status"], "eligible_report_missing")
            self.assertEqual(item["candidate_report_search_reason"], "no_reports_on_or_before_required_date")
            self.assertEqual(item["candidate_required_date"], "2026-05-23")
            self.assertEqual(item["candidate_report_min_as_of"], "2026-05-24")
            self.assertEqual(item["candidate_report_max_as_of"], "2026-05-24")
            self.assertEqual(item["candidate_eligible_date_report_count"], 0)
            self.assertEqual(item["candidate_symbol_report_count"], 1)
            self.assertEqual(item["candidate_symbol_eligible_report_count"], 0)
            self.assertEqual(item["candidate_apply_status"], "blocked")

    def test_eligible_report_policy_explains_required_date_report_without_features(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            web_dir = root / "web"
            reports_dir = root / "reports"
            payload = coverage_gap_snapshot()
            row = payload["backtest"]["pending_external_coverage_gap_plan"]["priority_rows"][0]
            row["as_of"] = "2026-05-23"
            row["required_external_observation_date"] = "2026-05-23"
            write_latest(web_dir, payload)
            write_report(
                reports_dir / "2026-05-23-postmarket.json",
                {
                    "as_of": "2026-05-23",
                    "session": "postmarket",
                    "news": [],
                },
            )
            write_report(
                reports_dir / "2026-05-24-premarket.json",
                {
                    "as_of": "2026-05-24",
                    "session": "premarket",
                    "feature_matrix": {
                        "rows": [
                            {
                                "symbol": "GOOG",
                                "external_feed_status": "limited",
                                "external_coverage_multiplier": 0.25,
                                "coverage_adjusted_external_signal_score": 0.0,
                            }
                        ]
                    },
                },
            )

            export = build_coverage_gap_plan_export(
                web_dir,
                reports_dir=reports_dir,
                resolve_candidates=True,
                candidate_source="eligible-reports",
            )

            self.assertEqual(export["candidate_resolution"]["candidate_raw_report_count"], 2)
            self.assertEqual(
                export["candidate_resolution"]["candidate_report_search_reason_counts"],
                {"required_date_reports_missing_feature_matrix": 1},
            )
            item = export["backfill_items"][0]
            self.assertEqual(item["candidate_report_search_reason"], "required_date_reports_missing_feature_matrix")
            self.assertEqual(item["candidate_raw_report_min_as_of"], "2026-05-23")
            self.assertEqual(item["candidate_raw_report_max_as_of"], "2026-05-24")
            self.assertEqual(item["candidate_report_min_as_of"], "2026-05-24")
            self.assertEqual(item["candidate_raw_eligible_date_report_count"], 1)
            self.assertEqual(item["candidate_eligible_date_report_count"], 0)
            self.assertEqual(item["candidate_raw_required_date_report_count"], 1)
            self.assertEqual(item["candidate_raw_required_date_feature_row_count"], 0)
            self.assertEqual(
                item["candidate_raw_required_date_reports"],
                [
                    {
                        "report": str(reports_dir / "2026-05-23-postmarket.json"),
                        "as_of": "2026-05-23",
                        "session": "postmarket",
                        "feature_row_count": 0,
                        "training_example_count": 0,
                        "approval_ticket_count": 0,
                        "action_queue_count": 0,
                        "research_item_count": 0,
                        "has_external_signals": False,
                        "external_provider_count": 0,
                        "external_signal_count": 0,
                    }
                ],
            )

    def test_eligible_report_policy_normalizes_and_prefers_source_trial_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            web_dir = root / "web"
            reports_dir = root / "reports"
            write_latest(web_dir, coverage_gap_snapshot())
            write_report(
                reports_dir / "2026-05-24-premarket.json",
                {
                    "as_of": "2026-05-24",
                    "session": "premarket",
                    "external_signals": external_signal_health(),
                    "recommendation_training_examples": [{"example_id": "out-of-scope"}],
                    "feature_matrix": {
                        "rows": [
                            {
                                "symbol": "GOOG",
                                "external_signal_score": 20.0,
                                "external_signal_count": 4,
                                "external_source_count": 1,
                            }
                        ]
                    },
                },
            )
            write_report(
                reports_dir / "2026-05-24-postmarket.json",
                {
                    "as_of": "2026-05-24",
                    "session": "postmarket",
                    "external_signals": external_signal_health(),
                    "recommendation_training_examples": [{"example_id": "trial-goog-1m"}],
                    "feature_matrix": {
                        "rows": [
                            {
                                "symbol": "GOOG",
                                "external_signal_score": 8.0,
                                "external_signal_count": 2,
                                "external_source_count": 1,
                            }
                        ]
                    },
                },
            )

            export = build_coverage_gap_plan_export(
                web_dir,
                reports_dir=reports_dir,
                resolve_candidates=True,
                candidate_source="eligible-reports",
            )

            item = export["backfill_items"][0]
            self.assertEqual(item["candidate_source"], str(reports_dir / "2026-05-24-postmarket.json"))
            self.assertEqual(item["candidate_apply_status"], "ready")
            self.assertEqual(item["candidate_backfill_values"]["external_feed_status"], "limited")
            self.assertEqual(item["candidate_backfill_values"]["external_coverage_multiplier"], 0.25)
            self.assertEqual(item["candidate_backfill_values"]["coverage_adjusted_external_signal_score"], 2.0)

    def test_applies_ready_candidates_to_matching_training_examples(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            web_dir = root / "web"
            reports_dir = root / "reports"
            write_latest(web_dir, coverage_gap_snapshot())
            report_path = reports_dir / "2026-05-24-postmarket.json"
            write_report(
                report_path,
                {
                    "as_of": "2026-05-24",
                    "session": "postmarket",
                    "external_signals": external_signal_health(),
                    "recommendation_training_examples": [
                        {"example_id": "trial-goog-1m", "symbol": "GOOG", "external_feed_status": None}
                    ],
                    "feature_matrix": {
                        "rows": [
                            {
                                "symbol": "GOOG",
                                "external_signal_score": 8.0,
                                "external_signal_count": 2,
                                "external_source_count": 1,
                            }
                        ]
                    },
                },
            )
            export = build_coverage_gap_plan_export(
                web_dir,
                reports_dir=reports_dir,
                resolve_candidates=True,
                candidate_source="eligible-reports",
            )

            result = apply_coverage_gap_candidate_backfills(export["backfill_items"])

            self.assertEqual(result["status"], "applied")
            self.assertEqual(result["applied_item_count"], 1)
            updated = json.loads(report_path.read_text(encoding="utf-8"))
            example = updated["recommendation_training_examples"][0]
            self.assertEqual(example["external_signal_score"], 8.0)
            self.assertEqual(example["external_feed_status"], "limited")
            self.assertEqual(example["external_coverage_multiplier"], 0.25)
            self.assertEqual(example["coverage_adjusted_external_signal_score"], 2.0)
            self.assertEqual(example["external_provider_count"], 6)
            self.assertEqual(example["external_provider_ok_count"], 1)
            self.assertEqual(example["external_signal_count"], 2)
            self.assertEqual(example["external_source_count"], 1)
            self.assertEqual(example["external_coverage_backfill"]["external_coverage_gap_id"], "gap-goog-1m")

    def test_apply_refuses_blocked_candidates(self):
        with self.assertRaises(RuntimeError):
            apply_coverage_gap_candidate_backfills([{"symbol": "GOOG", "candidate_apply_status": "blocked"}])

    def test_cli_prints_json_without_loading_private_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            write_latest(web_dir, coverage_gap_snapshot())
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(["coverage-gap-plan", "--web-dir", str(web_dir)])

            self.assertEqual(exit_code, 0)
            export = json.loads(stdout.getvalue())
            self.assertEqual(export["status"], "ready")
            self.assertEqual(export["priority_rows"][0]["symbol"], "GOOG")

    def test_exports_residual_non_blocking_gap_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            reports_dir = Path(tmp) / "reports"
            payload = coverage_gap_snapshot()
            plan = payload["backtest"]["pending_external_coverage_gap_plan"]
            residual = dict(plan["priority_rows"][0])
            residual["symbol"] = "NVDA"
            residual["residual_learning_value_score"] = 15.282
            residual["residual_learning_value_reason"] = "1m label; action=hold; expected_return=31.41; delta=0.0"
            residual["residual_backfill_status"] = "non_blocking"
            plan["residual_gap_count"] = 1
            plan["residual_gap_status"] = "non_blocking_learning_backlog"
            plan["residual_ranking_version"] = "2026-05-external-coverage-residual-ranking-v1"
            plan["residual_hidden_gap_count"] = 7
            plan["residual_required_observation_date_limit"] = 8
            plan["residual_required_observation_dates"] = [
                {
                    "required_external_observation_date": "2026-05-23",
                    "gap_count": 8,
                    "source_trial_count": 6,
                    "symbol_count": 6,
                    "symbols": ["ASML", "AVGO", "BE", "CRWV", "GOOG", "META"],
                    "earliest_due_date": "2026-06-25",
                    "latest_due_date": "2026-08-25",
                }
            ]
            plan["residual_rows"] = [residual]
            write_latest(web_dir, payload)
            write_report(
                reports_dir / "2026-05-23-postmarket.json",
                {
                    "as_of": "2026-05-23",
                    "session": "postmarket",
                    "portfolio_benchmark": {"action_queue": [{"symbol": "NVDA"}]},
                },
            )

            export = build_coverage_gap_plan_export(web_dir, reports_dir=reports_dir)

            self.assertEqual(export["residual_gap_count"], 1)
            self.assertEqual(export["residual_gap_status"], "non_blocking_learning_backlog")
            self.assertEqual(export["residual_hidden_gap_count"], 7)
            self.assertEqual(export["residual_required_observation_date_limit"], 8)
            self.assertEqual(export["residual_required_observation_dates"][0]["gap_count"], 8)
            self.assertEqual(export["residual_required_observation_dates"][0]["source_trial_count"], 6)
            self.assertEqual(export["residual_rows"][0]["symbol"], "NVDA")
            self.assertEqual(export["residual_rows"][0]["residual_learning_value_score"], 15.282)
            self.assertEqual(export["residual_backfill_items"][0]["symbol"], "NVDA")
            recovery_plan = export["residual_recovery_plan"]
            self.assertEqual(recovery_plan["status"], "blocked")
            self.assertEqual(recovery_plan["blocked_item_count"], 1)
            self.assertEqual(recovery_plan["items"][0]["status"], "required_date_reports_missing_feature_matrix")
            self.assertEqual(
                recovery_plan["items"][0]["missing_sections"],
                ["external_signals", "feature_matrix.rows", "recommendation_training_examples"],
            )
            self.assertEqual(recovery_plan["items"][0]["exact_reports"][0]["action_queue_count"], 1)

    def test_materializes_recovery_training_examples_from_legacy_action_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            reports_dir = Path(tmp) / "reports"
            payload = coverage_gap_snapshot()
            plan = payload["backtest"]["pending_external_coverage_gap_plan"]
            residual = dict(plan["priority_rows"][0])
            residual["as_of"] = "2026-05-23"
            residual["required_external_observation_date"] = "2026-05-23"
            residual["symbol"] = "NVDA"
            residual["source_trial_id"] = "legacy-trial"
            plan["priority_gap_count"] = 0
            plan["priority_rows"] = []
            plan["residual_gap_count"] = 1
            plan["residual_gap_status"] = "non_blocking_learning_backlog"
            plan["residual_hidden_gap_count"] = 0
            plan["residual_required_observation_dates"] = [
                {
                    "required_external_observation_date": "2026-05-23",
                    "gap_count": 1,
                    "source_trial_count": 1,
                    "symbol_count": 1,
                    "symbols": ["NVDA"],
                    "earliest_due_date": "2026-06-25",
                    "latest_due_date": "2026-06-25",
                }
            ]
            plan["residual_rows"] = [residual]
            write_latest(web_dir, payload)
            report_path = reports_dir / "2026-05-23-postmarket.json"
            legacy_report = {
                "as_of": "2026-05-23",
                "session": "postmarket",
                "decision_cards": [
                    {
                        "symbol": "NVDA",
                        "bucket": "semis_networking_hbm",
                        "signal_families": ["manager", "catalyst"],
                        "top_event_types": ["capex_signal"],
                    }
                ],
                "portfolio_benchmark": {
                    "action_queue": [
                        {
                            "symbol": "NVDA",
                            "portfolio_weight": 0.08,
                            "why": "Watch position while thesis develops.",
                            "event_types": ["capex_signal"],
                        }
                    ]
                },
            }
            write_report(report_path, legacy_report)
            expected_trial = trials_from_payload_actions(legacy_report)[0]
            export = build_coverage_gap_plan_export(web_dir, reports_dir=reports_dir)

            result = materialize_recovery_training_examples(export["residual_recovery_plan"], reports_dir)

            self.assertEqual(result["status"], "materialized")
            self.assertEqual(result["example_count"], 1)
            updated = json.loads(report_path.read_text(encoding="utf-8"))
            example = updated["recommendation_training_examples"][0]
            self.assertEqual(example["example_id"], expected_trial.trial_id)
            self.assertEqual(example["symbol"], "NVDA")
            self.assertEqual(example["trade_action"], "watch")
            self.assertEqual(example["external_signal_score"], None)
            self.assertEqual(example["external_feed_status"], None)
            self.assertEqual(example["legacy_recovery"]["source"], "portfolio_benchmark.action_queue")

    def test_materializes_recovery_feature_skeleton_without_external_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            reports_dir = Path(tmp) / "reports"
            payload = coverage_gap_snapshot()
            plan = payload["backtest"]["pending_external_coverage_gap_plan"]
            residual = dict(plan["priority_rows"][0])
            residual["as_of"] = "2026-05-23"
            residual["required_external_observation_date"] = "2026-05-23"
            residual["symbol"] = "NVDA"
            plan["priority_gap_count"] = 0
            plan["priority_rows"] = []
            plan["residual_gap_count"] = 1
            plan["residual_gap_status"] = "non_blocking_learning_backlog"
            plan["residual_hidden_gap_count"] = 0
            plan["residual_required_observation_dates"] = [
                {
                    "required_external_observation_date": "2026-05-23",
                    "gap_count": 1,
                    "source_trial_count": 1,
                    "symbol_count": 1,
                    "symbols": ["NVDA"],
                    "earliest_due_date": "2026-06-25",
                    "latest_due_date": "2026-06-25",
                }
            ]
            plan["residual_rows"] = [residual]
            write_latest(web_dir, payload)
            report_path = reports_dir / "2026-05-23-postmarket.json"
            write_report(
                report_path,
                {
                    "as_of": "2026-05-23",
                    "session": "postmarket",
                    "decision_cards": [
                        {
                            "symbol": "NVDA",
                            "bucket": "semis_networking_hbm",
                            "score": 55,
                            "event_score": 20,
                            "signal_families": ["manager", "catalyst"],
                            "signal_family_count": 2,
                            "top_event_types": ["capex_signal"],
                        }
                    ],
                    "portfolio_benchmark": {
                        "action_queue": [
                            {
                                "symbol": "NVDA",
                                "portfolio_weight": 0.08,
                                "why": "Watch position while thesis develops.",
                                "event_types": ["capex_signal"],
                            }
                        ]
                    },
                    "recommendation_training_examples": [{"example_id": "legacy-nvda", "symbol": "NVDA"}],
                },
            )
            export = build_coverage_gap_plan_export(web_dir, reports_dir=reports_dir)

            result = materialize_recovery_feature_skeleton(export["residual_recovery_plan"], reports_dir)

            self.assertEqual(result["status"], "materialized")
            self.assertEqual(result["feature_row_count"], 1)
            updated = json.loads(report_path.read_text(encoding="utf-8"))
            row = updated["feature_matrix"]["rows"][0]
            self.assertEqual(row["symbol"], "NVDA")
            self.assertEqual(row["bucket"], "semis_networking_hbm")
            self.assertEqual(row["score"], 55.0)
            self.assertEqual(row["external_signal_score"], None)
            self.assertEqual(row["external_feed_status"], None)
            self.assertEqual(row["legacy_recovery"]["source"], "portfolio_benchmark.action_queue+decision_cards")
            refreshed = build_coverage_gap_plan_export(web_dir, reports_dir=reports_dir)
            self.assertEqual(
                refreshed["residual_recovery_plan"]["items"][0]["status"],
                "required_date_reports_missing_external_signals",
            )
            self.assertEqual(refreshed["residual_recovery_plan"]["items"][0]["missing_sections"], ["external_signals"])

    def test_attaches_recovery_external_signal_snapshot_to_matching_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            reports_dir = Path(tmp) / "reports"
            payload = coverage_gap_snapshot()
            plan = payload["backtest"]["pending_external_coverage_gap_plan"]
            residual = dict(plan["priority_rows"][0])
            residual["as_of"] = "2026-05-23"
            residual["required_external_observation_date"] = "2026-05-23"
            residual["symbol"] = "NVDA"
            plan["priority_gap_count"] = 0
            plan["priority_rows"] = []
            plan["residual_gap_count"] = 1
            plan["residual_gap_status"] = "non_blocking_learning_backlog"
            plan["residual_hidden_gap_count"] = 0
            plan["residual_required_observation_dates"] = [
                {
                    "required_external_observation_date": "2026-05-23",
                    "gap_count": 1,
                    "source_trial_count": 1,
                    "symbol_count": 1,
                    "symbols": ["NVDA"],
                    "earliest_due_date": "2026-06-25",
                    "latest_due_date": "2026-06-25",
                }
            ]
            plan["residual_rows"] = [residual]
            write_latest(web_dir, payload)
            report_path = reports_dir / "2026-05-23-postmarket.json"
            write_report(
                report_path,
                {
                    "as_of": "2026-05-23",
                    "session": "postmarket",
                    "recommendation_training_examples": [{"example_id": "legacy-nvda", "symbol": "NVDA"}],
                    "feature_matrix": {
                        "rows": [
                            {
                                "symbol": "NVDA",
                                "external_signal_score": None,
                                "coverage_adjusted_external_signal_score": None,
                                "external_coverage_multiplier": None,
                                "external_feed_status": None,
                            }
                        ]
                    },
                },
            )
            snapshot_path = Path(tmp) / "2026-05-23-external-signals.json"
            snapshot = {
                "as_of": "2026-05-23",
                "status": "limited",
                "provider_count": 2,
                "provider_ok_count": 1,
                "provider_ok_ratio": 0.5,
                "signal_count": 1,
                "source_statuses": [
                    {"source": "test_ok", "status": "ok"},
                    {"source": "test_limited", "status": "limited"},
                ],
                "by_symbol": {
                    "NVDA": {
                        "external_signal_score": 8.0,
                        "signal_count": 1,
                        "source_count": 1,
                        "external_status": "limited",
                    }
                },
            }
            snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
            export = build_coverage_gap_plan_export(web_dir, reports_dir=reports_dir)

            result = attach_recovery_external_signals(export["residual_recovery_plan"], reports_dir, snapshot_path)

            self.assertEqual(result["status"], "attached")
            self.assertEqual(result["attached_report_count"], 1)
            updated = json.loads(report_path.read_text(encoding="utf-8"))
            row = updated["feature_matrix"]["rows"][0]
            self.assertEqual(updated["external_signals"]["as_of"], "2026-05-23")
            self.assertEqual(row["external_signal_score"], 8.0)
            self.assertEqual(row["external_feed_status"], "limited")
            self.assertEqual(row["external_provider_count"], 2)
            self.assertEqual(row["external_provider_ok_count"], 1)
            self.assertEqual(row["external_signal_count"], 1)
            self.assertEqual(row["external_source_count"], 1)
            example = updated["recommendation_training_examples"][0]
            self.assertEqual(example["external_signal_score"], 8.0)
            self.assertEqual(example["external_feed_status"], "limited")
            self.assertEqual(example["external_signal_count"], 1)
            self.assertEqual(updated["legacy_recovery"]["external_signals_source"], "attached_snapshot")
            refreshed = build_coverage_gap_plan_export(web_dir, reports_dir=reports_dir)
            self.assertEqual(refreshed["residual_recovery_plan"]["status"], "ready")

    def test_attach_recovery_external_signal_snapshot_syncs_existing_external_report_examples(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports_dir = Path(tmp) / "reports"
            report_path = reports_dir / "2026-05-23-postmarket.json"
            snapshot = {
                "as_of": "2026-05-23",
                "status": "limited",
                "provider_count": 2,
                "provider_ok_count": 1,
                "provider_ok_ratio": 0.5,
                "signal_count": 1,
                "source_statuses": [
                    {"source": "test_ok", "status": "ok"},
                    {"source": "test_limited", "status": "limited"},
                ],
                "by_symbol": {
                    "NVDA": {
                        "external_signal_score": 8.0,
                        "signal_count": 1,
                        "source_count": 1,
                        "external_status": "limited",
                    }
                },
            }
            write_report(
                report_path,
                {
                    "as_of": "2026-05-23",
                    "session": "postmarket",
                    "external_signals": snapshot,
                    "recommendation_training_examples": [{"example_id": "legacy-nvda", "symbol": "NVDA"}],
                    "feature_matrix": {
                        "rows": [
                            {
                                "symbol": "NVDA",
                                "external_signal_score": 8.0,
                                "coverage_adjusted_external_signal_score": 4.0,
                                "external_coverage_multiplier": 0.5,
                                "external_feed_status": "limited",
                                "external_provider_count": 2,
                                "external_provider_ok_count": 1,
                                "external_provider_ok_ratio": 0.5,
                                "external_provider_gap_count": 1,
                                "external_provider_configuration_gap_count": 0,
                                "external_provider_transient_gap_count": 0,
                                "external_provider_stale_gap_count": 0,
                                "external_provider_runtime_gap_count": 0,
                                "external_provider_other_gap_count": 1,
                                "external_provider_primary_gap_severity": "investigate",
                                "external_provider_gap_severity_score": 27.5,
                                "external_signal_count": 1,
                                "external_source_count": 1,
                            }
                        ]
                    },
                },
            )
            snapshot_path = Path(tmp) / "2026-05-23-external-signals.json"
            snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
            recovery_plan = {
                "items": [
                    {
                        "required_external_observation_date": "2026-05-23",
                        "exact_reports": [{"report": str(report_path)}],
                    }
                ]
            }

            result = attach_recovery_external_signals(recovery_plan, reports_dir, snapshot_path)

            self.assertEqual(result["status"], "synced")
            self.assertEqual(result["synced_report_count"], 1)
            self.assertGreater(result["training_example_field_update_count"], 0)
            updated = json.loads(report_path.read_text(encoding="utf-8"))
            example = updated["recommendation_training_examples"][0]
            self.assertEqual(example["external_signal_score"], 8.0)
            self.assertEqual(example["coverage_adjusted_external_signal_score"], 4.0)
            self.assertEqual(example["external_feed_status"], "limited")

    def test_attach_recovery_external_signal_snapshot_reports_attached_without_field_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports_dir = Path(tmp) / "reports"
            report_path = reports_dir / "2026-05-23-postmarket.json"
            write_report(
                report_path,
                {
                    "as_of": "2026-05-23",
                    "session": "postmarket",
                    "feature_matrix": {
                        "rows": [
                            {
                                "symbol": "NVDA",
                                "external_signal_score": 8.0,
                                "coverage_adjusted_external_signal_score": 4.0,
                                "external_coverage_multiplier": 0.5,
                                "external_feed_status": "limited",
                                "external_provider_count": 2,
                                "external_provider_ok_count": 1,
                                "external_provider_ok_ratio": 0.5,
                                "external_provider_gap_count": 1,
                                "external_provider_configuration_gap_count": 0,
                                "external_provider_transient_gap_count": 0,
                                "external_provider_stale_gap_count": 0,
                                "external_provider_runtime_gap_count": 0,
                                "external_provider_other_gap_count": 1,
                                "external_provider_primary_gap_severity": "investigate",
                                "external_provider_gap_severity_score": 27.5,
                                "external_signal_count": 1,
                                "external_source_count": 1,
                            }
                        ]
                    },
                },
            )
            snapshot_path = Path(tmp) / "2026-05-23-external-signals.json"
            snapshot = {
                "as_of": "2026-05-23",
                "status": "limited",
                "provider_count": 2,
                "provider_ok_count": 1,
                "provider_ok_ratio": 0.5,
                "signal_count": 1,
                "source_statuses": [
                    {"source": "test_ok", "status": "ok"},
                    {"source": "test_limited", "status": "limited"},
                ],
                "by_symbol": {
                    "NVDA": {
                        "external_signal_score": 8.0,
                        "signal_count": 1,
                        "source_count": 1,
                        "external_status": "limited",
                    }
                },
            }
            snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
            recovery_plan = {
                "items": [
                    {
                        "required_external_observation_date": "2026-05-23",
                        "exact_reports": [{"report": str(report_path)}],
                    }
                ]
            }

            result = attach_recovery_external_signals(recovery_plan, reports_dir, snapshot_path)

            self.assertEqual(result["status"], "attached")
            self.assertEqual(result["attached_report_count"], 1)
            self.assertEqual(result["field_update_count"], 0)
            updated = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["external_signals"]["as_of"], "2026-05-23")

    def test_residual_candidate_queue_is_opt_in_and_trial_deduped(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            payload = coverage_gap_snapshot()
            plan = payload["backtest"]["pending_external_coverage_gap_plan"]
            base = dict(plan["priority_rows"][0])
            nvda_1m = dict(
                base,
                external_coverage_gap_id="residual-nvda-1m",
                symbol="NVDA",
                horizon="1m",
                source_trial_id="trial-nvda",
            )
            nvda_3m = dict(
                base,
                external_coverage_gap_id="residual-nvda-3m",
                symbol="NVDA",
                horizon="3m",
                source_trial_id="trial-nvda",
            )
            mu_1m = dict(
                base,
                external_coverage_gap_id="residual-mu-1m",
                symbol="MU",
                horizon="1m",
                source_trial_id="trial-mu",
            )
            plan["priority_gap_count"] = 0
            plan["priority_rows"] = []
            plan["residual_gap_count"] = 3
            plan["residual_gap_status"] = "non_blocking_learning_backlog"
            plan["residual_rows"] = [nvda_1m, nvda_3m, mu_1m]
            write_latest(web_dir, payload)

            default_export = build_coverage_gap_plan_export(web_dir)
            residual_export = build_coverage_gap_plan_export(web_dir, candidate_queue="residual", candidate_limit=2)

            self.assertEqual(default_export["candidate_queue"], "priority")
            self.assertEqual(default_export["candidate_items"], [])
            self.assertEqual(residual_export["candidate_queue"], "residual")
            self.assertEqual(residual_export["candidate_limit"], 2)
            self.assertEqual(residual_export["candidate_item_count"], 2)
            self.assertEqual(
                [item["external_coverage_gap_id"] for item in residual_export["candidate_items"]],
                ["residual-nvda-1m", "residual-mu-1m"],
            )
            self.assertEqual(
                [item["source_trial_id"] for item in residual_export["candidate_items"]],
                ["trial-nvda", "trial-mu"],
            )

    def test_cli_applies_residual_candidate_queue_when_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            web_dir = root / "web"
            reports_dir = root / "reports"
            payload = coverage_gap_snapshot()
            plan = payload["backtest"]["pending_external_coverage_gap_plan"]
            residual = dict(plan["priority_rows"][0])
            residual["external_coverage_gap_id"] = "residual-goog-1m"
            plan["priority_gap_count"] = 0
            plan["priority_rows"] = []
            plan["residual_gap_count"] = 1
            plan["residual_gap_status"] = "non_blocking_learning_backlog"
            plan["residual_rows"] = [residual]
            write_latest(web_dir, payload)
            report_path = reports_dir / "2026-05-24-postmarket.json"
            write_report(
                report_path,
                {
                    "as_of": "2026-05-24",
                    "session": "postmarket",
                    "external_signals": external_signal_health(),
                    "recommendation_training_examples": [
                        {"example_id": "trial-goog-1m", "symbol": "GOOG", "external_feed_status": None}
                    ],
                    "feature_matrix": {
                        "rows": [
                            {
                                "symbol": "GOOG",
                                "external_signal_score": 8.0,
                                "external_signal_count": 2,
                                "external_source_count": 1,
                            }
                        ]
                    },
                },
            )
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "coverage-gap-plan",
                        "--web-dir",
                        str(web_dir),
                        "--reports-dir",
                        str(reports_dir),
                        "--candidate-source",
                        "eligible-reports",
                        "--candidate-queue",
                        "residual",
                        "--candidate-limit",
                        "1",
                        "--apply-candidates",
                    ]
                )

            self.assertEqual(exit_code, 0)
            export = json.loads(stdout.getvalue())
            self.assertEqual(export["candidate_item_count"], 1)
            self.assertEqual(export["candidate_resolution"]["candidate_apply_ready_count"], 1)
            self.assertEqual(export["apply_result"]["applied_item_count"], 1)
            updated = json.loads(report_path.read_text(encoding="utf-8"))
            example = updated["recommendation_training_examples"][0]
            self.assertEqual(example["external_feed_status"], "limited")
            self.assertEqual(example["external_coverage_backfill"]["external_coverage_gap_id"], "residual-goog-1m")

    def test_missing_gap_plan_is_empty_not_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            write_latest(web_dir, {"as_of": "2026-05-25", "backtest": {}})

            export = build_coverage_gap_plan_export(web_dir)

            self.assertEqual(export["status"], "empty")
            self.assertEqual(export["priority_rows"], [])
            self.assertEqual(export["backfill_items"], [])
            self.assertEqual(export["residual_rows"], [])
            self.assertEqual(export["residual_backfill_items"], [])
            self.assertEqual(export["priority_gap_count"], 0)


def external_alignment_review_snapshot() -> dict:
    return {
        "as_of": "2026-05-25",
        "session": "premarket",
        "site": {"source_report": "2026-05-25-premarket.json"},
        "backtest": {
            "pending_external_alignment_review_count": 2,
            "pending_external_alignment_review_item_count": 1,
            "pending_external_alignment_review_queue_limit": 12,
            "pending_external_alignment_review_hidden_item_count": 0,
            "pending_external_alignment_review_acceptance_summary": {
                "label_count": 2,
                "work_item_count": 1,
                "check_count": 4,
                "open_check_count": 1,
                "open_label_count": 2,
                "metadata_ready_work_item_count": 1,
                "open_check_counts": {"matured_label_available": 1},
                "next_open_check_due_date": "2026-06-02",
                "next_open_check_due_label_count": 2,
                "next_open_check_due_work_item_count": 1,
                "next_open_check_due_visible_work_item_count": 1,
                "next_open_check_due_hidden_work_item_count": 0,
                "next_open_check_due_symbols": ["GOOG"],
                "next_open_check_due_horizons": ["5d"],
                "next_open_check_due_focus_counts": {
                    "external_disagreement": {"label_count": 2, "work_item_count": 1},
                },
                "next_open_check_due_learning_action_counts": {
                    "When the label matures, compare realized direction with the external signal.": {
                        "label_count": 2,
                        "work_item_count": 1,
                    },
                },
                "next_open_check_due_measurement_missing_field_counts": {},
            },
            "pending_external_alignment_review_due_dates": [
                {
                    "due_date": "2026-06-02",
                    "label_count": 2,
                    "work_item_count": 1,
                    "symbols": ["GOOG"],
                    "horizons": ["5d"],
                    "focus_counts": {
                        "external_disagreement": {"label_count": 2, "work_item_count": 1},
                    },
                }
            ],
            "pending_external_alignment_review_queue": [
                {
                    "external_alignment_review_id": "review-goog-5d",
                    "external_alignment_review_version": "2026-05-external-alignment-review-v1",
                    "symbol": "GOOG",
                    "bucket": "frontier_ai_platforms",
                    "trade_action": "trim",
                    "horizon": "5d",
                    "as_of": "2026-05-23",
                    "due_date": "2026-06-02",
                    "status": "pending",
                    "source_outcome_id": "outcome-goog-5d",
                    "source_trial_id": "trial-goog-5d",
                    "external_alignment": "conflict",
                    "external_alignment_review_reason": "External signal disagrees with the action direction.",
                    "external_alignment_review_focus": "external_disagreement",
                    "external_alignment_review_label_count": 2,
                    "external_alignment_review_priority": 101,
                    "external_alignment_review_priority_reason": "external disagreement is reviewed first",
                    "external_alignment_review_learning_action": "When the label matures, compare realized direction with the external signal.",
                    "external_alignment_review_measurement_plan": {
                        "engine_direction": "negative",
                        "external_signal_direction": "positive",
                        "coverage_adjusted_external_signal_score": 4.0,
                        "risk_adjusted_expected_return": 9.41,
                        "missing_measurement_fields": [],
                        "summary": "engine negative; external positive score 4; expected 9.41",
                    },
                    "external_alignment_review_acceptance_checks": [
                        {
                            "check": "source_trace_present",
                            "field": "source_outcome_id/source_trial_id",
                            "status": "passed",
                        },
                        {
                            "check": "review_focus_present",
                            "field": "external_alignment_review_focus",
                            "status": "passed",
                        },
                        {
                            "check": "learning_action_present",
                            "field": "external_alignment_review_learning_action",
                            "status": "passed",
                        },
                        {
                            "check": "matured_label_available",
                            "field": "due_date",
                            "status": "pending",
                        },
                    ],
                    "external_alignment_review_open_check_count": 1,
                }
            ],
        },
    }


def coverage_gap_snapshot() -> dict:
    return {
        "as_of": "2026-05-25",
        "session": "premarket",
        "site": {"source_report": "2026-05-25-premarket.json"},
        "backtest": {
            "pending_external_coverage_gap_plan": {
                "additional_external_coverage_needed": 1,
                "priority_gap_count": 1,
                "priority_acceptance_check_count": 2,
                "priority_open_acceptance_check_count": 2,
                "priority_acceptance_check_status_counts": {"pending": 2},
                "minimum_external_long_horizon_required": 20,
                "projected_external_long_horizon_count_after_priority_backfill": 20,
                "projected_external_additional_needed_after_priority_backfill": 0,
                "external_learning_ready_after_priority_backfill": True,
                "projected_external_learning_ready_date_after_priority_backfill": "2026-06-25",
                "priority_rows": [
                    {
                        "external_coverage_gap_id": "gap-goog-1m",
                        "external_coverage_gap_version": "2026-05-external-coverage-gap-v1",
                        "symbol": "GOOG",
                        "bucket": "AI Infrastructure",
                        "trade_action": "watch",
                        "horizon": "1m",
                        "as_of": "2026-05-24",
                        "due_date": "2026-06-25",
                        "status": "pending",
                        "source_outcome_id": "outcome-goog-1m",
                        "source_trial_id": "trial-goog-1m",
                        "external_feed_status": "limited",
                        "external_coverage_multiplier": 0.25,
                        "coverage_adjusted_external_signal_score": 5.0,
                        "external_alignment": "confirming",
                        "missing_external_fields": ["external_feed_status"],
                        "minimum_external_fields_to_backfill": [
                            "external_feed_status",
                            "external_coverage_multiplier",
                            "coverage_adjusted_external_signal_score",
                        ],
                        "required_external_observation_date": "2026-05-24",
                        "external_coverage_gap_reason": "GOOG 1m has no external observation attached.",
                        "external_coverage_gap_action": "Rebuild GOOG from decision-time inputs.",
                        "external_coverage_backfill_policy": "decision_time_only",
                        "external_coverage_acceptance_checks": [
                            {
                                "check": "external_feed_status_present",
                                "expected": "non_empty_non_unknown",
                                "field": "external_feed_status",
                                "status": "pending",
                            },
                            {
                                "check": "decision_time_only",
                                "expected": "source inputs captured on or before 2026-05-24",
                                "field": "required_external_observation_date",
                                "status": "pending",
                            },
                        ],
                    }
                ],
            }
        },
    }


def provider_gap_severity_backfill_snapshot() -> dict:
    return {
        "as_of": "2026-05-25",
        "session": "premarket",
        "site": {"source_report": "2026-05-25-premarket.json"},
        "backtest": {
            "pending_external_provider_gap_severity_observation_gap_hidden_calibration_backfill_record_count": 1,
            "pending_external_provider_gap_severity_observation_gap_hidden_calibration_backfill_record_queue_limit": 8,
            "pending_external_provider_gap_severity_observation_gap_hidden_calibration_backfill_record_queue": [
                {
                    "external_provider_gap_severity_observation_backfill_record_id": "provider-gap-record-goog-1m",
                    "external_provider_gap_severity_observation_backfill_record_version": (
                        "2026-05-external-provider-gap-severity-backfill-record-v1"
                    ),
                    "external_provider_gap_severity_observation_work_item_id": "provider-gap-work-goog-1m",
                    "symbol": "GOOG",
                    "horizon": "1m",
                    "decision_as_of": "2026-05-24",
                    "session": "postmarket",
                    "due_date": "2026-06-25",
                    "target_section": "recommendation_training_examples",
                    "source_report": "2026-05-24-postmarket.json",
                    "source_report_available": True,
                    "source_trial_ids": ["trial-goog-1m"],
                    "source_outcome_ids": ["outcome-goog-1m"],
                    "candidate_apply_status": "ready",
                    "candidate_apply_policy": "update_matching_recommendation_training_examples_by_source_trial_id",
                    "candidate_backfill_policy": "decision_time_external_signals_provider_status_only",
                    "candidate_source_section": "external_signals.source_statuses",
                    "candidate_provider_gap_severities": [
                        "configuration_required",
                        "runtime_budget",
                        "stale_or_empty",
                    ],
                    "candidate_provider_gap_sources": [
                        "alpha_vantage_news",
                        "finra_short_interest",
                        "gdelt_global_news",
                    ],
                    "candidate_missing_required_fields": [],
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
                    "candidate_backfill_values": {
                        "external_provider_gap_count": 4,
                        "external_provider_configuration_gap_count": 2,
                        "external_provider_runtime_gap_count": 1,
                        "external_provider_stale_gap_count": 1,
                        "external_provider_transient_gap_count": 0,
                        "external_provider_other_gap_count": 0,
                        "external_provider_primary_gap_severity": "configuration_required",
                        "external_provider_gap_severity_score": 53.33,
                    },
                }
            ],
        },
    }


def measurement_gap_snapshot() -> dict:
    return {
        "as_of": "2026-05-25",
        "session": "premarket",
        "site": {"source_report": "2026-05-25-premarket.json"},
        "backtest": {
            "pending_external_alignment_measurement_gap_label_count": 2,
            "pending_external_alignment_measurement_gap_item_count": 1,
            "pending_external_alignment_measurement_gap_hidden_item_count": 0,
            "pending_external_alignment_measurement_gap_queue_limit": 12,
            "pending_external_alignment_measurement_gap_plan": {
                "version": "2026-05-external-alignment-measurement-gap-v1",
                "label_count": 2,
                "work_item_count": 1,
                "hidden_work_item_count": 0,
                "queue_limit": 12,
                "next_due_date": "2026-06-02",
                "next_due_label_count": 2,
                "next_due_work_item_count": 1,
                "next_due_symbols": ["GOOG"],
                "next_due_horizons": ["5d"],
                "next_due_field_counts": {
                    "risk_adjusted_expected_return": {"label_count": 2, "work_item_count": 1},
                },
                "field_counts": {
                    "risk_adjusted_expected_return": {"label_count": 2, "work_item_count": 1},
                },
                "priority_acceptance_check_count": 2,
                "priority_open_acceptance_check_count": 2,
                "priority_acceptance_check_status_counts": {"pending": 2},
                "priority_symbols": ["GOOG"],
                "due_dates": [
                    {
                        "due_date": "2026-06-02",
                        "label_count": 2,
                        "work_item_count": 1,
                        "symbols": ["GOOG"],
                        "horizons": ["5d"],
                        "field_counts": {
                            "risk_adjusted_expected_return": {"label_count": 2, "work_item_count": 1},
                        },
                    }
                ],
            },
            "pending_external_alignment_measurement_gap_queue": [
                {
                    "external_alignment_measurement_gap_id": "measurement-gap-goog-5d",
                    "external_alignment_measurement_gap_version": "2026-05-external-alignment-measurement-gap-v1",
                    "external_alignment_review_id": "review-goog-5d",
                    "symbol": "GOOG",
                    "bucket": "frontier_ai_platforms",
                    "trade_action": "trim",
                    "horizon": "5d",
                    "as_of": "2026-05-23",
                    "due_date": "2026-06-02",
                    "status": "pending",
                    "source_outcome_id": "outcome-goog-5d",
                    "source_trial_id": "trial-goog-5d",
                    "session": "postmarket",
                    "external_alignment": "conflict",
                    "external_alignment_review_focus": "external_disagreement",
                    "external_alignment_review_label_count": 2,
                    "external_alignment_measurement_missing_label_count": 2,
                    "external_alignment_measurement_missing_fields": ["risk_adjusted_expected_return"],
                    "external_alignment_measurement_missing_field_counts": {
                        "risk_adjusted_expected_return": {"label_count": 2, "work_item_count": 1},
                    },
                    "risk_adjusted_expected_return": None,
                    "coverage_adjusted_external_signal_score": 4.0,
                    "external_alignment_measurement_gap_action": "Backfill risk adjusted expected return for GOOG from recommendation-time model and risk inputs captured on or before 2026-05-23; do not use later prices, news, filings, or outcome labels.",
                    "external_alignment_measurement_backfill_policy": "decision_time_only",
                    "external_alignment_measurement_acceptance_checks": [
                        {
                            "check": "risk_adjusted_expected_return_present",
                            "expected": "non_null",
                            "field": "risk_adjusted_expected_return",
                            "status": "pending",
                        },
                        {
                            "check": "decision_time_only",
                            "expected": "source inputs captured on or before 2026-05-23",
                            "field": "required_measurement_observation_date",
                            "status": "pending",
                        },
                    ],
                }
            ],
        },
    }


def write_latest(web_dir: Path, payload: dict) -> None:
    data_dir = web_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "latest.json").write_text(json.dumps(payload), encoding="utf-8")


def write_report(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def training_example(symbol: str, as_of: str, external: bool = False) -> dict:
    example = {
        "example_id": f"{symbol.lower()}-{as_of}",
        "as_of": as_of,
        "session": "postmarket",
        "symbol": symbol,
        "bucket": "ai_infrastructure",
        "trade_action": "watch",
        "recommended_delta_weight": 0.0,
        "target_weight": 0.0,
        "risk_adjusted_expected_return": 10.0,
        "evidence_quality": 60.0,
        "signal_families": ["external_feeds"] if external else [],
    }
    if external:
        example.update(
            {
                "external_signal_score": 8.0,
                "coverage_adjusted_external_signal_score": 4.0,
                "external_coverage_multiplier": 0.5,
                "external_feed_status": "limited",
                "external_provider_count": 2,
                "external_provider_ok_count": 1,
                "external_provider_ok_ratio": 0.5,
                "external_signal_count": 1,
                "external_source_count": 1,
            }
        )
    return example


def external_signal_health() -> dict:
    return {
        "status": "limited",
        "provider_count": 6,
        "provider_ok_count": 1,
        "provider_ok_ratio": 0.1667,
        "provider_status_counts": {"ok": 1, "limited": 5},
        "signal_count": 10,
    }


if __name__ == "__main__":
    unittest.main()
