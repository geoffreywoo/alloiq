from datetime import date
import unittest

from invest.engine import (
    ENGINE_POLICY_VERSION,
    build_engine_features,
    build_engine_snapshot,
    build_learning_state,
    decision_readiness,
    rank_candidates,
)


class EngineTests(unittest.TestCase):
    def test_engine_feature_generation_is_deterministic_and_ranked(self):
        engine = build_engine_snapshot(
            date(2026, 5, 24),
            "premarket",
            [
                {
                    "symbol": "NVDA",
                    "bucket": "semis_networking_hbm",
                    "score": 50,
                    "signal_family_count": 3,
                    "signal_families": ["manager", "catalyst", "price_action"],
                    "consensus_manager_count": 4,
                    "event_score": 6,
                    "five_day_pct": 2,
                },
                {
                    "symbol": "AMD",
                    "bucket": "semis_networking_hbm",
                    "score": 30,
                    "signal_family_count": 2,
                    "signal_families": ["manager", "price_action"],
                    "consensus_manager_count": 2,
                    "event_score": 1,
                    "five_day_pct": 12,
                },
            ],
            {"by_symbol": [{"symbol": "NVDA", "weight": 0.08}]},
            {"exposure_gaps": [{"symbol": "AMD", "peer_avg_weight": 0.04}]},
            [{"ticket_id": "t1", "symbol": "NVDA", "trade_action": "add", "recommended_delta_weight": 0.01, "target_weight": 0.09}],
            {"max_single_name_weight": 0.15},
        )

        self.assertEqual(engine["version"], ENGINE_POLICY_VERSION)
        self.assertEqual(engine["ranked_candidates"][0]["symbol"], "NVDA")
        self.assertEqual(engine["optimizer"]["allocations"][0]["recommended_delta_weight"], 0.01)
        self.assertEqual(engine["live_order_execution"], "disabled")

    def test_learning_falls_back_without_enough_outcomes(self):
        learning = build_learning_state([{"forward_return_pct": 10, "expected_return_score": 40}])

        self.assertEqual(learning["status"], "baseline_fallback")
        self.assertEqual(learning["minimum_required"], 20)

    def test_learning_tracks_but_does_not_train_on_five_day_labels(self):
        learning = build_learning_state(
            [
                {"horizon": "5d", "forward_return_pct": 10, "expected_return_score": 40, "signal_families": ["manager"]}
                for _ in range(25)
            ]
        )

        self.assertEqual(learning["status"], "baseline_fallback")
        self.assertEqual(learning["outcome_count"], 0)
        self.assertEqual(learning["short_horizon_outcome_count"], 25)

    def test_learning_uses_signal_family_outcomes_without_expected_scores(self):
        outcomes = [
            {"horizon": "1m", "forward_return_pct": 14, "signal_families": ["manager"]}
            for _ in range(10)
        ] + [
            {"horizon": "1m", "forward_return_pct": -6, "signal_families": ["price_action"]}
            for _ in range(10)
        ]

        learning = build_learning_state(outcomes)

        self.assertEqual(learning["status"], "history_adjusted")
        self.assertEqual(learning["outcome_count"], 20)
        self.assertEqual(learning["expected_scored_outcome_count"], 0)
        self.assertGreater(learning["weight_adjustments"]["manager"], 0)
        self.assertLess(learning["weight_adjustments"]["price_action"], 0)
        self.assertEqual(learning["minimum_family_outcomes"], 3)

    def test_learning_prefers_residual_alpha_when_expected_returns_exist(self):
        outcomes = [
            {
                "horizon": "1m",
                "forward_return_pct": 18,
                "expected_return_score": None,
                "risk_adjusted_expected_return": 25,
                "signal_families": ["manager"],
            }
            for _ in range(5)
        ] + [
            {
                "horizon": "1m",
                "forward_return_pct": 18,
                "expected_return_score": 25,
                "signal_families": ["manager"],
            }
            for _ in range(5)
        ] + [
            {
                "horizon": "1m",
                "forward_return_pct": 4,
                "risk_adjusted_expected_return": -5,
                "signal_families": ["price_action"],
            }
            for _ in range(10)
        ]

        learning = build_learning_state(outcomes)

        self.assertEqual(learning["status"], "history_adjusted")
        self.assertEqual(learning["expected_scored_outcome_count"], 20)
        self.assertLess(learning["weight_adjustments"]["manager"], 0)
        self.assertGreater(learning["weight_adjustments"]["price_action"], 0)

    def test_learning_caps_extreme_residual_returns(self):
        outcomes = (
            [{"horizon": "1m", "forward_return_pct": 120, "risk_adjusted_expected_return": 0, "signal_families": ["manager"]}]
            + [{"horizon": "1m", "forward_return_pct": 0, "risk_adjusted_expected_return": 0, "signal_families": ["manager"]} for _ in range(2)]
            + [{"horizon": "1m", "forward_return_pct": 0, "risk_adjusted_expected_return": 0, "signal_families": ["price_action"]} for _ in range(17)]
        )

        learning = build_learning_state(outcomes)

        self.assertEqual(learning["status"], "history_adjusted")
        self.assertEqual(learning["learning_return_cap"], 40.0)
        self.assertLess(learning["weight_adjustments"]["manager"], 1.0)

    def test_learning_shrinks_weight_adjustments_for_low_sample_families(self):
        outcomes = (
            [{"horizon": "1m", "forward_return_pct": 20, "signal_families": ["manager"]} for _ in range(3)]
            + [{"horizon": "1m", "forward_return_pct": 20, "signal_families": ["quality"]} for _ in range(10)]
            + [{"horizon": "1m", "forward_return_pct": 0, "signal_families": ["price_action"]} for _ in range(10)]
        )

        learning = build_learning_state(outcomes)

        self.assertEqual(learning["status"], "history_adjusted")
        self.assertEqual(learning["full_family_confidence_outcomes"], 10)
        self.assertEqual(learning["family_confidence"]["manager"], 0.3)
        self.assertEqual(learning["family_confidence"]["quality"], 1.0)
        self.assertLess(learning["weight_adjustments"]["manager"], learning["weight_adjustments"]["quality"])

    def test_rank_candidates_caps_total_learning_adjustment(self):
        ranked = rank_candidates(
            [
                {"symbol": "STACK", "expected_return_score": 0, "signal_families": ["manager", "quality", "timing"]},
                {"symbol": "BASE", "expected_return_score": 5, "signal_families": []},
            ],
            {"weight_adjustments": {"manager": 1, "quality": 1, "timing": 1}},
        )

        by_symbol = {row["symbol"]: row for row in ranked}
        self.assertEqual(by_symbol["STACK"]["learning_adjustment"], 6.0)
        self.assertEqual(by_symbol["STACK"]["learning_adjustment_cap"], 6.0)
        self.assertEqual(by_symbol["STACK"]["expected_return_rank_score"], 6.0)
        self.assertEqual(by_symbol["BASE"]["expected_return_rank_score"], 5.0)

    def test_rank_candidates_collapses_equivalent_share_classes(self):
        ranked = rank_candidates(
            [
                {"symbol": "GOOGL", "expected_return_score": 25.0, "current_weight": 0.0, "signal_families": []},
                {"symbol": "GOOG", "expected_return_score": 25.0, "current_weight": 0.12, "signal_families": []},
                {"symbol": "MSFT", "expected_return_score": 22.0, "current_weight": 0.05, "signal_families": []},
            ],
            {"weight_adjustments": {}},
        )

        self.assertEqual([row["symbol"] for row in ranked], ["GOOG", "MSFT"])
        self.assertEqual(ranked[0]["symbol_proxy_key"], "GOOG")
        self.assertEqual(ranked[0]["deduplicated_equivalent_symbols"], ["GOOGL"])
        self.assertEqual(ranked[0]["rank"], 1)
        self.assertEqual(ranked[1]["rank"], 2)

    def test_rank_candidates_prefers_actionable_exact_ticket_for_equivalent_symbols(self):
        ranked = rank_candidates(
            [
                {"symbol": "GOOG", "expected_return_score": 25.0, "current_weight": 0.12, "signal_families": []},
                {"symbol": "GOOGL", "expected_return_score": 25.0, "current_weight": 0.0, "signal_families": []},
            ],
            {"weight_adjustments": {}},
            [{"symbol": "GOOGL", "trade_action": "add", "recommended_delta_weight": 0.02}],
        )

        self.assertEqual([row["symbol"] for row in ranked], ["GOOGL"])
        self.assertEqual(ranked[0]["deduplicated_equivalent_symbols"], ["GOOG"])

    def test_rank_candidates_surfaces_decision_readiness_evidence(self):
        ranked = rank_candidates(
            [
                {
                    "symbol": "NVDA",
                    "expected_return_score": 25.0,
                    "signal_families": [],
                    "evidence_quality": 42.0,
                    "company_review_required": True,
                    "external_feed_status": "limited",
                    "external_provider_gap_count": 3,
                    "external_provider_gap_severity_score": 50.0,
                }
            ],
            {"weight_adjustments": {}},
        )

        nvda = ranked[0]
        self.assertEqual(nvda["decision_readiness_bucket"], "evidence_blocked")
        self.assertLess(nvda["decision_readiness_score"], 65.0)
        self.assertIn("company_underwriting_review_required", nvda["decision_evidence_blockers"])
        self.assertIn("evidence_quality_watch", nvda["decision_evidence_blockers"])
        self.assertIn("external_feed_reliability_review_required", nvda["decision_evidence_blockers"])

    def test_rank_candidates_prefers_readier_equivalent_when_scores_match(self):
        ranked = rank_candidates(
            [
                {
                    "symbol": "GOOG",
                    "expected_return_score": 25.0,
                    "current_weight": 0.05,
                    "signal_families": [],
                    "company_review_required": True,
                    "evidence_quality": 25.0,
                },
                {
                    "symbol": "GOOGL",
                    "expected_return_score": 25.0,
                    "current_weight": 0.04,
                    "signal_families": [],
                    "evidence_quality": 85.0,
                },
            ],
            {"weight_adjustments": {}},
        )

        self.assertEqual([row["symbol"] for row in ranked], ["GOOGL"])
        self.assertEqual(ranked[0]["decision_readiness_bucket"], "approval_ready")
        self.assertEqual(ranked[0]["deduplicated_equivalent_symbols"], ["GOOG"])

    def test_engine_provenance_does_not_apply_proxy_ticket_after_deduping(self):
        engine = build_engine_snapshot(
            date(2026, 5, 24),
            "postmarket",
            [],
            {},
            {},
            [{"symbol": "GOOGL", "trade_action": "hold", "recommended_delta_weight": 0.0, "target_weight": 0.0}],
            feature_matrix={
                "rows": [
                    {"symbol": "GOOGL", "expected_return_score": 25.0, "current_weight": 0.0, "signal_families": []},
                    {"symbol": "GOOG", "expected_return_score": 25.0, "current_weight": 0.12, "signal_families": []},
                ]
            },
        )

        provenance = engine["recommendation_provenance"][0]
        self.assertEqual(provenance["symbol"], "GOOG")
        self.assertEqual(provenance["recommended_delta_weight"], 0)
        self.assertEqual(provenance["target_weight"], 0.12)
        self.assertEqual(provenance["decision_readiness_bucket"], "approval_ready")
        self.assertEqual(provenance["status"], "ranked")

    def test_learning_ignores_sparse_signal_family_outliers(self):
        outcomes = (
            [{"horizon": "1m", "forward_return_pct": 6, "signal_families": ["manager"]} for _ in range(10)]
            + [{"horizon": "1m", "forward_return_pct": 2, "signal_families": ["price_action"]} for _ in range(10)]
            + [{"horizon": "1m", "forward_return_pct": 80, "signal_families": ["one_off"]}]
        )

        learning = build_learning_state(outcomes)

        self.assertEqual(learning["status"], "history_adjusted")
        self.assertEqual(learning["family_sample_counts"]["one_off"], 1)
        self.assertNotIn("one_off", learning["weight_adjustments"])

    def test_learning_falls_back_when_all_families_are_sparse(self):
        outcomes = [
            {"horizon": "1m", "forward_return_pct": index, "signal_families": [f"family_{index}"]}
            for index in range(20)
        ]

        learning = build_learning_state(outcomes)

        self.assertEqual(learning["status"], "baseline_fallback")
        self.assertEqual(learning["outcome_count"], 20)
        self.assertEqual(learning["minimum_family_outcomes"], 3)
        self.assertEqual(learning["weight_adjustments"], {})
        self.assertIn("no signal family has enough samples", learning["message"])

    def test_engine_features_use_ex_cash_comparison_weights(self):
        features = build_engine_features(
            date(2026, 5, 24),
            [
                {"symbol": "NVDA", "bucket": "semis_networking_hbm", "score": 50},
                {"symbol": "CASH", "bucket": "cash_reserves", "score": 1},
            ],
            {
                "by_symbol": [
                    {"symbol": "NVDA", "weight": 0.08, "comparison_weight": 0.10},
                    {"symbol": "CASH", "weight": 0.20, "comparison_weight": 0.0, "is_cash": True},
                ]
            },
            {"exposure_gaps": []},
        )

        by_symbol = {row["symbol"]: row for row in features}
        self.assertEqual(by_symbol["NVDA"]["current_weight"], 0.10)
        self.assertEqual(by_symbol["CASH"]["current_weight"], 0.0)

    def test_matrix_engine_features_explain_coverage_adjusted_external_signals(self):
        features = build_engine_features(
            date(2026, 5, 24),
            [],
            {},
            {},
            {
                "rows": [
                    {
                        "symbol": "NVDA",
                        "bucket": "semis_networking_hbm",
                        "external_signal_score": 20.0,
                        "coverage_adjusted_external_signal_score": 5.0,
                        "external_coverage_multiplier": 0.25,
                        "external_feed_status": "limited",
                        "external_provider_count": 6,
                        "external_provider_ok_count": 1,
                        "external_provider_ok_ratio": 0.1667,
                    }
                ]
            },
        )

        nvda = features[0]
        self.assertEqual(nvda["external_signal_score"], 20.0)
        self.assertEqual(nvda["coverage_adjusted_external_signal_score"], 5.0)
        self.assertEqual(nvda["external_coverage_multiplier"], 0.25)
        self.assertEqual(nvda["external_feed_status"], "limited")
        self.assertEqual(nvda["external_provider_count"], 6)
        self.assertEqual(nvda["external_provider_ok_count"], 1)
        self.assertEqual(nvda["external_provider_ok_ratio"], 0.1667)
        self.assertEqual(nvda["component_scores"]["external_signals"], 5.0)
        self.assertEqual(nvda["component_scores"]["external_signals_raw"], 20.0)

    def test_decision_readiness_keeps_clean_evidence_approval_ready(self):
        readiness = decision_readiness(
            {
                "evidence_quality": 82.0,
                "external_feed_status": "ok",
                "external_provider_gap_count": 0,
                "company_review_required": False,
            }
        )

        self.assertEqual(readiness["score"], 100.0)
        self.assertEqual(readiness["bucket"], "approval_ready")
        self.assertEqual(readiness["blockers"], [])


if __name__ == "__main__":
    unittest.main()
