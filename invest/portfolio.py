from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from .config import AppConfig
from .util import decimal_or_zero


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
    by_symbol: dict[str, dict[str, Any]] = {}
    by_broker: dict[str, Decimal] = defaultdict(Decimal)
    by_bucket: dict[str, Decimal] = defaultdict(Decimal)
    gross_exposure = Decimal("0")
    net_exposure = Decimal("0")

    for row in rows:
        symbol = row["symbol"]
        market_value = Decimal(str(row["market_value"] or 0))
        quantity = Decimal(str(row["quantity"] or 0))
        cost_basis = Decimal(str(row["cost_basis"] or 0))
        bucket = config.symbol_to_bucket.get(symbol, "unmapped")
        gross_exposure += abs(market_value)
        net_exposure += market_value
        by_broker[row["broker"]] += market_value
        by_bucket[bucket] += market_value
        existing = by_symbol.setdefault(
            symbol,
            {
                "symbol": symbol,
                "bucket": bucket,
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
    return {
        "position_count": len(rows),
        "symbol_count": len(by_symbol),
        "gross_exposure": float(gross_exposure),
        "net_exposure": float(net_exposure),
        "by_symbol": [
            {
                "symbol": row["symbol"],
                "bucket": row["bucket"],
                "market_value": float(row["market_value"]),
                "quantity": float(row["quantity"]),
                "cost_basis": float(row["cost_basis"]),
                "weight": float(abs(row["market_value"]) / gross_exposure) if gross_exposure else 0.0,
                "brokers": sorted(row["brokers"]),
                "accounts": sorted(row["accounts"]),
            }
            for row in top_positions
        ],
        "by_bucket": [
            {
                "bucket": bucket,
                "market_value": float(value),
                "weight": float(abs(value) / gross_exposure) if gross_exposure else 0.0,
            }
            for bucket, value in bucket_rows
        ],
        "by_broker": [
            {
                "broker": broker,
                "market_value": float(value),
                "weight": float(abs(value) / gross_exposure) if gross_exposure else 0.0,
            }
            for broker, value in broker_rows
        ],
        "unmapped_symbols": sorted(symbol for symbol, row in by_symbol.items() if row["bucket"] == "unmapped"),
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
            price = decimal_or_zero((prices.get(symbol) or {}).get("last"))
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
