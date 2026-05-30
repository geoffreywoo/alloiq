from datetime import date
from decimal import Decimal
import unittest

from invest.features import build_feature_matrix
from invest.outcomes import build_outcome_diagnostics, build_training_examples, learning_readiness_projection, pending_label_schedule
from invest.research import build_research_book, scenario_returns
from invest.sizing import build_sizing_plan, target_for_item


class ResearchEngineTests(unittest.TestCase):
    def sample_cards(self):
        return [
            {
                "symbol": "NVDA",
                "bucket": "semis_networking_hbm",
                "score": 55,
                "signal_family_count": 3,
                "signal_families": ["manager", "catalyst", "price_action"],
                "consensus_manager_count": 4,
                "event_score": 8,
                "top_event_types": ["capex_signal"],
                "source_tiers": ["primary", "specialist"],
                "call_value": 100_000_000,
                "put_value": 0,
                "counterargument": "AI capex can slow.",
                "falsifier": "Backlog rolls over.",
            },
            {
                "symbol": "CRWV",
                "bucket": "neocloud_datacenters",
                "score": 48,
                "signal_family_count": 2,
                "signal_families": ["manager", "catalyst"],
                "consensus_manager_count": 3,
                "event_score": 7,
                "top_event_types": ["financing_risk"],
                "source_tiers": ["market_news"],
                "call_value": 0,
                "put_value": 0,
            },
        ]

    def test_feature_matrix_normalizes_ml_ready_rows(self):
        features = build_feature_matrix(
            date(2026, 5, 24),
            self.sample_cards(),
            {
                "by_symbol": [{"symbol": "NVDA", "bucket": "semis_networking_hbm", "weight": 0.14}],
                "by_bucket": [{"bucket": "semis_networking_hbm", "weight": 0.32}],
            },
            {
                "focus_managers": [
                    {
                        "status": "ok",
                        "manager_tier": "tier_1",
                        "manager_name": "Altimeter",
                        "positions": [{"symbol": "NVDA", "fund_weight": 0.12}],
                    }
                ],
                "top_adds": [{"symbol": "NVDA", "delta_value": 500_000_000}],
            },
            {"regime": "risk-on AI acceleration", "scores": {"ai_momentum": 4, "risk_momentum": 2}},
            {"NVDA": {"1d": Decimal("3"), "5d": Decimal("5"), "1m": Decimal("12"), "3m": Decimal("30"), "ytd": Decimal("60"), "1y": Decimal("100")}},
            [
                {
                    "symbol": "NVDA",
                    "event_type": "earnings",
                    "event_date": "2026-06-14",
                    "days_until": 21,
                    "source": "nasdaq_earnings_calendar",
                    "confirmed_or_estimated": "estimated",
                    "risk_window": "clear",
                }
            ],
        )

        self.assertEqual(features["feature_count"], 2)
        nvda = next(row for row in features["rows"] if row["symbol"] == "NVDA")
        self.assertEqual(nvda["current_weight"], 0.14)
        self.assertEqual(nvda["tier1_manager_count"], 1)
        self.assertGreater(nvda["evidence_quality"], 60)
        self.assertEqual(nvda["price_return_1d"], 3.0)
        self.assertEqual(nvda["price_return_3m"], 30.0)
        self.assertEqual(nvda["earnings_event_date"], "2026-06-14")
        self.assertEqual(nvda["earnings_event_source"], "nasdaq_earnings_calendar")
        self.assertEqual(nvda["earnings_confirmed_or_estimated"], "estimated")
        self.assertTrue(nvda["earnings_confirmation_required"])
        self.assertEqual(nvda["approval_data_friction_bucket"], "earnings_confirmation")
        self.assertIn("estimated_earnings_confirmation_required", nvda["approval_data_friction_reasons"])

    def test_approval_data_friction_reaches_research_and_training_examples(self):
        features = build_feature_matrix(
            date(2026, 5, 24),
            self.sample_cards(),
            {"by_symbol": [{"symbol": "NVDA", "bucket": "semis_networking_hbm", "weight": 0.08}]},
            {"focus_managers": []},
            {"regime": "risk-on AI acceleration", "scores": {"ai_momentum": 3}},
            {"NVDA": {"5d": Decimal("2"), "1m": Decimal("8"), "3m": Decimal("18")}},
            [
                {
                    "symbol": "NVDA",
                    "event_type": "earnings",
                    "event_date": "2026-05-25",
                    "days_until": 1,
                    "source": "nasdaq_earnings_calendar",
                    "confirmed_or_estimated": "estimated",
                    "risk_window": "blackout",
                }
            ],
            {
                "status": "limited",
                "provider_count": 6,
                "provider_ok_count": 2,
                "provider_ok_ratio": 0.3333,
                "provider_gaps": [
                    {"source": "alpha_vantage_news", "severity": "configuration_required"},
                    {"source": "gdelt_global_news", "severity": "transient_network"},
                    {"source": "finra_short_interest", "severity": "stale_or_empty"},
                ],
                "by_symbol": {"NVDA": {"external_signal_score": 18.0, "signal_count": 3, "source_count": 2}},
            },
        )

        feature = next(row for row in features["rows"] if row["symbol"] == "NVDA")
        self.assertEqual(feature["feature_version"], "2026-05-ml-feature-matrix-v5")
        self.assertEqual(feature["external_provider_gap_count"], 3)
        self.assertEqual(feature["external_provider_configuration_gap_count"], 1)
        self.assertEqual(feature["external_provider_transient_gap_count"], 1)
        self.assertEqual(feature["external_provider_stale_gap_count"], 1)
        self.assertEqual(feature["external_provider_primary_gap_severity"], "configuration_required")
        self.assertEqual(feature["external_provider_gap_severity_score"], 35.0)
        self.assertEqual(feature["approval_data_friction_bucket"], "earnings_and_external_review")
        self.assertGreater(feature["approval_data_friction_score"], 70)
        self.assertIn("external_feed_reliability_review_required", feature["approval_data_friction_reasons"])

        research = build_research_book(date(2026, 5, 24), features, self.sample_cards(), {"regime": "risk-on AI acceleration"})
        item = next(row for row in research["items"] if row["symbol"] == "NVDA")
        self.assertEqual(item["external_provider_primary_gap_severity"], "configuration_required")
        self.assertEqual(item["external_provider_gap_severity_score"], 35.0)
        self.assertEqual(item["approval_data_friction_bucket"], "earnings_and_external_review")
        self.assertGreater(item["approval_data_friction_penalty"], 0)

        examples = build_training_examples(
            date(2026, 5, 24),
            "premarket",
            [
                {
                    "ticket_id": "ticket-nvda",
                    "symbol": "NVDA",
                    "trade_action": "add",
                    "recommended_delta_weight": 0.02,
                    "target_weight": 0.10,
                    "approval_gate_status": "blocked_until_confirmation",
                    "approval_checks": [{"check": "earnings_date_confirmed", "status": "pending"}],
                }
            ],
            research,
            features,
        )

        self.assertEqual(examples[0]["approval_data_friction_bucket"], "earnings_and_external_review")
        self.assertEqual(examples[0]["external_provider_gap_count"], 3)
        self.assertEqual(examples[0]["external_provider_primary_gap_severity"], "configuration_required")
        self.assertEqual(examples[0]["external_provider_gap_severity_score"], 35.0)
        self.assertIn("estimated_earnings_confirmation_required", examples[0]["approval_data_friction_reasons"])

    def test_external_provider_health_reaches_symbols_without_signal_rows(self):
        features = build_feature_matrix(
            date(2026, 5, 24),
            self.sample_cards(),
            {"by_symbol": [], "by_bucket": []},
            {"focus_managers": []},
            {"regime": "mixed macro tape", "scores": {}},
            {},
            [],
            {
                "status": "limited",
                "symbols": ["NVDA", "CRWV"],
                "provider_count": 6,
                "provider_ok_count": 1,
                "provider_ok_ratio": 0.1667,
                "provider_gaps": [
                    {"source": "alpha_vantage_news", "severity": "configuration_required"},
                    {"source": "gdelt_global_news", "severity": "transient_network"},
                ],
                "by_symbol": {},
            },
        )

        crwv = next(row for row in features["rows"] if row["symbol"] == "CRWV")
        self.assertEqual(crwv["external_signal_score"], 0.0)
        self.assertEqual(crwv["external_feed_status"], "limited")
        self.assertEqual(crwv["external_provider_count"], 6)
        self.assertEqual(crwv["external_provider_ok_count"], 1)
        self.assertEqual(crwv["external_provider_gap_count"], 2)
        self.assertEqual(crwv["external_provider_primary_gap_severity"], "configuration_required")
        self.assertEqual(crwv["external_coverage_multiplier"], 0.25)
        self.assertEqual(crwv["approval_data_friction_bucket"], "external_review")
        self.assertIn("external_feed_reliability_review_required", crwv["approval_data_friction_reasons"])

    def test_fred_macro_stress_reaches_features_and_expected_returns(self):
        stressed = build_feature_matrix(
            date(2026, 5, 24),
            self.sample_cards(),
            {"by_symbol": [{"symbol": "CRWV", "bucket": "neocloud_datacenters", "weight": 0.02}], "by_bucket": [{"bucket": "neocloud_datacenters", "weight": 0.08}]},
            {"focus_managers": []},
            {
                "regime": "credit/liquidity stress",
                "scores": {
                    "credit_stress_score": 12,
                    "liquidity_pressure_score": 8,
                    "yield_curve_inversion_score": 5,
                    "energy_pressure_score": 4,
                },
            },
            {"CRWV": {"5d": Decimal("2"), "1m": Decimal("8"), "3m": Decimal("20")}},
            [],
        )
        calm = build_feature_matrix(
            date(2026, 5, 24),
            self.sample_cards(),
            {"by_symbol": [{"symbol": "CRWV", "bucket": "neocloud_datacenters", "weight": 0.02}], "by_bucket": [{"bucket": "neocloud_datacenters", "weight": 0.08}]},
            {"focus_managers": []},
            {"regime": "mixed macro tape", "scores": {}},
            {"CRWV": {"5d": Decimal("2"), "1m": Decimal("8"), "3m": Decimal("20")}},
            [],
        )

        stressed_research = build_research_book(date(2026, 5, 24), stressed, self.sample_cards(), {"regime": "credit/liquidity stress"})
        calm_research = build_research_book(date(2026, 5, 24), calm, self.sample_cards(), {"regime": "mixed macro tape"})
        stressed_crwv = next(row for row in stressed["rows"] if row["symbol"] == "CRWV")
        stressed_item = next(row for row in stressed_research["items"] if row["symbol"] == "CRWV")
        calm_item = next(row for row in calm_research["items"] if row["symbol"] == "CRWV")

        self.assertEqual(stressed_crwv["macro_credit_stress"], 12.0)
        self.assertGreater(stressed_crwv["drawdown_risk"], next(row for row in calm["rows"] if row["symbol"] == "CRWV")["drawdown_risk"])
        self.assertLess(stressed_item["risk_adjusted_expected_return"], calm_item["risk_adjusted_expected_return"])

    def test_research_scenarios_use_coverage_adjusted_external_score(self):
        base_feature = {
            "bucket": "semis_networking_hbm",
            "company_underwriting_score": 50,
            "sector_setup_score": 50,
            "evidence_quality": 50,
            "valuation_support": 50,
            "drawdown_risk": 45,
            "external_signal_score": 20.0,
        }

        raw = scenario_returns(base_feature, "semis_networking_hbm")
        adjusted = scenario_returns(
            dict(base_feature, coverage_adjusted_external_signal_score=5.0),
            "semis_networking_hbm",
        )
        same_as_adjusted_input = scenario_returns(
            dict(base_feature, external_signal_score=5.0),
            "semis_networking_hbm",
        )

        self.assertGreater(raw["base_return_12m"], adjusted["base_return_12m"])
        self.assertEqual(adjusted["base_return_12m"], same_as_adjusted_input["base_return_12m"])

    def test_bounded_llm_signal_adjusts_research_scores_without_setting_sizing(self):
        features = build_feature_matrix(
            date(2026, 5, 24),
            self.sample_cards(),
            {"by_symbol": [{"symbol": "NVDA", "bucket": "semis_networking_hbm", "weight": 0.08}]},
            {"focus_managers": []},
            {"regime": "risk-on AI acceleration", "scores": {"ai_momentum": 3}},
            {"NVDA": {"5d": Decimal("2"), "1m": Decimal("8"), "3m": Decimal("18")}},
            [],
        )
        baseline = build_research_book(date(2026, 5, 24), features, self.sample_cards(), {"regime": "risk-on AI acceleration"})
        llm_signal = {
            "status": "ok",
            "mode": "bounded_signal",
            "reviews": [
                {
                    "symbol": "NVDA",
                    "thesis_quality": "strong",
                    "llm_expected_return_delta": 6.0,
                    "llm_evidence_quality_delta": 10.0,
                    "llm_drawdown_risk_delta": -10.0,
                    "llm_conviction_score": 90,
                    "llm_variant_quality_score": 86,
                    "llm_source_quality_score": 84,
                    "llm_contradiction_risk_score": 10,
                    "llm_staleness_risk_score": 12,
                    "llm_review_required": False,
                    "decision_usefulness_score": 92,
                    "confidence": 0.5,
                    "rationale": "Variant evidence improves expected return.",
                }
            ],
        }

        adjusted = build_research_book(date(2026, 5, 24), features, self.sample_cards(), {"regime": "risk-on AI acceleration"}, llm_signal=llm_signal)
        base_nvda = next(row for row in baseline["items"] if row["symbol"] == "NVDA")
        adjusted_nvda = next(row for row in adjusted["items"] if row["symbol"] == "NVDA")

        self.assertTrue(adjusted_nvda["llm_signal_applied"])
        self.assertEqual(adjusted_nvda["base_risk_adjusted_expected_return"], base_nvda["risk_adjusted_expected_return"])
        self.assertAlmostEqual(adjusted_nvda["risk_adjusted_expected_return"], base_nvda["risk_adjusted_expected_return"] + 3.0, places=2)
        self.assertNotIn("recommended_delta_weight", adjusted_nvda["llm_signal"])

    def test_research_book_and_sizing_create_target_weights(self):
        features = build_feature_matrix(
            date(2026, 5, 24),
            self.sample_cards(),
            {
                "by_symbol": [{"symbol": "NVDA", "bucket": "semis_networking_hbm", "weight": 0.08}],
                "by_bucket": [{"bucket": "semis_networking_hbm", "weight": 0.2}],
            },
            {"focus_managers": []},
            {"regime": "risk-on AI acceleration", "scores": {"ai_momentum": 3}},
            {"NVDA": {"5d": Decimal("2"), "1m": Decimal("8"), "3m": Decimal("18")}},
            [
                {
                    "symbol": "NVDA",
                    "event_type": "earnings",
                    "event_date": "2026-06-14",
                    "days_until": 21,
                    "source": "nasdaq_earnings_calendar",
                    "confirmed_or_estimated": "estimated",
                    "risk_window": "clear",
                }
            ],
            {
                "status": "limited",
                "provider_count": 6,
                "provider_ok_count": 2,
                "provider_ok_ratio": 0.3333,
                "by_symbol": {
                    "NVDA": {
                        "external_signal_score": 20.0,
                        "signal_count": 4,
                        "source_count": 3,
                    }
                },
            },
        )
        research = build_research_book(date(2026, 5, 24), features, self.sample_cards(), {"regime": "risk-on AI acceleration"})
        sizing = build_sizing_plan(
            research,
            {
                "by_symbol": [{"symbol": "NVDA", "bucket": "semis_networking_hbm", "weight": 0.08}],
                "by_bucket": [{"bucket": "semis_networking_hbm", "weight": 0.2}],
            },
            [{"symbol": "NVDA", "weight": 0.08, "five_day_pct": 2, "contribution_pct": 0.16}],
            [],
            {"max_single_name_weight": 0.15, "max_one_ticket_delta": 0.03},
        )

        nvda = next(row for row in sizing["targets"] if row["symbol"] == "NVDA")
        self.assertIn(nvda["trade_action"], {"add", "hold", "trim"})
        self.assertIn("model_target_weight", nvda)
        self.assertLessEqual(abs(nvda["recommended_delta_weight"]), 0.03)
        self.assertEqual(nvda["target_weight"], nvda["post_action_weight"])
        self.assertEqual(nvda["trade_target_weight"], nvda["post_action_weight"])
        self.assertGreaterEqual(nvda["model_target_weight"], nvda["target_weight"])
        self.assertIn("risk_adjusted_expected_return", nvda)
        self.assertEqual(nvda["earnings_event_date"], "2026-06-14")
        self.assertEqual(nvda["earnings_confirmed_or_estimated"], "estimated")
        self.assertTrue(nvda["earnings_confirmation_required"])
        self.assertIn("estimated earnings", nvda["catalyst_clock"])
        research_nvda = next(row for row in research["items"] if row["symbol"] == "NVDA")
        self.assertEqual(research_nvda["external_signal_score"], 20.0)
        self.assertEqual(research_nvda["external_coverage_multiplier"], 0.3333)
        self.assertEqual(research_nvda["external_feed_status"], "limited")
        self.assertEqual(nvda["external_signal_score"], 20.0)
        self.assertEqual(nvda["external_coverage_multiplier"], 0.3333)
        self.assertEqual(nvda["external_provider_ok_ratio"], 0.3333)

    def test_sizing_plan_surfaces_research_only_fresh_candidates(self):
        research = {
            "items": [
                {
                    "symbol": "CEG",
                    "bucket": "power_grid_gas_nuclear",
                    "current_weight": 0.0,
                    "risk_adjusted_expected_return": 22.0,
                    "evidence_quality": 68.0,
                    "drawdown_risk": 32.0,
                    "timing_score": 63.0,
                    "peer_avg_weight": 0.04,
                    "tier1_peer_avg_weight": 0.05,
                    "verdict": "starter",
                    "signal_families": ["manager", "catalyst"],
                    "event_types": ["capex_signal"],
                    "manager_count": 4,
                    "company_add_eligible": False,
                    "company_trim_signal": False,
                }
            ]
        }

        sizing = build_sizing_plan(
            research,
            {"by_symbol": [], "by_bucket": []},
            [],
            [],
            {"max_single_name_weight": 0.15, "max_one_ticket_delta": 0.03},
        )

        self.assertEqual(sizing["action_queue"], [])
        self.assertEqual(sizing["research_queue"][0]["symbol"], "CEG")
        self.assertEqual(sizing["research_queue"][0]["trade_action"], "study")
        self.assertEqual(sizing["research_queue"][0]["recommended_delta_weight"], 0.0)
        self.assertEqual(sizing["research_queue"][0]["recommendation_type"], "research_only")

    def test_strong_13f_without_bottom_up_evidence_cannot_add(self):
        card = {
            "symbol": "TSLA",
            "bucket": "unmapped",
            "score": 70,
            "signal_family_count": 3,
            "signal_families": ["manager", "portfolio_fit", "price_action"],
            "consensus_manager_count": 8,
            "event_score": 2,
            "source_tiers": ["market_news"],
            "top_event_types": [],
        }
        company = {
            "item_count": 1,
            "items": [{
                "symbol": "TSLA",
                "bucket": "unmapped",
                "company_underwriting_score": 35,
                "evidence_quality": 30,
                "data_quality": 42,
                "source_quality": 35,
                "add_eligible": False,
                "trim_signal": False,
                "review_required": True,
                "review_status": "review_required",
                "company_reason": "Weak company evidence despite 13F ownership.",
            }],
        }
        sector = {"item_count": 1, "items": [{"bucket": "unmapped", "sector_setup_score": 50, "target_weight_modifier": 1.0}]}
        features = build_feature_matrix(
            date(2026, 5, 24),
            [card],
            {"by_symbol": [], "by_bucket": []},
            {"focus_managers": [{"status": "ok", "manager_tier": "tier_1", "manager_name": "Altimeter", "positions": [{"symbol": "TSLA", "fund_weight": 0.2}]}]},
            {"regime": "risk-on AI acceleration", "scores": {"ai_momentum": 5}},
            {"TSLA": {"5d": Decimal("3"), "1m": Decimal("7"), "3m": Decimal("12"), "1y": Decimal("20")}},
            [],
            None,
            company,
            sector,
        )
        research = build_research_book(date(2026, 5, 24), features, [card], {"regime": "risk-on AI acceleration"})
        item = research["items"][0]
        sizing = build_sizing_plan(research, {"by_symbol": [], "by_bucket": []}, [], [], {"max_single_name_weight": 0.15})
        target = sizing["targets"][0]

        self.assertNotIn(item["verdict"], {"starter", "buy_more"})
        self.assertEqual(target["model_target_weight"], 0.0)
        self.assertIn("bottom_up_evidence_floor", target["active_constraints"])

    def test_strong_bottom_up_without_13f_can_be_starter(self):
        card = {
            "symbol": "OKLO",
            "bucket": "power_grid_gas_nuclear",
            "score": 20,
            "signal_family_count": 2,
            "signal_families": ["catalyst", "price_action"],
            "consensus_manager_count": 0,
            "event_score": 14,
            "source_tiers": ["primary", "specialist"],
            "top_event_types": ["capex_signal", "contract_win"],
        }
        company = {
            "item_count": 1,
            "items": [{
                "symbol": "OKLO",
                "bucket": "power_grid_gas_nuclear",
                "company_underwriting_score": 82,
                "evidence_quality": 88,
                "data_quality": 74,
                "source_quality": 82,
                "add_eligible": True,
                "trim_signal": False,
                "review_required": False,
                "review_status": "ready",
                "company_reason": "Company evidence clears the bottom-up bar.",
            }],
        }
        sector = {"item_count": 1, "items": [{"bucket": "power_grid_gas_nuclear", "sector_setup_score": 72, "target_weight_modifier": 1.12, "sector_tailwind": True}]}
        features = build_feature_matrix(
            date(2026, 5, 24),
            [card],
            {"by_symbol": [], "by_bucket": []},
            {"focus_managers": []},
            {"regime": "risk-on AI acceleration", "scores": {"ai_momentum": 6}},
            {"OKLO": {"5d": Decimal("2"), "1m": Decimal("8"), "3m": Decimal("15"), "1y": Decimal("30")}},
            [],
            None,
            company,
            sector,
        )
        research = build_research_book(date(2026, 5, 24), features, [card], {"regime": "risk-on AI acceleration"})

        self.assertEqual(research["items"][0]["verdict"], "starter")
        self.assertTrue(research["items"][0]["company_add_eligible"])

    def test_sector_headwind_caps_company_add_target(self):
        base_item = {
            "symbol": "CRWV",
            "bucket": "neocloud_datacenters",
            "current_weight": 0.02,
            "risk_adjusted_expected_return": 35,
            "probability_weighted_return": 40,
            "evidence_quality": 82,
            "drawdown_risk": 45,
            "timing_score": 70,
            "peer_avg_weight": 0.0,
            "tier1_peer_avg_weight": 0.0,
            "verdict": "buy_more",
            "company_add_eligible": True,
            "company_underwriting_score": 80,
            "sector_setup_score": 40,
            "sector_headwind": True,
            "signal_families": ["catalyst"],
            "event_types": [],
        }
        headwind = target_for_item(base_item, {}, {}, {"neocloud_datacenters": 0.05}, {"max_single_name_weight": 0.15, "max_bucket_weight": 0.45, "max_one_ticket_delta": 0.03})
        tailwind_item = dict(base_item, sector_setup_score=72, sector_headwind=False, sector_tailwind=True)
        tailwind = target_for_item(tailwind_item, {}, {}, {"neocloud_datacenters": 0.05}, {"max_single_name_weight": 0.15, "max_bucket_weight": 0.45, "max_one_ticket_delta": 0.03})

        self.assertLess(headwind["model_target_weight"], tailwind["model_target_weight"])
        self.assertIn("sector_headwind", headwind["active_constraints"])

    def test_company_deterioration_can_trigger_trim_despite_manager_signal(self):
        card = {
            "symbol": "CRWV",
            "bucket": "neocloud_datacenters",
            "score": 60,
            "signal_family_count": 3,
            "signal_families": ["manager", "catalyst", "portfolio_fit"],
            "consensus_manager_count": 5,
            "event_score": 8,
            "source_tiers": ["market_news"],
            "top_event_types": ["financing_risk"],
        }
        company = {
            "item_count": 1,
            "items": [{
                "symbol": "CRWV",
                "bucket": "neocloud_datacenters",
                "company_underwriting_score": 34,
                "evidence_quality": 62,
                "data_quality": 65,
                "source_quality": 58,
                "add_eligible": False,
                "trim_signal": True,
                "review_required": True,
                "review_status": "deteriorating",
                "company_reason": "Financing deterioration overrides manager confirmation.",
            }],
        }
        sector = {"item_count": 1, "items": [{"bucket": "neocloud_datacenters", "sector_setup_score": 46, "target_weight_modifier": 0.78, "sector_headwind": True}]}
        features = build_feature_matrix(
            date(2026, 5, 24),
            [card],
            {"by_symbol": [{"symbol": "CRWV", "bucket": "neocloud_datacenters", "weight": 0.08}], "by_bucket": [{"bucket": "neocloud_datacenters", "weight": 0.08}]},
            {"focus_managers": [{"status": "ok", "manager_tier": "tier_1", "manager_name": "Altimeter", "positions": [{"symbol": "CRWV", "fund_weight": 0.09}]}]},
            {"regime": "risk-on AI acceleration", "scores": {"ai_momentum": 5}},
            {"CRWV": {"5d": Decimal("1"), "1m": Decimal("5"), "3m": Decimal("12"), "1y": Decimal("50")}},
            [],
            None,
            company,
            sector,
        )
        research = build_research_book(date(2026, 5, 24), features, [card], {"regime": "risk-on AI acceleration"})
        item = research["items"][0]
        sizing = build_sizing_plan(
            research,
            {"by_symbol": [{"symbol": "CRWV", "bucket": "neocloud_datacenters", "weight": 0.08}], "by_bucket": [{"bucket": "neocloud_datacenters", "weight": 0.08}]},
            [],
            [],
            {"max_single_name_weight": 0.15, "max_one_ticket_delta": 0.03},
        )
        target = sizing["targets"][0]

        self.assertEqual(item["verdict"], "trim")
        self.assertLess(target["recommended_delta_weight"], 0)
        self.assertIn("company_deterioration", target["active_constraints"])

    def test_positive_catalyst_gap_blocks_trim_funding(self):
        research = {
            "items": [
                {
                    "symbol": "MU",
                    "bucket": "semis_networking_hbm",
                    "current_weight": 0.114,
                    "risk_adjusted_expected_return": 0,
                    "probability_weighted_return": 1.8,
                    "evidence_quality": 78,
                    "drawdown_risk": 93,
                    "timing_score": 45,
                    "peer_avg_weight": 0.033,
                    "tier1_peer_avg_weight": 0.002,
                    "verdict": "trim",
                    "company_add_eligible": False,
                    "company_trim_signal": False,
                    "company_underwriting_score": 55,
                    "sector_setup_score": 54,
                    "signal_families": ["manager", "catalyst", "portfolio_fit", "price_action"],
                    "event_types": ["supply_constraint", "earnings_revision", "capex_signal"],
                    "price_return_1d": 16.0,
                    "price_return_5d": 19.0,
                }
            ]
        }

        sizing = build_sizing_plan(
            research,
            {
                "by_symbol": [{"symbol": "MU", "bucket": "semis_networking_hbm", "weight": 0.114}],
                "by_bucket": [{"bucket": "semis_networking_hbm", "weight": 0.114}],
            },
            [],
            [],
            {"max_single_name_weight": 0.15, "max_one_ticket_delta": 0.03},
        )
        target = sizing["targets"][0]

        self.assertEqual(target["trade_action"], "hold")
        self.assertEqual(target["recommended_delta_weight"], 0.0)
        self.assertLess(target["model_target_weight"], target["current_weight"])
        self.assertEqual(target["target_weight"], target["current_weight"])
        self.assertTrue(target["positive_catalyst_gap_trim_block"])
        self.assertIn("positive_catalyst_gap_trim_block", target["active_constraints"])
        self.assertEqual(sizing["action_queue"][0]["symbol"], "MU")
        self.assertIn("positive catalyst gap blocks trim", sizing["action_queue"][0]["action"])

    def test_sizing_can_use_capped_cash_reserve_for_target_pool(self):
        features = build_feature_matrix(
            date(2026, 5, 24),
            self.sample_cards(),
            {
                "equity_weight": 0.80,
                "cash_weight": 0.20,
                "by_symbol": [
                    {"symbol": "NVDA", "bucket": "semis_networking_hbm", "weight": 0.08},
                    {"symbol": "CASH", "bucket": "cash_reserves", "weight": 0.20, "asset_class": "cash", "is_cash": True},
                ],
                "by_bucket": [
                    {"bucket": "semis_networking_hbm", "weight": 0.08},
                    {"bucket": "cash_reserves", "weight": 0.20},
                ],
            },
            {"focus_managers": []},
            {"regime": "risk-on AI acceleration", "scores": {"ai_momentum": 3}},
            {"NVDA": {"5d": Decimal("2"), "1m": Decimal("8"), "3m": Decimal("18")}},
            [],
        )
        research = build_research_book(date(2026, 5, 24), features, self.sample_cards(), {"regime": "risk-on AI acceleration"})
        sizing = build_sizing_plan(
            research,
            {
                "equity_weight": 0.80,
                "cash_weight": 0.20,
                "by_symbol": [
                    {"symbol": "NVDA", "bucket": "semis_networking_hbm", "weight": 0.08},
                    {"symbol": "CASH", "bucket": "cash_reserves", "weight": 0.20, "asset_class": "cash", "is_cash": True},
                ],
                "by_bucket": [
                    {"bucket": "semis_networking_hbm", "weight": 0.08},
                    {"bucket": "cash_reserves", "weight": 0.20},
                ],
            },
            [{"symbol": "NVDA", "weight": 0.08, "five_day_pct": 2, "contribution_pct": 0.16}],
            [],
            {"max_single_name_weight": 0.15, "max_one_ticket_delta": 0.03},
        )

        self.assertEqual(sizing["starting_equity_weight"], 0.8)
        self.assertEqual(sizing["target_total_weight"], 0.82)
        self.assertEqual(sizing["cash_reserve_weight"], 0.2)
        self.assertEqual(sizing["cash_deployable_weight"], 0.02)
        self.assertLessEqual(sizing["post_trade_cash_weight"], 0.2)
        self.assertLessEqual(sum(row["model_target_weight"] for row in sizing["targets"]), 0.82)
        self.assertTrue(
            all(
                row["model_target_weight"] <= row["max_allowed_weight"]
                for row in sizing["targets"]
            )
        )
        self.assertTrue(all(row["symbol"] != "CASH" for row in sizing["targets"]))

    def test_training_examples_and_outcome_diagnostics_are_ml_ready(self):
        research = {
            "items": [
                {
                    "symbol": "NVDA",
                    "bucket": "semis_networking_hbm",
                    "model_policy_version": "policy",
                    "risk_adjusted_expected_return": 18,
                    "probability_weighted_return": 21,
                    "evidence_quality": 70,
                    "drawdown_risk": 40,
                    "timing_score": 65,
                }
            ]
        }
        features = {
            "rows": [
                {
                    "symbol": "NVDA",
                    "signal_families": ["manager"],
                    "event_types": ["capex_signal"],
                    "external_signal_score": 20.0,
                    "coverage_adjusted_external_signal_score": 5.0,
                    "external_coverage_multiplier": 0.25,
                    "external_feed_status": "limited",
                    "external_provider_count": 6,
                    "external_provider_ok_count": 1,
                    "external_provider_ok_ratio": 0.1667,
                    "external_signal_count": 4,
                    "external_source_count": 3,
                }
            ]
        }
        examples = build_training_examples(
            date(2026, 5, 24),
            "premarket",
            [
                {
                    "ticket_id": "t1",
                    "symbol": "NVDA",
                    "trade_action": "add",
                    "current_weight": 0.08,
                    "recommended_delta_weight": 0.01,
                    "target_weight": 0.09,
                    "earnings_days_until": 21,
                    "earnings_event_date": "2026-06-14",
                    "earnings_event_source": "nasdaq_earnings_calendar",
                    "earnings_confirmed_or_estimated": "estimated",
                    "earnings_risk_window": "clear",
                    "earnings_confirmation_required": True,
                    "approval_required": True,
                    "approval_gate_status": "blocked_until_confirmation",
                    "approval_open_check_count": 2,
                    "approval_checks": [
                        {"check": "approval_only_no_live_order", "status": "passed"},
                        {"check": "earnings_date_confirmed", "status": "pending"},
                        {"check": "external_feed_reliability_reviewed", "status": "pending"},
                    ],
                }
            ],
            research,
            features,
        )
        diagnostics = build_outcome_diagnostics(date(2026, 5, 24), examples)

        self.assertEqual(examples[0]["external_signal_score"], 20.0)
        self.assertEqual(examples[0]["coverage_adjusted_external_signal_score"], 5.0)
        self.assertEqual(examples[0]["external_coverage_multiplier"], 0.25)
        self.assertEqual(examples[0]["external_feed_status"], "limited")
        self.assertEqual(examples[0]["external_provider_count"], 6)
        self.assertEqual(examples[0]["external_provider_ok_count"], 1)
        self.assertEqual(examples[0]["external_provider_ok_ratio"], 0.1667)
        self.assertEqual(examples[0]["external_signal_count"], 4)
        self.assertEqual(examples[0]["external_source_count"], 3)
        self.assertEqual(examples[0]["earnings_days_until"], 21)
        self.assertEqual(examples[0]["earnings_event_date"], "2026-06-14")
        self.assertEqual(examples[0]["earnings_confirmed_or_estimated"], "estimated")
        self.assertTrue(examples[0]["earnings_confirmation_required"])
        self.assertTrue(examples[0]["approval_required"])
        self.assertEqual(examples[0]["approval_gate_status"], "blocked_until_confirmation")
        self.assertEqual(examples[0]["approval_open_check_count"], 2)
        self.assertEqual(
            examples[0]["approval_blocking_checks"],
            ["earnings_date_confirmed", "external_feed_reliability_reviewed"],
        )
        self.assertEqual(examples[0]["forward_return_labels"]["5d"], None)
        self.assertEqual(examples[0]["forward_return_labels"]["3m"], None)
        self.assertEqual(diagnostics["status"], "awaiting_forward_returns")
        self.assertEqual(diagnostics["current_training_example_count"], 1)
        self.assertEqual(diagnostics["pending_outcome_count"], 5)
        self.assertFalse(diagnostics["label_maturity"]["learning_ready"])

    def test_outcome_diagnostics_surface_label_maturity_from_backtest(self):
        history = [
            {
                "symbol": "NVDA",
                "horizon": "5d",
                "forward_return_pct": 4,
                "risk_adjusted_expected_return": 10,
                "signal_families": ["manager"],
                "trade_action": "add",
                "bucket": "semis_networking_hbm",
            },
            {
                "symbol": "NVDA",
                "horizon": "1m",
                "forward_return_pct": 12,
                "risk_adjusted_expected_return": 10,
                "signal_families": ["manager"],
                "trade_action": "add",
                "bucket": "semis_networking_hbm",
            },
        ]
        backtest = {
            "outcome_count": 7,
            "pending_outcome_count": 4,
            "missing_price_count": 1,
            "horizons": [
                {"horizon": "5d", "completed_count": 1, "pending_count": 0, "missing_price_count": 0},
                {"horizon": "1m", "completed_count": 1, "pending_count": 1, "missing_price_count": 0},
                {"horizon": "3m", "completed_count": 0, "pending_count": 1, "missing_price_count": 0},
            ],
            "outcomes": [
                {
                    "symbol": "NVDA",
                    "horizon": "5d",
                    "status": "pending",
                    "due_date": "2026-05-31",
                    "external_feed_status": "limited",
                    "external_coverage_multiplier": 0.25,
                    "approval_blocker_bucket": "review_required",
                    "approval_data_friction_bucket": "external_review",
                },
                {
                    "symbol": "NVDA",
                    "horizon": "1m",
                    "status": "pending",
                    "due_date": "2026-06-24",
                    "external_feed_status": "limited",
                    "external_coverage_multiplier": 0.25,
                    "approval_blocker_bucket": "review_required",
                    "approval_data_friction_bucket": "external_review",
                },
                {
                    "symbol": "AMD",
                    "horizon": "1m",
                    "status": "pending",
                    "due_date": "2026-06-24",
                    "approval_blocker_bucket": "no_approval_context",
                    "approval_data_friction_bucket": "clear",
                },
                {
                    "symbol": "NVDA",
                    "horizon": "3m",
                    "status": "pending",
                    "due_date": "2026-08-24",
                    "external_feed_status": "limited",
                    "external_coverage_multiplier": 0.25,
                    "approval_blocker_bucket": "blocked_until_confirmation",
                    "approval_data_friction_bucket": "earnings_and_external_review",
                },
            ],
        }

        diagnostics = build_outcome_diagnostics(date(2026, 5, 24), [], history, backtest)

        self.assertEqual(diagnostics["total_outcome_count"], 7)
        self.assertEqual(diagnostics["completed_outcome_count"], 2)
        self.assertEqual(diagnostics["pending_outcome_count"], 4)
        self.assertEqual(diagnostics["missing_price_count"], 1)
        self.assertEqual(diagnostics["horizon_label_counts"][0]["horizon"], "5d")
        self.assertEqual(diagnostics["label_maturity"]["completed_long_horizon_count"], 1)
        self.assertEqual(diagnostics["label_maturity"]["short_horizon_completed_count"], 1)
        self.assertEqual(diagnostics["label_maturity"]["additional_long_horizon_needed"], 19)
        self.assertEqual(diagnostics["learning_readiness_projection"]["projected_long_horizon_count_30d"], 1)
        self.assertEqual(diagnostics["learning_readiness_projection"]["projected_additional_needed_30d"], 19)
        self.assertEqual(diagnostics["learning_readiness_projection"]["next_learning_label_due_date"], "2026-06-24")
        self.assertEqual(diagnostics["learning_readiness_projection"]["next_learning_label_due_count"], 2)
        self.assertEqual(diagnostics["learning_readiness_projection"]["projected_long_horizon_count_next_learning_label"], 3)
        self.assertEqual(diagnostics["learning_readiness_projection"]["projected_additional_needed_next_learning_label"], 17)
        self.assertFalse(diagnostics["learning_readiness_projection"]["learning_ready_after_30d_due_window"])
        self.assertFalse(diagnostics["learning_readiness_projection"]["learning_ready_after_next_learning_label"])
        self.assertEqual(diagnostics["pending_label_schedule"]["next_label_due_date"], "2026-05-31")
        self.assertEqual(diagnostics["pending_label_schedule"]["next_learning_label_due_date"], "2026-06-24")
        self.assertEqual(diagnostics["pending_label_schedule"]["next_label"]["days_until_due"], 7)
        self.assertEqual(diagnostics["pending_label_schedule"]["next_learning_label"]["days_until_due"], 31)
        self.assertEqual(diagnostics["pending_label_schedule"]["next_learning_label"]["due_count"], 2)
        self.assertEqual(diagnostics["pending_label_schedule"]["overdue_label_count"], 0)
        self.assertEqual(diagnostics["pending_label_schedule"]["due_window_counts"]["due_next_7d"], 1)
        self.assertEqual(diagnostics["pending_label_schedule"]["due_window_counts"]["due_next_30d"], 1)
        self.assertEqual(diagnostics["pending_label_schedule"]["learning_due_window_counts"]["due_next_7d"], 0)
        self.assertEqual(diagnostics["pending_label_schedule"]["learning_due_window_counts"]["due_next_30d"], 0)
        external_projection = diagnostics["external_learning_readiness_projection"]
        self.assertEqual(external_projection["pending_external_learning_label_count"], 2)
        self.assertEqual(external_projection["next_external_learning_label_due_date"], "2026-06-24")
        self.assertEqual(external_projection["next_external_learning_label_due_count"], 1)
        self.assertEqual(external_projection["projected_external_long_horizon_count_all_scheduled"], 2)
        self.assertEqual(external_projection["projected_external_additional_needed_all_scheduled"], 18)
        self.assertEqual(external_projection["pending_external_fast_label_count"], 1)
        self.assertEqual(external_projection["next_external_fast_label_due_date"], "2026-05-31")
        self.assertEqual(external_projection["next_external_fast_label_due_count"], 1)
        self.assertEqual(external_projection["external_fast_labels_due_next_7d"], 1)
        self.assertFalse(external_projection["external_learning_ready_with_scheduled_pending_labels"])
        approval_projection = diagnostics["approval_learning_readiness_projection"]
        self.assertEqual(approval_projection["pending_approval_label_count"], 3)
        self.assertEqual(approval_projection["pending_approval_learning_label_count"], 2)
        self.assertEqual(approval_projection["pending_approval_fast_label_count"], 1)
        self.assertEqual(approval_projection["pending_approval_blocker_bucket_count"], 2)
        self.assertEqual(approval_projection["primary_approval_blocker_bucket"], "review_required")
        self.assertEqual(approval_projection["next_approval_label_due_date"], "2026-05-31")
        self.assertEqual(approval_projection["next_approval_label_due_count"], 1)
        self.assertEqual(approval_projection["next_approval_learning_label_due_date"], "2026-06-24")
        self.assertEqual(approval_projection["next_approval_learning_label_due_count"], 1)
        self.assertEqual(approval_projection["approval_labels_due_next_7d"], 1)
        self.assertEqual(approval_projection["approval_learning_labels_due_next_30d"], 0)
        friction_projection = diagnostics["approval_data_friction_learning_readiness_projection"]
        self.assertEqual(friction_projection["pending_approval_data_friction_label_count"], 3)
        self.assertEqual(friction_projection["pending_approval_data_friction_learning_label_count"], 2)
        self.assertEqual(friction_projection["pending_approval_data_friction_fast_label_count"], 1)
        self.assertEqual(friction_projection["pending_approval_data_friction_bucket_count"], 2)
        self.assertEqual(friction_projection["primary_approval_data_friction_bucket"], "external_review")
        self.assertEqual(friction_projection["next_approval_data_friction_label_due_date"], "2026-05-31")
        self.assertEqual(friction_projection["next_approval_data_friction_label_due_count"], 1)
        self.assertEqual(friction_projection["next_approval_data_friction_learning_label_due_date"], "2026-06-24")
        self.assertEqual(friction_projection["next_approval_data_friction_learning_label_due_count"], 1)
        self.assertEqual(friction_projection["approval_data_friction_labels_due_next_7d"], 1)
        self.assertEqual(friction_projection["approval_data_friction_learning_labels_due_next_30d"], 0)
        self.assertEqual(diagnostics["calibration"]["mean_error"], -2.0)
        self.assertEqual(diagnostics["calibration"]["mean_absolute_error"], 4.0)
        self.assertEqual(diagnostics["calibration"]["underprediction_count"], 1)
        self.assertEqual(diagnostics["calibration"]["overprediction_count"], 1)
        self.assertFalse(diagnostics["calibration"]["calibration_ready"])
        self.assertEqual(diagnostics["calibration"]["minimum_calibration_samples"], 20)
        self.assertEqual(diagnostics["calibration"]["additional_samples_needed"], 18)

    def test_learning_projection_estimates_ready_date_from_cumulative_pending_labels(self):
        rows = (
            [{"symbol": f"A{index}", "horizon": "1m", "status": "pending", "due_date": "2026-06-24"} for index in range(3)]
            + [{"symbol": f"B{index}", "horizon": "3m", "status": "pending", "due_date": "2026-07-24"} for index in range(4)]
            + [{"symbol": f"C{index}", "horizon": "6m", "status": "pending", "due_date": "2026-08-24"} for index in range(6)]
        )
        schedule = pending_label_schedule({"outcomes": rows}, date(2026, 5, 24))

        projection = learning_readiness_projection(
            {"completed_long_horizon_count": 10, "minimum_long_horizon_required": 20},
            schedule,
        )

        self.assertEqual(schedule["learning_due_dates"][0]["cumulative_due_count"], 3)
        self.assertEqual(projection["estimated_learning_ready_date"], "2026-08-24")
        self.assertEqual(projection["estimated_learning_ready_projected_count"], 23)
        self.assertTrue(projection["learning_ready_with_scheduled_pending_labels"])


if __name__ == "__main__":
    unittest.main()
