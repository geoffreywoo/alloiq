from datetime import date
from decimal import Decimal
from pathlib import Path
import sqlite3
import unittest

from invest.config import AppConfig
from invest.db import init_db, insert_positions
from invest.models import Position
from invest.portfolio import build_portfolio_exposure


class PortfolioTests(unittest.TestCase):
    def test_manual_positions_are_combined_with_database_positions(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            init_db(conn)
            insert_positions(
                conn,
                [
                    Position(
                        broker="ibkr",
                        account="taxable",
                        as_of=date(2026, 5, 23),
                        symbol="AMZN",
                        quantity=Decimal("10"),
                        market_value=Decimal("1000"),
                    )
                ],
            )
            config = AppConfig(
                path=Path("config/invest.toml"),
                data={
                    "portfolio": {
                        "manual_positions": [
                            {"broker": "external", "account": "sample-sleeve", "symbol": "NVDA", "quantity": 100},
                            {"broker": "external", "account": "sample-sleeve", "symbol": "TSM", "quantity": 50},
                            {"broker": "external", "account": "sample-sleeve", "symbol": "AMZN", "quantity": 25},
                        ]
                    },
                    "thesis_buckets": [
                        {"key": "semis_networking_hbm", "symbols": ["NVDA", "TSM"]},
                        {"key": "frontier_ai_platforms", "symbols": ["AMZN"]},
                    ],
                },
            )
            prices = {
                "NVDA": {"last": Decimal("2")},
                "TSM": {"last": Decimal("5")},
                "AMZN": {"last": Decimal("10")},
            }

            portfolio = build_portfolio_exposure(conn, config, prices=prices, as_of=date(2026, 5, 24))
            by_symbol = {row["symbol"]: row for row in portfolio["by_symbol"]}
            by_broker = {row["broker"]: row for row in portfolio["by_broker"]}

            self.assertEqual(portfolio["symbol_count"], 3)
            self.assertEqual(portfolio["position_count"], 4)
            self.assertAlmostEqual(by_symbol["NVDA"]["market_value"], 200.0)
            self.assertAlmostEqual(by_symbol["TSM"]["market_value"], 250.0)
            self.assertAlmostEqual(by_symbol["AMZN"]["market_value"], 1250.0)
            self.assertAlmostEqual(by_broker["external"]["market_value"], 700.0)
            self.assertAlmostEqual(by_broker["ibkr"]["market_value"], 1000.0)
            self.assertAlmostEqual(by_symbol["AMZN"]["weight"], 1250.0 / 1700.0)
        finally:
            conn.close()

    def test_cash_reserve_weight_changes_total_portfolio_denominator(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            init_db(conn)
            insert_positions(
                conn,
                [
                    Position(
                        broker="ibkr",
                        account="taxable",
                        as_of=date(2026, 5, 23),
                        symbol="NVDA",
                        quantity=Decimal("10"),
                        market_value=Decimal("800"),
                    )
                ],
            )
            config = AppConfig(
                path=Path("config/invest.toml"),
                data={
                    "portfolio": {
                        "cash_reserves": [
                            {"currency": "USD", "weight": 0.20, "description": "Cash reserves"},
                        ]
                    },
                    "thesis_buckets": [
                        {"key": "semis_networking_hbm", "symbols": ["NVDA"]},
                    ],
                },
            )

            portfolio = build_portfolio_exposure(conn, config, prices={}, as_of=date(2026, 5, 24))
            by_symbol = {row["symbol"]: row for row in portfolio["by_symbol"]}

            self.assertAlmostEqual(portfolio["equity_weight"], 0.8)
            self.assertAlmostEqual(portfolio["cash_weight"], 0.2)
            self.assertAlmostEqual(by_symbol["NVDA"]["weight"], 0.8)
            self.assertAlmostEqual(by_symbol["NVDA"]["ex_cash_weight"], 1.0)
            self.assertAlmostEqual(by_symbol["NVDA"]["comparison_weight"], 1.0)
            self.assertAlmostEqual(by_symbol["CASH"]["weight"], 0.2)
            self.assertAlmostEqual(by_symbol["CASH"]["ex_cash_weight"], 0.0)
            self.assertTrue(by_symbol["CASH"]["is_cash"])
            self.assertEqual(by_symbol["CASH"]["bucket"], "cash_reserves")
            self.assertEqual(portfolio["comparison_weight_basis"], "invested_equity_ex_cash")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
