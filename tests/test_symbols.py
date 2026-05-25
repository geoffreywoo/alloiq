from decimal import Decimal
import unittest

from invest.thesis import build_decision_cards


class SymbolProxyTests(unittest.TestCase):
    def test_goog_and_googl_proxy_decision_card_inputs(self):
        cards = build_decision_cards(
            ["GOOGL"],
            {"GOOGL": "frontier_ai_platforms"},
            {"GOOG": Decimal("100")},
            {"GOOG": Decimal("50")},
            {},
            {"GOOG": {"five_day_pct": Decimal("3"), "last": Decimal("150")}},
            {
                "GOOG": {
                    "common_value": 75,
                    "common_manager_count": 2,
                    "common_managers": ["A", "B"],
                }
            },
            {},
        )

        card = cards[0]
        self.assertEqual(card["symbol"], "GOOGL")
        self.assertEqual(card["portfolio_value"], 100.0)
        self.assertEqual(card["filing_value"], 50.0)
        self.assertEqual(card["consensus_value"], 75.0)
        self.assertEqual(card["consensus_manager_count"], 2)
        self.assertEqual(card["five_day_pct"], 3.0)


if __name__ == "__main__":
    unittest.main()
