from datetime import date
from decimal import Decimal
from pathlib import Path
import sqlite3
import unittest

from invest.config import AppConfig
from invest.db import init_db, upsert_filing
from invest.managers import build_manager_radar
from invest.models import Filing, Holding


class ManagerRadarTests(unittest.TestCase):
    def test_focus_manager_tracking_percentages_use_latest_common_13f(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_db(conn)
        config = AppConfig(
            path=Path("config/invest.toml"),
            data={
                "managers": [
                    {
                        "key": "altimeter",
                        "name": "Altimeter Capital Management, LP",
                        "cik": "0001541617",
                        "lens": "growth",
                    }
                ],
                "focus_managers": {"tier1_keys": ["altimeter"]},
                "watchlist": {"symbols": ["NVDA"]},
                "thesis_buckets": [
                    {"key": "semis_networking_hbm", "symbols": ["NVDA"]},
                ],
            },
        )
        filing = Filing(
            manager_key="altimeter",
            manager_name="Altimeter Capital Management, LP",
            cik="0001541617",
            accession_number="0001541617-26-000006",
            form="13F-HR",
            filing_date=date(2026, 5, 15),
            report_date=date(2026, 3, 31),
            url="https://www.sec.gov/example",
        )
        holdings = [
            Holding(
                accession_number=filing.accession_number,
                issuer="NVIDIA CORP",
                title_class="COM",
                cusip="67066G104",
                value_usd=Decimal("60"),
                shares=Decimal("1"),
                symbol="NVDA",
                bucket="semis_networking_hbm",
            ),
            Holding(
                accession_number=filing.accession_number,
                issuer="Unresolved Private Mapping",
                title_class="COM",
                cusip="000000000",
                value_usd=Decimal("40"),
                shares=Decimal("1"),
                symbol="",
            ),
            Holding(
                accession_number=filing.accession_number,
                issuer="NVIDIA CORP",
                title_class="CALL",
                cusip="67066G104",
                value_usd=Decimal("200"),
                shares=Decimal("1"),
                put_call="CALL",
                symbol="NVDA",
                bucket="semis_networking_hbm",
            ),
        ]
        upsert_filing(conn, filing, holdings)

        radar = build_manager_radar(conn, config, {"NVDA": 0.25})

        focus = radar["focus_managers"][0]
        self.assertEqual(focus["manager_key"], "altimeter")
        self.assertEqual(focus["manager_tier"], "tier_1")
        self.assertEqual(focus["manager_group"], "Tier 1 Watch")
        self.assertEqual(focus["symbol_coverage_pct"], 60.0)
        self.assertEqual(focus["alloiq_watchlist_pct"], 60.0)
        self.assertEqual(focus["bucket_classified_pct"], 60.0)
        self.assertEqual(focus["default_portfolio_overlap_pct"], 60.0)
        self.assertEqual(focus["top10_concentration_pct"], 100.0)
        self.assertEqual(focus["top_positions"][0]["symbol"], "NVDA")
        self.assertEqual(focus["top_positions"][0]["portfolio_weight"], 0.25)
        self.assertEqual(len(focus["positions"]), 2)
        self.assertEqual(focus["positions"][0]["rank"], 1)
        self.assertEqual(focus["positions"][0]["value"], 60.0)
        self.assertEqual(focus["positions"][1]["issuer"], "Unresolved Private Mapping")
        self.assertEqual(radar["focus_manager_groups"][0]["key"], "tier_1")
        self.assertEqual(radar["focus_manager_groups"][0]["managers"][0]["manager_key"], "altimeter")


if __name__ == "__main__":
    unittest.main()
