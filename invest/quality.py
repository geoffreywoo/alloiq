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
    approval_projection = diagnostics.get("approval_learning_readiness_projection") if isinstance(diagnostics, dict) else {}
    friction_projection = diagnostics.get("approval_data_friction_learning_readiness_projection") if isinstance(diagnostics, dict) else {}
    schedule = schedule if isinstance(schedule, dict) else {}
    projection = projection if isinstance(projection, dict) else {}
    maturity = maturity if isinstance(maturity, dict) else {}
    horizon_counts = horizon_counts if isinstance(horizon_counts, list) else []
    external_projection = external_projection if isinstance(external_projection, dict) else {}
    approval_projection = approval_projection if isinstance(approval_projection, dict) else {}
    friction_projection = friction_projection if isinstance(friction_projection, dict) else {}

    if int_value(schedule.get("pending_label_count")) <= 0:
        failures.append(f"{snapshot_path}: pending outcomes require outcome_diagnostics.pending_label_schedule")
    if sum(int_value((row or {}).get("pending_count")) for row in horizon_counts if isinstance(row, dict)) <= 0:
        failures.append(f"{snapshot_path}: pending outcomes require outcome_diagnostics.horizon_label_counts")
    failures.extend(pending_external_summary_failures(backtest, pending_count, snapshot_path))
    failures.extend(pending_approval_summary_failures(backtest, pending_count, snapshot_path))
    failures.extend(pending_external_alignment_watchlist_failures(backtest, snapshot_path))
    failures.extend(external_learning_projection_failures(backtest, external_projection, snapshot_path))
    failures.extend(approval_learning_projection_failures(backtest, approval_projection, snapshot_path))
    failures.extend(approval_data_friction_projection_failures(backtest, friction_projection, snapshot_path))
    failures.extend(external_coverage_gap_queue_failures(backtest, external_projection, snapshot_path))
    failures.extend(external_provider_gap_severity_observation_gap_queue_failures(backtest, snapshot_path))
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


def approval_learning_projection_failures(
    backtest: dict[str, Any],
    projection: dict[str, Any],
    snapshot_path: Path,
) -> list[str]:
    counts = approval_blocked_pending_counts(backtest)
    expected_total = counts["total"]
    if expected_total <= 0:
        expected_total = approval_blocked_summary_count(backtest)
        if expected_total <= 0:
            return []
    failures: list[str] = []
    projected_total = int_value(projection.get("pending_approval_label_count"))
    if projected_total <= 0:
        failures.append(
            f"{snapshot_path}: approval-gated pending outcomes require "
            "outcome_diagnostics.approval_learning_readiness_projection"
        )
        return failures
    if projected_total != expected_total:
        failures.append(
            f"{snapshot_path}: outcome_diagnostics.approval_learning_readiness_projection covers "
            f"{projected_total} approval-gated labels; expected {expected_total}"
        )
    if counts["total"] <= 0:
        return failures
    projected_learning = int_value(projection.get("pending_approval_learning_label_count"))
    projected_fast = int_value(projection.get("pending_approval_fast_label_count"))
    if projected_learning != counts["learning"]:
        failures.append(
            f"{snapshot_path}: outcome_diagnostics.approval_learning_readiness_projection covers "
            f"{projected_learning} approval-gated learning labels; expected {counts['learning']}"
        )
    if projected_fast != counts["fast"]:
        failures.append(
            f"{snapshot_path}: outcome_diagnostics.approval_learning_readiness_projection covers "
            f"{projected_fast} approval-gated fast-check labels; expected {counts['fast']}"
        )
    bucket_rows = projection.get("pending_approval_blocker_buckets")
    bucket_rows = bucket_rows if isinstance(bucket_rows, list) else []
    projected_bucket_total = sum(
        int_value(row.get("pending_count"))
        for row in bucket_rows
        if isinstance(row, dict) and approval_bucket_is_blocking(row.get("key"))
    )
    if projected_bucket_total != counts["total"]:
        failures.append(
            f"{snapshot_path}: outcome_diagnostics.approval_learning_readiness_projection blocker buckets cover "
            f"{projected_bucket_total} approval-gated labels; expected {counts['total']}"
        )
    return failures


def approval_data_friction_projection_failures(
    backtest: dict[str, Any],
    projection: dict[str, Any],
    snapshot_path: Path,
) -> list[str]:
    counts = approval_data_friction_pending_counts(backtest)
    expected_total = counts["total"]
    if expected_total <= 0:
        expected_total = approval_data_friction_summary_count(backtest)
        if expected_total <= 0:
            return []
    failures: list[str] = []
    projected_total = int_value(projection.get("pending_approval_data_friction_label_count"))
    if projected_total <= 0:
        failures.append(
            f"{snapshot_path}: approval data-friction pending outcomes require "
            "outcome_diagnostics.approval_data_friction_learning_readiness_projection"
        )
        return failures
    if projected_total != expected_total:
        failures.append(
            f"{snapshot_path}: outcome_diagnostics.approval_data_friction_learning_readiness_projection covers "
            f"{projected_total} approval data-friction labels; expected {expected_total}"
        )
    if counts["total"] <= 0:
        return failures
    projected_learning = int_value(projection.get("pending_approval_data_friction_learning_label_count"))
    projected_fast = int_value(projection.get("pending_approval_data_friction_fast_label_count"))
    if projected_learning != counts["learning"]:
        failures.append(
            f"{snapshot_path}: outcome_diagnostics.approval_data_friction_learning_readiness_projection covers "
            f"{projected_learning} approval data-friction learning labels; expected {counts['learning']}"
        )
    if projected_fast != counts["fast"]:
        failures.append(
            f"{snapshot_path}: outcome_diagnostics.approval_data_friction_learning_readiness_projection covers "
            f"{projected_fast} approval data-friction fast-check labels; expected {counts['fast']}"
        )
    bucket_rows = projection.get("pending_approval_data_friction_buckets")
    bucket_rows = bucket_rows if isinstance(bucket_rows, list) else []
    projected_bucket_total = sum(
        int_value(row.get("pending_count"))
        for row in bucket_rows
        if isinstance(row, dict) and approval_data_friction_bucket_is_actionable(row.get("key"))
    )
    if projected_bucket_total != counts["total"]:
        failures.append(
            f"{snapshot_path}: outcome_diagnostics.approval_data_friction_learning_readiness_projection buckets cover "
            f"{projected_bucket_total} approval data-friction labels; expected {counts['total']}"
        )
    return failures


def approval_data_friction_pending_counts(backtest: dict[str, Any]) -> dict[str, int]:
    counts = {"total": 0, "learning": 0, "fast": 0}
    for row in backtest.get("outcomes") or []:
        if not isinstance(row, dict) or row.get("status") != "pending":
            continue
        if not approval_data_friction_bucket_is_actionable(row.get("approval_data_friction_bucket")):
            continue
        counts["total"] += 1
        if str(row.get("horizon") or "") == "5d":
            counts["fast"] += 1
        else:
            counts["learning"] += 1
    return counts


def approval_data_friction_summary_count(backtest: dict[str, Any]) -> int:
    return sum(
        int_value(row.get("pending_count"))
        for row in backtest.get("pending_by_approval_data_friction_bucket") or []
        if isinstance(row, dict) and approval_data_friction_bucket_is_actionable(row.get("key"))
    )


def approval_data_friction_bucket_is_actionable(bucket: Any) -> bool:
    return str(bucket or "").strip().lower() not in {"", "unknown", "clear", "no_friction", "none"}


def approval_blocked_pending_counts(backtest: dict[str, Any]) -> dict[str, int]:
    counts = {"total": 0, "learning": 0, "fast": 0}
    for row in backtest.get("outcomes") or []:
        if not isinstance(row, dict) or row.get("status") != "pending":
            continue
        if not approval_bucket_is_blocking(approval_blocker_bucket(row)):
            continue
        counts["total"] += 1
        if str(row.get("horizon") or "") == "5d":
            counts["fast"] += 1
        else:
            counts["learning"] += 1
    return counts


def approval_blocked_summary_count(backtest: dict[str, Any]) -> int:
    return sum(
        int_value(row.get("pending_count"))
        for row in backtest.get("pending_by_approval_blocker_bucket") or []
        if isinstance(row, dict) and approval_bucket_is_blocking(row.get("key"))
    )


def approval_blocker_bucket(row: dict[str, Any]) -> str:
    blocker_bucket = str(row.get("approval_blocker_bucket") or "").strip().lower()
    if blocker_bucket:
        return blocker_bucket
    gate_status = str(row.get("approval_gate_status") or "").strip().lower()
    if gate_status:
        return gate_status
    if row.get("approval_required"):
        return "approval_required_unknown"
    return "no_approval_context"


def approval_bucket_is_blocking(bucket: Any) -> bool:
    return str(bucket or "").strip().lower() not in {"", "unknown", "no_approval_context", "ready"}


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


def external_provider_gap_severity_observation_gap_queue_failures(
    backtest: dict[str, Any],
    snapshot_path: Path,
) -> list[str]:
    summary = backtest.get("pending_external_provider_gap_severity_observation_summary")
    summary = summary if isinstance(summary, dict) else {}
    unknown_count = int_value(summary.get("unknown_label_count"))
    if unknown_count <= 0:
        unknown_count = sum(
            int_value(row.get("pending_count"))
            for row in backtest.get("pending_by_external_provider_gap_severity") or []
            if isinstance(row, dict) and str(row.get("key") or "").strip().lower() == "unknown"
        )
    if unknown_count <= 0:
        return []
    if not summary:
        return [
            f"{snapshot_path}: unknown provider gap severity labels require "
            "backtest.pending_external_provider_gap_severity_observation_summary"
        ]
    if int_value(summary.get("pending_label_count")) <= 0 or int_value(summary.get("observed_label_count")) < 0:
        return [
            f"{snapshot_path}: pending provider gap severity observation summary must expose label counts"
        ]
    reported_count = int_value(backtest.get("pending_external_provider_gap_severity_observation_gap_count"))
    if reported_count != unknown_count:
        return [
            f"{snapshot_path}: backtest.pending_external_provider_gap_severity_observation_gap_count is "
            f"{reported_count}; expected {unknown_count}"
        ]
    queue = backtest.get("pending_external_provider_gap_severity_observation_gap_queue")
    queue = queue if isinstance(queue, list) else []
    if not queue:
        return [
            f"{snapshot_path}: unknown provider gap severity labels require "
            "backtest.pending_external_provider_gap_severity_observation_gap_queue"
        ]
    queue_limit = int_value(backtest.get("pending_external_provider_gap_severity_observation_gap_queue_limit"))
    if queue_limit > 0 and len(queue) != min(unknown_count, queue_limit):
        return [
            f"{snapshot_path}: provider gap severity observation queue has {len(queue)} rows; "
            f"expected {min(unknown_count, queue_limit)}"
        ]
    hidden_count = int_value(backtest.get("pending_external_provider_gap_severity_observation_gap_hidden_label_count"))
    expected_hidden_count = max(0, unknown_count - len(queue))
    if hidden_count != expected_hidden_count:
        return [
            f"{snapshot_path}: provider gap severity observation hidden label count is "
            f"{hidden_count}; expected {expected_hidden_count}"
        ]
    queue_ids = [
        row.get("external_provider_gap_severity_observation_gap_id")
        for row in queue
        if isinstance(row, dict)
    ]
    if not queue_ids or any(not item for item in queue_ids) or len(set(queue_ids)) != len(queue_ids):
        return [f"{snapshot_path}: provider gap severity observation queue requires unique gap ids"]
    required_fields = {
        "external_provider_gap_count",
        "external_provider_primary_gap_severity",
        "external_provider_gap_severity_score",
    }
    for row in queue:
        if not isinstance(row, dict):
            return [f"{snapshot_path}: provider gap severity observation queue rows must be objects"]
        fields = set(row.get("minimum_external_provider_gap_severity_fields_to_backfill") or [])
        if not required_fields.issubset(fields):
            return [
                f"{snapshot_path}: provider gap severity observation queue requires severity fields to backfill"
            ]
        if not (
            row.get("external_provider_gap_severity_observation_gap_reason")
            and row.get("external_provider_gap_severity_observation_gap_action")
            and row.get("external_provider_gap_severity_observation_backfill_policy") == "decision_time_only"
            and row.get("required_external_provider_gap_severity_observation_date")
            and row.get("source_outcome_id")
        ):
            return [
                f"{snapshot_path}: provider gap severity observation queue requires decision-time backfill instructions"
            ]
    work_item_count = int_value(backtest.get("pending_external_provider_gap_severity_observation_gap_work_item_count"))
    if work_item_count <= 0:
        return [
            f"{snapshot_path}: unknown provider gap severity labels require "
            "backtest.pending_external_provider_gap_severity_observation_gap_work_item_count"
        ]
    work_item_queue = backtest.get("pending_external_provider_gap_severity_observation_gap_work_item_queue")
    work_item_queue = work_item_queue if isinstance(work_item_queue, list) else []
    if not work_item_queue:
        return [
            f"{snapshot_path}: unknown provider gap severity labels require "
            "backtest.pending_external_provider_gap_severity_observation_gap_work_item_queue"
        ]
    work_item_queue_limit = int_value(
        backtest.get("pending_external_provider_gap_severity_observation_gap_work_item_queue_limit")
    )
    if work_item_queue_limit > 0 and len(work_item_queue) != min(work_item_count, work_item_queue_limit):
        return [
            f"{snapshot_path}: provider gap severity observation work item queue has {len(work_item_queue)} rows; "
            f"expected {min(work_item_count, work_item_queue_limit)}"
        ]
    hidden_work_item_count = int_value(
        backtest.get("pending_external_provider_gap_severity_observation_gap_hidden_work_item_count")
    )
    expected_hidden_work_item_count = max(0, work_item_count - len(work_item_queue))
    if hidden_work_item_count != expected_hidden_work_item_count:
        return [
            f"{snapshot_path}: provider gap severity observation hidden work item count is "
            f"{hidden_work_item_count}; expected {expected_hidden_work_item_count}"
        ]
    visible_work_item_label_count = int_value(
        backtest.get("pending_external_provider_gap_severity_observation_gap_visible_work_item_label_count")
    )
    expected_visible_work_item_label_count = sum(
        int_value(row.get("label_count"))
        for row in work_item_queue
        if isinstance(row, dict)
    )
    if visible_work_item_label_count != expected_visible_work_item_label_count:
        return [
            f"{snapshot_path}: provider gap severity observation visible work item labels cover "
            f"{visible_work_item_label_count}; expected {expected_visible_work_item_label_count}"
        ]
    hidden_work_item_label_count = int_value(
        backtest.get("pending_external_provider_gap_severity_observation_gap_hidden_work_item_label_count")
    )
    expected_hidden_work_item_label_count = max(0, unknown_count - visible_work_item_label_count)
    if hidden_work_item_label_count != expected_hidden_work_item_label_count:
        return [
            f"{snapshot_path}: provider gap severity observation hidden work item labels cover "
            f"{hidden_work_item_label_count}; expected {expected_hidden_work_item_label_count}"
        ]
    work_item_ids = [
        row.get("external_provider_gap_severity_observation_work_item_id")
        for row in work_item_queue
        if isinstance(row, dict)
    ]
    if not work_item_ids or any(not item for item in work_item_ids) or len(set(work_item_ids)) != len(work_item_ids):
        return [f"{snapshot_path}: provider gap severity observation work item queue requires unique work item ids"]
    for row in work_item_queue:
        if not isinstance(row, dict):
            return [f"{snapshot_path}: provider gap severity observation work item queue rows must be objects"]
        fields = set(row.get("minimum_external_provider_gap_severity_fields_to_backfill") or [])
        if not required_fields.issubset(fields):
            return [
                f"{snapshot_path}: provider gap severity observation work item queue requires severity fields to backfill"
            ]
        if not (
            int_value(row.get("label_count")) > 0
            and row.get("external_provider_gap_severity_observation_gap_reason")
            and row.get("external_provider_gap_severity_observation_gap_action")
            and row.get("external_provider_gap_severity_observation_backfill_policy") == "decision_time_only"
            and row.get("required_external_provider_gap_severity_observation_date")
            and isinstance(row.get("source_outcome_ids"), list)
            and row.get("source_outcome_ids")
        ):
            return [
                f"{snapshot_path}: provider gap severity observation work item queue requires decision-time backfill instructions"
            ]
    due_dates = backtest.get("pending_external_provider_gap_severity_observation_gap_due_dates")
    due_dates = due_dates if isinstance(due_dates, list) else []
    if not due_dates:
        return [
            f"{snapshot_path}: unknown provider gap severity labels require "
            "backtest.pending_external_provider_gap_severity_observation_gap_due_dates"
        ]
    for row in due_dates:
        if not isinstance(row, dict) or not row.get("due_date"):
            return [f"{snapshot_path}: provider gap severity observation due dates require due_date rows"]
    due_label_count = sum(int_value(row.get("label_count")) for row in due_dates)
    due_work_item_count = sum(int_value(row.get("work_item_count")) for row in due_dates)
    if due_label_count != unknown_count or due_work_item_count != work_item_count:
        return [
            f"{snapshot_path}: provider gap severity observation due dates cover "
            f"{due_label_count} labels/{due_work_item_count} work items; expected "
            f"{unknown_count} labels/{work_item_count} work items"
        ]
    backtest_as_of = parse_date(backtest.get("as_of"))
    cumulative_label_count = 0
    cumulative_work_item_count = 0
    cumulative_visible_label_count = 0
    cumulative_visible_work_item_count = 0
    cumulative_hidden_label_count = 0
    cumulative_hidden_work_item_count = 0
    for row in due_dates:
        due_date = parse_date(row.get("due_date"))
        if backtest_as_of and due_date:
            expected_days_until_due = (due_date - backtest_as_of).days
            if row.get("days_until_due") is None or int_value(row.get("days_until_due")) != expected_days_until_due:
                return [
                    f"{snapshot_path}: provider gap severity observation due date {row.get('due_date')} "
                    f"days_until_due is {row.get('days_until_due')}; expected {expected_days_until_due}"
                ]
            expected_due_window = provider_gap_severity_observation_due_window(expected_days_until_due)
            if row.get("due_window") != expected_due_window:
                return [
                    f"{snapshot_path}: provider gap severity observation due date {row.get('due_date')} "
                    f"due_window is {row.get('due_window')}; expected {expected_due_window}"
                ]
        cumulative_label_count += int_value(row.get("label_count"))
        cumulative_work_item_count += int_value(row.get("work_item_count"))
        cumulative_visible_label_count += int_value(row.get("visible_label_count"))
        cumulative_visible_work_item_count += int_value(row.get("visible_work_item_count"))
        cumulative_hidden_label_count += int_value(row.get("hidden_label_count"))
        cumulative_hidden_work_item_count += int_value(row.get("hidden_work_item_count"))
        if (
            int_value(row.get("cumulative_label_count")) != cumulative_label_count
            or int_value(row.get("cumulative_work_item_count")) != cumulative_work_item_count
            or int_value(row.get("cumulative_visible_label_count")) != cumulative_visible_label_count
            or int_value(row.get("cumulative_visible_work_item_count")) != cumulative_visible_work_item_count
            or int_value(row.get("cumulative_hidden_label_count")) != cumulative_hidden_label_count
            or int_value(row.get("cumulative_hidden_work_item_count")) != cumulative_hidden_work_item_count
        ):
            return [
                f"{snapshot_path}: provider gap severity observation due dates require cumulative coverage "
                f"through {row.get('due_date')}"
            ]
    due_visible_label_count = sum(int_value(row.get("visible_label_count")) for row in due_dates)
    due_visible_work_item_count = sum(int_value(row.get("visible_work_item_count")) for row in due_dates)
    if (
        due_visible_label_count != visible_work_item_label_count
        or due_visible_work_item_count != len(work_item_queue)
    ):
        return [
            f"{snapshot_path}: provider gap severity observation due dates visible coverage is "
            f"{due_visible_label_count} labels/{due_visible_work_item_count} work items; expected "
            f"{visible_work_item_label_count} labels/{len(work_item_queue)} work items"
        ]
    due_hidden_label_count = sum(int_value(row.get("hidden_label_count")) for row in due_dates)
    due_hidden_work_item_count = sum(int_value(row.get("hidden_work_item_count")) for row in due_dates)
    if (
        due_hidden_label_count != hidden_work_item_label_count
        or due_hidden_work_item_count != hidden_work_item_count
    ):
        return [
            f"{snapshot_path}: provider gap severity observation due dates hidden coverage is "
            f"{due_hidden_label_count} labels/{due_hidden_work_item_count} work items; expected "
            f"{hidden_work_item_label_count} labels/{hidden_work_item_count} work items"
        ]
    due_window_counts = backtest.get("pending_external_provider_gap_severity_observation_gap_due_window_counts")
    due_window_counts = due_window_counts if isinstance(due_window_counts, list) else []
    if not due_window_counts:
        return [
            f"{snapshot_path}: unknown provider gap severity labels require "
            "backtest.pending_external_provider_gap_severity_observation_gap_due_window_counts"
        ]
    expected_windows = provider_gap_severity_observation_due_window_counts(due_dates)
    observed_windows = {
        str(row.get("due_window") or "unknown"): row
        for row in due_window_counts
        if isinstance(row, dict)
    }
    if set(observed_windows) != set(expected_windows):
        return [
            f"{snapshot_path}: provider gap severity observation due window counts cover "
            f"{sorted(observed_windows)}; expected {sorted(expected_windows)}"
        ]
    for due_window, expected in expected_windows.items():
        observed = observed_windows[due_window]
        for key in (
            "label_count",
            "work_item_count",
            "visible_label_count",
            "visible_work_item_count",
            "hidden_label_count",
            "hidden_work_item_count",
            "due_date_count",
        ):
            if int_value(observed.get(key)) != int_value(expected.get(key)):
                return [
                    f"{snapshot_path}: provider gap severity observation due window {due_window} "
                    f"{key} is {observed.get(key)}; expected {expected.get(key)}"
                ]
        for key in ("earliest_due_date", "latest_due_date"):
            if observed.get(key) != expected.get(key):
                return [
                    f"{snapshot_path}: provider gap severity observation due window {due_window} "
                    f"{key} is {observed.get(key)}; expected {expected.get(key)}"
                ]
    horizon_counts = backtest.get("pending_external_provider_gap_severity_observation_gap_horizon_counts")
    horizon_counts = horizon_counts if isinstance(horizon_counts, list) else []
    if not horizon_counts:
        return [
            f"{snapshot_path}: unknown provider gap severity labels require "
            "backtest.pending_external_provider_gap_severity_observation_gap_horizon_counts"
        ]
    horizon_label_count = sum(int_value(row.get("label_count")) for row in horizon_counts if isinstance(row, dict))
    horizon_work_item_count = sum(
        int_value(row.get("work_item_count")) for row in horizon_counts if isinstance(row, dict)
    )
    if horizon_label_count != unknown_count or horizon_work_item_count != work_item_count:
        return [
            f"{snapshot_path}: provider gap severity observation horizon counts cover "
            f"{horizon_label_count} labels/{horizon_work_item_count} work items; expected "
            f"{unknown_count} labels/{work_item_count} work items"
        ]
    horizon_visible_label_count = sum(
        int_value(row.get("visible_label_count")) for row in horizon_counts if isinstance(row, dict)
    )
    horizon_visible_work_item_count = sum(
        int_value(row.get("visible_work_item_count")) for row in horizon_counts if isinstance(row, dict)
    )
    if (
        horizon_visible_label_count != visible_work_item_label_count
        or horizon_visible_work_item_count != len(work_item_queue)
    ):
        return [
            f"{snapshot_path}: provider gap severity observation horizon visible coverage is "
            f"{horizon_visible_label_count} labels/{horizon_visible_work_item_count} work items; expected "
            f"{visible_work_item_label_count} labels/{len(work_item_queue)} work items"
        ]
    horizon_hidden_label_count = sum(
        int_value(row.get("hidden_label_count")) for row in horizon_counts if isinstance(row, dict)
    )
    horizon_hidden_work_item_count = sum(
        int_value(row.get("hidden_work_item_count")) for row in horizon_counts if isinstance(row, dict)
    )
    if (
        horizon_hidden_label_count != hidden_work_item_label_count
        or horizon_hidden_work_item_count != hidden_work_item_count
    ):
        return [
            f"{snapshot_path}: provider gap severity observation horizon hidden coverage is "
            f"{horizon_hidden_label_count} labels/{horizon_hidden_work_item_count} work items; expected "
            f"{hidden_work_item_label_count} labels/{hidden_work_item_count} work items"
        ]
    for row in horizon_counts:
        if not isinstance(row, dict) or not row.get("horizon") or not row.get("learning_role"):
            return [
                f"{snapshot_path}: provider gap severity observation horizon counts require horizon and learning_role"
            ]
        next_due_date = parse_date(row.get("next_due_date"))
        if backtest_as_of and next_due_date:
            expected_days_until = (next_due_date - backtest_as_of).days
            if (
                row.get("days_until_next_due") is None
                or int_value(row.get("days_until_next_due")) != expected_days_until
            ):
                return [
                    f"{snapshot_path}: provider gap severity observation horizon {row.get('horizon')} "
                    f"days_until_next_due is {row.get('days_until_next_due')}; expected {expected_days_until}"
                ]
            expected_due_window = provider_gap_severity_observation_due_window(expected_days_until)
            if row.get("next_due_window") != expected_due_window:
                return [
                    f"{snapshot_path}: provider gap severity observation horizon {row.get('horizon')} "
                    f"next_due_window is {row.get('next_due_window')}; expected {expected_due_window}"
                ]
        for visibility, count_key in (
            ("visible", "visible_label_count"),
            ("hidden", "hidden_label_count"),
        ):
            if int_value(row.get(count_key)) <= 0:
                continue
            visibility_due = parse_date(row.get(f"next_{visibility}_due_date"))
            if not visibility_due:
                return [
                    f"{snapshot_path}: provider gap severity observation horizon {row.get('horizon')} "
                    f"requires next_{visibility}_due_date"
                ]
            if backtest_as_of:
                expected_days_until = (visibility_due - backtest_as_of).days
                days_key = f"days_until_next_{visibility}_due"
                if row.get(days_key) is None or int_value(row.get(days_key)) != expected_days_until:
                    return [
                        f"{snapshot_path}: provider gap severity observation horizon {row.get('horizon')} "
                        f"{days_key} is {row.get(days_key)}; expected {expected_days_until}"
                    ]
                window_key = f"next_{visibility}_due_window"
                expected_due_window = provider_gap_severity_observation_due_window(expected_days_until)
                if row.get(window_key) != expected_due_window:
                    return [
                        f"{snapshot_path}: provider gap severity observation horizon {row.get('horizon')} "
                        f"{window_key} is {row.get(window_key)}; expected {expected_due_window}"
                    ]
            label_count_key = f"next_{visibility}_due_label_count"
            work_item_count_key = f"next_{visibility}_due_work_item_count"
            next_due_label_count = int_value(row.get(label_count_key))
            next_due_work_item_count = int_value(row.get(work_item_count_key))
            if next_due_label_count <= 0 or next_due_label_count > int_value(row.get(count_key)):
                return [
                    f"{snapshot_path}: provider gap severity observation horizon {row.get('horizon')} "
                    f"{label_count_key} is {row.get(label_count_key)}; expected 1..{row.get(count_key)}"
                ]
            work_item_count_key_total = f"{visibility}_work_item_count"
            if next_due_work_item_count < 0 or next_due_work_item_count > int_value(row.get(work_item_count_key_total)):
                return [
                    f"{snapshot_path}: provider gap severity observation horizon {row.get('horizon')} "
                    f"{work_item_count_key} is {row.get(work_item_count_key)}; "
                    f"expected 0..{row.get(work_item_count_key_total)}"
                ]
            expected_horizons = [str(row.get("horizon"))]
            observed_horizons = [str(item) for item in row.get(f"next_{visibility}_due_horizons") or []]
            if observed_horizons != expected_horizons:
                return [
                    f"{snapshot_path}: provider gap severity observation horizon {row.get('horizon')} "
                    f"next_{visibility}_due_horizons are {observed_horizons}; expected {expected_horizons}"
                ]
    role_counts = backtest.get("pending_external_provider_gap_severity_observation_gap_learning_role_counts")
    role_counts = role_counts if isinstance(role_counts, list) else []
    if not role_counts:
        return [
            f"{snapshot_path}: unknown provider gap severity labels require "
            "backtest.pending_external_provider_gap_severity_observation_gap_learning_role_counts"
        ]
    expected_roles = provider_gap_severity_observation_learning_role_counts(horizon_counts, backtest_as_of)
    observed_roles = {
        str(row.get("learning_role") or "unknown"): row
        for row in role_counts
        if isinstance(row, dict)
    }
    if set(observed_roles) != set(expected_roles):
        return [
            f"{snapshot_path}: provider gap severity observation learning role counts cover "
            f"{sorted(observed_roles)}; expected {sorted(expected_roles)}"
        ]
    for learning_role, expected in expected_roles.items():
        observed = observed_roles[learning_role]
        for key in (
            "label_count",
            "work_item_count",
            "visible_label_count",
            "visible_work_item_count",
            "hidden_label_count",
            "hidden_work_item_count",
            "due_date_count",
            "horizon_count",
            "days_until_next_due",
            "days_until_next_visible_due",
            "days_until_next_hidden_due",
            "next_visible_due_label_count",
            "next_visible_due_work_item_count",
            "next_hidden_due_label_count",
            "next_hidden_due_work_item_count",
        ):
            if int_value(observed.get(key)) != int_value(expected.get(key)):
                return [
                    f"{snapshot_path}: provider gap severity observation learning role {learning_role} "
                    f"{key} is {observed.get(key)}; expected {expected.get(key)}"
                ]
        for key in (
            "next_due_date",
            "latest_due_date",
            "next_due_window",
            "next_visible_due_date",
            "latest_visible_due_date",
            "next_visible_due_window",
            "next_hidden_due_date",
            "latest_hidden_due_date",
            "next_hidden_due_window",
        ):
            if observed.get(key) != expected.get(key):
                return [
                    f"{snapshot_path}: provider gap severity observation learning role {learning_role} "
                    f"{key} is {observed.get(key)}; expected {expected.get(key)}"
                ]
        for key in ("next_visible_due_horizons", "next_hidden_due_horizons"):
            if list(observed.get(key) or []) != list(expected.get(key) or []):
                return [
                    f"{snapshot_path}: provider gap severity observation learning role {learning_role} "
                    f"{key} is {observed.get(key)}; expected {expected.get(key)}"
                ]
        for key in ("visible_label_coverage_pct", "visible_work_item_coverage_pct"):
            if round(float_value(observed.get(key)), 1) != round(float_value(expected.get(key)), 1):
                return [
                    f"{snapshot_path}: provider gap severity observation learning role {learning_role} "
                    f"{key} is {observed.get(key)}; expected {expected.get(key)}"
                ]
        if observed.get("queue_visibility_status") != expected.get("queue_visibility_status"):
            return [
                f"{snapshot_path}: provider gap severity observation learning role {learning_role} "
                f"queue_visibility_status is {observed.get('queue_visibility_status')}; "
                f"expected {expected.get('queue_visibility_status')}"
            ]
        if list(observed.get("horizons") or []) != list(expected.get("horizons") or []):
            return [
                f"{snapshot_path}: provider gap severity observation learning role {learning_role} "
                "horizons do not match horizon counts"
            ]
    calibration_role = observed_roles.get("calibration_label") or {}
    if int_value(calibration_role.get("hidden_label_count")) > 0:
        hidden_calibration_count = int_value(
            backtest.get(
                "pending_external_provider_gap_severity_observation_gap_hidden_calibration_work_item_count"
            )
        )
        hidden_calibration_queue = backtest.get(
            "pending_external_provider_gap_severity_observation_gap_hidden_calibration_work_item_queue"
        )
        hidden_calibration_queue = hidden_calibration_queue if isinstance(hidden_calibration_queue, list) else []
        if hidden_calibration_count <= 0 or not hidden_calibration_queue:
            return [
                f"{snapshot_path}: hidden calibration provider gap severity labels require "
                "backtest.pending_external_provider_gap_severity_observation_gap_hidden_calibration_work_item_queue"
            ]
        hidden_calibration_queue_limit = int_value(
            backtest.get(
                "pending_external_provider_gap_severity_observation_gap_hidden_calibration_work_item_queue_limit"
            )
        )
        if (
            hidden_calibration_queue_limit > 0
            and len(hidden_calibration_queue) != min(hidden_calibration_count, hidden_calibration_queue_limit)
        ):
            return [
                f"{snapshot_path}: provider gap severity observation hidden calibration queue has "
                f"{len(hidden_calibration_queue)} rows; expected "
                f"{min(hidden_calibration_count, hidden_calibration_queue_limit)}"
            ]
        hidden_calibration_ids = [
            row.get("external_provider_gap_severity_observation_work_item_id")
            for row in hidden_calibration_queue
            if isinstance(row, dict)
        ]
        if (
            not hidden_calibration_ids
            or any(not item for item in hidden_calibration_ids)
            or len(set(hidden_calibration_ids)) != len(hidden_calibration_ids)
        ):
            return [
                f"{snapshot_path}: provider gap severity observation hidden calibration queue requires "
                "unique work item ids"
            ]
        visible_work_item_ids = set(work_item_ids)
        hidden_calibration_due_dates = [
            str(row.get("due_date") or "")[:10]
            for row in hidden_calibration_queue
            if isinstance(row, dict)
        ]
        if len(hidden_calibration_due_dates) != len(hidden_calibration_queue):
            return [
                f"{snapshot_path}: provider gap severity observation hidden calibration queue rows must be objects"
            ]
        if hidden_calibration_due_dates != sorted(hidden_calibration_due_dates):
            return [
                f"{snapshot_path}: provider gap severity observation hidden calibration queue must be sorted by due date"
            ]
        next_hidden_calibration_due = str(calibration_role.get("next_hidden_due_date") or "")[:10]
        if next_hidden_calibration_due and hidden_calibration_due_dates[0] != next_hidden_calibration_due:
            return [
                f"{snapshot_path}: provider gap severity observation hidden calibration queue starts "
                f"{hidden_calibration_due_dates[0]}; expected {next_hidden_calibration_due}"
            ]
        for row in hidden_calibration_queue:
            if not isinstance(row, dict):
                return [
                    f"{snapshot_path}: provider gap severity observation hidden calibration queue rows must be objects"
                ]
            if row.get("external_provider_gap_severity_observation_work_item_id") in visible_work_item_ids:
                return [
                    f"{snapshot_path}: provider gap severity observation hidden calibration queue must only include "
                    "work items hidden from the main queue"
                ]
            if str(row.get("horizon") or "") not in {"1m", "3m", "6m", "12m"}:
                return [
                    f"{snapshot_path}: provider gap severity observation hidden calibration queue must only include "
                    "calibration horizons"
                ]
            decision_time_report_json = str(row.get("decision_time_report_json") or "")
            if not decision_time_report_json:
                return [
                    f"{snapshot_path}: provider gap severity observation hidden calibration queue requires "
                    "decision-time report artifacts"
                ]
            if row.get("decision_time_report_json_available") is not True:
                return [
                    f"{snapshot_path}: provider gap severity observation hidden calibration queue requires "
                    "available decision-time report JSON artifacts"
                ]
            required_observation_date = str(
                row.get("required_external_provider_gap_severity_observation_date") or ""
            )[:10]
            session = str(row.get("session") or "")
            if required_observation_date and session:
                expected_report_json = f"{required_observation_date}-{session}.json"
                if decision_time_report_json != expected_report_json:
                    return [
                        f"{snapshot_path}: provider gap severity observation hidden calibration queue report "
                        f"is {decision_time_report_json}; expected {expected_report_json}"
                    ]
            candidate_values = row.get("candidate_backfill_values")
            candidate_values = candidate_values if isinstance(candidate_values, dict) else {}
            if row.get("candidate_backfill_status") != "ready" or not candidate_values:
                return [
                    f"{snapshot_path}: provider gap severity observation hidden calibration queue requires "
                    "ready candidate backfill values"
                ]
            if not required_fields.issubset(set(candidate_values)):
                return [
                    f"{snapshot_path}: provider gap severity observation hidden calibration queue requires "
                    "provider gap severity candidate fields"
                ]
            if (
                row.get("candidate_source_section") != "external_signals.source_statuses"
                or row.get("candidate_backfill_policy") != "decision_time_external_signals_provider_status_only"
            ):
                return [
                    f"{snapshot_path}: provider gap severity observation hidden calibration queue requires "
                    "decision-time external signal candidate metadata"
                ]
            fields = set(row.get("minimum_external_provider_gap_severity_fields_to_backfill") or [])
            if not required_fields.issubset(fields):
                return [
                    f"{snapshot_path}: provider gap severity observation hidden calibration queue requires "
                    "severity fields to backfill"
                ]
            if not (
                int_value(row.get("label_count")) > 0
                and row.get("external_provider_gap_severity_observation_gap_reason")
                and row.get("external_provider_gap_severity_observation_gap_action")
                and row.get("external_provider_gap_severity_observation_backfill_policy") == "decision_time_only"
                and row.get("required_external_provider_gap_severity_observation_date")
                and isinstance(row.get("source_outcome_ids"), list)
                and row.get("source_outcome_ids")
            ):
                return [
                    f"{snapshot_path}: provider gap severity observation hidden calibration queue requires "
                    "decision-time backfill instructions"
                ]
        hidden_calibration_report_batch_count = int_value(
            backtest.get(
                "pending_external_provider_gap_severity_observation_gap_hidden_calibration_report_batch_count"
            )
        )
        hidden_calibration_report_batch_queue = backtest.get(
            "pending_external_provider_gap_severity_observation_gap_hidden_calibration_report_batch_queue"
        )
        hidden_calibration_report_batch_queue = (
            hidden_calibration_report_batch_queue
            if isinstance(hidden_calibration_report_batch_queue, list)
            else []
        )
        if hidden_calibration_report_batch_count <= 0 or not hidden_calibration_report_batch_queue:
            return [
                f"{snapshot_path}: hidden calibration provider gap severity labels require "
                "backtest.pending_external_provider_gap_severity_observation_gap_hidden_calibration_report_batch_queue"
            ]
        hidden_calibration_report_batch_queue_limit = int_value(
            backtest.get(
                "pending_external_provider_gap_severity_observation_gap_hidden_calibration_report_batch_queue_limit"
            )
        )
        if (
            hidden_calibration_report_batch_queue_limit > 0
            and len(hidden_calibration_report_batch_queue)
            != min(hidden_calibration_report_batch_count, hidden_calibration_report_batch_queue_limit)
        ):
            return [
                f"{snapshot_path}: provider gap severity observation hidden calibration report batch queue has "
                f"{len(hidden_calibration_report_batch_queue)} rows; expected "
                f"{min(hidden_calibration_report_batch_count, hidden_calibration_report_batch_queue_limit)}"
            ]
        report_batch_reports = {
            str(row.get("decision_time_report_json") or "")
            for row in hidden_calibration_report_batch_queue
            if isinstance(row, dict)
        }
        hidden_calibration_reports = {
            str(row.get("decision_time_report_json") or "")
            for row in hidden_calibration_queue
            if isinstance(row, dict)
        }
        if not hidden_calibration_reports.issubset(report_batch_reports):
            return [
                f"{snapshot_path}: provider gap severity observation hidden calibration report batches must cover "
                "the visible hidden-calibration work-item queue"
            ]
        for row in hidden_calibration_report_batch_queue:
            if not isinstance(row, dict):
                return [
                    f"{snapshot_path}: provider gap severity observation hidden calibration report batch rows "
                    "must be objects"
                ]
            if not row.get("decision_time_report_json") or row.get("decision_time_report_json_available") is not True:
                return [
                    f"{snapshot_path}: provider gap severity observation hidden calibration report batches require "
                    "available decision-time report JSON artifacts"
                ]
            if int_value(row.get("work_item_count")) <= 0 or int_value(row.get("label_count")) <= 0:
                return [
                    f"{snapshot_path}: provider gap severity observation hidden calibration report batches require "
                    "positive work item and label counts"
                ]
            if int_value(row.get("work_item_count")) > hidden_calibration_count:
                return [
                    f"{snapshot_path}: provider gap severity observation hidden calibration report batch work item "
                    "count exceeds hidden calibration count"
                ]
            if not row.get("earliest_due_date") or not row.get("latest_due_date"):
                return [
                    f"{snapshot_path}: provider gap severity observation hidden calibration report batches require "
                    "due date spans"
                ]
            horizons = [str(item) for item in row.get("horizons") or []]
            if not horizons or any(horizon not in {"1m", "3m", "6m", "12m"} for horizon in horizons):
                return [
                    f"{snapshot_path}: provider gap severity observation hidden calibration report batches must "
                    "only include calibration horizons"
                ]
            candidate_values = row.get("candidate_backfill_values")
            candidate_values = candidate_values if isinstance(candidate_values, dict) else {}
            if row.get("candidate_backfill_status") != "ready" or not candidate_values:
                return [
                    f"{snapshot_path}: provider gap severity observation hidden calibration report batches require "
                    "ready candidate backfill values"
                ]
            candidate_fields = set(candidate_values)
            if not required_fields.issubset(candidate_fields):
                return [
                    f"{snapshot_path}: provider gap severity observation hidden calibration report batches require "
                    "all provider gap severity candidate fields"
                ]
            if (
                int_value(candidate_values.get("external_provider_gap_count")) < 0
                or not candidate_values.get("external_provider_primary_gap_severity")
                or row.get("candidate_source_section") != "external_signals.source_statuses"
                or row.get("candidate_backfill_policy") != "decision_time_external_signals_provider_status_only"
            ):
                return [
                    f"{snapshot_path}: provider gap severity observation hidden calibration report batches require "
                    "decision-time external signal candidate metadata"
                ]
        backfill_record_count = int_value(
            backtest.get(
                "pending_external_provider_gap_severity_observation_gap_hidden_calibration_backfill_record_count"
            )
        )
        backfill_record_queue = backtest.get(
            "pending_external_provider_gap_severity_observation_gap_hidden_calibration_backfill_record_queue"
        )
        backfill_record_queue = backfill_record_queue if isinstance(backfill_record_queue, list) else []
        if backfill_record_count <= 0 or not backfill_record_queue:
            return [
                f"{snapshot_path}: hidden calibration provider gap severity labels require "
                "backtest.pending_external_provider_gap_severity_observation_gap_hidden_calibration_backfill_record_queue"
            ]
        backfill_record_queue_limit = int_value(
            backtest.get(
                "pending_external_provider_gap_severity_observation_gap_hidden_calibration_backfill_record_queue_limit"
            )
        )
        if (
            backfill_record_queue_limit > 0
            and len(backfill_record_queue) != min(backfill_record_count, backfill_record_queue_limit)
        ):
            return [
                f"{snapshot_path}: provider gap severity observation hidden calibration backfill record queue has "
                f"{len(backfill_record_queue)} rows; expected {min(backfill_record_count, backfill_record_queue_limit)}"
            ]
        backfill_record_ids = [
            row.get("external_provider_gap_severity_observation_backfill_record_id")
            for row in backfill_record_queue
            if isinstance(row, dict)
        ]
        if (
            not backfill_record_ids
            or any(not item for item in backfill_record_ids)
            or len(set(backfill_record_ids)) != len(backfill_record_ids)
        ):
            return [
                f"{snapshot_path}: provider gap severity observation hidden calibration backfill records require "
                "unique record ids"
            ]
        backfill_work_item_ids = {
            str(row.get("external_provider_gap_severity_observation_work_item_id") or "")
            for row in backfill_record_queue
            if isinstance(row, dict)
        }
        if not set(str(item or "") for item in hidden_calibration_ids).issubset(backfill_work_item_ids):
            return [
                f"{snapshot_path}: provider gap severity observation hidden calibration backfill records must cover "
                "the visible hidden-calibration work-item queue"
            ]
        full_candidate_fields = {
            "external_provider_gap_count",
            "external_provider_configuration_gap_count",
            "external_provider_runtime_gap_count",
            "external_provider_stale_gap_count",
            "external_provider_transient_gap_count",
            "external_provider_other_gap_count",
            "external_provider_primary_gap_severity",
            "external_provider_gap_severity_score",
        }
        for row in backfill_record_queue:
            if not isinstance(row, dict):
                return [
                    f"{snapshot_path}: provider gap severity observation hidden calibration backfill record rows "
                    "must be objects"
                ]
            candidate_values = row.get("candidate_backfill_values")
            candidate_values = candidate_values if isinstance(candidate_values, dict) else {}
            if (
                row.get("candidate_apply_status") != "ready"
                or row.get("target_section") != "recommendation_training_examples"
                or row.get("candidate_apply_policy") != "update_matching_recommendation_training_examples_by_source_trial_id"
                or row.get("source_report_available") is not True
                or not row.get("source_outcome_ids")
                or not row.get("source_trial_ids")
            ):
                return [
                    f"{snapshot_path}: provider gap severity observation hidden calibration backfill records require "
                    "ready apply metadata"
                ]
            if not full_candidate_fields.issubset(set(row.get("fields_to_backfill") or [])):
                return [
                    f"{snapshot_path}: provider gap severity observation hidden calibration backfill records require "
                    "all provider gap severity fields to backfill"
                ]
            if not full_candidate_fields.issubset(set(candidate_values)):
                return [
                    f"{snapshot_path}: provider gap severity observation hidden calibration backfill records require "
                    "all provider gap severity candidate values"
                ]
    return []


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


def provider_gap_severity_observation_due_window_counts(due_dates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    due_dates_by_window: dict[str, set[str]] = {}
    for row in due_dates:
        if not isinstance(row, dict):
            continue
        due_window = str(row.get("due_window") or "unknown")
        due_date = str(row.get("due_date") or "")[:10]
        grouped.setdefault(
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
        grouped[due_window]["label_count"] += int_value(row.get("label_count"))
        grouped[due_window]["work_item_count"] += int_value(row.get("work_item_count"))
        grouped[due_window]["visible_label_count"] += int_value(row.get("visible_label_count"))
        grouped[due_window]["visible_work_item_count"] += int_value(row.get("visible_work_item_count"))
        grouped[due_window]["hidden_label_count"] += int_value(row.get("hidden_label_count"))
        grouped[due_window]["hidden_work_item_count"] += int_value(row.get("hidden_work_item_count"))
        if due_date:
            due_dates_by_window.setdefault(due_window, set()).add(due_date)
    for due_window, row in grouped.items():
        bucket_due_dates = sorted(due_dates_by_window.get(due_window, set()))
        row["due_date_count"] = len(bucket_due_dates)
        row["earliest_due_date"] = bucket_due_dates[0] if bucket_due_dates else None
        row["latest_due_date"] = bucket_due_dates[-1] if bucket_due_dates else None
    return grouped


def provider_gap_severity_observation_learning_role_counts(
    horizon_counts: list[dict[str, Any]],
    as_of: Any,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    horizons_by_role: dict[str, set[str]] = {}
    due_dates_by_role: dict[str, set[str]] = {}
    visible_due_buckets_by_role: dict[str, dict[str, dict[str, Any]]] = {}
    hidden_due_buckets_by_role: dict[str, dict[str, dict[str, Any]]] = {}
    for row in horizon_counts:
        if not isinstance(row, dict):
            continue
        learning_role = str(row.get("learning_role") or "unknown")
        grouped.setdefault(
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
        grouped[learning_role]["label_count"] += int_value(row.get("label_count"))
        grouped[learning_role]["work_item_count"] += int_value(row.get("work_item_count"))
        grouped[learning_role]["visible_label_count"] += int_value(row.get("visible_label_count"))
        grouped[learning_role]["visible_work_item_count"] += int_value(row.get("visible_work_item_count"))
        grouped[learning_role]["hidden_label_count"] += int_value(row.get("hidden_label_count"))
        grouped[learning_role]["hidden_work_item_count"] += int_value(row.get("hidden_work_item_count"))
        grouped[learning_role]["due_date_count"] += int_value(row.get("due_date_count"))
        horizon = str(row.get("horizon") or "")
        if horizon:
            horizons_by_role.setdefault(learning_role, set()).add(horizon)
        for key in ("next_due_date", "latest_due_date"):
            due_date = str(row.get(key) or "")[:10]
            if due_date:
                due_dates_by_role.setdefault(learning_role, set()).add(due_date)
        for key in ("next_visible_due_date", "latest_visible_due_date"):
            due_date = str(row.get(key) or "")[:10]
            if due_date:
                due_dates_by_role.setdefault(learning_role, set()).add(due_date)
        visible_due_date = str(row.get("next_visible_due_date") or "")[:10]
        if visible_due_date:
            increment_due_bucket(
                visible_due_buckets_by_role.setdefault(learning_role, {}),
                visible_due_date,
                int_value(row.get("next_visible_due_label_count")),
                int_value(row.get("next_visible_due_work_item_count")),
                row.get("next_visible_due_horizons") or [],
            )
        for key in ("next_hidden_due_date", "latest_hidden_due_date"):
            due_date = str(row.get(key) or "")[:10]
            if due_date:
                due_dates_by_role.setdefault(learning_role, set()).add(due_date)
        hidden_due_date = str(row.get("next_hidden_due_date") or "")[:10]
        if hidden_due_date:
            increment_due_bucket(
                hidden_due_buckets_by_role.setdefault(learning_role, {}),
                hidden_due_date,
                int_value(row.get("next_hidden_due_label_count")),
                int_value(row.get("next_hidden_due_work_item_count")),
                row.get("next_hidden_due_horizons") or [],
            )
    for learning_role, row in grouped.items():
        due_dates = sorted(due_dates_by_role.get(learning_role, set()))
        next_due_date = due_dates[0] if due_dates else None
        next_due = parse_date(next_due_date)
        days_until_next_due = (next_due - as_of).days if as_of and next_due else None
        horizons = sorted(
            horizons_by_role.get(learning_role, set()),
            key=lambda horizon: (provider_gap_severity_observation_horizon_sort(horizon), horizon),
        )
        row["horizon_count"] = len(horizons)
        row["horizons"] = horizons
        row["next_due_date"] = next_due_date
        row["latest_due_date"] = due_dates[-1] if due_dates else None
        row["days_until_next_due"] = days_until_next_due
        row["next_due_window"] = provider_gap_severity_observation_due_window(days_until_next_due)
        row.update(
            due_timing_fields(
                visible_due_buckets_by_role.get(learning_role, {}),
                as_of,
                "visible",
            )
        )
        row.update(
            due_timing_fields(
                hidden_due_buckets_by_role.get(learning_role, {}),
                as_of,
                "hidden",
            )
        )
        row["visible_label_coverage_pct"] = coverage_pct(
            row.get("visible_label_count"),
            row.get("label_count"),
        )
        row["visible_work_item_coverage_pct"] = coverage_pct(
            row.get("visible_work_item_count"),
            row.get("work_item_count"),
        )
        row["queue_visibility_status"] = queue_visibility_status(
            row.get("visible_label_count"),
            row.get("label_count"),
        )
    return grouped


def due_timing_fields(due_buckets: dict[str, dict[str, Any]], as_of: Any, visibility: str) -> dict[str, Any]:
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
        f"next_{visibility}_due_label_count": int_value(next_bucket.get("label_count")),
        f"next_{visibility}_due_work_item_count": int_value(next_bucket.get("work_item_count")),
        f"next_{visibility}_due_horizons": next_horizons,
    }


def increment_due_bucket(
    due_buckets: dict[str, dict[str, Any]],
    due_date: str,
    label_count: int,
    work_item_count: int,
    horizons: list[Any],
) -> None:
    bucket = due_buckets.setdefault(due_date, {"label_count": 0, "work_item_count": 0, "horizons": set()})
    bucket["label_count"] += int_value(label_count)
    bucket["work_item_count"] += int_value(work_item_count)
    for horizon in horizons:
        if horizon:
            bucket["horizons"].add(str(horizon))


def coverage_pct(visible_count: Any, total_count: Any) -> float:
    total = int_value(total_count)
    if total <= 0:
        return 0.0
    return round((int_value(visible_count) / total) * 100.0, 1)


def queue_visibility_status(visible_count: Any, total_count: Any) -> str:
    visible = int_value(visible_count)
    total = int_value(total_count)
    if total <= 0:
        return "empty"
    if visible <= 0:
        return "hidden"
    if visible >= total:
        return "fully_visible"
    return "partially_visible"


def provider_gap_severity_observation_horizon_sort(horizon: Any) -> int:
    order = {"5d": 0, "1m": 1, "3m": 2, "6m": 3, "12m": 4}
    return order.get(str(horizon or "unknown"), 99)


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


def pending_approval_summary_failures(backtest: dict[str, Any], pending_count: int, snapshot_path: Path) -> list[str]:
    failures: list[str] = []
    checks = [
        ("pending_by_approval_gate_status", "backtest.pending_by_approval_gate_status"),
        ("pending_by_approval_blocker_bucket", "backtest.pending_by_approval_blocker_bucket"),
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
        failures.extend(source_approval_blocker_queue_failures(source, label, snapshot_path))
    failures.extend(data_health_approval_blocker_summary_failures(payload.get("data_health") or {}, sources, snapshot_path))
    return failures


def data_health_approval_blocker_summary_failures(
    data_health: dict[str, Any],
    sources: list[dict[str, Any]],
    snapshot_path: Path,
) -> list[str]:
    external_count = sum(int_value(source.get("approval_blocked_external_gap_count")) for source in sources)
    confirmation_count = sum(int_value(source.get("approval_blocked_confirmation_gap_count")) for source in sources)
    if external_count + confirmation_count <= 0:
        return []
    summary = data_health.get("approval_blocker_summary")
    if not isinstance(summary, dict) or not summary:
        return [f"{snapshot_path}: approval blockers require data_health.approval_blocker_summary"]
    failures: list[str] = []
    expected_total = external_count + confirmation_count
    if summary.get("status") != "attention":
        failures.append(f"{snapshot_path}: data_health.approval_blocker_summary status must be attention")
    if int_value(summary.get("total_source_blocker_count")) != expected_total:
        failures.append(
            f"{snapshot_path}: data_health.approval_blocker_summary total_source_blocker_count is "
            f"{int_value(summary.get('total_source_blocker_count'))}; expected {expected_total}"
        )
    if int_value(summary.get("external_gap_ticket_count")) != external_count:
        failures.append(
            f"{snapshot_path}: data_health.approval_blocker_summary external_gap_ticket_count is "
            f"{int_value(summary.get('external_gap_ticket_count'))}; expected {external_count}"
        )
    if int_value(summary.get("earnings_confirmation_ticket_count")) != confirmation_count:
        failures.append(
            f"{snapshot_path}: data_health.approval_blocker_summary earnings_confirmation_ticket_count is "
            f"{int_value(summary.get('earnings_confirmation_ticket_count'))}; expected {confirmation_count}"
        )
    visible_rows = approval_blocker_rows(sources, "approval_blocked_external_gaps") + approval_blocker_rows(
        sources,
        "approval_blocked_confirmation_gaps",
    )
    if int_value(summary.get("visible_blocker_row_count")) != len(visible_rows):
        failures.append(
            f"{snapshot_path}: data_health.approval_blocker_summary visible_blocker_row_count is "
            f"{int_value(summary.get('visible_blocker_row_count'))}; expected {len(visible_rows)}"
        )
    visible_blockers = visible_approval_blockers(visible_rows)
    if int_value(summary.get("blocked_ticket_count")) != len(visible_blockers):
        failures.append(
            f"{snapshot_path}: data_health.approval_blocker_summary blocked_ticket_count is "
            f"{int_value(summary.get('blocked_ticket_count'))}; expected {len(visible_blockers)}"
        )
    expected_symbols = sorted(
        {
            str(row.get("symbol") or "").upper()
            for row in visible_blockers.values()
            if row.get("symbol")
        }
    )
    if summary.get("blocked_symbols") != expected_symbols:
        failures.append(f"{snapshot_path}: data_health.approval_blocker_summary blocked_symbols must match visible blockers")
    expected_open_counts = visible_blocker_open_check_counts(visible_blockers)
    open_counts = summary.get("open_check_counts") if isinstance(summary.get("open_check_counts"), dict) else {}
    normalized_open_counts = {str(key): int_value(value) for key, value in open_counts.items() if int_value(value) > 0}
    if normalized_open_counts != expected_open_counts:
        failures.append(f"{snapshot_path}: data_health.approval_blocker_summary open_check_counts must match visible blockers")
    if sum(int_value(value) for value in open_counts.values()) != int_value(summary.get("open_check_count")):
        failures.append(f"{snapshot_path}: data_health.approval_blocker_summary open check counts must sum to open_check_count")
    if int_value(summary.get("open_check_count")) != sum(expected_open_counts.values()):
        failures.append(
            f"{snapshot_path}: data_health.approval_blocker_summary open_check_count is "
            f"{int_value(summary.get('open_check_count'))}; expected {sum(expected_open_counts.values())}"
        )
    expected_provider_counts = count_nested_values(
        approval_blocker_rows(sources, "approval_blocked_external_gaps"),
        "provider_gap_sources",
    )
    provider_counts = summary.get("provider_gap_source_counts") if isinstance(summary.get("provider_gap_source_counts"), dict) else {}
    normalized_provider_counts = {str(key): int_value(value) for key, value in provider_counts.items() if int_value(value) > 0}
    if normalized_provider_counts != expected_provider_counts:
        failures.append(f"{snapshot_path}: data_health.approval_blocker_summary provider_gap_source_counts must match visible blockers")
    expected_priority_counts = count_scalar_values(
        approval_blocker_rows(sources, "approval_blocked_confirmation_gaps"),
        "confirmation_priority",
    )
    priority_counts = summary.get("confirmation_priority_counts") if isinstance(summary.get("confirmation_priority_counts"), dict) else {}
    normalized_priority_counts = {str(key): int_value(value) for key, value in priority_counts.items() if int_value(value) > 0}
    if normalized_priority_counts != expected_priority_counts:
        failures.append(f"{snapshot_path}: data_health.approval_blocker_summary confirmation_priority_counts must match visible blockers")
    expected_deadline, expected_deadline_symbols = next_confirmation_deadline(
        approval_blocker_rows(sources, "approval_blocked_confirmation_gaps")
    )
    if summary.get("next_confirmation_deadline") != expected_deadline:
        failures.append(f"{snapshot_path}: data_health.approval_blocker_summary next_confirmation_deadline must match visible blockers")
    if (summary.get("next_confirmation_symbols") or []) != expected_deadline_symbols:
        failures.append(f"{snapshot_path}: data_health.approval_blocker_summary next_confirmation_symbols must match visible blockers")
    return failures


def visible_approval_blockers(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    blockers: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").upper()
        ticket_id = str(row.get("ticket_id") or "").strip()
        key = ticket_id or symbol
        if not key:
            continue
        blocker = blockers.setdefault(key, {"symbol": symbol, "approval_blocking_checks": set()})
        if symbol and not blocker.get("symbol"):
            blocker["symbol"] = symbol
        for check in row.get("approval_blocking_checks") or []:
            check_name = str(check or "")
            if check_name:
                blocker["approval_blocking_checks"].add(check_name)
    return blockers


def visible_blocker_open_check_counts(blockers: dict[str, dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for blocker in blockers.values():
        for check in blocker.get("approval_blocking_checks") or set():
            counts[check] = counts.get(check, 0) + 1
    return dict(sorted(counts.items()))


def count_nested_values(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for value in row.get(key) or []:
            item = str(value or "")
            if item:
                counts[item] = counts.get(item, 0) + 1
    return dict(sorted(counts.items()))


def count_scalar_values(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        item = str(row.get(key) or "")
        if item:
            counts[item] = counts.get(item, 0) + 1
    return dict(sorted(counts.items()))


def next_confirmation_deadline(rows: list[dict[str, Any]]) -> tuple[str | None, list[str]]:
    symbols_by_deadline: dict[str, set[str]] = {}
    for row in rows:
        deadline = str(row.get("confirmation_deadline") or "")
        symbol = str(row.get("symbol") or "").upper()
        if not deadline:
            continue
        symbols_by_deadline.setdefault(deadline, set())
        if symbol:
            symbols_by_deadline[deadline].add(symbol)
    deadline = min(symbols_by_deadline) if symbols_by_deadline else None
    return deadline, sorted(symbols_by_deadline.get(deadline, set())) if deadline else []


def source_approval_blocker_queue_failures(source: dict[str, Any], label: str, snapshot_path: Path) -> list[str]:
    blocker_specs = [
        (
            "approval_blocked_external_gap_count",
            "approval_blocked_external_gaps",
            "provider gap",
        ),
        (
            "approval_blocked_confirmation_gap_count",
            "approval_blocked_confirmation_gaps",
            "earnings confirmation",
        ),
    ]
    failures: list[str] = []
    for count_key, rows_key, blocker_type in blocker_specs:
        reported_count = int_value(source.get(count_key))
        raw_rows = source.get(rows_key)
        rows = raw_rows if isinstance(raw_rows, list) else []
        if reported_count <= 0 and not rows:
            continue
        if reported_count > 0 and not rows:
            failures.append(
                f"{snapshot_path}: data_health source {label} {rows_key} requires visible blocker rows "
                f"when {count_key} is {reported_count}"
            )
            continue
        if reported_count < len(rows):
            failures.append(
                f"{snapshot_path}: data_health source {label} {rows_key} count is "
                f"{reported_count}; expected at least {len(rows)} visible blockers"
            )
        if not all(
            isinstance(row, dict)
            and row.get("symbol")
            and row.get("ticket_id")
            and row.get("approval_gate_status")
            and int_value(row.get("approval_open_check_count")) > 0
            and isinstance(row.get("approval_blocking_checks"), list)
            and row.get("approval_blocking_checks")
            for row in rows
        ):
            failures.append(
                f"{snapshot_path}: data_health source {label} {rows_key} rows require symbol, ticket id, "
                "approval gate status, open check count, and blocking checks"
            )
        if blocker_type == "provider gap" and not all(
            isinstance(row, dict)
            and int_value(row.get("provider_gap_count")) > 0
            and isinstance(row.get("provider_gap_sources"), list)
            and row.get("provider_gap_sources")
            for row in rows
        ):
            failures.append(
                f"{snapshot_path}: data_health source {label} {rows_key} rows require provider gap sources and counts"
            )
        if blocker_type == "earnings confirmation" and not all(
            isinstance(row, dict)
            and row.get("event_date")
            and row.get("confirmation_deadline")
            and row.get("confirmation_priority")
            for row in rows
        ):
            failures.append(
                f"{snapshot_path}: data_health source {label} {rows_key} rows require event date, "
                "confirmation deadline, and confirmation priority"
            )
    return failures


def approval_blocker_rows(sources: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in sources:
        for row in source.get(key) or []:
            if isinstance(row, dict):
                rows.append(row)
    return rows


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
    approval_projection = ((payload.get("outcome_diagnostics") or {}).get("approval_learning_readiness_projection") or {})
    if int_value(approval_projection.get("pending_approval_label_count")) > 0:
        approval_dates = [
            str(approval_projection.get(key) or "")
            for key in ("next_approval_label_due_date", "next_approval_learning_label_due_date")
            if approval_projection.get(key)
        ]
        if "Approval-gated learning labels" not in detail or not any(date_text in detail for date_text in approval_dates):
            return [f"{snapshot_path}: Learning reranker audit gap must include approval-gated label projection"]
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


def float_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def assert_public_snapshot_quality(web_dir: Path = Path("web")) -> None:
    failures = public_snapshot_quality_failures(web_dir)
    if failures:
        raise RuntimeError("\n".join(failures))
