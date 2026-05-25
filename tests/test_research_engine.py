from datetime import date
from decimal import Decimal
import unittest

from invest.features import build_feature_matrix
from invest.outcomes import build_outcome_diagnostics, build_training_examples
from invest.research import build_research_book
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
            {"NVDA": {"5d": Decimal("5"), "1m": Decimal("12"), "3m": Decimal("30"), "ytd": Decimal("60"), "1y": Decimal("100")}},
            [{"symbol": "NVDA", "days_until": 21}],
        )

        self.assertEqual(features["feature_count"], 2)
        nvda = next(row for row in features["rows"] if row["symbol"] == "NVDA")
        self.assertEqual(nvda["current_weight"], 0.14)
        self.assertEqual(nvda["tier1_manager_count"], 1)
        self.assertGreater(nvda["evidence_quality"], 60)
        self.assertEqual(nvda["price_return_3m"], 30.0)

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
            [],
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
        self.assertAlmostEqual(sum(row["model_target_weight"] for row in sizing["targets"]), 0.82, places=5)
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
        features = {"rows": [{"symbol": "NVDA", "signal_families": ["manager"], "event_types": ["capex_signal"]}]}
        examples = build_training_examples(
            date(2026, 5, 24),
            "premarket",
            [{"ticket_id": "t1", "symbol": "NVDA", "trade_action": "add", "current_weight": 0.08, "recommended_delta_weight": 0.01, "target_weight": 0.09}],
            research,
            features,
        )
        diagnostics = build_outcome_diagnostics(date(2026, 5, 24), examples)

        self.assertEqual(examples[0]["forward_return_labels"]["5d"], None)
        self.assertEqual(examples[0]["forward_return_labels"]["3m"], None)
        self.assertEqual(diagnostics["status"], "awaiting_forward_returns")
        self.assertEqual(diagnostics["current_training_example_count"], 1)


if __name__ == "__main__":
    unittest.main()
