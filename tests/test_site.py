import unittest

from invest.site import build_public_moves, sanitize_payload


class SiteTests(unittest.TestCase):
    def test_public_payload_redacts_broker_data_and_adds_moves(self):
        payload = {
            "product": {"name": "Old", "domain": "old.example"},
            "positions": {"NVDA": 1000},
            "transactions": [{"symbol": "NVDA"}],
            "portfolio": {
                "position_count": 1,
                "symbol_count": 1,
                "gross_exposure": 1000,
                "net_exposure": 1000,
                "by_bucket": [{"bucket": "semis_networking_hbm", "weight": 1.0, "market_value": 1000}],
                "by_symbol": [{"symbol": "NVDA", "bucket": "semis_networking_hbm", "weight": 1.0, "market_value": 1000}],
            },
            "decision_cards": [
                {
                    "symbol": "NVDA",
                    "score": 50,
                    "bucket": "semis_networking_hbm",
                    "candidate": "research hold/add-on-dip candidate",
                    "portfolio_value": 1000,
                    "consensus_manager_count": 4,
                    "news_count": 2,
                    "put_value": 0,
                    "call_value": 0,
                    "consensus_value": 100000000,
                    "counterargument": "Risk.",
                    "falsifier": "Wrong.",
                }
            ],
            "macro": {"regime": "mixed macro tape"},
            "ideas": [],
            "manager_radar": {
                "focus_managers": [
                    {
                        "manager_key": "altimeter",
                        "manager_name": "Altimeter",
                        "manager_tier": "tier_1",
                        "manager_group": "AI Thesis Core",
                        "total_common_value": 1000000,
                        "symbol_coverage_pct": 100,
                        "top_positions": [
                            {
                                "rank": 1,
                                "symbol": "NVDA",
                                "issuer": "NVIDIA CORP",
                                "bucket": "semis_networking_hbm",
                                "fund_weight": 1.0,
                                "portfolio_weight": 1.0,
                                "value": 1000000,
                            }
                        ],
                        "positions": [
                            {
                                "rank": 1,
                                "symbol": "NVDA",
                                "issuer": "NVIDIA CORP",
                                "bucket": "semis_networking_hbm",
                                "fund_weight": 1.0,
                                "portfolio_weight": 1.0,
                                "value": 1000000,
                                "shares": 10,
                            }
                        ],
                    }
                ]
            },
        }

        public = sanitize_payload(payload)

        self.assertTrue(public["private_data_redacted"])
        self.assertEqual(public["positions"], {})
        self.assertEqual(public["transactions"], [])
        self.assertEqual(public["portfolio"]["value_basis"], "weights_only")
        self.assertEqual(public["portfolio"]["display_name"], "Geoffrey Woo Portfolio")
        self.assertEqual(public["portfolio"]["by_symbol"][0]["weight"], 1.0)
        self.assertNotIn("market_value", public["portfolio"]["by_symbol"][0])
        self.assertNotIn("portfolio_value", public["decision_cards"][0])
        self.assertEqual(public["decision_cards"][0]["portfolio_weight"], 1.0)
        self.assertNotIn("total_common_value", public["manager_radar"]["focus_managers"][0])
        self.assertNotIn("value", public["manager_radar"]["focus_managers"][0]["top_positions"][0])
        self.assertEqual(public["manager_radar"]["focus_managers"][0]["positions"][0]["rank"], 1)
        self.assertNotIn("value", public["manager_radar"]["focus_managers"][0]["positions"][0])
        self.assertNotIn("shares", public["manager_radar"]["focus_managers"][0]["positions"][0])
        self.assertEqual(public["manager_radar"]["focus_manager_groups"][0]["key"], "tier_1")
        self.assertNotIn(
            "value",
            public["manager_radar"]["focus_manager_groups"][0]["managers"][0]["top_positions"][0],
        )
        self.assertEqual(public["product"]["domain"], "alloiq.com")
        self.assertEqual(public["recommended_moves"][0]["action"], "Core position review")

    def test_public_moves_call_out_put_heavy_names(self):
        moves = build_public_moves(
            [
                {
                    "symbol": "AMD",
                    "score": 40,
                    "bucket": "semis_networking_hbm",
                    "portfolio_weight": 0,
                    "consensus_manager_count": 3,
                    "news_count": 0,
                    "put_value": 100000000,
                    "call_value": 0,
                    "counterargument": "Risk.",
                    "falsifier": "Wrong.",
                }
            ],
            {"regime": "risk-on AI acceleration"},
        )

        self.assertEqual(moves[0]["action"], "Hedge watch")
        self.assertEqual(moves[0]["posture"], "Cautious")

    def test_public_moves_use_portfolio_weights_for_owned_hedge(self):
        moves = build_public_moves(
            [
                {
                    "symbol": "NVDA",
                    "score": 40,
                    "bucket": "semis_networking_hbm",
                    "portfolio_weight": 0.05,
                    "consensus_manager_count": 3,
                    "news_count": 0,
                    "put_value": 100000000,
                    "call_value": 0,
                    "counterargument": "Risk.",
                    "falsifier": "Wrong.",
                }
            ],
            {"regime": "risk-on AI acceleration"},
            {"by_bucket": [{"bucket": "semis_networking_hbm", "weight": 0.3}]},
        )

        self.assertEqual(moves[0]["action"], "Hedge existing exposure")
        self.assertEqual(moves[0]["portfolio_weight"], 0.05)


if __name__ == "__main__":
    unittest.main()
