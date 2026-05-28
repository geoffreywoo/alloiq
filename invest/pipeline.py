from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from typing import Any

from .brokers.ibkr import FlexError, fetch_flex_statement, parse_flex_xml
from .brokers.vanguard import parse_vanguard_file, parse_vanguard_positions_file
from .config import AppConfig
from .db import (
    insert_positions,
    insert_transactions,
    manager_filing_holding_count,
    record_import,
    upsert_filing,
)
from .filings.sec import DEFAULT_CUSIP_SYMBOL_MAP, DEFAULT_ISSUER_SYMBOL_MAP, fetch_13f_holdings, fetch_recent_filings
from .privacy import assert_public_assets_safe
from .quality import assert_public_snapshot_quality
from .reports import generate_brief, rebuild_portfolio_performance_analytics
from .scheduler import parse_scheduled_at, should_run_pipeline
from .site import build_site
from .warehouse import sync_report_payload


class PortfolioSnapshotRegression(RuntimeError):
    pass


BROKER_SYNC_KINDS = {"premarket", "midday", "postmarket", "weekly"}
PIPELINE_RESULT_KEYS = frozenset({"kind", "privacy", "schedule", "status"})
MIN_SUSPICIOUS_PUBLIC_PORTFOLIO_SYMBOLS = 8
SUSPICIOUS_PUBLIC_PORTFOLIO_SHRINK_RATIO = 0.5


def extract_pipeline_result_json(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    result: dict[str, Any] | None = None
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and PIPELINE_RESULT_KEYS.issubset(candidate):
            result = candidate
    if result is None:
        raise ValueError("No pipeline result JSON object found in pipeline output")
    return result


def run_pipeline(
    conn,
    config: AppConfig,
    kind: str,
    privacy: str = "public",
    out_dir: Path = Path("web"),
    force: bool = False,
    scheduled_at: str | None = None,
) -> dict[str, Any]:
    schedule = should_run_pipeline(kind, parse_scheduled_at(scheduled_at), force=force)
    result: dict[str, Any] = {
        "kind": kind,
        "privacy": privacy,
        "schedule": schedule.as_dict(),
        "status": "skipped",
        "reason": schedule.reason,
    }
    if not schedule.should_run:
        result["warehouse"] = sync_report_payload(None, result)
        return result

    filing_result = refresh_filings(conn, config)
    broker_result = (
        sync_brokers(conn, config)
        if kind in BROKER_SYNC_KINDS
        else {"imported": 0, "status": "not_run", "reason": f"{kind} refresh reuses latest stored broker positions"}
    )
    fallback_candidate = previous_public_portfolio_fallback(out_dir, broker_result, privacy)
    manager_fallback_candidate = previous_public_manager_radar_fallback(out_dir, privacy)
    md_path, json_path = generate_brief(conn, config, kind)
    report_payload = json.loads(json_path.read_text(encoding="utf-8")) if json_path.exists() else {}
    fallback_applied = apply_public_portfolio_fallback_if_needed(
        report_payload,
        fallback_candidate,
        broker_result,
        out_dir,
        privacy,
    )
    manager_fallback_applied = apply_public_manager_radar_fallback_if_needed(
        report_payload,
        manager_fallback_candidate,
        privacy,
    )
    if (fallback_applied or manager_fallback_applied) and json_path.exists():
        json_path.write_text(json.dumps(report_payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    regression_reason = public_portfolio_regression_reason(report_payload, broker_result, out_dir, privacy)
    if regression_reason:
        deferred_result = {
            **result,
            "status": "deferred",
            "reason": regression_reason,
            "filings": filing_result,
            "brokers": broker_result,
            "report_markdown": str(md_path),
            "report_json": str(json_path),
        }
        if manager_fallback_applied:
            deferred_result["manager_radar_fallback"] = manager_fallback_applied
        deferred_result["warehouse"] = sync_report_payload(None, deferred_result)
        return deferred_result
    site_result = build_site(
        config.reports_dir,
        out_dir,
        privacy=privacy,
        run_kind=kind,
        workflow=github_workflow_metadata(),
    )
    if privacy == "public":
        assert_public_assets_safe(out_dir)
        assert_public_snapshot_quality(out_dir)
    ran_result = {
        **result,
        "status": "ran",
        "filings": filing_result,
        "brokers": broker_result,
        "report_markdown": str(md_path),
        "report_json": str(json_path),
        "site": site_result,
    }
    if fallback_applied:
        ran_result["portfolio_fallback"] = fallback_applied
    if manager_fallback_applied:
        ran_result["manager_radar_fallback"] = manager_fallback_applied
    ran_result["warehouse"] = sync_report_payload(report_payload, ran_result)
    return ran_result


def assert_no_public_portfolio_regression(
    report_payload: dict[str, Any],
    broker_result: dict[str, Any],
    out_dir: Path,
    privacy: str,
) -> None:
    reason = public_portfolio_regression_reason(report_payload, broker_result, out_dir, privacy)
    if reason:
        raise PortfolioSnapshotRegression(reason)


def public_portfolio_regression_reason(
    report_payload: dict[str, Any],
    broker_result: dict[str, Any],
    out_dir: Path,
    privacy: str,
) -> str:
    if privacy != "public":
        return ""
    previous = load_existing_public_snapshot(out_dir)
    if not previous:
        return ""
    previous_portfolio = previous.get("portfolio") or {}
    current_portfolio = report_payload.get("portfolio") or {}
    previous_symbols = int(previous_portfolio.get("symbol_count") or 0)
    current_symbols = int(current_portfolio.get("symbol_count") or 0)
    previous_rows = int(previous_portfolio.get("position_count") or 0)
    current_rows = int(current_portfolio.get("position_count") or 0)
    broker_problem = broker_sync_has_problem(broker_result)
    if broker_problem and previous_symbols and current_symbols < previous_symbols:
        return (
            "Refusing to publish public snapshot because broker sync failed/skipped "
            f"and portfolio symbol count would shrink from {previous_symbols} to {current_symbols}."
        )
    if broker_problem and previous_rows and current_rows < previous_rows:
        return (
            "Refusing to publish public snapshot because broker sync failed/skipped "
            f"and portfolio position rows would shrink from {previous_rows} to {current_rows}."
        )
    if suspicious_public_portfolio_shrink(previous_symbols, current_symbols, previous_rows, current_rows):
        return (
            "Refusing to publish public snapshot because the current position snapshot looks incomplete: "
            f"portfolio symbol count would shrink from {previous_symbols} to {current_symbols}."
        )
    return ""


def suspicious_public_portfolio_shrink(
    previous_symbols: int,
    current_symbols: int,
    previous_rows: int,
    current_rows: int,
) -> bool:
    symbol_shrink = (
        previous_symbols >= MIN_SUSPICIOUS_PUBLIC_PORTFOLIO_SYMBOLS
        and current_symbols < previous_symbols * SUSPICIOUS_PUBLIC_PORTFOLIO_SHRINK_RATIO
    )
    row_shrink = (
        previous_rows >= MIN_SUSPICIOUS_PUBLIC_PORTFOLIO_SYMBOLS
        and current_rows < previous_rows * SUSPICIOUS_PUBLIC_PORTFOLIO_SHRINK_RATIO
    )
    return symbol_shrink or row_shrink


def previous_public_portfolio_fallback(
    out_dir: Path,
    broker_result: dict[str, Any],
    privacy: str,
) -> dict[str, Any] | None:
    if privacy != "public":
        return None
    previous = load_existing_public_snapshot(out_dir)
    previous_portfolio = previous.get("portfolio") or {}
    if not previous_portfolio.get("by_symbol"):
        return None
    site = previous.get("site") or {}
    return {
        "portfolio": report_portfolio_from_public_snapshot(previous_portfolio),
        "benchmark": previous.get("portfolio_benchmark") or {},
        "metadata": {
            "status": "used_previous_public_portfolio",
            "reason": public_portfolio_fallback_reason(broker_result),
            "source": "web/data/latest.json",
            "source_report": site.get("source_report", ""),
            "source_built_at": site.get("built_at", ""),
            "previous_symbol_count": int(previous_portfolio.get("symbol_count") or 0),
            "previous_position_count": int(previous_portfolio.get("position_count") or 0),
        },
    }


def public_portfolio_fallback_reason(broker_result: dict[str, Any]) -> str:
    if broker_sync_has_problem(broker_result):
        return broker_problem_summary(broker_result)
    return "Current public position snapshot appears incomplete relative to the previous public snapshot."


def apply_public_portfolio_fallback_if_needed(
    report_payload: dict[str, Any],
    fallback: dict[str, Any] | None,
    broker_result: dict[str, Any],
    out_dir: Path,
    privacy: str,
) -> dict[str, Any] | None:
    if not fallback:
        return None
    regression_reason = public_portfolio_regression_reason(report_payload, broker_result, out_dir, privacy)
    if not regression_reason:
        return None
    metadata = dict(fallback["metadata"])
    metadata["regression_reason"] = regression_reason
    fallback_portfolio = deepcopy(fallback["portfolio"])
    report_payload["portfolio"] = fallback_portfolio
    report_payload["portfolio_fallback"] = metadata
    report_payload["positions"] = {
        row["symbol"]: 0.0
        for row in fallback_portfolio.get("by_symbol", [])
        if row.get("symbol")
    }
    fallback_weights = {
        str(row.get("symbol") or "").upper(): float(row.get("comparison_weight") or row.get("ex_cash_weight") or 0)
        for row in fallback_portfolio.get("by_symbol", [])
        if row.get("symbol") and not row.get("is_cash")
    }
    update_portfolio_weight_fields(report_payload, fallback_weights)
    sync_engine_optimizer_from_tickets(report_payload)
    refresh_fallback_portfolio_benchmark(report_payload, fallback.get("benchmark") or {})
    normalize_fallback_rebalance_budget(report_payload)
    append_portfolio_fallback_health(report_payload, metadata)
    print(f"Using previous public portfolio fallback: {regression_reason}")
    return metadata


def refresh_fallback_portfolio_benchmark(
    report_payload: dict[str, Any],
    previous_benchmark: dict[str, Any],
) -> None:
    benchmark = report_payload.get("portfolio_benchmark")
    portfolio = report_payload.get("portfolio") or {}
    if not isinstance(benchmark, dict):
        return
    return_windows = report_payload.get("market_return_windows") or {}
    if return_windows:
        report_payload["portfolio_benchmark"] = rebuild_portfolio_performance_analytics(
            benchmark,
            portfolio,
            report_payload.get("manager_radar") or {},
            report_payload.get("macro") or {},
            return_windows,
        )
        return
    if not isinstance(previous_benchmark, dict) or not previous_benchmark:
        return
    preserve_keys = {
        "portfolio_return_5d",
        "total_portfolio_return_5d",
        "price_coverage_pct",
        "total_price_coverage_pct",
        "primary_horizon",
        "primary_label",
        "primary_portfolio_return",
        "primary_price_coverage_pct",
        "horizon_returns",
        "total_horizon_returns",
        "equity_horizon_returns",
        "primary_equity_return",
        "primary_equity_price_coverage_pct",
        "return_analytics",
        "performance_universe",
        "performance_components",
        "benchmarks",
        "peer_proxies",
        "top_contributors",
        "top_detractors",
    }
    for key in preserve_keys:
        if key in previous_benchmark:
            benchmark[key] = deepcopy(previous_benchmark[key])
    universe = benchmark.get("performance_universe")
    if isinstance(universe, dict):
        universe["restored_after_portfolio_fallback"] = True


def previous_public_manager_radar_fallback(out_dir: Path, privacy: str) -> dict[str, Any] | None:
    if privacy != "public":
        return None
    previous = load_existing_public_snapshot(out_dir)
    previous_radar = previous.get("manager_radar") or {}
    if not previous_manager_radar_has_positions(previous_radar):
        return None
    site = previous.get("site") or {}
    return {
        "manager_radar": previous_radar,
        "metadata": {
            "status": "used_previous_public_manager_radar",
            "source": "web/data/latest.json",
            "source_report": site.get("source_report", ""),
            "source_built_at": site.get("built_at", ""),
            "previous_stored_latest_count": int(previous_radar.get("stored_latest_count") or 0),
            "previous_manager_count": int(previous_radar.get("manager_count") or 0),
        },
    }


def apply_public_manager_radar_fallback_if_needed(
    report_payload: dict[str, Any],
    fallback: dict[str, Any] | None,
    privacy: str,
) -> dict[str, Any] | None:
    if privacy != "public" or not fallback:
        return None
    regression_reason = public_manager_radar_regression_reason(report_payload, fallback)
    if not regression_reason:
        return None
    metadata = dict(fallback["metadata"])
    metadata["regression_reason"] = regression_reason
    report_payload["manager_radar"] = deepcopy(fallback["manager_radar"])
    report_payload["manager_radar_fallback"] = metadata
    append_manager_radar_fallback_health(report_payload, metadata)
    print(f"Using previous public manager radar fallback: {regression_reason}")
    return metadata


def public_manager_radar_regression_reason(report_payload: dict[str, Any], fallback: dict[str, Any]) -> str:
    previous = fallback.get("manager_radar") or {}
    current = report_payload.get("manager_radar") or {}
    previous_stored = int(previous.get("stored_latest_count") or 0)
    current_stored = int(current.get("stored_latest_count") or 0)
    if previous_stored and current_stored < previous_stored:
        return (
            "Refusing to publish manager radar regression because stored manager filing count "
            f"would shrink from {previous_stored} to {current_stored}."
        )
    missing = managers_losing_known_positions(current, previous)
    if missing:
        names = ", ".join(missing[:4])
        suffix = f", and {len(missing) - 4} more" if len(missing) > 4 else ""
        return (
            "Refusing to publish manager radar regression because previously known focus-manager "
            f"positions are missing for {names}{suffix}."
        )
    return ""


def previous_manager_radar_has_positions(radar: dict[str, Any]) -> bool:
    return any(manager_has_positions(row) for row in radar.get("focus_managers", []))


def managers_losing_known_positions(current: dict[str, Any], previous: dict[str, Any]) -> list[str]:
    current_by_key = {
        str(row.get("manager_key") or ""): row
        for row in current.get("focus_managers", [])
        if row.get("manager_key")
    }
    missing: list[str] = []
    for previous_row in previous.get("focus_managers", []):
        if not manager_has_positions(previous_row):
            continue
        manager_key = str(previous_row.get("manager_key") or "")
        current_row = current_by_key.get(manager_key)
        if manager_has_positions(current_row or {}):
            continue
        missing.append(str(previous_row.get("manager_name") or manager_key))
    return missing


def manager_has_positions(row: dict[str, Any]) -> bool:
    if row.get("status") != "ok":
        return False
    return bool(row.get("positions") or row.get("top_positions"))


def append_manager_radar_fallback_health(report_payload: dict[str, Any], metadata: dict[str, Any]) -> None:
    data_health = report_payload.setdefault("data_health", {})
    sources = data_health.setdefault("sources", [])
    sources.insert(
        0,
        {
            "source": "manager_radar_fallback",
            "label": "Manager 13F fallback",
            "status": "stale",
            "detail": (
                "Using previous public manager 13F radar because the newly generated manager data "
                "would have regressed."
            ),
            "source_report": metadata.get("source_report", ""),
            "source_built_at": metadata.get("source_built_at", ""),
        },
    )
    data_health["recommendation_posture"] = "reduced_confidence"
    if not data_health.get("summary") or data_health.get("summary") == "ok":
        data_health["summary"] = (
            "Manager radar uses the previous public snapshot because the current refresh missed known 13F data; "
            "recommendations remain approval-only."
        )
    weak_statuses = {"missing", "stale", "limited", "estimated", "unknown", "failed", "error"}
    data_health["weak_source_count"] = sum(1 for row in sources if row.get("status") in weak_statuses)


def report_portfolio_from_public_snapshot(portfolio: dict[str, Any]) -> dict[str, Any]:
    cash_weight = max(0.0, float(portfolio.get("cash_weight") or 0))
    equity_weight = max(0.0, float(portfolio.get("equity_weight", 1.0 - cash_weight) or 0))
    symbol_rows = [
        report_position_row_from_public(row, equity_weight)
        for row in portfolio.get("by_symbol", [])
        if row.get("symbol") and not row.get("is_cash")
    ]
    bucket_rows = [
        report_bucket_row_from_public(row, equity_weight)
        for row in portfolio.get("by_bucket", [])
        if row.get("bucket") != "cash_reserves"
    ]
    return {
        "position_count": int(portfolio.get("position_count") or len(symbol_rows)),
        "symbol_count": int(portfolio.get("symbol_count") or len(symbol_rows)),
        "security_symbol_count": int(portfolio.get("security_symbol_count") or len(symbol_rows)),
        "gross_exposure": 0.0,
        "net_exposure": 0.0,
        "equity_exposure": 0.0,
        "cash_exposure": 0.0,
        "equity_weight": equity_weight,
        "cash_weight": cash_weight,
        "cash_reserves": {
            "symbol": "CASH",
            "bucket": "cash_reserves",
            "asset_class": "cash",
            "weight": cash_weight,
            "policy": "previous_public_portfolio_fallback",
        },
        "weight_basis": portfolio.get("total_weight_basis", "total_portfolio_including_cash"),
        "comparison_weight_basis": portfolio.get("weight_basis", "invested_equity_ex_cash"),
        "by_symbol": symbol_rows,
        "by_bucket": bucket_rows,
        "by_broker": [
            {
                "broker": "previous_public_snapshot",
                "market_value": 0.0,
                "weight": 1.0,
            }
        ],
        "unmapped_symbols": [
            str(row.get("symbol") or "").upper()
            for row in symbol_rows
            if row.get("bucket") == "unmapped"
        ],
    }


def report_position_row_from_public(row: dict[str, Any], equity_weight: float) -> dict[str, Any]:
    comparison = public_snapshot_comparison_weight(row)
    total_weight = public_snapshot_total_weight(row, comparison, equity_weight)
    return {
        "symbol": str(row.get("symbol") or "").upper(),
        "bucket": row.get("bucket", "unmapped"),
        "asset_class": row.get("asset_class", "equity"),
        "is_cash": False,
        "market_value": 0.0,
        "quantity": 0.0,
        "cost_basis": 0.0,
        "weight": total_weight,
        "total_weight": total_weight,
        "ex_cash_weight": comparison,
        "comparison_weight": comparison,
        "brokers": ["previous_public_snapshot"],
        "accounts": ["public_weights_fallback"],
    }


def report_bucket_row_from_public(row: dict[str, Any], equity_weight: float) -> dict[str, Any]:
    comparison = public_snapshot_comparison_weight(row)
    total_weight = public_snapshot_total_weight(row, comparison, equity_weight)
    return {
        "bucket": row.get("bucket", "unmapped"),
        "market_value": 0.0,
        "weight": total_weight,
        "total_weight": total_weight,
        "ex_cash_weight": comparison,
        "comparison_weight": comparison,
    }


def public_snapshot_comparison_weight(row: dict[str, Any]) -> float:
    for key in ("comparison_weight", "ex_cash_weight", "weight"):
        if row.get(key) is not None:
            return max(0.0, float(row.get(key) or 0))
    return 0.0


def public_snapshot_total_weight(row: dict[str, Any], comparison: float, equity_weight: float) -> float:
    if row.get("total_weight") is not None:
        return max(0.0, float(row.get("total_weight") or 0))
    return max(0.0, comparison * equity_weight)


def update_portfolio_weight_fields(value: Any, fallback_weights: dict[str, float]) -> None:
    if isinstance(value, list):
        for item in value:
            update_portfolio_weight_fields(item, fallback_weights)
        return
    if not isinstance(value, dict):
        return
    symbol = str(value.get("symbol") or "").upper()
    is_action = action_weight_row(value)
    if symbol and (symbol in fallback_weights or is_action):
        current_weight = round(fallback_weights.get(symbol, 0.0), 6)
        for key in ("portfolio_weight", "current_weight", "current_portfolio_weight"):
            if key in value:
                value[key] = current_weight
        if is_action:
            value["recommended_delta_weight"] = 0.0
            value["post_action_weight"] = current_weight
            value["target_weight"] = current_weight
            value["trade_target_weight"] = current_weight
            value["trade_action"] = "hold"
            value["fallback_research_only"] = True
            value["fallback_reason"] = "Live broker sync failed; keeping prior public portfolio weight."
            max_allowed = float(value.get("max_allowed_weight") or current_weight or 0)
            if "model_target_weight" in value:
                value["model_target_weight"] = round(min(current_weight, max_allowed), 6)
            if "risk_adjusted_target_weight" in value:
                value["risk_adjusted_target_weight"] = round(min(current_weight, max_allowed), 6)
    for item in value.values():
        update_portfolio_weight_fields(item, fallback_weights)


def action_weight_row(value: dict[str, Any]) -> bool:
    return any(
        key in value
        for key in (
            "recommended_delta_weight",
            "post_action_weight",
            "target_weight",
            "trade_target_weight",
            "trade_action",
        )
    )


def sync_engine_optimizer_from_tickets(report_payload: dict[str, Any]) -> None:
    tickets = {
        str(row.get("symbol") or "").upper(): row
        for row in report_payload.get("approval_tickets") or []
        if isinstance(row, dict) and row.get("symbol")
    }
    if not tickets:
        return
    engine = report_payload.get("engine") or {}
    optimizer = engine.get("optimizer") or {}
    for row in optimizer.get("allocations") or []:
        if not isinstance(row, dict):
            continue
        ticket = tickets.get(str(row.get("symbol") or "").upper())
        if not ticket:
            continue
        for key in ("recommended_delta_weight", "target_weight", "model_target_weight"):
            if key in row and key in ticket:
                row[key] = ticket[key]


def normalize_fallback_rebalance_budget(report_payload: dict[str, Any]) -> None:
    benchmark = report_payload.get("portfolio_benchmark") or {}
    sizing = benchmark.get("sizing_plan") or {}
    budget = sizing.get("rebalance_budget") or {}
    if not budget:
        return
    starting_cash = float((report_payload.get("portfolio") or {}).get("cash_weight") or budget.get("starting_cash_weight") or 0)
    budget["starting_cash_weight"] = round(starting_cash, 6)
    budget["total_add_weight"] = 0.0
    budget["total_trim_weight"] = 0.0
    budget["net_delta_weight"] = 0.0
    budget["cash_deployed_weight"] = 0.0
    budget["cash_raised_weight"] = 0.0
    budget["post_trade_cash_weight"] = round(starting_cash, 6)


def append_portfolio_fallback_health(report_payload: dict[str, Any], metadata: dict[str, Any]) -> None:
    data_health = report_payload.setdefault("data_health", {})
    sources = data_health.setdefault("sources", [])
    for source in sources:
        if source.get("source") in {"broker_positions", "position_snapshot"}:
            source["status"] = "stale"
            source["detail"] = (
                "Live broker sync failed; portfolio weights use the previous public snapshot "
                "instead of the current partial broker rows."
            )
    sources.insert(
        0,
        {
            "source": "portfolio_fallback",
            "label": "Portfolio fallback",
            "status": "stale",
            "detail": (
                "Using previous public portfolio weights because the live broker sync failed "
                "and the newly generated portfolio would have regressed."
            ),
            "source_report": metadata.get("source_report", ""),
            "source_built_at": metadata.get("source_built_at", ""),
        },
    )
    data_health["recommendation_posture"] = "reduced_confidence"
    data_health["summary"] = (
        "Portfolio weights use the previous public snapshot because broker sync failed after retries; "
        "recommendations remain approval-only."
    )
    weak_statuses = {"missing", "stale", "limited", "estimated", "unknown", "failed", "error"}
    data_health["weak_source_count"] = sum(1 for row in sources if row.get("status") in weak_statuses)


def broker_problem_summary(broker_result: dict[str, Any]) -> str:
    details = broker_result.get("details") or {}
    messages = []
    for detail in details.values():
        status = str((detail or {}).get("status") or "unknown")
        if status not in {"failed", "skipped"}:
            continue
        reason = (detail or {}).get("error") or (detail or {}).get("reason") or "No broker detail emitted."
        attempts = (detail or {}).get("attempts")
        wait_seconds = (detail or {}).get("wait_seconds")
        retry_text = f" after {attempts} attempts" if attempts else ""
        wait_text = f" with {wait_seconds}s waits" if wait_seconds else ""
        messages.append(f"live broker {status}{retry_text}{wait_text}: {public_safe_broker_detail(str(reason))}")
    return "; ".join(messages) or "Broker sync failed or was skipped."


def public_safe_broker_detail(value: str) -> str:
    return (
        value.replace("IBKR Flex", "broker statement")
        .replace("IBKR", "broker")
        .replace("ibkr", "broker")
    )


def broker_sync_has_problem(broker_result: dict[str, Any]) -> bool:
    details = broker_result.get("details") or {}
    for detail in details.values():
        status = str((detail or {}).get("status") or "")
        if status in {"failed", "skipped"}:
            return True
    return False


def load_existing_public_snapshot(out_dir: Path) -> dict[str, Any]:
    path = out_dir / "data" / "latest.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def refresh_filings(conn, config: AppConfig, max_filings: int | None = 2) -> dict[str, Any]:
    managers = 0
    stored = 0
    failures: list[dict[str, str]] = []
    for manager in config.data.get("managers", []):
        key = str(manager.get("key") or "")
        if not key or not manager.get("cik"):
            continue
        managers += 1
        try:
            stored += store_filings_for_manager(conn, config, manager, max_filings=max_filings)
        except Exception as exc:
            failures.append({"manager": key, "error": str(exc)})
            print(f"{key} filings failed: {exc}")
    return {"managers": managers, "stored": stored, "failures": failures}


def store_filings_for_manager(conn, config: AppConfig, manager: dict[str, Any], max_filings: int | None = 2) -> int:
    filings = fetch_recent_filings(manager, forms={"13F-HR", "13F-HR/A"})
    if max_filings is not None:
        filings = filings[:max(0, max_filings)]
    stored = 0
    for filing in filings:
        holdings = fetch_13f_holdings(
            filing.cik,
            filing.accession_number,
            DEFAULT_CUSIP_SYMBOL_MAP,
            config.symbol_to_bucket,
            DEFAULT_ISSUER_SYMBOL_MAP,
        )
        if not holdings:
            previous_count = manager_filing_holding_count(conn, filing.manager_key)
            if previous_count:
                print(
                    f"Skipped {filing.form} {filing.accession_number}: fetched 0 holdings; "
                    "kept previous known manager holdings"
                )
                continue
            raise ValueError(f"{filing.accession_number} parsed 0 holdings and no previous holdings are stored")
        upsert_filing(conn, filing, holdings)
        stored += 1
        print(f"Stored {filing.form} {filing.accession_number} with {len(holdings)} holdings")
    return stored


def sync_brokers(conn, config: AppConfig) -> dict[str, Any]:
    imported = 0
    details: dict[str, Any] = {}
    if config.ibkr_enabled:
        count, detail = sync_ibkr(conn, config)
        imported += count
        details["ibkr"] = detail
    if config.vanguard_enabled:
        count, detail = sync_vanguard(conn, config)
        imported += count
        details["vanguard"] = detail
    return {"imported": imported, "details": details}


def sync_ibkr(conn, config: AppConfig) -> tuple[int, dict[str, Any]]:
    if not config.ibkr_token or not config.ibkr_activity_query_id:
        message = "IBKR skipped: set IBKR_FLEX_TOKEN and IBKR_FLEX_ACTIVITY_QUERY_ID"
        print(message)
        return 0, {"status": "skipped", "reason": message}
    attempts = int_env("IBKR_FLEX_ATTEMPTS", 6)
    wait_seconds = float_env("IBKR_FLEX_WAIT_SECONDS", 10.0)
    try:
        path = fetch_flex_statement(
            config.ibkr_token,
            config.ibkr_activity_query_id,
            config.ibkr_raw_dir,
            attempts=attempts,
            wait_seconds=wait_seconds,
        )
    except FlexError as exc:
        print(f"IBKR failed: {exc}")
        return 0, {
            "status": "failed",
            "error": str(exc),
            "attempts": attempts,
            "wait_seconds": wait_seconds,
            "retryable": exc.retryable,
            "error_code": exc.code,
        }
    transactions, positions = parse_flex_xml(path)
    tx_count = insert_transactions(conn, transactions)
    pos_count = insert_positions(conn, positions)
    print(f"IBKR imported {tx_count} transactions and {pos_count} positions from {path}")
    return tx_count + pos_count, {
        "status": "imported",
        "path": str(path),
        "transactions": tx_count,
        "positions": pos_count,
        "attempts": attempts,
        "wait_seconds": wait_seconds,
    }


def int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def float_env(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def sync_vanguard(conn, config: AppConfig) -> tuple[int, dict[str, Any]]:
    import_dir = config.vanguard_import_dir
    import_dir.mkdir(parents=True, exist_ok=True)
    files = sorted([p for p in import_dir.iterdir() if p.suffix.lower() in {".csv", ".qfx", ".ofx"}])
    if not files:
        message = f"Vanguard skipped: no CSV/QFX/OFX files in {import_dir}"
        print(message)
        return 0, {"status": "skipped", "reason": message}
    total = 0
    file_results = []
    for path in files:
        transactions = parse_vanguard_file(path)
        positions = parse_vanguard_positions_file(path)
        tx_count = insert_transactions(conn, transactions)
        pos_count = insert_positions(conn, positions) if positions else 0
        record_import(conn, "vanguard", path, len(transactions) + len(positions))
        total += tx_count + pos_count
        file_results.append({"path": str(path), "transactions": tx_count, "positions": pos_count})
        print(
            f"Vanguard parsed {len(transactions)} transactions and {len(positions)} positions from {path}, "
            f"imported {tx_count} new transactions and {pos_count} positions"
        )
    return total, {"status": "imported", "files": file_results}


def github_workflow_metadata() -> dict[str, str]:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return {"provider": "local", "name": "", "run_id": "", "run_attempt": "", "sha": ""}
    return {
        "provider": "github_actions",
        "name": os.environ.get("GITHUB_WORKFLOW", ""),
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "event_name": os.environ.get("GITHUB_EVENT_NAME", ""),
        "sha": os.environ.get("GITHUB_SHA", ""),
    }
