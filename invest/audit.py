from __future__ import annotations

from datetime import date
from typing import Any


AUDIT_VERSION = "2026-05-site-engine-audit-v1"


def build_audit_snapshot(
    as_of: date,
    session: str,
    data_health: dict[str, Any],
    calendars: dict[str, Any],
    engine: dict[str, Any],
    paper_portfolio: dict[str, Any],
    methodology: dict[str, Any],
) -> dict[str, Any]:
    gaps = data_gaps(data_health, calendars, engine)
    return {
        "version": AUDIT_VERSION,
        "as_of": as_of.isoformat(),
        "session": session,
        "overall_status": "attention" if gaps else "ok",
        "engine_version": engine.get("version", ""),
        "methodology_version": methodology.get("version", ""),
        "privacy_scan": {"status": "required_after_build", "scope": "public web assets"},
        "source_freshness": source_freshness(data_health),
        "schedule_health": schedule_health(session),
        "calendar_health": calendar_health(calendars),
        "engine_health": engine_health(engine, paper_portfolio),
        "data_gaps": gaps,
    }


def source_freshness(data_health: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for source in data_health.get("sources", []):
        rows.append(
            {
                "source": source.get("source", ""),
                "label": source.get("label", ""),
                "status": source.get("status", "unknown"),
                "detail": source.get("detail", ""),
            }
        )
    return rows


def schedule_health(session: str) -> dict[str, Any]:
    return {
        "status": "configured",
        "current_run_kind": session,
        "expected_runs": [
            {"kind": "premarket", "cadence": "8:00 AM ET on NYSE trading days"},
            {"kind": "postmarket", "cadence": "4:30 PM ET on NYSE trading days"},
            {"kind": "weekly", "cadence": "Sunday morning ET"},
        ],
        "deferred_publication_policy": "Do not publish if failed broker sync would shrink the public portfolio snapshot.",
    }


def calendar_health(calendars: dict[str, Any]) -> dict[str, Any]:
    earnings = calendars.get("earnings") or {}
    filings = calendars.get("filings_13f") or {}
    earnings_quality = earnings.get("source_quality", "unknown")
    earnings_ok = earnings.get("event_count") and earnings_quality in {"ok", "estimated"}
    return {
        "status": "ok" if earnings_ok and filings.get("manager_count") else "limited",
        "earnings_event_count": earnings.get("event_count", 0),
        "earnings_source_quality": earnings_quality,
        "filing_cycle": (filings.get("current_cycle") or {}).get("label", ""),
        "filing_deadline": (filings.get("current_cycle") or {}).get("deadline", ""),
        "filing_late_count": filings.get("late_count", 0),
    }


def engine_health(engine: dict[str, Any], paper_portfolio: dict[str, Any]) -> dict[str, Any]:
    learning = engine.get("learning") or {}
    metrics = paper_portfolio.get("metrics") or {}
    return {
        "status": "ok" if engine.get("feature_count", 0) else "limited",
        "mode": engine.get("mode", ""),
        "objective": engine.get("objective", ""),
        "learning_status": learning.get("status", "unknown"),
        "feature_count": engine.get("feature_count", 0),
        "ranked_candidate_count": len(engine.get("ranked_candidates") or []),
        "paper_trade_count": metrics.get("paper_trade_count", 0),
        "live_order_execution": engine.get("live_order_execution", "disabled"),
    }


def data_gaps(data_health: dict[str, Any], calendars: dict[str, Any], engine: dict[str, Any]) -> list[dict[str, Any]]:
    gaps = []
    for source in data_health.get("sources", []):
        if source.get("status") in {"missing", "stale", "limited"}:
            gaps.append({"area": "source", "label": source.get("label", ""), "status": source.get("status", ""), "detail": source.get("detail", "")})
    earnings = calendars.get("earnings") or {}
    if not earnings.get("event_count"):
        gaps.append({"area": "calendar", "label": "Earnings calendar", "status": "limited", "detail": "No earnings events available."})
    elif earnings.get("source_quality") == "limited":
        gaps.append({"area": "calendar", "label": "Earnings calendar", "status": "limited", "detail": "Only catalyst/result markers are available; no forward earnings-date provider matched."})
    if (engine.get("learning") or {}).get("status") == "baseline_fallback":
        gaps.append({"area": "engine", "label": "Learning reranker", "status": "baseline_fallback", "detail": (engine.get("learning") or {}).get("message", "")})
    return gaps
