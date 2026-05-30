from __future__ import annotations

import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .util import stable_id


WAREHOUSE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    as_of DATE,
    session TEXT NOT NULL DEFAULT '',
    scheduled_at_utc TIMESTAMPTZ,
    schedule JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_freshness JSONB NOT NULL DEFAULT '{}'::jsonb,
    workflow JSONB NOT NULL DEFAULT '{}'::jsonb,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    as_of DATE NOT NULL,
    session TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    gross_exposure NUMERIC,
    net_exposure NUMERIC,
    position_count INTEGER NOT NULL DEFAULT 0,
    symbol_count INTEGER NOT NULL DEFAULT 0,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS position_snapshots (
    position_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES portfolio_snapshots(snapshot_id) ON DELETE CASCADE,
    as_of DATE NOT NULL,
    broker TEXT NOT NULL DEFAULT '',
    account TEXT NOT NULL DEFAULT '',
    symbol TEXT NOT NULL,
    bucket TEXT NOT NULL DEFAULT 'unmapped',
    quantity NUMERIC,
    cost_basis NUMERIC,
    market_value NUMERIC,
    weight NUMERIC,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS signal_snapshots (
    signal_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    as_of DATE NOT NULL,
    symbol TEXT NOT NULL,
    bucket TEXT NOT NULL DEFAULT 'unmapped',
    score NUMERIC,
    signal_family_count INTEGER NOT NULL DEFAULT 0,
    manager_count INTEGER NOT NULL DEFAULT 0,
    event_score NUMERIC,
    macro_regime TEXT NOT NULL DEFAULT '',
    signal_families JSONB NOT NULL DEFAULT '[]'::jsonb,
    event_types JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS research_snapshots (
    research_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    as_of DATE NOT NULL,
    symbol TEXT NOT NULL,
    bucket TEXT NOT NULL DEFAULT 'unmapped',
    model_policy_version TEXT NOT NULL DEFAULT '',
    verdict TEXT NOT NULL DEFAULT 'study',
    risk_adjusted_expected_return NUMERIC,
    probability_weighted_return NUMERIC,
    evidence_quality NUMERIC,
    drawdown_risk NUMERIC,
    timing_score NUMERIC,
    current_weight NUMERIC,
    peer_avg_weight NUMERIC,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS company_underwriting_snapshots (
    underwriting_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    as_of DATE NOT NULL,
    symbol TEXT NOT NULL,
    bucket TEXT NOT NULL DEFAULT 'unmapped',
    company_underwriting_score NUMERIC,
    evidence_quality NUMERIC,
    data_quality NUMERIC,
    source_quality NUMERIC,
    review_status TEXT NOT NULL DEFAULT '',
    add_eligible BOOLEAN NOT NULL DEFAULT false,
    trim_signal BOOLEAN NOT NULL DEFAULT false,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sector_underwriting_snapshots (
    underwriting_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    as_of DATE NOT NULL,
    bucket TEXT NOT NULL DEFAULT 'unmapped',
    sector_setup_score NUMERIC,
    target_weight_modifier NUMERIC,
    sector_headwind BOOLEAN NOT NULL DEFAULT false,
    sector_tailwind BOOLEAN NOT NULL DEFAULT false,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS llm_signal_snapshots (
    llm_signal_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    as_of DATE NOT NULL,
    session TEXT NOT NULL DEFAULT '',
    symbol TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL DEFAULT '',
    schema_version TEXT NOT NULL DEFAULT '',
    thesis_quality TEXT NOT NULL DEFAULT '',
    llm_expected_return_delta NUMERIC,
    llm_evidence_quality_delta NUMERIC,
    llm_drawdown_risk_delta NUMERIC,
    llm_conviction_score NUMERIC,
    llm_variant_quality_score NUMERIC,
    llm_source_quality_score NUMERIC,
    llm_contradiction_risk_score NUMERIC,
    llm_staleness_risk_score NUMERIC,
    llm_review_required BOOLEAN NOT NULL DEFAULT false,
    confidence NUMERIC,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS trade_recommendations (
    ticket_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    as_of DATE NOT NULL,
    session TEXT NOT NULL DEFAULT '',
    symbol TEXT NOT NULL,
    trade_action TEXT NOT NULL DEFAULT 'study',
    status TEXT NOT NULL DEFAULT 'open',
    current_weight NUMERIC NOT NULL DEFAULT 0,
    recommended_delta_weight NUMERIC NOT NULL DEFAULT 0,
    target_weight NUMERIC NOT NULL DEFAULT 0,
    confidence INTEGER NOT NULL DEFAULT 0,
    risk_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    rationale TEXT NOT NULL DEFAULT '',
    trigger TEXT NOT NULL DEFAULT '',
    risk TEXT NOT NULL DEFAULT '',
    falsifier TEXT NOT NULL DEFAULT '',
    sizing_basis TEXT NOT NULL DEFAULT '',
    estimated_notional NUMERIC,
    estimated_shares NUMERIC,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS decision_ledger (
    ticket_id TEXT PRIMARY KEY,
    decision TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    rejection_reason TEXT NOT NULL DEFAULT '',
    execution_status TEXT NOT NULL DEFAULT 'not_executed',
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS performance_attribution (
    attribution_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    as_of DATE NOT NULL,
    symbol TEXT NOT NULL,
    horizon TEXT NOT NULL DEFAULT '5d',
    role TEXT NOT NULL DEFAULT '',
    portfolio_weight NUMERIC,
    return_pct NUMERIC,
    contribution_pct NUMERIC,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS earnings_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    as_of DATE NOT NULL,
    symbol TEXT NOT NULL,
    event_date DATE,
    event_type TEXT NOT NULL DEFAULT 'earnings',
    source TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    days_until INTEGER,
    catalyst_types JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS calendar_events (
    calendar_event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    as_of DATE NOT NULL,
    calendar_kind TEXT NOT NULL DEFAULT '',
    symbol TEXT NOT NULL DEFAULT '',
    event_date DATE,
    event_type TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    confidence NUMERIC,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS manager_filing_calendar (
    calendar_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    manager_key TEXT NOT NULL,
    manager_name TEXT NOT NULL DEFAULT '',
    quarter_end DATE NOT NULL,
    deadline DATE NOT NULL,
    status TEXT NOT NULL DEFAULT '',
    latest_report_date DATE,
    latest_filing_date DATE,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS engine_features (
    feature_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    as_of DATE NOT NULL,
    symbol TEXT NOT NULL,
    bucket TEXT NOT NULL DEFAULT 'unmapped',
    expected_return_score NUMERIC,
    expected_return_rank_score NUMERIC,
    signal_family_count INTEGER NOT NULL DEFAULT 0,
    current_weight NUMERIC,
    external_signal_score NUMERIC,
    coverage_adjusted_external_signal_score NUMERIC,
    external_coverage_multiplier NUMERIC,
    external_feed_status TEXT NOT NULL DEFAULT '',
    external_provider_count INTEGER,
    external_provider_ok_count INTEGER,
    external_provider_ok_ratio NUMERIC,
    external_signal_count INTEGER,
    external_source_count INTEGER,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS engine_predictions (
    prediction_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    as_of DATE NOT NULL,
    symbol TEXT NOT NULL,
    model_policy_version TEXT NOT NULL DEFAULT '',
    expected_return_rank_score NUMERIC,
    current_weight NUMERIC,
    recommended_delta_weight NUMERIC,
    target_weight NUMERIC,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS paper_trades (
    paper_trade_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    ticket_id TEXT NOT NULL DEFAULT '',
    as_of DATE NOT NULL,
    symbol TEXT NOT NULL,
    trade_action TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    current_weight NUMERIC,
    recommended_delta_weight NUMERIC,
    target_weight NUMERIC,
    proxy_fill_price NUMERIC,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS paper_portfolio_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    as_of DATE NOT NULL,
    symbol TEXT NOT NULL,
    current_weight NUMERIC,
    paper_target_weight NUMERIC,
    paper_delta_weight NUMERIC,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS recommendation_outcomes (
    outcome_id TEXT PRIMARY KEY,
    ticket_id TEXT NOT NULL DEFAULT '',
    paper_trade_id TEXT NOT NULL DEFAULT '',
    symbol TEXT NOT NULL,
    horizon TEXT NOT NULL,
    as_of DATE NOT NULL,
    forward_return_pct NUMERIC,
    expected_return_score NUMERIC,
    signal_families JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    backtest_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    as_of DATE NOT NULL,
    version TEXT NOT NULL DEFAULT '',
    model_policy_version TEXT NOT NULL DEFAULT '',
    source_report_count INTEGER NOT NULL DEFAULT 0,
    trial_count INTEGER NOT NULL DEFAULT 0,
    completed_outcome_count INTEGER NOT NULL DEFAULT 0,
    pending_outcome_count INTEGER NOT NULL DEFAULT 0,
    calibration JSONB NOT NULL DEFAULT '{}'::jsonb,
    horizon_summary JSONB NOT NULL DEFAULT '[]'::jsonb,
    external_feed_status_summary JSONB NOT NULL DEFAULT '[]'::jsonb,
    external_coverage_summary JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS backtest_outcomes (
    outcome_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    backtest_id TEXT NOT NULL DEFAULT '',
    trial_id TEXT NOT NULL DEFAULT '',
    as_of DATE NOT NULL,
    session TEXT NOT NULL DEFAULT '',
    symbol TEXT NOT NULL,
    bucket TEXT NOT NULL DEFAULT 'unmapped',
    trade_action TEXT NOT NULL DEFAULT '',
    horizon TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '',
    direction INTEGER NOT NULL DEFAULT 0,
    entry_date DATE,
    exit_date DATE,
    entry_price NUMERIC,
    exit_price NUMERIC,
    raw_forward_return_pct NUMERIC,
    decision_forward_return_pct NUMERIC,
    risk_adjusted_expected_return NUMERIC,
    expected_vs_realized_error NUMERIC,
    signal_families JSONB NOT NULL DEFAULT '[]'::jsonb,
    external_signal_score NUMERIC,
    coverage_adjusted_external_signal_score NUMERIC,
    external_coverage_multiplier NUMERIC,
    external_feed_status TEXT NOT NULL DEFAULT '',
    external_provider_count INTEGER,
    external_provider_ok_count INTEGER,
    external_provider_ok_ratio NUMERIC,
    external_signal_count INTEGER,
    external_source_count INTEGER,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS recommendation_training_examples (
    example_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    ticket_id TEXT NOT NULL DEFAULT '',
    as_of DATE NOT NULL,
    session TEXT NOT NULL DEFAULT '',
    symbol TEXT NOT NULL,
    bucket TEXT NOT NULL DEFAULT 'unmapped',
    model_policy_version TEXT NOT NULL DEFAULT '',
    trade_action TEXT NOT NULL DEFAULT 'study',
    current_weight NUMERIC,
    recommended_delta_weight NUMERIC,
    target_weight NUMERIC,
    risk_adjusted_expected_return NUMERIC,
    base_risk_adjusted_expected_return NUMERIC,
    base_evidence_quality NUMERIC,
    base_drawdown_risk NUMERIC,
    llm_signal_applied BOOLEAN NOT NULL DEFAULT false,
    llm_expected_return_delta NUMERIC,
    llm_expected_return_adjustment NUMERIC,
    llm_evidence_quality_delta NUMERIC,
    llm_evidence_quality_adjustment NUMERIC,
    llm_drawdown_risk_delta NUMERIC,
    llm_drawdown_risk_adjustment NUMERIC,
    llm_conviction_score NUMERIC,
    llm_variant_quality_score NUMERIC,
    llm_source_quality_score NUMERIC,
    llm_contradiction_risk_score NUMERIC,
    llm_staleness_risk_score NUMERIC,
    llm_review_required BOOLEAN NOT NULL DEFAULT false,
    external_signal_score NUMERIC,
    coverage_adjusted_external_signal_score NUMERIC,
    external_coverage_multiplier NUMERIC,
    external_feed_status TEXT NOT NULL DEFAULT '',
    external_provider_count INTEGER,
    external_provider_ok_count INTEGER,
    external_provider_ok_ratio NUMERIC,
    external_signal_count INTEGER,
    external_source_count INTEGER,
    forward_return_labels JSONB NOT NULL DEFAULT '{}'::jsonb,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS model_policy_versions (
    policy_version TEXT PRIMARY KEY,
    objective TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT '',
    universe TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS external_feed_status_summary JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS external_coverage_summary JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE engine_features ADD COLUMN IF NOT EXISTS external_signal_score NUMERIC;
ALTER TABLE engine_features ADD COLUMN IF NOT EXISTS coverage_adjusted_external_signal_score NUMERIC;
ALTER TABLE engine_features ADD COLUMN IF NOT EXISTS external_coverage_multiplier NUMERIC;
ALTER TABLE engine_features ADD COLUMN IF NOT EXISTS external_feed_status TEXT NOT NULL DEFAULT '';
ALTER TABLE engine_features ADD COLUMN IF NOT EXISTS external_provider_count INTEGER;
ALTER TABLE engine_features ADD COLUMN IF NOT EXISTS external_provider_ok_count INTEGER;
ALTER TABLE engine_features ADD COLUMN IF NOT EXISTS external_provider_ok_ratio NUMERIC;
ALTER TABLE engine_features ADD COLUMN IF NOT EXISTS external_signal_count INTEGER;
ALTER TABLE engine_features ADD COLUMN IF NOT EXISTS external_source_count INTEGER;
ALTER TABLE backtest_outcomes ADD COLUMN IF NOT EXISTS external_signal_score NUMERIC;
ALTER TABLE backtest_outcomes ADD COLUMN IF NOT EXISTS coverage_adjusted_external_signal_score NUMERIC;
ALTER TABLE backtest_outcomes ADD COLUMN IF NOT EXISTS external_coverage_multiplier NUMERIC;
ALTER TABLE backtest_outcomes ADD COLUMN IF NOT EXISTS external_feed_status TEXT NOT NULL DEFAULT '';
ALTER TABLE backtest_outcomes ADD COLUMN IF NOT EXISTS external_provider_count INTEGER;
ALTER TABLE backtest_outcomes ADD COLUMN IF NOT EXISTS external_provider_ok_count INTEGER;
ALTER TABLE backtest_outcomes ADD COLUMN IF NOT EXISTS external_provider_ok_ratio NUMERIC;
ALTER TABLE backtest_outcomes ADD COLUMN IF NOT EXISTS external_signal_count INTEGER;
ALTER TABLE backtest_outcomes ADD COLUMN IF NOT EXISTS external_source_count INTEGER;
ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS external_signal_score NUMERIC;
ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS coverage_adjusted_external_signal_score NUMERIC;
ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS external_coverage_multiplier NUMERIC;
ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS external_feed_status TEXT NOT NULL DEFAULT '';
ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS external_provider_count INTEGER;
ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS external_provider_ok_count INTEGER;
ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS external_provider_ok_ratio NUMERIC;
ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS external_signal_count INTEGER;
ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS external_source_count INTEGER;
ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS base_risk_adjusted_expected_return NUMERIC;
ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS base_evidence_quality NUMERIC;
ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS base_drawdown_risk NUMERIC;
ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS llm_signal_applied BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS llm_expected_return_delta NUMERIC;
ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS llm_expected_return_adjustment NUMERIC;
ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS llm_evidence_quality_delta NUMERIC;
ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS llm_evidence_quality_adjustment NUMERIC;
ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS llm_drawdown_risk_delta NUMERIC;
ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS llm_drawdown_risk_adjustment NUMERIC;
ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS llm_conviction_score NUMERIC;
ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS llm_variant_quality_score NUMERIC;
ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS llm_source_quality_score NUMERIC;
ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS llm_contradiction_risk_score NUMERIC;
ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS llm_staleness_risk_score NUMERIC;
ALTER TABLE recommendation_training_examples ADD COLUMN IF NOT EXISTS llm_review_required BOOLEAN NOT NULL DEFAULT false;
"""


class WarehouseDisabled(RuntimeError):
    pass


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip()


def connect_warehouse(db_url: str | None = None):
    url = db_url or database_url()
    if not url:
        raise WarehouseDisabled("DATABASE_URL is not set")
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ModuleNotFoundError as exc:
        raise RuntimeError("Install psycopg[binary] to use the private warehouse.") from exc
    return psycopg.connect(url, autocommit=True, row_factory=dict_row)


def split_sql(sql: str) -> list[str]:
    return [statement.strip() for statement in sql.split(";") if statement.strip()]


def migrate(conn=None, db_url: str | None = None) -> dict[str, Any]:
    owns_connection = conn is None
    if conn is None:
        conn = connect_warehouse(db_url)
    try:
        with conn.cursor() as cur:
            for statement in split_sql(WAREHOUSE_SCHEMA_SQL):
                cur.execute(statement)
        return {"status": "ok", "tables": warehouse_tables()}
    finally:
        if owns_connection:
            conn.close()


def health(db_url: str | None = None) -> dict[str, Any]:
    if not (db_url or database_url()):
        return {"status": "disabled", "reason": "DATABASE_URL is not set"}
    try:
        conn = connect_warehouse(db_url)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 AS ok")
                row = cur.fetchone()
            return {"status": "ok", "database": "connected", "select_1": row["ok"] if row else None}
        finally:
            conn.close()
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}


def sync_report_payload(
    payload: dict[str, Any] | None,
    pipeline_result: dict[str, Any],
    db_url: str | None = None,
) -> dict[str, Any]:
    if not (db_url or database_url()):
        return {"status": "skipped", "reason": "DATABASE_URL is not set"}
    conn = connect_warehouse(db_url)
    try:
        migrate(conn)
        counts = upsert_report_payload(conn, payload or {}, pipeline_result)
        return {"status": "synced", **counts}
    finally:
        conn.close()


def upsert_report_payload(conn, payload: dict[str, Any], pipeline_result: dict[str, Any]) -> dict[str, int]:
    run_id = pipeline_run_id(payload, pipeline_result)
    run_sql = """
        INSERT INTO pipeline_runs
        (run_id, kind, status, as_of, session, scheduled_at_utc, schedule, source_freshness, workflow, details, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, now())
        ON CONFLICT (run_id) DO UPDATE SET
          status = EXCLUDED.status,
          as_of = EXCLUDED.as_of,
          session = EXCLUDED.session,
          scheduled_at_utc = EXCLUDED.scheduled_at_utc,
          schedule = EXCLUDED.schedule,
          source_freshness = EXCLUDED.source_freshness,
          workflow = EXCLUDED.workflow,
          details = EXCLUDED.details,
          updated_at = now()
    """
    schedule = pipeline_result.get("schedule") or {}
    workflow = ((payload.get("site") or {}).get("workflow")) or pipeline_result.get("workflow") or {}
    with conn.cursor() as cur:
        cur.execute(
            run_sql,
            (
                run_id,
                pipeline_result.get("kind") or payload.get("session") or "manual",
                pipeline_result.get("status") or "unknown",
                payload.get("as_of") or None,
                payload.get("session") or "",
                schedule.get("scheduled_at_utc"),
                json_param(schedule),
                json_param(payload.get("data_health") or {}),
                json_param(workflow),
                json_param(pipeline_result),
            ),
        )
    if not payload:
        return {
            "pipeline_runs": 1,
            "portfolio_snapshots": 0,
            "position_snapshots": 0,
            "signal_snapshots": 0,
            "research_snapshots": 0,
            "company_underwriting_snapshots": 0,
            "sector_underwriting_snapshots": 0,
            "llm_signal_snapshots": 0,
            "trade_recommendations": 0,
            "performance_attribution": 0,
            "earnings_events": 0,
            "calendar_events": 0,
            "manager_filing_calendar": 0,
            "engine_features": 0,
            "engine_predictions": 0,
            "paper_trades": 0,
            "paper_portfolio_snapshots": 0,
            "backtest_runs": 0,
            "backtest_outcomes": 0,
            "recommendation_training_examples": 0,
            "model_policy_versions": 0,
        }

    counts = {
        "pipeline_runs": 1,
        "portfolio_snapshots": upsert_portfolio_snapshot(conn, run_id, payload),
        "position_snapshots": replace_position_snapshots(conn, run_id, payload),
        "signal_snapshots": replace_signal_snapshots(conn, run_id, payload),
        "research_snapshots": replace_research_snapshots(conn, run_id, payload),
        "company_underwriting_snapshots": replace_company_underwriting_snapshots(conn, run_id, payload),
        "sector_underwriting_snapshots": replace_sector_underwriting_snapshots(conn, run_id, payload),
        "llm_signal_snapshots": replace_llm_signal_snapshots(conn, run_id, payload),
        "trade_recommendations": replace_trade_recommendations(conn, run_id, payload),
        "performance_attribution": replace_performance_attribution(conn, run_id, payload),
        "earnings_events": replace_earnings_events(conn, run_id, payload),
        "calendar_events": replace_calendar_events(conn, run_id, payload),
        "manager_filing_calendar": replace_manager_filing_calendar(conn, run_id, payload),
        "engine_features": replace_engine_features(conn, run_id, payload),
        "engine_predictions": replace_engine_predictions(conn, run_id, payload),
        "paper_trades": replace_paper_trades(conn, run_id, payload),
        "paper_portfolio_snapshots": replace_paper_portfolio_snapshots(conn, run_id, payload),
        "backtest_runs": replace_backtest_run(conn, run_id, payload),
        "backtest_outcomes": replace_backtest_outcomes(conn, run_id, payload),
        "recommendation_training_examples": replace_recommendation_training_examples(conn, run_id, payload),
        "model_policy_versions": upsert_model_policy_version(conn, payload),
    }
    return counts


def upsert_portfolio_snapshot(conn, run_id: str, payload: dict[str, Any]) -> int:
    portfolio = payload.get("portfolio") or {}
    snapshot_id = portfolio_snapshot_id(payload)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO portfolio_snapshots
            (snapshot_id, run_id, as_of, session, display_name, gross_exposure, net_exposure,
             position_count, symbol_count, raw, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, now())
            ON CONFLICT (snapshot_id) DO UPDATE SET
              run_id = EXCLUDED.run_id,
              display_name = EXCLUDED.display_name,
              gross_exposure = EXCLUDED.gross_exposure,
              net_exposure = EXCLUDED.net_exposure,
              position_count = EXCLUDED.position_count,
              symbol_count = EXCLUDED.symbol_count,
              raw = EXCLUDED.raw,
              updated_at = now()
            """,
            (
                snapshot_id,
                run_id,
                payload.get("as_of"),
                payload.get("session") or "",
                portfolio.get("display_name", ""),
                numeric(portfolio.get("gross_exposure")),
                numeric(portfolio.get("net_exposure")),
                int(portfolio.get("position_count") or 0),
                int(portfolio.get("symbol_count") or 0),
                json_param(portfolio),
            ),
        )
    return 1


def replace_position_snapshots(conn, run_id: str, payload: dict[str, Any]) -> int:
    snapshot_id = portfolio_snapshot_id(payload)
    rows = payload.get("portfolio", {}).get("by_symbol", [])
    with conn.cursor() as cur:
        cur.execute("DELETE FROM position_snapshots WHERE snapshot_id = %s", (snapshot_id,))
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            cur.execute(
                """
                INSERT INTO position_snapshots
                (position_id, snapshot_id, as_of, broker, account, symbol, bucket, quantity, cost_basis, market_value, weight, raw)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    stable_id([snapshot_id, symbol, ",".join(row.get("brokers") or []), ",".join(row.get("accounts") or [])]),
                    snapshot_id,
                    payload.get("as_of"),
                    ",".join(row.get("brokers") or []),
                    ",".join(row.get("accounts") or []),
                    symbol,
                    row.get("bucket", "unmapped"),
                    numeric(row.get("quantity")),
                    numeric(row.get("cost_basis")),
                    numeric(row.get("market_value")),
                    numeric(row.get("weight")),
                    json_param(row),
                ),
            )
    return len(rows)


def replace_signal_snapshots(conn, run_id: str, payload: dict[str, Any]) -> int:
    rows = payload.get("decision_cards", [])
    macro_regime = str((payload.get("macro") or {}).get("regime") or "")
    with conn.cursor() as cur:
        cur.execute("DELETE FROM signal_snapshots WHERE run_id = %s", (run_id,))
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            cur.execute(
                """
                INSERT INTO signal_snapshots
                (signal_id, run_id, as_of, symbol, bucket, score, signal_family_count,
                 manager_count, event_score, macro_regime, signal_families, event_types, raw)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)
                """,
                (
                    stable_id([run_id, "signal", symbol]),
                    run_id,
                    payload.get("as_of"),
                    symbol,
                    row.get("bucket", "unmapped"),
                    numeric(row.get("score")),
                    int(row.get("signal_family_count") or len(row.get("signal_families") or [])),
                    int(row.get("consensus_manager_count") or 0),
                    numeric(row.get("event_score")),
                    macro_regime,
                    json_param(row.get("signal_families") or []),
                    json_param(row.get("top_event_types") or []),
                    json_param(row),
                ),
            )
    return len(rows)


def replace_research_snapshots(conn, run_id: str, payload: dict[str, Any]) -> int:
    rows = (payload.get("research_book") or {}).get("items", [])
    with conn.cursor() as cur:
        cur.execute("DELETE FROM research_snapshots WHERE run_id = %s", (run_id,))
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            cur.execute(
                """
                INSERT INTO research_snapshots
                (research_id, run_id, as_of, symbol, bucket, model_policy_version, verdict,
                 risk_adjusted_expected_return, probability_weighted_return, evidence_quality,
                 drawdown_risk, timing_score, current_weight, peer_avg_weight, raw)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    stable_id([run_id, row.get("research_id") or "research", symbol]),
                    run_id,
                    payload.get("as_of"),
                    symbol,
                    row.get("bucket", "unmapped"),
                    row.get("model_policy_version") or "",
                    row.get("verdict") or "study",
                    numeric(row.get("risk_adjusted_expected_return")),
                    numeric(row.get("probability_weighted_return")),
                    numeric(row.get("evidence_quality")),
                    numeric(row.get("drawdown_risk")),
                    numeric(row.get("timing_score")),
                    numeric(row.get("current_weight")),
                    numeric(row.get("peer_avg_weight")),
                    json_param(row),
                ),
            )
    return len(rows)


def replace_company_underwriting_snapshots(conn, run_id: str, payload: dict[str, Any]) -> int:
    rows = (payload.get("company_underwriting") or {}).get("items", [])
    with conn.cursor() as cur:
        cur.execute("DELETE FROM company_underwriting_snapshots WHERE run_id = %s", (run_id,))
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            cur.execute(
                """
                INSERT INTO company_underwriting_snapshots
                (underwriting_id, run_id, as_of, symbol, bucket, company_underwriting_score,
                 evidence_quality, data_quality, source_quality, review_status, add_eligible,
                 trim_signal, raw)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    stable_id([run_id, row.get("underwriting_id") or "company", symbol]),
                    run_id,
                    payload.get("as_of"),
                    symbol,
                    row.get("bucket", "unmapped"),
                    numeric(row.get("company_underwriting_score")),
                    numeric(row.get("evidence_quality")),
                    numeric(row.get("data_quality")),
                    numeric(row.get("source_quality")),
                    row.get("review_status") or "",
                    bool(row.get("add_eligible", False)),
                    bool(row.get("trim_signal", False)),
                    json_param(row),
                ),
            )
    return len(rows)


def replace_sector_underwriting_snapshots(conn, run_id: str, payload: dict[str, Any]) -> int:
    rows = (payload.get("sector_underwriting") or {}).get("items", [])
    with conn.cursor() as cur:
        cur.execute("DELETE FROM sector_underwriting_snapshots WHERE run_id = %s", (run_id,))
        for row in rows:
            bucket = str(row.get("bucket") or "unmapped")
            cur.execute(
                """
                INSERT INTO sector_underwriting_snapshots
                (underwriting_id, run_id, as_of, bucket, sector_setup_score,
                 target_weight_modifier, sector_headwind, sector_tailwind, raw)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    stable_id([run_id, row.get("underwriting_id") or "sector", bucket]),
                    run_id,
                    payload.get("as_of"),
                    bucket,
                    numeric(row.get("sector_setup_score")),
                    numeric(row.get("target_weight_modifier")),
                    bool(row.get("sector_headwind", False)),
                    bool(row.get("sector_tailwind", False)),
                    json_param(row),
                ),
            )
    return len(rows)


def replace_llm_signal_snapshots(conn, run_id: str, payload: dict[str, Any]) -> int:
    snapshot = payload.get("llm_signal") or payload.get("llm_review") or {}
    rows = snapshot.get("reviews") or []
    with conn.cursor() as cur:
        cur.execute("DELETE FROM llm_signal_snapshots WHERE run_id = %s", (run_id,))
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            cur.execute(
                """
                INSERT INTO llm_signal_snapshots
                (llm_signal_id, run_id, as_of, session, symbol, mode, model, prompt_version,
                 schema_version, thesis_quality, llm_expected_return_delta,
                 llm_evidence_quality_delta, llm_drawdown_risk_delta, llm_conviction_score,
                 llm_variant_quality_score, llm_source_quality_score,
                 llm_contradiction_risk_score, llm_staleness_risk_score,
                 llm_review_required, confidence, raw)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    stable_id([run_id, "llm_signal", symbol, snapshot.get("prompt_version"), snapshot.get("schema_version")]),
                    run_id,
                    row.get("as_of") or snapshot.get("as_of") or payload.get("as_of"),
                    row.get("session") or snapshot.get("session") or payload.get("session") or "",
                    symbol,
                    snapshot.get("mode") or "",
                    snapshot.get("model") or "",
                    snapshot.get("prompt_version") or "",
                    snapshot.get("schema_version") or "",
                    row.get("thesis_quality") or "",
                    numeric(row.get("llm_expected_return_delta")),
                    numeric(row.get("llm_evidence_quality_delta")),
                    numeric(row.get("llm_drawdown_risk_delta")),
                    numeric(row.get("llm_conviction_score")),
                    numeric(row.get("llm_variant_quality_score")),
                    numeric(row.get("llm_source_quality_score")),
                    numeric(row.get("llm_contradiction_risk_score")),
                    numeric(row.get("llm_staleness_risk_score")),
                    bool(row.get("llm_review_required", row.get("review_required", False))),
                    numeric(row.get("confidence")),
                    json_param(row),
                ),
            )
    return len(rows)


def replace_trade_recommendations(conn, run_id: str, payload: dict[str, Any]) -> int:
    tickets = payload.get("approval_tickets") or []
    if not tickets:
        tickets = action_queue_to_tickets(payload)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM trade_recommendations WHERE run_id = %s", (run_id,))
        for ticket in tickets:
            symbol = str(ticket.get("symbol") or "").upper()
            if not symbol:
                continue
            cur.execute(
                """
                INSERT INTO trade_recommendations
                (ticket_id, run_id, as_of, session, symbol, trade_action, status,
                 current_weight, recommended_delta_weight, target_weight, confidence,
                 risk_flags, evidence, rationale, trigger, risk, falsifier, sizing_basis,
                 estimated_notional, estimated_shares, raw, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                        %s, %s, %s, %s, %s, %s, %s, %s::jsonb, now())
                ON CONFLICT (ticket_id) DO UPDATE SET
                  run_id = EXCLUDED.run_id,
                  status = EXCLUDED.status,
                  current_weight = EXCLUDED.current_weight,
                  recommended_delta_weight = EXCLUDED.recommended_delta_weight,
                  target_weight = EXCLUDED.target_weight,
                  confidence = EXCLUDED.confidence,
                  risk_flags = EXCLUDED.risk_flags,
                  evidence = EXCLUDED.evidence,
                  rationale = EXCLUDED.rationale,
                  trigger = EXCLUDED.trigger,
                  risk = EXCLUDED.risk,
                  falsifier = EXCLUDED.falsifier,
                  sizing_basis = EXCLUDED.sizing_basis,
                  estimated_notional = EXCLUDED.estimated_notional,
                  estimated_shares = EXCLUDED.estimated_shares,
                  raw = EXCLUDED.raw,
                  updated_at = now()
                """,
                (
                    ticket.get("ticket_id") or stable_id([run_id, symbol, ticket.get("trade_action")]),
                    run_id,
                    payload.get("as_of"),
                    payload.get("session") or "",
                    symbol,
                    ticket.get("trade_action") or "study",
                    ticket.get("status") or "open",
                    numeric(ticket.get("current_weight", ticket.get("portfolio_weight"))),
                    numeric(ticket.get("recommended_delta_weight")),
                    numeric(ticket.get("target_weight")),
                    int(ticket.get("confidence") or 0),
                    json_param(ticket.get("risk_flags") or []),
                    json_param(ticket.get("evidence") or {}),
                    ticket.get("rationale") or ticket.get("action") or "",
                    ticket.get("trigger") or "",
                    ticket.get("risk") or "",
                    ticket.get("falsifier") or "",
                    ticket.get("sizing_basis") or "portfolio-weight research proposal; approval required",
                    numeric(ticket.get("estimated_notional")),
                    numeric(ticket.get("estimated_shares")),
                    json_param(ticket),
                ),
            )
    return len(tickets)


def replace_performance_attribution(conn, run_id: str, payload: dict[str, Any]) -> int:
    benchmark = payload.get("portfolio_benchmark") or {}
    rows: list[dict[str, Any]] = []
    for role, key in [("contributor", "top_contributors"), ("detractor", "top_detractors")]:
        for row in benchmark.get(key, []):
            item = dict(row)
            item["role"] = role
            rows.append(item)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM performance_attribution WHERE run_id = %s", (run_id,))
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            cur.execute(
                """
                INSERT INTO performance_attribution
                (attribution_id, run_id, as_of, symbol, horizon, role, portfolio_weight, return_pct, contribution_pct, raw)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    stable_id([run_id, "attribution", row.get("role"), symbol]),
                    run_id,
                    payload.get("as_of"),
                    symbol,
                    benchmark.get("primary_horizon") or "5d",
                    row.get("role") or "",
                    numeric(row.get("weight")),
                    numeric(row.get("five_day_pct")),
                    numeric(row.get("contribution_pct")),
                    json_param(row),
                ),
            )
    return len(rows)


def replace_earnings_events(conn, run_id: str, payload: dict[str, Any]) -> int:
    rows = payload.get("earnings_events") or []
    with conn.cursor() as cur:
        cur.execute("DELETE FROM earnings_events WHERE run_id = %s", (run_id,))
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            cur.execute(
                """
                INSERT INTO earnings_events
                (event_id, run_id, as_of, symbol, event_date, event_type, source, title, status, days_until, catalyst_types, raw)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                """,
                (
                    stable_id([run_id, row.get("event_id") or "earnings", symbol, row.get("event_date"), row.get("event_type")]),
                    run_id,
                    payload.get("as_of"),
                    symbol,
                    row.get("event_date"),
                    row.get("event_type") or "earnings",
                    row.get("source") or "",
                    row.get("title") or "",
                    row.get("status") or "",
                    row.get("days_until"),
                    json_param(row.get("catalyst_types") or []),
                    json_param(row),
                ),
            )
    return len(rows)


def replace_calendar_events(conn, run_id: str, payload: dict[str, Any]) -> int:
    calendars = payload.get("calendars") or {}
    rows = calendars.get("earnings", {}).get("events", [])
    with conn.cursor() as cur:
        cur.execute("DELETE FROM calendar_events WHERE run_id = %s", (run_id,))
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            cur.execute(
                """
                INSERT INTO calendar_events
                (calendar_event_id, run_id, as_of, calendar_kind, symbol, event_date, event_type, source, status, confidence, raw)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    stable_id([run_id, row.get("event_id") or "calendar", symbol, row.get("event_date"), row.get("event_type")]),
                    run_id,
                    payload.get("as_of"),
                    "earnings",
                    symbol,
                    row.get("event_date"),
                    row.get("event_type") or "",
                    row.get("source") or "",
                    row.get("status") or "",
                    numeric(row.get("confidence")),
                    json_param(row),
                ),
            )
    return len(rows)


def replace_manager_filing_calendar(conn, run_id: str, payload: dict[str, Any]) -> int:
    rows = ((payload.get("calendars") or {}).get("filings_13f") or {}).get("managers", [])
    with conn.cursor() as cur:
        cur.execute("DELETE FROM manager_filing_calendar WHERE run_id = %s", (run_id,))
        for row in rows:
            key = str(row.get("manager_key") or "")
            if not key:
                continue
            cur.execute(
                """
                INSERT INTO manager_filing_calendar
                (calendar_id, run_id, manager_key, manager_name, quarter_end, deadline, status, latest_report_date, latest_filing_date, raw)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    stable_id([run_id, "13f", key, row.get("quarter_end")]),
                    run_id,
                    key,
                    row.get("manager_name") or "",
                    row.get("quarter_end"),
                    row.get("deadline"),
                    row.get("status") or "",
                    row.get("latest_report_date") or None,
                    row.get("latest_filing_date") or None,
                    json_param(row),
                ),
            )
    return len(rows)


def replace_engine_features(conn, run_id: str, payload: dict[str, Any]) -> int:
    rows = (payload.get("engine") or {}).get("ranked_candidates", [])
    with conn.cursor() as cur:
        cur.execute("DELETE FROM engine_features WHERE run_id = %s", (run_id,))
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            cur.execute(
                """
                INSERT INTO engine_features
                (feature_id, run_id, as_of, symbol, bucket, expected_return_score,
                 expected_return_rank_score, signal_family_count, current_weight,
                 external_signal_score, coverage_adjusted_external_signal_score,
                 external_coverage_multiplier, external_feed_status, external_provider_count,
                 external_provider_ok_count, external_provider_ok_ratio, external_signal_count,
                 external_source_count, raw)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    stable_id([run_id, row.get("feature_id") or "feature", symbol]),
                    run_id,
                    payload.get("as_of"),
                    symbol,
                    row.get("bucket", "unmapped"),
                    numeric(row.get("expected_return_score")),
                    numeric(row.get("expected_return_rank_score")),
                    int(row.get("signal_family_count") or 0),
                    numeric(row.get("current_weight")),
                    numeric(row.get("external_signal_score")),
                    numeric(row.get("coverage_adjusted_external_signal_score")),
                    numeric(row.get("external_coverage_multiplier")),
                    row.get("external_feed_status") or "",
                    row.get("external_provider_count"),
                    row.get("external_provider_ok_count"),
                    numeric(row.get("external_provider_ok_ratio")),
                    row.get("external_signal_count"),
                    row.get("external_source_count"),
                    json_param(row),
                ),
            )
    return len(rows)


def replace_engine_predictions(conn, run_id: str, payload: dict[str, Any]) -> int:
    engine = payload.get("engine") or {}
    rows = engine.get("recommendation_provenance", [])
    with conn.cursor() as cur:
        cur.execute("DELETE FROM engine_predictions WHERE run_id = %s", (run_id,))
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            cur.execute(
                """
                INSERT INTO engine_predictions
                (prediction_id, run_id, as_of, symbol, model_policy_version, expected_return_rank_score,
                 current_weight, recommended_delta_weight, target_weight, raw)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    stable_id([run_id, "prediction", symbol]),
                    run_id,
                    payload.get("as_of"),
                    symbol,
                    row.get("model_policy_version") or engine.get("version") or "",
                    numeric(row.get("expected_return_rank_score")),
                    numeric(row.get("current_weight")),
                    numeric(row.get("recommended_delta_weight")),
                    numeric(row.get("target_weight")),
                    json_param(row),
                ),
            )
    return len(rows)


def replace_paper_trades(conn, run_id: str, payload: dict[str, Any]) -> int:
    rows = (payload.get("paper_portfolio") or {}).get("paper_trades", [])
    with conn.cursor() as cur:
        cur.execute("DELETE FROM paper_trades WHERE run_id = %s", (run_id,))
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            cur.execute(
                """
                INSERT INTO paper_trades
                (paper_trade_id, run_id, ticket_id, as_of, symbol, trade_action, status,
                 current_weight, recommended_delta_weight, target_weight, proxy_fill_price, raw)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    stable_id([run_id, row.get("paper_trade_id") or "paper", symbol, row.get("ticket_id")]),
                    run_id,
                    row.get("ticket_id") or "",
                    payload.get("as_of"),
                    symbol,
                    row.get("trade_action") or "",
                    row.get("status") or "",
                    numeric(row.get("current_weight")),
                    numeric(row.get("recommended_delta_weight")),
                    numeric(row.get("target_weight")),
                    numeric(row.get("proxy_fill_price")),
                    json_param(row),
                ),
            )
    return len(rows)


def replace_paper_portfolio_snapshots(conn, run_id: str, payload: dict[str, Any]) -> int:
    rows = (payload.get("paper_portfolio") or {}).get("snapshots", [])
    with conn.cursor() as cur:
        cur.execute("DELETE FROM paper_portfolio_snapshots WHERE run_id = %s", (run_id,))
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            cur.execute(
                """
                INSERT INTO paper_portfolio_snapshots
                (snapshot_id, run_id, as_of, symbol, current_weight, paper_target_weight, paper_delta_weight, raw)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    stable_id([run_id, "paper_snapshot", symbol]),
                    run_id,
                    payload.get("as_of"),
                    symbol,
                    numeric(row.get("current_weight")),
                    numeric(row.get("paper_target_weight")),
                    numeric(row.get("paper_delta_weight")),
                    json_param(row),
                ),
            )
    return len(rows)


def replace_backtest_run(conn, run_id: str, payload: dict[str, Any]) -> int:
    backtest = payload.get("backtest") or {}
    if not backtest:
        return 0
    backtest_id = stable_id([run_id, "backtest", backtest.get("version") or ""])
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO backtest_runs
            (backtest_id, run_id, as_of, version, model_policy_version, source_report_count,
             trial_count, completed_outcome_count, pending_outcome_count, calibration,
             horizon_summary, external_feed_status_summary, external_coverage_summary, raw, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, now())
            ON CONFLICT (backtest_id) DO UPDATE SET
              run_id = EXCLUDED.run_id,
              as_of = EXCLUDED.as_of,
              version = EXCLUDED.version,
              model_policy_version = EXCLUDED.model_policy_version,
              source_report_count = EXCLUDED.source_report_count,
              trial_count = EXCLUDED.trial_count,
              completed_outcome_count = EXCLUDED.completed_outcome_count,
              pending_outcome_count = EXCLUDED.pending_outcome_count,
              calibration = EXCLUDED.calibration,
              horizon_summary = EXCLUDED.horizon_summary,
              external_feed_status_summary = EXCLUDED.external_feed_status_summary,
              external_coverage_summary = EXCLUDED.external_coverage_summary,
              raw = EXCLUDED.raw,
              updated_at = now()
            """,
            (
                backtest_id,
                run_id,
                backtest.get("as_of") or payload.get("as_of"),
                backtest.get("version") or "",
                backtest.get("model_policy_version") or "",
                int(backtest.get("source_report_count") or 0),
                int(backtest.get("trial_count") or 0),
                int(backtest.get("completed_outcome_count") or 0),
                int(backtest.get("pending_outcome_count") or 0),
                json_param(backtest.get("calibration") or {}),
                json_param(backtest.get("horizons") or []),
                json_param(backtest.get("by_external_feed_status") or []),
                json_param(backtest.get("by_external_coverage") or []),
                json_param(backtest),
            ),
        )
    return 1


def replace_backtest_outcomes(conn, run_id: str, payload: dict[str, Any]) -> int:
    backtest = payload.get("backtest") or {}
    rows = backtest.get("outcomes") or []
    backtest_id = stable_id([run_id, "backtest", backtest.get("version") or ""])
    with conn.cursor() as cur:
        cur.execute("DELETE FROM backtest_outcomes WHERE run_id = %s", (run_id,))
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            cur.execute(
                """
                INSERT INTO backtest_outcomes
                (outcome_id, run_id, backtest_id, trial_id, as_of, session, symbol, bucket,
                 trade_action, horizon, status, direction, entry_date, exit_date, entry_price,
                 exit_price, raw_forward_return_pct, decision_forward_return_pct,
                 risk_adjusted_expected_return, expected_vs_realized_error, signal_families,
                 external_signal_score, coverage_adjusted_external_signal_score,
                 external_coverage_multiplier, external_feed_status, external_provider_count,
                 external_provider_ok_count, external_provider_ok_ratio, external_signal_count,
                 external_source_count, raw)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    row.get("outcome_id") or stable_id([run_id, "backtest_outcome", symbol, row.get("horizon")]),
                    run_id,
                    backtest_id,
                    row.get("trial_id") or "",
                    row.get("as_of") or payload.get("as_of"),
                    row.get("session") or payload.get("session") or "",
                    symbol,
                    row.get("bucket", "unmapped"),
                    row.get("trade_action") or "",
                    row.get("horizon") or "",
                    row.get("status") or "",
                    int(row.get("direction") or 0),
                    row.get("entry_date") or None,
                    row.get("exit_date") or None,
                    numeric(row.get("entry_price")),
                    numeric(row.get("exit_price")),
                    numeric(row.get("raw_forward_return_pct")),
                    numeric(row.get("decision_forward_return_pct")),
                    numeric(row.get("risk_adjusted_expected_return")),
                    numeric(row.get("expected_vs_realized_error")),
                    json_param(row.get("signal_families") or []),
                    numeric(row.get("external_signal_score")),
                    numeric(row.get("coverage_adjusted_external_signal_score")),
                    numeric(row.get("external_coverage_multiplier")),
                    row.get("external_feed_status") or "",
                    row.get("external_provider_count"),
                    row.get("external_provider_ok_count"),
                    numeric(row.get("external_provider_ok_ratio")),
                    row.get("external_signal_count"),
                    row.get("external_source_count"),
                    json_param(row),
                ),
            )
    return len(rows)


def replace_recommendation_training_examples(conn, run_id: str, payload: dict[str, Any]) -> int:
    rows = payload.get("recommendation_training_examples") or []
    with conn.cursor() as cur:
        cur.execute("DELETE FROM recommendation_training_examples WHERE run_id = %s", (run_id,))
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            cur.execute(
                """
                INSERT INTO recommendation_training_examples
                (example_id, run_id, ticket_id, as_of, session, symbol, bucket, model_policy_version,
                 trade_action, current_weight, recommended_delta_weight, target_weight,
                 risk_adjusted_expected_return, base_risk_adjusted_expected_return,
                 base_evidence_quality, base_drawdown_risk, llm_signal_applied,
                 llm_expected_return_delta, llm_expected_return_adjustment,
                 llm_evidence_quality_delta, llm_evidence_quality_adjustment,
                 llm_drawdown_risk_delta, llm_drawdown_risk_adjustment,
                 llm_conviction_score, llm_variant_quality_score, llm_source_quality_score,
                 llm_contradiction_risk_score, llm_staleness_risk_score, llm_review_required,
                 external_signal_score,
                 coverage_adjusted_external_signal_score, external_coverage_multiplier,
                 external_feed_status, external_provider_count, external_provider_ok_count,
                 external_provider_ok_ratio, external_signal_count, external_source_count,
                 forward_return_labels, raw)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                """,
                (
                    row.get("example_id") or stable_id([run_id, "training", symbol, row.get("ticket_id")]),
                    run_id,
                    row.get("ticket_id") or "",
                    row.get("as_of") or payload.get("as_of"),
                    row.get("session") or payload.get("session") or "",
                    symbol,
                    row.get("bucket", "unmapped"),
                    row.get("model_policy_version") or "",
                    row.get("trade_action") or "study",
                    numeric(row.get("current_weight")),
                    numeric(row.get("recommended_delta_weight")),
                    numeric(row.get("target_weight")),
                    numeric(row.get("risk_adjusted_expected_return")),
                    numeric(row.get("base_risk_adjusted_expected_return")),
                    numeric(row.get("base_evidence_quality")),
                    numeric(row.get("base_drawdown_risk")),
                    bool(row.get("llm_signal_applied", False)),
                    numeric(row.get("llm_expected_return_delta")),
                    numeric(row.get("llm_expected_return_adjustment")),
                    numeric(row.get("llm_evidence_quality_delta")),
                    numeric(row.get("llm_evidence_quality_adjustment")),
                    numeric(row.get("llm_drawdown_risk_delta")),
                    numeric(row.get("llm_drawdown_risk_adjustment")),
                    numeric(row.get("llm_conviction_score")),
                    numeric(row.get("llm_variant_quality_score")),
                    numeric(row.get("llm_source_quality_score")),
                    numeric(row.get("llm_contradiction_risk_score")),
                    numeric(row.get("llm_staleness_risk_score")),
                    bool(row.get("llm_review_required", False)),
                    numeric(row.get("external_signal_score")),
                    numeric(row.get("coverage_adjusted_external_signal_score")),
                    numeric(row.get("external_coverage_multiplier")),
                    row.get("external_feed_status") or "",
                    row.get("external_provider_count"),
                    row.get("external_provider_ok_count"),
                    numeric(row.get("external_provider_ok_ratio")),
                    row.get("external_signal_count"),
                    row.get("external_source_count"),
                    json_param(row.get("forward_return_labels") or {}),
                    json_param(row),
                ),
            )
    return len(rows)


def upsert_model_policy_version(conn, payload: dict[str, Any]) -> int:
    engine = payload.get("engine") or {}
    version = engine.get("version")
    if not version:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO model_policy_versions
            (policy_version, objective, mode, universe, status, raw, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, now())
            ON CONFLICT (policy_version) DO UPDATE SET
              objective = EXCLUDED.objective,
              mode = EXCLUDED.mode,
              universe = EXCLUDED.universe,
              status = EXCLUDED.status,
              raw = EXCLUDED.raw,
              updated_at = now()
            """,
            (
                version,
                engine.get("objective") or "",
                engine.get("mode") or "",
                engine.get("universe") or "",
                "active",
                json_param(engine),
            ),
        )
    return 1


def action_queue_to_tickets(payload: dict[str, Any]) -> list[dict[str, Any]]:
    benchmark = payload.get("portfolio_benchmark") or {}
    return [dict(row) for row in benchmark.get("action_queue", [])]


def record_decision(
    ticket_id: str,
    decision: str,
    notes: str = "",
    rejection_reason: str = "",
    execution_status: str = "not_executed",
    db_url: str | None = None,
) -> dict[str, Any]:
    conn = connect_warehouse(db_url)
    try:
        migrate(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO decision_ledger
                (ticket_id, decision, notes, rejection_reason, execution_status, raw, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, now())
                ON CONFLICT (ticket_id) DO UPDATE SET
                  decision = EXCLUDED.decision,
                  notes = EXCLUDED.notes,
                  rejection_reason = EXCLUDED.rejection_reason,
                  execution_status = EXCLUDED.execution_status,
                  raw = EXCLUDED.raw,
                  updated_at = now()
                """,
                (
                    ticket_id,
                    decision,
                    notes,
                    rejection_reason,
                    execution_status,
                    json_param({"source": "cli"}),
                ),
            )
        return {"status": "recorded", "ticket_id": ticket_id, "decision": decision}
    finally:
        conn.close()


def list_recommendations(status: str = "open", limit: int = 50, db_url: str | None = None) -> list[dict[str, Any]]:
    conn = connect_warehouse(db_url)
    try:
        migrate(conn)
        with conn.cursor() as cur:
            if status == "open":
                cur.execute(
                    """
                    SELECT r.*, d.decision, d.notes, d.execution_status
                    FROM trade_recommendations r
                    LEFT JOIN decision_ledger d ON d.ticket_id = r.ticket_id
                    WHERE d.ticket_id IS NULL AND r.status = 'open'
                    ORDER BY r.as_of DESC, ABS(r.recommended_delta_weight) DESC, r.symbol
                    LIMIT %s
                    """,
                    (limit,),
                )
            else:
                cur.execute(
                    """
                    SELECT r.*, d.decision, d.notes, d.execution_status
                    FROM trade_recommendations r
                    LEFT JOIN decision_ledger d ON d.ticket_id = r.ticket_id
                    WHERE COALESCE(d.decision, r.status) = %s
                    ORDER BY r.as_of DESC, ABS(r.recommended_delta_weight) DESC, r.symbol
                    LIMIT %s
                    """,
                    (status, limit),
                )
            return normalize_rows(cur.fetchall())
    finally:
        conn.close()


def export_latest_tickets_from_reports(reports_dir: Path) -> list[dict[str, Any]]:
    paths = sorted(reports_dir.glob("*.json"), key=lambda path: (path.stat().st_mtime, path.name))
    if not paths:
        return []
    payload = json.loads(paths[-1].read_text(encoding="utf-8"))
    return payload.get("approval_tickets") or action_queue_to_tickets(payload)


def format_tickets_markdown(tickets: list[dict[str, Any]]) -> str:
    lines = ["# AlloIQ Approval Tickets", ""]
    if not tickets:
        lines.append("- No open approval tickets.")
        return "\n".join(lines) + "\n"
    for ticket in tickets:
        lines.append(f"## {ticket.get('symbol', 'Ticket')} - {ticket.get('trade_action', 'study')}")
        lines.append(f"- Ticket: `{ticket.get('ticket_id', '')}`")
        lines.append(f"- Delta: {ticket.get('recommended_delta_weight', 0)}; target: {ticket.get('target_weight', 0)}")
        lines.append(f"- Confidence: {ticket.get('confidence', 0)}")
        lines.append(f"- Rationale: {ticket.get('rationale') or ticket.get('action') or ''}")
        lines.append(f"- Trigger: {ticket.get('trigger', '')}")
        lines.append(f"- Risk: {ticket.get('risk', '')}")
        lines.append("")
    return "\n".join(lines)


def pipeline_run_id(payload: dict[str, Any], pipeline_result: dict[str, Any]) -> str:
    schedule = pipeline_result.get("schedule") or {}
    return stable_id(
        [
            pipeline_result.get("kind") or payload.get("session") or "manual",
            pipeline_result.get("status") or "unknown",
            payload.get("as_of") or "",
            schedule.get("scheduled_at_utc") or datetime.utcnow().isoformat(),
        ]
    )


def portfolio_snapshot_id(payload: dict[str, Any]) -> str:
    return stable_id([payload.get("as_of") or date.today().isoformat(), payload.get("session") or "manual", "portfolio"])


def warehouse_tables() -> list[str]:
    return [
        "pipeline_runs",
        "portfolio_snapshots",
        "position_snapshots",
        "signal_snapshots",
        "research_snapshots",
        "company_underwriting_snapshots",
        "sector_underwriting_snapshots",
        "llm_signal_snapshots",
        "trade_recommendations",
        "decision_ledger",
        "performance_attribution",
        "earnings_events",
        "calendar_events",
        "manager_filing_calendar",
        "engine_features",
        "engine_predictions",
        "paper_trades",
        "paper_portfolio_snapshots",
        "recommendation_outcomes",
        "backtest_runs",
        "backtest_outcomes",
        "recommendation_training_examples",
        "model_policy_versions",
    ]


def numeric(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def json_param(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True, default=str)


def normalize_rows(rows: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            normalized.append(dict(row))
        else:
            normalized.append(dict(row))
    return normalized
