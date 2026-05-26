from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .audit import WEAK_SOURCE_STATUSES
from .backtest import BACKTEST_VERSION, estimated_label_due_date, pending_external_coverage_gap_count
from .instrumentation import INSTRUMENTATION_AUDIT_VERSION, build_instrumentation_audit
from .util import parse_date


def public_snapshot_quality_failures(web_dir: Path = Path("web")) -> list[str]:
    snapshot_path = web_dir / "data" / "latest.json"
    if not snapshot_path.exists():
        return [f"{snapshot_path}: missing public snapshot"]
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{snapshot_path}: invalid JSON: {exc}"]
    if not isinstance(payload, dict):
        return [f"{snapshot_path}: expected JSON object"]

    failures: list[str] = []
    embedded = payload.get("instrumentation_audit") or {}
    embedded_version = embedded.get("version")
    if embedded_version != INSTRUMENTATION_AUDIT_VERSION:
        failures.append(
            f"{snapshot_path}: stale instrumentation audit version "
            f"{embedded_version or 'missing'}; expected {INSTRUMENTATION_AUDIT_VERSION}"
        )

    recomputed = build_instrumentation_audit(payload)
    if recomputed.get("status") != "ok":
        failures.append(f"{snapshot_path}: {instrumentation_failure_summary(recomputed)}")
    failures.extend(public_snapshot_semantic_failures(payload, snapshot_path))
    return failures


def public_snapshot_semantic_failures(payload: dict[str, Any], snapshot_path: Path) -> list[str]:
    failures: list[str] = []
    backtest = payload.get("backtest") or {}
    pending_count = int_value(backtest.get("pending_outcome_count"))
    if not pending_count:
        pending_count = sum(
            1
            for row in backtest.get("outcomes") or []
            if isinstance(row, dict) and row.get("status") == "pending"
        )
    failures.extend(source_health_consistency_failures(payload, snapshot_path))
    failures.extend(backtest_due_date_policy_failures(backtest, snapshot_path))
    if pending_count <= 0:
        return failures

    diagnostics = payload.get("outcome_diagnostics") or {}
    schedule = diagnostics.get("pending_label_schedule") if isinstance(diagnostics, dict) else {}
    projection = diagnostics.get("learning_readiness_projection") if isinstance(diagnostics, dict) else {}
    maturity = diagnostics.get("label_maturity") if isinstance(diagnostics, dict) else {}
    horizon_counts = diagnostics.get("horizon_label_counts") if isinstance(diagnostics, dict) else []
    external_projection = diagnostics.get("external_learning_readiness_projection") if isinstance(diagnostics, dict) else {}
    schedule = schedule if isinstance(schedule, dict) else {}
    projection = projection if isinstance(projection, dict) else {}
    maturity = maturity if isinstance(maturity, dict) else {}
    horizon_counts = horizon_counts if isinstance(horizon_counts, list) else []
    external_projection = external_projection if isinstance(external_projection, dict) else {}

    if int_value(schedule.get("pending_label_count")) <= 0:
        failures.append(f"{snapshot_path}: pending outcomes require outcome_diagnostics.pending_label_schedule")
    if sum(int_value((row or {}).get("pending_count")) for row in horizon_counts if isinstance(row, dict)) <= 0:
        failures.append(f"{snapshot_path}: pending outcomes require outcome_diagnostics.horizon_label_counts")
    failures.extend(pending_external_summary_failures(backtest, pending_count, snapshot_path))
    failures.extend(pending_external_alignment_watchlist_failures(backtest, snapshot_path))
    failures.extend(external_learning_projection_failures(backtest, external_projection, snapshot_path))
    failures.extend(external_coverage_gap_queue_failures(backtest, external_projection, snapshot_path))
    if "pending_learning_labels_needed_for_readiness" not in projection:
        failures.append(f"{snapshot_path}: pending outcomes require outcome_diagnostics.learning_readiness_projection")
    if maturity and not bool(maturity.get("learning_ready")) and int_value(schedule.get("pending_learning_label_count")) > 0:
        if not projection.get("next_learning_label_due_date") and not projection.get("estimated_learning_ready_date"):
            failures.append(f"{snapshot_path}: learning projection must expose the next or estimated learning-ready date")
        failures.extend(learning_gap_detail_failures(payload, projection, snapshot_path))
    return failures


def pending_external_alignment_watchlist_failures(backtest: dict[str, Any], snapshot_path: Path) -> list[str]:
    actionable_alignment_count = 0
    for row in backtest.get("pending_by_external_alignment") or []:
        if not isinstance(row, dict) or row.get("key") == "unknown":
            continue
        actionable_alignment_count += int_value(row.get("pending_count"))
    if actionable_alignment_count <= 0:
        return []
    watchlist = backtest.get("pending_external_alignment_watchlist")
    watchlist = watchlist if isinstance(watchlist, list) else []
    due_dates = backtest.get("pending_external_alignment_due_dates")
    due_dates = due_dates if isinstance(due_dates, list) else []
    due_count = sum(int_value(row.get("due_count")) for row in due_dates if isinstance(row, dict))
    if due_count != actionable_alignment_count:
        return [
            f"{snapshot_path}: backtest.pending_external_alignment_due_dates covers "
            f"{due_count} actionable external alignment labels; expected {actionable_alignment_count}"
        ]
    if not watchlist:
        return [f"{snapshot_path}: pending external alignment buckets require backtest.pending_external_alignment_watchlist"]
    if not all(
        isinstance(row, dict) and row.get("external_alignment_review_reason")
        for row in watchlist
        if isinstance(row, dict) and row.get("external_alignment") != "unknown"
    ):
        return [f"{snapshot_path}: pending external alignment watchlist requires review reasons"]
    if not any(isinstance(row, dict) and row.get("external_alignment") == "conflict" for row in watchlist):
        conflict_count = sum(
            int_value(row.get("pending_count"))
            for row in backtest.get("pending_by_external_alignment") or []
            if isinstance(row, dict) and row.get("key") == "conflict"
        )
        if conflict_count > 0:
            return [f"{snapshot_path}: pending external alignment watchlist must include conflict examples"]
    reviewable_count = sum(
        int_value(row.get("pending_count"))
        for row in backtest.get("pending_by_external_alignment") or []
        if isinstance(row, dict) and row.get("key") in {"conflict", "engine_neutral", "external_neutral"}
    )
    if reviewable_count <= 0:
        return []
    review_count = int_value(backtest.get("pending_external_alignment_review_count"))
    if review_count != reviewable_count:
        return [
            f"{snapshot_path}: backtest.pending_external_alignment_review_count is "
            f"{review_count}; expected {reviewable_count}"
        ]
    review_item_count = int_value(backtest.get("pending_external_alignment_review_item_count"))
    review_queue = backtest.get("pending_external_alignment_review_queue")
    review_queue = review_queue if isinstance(review_queue, list) else []
    if review_item_count <= 0 or not review_queue:
        return [f"{snapshot_path}: non-confirming pending external alignment labels require backtest.pending_external_alignment_review_queue"]
    hidden_item_count = int_value(backtest.get("pending_external_alignment_review_hidden_item_count"))
    expected_hidden_count = max(0, review_item_count - len(review_queue))
    if hidden_item_count != expected_hidden_count:
        return [
            f"{snapshot_path}: backtest.pending_external_alignment_review_hidden_item_count is "
            f"{hidden_item_count}; expected {expected_hidden_count}"
        ]
    acceptance_summary = backtest.get("pending_external_alignment_review_acceptance_summary")
    if not isinstance(acceptance_summary, dict) or not acceptance_summary:
        return [f"{snapshot_path}: non-confirming pending external alignment labels require backtest.pending_external_alignment_review_acceptance_summary"]
    if (
        int_value(acceptance_summary.get("label_count")) != review_count
        or int_value(acceptance_summary.get("work_item_count")) != review_item_count
    ):
        return [
            f"{snapshot_path}: backtest.pending_external_alignment_review_acceptance_summary covers "
            f"{int_value(acceptance_summary.get('label_count'))} labels/"
            f"{int_value(acceptance_summary.get('work_item_count'))} work items; expected "
            f"{review_count} labels/{review_item_count} work items"
        ]
    open_counts = acceptance_summary.get("open_check_counts")
    open_counts = open_counts if isinstance(open_counts, dict) else {}
    if sum(int_value(value) for value in open_counts.values()) != int_value(acceptance_summary.get("open_check_count")):
        return [f"{snapshot_path}: pending external alignment review acceptance summary open check counts must sum to open_check_count"]
    open_due_dates = acceptance_summary.get("open_check_due_dates")
    open_due_dates = open_due_dates if isinstance(open_due_dates, list) else []
    if int_value(acceptance_summary.get("open_check_count")) > 0:
        due_open_count = sum(int_value(row.get("open_check_count")) for row in open_due_dates if isinstance(row, dict))
        due_label_count = sum(int_value(row.get("label_count")) for row in open_due_dates if isinstance(row, dict))
        if due_open_count != int_value(acceptance_summary.get("open_check_count")):
            return [
                f"{snapshot_path}: pending external alignment review acceptance summary due dates cover "
                f"{due_open_count} open checks; expected {int_value(acceptance_summary.get('open_check_count'))}"
            ]
        if due_label_count != int_value(acceptance_summary.get("open_label_count")):
            return [
                f"{snapshot_path}: pending external alignment review acceptance summary due dates cover "
                f"{due_label_count} open labels; expected {int_value(acceptance_summary.get('open_label_count'))}"
            ]
        next_due = open_due_dates[0].get("due_date") if open_due_dates and isinstance(open_due_dates[0], dict) else None
        if acceptance_summary.get("next_open_check_due_date") != next_due:
            return [f"{snapshot_path}: pending external alignment review acceptance summary next due date must match first due-date bucket"]
        first_due = open_due_dates[0] if open_due_dates and isinstance(open_due_dates[0], dict) else {}
        if (
            int_value(acceptance_summary.get("next_open_check_due_open_check_count")) != int_value(first_due.get("open_check_count"))
            or int_value(acceptance_summary.get("next_open_check_due_label_count")) != int_value(first_due.get("label_count"))
            or int_value(acceptance_summary.get("next_open_check_due_work_item_count")) != int_value(first_due.get("work_item_count"))
        ):
            return [f"{snapshot_path}: pending external alignment review acceptance summary next due counts must match first due-date bucket"]
        first_symbols = first_due.get("symbols") if isinstance(first_due.get("symbols"), list) else []
        first_horizons = first_due.get("horizons") if isinstance(first_due.get("horizons"), list) else []
        if (
            acceptance_summary.get("next_open_check_due_symbols") != first_symbols
            or acceptance_summary.get("next_open_check_due_horizons") != first_horizons
        ):
            return [f"{snapshot_path}: pending external alignment review acceptance summary next due symbols and horizons must match first due-date bucket"]
        first_focus_counts = first_due.get("focus_counts") if isinstance(first_due.get("focus_counts"), dict) else {}
        if acceptance_summary.get("next_open_check_due_focus_counts") != first_focus_counts:
            return [f"{snapshot_path}: pending external alignment review acceptance summary next due focus counts must match first due-date bucket"]
        first_action_counts = first_due.get("learning_action_counts") if isinstance(first_due.get("learning_action_counts"), dict) else {}
        if acceptance_summary.get("next_open_check_due_learning_action_counts") != first_action_counts:
            return [f"{snapshot_path}: pending external alignment review acceptance summary next due learning actions must match first due-date bucket"]
        first_missing_counts = (
            first_due.get("measurement_missing_field_counts")
            if isinstance(first_due.get("measurement_missing_field_counts"), dict)
            else {}
        )
        if acceptance_summary.get("next_open_check_due_measurement_missing_field_counts") != first_missing_counts:
            return [f"{snapshot_path}: pending external alignment review acceptance summary next due measurement gaps must match first due-date bucket"]
        visible_due_items = sum(
            1
            for row in review_queue
            if isinstance(row, dict) and str(row.get("due_date") or "")[:10] == str(next_due or "")
        )
        hidden_due_items = max(0, int_value(first_due.get("work_item_count")) - visible_due_items)
        if (
            int_value(acceptance_summary.get("next_open_check_due_visible_work_item_count")) != visible_due_items
            or int_value(acceptance_summary.get("next_open_check_due_hidden_work_item_count")) != hidden_due_items
        ):
            return [f"{snapshot_path}: pending external alignment review acceptance summary next due queue coverage must match visible review queue"]
        if bool(acceptance_summary.get("next_open_check_due_fully_visible")) != (hidden_due_items == 0):
            return [f"{snapshot_path}: pending external alignment review acceptance summary next due queue visibility flag is inconsistent"]
        if hidden_due_items:
            return [f"{snapshot_path}: pending external alignment review queue must include every work item in the next due bucket"]
        for row in open_due_dates:
            if not isinstance(row, dict):
                continue
            focus_counts = row.get("focus_counts") if isinstance(row.get("focus_counts"), dict) else {}
            focus_label_count = sum(
                int_value(counts.get("label_count"))
                for counts in focus_counts.values()
                if isinstance(counts, dict)
            )
            focus_work_item_count = sum(
                int_value(counts.get("work_item_count"))
                for counts in focus_counts.values()
                if isinstance(counts, dict)
            )
            if focus_label_count != int_value(row.get("label_count")) or focus_work_item_count != int_value(row.get("work_item_count")):
                return [f"{snapshot_path}: pending external alignment review acceptance summary due-date focus counts must cover labels and work items"]
            action_counts = row.get("learning_action_counts") if isinstance(row.get("learning_action_counts"), dict) else {}
            action_label_count = sum(
                int_value(counts.get("label_count"))
                for counts in action_counts.values()
                if isinstance(counts, dict)
            )
            action_work_item_count = sum(
                int_value(counts.get("work_item_count"))
                for counts in action_counts.values()
                if isinstance(counts, dict)
            )
            if action_label_count != int_value(row.get("label_count")) or action_work_item_count != int_value(row.get("work_item_count")):
                return [f"{snapshot_path}: pending external alignment review acceptance summary due-date learning action counts must cover labels and work items"]
            measurement_missing_counts = (
                row.get("measurement_missing_field_counts")
                if isinstance(row.get("measurement_missing_field_counts"), dict)
                else {}
            )
            for field, counts in measurement_missing_counts.items():
                if (
                    not field
                    or not isinstance(counts, dict)
                    or int_value(counts.get("label_count")) <= 0
                    or int_value(counts.get("work_item_count")) <= 0
                    or int_value(counts.get("label_count")) > int_value(row.get("label_count"))
                    or int_value(counts.get("work_item_count")) > int_value(row.get("work_item_count"))
                ):
                    return [
                        f"{snapshot_path}: pending external alignment review acceptance summary "
                        "due-date measurement gap counts must be positive and within bucket counts"
                    ]
    if not all(
        isinstance(row, dict)
        and row.get("external_alignment_review_id")
        and row.get("external_alignment") in {"conflict", "engine_neutral", "external_neutral"}
        and row.get("external_alignment_review_reason")
        and row.get("external_alignment_review_focus")
        and row.get("external_alignment_review_priority_reason")
        and row.get("external_alignment_review_learning_action")
        and isinstance(row.get("external_alignment_review_measurement_plan"), dict)
        and row.get("external_alignment_review_measurement_plan", {}).get("summary")
        and isinstance(row.get("external_alignment_review_measurement_plan", {}).get("missing_measurement_fields"), list)
        and isinstance(row.get("external_alignment_review_acceptance_checks"), list)
        and row.get("external_alignment_review_acceptance_checks")
        and "external_alignment_review_open_check_count" in row
        and int_value(row.get("external_alignment_review_label_count")) > 0
        and row.get("source_outcome_id")
        and row.get("source_trial_id")
        for row in review_queue
    ):
        return [
            f"{snapshot_path}: pending external alignment review queue requires "
            "review ids, focus, priority reasons, learning actions, measurement plans, acceptance checks, label count, review reasons, and source ids"
        ]
    for row in review_queue:
        if not isinstance(row, dict):
            continue
        checks = row.get("external_alignment_review_acceptance_checks") or []
        if not all(isinstance(check, dict) and check.get("check") and check.get("status") for check in checks):
            return [f"{snapshot_path}: pending external alignment review acceptance checks require names and statuses"]
        open_check_count = sum(1 for check in checks if isinstance(check, dict) and check.get("status") != "passed")
        if int_value(row.get("external_alignment_review_open_check_count")) != open_check_count:
            return [
                f"{snapshot_path}: pending external alignment review open check count is "
                f"{int_value(row.get('external_alignment_review_open_check_count'))}; expected {open_check_count}"
            ]
    review_queue_ids = [row.get("external_alignment_review_id") for row in review_queue if isinstance(row, dict)]
    if len(set(review_queue_ids)) != len(review_queue_ids):
        return [f"{snapshot_path}: pending external alignment review queue requires unique review ids"]
    next_measurement_missing_counts = (
        acceptance_summary.get("next_open_check_due_measurement_missing_field_counts")
        if isinstance(acceptance_summary.get("next_open_check_due_measurement_missing_field_counts"), dict)
        else {}
    )
    if next_measurement_missing_counts:
        measurement_gap_plan = backtest.get("pending_external_alignment_measurement_gap_plan")
        measurement_gap_queue = backtest.get("pending_external_alignment_measurement_gap_queue")
        measurement_gap_queue = measurement_gap_queue if isinstance(measurement_gap_queue, list) else []
        if not isinstance(measurement_gap_plan, dict) or not measurement_gap_plan or not measurement_gap_queue:
            return [
                f"{snapshot_path}: pending external alignment measurement gaps require "
                "backtest.pending_external_alignment_measurement_gap_plan and queue"
            ]
        if measurement_gap_plan.get("next_due_date") != acceptance_summary.get("next_open_check_due_date"):
            return [f"{snapshot_path}: pending external alignment measurement gap plan next due date must match review blocker due date"]
        if measurement_gap_plan.get("next_due_field_counts") != next_measurement_missing_counts:
            return [f"{snapshot_path}: pending external alignment measurement gap plan next due field counts must match review acceptance summary"]
        gap_item_count = int_value(backtest.get("pending_external_alignment_measurement_gap_item_count"))
        gap_hidden_count = int_value(backtest.get("pending_external_alignment_measurement_gap_hidden_item_count"))
        if gap_hidden_count != max(0, gap_item_count - len(measurement_gap_queue)):
            return [f"{snapshot_path}: pending external alignment measurement gap hidden count must match visible queue"]
        next_due = str(measurement_gap_plan.get("next_due_date") or "")
        next_due_visible = sum(
            1
            for row in measurement_gap_queue
            if isinstance(row, dict) and str(row.get("due_date") or "")[:10] == next_due
        )
        if next_due_visible != int_value(measurement_gap_plan.get("next_due_work_item_count")):
            return [f"{snapshot_path}: pending external alignment measurement gap queue must include every work item in the next due bucket"]
        if not all(
            isinstance(row, dict)
            and row.get("external_alignment_measurement_gap_id")
            and row.get("external_alignment_measurement_gap_version")
            and row.get("external_alignment_review_id")
            and isinstance(row.get("external_alignment_measurement_missing_fields"), list)
            and row.get("external_alignment_measurement_missing_fields")
            and isinstance(row.get("external_alignment_measurement_missing_field_counts"), dict)
            and int_value(row.get("external_alignment_measurement_missing_label_count")) > 0
            and row.get("external_alignment_measurement_gap_action")
            and row.get("external_alignment_measurement_backfill_policy") == "decision_time_only"
            and isinstance(row.get("external_alignment_measurement_acceptance_checks"), list)
            and row.get("external_alignment_measurement_acceptance_checks")
            for row in measurement_gap_queue
        ):
            return [
                f"{snapshot_path}: pending external alignment measurement gap queue requires "
                "ids, review ids, missing fields, decision-time actions, acceptance checks, and label counts"
            ]
        for row in measurement_gap_queue:
            checks = row.get("external_alignment_measurement_acceptance_checks") or []
            if not all(isinstance(check, dict) and check.get("check") and check.get("status") for check in checks):
                return [f"{snapshot_path}: pending external alignment measurement gap acceptance checks require names and statuses"]
        gap_ids = [row.get("external_alignment_measurement_gap_id") for row in measurement_gap_queue if isinstance(row, dict)]
        if len(set(gap_ids)) != len(gap_ids):
            return [f"{snapshot_path}: pending external alignment measurement gap queue requires unique gap ids"]
    review_due_dates = backtest.get("pending_external_alignment_review_due_dates")
    review_due_dates = review_due_dates if isinstance(review_due_dates, list) else []
    due_label_count = sum(int_value(row.get("label_count")) for row in review_due_dates if isinstance(row, dict))
    due_work_item_count = sum(int_value(row.get("work_item_count")) for row in review_due_dates if isinstance(row, dict))
    if due_label_count != review_count or due_work_item_count != review_item_count:
        return [
            f"{snapshot_path}: backtest.pending_external_alignment_review_due_dates covers "
            f"{due_label_count} labels/{due_work_item_count} work items; expected "
            f"{review_count} labels/{review_item_count} work items"
        ]
    return []


def external_learning_projection_failures(
    backtest: dict[str, Any],
    projection: dict[str, Any],
    snapshot_path: Path,
) -> list[str]:
    external_pending = externally_observed_pending_count(backtest)
    external_fast_pending = externally_observed_pending_count(backtest, horizon="5d")
    if external_pending <= 0 and external_fast_pending <= 0:
        return []
    failures: list[str] = []
    projected_pending = int_value(projection.get("pending_external_learning_label_count"))
    if external_pending > 0 and projected_pending <= 0:
        failures.append(
            f"{snapshot_path}: externally covered pending outcomes require "
            "outcome_diagnostics.external_learning_readiness_projection"
        )
    elif external_pending > 0 and projected_pending != external_pending:
        failures.append(
            f"{snapshot_path}: outcome_diagnostics.external_learning_readiness_projection covers "
            f"{projected_pending} external learning labels; expected {external_pending}"
        )
    projected_fast_pending = int_value(projection.get("pending_external_fast_label_count"))
    if external_fast_pending > 0 and projected_fast_pending != external_fast_pending:
        failures.append(
            f"{snapshot_path}: outcome_diagnostics.external_learning_readiness_projection covers "
            f"{projected_fast_pending} external fast-check labels; expected {external_fast_pending}"
        )
    return failures


def external_coverage_gap_queue_failures(
    backtest: dict[str, Any],
    projection: dict[str, Any],
    snapshot_path: Path,
) -> list[str]:
    gap_count = pending_external_coverage_gap_count(
        [row for row in backtest.get("outcomes") or [] if isinstance(row, dict)]
    )
    if gap_count <= 0:
        return []
    reported_count = int_value(backtest.get("pending_external_coverage_gap_count"))
    if reported_count != gap_count:
        return [
            f"{snapshot_path}: backtest.pending_external_coverage_gap_count is "
            f"{reported_count}; expected {gap_count}"
        ]
    shortfall = int_value(projection.get("projected_external_additional_needed_all_scheduled"))
    if shortfall <= 0:
        return []
    queue = backtest.get("pending_external_coverage_gap_queue")
    queue = queue if isinstance(queue, list) else []
    if not queue:
        return [
            f"{snapshot_path}: external learning shortfall requires "
            "backtest.pending_external_coverage_gap_queue"
        ]
    if not all(isinstance(row, dict) and row.get("external_coverage_gap_reason") for row in queue):
        return [f"{snapshot_path}: pending external coverage gap queue requires gap reasons"]
    queue_ids = [row.get("external_coverage_gap_id") for row in queue if isinstance(row, dict)]
    if not queue_ids or any(not item for item in queue_ids) or len(set(queue_ids)) != len(queue_ids):
        return [f"{snapshot_path}: pending external coverage gap queue requires unique gap ids"]
    plan = backtest.get("pending_external_coverage_gap_plan")
    if not isinstance(plan, dict) or not plan:
        return [
            f"{snapshot_path}: external learning shortfall requires "
            "backtest.pending_external_coverage_gap_plan"
        ]
    expected_priority = min(shortfall, gap_count)
    if int_value(plan.get("additional_external_coverage_needed")) != shortfall:
        return [
            f"{snapshot_path}: backtest.pending_external_coverage_gap_plan additional need is "
            f"{int_value(plan.get('additional_external_coverage_needed'))}; expected {shortfall}"
        ]
    if int_value(plan.get("candidate_gap_count")) != gap_count:
        return [
            f"{snapshot_path}: backtest.pending_external_coverage_gap_plan candidate count is "
            f"{int_value(plan.get('candidate_gap_count'))}; expected {gap_count}"
        ]
    priority_rows = plan.get("priority_rows")
    priority_rows = priority_rows if isinstance(priority_rows, list) else []
    if int_value(plan.get("priority_gap_count")) != expected_priority or len(priority_rows) != expected_priority:
        return [
            f"{snapshot_path}: backtest.pending_external_coverage_gap_plan priority count must be "
            f"{expected_priority}"
        ]
    if not all(isinstance(row, dict) and row.get("external_coverage_gap_reason") for row in priority_rows):
        return [f"{snapshot_path}: pending external coverage gap plan requires priority gap reasons"]
    priority_ids = [row.get("external_coverage_gap_id") for row in priority_rows if isinstance(row, dict)]
    if not priority_ids or any(not item for item in priority_ids) or len(set(priority_ids)) != len(priority_ids):
        return [f"{snapshot_path}: pending external coverage gap plan requires unique priority gap ids"]
    if not all(
        isinstance(row, dict)
        and row.get("external_coverage_gap_action")
        and row.get("external_coverage_backfill_policy") == "decision_time_only"
        and row.get("required_external_observation_date")
        for row in priority_rows
    ):
        return [f"{snapshot_path}: pending external coverage gap plan requires decision-time backfill instructions"]
    required_checks = {
        "external_feed_status_present",
        "external_coverage_multiplier_present",
        "coverage_adjusted_external_signal_score_present",
        "decision_time_only",
    }
    for row in priority_rows:
        checks = row.get("external_coverage_acceptance_checks") if isinstance(row, dict) else []
        checks = checks if isinstance(checks, list) else []
        check_names = {check.get("check") for check in checks if isinstance(check, dict)}
        if not required_checks.issubset(check_names):
            return [f"{snapshot_path}: pending external coverage gap plan requires acceptance checks"]
        if not all(isinstance(check, dict) and check.get("status") for check in checks):
            return [f"{snapshot_path}: pending external coverage gap plan requires acceptance check statuses"]
    check_total = sum(
        len(row.get("external_coverage_acceptance_checks") or [])
        for row in priority_rows
        if isinstance(row, dict)
    )
    if int_value(plan.get("priority_acceptance_check_count")) != check_total:
        return [
            f"{snapshot_path}: backtest.pending_external_coverage_gap_plan acceptance check count is "
            f"{int_value(plan.get('priority_acceptance_check_count'))}; expected {check_total}"
        ]
    status_counts = plan.get("priority_acceptance_check_status_counts")
    status_counts = status_counts if isinstance(status_counts, dict) else {}
    reported_status_total = sum(int_value(value) for value in status_counts.values())
    if reported_status_total != check_total:
        return [
            f"{snapshot_path}: backtest.pending_external_coverage_gap_plan acceptance status counts cover "
            f"{reported_status_total} checks; expected {check_total}"
        ]
    expected_open = sum(int_value(value) for status, value in status_counts.items() if status != "passed")
    if int_value(plan.get("priority_open_acceptance_check_count")) != expected_open:
        return [
            f"{snapshot_path}: backtest.pending_external_coverage_gap_plan open acceptance check count is "
            f"{int_value(plan.get('priority_open_acceptance_check_count'))}; expected {expected_open}"
        ]
    projected_after_priority = int_value(plan.get("projected_external_long_horizon_count_after_priority_backfill"))
    expected_projected = int_value(plan.get("observed_external_long_horizon_label_count")) + expected_priority
    if projected_after_priority != expected_projected:
        return [
            f"{snapshot_path}: backtest.pending_external_coverage_gap_plan projected count is "
            f"{projected_after_priority}; expected {expected_projected}"
        ]
    projected_needed = int_value(plan.get("projected_external_additional_needed_after_priority_backfill"))
    expected_needed = max(0, int_value(plan.get("minimum_external_long_horizon_required")) - expected_projected)
    if projected_needed != expected_needed:
        return [
            f"{snapshot_path}: backtest.pending_external_coverage_gap_plan post-priority need is "
            f"{projected_needed}; expected {expected_needed}"
        ]
    return []


def externally_observed_pending_count(backtest: dict[str, Any], horizon: str | None = None) -> int:
    count = 0
    for row in backtest.get("outcomes") or []:
        if not isinstance(row, dict) or row.get("status") != "pending":
            continue
        row_horizon = str(row.get("horizon") or "")
        if horizon and row_horizon != horizon:
            continue
        if horizon is None and row_horizon == "5d":
            continue
        feed_status = str(row.get("external_feed_status") or "").strip().lower()
        has_feed_status = bool(feed_status and feed_status != "unknown")
        has_external_value = any(
            row.get(key) is not None
            for key in ("external_coverage_multiplier", "external_provider_count", "external_signal_count", "external_source_count")
        )
        if has_feed_status or has_external_value:
            count += 1
    return count


def pending_external_summary_failures(backtest: dict[str, Any], pending_count: int, snapshot_path: Path) -> list[str]:
    failures: list[str] = []
    checks = [
        ("pending_by_external_feed_status", "backtest.pending_by_external_feed_status"),
        ("pending_by_external_coverage", "backtest.pending_by_external_coverage"),
        ("pending_by_external_alignment", "backtest.pending_by_external_alignment"),
    ]
    for key, label in checks:
        rows = backtest.get(key) if isinstance(backtest, dict) else []
        rows = rows if isinstance(rows, list) else []
        summary_count = sum(int_value((row or {}).get("pending_count")) for row in rows if isinstance(row, dict))
        if summary_count <= 0:
            failures.append(f"{snapshot_path}: pending outcomes require {label}")
        elif pending_count > 0 and summary_count != pending_count:
            failures.append(
                f"{snapshot_path}: {label} covers {summary_count} pending labels; expected {pending_count}"
            )
    return failures


def backtest_due_date_policy_failures(backtest: dict[str, Any], snapshot_path: Path) -> list[str]:
    checked = 0
    mismatches: list[str] = []
    for section in ("outcomes", "recent_pending"):
        for row in backtest.get(section) or []:
            if not isinstance(row, dict) or row.get("status") != "pending":
                continue
            as_of = parse_date(row.get("as_of"))
            horizon = str(row.get("horizon") or "")
            due_date = str(row.get("due_date") or "")[:10]
            if as_of is None or not horizon or not due_date:
                continue
            try:
                expected = estimated_label_due_date(as_of, horizon).isoformat()
            except (KeyError, TypeError, ValueError):
                continue
            checked += 1
            if due_date != expected:
                symbol = str(row.get("symbol") or "unknown")
                mismatches.append(f"{section} {symbol} {horizon} due {due_date} expected {expected}")

    failures: list[str] = []
    if checked and backtest.get("due_date_policy_version") != BACKTEST_VERSION:
        failures.append(
            f"{snapshot_path}: pending backtest labels require due_date_policy_version {BACKTEST_VERSION}"
        )
    if mismatches:
        sample = "; ".join(mismatches[:5])
        suffix = f"; +{len(mismatches) - 5} more" if len(mismatches) > 5 else ""
        failures.append(f"{snapshot_path}: stale pending backtest due dates: {sample}{suffix}")
    return failures


def source_health_consistency_failures(payload: dict[str, Any], snapshot_path: Path) -> list[str]:
    sources = [
        row for row in ((payload.get("data_health") or {}).get("sources") or [])
        if isinstance(row, dict)
    ]
    if not sources:
        return []
    audit = payload.get("audit") or {}
    freshness_rows = [
        row for row in audit.get("source_freshness") or []
        if isinstance(row, dict)
    ]
    source_gaps = [
        row for row in audit.get("data_gaps") or []
        if isinstance(row, dict) and row.get("area") == "source"
    ]
    failures: list[str] = []
    if not freshness_rows:
        failures.append(f"{snapshot_path}: data_health sources require audit.source_freshness")
    freshness_by_source = {
        str(row.get("source") or ""): row
        for row in freshness_rows
        if row.get("source")
    }
    for source in sources:
        source_key = str(source.get("source") or "")
        label = str(source.get("label") or source_key or "unknown source")
        status = str(source.get("status") or "unknown")
        freshness = freshness_by_source.get(source_key)
        if freshness and str(freshness.get("status") or "unknown") != status:
            failures.append(f"{snapshot_path}: audit.source_freshness status mismatch for {label}")
        if status in WEAK_SOURCE_STATUSES and not matching_source_gap(source_gaps, label, status):
            failures.append(f"{snapshot_path}: weak data_health source {label} requires matching audit source gap")
    return failures


def matching_source_gap(source_gaps: list[dict[str, Any]], label: str, status: str) -> bool:
    return any(
        str(row.get("label") or "") == label and str(row.get("status") or "unknown") == status
        for row in source_gaps
    )


def learning_gap_detail_failures(payload: dict[str, Any], projection: dict[str, Any], snapshot_path: Path) -> list[str]:
    learning = ((payload.get("engine") or {}).get("learning") or {})
    if learning.get("status") != "baseline_fallback":
        return []
    gaps = ((payload.get("audit") or {}).get("data_gaps") or [])
    learning_gap = next(
        (
            row for row in gaps
            if isinstance(row, dict) and row.get("area") == "engine" and row.get("label") == "Learning reranker"
        ),
        {},
    )
    detail = str(learning_gap.get("detail") or "")
    if not detail:
        return [f"{snapshot_path}: baseline learning fallback requires a Learning reranker audit gap"]
    expected_dates = [
        str(projection.get(key) or "")
        for key in ("next_learning_label_due_date", "estimated_learning_ready_date")
        if projection.get(key)
    ]
    if expected_dates and not any(date_text in detail for date_text in expected_dates):
        return [f"{snapshot_path}: Learning reranker audit gap must include the projected learning label dates"]
    gap_plan = ((payload.get("backtest") or {}).get("pending_external_coverage_gap_plan") or {})
    priority_rows = gap_plan.get("priority_rows") if isinstance(gap_plan, dict) else []
    priority_rows = priority_rows if isinstance(priority_rows, list) else []
    if priority_rows:
        gap_ids = [str(row.get("external_coverage_gap_id") or "") for row in priority_rows if isinstance(row, dict)]
        if "External coverage priority backfill" not in detail or not any(gap_id and gap_id in detail for gap_id in gap_ids):
            return [f"{snapshot_path}: Learning reranker audit gap must include external coverage priority backfill"]
    return []


def instrumentation_failure_summary(audit: dict[str, Any]) -> str:
    failure_count = int(audit.get("failure_count") or 0)
    names = [
        str(row.get("name") or "unknown_check")
        for row in audit.get("failures") or []
        if isinstance(row, dict)
    ][:8]
    suffix = f": {', '.join(names)}" if names else ""
    return f"instrumentation audit {audit.get('status', 'unknown')} with {failure_count} failures{suffix}"


def int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def assert_public_snapshot_quality(web_dir: Path = Path("web")) -> None:
    failures = public_snapshot_quality_failures(web_dir)
    if failures:
        raise RuntimeError("\n".join(failures))
