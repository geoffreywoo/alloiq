from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from invest.backtest import (
    build_backtest_summary,
    estimated_label_due_date,
    outcome_history_from_backtest,
    pending_external_coverage_gap_count,
    pending_external_coverage_gap_plan,
    pending_external_coverage_gap_queue,
    pending_external_alignment_review_acceptance_summary,
    pending_external_alignment_review_due_dates,
    pending_external_alignment_review_queue,
    pending_external_alignment_measurement_gap_plan,
    pending_external_alignment_measurement_gap_queue,
)


def price_history(start: date, prices: list[float]) -> list[dict]:
    return [
        {"date": start + timedelta(days=index), "close": price}
        for index, price in enumerate(prices)
    ]


class BacktestTests(unittest.TestCase):
    def test_label_due_date_uses_trading_calendar_after_weekend_and_holiday(self):
        self.assertEqual(estimated_label_due_date(date(2026, 1, 2), "5d"), date(2026, 1, 9))
        self.assertEqual(estimated_label_due_date(date(2026, 5, 23), "5d"), date(2026, 6, 2))

    def test_external_coverage_gap_queue_dedupes_work_items(self):
        rows = [
            {
                "status": "pending",
                "symbol": "AMD",
                "horizon": "1m",
                "due_date": "2026-06-25",
                "trade_action": "trim",
                "bucket": "semis_networking_hbm",
            },
            {
                "status": "pending",
                "symbol": "AMD",
                "horizon": "1m",
                "due_date": "2026-06-25",
                "trade_action": "trim",
                "bucket": "semis_networking_hbm",
            },
            {
                "status": "pending",
                "symbol": "ASML",
                "horizon": "1m",
                "due_date": "2026-06-25",
                "trade_action": "risk_review",
                "bucket": "semis_networking_hbm",
            },
        ]

        self.assertEqual(pending_external_coverage_gap_count(rows), 2)
        queue = pending_external_coverage_gap_queue(rows)
        self.assertEqual([row["symbol"] for row in queue], ["AMD", "ASML"])
        self.assertTrue(queue[0]["external_coverage_gap_id"])
        self.assertNotEqual(queue[0]["external_coverage_gap_id"], queue[1]["external_coverage_gap_id"])
        self.assertEqual(queue[0]["external_coverage_backfill_policy"], "decision_time_only")
        self.assertIn("do not use later", queue[0]["external_coverage_gap_action"])
        self.assertEqual(queue[0]["minimum_external_fields_to_backfill"][0], "external_feed_status")
        self.assertEqual(
            {row["check"] for row in queue[0]["external_coverage_acceptance_checks"]},
            {
                "external_feed_status_present",
                "external_coverage_multiplier_present",
                "coverage_adjusted_external_signal_score_present",
                "decision_time_only",
            },
        )
        plan = pending_external_coverage_gap_plan(rows)
        self.assertEqual(plan["candidate_gap_count"], 2)
        self.assertEqual(plan["priority_gap_count"], 2)
        self.assertEqual(plan["priority_acceptance_check_count"], 8)
        self.assertEqual(plan["priority_open_acceptance_check_count"], 8)
        self.assertEqual(plan["priority_acceptance_check_status_counts"], {"pending": 8})
        self.assertEqual(plan["additional_external_coverage_needed"], 20)
        self.assertEqual(plan["priority_symbols"], ["AMD", "ASML"])
        self.assertEqual(plan["residual_gap_count"], 0)
        self.assertEqual(plan["residual_gap_status"], "none")
        self.assertEqual(plan["residual_rows"], [])
        self.assertEqual(plan["projected_external_long_horizon_count_after_priority_backfill"], 2)
        self.assertEqual(plan["projected_external_additional_needed_after_priority_backfill"], 18)
        self.assertFalse(plan["external_learning_ready_after_priority_backfill"])

    def test_external_alignment_review_queue_prioritizes_non_confirming_labels(self):
        rows = [
            {
                "status": "pending",
                "symbol": "ALIGNED",
                "horizon": "1m",
                "due_date": "2026-06-25",
                "trade_action": "add",
                "recommended_delta_weight": 0.02,
                "risk_adjusted_expected_return": 30,
                "coverage_adjusted_external_signal_score": 6.0,
            },
            {
                "status": "pending",
                "symbol": "CONFLICT",
                "horizon": "1m",
                "due_date": "2026-06-25",
                "outcome_id": "outcome-conflict-high",
                "trial_id": "trial-conflict-high",
                "session": "postmarket",
                "trade_action": "trim",
                "recommended_delta_weight": -0.01,
                "risk_adjusted_expected_return": 35,
                "coverage_adjusted_external_signal_score": 7.0,
            },
            {
                "status": "pending",
                "symbol": "CONFLICT",
                "horizon": "1m",
                "due_date": "2026-06-25",
                "trade_action": "trim",
                "recommended_delta_weight": -0.01,
                "risk_adjusted_expected_return": 20,
                "coverage_adjusted_external_signal_score": 6.0,
            },
            {
                "status": "pending",
                "symbol": "ENGINE",
                "horizon": "1m",
                "due_date": "2026-06-25",
                "trade_action": "watch",
                "recommended_delta_weight": 0,
                "risk_adjusted_expected_return": 10,
                "coverage_adjusted_external_signal_score": 5.0,
            },
            {
                "status": "pending",
                "symbol": "INTERNAL",
                "horizon": "1m",
                "due_date": "2026-06-25",
                "trade_action": "add",
                "recommended_delta_weight": 0.01,
                "risk_adjusted_expected_return": 20,
                "coverage_adjusted_external_signal_score": 0.2,
            },
            {
                "status": "pending",
                "symbol": "UNKNOWN",
                "horizon": "1m",
                "due_date": "2026-06-25",
                "trade_action": "add",
                "recommended_delta_weight": 0.01,
            },
        ]

        queue = pending_external_alignment_review_queue(rows)

        self.assertEqual([row["symbol"] for row in queue], ["CONFLICT", "ENGINE", "INTERNAL"])
        self.assertEqual(queue[0]["external_alignment"], "conflict")
        self.assertTrue(queue[0]["external_alignment_review_id"])
        self.assertNotEqual(queue[0]["external_alignment_review_id"], queue[1]["external_alignment_review_id"])
        self.assertEqual(queue[0]["external_alignment_review_version"], "2026-05-external-alignment-review-v1")
        self.assertEqual(queue[0]["external_alignment_review_label_count"], 2)
        self.assertEqual(queue[0]["external_alignment_review_focus"], "external_disagreement")
        self.assertIn("external disagreement", queue[0]["external_alignment_review_priority_reason"])
        self.assertIn("abs expected return 35", queue[0]["external_alignment_review_priority_reason"])
        self.assertIn("compare realized direction", queue[0]["external_alignment_review_learning_action"])
        self.assertEqual(queue[0]["external_alignment_review_measurement_plan"]["engine_direction"], "negative")
        self.assertEqual(queue[0]["external_alignment_review_measurement_plan"]["external_signal_direction"], "positive")
        self.assertEqual(queue[0]["external_alignment_review_measurement_plan"]["risk_adjusted_expected_return"], 35.0)
        self.assertEqual(queue[0]["external_alignment_review_measurement_plan"]["coverage_adjusted_external_signal_score"], 7.0)
        self.assertEqual(queue[0]["external_alignment_review_measurement_plan"]["missing_measurement_fields"], [])
        self.assertIn("engine negative", queue[0]["external_alignment_review_measurement_plan"]["summary"])
        self.assertEqual(queue[0]["external_alignment_review_open_check_count"], 1)
        self.assertEqual(
            [check["check"] for check in queue[0]["external_alignment_review_acceptance_checks"]],
            [
                "source_trace_present",
                "review_focus_present",
                "learning_action_present",
                "matured_label_available",
            ],
        )
        self.assertEqual(
            [check["status"] for check in queue[0]["external_alignment_review_acceptance_checks"]],
            ["passed", "passed", "passed", "pending"],
        )
        self.assertEqual(queue[0]["source_outcome_id"], "outcome-conflict-high")
        self.assertEqual(queue[0]["source_trial_id"], "trial-conflict-high")
        self.assertEqual(queue[0]["session"], "postmarket")
        self.assertGreater(queue[0]["external_alignment_review_priority"], queue[1]["external_alignment_review_priority"])
        self.assertEqual(queue[1]["external_alignment_review_focus"], "missed_external_signal")
        self.assertEqual(queue[2]["external_alignment_review_focus"], "internal_signal_only")
        due_dates = pending_external_alignment_review_due_dates(rows)
        self.assertEqual(due_dates[0]["due_date"], "2026-06-25")
        self.assertEqual(due_dates[0]["label_count"], 4)
        self.assertEqual(due_dates[0]["work_item_count"], 3)
        self.assertEqual(due_dates[0]["focus_counts"]["external_disagreement"], {"label_count": 2, "work_item_count": 1})
        self.assertEqual(due_dates[0]["focus_counts"]["missed_external_signal"], {"label_count": 1, "work_item_count": 1})
        self.assertEqual(due_dates[0]["focus_counts"]["internal_signal_only"], {"label_count": 1, "work_item_count": 1})
        acceptance = pending_external_alignment_review_acceptance_summary(rows)
        conflict_action = "When the label matures, compare realized direction with the external signal before changing external-signal trust."
        missed_action = "When the label matures, test whether the external signal should have promoted a directional size or timing change."
        internal_action = "When the label matures, test whether internal signal families carried the return without external confirmation."
        self.assertEqual(acceptance["label_count"], 4)
        self.assertEqual(acceptance["work_item_count"], 3)
        self.assertEqual(acceptance["check_count"], 12)
        self.assertEqual(acceptance["open_check_count"], 5)
        self.assertEqual(acceptance["open_label_count"], 4)
        self.assertEqual(acceptance["open_check_counts"], {"matured_label_available": 3, "source_trace_present": 2})
        self.assertEqual(acceptance["metadata_ready_work_item_count"], 1)
        self.assertEqual(acceptance["next_open_check_due_date"], "2026-06-25")
        self.assertEqual(acceptance["next_open_check_due_open_check_count"], 5)
        self.assertEqual(acceptance["next_open_check_due_label_count"], 4)
        self.assertEqual(acceptance["next_open_check_due_work_item_count"], 3)
        self.assertEqual(acceptance["next_open_check_due_visible_work_item_count"], 3)
        self.assertEqual(acceptance["next_open_check_due_hidden_work_item_count"], 0)
        self.assertTrue(acceptance["next_open_check_due_fully_visible"])
        self.assertEqual(acceptance["next_open_check_due_symbols"], ["CONFLICT", "ENGINE", "INTERNAL"])
        self.assertEqual(acceptance["next_open_check_due_horizons"], ["1m"])
        self.assertEqual(
            acceptance["next_open_check_due_focus_counts"],
            {
                "external_disagreement": {"label_count": 2, "work_item_count": 1},
                "internal_signal_only": {"label_count": 1, "work_item_count": 1},
                "missed_external_signal": {"label_count": 1, "work_item_count": 1},
            },
        )
        self.assertEqual(
            acceptance["next_open_check_due_learning_action_counts"],
            {
                conflict_action: {"label_count": 2, "work_item_count": 1},
                internal_action: {"label_count": 1, "work_item_count": 1},
                missed_action: {"label_count": 1, "work_item_count": 1},
            },
        )
        self.assertEqual(acceptance["next_open_check_due_measurement_missing_field_counts"], {})
        self.assertEqual(
            acceptance["open_check_due_dates"],
            [
                {
                    "due_date": "2026-06-25",
                    "open_check_count": 5,
                    "label_count": 4,
                    "work_item_count": 3,
                    "symbols": ["CONFLICT", "ENGINE", "INTERNAL"],
                    "horizons": ["1m"],
                    "focus_counts": {
                        "external_disagreement": {"label_count": 2, "work_item_count": 1},
                        "internal_signal_only": {"label_count": 1, "work_item_count": 1},
                        "missed_external_signal": {"label_count": 1, "work_item_count": 1},
                    },
                    "learning_action_counts": {
                        conflict_action: {"label_count": 2, "work_item_count": 1},
                        internal_action: {"label_count": 1, "work_item_count": 1},
                        missed_action: {"label_count": 1, "work_item_count": 1},
                    },
                    "measurement_missing_field_counts": {},
                    "check_counts": {"matured_label_available": 3, "source_trace_present": 2},
                }
            ],
        )

    def test_backtest_summary_exposes_hidden_alignment_review_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            payload = {
                "as_of": "2026-05-23",
                "session": "postmarket",
                "recommendation_training_examples": [
                    {
                        "example_id": f"trim-{index}",
                        "as_of": "2026-05-23",
                        "session": "postmarket",
                        "symbol": f"T{index}",
                        "bucket": "test",
                        "trade_action": "trim",
                        "recommended_delta_weight": -0.01,
                        "coverage_adjusted_external_signal_score": 5.0,
                        "external_feed_status": "limited",
                    }
                    for index in range(13)
                ],
            }
            (reports / "2026-05-23-postmarket.json").write_text(json.dumps(payload), encoding="utf-8")

            summary = build_backtest_summary(reports, as_of=date(2026, 5, 24), price_history={})

        self.assertEqual(summary["pending_external_alignment_review_count"], 65)
        self.assertEqual(summary["pending_external_alignment_review_item_count"], 65)
        self.assertEqual(summary["pending_external_alignment_review_queue_limit"], 12)
        self.assertEqual(len(summary["pending_external_alignment_review_queue"]), 13)
        self.assertEqual(summary["pending_external_alignment_review_hidden_item_count"], 52)
        self.assertEqual(summary["pending_external_alignment_review_due_dates"][0]["label_count"], 13)
        self.assertEqual(summary["pending_external_alignment_review_due_dates"][0]["work_item_count"], 13)
        self.assertEqual(summary["pending_external_alignment_review_acceptance_summary"]["work_item_count"], 65)
        self.assertEqual(summary["pending_external_alignment_review_acceptance_summary"]["open_label_count"], 65)
        self.assertEqual(summary["pending_external_alignment_review_acceptance_summary"]["open_check_counts"], {"matured_label_available": 65})
        self.assertEqual(summary["pending_external_alignment_review_acceptance_summary"]["next_open_check_due_date"], "2026-06-02")
        self.assertEqual(summary["pending_external_alignment_review_acceptance_summary"]["next_open_check_due_label_count"], 13)
        self.assertEqual(summary["pending_external_alignment_review_acceptance_summary"]["next_open_check_due_work_item_count"], 13)
        self.assertEqual(summary["pending_external_alignment_review_acceptance_summary"]["next_open_check_due_visible_work_item_count"], 13)
        self.assertEqual(summary["pending_external_alignment_review_acceptance_summary"]["next_open_check_due_hidden_work_item_count"], 0)
        self.assertTrue(summary["pending_external_alignment_review_acceptance_summary"]["next_open_check_due_fully_visible"])
        self.assertEqual(summary["pending_external_alignment_review_acceptance_summary"]["next_open_check_due_horizons"], ["5d"])
        self.assertIn("T12", summary["pending_external_alignment_review_acceptance_summary"]["next_open_check_due_symbols"])
        self.assertEqual(
            summary["pending_external_alignment_review_acceptance_summary"]["next_open_check_due_focus_counts"],
            {"external_disagreement": {"label_count": 13, "work_item_count": 13}},
        )
        self.assertEqual(
            summary["pending_external_alignment_review_acceptance_summary"]["next_open_check_due_learning_action_counts"],
            {
                "When the label matures, compare realized direction with the external signal before changing external-signal trust.": {
                    "label_count": 13,
                    "work_item_count": 13,
                }
            },
        )
        self.assertEqual(
            summary["pending_external_alignment_review_acceptance_summary"]["next_open_check_due_measurement_missing_field_counts"],
            {"risk_adjusted_expected_return": {"label_count": 13, "work_item_count": 13}},
        )
        self.assertEqual(summary["pending_external_alignment_review_acceptance_summary"]["open_check_due_dates"][0]["open_check_count"], 13)
        self.assertEqual(summary["pending_external_alignment_review_acceptance_summary"]["open_check_due_dates"][0]["label_count"], 13)
        self.assertEqual(summary["pending_external_alignment_review_acceptance_summary"]["open_check_due_dates"][0]["horizons"], ["5d"])
        self.assertEqual(
            summary["pending_external_alignment_review_acceptance_summary"]["open_check_due_dates"][0]["focus_counts"],
            {"external_disagreement": {"label_count": 13, "work_item_count": 13}},
        )
        self.assertEqual(
            summary["pending_external_alignment_review_acceptance_summary"]["open_check_due_dates"][0]["learning_action_counts"],
            {
                "When the label matures, compare realized direction with the external signal before changing external-signal trust.": {
                    "label_count": 13,
                    "work_item_count": 13,
                }
            },
        )
        self.assertEqual(
            summary["pending_external_alignment_review_acceptance_summary"]["open_check_due_dates"][0]["measurement_missing_field_counts"],
            {"risk_adjusted_expected_return": {"label_count": 13, "work_item_count": 13}},
        )
        self.assertEqual(summary["pending_external_alignment_measurement_gap_label_count"], 65)
        self.assertEqual(summary["pending_external_alignment_measurement_gap_item_count"], 65)
        self.assertEqual(summary["pending_external_alignment_measurement_gap_hidden_item_count"], 52)
        self.assertEqual(len(summary["pending_external_alignment_measurement_gap_queue"]), 13)
        self.assertEqual(summary["pending_external_alignment_measurement_gap_queue"][0]["external_alignment_measurement_missing_fields"], ["risk_adjusted_expected_return"])
        self.assertEqual(summary["pending_external_alignment_measurement_gap_queue"][0]["external_alignment_measurement_missing_label_count"], 1)
        self.assertEqual(summary["pending_external_alignment_measurement_gap_queue"][0]["external_alignment_measurement_backfill_policy"], "decision_time_only")
        self.assertIn("do not use later", summary["pending_external_alignment_measurement_gap_queue"][0]["external_alignment_measurement_gap_action"])
        self.assertEqual(
            [check["check"] for check in summary["pending_external_alignment_measurement_gap_queue"][0]["external_alignment_measurement_acceptance_checks"]],
            ["risk_adjusted_expected_return_present", "decision_time_only"],
        )
        self.assertEqual(summary["pending_external_alignment_measurement_gap_plan"]["label_count"], 65)
        self.assertEqual(summary["pending_external_alignment_measurement_gap_plan"]["work_item_count"], 65)
        self.assertEqual(summary["pending_external_alignment_measurement_gap_plan"]["next_due_date"], "2026-06-02")
        self.assertEqual(
            summary["pending_external_alignment_measurement_gap_plan"]["next_due_field_counts"],
            {"risk_adjusted_expected_return": {"label_count": 13, "work_item_count": 13}},
        )
        self.assertEqual(summary["pending_external_alignment_measurement_gap_plan"]["priority_open_acceptance_check_count"], 26)
        self.assertEqual(
            summary["pending_external_alignment_review_due_dates"][0]["focus_counts"]["external_disagreement"],
            {"label_count": 13, "work_item_count": 13},
        )

    def test_external_coverage_gap_plan_ranks_non_blocking_residual_backlog(self):
        observed = [
            {
                "status": "pending",
                "symbol": f"OBS{index}",
                "horizon": "1m",
                "due_date": "2026-06-25",
                "external_feed_status": "limited",
            }
            for index in range(20)
        ]
        rows = observed + [
            {
                "status": "pending",
                "symbol": "LOW",
                "horizon": "3m",
                "as_of": "2026-05-23",
                "due_date": "2026-08-24",
                "trade_action": "study",
                "risk_adjusted_expected_return": 1,
                "recommended_delta_weight": 0,
                "bucket": "research",
            },
            {
                "status": "pending",
                "symbol": "ALPHA",
                "horizon": "1m",
                "as_of": "2026-05-23",
                "due_date": "2026-06-25",
                "trade_action": "add",
                "risk_adjusted_expected_return": 40,
                "recommended_delta_weight": 0.03,
                "bucket": "semis_networking_hbm",
            },
        ]

        plan = pending_external_coverage_gap_plan(rows)

        self.assertEqual(plan["additional_external_coverage_needed"], 0)
        self.assertEqual(plan["priority_gap_count"], 0)
        self.assertEqual(plan["residual_gap_count"], 2)
        self.assertEqual(plan["residual_gap_status"], "non_blocking_learning_backlog")
        self.assertEqual(plan["residual_ranking_version"], "2026-05-external-coverage-residual-ranking-v1")
        self.assertEqual(plan["residual_hidden_gap_count"], 0)
        self.assertEqual(
            plan["residual_required_observation_dates"],
            [
                {
                    "required_external_observation_date": "2026-05-23",
                    "gap_count": 2,
                    "source_trial_count": 0,
                    "symbol_count": 2,
                    "symbols": ["ALPHA", "LOW"],
                    "earliest_due_date": "2026-06-25",
                    "latest_due_date": "2026-08-24",
                }
            ],
        )
        self.assertEqual([row["symbol"] for row in plan["residual_rows"]], ["ALPHA", "LOW"])
        self.assertGreater(plan["residual_rows"][0]["residual_learning_value_score"], plan["residual_rows"][1]["residual_learning_value_score"])
        self.assertEqual(plan["residual_rows"][0]["residual_backfill_status"], "non_blocking")
        self.assertIn("expected_return=40", plan["residual_rows"][0]["residual_learning_value_reason"])

    def test_external_coverage_gap_plan_summarizes_hidden_residual_required_dates(self):
        observed = [
            {
                "status": "pending",
                "symbol": f"OBS{index}",
                "horizon": "1m",
                "due_date": "2026-06-25",
                "external_feed_status": "limited",
            }
            for index in range(20)
        ]
        residuals = [
            {
                "status": "pending",
                "symbol": f"D23_{index}",
                "horizon": "1m",
                "as_of": "2026-05-23",
                "due_date": "2026-06-25",
                "trade_action": "watch",
                "risk_adjusted_expected_return": 5 + index,
                "recommended_delta_weight": 0,
                "bucket": "research",
                "trial_id": f"trial-d23-{index}",
            }
            for index in range(6)
        ] + [
            {
                "status": "pending",
                "symbol": f"D24_{index}",
                "horizon": "1m",
                "as_of": "2026-05-24",
                "due_date": "2026-06-26",
                "trade_action": "watch",
                "risk_adjusted_expected_return": 1 + index,
                "recommended_delta_weight": 0,
                "bucket": "research",
                "trial_id": f"trial-d24-{index}",
            }
            for index in range(4)
        ]

        plan = pending_external_coverage_gap_plan(observed + residuals)

        self.assertEqual(plan["residual_gap_count"], 10)
        self.assertEqual(len(plan["residual_rows"]), 8)
        self.assertEqual(plan["residual_hidden_gap_count"], 2)
        self.assertEqual(
            plan["residual_required_observation_dates"],
            [
                {
                    "required_external_observation_date": "2026-05-23",
                    "gap_count": 6,
                    "source_trial_count": 6,
                    "symbol_count": 6,
                    "symbols": ["D23_0", "D23_1", "D23_2", "D23_3", "D23_4", "D23_5"],
                    "earliest_due_date": "2026-06-25",
                    "latest_due_date": "2026-06-25",
                },
                {
                    "required_external_observation_date": "2026-05-24",
                    "gap_count": 4,
                    "source_trial_count": 4,
                    "symbol_count": 4,
                    "symbols": ["D24_0", "D24_1", "D24_2", "D24_3"],
                    "earliest_due_date": "2026-06-26",
                    "latest_due_date": "2026-06-26",
                },
            ],
        )

    def test_labels_adds_and_trims_by_forward_return_direction(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            payload = {
                "as_of": "2026-01-02",
                "session": "premarket",
                "recommendation_training_examples": [
                    {
                        "example_id": "add-nvda",
                        "as_of": "2026-01-02",
                        "session": "premarket",
                        "symbol": "NVDA",
                        "bucket": "semis_networking_hbm",
                        "trade_action": "add",
                        "recommended_delta_weight": 0.02,
                        "target_weight": 0.12,
                        "risk_adjusted_expected_return": 25,
                        "evidence_quality": 90,
                        "signal_families": ["manager", "catalyst"],
                        "external_signal_score": 20.0,
                        "coverage_adjusted_external_signal_score": 5.0,
                        "external_coverage_multiplier": 0.25,
                        "external_feed_status": "limited",
                        "external_provider_count": 6,
                        "external_provider_ok_count": 1,
                        "external_provider_ok_ratio": 0.1667,
                        "external_signal_count": 4,
                        "external_source_count": 3,
                    },
                    {
                        "example_id": "trim-amd",
                        "as_of": "2026-01-02",
                        "session": "premarket",
                        "symbol": "AMD",
                        "bucket": "semis_networking_hbm",
                        "trade_action": "trim",
                        "recommended_delta_weight": -0.01,
                        "target_weight": 0.02,
                        "risk_adjusted_expected_return": 12,
                        "evidence_quality": 60,
                        "signal_families": ["price_action"],
                    },
                ],
            }
            (reports / "2026-01-02-premarket.json").write_text(json.dumps(payload), encoding="utf-8")

            summary = build_backtest_summary(
                reports,
                as_of=date(2026, 2, 10),
                price_history={
                    "NVDA": price_history(date(2026, 1, 2), [100 + index for index in range(80)]),
                    "AMD": price_history(date(2026, 1, 2), [100 - index for index in range(80)]),
                },
            )

        self.assertEqual(summary["trial_count"], 2)
        self.assertEqual(summary["completed_outcome_count"], 4)
        self.assertEqual(summary["pending_outcome_count"], 6)
        five_day = next(row for row in summary["horizons"] if row["horizon"] == "5d")
        self.assertEqual(five_day["hit_rate"], 1.0)
        one_month = next(row for row in summary["horizons"] if row["horizon"] == "1m")
        self.assertEqual(one_month["hit_rate"], 1.0)
        self.assertGreater(one_month["average_decision_return"], 0)
        self.assertEqual(summary["calibration"]["mean_error"], -5.5)
        self.assertEqual(summary["calibration"]["mean_absolute_error"], 10.0)
        self.assertEqual(summary["calibration"]["underprediction_count"], 1)
        self.assertEqual(summary["calibration"]["overprediction_count"], 3)
        self.assertFalse(summary["calibration"]["calibration_ready"])
        self.assertEqual(summary["calibration"]["minimum_calibration_samples"], 20)
        self.assertEqual(summary["calibration"]["additional_samples_needed"], 16)
        buckets = {row["key"]: row for row in summary["calibration"]["buckets"]}
        self.assertEqual(buckets["mid_expected"]["mean_error"], 1.0)
        self.assertEqual(buckets["mid_expected"]["mean_absolute_error"], 8.0)
        self.assertEqual(buckets["mid_expected"]["underprediction_count"], 1)
        self.assertEqual(buckets["mid_expected"]["overprediction_count"], 1)
        self.assertEqual(buckets["high_expected"]["mean_error"], -12.0)
        self.assertEqual(buckets["high_expected"]["mean_absolute_error"], 12.0)
        self.assertEqual(summary["calibration"]["priority_bucket"]["key"], "high_expected")
        self.assertEqual(summary["calibration"]["priority_bucket"]["bias"], "overprediction")
        self.assertEqual(summary["calibration"]["priority_bucket"]["mean_absolute_error"], 12.0)
        by_external_status = {row["key"]: row for row in summary["by_external_feed_status"]}
        self.assertEqual(by_external_status["limited"]["completed_count"], 2)
        self.assertEqual(by_external_status["limited"]["mean_error"], -12.0)
        self.assertEqual(by_external_status["unknown"]["completed_count"], 2)
        by_external_coverage = {row["key"]: row for row in summary["by_external_coverage"]}
        self.assertEqual(by_external_coverage["thin_coverage"]["completed_count"], 2)
        self.assertEqual(by_external_coverage["thin_coverage"]["mean_absolute_error"], 12.0)
        self.assertEqual(by_external_coverage["unknown"]["completed_count"], 2)
        by_external_alignment = {row["key"]: row for row in summary["by_external_alignment"]}
        self.assertEqual(by_external_alignment["aligned"]["completed_count"], 2)
        self.assertEqual(by_external_alignment["unknown"]["completed_count"], 2)
        pending_external_status = {row["key"]: row for row in summary["pending_by_external_feed_status"]}
        self.assertEqual(pending_external_status["limited"]["pending_count"], 3)
        self.assertEqual(
            pending_external_status["limited"]["next_due_date"],
            estimated_label_due_date(date(2026, 1, 2), "3m").isoformat(),
        )
        self.assertEqual(pending_external_status["unknown"]["pending_count"], 3)
        pending_external_coverage = {row["key"]: row for row in summary["pending_by_external_coverage"]}
        self.assertEqual(pending_external_coverage["thin_coverage"]["pending_count"], 3)
        self.assertEqual(pending_external_coverage["unknown"]["pending_count"], 3)
        self.assertEqual(summary["pending_external_coverage_gap_count"], 3)
        self.assertEqual(len(summary["pending_external_coverage_gap_queue"]), 3)
        self.assertEqual(summary["pending_external_coverage_gap_queue"][0]["symbol"], "AMD")
        self.assertTrue(summary["pending_external_coverage_gap_queue"][0]["external_coverage_gap_id"])
        self.assertTrue(summary["pending_external_coverage_gap_queue"][0]["source_outcome_id"])
        self.assertIn("no external observation", summary["pending_external_coverage_gap_queue"][0]["external_coverage_gap_reason"])
        self.assertIn("do not use later", summary["pending_external_coverage_gap_queue"][0]["external_coverage_gap_action"])
        self.assertEqual(summary["pending_external_coverage_gap_queue"][0]["external_coverage_backfill_policy"], "decision_time_only")
        self.assertEqual(summary["pending_external_coverage_gap_plan"]["candidate_gap_count"], 3)
        self.assertEqual(summary["pending_external_coverage_gap_plan"]["priority_gap_count"], 3)
        self.assertEqual(summary["pending_external_coverage_gap_plan"]["observed_external_long_horizon_label_count"], 4)
        self.assertEqual(summary["pending_external_coverage_gap_plan"]["projected_external_long_horizon_count_after_priority_backfill"], 7)
        self.assertEqual(summary["pending_external_coverage_gap_plan"]["projected_external_additional_needed_after_priority_backfill"], 13)
        pending_external_alignment = {row["key"]: row for row in summary["pending_by_external_alignment"]}
        self.assertEqual(pending_external_alignment["aligned"]["pending_count"], 3)
        self.assertEqual(pending_external_alignment["unknown"]["pending_count"], 3)
        self.assertEqual(sum(row["due_count"] for row in summary["pending_external_alignment_due_dates"]), 3)
        self.assertEqual(sum(row["aligned_count"] for row in summary["pending_external_alignment_due_dates"]), 3)
        self.assertEqual(summary["pending_external_alignment_due_dates"][0]["due_date"], estimated_label_due_date(date(2026, 1, 2), "3m").isoformat())
        self.assertEqual(summary["pending_external_alignment_due_dates"][0]["due_count"], 1)
        self.assertEqual(summary["pending_external_alignment_due_dates"][0]["aligned_count"], 1)
        self.assertEqual(summary["pending_external_alignment_due_dates"][0]["symbols"], ["NVDA"])
        self.assertEqual(len(summary["pending_external_alignment_watchlist"]), 3)
        self.assertEqual(summary["pending_external_alignment_watchlist"][0]["symbol"], "NVDA")
        self.assertEqual(summary["pending_external_alignment_watchlist"][0]["external_alignment"], "aligned")
        self.assertIn("confirmation sample", summary["pending_external_alignment_watchlist"][0]["external_alignment_review_reason"])
        nvda_five_day = next(row for row in summary["outcomes"] if row["symbol"] == "NVDA" and row["horizon"] == "5d")
        self.assertEqual(nvda_five_day["external_alignment"], "aligned")
        self.assertEqual(nvda_five_day["external_signal_score"], 20.0)
        self.assertEqual(nvda_five_day["coverage_adjusted_external_signal_score"], 5.0)
        self.assertEqual(nvda_five_day["external_coverage_multiplier"], 0.25)
        self.assertEqual(nvda_five_day["external_feed_status"], "limited")
        self.assertEqual(nvda_five_day["external_provider_count"], 6)
        self.assertEqual(nvda_five_day["external_provider_ok_count"], 1)
        self.assertEqual(nvda_five_day["external_provider_ok_ratio"], 0.1667)
        self.assertEqual(nvda_five_day["external_signal_count"], 4)
        self.assertEqual(nvda_five_day["external_source_count"], 3)
        history = outcome_history_from_backtest(summary)
        self.assertEqual(len(history), 4)
        self.assertTrue(all(row["forward_return_pct"] > 0 for row in history))
        nvda_history = next(row for row in history if row["symbol"] == "NVDA" and row["horizon"] == "5d")
        self.assertEqual(nvda_history["coverage_adjusted_external_signal_score"], 5.0)
        self.assertEqual(nvda_history["external_feed_status"], "limited")

    def test_reconstructs_trials_from_public_action_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            payload = {
                "as_of": "2026-01-02",
                "session": "postmarket",
                "portfolio_benchmark": {
                    "action_queue": [
                        {
                            "symbol": "CRWV",
                            "bucket": "neocloud_datacenters",
                            "trade_action": "add",
                            "recommended_delta_weight": 0.03,
                            "target_weight": 0.08,
                            "risk_adjusted_expected_return": 30,
                        }
                    ]
                },
                "feature_matrix": {
                    "rows": [
                        {
                            "symbol": "CRWV",
                            "signal_families": ["manager"],
                            "event_types": ["contract_win"],
                            "external_signal_score": 12.0,
                            "coverage_adjusted_external_signal_score": 6.0,
                            "external_coverage_multiplier": 0.5,
                            "external_feed_status": "limited",
                        }
                    ]
                },
            }
            (reports / "2026-01-02-postmarket.json").write_text(json.dumps(payload), encoding="utf-8")

            summary = build_backtest_summary(
                reports,
                as_of=date(2026, 2, 10),
                price_history={"CRWV": price_history(date(2026, 1, 2), [100 + index for index in range(80)])},
        )

        self.assertEqual(summary["trial_count"], 1)
        self.assertEqual(summary["completed_outcome_count"], 2)
        self.assertEqual(summary["outcomes"][0]["symbol"], "CRWV")
        self.assertEqual(summary["outcomes"][0]["coverage_adjusted_external_signal_score"], 6.0)
        self.assertEqual(summary["outcomes"][0]["external_coverage_multiplier"], 0.5)
        self.assertEqual(summary["outcomes"][0]["external_feed_status"], "limited")

    def test_legacy_action_queue_uses_decision_card_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            payload = {
                "as_of": "2026-01-02",
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
                            "action": "Re-underwrite sizing, hedge need, and falsifier before adding exposure.",
                            "why": "Owned position has a risk catalyst or put-heavy 13F signal.",
                            "portfolio_weight": 0.08,
                            "score": 50,
                        }
                    ]
                },
            }
            (reports / "2026-01-02-postmarket.json").write_text(json.dumps(payload), encoding="utf-8")

            summary = build_backtest_summary(
                reports,
                as_of=date(2026, 1, 20),
                price_history={"NVDA": price_history(date(2026, 1, 2), [100 + index for index in range(20)])},
            )

        five_day = next(row for row in summary["outcomes"] if row["horizon"] == "5d")
        self.assertEqual(five_day["bucket"], "semis_networking_hbm")
        self.assertEqual(five_day["trade_action"], "risk_review")
        self.assertEqual(five_day["current_weight"], 0.08)
        self.assertEqual(five_day["target_weight"], 0.08)
        self.assertEqual(five_day["signal_families"], ["manager", "catalyst"])
        self.assertEqual(five_day["event_types"], ["capex_signal"])


if __name__ == "__main__":
    unittest.main()
