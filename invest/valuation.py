from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from .config import AppConfig
from .market import fetch_daily_prices
from .symbols import proxied_lookup
from .util import decimal_or_zero


AI_MAXXI_MANAGER_KEYS = ["situational-awareness", "altimeter", "dragoneer"]
VALUATION_VERSION = "2026-05-13f-entry-current-value-v1"


def build_valuation_snapshot(
    conn: sqlite3.Connection,
    config: AppConfig,
    as_of: date | None = None,
    prices: dict[str, dict[str, Decimal]] | None = None,
    manager_keys: list[str] | None = None,
) -> dict[str, Any]:
    as_of = as_of or date.today()
    manager_keys = manager_keys or AI_MAXXI_MANAGER_KEYS
    prices = prices or fetch_daily_prices(manager_valuation_symbols(conn, manager_keys))
    return {
        "version": VALUATION_VERSION,
        "as_of": as_of.isoformat(),
        "manager_scope": manager_keys,
        "methodology": valuation_methodology(),
        "managers": build_manager_valuation_snapshot(conn, config, prices, manager_keys),
        "portfolio": {},
    }


def valuation_methodology() -> dict[str, Any]:
    return {
        "manager_entry_proxy": "Observed 13F share-delta weighted average. First observed position uses the quarter-end 13F implied mark; later additions use the quarter-end mark for added units; reductions reduce units at the running proxy basis.",
        "manager_current_value_proxy": "Latest reported 13F common-stock units multiplied by latest available free daily close.",
        "portfolio_entry_proxy": "Broker cost basis divided by quantity when available; otherwise latest position mark is treated as a weak proxy.",
        "caveat": "13F filings do not disclose true entry price, intraday trading, shorts, private marks, or post-quarter changes.",
    }


def manager_valuation_symbols(conn: sqlite3.Connection, manager_keys: list[str] | None = None) -> list[str]:
    manager_keys = manager_keys or AI_MAXXI_MANAGER_KEYS
    latest_ids = [row["id"] for row in latest_selected_filings(conn, manager_keys).values()]
    if not latest_ids:
        return []
    placeholders = ",".join("?" for _ in latest_ids)
    rows = conn.execute(
        f"""
        SELECT DISTINCT UPPER(symbol) AS symbol
        FROM filing_holdings
        WHERE filing_id IN ({placeholders})
          AND COALESCE(put_call, '') = ''
          AND symbol != ''
        ORDER BY symbol
        """,
        latest_ids,
    ).fetchall()
    return [row["symbol"] for row in rows if row["symbol"]]


def build_manager_valuation_snapshot(
    conn: sqlite3.Connection,
    config: AppConfig,
    prices: dict[str, dict[str, Decimal]] | None,
    manager_keys: list[str] | None = None,
) -> dict[str, Any]:
    manager_keys = manager_keys or AI_MAXXI_MANAGER_KEYS
    prices = prices or {}
    histories = manager_position_histories(conn, manager_keys)
    manager_lookup = {str(row.get("key")): row for row in config.data.get("managers", [])}
    managers = []
    for manager_key in manager_keys:
        symbol_histories = histories.get(manager_key, {})
        latest_period = max((rows[-1]["report_date"] for rows in symbol_histories.values() if rows), default="")
        latest_rows = [
            rows[-1]
            for rows in symbol_histories.values()
            if rows and rows[-1].get("report_date") == latest_period
        ]
        latest_rows.sort(key=lambda row: row["reported_amount"], reverse=True)
        manager_config = manager_lookup.get(manager_key, {})
        positions = [
            manager_position_valuation(row, symbol_histories.get(row["symbol"], []), prices)
            for row in latest_rows
        ]
        positions = [row for row in positions if row]
        current_total = sum(Decimal(str(row.get("current_value_estimate") or 0)) for row in positions)
        reported_total = sum(Decimal(str(row.get("reported_amount") or 0)) for row in positions)
        managers.append(
            {
                "manager_key": manager_key,
                "manager_name": manager_config.get("display_name") or manager_config.get("name") or manager_key,
                "latest_report_date": max((row.get("report_date", "") for row in latest_rows), default=""),
                "position_count": len(positions),
                "priced_position_count": sum(1 for row in positions if row.get("current_price") is not None),
                "reported_total": rounded_float(reported_total),
                "current_value_estimate_total": rounded_float(current_total) if current_total else None,
                "value_change_since_report_pct": pct_change(current_total, reported_total),
                "positions": positions,
            }
        )
    return {
        "version": VALUATION_VERSION,
        "scope": "ai_maxxi_core_13f_managers",
        "manager_count": len(managers),
        "methodology": valuation_methodology(),
        "managers": managers,
    }


def latest_selected_filings(conn: sqlite3.Connection, manager_keys: list[str]) -> dict[str, sqlite3.Row]:
    filings = selected_filings_by_report_period(conn, manager_keys)
    latest: dict[str, sqlite3.Row] = {}
    for row in filings:
        latest[row["manager_key"]] = row
    return latest


def selected_filings_by_report_period(conn: sqlite3.Connection, manager_keys: list[str]) -> list[sqlite3.Row]:
    if not manager_keys:
        return []
    placeholders = ",".join("?" for _ in manager_keys)
    rows = conn.execute(
        f"""
        SELECT *
        FROM filings
        WHERE manager_key IN ({placeholders}) AND form IN ('13F-HR', '13F-HR/A')
        ORDER BY manager_key, COALESCE(report_date, filing_date), filing_date DESC, accession_number DESC
        """,
        manager_keys,
    ).fetchall()
    selected: dict[tuple[str, str], sqlite3.Row] = {}
    for row in rows:
        period = str(row["report_date"] or row["filing_date"] or "")
        selected.setdefault((row["manager_key"], period), row)
    return sorted(selected.values(), key=lambda row: (row["manager_key"], row["report_date"] or row["filing_date"] or ""))


def manager_position_histories(
    conn: sqlite3.Connection,
    manager_keys: list[str],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    filings = selected_filings_by_report_period(conn, manager_keys)
    if not filings:
        return {}
    filing_by_id = {row["id"]: row for row in filings}
    placeholders = ",".join("?" for _ in filing_by_id)
    rows = conn.execute(
        f"""
        SELECT filing_id, UPPER(symbol) AS symbol, issuer, bucket,
               SUM(CAST(value_usd AS REAL)) AS reported_amount,
               SUM(CAST(shares AS REAL)) AS unit_count
        FROM filing_holdings
        WHERE filing_id IN ({placeholders})
          AND COALESCE(put_call, '') = ''
          AND symbol != ''
        GROUP BY filing_id, UPPER(symbol)
        ORDER BY UPPER(symbol)
        """,
        list(filing_by_id),
    ).fetchall()
    histories: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        filing = filing_by_id[row["filing_id"]]
        reported_amount = decimal_or_zero(row["reported_amount"])
        unit_count = decimal_or_zero(row["unit_count"])
        symbol = str(row["symbol"] or "").upper()
        if not symbol or unit_count <= 0:
            continue
        histories[filing["manager_key"]][symbol].append(
            {
                "manager_key": filing["manager_key"],
                "symbol": symbol,
                "issuer": row["issuer"] or "",
                "bucket": row["bucket"] or "",
                "report_date": filing["report_date"] or filing["filing_date"] or "",
                "filing_date": filing["filing_date"] or "",
                "filing_url": filing["url"] or "",
                "accession_number": filing["accession_number"] or "",
                "reported_amount": reported_amount,
                "unit_count": unit_count,
                "latest_report_price": safe_div(reported_amount, unit_count),
            }
        )
    for symbol_histories in histories.values():
        for rows_for_symbol in symbol_histories.values():
            rows_for_symbol.sort(key=lambda row: row["report_date"])
    return histories


def manager_position_valuation(
    latest: dict[str, Any],
    history: list[dict[str, Any]],
    prices: dict[str, dict[str, Decimal]],
) -> dict[str, Any]:
    symbol = str(latest.get("symbol") or "").upper()
    unit_count = decimal_or_zero(latest.get("unit_count"))
    reported_amount = decimal_or_zero(latest.get("reported_amount"))
    report_price = decimal_or_zero(latest.get("latest_report_price"))
    entry = entry_price_proxy(history)
    current_price = price_for_symbol(prices, symbol)
    current_amount = unit_count * current_price if current_price is not None else None
    return {
        "symbol": symbol,
        "issuer": latest.get("issuer", ""),
        "bucket": latest.get("bucket", ""),
        "report_date": latest.get("report_date", ""),
        "filing_date": latest.get("filing_date", ""),
        "filing_url": latest.get("filing_url", ""),
        "reported_amount": rounded_float(reported_amount),
        "latest_report_price": rounded_float(report_price),
        "entry_price_estimate": rounded_float(entry["entry_price"]) if entry.get("entry_price") is not None else None,
        "current_price": rounded_float(current_price) if current_price is not None else None,
        "current_value_estimate": rounded_float(current_amount) if current_amount is not None else None,
        "entry_return_estimate_pct": pct_change(current_price, entry.get("entry_price")),
        "value_change_since_report_pct": pct_change(current_amount, reported_amount),
        "valuation_confidence": entry["confidence"],
        "valuation_method": entry["method"],
        "observed_quarters": entry["observed_quarters"],
        "excluded_quarters": entry.get("excluded_quarters", 0),
        "first_seen_report_date": entry["first_seen_report_date"],
        "source": "public_13f_plus_latest_daily_close",
    }


def entry_price_proxy(history: list[dict[str, Any]]) -> dict[str, Any]:
    anchor = decimal_or_zero((history[-1] if history else {}).get("latest_report_price"))
    cost = Decimal("0")
    units = Decimal("0")
    previous_units = Decimal("0")
    add_count = 0
    first_seen = ""
    excluded = 0
    for row in history:
        row_units = decimal_or_zero(row.get("unit_count"))
        price = decimal_or_zero(row.get("latest_report_price"))
        if row_units <= 0 or price <= 0:
            continue
        if anchor > 0 and (price > anchor * Decimal("10") or price < anchor / Decimal("10")):
            excluded += 1
            continue
        if not first_seen:
            first_seen = str(row.get("report_date") or "")
        if previous_units == 0:
            cost += row_units * price
            units += row_units
            add_count += 1
        elif row_units > previous_units:
            delta = row_units - previous_units
            cost += delta * price
            units += delta
            add_count += 1
        elif row_units < previous_units and units > 0:
            reduction = min(units, previous_units - row_units)
            average = safe_div(cost, units) or Decimal("0")
            cost -= reduction * average
            units -= reduction
        previous_units = row_units
    entry = safe_div(cost, units)
    observed = len(history) - excluded
    confidence = "medium" if observed >= 4 else "low" if observed else "unknown"
    if excluded:
        confidence = "low_outlier_filtered"
    return {
        "entry_price": entry,
        "method": "observed_13f_share_delta_weighted_average",
        "confidence": confidence,
        "observed_quarters": observed,
        "excluded_quarters": excluded,
        "observed_add_count": add_count,
        "first_seen_report_date": first_seen,
    }


def attach_manager_valuations(manager_radar: dict[str, Any], valuation: dict[str, Any]) -> dict[str, Any]:
    by_manager: dict[str, dict[str, dict[str, Any]]] = {}
    for manager in valuation.get("managers", []):
        by_manager[str(manager.get("manager_key") or "")] = {
            str(row.get("symbol") or "").upper(): row
            for row in manager.get("positions", [])
            if row.get("symbol")
        }
    radar = dict(manager_radar)
    radar["ai_maxxi_valuation"] = valuation
    focus = []
    for manager in radar.get("focus_managers", []):
        row = dict(manager)
        position_valuations = by_manager.get(str(row.get("manager_key") or ""), {})
        for key in ("positions", "top_positions"):
            row[key] = [
                attach_position_valuation(position, position_valuations)
                for position in row.get(key, [])
            ]
        focus.append(row)
    radar["focus_managers"] = focus
    return radar


def attach_position_valuation(position: dict[str, Any], valuations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    row = dict(position)
    valuation = valuations.get(str(row.get("symbol") or "").upper())
    if not valuation:
        return row
    for key in (
        "reported_amount",
        "latest_report_price",
        "entry_price_estimate",
        "current_price",
        "current_value_estimate",
        "entry_return_estimate_pct",
        "value_change_since_report_pct",
        "valuation_confidence",
        "valuation_method",
        "observed_quarters",
        "excluded_quarters",
        "first_seen_report_date",
        "source",
    ):
        row[key] = valuation.get(key)
    return row


def build_portfolio_valuation_snapshot(portfolio: dict[str, Any], as_of: date | None = None) -> dict[str, Any]:
    rows = []
    for position in portfolio.get("by_symbol", []):
        quantity = decimal_or_zero(position.get("quantity"))
        current_value = decimal_or_zero(position.get("market_value"))
        cost_basis = decimal_or_zero(position.get("cost_basis"))
        current_price = safe_div(current_value, quantity)
        entry_price = safe_div(cost_basis, quantity) if cost_basis > 0 else current_price
        source = "broker_cost_basis" if cost_basis > 0 else "latest_position_mark_proxy"
        rows.append(
            {
                "symbol": position.get("symbol", ""),
                "bucket": position.get("bucket", "unmapped"),
                "portfolio_weight": round(float(position.get("comparison_weight", position.get("ex_cash_weight", position.get("weight") or 0)) or 0), 6),
                "entry_price_estimate": rounded_float(entry_price),
                "current_price": rounded_float(current_price),
                "current_value": rounded_float(current_value),
                "cost_basis": rounded_float(cost_basis) if cost_basis else None,
                "unrealized_return_estimate_pct": pct_change(current_value, cost_basis) if cost_basis else None,
                "estimate_method": source,
            }
        )
    current_total = sum(decimal_or_zero(row.get("current_value")) for row in rows)
    cost_total = sum(decimal_or_zero(row.get("cost_basis")) for row in rows)
    return {
        "version": VALUATION_VERSION,
        "as_of": (as_of or date.today()).isoformat(),
        "scope": "private_portfolio",
        "methodology": valuation_methodology(),
        "current_value_total": rounded_float(current_total),
        "cost_basis_total": rounded_float(cost_total) if cost_total else None,
        "unrealized_return_estimate_pct": pct_change(current_total, cost_total) if cost_total else None,
        "position_count": len(rows),
        "positions": rows,
    }


def format_valuation_markdown(snapshot: dict[str, Any]) -> str:
    lines = [
        f"# AlloIQ Entry And Current Value Estimates ({snapshot.get('as_of', '')})",
        "",
        "_Best-effort estimates. 13F filings do not disclose true entry price or post-quarter trading._",
        "",
    ]
    manager_snapshot = snapshot.get("managers") or {}
    if manager_snapshot.get("managers"):
        lines.append("## AI-Maxxi 13F Managers")
        for manager in manager_snapshot["managers"]:
            lines.append("")
            lines.append(f"### {manager.get('manager_name', manager.get('manager_key', 'Manager'))}")
            lines.append(f"- Report date: {manager.get('latest_report_date') or 'n/a'}")
            lines.append(f"- Reported total: {money(manager.get('reported_total'))}; current value estimate: {money(manager.get('current_value_estimate_total'))}")
            lines.append("")
            lines.append("| Symbol | Entry Proxy | 13F Mark | Latest Price | Reported Value | Current Value Est. | Entry Return Est. |")
            lines.append("|---|---:|---:|---:|---:|---:|---:|")
            for row in manager.get("positions", []):
                lines.append(
                    f"| {row.get('symbol', '')} | {price(row.get('entry_price_estimate'))} | "
                    f"{price(row.get('latest_report_price'))} | {price(row.get('current_price'))} | "
                    f"{money(row.get('reported_amount'))} | {money(row.get('current_value_estimate'))} | "
                    f"{pct(row.get('entry_return_estimate_pct'))} |"
                )
    portfolio = snapshot.get("portfolio") or {}
    if portfolio.get("positions"):
        lines.extend(["", "## Private Portfolio", ""])
        lines.append(f"- Current value: {money(portfolio.get('current_value_total'))}")
        lines.append(f"- Cost basis: {money(portfolio.get('cost_basis_total'))}")
        lines.append(f"- Unrealized return estimate: {pct(portfolio.get('unrealized_return_estimate_pct'))}")
        lines.append("")
        lines.append("| Symbol | Weight | Entry Proxy | Current Price | Current Value | Cost Basis | Unrealized Return Est. | Method |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---|")
        for row in portfolio.get("positions", []):
            lines.append(
                f"| {row.get('symbol', '')} | {pct_weight(row.get('portfolio_weight'))} | "
                f"{price(row.get('entry_price_estimate'))} | {price(row.get('current_price'))} | "
                f"{money(row.get('current_value'))} | {money(row.get('cost_basis'))} | "
                f"{pct(row.get('unrealized_return_estimate_pct'))} | {row.get('estimate_method', '')} |"
            )
    lines.append("")
    return "\n".join(lines)


def write_valuation_report(snapshot: dict[str, Any], path: Path, format_: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if format_ == "json":
        import json

        path.write_text(json.dumps(snapshot, indent=2, sort_keys=True, default=str), encoding="utf-8")
    else:
        path.write_text(format_valuation_markdown(snapshot), encoding="utf-8")
    return path


def price_for_symbol(prices: dict[str, dict[str, Decimal]], symbol: str) -> Decimal | None:
    row = proxied_lookup(prices, symbol)
    if not row:
        return None
    value = decimal_or_zero(row.get("last"))
    return value if value > 0 else None


def safe_div(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator == 0:
        return None
    return numerator / denominator


def pct_change(current: Any, basis: Any) -> float | None:
    current_decimal = decimal_or_zero(current)
    basis_decimal = decimal_or_zero(basis)
    if basis_decimal == 0:
        return None
    return rounded_float(((current_decimal - basis_decimal) / basis_decimal) * Decimal("100"), places=2)


def rounded_float(value: Any, places: int = 4) -> float | None:
    if value is None:
        return None
    decimal = decimal_or_zero(value)
    if decimal == 0:
        return 0.0
    return float(round(decimal, places))


def money(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"${float(value):,.0f}"


def price(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"${float(value):,.2f}"


def pct(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):+.2f}%"


def pct_weight(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.2f}%"
