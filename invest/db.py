from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Filing, Holding, NewsItem, Position, Transaction
from .util import ensure_dir, json_dumps


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    broker TEXT NOT NULL,
    name TEXT NOT NULL,
    external_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (broker, name, external_id)
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    broker TEXT NOT NULL,
    account TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    action TEXT NOT NULL,
    symbol TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    quantity TEXT NOT NULL DEFAULT '0',
    price TEXT NOT NULL DEFAULT '0',
    amount TEXT NOT NULL DEFAULT '0',
    fees TEXT NOT NULL DEFAULT '0',
    currency TEXT NOT NULL DEFAULT 'USD',
    external_id TEXT NOT NULL,
    raw_json TEXT NOT NULL DEFAULT '{}',
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (broker, account, external_id)
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY,
    broker TEXT NOT NULL,
    account TEXT NOT NULL,
    as_of TEXT NOT NULL,
    symbol TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    quantity TEXT NOT NULL DEFAULT '0',
    cost_basis TEXT NOT NULL DEFAULT '0',
    market_value TEXT NOT NULL DEFAULT '0',
    currency TEXT NOT NULL DEFAULT 'USD',
    raw_json TEXT NOT NULL DEFAULT '{}',
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (broker, account, as_of, symbol)
);

CREATE TABLE IF NOT EXISTS filings (
    id INTEGER PRIMARY KEY,
    manager_key TEXT NOT NULL,
    manager_name TEXT NOT NULL,
    cik TEXT NOT NULL,
    accession_number TEXT NOT NULL,
    form TEXT NOT NULL,
    filing_date TEXT NOT NULL,
    report_date TEXT,
    url TEXT NOT NULL,
    raw_json TEXT NOT NULL DEFAULT '{}',
    processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (manager_key, accession_number)
);

CREATE TABLE IF NOT EXISTS filing_holdings (
    id INTEGER PRIMARY KEY,
    filing_id INTEGER NOT NULL REFERENCES filings(id) ON DELETE CASCADE,
    accession_number TEXT NOT NULL,
    issuer TEXT NOT NULL,
    title_class TEXT NOT NULL DEFAULT '',
    cusip TEXT NOT NULL DEFAULT '',
    value_usd TEXT NOT NULL DEFAULT '0',
    shares TEXT NOT NULL DEFAULT '0',
    put_call TEXT NOT NULL DEFAULT '',
    symbol TEXT NOT NULL DEFAULT '',
    bucket TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (filing_id, issuer, cusip, put_call)
);

CREATE TABLE IF NOT EXISTS news_items (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    published_at TEXT,
    summary TEXT NOT NULL DEFAULT '',
    query TEXT NOT NULL DEFAULT '',
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (url)
);

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    path TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    row_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE (source, path)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    ensure_dir(path.parent)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def insert_transactions(conn: sqlite3.Connection, transactions: list[Transaction]) -> int:
    count = 0
    for tx in transactions:
        before = conn.total_changes
        conn.execute(
            """
            INSERT OR IGNORE INTO transactions
            (broker, account, trade_date, action, symbol, description, quantity, price, amount, fees, currency, external_id, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tx.broker,
                tx.account,
                tx.trade_date.isoformat(),
                tx.action,
                tx.symbol.upper(),
                tx.description,
                str(tx.quantity),
                str(tx.price),
                str(tx.amount),
                str(tx.fees),
                tx.currency,
                tx.external_id,
                json_dumps(tx.raw),
            ),
        )
        count += conn.total_changes - before
    conn.commit()
    return count


def insert_positions(conn: sqlite3.Connection, positions: list[Position]) -> int:
    count = 0
    for pos in positions:
        before = conn.total_changes
        conn.execute(
            """
            INSERT OR REPLACE INTO positions
            (broker, account, as_of, symbol, description, quantity, cost_basis, market_value, currency, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pos.broker,
                pos.account,
                pos.as_of.isoformat(),
                pos.symbol.upper(),
                pos.description,
                str(pos.quantity),
                str(pos.cost_basis),
                str(pos.market_value),
                pos.currency,
                json_dumps(pos.raw),
            ),
        )
        count += conn.total_changes - before
    conn.commit()
    return count


def upsert_filing(conn: sqlite3.Connection, filing: Filing, holdings: list[Holding]) -> int:
    conn.execute(
        """
        INSERT OR REPLACE INTO filings
        (manager_key, manager_name, cik, accession_number, form, filing_date, report_date, url, raw_json, processed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            filing.manager_key,
            filing.manager_name,
            filing.cik,
            filing.accession_number,
            filing.form,
            filing.filing_date.isoformat(),
            filing.report_date.isoformat() if filing.report_date else None,
            filing.url,
            json_dumps(filing.raw),
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )
    filing_id = conn.execute(
        "SELECT id FROM filings WHERE manager_key = ? AND accession_number = ?",
        (filing.manager_key, filing.accession_number),
    ).fetchone()["id"]
    conn.execute("DELETE FROM filing_holdings WHERE filing_id = ?", (filing_id,))
    merged_holdings = merge_duplicate_holdings(holdings)
    for holding in merged_holdings:
        conn.execute(
            """
            INSERT INTO filing_holdings
            (filing_id, accession_number, issuer, title_class, cusip, value_usd, shares, put_call, symbol, bucket, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                filing_id,
                holding.accession_number,
                holding.issuer,
                holding.title_class,
                holding.cusip,
                str(holding.value_usd),
                str(holding.shares),
                holding.put_call,
                holding.symbol.upper(),
                holding.bucket,
                json_dumps(holding.raw),
            ),
        )
    conn.commit()
    return len(merged_holdings)


def merge_duplicate_holdings(holdings: list[Holding]) -> list[Holding]:
    merged: dict[tuple[str, str, str], Holding] = {}
    for holding in holdings:
        key = (holding.issuer, holding.cusip, holding.put_call)
        existing = merged.get(key)
        if not existing:
            merged[key] = holding
            continue
        title_class = existing.title_class
        if holding.title_class and holding.title_class not in title_class:
            title_class = f"{title_class}; {holding.title_class}" if title_class else holding.title_class
        merged[key] = Holding(
            accession_number=existing.accession_number,
            issuer=existing.issuer,
            title_class=title_class,
            cusip=existing.cusip,
            value_usd=existing.value_usd + holding.value_usd,
            shares=existing.shares + holding.shares,
            put_call=existing.put_call,
            symbol=existing.symbol or holding.symbol,
            bucket=existing.bucket or holding.bucket,
            raw={"merged_rows": [existing.raw, holding.raw]},
        )
    return list(merged.values())


def insert_news(conn: sqlite3.Connection, items: list[NewsItem]) -> int:
    count = 0
    for item in items:
        before = conn.total_changes
        conn.execute(
            """
            INSERT OR IGNORE INTO news_items
            (source, title, url, published_at, summary, query)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                item.source,
                item.title,
                item.url,
                item.published_at.isoformat() if item.published_at else None,
                item.summary,
                item.query,
            ),
        )
        count += conn.total_changes - before
    conn.commit()
    return count


def record_import(conn: sqlite3.Connection, source: str, path: Path, row_count: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO imports (source, path, row_count, imported_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
        (source, str(path), row_count),
    )
    conn.commit()
