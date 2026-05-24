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


if __name__ == "__main__":
    unittest.main()
