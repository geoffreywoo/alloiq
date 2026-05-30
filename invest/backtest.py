from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from statistics import mean
from typing import Any, Callable

from .external_signals import external_provider_gap_rows
from .features import MODEL_POLICY_VERSION, approval_data_friction, external_provider_gap_features
from .market import fetch_chart_history
from .outcomes import FORWARD_HORIZONS, MIN_CALIBRATION_SAMPLES, approval_blocking_check_names, approval_open_check_count, pending_label_schedule
from .symbols import proxied_lookup, proxy_index
from .util import stable_id


BACKTEST_VERSION = "2026-05-recommendation-backtest-v4"
EXTERNAL_COVERAGE_GAP_VERSION = "2026-05-external-coverage-gap-v1"
EXTERNAL_COVERAGE_RESIDUAL_RANKING_VERSION = "2026-05-external-coverage-residual-ranking-v1"
EXTERNAL_ALIGNMENT_REVIEW_VERSION = "2026-05-external-alignment-review-v1"
EXTERNAL_ALIGNMENT_MEASUREMENT_PLAN_VERSION = "2026-05-external-alignment-measurement-plan-v1"
EXTERNAL_ALIGNMENT_MEASUREMENT_GAP_VERSION = "2026-05-external-alignment-measurement-gap-v1"
EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_GAP_VERSION = "2026-05-external-provider-gap-severity-observation-gap-v1"
EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_WORK_ITEM_VERSION = "2026-05-external-provider-gap-severity-observation-work-item-v1"
EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_BACKFILL_RECORD_VERSION = (
    "2026-05-external-provider-gap-severity-backfill-record-v1"
)
RESIDUAL_EXTERNAL_COVERAGE_GAP_LIMIT = 8
RESIDUAL_EXTERNAL_COVERAGE_REQUIRED_DATE_LIMIT = 8
PENDING_EXTERNAL_ALIGNMENT_REVIEW_QUEUE_LIMIT = 12
PENDING_EXTERNAL_ALIGNMENT_MEASUREMENT_GAP_QUEUE_LIMIT = 12
PENDING_EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_QUEUE_LIMIT = 12
PENDING_EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_WORK_ITEM_QUEUE_LIMIT = 12
PENDING_EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_CALIBRATION_WORK_ITEM_QUEUE_LIMIT = 8
PENDING_EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_CALIBRATION_REPORT_BATCH_QUEUE_LIMIT = 8
PENDING_EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_CALIBRATION_BACKFILL_RECORD_QUEUE_LIMIT = 8
EXTERNAL_PROVIDER_GAP_SEVERITY_COUNT_FIELDS = (
    ("configuration_required", "external_provider_configuration_gap_count"),
    ("runtime_budget", "external_provider_runtime_gap_count"),
    ("stale_or_empty", "external_provider_stale_gap_count"),
    ("transient_network", "external_provider_transient_gap_count"),
    ("other", "external_provider_other_gap_count"),
)
EXTERNAL_PROVIDER_GAP_SEVERITY_SORT = {
    "configuration_required": 5,
    "runtime_budget": 4,
    "stale_or_empty": 3,
    "transient_network": 2,
    "other": 1,
    "none": 0,
    "unknown": -1,
}
EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_FIELDS = (
    "external_provider_gap_count",
    "external_provider_configuration_gap_count",
    "external_provider_runtime_gap_count",
    "external_provider_stale_gap_count",
    "external_provider_transient_gap_count",
    "external_provider_other_gap_count",
    "external_provider_primary_gap_severity",
    "external_provider_gap_severity_score",
)
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
    base_risk_adjusted_expected_return: float | None
    evidence_quality: float | None
    base_evidence_quality: float | None
    drawdown_risk: float | None
    base_drawdown_risk: float | None
    timing_score: float | None
    signal_families: tuple[str, ...]
    event_types: tuple[str, ...]
    model_policy_version: str
    llm_signal_applied: bool = False
    llm_expected_return_delta: float | None = None
    llm_expected_return_adjustment: float | None = None
    llm_evidence_quality_delta: float | None = None
    llm_evidence_quality_adjustment: float | None = None
    llm_drawdown_risk_delta: float | None = None
    llm_drawdown_risk_adjustment: float | None = None
    llm_conviction_score: float | None = None
    llm_variant_quality_score: float | None = None
    llm_source_quality_score: float | None = None
    llm_contradiction_risk_score: float | None = None
    llm_staleness_risk_score: float | None = None
    llm_review_required: bool = False
    external_signal_score: float | None = None
    coverage_adjusted_external_signal_score: float | None = None
    external_coverage_multiplier: float | None = None
    external_feed_status: str = ""
    external_provider_count: int | None = None
    external_provider_ok_count: int | None = None
    external_provider_ok_ratio: float | None = None
    external_provider_gap_count: int | None = None
    external_provider_configuration_gap_count: int | None = None
    external_provider_transient_gap_count: int | None = None
    external_provider_stale_gap_count: int | None = None
    external_provider_runtime_gap_count: int | None = None
    external_provider_other_gap_count: int | None = None
    external_provider_primary_gap_severity: str = "none"
    external_provider_gap_severity_score: float | None = None
    external_signal_count: int | None = None
    external_source_count: int | None = None
    approval_data_friction_score: float | None = None
    approval_data_friction_bucket: str = ""
    approval_data_friction_reasons: tuple[str, ...] = ()
    approval_data_friction_penalty: float | None = None
    earnings_days_until: int | None = None
    earnings_event_date: str = ""
    earnings_event_source: str = ""
    earnings_confirmed_or_estimated: str = ""
    earnings_risk_window: str = ""
    earnings_confirmation_required: bool = False
    approval_required: bool = False
    approval_gate_status: str = ""
    approval_open_check_count: int | None = None
    approval_blocking_checks: tuple[str, ...] = ()


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
    if include_current_examples:
        snapshots = exclude_current_example_snapshots(snapshots, include_current_examples)
    report_payloads_by_name = provider_gap_severity_observation_report_payloads_by_name(snapshots)
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
    label_schedule = pending_label_schedule({"outcomes": outcomes}, today)
    alignment_review_item_count = pending_external_alignment_review_item_count(pending)
    alignment_review_queue = pending_external_alignment_review_queue(pending)
    alignment_review_due_dates = pending_external_alignment_review_due_dates(pending)
    alignment_review_acceptance_summary = pending_external_alignment_review_acceptance_summary(pending)
    measurement_gap_items = pending_external_alignment_measurement_gap_work_items(pending)
    measurement_gap_queue = pending_external_alignment_measurement_gap_queue(pending)
    provider_gap_severity_observation_gap_count = pending_external_provider_gap_severity_observation_gap_count(pending)
    provider_gap_severity_observation_gap_queue = pending_external_provider_gap_severity_observation_gap_queue(pending)
    provider_gap_severity_observation_work_items = pending_external_provider_gap_severity_observation_gap_work_items(pending)
    provider_gap_severity_observation_work_item_queue = (
        pending_external_provider_gap_severity_observation_gap_work_item_queue(pending)
    )
    provider_gap_severity_observation_hidden_calibration_work_items = (
        pending_external_provider_gap_severity_observation_gap_hidden_calibration_work_items(
            provider_gap_severity_observation_work_items,
            provider_gap_severity_observation_work_item_queue,
        )
    )
    provider_gap_severity_observation_hidden_calibration_work_items = (
        provider_gap_severity_observation_work_items_with_report_availability(
            provider_gap_severity_observation_hidden_calibration_work_items,
            reports_dir,
        )
    )
    provider_gap_severity_observation_hidden_calibration_work_items = (
        provider_gap_severity_observation_work_items_with_candidate_backfill(
            provider_gap_severity_observation_hidden_calibration_work_items,
            report_payloads_by_name,
        )
    )
    provider_gap_severity_observation_hidden_calibration_work_item_queue = (
        pending_external_provider_gap_severity_observation_gap_hidden_calibration_work_item_queue(
            provider_gap_severity_observation_hidden_calibration_work_items
        )
    )
    provider_gap_severity_observation_hidden_calibration_report_batches = (
        pending_external_provider_gap_severity_observation_gap_hidden_calibration_report_batches(
            provider_gap_severity_observation_hidden_calibration_work_items,
            report_payloads_by_name,
        )
    )
    provider_gap_severity_observation_hidden_calibration_report_batch_queue = (
        pending_external_provider_gap_severity_observation_gap_hidden_calibration_report_batch_queue(
            provider_gap_severity_observation_hidden_calibration_report_batches
        )
    )
    provider_gap_severity_observation_hidden_calibration_backfill_records = (
        pending_external_provider_gap_severity_observation_gap_hidden_calibration_backfill_records(
            provider_gap_severity_observation_hidden_calibration_work_items
        )
    )
    provider_gap_severity_observation_hidden_calibration_backfill_record_queue = (
        pending_external_provider_gap_severity_observation_gap_hidden_calibration_backfill_record_queue(
            provider_gap_severity_observation_hidden_calibration_backfill_records
        )
    )
    provider_gap_severity_observation_visible_work_item_label_count = sum(
        int(item.get("label_count") or 0) for item in provider_gap_severity_observation_work_item_queue
    )
    provider_gap_severity_observation_due_dates = (
        pending_external_provider_gap_severity_observation_gap_due_dates(
            provider_gap_severity_observation_work_items,
            provider_gap_severity_observation_work_item_queue,
            today,
        )
    )
    provider_gap_severity_observation_due_window_counts = (
        pending_external_provider_gap_severity_observation_gap_due_window_counts(
            provider_gap_severity_observation_due_dates
        )
    )
    provider_gap_severity_observation_horizon_counts = (
        pending_external_provider_gap_severity_observation_gap_horizon_counts(
            provider_gap_severity_observation_work_items,
            provider_gap_severity_observation_work_item_queue,
            today,
        )
    )
    provider_gap_severity_observation_learning_role_counts = (
        pending_external_provider_gap_severity_observation_gap_learning_role_counts(
            provider_gap_severity_observation_horizon_counts,
            today,
        )
    )
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
        "by_external_feed_status": group_summaries(completed, "external_feed_status"),
        "by_external_coverage": external_coverage_summaries(completed),
        "by_external_alignment": external_alignment_summaries(completed),
        "by_external_provider_gap_severity": group_summaries(completed, "external_provider_primary_gap_severity"),
        "by_external_provider_gap_severity_exposure": external_provider_gap_severity_exposure_summaries(completed),
        "by_approval_data_friction_bucket": group_summaries(completed, "approval_data_friction_bucket"),
        "by_earnings_event_status": group_summaries(completed, "earnings_event_status"),
        "by_earnings_risk_window": group_summaries(completed, "earnings_risk_window"),
        "by_earnings_confirmation_bucket": group_summaries(completed, "earnings_confirmation_bucket"),
        "by_approval_gate_status": group_summaries(completed, "approval_gate_status"),
        "by_approval_blocker_bucket": group_summaries(completed, "approval_blocker_bucket"),
        "by_llm_signal_applied": group_summaries(completed, "llm_signal_applied"),
        "by_llm_review_required": group_summaries(completed, "llm_review_required"),
        "by_llm_conviction_bucket": group_summaries(completed, "llm_conviction_bucket"),
        "by_llm_contradiction_risk_bucket": group_summaries(completed, "llm_contradiction_risk_bucket"),
        "by_llm_staleness_risk_bucket": group_summaries(completed, "llm_staleness_risk_bucket"),
        "by_llm_expected_return_delta_bucket": group_summaries(completed, "llm_expected_return_delta_bucket"),
        "pending_by_external_feed_status": pending_group_summaries(pending, "external_feed_status"),
        "pending_by_external_coverage": pending_external_coverage_summaries(pending),
        "pending_by_external_alignment": pending_external_alignment_summaries(pending),
        "pending_by_external_provider_gap_severity": pending_group_summaries(pending, "external_provider_primary_gap_severity"),
        "pending_by_external_provider_gap_severity_exposure": pending_external_provider_gap_severity_exposure_summaries(pending),
        "pending_external_provider_gap_severity_observation_summary": pending_external_provider_gap_severity_observation_summary(pending),
        "pending_external_provider_gap_severity_observation_gap_count": provider_gap_severity_observation_gap_count,
        "pending_external_provider_gap_severity_observation_gap_queue_limit": PENDING_EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_QUEUE_LIMIT,
        "pending_external_provider_gap_severity_observation_gap_hidden_label_count": max(
            0,
            provider_gap_severity_observation_gap_count - len(provider_gap_severity_observation_gap_queue),
        ),
        "pending_external_provider_gap_severity_observation_gap_queue": provider_gap_severity_observation_gap_queue,
        "pending_external_provider_gap_severity_observation_gap_work_item_count": len(
            provider_gap_severity_observation_work_items
        ),
        "pending_external_provider_gap_severity_observation_gap_work_item_queue_limit": (
            PENDING_EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_WORK_ITEM_QUEUE_LIMIT
        ),
        "pending_external_provider_gap_severity_observation_gap_visible_work_item_label_count": (
            provider_gap_severity_observation_visible_work_item_label_count
        ),
        "pending_external_provider_gap_severity_observation_gap_hidden_work_item_label_count": max(
            0,
            provider_gap_severity_observation_gap_count
            - provider_gap_severity_observation_visible_work_item_label_count,
        ),
        "pending_external_provider_gap_severity_observation_gap_hidden_work_item_count": max(
            0,
            len(provider_gap_severity_observation_work_items) - len(provider_gap_severity_observation_work_item_queue),
        ),
        "pending_external_provider_gap_severity_observation_gap_hidden_calibration_work_item_count": len(
            provider_gap_severity_observation_hidden_calibration_work_items
        ),
        "pending_external_provider_gap_severity_observation_gap_hidden_calibration_work_item_queue_limit": (
            PENDING_EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_CALIBRATION_WORK_ITEM_QUEUE_LIMIT
        ),
        "pending_external_provider_gap_severity_observation_gap_hidden_calibration_work_item_queue": (
            provider_gap_severity_observation_hidden_calibration_work_item_queue
        ),
        "pending_external_provider_gap_severity_observation_gap_hidden_calibration_report_batch_count": len(
            provider_gap_severity_observation_hidden_calibration_report_batches
        ),
        "pending_external_provider_gap_severity_observation_gap_hidden_calibration_report_batch_queue_limit": (
            PENDING_EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_CALIBRATION_REPORT_BATCH_QUEUE_LIMIT
        ),
        "pending_external_provider_gap_severity_observation_gap_hidden_calibration_report_batch_queue": (
            provider_gap_severity_observation_hidden_calibration_report_batch_queue
        ),
        "pending_external_provider_gap_severity_observation_gap_hidden_calibration_backfill_record_count": len(
            provider_gap_severity_observation_hidden_calibration_backfill_records
        ),
        "pending_external_provider_gap_severity_observation_gap_hidden_calibration_backfill_record_queue_limit": (
            PENDING_EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_CALIBRATION_BACKFILL_RECORD_QUEUE_LIMIT
        ),
        "pending_external_provider_gap_severity_observation_gap_hidden_calibration_backfill_record_queue": (
            provider_gap_severity_observation_hidden_calibration_backfill_record_queue
        ),
        "pending_external_provider_gap_severity_observation_gap_work_item_queue": (
            provider_gap_severity_observation_work_item_queue
        ),
        "pending_external_provider_gap_severity_observation_gap_due_dates": (
            provider_gap_severity_observation_due_dates
        ),
        "pending_external_provider_gap_severity_observation_gap_due_window_counts": (
            provider_gap_severity_observation_due_window_counts
        ),
        "pending_external_provider_gap_severity_observation_gap_horizon_counts": (
            provider_gap_severity_observation_horizon_counts
        ),
        "pending_external_provider_gap_severity_observation_gap_learning_role_counts": (
            provider_gap_severity_observation_learning_role_counts
        ),
        "pending_by_approval_data_friction_bucket": pending_group_summaries(pending, "approval_data_friction_bucket"),
        "pending_by_earnings_event_status": pending_group_summaries(pending, "earnings_event_status"),
        "pending_by_earnings_risk_window": pending_group_summaries(pending, "earnings_risk_window"),
        "pending_by_earnings_confirmation_bucket": pending_group_summaries(pending, "earnings_confirmation_bucket"),
        "pending_by_approval_gate_status": pending_group_summaries(pending, "approval_gate_status"),
        "pending_by_approval_blocker_bucket": pending_group_summaries(pending, "approval_blocker_bucket"),
        "pending_by_llm_signal_applied": pending_group_summaries(pending, "llm_signal_applied"),
        "pending_by_llm_review_required": pending_group_summaries(pending, "llm_review_required"),
        "pending_label_schedule": label_schedule,
        "next_label_maturity": label_schedule.get("next_label") or {},
        "next_learning_label_maturity": label_schedule.get("next_learning_label") or {},
        "next_label_maturity_date": label_schedule.get("next_label_due_date"),
        "next_learning_label_maturity_date": label_schedule.get("next_learning_label_due_date"),
        "pending_external_coverage_gap_count": pending_external_coverage_gap_count(outcomes),
        "pending_external_coverage_gap_queue": pending_external_coverage_gap_queue(outcomes),
        "pending_external_coverage_gap_plan": pending_external_coverage_gap_plan(outcomes),
        "pending_external_alignment_due_dates": pending_external_alignment_due_dates(pending),
        "pending_external_alignment_watchlist": pending_external_alignment_watchlist(pending),
        "pending_external_alignment_review_count": pending_external_alignment_review_count(pending),
        "pending_external_alignment_review_item_count": alignment_review_item_count,
        "pending_external_alignment_review_queue_limit": PENDING_EXTERNAL_ALIGNMENT_REVIEW_QUEUE_LIMIT,
        "pending_external_alignment_review_hidden_item_count": max(0, alignment_review_item_count - len(alignment_review_queue)),
        "pending_external_alignment_review_acceptance_summary": alignment_review_acceptance_summary,
        "pending_external_alignment_review_due_dates": alignment_review_due_dates,
        "pending_external_alignment_review_queue": alignment_review_queue,
        "pending_external_alignment_measurement_gap_label_count": sum(
            int(item.get("missing_label_count") or 0) for item in measurement_gap_items
        ),
        "pending_external_alignment_measurement_gap_item_count": len(measurement_gap_items),
        "pending_external_alignment_measurement_gap_queue_limit": PENDING_EXTERNAL_ALIGNMENT_MEASUREMENT_GAP_QUEUE_LIMIT,
        "pending_external_alignment_measurement_gap_hidden_item_count": max(0, len(measurement_gap_items) - len(measurement_gap_queue)),
        "pending_external_alignment_measurement_gap_plan": pending_external_alignment_measurement_gap_plan(pending),
        "pending_external_alignment_measurement_gap_queue": measurement_gap_queue,
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
                "external_signal_score": row.get("external_signal_score"),
                "coverage_adjusted_external_signal_score": row.get("coverage_adjusted_external_signal_score"),
                "external_coverage_multiplier": row.get("external_coverage_multiplier"),
                "external_feed_status": row.get("external_feed_status"),
                "external_provider_count": row.get("external_provider_count"),
                "external_provider_ok_count": row.get("external_provider_ok_count"),
                "external_provider_ok_ratio": row.get("external_provider_ok_ratio"),
                "external_provider_gap_count": row.get("external_provider_gap_count"),
                "external_provider_configuration_gap_count": row.get("external_provider_configuration_gap_count"),
                "external_provider_transient_gap_count": row.get("external_provider_transient_gap_count"),
                "external_provider_stale_gap_count": row.get("external_provider_stale_gap_count"),
                "external_provider_runtime_gap_count": row.get("external_provider_runtime_gap_count"),
                "external_provider_other_gap_count": row.get("external_provider_other_gap_count"),
                "external_provider_primary_gap_severity": row.get("external_provider_primary_gap_severity"),
                "external_provider_gap_severity_score": row.get("external_provider_gap_severity_score"),
                "external_signal_count": row.get("external_signal_count"),
                "external_source_count": row.get("external_source_count"),
                "approval_data_friction_score": row.get("approval_data_friction_score"),
                "approval_data_friction_bucket": row.get("approval_data_friction_bucket"),
                "approval_data_friction_reasons": row.get("approval_data_friction_reasons") or [],
                "approval_data_friction_penalty": row.get("approval_data_friction_penalty"),
                "earnings_days_until": row.get("earnings_days_until"),
                "earnings_event_date": row.get("earnings_event_date"),
                "earnings_event_source": row.get("earnings_event_source"),
                "earnings_confirmed_or_estimated": row.get("earnings_confirmed_or_estimated"),
                "earnings_event_status": row.get("earnings_event_status"),
                "earnings_risk_window": row.get("earnings_risk_window"),
                "earnings_confirmation_required": row.get("earnings_confirmation_required"),
                "earnings_confirmation_bucket": row.get("earnings_confirmation_bucket"),
                "approval_required": row.get("approval_required"),
                "approval_gate_status": row.get("approval_gate_status"),
                "approval_open_check_count": row.get("approval_open_check_count"),
                "approval_blocking_checks": row.get("approval_blocking_checks") or [],
                "approval_blocker_bucket": row.get("approval_blocker_bucket"),
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


def provider_gap_severity_observation_report_payloads_by_name(
    snapshots: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    for payload in snapshots:
        if not isinstance(payload, dict):
            continue
        as_of = str(payload.get("as_of") or "")[:10]
        session = str(payload.get("session") or "").strip()
        if as_of and session:
            payloads[f"{as_of}-{session}.json"] = payload
    return payloads


def exclude_current_example_snapshots(
    snapshots: list[dict[str, Any]],
    current_examples: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    current_keys = {
        (str(example.get("as_of") or "")[:10], str(example.get("session") or ""))
        for example in current_examples
        if example.get("as_of")
    }
    if not current_keys:
        return snapshots
    return [
        payload for payload in snapshots
        if (str(payload.get("as_of") or "")[:10], str(payload.get("session") or "")) not in current_keys
    ]


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
        blocking_checks = approval_blocking_check_tuple(example)
        friction = approval_data_friction_fields(example)
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
                base_risk_adjusted_expected_return=optional_float(example.get("base_risk_adjusted_expected_return")),
                evidence_quality=optional_float(example.get("evidence_quality")),
                base_evidence_quality=optional_float(example.get("base_evidence_quality")),
                drawdown_risk=optional_float(example.get("drawdown_risk")),
                base_drawdown_risk=optional_float(example.get("base_drawdown_risk")),
                timing_score=optional_float(example.get("timing_score")),
                signal_families=tuple(str(item) for item in example.get("signal_families") or []),
                event_types=tuple(str(item) for item in example.get("event_types") or []),
                model_policy_version=str(example.get("model_policy_version") or MODEL_POLICY_VERSION),
                llm_signal_applied=optional_bool(example.get("llm_signal_applied")),
                llm_expected_return_delta=optional_float(example.get("llm_expected_return_delta")),
                llm_expected_return_adjustment=optional_float(example.get("llm_expected_return_adjustment")),
                llm_evidence_quality_delta=optional_float(example.get("llm_evidence_quality_delta")),
                llm_evidence_quality_adjustment=optional_float(example.get("llm_evidence_quality_adjustment")),
                llm_drawdown_risk_delta=optional_float(example.get("llm_drawdown_risk_delta")),
                llm_drawdown_risk_adjustment=optional_float(example.get("llm_drawdown_risk_adjustment")),
                llm_conviction_score=optional_float(example.get("llm_conviction_score")),
                llm_variant_quality_score=optional_float(example.get("llm_variant_quality_score")),
                llm_source_quality_score=optional_float(example.get("llm_source_quality_score")),
                llm_contradiction_risk_score=optional_float(example.get("llm_contradiction_risk_score")),
                llm_staleness_risk_score=optional_float(example.get("llm_staleness_risk_score")),
                llm_review_required=optional_bool(example.get("llm_review_required")),
                external_signal_score=optional_float(example.get("external_signal_score")),
                coverage_adjusted_external_signal_score=optional_float(example.get("coverage_adjusted_external_signal_score")),
                external_coverage_multiplier=optional_float(example.get("external_coverage_multiplier")),
                external_feed_status=str(example.get("external_feed_status") or ""),
                external_provider_count=optional_int(example.get("external_provider_count")),
                external_provider_ok_count=optional_int(example.get("external_provider_ok_count")),
                external_provider_ok_ratio=optional_float(example.get("external_provider_ok_ratio")),
                external_provider_gap_count=optional_int(example.get("external_provider_gap_count")),
                external_provider_configuration_gap_count=optional_int(example.get("external_provider_configuration_gap_count")),
                external_provider_transient_gap_count=optional_int(example.get("external_provider_transient_gap_count")),
                external_provider_stale_gap_count=optional_int(example.get("external_provider_stale_gap_count")),
                external_provider_runtime_gap_count=optional_int(example.get("external_provider_runtime_gap_count")),
                external_provider_other_gap_count=optional_int(example.get("external_provider_other_gap_count")),
                external_provider_primary_gap_severity=str(example.get("external_provider_primary_gap_severity") or ""),
                external_provider_gap_severity_score=optional_float(example.get("external_provider_gap_severity_score")),
                external_signal_count=optional_int(example.get("external_signal_count")),
                external_source_count=optional_int(example.get("external_source_count")),
                approval_data_friction_score=friction.get("score"),
                approval_data_friction_bucket=str(friction.get("bucket") or ""),
                approval_data_friction_reasons=tuple(str(item) for item in friction.get("reasons") or []),
                approval_data_friction_penalty=friction.get("penalty"),
                earnings_days_until=optional_int(example.get("earnings_days_until")),
                earnings_event_date=str(example.get("earnings_event_date") or ""),
                earnings_event_source=str(example.get("earnings_event_source") or ""),
                earnings_confirmed_or_estimated=str(example.get("earnings_confirmed_or_estimated") or ""),
                earnings_risk_window=str(example.get("earnings_risk_window") or ""),
                earnings_confirmation_required=optional_bool(example.get("earnings_confirmation_required")),
                approval_required=optional_bool(example.get("approval_required")),
                approval_gate_status=str(example.get("approval_gate_status") or ""),
                approval_open_check_count=approval_open_check_count(example, list(blocking_checks)),
                approval_blocking_checks=blocking_checks,
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
        blocking_checks = approval_blocking_check_tuple(action)
        friction = approval_data_friction_fields(action, research, feature)
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
                base_risk_adjusted_expected_return=optional_float(action.get("base_risk_adjusted_expected_return", research.get("base_risk_adjusted_expected_return"))),
                evidence_quality=optional_float(action.get("evidence_quality", research.get("evidence_quality", feature.get("evidence_quality")))),
                base_evidence_quality=optional_float(action.get("base_evidence_quality", research.get("base_evidence_quality"))),
                drawdown_risk=optional_float(action.get("drawdown_risk", research.get("drawdown_risk", feature.get("drawdown_risk")))),
                base_drawdown_risk=optional_float(action.get("base_drawdown_risk", research.get("base_drawdown_risk"))),
                timing_score=optional_float(action.get("timing_score", research.get("timing_score", feature.get("timing_score")))),
                signal_families=tuple(str(item) for item in feature.get("signal_families") or research.get("signal_families") or card.get("signal_families") or []),
                event_types=tuple(str(item) for item in feature.get("event_types") or research.get("event_types") or action.get("event_types") or card.get("top_event_types") or []),
                model_policy_version=str(action.get("model_policy_version") or research.get("model_policy_version") or MODEL_POLICY_VERSION),
                llm_signal_applied=optional_bool(action.get("llm_signal_applied", research.get("llm_signal_applied"))),
                llm_expected_return_delta=optional_float(action.get("llm_expected_return_delta", research.get("llm_expected_return_delta"))),
                llm_expected_return_adjustment=optional_float(action.get("llm_expected_return_adjustment", research.get("llm_expected_return_adjustment"))),
                llm_evidence_quality_delta=optional_float(action.get("llm_evidence_quality_delta", research.get("llm_evidence_quality_delta"))),
                llm_evidence_quality_adjustment=optional_float(action.get("llm_evidence_quality_adjustment", research.get("llm_evidence_quality_adjustment"))),
                llm_drawdown_risk_delta=optional_float(action.get("llm_drawdown_risk_delta", research.get("llm_drawdown_risk_delta"))),
                llm_drawdown_risk_adjustment=optional_float(action.get("llm_drawdown_risk_adjustment", research.get("llm_drawdown_risk_adjustment"))),
                llm_conviction_score=optional_float(action.get("llm_conviction_score", research.get("llm_conviction_score"))),
                llm_variant_quality_score=optional_float(action.get("llm_variant_quality_score", research.get("llm_variant_quality_score"))),
                llm_source_quality_score=optional_float(action.get("llm_source_quality_score", research.get("llm_source_quality_score"))),
                llm_contradiction_risk_score=optional_float(action.get("llm_contradiction_risk_score", research.get("llm_contradiction_risk_score"))),
                llm_staleness_risk_score=optional_float(action.get("llm_staleness_risk_score", research.get("llm_staleness_risk_score"))),
                llm_review_required=optional_bool(action.get("llm_review_required", research.get("llm_review_required"))),
                external_signal_score=optional_float(action.get("external_signal_score", feature.get("external_signal_score"))),
                coverage_adjusted_external_signal_score=optional_float(action.get("coverage_adjusted_external_signal_score", feature.get("coverage_adjusted_external_signal_score"))),
                external_coverage_multiplier=optional_float(action.get("external_coverage_multiplier", feature.get("external_coverage_multiplier"))),
                external_feed_status=str(action.get("external_feed_status", feature.get("external_feed_status") or "") or ""),
                external_provider_count=optional_int(action.get("external_provider_count", feature.get("external_provider_count"))),
                external_provider_ok_count=optional_int(action.get("external_provider_ok_count", feature.get("external_provider_ok_count"))),
                external_provider_ok_ratio=optional_float(action.get("external_provider_ok_ratio", feature.get("external_provider_ok_ratio"))),
                external_provider_gap_count=optional_int(action.get("external_provider_gap_count", feature.get("external_provider_gap_count"))),
                external_provider_configuration_gap_count=optional_int(action.get("external_provider_configuration_gap_count", feature.get("external_provider_configuration_gap_count"))),
                external_provider_transient_gap_count=optional_int(action.get("external_provider_transient_gap_count", feature.get("external_provider_transient_gap_count"))),
                external_provider_stale_gap_count=optional_int(action.get("external_provider_stale_gap_count", feature.get("external_provider_stale_gap_count"))),
                external_provider_runtime_gap_count=optional_int(action.get("external_provider_runtime_gap_count", feature.get("external_provider_runtime_gap_count"))),
                external_provider_other_gap_count=optional_int(action.get("external_provider_other_gap_count", feature.get("external_provider_other_gap_count"))),
                external_provider_primary_gap_severity=str(action.get("external_provider_primary_gap_severity", feature.get("external_provider_primary_gap_severity") or "") or ""),
                external_provider_gap_severity_score=optional_float(action.get("external_provider_gap_severity_score", feature.get("external_provider_gap_severity_score"))),
                external_signal_count=optional_int(action.get("external_signal_count", feature.get("external_signal_count"))),
                external_source_count=optional_int(action.get("external_source_count", feature.get("external_source_count"))),
                approval_data_friction_score=friction.get("score"),
                approval_data_friction_bucket=str(friction.get("bucket") or ""),
                approval_data_friction_reasons=tuple(str(item) for item in friction.get("reasons") or []),
                approval_data_friction_penalty=friction.get("penalty"),
                earnings_days_until=optional_int(action.get("earnings_days_until", research.get("earnings_days_until", feature.get("earnings_days_until")))),
                earnings_event_date=str(action.get("earnings_event_date", research.get("earnings_event_date", feature.get("earnings_event_date") or "")) or ""),
                earnings_event_source=str(action.get("earnings_event_source", research.get("earnings_event_source", feature.get("earnings_event_source") or "")) or ""),
                earnings_confirmed_or_estimated=str(
                    action.get(
                        "earnings_confirmed_or_estimated",
                        research.get("earnings_confirmed_or_estimated", feature.get("earnings_confirmed_or_estimated") or ""),
                    )
                    or ""
                ),
                earnings_risk_window=str(action.get("earnings_risk_window", research.get("earnings_risk_window", feature.get("earnings_risk_window") or "")) or ""),
                earnings_confirmation_required=optional_bool(
                    action.get(
                        "earnings_confirmation_required",
                        research.get("earnings_confirmation_required", feature.get("earnings_confirmation_required")),
                    )
                ),
                approval_required=optional_bool(action.get("approval_required")),
                approval_gate_status=str(action.get("approval_gate_status") or ""),
                approval_open_check_count=approval_open_check_count(action, list(blocking_checks)),
                approval_blocking_checks=blocking_checks,
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
    due_date = estimated_label_due_date(trial.as_of, horizon)
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
        "base_risk_adjusted_expected_return": trial.base_risk_adjusted_expected_return,
        "evidence_quality": trial.evidence_quality,
        "base_evidence_quality": trial.base_evidence_quality,
        "drawdown_risk": trial.drawdown_risk,
        "base_drawdown_risk": trial.base_drawdown_risk,
        "timing_score": trial.timing_score,
        "llm_signal_applied": trial.llm_signal_applied,
        "llm_expected_return_delta": trial.llm_expected_return_delta,
        "llm_expected_return_adjustment": trial.llm_expected_return_adjustment,
        "llm_expected_return_delta_bucket": llm_delta_bucket(trial.llm_expected_return_delta),
        "llm_evidence_quality_delta": trial.llm_evidence_quality_delta,
        "llm_evidence_quality_adjustment": trial.llm_evidence_quality_adjustment,
        "llm_drawdown_risk_delta": trial.llm_drawdown_risk_delta,
        "llm_drawdown_risk_adjustment": trial.llm_drawdown_risk_adjustment,
        "llm_conviction_score": trial.llm_conviction_score,
        "llm_conviction_bucket": llm_score_bucket(trial.llm_conviction_score),
        "llm_variant_quality_score": trial.llm_variant_quality_score,
        "llm_source_quality_score": trial.llm_source_quality_score,
        "llm_contradiction_risk_score": trial.llm_contradiction_risk_score,
        "llm_contradiction_risk_bucket": llm_score_bucket(trial.llm_contradiction_risk_score),
        "llm_staleness_risk_score": trial.llm_staleness_risk_score,
        "llm_staleness_risk_bucket": llm_score_bucket(trial.llm_staleness_risk_score),
        "llm_review_required": trial.llm_review_required,
        "signal_families": list(trial.signal_families),
        "event_types": list(trial.event_types),
        "external_signal_score": trial.external_signal_score,
        "coverage_adjusted_external_signal_score": trial.coverage_adjusted_external_signal_score,
        "external_coverage_multiplier": trial.external_coverage_multiplier,
        "external_feed_status": trial.external_feed_status,
        "external_provider_count": trial.external_provider_count,
        "external_provider_ok_count": trial.external_provider_ok_count,
        "external_provider_ok_ratio": trial.external_provider_ok_ratio,
        "external_provider_gap_count": trial.external_provider_gap_count,
        "external_provider_configuration_gap_count": trial.external_provider_configuration_gap_count,
        "external_provider_transient_gap_count": trial.external_provider_transient_gap_count,
        "external_provider_stale_gap_count": trial.external_provider_stale_gap_count,
        "external_provider_runtime_gap_count": trial.external_provider_runtime_gap_count,
        "external_provider_other_gap_count": trial.external_provider_other_gap_count,
        "external_provider_primary_gap_severity": trial.external_provider_primary_gap_severity,
        "external_provider_gap_severity_score": trial.external_provider_gap_severity_score,
        "external_signal_count": trial.external_signal_count,
        "external_source_count": trial.external_source_count,
        "approval_data_friction_score": trial.approval_data_friction_score,
        "approval_data_friction_bucket": approval_data_friction_bucket(trial),
        "approval_data_friction_reasons": list(trial.approval_data_friction_reasons),
        "approval_data_friction_penalty": trial.approval_data_friction_penalty,
        "earnings_days_until": trial.earnings_days_until,
        "earnings_event_date": trial.earnings_event_date,
        "earnings_event_source": trial.earnings_event_source,
        "earnings_confirmed_or_estimated": trial.earnings_confirmed_or_estimated,
        "earnings_event_status": earnings_event_status(trial),
        "earnings_risk_window": trial.earnings_risk_window,
        "earnings_confirmation_required": trial.earnings_confirmation_required,
        "earnings_confirmation_bucket": earnings_confirmation_bucket(trial),
        "approval_required": trial.approval_required,
        "approval_gate_status": trial.approval_gate_status,
        "approval_open_check_count": trial.approval_open_check_count,
        "approval_blocking_checks": list(trial.approval_blocking_checks),
        "approval_blocker_bucket": approval_blocker_bucket(trial),
    }
    base["external_alignment"] = external_alignment_bucket(base)
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


def earnings_event_status(trial: RecommendationTrial) -> str:
    status = str(trial.earnings_confirmed_or_estimated or "").strip().lower()
    if status:
        return status
    if trial.earnings_event_date or trial.earnings_days_until is not None:
        return "unknown_event_status"
    return "no_event"


def earnings_confirmation_bucket(trial: RecommendationTrial) -> str:
    if trial.earnings_confirmation_required:
        return "confirmation_required"
    if trial.earnings_event_date or trial.earnings_days_until is not None:
        return "no_confirmation_required"
    return "no_event"


def approval_blocking_check_tuple(source: dict[str, Any]) -> tuple[str, ...]:
    checks = approval_blocking_check_names(source.get("approval_checks"))
    if not checks:
        checks = [
            str(item).strip()
            for item in source.get("approval_blocking_checks") or []
            if str(item or "").strip()
        ]
    return tuple(sorted(set(checks)))


def approval_data_friction_fields(*sources: dict[str, Any]) -> dict[str, Any]:
    explicit_score = first_optional_float(sources, "approval_data_friction_score")
    explicit_bucket = first_text(sources, "approval_data_friction_bucket")
    explicit_reasons = first_list(sources, "approval_data_friction_reasons")
    explicit_penalty = first_optional_float(sources, "approval_data_friction_penalty")
    derived = derived_approval_data_friction(sources)
    bucket = explicit_bucket if explicit_bucket and explicit_bucket != "unknown" else str(derived.get("bucket") or explicit_bucket or "")
    score = explicit_score if explicit_score is not None else derived.get("score")
    reasons = explicit_reasons or tuple(str(item) for item in derived.get("reasons") or [])
    return {
        "score": score,
        "bucket": bucket,
        "reasons": reasons,
        "penalty": explicit_penalty,
    }


def derived_approval_data_friction(sources: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    earnings_required_value = first_value(sources, "earnings_confirmation_required")
    earnings_required = optional_bool(earnings_required_value)
    earnings_status = first_text(sources, "earnings_confirmed_or_estimated")
    earnings_days = first_optional_int(sources, "earnings_days_until")
    earnings_date = first_text(sources, "earnings_event_date")
    earnings_source = first_text(sources, "earnings_event_source")
    earnings_risk_window = first_text(sources, "earnings_risk_window")
    has_earnings_context = any(
        value not in (None, "")
        for value in (earnings_required_value, earnings_status, earnings_days, earnings_date, earnings_source, earnings_risk_window)
    )

    external_status = first_text(sources, "external_feed_status")
    external_provider_count = first_optional_int(sources, "external_provider_count")
    external_provider_ok_count = first_optional_int(sources, "external_provider_ok_count")
    external_provider_ok_ratio = first_optional_float(sources, "external_provider_ok_ratio")
    external_coverage = first_optional_float(sources, "external_coverage_multiplier")
    external_gap_count = first_optional_int(sources, "external_provider_gap_count")
    external_primary_gap_severity = first_text(sources, "external_provider_primary_gap_severity")
    external_gap_severity_score = first_optional_float(sources, "external_provider_gap_severity_score")
    has_external_context = any(
        value not in (None, "")
        for value in (
            external_status,
            external_provider_count,
            external_provider_ok_count,
            external_provider_ok_ratio,
            external_coverage,
            external_gap_count,
            external_primary_gap_severity,
            external_gap_severity_score,
        )
    )

    company_review = first_value(sources, "company_review_required")
    if company_review is None:
        company_review = first_value(sources, "review_required")
    has_company_context = company_review is not None

    if not has_earnings_context and not has_external_context and not has_company_context:
        return {}

    event = None
    if has_earnings_context:
        event = {
            "event_type": "earnings",
            "confirmed_or_estimated": earnings_status or ("estimated" if earnings_required else ""),
            "source": earnings_source,
            "risk_window": earnings_risk_window,
        }
        if earnings_days is not None:
            event["days_until"] = earnings_days
        if earnings_date:
            event["event_date"] = earnings_date
    external = {}
    if has_external_context:
        external = {
            "external_status": external_status,
            "provider_count": external_provider_count,
            "provider_ok_count": external_provider_ok_count,
            "provider_ok_ratio": external_provider_ok_ratio if external_provider_ok_ratio is not None else external_coverage,
            "provider_gaps": synthetic_provider_gaps_from_external_fields(sources),
        }
    company = {"review_required": optional_bool(company_review)} if has_company_context else {}
    return approval_data_friction(event, external, company)


def synthetic_provider_gaps_from_external_fields(sources: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    severity_counts = {
        "configuration_required": first_optional_int(sources, "external_provider_configuration_gap_count") or 0,
        "transient_network": first_optional_int(sources, "external_provider_transient_gap_count") or 0,
        "stale_or_empty": first_optional_int(sources, "external_provider_stale_gap_count") or 0,
        "runtime_budget": first_optional_int(sources, "external_provider_runtime_gap_count") or 0,
        "investigate": first_optional_int(sources, "external_provider_other_gap_count") or 0,
    }
    for severity, count in severity_counts.items():
        gaps.extend({"severity": severity} for _ in range(max(0, count)))
    if gaps:
        return gaps
    primary = first_text(sources, "external_provider_primary_gap_severity")
    gap_count = first_optional_int(sources, "external_provider_gap_count") or 0
    if primary and gap_count > 0:
        return [{"severity": primary} for _ in range(gap_count)]
    return []


def first_value(sources: tuple[dict[str, Any], ...], key: str) -> Any:
    for source in sources:
        if not isinstance(source, dict):
            continue
        if key in source and source.get(key) not in (None, ""):
            return source.get(key)
    return None


def first_text(sources: tuple[dict[str, Any], ...], key: str) -> str:
    value = first_value(sources, key)
    return str(value).strip() if value not in (None, "") else ""


def first_list(sources: tuple[dict[str, Any], ...], key: str) -> tuple[str, ...]:
    for source in sources:
        if not isinstance(source, dict):
            continue
        value = source.get(key)
        if isinstance(value, list) and value:
            return tuple(str(item) for item in value if str(item).strip())
    return ()


def first_optional_float(sources: tuple[dict[str, Any], ...], key: str) -> float | None:
    return optional_float(first_value(sources, key))


def first_optional_int(sources: tuple[dict[str, Any], ...], key: str) -> int | None:
    return optional_int(first_value(sources, key))


def approval_blocker_bucket(trial: RecommendationTrial) -> str:
    gate_status = str(trial.approval_gate_status or "").strip().lower()
    open_count = trial.approval_open_check_count
    if gate_status == "blocked_until_confirmation":
        return "blocked_until_confirmation"
    if gate_status == "review_required" or (open_count is not None and open_count > 0):
        return "review_required"
    if open_count == 0:
        return "ready"
    if trial.approval_required:
        return "approval_required_unknown"
    return "no_approval_context"


def approval_data_friction_bucket(trial: RecommendationTrial) -> str:
    bucket = str(trial.approval_data_friction_bucket or "").strip().lower()
    if bucket:
        return bucket
    return "unknown"


@lru_cache(maxsize=1024)
def estimated_label_due_date(as_of: date, horizon: str) -> date:
    fallback = as_of + timedelta(days=HORIZON_CALENDAR_DAYS[horizon])
    trading_days = HORIZON_TRADING_DAYS[horizon]
    try:
        import pandas_market_calendars as mcal

        calendar = mcal.get_calendar("XNYS")
        end_date = as_of + timedelta(days=max(HORIZON_CALENDAR_DAYS[horizon] * 2, trading_days * 3 + 14))
        schedule = calendar.schedule(start_date=as_of.isoformat(), end_date=end_date.isoformat())
        trading_dates = [row.date() for row in schedule.index]
    except Exception:
        return fallback
    if len(trading_dates) > trading_days:
        return trading_dates[trading_days]
    return fallback


def horizon_summaries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [summary_for_rows(horizon, [row for row in rows if row.get("horizon") == horizon]) for horizon in FORWARD_HORIZONS]


def summary_for_rows(label: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [row for row in rows if row.get("status") == "complete"]
    pending = [row for row in rows if row.get("status") == "pending"]
    missing = [row for row in rows if row.get("status") == "missing_price"]
    returns = [float(row.get("decision_forward_return_pct") or 0) for row in completed]
    summary = {
        "horizon": label,
        "trial_count": len(rows),
        "completed_count": len(completed),
        "pending_count": len(pending),
        "missing_price_count": len(missing),
        "hit_rate": round(sum(1 for row in completed if row.get("hit")) / len(completed), 4) if completed else None,
        "average_decision_return": round(mean(returns), 2) if returns else None,
        "median_like_decision_return": median_like(returns),
        "average_expected_return": avg(row.get("risk_adjusted_expected_return") for row in completed),
    }
    summary.update(error_summary(completed))
    return summary


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


def llm_score_bucket(value: float | None) -> str:
    if value is None:
        return "no_llm_score"
    if value >= 75:
        return "high"
    if value >= 45:
        return "medium"
    return "low"


def llm_delta_bucket(value: float | None) -> str:
    if value is None:
        return "no_llm_delta"
    if value >= 1.0:
        return "positive"
    if value <= -1.0:
        return "negative"
    return "neutral"


def external_coverage_summaries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[external_coverage_band(row)].append(row)
    return sorted(
        [summary_for_group(label, group) for label, group in grouped.items()],
        key=lambda row: (coverage_band_sort_key(str(row.get("key") or "")), row["completed_count"]),
        reverse=True,
    )


def external_alignment_summaries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[external_alignment_bucket(row)].append(row)
    return sorted(
        [summary_for_group(label, group) for label, group in grouped.items()],
        key=lambda row: (external_alignment_sort_key(str(row.get("key") or "")), row["completed_count"]),
        reverse=True,
    )


def external_provider_gap_severity_exposure_summaries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for label in external_provider_gap_severity_exposure_labels(row):
            grouped[label].append(row)
    return sorted(
        [summary_for_group(label, group) for label, group in grouped.items()],
        key=lambda row: (external_provider_gap_severity_sort_key(str(row.get("key") or "")), row["completed_count"]),
        reverse=True,
    )


def pending_group_summaries(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        values = row.get(key) if key == "signal_families" else [row.get(key)]
        for value in values or ["unknown"]:
            grouped[str(value or "unknown")].append(row)
    return sorted(
        [pending_summary_for_group(label, group) for label, group in grouped.items()],
        key=lambda row: (-int(row["pending_count"]), str(row.get("key") or "")),
    )


def pending_external_coverage_summaries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[external_coverage_band(row)].append(row)
    return sorted(
        [pending_summary_for_group(label, group) for label, group in grouped.items()],
        key=lambda row: (-coverage_band_sort_key(str(row.get("key") or "")), -int(row["pending_count"])),
    )


def pending_external_alignment_summaries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[external_alignment_bucket(row)].append(row)
    return sorted(
        [pending_summary_for_group(label, group) for label, group in grouped.items()],
        key=lambda row: (-external_alignment_sort_key(str(row.get("key") or "")), -int(row["pending_count"])),
    )


def pending_external_provider_gap_severity_exposure_summaries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for label in external_provider_gap_severity_exposure_labels(row):
            grouped[label].append(row)
    return sorted(
        [pending_summary_for_group(label, group) for label, group in grouped.items()],
        key=lambda row: (-int(row["pending_count"]), -external_provider_gap_severity_sort_key(str(row.get("key") or ""))),
    )


def pending_external_provider_gap_severity_observation_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    unknown_rows = pending_external_provider_gap_severity_observation_gap_candidates(rows)
    observed_count = len(rows) - len(unknown_rows)
    due_dates = sorted(str(row.get("due_date") or "")[:10] for row in unknown_rows if row.get("due_date"))
    return {
        "status": "complete" if not unknown_rows else "needs_decision_time_backfill",
        "pending_label_count": len(rows),
        "observed_label_count": observed_count,
        "unknown_label_count": len(unknown_rows),
        "observed_ratio": round(observed_count / len(rows), 4) if rows else None,
        "unknown_next_due_date": due_dates[0] if due_dates else None,
        "unknown_horizons": sorted({str(row.get("horizon") or "") for row in unknown_rows if row.get("horizon")}),
        "unknown_symbols": sorted({str(row.get("symbol") or "") for row in unknown_rows if row.get("symbol")})[:12],
        "backfill_policy": "decision_time_only",
        "minimum_fields_to_backfill": list(EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_FIELDS),
        "backfill_action": (
            "Backfill provider gap severity fields from decision-time reports before using these labels "
            "to calibrate provider reliability features."
        ),
    }


def pending_external_provider_gap_severity_observation_gap_count(rows: list[dict[str, Any]]) -> int:
    return len(pending_external_provider_gap_severity_observation_gap_candidates(rows))


def pending_external_provider_gap_severity_observation_gap_queue(
    rows: list[dict[str, Any]],
    limit: int = PENDING_EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_QUEUE_LIMIT,
) -> list[dict[str, Any]]:
    return [
        external_provider_gap_severity_observation_gap_row(row)
        for row in pending_external_provider_gap_severity_observation_gap_candidates(rows)[:limit]
    ]


def pending_external_provider_gap_severity_observation_gap_work_item_queue(
    rows: list[dict[str, Any]],
    limit: int = PENDING_EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_WORK_ITEM_QUEUE_LIMIT,
) -> list[dict[str, Any]]:
    return pending_external_provider_gap_severity_observation_gap_work_items(rows)[:limit]


def pending_external_provider_gap_severity_observation_gap_hidden_calibration_work_item_queue(
    work_items: list[dict[str, Any]],
    limit: int = PENDING_EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_CALIBRATION_WORK_ITEM_QUEUE_LIMIT,
) -> list[dict[str, Any]]:
    return work_items[:limit]


def pending_external_provider_gap_severity_observation_gap_hidden_calibration_report_batch_queue(
    report_batches: list[dict[str, Any]],
    limit: int = PENDING_EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_CALIBRATION_REPORT_BATCH_QUEUE_LIMIT,
) -> list[dict[str, Any]]:
    return report_batches[:limit]


def pending_external_provider_gap_severity_observation_gap_hidden_calibration_backfill_record_queue(
    records: list[dict[str, Any]],
    limit: int = PENDING_EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_CALIBRATION_BACKFILL_RECORD_QUEUE_LIMIT,
) -> list[dict[str, Any]]:
    return records[:limit]


def pending_external_provider_gap_severity_observation_gap_hidden_calibration_work_items(
    work_items: list[dict[str, Any]],
    visible_work_items: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    visible_ids = {
        str(item.get("external_provider_gap_severity_observation_work_item_id") or "")
        for item in visible_work_items or []
        if isinstance(item, dict)
    }
    return sorted(
        [
            item
            for item in work_items
            if isinstance(item, dict)
            and str(item.get("external_provider_gap_severity_observation_work_item_id") or "") not in visible_ids
            and provider_gap_severity_observation_horizon_learning_role(str(item.get("horizon") or ""))
            == "calibration_label"
        ],
        key=lambda row: (
            str(row.get("due_date") or "9999-99-99"),
            provider_gap_severity_observation_horizon_sort(row.get("horizon")),
            -float(row.get("external_provider_gap_severity_observation_learning_value_score") or 0.0),
            str(row.get("symbol") or ""),
            str(row.get("as_of") or ""),
            str(row.get("session") or ""),
        ),
    )


def pending_external_provider_gap_severity_observation_gap_hidden_calibration_report_batches(
    work_items: list[dict[str, Any]],
    report_payloads_by_name: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in work_items:
        if not isinstance(item, dict):
            continue
        report_json = str(item.get("decision_time_report_json") or "unknown")
        grouped[report_json].append(item)

    rows = []
    for report_json, items in grouped.items():
        due_dates = sorted({str(item.get("due_date") or "")[:10] for item in items if item.get("due_date")})
        horizons = sorted(
            {str(item.get("horizon") or "unknown") for item in items},
            key=lambda horizon: (provider_gap_severity_observation_horizon_sort(horizon), horizon),
        )
        symbols = sorted({str(item.get("symbol") or "unknown") for item in items})
        sample = items[0]
        report_payload = (report_payloads_by_name or {}).get(report_json) if report_json != "unknown" else None
        candidate = provider_gap_severity_observation_candidate_backfill_from_report(report_payload)
        rows.append(
            {
                "decision_time_report_json": None if report_json == "unknown" else report_json,
                "decision_time_report_markdown": sample.get("decision_time_report_markdown"),
                "decision_time_report_stem": sample.get("decision_time_report_stem"),
                "decision_time_report_json_available": all(
                    item.get("decision_time_report_json_available") is True for item in items
                ),
                "decision_time_report_markdown_available": all(
                    item.get("decision_time_report_markdown_available") is True for item in items
                ),
                "as_of": sample.get("as_of"),
                "session": sample.get("session"),
                "label_count": sum(int(item.get("label_count") or 0) for item in items),
                "work_item_count": len(items),
                "due_date_count": len(due_dates),
                "earliest_due_date": due_dates[0] if due_dates else None,
                "latest_due_date": due_dates[-1] if due_dates else None,
                "horizons": horizons,
                "symbols": symbols[:12],
                "symbol_count": len(symbols),
                "sample_work_item_ids": [
                    str(item.get("external_provider_gap_severity_observation_work_item_id") or "")
                    for item in items[:8]
                    if item.get("external_provider_gap_severity_observation_work_item_id")
                ],
                **candidate,
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("earliest_due_date") or "9999-99-99"),
            -int(row.get("work_item_count") or 0),
            str(row.get("decision_time_report_json") or ""),
        ),
    )


def pending_external_provider_gap_severity_observation_gap_hidden_calibration_backfill_records(
    work_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = [
        provider_gap_severity_observation_hidden_calibration_backfill_record(item)
        for item in work_items
        if isinstance(item, dict) and item.get("candidate_backfill_status") == "ready"
    ]
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("due_date") or "9999-99-99"),
            provider_gap_severity_observation_horizon_sort(row.get("horizon")),
            -float(row.get("external_provider_gap_severity_observation_learning_value_score") or 0.0),
            str(row.get("symbol") or ""),
            str(row.get("decision_as_of") or ""),
            str(row.get("session") or ""),
        ),
    )


def provider_gap_severity_observation_hidden_calibration_backfill_record(
    item: dict[str, Any],
) -> dict[str, Any]:
    work_item_id = str(item.get("external_provider_gap_severity_observation_work_item_id") or "")
    fields = list(EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_FIELDS)
    candidate_values = item.get("candidate_backfill_values")
    candidate_values = candidate_values if isinstance(candidate_values, dict) else {}
    values = {field: candidate_values.get(field) for field in fields if field in candidate_values}
    missing_fields = [field for field in fields if field not in values]
    source_outcome_ids = [str(row) for row in item.get("source_outcome_ids") or [] if row]
    source_trial_ids = [str(row) for row in item.get("source_trial_ids") or [] if row]
    candidate_apply_status = (
        "ready"
        if not missing_fields
        and bool(source_outcome_ids)
        and item.get("decision_time_report_json_available") is True
        else "blocked"
    )
    return {
        "external_provider_gap_severity_observation_backfill_record_id": stable_id(
            [
                EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_BACKFILL_RECORD_VERSION,
                work_item_id,
                item.get("decision_time_report_json"),
            ]
        ),
        "external_provider_gap_severity_observation_backfill_record_version": (
            EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_BACKFILL_RECORD_VERSION
        ),
        "external_provider_gap_severity_observation_work_item_id": work_item_id,
        "candidate_apply_status": candidate_apply_status,
        "candidate_apply_policy": "update_matching_recommendation_training_examples_by_source_trial_id",
        "target_section": "recommendation_training_examples",
        "symbol": item.get("symbol"),
        "horizon": item.get("horizon"),
        "external_provider_gap_severity_observation_learning_value_score": (
            item.get("external_provider_gap_severity_observation_learning_value_score")
        ),
        "decision_as_of": item.get("as_of"),
        "session": item.get("session"),
        "due_date": item.get("due_date"),
        "source_report": item.get("decision_time_report_json"),
        "source_report_markdown": item.get("decision_time_report_markdown"),
        "source_report_available": item.get("decision_time_report_json_available") is True,
        "source_outcome_ids": source_outcome_ids,
        "source_trial_ids": source_trial_ids,
        "fields_to_backfill": fields,
        "candidate_backfill_values": values,
        "candidate_missing_required_fields": missing_fields,
        "candidate_source_section": item.get("candidate_source_section"),
        "candidate_backfill_policy": item.get("candidate_backfill_policy"),
        "candidate_provider_gap_sources": list(item.get("candidate_provider_gap_sources") or []),
        "candidate_provider_gap_severities": list(item.get("candidate_provider_gap_severities") or []),
    }


def provider_gap_severity_observation_candidate_backfill_from_report(
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    base = {
        "candidate_backfill_status": "missing_report",
        "candidate_source_section": "",
        "candidate_backfill_policy": "decision_time_external_signals_provider_status_only",
        "candidate_backfill_values": {},
        "candidate_missing_fields": list(EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_FIELDS),
        "candidate_provider_gap_count": 0,
        "candidate_provider_gap_sources": [],
        "candidate_provider_gap_severities": [],
    }
    if not isinstance(payload, dict):
        return base
    external = payload.get("external_signals")
    if not isinstance(external, dict) or not external:
        return {**base, "candidate_backfill_status": "missing_external_signals"}
    source_statuses = external.get("source_statuses")
    if not isinstance(source_statuses, list) or not source_statuses:
        return {**base, "candidate_backfill_status": "missing_external_signal_source_statuses"}
    provider_gaps = external_provider_gap_rows(external, limit=None)
    provider_gaps = [row for row in provider_gaps if isinstance(row, dict)]
    features = external_provider_gap_features({**external, "provider_gaps": provider_gaps})
    values = {
        "external_provider_gap_count": features.get("gap_count"),
        "external_provider_configuration_gap_count": features.get("configuration_gap_count"),
        "external_provider_runtime_gap_count": features.get("runtime_gap_count"),
        "external_provider_stale_gap_count": features.get("stale_gap_count"),
        "external_provider_transient_gap_count": features.get("transient_gap_count"),
        "external_provider_other_gap_count": features.get("other_gap_count"),
        "external_provider_primary_gap_severity": features.get("primary_gap_severity"),
        "external_provider_gap_severity_score": features.get("gap_severity_score"),
    }
    missing_fields = [
        field
        for field in EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_FIELDS
        if values.get(field) is None or values.get(field) == ""
    ]
    return {
        **base,
        "candidate_backfill_status": "ready" if not missing_fields else "incomplete",
        "candidate_source_section": "external_signals.source_statuses",
        "candidate_backfill_values": values if not missing_fields else {},
        "candidate_missing_fields": missing_fields,
        "candidate_provider_gap_count": len(provider_gaps),
        "candidate_provider_gap_sources": sorted(
            {
                str(row.get("source") or row.get("label") or "")
                for row in provider_gaps
                if row.get("source") or row.get("label")
            }
        )[:12],
        "candidate_provider_gap_severities": sorted(
            {str(row.get("severity") or "") for row in provider_gaps if row.get("severity")}
        ),
    }


def provider_gap_severity_observation_work_items_with_report_availability(
    work_items: list[dict[str, Any]],
    reports_dir: Path,
) -> list[dict[str, Any]]:
    annotated = []
    for item in work_items:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        report_json = str(row.get("decision_time_report_json") or "")
        report_markdown = str(row.get("decision_time_report_markdown") or "")
        row["decision_time_report_json_available"] = bool(report_json and (reports_dir / report_json).exists())
        row["decision_time_report_markdown_available"] = bool(
            report_markdown and (reports_dir / report_markdown).exists()
        )
        annotated.append(row)
    return annotated


def provider_gap_severity_observation_work_items_with_candidate_backfill(
    work_items: list[dict[str, Any]],
    report_payloads_by_name: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates_by_report: dict[str, dict[str, Any]] = {}
    annotated = []
    for item in work_items:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        report_json = str(row.get("decision_time_report_json") or "")
        if report_json not in candidates_by_report:
            candidates_by_report[report_json] = provider_gap_severity_observation_candidate_backfill_from_report(
                report_payloads_by_name.get(report_json)
            )
        candidate = candidates_by_report[report_json]
        row.update(provider_gap_severity_observation_candidate_backfill_copy(candidate))
        annotated.append(row)
    return annotated


def provider_gap_severity_observation_candidate_backfill_copy(
    candidate: dict[str, Any],
) -> dict[str, Any]:
    return {
        "candidate_backfill_status": candidate.get("candidate_backfill_status"),
        "candidate_source_section": candidate.get("candidate_source_section"),
        "candidate_backfill_policy": candidate.get("candidate_backfill_policy"),
        "candidate_backfill_values": dict(candidate.get("candidate_backfill_values") or {}),
        "candidate_missing_fields": list(candidate.get("candidate_missing_fields") or []),
        "candidate_provider_gap_count": candidate.get("candidate_provider_gap_count"),
        "candidate_provider_gap_sources": list(candidate.get("candidate_provider_gap_sources") or []),
        "candidate_provider_gap_severities": list(candidate.get("candidate_provider_gap_severities") or []),
    }


def pending_external_provider_gap_severity_observation_gap_work_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in pending_external_provider_gap_severity_observation_gap_candidates(rows):
        grouped[external_provider_gap_severity_observation_work_item_key(row)].append(row)
    return sorted(
        [external_provider_gap_severity_observation_work_item_row(group) for group in grouped.values()],
        key=lambda row: (
            str(row.get("due_date") or "9999-99-99"),
            -float(row.get("external_provider_gap_severity_observation_learning_value_score") or 0.0),
            str(row.get("symbol") or ""),
            str(row.get("horizon") or ""),
            str(row.get("as_of") or ""),
        ),
    )


def pending_external_provider_gap_severity_observation_gap_due_dates(
    work_items: list[dict[str, Any]],
    visible_work_items: list[dict[str, Any]] | None = None,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    visible_ids = {
        str(item.get("external_provider_gap_severity_observation_work_item_id") or "")
        for item in visible_work_items or []
        if isinstance(item, dict)
    }
    grouped: dict[str, dict[str, Any]] = {}
    horizons: dict[str, set[str]] = defaultdict(set)
    symbols: dict[str, set[str]] = defaultdict(set)
    for item in work_items:
        if not isinstance(item, dict):
            continue
        due_date = str(item.get("due_date") or "unknown")[:10] or "unknown"
        label_count = int(item.get("label_count") or 0)
        row = grouped.setdefault(
            due_date,
            {
                "due_date": due_date,
                "label_count": 0,
                "work_item_count": 0,
                "visible_label_count": 0,
                "visible_work_item_count": 0,
                "hidden_label_count": 0,
                "hidden_work_item_count": 0,
            },
        )
        row["label_count"] += label_count
        row["work_item_count"] += 1
        work_item_id = str(item.get("external_provider_gap_severity_observation_work_item_id") or "")
        if work_item_id in visible_ids:
            row["visible_label_count"] += label_count
            row["visible_work_item_count"] += 1
        else:
            row["hidden_label_count"] += label_count
            row["hidden_work_item_count"] += 1
        horizons[due_date].add(str(item.get("horizon") or "unknown"))
        symbols[due_date].add(str(item.get("symbol") or "unknown"))
    rows = sorted(
        [
            {
                **row,
                "horizons": sorted(horizons[row["due_date"]])[:12],
                "symbols": sorted(symbols[row["due_date"]])[:12],
            }
            for row in grouped.values()
        ],
        key=lambda row: (row.get("due_date") == "unknown", str(row.get("due_date") or "")),
    )
    cumulative_label_count = 0
    cumulative_work_item_count = 0
    cumulative_visible_label_count = 0
    cumulative_visible_work_item_count = 0
    cumulative_hidden_label_count = 0
    cumulative_hidden_work_item_count = 0
    for row in rows:
        due_date = parse_date(row.get("due_date"))
        days_until_due = (due_date - as_of).days if as_of and due_date else None
        row["days_until_due"] = days_until_due
        row["due_window"] = provider_gap_severity_observation_due_window(days_until_due)
        cumulative_label_count += int(row.get("label_count") or 0)
        cumulative_work_item_count += int(row.get("work_item_count") or 0)
        cumulative_visible_label_count += int(row.get("visible_label_count") or 0)
        cumulative_visible_work_item_count += int(row.get("visible_work_item_count") or 0)
        cumulative_hidden_label_count += int(row.get("hidden_label_count") or 0)
        cumulative_hidden_work_item_count += int(row.get("hidden_work_item_count") or 0)
        row["cumulative_label_count"] = cumulative_label_count
        row["cumulative_work_item_count"] = cumulative_work_item_count
        row["cumulative_visible_label_count"] = cumulative_visible_label_count
        row["cumulative_visible_work_item_count"] = cumulative_visible_work_item_count
        row["cumulative_hidden_label_count"] = cumulative_hidden_label_count
        row["cumulative_hidden_work_item_count"] = cumulative_hidden_work_item_count
    return rows


def provider_gap_severity_observation_due_window(days_until_due: int | None) -> str:
    if days_until_due is None:
        return "unknown"
    if days_until_due < 0:
        return "overdue"
    if days_until_due == 0:
        return "due_today"
    if days_until_due <= 7:
        return "due_next_7d"
    if days_until_due <= 30:
        return "due_next_30d"
    return "later"


def pending_external_provider_gap_severity_observation_gap_due_window_counts(
    due_dates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    due_dates_by_window: dict[str, set[str]] = defaultdict(set)
    for row in due_dates:
        if not isinstance(row, dict):
            continue
        due_window = str(row.get("due_window") or "unknown")
        due_date = str(row.get("due_date") or "")[:10]
        bucket = grouped.setdefault(
            due_window,
            {
                "due_window": due_window,
                "label_count": 0,
                "work_item_count": 0,
                "visible_label_count": 0,
                "visible_work_item_count": 0,
                "hidden_label_count": 0,
                "hidden_work_item_count": 0,
            },
        )
        bucket["label_count"] += int(row.get("label_count") or 0)
        bucket["work_item_count"] += int(row.get("work_item_count") or 0)
        bucket["visible_label_count"] += int(row.get("visible_label_count") or 0)
        bucket["visible_work_item_count"] += int(row.get("visible_work_item_count") or 0)
        bucket["hidden_label_count"] += int(row.get("hidden_label_count") or 0)
        bucket["hidden_work_item_count"] += int(row.get("hidden_work_item_count") or 0)
        if due_date:
            due_dates_by_window[due_window].add(due_date)
    rows = []
    for due_window, bucket in grouped.items():
        bucket_due_dates = sorted(due_dates_by_window[due_window])
        rows.append(
            {
                **bucket,
                "due_date_count": len(bucket_due_dates),
                "earliest_due_date": bucket_due_dates[0] if bucket_due_dates else None,
                "latest_due_date": bucket_due_dates[-1] if bucket_due_dates else None,
            }
        )
    return sorted(rows, key=lambda row: (provider_gap_severity_observation_due_window_sort(row.get("due_window")), str(row.get("due_window") or "")))


def pending_external_provider_gap_severity_observation_gap_horizon_counts(
    work_items: list[dict[str, Any]],
    visible_work_items: list[dict[str, Any]] | None = None,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    visible_ids = {
        str(item.get("external_provider_gap_severity_observation_work_item_id") or "")
        for item in visible_work_items or []
        if isinstance(item, dict)
    }
    grouped: dict[str, dict[str, Any]] = {}
    due_dates_by_horizon: dict[str, set[str]] = defaultdict(set)
    visible_due_buckets_by_horizon: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    hidden_due_buckets_by_horizon: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for item in work_items:
        if not isinstance(item, dict):
            continue
        horizon = str(item.get("horizon") or "unknown")
        label_count = int(item.get("label_count") or 0)
        work_item_id = str(item.get("external_provider_gap_severity_observation_work_item_id") or "")
        is_visible = work_item_id in visible_ids
        row = grouped.setdefault(
            horizon,
            {
                "horizon": horizon,
                "learning_role": provider_gap_severity_observation_horizon_learning_role(horizon),
                "label_count": 0,
                "work_item_count": 0,
                "visible_label_count": 0,
                "visible_work_item_count": 0,
                "hidden_label_count": 0,
                "hidden_work_item_count": 0,
            },
        )
        row["label_count"] += label_count
        row["work_item_count"] += 1
        if is_visible:
            row["visible_label_count"] += label_count
            row["visible_work_item_count"] += 1
        else:
            row["hidden_label_count"] += label_count
            row["hidden_work_item_count"] += 1
        due_date = str(item.get("due_date") or "")[:10]
        if due_date:
            due_dates_by_horizon[horizon].add(due_date)
            if is_visible:
                increment_provider_gap_severity_observation_due_bucket(
                    visible_due_buckets_by_horizon[horizon],
                    due_date,
                    label_count,
                    horizon=horizon,
                )
            else:
                increment_provider_gap_severity_observation_due_bucket(
                    hidden_due_buckets_by_horizon[horizon],
                    due_date,
                    label_count,
                    horizon=horizon,
                )
    rows: list[dict[str, Any]] = []
    for horizon, row in grouped.items():
        due_dates = sorted(due_dates_by_horizon[horizon])
        next_due_date = due_dates[0] if due_dates else None
        next_due = parse_date(next_due_date)
        days_until_next_due = (next_due - as_of).days if as_of and next_due else None
        rows.append(
            {
                **row,
                "due_date_count": len(due_dates),
                "next_due_date": next_due_date,
                "latest_due_date": due_dates[-1] if due_dates else None,
                "days_until_next_due": days_until_next_due,
                "next_due_window": provider_gap_severity_observation_due_window(days_until_next_due),
                **provider_gap_severity_observation_due_timing_fields(
                    visible_due_buckets_by_horizon[horizon],
                    as_of,
                    "visible",
                ),
                **provider_gap_severity_observation_due_timing_fields(
                    hidden_due_buckets_by_horizon[horizon],
                    as_of,
                    "hidden",
                ),
            }
        )
    return sorted(rows, key=lambda row: (provider_gap_severity_observation_horizon_sort(row.get("horizon")), str(row.get("horizon") or "")))


def pending_external_provider_gap_severity_observation_gap_learning_role_counts(
    horizon_counts: list[dict[str, Any]],
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    horizons_by_role: dict[str, set[str]] = defaultdict(set)
    due_dates_by_role: dict[str, set[str]] = defaultdict(set)
    visible_due_buckets_by_role: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    hidden_due_buckets_by_role: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in horizon_counts:
        if not isinstance(row, dict):
            continue
        learning_role = str(row.get("learning_role") or "unknown")
        group = grouped.setdefault(
            learning_role,
            {
                "learning_role": learning_role,
                "label_count": 0,
                "work_item_count": 0,
                "visible_label_count": 0,
                "visible_work_item_count": 0,
                "hidden_label_count": 0,
                "hidden_work_item_count": 0,
                "due_date_count": 0,
            },
        )
        group["label_count"] += int(row.get("label_count") or 0)
        group["work_item_count"] += int(row.get("work_item_count") or 0)
        group["visible_label_count"] += int(row.get("visible_label_count") or 0)
        group["visible_work_item_count"] += int(row.get("visible_work_item_count") or 0)
        group["hidden_label_count"] += int(row.get("hidden_label_count") or 0)
        group["hidden_work_item_count"] += int(row.get("hidden_work_item_count") or 0)
        group["due_date_count"] += int(row.get("due_date_count") or 0)
        horizon = str(row.get("horizon") or "")
        if horizon:
            horizons_by_role[learning_role].add(horizon)
        for key in ("next_due_date", "latest_due_date"):
            due_date = str(row.get(key) or "")[:10]
            if due_date:
                due_dates_by_role[learning_role].add(due_date)
        for key in ("next_visible_due_date", "latest_visible_due_date"):
            due_date = str(row.get(key) or "")[:10]
            if due_date:
                due_dates_by_role[learning_role].add(due_date)
        visible_due_date = str(row.get("next_visible_due_date") or "")[:10]
        if visible_due_date:
            increment_provider_gap_severity_observation_due_bucket(
                visible_due_buckets_by_role[learning_role],
                visible_due_date,
                int(row.get("next_visible_due_label_count") or 0),
                int(row.get("next_visible_due_work_item_count") or 0),
                horizons=row.get("next_visible_due_horizons") or [],
            )
        for key in ("next_hidden_due_date", "latest_hidden_due_date"):
            due_date = str(row.get(key) or "")[:10]
            if due_date:
                due_dates_by_role[learning_role].add(due_date)
        hidden_due_date = str(row.get("next_hidden_due_date") or "")[:10]
        if hidden_due_date:
            increment_provider_gap_severity_observation_due_bucket(
                hidden_due_buckets_by_role[learning_role],
                hidden_due_date,
                int(row.get("next_hidden_due_label_count") or 0),
                int(row.get("next_hidden_due_work_item_count") or 0),
                horizons=row.get("next_hidden_due_horizons") or [],
            )
    rows = []
    for learning_role, row in grouped.items():
        due_dates = sorted(due_dates_by_role[learning_role])
        next_due_date = due_dates[0] if due_dates else None
        next_due = parse_date(next_due_date)
        days_until_next_due = (next_due - as_of).days if as_of and next_due else None
        rows.append(
            {
                **row,
                "horizon_count": len(horizons_by_role[learning_role]),
                "horizons": sorted(
                    horizons_by_role[learning_role],
                    key=lambda horizon: (provider_gap_severity_observation_horizon_sort(horizon), horizon),
                ),
                "next_due_date": next_due_date,
                "latest_due_date": due_dates[-1] if due_dates else None,
                "days_until_next_due": days_until_next_due,
                "next_due_window": provider_gap_severity_observation_due_window(days_until_next_due),
                **provider_gap_severity_observation_due_timing_fields(
                    visible_due_buckets_by_role[learning_role],
                    as_of,
                    "visible",
                ),
                **provider_gap_severity_observation_due_timing_fields(
                    hidden_due_buckets_by_role[learning_role],
                    as_of,
                    "hidden",
                ),
                "visible_label_coverage_pct": provider_gap_severity_observation_coverage_pct(
                    row.get("visible_label_count"),
                    row.get("label_count"),
                ),
                "visible_work_item_coverage_pct": provider_gap_severity_observation_coverage_pct(
                    row.get("visible_work_item_count"),
                    row.get("work_item_count"),
                ),
                "queue_visibility_status": provider_gap_severity_observation_queue_visibility_status(
                    row.get("visible_label_count"),
                    row.get("label_count"),
                ),
            }
        )
    return sorted(rows, key=lambda row: (provider_gap_severity_observation_learning_role_sort(row.get("learning_role")), str(row.get("learning_role") or "")))


def provider_gap_severity_observation_horizon_learning_role(horizon: str) -> str:
    if horizon == "5d":
        return "fast_check"
    if horizon in {"1m", "3m", "6m", "12m"}:
        return "calibration_label"
    return "unknown"


def provider_gap_severity_observation_learning_role_sort(learning_role: Any) -> int:
    order = {
        "fast_check": 0,
        "calibration_label": 1,
        "unknown": 2,
    }
    return order.get(str(learning_role or "unknown"), 99)


def provider_gap_severity_observation_coverage_pct(visible_count: Any, total_count: Any) -> float:
    try:
        visible = int(visible_count or 0)
        total = int(total_count or 0)
    except (TypeError, ValueError):
        return 0.0
    if total <= 0:
        return 0.0
    return round((visible / total) * 100.0, 1)


def provider_gap_severity_observation_queue_visibility_status(
    visible_count: Any,
    total_count: Any,
) -> str:
    try:
        visible = int(visible_count or 0)
        total = int(total_count or 0)
    except (TypeError, ValueError):
        return "unknown"
    if total <= 0:
        return "empty"
    if visible <= 0:
        return "hidden"
    if visible >= total:
        return "fully_visible"
    return "partially_visible"


def provider_gap_severity_observation_due_timing_fields(
    due_buckets: dict[str, dict[str, Any]],
    as_of: date | None,
    visibility: str,
) -> dict[str, Any]:
    sorted_due_dates = sorted(due_buckets)
    next_due_date = sorted_due_dates[0] if sorted_due_dates else None
    next_due = parse_date(next_due_date)
    days_until_next_due = (next_due - as_of).days if as_of and next_due else None
    next_bucket = due_buckets.get(next_due_date or "", {})
    next_horizons = sorted(
        next_bucket.get("horizons") or [],
        key=lambda horizon: (provider_gap_severity_observation_horizon_sort(horizon), str(horizon)),
    )
    return {
        f"next_{visibility}_due_date": next_due_date,
        f"latest_{visibility}_due_date": sorted_due_dates[-1] if sorted_due_dates else None,
        f"days_until_next_{visibility}_due": days_until_next_due,
        f"next_{visibility}_due_window": provider_gap_severity_observation_due_window(days_until_next_due),
        f"next_{visibility}_due_label_count": int(next_bucket.get("label_count") or 0),
        f"next_{visibility}_due_work_item_count": int(next_bucket.get("work_item_count") or 0),
        f"next_{visibility}_due_horizons": next_horizons,
    }


def increment_provider_gap_severity_observation_due_bucket(
    due_buckets: dict[str, dict[str, Any]],
    due_date: str,
    label_count: int,
    work_item_count: int = 1,
    horizon: str | None = None,
    horizons: list[Any] | None = None,
) -> None:
    bucket = due_buckets.setdefault(due_date, {"label_count": 0, "work_item_count": 0, "horizons": set()})
    bucket["label_count"] += int(label_count or 0)
    bucket["work_item_count"] += int(work_item_count or 0)
    if horizon:
        bucket["horizons"].add(str(horizon))
    for item in horizons or []:
        if item:
            bucket["horizons"].add(str(item))


def provider_gap_severity_observation_horizon_sort(horizon: Any) -> int:
    try:
        return list(FORWARD_HORIZONS).index(str(horizon))
    except ValueError:
        return 99


def provider_gap_severity_observation_due_window_sort(due_window: Any) -> int:
    order = {
        "overdue": 0,
        "due_today": 1,
        "due_next_7d": 2,
        "due_next_30d": 3,
        "later": 4,
        "unknown": 5,
    }
    return order.get(str(due_window or "unknown"), 99)


def pending_external_provider_gap_severity_observation_gap_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [row for row in rows if not has_external_provider_gap_severity_observation(row)],
        key=lambda row: (
            str(row.get("due_date") or "9999-99-99"),
            -external_provider_gap_severity_observation_learning_value_score(row),
            str(row.get("symbol") or ""),
            str(row.get("horizon") or ""),
        ),
    )


def external_provider_gap_severity_observation_work_item_key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(row.get("symbol") or ""),
        str(row.get("horizon") or ""),
        str(row.get("as_of") or "")[:10],
        str(row.get("session") or ""),
        str(row.get("due_date") or "")[:10],
    )


def external_provider_gap_severity_observation_decision_time_report_fields(row: dict[str, Any]) -> dict[str, str]:
    as_of = str(row.get("as_of") or "")[:10]
    session = str(row.get("session") or "").strip()
    if not as_of or not session:
        return {}
    stem = f"{as_of}-{session}"
    return {
        "decision_time_report_stem": stem,
        "decision_time_report_json": f"{stem}.json",
        "decision_time_report_markdown": f"{stem}.md",
    }


def external_provider_gap_severity_observation_work_item_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(
        rows,
        key=lambda row: (
            str(row.get("outcome_id") or ""),
            str(row.get("trial_id") or ""),
            str(row.get("trade_action") or ""),
        ),
    )
    sample = ordered[0] if ordered else {}
    source_outcome_ids = sorted({str(row.get("outcome_id") or "") for row in ordered if row.get("outcome_id")})
    source_trial_ids = sorted({str(row.get("trial_id") or "") for row in ordered if row.get("trial_id")})
    max_score = max((external_provider_gap_severity_observation_learning_value_score(row) for row in ordered), default=0.0)
    learning_value_score = round(max_score + min(max(len(ordered) - 1, 0), 5), 4)
    return {
        "external_provider_gap_severity_observation_work_item_id": stable_id(
            [
                EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_WORK_ITEM_VERSION,
                sample.get("symbol"),
                sample.get("horizon"),
                str(sample.get("as_of") or "")[:10],
                sample.get("session"),
                str(sample.get("due_date") or "")[:10],
            ]
        ),
        "external_provider_gap_severity_observation_work_item_version": (
            EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_WORK_ITEM_VERSION
        ),
        "symbol": sample.get("symbol"),
        "horizon": sample.get("horizon"),
        "as_of": str(sample.get("as_of") or "")[:10] or None,
        "session": sample.get("session"),
        **external_provider_gap_severity_observation_decision_time_report_fields(sample),
        "due_date": str(sample.get("due_date") or "")[:10] or None,
        "label_count": len(ordered),
        "source_outcome_ids": source_outcome_ids[:12],
        "source_trial_ids": source_trial_ids[:12],
        "external_provider_gap_severity_observation_gap_reason": (
            f"{sample.get('symbol') or 'unknown'} {sample.get('horizon') or 'unknown'} "
            f"decision-time observation covers {len(ordered)} labels lacking provider gap severity fields."
        ),
        "external_provider_gap_severity_observation_gap_action": (
            "Backfill provider gap severity fields once for this decision-time work item, then apply them "
            "to each source label without using later provider status."
        ),
        "external_provider_gap_severity_observation_backfill_policy": "decision_time_only",
        "required_external_provider_gap_severity_observation_date": str(sample.get("as_of") or "")[:10] or None,
        "minimum_external_provider_gap_severity_fields_to_backfill": list(EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_FIELDS),
        "external_provider_gap_severity_observation_learning_value_score": learning_value_score,
    }


def external_provider_gap_severity_observation_gap_row(row: dict[str, Any]) -> dict[str, Any]:
    compact = compact_outcome(row)
    compact.update(external_provider_gap_severity_observation_decision_time_report_fields(row))
    compact["external_provider_gap_severity_observation_gap_id"] = external_provider_gap_severity_observation_gap_id(row)
    compact["external_provider_gap_severity_observation_gap_version"] = EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_GAP_VERSION
    if row.get("outcome_id"):
        compact["source_outcome_id"] = row.get("outcome_id")
    if row.get("trial_id"):
        compact["source_trial_id"] = row.get("trial_id")
    compact["external_provider_gap_severity_observation_gap_reason"] = (
        f"{row.get('symbol') or 'unknown'} {row.get('horizon') or 'unknown'} label lacks provider gap severity fields."
    )
    compact["external_provider_gap_severity_observation_gap_action"] = (
        "Backfill provider gap severity fields from the decision-time report before using this label "
        "to calibrate provider reliability features; do not use later provider status."
    )
    compact["external_provider_gap_severity_observation_backfill_policy"] = "decision_time_only"
    compact["required_external_provider_gap_severity_observation_date"] = str(row.get("as_of") or "")[:10] or None
    compact["minimum_external_provider_gap_severity_fields_to_backfill"] = list(EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_FIELDS)
    compact["external_provider_gap_severity_observation_learning_value_score"] = (
        external_provider_gap_severity_observation_learning_value_score(row)
    )
    return compact


def external_provider_gap_severity_observation_gap_id(row: dict[str, Any]) -> str:
    return stable_id(
        [
            EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_GAP_VERSION,
            row.get("outcome_id"),
            row.get("trial_id"),
            row.get("symbol"),
            row.get("horizon"),
            str(row.get("as_of") or "")[:10],
            row.get("session"),
            row.get("trade_action"),
            row.get("recommended_delta_weight"),
        ]
    )


def external_provider_gap_severity_observation_learning_value_score(row: dict[str, Any]) -> float:
    horizon_bonus = {"1m": 8.0, "3m": 6.0, "6m": 4.0, "12m": 3.0, "5d": 2.0}.get(str(row.get("horizon") or ""), 0.0)
    expected_return = abs(optional_float(row.get("risk_adjusted_expected_return")) or 0.0)
    delta_weight = abs(optional_float(row.get("recommended_delta_weight")) or 0.0)
    return round(horizon_bonus + min(expected_return / 5.0, 10.0) + min(delta_weight * 100.0, 5.0), 4)


def has_external_provider_gap_severity_observation(row: dict[str, Any]) -> bool:
    for field in EXTERNAL_PROVIDER_GAP_SEVERITY_OBSERVATION_FIELDS:
        value = row.get(field)
        if field == "external_provider_primary_gap_severity":
            primary = str(value or "").strip().lower()
            if primary and primary != "unknown":
                return True
            continue
        if value is not None:
            return True
    return False


def external_provider_gap_severity_exposure_labels(row: dict[str, Any]) -> list[str]:
    labels = [
        label
        for label, field in EXTERNAL_PROVIDER_GAP_SEVERITY_COUNT_FIELDS
        if (optional_int(row.get(field)) or 0) > 0
    ]
    if labels:
        return labels
    primary = str(row.get("external_provider_primary_gap_severity") or "").strip().lower()
    if primary and primary not in {"none", "unknown"}:
        return [primary]
    gap_count = optional_int(row.get("external_provider_gap_count"))
    if primary == "none" or gap_count == 0:
        return ["none"]
    return ["unknown"]


def external_provider_gap_severity_sort_key(label: str) -> int:
    return EXTERNAL_PROVIDER_GAP_SEVERITY_SORT.get(label, 0)


def pending_external_coverage_gap_count(rows: list[dict[str, Any]]) -> int:
    return len(pending_external_coverage_gap_candidates(rows))


def pending_external_coverage_gap_queue(rows: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    return [
        external_coverage_gap_row(row)
        for row in pending_external_coverage_gap_candidates(rows)[:limit]
    ]


def pending_external_coverage_gap_plan(rows: list[dict[str, Any]]) -> dict[str, Any]:
    observed = external_observed_long_horizon_label_count(rows)
    shortfall = max(0, MIN_CALIBRATION_SAMPLES - observed)
    candidates = pending_external_coverage_gap_candidates(rows)
    priority_rows = candidates[:min(shortfall, len(candidates))]
    residual_rows = residual_external_coverage_gap_candidates(candidates, len(priority_rows))
    priority_gap_rows = [external_coverage_gap_row(row) for row in priority_rows]
    residual_gap_rows = [
        residual_external_coverage_gap_row(row)
        for row in residual_rows[:RESIDUAL_EXTERNAL_COVERAGE_GAP_LIMIT]
    ]
    residual_required_dates = residual_external_coverage_required_observation_date_rows(residual_rows)
    acceptance_counts = acceptance_check_status_counts(priority_gap_rows)
    projected_after_priority = observed + len(priority_rows)
    projected_ready = projected_after_priority >= MIN_CALIBRATION_SAMPLES
    return {
        "minimum_external_long_horizon_required": MIN_CALIBRATION_SAMPLES,
        "observed_external_long_horizon_label_count": observed,
        "additional_external_coverage_needed": shortfall,
        "candidate_gap_count": len(candidates),
        "priority_gap_count": len(priority_rows),
        "residual_gap_count": len(residual_rows),
        "residual_gap_status": residual_gap_status(shortfall, residual_rows),
        "residual_ranking_version": EXTERNAL_COVERAGE_RESIDUAL_RANKING_VERSION,
        "residual_rank_limit": RESIDUAL_EXTERNAL_COVERAGE_GAP_LIMIT,
        "residual_hidden_gap_count": max(0, len(residual_rows) - len(residual_gap_rows)),
        "residual_required_observation_date_limit": RESIDUAL_EXTERNAL_COVERAGE_REQUIRED_DATE_LIMIT,
        "residual_required_observation_dates": residual_required_dates,
        "priority_acceptance_check_count": sum(acceptance_counts.values()),
        "priority_open_acceptance_check_count": sum(
            count for status, count in acceptance_counts.items() if status != "passed"
        ),
        "priority_acceptance_check_status_counts": acceptance_counts,
        "priority_due_date": str(priority_rows[0].get("due_date") or "")[:10] if priority_rows else None,
        "priority_symbols": sorted({str(row.get("symbol") or "") for row in priority_rows if row.get("symbol")}),
        "projected_external_long_horizon_count_after_priority_backfill": projected_after_priority,
        "projected_external_additional_needed_after_priority_backfill": max(
            0, MIN_CALIBRATION_SAMPLES - projected_after_priority
        ),
        "external_learning_ready_after_priority_backfill": projected_ready,
        "projected_external_learning_ready_date_after_priority_backfill": (
            str(priority_rows[-1].get("due_date") or "")[:10]
            if projected_ready and priority_rows
            else None
        ),
        "priority_rows": priority_gap_rows,
        "residual_rows": residual_gap_rows,
    }


def acceptance_check_status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        for check in row.get("external_coverage_acceptance_checks") or []:
            if isinstance(check, dict):
                counts[str(check.get("status") or "pending")] += 1
    return dict(sorted(counts.items()))


def external_observed_long_horizon_label_count(rows: list[dict[str, Any]]) -> int:
    return sum(
        1 for row in rows
        if row.get("status") in {"complete", "pending"}
        and row.get("horizon") != "5d"
        and has_external_observation(row)
    )


def pending_external_coverage_gap_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [
        row for row in rows
        if row.get("status") == "pending"
        and row.get("horizon") != "5d"
        and row.get("due_date")
        and not has_external_observation(row)
    ]
    ordered = sorted(
        candidates,
        key=lambda row: (
            str(row.get("due_date") or ""),
            -abs(float(row.get("recommended_delta_weight") or 0)),
            -float(row.get("risk_adjusted_expected_return") or 0),
            str(row.get("symbol") or ""),
            str(row.get("horizon") or ""),
        ),
    )
    deduped = []
    seen = set()
    for row in ordered:
        key = (
            str(row.get("symbol") or ""),
            str(row.get("horizon") or ""),
            str(row.get("due_date") or ""),
            str(row.get("trade_action") or ""),
            str(row.get("bucket") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def residual_external_coverage_gap_candidates(candidates: list[dict[str, Any]], priority_count: int) -> list[dict[str, Any]]:
    residual = candidates[priority_count:]
    return sorted(
        residual,
        key=lambda row: (
            -residual_external_coverage_learning_value_score(row),
            str(row.get("due_date") or ""),
            str(row.get("symbol") or ""),
            str(row.get("horizon") or ""),
        ),
    )


def residual_gap_status(shortfall: int, residual_rows: list[dict[str, Any]]) -> str:
    if not residual_rows:
        return "none"
    if shortfall <= 0:
        return "non_blocking_learning_backlog"
    return "after_priority_backlog"


def residual_external_coverage_gap_row(row: dict[str, Any]) -> dict[str, Any]:
    compact = external_coverage_gap_row(row)
    compact["residual_learning_value_score"] = residual_external_coverage_learning_value_score(row)
    compact["residual_learning_value_reason"] = residual_external_coverage_learning_value_reason(row)
    compact["residual_backfill_status"] = "non_blocking"
    return compact


def residual_external_coverage_required_observation_date_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        required_date = str(row.get("as_of") or "")[:10] or "unknown"
        grouped[required_date].append(row)
    summaries = [
        residual_external_coverage_required_observation_date_row(required_date, group)
        for required_date, group in grouped.items()
    ]
    return sorted(
        summaries,
        key=lambda row: (
            str(row.get("required_external_observation_date") or "9999-99-99"),
            -int(row.get("gap_count") or 0),
        ),
    )[:RESIDUAL_EXTERNAL_COVERAGE_REQUIRED_DATE_LIMIT]


def residual_external_coverage_required_observation_date_row(
    required_date: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    symbols = sorted({str(row.get("symbol") or "") for row in rows if row.get("symbol")})
    source_trials = sorted({str(row.get("trial_id") or "") for row in rows if row.get("trial_id")})
    due_dates = sorted({str(row.get("due_date") or "")[:10] for row in rows if row.get("due_date")})
    return {
        "required_external_observation_date": None if required_date == "unknown" else required_date,
        "gap_count": len(rows),
        "source_trial_count": len(source_trials),
        "symbol_count": len(symbols),
        "symbols": symbols[:12],
        "earliest_due_date": due_dates[0] if due_dates else None,
        "latest_due_date": due_dates[-1] if due_dates else None,
    }


def residual_external_coverage_learning_value_score(row: dict[str, Any]) -> float:
    horizon_bonus = {"1m": 8.0, "3m": 6.0, "6m": 4.0, "12m": 3.0}.get(str(row.get("horizon") or ""), 0.0)
    expected_return = abs(optional_float(row.get("risk_adjusted_expected_return")) or 0.0)
    delta_weight = abs(optional_float(row.get("recommended_delta_weight")) or 0.0)
    action = str(row.get("trade_action") or "")
    action_bonus = 3.0 if action in {"add", "trim"} else 1.0 if action in {"hold", "watch"} else 0.0
    return round(horizon_bonus + min(expected_return / 5.0, 10.0) + min(delta_weight * 100.0, 5.0) + action_bonus, 4)


def residual_external_coverage_learning_value_reason(row: dict[str, Any]) -> str:
    return (
        f"{row.get('horizon') or 'unknown'} label; "
        f"action={row.get('trade_action') or 'unknown'}; "
        f"expected_return={row.get('risk_adjusted_expected_return')}; "
        f"delta={row.get('recommended_delta_weight')}"
    )


def external_coverage_gap_row(row: dict[str, Any]) -> dict[str, Any]:
    compact = compact_outcome(row)
    compact["external_coverage_gap_id"] = external_coverage_gap_id(row)
    compact["external_coverage_gap_version"] = EXTERNAL_COVERAGE_GAP_VERSION
    if row.get("outcome_id"):
        compact["source_outcome_id"] = row.get("outcome_id")
    if row.get("trial_id"):
        compact["source_trial_id"] = row.get("trial_id")
    compact["external_coverage_gap_reason"] = external_coverage_gap_reason(row)
    compact["external_coverage_gap_action"] = external_coverage_gap_action(row)
    compact["external_coverage_backfill_policy"] = "decision_time_only"
    compact["required_external_observation_date"] = str(row.get("as_of") or "")[:10] or None
    compact["minimum_external_fields_to_backfill"] = minimum_external_fields_to_backfill()
    compact["external_coverage_acceptance_checks"] = external_coverage_acceptance_checks(row)
    compact["missing_external_fields"] = missing_external_fields(row)
    return compact


def external_coverage_gap_id(row: dict[str, Any]) -> str:
    return stable_id(
        [
            EXTERNAL_COVERAGE_GAP_VERSION,
            row.get("symbol"),
            row.get("horizon"),
            str(row.get("as_of") or "")[:10],
            str(row.get("due_date") or "")[:10],
            row.get("trade_action"),
            row.get("bucket"),
        ]
    )


def external_coverage_gap_reason(row: dict[str, Any]) -> str:
    symbol = str(row.get("symbol") or "this symbol")
    horizon = str(row.get("horizon") or "long-horizon")
    return (
        f"{symbol} {horizon} has no external observation attached; backfill provider "
        "coverage before the label matures so it can count toward external-signal learning."
    )


def external_coverage_gap_action(row: dict[str, Any]) -> str:
    symbol = str(row.get("symbol") or "this symbol")
    as_of = str(row.get("as_of") or "the decision date")[:10]
    return (
        f"Rebuild or attach {symbol} external-signal fields from inputs captured on or before {as_of}; "
        "do not use later news, prices, filings, or provider scores."
    )


def minimum_external_fields_to_backfill() -> list[str]:
    return [
        "external_feed_status",
        "external_coverage_multiplier",
        "coverage_adjusted_external_signal_score",
    ]


def external_coverage_acceptance_checks(row: dict[str, Any]) -> list[dict[str, Any]]:
    observation_date = str(row.get("as_of") or "")[:10] or None
    return [
        {
            "check": "external_feed_status_present",
            "field": "external_feed_status",
            "expected": "non_empty_non_unknown",
            "status": "pending",
        },
        {
            "check": "external_coverage_multiplier_present",
            "field": "external_coverage_multiplier",
            "expected": "non_null",
            "status": "pending",
        },
        {
            "check": "coverage_adjusted_external_signal_score_present",
            "field": "coverage_adjusted_external_signal_score",
            "expected": "non_null",
            "status": "pending",
        },
        {
            "check": "decision_time_only",
            "field": "required_external_observation_date",
            "expected": f"source inputs captured on or before {observation_date}",
            "status": "pending",
        },
    ]


def missing_external_fields(row: dict[str, Any]) -> list[str]:
    return [
        key for key in (
            "coverage_adjusted_external_signal_score",
            "external_coverage_multiplier",
            "external_feed_status",
            "external_signal_count",
            "external_source_count",
        )
        if row.get(key) in (None, "")
    ]


def pending_external_alignment_watchlist(rows: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    candidates = [
        row for row in rows
        if external_alignment_bucket(row) != "unknown" and row.get("due_date")
    ]
    ordered = sorted(
        candidates,
        key=lambda row: (
            str(row.get("due_date") or ""),
            -external_alignment_sort_key(external_alignment_bucket(row)),
            str(row.get("horizon") or ""),
            str(row.get("symbol") or ""),
        ),
    )
    return [compact_outcome(row) for row in ordered[:limit]]


def pending_external_alignment_review_count(rows: list[dict[str, Any]]) -> int:
    return len(pending_external_alignment_review_candidates(rows))


def pending_external_alignment_review_item_count(rows: list[dict[str, Any]]) -> int:
    return len(pending_external_alignment_review_work_items(rows))


def pending_external_alignment_review_acceptance_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    work_items = pending_external_alignment_review_work_items(rows)
    status_counts: dict[str, int] = defaultdict(int)
    open_check_counts: dict[str, int] = defaultdict(int)
    open_due_dates: dict[str, dict[str, Any]] = {}
    check_count = 0
    open_check_count = 0
    open_label_count = 0
    ready_work_item_count = 0
    metadata_ready_work_item_count = 0
    for item in work_items:
        row = item["row"]
        checks = external_alignment_review_acceptance_checks(row)
        item_open_count = 0
        metadata_checks_passed = True
        for check in checks:
            name = str(check.get("check") or "unknown")
            status = str(check.get("status") or "unknown")
            check_count += 1
            status_counts[status] += 1
            if status != "passed":
                open_check_count += 1
                item_open_count += 1
                open_check_counts[name] += 1
                due_date = str(row.get("due_date") or "")[:10] or "unknown"
                due = open_due_dates.setdefault(
                    due_date,
                    {
                        "due_date": due_date,
                        "open_check_count": 0,
                        "label_count": 0,
                        "work_item_count": 0,
                        "symbols": set(),
                        "horizons": set(),
                        "focus_counts": defaultdict(lambda: {"label_count": 0, "work_item_count": 0}),
                        "learning_action_counts": defaultdict(lambda: {"label_count": 0, "work_item_count": 0}),
                        "measurement_missing_field_counts": defaultdict(lambda: {"label_count": 0, "work_item_count": 0}),
                        "check_counts": defaultdict(int),
                    },
                )
                due["open_check_count"] += 1
                due["check_counts"][name] += 1
                if name != "matured_label_available":
                    metadata_checks_passed = False
        if item_open_count == 0:
            ready_work_item_count += 1
        elif item_open_count > 0:
            due_date = str(row.get("due_date") or "")[:10] or "unknown"
            due = open_due_dates.setdefault(
                due_date,
                {
                    "due_date": due_date,
                    "open_check_count": 0,
                    "label_count": 0,
                    "work_item_count": 0,
                    "symbols": set(),
                    "horizons": set(),
                    "focus_counts": defaultdict(lambda: {"label_count": 0, "work_item_count": 0}),
                    "learning_action_counts": defaultdict(lambda: {"label_count": 0, "work_item_count": 0}),
                    "measurement_missing_field_counts": defaultdict(lambda: {"label_count": 0, "work_item_count": 0}),
                    "check_counts": defaultdict(int),
                },
            )
            label_count = int(item.get("label_count") or 0)
            open_label_count += label_count
            due["label_count"] += label_count
            due["work_item_count"] += 1
            if row.get("symbol"):
                due["symbols"].add(str(row.get("symbol")))
            if row.get("horizon"):
                due["horizons"].add(str(row.get("horizon")))
            focus = external_alignment_review_focus(row)
            if focus:
                counts = due["focus_counts"].setdefault(focus, {"label_count": 0, "work_item_count": 0})
                counts["label_count"] += label_count
                counts["work_item_count"] += 1
            action = external_alignment_review_learning_action(row)
            if action:
                counts = due["learning_action_counts"].setdefault(action, {"label_count": 0, "work_item_count": 0})
                counts["label_count"] += label_count
                counts["work_item_count"] += 1
            item_missing_fields = set()
            for label_row in item.get("rows") or [row]:
                measurement = external_alignment_review_measurement_plan(label_row)
                for field in measurement.get("missing_measurement_fields") or []:
                    if not field:
                        continue
                    field_name = str(field)
                    counts = due["measurement_missing_field_counts"].setdefault(
                        field_name,
                        {"label_count": 0, "work_item_count": 0},
                    )
                    counts["label_count"] += 1
                    item_missing_fields.add(field_name)
            for field_name in item_missing_fields:
                due["measurement_missing_field_counts"][field_name]["work_item_count"] += 1
        if metadata_checks_passed:
            metadata_ready_work_item_count += 1
    due_rows = [
        {
            "due_date": row["due_date"],
            "open_check_count": int(row["open_check_count"]),
            "label_count": int(row["label_count"]),
            "work_item_count": int(row["work_item_count"]),
            "symbols": sorted(row["symbols"]),
            "horizons": sorted(row["horizons"]),
            "focus_counts": {
                focus: dict(counts)
                for focus, counts in sorted(row["focus_counts"].items())
            },
            "learning_action_counts": {
                action: dict(counts)
                for action, counts in sorted(row["learning_action_counts"].items())
            },
            "measurement_missing_field_counts": {
                field: dict(counts)
                for field, counts in sorted(row["measurement_missing_field_counts"].items())
            },
            "check_counts": dict(sorted(row["check_counts"].items())),
        }
        for row in sorted(open_due_dates.values(), key=lambda item: str(item["due_date"]))
    ]
    next_due = due_rows[0] if due_rows else {}
    next_due_work_item_count = int(next_due.get("work_item_count") or 0)
    next_due_date = str(next_due.get("due_date") or "")
    visible_due_work_items = sum(
        1
        for row in pending_external_alignment_review_queue(rows)
        if next_due_date and str(row.get("due_date") or "")[:10] == next_due_date
    )
    hidden_due_work_items = max(0, next_due_work_item_count - visible_due_work_items)
    return {
        "label_count": sum(int(item.get("label_count") or 0) for item in work_items),
        "work_item_count": len(work_items),
        "check_count": check_count,
        "open_check_count": open_check_count,
        "open_label_count": open_label_count,
        "ready_work_item_count": ready_work_item_count,
        "metadata_ready_work_item_count": metadata_ready_work_item_count,
        "next_open_check_due_date": next_due.get("due_date"),
        "next_open_check_due_open_check_count": int(next_due.get("open_check_count") or 0),
        "next_open_check_due_label_count": int(next_due.get("label_count") or 0),
        "next_open_check_due_work_item_count": next_due_work_item_count,
        "next_open_check_due_visible_work_item_count": visible_due_work_items,
        "next_open_check_due_hidden_work_item_count": hidden_due_work_items,
        "next_open_check_due_fully_visible": hidden_due_work_items == 0,
        "next_open_check_due_symbols": next_due.get("symbols") or [],
        "next_open_check_due_horizons": next_due.get("horizons") or [],
        "next_open_check_due_focus_counts": next_due.get("focus_counts") or {},
        "next_open_check_due_learning_action_counts": next_due.get("learning_action_counts") or {},
        "next_open_check_due_measurement_missing_field_counts": next_due.get("measurement_missing_field_counts") or {},
        "open_check_due_dates": due_rows,
        "status_counts": dict(sorted(status_counts.items())),
        "open_check_counts": dict(sorted(open_check_counts.items())),
    }


def pending_external_alignment_review_queue(
    rows: list[dict[str, Any]],
    limit: int = PENDING_EXTERNAL_ALIGNMENT_REVIEW_QUEUE_LIMIT,
) -> list[dict[str, Any]]:
    ordered = sorted(
        pending_external_alignment_review_work_items(rows),
        key=lambda item: (
            str(item["row"].get("due_date") or ""),
            -external_alignment_review_priority(item["row"]),
            str(item["row"].get("horizon") or ""),
            str(item["row"].get("symbol") or ""),
        ),
    )
    next_due_date = str(ordered[0]["row"].get("due_date") or "")[:10] if ordered else ""
    selected_items = [
        item
        for index, item in enumerate(ordered)
        if index < limit or (next_due_date and str(item["row"].get("due_date") or "")[:10] == next_due_date)
    ]
    queue = []
    for item in selected_items:
        row = item["row"]
        compact = compact_outcome(row)
        compact["external_alignment_review_id"] = external_alignment_review_id(row)
        compact["external_alignment_review_version"] = EXTERNAL_ALIGNMENT_REVIEW_VERSION
        compact["external_alignment"] = external_alignment_bucket(row)
        compact["external_alignment_review_label_count"] = item["label_count"]
        compact["external_alignment_review_priority"] = external_alignment_review_priority(row)
        compact["external_alignment_review_priority_reason"] = external_alignment_review_priority_reason(row)
        compact["external_alignment_review_focus"] = external_alignment_review_focus(row)
        compact["external_alignment_review_learning_action"] = external_alignment_review_learning_action(row)
        compact["external_alignment_review_measurement_plan"] = external_alignment_review_measurement_plan(row)
        checks = external_alignment_review_acceptance_checks(row)
        compact["external_alignment_review_acceptance_checks"] = checks
        compact["external_alignment_review_open_check_count"] = sum(
            1 for check in checks if check.get("status") != "passed"
        )
        if row.get("outcome_id"):
            compact["source_outcome_id"] = row.get("outcome_id")
        if row.get("trial_id"):
            compact["source_trial_id"] = row.get("trial_id")
        if row.get("session"):
            compact["session"] = row.get("session")
        queue.append(compact)
    return queue


def pending_external_alignment_measurement_gap_work_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gap_items = []
    for item in pending_external_alignment_review_work_items(rows):
        item_rows = item.get("rows") or [item["row"]]
        missing_rows = []
        field_counts: dict[str, dict[str, int]] = {}
        for row in item_rows:
            measurement = external_alignment_review_measurement_plan(row)
            missing_fields = [
                str(field)
                for field in measurement.get("missing_measurement_fields") or []
                if field
            ]
            if not missing_fields:
                continue
            missing_rows.append(row)
            for field in missing_fields:
                counts = field_counts.setdefault(field, {"label_count": 0, "work_item_count": 0})
                counts["label_count"] += 1
        if not missing_rows:
            continue
        for counts in field_counts.values():
            counts["work_item_count"] = 1
        review_row = max(missing_rows, key=external_alignment_review_priority)
        gap_items.append(
            {
                "row": review_row,
                "rows": missing_rows,
                "review_label_count": int(item.get("label_count") or 0),
                "missing_label_count": len(missing_rows),
                "missing_field_counts": dict(sorted(field_counts.items())),
                "missing_fields": sorted(field_counts),
            }
        )
    return gap_items


def pending_external_alignment_measurement_gap_queue(
    rows: list[dict[str, Any]],
    limit: int = PENDING_EXTERNAL_ALIGNMENT_MEASUREMENT_GAP_QUEUE_LIMIT,
) -> list[dict[str, Any]]:
    ordered = sorted(
        pending_external_alignment_measurement_gap_work_items(rows),
        key=lambda item: (
            str(item["row"].get("due_date") or ""),
            -external_alignment_review_priority(item["row"]),
            -int(item.get("missing_label_count") or 0),
            str(item["row"].get("horizon") or ""),
            str(item["row"].get("symbol") or ""),
        ),
    )
    next_due_date = str(ordered[0]["row"].get("due_date") or "")[:10] if ordered else ""
    selected_items = [
        item
        for index, item in enumerate(ordered)
        if index < limit or (next_due_date and str(item["row"].get("due_date") or "")[:10] == next_due_date)
    ]
    return [external_alignment_measurement_gap_row(item) for item in selected_items]


def pending_external_alignment_measurement_gap_plan(rows: list[dict[str, Any]]) -> dict[str, Any]:
    items = pending_external_alignment_measurement_gap_work_items(rows)
    queue = pending_external_alignment_measurement_gap_queue(rows)
    due_counts: dict[str, dict[str, Any]] = {}
    field_counts: dict[str, dict[str, int]] = {}
    for item in items:
        row = item["row"]
        due_date = str(row.get("due_date") or "")[:10] or "unknown"
        due = due_counts.setdefault(
            due_date,
            {
                "due_date": due_date,
                "label_count": 0,
                "work_item_count": 0,
                "symbols": set(),
                "horizons": set(),
                "field_counts": defaultdict(lambda: {"label_count": 0, "work_item_count": 0}),
            },
        )
        missing_label_count = int(item.get("missing_label_count") or 0)
        due["label_count"] += missing_label_count
        due["work_item_count"] += 1
        if row.get("symbol"):
            due["symbols"].add(str(row.get("symbol")))
        if row.get("horizon"):
            due["horizons"].add(str(row.get("horizon")))
        for field, counts in (item.get("missing_field_counts") or {}).items():
            due_field_counts = due["field_counts"].setdefault(field, {"label_count": 0, "work_item_count": 0})
            due_field_counts["label_count"] += int(counts.get("label_count") or 0)
            due_field_counts["work_item_count"] += int(counts.get("work_item_count") or 0)
            all_field_counts = field_counts.setdefault(field, {"label_count": 0, "work_item_count": 0})
            all_field_counts["label_count"] += int(counts.get("label_count") or 0)
            all_field_counts["work_item_count"] += int(counts.get("work_item_count") or 0)
    due_rows = [
        {
            "due_date": row["due_date"],
            "label_count": int(row["label_count"]),
            "work_item_count": int(row["work_item_count"]),
            "symbols": sorted(row["symbols"]),
            "horizons": sorted(row["horizons"]),
            "field_counts": {
                field: dict(counts)
                for field, counts in sorted(row["field_counts"].items())
            },
        }
        for row in sorted(due_counts.values(), key=lambda item: str(item["due_date"]))
    ]
    next_due = due_rows[0] if due_rows else {}
    acceptance_checks = [
        check
        for item in queue
        for check in item.get("external_alignment_measurement_acceptance_checks") or []
        if isinstance(check, dict)
    ]
    status_counts: dict[str, int] = defaultdict(int)
    for check in acceptance_checks:
        status_counts[str(check.get("status") or "unknown")] += 1
    return {
        "version": EXTERNAL_ALIGNMENT_MEASUREMENT_GAP_VERSION,
        "label_count": sum(int(item.get("missing_label_count") or 0) for item in items),
        "work_item_count": len(items),
        "queue_limit": PENDING_EXTERNAL_ALIGNMENT_MEASUREMENT_GAP_QUEUE_LIMIT,
        "hidden_work_item_count": max(0, len(items) - len(queue)),
        "next_due_date": next_due.get("due_date"),
        "next_due_label_count": int(next_due.get("label_count") or 0),
        "next_due_work_item_count": int(next_due.get("work_item_count") or 0),
        "next_due_field_counts": next_due.get("field_counts") or {},
        "next_due_symbols": next_due.get("symbols") or [],
        "next_due_horizons": next_due.get("horizons") or [],
        "field_counts": dict(sorted(field_counts.items())),
        "due_dates": due_rows,
        "priority_acceptance_check_count": len(acceptance_checks),
        "priority_open_acceptance_check_count": sum(1 for check in acceptance_checks if check.get("status") != "passed"),
        "priority_acceptance_check_status_counts": dict(sorted(status_counts.items())),
        "priority_symbols": sorted({str(row.get("symbol") or "") for row in queue if row.get("symbol")}),
    }


def external_alignment_measurement_gap_row(item: dict[str, Any]) -> dict[str, Any]:
    row = item["row"]
    compact = compact_outcome(row)
    missing_fields = list(item.get("missing_fields") or [])
    compact["external_alignment_measurement_gap_id"] = external_alignment_measurement_gap_id(row, missing_fields)
    compact["external_alignment_measurement_gap_version"] = EXTERNAL_ALIGNMENT_MEASUREMENT_GAP_VERSION
    compact["external_alignment_review_id"] = external_alignment_review_id(row)
    compact["external_alignment"] = external_alignment_bucket(row)
    compact["external_alignment_review_focus"] = external_alignment_review_focus(row)
    compact["external_alignment_review_label_count"] = int(item.get("review_label_count") or 0)
    compact["external_alignment_measurement_missing_label_count"] = int(item.get("missing_label_count") or 0)
    compact["external_alignment_measurement_missing_fields"] = missing_fields
    compact["external_alignment_measurement_missing_field_counts"] = item.get("missing_field_counts") or {}
    compact["external_alignment_measurement_gap_action"] = external_alignment_measurement_gap_action(row, missing_fields)
    compact["external_alignment_measurement_backfill_policy"] = "decision_time_only"
    compact["external_alignment_measurement_acceptance_checks"] = external_alignment_measurement_acceptance_checks(row, missing_fields)
    if row.get("outcome_id"):
        compact["source_outcome_id"] = row.get("outcome_id")
    if row.get("trial_id"):
        compact["source_trial_id"] = row.get("trial_id")
    if row.get("session"):
        compact["session"] = row.get("session")
    return compact


def external_alignment_measurement_gap_id(row: dict[str, Any], fields: list[str]) -> str:
    return stable_id(
        [
            EXTERNAL_ALIGNMENT_MEASUREMENT_GAP_VERSION,
            row.get("symbol"),
            row.get("horizon"),
            str(row.get("as_of") or "")[:10],
            str(row.get("due_date") or "")[:10],
            external_alignment_bucket(row),
            ",".join(sorted(fields)),
            row.get("outcome_id") or row.get("trial_id") or row.get("trade_action"),
        ]
    )


def external_alignment_measurement_gap_action(row: dict[str, Any], fields: list[str]) -> str:
    symbol = str(row.get("symbol") or "this symbol")
    as_of = str(row.get("as_of") or "the decision date")[:10]
    field_list = ", ".join(field.replace("_", " ") for field in fields) or "missing measurement fields"
    return (
        f"Backfill {field_list} for {symbol} from recommendation-time model and risk inputs "
        f"captured on or before {as_of}; do not use later prices, news, filings, or outcome labels."
    )


def external_alignment_measurement_acceptance_checks(row: dict[str, Any], fields: list[str]) -> list[dict[str, Any]]:
    observation_date = str(row.get("as_of") or "")[:10] or None
    checks = [
        {
            "check": f"{field}_present",
            "field": field,
            "expected": "non_null",
            "status": "pending",
        }
        for field in fields
    ]
    checks.append(
        {
            "check": "decision_time_only",
            "field": "required_measurement_observation_date",
            "expected": f"source inputs captured on or before {observation_date}",
            "status": "pending",
        }
    )
    return checks


def external_alignment_review_id(row: dict[str, Any]) -> str:
    return stable_id(
        [
            EXTERNAL_ALIGNMENT_REVIEW_VERSION,
            row.get("symbol"),
            row.get("horizon"),
            str(row.get("as_of") or "")[:10],
            str(row.get("due_date") or "")[:10],
            external_alignment_bucket(row),
            external_alignment_review_focus(row),
            row.get("outcome_id") or row.get("trial_id") or row.get("trade_action"),
        ]
    )


def pending_external_alignment_review_due_dates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in pending_external_alignment_review_work_items(rows):
        due_date = str(item["row"].get("due_date") or "")[:10]
        if due_date:
            grouped[due_date].append(item)
    schedule = []
    cumulative_label_count = 0
    cumulative_work_item_count = 0
    for due_date in sorted(grouped):
        group = grouped[due_date]
        label_count = sum(int(item.get("label_count") or 0) for item in group)
        work_item_count = len(group)
        cumulative_label_count += label_count
        cumulative_work_item_count += work_item_count
        focus_counts: dict[str, dict[str, int]] = {}
        for item in group:
            row = item["row"]
            focus = external_alignment_review_focus(row)
            if not focus:
                continue
            counts = focus_counts.setdefault(focus, {"label_count": 0, "work_item_count": 0})
            counts["label_count"] += int(item.get("label_count") or 0)
            counts["work_item_count"] += 1
        schedule.append(
            {
                "due_date": due_date,
                "label_count": label_count,
                "work_item_count": work_item_count,
                "cumulative_label_count": cumulative_label_count,
                "cumulative_work_item_count": cumulative_work_item_count,
                "focus_counts": dict(sorted(focus_counts.items())),
                "symbols": sorted({str(item["row"].get("symbol") or "") for item in group if item["row"].get("symbol")}),
                "horizons": sorted({str(item["row"].get("horizon") or "") for item in group if item["row"].get("horizon")}),
            }
        )
    return schedule


def pending_external_alignment_review_work_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in pending_external_alignment_review_candidates(rows):
        alignment = external_alignment_bucket(row)
        key = (
            str(row.get("due_date") or "")[:10],
            str(row.get("symbol") or ""),
            str(row.get("horizon") or ""),
            alignment,
        )
        current = groups.get(key)
        if current is None:
            groups[key] = {"row": row, "rows": [row], "label_count": 1}
            continue
        current["label_count"] += 1
        current.setdefault("rows", []).append(row)
        if external_alignment_review_priority(row) > external_alignment_review_priority(current["row"]):
            current["row"] = row
    return list(groups.values())


def pending_external_alignment_review_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row for row in rows
        if external_alignment_bucket(row) in {"conflict", "engine_neutral", "external_neutral"}
        and row.get("due_date")
    ]


def external_alignment_review_priority(row: dict[str, Any]) -> int:
    alignment = external_alignment_bucket(row)
    base = {
        "conflict": 100,
        "engine_neutral": 80,
        "external_neutral": 60,
    }.get(alignment, 0)
    expected = abs(optional_float(row.get("risk_adjusted_expected_return")) or 0.0)
    return base + min(20, int(expected // 5))


def external_alignment_review_priority_reason(row: dict[str, Any]) -> str:
    alignment = external_alignment_bucket(row)
    focus = external_alignment_review_focus(row).replace("_", " ") or "review"
    expected = optional_float(row.get("risk_adjusted_expected_return"))
    expected_detail = (
        "missing expected return"
        if expected is None
        else f"abs expected return {round(abs(expected), 2)}"
    )
    if alignment == "conflict":
        return f"{focus}: external disagreement is reviewed first; {expected_detail}"
    if alignment == "engine_neutral":
        return f"{focus}: external directional signal may reveal missed sizing; {expected_detail}"
    if alignment == "external_neutral":
        return f"{focus}: internal-only decision validates non-external signals; {expected_detail}"
    return f"{focus}: {expected_detail}"


def external_alignment_review_learning_action(row: dict[str, Any]) -> str:
    alignment = external_alignment_bucket(row)
    if alignment == "conflict":
        return "When the label matures, compare realized direction with the external signal before changing external-signal trust."
    if alignment == "engine_neutral":
        return "When the label matures, test whether the external signal should have promoted a directional size or timing change."
    if alignment == "external_neutral":
        return "When the label matures, test whether internal signal families carried the return without external confirmation."
    return "When the label matures, record whether this alignment bucket improved calibration."


def external_alignment_review_measurement_plan(row: dict[str, Any]) -> dict[str, Any]:
    delta = optional_float(row.get("recommended_delta_weight"))
    direction = optional_int(row.get("direction"))
    if direction is None:
        direction = direction_for(str(row.get("trade_action") or ""), delta or 0.0)
    score = optional_float(row.get("coverage_adjusted_external_signal_score"))
    external_direction = 0 if score is None or score == 0 else (1 if score > 0 else -1)
    expected = optional_float(row.get("risk_adjusted_expected_return"))
    missing = []
    if not row.get("trade_action"):
        missing.append("trade_action")
    if delta is None:
        missing.append("recommended_delta_weight")
    if score is None:
        missing.append("coverage_adjusted_external_signal_score")
    if expected is None:
        missing.append("risk_adjusted_expected_return")
    score_detail = "score missing" if score is None else f"score {round(score, 2)}"
    expected_detail = "expected missing" if expected is None else f"expected {round(expected, 2)}"
    summary = (
        f"engine {direction_label(direction)}; external {direction_label(external_direction)} "
        f"{score_detail}; {expected_detail}"
    )
    return {
        "version": EXTERNAL_ALIGNMENT_MEASUREMENT_PLAN_VERSION,
        "engine_direction": direction_label(direction),
        "external_signal_direction": direction_label(external_direction),
        "recommended_delta_weight": delta,
        "risk_adjusted_expected_return": expected,
        "coverage_adjusted_external_signal_score": score,
        "missing_measurement_fields": missing,
        "summary": summary,
    }


def external_alignment_review_acceptance_checks(row: dict[str, Any]) -> list[dict[str, Any]]:
    focus = external_alignment_review_focus(row)
    action = external_alignment_review_learning_action(row)
    return [
        {
            "check": "source_trace_present",
            "field": "source_outcome_id/source_trial_id",
            "expected": "source outcome and trial ids are present",
            "status": "passed" if row.get("outcome_id") and row.get("trial_id") else "failed",
        },
        {
            "check": "review_focus_present",
            "field": "external_alignment_review_focus",
            "expected": "non_empty_review_focus",
            "status": "passed" if focus else "failed",
        },
        {
            "check": "learning_action_present",
            "field": "external_alignment_review_learning_action",
            "expected": "non_empty_learning_action",
            "status": "passed" if action else "failed",
        },
        {
            "check": "matured_label_available",
            "field": "status",
            "expected": "complete after due date matures",
            "status": "passed" if row.get("status") == "complete" else "pending",
        },
    ]


def external_alignment_review_focus(row: dict[str, Any]) -> str:
    alignment = external_alignment_bucket(row)
    if alignment == "conflict":
        return "external_disagreement"
    if alignment == "engine_neutral":
        return "missed_external_signal"
    if alignment == "external_neutral":
        return "internal_signal_only"
    return ""


def pending_external_alignment_due_dates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        alignment = external_alignment_bucket(row)
        due_date = str(row.get("due_date") or "")[:10]
        if alignment == "unknown" or not due_date:
            continue
        grouped[due_date].append(row)
    schedule = []
    cumulative = 0
    for due_date in sorted(grouped):
        group = grouped[due_date]
        cumulative += len(group)
        counts = {
            "conflict_count": sum(1 for row in group if external_alignment_bucket(row) == "conflict"),
            "aligned_count": sum(1 for row in group if external_alignment_bucket(row) == "aligned"),
            "engine_neutral_count": sum(1 for row in group if external_alignment_bucket(row) == "engine_neutral"),
            "external_neutral_count": sum(1 for row in group if external_alignment_bucket(row) == "external_neutral"),
        }
        schedule.append(
            {
                "due_date": due_date,
                "due_count": len(group),
                "cumulative_due_count": cumulative,
                "symbols": sorted({str(row.get("symbol") or "") for row in group if row.get("symbol")}),
                "horizons": sorted({str(row.get("horizon") or "") for row in group if row.get("horizon")}),
                **counts,
            }
        )
    return schedule


def pending_summary_for_group(label: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    due_dates = sorted(str(row.get("due_date") or "")[:10] for row in rows if row.get("due_date"))
    horizons = sorted({str(row.get("horizon") or "") for row in rows if row.get("horizon")})
    return {
        "key": label,
        "pending_count": len(rows),
        "next_due_date": due_dates[0] if due_dates else None,
        "horizons": horizons,
    }


def external_coverage_band(row: dict[str, Any]) -> str:
    multiplier = optional_float(row.get("external_coverage_multiplier"))
    if multiplier is None:
        return "unknown"
    if multiplier >= 0.95:
        return "full_coverage"
    if multiplier >= 0.50:
        return "partial_coverage"
    return "thin_coverage"


def external_alignment_bucket(row: dict[str, Any]) -> str:
    score = optional_float(row.get("coverage_adjusted_external_signal_score"))
    if score is None:
        return "unknown"
    if abs(score) < 1.0:
        return "external_neutral"
    direction = optional_int(row.get("direction"))
    if direction is None:
        direction = direction_for(str(row.get("trade_action") or ""), float(row.get("recommended_delta_weight") or 0))
    if direction == 0:
        return "engine_neutral"
    external_direction = 1 if score > 0 else -1
    return "aligned" if external_direction == direction else "conflict"


def has_external_observation(row: dict[str, Any]) -> bool:
    feed_status = str(row.get("external_feed_status") or "").strip().lower()
    if feed_status and feed_status != "unknown":
        return True
    for key in ("external_coverage_multiplier", "external_provider_count", "external_signal_count", "external_source_count"):
        if row.get(key) is not None:
            return True
    return False


def external_alignment_sort_key(label: str) -> int:
    return {
        "conflict": 4,
        "aligned": 3,
        "engine_neutral": 2,
        "external_neutral": 1,
        "unknown": 0,
    }.get(label, 0)


def coverage_band_sort_key(label: str) -> int:
    return {
        "full_coverage": 3,
        "partial_coverage": 2,
        "thin_coverage": 1,
        "unknown": 0,
    }.get(label, 0)


def summary_for_group(label: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    returns = [float(row.get("decision_forward_return_pct") or 0) for row in rows]
    summary = {
        "key": label,
        "completed_count": len(rows),
        "hit_rate": round(sum(1 for row in rows if row.get("hit")) / len(rows), 4) if rows else None,
        "average_decision_return": round(mean(returns), 2) if returns else None,
        "average_expected_return": avg(row.get("risk_adjusted_expected_return") for row in rows),
    }
    summary.update(error_summary(rows))
    return summary


def error_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    errors = [
        float(row.get("expected_vs_realized_error") or 0)
        for row in rows
        if row.get("expected_vs_realized_error") is not None
    ]
    if not errors:
        return {
            "mean_error": None,
            "mean_absolute_error": None,
            "underprediction_count": 0,
            "overprediction_count": 0,
        }
    return {
        "mean_error": round(mean(errors), 2),
        "mean_absolute_error": round(mean(abs(error) for error in errors), 2),
        "underprediction_count": sum(1 for error in errors if error > 0),
        "overprediction_count": sum(1 for error in errors if error < 0),
    }


def calibration_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [row for row in rows if row.get("risk_adjusted_expected_return") is not None and row.get("decision_forward_return_pct") is not None]
    if not usable:
        return {
            "status": "insufficient_data",
            "message": "No matured expected-vs-realized labels yet.",
            "sample_count": 0,
            "mean_error": None,
            "mean_absolute_error": None,
            "underprediction_count": 0,
            "overprediction_count": 0,
            "minimum_calibration_samples": MIN_CALIBRATION_SAMPLES,
            "additional_samples_needed": MIN_CALIBRATION_SAMPLES,
            "calibration_ready": False,
            "priority_bucket": {},
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
    errors = error_summary(usable)
    return {
        "status": "available",
        "sample_count": len(usable),
        "mean_error": errors["mean_error"],
        "mean_absolute_error": errors["mean_absolute_error"],
        "underprediction_count": errors["underprediction_count"],
        "overprediction_count": errors["overprediction_count"],
        "minimum_calibration_samples": MIN_CALIBRATION_SAMPLES,
        "additional_samples_needed": max(0, MIN_CALIBRATION_SAMPLES - len(usable)),
        "calibration_ready": len(usable) >= MIN_CALIBRATION_SAMPLES,
        "priority_bucket": priority_calibration_bucket(bucket_rows),
        "buckets": bucket_rows,
        "message": "Positive mean error means realized decision returns beat expected returns.",
    }


def priority_calibration_bucket(bucket_rows: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [row for row in bucket_rows if row.get("mean_absolute_error") is not None]
    if not candidates:
        return {}
    bucket = max(candidates, key=lambda row: (float(row.get("mean_absolute_error") or 0), int(row.get("completed_count") or 0)))
    mean_error = bucket.get("mean_error")
    if mean_error is None:
        bias = "unknown"
    elif float(mean_error) > 0:
        bias = "underprediction"
    elif float(mean_error) < 0:
        bias = "overprediction"
    else:
        bias = "balanced"
    return {
        "key": bucket.get("key"),
        "completed_count": bucket.get("completed_count", 0),
        "mean_error": mean_error,
        "mean_absolute_error": bucket.get("mean_absolute_error"),
        "bias": bias,
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
        "coverage_adjusted_external_signal_score",
        "external_coverage_multiplier",
        "external_feed_status",
        "external_alignment",
    ]
    compact = {key: row.get(key) for key in keys if key in row}
    reason = external_alignment_review_reason(row)
    if reason:
        compact["external_alignment_review_reason"] = reason
    return compact


def external_alignment_review_reason(row: dict[str, Any]) -> str:
    alignment = str(row.get("external_alignment") or external_alignment_bucket(row))
    if alignment == "conflict":
        return "External signal disagrees with the action direction; inspect this label before trusting the signal family."
    if alignment == "aligned":
        return "External signal reinforces the action direction; use the matured label as a confirmation sample."
    if alignment == "engine_neutral":
        return "External signal is directional while the engine stayed neutral; test for a missed sizing or timing signal."
    if alignment == "external_neutral":
        return "Engine action is directional while the external signal is neutral; test whether internal signals carried the label."
    return ""


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


def direction_label(direction: int | None) -> str:
    if direction is None:
        return "unknown"
    if direction > 0:
        return "positive"
    if direction < 0:
        return "negative"
    return "neutral"


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


def optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def optional_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


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
