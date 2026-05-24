import unittest

from invest import warehouse


class FakeCursor:
    def __init__(self, executed):
        self.executed = executed

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return {"ok": 1}

    def fetchall(self):
        return []


class FakeConnection:
    def __init__(self):
        self.executed = []

    def cursor(self):
        return FakeCursor(self.executed)


class WarehouseTests(unittest.TestCase):
    def test_schema_contains_expected_private_tables(self):
        for table in warehouse.warehouse_tables():
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", warehouse.WAREHOUSE_SCHEMA_SQL)

    def test_sync_upserts_private_snapshot_shapes(self):
        conn = FakeConnection()
        payload = {
            "as_of": "2026-05-24",
            "session": "premarket",
            "portfolio": {
                "gross_exposure": 100000,
                "net_exposure": 90000,
                "position_count": 1,
                "symbol_count": 1,
                "by_symbol": [
                    {
                        "symbol": "NVDA",
                        "bucket": "semis_networking_hbm",
                        "quantity": 10,
                        "cost_basis": 9000,
                        "market_value": 10000,
                        "weight": 0.1,
                        "brokers": ["ibkr"],
                        "accounts": ["U123"],
                    }
                ],
            },
            "decision_cards": [{"symbol": "NVDA", "score": 50, "signal_family_count": 3}],
            "portfolio_benchmark": {
                "primary_horizon": "5d",
                "top_contributors": [{"symbol": "NVDA", "weight": 0.1, "five_day_pct": 5, "contribution_pct": 0.5}],
            },
            "approval_tickets": [
                {
                    "ticket_id": "ticket-1",
                    "symbol": "NVDA",
                    "trade_action": "add",
                    "recommended_delta_weight": 0.01,
                    "target_weight": 0.11,
                    "confidence": 80,
                }
            ],
            "earnings_events": [{"symbol": "NVDA", "event_date": "2026-05-27", "days_until": 3}],
        }

        counts = warehouse.upsert_report_payload(
            conn,
            payload,
            {"kind": "premarket", "status": "ran", "schedule": {"scheduled_at_utc": "2026-05-24T12:00:00Z"}},
        )

        self.assertEqual(counts["pipeline_runs"], 1)
        self.assertEqual(counts["portfolio_snapshots"], 1)
        self.assertEqual(counts["position_snapshots"], 1)
        self.assertEqual(counts["trade_recommendations"], 1)
        self.assertTrue(any("INSERT INTO trade_recommendations" in sql for sql, _ in conn.executed))


if __name__ == "__main__":
    unittest.main()
