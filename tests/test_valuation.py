from datetime import date
from decimal import Decimal
from pathlib import Path
import sqlite3
import unittest

from invest.config import AppConfig
from invest.db import init_db, upsert_filing
from invest.models import Filing, Holding
from invest.valuation import build_manager_valuation_snapshot, build_portfolio_valuation_snapshot


class ValuationTests(unittest.TestCase):
    def test_manager_entry_proxy_uses_observed_13f_additions(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_db(conn)
        config = AppConfig(
            path=Path("config/invest.toml"),
            data={
                "managers": [{"key": "altimeter", "name": "Altimeter", "cik": "1"}],
                "focus_managers": {"tier1_keys": ["altimeter"]},
                "watchlist": {"symbols": ["NVDA"]},
            },
        )
        filings = [
            (
                Filing("altimeter", "Altimeter", "1", "q1", "13F-HR", date(2026, 5, 15), date(2026, 3, 31), "https://sec/q1"),
                Decimal("1000"),
                Decimal("10"),
            ),
            (
                Filing("altimeter", "Altimeter", "1", "q2", "13F-HR", date(2026, 8, 14), date(2026, 6, 30), "https://sec/q2"),
                Decimal("3000"),
                Decimal("15"),
            ),
        ]
        for filing, value, units in filings:
            upsert_filing(
                conn,
                filing,
                [
                    Holding(
                        accession_number=filing.accession_number,
                        issuer="NVIDIA CORP",
                        title_class="COM",
                        cusip="67066G104",
                        value_usd=value,
                        shares=units,
                        symbol="NVDA",
                    )
                ],
            )

        snapshot = build_manager_valuation_snapshot(
            conn,
            config,
            {"NVDA": {"last": Decimal("220")}},
            ["altimeter"],
        )

        position = snapshot["managers"][0]["positions"][0]
        self.assertEqual(position["latest_report_price"], 200.0)
        self.assertAlmostEqual(position["entry_price_estimate"], 133.3333)
        self.assertEqual(position["current_value_estimate"], 3300.0)
        self.assertAlmostEqual(position["entry_return_estimate_pct"], 65.0)

    def test_private_portfolio_valuation_uses_cost_basis_when_present(self):
        portfolio = {
            "by_symbol": [
                {
                    "symbol": "NVDA",
                    "bucket": "semis_networking_hbm",
                    "quantity": 10,
                    "cost_basis": 1000,
                    "market_value": 2200,
                    "weight": 1.0,
                }
            ]
        }

        snapshot = build_portfolio_valuation_snapshot(portfolio, date(2026, 5, 24))

        row = snapshot["positions"][0]
        self.assertEqual(row["entry_price_estimate"], 100.0)
        self.assertEqual(row["current_price"], 220.0)
        self.assertEqual(row["current_value"], 2200.0)
        self.assertEqual(row["unrealized_return_estimate_pct"], 120.0)


if __name__ == "__main__":
    unittest.main()
