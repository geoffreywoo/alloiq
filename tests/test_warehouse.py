from decimal import Decimal
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
        if params is not None and sql.count("%s") != len(params):
            raise AssertionError(f"placeholder mismatch: {sql.count('%s')} placeholders for {len(params)} params")
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
        self.assertIn("coverage_adjusted_external_signal_score NUMERIC", warehouse.WAREHOUSE_SCHEMA_SQL)
        self.assertIn("external_feed_status TEXT NOT NULL DEFAULT ''", warehouse.WAREHOUSE_SCHEMA_SQL)
        self.assertIn("ALTER TABLE engine_features ADD COLUMN IF NOT EXISTS external_provider_ok_ratio", warehouse.WAREHOUSE_SCHEMA_SQL)
        self.assertIn("ALTER TABLE engine_features ADD COLUMN IF NOT EXISTS coverage_adjusted_external_signal_score", warehouse.WAREHOUSE_SCHEMA_SQL)
        self.assertIn("ALTER TABLE backtest_outcomes ADD COLUMN IF NOT EXISTS external_coverage_multiplier", warehouse.WAREHOUSE_SCHEMA_SQL)
        self.assertIn("ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS external_provider_ok_ratio", warehouse.WAREHOUSE_SCHEMA_SQL)
        self.assertIn("CREATE TABLE IF NOT EXISTS llm_signal_snapshots", warehouse.WAREHOUSE_SCHEMA_SQL)
        self.assertIn("ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS llm_expected_return_delta", warehouse.WAREHOUSE_SCHEMA_SQL)

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
            "research_book": {
                "items": [
                    {
                        "research_id": "research-1",
                        "symbol": "NVDA",
                        "bucket": "semis_networking_hbm",
                        "model_policy_version": "2026-05-scenario-sizing-v1",
                        "verdict": "buy_more",
                        "risk_adjusted_expected_return": 22,
                        "probability_weighted_return": 25,
                        "evidence_quality": 80,
                        "drawdown_risk": 35,
                        "timing_score": 70,
                        "current_weight": 0.1,
                        "peer_avg_weight": 0.12,
                    }
                ]
            },
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
            "llm_signal": {
                "status": "ok",
                "mode": "bounded_signal",
                "model": "gpt-5.5",
                "prompt_version": "2026-05-bounded-signal-v1",
                "schema_version": "2026-05-llm-signal-schema-v2",
                "reviews": [
                    {
                        "symbol": "NVDA",
                        "thesis_quality": "strong",
                        "llm_expected_return_delta": 2,
                        "llm_evidence_quality_delta": 4,
                        "llm_drawdown_risk_delta": -3,
                        "llm_conviction_score": 90,
                        "llm_variant_quality_score": 85,
                        "llm_source_quality_score": 80,
                        "llm_contradiction_risk_score": 10,
                        "llm_staleness_risk_score": 12,
                        "llm_review_required": False,
                        "confidence": 0.8,
                    }
                ],
            },
            "earnings_events": [{"symbol": "NVDA", "event_date": "2026-05-27", "days_until": 3}],
            "calendars": {
                "earnings": {"events": [{"event_id": "event-1", "symbol": "NVDA", "event_date": "2026-05-27", "source": "manual", "confidence": 1.0}]},
                "filings_13f": {"managers": [{"manager_key": "m1", "manager_name": "Manager One", "quarter_end": "2026-06-30", "deadline": "2026-08-14", "status": "pending"}]},
            },
            "engine": {
                "version": "2026-05-equity-max-return-v1",
                "mode": "approval_plus_paper",
                "universe": "equities_only",
                "objective": "maximize_expected_3_12m_forward_return",
                "ranked_candidates": [
                    {
                        "feature_id": "feature-1",
                        "symbol": "NVDA",
                        "bucket": "semis_networking_hbm",
                        "expected_return_score": 55,
                        "expected_return_rank_score": 60,
                        "signal_family_count": 3,
                        "current_weight": 0.1,
                        "external_signal_score": 20,
                        "coverage_adjusted_external_signal_score": 5,
                        "external_coverage_multiplier": 0.25,
                        "external_feed_status": "limited",
                        "external_provider_count": 6,
                        "external_provider_ok_count": 1,
                        "external_provider_ok_ratio": 0.1667,
                        "external_signal_count": 4,
                        "external_source_count": 3,
                    }
                ],
                "recommendation_provenance": [{"symbol": "NVDA", "model_policy_version": "2026-05-equity-max-return-v1", "expected_return_rank_score": 60, "current_weight": 0.1, "recommended_delta_weight": 0.01, "target_weight": 0.11}],
            },
            "paper_portfolio": {
                "paper_trades": [{"paper_trade_id": "paper-1", "ticket_id": "ticket-1", "symbol": "NVDA", "trade_action": "add", "status": "planned", "current_weight": 0.1, "recommended_delta_weight": 0.01, "target_weight": 0.11, "proxy_fill_price": 120}],
                "snapshots": [{"symbol": "NVDA", "current_weight": 0.1, "paper_target_weight": 0.11, "paper_delta_weight": 0.01}],
            },
            "backtest": {
                "version": "2026-05-recommendation-backtest-v2",
                "model_policy_version": "2026-05-scenario-sizing-v1",
                "as_of": "2026-05-24",
                "source_report_count": 1,
                "trial_count": 1,
                "completed_outcome_count": 1,
                "pending_outcome_count": 3,
                "calibration": {"status": "available", "mean_error": 2},
                "horizons": [{"horizon": "1m", "completed_count": 1}],
                "by_external_feed_status": [{"key": "limited", "completed_count": 1}],
                "by_external_coverage": [{"key": "thin_coverage", "completed_count": 1}],
                "outcomes": [
                    {
                        "outcome_id": "outcome-1",
                        "trial_id": "example-1",
                        "as_of": "2026-05-24",
                        "session": "premarket",
                        "symbol": "NVDA",
                        "bucket": "semis_networking_hbm",
                        "trade_action": "add",
                        "horizon": "1m",
                        "status": "complete",
                        "direction": 1,
                        "entry_date": "2026-05-24",
                        "exit_date": "2026-06-24",
                        "entry_price": 120,
                        "exit_price": 132,
                        "raw_forward_return_pct": 10,
                        "decision_forward_return_pct": 10,
                        "risk_adjusted_expected_return": 8,
                        "expected_vs_realized_error": 2,
                        "signal_families": ["manager"],
                        "external_signal_score": 20,
                        "coverage_adjusted_external_signal_score": 5,
                        "external_coverage_multiplier": 0.25,
                        "external_feed_status": "limited",
                        "external_provider_count": 6,
                        "external_provider_ok_count": 1,
                        "external_provider_ok_ratio": 0.1667,
                        "external_signal_count": 4,
                        "external_source_count": 3,
                    }
                ],
            },
            "recommendation_training_examples": [
                {
                    "example_id": "example-1",
                    "ticket_id": "ticket-1",
                    "as_of": "2026-05-24",
                    "session": "premarket",
                    "symbol": "NVDA",
                    "bucket": "semis_networking_hbm",
                    "model_policy_version": "2026-05-scenario-sizing-v1",
                    "trade_action": "add",
                    "current_weight": 0.1,
                    "recommended_delta_weight": 0.01,
                    "target_weight": 0.11,
                    "risk_adjusted_expected_return": 22,
                    "base_risk_adjusted_expected_return": 20,
                    "base_evidence_quality": 76,
                    "base_drawdown_risk": 34,
                    "llm_signal_applied": True,
                    "llm_expected_return_delta": 2,
                    "llm_expected_return_adjustment": 1.6,
                    "llm_evidence_quality_delta": 4,
                    "llm_evidence_quality_adjustment": 3.2,
                    "llm_drawdown_risk_delta": -3,
                    "llm_drawdown_risk_adjustment": -2.4,
                    "llm_conviction_score": 90,
                    "llm_variant_quality_score": 85,
                    "llm_source_quality_score": 80,
                    "llm_contradiction_risk_score": 10,
                    "llm_staleness_risk_score": 12,
                    "llm_review_required": False,
                    "external_signal_score": 20,
                    "coverage_adjusted_external_signal_score": 5,
                    "external_coverage_multiplier": 0.25,
                    "external_feed_status": "limited",
                    "external_provider_count": 6,
                    "external_provider_ok_count": 1,
                    "external_provider_ok_ratio": 0.1667,
                    "external_signal_count": 4,
                    "external_source_count": 3,
                    "forward_return_labels": {"3m": None},
                }
            ],
        }

        counts = warehouse.upsert_report_payload(
            conn,
            payload,
            {"kind": "premarket", "status": "ran", "schedule": {"scheduled_at_utc": "2026-05-24T12:00:00Z"}},
        )

        self.assertEqual(counts["pipeline_runs"], 1)
        self.assertEqual(counts["portfolio_snapshots"], 1)
        self.assertEqual(counts["position_snapshots"], 1)
        self.assertEqual(counts["research_snapshots"], 1)
        self.assertEqual(counts["llm_signal_snapshots"], 1)
        self.assertEqual(counts["trade_recommendations"], 1)
        self.assertEqual(counts["calendar_events"], 1)
        self.assertEqual(counts["manager_filing_calendar"], 1)
        self.assertEqual(counts["engine_features"], 1)
        self.assertEqual(counts["engine_predictions"], 1)
        self.assertEqual(counts["paper_trades"], 1)
        self.assertEqual(counts["paper_portfolio_snapshots"], 1)
        self.assertEqual(counts["backtest_runs"], 1)
        self.assertEqual(counts["backtest_outcomes"], 1)
        self.assertEqual(counts["recommendation_training_examples"], 1)
        self.assertEqual(counts["model_policy_versions"], 1)
        self.assertTrue(any("INSERT INTO trade_recommendations" in sql for sql, _ in conn.executed))
        engine_feature = next((sql, params) for sql, params in conn.executed if "INSERT INTO engine_features" in sql)
        self.assertIn("coverage_adjusted_external_signal_score", engine_feature[0])
        self.assertIn("external_provider_ok_ratio", engine_feature[0])
        self.assertIn(Decimal("5"), engine_feature[1])
        self.assertIn("limited", engine_feature[1])
        backtest_run = next((sql, params) for sql, params in conn.executed if "INSERT INTO backtest_runs" in sql)
        self.assertIn("external_feed_status_summary", backtest_run[0])
        self.assertTrue(any("thin_coverage" in str(param) for param in backtest_run[1]))
        backtest_outcome = next((sql, params) for sql, params in conn.executed if "INSERT INTO backtest_outcomes" in sql)
        self.assertIn("coverage_adjusted_external_signal_score", backtest_outcome[0])
        self.assertIn(Decimal("5"), backtest_outcome[1])
        self.assertIn("limited", backtest_outcome[1])
        training_example = next((sql, params) for sql, params in conn.executed if "INSERT INTO recommendation_training_examples" in sql)
        self.assertIn("external_provider_ok_ratio", training_example[0])
        self.assertIn("llm_expected_return_delta", training_example[0])
        self.assertIn(Decimal("0.1667"), training_example[1])
        self.assertIn(Decimal("2"), training_example[1])
        llm_signal = next((sql, params) for sql, params in conn.executed if "INSERT INTO llm_signal_snapshots" in sql)
        self.assertIn("llm_expected_return_delta", llm_signal[0])
        self.assertIn(Decimal("2"), llm_signal[1])


if __name__ == "__main__":
    unittest.main()
