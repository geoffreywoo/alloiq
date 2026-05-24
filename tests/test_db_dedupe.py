from datetime import date
from decimal import Decimal
import unittest

from invest.db import connect, init_db, insert_transactions
from invest.db import merge_duplicate_holdings
from invest.models import Holding, Transaction


class DbDedupeTests(unittest.TestCase):
    def test_transaction_import_is_idempotent(self):
        with self.subTest("sqlite tmp path"):
            import tempfile
            from pathlib import Path

            with tempfile.TemporaryDirectory() as tmp:
                conn = connect(Path(tmp) / "invest.db")
                init_db(conn)
                tx = Transaction(
                    broker="ibkr",
                    account="U123",
                    trade_date=date(2026, 5, 7),
                    action="BUY",
                    symbol="NVDA",
                    quantity=Decimal("1"),
                    price=Decimal("900"),
                    external_id="T1",
                )

                self.assertEqual(insert_transactions(conn, [tx]), 1)
                self.assertEqual(insert_transactions(conn, [tx]), 0)

    def test_duplicate_filing_holdings_are_coalesced_before_insert(self):
        holdings = [
            Holding(
                accession_number="A1",
                issuer="DUPLICATE INC",
                title_class="COM",
                cusip="123456789",
                value_usd=Decimal("1000"),
                shares=Decimal("10"),
                symbol="DUP",
            ),
            Holding(
                accession_number="A1",
                issuer="DUPLICATE INC",
                title_class="CL A",
                cusip="123456789",
                value_usd=Decimal("2000"),
                shares=Decimal("20"),
                symbol="DUP",
            ),
        ]

        merged = merge_duplicate_holdings(holdings)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].value_usd, Decimal("3000"))
        self.assertEqual(merged[0].shares, Decimal("30"))


if __name__ == "__main__":
    unittest.main()
