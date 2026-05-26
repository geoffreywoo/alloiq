from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .brokers.ibkr import FlexError, fetch_flex_statement, parse_flex_xml
from .brokers.vanguard import parse_vanguard_file, parse_vanguard_positions_file
from .config import AppConfig
from .db import insert_positions, insert_transactions, record_import, upsert_filing
from .filings.sec import DEFAULT_CUSIP_SYMBOL_MAP, DEFAULT_ISSUER_SYMBOL_MAP, fetch_13f_holdings, fetch_recent_filings
from .privacy import assert_public_assets_safe
from .quality import assert_public_snapshot_quality
from .reports import generate_brief
from .scheduler import parse_scheduled_at, should_run_pipeline
from .site import build_site
from .warehouse import sync_report_payload


class PortfolioSnapshotRegression(RuntimeError):
    pass


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
    broker_result = sync_brokers(conn, config)
    md_path, json_path = generate_brief(conn, config, kind)
    report_payload = json.loads(json_path.read_text(encoding="utf-8")) if json_path.exists() else {}
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
    if privacy != "public" or not broker_sync_has_problem(broker_result):
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
    if previous_symbols and current_symbols < previous_symbols:
        return (
            "Refusing to publish public snapshot because broker sync failed/skipped "
            f"and portfolio symbol count would shrink from {previous_symbols} to {current_symbols}."
        )
    if previous_rows and current_rows < previous_rows:
        return (
            "Refusing to publish public snapshot because broker sync failed/skipped "
            f"and portfolio position rows would shrink from {previous_rows} to {current_rows}."
        )
    return ""


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
    try:
        path = fetch_flex_statement(config.ibkr_token, config.ibkr_activity_query_id, config.ibkr_raw_dir)
    except FlexError as exc:
        print(f"IBKR failed: {exc}")
        return 0, {"status": "failed", "error": str(exc)}
    transactions, positions = parse_flex_xml(path)
    tx_count = insert_transactions(conn, transactions)
    pos_count = insert_positions(conn, positions)
    print(f"IBKR imported {tx_count} transactions and {pos_count} positions from {path}")
    return tx_count + pos_count, {
        "status": "imported",
        "path": str(path),
        "transactions": tx_count,
        "positions": pos_count,
    }


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
