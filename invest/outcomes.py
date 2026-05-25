from __future__ import annotations

from datetime import date
from statistics import mean
from typing import Any

from .features import MODEL_POLICY_VERSION
from .util import stable_id


OUTCOME_VERSION = "2026-05-outcome-diagnostics-v1"
TRAINING_EXAMPLE_VERSION = "2026-05-recommendation-training-example-v1"
FORWARD_HORIZONS = ["5d", "1m", "3m", "6m", "12m"]


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
                "forward_return_labels": {horizon: None for horizon in FORWARD_HORIZONS},
                "label_status": "pending_forward_returns",
            }
        )
    return examples


def build_outcome_diagnostics(
    as_of: date,
    training_examples: list[dict[str, Any]],
    outcome_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    history = outcome_history or []
    completed = [row for row in history if row.get("forward_return_pct") is not None]
    return {
        "version": OUTCOME_VERSION,
        "model_policy_version": MODEL_POLICY_VERSION,
        "as_of": as_of.isoformat(),
        "horizons": FORWARD_HORIZONS,
        "current_training_example_count": len(training_examples),
        "completed_outcome_count": len(completed),
        "status": "tracking" if completed else "awaiting_forward_returns",
        "hit_rate": hit_rate(completed),
        "average_forward_return": average_forward_return(completed),
        "by_signal_family": group_forward_returns(completed, "signal_families"),
        "by_trade_action": group_forward_returns(completed, "trade_action"),
        "by_bucket": group_forward_returns(completed, "bucket"),
        "calibration": calibration(completed),
    }


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
            "message": "Forward-return labels are pending; calibration starts once outcomes mature.",
        }
    errors = [
        float(row.get("forward_return_pct") or 0) - float(row.get("risk_adjusted_expected_return") or 0)
        for row in usable
    ]
    return {
        "status": "available",
        "mean_error": round(mean(errors), 2),
        "sample_count": len(usable),
    }
