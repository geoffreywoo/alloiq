import unittest

from invest.audit import data_gaps


class AuditTests(unittest.TestCase):
    def test_estimated_source_status_is_a_data_gap(self):
        gaps = data_gaps(
            {
                "sources": [
                    {
                        "label": "Earnings calendar",
                        "status": "estimated",
                        "detail": "Only provider-estimated forward dates are available.",
                        "confirmation_gap_count": 1,
                        "confirmation_gaps": [{"symbol": "MRVL", "event_date": "2026-05-27"}],
                        "action_linked_confirmation_gap_count": 1,
                        "approval_blocked_confirmation_gap_count": 1,
                        "approval_blocked_confirmation_gaps": [{"symbol": "MRVL", "ticket_id": "ticket-mrvl"}],
                        "approval_blocked_external_gap_count": 2,
                        "approval_blocked_external_gaps": [{"symbol": "NVDA", "ticket_id": "ticket-nvda"}],
                    }
                ]
            },
            {"earnings": {"event_count": 4, "source_quality": "estimated"}},
            {"learning": {"status": "trained"}},
        )

        self.assertEqual(gaps[0]["area"], "source")
        self.assertEqual(gaps[0]["label"], "Earnings calendar")
        self.assertEqual(gaps[0]["status"], "estimated")
        self.assertEqual(gaps[0]["confirmation_gap_count"], 1)
        self.assertEqual(gaps[0]["confirmation_gaps"][0]["symbol"], "MRVL")
        self.assertEqual(gaps[0]["action_linked_confirmation_gap_count"], 1)
        self.assertEqual(gaps[0]["approval_blocked_confirmation_gap_count"], 1)
        self.assertEqual(gaps[0]["approval_blocked_confirmation_gaps"][0]["ticket_id"], "ticket-mrvl")
        self.assertEqual(gaps[0]["approval_blocked_external_gap_count"], 2)
        self.assertEqual(gaps[0]["approval_blocked_external_gaps"][0]["ticket_id"], "ticket-nvda")

    def test_learning_gap_includes_label_maturity_and_next_due_date(self):
        gaps = data_gaps(
            {"sources": []},
            {"earnings": {"event_count": 4, "source_quality": "ok"}},
            {
                "learning": {
                    "status": "baseline_fallback",
                    "minimum_required": 20,
                    "message": "Insufficient completed outcomes.",
                }
            },
            {
                "label_maturity": {
                    "completed_long_horizon_count": 3,
                    "minimum_long_horizon_required": 20,
                    "additional_long_horizon_needed": 17,
                },
                "learning_readiness_projection": {
                    "minimum_long_horizon_required": 20,
                    "projected_long_horizon_count_30d": 8,
                    "projected_additional_needed_30d": 12,
                    "next_learning_label_due_date": "2026-06-24",
                    "projected_long_horizon_count_next_learning_label": 9,
                    "projected_additional_needed_next_learning_label": 11,
                    "estimated_learning_ready_date": "2026-08-24",
                    "estimated_learning_ready_projected_count": 20,
                    "learning_ready_with_scheduled_pending_labels": True,
                },
                "external_learning_readiness_projection": {
                    "minimum_external_long_horizon_required": 20,
                    "projected_external_long_horizon_count_all_scheduled": 5,
                    "projected_external_additional_needed_all_scheduled": 15,
                    "next_external_fast_label_due_date": "2026-05-31",
                    "next_external_fast_label_due_count": 2,
                    "external_fast_labels_due_next_30d": 2,
                    "external_learning_ready_with_scheduled_pending_labels": False,
                },
                "approval_learning_readiness_projection": {
                    "pending_approval_label_count": 6,
                    "pending_approval_learning_label_count": 4,
                    "pending_approval_fast_label_count": 2,
                    "next_approval_label_due_date": "2026-05-31",
                    "next_approval_label_due_count": 2,
                    "next_approval_learning_label_due_date": "2026-06-24",
                    "next_approval_learning_label_due_count": 4,
                    "pending_approval_blocker_buckets": [
                        {"key": "review_required", "pending_count": 4},
                        {"key": "blocked_until_confirmation", "pending_count": 2},
                    ],
                },
                "approval_data_friction_learning_readiness_projection": {
                    "pending_approval_data_friction_label_count": 6,
                    "pending_approval_data_friction_learning_label_count": 4,
                    "pending_approval_data_friction_fast_label_count": 2,
                    "next_approval_data_friction_learning_label_due_date": "2026-06-24",
                    "next_approval_data_friction_learning_label_due_count": 4,
                    "pending_approval_data_friction_buckets": [
                        {"key": "external_review", "pending_count": 4},
                        {"key": "earnings_and_external_review", "pending_count": 2},
                    ],
                },
                "external_coverage_gap_plan": {
                    "minimum_external_long_horizon_required": 20,
                    "priority_gap_count": 2,
                    "projected_external_long_horizon_count_after_priority_backfill": 7,
                    "projected_external_learning_ready_date_after_priority_backfill": "2026-06-24",
                    "priority_rows": [
                        {"symbol": "AMD", "external_coverage_gap_id": "gap-amd-1m"},
                        {"symbol": "ASML", "external_coverage_gap_id": "gap-asml-1m"},
                    ],
                },
                "pending_label_schedule": {
                    "next_learning_label": {
                        "due_date": "2026-06-24",
                        "days_until_due": 31,
                    },
                    "learning_due_window_counts": {
                        "due_next_7d": 2,
                        "due_next_30d": 5,
                    },
                },
            },
        )

        self.assertEqual(gaps[0]["area"], "engine")
        self.assertEqual(gaps[0]["status"], "baseline_fallback")
        self.assertIn("3/20 completed 1-12 month labels", gaps[0]["detail"])
        self.assertIn("30-day projection: 8/20 labels; 12 more still needed", gaps[0]["detail"])
        self.assertIn("Next learning-label projection: 9/20 labels after 2026-06-24; 11 more still needed", gaps[0]["detail"])
        self.assertIn("Estimated learning-ready date: 2026-08-24 (20/20 labels)", gaps[0]["detail"])
        self.assertIn("External-signal learning bottleneck: 5/20 externally covered labels", gaps[0]["detail"])
        self.assertIn("External-signal fast check: 2 5-day labels due 2026-05-31", gaps[0]["detail"])
        self.assertIn("Approval-gated learning labels: 6 pending; 4 learning-eligible; 2 5-day fast checks", gaps[0]["detail"])
        self.assertIn("Next approval-gated label due 2026-05-31 (2 labels)", gaps[0]["detail"])
        self.assertIn("Next learning-eligible approval label due 2026-06-24 (4 labels)", gaps[0]["detail"])
        self.assertIn("Approval blockers queued for labels: review required 4; blocked until confirmation 2", gaps[0]["detail"])
        self.assertIn("Approval data-friction labels: 6 pending; 4 learning-eligible; 2 5-day fast checks", gaps[0]["detail"])
        self.assertIn("Next learning-eligible friction label due 2026-06-24 (4 labels)", gaps[0]["detail"])
        self.assertIn("Approval data-friction buckets queued for labels: external review 4; earnings and external review 2", gaps[0]["detail"])
        self.assertIn("External coverage priority backfill: 2 decision-time items (AMD, ASML)", gaps[0]["detail"])
        self.assertIn("gap-amd-1m", gaps[0]["detail"])
        self.assertIn("Next learning-eligible label due 2026-06-24, in 31 days", gaps[0]["detail"])
        self.assertIn("Learning labels due soon: 2 within 7 days, 5 within 30 days", gaps[0]["detail"])


if __name__ == "__main__":
    unittest.main()
