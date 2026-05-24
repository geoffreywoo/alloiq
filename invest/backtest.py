from __future__ import annotations

import sqlite3
from decimal import Decimal


def backtest_signal(conn: sqlite3.Connection, signal: str) -> dict[str, object]:
    if signal != "ai-infra-momentum":
        return {
            "signal": signal,
            "status": "unknown",
            "message": "Only ai-infra-momentum is implemented in v1.",
        }
    filing_rows = conn.execute(
        """
        SELECT f.report_date, h.symbol, CAST(h.value_usd AS REAL) AS value_usd
        FROM filing_holdings h
        JOIN filings f ON f.id = h.filing_id
        WHERE h.symbol != '' AND COALESCE(h.put_call, '') = ''
        ORDER BY f.report_date, h.value_usd DESC
        """
    ).fetchall()
    quarters = sorted({row["report_date"] for row in filing_rows if row["report_date"]})
    if len(quarters) < 2:
        return {
            "signal": signal,
            "status": "insufficient_data",
            "message": "Need at least two stored 13F quarters to compare manager position changes.",
        }
    latest, previous = quarters[-1], quarters[-2]
    latest_values = values_for_quarter(filing_rows, latest)
    previous_values = values_for_quarter(filing_rows, previous)
    adds = []
    for symbol, value in latest_values.items():
        prev = previous_values.get(symbol, Decimal("0"))
        delta = value - prev
        if delta > 0:
            adds.append({"symbol": symbol, "delta_value": float(delta), "latest_value": float(value)})
    adds.sort(key=lambda row: row["delta_value"], reverse=True)
    return {
        "signal": signal,
        "status": "ok",
        "latest_quarter": latest,
        "previous_quarter": previous,
        "top_adds": adds[:10],
        "message": "This is a filing-change diagnostic, not a return backtest; add price history for performance attribution.",
    }


def values_for_quarter(rows, quarter: str) -> dict[str, Decimal]:
    values: dict[str, Decimal] = {}
    for row in rows:
        if row["report_date"] != quarter:
            continue
        values[row["symbol"]] = values.get(row["symbol"], Decimal("0")) + Decimal(str(row["value_usd"] or 0))
    return values
