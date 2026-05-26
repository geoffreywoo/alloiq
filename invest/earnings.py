from __future__ import annotations

import csv
import html
import io
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

from .config import AppConfig
from .util import SEC_USER_AGENT, parse_date, stable_id


SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
NASDAQ_EARNINGS_URL = "https://api.nasdaq.com/api/calendar/earnings"
RESULT_FORMS = {"8-K", "10-Q", "10-K"}
DATE_PROVIDER_SOURCES = {
    "manual",
    "company_ir_feed",
    "alpha_vantage_earnings_calendar",
    "nasdaq_earnings_calendar",
}
SOURCE_PRIORITY = {
    "manual": 100,
    "company_ir_feed": 90,
    "alpha_vantage_earnings_calendar": 80,
    "nasdaq_earnings_calendar": 70,
    "sec_company_submissions": 50,
    "news": 20,
}
EARNINGS_KEYWORDS = (
    "earnings",
    "financial results",
    "quarterly results",
    "fiscal results",
    "results conference call",
    "earnings conference call",
)
MONTH_PATTERN = (
    r"January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan\.?|Feb\.?|Mar\.?|Apr\.?|Jun\.?|Jul\.?|Aug\.?|Sep\.?|Sept\.?|Oct\.?|Nov\.?|Dec\.?"
)


def build_earnings_events(
    config: AppConfig,
    symbols: list[str],
    as_of: date,
    news_events: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    allowed = {symbol.upper() for symbol in symbols}
    events: list[dict[str, Any]] = []
    events.extend(manual_earnings_events(config, as_of, allowed))
    events.extend(alpha_vantage_earnings_events(config, as_of, allowed))
    events.extend(nasdaq_earnings_events(config, as_of, allowed))
    events.extend(ir_feed_earnings_events(config, as_of, allowed))
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


def alpha_vantage_earnings_events(config: AppConfig, as_of: date, allowed_symbols: set[str]) -> list[dict[str, Any]]:
    settings = earnings_provider_settings(config)
    if not bool(settings.get("alpha_vantage_enabled", True)):
        return []
    api_key_env = str(settings.get("alpha_vantage_api_key_env") or "ALPHA_VANTAGE_API_KEY")
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        return []
    horizon = str(settings.get("alpha_vantage_horizon") or "3month")
    if horizon not in {"3month", "6month", "12month"}:
        horizon = "3month"
    params = urllib.parse.urlencode(
        {
            "function": "EARNINGS_CALENDAR",
            "horizon": horizon,
            "apikey": api_key,
        }
    )
    try:
        text = fetch_text(f"{ALPHA_VANTAGE_URL}?{params}", timeout=25)
    except Exception:
        return []
    return parse_alpha_vantage_calendar(text, as_of, allowed_symbols)


def parse_alpha_vantage_calendar(text: str, as_of: date, allowed_symbols: set[str]) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text.strip()))
    if not reader.fieldnames or "symbol" not in {name.strip() for name in reader.fieldnames if name}:
        return []
    events: list[dict[str, Any]] = []
    for row in reader:
        symbol = str(row.get("symbol") or "").upper().strip()
        event_date = parse_date(row.get("reportDate"))
        if not symbol or symbol not in allowed_symbols or not event_date:
            continue
        events.append(
            event_payload(
                symbol=symbol,
                as_of=as_of,
                event_date=event_date,
                event_type="earnings",
                source="alpha_vantage_earnings_calendar",
                title=f"{symbol} expected earnings report",
                status="estimated",
                catalyst_types=["earnings"],
                raw={
                    "name": row.get("name", ""),
                    "fiscalDateEnding": row.get("fiscalDateEnding", ""),
                    "timeOfTheDay": row.get("timeOfTheDay", ""),
                },
            )
        )
    return events


def nasdaq_earnings_events(config: AppConfig, as_of: date, allowed_symbols: set[str]) -> list[dict[str, Any]]:
    settings = earnings_provider_settings(config)
    if not bool(settings.get("nasdaq_enabled", True)):
        return []
    lookahead_days = int(settings.get("nasdaq_lookahead_days") or 45)
    lookahead_days = max(0, min(lookahead_days, 120))
    max_requests = max(1, min(int(settings.get("nasdaq_max_requests") or 20), 90))
    timeout_seconds = max(1, min(int(settings.get("nasdaq_timeout_seconds") or 3), 15))
    retries = max(1, min(int(settings.get("nasdaq_retries") or 2), 4))
    events: list[dict[str, Any]] = []
    request_count = 0
    for offset in range(lookahead_days + 1):
        event_date = as_of + timedelta(days=offset)
        if event_date.weekday() >= 5:
            continue
        if request_count >= max_requests:
            break
        rows: list[dict[str, Any]] = []
        for attempt in range(retries):
            try:
                rows = fetch_nasdaq_earnings_rows(event_date, timeout=timeout_seconds)
                break
            except Exception:
                if attempt == retries - 1:
                    rows = []
        request_count += 1
        if not rows:
            continue
        events.extend(parse_nasdaq_calendar_rows(rows, as_of, event_date, allowed_symbols))
        found_symbols = {row.get("symbol") for row in events}
        if allowed_symbols and found_symbols >= allowed_symbols:
            break
    return events


def fetch_nasdaq_earnings_rows(event_date: date, timeout: int = 5) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"date": event_date.isoformat()})
    text = fetch_text(
        f"{NASDAQ_EARNINGS_URL}?{params}",
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "identity",
            "Connection": "close",
            "Origin": "https://www.nasdaq.com",
            "Referer": "https://www.nasdaq.com/market-activity/earnings",
        },
        timeout=timeout,
    )
    payload = json.loads(text)
    data = payload.get("data") or {}
    return data.get("rows") or []


def parse_nasdaq_calendar_rows(
    rows: list[dict[str, Any]],
    as_of: date,
    event_date: date,
    allowed_symbols: set[str],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in rows:
        symbol = clean_text(row.get("symbol", "")).upper()
        if not symbol or symbol not in allowed_symbols:
            continue
        fiscal_quarter = clean_text(row.get("fiscalQuarterEnding", ""))
        time_label = clean_text(row.get("time", "")).replace("time-", "").replace("-", " ")
        title = f"{symbol} expected earnings"
        if fiscal_quarter:
            title += f" for quarter ending {fiscal_quarter}"
        events.append(
            event_payload(
                symbol=symbol,
                as_of=as_of,
                event_date=event_date,
                event_type="earnings",
                source="nasdaq_earnings_calendar",
                title=title,
                status="estimated",
                catalyst_types=["earnings"],
                raw={
                    "name": clean_text(row.get("name", "")),
                    "time": time_label,
                    "lastYearRptDt": clean_text(row.get("lastYearRptDt", "")),
                    "data_provider": "Zacks via Nasdaq",
                },
            )
        )
    return events


def ir_feed_earnings_events(config: AppConfig, as_of: date, allowed_symbols: set[str]) -> list[dict[str, Any]]:
    settings = earnings_provider_settings(config)
    if not bool(settings.get("ir_feed_enabled", True)):
        return []
    events: list[dict[str, Any]] = []
    for feed in config.earnings_ir_feeds:
        symbol = str(feed.get("symbol") or "").upper().strip()
        url = str(feed.get("url") or "").strip()
        if not symbol or symbol not in allowed_symbols or not url:
            continue
        try:
            text = fetch_text(url, timeout=20)
            items = parse_ir_feed_items(text)
        except Exception:
            continue
        max_items = int(feed.get("max_items") or 20)
        for item in items[:max_items]:
            event = ir_feed_item_event(symbol, url, item, as_of)
            if event:
                events.append(event)
    return events


def parse_ir_feed_items(text: str) -> list[dict[str, str]]:
    root = ET.fromstring(text)
    items: list[dict[str, str]] = []
    for item in root.findall(".//item"):
        items.append(
            {
                "title": node_text(item, "title"),
                "summary": node_text(item, "description"),
                "link": node_text(item, "link"),
                "published_at": node_text(item, "pubDate") or node_text(item, "date"),
            }
        )
    atom_ns = "{http://www.w3.org/2005/Atom}"
    for entry in root.findall(f".//{atom_ns}entry"):
        link = ""
        link_node = entry.find(f"{atom_ns}link")
        if link_node is not None:
            link = str(link_node.attrib.get("href") or "")
        items.append(
            {
                "title": node_text(entry, f"{atom_ns}title"),
                "summary": node_text(entry, f"{atom_ns}summary") or node_text(entry, f"{atom_ns}content"),
                "link": link,
                "published_at": node_text(entry, f"{atom_ns}updated") or node_text(entry, f"{atom_ns}published"),
            }
        )
    return items


def node_text(parent: ET.Element, name: str) -> str:
    node = parent.find(name)
    if node is None or node.text is None:
        return ""
    return clean_text(node.text)


def ir_feed_item_event(symbol: str, feed_url: str, item: dict[str, str], as_of: date) -> dict[str, Any] | None:
    title = clean_text(item.get("title", ""))
    summary = clean_text(item.get("summary", ""))
    text = f"{title} {summary}".strip()
    if not text or not contains_earnings_language(text):
        return None
    parsed_event_date = extract_earnings_date(text, as_of)
    published_date = parse_feed_date(item.get("published_at"))
    event_date = parsed_event_date or published_date
    if not event_date:
        return None
    event_type = "earnings" if parsed_event_date and event_date >= as_of - timedelta(days=1) else "ir_result_marker"
    status = "confirmed" if event_type == "earnings" else "filed"
    return event_payload(
        symbol=symbol,
        as_of=as_of,
        event_date=event_date,
        event_type=event_type,
        source="company_ir_feed",
        title=title or f"{symbol} company IR earnings update",
        status=status,
        catalyst_types=["earnings", "company_ir"],
        raw={
            "feed_url": feed_url,
            "link": item.get("link", ""),
            "published_at": item.get("published_at", ""),
        },
    )


def contains_earnings_language(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in EARNINGS_KEYWORDS)


def extract_earnings_date(text: str, as_of: date) -> date | None:
    iso_match = re.search(r"\b20\d{2}-\d{2}-\d{2}\b", text)
    if iso_match:
        return parse_date(iso_match.group(0))
    numeric_match = re.search(r"\b\d{1,2}/\d{1,2}/20\d{2}\b", text)
    if numeric_match:
        return parse_date(numeric_match.group(0))
    month_year_match = re.search(rf"\b({MONTH_PATTERN})\s+\d{{1,2}},\s+20\d{{2}}\b", text, flags=re.IGNORECASE)
    if month_year_match:
        for fmt in ("%B %d, %Y", "%b %d, %Y", "%b. %d, %Y"):
            try:
                return datetime.strptime(month_year_match.group(0).replace("Sept.", "Sep."), fmt).date()
            except ValueError:
                continue
    month_day_match = re.search(rf"\b({MONTH_PATTERN})\s+\d{{1,2}}\b", text, flags=re.IGNORECASE)
    if month_day_match:
        candidate_text = f"{month_day_match.group(0).replace('Sept.', 'Sep.')} {as_of.year}"
        for fmt in ("%B %d %Y", "%b %d %Y", "%b. %d %Y"):
            try:
                candidate = datetime.strptime(candidate_text, fmt).date()
            except ValueError:
                continue
            if candidate < as_of - timedelta(days=30):
                candidate = candidate.replace(year=candidate.year + 1)
            return candidate
    return None


def parse_feed_date(value: Any) -> date | None:
    parsed = parse_date(value)
    if parsed:
        return parsed
    try:
        parsed_dt = parsedate_to_datetime(str(value))
    except (TypeError, ValueError):
        return None
    return parsed_dt.date()


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
    confidence = confidence_for_source(source)
    confirmed_or_estimated = confirmation_for_event(source, status)
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
        "confidence": confidence,
        "confirmed_or_estimated": confirmed_or_estimated,
        "last_checked_at": f"{as_of.isoformat()}T00:00:00Z",
        "risk_window": "blackout" if abs(days_until) <= 2 else "risk_window" if abs(days_until) <= 7 else "clear",
        "raw": raw,
    }


def dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for event in events:
        key = dedupe_key(event)
        existing = deduped.get(key)
        if existing is None or event_priority(event) > event_priority(existing):
            deduped[key] = event
    return sorted(
        deduped.values(),
        key=lambda row: (abs(int(row.get("days_until") or 9999)), row.get("symbol", ""), row.get("event_type", "")),
    )


def dedupe_key(event: dict[str, Any]) -> str:
    event_type = str(event.get("event_type") or "")
    symbol = str(event.get("symbol") or "")
    event_date = str(event.get("event_date") or "")
    if event_type == "earnings":
        return stable_id([symbol, event_date, "earnings"])
    if event_type in {"sec_result_marker", "ir_result_marker"}:
        return stable_id([symbol, event_date, event_type])
    return str(event.get("event_id") or stable_id([symbol, event_date, event_type, event.get("source")]))


def event_priority(event: dict[str, Any]) -> tuple[int, float]:
    source = str(event.get("source") or "")
    return (SOURCE_PRIORITY.get(source, 0), float(event.get("confidence") or 0))


def confidence_for_source(source: str) -> float:
    if source == "manual":
        return 1.0
    if source == "company_ir_feed":
        return 0.9
    if source == "alpha_vantage_earnings_calendar":
        return 0.85
    if source == "nasdaq_earnings_calendar":
        return 0.7
    if source == "sec_company_submissions":
        return 0.75
    if source == "news":
        return 0.45
    return 0.35


def confirmation_for_event(source: str, status: str) -> str:
    if source in {"manual", "company_ir_feed", "sec_company_submissions"}:
        return "confirmed"
    if str(status).lower() in {"confirmed", "filed", "scheduled"}:
        return "confirmed"
    return "estimated"


def earnings_source_quality(events: list[dict[str, Any]]) -> str:
    if not events:
        return "limited"
    if any(row.get("event_type") == "earnings" and row.get("confirmed_or_estimated") == "confirmed" for row in events):
        return "ok"
    if any(row.get("event_type") == "earnings" and row.get("source") in DATE_PROVIDER_SOURCES for row in events):
        return "estimated"
    return "limited"


def earnings_health_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    source_quality = earnings_source_quality(events)
    date_events = [row for row in events if is_forward_earnings_date(row)]
    confirmed = sum(1 for row in date_events if row.get("confirmed_or_estimated") == "confirmed")
    estimated = sum(1 for row in date_events if row.get("confirmed_or_estimated") == "estimated")
    marker_count = len(events) - len(date_events)
    return {
        "status": "ok" if source_quality == "ok" else "limited" if source_quality == "limited" else "estimated",
        "source_quality": source_quality,
        "confirmed_count": confirmed,
        "estimated_count": estimated,
        "provider_date_count": len(date_events),
        "catalyst_marker_count": marker_count,
        "event_count": len(events),
    }


def is_forward_earnings_date(event: dict[str, Any]) -> bool:
    return event.get("event_type") == "earnings"


def earnings_provider_settings(config: AppConfig) -> dict[str, Any]:
    try:
        return config.earnings_provider_settings
    except AttributeError:
        return dict(config.data.get("earnings", {}).get("providers", {}))


def fetch_text(url: str, headers: dict[str, str] | None = None, timeout: int = 20) -> str:
    request = urllib.request.Request(
        url,
        headers=headers or {"User-Agent": "AlloIQ/0.1 earnings-calendar https://alloiq.com"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()
