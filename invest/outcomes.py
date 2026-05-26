from __future__ import annotations

from datetime import date
from statistics import mean
from typing import Any

from .features import MODEL_POLICY_VERSION
from .util import stable_id


OUTCOME_VERSION = "2026-05-outcome-diagnostics-v1"
TRAINING_EXAMPLE_VERSION = "2026-05-recommendation-training-example-v2"
FORWARD_HORIZONS = ["5d", "1m", "3m", "6m", "12m"]
MIN_LONG_HORIZON_LEARNING_LABELS = 20
MIN_CALIBRATION_SAMPLES = 20


def build_training_examples(
    as_of: date,
    session: str,
    approval_tickets: list[dict[str, Any]],
    research_book: dict[str, Any],
    feature_matrix: dict[str, Any],
) -> list[dict[str, Any]]:
    research_by_symbol = {
        str(item.get("symbol") or "").upper(): item
        for item in research_book.get("items", [])
    }
    feature_by_symbol = {
        str(row.get("symbol") or "").upper(): row
        for row in feature_matrix.get("rows", [])
    }
    examples = []
    for ticket in approval_tickets:
        symbol = str(ticket.get("symbol") or "").upper()
        if not symbol:
            continue
        research = research_by_symbol.get(symbol, {})
        feature = feature_by_symbol.get(symbol, {})
        examples.append(
            {
                "example_id": stable_id([as_of.isoformat(), session, symbol, ticket.get("ticket_id"), TRAINING_EXAMPLE_VERSION]),
                "version": TRAINING_EXAMPLE_VERSION,
                "model_policy_version": ticket.get("model_policy_version") or research.get("model_policy_version") or MODEL_POLICY_VERSION,
                "ticket_id": ticket.get("ticket_id", ""),
                "as_of": as_of.isoformat(),
                "session": session,
                "symbol": symbol,
                "bucket": ticket.get("bucket") or research.get("bucket") or feature.get("bucket") or "unmapped",
                "trade_action": ticket.get("trade_action", "study"),
                "current_weight": round(float(ticket.get("current_weight") or 0), 6),
                "recommended_delta_weight": round(float(ticket.get("recommended_delta_weight") or 0), 6),
                "target_weight": round(float(ticket.get("target_weight") or 0), 6),
                "post_action_weight": round(float(ticket.get("post_action_weight", ticket.get("target_weight") or 0) or 0), 6),
                "trade_target_weight": round(float(ticket.get("trade_target_weight", ticket.get("post_action_weight", ticket.get("target_weight") or 0)) or 0), 6),
                "model_target_weight": round(float(ticket.get("model_target_weight", ticket.get("target_weight") or 0) or 0), 6),
                "risk_adjusted_expected_return": research.get("risk_adjusted_expected_return"),
                "probability_weighted_return": research.get("probability_weighted_return"),
                "evidence_quality": research.get("evidence_quality"),
                "drawdown_risk": research.get("drawdown_risk"),
                "timing_score": research.get("timing_score"),
                "company_underwriting_score": research.get("company_underwriting_score", feature.get("company_underwriting_score")),
                "sector_setup_score": research.get("sector_setup_score", feature.get("sector_setup_score")),
                "company_add_eligible": research.get("company_add_eligible", feature.get("company_add_eligible")),
                "company_trim_signal": research.get("company_trim_signal", feature.get("company_trim_signal")),
                "decision_stack": research.get("decision_stack", {}),
                "signal_families": feature.get("signal_families") or research.get("signal_families") or [],
                "event_types": feature.get("event_types") or research.get("event_types") or [],
                "external_signal_score": feature.get("external_signal_score"),
                "coverage_adjusted_external_signal_score": feature.get("coverage_adjusted_external_signal_score"),
                "external_coverage_multiplier": feature.get("external_coverage_multiplier"),
                "external_feed_status": feature.get("external_feed_status"),
                "external_provider_count": feature.get("external_provider_count"),
                "external_provider_ok_count": feature.get("external_provider_ok_count"),
                "external_provider_ok_ratio": feature.get("external_provider_ok_ratio"),
                "external_signal_count": feature.get("external_signal_count"),
                "external_source_count": feature.get("external_source_count"),
                "forward_return_labels": {horizon: None for horizon in FORWARD_HORIZONS},
                "label_status": "pending_forward_returns",
            }
        )
    return examples


def build_outcome_diagnostics(
    as_of: date,
    training_examples: list[dict[str, Any]],
    outcome_history: list[dict[str, Any]] | None = None,
    backtest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    history = outcome_history or []
    completed = [row for row in history if row.get("forward_return_pct") is not None]
    pending_count = int((backtest or {}).get("pending_outcome_count") or 0)
    missing_count = int((backtest or {}).get("missing_price_count") or 0)
    if not backtest:
        pending_count = len(training_examples) * len(FORWARD_HORIZONS)
    long_horizon_completed = [row for row in completed if row.get("horizon") != "5d"]
    short_horizon_completed = [row for row in completed if row.get("horizon") == "5d"]
    schedule = pending_label_schedule(backtest, as_of)
    maturity = label_maturity(long_horizon_completed, short_horizon_completed, pending_count, missing_count)
    return {
        "version": OUTCOME_VERSION,
        "model_policy_version": MODEL_POLICY_VERSION,
        "as_of": as_of.isoformat(),
        "horizons": FORWARD_HORIZONS,
        "current_training_example_count": len(training_examples),
        "total_outcome_count": int((backtest or {}).get("outcome_count") or len(completed) + pending_count + missing_count),
        "completed_outcome_count": len(completed),
        "pending_outcome_count": pending_count,
        "missing_price_count": missing_count,
        "horizon_label_counts": horizon_label_counts(backtest, training_examples, completed),
        "pending_label_schedule": schedule,
        "label_maturity": maturity,
        "learning_readiness_projection": learning_readiness_projection(maturity, schedule),
        "external_learning_readiness_projection": external_learning_readiness_projection(backtest, as_of),
        "status": "tracking" if completed else "awaiting_forward_returns",
        "hit_rate": hit_rate(completed),
        "average_forward_return": average_forward_return(completed),
        "by_signal_family": group_forward_returns(completed, "signal_families"),
        "by_trade_action": group_forward_returns(completed, "trade_action"),
        "by_bucket": group_forward_returns(completed, "bucket"),
        "calibration": calibration(completed),
    }


def horizon_label_counts(backtest: dict[str, Any] | None, training_examples: list[dict[str, Any]], completed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    backtest_horizons = (backtest or {}).get("horizons") or []
    if backtest_horizons:
        return [
            {
                "horizon": row.get("horizon"),
                "completed_count": int(row.get("completed_count") or 0),
                "pending_count": int(row.get("pending_count") or 0),
                "missing_price_count": int(row.get("missing_price_count") or 0),
            }
            for row in backtest_horizons
            if row.get("horizon")
        ]
    return [
        {
            "horizon": horizon,
            "completed_count": sum(1 for row in completed if row.get("horizon") == horizon),
            "pending_count": len(training_examples),
            "missing_price_count": 0,
        }
        for horizon in FORWARD_HORIZONS
    ]


def pending_label_schedule(backtest: dict[str, Any] | None, as_of: date) -> dict[str, Any]:
    rows = [
        row for row in (backtest or {}).get("outcomes") or []
        if row.get("status") == "pending" and row.get("due_date")
    ]
    if not rows:
        rows = [
            row for row in (backtest or {}).get("recent_pending") or []
            if row.get("status") == "pending" and row.get("due_date")
        ]
    learning_rows = [row for row in rows if row.get("horizon") != "5d"]
    next_any = next_pending_label(rows, as_of)
    next_learning = next_pending_label(learning_rows, as_of)
    return {
        "pending_label_count": len(rows),
        "pending_learning_label_count": len(learning_rows),
        "overdue_label_count": overdue_label_count(rows, as_of),
        "overdue_learning_label_count": overdue_label_count(learning_rows, as_of),
        "due_window_counts": due_window_counts(rows, as_of),
        "learning_due_window_counts": due_window_counts(learning_rows, as_of),
        "learning_due_dates": pending_due_dates(learning_rows, as_of),
        "next_label": next_any,
        "next_learning_label": next_learning,
        "next_label_due_date": next_any.get("due_date") if next_any else None,
        "next_learning_label_due_date": next_learning.get("due_date") if next_learning else None,
    }


def next_pending_label(rows: list[dict[str, Any]], as_of: date) -> dict[str, Any]:
    if not rows:
        return {}
    row = sorted(rows, key=lambda item: (str(item.get("due_date") or ""), str(item.get("horizon") or ""), str(item.get("symbol") or "")))[0]
    due_date = row.get("due_date")
    return {
        "due_date": due_date,
        "days_until_due": days_until_due(due_date, as_of),
        "horizon": row.get("horizon"),
        "symbol": row.get("symbol"),
        "due_count": sum(1 for item in rows if item.get("due_date") == due_date),
    }


def overdue_label_count(rows: list[dict[str, Any]], as_of: date) -> int:
    return sum(
        1
        for row in rows
        if (days := days_until_due(row.get("due_date"), as_of)) is not None and days < 0
    )


def due_window_counts(rows: list[dict[str, Any]], as_of: date) -> dict[str, int]:
    days = [
        value
        for row in rows
        if (value := days_until_due(row.get("due_date"), as_of)) is not None
    ]
    return {
        "overdue": sum(1 for value in days if value < 0),
        "due_today": sum(1 for value in days if value == 0),
        "due_next_7d": sum(1 for value in days if 0 <= value <= 7),
        "due_next_30d": sum(1 for value in days if 0 <= value <= 30),
    }


def pending_due_dates(rows: list[dict[str, Any]], as_of: date) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for row in rows:
        due_date = row.get("due_date")
        if due_date:
            key = str(due_date)[:10]
            counts[key] = counts.get(key, 0) + 1
    cumulative = 0
    due_dates = []
    for due_date in sorted(counts):
        cumulative += counts[due_date]
        due_dates.append(
            {
                "due_date": due_date,
                "days_until_due": days_until_due(due_date, as_of),
                "due_count": counts[due_date],
                "cumulative_due_count": cumulative,
            }
        )
    return due_dates


def days_until_due(due_date: Any, as_of: date) -> int | None:
    try:
        parsed = date.fromisoformat(str(due_date)[:10])
    except (TypeError, ValueError):
        return None
    return (parsed - as_of).days


def label_maturity(
    long_horizon_completed: list[dict[str, Any]],
    short_horizon_completed: list[dict[str, Any]],
    pending_count: int,
    missing_count: int,
) -> dict[str, Any]:
    completed_count = len(long_horizon_completed)
    needed = max(0, MIN_LONG_HORIZON_LEARNING_LABELS - completed_count)
    return {
        "learning_ready": completed_count >= MIN_LONG_HORIZON_LEARNING_LABELS,
        "completed_long_horizon_count": completed_count,
        "short_horizon_completed_count": len(short_horizon_completed),
        "minimum_long_horizon_required": MIN_LONG_HORIZON_LEARNING_LABELS,
        "additional_long_horizon_needed": needed,
        "pending_outcome_count": pending_count,
        "missing_price_count": missing_count,
        "message": (
            "Enough completed 1-12 month labels are available for learning."
            if needed == 0
            else f"Need {needed} more completed 1-12 month labels before learning can adjust ranking weights."
        ),
    }


def learning_readiness_projection(maturity: dict[str, Any], schedule: dict[str, Any]) -> dict[str, Any]:
    completed = int(maturity.get("completed_long_horizon_count") or 0)
    required = int(maturity.get("minimum_long_horizon_required") or MIN_LONG_HORIZON_LEARNING_LABELS)
    learning_windows = schedule.get("learning_due_window_counts") or {}
    due_next_7d = int(learning_windows.get("due_next_7d") or 0)
    due_next_30d = int(learning_windows.get("due_next_30d") or 0)
    next_learning = schedule.get("next_learning_label") or {}
    next_due_count = int(next_learning.get("due_count") or 0)
    projected_7d = completed + due_next_7d
    projected_30d = completed + due_next_30d
    projected_next_due = completed + next_due_count
    readiness_date = estimated_learning_ready_date(completed, required, schedule.get("learning_due_dates") or [])
    return {
        "completed_long_horizon_count": completed,
        "minimum_long_horizon_required": required,
        "pending_learning_labels_needed_for_readiness": max(0, required - completed),
        "learning_labels_due_next_7d": due_next_7d,
        "learning_labels_due_next_30d": due_next_30d,
        "next_learning_label_due_date": next_learning.get("due_date"),
        "next_learning_label_days_until_due": next_learning.get("days_until_due"),
        "next_learning_label_due_count": next_due_count,
        "projected_long_horizon_count_7d": projected_7d,
        "projected_long_horizon_count_30d": projected_30d,
        "projected_long_horizon_count_next_learning_label": projected_next_due,
        "projected_additional_needed_7d": max(0, required - projected_7d),
        "projected_additional_needed_30d": max(0, required - projected_30d),
        "projected_additional_needed_next_learning_label": max(0, required - projected_next_due),
        "learning_ready_after_30d_due_window": projected_30d >= required,
        "learning_ready_after_next_learning_label": projected_next_due >= required,
        "estimated_learning_ready_date": readiness_date.get("due_date"),
        "estimated_learning_ready_days_until_due": readiness_date.get("days_until_due"),
        "estimated_learning_ready_cumulative_due_count": readiness_date.get("cumulative_due_count", 0),
        "estimated_learning_ready_projected_count": completed + int(readiness_date.get("cumulative_due_count") or 0),
        "learning_ready_with_scheduled_pending_labels": bool(readiness_date) or completed >= required,
    }


def external_learning_readiness_projection(backtest: dict[str, Any] | None, as_of: date) -> dict[str, Any]:
    rows = [row for row in (backtest or {}).get("outcomes") or [] if isinstance(row, dict)]
    long_external_completed = [
        row for row in rows
        if row.get("status") == "complete" and row.get("horizon") != "5d" and has_external_observation(row)
    ]
    long_external_pending = [
        row for row in rows
        if row.get("status") == "pending"
        and row.get("horizon") != "5d"
        and row.get("due_date")
        and has_external_observation(row)
    ]
    short_external_completed = [
        row for row in rows
        if row.get("status") == "complete" and row.get("horizon") == "5d" and has_external_observation(row)
    ]
    short_external_pending = [
        row for row in rows
        if row.get("status") == "pending"
        and row.get("horizon") == "5d"
        and row.get("due_date")
        and has_external_observation(row)
    ]
    if not long_external_completed and not long_external_pending and not short_external_completed and not short_external_pending:
        return {}
    completed = len(long_external_completed)
    required = MIN_CALIBRATION_SAMPLES
    schedule = pending_label_schedule({"outcomes": long_external_pending}, as_of)
    fast_schedule = pending_label_schedule({"outcomes": short_external_pending}, as_of)
    learning_windows = schedule.get("learning_due_window_counts") or {}
    fast_windows = fast_schedule.get("due_window_counts") or {}
    due_next_7d = int(learning_windows.get("due_next_7d") or 0)
    due_next_30d = int(learning_windows.get("due_next_30d") or 0)
    next_external = schedule.get("next_learning_label") or {}
    next_fast = fast_schedule.get("next_label") or {}
    next_due_count = int(next_external.get("due_count") or 0)
    scheduled_count = int(schedule.get("pending_learning_label_count") or 0)
    readiness_date = estimated_learning_ready_date(completed, required, schedule.get("learning_due_dates") or [])
    return {
        "completed_external_long_horizon_count": completed,
        "completed_external_short_horizon_count": len(short_external_completed),
        "minimum_external_long_horizon_required": required,
        "pending_external_learning_label_count": scheduled_count,
        "pending_external_fast_label_count": int(fast_schedule.get("pending_label_count") or 0),
        "pending_external_learning_labels_needed_for_readiness": max(0, required - completed),
        "external_learning_labels_due_next_7d": due_next_7d,
        "external_learning_labels_due_next_30d": due_next_30d,
        "external_fast_labels_due_next_7d": int(fast_windows.get("due_next_7d") or 0),
        "external_fast_labels_due_next_30d": int(fast_windows.get("due_next_30d") or 0),
        "next_external_learning_label_due_date": next_external.get("due_date"),
        "next_external_learning_label_days_until_due": next_external.get("days_until_due"),
        "next_external_learning_label_due_count": next_due_count,
        "next_external_fast_label_due_date": next_fast.get("due_date"),
        "next_external_fast_label_days_until_due": next_fast.get("days_until_due"),
        "next_external_fast_label_due_count": int(next_fast.get("due_count") or 0),
        "projected_external_long_horizon_count_30d": completed + due_next_30d,
        "projected_external_long_horizon_count_next_learning_label": completed + next_due_count,
        "projected_external_long_horizon_count_all_scheduled": completed + scheduled_count,
        "projected_external_additional_needed_30d": max(0, required - completed - due_next_30d),
        "projected_external_additional_needed_next_learning_label": max(0, required - completed - next_due_count),
        "projected_external_additional_needed_all_scheduled": max(0, required - completed - scheduled_count),
        "estimated_external_learning_ready_date": readiness_date.get("due_date"),
        "estimated_external_learning_ready_days_until_due": readiness_date.get("days_until_due"),
        "estimated_external_learning_ready_cumulative_due_count": readiness_date.get("cumulative_due_count", 0),
        "estimated_external_learning_ready_projected_count": completed + int(readiness_date.get("cumulative_due_count") or 0),
        "external_learning_ready_with_scheduled_pending_labels": bool(readiness_date) or completed >= required,
    }


def has_external_observation(row: dict[str, Any]) -> bool:
    feed_status = str(row.get("external_feed_status") or "").strip().lower()
    if feed_status and feed_status != "unknown":
        return True
    for key in ("external_coverage_multiplier", "external_provider_count", "external_signal_count", "external_source_count"):
        if row.get(key) is not None:
            return True
    return False


def estimated_learning_ready_date(completed: int, required: int, learning_due_dates: list[dict[str, Any]]) -> dict[str, Any]:
    if completed >= required:
        return {}
    for due_date in learning_due_dates:
        if completed + int(due_date.get("cumulative_due_count") or 0) >= required:
            return due_date
    return {}


def hit_rate(rows: list[dict[str, Any]]) -> float | None:
    if not rows:
        return None
    winners = [row for row in rows if float(row.get("forward_return_pct") or 0) > 0]
    return round(len(winners) / len(rows), 4)


def average_forward_return(rows: list[dict[str, Any]]) -> float | None:
    if not rows:
        return None
    return round(mean(float(row.get("forward_return_pct") or 0) for row in rows), 2)


def group_forward_returns(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[float]] = {}
    for row in rows:
        values = row.get(key) if key == "signal_families" else [row.get(key)]
        for value in values or []:
            label = str(value or "unknown")
            groups.setdefault(label, []).append(float(row.get("forward_return_pct") or 0))
    return [
        {
            "key": label,
            "count": len(values),
            "average_forward_return": round(mean(values), 2),
            "hit_rate": round(sum(1 for value in values if value > 0) / len(values), 4),
        }
        for label, values in sorted(groups.items())
        if values
    ]


def calibration(rows: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [
        row for row in rows
        if row.get("risk_adjusted_expected_return") is not None and row.get("forward_return_pct") is not None
    ]
    if not usable:
        return {
            "status": "insufficient_data",
            "mean_error": None,
            "mean_absolute_error": None,
            "underprediction_count": 0,
            "overprediction_count": 0,
            "sample_count": 0,
            "minimum_calibration_samples": MIN_CALIBRATION_SAMPLES,
            "additional_samples_needed": MIN_CALIBRATION_SAMPLES,
            "calibration_ready": False,
            "message": "Forward-return labels are pending; calibration starts once outcomes mature.",
        }
    errors = [
        float(row.get("forward_return_pct") or 0) - float(row.get("risk_adjusted_expected_return") or 0)
        for row in usable
    ]
    return {
        "status": "available",
        "mean_error": round(mean(errors), 2),
        "mean_absolute_error": round(mean(abs(error) for error in errors), 2),
        "underprediction_count": sum(1 for error in errors if error > 0),
        "overprediction_count": sum(1 for error in errors if error < 0),
        "sample_count": len(usable),
        "minimum_calibration_samples": MIN_CALIBRATION_SAMPLES,
        "additional_samples_needed": max(0, MIN_CALIBRATION_SAMPLES - len(usable)),
        "calibration_ready": len(usable) >= MIN_CALIBRATION_SAMPLES,
    }
