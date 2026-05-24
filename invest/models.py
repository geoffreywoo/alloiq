from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class Transaction:
    broker: str
    account: str
    trade_date: date
    action: str
    symbol: str = ""
    description: str = ""
    quantity: Decimal = Decimal("0")
    price: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    currency: str = "USD"
    external_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Position:
    broker: str
    account: str
    as_of: date
    symbol: str
    description: str = ""
    quantity: Decimal = Decimal("0")
    cost_basis: Decimal = Decimal("0")
    market_value: Decimal = Decimal("0")
    currency: str = "USD"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Filing:
    manager_key: str
    manager_name: str
    cik: str
    accession_number: str
    form: str
    filing_date: date
    report_date: date | None
    url: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Holding:
    accession_number: str
    issuer: str
    title_class: str
    cusip: str
    value_usd: Decimal
    shares: Decimal
    put_call: str = ""
    symbol: str = ""
    bucket: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NewsItem:
    source: str
    title: str
    url: str
    published_at: datetime | None
    summary: str = ""
    query: str = ""

