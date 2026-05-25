from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import mean
from typing import Any, Callable

from .features import MODEL_POLICY_VERSION
from .market import fetch_chart_history
from .outcomes import FORWARD_HORIZONS
from .symbols import proxied_lookup, proxy_index
from .util import stable_id


BACKTEST_VERSION = "2026-05-recommendation-backtest-v1"
HORIZON_TRADING_DAYS = {"5d": 5, "1m": 21, "3m": 63, "6m": 126, "12m": 252}
HORIZON_CALENDAR_DAYS = {"5d": 7, "1m": 31, "3m": 92, "6m": 183, "12m": 366}
ACTION_DIRECTION = {
    "add": 1,
    "buy_more": 1,
    "raise_target": 1,
    "starter": 1,
    "trim": -1,
    "cut_target": -1,
    "sell": -1,
}


@dataclass(frozen=True)
class RecommendationTrial:
    trial_id: str
    as_of: date
    session: str
    symbol: str
    bucket: str
    trade_action: str
    direction: int
    current_weight: float
    recommended_delta_weight: float
    target_weight: float
    risk_adjusted_expected_return: float | None
    evidence_quality: float | None
    drawdown_risk: float | None
    timing_score: float | None
    signal_families: tuple[str, ...]
    event_types: tuple[str, ...]
    model_policy_version: str


def build_backtest_summary(
    reports_dir: Path,
    *,
    as_of: date | None = None,
    price_history: dict[str, list[dict[str, Any]]] | None = None,
    price_fetcher: Callable[[str], list[dict[str, Any]]] | None = None,
    include_current_examples: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build immutable recommendation-outcome labels from saved daily reports.

    Reports are the source of recommendations; price history is the source of
    realized forward returns. Current examples can be included as pending rows
    before today's report has been written to disk.
    """

    today = as_of or date.today()
    snapshots = load_report_payloads(reports_dir)
    trials = recommendation_trials_from_payloads(snapshots)
    if include_current_examples:
        trials.extend(trials_from_training_examples(include_current_examples))
    trials = dedupe_trials(trials)
    histories = price_history if price_history is not None else fetch_histories(
        sorted({trial.symbol for trial in trials}),
        price_fetcher or (lambda symbol: fetch_chart_history(symbol, range_="5y")),
    )
    outcomes = [
        outcome_row(trial, horizon, histories.get(trial.symbol, []), today)
        for trial in trials
        for horizon in FORWARD_HORIZONS
    ]
    completed = [row for row in outcomes if row.get("status") == "complete"]
    pending = [row for row in outcomes if row.get("status") == "pending"]
    missing = [row for row in outcomes if row.get("status") == "missing_price"]
    return {
        "version": BACKTEST_VERSION,
        "model_policy_version": MODEL_POLICY_VERSION,
        "as_of": today.isoformat(),
        "source_report_count": len(snapshots),
        "trial_count": len(trials),
        "outcome_count": len(outcomes),
        "completed_outcome_count": len(completed),
        "pending_outcome_count": len(pending),
        "missing_price_count": len(missing),
        "status": "tracking" if completed else "awaiting_matured_outcomes",
        "horizons": horizon_summaries(outcomes),
        "by_signal_family": group_summaries(completed, "signal_families"),
        "by_bucket": group_summaries(completed, "bucket"),
        "by_trade_action": group_summaries(completed, "trade_action"),
        "calibration": calibration_summary(completed),
        "confidence_curve": confidence_curve(completed),
        "top_wins": top_outcomes(completed, reverse=True),
        "top_losses": top_outcomes(completed, reverse=False),
        "recent_pending": recent_pending(pending),
        "outcomes": outcomes,
    }


def outcome_history_from_backtest(backtest: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in backtest.get("outcomes", []):
        if row.get("status") != "complete":
            continue
        rows.append(
            {
                "outcome_id": row.get("outcome_id"),
                "ticket_id": row.get("trial_id", ""),
                "symbol": row.get("symbol"),
                "horizon": row.get("horizon"),
                "as_of": row.get("as_of"),
                "forward_return_pct": row.get("decision_forward_return_pct"),
                "raw_forward_return_pct": row.get("raw_forward_return_pct"),
                "expected_return_score": row.get("risk_adjusted_expected_return"),
                "risk_adjusted_expected_return": row.get("risk_adjusted_expected_return"),
                "signal_families": row.get("signal_families") or [],
                "bucket": row.get("bucket"),
                "trade_action": row.get("trade_action"),
            }
        )
    return rows


def load_report_payloads(reports_dir: Path) -> list[dict[str, Any]]:
    payloads = []
    for path in sorted(reports_dir.glob("*.json"), key=lambda item: (item.stat().st_mtime, item.name)):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if payload.get("as_of"):
            payloads.append(payload)
    return payloads


def recommendation_trials_from_payloads(payloads: list[dict[str, Any]]) -> list[RecommendationTrial]:
    trials: list[RecommendationTrial] = []
    for payload in payloads:
        examples = payload.get("recommendation_training_examples") or []
        if examples:
            trials.extend(trials_from_training_examples(examples))
            continue
        trials.extend(trials_from_payload_actions(payload))
    return trials


def trials_from_training_examples(examples: list[dict[str, Any]]) -> list[RecommendationTrial]:
    trials = []
    for example in examples:
        symbol = str(example.get("symbol") or "").upper()
        as_of = parse_date(example.get("as_of"))
        if not symbol or not as_of:
            continue
        delta = float(example.get("recommended_delta_weight") or 0)
        action = str(example.get("trade_action") or "hold")
        trials.append(
            RecommendationTrial(
                trial_id=str(example.get("example_id") or stable_id([as_of.isoformat(), symbol, action, delta])),
                as_of=as_of,
                session=str(example.get("session") or ""),
                symbol=symbol,
                bucket=str(example.get("bucket") or "unmapped"),
                trade_action=action,
                direction=direction_for(action, delta),
                current_weight=float(example.get("current_weight") or 0),
                recommended_delta_weight=delta,
                target_weight=float(example.get("target_weight") or 0),
                risk_adjusted_expected_return=optional_float(example.get("risk_adjusted_expected_return")),
                evidence_quality=optional_float(example.get("evidence_quality")),
                drawdown_risk=optional_float(example.get("drawdown_risk")),
                timing_score=optional_float(example.get("timing_score")),
                signal_families=tuple(str(item) for item in example.get("signal_families") or []),
                event_types=tuple(str(item) for item in example.get("event_types") or []),
                model_policy_version=str(example.get("model_policy_version") or MODEL_POLICY_VERSION),
            )
        )
    return trials


def trials_from_payload_actions(payload: dict[str, Any]) -> list[RecommendationTrial]:
    card_by_symbol = proxy_index(payload.get("decision_cards") or [])
    research_by_symbol = {
        str(row.get("symbol") or "").upper(): row
        for row in (payload.get("research_book") or {}).get("items", [])
    }
    feature_by_symbol = {
        str(row.get("symbol") or "").upper(): row
        for row in (payload.get("feature_matrix") or {}).get("rows", [])
    }
    actions = payload.get("approval_tickets") or (payload.get("portfolio_benchmark") or {}).get("action_queue", [])
    trials = []
    as_of = parse_date(payload.get("as_of"))
    if not as_of:
        return trials
    for action in actions:
        symbol = str(action.get("symbol") or "").upper()
        if not symbol:
            continue
        research = research_by_symbol.get(symbol, {})
        feature = feature_by_symbol.get(symbol, {})
        card = proxied_lookup(card_by_symbol, symbol, {})
        delta = float(action.get("recommended_delta_weight") or 0)
        trade_action = str(action.get("trade_action") or action.get("verdict") or legacy_action_label(action))
        current_weight = optional_float(action.get("current_weight", action.get("portfolio_weight"))) or 0.0
        target_weight = optional_float(action.get("target_weight", action.get("model_target_weight")))
        if target_weight is None:
            target_weight = current_weight + delta
        trials.append(
            RecommendationTrial(
                trial_id=str(action.get("ticket_id") or stable_id([as_of.isoformat(), payload.get("session"), symbol, trade_action, delta])),
                as_of=as_of,
                session=str(payload.get("session") or ""),
                symbol=symbol,
                bucket=str(action.get("bucket") or research.get("bucket") or feature.get("bucket") or card.get("bucket") or "unmapped"),
                trade_action=trade_action,
                direction=direction_for(trade_action, delta),
                current_weight=current_weight,
                recommended_delta_weight=delta,
                target_weight=target_weight,
                risk_adjusted_expected_return=optional_float(action.get("risk_adjusted_expected_return", research.get("risk_adjusted_expected_return"))),
                evidence_quality=optional_float(action.get("evidence_quality", research.get("evidence_quality", feature.get("evidence_quality")))),
                drawdown_risk=optional_float(action.get("drawdown_risk", research.get("drawdown_risk", feature.get("drawdown_risk")))),
                timing_score=optional_float(action.get("timing_score", research.get("timing_score", feature.get("timing_score")))),
                signal_families=tuple(str(item) for item in feature.get("signal_families") or research.get("signal_families") or card.get("signal_families") or []),
                event_types=tuple(str(item) for item in feature.get("event_types") or research.get("event_types") or action.get("event_types") or card.get("top_event_types") or []),
                model_policy_version=str(action.get("model_policy_version") or research.get("model_policy_version") or MODEL_POLICY_VERSION),
            )
        )
    return trials


def legacy_action_label(action: dict[str, Any]) -> str:
    text = " ".join(
        str(action.get(key) or "")
        for key in ["trade_action", "verdict", "action", "why"]
    ).lower()
    if any(token in text for token in ["re-underwrite", "risk review", "hedge need", "risk catalyst"]):
        return "risk_review"
    if any(token in text for token in ["trim", "reduce", "decrease", "sell"]):
        return "trim"
    if any(token in text for token in ["add", "buy", "starter", "initiate"]):
        return "add"
    if "watch" in text:
        return "watch"
    if any(token in text for token in ["study", "underwrite", "research"]):
        return "study"
    if "hold" in text:
        return "hold"
    return "hold"


def dedupe_trials(trials: list[RecommendationTrial]) -> list[RecommendationTrial]:
    seen: set[tuple[str, str, str, str]] = set()
    unique = []
    for trial in sorted(trials, key=lambda item: (item.as_of, item.session, item.symbol, item.trial_id)):
        key = (trial.as_of.isoformat(), trial.session, trial.symbol, trial.trade_action)
        if key in seen:
            continue
        seen.add(key)
        unique.append(trial)
    return unique


def fetch_histories(symbols: list[str], fetcher: Callable[[str], list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    histories = {}
    for symbol in symbols:
        history = normalize_history(fetcher(symbol))
        if history:
            histories[symbol] = history
    return histories


def outcome_row(
    trial: RecommendationTrial,
    horizon: str,
    history: list[dict[str, Any]],
    today: date,
) -> dict[str, Any]:
    due_date = trial.as_of + timedelta(days=HORIZON_CALENDAR_DAYS[horizon])
    base = {
        "outcome_id": stable_id([trial.trial_id, horizon, BACKTEST_VERSION]),
        "trial_id": trial.trial_id,
        "version": BACKTEST_VERSION,
        "model_policy_version": trial.model_policy_version,
        "as_of": trial.as_of.isoformat(),
        "session": trial.session,
        "symbol": trial.symbol,
        "bucket": trial.bucket,
        "trade_action": trial.trade_action,
        "direction": trial.direction,
        "horizon": horizon,
        "due_date": due_date.isoformat(),
        "current_weight": round(trial.current_weight, 6),
        "recommended_delta_weight": round(trial.recommended_delta_weight, 6),
        "target_weight": round(trial.target_weight, 6),
        "risk_adjusted_expected_return": trial.risk_adjusted_expected_return,
        "evidence_quality": trial.evidence_quality,
        "drawdown_risk": trial.drawdown_risk,
        "timing_score": trial.timing_score,
        "signal_families": list(trial.signal_families),
        "event_types": list(trial.event_types),
    }
    if due_date > today:
        return {**base, "status": "pending", "message": "Forward horizon has not matured yet."}
    rows = normalize_history(history)
    entry = price_on_or_after(rows, trial.as_of)
    if not entry:
        return {**base, "status": "missing_price", "message": "No entry price available."}
    target_index = rows.index(entry) + HORIZON_TRADING_DAYS[horizon]
    if target_index >= len(rows):
        return {**base, "status": "pending", "entry_date": entry["date"].isoformat(), "entry_price": float(entry["close"]), "message": "Price history has not reached the horizon yet."}
    exit_row = rows[target_index]
    raw_return = pct_return(float(entry["close"]), float(exit_row["close"]))
    decision_return = raw_return * trial.direction if trial.direction else raw_return
    expected = trial.risk_adjusted_expected_return
    return {
        **base,
        "status": "complete",
        "entry_date": entry["date"].isoformat(),
        "exit_date": exit_row["date"].isoformat(),
        "entry_price": round(float(entry["close"]), 4),
        "exit_price": round(float(exit_row["close"]), 4),
        "raw_forward_return_pct": round(raw_return, 2),
        "decision_forward_return_pct": round(decision_return, 2),
        "weighted_decision_return": round(decision_return * abs(trial.recommended_delta_weight), 4),
        "hit": decision_return > 0,
        "expected_vs_realized_error": round(decision_return - expected, 2) if expected is not None else None,
    }


def horizon_summaries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [summary_for_rows(horizon, [row for row in rows if row.get("horizon") == horizon]) for horizon in FORWARD_HORIZONS]


def summary_for_rows(label: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [row for row in rows if row.get("status") == "complete"]
    pending = [row for row in rows if row.get("status") == "pending"]
    missing = [row for row in rows if row.get("status") == "missing_price"]
    returns = [float(row.get("decision_forward_return_pct") or 0) for row in completed]
    errors = [float(row.get("expected_vs_realized_error") or 0) for row in completed if row.get("expected_vs_realized_error") is not None]
    return {
        "horizon": label,
        "trial_count": len(rows),
        "completed_count": len(completed),
        "pending_count": len(pending),
        "missing_price_count": len(missing),
        "hit_rate": round(sum(1 for row in completed if row.get("hit")) / len(completed), 4) if completed else None,
        "average_decision_return": round(mean(returns), 2) if returns else None,
        "median_like_decision_return": median_like(returns),
        "average_expected_return": avg(row.get("risk_adjusted_expected_return") for row in completed),
        "mean_error": round(mean(errors), 2) if errors else None,
    }


def group_summaries(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        values = row.get(key) if key == "signal_families" else [row.get(key)]
        for value in values or ["unknown"]:
            grouped[str(value or "unknown")].append(row)
    return sorted(
        [summary_for_group(label, group) for label, group in grouped.items()],
        key=lambda row: (row["completed_count"], row.get("average_decision_return") or -999),
        reverse=True,
    )


def summary_for_group(label: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    returns = [float(row.get("decision_forward_return_pct") or 0) for row in rows]
    return {
        "key": label,
        "completed_count": len(rows),
        "hit_rate": round(sum(1 for row in rows if row.get("hit")) / len(rows), 4) if rows else None,
        "average_decision_return": round(mean(returns), 2) if returns else None,
        "average_expected_return": avg(row.get("risk_adjusted_expected_return") for row in rows),
    }


def calibration_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [row for row in rows if row.get("risk_adjusted_expected_return") is not None and row.get("decision_forward_return_pct") is not None]
    if not usable:
        return {
            "status": "insufficient_data",
            "message": "No matured expected-vs-realized labels yet.",
            "sample_count": 0,
            "mean_error": None,
            "buckets": [],
        }
    buckets = [
        ("low_expected", -999.0, 10.0),
        ("mid_expected", 10.0, 25.0),
        ("high_expected", 25.0, 999.0),
    ]
    bucket_rows = []
    for label, low, high in buckets:
        members = [row for row in usable if low <= float(row.get("risk_adjusted_expected_return") or 0) < high]
        if members:
            bucket_rows.append(summary_for_group(label, members))
    errors = [float(row.get("expected_vs_realized_error") or 0) for row in usable]
    return {
        "status": "available",
        "sample_count": len(usable),
        "mean_error": round(mean(errors), 2),
        "buckets": bucket_rows,
        "message": "Positive mean error means realized decision returns beat expected returns.",
    }


def confidence_curve(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bands = [("low", 0, 50), ("medium", 50, 75), ("high", 75, 101)]
    curve = []
    for label, low, high in bands:
        members = [row for row in rows if low <= float(row.get("evidence_quality") or 0) < high]
        if members:
            curve.append(summary_for_group(label, members))
    return curve


def top_outcomes(rows: list[dict[str, Any]], *, reverse: bool) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: float(row.get("decision_forward_return_pct") or 0), reverse=reverse)
    return [compact_outcome(row) for row in ordered[:8]]


def recent_pending(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: (row.get("due_date") or "", row.get("as_of") or ""), reverse=False)
    return [compact_outcome(row) for row in ordered[:10]]


def compact_outcome(row: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "symbol",
        "horizon",
        "status",
        "as_of",
        "due_date",
        "trade_action",
        "bucket",
        "decision_forward_return_pct",
        "raw_forward_return_pct",
        "risk_adjusted_expected_return",
        "expected_vs_realized_error",
    ]
    return {key: row.get(key) for key in keys if key in row}


def normalize_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in history or []:
        row_date = parse_date(row.get("date"))
        close = optional_float(row.get("close"))
        if row_date and close is not None and close > 0:
            rows.append({"date": row_date, "close": close})
    return sorted(rows, key=lambda row: row["date"])


def price_on_or_after(rows: list[dict[str, Any]], target: date) -> dict[str, Any] | None:
    for row in rows:
        if row["date"] >= target:
            return row
    return None


def pct_return(entry: float, exit_price: float) -> float:
    if entry == 0:
        return 0.0
    return ((exit_price - entry) / entry) * 100.0


def direction_for(action: str, delta: float) -> int:
    clean = str(action or "").lower()
    if delta > 0:
        return 1
    if delta < 0:
        return -1
    for key, direction in ACTION_DIRECTION.items():
        if key in clean:
            return direction
    return 0


def parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def avg(values) -> float | None:
    usable = [float(value) for value in values if value is not None]
    return round(mean(usable), 2) if usable else None


def median_like(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[len(ordered) // 2], 2)


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
        "message": "This is a filing-change diagnostic, not a return backtest; use `invest backtest run` for recommendation outcomes.",
    }


def values_for_quarter(rows, quarter: str) -> dict[str, Decimal]:
    values: dict[str, Decimal] = {}
    for row in rows:
        if row["report_date"] != quarter:
            continue
        values[row["symbol"]] = values.get(row["symbol"], Decimal("0")) + Decimal(str(row["value_usd"] or 0))
    return values
