from __future__ import annotations

from datetime import date
from typing import Any


AUDIT_VERSION = "2026-05-site-engine-audit-v1"
WEAK_SOURCE_STATUSES = {"missing", "stale", "limited", "estimated", "unknown", "failed", "error"}


def build_audit_snapshot(
    as_of: date,
    session: str,
    data_health: dict[str, Any],
    calendars: dict[str, Any],
    engine: dict[str, Any],
    paper_portfolio: dict[str, Any],
    methodology: dict[str, Any],
    outcome_diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gaps = data_gaps(data_health, calendars, engine, outcome_diagnostics)
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


def data_gaps(
    data_health: dict[str, Any],
    calendars: dict[str, Any],
    engine: dict[str, Any],
    outcome_diagnostics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    gaps = []
    for source in data_health.get("sources", []):
        if source.get("status") in WEAK_SOURCE_STATUSES:
            gaps.append({"area": "source", "label": source.get("label", ""), "status": source.get("status", ""), "detail": source.get("detail", "")})
    earnings = calendars.get("earnings") or {}
    if not earnings.get("event_count"):
        gaps.append({"area": "calendar", "label": "Earnings calendar", "status": "limited", "detail": "No earnings events available."})
    elif earnings.get("source_quality") == "limited":
        gaps.append({"area": "calendar", "label": "Earnings calendar", "status": "limited", "detail": "Only catalyst/result markers are available; no forward earnings-date provider matched."})
    if (engine.get("learning") or {}).get("status") == "baseline_fallback":
        learning = engine.get("learning") or {}
        gaps.append({"area": "engine", "label": "Learning reranker", "status": "baseline_fallback", "detail": learning_gap_detail(learning, outcome_diagnostics or {})})
    return gaps


def learning_gap_detail(learning: dict[str, Any], outcome_diagnostics: dict[str, Any]) -> str:
    details = [str(learning.get("message") or "Insufficient completed outcomes for learning.")]
    maturity = outcome_diagnostics.get("label_maturity") or {}
    if maturity:
        completed = int(maturity.get("completed_long_horizon_count") or 0)
        required = int(maturity.get("minimum_long_horizon_required") or learning.get("minimum_required") or 0)
        needed = int(maturity.get("additional_long_horizon_needed") or max(0, required - completed))
        details.append(f"{completed}/{required} completed 1-12 month labels; {needed} more needed.")
    projection = outcome_diagnostics.get("learning_readiness_projection") or {}
    if projection:
        details.append(
            "30-day projection: "
            f"{int(projection.get('projected_long_horizon_count_30d') or 0)}/"
            f"{int(projection.get('minimum_long_horizon_required') or 0)} labels; "
            f"{int(projection.get('projected_additional_needed_30d') or 0)} more still needed."
        )
        if projection.get("next_learning_label_due_date"):
            details.append(
                "Next learning-label projection: "
                f"{int(projection.get('projected_long_horizon_count_next_learning_label') or 0)}/"
                f"{int(projection.get('minimum_long_horizon_required') or 0)} labels after "
                f"{projection.get('next_learning_label_due_date')}; "
                f"{int(projection.get('projected_additional_needed_next_learning_label') or 0)} more still needed."
            )
        if projection.get("estimated_learning_ready_date"):
            details.append(
                "Estimated learning-ready date: "
                f"{projection.get('estimated_learning_ready_date')} "
                f"({int(projection.get('estimated_learning_ready_projected_count') or 0)}/"
                f"{int(projection.get('minimum_long_horizon_required') or 0)} labels)."
            )
        elif not projection.get("learning_ready_with_scheduled_pending_labels"):
            details.append("Queued learning labels do not yet cover the readiness threshold.")
    external_projection = outcome_diagnostics.get("external_learning_readiness_projection") or {}
    if external_projection:
        required = int(external_projection.get("minimum_external_long_horizon_required") or 0)
        scheduled = int(external_projection.get("projected_external_long_horizon_count_all_scheduled") or 0)
        remaining = int(external_projection.get("projected_external_additional_needed_all_scheduled") or 0)
        if external_projection.get("estimated_external_learning_ready_date"):
            details.append(
                "External-signal learning projection: "
                f"{external_projection.get('estimated_external_learning_ready_projected_count')}/"
                f"{required} externally covered labels after "
                f"{external_projection.get('estimated_external_learning_ready_date')}."
            )
        else:
            details.append(
                "External-signal learning bottleneck: "
                f"{scheduled}/{required} externally covered labels after all scheduled labels; "
                f"{remaining} more still needed."
            )
        if external_projection.get("next_external_fast_label_due_date"):
            details.append(
                "External-signal fast check: "
                f"{int(external_projection.get('next_external_fast_label_due_count') or 0)} "
                f"5-day labels due {external_projection.get('next_external_fast_label_due_date')}; "
                f"{int(external_projection.get('external_fast_labels_due_next_30d') or 0)} due within 30 days."
            )
    coverage_plan = outcome_diagnostics.get("external_coverage_gap_plan") or {}
    priority_rows = coverage_plan.get("priority_rows") or []
    if priority_rows:
        symbols = ", ".join(str(row.get("symbol") or "") for row in priority_rows[:5] if row.get("symbol"))
        gap_ids = ", ".join(str(row.get("external_coverage_gap_id") or "") for row in priority_rows[:3] if row.get("external_coverage_gap_id"))
        details.append(
            "External coverage priority backfill: "
            f"{int(coverage_plan.get('priority_gap_count') or len(priority_rows))} decision-time items"
            f"{f' ({symbols})' if symbols else ''} project "
            f"{int(coverage_plan.get('projected_external_long_horizon_count_after_priority_backfill') or 0)}/"
            f"{int(coverage_plan.get('minimum_external_long_horizon_required') or 0)} external labels"
            f"{f' by {coverage_plan.get('projected_external_learning_ready_date_after_priority_backfill')}' if coverage_plan.get('projected_external_learning_ready_date_after_priority_backfill') else ''}; "
            f"gap ids {gap_ids}."
        )
    schedule = outcome_diagnostics.get("pending_label_schedule") or {}
    next_learning = schedule.get("next_learning_label") or {}
    if next_learning.get("due_date"):
        details.append("Next learning-eligible label " + due_phrase(next_learning) + ".")
    learning_windows = schedule.get("learning_due_window_counts") or {}
    if learning_windows:
        details.append(
            "Learning labels due soon: "
            f"{int(learning_windows.get('due_next_7d') or 0)} within 7 days, "
            f"{int(learning_windows.get('due_next_30d') or 0)} within 30 days."
        )
    overdue = int(schedule.get("overdue_learning_label_count") or 0)
    if overdue:
        details.append(f"{overdue} learning-eligible labels are overdue for price-history refresh.")
    return " ".join(details)


def due_phrase(label: dict[str, Any]) -> str:
    due_date = label.get("due_date")
    days = label.get("days_until_due")
    if days is None:
        return f"due {due_date}"
    if days < 0:
        return f"due {due_date}, overdue by {abs(int(days))} days"
    if days == 0:
        return f"due {due_date}, today"
    return f"due {due_date}, in {int(days)} days"
