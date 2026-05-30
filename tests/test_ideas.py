import unittest

from invest.ideas import build_idea_book


class IdeaSignalTests(unittest.TestCase):
    def test_idea_book_requires_multiple_signal_families_for_ordinary_card(self):
        cards = [
            {
                "symbol": "NVDA",
                "bucket": "semis_networking_hbm",
                "score": 37,
                "signal_families": ["manager"],
                "consensus_manager_count": 4,
                "news_count": 0,
                "counterargument": "Risk.",
                "falsifier": "Wrong.",
            },
            {
                "symbol": "AMZN",
                "bucket": "frontier_ai_platforms",
                "score": 39,
                "signal_families": ["manager", "catalyst"],
                "top_event_types": ["capex_signal"],
                "consensus_manager_count": 3,
                "news_count": 2,
                "counterargument": "Risk.",
                "falsifier": "Wrong.",
            },
        ]

        ideas = build_idea_book(cards, {}, {"by_symbol": []}, {"regime": "mixed macro tape"})

        self.assertEqual([idea["symbol"] for idea in ideas], ["AMZN"])
        self.assertEqual(ideas[0]["type"], "catalyst-confirmed white-space research")

    def test_idea_book_reserves_room_for_manager_discovered_fresh_ideas(self):
        cards = [
            {
                "symbol": "NVDA",
                "bucket": "semis_networking_hbm",
                "score": 70,
                "signal_families": ["manager", "catalyst", "price_action"],
                "top_event_types": ["capex_signal"],
                "consensus_manager_count": 5,
                "portfolio_value": 1_000_000,
                "counterargument": "Risk.",
                "falsifier": "Wrong.",
            },
            {
                "symbol": "GOOGL",
                "bucket": "frontier_ai_platforms",
                "score": 66,
                "signal_families": ["manager", "portfolio_fit"],
                "consensus_manager_count": 4,
                "portfolio_value": 1_000_000,
                "counterargument": "Risk.",
                "falsifier": "Wrong.",
            },
            {
                "symbol": "CEG",
                "bucket": "power_grid_gas_nuclear",
                "score": 4,
                "signal_families": [],
                "consensus_manager_count": 0,
                "portfolio_value": 0,
                "counterargument": "Risk.",
                "falsifier": "Wrong.",
            },
        ]
        manager_radar = {
            "top_adds": [
                {"symbol": "CEG", "bucket": "power_grid_gas_nuclear", "manager_count": 4, "delta_value": 800_000_000}
            ],
            "top_consensus": [
                {"symbol": "VST", "bucket": "power_grid_gas_nuclear", "common_manager_count": 4, "common_value": 2_000_000_000}
            ],
        }

        ideas = build_idea_book(
            cards,
            manager_radar,
            {"by_symbol": [{"symbol": "NVDA"}, {"symbol": "GOOGL"}]},
            {"regime": "risk-on AI acceleration"},
        )

        symbols = [idea["symbol"] for idea in ideas]
        self.assertIn("CEG", symbols)
        self.assertIn("VST", symbols)
        self.assertLess(symbols.index("CEG"), symbols.index("NVDA"))
        fresh = [idea for idea in ideas if idea.get("freshness") == "fresh_research"]
        self.assertGreaterEqual(len(fresh), 2)
        self.assertTrue(all(idea.get("exploration_reason") for idea in fresh))


if __name__ == "__main__":
    unittest.main()
