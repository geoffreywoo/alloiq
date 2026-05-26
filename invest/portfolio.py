from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from .config import AppConfig
from .symbols import proxied_lookup
from .util import decimal_or_zero


CASH_BUCKET = "cash_reserves"
CASH_SYMBOLS = {"CASH", "USD", "USD.CASH", "CASHUSD", "CUR:USD", "BASE_CURRENCY"}


def latest_positions_value_by_symbol(conn: sqlite3.Connection) -> dict[str, Decimal]:
    values: dict[str, Decimal] = defaultdict(Decimal)
    for row in latest_position_rows(conn):
        values[row["symbol"]] += Decimal(str(row["market_value"] or 0))
    return dict(values)


def build_portfolio_exposure(
    conn: sqlite3.Connection,
    config: AppConfig,
    prices: dict[str, dict[str, Decimal]] | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    rows = list(latest_position_rows(conn))
    manual_rows = configured_manual_position_rows(config, prices or {}, as_of or date.today())
    rows.extend(manual_rows)
    rows.extend(configured_cash_reserve_rows(config, rows, as_of or date.today()))
    by_symbol: dict[str, dict[str, Any]] = {}
    by_broker: dict[str, Decimal] = defaultdict(Decimal)
    by_bucket: dict[str, Decimal] = defaultdict(Decimal)
    gross_exposure = Decimal("0")
    net_exposure = Decimal("0")
    cash_exposure = Decimal("0")
    equity_exposure = Decimal("0")

    for row in rows:
        symbol = str(row["symbol"]).upper()
        market_value = Decimal(str(row["market_value"] or 0))
        quantity = Decimal(str(row["quantity"] or 0))
        cost_basis = Decimal(str(row["cost_basis"] or 0))
        is_cash = is_cash_row(row)
        bucket = CASH_BUCKET if is_cash else config.symbol_to_bucket.get(symbol, "unmapped")
        asset_class = "cash" if is_cash else "equity"
        gross_exposure += abs(market_value)
        net_exposure += market_value
        if is_cash:
            cash_exposure += market_value
        else:
            equity_exposure += abs(market_value)
        by_broker[row["broker"]] += market_value
        by_bucket[bucket] += market_value
        existing = by_symbol.setdefault(
            symbol,
            {
                "symbol": symbol,
                "bucket": bucket,
                "asset_class": asset_class,
                "is_cash": is_cash,
                "market_value": Decimal("0"),
                "quantity": Decimal("0"),
                "cost_basis": Decimal("0"),
                "accounts": set(),
                "brokers": set(),
            },
        )
        existing["market_value"] += market_value
        existing["quantity"] += quantity
        existing["cost_basis"] += cost_basis
        existing["accounts"].add(row["account"])
        existing["brokers"].add(row["broker"])

    top_positions = sorted(by_symbol.values(), key=lambda item: abs(item["market_value"]), reverse=True)
    bucket_rows = sorted(by_bucket.items(), key=lambda item: abs(item[1]), reverse=True)
    broker_rows = sorted(by_broker.items(), key=lambda item: abs(item[1]), reverse=True)
    equity_denominator = equity_exposure if equity_exposure else Decimal("0")

    def total_weight(value: Decimal) -> float:
        return float(abs(value) / gross_exposure) if gross_exposure else 0.0

    def equity_weight(value: Decimal, is_cash: bool = False) -> float:
        if is_cash or equity_denominator <= 0:
            return 0.0
        return float(abs(value) / equity_denominator)

    return {
        "position_count": len(rows),
        "symbol_count": len(by_symbol),
        "security_symbol_count": len([row for row in by_symbol.values() if not row.get("is_cash")]),
        "gross_exposure": float(gross_exposure),
        "net_exposure": float(net_exposure),
        "equity_exposure": float(equity_exposure),
        "cash_exposure": float(cash_exposure),
        "equity_weight": float(equity_exposure / gross_exposure) if gross_exposure else 0.0,
        "cash_weight": float(abs(cash_exposure) / gross_exposure) if gross_exposure else 0.0,
        "cash_reserves": {
            "symbol": "CASH",
            "bucket": CASH_BUCKET,
            "asset_class": "cash",
            "weight": total_weight(cash_exposure),
            "policy": "available_for_capped_high_conviction_adds",
        },
        "weight_basis": "total_portfolio_including_cash",
        "comparison_weight_basis": "invested_equity_ex_cash",
        "by_symbol": [
            {
                "symbol": row["symbol"],
                "bucket": row["bucket"],
                "asset_class": row["asset_class"],
                "is_cash": bool(row["is_cash"]),
                "market_value": float(row["market_value"]),
                "quantity": float(row["quantity"]),
                "cost_basis": float(row["cost_basis"]),
                "weight": total_weight(row["market_value"]),
                "total_weight": total_weight(row["market_value"]),
                "ex_cash_weight": equity_weight(row["market_value"], bool(row["is_cash"])),
                "comparison_weight": equity_weight(row["market_value"], bool(row["is_cash"])),
                "brokers": sorted(row["brokers"]),
                "accounts": sorted(row["accounts"]),
            }
            for row in top_positions
        ],
        "by_bucket": [
            {
                "bucket": bucket,
                "market_value": float(value),
                "weight": total_weight(value),
                "total_weight": total_weight(value),
                "ex_cash_weight": equity_weight(value, bucket == CASH_BUCKET),
                "comparison_weight": equity_weight(value, bucket == CASH_BUCKET),
            }
            for bucket, value in bucket_rows
        ],
        "by_broker": [
            {
                "broker": broker,
                "market_value": float(value),
                "weight": total_weight(value),
            }
            for broker, value in broker_rows
        ],
        "unmapped_symbols": sorted(
            symbol for symbol, row in by_symbol.items() if row["bucket"] == "unmapped" and not row.get("is_cash")
        ),
    }


def configured_manual_position_rows(
    config: AppConfig,
    prices: dict[str, dict[str, Decimal]],
    as_of: date,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in config.manual_positions:
        symbol = str(row.get("symbol", "")).upper()
        quantity = decimal_or_zero(row.get("quantity"))
        if not symbol or quantity == 0:
            continue
        price = decimal_or_zero(row.get("price"))
        if price == 0:
            price = decimal_or_zero((proxied_lookup(prices, symbol, {}) or {}).get("last"))
        market_value = decimal_or_zero(row.get("market_value"))
        if market_value == 0 and price != 0:
            market_value = quantity * price
        rows.append(
            {
                "broker": row.get("broker", "manual"),
                "account": row.get("account", "manual-sleeve"),
                "as_of": as_of.isoformat(),
                "symbol": symbol,
                "description": row.get("description", ""),
                "quantity": quantity,
                "cost_basis": Decimal("0"),
                "market_value": market_value,
                "currency": row.get("currency", "USD"),
            }
        )
    return rows


def configured_cash_reserve_rows(
    config: AppConfig,
    existing_rows: list[Any],
    as_of: date,
) -> list[dict[str, Any]]:
    reserves = config.cash_reserves
    if not reserves:
        return []
    fixed_rows: list[dict[str, Any]] = []
    proportional_reserves: list[dict[str, Any]] = []
    for row in reserves:
        market_value = decimal_or_zero(row.get("market_value"))
        weight = decimal_or_zero(row.get("weight"))
        if market_value != 0:
            fixed_rows.append(cash_row(row, market_value, as_of))
        elif weight > 0:
            proportional_reserves.append(row)
    if proportional_reserves:
        base_gross = sum(
            abs(Decimal(str(row["market_value"] or 0)))
            for row in existing_rows
        ) + sum(abs(Decimal(str(row["market_value"] or 0))) for row in fixed_rows)
        total_weight = sum(decimal_or_zero(row.get("weight")) for row in proportional_reserves)
        total_weight = min(max(total_weight, Decimal("0")), Decimal("0.95"))
        if base_gross > 0 and total_weight > 0:
            total_cash_value = base_gross * total_weight / (Decimal("1") - total_weight)
            for row in proportional_reserves:
                weight = decimal_or_zero(row.get("weight"))
                market_value = total_cash_value * (weight / total_weight)
                fixed_rows.append(cash_row(row, market_value, as_of))
    return fixed_rows


def cash_row(row: dict[str, Any], market_value: Decimal, as_of: date) -> dict[str, Any]:
    return {
        "broker": row.get("broker", "manual"),
        "account": row.get("account", "cash-reserve"),
        "as_of": as_of.isoformat(),
        "symbol": str(row.get("symbol") or "CASH").upper(),
        "description": row.get("description", "Cash reserves"),
        "quantity": market_value,
        "cost_basis": Decimal("0"),
        "market_value": market_value,
        "currency": row.get("currency", "USD"),
    }


def is_cash_row(row: Any) -> bool:
    symbol = str(row["symbol"] if hasattr(row, "keys") and "symbol" in row.keys() else row.get("symbol", "")).upper()
    description = str(row["description"] if hasattr(row, "keys") and "description" in row.keys() else row.get("description", "")).upper()
    if symbol in CASH_SYMBOLS or symbol.startswith("CASH_"):
        return True
    return "CASH" in description and symbol in {"", "USD"}


def latest_position_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        WITH latest AS (
          SELECT broker, account, symbol, MAX(as_of) AS as_of
          FROM positions
          GROUP BY broker, account, symbol
        )
        SELECT p.broker, p.account, p.as_of, p.symbol, p.description,
               CAST(p.quantity AS REAL) AS quantity,
               CAST(p.cost_basis AS REAL) AS cost_basis,
               CAST(p.market_value AS REAL) AS market_value,
               p.currency
        FROM positions p
        JOIN latest l
          ON p.broker = l.broker
         AND p.account = l.account
         AND p.symbol = l.symbol
         AND p.as_of = l.as_of
        WHERE p.symbol != ''
        ORDER BY ABS(CAST(p.market_value AS REAL)) DESC
        """
    ).fetchall()
