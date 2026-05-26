import json
from pathlib import Path
import tempfile
import unittest

from invest.backtest import BACKTEST_VERSION
from invest.instrumentation import build_instrumentation_audit
from invest.quality import public_snapshot_quality_failures


class PublicSnapshotQualityTests(unittest.TestCase):
    def test_accepts_current_instrumentation_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            payload = valid_public_payload()
            payload["instrumentation_audit"] = build_instrumentation_audit(payload)
            write_latest(web_dir, payload)

            self.assertEqual(public_snapshot_quality_failures(web_dir), [])

    def test_recomputes_audit_instead_of_trusting_stale_embedded_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            payload = valid_public_payload()
            payload["feature_matrix"]["rows"] = [{"symbol": "NVDA"}]
            payload["engine"]["ranked_candidates"] = [{"symbol": "NVDA"}]
            payload["instrumentation_audit"] = {
                "version": "2026-05-number-wiring-audit-v1",
                "status": "ok",
                "failure_count": 0,
                "failures": [],
            }
            write_latest(web_dir, payload)

            failures = public_snapshot_quality_failures(web_dir)

            self.assertTrue(any("stale instrumentation audit version" in failure for failure in failures))
            self.assertTrue(any("feature_matrix_external_reliability_fields_present" in failure for failure in failures))

    def test_requires_learning_projection_when_public_outcomes_are_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            payload = valid_public_payload()
            payload["backtest"] = {
                "outcome_count": 2,
                "completed_outcome_count": 0,
                "pending_outcome_count": 2,
                "missing_price_count": 0,
                "outcomes": [
                    {"status": "pending", "symbol": "NVDA", "horizon": "1m", "due_date": "2026-06-24"},
                    {"status": "pending", "symbol": "AMD", "horizon": "3m", "due_date": "2026-08-24"},
                ],
            }
            payload["outcome_diagnostics"] = {
                "label_maturity": {
                    "learning_ready": False,
                    "completed_long_horizon_count": 0,
                    "minimum_long_horizon_required": 20,
                }
            }
            payload["instrumentation_audit"] = build_instrumentation_audit(payload)
            write_latest(web_dir, payload)

            failures = public_snapshot_quality_failures(web_dir)

            self.assertTrue(any("pending outcomes require outcome_diagnostics.pending_label_schedule" in failure for failure in failures))
            self.assertTrue(any("pending outcomes require outcome_diagnostics.horizon_label_counts" in failure for failure in failures))
            self.assertTrue(any("pending outcomes require backtest.pending_by_external_feed_status" in failure for failure in failures))
            self.assertTrue(any("pending outcomes require backtest.pending_by_external_coverage" in failure for failure in failures))
            self.assertTrue(any("pending outcomes require backtest.pending_by_external_alignment" in failure for failure in failures))
            self.assertTrue(any("pending outcomes require outcome_diagnostics.learning_readiness_projection" in failure for failure in failures))

    def test_requires_pending_external_alignment_watchlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            payload = valid_public_payload()
            payload["backtest"] = {
                "outcome_count": 1,
                "completed_outcome_count": 0,
                "pending_outcome_count": 1,
                "missing_price_count": 0,
                "outcomes": [
                    {
                        "status": "pending",
                        "symbol": "MU",
                        "horizon": "5d",
                        "due_date": "2026-06-02",
                        "external_alignment": "conflict",
                    }
                ],
                "pending_by_external_feed_status": [{"key": "limited", "pending_count": 1}],
                "pending_by_external_coverage": [{"key": "thin_coverage", "pending_count": 1}],
                "pending_by_external_alignment": [{"key": "conflict", "pending_count": 1}],
                "pending_external_alignment_due_dates": [
                    {"due_date": "2026-06-02", "due_count": 1, "conflict_count": 1}
                ],
            }
            payload["outcome_diagnostics"] = {
                "label_maturity": {"learning_ready": True},
                "pending_label_schedule": {"pending_label_count": 1},
                "horizon_label_counts": [{"horizon": "5d", "pending_count": 1}],
                "learning_readiness_projection": {"pending_learning_labels_needed_for_readiness": 0},
            }
            payload["instrumentation_audit"] = build_instrumentation_audit(payload)
            write_latest(web_dir, payload)

            failures = public_snapshot_quality_failures(web_dir)

            self.assertTrue(any("pending external alignment buckets require backtest.pending_external_alignment_watchlist" in failure for failure in failures))

    def test_requires_pending_external_alignment_watchlist_review_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            payload = valid_public_payload()
            payload["backtest"] = {
                "outcome_count": 1,
                "completed_outcome_count": 0,
                "pending_outcome_count": 1,
                "missing_price_count": 0,
                "outcomes": [
                    {"status": "pending", "symbol": "MU", "horizon": "5d", "due_date": "2026-06-02", "external_alignment": "conflict"}
                ],
                "pending_by_external_feed_status": [{"key": "limited", "pending_count": 1}],
                "pending_by_external_coverage": [{"key": "thin_coverage", "pending_count": 1}],
                "pending_by_external_alignment": [{"key": "conflict", "pending_count": 1}],
                "pending_external_alignment_due_dates": [
                    {"due_date": "2026-06-02", "due_count": 1, "conflict_count": 1}
                ],
                "pending_external_alignment_watchlist": [
                    {"status": "pending", "symbol": "MU", "horizon": "5d", "due_date": "2026-06-02", "external_alignment": "conflict"}
                ],
            }
            payload["outcome_diagnostics"] = {
                "label_maturity": {"learning_ready": True},
                "pending_label_schedule": {"pending_label_count": 1},
                "horizon_label_counts": [{"horizon": "5d", "pending_count": 1}],
                "learning_readiness_projection": {"pending_learning_labels_needed_for_readiness": 0},
            }
            payload["instrumentation_audit"] = build_instrumentation_audit(payload)
            write_latest(web_dir, payload)

            failures = public_snapshot_quality_failures(web_dir)

            self.assertTrue(any("pending external alignment watchlist requires review reasons" in failure for failure in failures))

    def test_requires_pending_external_alignment_due_dates_to_match_buckets(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            payload = valid_public_payload()
            payload["backtest"] = {
                "outcome_count": 1,
                "completed_outcome_count": 0,
                "pending_outcome_count": 1,
                "missing_price_count": 0,
                "outcomes": [
                    {"status": "pending", "symbol": "MU", "horizon": "5d", "due_date": "2026-06-02", "external_alignment": "conflict"}
                ],
                "pending_by_external_feed_status": [{"key": "limited", "pending_count": 1}],
                "pending_by_external_coverage": [{"key": "thin_coverage", "pending_count": 1}],
                "pending_by_external_alignment": [{"key": "conflict", "pending_count": 1}],
                "pending_external_alignment_due_dates": [],
                "pending_external_alignment_watchlist": [
                    {
                        "status": "pending",
                        "symbol": "MU",
                        "horizon": "5d",
                        "due_date": "2026-06-02",
                        "external_alignment": "conflict",
                        "external_alignment_review_reason": "External signal disagrees with the action direction.",
                    }
                ],
            }
            payload["outcome_diagnostics"] = {
                "label_maturity": {"learning_ready": True},
                "pending_label_schedule": {"pending_label_count": 1},
                "horizon_label_counts": [{"horizon": "5d", "pending_count": 1}],
                "learning_readiness_projection": {"pending_learning_labels_needed_for_readiness": 0},
            }
            payload["instrumentation_audit"] = build_instrumentation_audit(payload)
            write_latest(web_dir, payload)

            failures = public_snapshot_quality_failures(web_dir)

            self.assertTrue(any("backtest.pending_external_alignment_due_dates covers 0 actionable external alignment labels; expected 1" in failure for failure in failures))

    def test_requires_pending_external_alignment_review_queue_for_non_confirming_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            payload = valid_public_payload()
            payload["backtest"] = {
                "outcome_count": 1,
                "completed_outcome_count": 0,
                "pending_outcome_count": 1,
                "missing_price_count": 0,
                "outcomes": [
                    {"status": "pending", "symbol": "MU", "horizon": "5d", "due_date": "2026-06-02", "external_alignment": "conflict"}
                ],
                "pending_by_external_feed_status": [{"key": "limited", "pending_count": 1}],
                "pending_by_external_coverage": [{"key": "thin_coverage", "pending_count": 1}],
                "pending_by_external_alignment": [{"key": "conflict", "pending_count": 1}],
                "pending_external_alignment_due_dates": [
                    {"due_date": "2026-06-02", "due_count": 1, "conflict_count": 1}
                ],
                "pending_external_alignment_watchlist": [
                    {
                        "status": "pending",
                        "symbol": "MU",
                        "horizon": "5d",
                        "due_date": "2026-06-02",
                        "external_alignment": "conflict",
                        "external_alignment_review_reason": "External signal disagrees with the action direction.",
                    }
                ],
            }
            payload["outcome_diagnostics"] = {
                "label_maturity": {"learning_ready": True},
                "pending_label_schedule": {"pending_label_count": 1},
                "horizon_label_counts": [{"horizon": "5d", "pending_count": 1}],
                "learning_readiness_projection": {"pending_learning_labels_needed_for_readiness": 0},
            }
            payload["instrumentation_audit"] = build_instrumentation_audit(payload)
            write_latest(web_dir, payload)

            failures = public_snapshot_quality_failures(web_dir)

            self.assertTrue(
                any("backtest.pending_external_alignment_review_count is 0; expected 1" in failure for failure in failures)
            )
            payload["backtest"]["pending_external_alignment_review_count"] = 1
            payload["backtest"]["pending_external_alignment_review_item_count"] = 1
            payload["backtest"]["pending_external_alignment_review_queue"] = [
                {
                    "status": "pending",
                    "symbol": "MU",
                    "horizon": "5d",
                    "due_date": "2026-06-02",
                    "external_alignment": "conflict",
                    "external_alignment_review_reason": "External signal disagrees with the action direction.",
                }
            ]
            payload["backtest"]["pending_external_alignment_review_hidden_item_count"] = 1
            payload["backtest"]["pending_external_alignment_review_due_dates"] = [
                {"due_date": "2026-06-02", "label_count": 1, "work_item_count": 1}
            ]
            write_latest(web_dir, payload)

            failures = public_snapshot_quality_failures(web_dir)

            self.assertTrue(
                any("backtest.pending_external_alignment_review_hidden_item_count is 1; expected 0" in failure for failure in failures)
            )
            payload["backtest"]["pending_external_alignment_review_hidden_item_count"] = 0
            write_latest(web_dir, payload)

            failures = public_snapshot_quality_failures(web_dir)

            self.assertTrue(
                any(
                    "non-confirming pending external alignment labels require backtest.pending_external_alignment_review_acceptance_summary" in failure
                    for failure in failures
                )
            )
            payload["backtest"]["pending_external_alignment_review_acceptance_summary"] = {
                "label_count": 1,
                "work_item_count": 1,
                "check_count": 2,
                "open_check_count": 1,
                "open_label_count": 1,
                "metadata_ready_work_item_count": 1,
                "open_check_counts": {"matured_label_available": 1},
                "next_open_check_due_date": "2026-06-02",
                "next_open_check_due_open_check_count": 1,
                "next_open_check_due_label_count": 1,
                "next_open_check_due_work_item_count": 1,
                "next_open_check_due_visible_work_item_count": 1,
                "next_open_check_due_hidden_work_item_count": 0,
                "next_open_check_due_fully_visible": True,
                "next_open_check_due_symbols": ["MU"],
                "next_open_check_due_horizons": ["5d"],
                "next_open_check_due_focus_counts": {
                    "external_disagreement": {"label_count": 1, "work_item_count": 1},
                },
                "next_open_check_due_learning_action_counts": {
                    "Compare realized direction with the external signal.": {"label_count": 1, "work_item_count": 1},
                },
                "next_open_check_due_measurement_missing_field_counts": {},
                "open_check_due_dates": [
                    {
                        "due_date": "2026-06-02",
                        "open_check_count": 1,
                        "label_count": 1,
                        "work_item_count": 1,
                        "symbols": ["MU"],
                        "horizons": ["5d"],
                        "focus_counts": {
                            "external_disagreement": {"label_count": 1, "work_item_count": 1},
                        },
                        "learning_action_counts": {
                            "Compare realized direction with the external signal.": {"label_count": 1, "work_item_count": 1},
                        },
                        "measurement_missing_field_counts": {},
                        "check_counts": {"matured_label_available": 1},
                    }
                ],
            }
            write_latest(web_dir, payload)

            failures = public_snapshot_quality_failures(web_dir)

            self.assertTrue(
                any(
                    "pending external alignment review queue requires review ids, focus, priority reasons, learning actions, measurement plans, acceptance checks, label count, review reasons, and source ids" in failure
                    for failure in failures
                )
            )
            payload["backtest"]["pending_external_alignment_review_queue"][0].update(
                {
                    "external_alignment_review_focus": "external_disagreement",
                    "external_alignment_review_id": "review-mu-conflict",
                    "external_alignment_review_priority_reason": "external disagreement is reviewed first",
                    "external_alignment_review_learning_action": "Compare realized direction with the external signal.",
                    "external_alignment_review_measurement_plan": {
                        "summary": "engine negative; external positive score 5; expected 10",
                        "missing_measurement_fields": [],
                    },
                    "external_alignment_review_acceptance_checks": [
                        {"check": "source_trace_present", "status": "passed"},
                        {"check": "matured_label_available", "status": "pending"},
                    ],
                    "external_alignment_review_open_check_count": 1,
                    "external_alignment_review_label_count": 1,
                    "source_outcome_id": "outcome-mu-conflict",
                    "source_trial_id": "trial-mu-conflict",
                }
            )
            payload["backtest"]["pending_external_alignment_review_due_dates"] = [
                {"due_date": "2026-06-02", "label_count": 0, "work_item_count": 0}
            ]
            write_latest(web_dir, payload)

            failures = public_snapshot_quality_failures(web_dir)

            self.assertTrue(
                any("backtest.pending_external_alignment_review_due_dates covers 0 labels/0 work items; expected 1 labels/1 work items" in failure for failure in failures)
            )

    def test_requires_learning_audit_gap_to_include_projection_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            payload = valid_public_payload()
            payload["engine"]["learning"] = {
                "status": "baseline_fallback",
                "message": "Insufficient completed outcomes.",
                "minimum_required": 20,
            }
            payload["backtest"] = {
                "outcome_count": 2,
                "completed_outcome_count": 0,
                "pending_outcome_count": 2,
                "missing_price_count": 0,
                "outcomes": [
                    {"status": "pending", "symbol": "NVDA", "horizon": "1m", "due_date": "2026-06-24"},
                    {"status": "pending", "symbol": "AMD", "horizon": "3m", "due_date": "2026-08-24"},
                ],
            }
            payload["outcome_diagnostics"] = {
                "label_maturity": {
                    "learning_ready": False,
                    "completed_long_horizon_count": 0,
                    "minimum_long_horizon_required": 20,
                },
                "pending_label_schedule": {
                    "pending_label_count": 2,
                    "pending_learning_label_count": 2,
                },
                "learning_readiness_projection": {
                    "pending_learning_labels_needed_for_readiness": 20,
                    "next_learning_label_due_date": "2026-06-24",
                    "estimated_learning_ready_date": "2026-08-24",
                },
            }
            payload["audit"] = {
                "data_gaps": [
                    {
                        "area": "engine",
                        "label": "Learning reranker",
                        "status": "baseline_fallback",
                        "detail": "Insufficient completed outcomes.",
                    }
                ]
            }
            payload["instrumentation_audit"] = build_instrumentation_audit(payload)
            write_latest(web_dir, payload)

            failures = public_snapshot_quality_failures(web_dir)

            self.assertTrue(any("Learning reranker audit gap must include the projected learning label dates" in failure for failure in failures))

    def test_requires_learning_audit_gap_to_include_external_coverage_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            payload = valid_public_payload()
            payload["engine"]["learning"] = {
                "status": "baseline_fallback",
                "message": "Insufficient completed outcomes.",
                "minimum_required": 20,
            }
            payload["backtest"] = {
                "outcome_count": 2,
                "completed_outcome_count": 0,
                "pending_outcome_count": 2,
                "missing_price_count": 0,
                "outcomes": [
                    {
                        "status": "pending",
                        "symbol": "NVDA",
                        "horizon": "1m",
                        "due_date": "2026-06-24",
                        "external_feed_status": "limited",
                        "external_coverage_multiplier": 0.25,
                        "coverage_adjusted_external_signal_score": 5.0,
                        "external_alignment": "aligned",
                    },
                    {"status": "pending", "symbol": "AMD", "horizon": "1m", "as_of": "2026-05-24", "due_date": "2026-06-24"},
                ],
                "pending_by_external_feed_status": [{"key": "limited", "pending_count": 1}, {"key": "unknown", "pending_count": 1}],
                "pending_by_external_coverage": [{"key": "thin_coverage", "pending_count": 1}, {"key": "unknown", "pending_count": 1}],
                "pending_by_external_alignment": [{"key": "aligned", "pending_count": 1}, {"key": "unknown", "pending_count": 1}],
                "pending_external_alignment_due_dates": [{"due_date": "2026-06-24", "due_count": 1, "aligned_count": 1}],
                "pending_external_alignment_watchlist": [
                    {
                        "status": "pending",
                        "symbol": "NVDA",
                        "horizon": "1m",
                        "due_date": "2026-06-24",
                        "external_alignment": "aligned",
                        "external_alignment_review_reason": "External signal reinforces the action direction.",
                    }
                ],
                "pending_external_coverage_gap_count": 1,
                "pending_external_coverage_gap_queue": [
                    {
                        "status": "pending",
                        "external_coverage_gap_id": "gap-amd-1m",
                        "symbol": "AMD",
                        "horizon": "1m",
                        "due_date": "2026-06-24",
                        "external_coverage_gap_reason": "AMD 1m has no external observation attached.",
                    }
                ],
                "pending_external_coverage_gap_plan": {
                    "additional_external_coverage_needed": 19,
                    "candidate_gap_count": 1,
                    "minimum_external_long_horizon_required": 20,
                    "observed_external_long_horizon_label_count": 1,
                    "priority_gap_count": 1,
                    "projected_external_long_horizon_count_after_priority_backfill": 2,
                    "projected_external_additional_needed_after_priority_backfill": 18,
                    "priority_rows": [
                        {
                            "status": "pending",
                            "external_coverage_gap_id": "gap-amd-1m",
                            "symbol": "AMD",
                            "horizon": "1m",
                            "due_date": "2026-06-24",
                            "external_coverage_gap_reason": "AMD 1m has no external observation attached.",
                            "external_coverage_gap_action": "Rebuild AMD from decision-time inputs.",
                            "external_coverage_backfill_policy": "decision_time_only",
                            "required_external_observation_date": "2026-05-24",
                            "external_coverage_acceptance_checks": [
                                {"check": "external_feed_status_present"},
                                {"check": "external_coverage_multiplier_present"},
                                {"check": "coverage_adjusted_external_signal_score_present"},
                                {"check": "decision_time_only"},
                            ],
                        }
                    ],
                },
            }
            payload["outcome_diagnostics"] = {
                "label_maturity": {"learning_ready": False, "completed_long_horizon_count": 0, "minimum_long_horizon_required": 20},
                "pending_label_schedule": {"pending_label_count": 2, "pending_learning_label_count": 2},
                "horizon_label_counts": [{"horizon": "1m", "pending_count": 2}],
                "learning_readiness_projection": {
                    "pending_learning_labels_needed_for_readiness": 20,
                    "next_learning_label_due_date": "2026-06-24",
                    "estimated_learning_ready_date": "2026-08-24",
                },
                "external_learning_readiness_projection": {
                    "pending_external_learning_label_count": 1,
                    "pending_external_fast_label_count": 0,
                    "projected_external_additional_needed_all_scheduled": 19,
                },
            }
            payload["audit"] = {
                "data_gaps": [
                    {
                        "area": "engine",
                        "label": "Learning reranker",
                        "status": "baseline_fallback",
                        "detail": "Next learning labels due 2026-06-24; estimated ready 2026-08-24.",
                    }
                ]
            }
            payload["instrumentation_audit"] = build_instrumentation_audit(payload)
            write_latest(web_dir, payload)

            failures = public_snapshot_quality_failures(web_dir)

            self.assertTrue(any("Learning reranker audit gap must include external coverage priority backfill" in failure for failure in failures))

    def test_requires_external_learning_projection_when_external_pending_labels_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            payload = valid_public_payload()
            payload["backtest"] = {
                "outcome_count": 2,
                "completed_outcome_count": 0,
                "pending_outcome_count": 2,
                "missing_price_count": 0,
                "outcomes": [
                    {
                        "status": "pending",
                        "symbol": "NVDA",
                        "horizon": "5d",
                        "due_date": "2026-06-02",
                        "external_feed_status": "limited",
                        "external_coverage_multiplier": 0.25,
                    },
                    {
                        "status": "pending",
                        "symbol": "NVDA",
                        "horizon": "1m",
                        "due_date": "2026-06-25",
                        "external_feed_status": "limited",
                        "external_coverage_multiplier": 0.25,
                    }
                ],
                "pending_by_external_feed_status": [{"key": "limited", "pending_count": 2}],
                "pending_by_external_coverage": [{"key": "thin_coverage", "pending_count": 2}],
            }
            payload["outcome_diagnostics"] = {
                "label_maturity": {"learning_ready": True},
                "pending_label_schedule": {"pending_label_count": 2},
                "horizon_label_counts": [{"horizon": "5d", "pending_count": 1}, {"horizon": "1m", "pending_count": 1}],
                "learning_readiness_projection": {"pending_learning_labels_needed_for_readiness": 0},
            }
            payload["instrumentation_audit"] = build_instrumentation_audit(payload)
            write_latest(web_dir, payload)

            failures = public_snapshot_quality_failures(web_dir)

            self.assertTrue(
                any(
                    "externally covered pending outcomes require outcome_diagnostics.external_learning_readiness_projection" in failure
                    for failure in failures
                )
            )
            self.assertTrue(any("external fast-check labels" in failure for failure in failures))

    def test_requires_external_coverage_gap_queue_when_external_learning_is_short(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            payload = valid_public_payload()
            payload["backtest"] = {
                "outcome_count": 2,
                "completed_outcome_count": 0,
                "pending_outcome_count": 2,
                "missing_price_count": 0,
                "outcomes": [
                    {
                        "status": "pending",
                        "symbol": "NVDA",
                        "horizon": "1m",
                        "due_date": "2026-06-25",
                        "trade_action": "add",
                        "direction": 1,
                        "external_alignment": "aligned",
                        "external_feed_status": "limited",
                        "external_coverage_multiplier": 0.25,
                        "coverage_adjusted_external_signal_score": 5.0,
                    },
                    {"status": "pending", "symbol": "AMD", "horizon": "1m", "due_date": "2026-06-25"},
                ],
                "pending_by_external_feed_status": [
                    {"key": "limited", "pending_count": 1},
                    {"key": "unknown", "pending_count": 1},
                ],
                "pending_by_external_coverage": [
                    {"key": "thin_coverage", "pending_count": 1},
                    {"key": "unknown", "pending_count": 1},
                ],
                "pending_by_external_alignment": [
                    {"key": "aligned", "pending_count": 1},
                    {"key": "unknown", "pending_count": 1},
                ],
                "pending_external_alignment_due_dates": [
                    {"due_date": "2026-06-25", "due_count": 1, "aligned_count": 1}
                ],
                "pending_external_alignment_watchlist": [
                    {
                        "status": "pending",
                        "symbol": "NVDA",
                        "horizon": "1m",
                        "due_date": "2026-06-25",
                        "external_alignment": "aligned",
                        "external_alignment_review_reason": "External signal reinforces the action direction.",
                    }
                ],
                "pending_external_coverage_gap_count": 1,
            }
            payload["outcome_diagnostics"] = {
                "label_maturity": {"learning_ready": True},
                "pending_label_schedule": {"pending_label_count": 2},
                "horizon_label_counts": [{"horizon": "1m", "pending_count": 2}],
                "learning_readiness_projection": {"pending_learning_labels_needed_for_readiness": 0},
                "external_learning_readiness_projection": {
                    "pending_external_learning_label_count": 1,
                    "pending_external_fast_label_count": 0,
                    "projected_external_additional_needed_all_scheduled": 19,
                },
            }
            payload["instrumentation_audit"] = build_instrumentation_audit(payload)
            write_latest(web_dir, payload)

            failures = public_snapshot_quality_failures(web_dir)

            self.assertTrue(
                any("external learning shortfall requires backtest.pending_external_coverage_gap_queue" in failure for failure in failures)
            )

    def test_requires_external_coverage_gap_plan_when_queue_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            payload = valid_public_payload()
            payload["backtest"] = {
                "outcome_count": 2,
                "completed_outcome_count": 0,
                "pending_outcome_count": 2,
                "missing_price_count": 0,
                "outcomes": [
                    {
                        "status": "pending",
                        "symbol": "NVDA",
                        "horizon": "1m",
                        "due_date": "2026-06-25",
                        "trade_action": "add",
                        "direction": 1,
                        "external_alignment": "aligned",
                        "external_feed_status": "limited",
                        "external_coverage_multiplier": 0.25,
                        "coverage_adjusted_external_signal_score": 5.0,
                    },
                    {"status": "pending", "symbol": "AMD", "horizon": "1m", "due_date": "2026-06-25"},
                ],
                "pending_by_external_feed_status": [
                    {"key": "limited", "pending_count": 1},
                    {"key": "unknown", "pending_count": 1},
                ],
                "pending_by_external_coverage": [
                    {"key": "thin_coverage", "pending_count": 1},
                    {"key": "unknown", "pending_count": 1},
                ],
                "pending_by_external_alignment": [
                    {"key": "aligned", "pending_count": 1},
                    {"key": "unknown", "pending_count": 1},
                ],
                "pending_external_alignment_due_dates": [
                    {"due_date": "2026-06-25", "due_count": 1, "aligned_count": 1}
                ],
                "pending_external_alignment_watchlist": [
                    {
                        "status": "pending",
                        "symbol": "NVDA",
                        "horizon": "1m",
                        "due_date": "2026-06-25",
                        "external_alignment": "aligned",
                        "external_alignment_review_reason": "External signal reinforces the action direction.",
                    }
                ],
                "pending_external_coverage_gap_count": 1,
                "pending_external_coverage_gap_queue": [
                    {
                        "status": "pending",
                        "external_coverage_gap_id": "gap-amd-1m",
                        "symbol": "AMD",
                        "horizon": "1m",
                        "due_date": "2026-06-25",
                        "external_coverage_gap_reason": "AMD 1m has no external observation attached.",
                    }
                ],
            }
            payload["outcome_diagnostics"] = {
                "label_maturity": {"learning_ready": True},
                "pending_label_schedule": {"pending_label_count": 2},
                "horizon_label_counts": [{"horizon": "1m", "pending_count": 2}],
                "learning_readiness_projection": {"pending_learning_labels_needed_for_readiness": 0},
                "external_learning_readiness_projection": {
                    "pending_external_learning_label_count": 1,
                    "pending_external_fast_label_count": 0,
                    "projected_external_additional_needed_all_scheduled": 19,
                },
            }
            payload["instrumentation_audit"] = build_instrumentation_audit(payload)
            write_latest(web_dir, payload)

            failures = public_snapshot_quality_failures(web_dir)

            self.assertTrue(
                any("external learning shortfall requires backtest.pending_external_coverage_gap_plan" in failure for failure in failures)
            )

    def test_requires_external_coverage_gap_plan_backfill_instructions(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            payload = valid_public_payload()
            payload["backtest"] = {
                "outcome_count": 2,
                "completed_outcome_count": 0,
                "pending_outcome_count": 2,
                "missing_price_count": 0,
                "outcomes": [
                    {
                        "status": "pending",
                        "symbol": "NVDA",
                        "horizon": "1m",
                        "due_date": "2026-06-25",
                        "external_feed_status": "limited",
                        "external_coverage_multiplier": 0.25,
                        "coverage_adjusted_external_signal_score": 5.0,
                        "external_alignment": "aligned",
                    },
                    {"status": "pending", "symbol": "AMD", "horizon": "1m", "as_of": "2026-05-24", "due_date": "2026-06-25"},
                ],
                "pending_by_external_feed_status": [{"key": "limited", "pending_count": 1}, {"key": "unknown", "pending_count": 1}],
                "pending_by_external_coverage": [{"key": "thin_coverage", "pending_count": 1}, {"key": "unknown", "pending_count": 1}],
                "pending_by_external_alignment": [{"key": "aligned", "pending_count": 1}, {"key": "unknown", "pending_count": 1}],
                "pending_external_alignment_due_dates": [{"due_date": "2026-06-25", "due_count": 1, "aligned_count": 1}],
                "pending_external_alignment_watchlist": [
                    {
                        "status": "pending",
                        "symbol": "NVDA",
                        "horizon": "1m",
                        "due_date": "2026-06-25",
                        "external_alignment": "aligned",
                        "external_alignment_review_reason": "External signal reinforces the action direction.",
                    }
                ],
                "pending_external_coverage_gap_count": 1,
                "pending_external_coverage_gap_queue": [
                    {
                        "status": "pending",
                        "external_coverage_gap_id": "gap-amd-1m",
                        "symbol": "AMD",
                        "horizon": "1m",
                        "due_date": "2026-06-25",
                        "external_coverage_gap_reason": "AMD 1m has no external observation attached.",
                    }
                ],
                "pending_external_coverage_gap_plan": {
                    "additional_external_coverage_needed": 19,
                    "candidate_gap_count": 1,
                    "minimum_external_long_horizon_required": 20,
                    "observed_external_long_horizon_label_count": 1,
                    "priority_gap_count": 1,
                    "projected_external_long_horizon_count_after_priority_backfill": 2,
                    "projected_external_additional_needed_after_priority_backfill": 18,
                    "priority_rows": [
                        {
                            "status": "pending",
                            "external_coverage_gap_id": "gap-amd-1m",
                            "symbol": "AMD",
                            "horizon": "1m",
                            "due_date": "2026-06-25",
                            "external_coverage_gap_reason": "AMD 1m has no external observation attached.",
                        }
                    ],
                },
            }
            payload["outcome_diagnostics"] = {
                "label_maturity": {"learning_ready": True},
                "pending_label_schedule": {"pending_label_count": 2},
                "horizon_label_counts": [{"horizon": "1m", "pending_count": 2}],
                "learning_readiness_projection": {"pending_learning_labels_needed_for_readiness": 0},
                "external_learning_readiness_projection": {
                    "pending_external_learning_label_count": 1,
                    "pending_external_fast_label_count": 0,
                    "projected_external_additional_needed_all_scheduled": 19,
                },
            }
            payload["instrumentation_audit"] = build_instrumentation_audit(payload)
            write_latest(web_dir, payload)

            failures = public_snapshot_quality_failures(web_dir)

            self.assertTrue(
                any("pending external coverage gap plan requires decision-time backfill instructions" in failure for failure in failures)
            )
            priority = payload["backtest"]["pending_external_coverage_gap_plan"]["priority_rows"][0]
            priority.update(
                {
                    "external_coverage_gap_action": "Rebuild AMD from decision-time inputs.",
                    "external_coverage_backfill_policy": "decision_time_only",
                    "required_external_observation_date": "2026-05-24",
                }
            )
            write_latest(web_dir, payload)

            failures = public_snapshot_quality_failures(web_dir)

            self.assertTrue(
                any("pending external coverage gap plan requires acceptance checks" in failure for failure in failures)
            )
            priority["external_coverage_acceptance_checks"] = [
                {"check": "external_feed_status_present", "status": "pending"},
                {"check": "external_coverage_multiplier_present", "status": "pending"},
                {"check": "coverage_adjusted_external_signal_score_present", "status": "pending"},
                {"check": "decision_time_only", "status": "pending"},
            ]
            payload["backtest"]["pending_external_coverage_gap_plan"]["priority_acceptance_check_count"] = 3
            payload["backtest"]["pending_external_coverage_gap_plan"]["priority_open_acceptance_check_count"] = 4
            payload["backtest"]["pending_external_coverage_gap_plan"]["priority_acceptance_check_status_counts"] = {"pending": 4}
            write_latest(web_dir, payload)

            failures = public_snapshot_quality_failures(web_dir)

            self.assertTrue(
                any("backtest.pending_external_coverage_gap_plan acceptance check count is 3; expected 4" in failure for failure in failures)
            )

    def test_requires_weak_data_health_sources_to_match_audit_surfaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            payload = valid_public_payload()
            payload["data_health"] = {
                "sources": [
                    {
                        "source": "earnings",
                        "label": "Earnings calendar",
                        "status": "estimated",
                        "detail": "0 confirmed, 13 estimated.",
                    }
                ]
            }
            payload["audit"] = {
                "source_freshness": [
                    {
                        "source": "earnings",
                        "label": "Earnings calendar",
                        "status": "ok",
                        "detail": "stale audit row",
                    }
                ],
                "data_gaps": [],
            }
            payload["instrumentation_audit"] = build_instrumentation_audit(payload)
            write_latest(web_dir, payload)

            failures = public_snapshot_quality_failures(web_dir)

            self.assertTrue(any("audit.source_freshness status mismatch for Earnings calendar" in failure for failure in failures))
            self.assertTrue(any("weak data_health source Earnings calendar requires matching audit source gap" in failure for failure in failures))

    def test_requires_pending_backtest_due_dates_to_match_trading_calendar_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            payload = valid_public_payload()
            payload["backtest"] = {
                "outcome_count": 1,
                "completed_outcome_count": 0,
                "pending_outcome_count": 1,
                "missing_price_count": 0,
                "due_date_policy_version": BACKTEST_VERSION,
                "outcomes": [
                    {
                        "status": "pending",
                        "symbol": "NVDA",
                        "horizon": "5d",
                        "as_of": "2026-05-23",
                        "due_date": "2026-05-30",
                    }
                ],
            }
            payload["outcome_diagnostics"] = {
                "label_maturity": {"learning_ready": True},
                "pending_label_schedule": {"pending_label_count": 1},
                "learning_readiness_projection": {"pending_learning_labels_needed_for_readiness": 0},
            }
            payload["instrumentation_audit"] = build_instrumentation_audit(payload)
            write_latest(web_dir, payload)

            failures = public_snapshot_quality_failures(web_dir)

            self.assertTrue(any("stale pending backtest due dates" in failure for failure in failures))
            self.assertTrue(any("outcomes NVDA 5d due 2026-05-30 expected 2026-06-02" in failure for failure in failures))

    def test_requires_recent_pending_due_dates_to_match_trading_calendar_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            payload = valid_public_payload()
            payload["backtest"] = {
                "outcome_count": 1,
                "completed_outcome_count": 0,
                "pending_outcome_count": 1,
                "missing_price_count": 0,
                "due_date_policy_version": BACKTEST_VERSION,
                "outcomes": [
                    {
                        "status": "pending",
                        "symbol": "NVDA",
                        "horizon": "5d",
                        "as_of": "2026-05-23",
                        "due_date": "2026-06-02",
                    }
                ],
                "recent_pending": [
                    {
                        "status": "pending",
                        "symbol": "NVDA",
                        "horizon": "5d",
                        "as_of": "2026-05-23",
                        "due_date": "2026-05-30",
                    }
                ],
            }
            payload["outcome_diagnostics"] = {
                "label_maturity": {"learning_ready": True},
                "pending_label_schedule": {"pending_label_count": 1},
                "horizon_label_counts": [{"horizon": "5d", "pending_count": 1}],
                "learning_readiness_projection": {"pending_learning_labels_needed_for_readiness": 0},
            }
            payload["instrumentation_audit"] = build_instrumentation_audit(payload)
            write_latest(web_dir, payload)

            failures = public_snapshot_quality_failures(web_dir)

            self.assertTrue(any("recent_pending NVDA 5d due 2026-05-30 expected 2026-06-02" in failure for failure in failures))

    def test_requires_pending_backtest_due_date_policy_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_dir = Path(tmp) / "web"
            payload = valid_public_payload()
            payload["backtest"] = {
                "outcome_count": 1,
                "completed_outcome_count": 0,
                "pending_outcome_count": 1,
                "missing_price_count": 0,
                "outcomes": [
                    {
                        "status": "pending",
                        "symbol": "NVDA",
                        "horizon": "5d",
                        "as_of": "2026-05-23",
                        "due_date": "2026-06-02",
                    }
                ],
            }
            payload["outcome_diagnostics"] = {
                "label_maturity": {"learning_ready": True},
                "pending_label_schedule": {"pending_label_count": 1},
                "learning_readiness_projection": {"pending_learning_labels_needed_for_readiness": 0},
            }
            payload["instrumentation_audit"] = build_instrumentation_audit(payload)
            write_latest(web_dir, payload)

            failures = public_snapshot_quality_failures(web_dir)

            self.assertTrue(any(f"due_date_policy_version {BACKTEST_VERSION}" in failure for failure in failures))


def valid_public_payload() -> dict:
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
    return {
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


def write_latest(web_dir: Path, payload: dict) -> None:
    data_dir = web_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "latest.json").write_text(json.dumps(payload), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
