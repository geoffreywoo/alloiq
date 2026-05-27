from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .config import AppConfig
from .earnings import earnings_confirmation_gaps, earnings_source_quality, is_forward_earnings_date
from .scheduler import EASTERN, is_nyse_trading_day


SEC_13F_RULE_URL = "https://www.sec.gov/divisions/investment/13ffaq.htm"


def build_calendar_snapshot(
    config: AppConfig,
    as_of: date,
    manager_radar: dict[str, Any],
    earnings_events: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "version": "2026-05-calendar-v1",
        "as_of": as_of.isoformat(),
        "earnings": normalize_earnings_calendar(earnings_events, as_of),
        "filings_13f": build_13f_calendar(config, as_of, manager_radar),
    }


def normalize_earnings_calendar(events: list[dict[str, Any]], as_of: date) -> dict[str, Any]:
    enriched = []
    missing_confidence = 0
    for event in events:
        row = dict(event)
        source = str(row.get("source") or "unknown")
        confidence = row.get("confidence")
        if confidence is None:
            confidence = confidence_for_source(source)
            missing_confidence += 1
        days_until = row.get("days_until")
        row["confidence"] = round(float(confidence), 2)
        row["confirmed_or_estimated"] = row.get("confirmed_or_estimated") or confirmation_status(row)
        row["last_checked_at"] = row.get("last_checked_at") or f"{as_of.isoformat()}T00:00:00Z"
        row["risk_window"] = earnings_risk_window(days_until)
        enriched.append(row)
    enriched.sort(key=lambda row: (abs(int(row.get("days_until") or 9999)), row.get("symbol", "")))
    date_events = [row for row in enriched if is_forward_earnings_date(row)]
    return {
        "events": enriched,
        "event_count": len(enriched),
        "confirmed_count": sum(1 for row in date_events if row.get("confirmed_or_estimated") == "confirmed"),
        "estimated_count": sum(1 for row in date_events if row.get("confirmed_or_estimated") == "estimated"),
        "provider_date_count": len(date_events),
        "catalyst_marker_count": len(enriched) - len(date_events),
        "confirmation_gap_count": len(earnings_confirmation_gaps(enriched, limit=None)),
        "confirmation_gaps": earnings_confirmation_gaps(enriched),
        "source_quality": earnings_source_quality(enriched),
        "missing_confidence_count": missing_confidence,
        "policy": "Manual and company IR dates are canonical; Alpha Vantage and Nasdaq provide estimated forward dates; SEC/result markers and news-derived events enrich risk windows.",
    }


def confidence_for_source(source: str) -> float:
    if source == "manual":
        return 1.0
    if source == "sec_company_submissions":
        return 0.75
    if source == "news":
        return 0.45
    return 0.35


def confirmation_status(event: dict[str, Any]) -> str:
    status = str(event.get("status") or "").lower()
    source = str(event.get("source") or "")
    if source in {"manual", "sec_company_submissions"} or status in {"confirmed", "filed", "scheduled"}:
        return "confirmed"
    return "estimated"


def earnings_risk_window(days_until: Any) -> str:
    if days_until is None:
        return "unknown"
    days = abs(int(days_until))
    if days <= 2:
        return "blackout"
    if days <= 7:
        return "risk_window"
    return "clear"


def build_13f_calendar(config: AppConfig, as_of: date, manager_radar: dict[str, Any]) -> dict[str, Any]:
    quarter = active_13f_quarter(as_of)
    previous = previous_13f_quarter(quarter["quarter_end"])
    quarters = [previous, quarter, next_13f_quarter(quarter["quarter_end"])]
    manager_status = manager_status_by_key(manager_radar)
    rows = []
    for manager in config.data.get("managers", []):
        key = str(manager.get("key") or "")
        latest = manager_status.get(key, {})
        rows.append(manager_13f_status(manager, latest, quarter, as_of))
    return {
        "rule": "Form 13F is due within 45 days after each calendar quarter end; weekend/holiday deadlines move to the next NYSE business day.",
        "rule_source": SEC_13F_RULE_URL,
        "current_cycle": quarter,
        "quarters": quarters,
        "manager_count": len(rows),
        "filed_count": sum(1 for row in rows if row["status"] == "filed"),
        "pending_count": sum(1 for row in rows if row["status"] == "pending"),
        "late_count": sum(1 for row in rows if row["status"] == "late"),
        "managers": rows,
    }


def manager_13f_status(
    manager: dict[str, Any],
    latest: dict[str, Any],
    cycle: dict[str, Any],
    as_of: date,
) -> dict[str, Any]:
    report_date = parse_date_text(latest.get("report_date"))
    filing_date = parse_date_text(latest.get("filing_date") or latest.get("latest_filing_date"))
    deadline = parse_date_text(cycle["deadline"])
    quarter_end = parse_date_text(cycle["quarter_end"])
    filed_for_cycle = bool(report_date and quarter_end and report_date == quarter_end)
    if filed_for_cycle:
        status = "filed"
    elif deadline and as_of > deadline:
        status = "late"
    else:
        status = "pending"
    return {
        "manager_key": str(manager.get("key") or ""),
        "manager_name": str(manager.get("display_name") or manager.get("name") or manager.get("key") or ""),
        "quarter_end": cycle["quarter_end"],
        "deadline": cycle["deadline"],
        "filing_season_start": cycle["filing_season_start"],
        "followup_end": cycle["followup_end"],
        "status": status,
        "latest_report_date": report_date.isoformat() if report_date else "",
        "latest_filing_date": filing_date.isoformat() if filing_date else "",
        "days_until_deadline": (deadline - as_of).days if deadline else None,
    }


def manager_status_by_key(manager_radar: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = {}
    for row in manager_radar.get("manager_status", []):
        key = str(row.get("manager_key") or "")
        if key:
            rows[key] = row
    return rows


def active_13f_quarter(as_of: date) -> dict[str, Any]:
    candidates = [quarter_payload(q) for q in quarter_ends_around(as_of)]
    filing_season = [
        row for row in candidates
        if parse_date_text(row["filing_season_start"]) <= as_of <= parse_date_text(row["followup_end"])
    ]
    if filing_season:
        return filing_season[0]
    future = [row for row in candidates if parse_date_text(row["deadline"]) >= as_of]
    return future[0] if future else candidates[-1]


def quarter_ends_around(as_of: date) -> list[date]:
    year = as_of.year
    quarters = []
    for candidate_year in (year - 1, year, year + 1):
        quarters.extend(
            [
                date(candidate_year, 3, 31),
                date(candidate_year, 6, 30),
                date(candidate_year, 9, 30),
                date(candidate_year, 12, 31),
            ]
        )
    return sorted(quarters)


def previous_13f_quarter(quarter_end: str) -> dict[str, Any]:
    end = parse_date_text(quarter_end)
    quarters = [row for row in quarter_ends_around(end) if row < end]
    return quarter_payload(quarters[-1])


def next_13f_quarter(quarter_end: str) -> dict[str, Any]:
    end = parse_date_text(quarter_end)
    quarters = [row for row in quarter_ends_around(end) if row > end]
    return quarter_payload(quarters[0])


def quarter_payload(quarter_end: date) -> dict[str, Any]:
    deadline = next_business_day(quarter_end + timedelta(days=45))
    return {
        "quarter_end": quarter_end.isoformat(),
        "deadline": deadline.isoformat(),
        "filing_season_start": (quarter_end + timedelta(days=35)).isoformat(),
        "followup_end": add_business_days(deadline, 5).isoformat(),
        "label": f"Q{((quarter_end.month - 1) // 3) + 1} {quarter_end.year}",
    }


def next_business_day(value: date) -> date:
    candidate = value
    while not is_business_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def add_business_days(value: date, days: int) -> date:
    candidate = value
    added = 0
    while added < days:
        candidate += timedelta(days=1)
        if is_business_day(candidate):
            added += 1
    return candidate


def is_business_day(value: date) -> bool:
    return is_nyse_trading_day(value_to_eastern_midday(value))


def value_to_eastern_midday(value: date):
    from datetime import datetime

    return datetime(value.year, value.month, value.day, 12, 0, tzinfo=EASTERN)


def parse_date_text(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
