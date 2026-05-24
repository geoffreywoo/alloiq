from __future__ import annotations

import json
import urllib.request
from datetime import date
from typing import Any

from .config import AppConfig
from .util import SEC_USER_AGENT, parse_date, stable_id


SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
RESULT_FORMS = {"8-K", "10-Q", "10-K"}


def build_earnings_events(
    config: AppConfig,
    symbols: list[str],
    as_of: date,
    news_events: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    allowed = {symbol.upper() for symbol in symbols}
    events: list[dict[str, Any]] = []
    events.extend(manual_earnings_events(config, as_of, allowed))
    events.extend(news_earnings_events(news_events or {}, as_of, allowed))
    events.extend(sec_company_events(config, as_of, allowed))
    return dedupe_events(events)


def manual_earnings_events(config: AppConfig, as_of: date, allowed_symbols: set[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in config.manual_earnings_events:
        symbol = str(row.get("symbol") or "").upper().strip()
        event_date = parse_date(row.get("event_date") or row.get("date"))
        if not symbol or symbol not in allowed_symbols or not event_date:
            continue
        events.append(
            event_payload(
                symbol=symbol,
                as_of=as_of,
                event_date=event_date,
                event_type="earnings",
                source=str(row.get("source") or "manual"),
                title=str(row.get("title") or f"{symbol} earnings {row.get('fiscal_period', '')}").strip(),
                status=str(row.get("status") or "scheduled"),
                catalyst_types=["earnings"],
                raw=row,
            )
        )
    return events


def news_earnings_events(
    news_events: dict[str, dict[str, Any]],
    as_of: date,
    allowed_symbols: set[str],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for symbol, row in news_events.items():
        normalized = str(symbol).upper()
        event_types = set(row.get("event_types") or [])
        if normalized not in allowed_symbols or "earnings_revision" not in event_types:
            continue
        events.append(
            event_payload(
                symbol=normalized,
                as_of=as_of,
                event_date=as_of,
                event_type="earnings_catalyst",
                source="news",
                title=f"{normalized} earnings or guidance catalyst in news tape",
                status="detected",
                catalyst_types=sorted(event_types),
                raw=row,
            )
        )
    return events


def sec_company_events(config: AppConfig, as_of: date, allowed_symbols: set[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in config.earnings_sec_companies:
        symbol = str(row.get("symbol") or "").upper().strip()
        cik = str(row.get("cik") or "").strip()
        if not symbol or symbol not in allowed_symbols or not cik:
            continue
        try:
            latest = latest_company_result_filing(cik)
        except Exception:
            continue
        if not latest:
            continue
        event_date = parse_date(latest.get("filingDate")) or as_of
        events.append(
            event_payload(
                symbol=symbol,
                as_of=as_of,
                event_date=event_date,
                event_type="sec_result_marker",
                source="sec_company_submissions",
                title=f"{symbol} latest {latest.get('form', 'SEC')} filed {event_date.isoformat()}",
                status="filed",
                catalyst_types=["sec_filing", str(latest.get("form") or "").lower()],
                raw=latest,
            )
        )
    return events


def latest_company_result_filing(cik: str) -> dict[str, Any] | None:
    cik_padded = str(cik).strip().lstrip("0").zfill(10)
    request = urllib.request.Request(
        SEC_SUBMISSIONS_URL.format(cik=cik_padded),
        headers={"User-Agent": SEC_USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form") or []
    filing_dates = recent.get("filingDate") or []
    accession_numbers = recent.get("accessionNumber") or []
    primary_docs = recent.get("primaryDocument") or []
    for form, filing_date, accession, primary_doc in zip(forms, filing_dates, accession_numbers, primary_docs):
        if form in RESULT_FORMS:
            return {
                "form": form,
                "filingDate": filing_date,
                "accessionNumber": accession,
                "primaryDocument": primary_doc,
            }
    return None


def event_payload(
    symbol: str,
    as_of: date,
    event_date: date,
    event_type: str,
    source: str,
    title: str,
    status: str,
    catalyst_types: list[str],
    raw: dict[str, Any],
) -> dict[str, Any]:
    days_until = (event_date - as_of).days
    return {
        "event_id": stable_id([symbol, event_date.isoformat(), event_type, source]),
        "symbol": symbol,
        "event_date": event_date.isoformat(),
        "event_type": event_type,
        "source": source,
        "title": title,
        "status": status,
        "days_until": days_until,
        "catalyst_types": catalyst_types,
        "raw": raw,
    }


def dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for event in events:
        key = event.get("event_id") or stable_id([event.get("symbol"), event.get("event_date"), event.get("event_type")])
        deduped[str(key)] = event
    return sorted(
        deduped.values(),
        key=lambda row: (abs(int(row.get("days_until") or 9999)), row.get("symbol", ""), row.get("event_type", "")),
    )
