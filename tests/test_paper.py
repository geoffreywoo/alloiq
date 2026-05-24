from datetime import date
import unittest

from invest.paper import build_paper_portfolio


class PaperTests(unittest.TestCase):
    def test_every_ticket_gets_paper_trade_without_execution(self):
        paper = build_paper_portfolio(
            date(2026, 5, 24),
            "postmarket",
            {"by_symbol": [{"symbol": "NVDA", "bucket": "semis_networking_hbm", "weight": 0.08}]},
            [{"ticket_id": "t1", "symbol": "NVDA", "trade_action": "add", "current_weight": 0.08, "recommended_delta_weight": 0.01, "target_weight": 0.09}],
            [{"symbol": "NVDA", "last_price": 120}],
        )

        self.assertEqual(paper["metrics"]["paper_trade_count"], 1)
        self.assertEqual(paper["paper_trades"][0]["status"], "filled_proxy")
        self.assertEqual(paper["paper_trades"][0]["proxy_fill_price"], 120)
        self.assertEqual(paper["live_order_execution"], "disabled")


if __name__ == "__main__":
    unittest.main()
