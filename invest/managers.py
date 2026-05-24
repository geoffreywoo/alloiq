from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from decimal import Decimal
from typing import Any

from .config import AppConfig


def configured_manager_keys(config: AppConfig) -> list[str]:
    return [str(manager["key"]) for manager in config.data.get("managers", []) if manager.get("cik")]


def build_manager_radar(
    conn: sqlite3.Connection,
    config: AppConfig,
    portfolio_weights_by_symbol: dict[str, float] | None = None,
) -> dict[str, Any]:
    manager_keys = configured_manager_keys(config)
    latest = latest_filings_by_manager(conn, manager_keys)
    latest_ids = [row["id"] for row in latest.values()]
    holdings = latest_holdings(conn, latest_ids, include_unresolved=True)
    resolved_holdings = [row for row in holdings if row["symbol"]]
    by_symbol = aggregate_latest_holdings(resolved_holdings, config)
    flows = aggregate_manager_flows(conn, manager_keys, config)
    focus_managers = build_focus_manager_tracking(
        holdings,
        latest,
        config,
        portfolio_weights_by_symbol or {},
    )
    return {
        "manager_count": len(manager_keys),
        "stored_latest_count": len(latest),
        "manager_status": [render_manager_status(row) for row in latest.values()],
        "focus_manager_keys": config.focus_manager_keys,
        "focus_manager_groups": build_focus_manager_groups(focus_managers),
        "focus_managers": focus_managers,
        "by_symbol": by_symbol,
        "top_consensus": sorted(by_symbol.values(), key=consensus_sort_key, reverse=True)[:20],
        "top_adds": flows["top_adds"],
        "top_reductions": flows["top_reductions"],
        "new_positions": flows["new_positions"],
        "option_watch": sorted(by_symbol.values(), key=option_sort_key, reverse=True)[:12],
    }


def latest_filings_by_manager(conn: sqlite3.Connection, manager_keys: list[str]) -> dict[str, sqlite3.Row]:
    if not manager_keys:
        return {}
    placeholders = ",".join("?" for _ in manager_keys)
    rows = conn.execute(
        f"""
        SELECT *
        FROM filings
        WHERE manager_key IN ({placeholders}) AND form IN ('13F-HR', '13F-HR/A')
        ORDER BY manager_key, COALESCE(report_date, filing_date) DESC, filing_date DESC, accession_number DESC
        """,
        manager_keys,
    ).fetchall()
    latest: dict[str, sqlite3.Row] = {}
    for row in rows:
        latest.setdefault(row["manager_key"], row)
    return latest


def latest_holdings(conn: sqlite3.Connection, filing_ids: list[int], include_unresolved: bool = False) -> list[sqlite3.Row]:
    if not filing_ids:
        return []
    symbol_filter = "" if include_unresolved else "AND h.symbol != ''"
    placeholders = ",".join("?" for _ in filing_ids)
    return conn.execute(
        f"""
        SELECT f.manager_key, f.manager_name, f.report_date, f.filing_date, h.symbol, h.issuer,
               h.bucket, h.put_call, CAST(h.value_usd AS REAL) AS value_usd
        FROM filing_holdings h
        JOIN filings f ON f.id = h.filing_id
        WHERE h.filing_id IN ({placeholders}) {symbol_filter}
        """,
        filing_ids,
    ).fetchall()


def build_focus_manager_tracking(
    rows: list[sqlite3.Row],
    latest: dict[str, sqlite3.Row],
    config: AppConfig,
    portfolio_weights_by_symbol: dict[str, float],
) -> list[dict[str, Any]]:
    manager_lookup = {str(manager.get("key")): manager for manager in config.data.get("managers", [])}
    tier_map = config.focus_manager_tier_map
    focus_keys = config.focus_manager_keys or [
        str(manager["key"]) for manager in config.data.get("managers", []) if manager.get("primary")
    ]
    rows_by_manager: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        rows_by_manager[row["manager_key"]].append(row)

    tracking: list[dict[str, Any]] = []
    for manager_key in focus_keys:
        manager = manager_lookup.get(manager_key, {"key": manager_key, "name": manager_key})
        filing = latest.get(manager_key)
        common_rows = [
            row
            for row in rows_by_manager.get(manager_key, [])
            if not str(row["put_call"] or "").strip()
        ]
        if not filing:
            tracking.append(
                {
                    "manager_key": manager_key,
                    "manager_name": manager.get("display_name") or manager.get("name") or manager_key,
                    "manager_tier": manager_tier(manager_key, manager, tier_map),
                    "manager_group": manager_group_label(manager_tier(manager_key, manager, tier_map)),
                    "lens": manager.get("lens", ""),
                    "status": "missing_latest_filing",
                    "latest_report_date": None,
                    "latest_filing_date": None,
                    "symbol_coverage_pct": 0.0,
                    "alloiq_watchlist_pct": 0.0,
                    "bucket_classified_pct": 0.0,
                    "default_portfolio_overlap_pct": 0.0,
                    "top10_concentration_pct": 0.0,
                    "top_positions": [],
                    "positions": [],
                }
            )
            continue

        tracking.append(
            render_focus_manager(
                manager_key,
                manager,
                filing,
                common_rows,
                config,
                portfolio_weights_by_symbol,
                tier_map,
            )
        )
    return tracking


def render_focus_manager(
    manager_key: str,
    manager: dict[str, Any],
    filing: sqlite3.Row,
    rows: list[sqlite3.Row],
    config: AppConfig,
    portfolio_weights_by_symbol: dict[str, float],
    tier_map: dict[str, str],
) -> dict[str, Any]:
    by_position: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row["symbol"] or "").upper()
        issuer = str(row["issuer"] or "").strip()
        key = symbol or issuer.upper()
        if not key:
            continue
        value = Decimal(str(row["value_usd"] or 0))
        bucket = str(row["bucket"] or config.symbol_to_bucket.get(symbol, "unmapped") if symbol else "unmapped")
        position = by_position.setdefault(
            key,
            {
                "symbol": symbol,
                "issuer": issuer,
                "bucket": bucket,
                "value": Decimal("0"),
            },
        )
        position["value"] += value
        if symbol and not position["symbol"]:
            position["symbol"] = symbol
        if bucket != "unmapped":
            position["bucket"] = bucket

    total = sum((row["value"] for row in by_position.values()), Decimal("0"))
    watchlist = set(config.watchlist_symbols)
    portfolio_symbols = {symbol.upper() for symbol, weight in portfolio_weights_by_symbol.items() if weight}
    resolved_value = sum((row["value"] for row in by_position.values() if row["symbol"]), Decimal("0"))
    watchlist_value = sum((row["value"] for row in by_position.values() if row["symbol"] in watchlist), Decimal("0"))
    bucket_value = sum(
        (
            row["value"]
            for row in by_position.values()
            if row["bucket"] and row["bucket"] != "unmapped"
        ),
        Decimal("0"),
    )
    portfolio_overlap_value = sum(
        (row["value"] for row in by_position.values() if row["symbol"] in portfolio_symbols),
        Decimal("0"),
    )
    sorted_positions = sorted(by_position.values(), key=lambda row: row["value"], reverse=True)
    top10_value = sum((row["value"] for row in sorted_positions[:10]), Decimal("0"))
    positions = [
        render_focus_position(index, row, total, portfolio_weights_by_symbol)
        for index, row in enumerate(sorted_positions, start=1)
    ]
    tier = manager_tier(manager_key, manager, tier_map)
    return {
        "manager_key": manager_key,
        "manager_name": manager.get("display_name") or manager.get("name") or manager_key,
        "manager_tier": tier,
        "manager_group": manager_group_label(tier),
        "lens": manager.get("lens", ""),
        "status": "ok",
        "latest_report_date": filing["report_date"],
        "latest_filing_date": filing["filing_date"],
        "filing_url": filing["url"],
        "accession_number": filing["accession_number"],
        "total_common_value": float(total),
        "position_count": len(by_position),
        "resolved_position_count": sum(1 for row in by_position.values() if row["symbol"]),
        "symbol_coverage_pct": round(decimal_pct(resolved_value, total), 2),
        "alloiq_watchlist_pct": round(decimal_pct(watchlist_value, total), 2),
        "bucket_classified_pct": round(decimal_pct(bucket_value, total), 2),
        "default_portfolio_overlap_pct": round(decimal_pct(portfolio_overlap_value, total), 2),
        "top10_concentration_pct": round(decimal_pct(top10_value, total), 2),
        "top_positions": positions[:10],
        "positions": positions,
    }


def render_focus_position(
    rank: int,
    row: dict[str, Any],
    total: Decimal,
    portfolio_weights_by_symbol: dict[str, float],
) -> dict[str, Any]:
    symbol = str(row["symbol"] or "").upper()
    return {
        "rank": rank,
        "symbol": symbol,
        "issuer": row["issuer"],
        "bucket": row["bucket"],
        "fund_weight": round(decimal_ratio(row["value"], total), 6),
        "portfolio_weight": round(float(portfolio_weights_by_symbol.get(symbol, 0.0)), 6) if symbol else 0.0,
        "value": float(row["value"]),
    }


def manager_tier(manager_key: str, manager: dict[str, Any], tier_map: dict[str, str]) -> str:
    return str(manager.get("tier") or tier_map.get(manager_key) or "tier_2")


def manager_group_label(tier: str) -> str:
    if tier == "tier_1":
        return "AI Thesis Core"
    return "Manager Context Bench"


def build_focus_manager_groups(focus_managers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups = [
        {
            "key": "tier_1",
            "label": manager_group_label("tier_1"),
            "description": "Leopold/Situational Awareness, Altimeter, and Dragoneer.",
            "managers": [row for row in focus_managers if row.get("manager_tier") == "tier_1"],
        },
        {
            "key": "tier_2",
            "label": manager_group_label("tier_2"),
            "description": "All other tracked public 13F managers.",
            "managers": [row for row in focus_managers if row.get("manager_tier") != "tier_1"],
        },
    ]
    return [group for group in groups if group["managers"]]


def decimal_ratio(numerator: Decimal, denominator: Decimal) -> float:
    if not denominator:
        return 0.0
    return float(numerator / denominator)


def decimal_pct(numerator: Decimal, denominator: Decimal) -> float:
    return decimal_ratio(numerator, denominator) * 100


def aggregate_latest_holdings(rows: list[sqlite3.Row], config: AppConfig) -> dict[str, dict[str, Any]]:
    symbols: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = row["symbol"].upper()
        put_call = (row["put_call"] or "").upper()
        value = Decimal(str(row["value_usd"] or 0))
        bucket = row["bucket"] or config.symbol_to_bucket.get(symbol, "unmapped")
        data = symbols.setdefault(
            symbol,
            {
                "symbol": symbol,
                "bucket": bucket,
                "common_value": Decimal("0"),
                "call_value": Decimal("0"),
                "put_value": Decimal("0"),
                "common_managers": set(),
                "call_managers": set(),
                "put_managers": set(),
                "issuers": set(),
            },
        )
        data["issuers"].add(row["issuer"])
        if put_call == "CALL":
            data["call_value"] += value
            data["call_managers"].add(row["manager_name"])
        elif put_call == "PUT":
            data["put_value"] += value
            data["put_managers"].add(row["manager_name"])
        else:
            data["common_value"] += value
            data["common_managers"].add(row["manager_name"])

    rendered: dict[str, dict[str, Any]] = {}
    for symbol, data in symbols.items():
        common_value = data["common_value"]
        call_value = data["call_value"]
        put_value = data["put_value"]
        common_count = len(data["common_managers"])
        rendered[symbol] = {
            "symbol": symbol,
            "bucket": data["bucket"],
            "common_value": float(common_value),
            "call_value": float(call_value),
            "put_value": float(put_value),
            "common_manager_count": common_count,
            "call_manager_count": len(data["call_managers"]),
            "put_manager_count": len(data["put_managers"]),
            "common_managers": sorted(data["common_managers"]),
            "call_managers": sorted(data["call_managers"]),
            "put_managers": sorted(data["put_managers"]),
            "issuers": sorted(data["issuers"]),
            "consensus_score": float(consensus_score(common_value, common_count, call_value, put_value)),
        }
    return rendered


def aggregate_manager_flows(conn: sqlite3.Connection, manager_keys: list[str], config: AppConfig) -> dict[str, list[dict[str, Any]]]:
    deltas: dict[str, Decimal] = defaultdict(Decimal)
    latest_values: dict[str, Decimal] = defaultdict(Decimal)
    previous_values: dict[str, Decimal] = defaultdict(Decimal)
    latest_managers: dict[str, set[str]] = defaultdict(set)
    new_positions: list[dict[str, Any]] = []

    for manager_key in manager_keys:
        filings = latest_two_filings(conn, manager_key)
        if not filings:
            continue
        latest = filings[0]
        latest_by_symbol = common_values_for_filing(conn, latest["id"])
        previous_by_symbol: dict[str, Decimal] = {}
        if len(filings) > 1:
            previous_by_symbol = common_values_for_filing(conn, filings[1]["id"])
        for symbol, value in latest_by_symbol.items():
            prev = previous_by_symbol.get(symbol, Decimal("0"))
            deltas[symbol] += value - prev
            latest_values[symbol] += value
            previous_values[symbol] += prev
            latest_managers[symbol].add(latest["manager_name"])
            if previous_by_symbol and symbol not in previous_by_symbol:
                new_positions.append(
                    {
                        "symbol": symbol,
                        "manager": latest["manager_name"],
                        "value": float(value),
                        "bucket": config.symbol_to_bucket.get(symbol, "unmapped"),
                    }
                )

    rows = [
        {
            "symbol": symbol,
            "delta_value": float(delta),
            "latest_value": float(latest_values[symbol]),
            "previous_value": float(previous_values[symbol]),
            "manager_count": len(latest_managers[symbol]),
            "managers": sorted(latest_managers[symbol]),
            "bucket": config.symbol_to_bucket.get(symbol, "unmapped"),
        }
        for symbol, delta in deltas.items()
        if delta
    ]
    rows.sort(key=lambda row: row["delta_value"], reverse=True)
    new_positions.sort(key=lambda row: row["value"], reverse=True)
    return {
        "top_adds": rows[:15],
        "top_reductions": list(reversed(rows[-15:])),
        "new_positions": new_positions[:15],
    }


def latest_two_filings(conn: sqlite3.Connection, manager_key: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM filings
        WHERE manager_key = ? AND form IN ('13F-HR', '13F-HR/A')
        ORDER BY COALESCE(report_date, filing_date) DESC, filing_date DESC, accession_number DESC
        LIMIT 2
        """,
        (manager_key,),
    ).fetchall()


def common_values_for_filing(conn: sqlite3.Connection, filing_id: int) -> dict[str, Decimal]:
    rows = conn.execute(
        """
        SELECT symbol, SUM(CAST(value_usd AS REAL)) AS value_usd
        FROM filing_holdings
        WHERE filing_id = ? AND symbol != '' AND COALESCE(put_call, '') = ''
        GROUP BY symbol
        """,
        (filing_id,),
    ).fetchall()
    return {row["symbol"]: Decimal(str(row["value_usd"] or 0)) for row in rows}


def consensus_score(common_value: Decimal, manager_count: int, call_value: Decimal, put_value: Decimal) -> Decimal:
    value_score = Decimal(str(math.log10(float(common_value) + 1))) if common_value > 0 else Decimal("0")
    option_tilt = Decimal("0")
    if call_value:
        option_tilt += Decimal(str(math.log10(float(call_value) + 1))) / Decimal("3")
    if put_value:
        option_tilt -= Decimal(str(math.log10(float(put_value) + 1))) / Decimal("2")
    return Decimal(manager_count * 10) + value_score + option_tilt


def consensus_sort_key(row: dict[str, Any]) -> tuple[float, float]:
    return (float(row["consensus_score"]), float(row["common_value"]))


def option_sort_key(row: dict[str, Any]) -> tuple[float, float]:
    return (float(row["put_value"]) + float(row["call_value"]), float(row["common_value"]))


def render_manager_status(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "manager_key": row["manager_key"],
        "manager_name": row["manager_name"],
        "form": row["form"],
        "filing_date": row["filing_date"],
        "report_date": row["report_date"],
        "url": row["url"],
        "accession_number": row["accession_number"],
    }
