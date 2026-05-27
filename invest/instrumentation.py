from __future__ import annotations

from typing import Any

from .symbols import equivalent_symbols


INSTRUMENTATION_AUDIT_VERSION = "2026-05-source-blocker-audit-v1"
TOLERANCE = 0.00001
WEAK_SOURCE_STATUSES = {"missing", "stale", "limited", "estimated", "unknown", "failed", "error"}
APPROVAL_DATA_FRICTION_FEATURE_VERSION = "2026-05-ml-feature-matrix-v5"
EXTERNAL_RELIABILITY_FIELDS = [
    "external_signal_score",
    "coverage_adjusted_external_signal_score",
    "external_coverage_multiplier",
    "external_feed_status",
    "external_provider_count",
    "external_provider_ok_count",
    "external_provider_ok_ratio",
    "external_provider_gap_count",
    "external_provider_configuration_gap_count",
    "external_provider_transient_gap_count",
    "external_provider_stale_gap_count",
    "external_provider_runtime_gap_count",
    "external_provider_other_gap_count",
    "external_provider_primary_gap_severity",
    "external_provider_gap_severity_score",
    "external_signal_count",
    "external_source_count",
]
EARNINGS_ACTION_FIELDS = [
    "earnings_days_until",
    "earnings_event_date",
    "earnings_event_source",
    "earnings_confirmed_or_estimated",
    "earnings_risk_window",
    "earnings_confirmation_required",
]
APPROVAL_TICKET_ACTION_METADATA_FIELDS = EARNINGS_ACTION_FIELDS + EXTERNAL_RELIABILITY_FIELDS
EARNINGS_OUTCOME_FIELDS = [
    "earnings_event_status",
    "earnings_confirmation_bucket",
    "earnings_confirmation_required",
]
APPROVAL_OUTCOME_FIELDS = [
    "approval_required",
    "approval_gate_status",
    "approval_open_check_count",
    "approval_blocking_checks",
    "approval_blocker_bucket",
]
TRAINING_EXAMPLE_APPROVAL_FIELDS = [
    "approval_required",
    "approval_gate_status",
    "approval_open_check_count",
    "approval_blocking_checks",
]
APPROVAL_DATA_FRICTION_FEATURE_FIELDS = [
    "approval_data_friction_score",
    "approval_data_friction_bucket",
    "approval_data_friction_reasons",
]
APPROVAL_DATA_FRICTION_RESEARCH_FIELDS = APPROVAL_DATA_FRICTION_FEATURE_FIELDS + [
    "approval_data_friction_penalty",
]
APPROVAL_DATA_FRICTION_OUTCOME_FIELDS = APPROVAL_DATA_FRICTION_RESEARCH_FIELDS
APPROVAL_CHECK_FIELDS = ["check", "status", "detail"]


def build_instrumentation_audit(payload: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    checks.extend(count_checks(payload))
    checks.extend(symbol_linkage_checks(payload))
    checks.extend(weight_math_checks(payload))
    checks.extend(engine_wiring_checks(payload))
    checks.extend(return_wiring_checks(payload))
    checks.extend(backtest_wiring_checks(payload))
    checks.extend(external_signal_schema_checks(payload))
    checks.extend(external_reliability_wiring_checks(payload))
    checks.extend(approval_data_friction_feature_schema_checks(payload))
    checks.extend(earnings_action_wiring_checks(payload))
    checks.extend(approval_ticket_action_metadata_checks(payload))
    checks.extend(approval_ticket_check_schema_checks(payload))
    checks.extend(training_example_approval_schema_checks(payload))
    checks.extend(backtest_external_schema_checks(payload))
    checks.extend(backtest_earnings_schema_checks(payload))
    checks.extend(backtest_approval_schema_checks(payload))
    checks.extend(backtest_approval_data_friction_schema_checks(payload))
    checks.extend(backtest_label_schedule_checks(payload))
    checks.extend(approval_blocker_summary_checks(payload))
    checks.extend(audit_source_gap_approval_context_checks(payload))
    failures = [check for check in checks if check.get("status") != "ok"]
    backtest = payload.get("backtest") or {}
    return {
        "version": INSTRUMENTATION_AUDIT_VERSION,
        "as_of": payload.get("as_of", ""),
        "session": payload.get("session", ""),
        "status": "ok" if not failures else "attention",
        "check_count": len(checks),
        "failure_count": len(failures),
        "checks": checks,
        "failures": failures[:20],
        "prediction_provenance": {
            "policy": "deterministic_scenario_sizing",
            "model_policy_version": ((payload.get("engine") or {}).get("version") or ""),
            "ml_model_active": False,
            "completed_backtest_label_count": int(backtest.get("completed_outcome_count") or 0),
            "note": "Expected returns and target weights are deterministic model outputs until enough forward labels mature for ML calibration.",
        },
    }


def count_checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    feature = payload.get("feature_matrix") or {}
    company = payload.get("company_underwriting") or {}
    sector = payload.get("sector_underwriting") or {}
    research = payload.get("research_book") or {}
    benchmark = payload.get("portfolio_benchmark") or {}
    sizing = benchmark.get("sizing_plan") or {}
    action_queue = benchmark.get("action_queue") or []
    tickets = payload.get("approval_tickets") or []
    portfolio_rows = (payload.get("portfolio") or {}).get("by_symbol") or []
    checks = [
        check_equal("feature_count_matches_rows", feature.get("feature_count"), len(feature.get("rows") or [])),
        check_equal("research_count_matches_items", research.get("item_count"), len(research.get("items") or [])),
        check_equal("sizing_target_count_matches_rows", sizing.get("target_count"), len(sizing.get("targets") or [])),
        check_equal("sizing_action_count_matches_queue", sizing.get("action_count"), len(action_queue)),
        check_equal("approval_ticket_count_matches_actions", len(tickets), len(action_queue)),
        check_close("portfolio_weights_sum_to_100_pct", sum(float(row.get("weight") or 0) for row in portfolio_rows), 1.0),
    ]
    if "company_underwriting" in payload:
        checks.append(check_equal("company_underwriting_count_matches_items", company.get("item_count"), len(company.get("items") or [])))
    if "sector_underwriting" in payload:
        checks.append(check_equal("sector_underwriting_count_matches_items", sector.get("item_count"), len(sector.get("items") or [])))
    engine = payload.get("engine") or {}
    if feature.get("feature_count") is not None and engine.get("feature_count") is not None:
        checks.append(check_equal("engine_feature_count_matches_feature_matrix", engine.get("feature_count"), feature.get("feature_count")))
    return checks


def symbol_linkage_checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    feature_symbols = symbol_set((payload.get("feature_matrix") or {}).get("rows") or [])
    company_symbols = symbol_set((payload.get("company_underwriting") or {}).get("items") or [])
    research_symbols = symbol_set((payload.get("research_book") or {}).get("items") or [])
    sizing = ((payload.get("portfolio_benchmark") or {}).get("sizing_plan") or {})
    target_symbols = symbol_set(sizing.get("targets") or [])
    action_symbols = symbol_set((payload.get("portfolio_benchmark") or {}).get("action_queue") or [])
    ticket_symbols = symbol_set(payload.get("approval_tickets") or [])
    engine_symbols = symbol_set((payload.get("engine") or {}).get("ranked_candidates") or [])
    checks = [
        check_subset("research_symbols_have_features", research_symbols, feature_symbols),
        check_subset("sizing_targets_have_research", target_symbols, research_symbols),
        check_subset("actions_have_sizing_targets", action_symbols, target_symbols),
        check_subset("tickets_have_actions", ticket_symbols, action_symbols),
        check_subset("engine_ranked_symbols_have_features", engine_symbols, feature_symbols),
    ]
    if company_symbols:
        checks.insert(0, check_subset("feature_symbols_have_company_underwriting", feature_symbols, company_symbols))
        checks.insert(2, check_subset("research_symbols_have_company_underwriting", research_symbols, company_symbols))
    return checks


def weight_math_checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    benchmark = payload.get("portfolio_benchmark") or {}
    sizing = benchmark.get("sizing_plan") or {}
    limits = sizing.get("limits") or {}
    max_delta = float(limits.get("max_one_ticket_delta") or 1)
    max_turnover = float(limits.get("max_daily_turnover") or 1)
    max_single = float(limits.get("max_single_name_weight") or 1)
    checks: list[dict[str, Any]] = []
    action_queue = benchmark.get("action_queue") or []
    research_by_symbol = {str(row.get("symbol") or "").upper(): row for row in (payload.get("research_book") or {}).get("items") or []}
    bottom_up_active = bool(payload.get("company_underwriting"))
    for action in action_queue:
        symbol = str(action.get("symbol") or "")
        current = float(action.get("current_weight", action.get("portfolio_weight") or 0) or 0)
        delta = float(action.get("recommended_delta_weight") or 0)
        post = float(action.get("post_action_weight") or 0)
        target = float(action.get("target_weight") or 0)
        trade_target = float(action.get("trade_target_weight", target) or 0)
        model_target = float(action.get("model_target_weight", post) or 0)
        max_allowed = float(action.get("max_allowed_weight", max_single) or max_single)
        trade_action = str(action.get("trade_action") or "")
        checks.append(check_close(f"{symbol}_post_equals_current_plus_delta", post, current + delta))
        checks.append(check_close(f"{symbol}_target_is_trade_target", target, post))
        checks.append(check_close(f"{symbol}_trade_target_matches_post_action", trade_target, post))
        checks.append(check_lte(f"{symbol}_ticket_delta_within_cap", abs(delta), max_delta))
        if current > max_single and delta <= 0:
            checks.append(check_lte(f"{symbol}_overweight_position_is_not_increased", post, current))
        else:
            checks.append(check_lte(f"{symbol}_post_action_within_single_name_cap", post, max_single))
        checks.append(check_lte(f"{symbol}_model_target_within_max_allowed", model_target, max_allowed))
        checks.append(check_zero_delta_action(symbol, delta, trade_action))
        if bottom_up_active:
            checks.extend(required_action_field_checks(symbol, action))
        research = research_by_symbol.get(symbol)
        if research and action.get("risk_adjusted_expected_return") is not None and research.get("risk_adjusted_expected_return") is not None:
            checks.append(check_close(f"{symbol}_expected_return_traces_to_research", action.get("risk_adjusted_expected_return"), research.get("risk_adjusted_expected_return")))
        if bottom_up_active and trade_action == "add":
            checks.append(check_truthy(f"{symbol}_add_has_bottom_up_evidence", action.get("company_add_eligible")))
            checks.append(check_truthy(f"{symbol}_add_has_funding_source", action.get("funding_source")))
    turnover = sum(abs(float(row.get("recommended_delta_weight") or 0)) for row in action_queue)
    checks.append(check_lte("action_queue_turnover_within_daily_cap", turnover, max_turnover))
    adds = sum(max(0.0, float(row.get("recommended_delta_weight") or 0)) for row in action_queue)
    trims = sum(abs(min(0.0, float(row.get("recommended_delta_weight") or 0))) for row in action_queue)
    budget = sizing.get("rebalance_budget") or {}
    cash_available = float(budget.get("max_cash_deploy_weight", sizing.get("cash_deployable_weight") or 0) or 0)
    checks.append(check_lte("action_queue_adds_are_funded_by_trims_or_cash", adds, trims + cash_available))
    sizing = benchmark.get("sizing_plan") or {}
    model_target_total = sum(float(row.get("model_target_weight") or 0) for row in sizing.get("targets") or [])
    current_total = sum(float(row.get("current_weight") or 0) for row in sizing.get("targets") or [])
    target_total = sizing.get("target_total_weight", current_total)
    if sizing.get("targets"):
        checks.append(check_lte("model_targets_do_not_exceed_target_public_equity_weight", model_target_total, target_total))
    portfolio = payload.get("portfolio") or {}
    if portfolio.get("cash_weight") is not None and portfolio.get("equity_weight") is not None:
        checks.append(check_close(
            "portfolio_cash_plus_equity_weight_matches_total",
            float(portfolio.get("cash_weight") or 0) + float(portfolio.get("equity_weight") or 0),
            sum(float(row.get("weight") or 0) for row in portfolio.get("by_symbol") or []),
        ))
    if budget:
        checks.append(check_close("rebalance_budget_add_sum_matches_actions", budget.get("total_add_weight"), adds))
        checks.append(check_close("rebalance_budget_trim_sum_matches_actions", budget.get("total_trim_weight"), trims))
        checks.append(check_close("rebalance_budget_net_delta_matches_actions", budget.get("net_delta_weight"), adds - trims))
        checks.append(check_close("rebalance_budget_cash_deployed_matches_net_add", budget.get("cash_deployed_weight"), max(0.0, adds - trims)))
        checks.append(check_close(
            "rebalance_budget_post_cash_matches_sources",
            budget.get("post_trade_cash_weight"),
            float(budget.get("starting_cash_weight") or 0) - float(budget.get("cash_deployed_weight") or 0) + float(budget.get("cash_raised_weight") or 0),
        ))

    action_by_symbol = {str(row.get("symbol") or "").upper(): row for row in action_queue}
    for ticket in payload.get("approval_tickets") or []:
        symbol = str(ticket.get("symbol") or "").upper()
        action = action_by_symbol.get(symbol)
        if not action:
            continue
        for key in ("current_weight", "recommended_delta_weight", "post_action_weight", "trade_target_weight", "target_weight", "model_target_weight"):
            checks.append(check_close(f"{symbol}_ticket_{key}_mirrors_action", ticket.get(key), action.get(key)))
    return checks


def engine_wiring_checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    ticket_by_symbol = {str(row.get("symbol") or "").upper(): row for row in payload.get("approval_tickets") or []}
    engine = payload.get("engine") or {}
    for row in (engine.get("optimizer") or {}).get("allocations") or []:
        symbol = str(row.get("symbol") or "").upper()
        ticket = ticket_by_symbol.get(symbol)
        if not ticket:
            continue
        checks.append(check_close(f"{symbol}_optimizer_delta_mirrors_ticket", row.get("recommended_delta_weight"), ticket.get("recommended_delta_weight")))
        checks.append(check_close(f"{symbol}_optimizer_target_mirrors_ticket", row.get("target_weight"), ticket.get("target_weight")))
        checks.append(check_close(f"{symbol}_optimizer_model_target_mirrors_ticket", row.get("model_target_weight"), ticket.get("model_target_weight")))
    for row in engine.get("recommendation_provenance") or []:
        symbol = str(row.get("symbol") or "").upper()
        ticket = ticket_by_symbol.get(symbol)
        if not ticket:
            continue
        checks.append(check_close(f"{symbol}_provenance_delta_mirrors_ticket", row.get("recommended_delta_weight"), ticket.get("recommended_delta_weight")))
        checks.append(check_close(f"{symbol}_provenance_target_mirrors_ticket", row.get("target_weight"), ticket.get("target_weight")))
    return checks


def return_wiring_checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    benchmark = payload.get("portfolio_benchmark") or {}
    rows = benchmark.get("horizon_returns") or []
    primary_key = benchmark.get("primary_horizon")
    primary = next((row for row in rows if row.get("key") == primary_key), None)
    if not primary:
        return [fail("primary_horizon_has_matching_return_row", {"primary_horizon": primary_key})]
    return [
        check_close("primary_portfolio_return_matches_horizon_row", benchmark.get("primary_portfolio_return"), primary.get("portfolio_return")),
        check_close("primary_price_coverage_matches_horizon_row", benchmark.get("primary_price_coverage_pct"), primary.get("price_coverage_pct")),
    ]


def backtest_wiring_checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    backtest = payload.get("backtest") or {}
    outcomes = backtest.get("outcomes") or []
    completed = [row for row in outcomes if row.get("status") == "complete"]
    pending = [row for row in outcomes if row.get("status") == "pending"]
    missing = [row for row in outcomes if row.get("status") == "missing_price"]
    return [
        check_equal("backtest_outcome_count_matches_rows", backtest.get("outcome_count"), len(outcomes)),
        check_equal("backtest_completed_count_matches_rows", backtest.get("completed_outcome_count"), len(completed)),
        check_equal("backtest_pending_count_matches_rows", backtest.get("pending_outcome_count"), len(pending)),
        check_equal("backtest_missing_price_count_matches_rows", backtest.get("missing_price_count"), len(missing)),
    ]


def external_signal_schema_checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    external = payload.get("external_signals") or {}
    provider_count = int(external.get("provider_count") or 0)
    if not provider_count:
        return []
    return [
        check_present("external_signals_provider_ok_count_present", external.get("provider_ok_count")),
        check_present("external_signals_provider_ok_ratio_present", external.get("provider_ok_ratio")),
        check_present("external_signals_provider_status_counts_present", external.get("provider_status_counts")),
    ]


def external_reliability_wiring_checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    external = payload.get("external_signals") or {}
    provider_count = int(external.get("provider_count") or 0)
    if not provider_count:
        return []
    feature_rows = (payload.get("feature_matrix") or {}).get("rows") or []
    engine_rows = (payload.get("engine") or {}).get("ranked_candidates") or []
    action_rows = ((payload.get("portfolio_benchmark") or {}).get("action_queue") or [])
    return [
        check_rows_have_fields("feature_matrix_external_reliability_fields_present", feature_rows, EXTERNAL_RELIABILITY_FIELDS),
        check_rows_have_fields("engine_external_reliability_fields_present", engine_rows, EXTERNAL_RELIABILITY_FIELDS),
        check_rows_have_fields("action_queue_external_reliability_fields_present", action_rows, EXTERNAL_RELIABILITY_FIELDS),
        check_engine_external_reliability_mirrors_features(engine_rows, feature_rows),
    ]


def check_rows_have_fields(name: str, rows: list[dict[str, Any]], fields: list[str]) -> dict[str, Any]:
    missing: list[dict[str, str]] = []
    for row in rows:
        symbol = str(row.get("symbol") or "unknown").upper()
        for field in fields:
            if not value_present(row.get(field)):
                missing.append({"symbol": symbol, "field": field})
    return {
        "name": name,
        "status": "ok" if not missing else "fail",
        "row_count": len(rows),
        "missing_count": len(missing),
        "missing_sample": missing[:10],
        "expected_fields": fields,
    }


def check_rows_include_fields(name: str, rows: list[dict[str, Any]], fields: list[str]) -> dict[str, Any]:
    missing: list[dict[str, str]] = []
    for row in rows:
        symbol = str(row.get("symbol") or "unknown").upper()
        for field in fields:
            if field not in row:
                missing.append({"symbol": symbol, "field": field})
    return {
        "name": name,
        "status": "ok" if not missing else "fail",
        "row_count": len(rows),
        "missing_count": len(missing),
        "missing_sample": missing[:10],
        "expected_fields": fields,
    }


def check_engine_external_reliability_mirrors_features(engine_rows: list[dict[str, Any]], feature_rows: list[dict[str, Any]]) -> dict[str, Any]:
    features_by_symbol = {str(row.get("symbol") or "").upper(): row for row in feature_rows if row.get("symbol")}
    mismatches: list[dict[str, Any]] = []
    for row in engine_rows:
        symbol = str(row.get("symbol") or "").upper()
        feature = features_by_symbol.get(symbol)
        if not feature:
            continue
        for field in EXTERNAL_RELIABILITY_FIELDS:
            observed = row.get(field)
            expected = feature.get(field)
            if not values_match(observed, expected):
                mismatches.append({"symbol": symbol, "field": field, "observed": observed, "expected": expected})
    return {
        "name": "engine_external_reliability_mirrors_feature_matrix",
        "status": "ok" if not mismatches else "fail",
        "engine_row_count": len(engine_rows),
        "mismatch_count": len(mismatches),
        "mismatch_sample": mismatches[:10],
    }


def backtest_external_schema_checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    backtest = payload.get("backtest") or {}
    completed_count = int(backtest.get("completed_outcome_count") or 0)
    if not completed_count:
        return []
    status_groups = backtest.get("by_external_feed_status")
    coverage_groups = backtest.get("by_external_coverage")
    return [
        check_non_empty("backtest_external_feed_status_groups_present", status_groups),
        check_non_empty("backtest_external_coverage_groups_present", coverage_groups),
        check_equal(
            "backtest_external_feed_status_count_matches_completed",
            group_completed_count(status_groups),
            completed_count,
        ),
        check_equal(
            "backtest_external_coverage_count_matches_completed",
            group_completed_count(coverage_groups),
            completed_count,
        ),
    ]


def backtest_earnings_schema_checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    backtest = payload.get("backtest") or {}
    outcomes = [row for row in backtest.get("outcomes") or [] if isinstance(row, dict)]
    has_earnings_schema = (
        str(backtest.get("version") or "") == "2026-05-recommendation-backtest-v4"
        or any("earnings_event_status" in row or "earnings_confirmation_bucket" in row for row in outcomes)
        or any(
            backtest.get(key) is not None
            for key in (
                "by_earnings_event_status",
                "by_earnings_risk_window",
                "by_earnings_confirmation_bucket",
                "pending_by_earnings_event_status",
                "pending_by_earnings_risk_window",
                "pending_by_earnings_confirmation_bucket",
            )
        )
    )
    if not has_earnings_schema:
        return []
    completed_count = int(backtest.get("completed_outcome_count") or 0)
    pending_count = int(backtest.get("pending_outcome_count") or 0)
    checks = [check_rows_have_fields("backtest_outcomes_earnings_learning_fields_present", outcomes, EARNINGS_OUTCOME_FIELDS)]
    if completed_count:
        checks.extend(
            backtest_group_count_checks(
                [
                    ("backtest_earnings_event_status_groups_present", "backtest_earnings_event_status_count_matches_completed", "by_earnings_event_status"),
                    ("backtest_earnings_risk_window_groups_present", "backtest_earnings_risk_window_count_matches_completed", "by_earnings_risk_window"),
                    ("backtest_earnings_confirmation_bucket_groups_present", "backtest_earnings_confirmation_bucket_count_matches_completed", "by_earnings_confirmation_bucket"),
                ],
                backtest,
                expected_count=completed_count,
                count_key="completed_count",
            )
        )
    if pending_count:
        checks.extend(
            backtest_group_count_checks(
                [
                    ("backtest_pending_earnings_event_status_groups_present", "backtest_pending_earnings_event_status_count_matches_pending", "pending_by_earnings_event_status"),
                    ("backtest_pending_earnings_risk_window_groups_present", "backtest_pending_earnings_risk_window_count_matches_pending", "pending_by_earnings_risk_window"),
                    ("backtest_pending_earnings_confirmation_bucket_groups_present", "backtest_pending_earnings_confirmation_bucket_count_matches_pending", "pending_by_earnings_confirmation_bucket"),
                ],
                backtest,
                expected_count=pending_count,
                count_key="pending_count",
            )
        )
    return checks


def backtest_approval_schema_checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    backtest = payload.get("backtest") or {}
    outcomes = [row for row in backtest.get("outcomes") or [] if isinstance(row, dict)]
    has_approval_schema = (
        str(backtest.get("version") or "") == "2026-05-recommendation-backtest-v4"
        or any("approval_blocker_bucket" in row or "approval_gate_status" in row for row in outcomes)
        or any(
            backtest.get(key) is not None
            for key in (
                "by_approval_gate_status",
                "by_approval_blocker_bucket",
                "pending_by_approval_gate_status",
                "pending_by_approval_blocker_bucket",
            )
        )
    )
    if not has_approval_schema:
        return []
    completed_count = int(backtest.get("completed_outcome_count") or 0)
    pending_count = int(backtest.get("pending_outcome_count") or 0)
    checks = [check_rows_include_fields("backtest_outcomes_approval_learning_fields_present", outcomes, APPROVAL_OUTCOME_FIELDS)]
    if completed_count:
        checks.extend(
            backtest_group_count_checks(
                [
                    ("backtest_approval_gate_status_groups_present", "backtest_approval_gate_status_count_matches_completed", "by_approval_gate_status"),
                    ("backtest_approval_blocker_bucket_groups_present", "backtest_approval_blocker_bucket_count_matches_completed", "by_approval_blocker_bucket"),
                ],
                backtest,
                expected_count=completed_count,
                count_key="completed_count",
            )
        )
    if pending_count:
        checks.extend(
            backtest_group_count_checks(
                [
                    ("backtest_pending_approval_gate_status_groups_present", "backtest_pending_approval_gate_status_count_matches_pending", "pending_by_approval_gate_status"),
                    ("backtest_pending_approval_blocker_bucket_groups_present", "backtest_pending_approval_blocker_bucket_count_matches_pending", "pending_by_approval_blocker_bucket"),
                ],
                backtest,
                expected_count=pending_count,
                count_key="pending_count",
            )
        )
    return checks


def approval_data_friction_feature_schema_checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    feature = payload.get("feature_matrix") or {}
    feature_rows = [row for row in feature.get("rows") or [] if isinstance(row, dict)]
    research_rows = [
        row for row in ((payload.get("research_book") or {}).get("items") or [])
        if isinstance(row, dict)
    ]
    has_friction_schema = (
        str(feature.get("version") or "") == APPROVAL_DATA_FRICTION_FEATURE_VERSION
        or any(any(field in row for field in APPROVAL_DATA_FRICTION_FEATURE_FIELDS) for row in feature_rows)
        or any(any(field in row for field in APPROVAL_DATA_FRICTION_RESEARCH_FIELDS) for row in research_rows)
    )
    if not has_friction_schema:
        return []
    checks = [
        check_rows_have_fields(
            "feature_matrix_approval_data_friction_fields_present",
            feature_rows,
            APPROVAL_DATA_FRICTION_FEATURE_FIELDS,
        )
    ]
    if research_rows:
        checks.append(
            check_rows_have_fields(
                "research_book_approval_data_friction_fields_present",
                research_rows,
                APPROVAL_DATA_FRICTION_RESEARCH_FIELDS,
            )
        )
    return checks


def backtest_approval_data_friction_schema_checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    feature = payload.get("feature_matrix") or {}
    backtest = payload.get("backtest") or {}
    outcomes = [row for row in backtest.get("outcomes") or [] if isinstance(row, dict)]
    has_friction_schema = (
        str(feature.get("version") or "") == APPROVAL_DATA_FRICTION_FEATURE_VERSION
        or any(any(field in row for field in APPROVAL_DATA_FRICTION_OUTCOME_FIELDS) for row in outcomes)
        or any(
            backtest.get(key) is not None
            for key in ("by_approval_data_friction_bucket", "pending_by_approval_data_friction_bucket")
        )
    )
    if not has_friction_schema:
        return []
    completed_count = int(backtest.get("completed_outcome_count") or 0)
    pending_count = int(backtest.get("pending_outcome_count") or 0)
    checks = [
        check_rows_include_fields(
            "backtest_outcomes_approval_data_friction_fields_present",
            outcomes,
            APPROVAL_DATA_FRICTION_OUTCOME_FIELDS,
        ),
        check_equal(
            "backtest_pending_approval_data_friction_unknown_context_count_zero",
            approval_data_friction_unknown_context_count([row for row in outcomes if row.get("status") == "pending"]),
            0,
        ),
    ]
    if completed_count:
        checks.extend(
            backtest_group_count_checks(
                [
                    (
                        "backtest_approval_data_friction_groups_present",
                        "backtest_approval_data_friction_count_matches_completed",
                        "by_approval_data_friction_bucket",
                    ),
                ],
                backtest,
                expected_count=completed_count,
                count_key="completed_count",
            )
        )
    if pending_count:
        checks.extend(
            backtest_group_count_checks(
                [
                    (
                        "backtest_pending_approval_data_friction_groups_present",
                        "backtest_pending_approval_data_friction_count_matches_pending",
                        "pending_by_approval_data_friction_bucket",
                    ),
                ],
                backtest,
                expected_count=pending_count,
                count_key="pending_count",
            )
        )
    return checks


def approval_data_friction_unknown_context_count(rows: list[dict[str, Any]]) -> int:
    return sum(
        1
        for row in rows
        if str(row.get("approval_data_friction_bucket") or "").strip().lower() == "unknown"
        and approval_data_friction_context_present(row)
    )


def approval_data_friction_context_present(row: dict[str, Any]) -> bool:
    context_fields = (
        "external_feed_status",
        "external_coverage_multiplier",
        "external_provider_count",
        "external_provider_ok_count",
        "external_provider_ok_ratio",
        "earnings_days_until",
        "earnings_event_date",
        "earnings_event_source",
        "earnings_confirmed_or_estimated",
        "earnings_risk_window",
        "earnings_confirmation_required",
        "company_review_required",
        "review_required",
    )
    return any(value_present(row.get(field)) for field in context_fields)


def backtest_group_count_checks(
    specs: list[tuple[str, str, str]],
    backtest: dict[str, Any],
    *,
    expected_count: int,
    count_key: str,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for present_name, count_name, key in specs:
        rows = backtest.get(key)
        checks.append(check_non_empty(present_name, rows))
        checks.append(check_equal(count_name, group_count(rows, count_key), expected_count))
    return checks


def approval_ticket_action_metadata_checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    action_by_symbol = {
        str(row.get("symbol") or "").upper(): row
        for row in ((payload.get("portfolio_benchmark") or {}).get("action_queue") or [])
        if isinstance(row, dict) and row.get("symbol")
    }
    mismatches: list[dict[str, Any]] = []
    for ticket in payload.get("approval_tickets") or []:
        if not isinstance(ticket, dict):
            continue
        symbol = str(ticket.get("symbol") or "").upper()
        action = action_by_symbol.get(symbol)
        if not action:
            continue
        for field in APPROVAL_TICKET_ACTION_METADATA_FIELDS:
            action_value = action.get(field)
            if not value_present(action_value):
                continue
            if not values_match(ticket.get(field), action_value):
                mismatches.append({"symbol": symbol, "field": field, "observed": ticket.get(field), "expected": action_value})
    return [
        {
            "name": "approval_tickets_mirror_action_metadata",
            "status": "ok" if not mismatches else "fail",
            "mismatch_count": len(mismatches),
            "mismatch_sample": mismatches[:10],
        }
    ]


def approval_ticket_check_schema_checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for ticket in payload.get("approval_tickets") or []:
        if not isinstance(ticket, dict):
            continue
        symbol = str(ticket.get("symbol") or "unknown").upper()
        risk_flags = ticket.get("risk_flags") or []
        if isinstance(risk_flags, str):
            risk_flags = [risk_flags]
        requires_confirmation = bool(ticket.get("earnings_confirmation_required")) or "earnings_confirmation_required" in risk_flags
        has_check_schema = any(field in ticket for field in ("approval_checks", "approval_open_check_count", "approval_gate_status"))
        if not requires_confirmation and not has_check_schema:
            continue
        ticket_checks = ticket.get("approval_checks")
        checks.append(check_non_empty(f"{symbol}_approval_ticket_checks_present", ticket_checks))
        checks.append(check_present(f"{symbol}_approval_ticket_gate_status_present", ticket.get("approval_gate_status")))
        checks.append(check_present(f"{symbol}_approval_ticket_open_check_count_present", ticket.get("approval_open_check_count")))
        if isinstance(ticket_checks, list):
            checks.append(check_rows_have_fields(f"{symbol}_approval_ticket_check_rows_have_schema", ticket_checks, APPROVAL_CHECK_FIELDS))
            expected_open_count = sum(1 for row in ticket_checks if isinstance(row, dict) and row.get("status") != "passed")
            checks.append(
                check_equal(
                    f"{symbol}_approval_ticket_open_check_count_matches_checks",
                    ticket.get("approval_open_check_count"),
                    expected_open_count,
                )
            )
        delta = abs(float(ticket.get("recommended_delta_weight") or 0))
        if requires_confirmation and delta > TOLERANCE:
            earnings_statuses = [
                row.get("status")
                for row in ticket_checks or []
                if isinstance(row, dict) and row.get("check") == "earnings_date_confirmed"
            ]
            checks.append(
                {
                    "name": f"{symbol}_approval_ticket_has_pending_earnings_confirmation_check",
                    "status": "ok" if any(status and status != "passed" for status in earnings_statuses) else "fail",
                    "observed": earnings_statuses,
                    "expected": "non-passed earnings_date_confirmed check",
                }
            )
            checks.append(
                check_equal(
                    f"{symbol}_approval_ticket_confirmation_gate_blocks",
                    ticket.get("approval_gate_status"),
                    "blocked_until_confirmation",
                )
            )
    return checks


def training_example_approval_schema_checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    examples = [
        row for row in payload.get("recommendation_training_examples") or []
        if isinstance(row, dict)
    ]
    if not examples:
        return []
    tickets = [
        row for row in payload.get("approval_tickets") or []
        if isinstance(row, dict)
    ]
    checks = [
        check_rows_include_fields(
            "training_examples_approval_learning_fields_present",
            examples,
            TRAINING_EXAMPLE_APPROVAL_FIELDS,
        )
    ]
    if not tickets:
        return checks
    checks.append(check_equal("training_example_count_matches_approval_tickets", len(examples), len(tickets)))
    ticket_by_symbol = {
        str(ticket.get("symbol") or "").upper(): ticket
        for ticket in tickets
        if ticket.get("symbol")
    }
    mismatches: list[dict[str, Any]] = []
    for example in examples:
        symbol = str(example.get("symbol") or "").upper()
        ticket = ticket_by_symbol.get(symbol)
        if not ticket:
            continue
        expected_blocking_checks = approval_ticket_blocking_check_names(ticket)
        comparisons = {
            "approval_required": bool(ticket.get("approval_required")),
            "approval_gate_status": str(ticket.get("approval_gate_status") or ""),
            "approval_open_check_count": ticket.get("approval_open_check_count"),
            "approval_blocking_checks": expected_blocking_checks,
        }
        for field, expected in comparisons.items():
            observed = sorted(example.get(field) or []) if field == "approval_blocking_checks" else example.get(field)
            if not values_match(observed, expected):
                mismatches.append({"symbol": symbol, "field": field, "observed": observed, "expected": expected})
    checks.append(
        {
            "name": "training_examples_mirror_approval_ticket_context",
            "status": "ok" if not mismatches else "fail",
            "mismatch_count": len(mismatches),
            "mismatch_sample": mismatches[:10],
        }
    )
    return checks


def approval_ticket_blocking_check_names(ticket: dict[str, Any]) -> list[str]:
    names = []
    for check in ticket.get("approval_checks") or []:
        if not isinstance(check, dict) or check.get("status") == "passed":
            continue
        name = str(check.get("check") or "").strip()
        if name:
            names.append(name)
    return sorted(set(names))


def approval_blocker_summary_checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data_health = payload.get("data_health") or {}
    sources = [row for row in data_health.get("sources") or [] if isinstance(row, dict)]
    external_count = sum(int(row.get("approval_blocked_external_gap_count") or 0) for row in sources)
    confirmation_count = sum(int(row.get("approval_blocked_confirmation_gap_count") or 0) for row in sources)
    if external_count + confirmation_count <= 0:
        return []
    summary = data_health.get("approval_blocker_summary")
    checks = [check_non_empty("data_health_approval_blocker_summary_present", summary)]
    if not isinstance(summary, dict):
        return checks

    external_rows = approval_blocker_rows(sources, "approval_blocked_external_gaps")
    confirmation_rows = approval_blocker_rows(sources, "approval_blocked_confirmation_gaps")
    visible_rows = external_rows + confirmation_rows
    visible_blockers = visible_approval_blockers(visible_rows)
    open_check_counts = visible_blocker_open_check_counts(visible_blockers)
    provider_gap_source_counts = count_nested_values(external_rows, "provider_gap_sources")
    confirmation_priority_counts = count_scalar_values(confirmation_rows, "confirmation_priority")
    next_deadline, next_symbols = next_confirmation_deadline(confirmation_rows)
    checks.extend(
        [
            check_equal("data_health_approval_blocker_summary_status_attention", summary.get("status"), "attention"),
            check_equal(
                "data_health_approval_blocker_summary_total_count_matches_sources",
                summary.get("total_source_blocker_count"),
                external_count + confirmation_count,
            ),
            check_equal(
                "data_health_approval_blocker_summary_external_count_matches_sources",
                summary.get("external_gap_ticket_count"),
                external_count,
            ),
            check_equal(
                "data_health_approval_blocker_summary_confirmation_count_matches_sources",
                summary.get("earnings_confirmation_ticket_count"),
                confirmation_count,
            ),
            check_equal(
                "data_health_approval_blocker_summary_visible_count_matches_rows",
                summary.get("visible_blocker_row_count"),
                len(visible_rows),
            ),
            check_equal(
                "data_health_approval_blocker_summary_blocked_ticket_count_matches_rows",
                summary.get("blocked_ticket_count"),
                len(visible_blockers),
            ),
            check_equal(
                "data_health_approval_blocker_summary_symbols_match_rows",
                summary.get("blocked_symbols"),
                sorted({row.get("symbol") for row in visible_blockers.values() if row.get("symbol")}),
            ),
            check_equal(
                "data_health_approval_blocker_summary_open_check_counts_match_rows",
                normalized_count_map(summary.get("open_check_counts")),
                open_check_counts,
            ),
            check_equal(
                "data_health_approval_blocker_summary_open_check_count_matches_rows",
                summary.get("open_check_count"),
                sum(open_check_counts.values()),
            ),
            check_equal(
                "data_health_approval_blocker_summary_provider_gap_counts_match_rows",
                normalized_count_map(summary.get("provider_gap_source_counts")),
                provider_gap_source_counts,
            ),
            check_equal(
                "data_health_approval_blocker_summary_confirmation_priority_counts_match_rows",
                normalized_count_map(summary.get("confirmation_priority_counts")),
                confirmation_priority_counts,
            ),
            check_equal(
                "data_health_approval_blocker_summary_next_confirmation_deadline_matches_rows",
                summary.get("next_confirmation_deadline"),
                next_deadline,
            ),
            check_equal(
                "data_health_approval_blocker_summary_next_confirmation_symbols_match_rows",
                summary.get("next_confirmation_symbols") or [],
                next_symbols,
            ),
        ]
    )
    return checks


def audit_source_gap_approval_context_checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data_health = payload.get("data_health") or {}
    sources = [row for row in data_health.get("sources") or [] if isinstance(row, dict)]
    audit_gaps = [
        row for row in ((payload.get("audit") or {}).get("data_gaps") or [])
        if isinstance(row, dict) and row.get("area") == "source"
    ]
    checks: list[dict[str, Any]] = []
    for source in sources:
        status = str(source.get("status") or "unknown")
        if status not in WEAK_SOURCE_STATUSES:
            continue
        external_rows = approval_blocker_rows([source], "approval_blocked_external_gaps")
        confirmation_rows = approval_blocker_rows([source], "approval_blocked_confirmation_gaps")
        action_linked_count = int(source.get("action_linked_confirmation_gap_count") or 0)
        if not external_rows and not confirmation_rows and action_linked_count <= 0:
            continue
        label = str(source.get("label") or source.get("source") or "unknown_source")
        suffix = source_check_suffix(label)
        gap = matching_audit_source_gap(audit_gaps, label, status)
        checks.append(check_non_empty(f"audit_source_gap_approval_context_present_{suffix}", gap))
        if not isinstance(gap, dict) or not gap:
            continue
        checks.extend(
            [
                check_equal(
                    f"audit_source_gap_external_blocker_count_matches_data_health_{suffix}",
                    int(gap.get("approval_blocked_external_gap_count") or 0),
                    int(source.get("approval_blocked_external_gap_count") or 0),
                ),
                check_equal(
                    f"audit_source_gap_external_blocker_rows_match_data_health_{suffix}",
                    len(approval_blocker_rows([gap], "approval_blocked_external_gaps")),
                    len(external_rows),
                ),
                check_equal(
                    f"audit_source_gap_action_confirmation_count_matches_data_health_{suffix}",
                    int(gap.get("action_linked_confirmation_gap_count") or 0),
                    action_linked_count,
                ),
                check_equal(
                    f"audit_source_gap_confirmation_blocker_count_matches_data_health_{suffix}",
                    int(gap.get("approval_blocked_confirmation_gap_count") or 0),
                    int(source.get("approval_blocked_confirmation_gap_count") or 0),
                ),
                check_equal(
                    f"audit_source_gap_confirmation_blocker_rows_match_data_health_{suffix}",
                    len(approval_blocker_rows([gap], "approval_blocked_confirmation_gaps")),
                    len(confirmation_rows),
                ),
            ]
        )
    return checks


def matching_audit_source_gap(audit_gaps: list[dict[str, Any]], label: str, status: str) -> dict[str, Any]:
    for gap in audit_gaps:
        if str(gap.get("label") or "") == label and str(gap.get("status") or "unknown") == status:
            return gap
    return {}


def source_check_suffix(label: str) -> str:
    suffix = "".join(char.lower() if char.isalnum() else "_" for char in label.strip())
    return "_".join(part for part in suffix.split("_") if part) or "source"


def approval_blocker_rows(sources: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in sources:
        for row in source.get(key) or []:
            if isinstance(row, dict):
                rows.append(row)
    return rows


def visible_approval_blockers(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    blockers: dict[str, dict[str, Any]] = {}
    for row in rows:
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


def normalized_count_map(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    counts: dict[str, int] = {}
    for key, count in value.items():
        try:
            normalized = int(count)
        except (TypeError, ValueError):
            continue
        item = str(key or "")
        if item and normalized > 0:
            counts[item] = normalized
    return dict(sorted(counts.items()))


def backtest_label_schedule_checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    backtest = payload.get("backtest") or {}
    pending_count = int(backtest.get("pending_outcome_count") or 0)
    if not pending_count:
        return []
    schedule = backtest.get("pending_label_schedule")
    checks = [
        check_non_empty("backtest_pending_label_schedule_present", schedule),
    ]
    if not isinstance(schedule, dict):
        return checks
    checks.append(
        check_equal(
            "backtest_pending_label_schedule_count_matches_pending",
            schedule.get("pending_label_count"),
            pending_count,
        )
    )
    next_label = schedule.get("next_label") or {}
    if next_label:
        checks.append(
            check_equal(
                "backtest_next_label_maturity_matches_schedule",
                backtest.get("next_label_maturity"),
                next_label,
            )
        )
        checks.append(
            check_equal(
                "backtest_next_label_maturity_date_matches_schedule",
                backtest.get("next_label_maturity_date"),
                next_label.get("due_date"),
            )
        )
    next_learning = schedule.get("next_learning_label") or {}
    if next_learning:
        checks.append(
            check_equal(
                "backtest_next_learning_label_maturity_matches_schedule",
                backtest.get("next_learning_label_maturity"),
                next_learning,
            )
        )
        checks.append(
            check_equal(
                "backtest_next_learning_label_maturity_date_matches_schedule",
                backtest.get("next_learning_label_maturity_date"),
                next_learning.get("due_date"),
            )
        )
    return checks


def earnings_action_wiring_checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    event_by_symbol = nearest_earnings_event_by_symbol(payload_earnings_events(payload))
    if not event_by_symbol:
        return []
    action_queue = ((payload.get("portfolio_benchmark") or {}).get("action_queue") or [])
    checks: list[dict[str, Any]] = []
    for action in action_queue:
        if not isinstance(action, dict):
            continue
        symbol = str(action.get("symbol") or "").upper()
        event = event_by_symbol.get(symbol)
        if not event or str(event.get("event_type") or "") != "earnings":
            continue
        checks.append(check_rows_have_fields(f"{symbol}_action_has_earnings_event_metadata", [action], EARNINGS_ACTION_FIELDS))
        if earnings_event_estimated(event) and abs(float(action.get("recommended_delta_weight") or 0)) > TOLERANCE:
            checks.append(check_truthy(f"{symbol}_estimated_earnings_trade_requires_confirmation_gate", action.get("earnings_confirmation_required")))
            checks.append(
                check_contains(
                    f"{symbol}_estimated_earnings_trade_has_confirmation_risk_flag",
                    action.get("risk_flags"),
                    "earnings_confirmation_required",
                )
            )
    return checks


def payload_earnings_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in payload.get("earnings_events") or []:
        if isinstance(row, dict):
            events.append(row)
    for row in (((payload.get("calendars") or {}).get("earnings") or {}).get("events") or []):
        if isinstance(row, dict):
            events.append(row)
    return events


def nearest_earnings_event_by_symbol(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_symbol: dict[str, dict[str, Any]] = {}
    for event in events:
        symbol = str(event.get("symbol") or "").upper()
        if not symbol:
            continue
        for candidate in equivalent_symbols(symbol):
            current = by_symbol.get(candidate)
            if current is None or event_sort_distance(event) < event_sort_distance(current):
                by_symbol[candidate] = event
    return by_symbol


def event_sort_distance(event: dict[str, Any]) -> int:
    try:
        return abs(int(event.get("days_until")))
    except (TypeError, ValueError):
        return 999999


def earnings_event_estimated(event: dict[str, Any]) -> bool:
    status = str(event.get("confirmed_or_estimated") or event.get("status") or "").strip().lower()
    if status:
        return status == "estimated"
    source = str(event.get("source") or "")
    return source not in {"manual", "company_ir_feed", "sec_company_submissions"}


def group_completed_count(rows: Any) -> int:
    if not isinstance(rows, list):
        return 0
    return sum(int(row.get("completed_count") or 0) for row in rows if isinstance(row, dict))


def group_count(rows: Any, key: str) -> int:
    if not isinstance(rows, list):
        return 0
    return sum(int(row.get(key) or 0) for row in rows if isinstance(row, dict))


def required_action_field_checks(symbol: str, action: dict[str, Any]) -> list[dict[str, Any]]:
    required = [
        "current_weight",
        "target_weight",
        "recommended_delta_weight",
        "funding_source",
        "risk_adjusted_expected_return",
        "confidence",
        "catalyst_clock",
        "company_reason",
        "sector_reason",
        "tertiary_signal_summary",
    ]
    return [check_present(f"{symbol}_action_has_{key}", action.get(key)) for key in required]


def symbol_set(rows: list[dict[str, Any]]) -> set[str]:
    return {str(row.get("symbol") or "").upper() for row in rows if row.get("symbol")}


def check_equal(name: str, observed: Any, expected: Any) -> dict[str, Any]:
    status = "ok" if observed == expected else "fail"
    return {"name": name, "status": status, "observed": observed, "expected": expected}


def check_close(name: str, observed: Any, expected: Any) -> dict[str, Any]:
    observed_float = as_float(observed)
    expected_float = as_float(expected)
    status = "ok" if observed_float is not None and expected_float is not None and abs(observed_float - expected_float) <= TOLERANCE else "fail"
    return {"name": name, "status": status, "observed": observed, "expected": expected}


def check_lte(name: str, observed: Any, expected: Any) -> dict[str, Any]:
    observed_float = as_float(observed)
    expected_float = as_float(expected)
    status = "ok" if observed_float is not None and expected_float is not None and observed_float <= expected_float + TOLERANCE else "fail"
    return {"name": name, "status": status, "observed": observed, "expected_max": expected}


def check_present(name: str, observed: Any) -> dict[str, Any]:
    ok = value_present(observed)
    return {"name": name, "status": "ok" if ok else "fail", "observed": observed, "expected": "present"}


def value_present(observed: Any) -> bool:
    if isinstance(observed, str):
        return bool(observed.strip())
    if isinstance(observed, (list, dict)):
        return True
    return observed is not None


def values_match(observed: Any, expected: Any) -> bool:
    observed_float = as_float(observed)
    expected_float = as_float(expected)
    if observed_float is not None or expected_float is not None:
        return observed_float is not None and expected_float is not None and abs(observed_float - expected_float) <= TOLERANCE
    return observed == expected


def check_non_empty(name: str, observed: Any) -> dict[str, Any]:
    ok = isinstance(observed, (list, dict, str)) and bool(observed)
    return {"name": name, "status": "ok" if ok else "fail", "observed": observed, "expected": "non_empty"}


def check_truthy(name: str, observed: Any) -> dict[str, Any]:
    return {"name": name, "status": "ok" if bool(observed) else "fail", "observed": observed, "expected": True}


def check_contains(name: str, observed: Any, expected_item: Any) -> dict[str, Any]:
    if isinstance(observed, list):
        ok = expected_item in observed
    elif isinstance(observed, str):
        ok = str(expected_item) == observed
    else:
        ok = False
    return {"name": name, "status": "ok" if ok else "fail", "observed": observed, "expected": expected_item}


def check_subset(name: str, observed: set[str], expected: set[str]) -> dict[str, Any]:
    missing = sorted(observed - expected)
    return {
        "name": name,
        "status": "ok" if not missing else "fail",
        "observed_count": len(observed),
        "expected_count": len(expected),
        "missing": missing,
    }


def check_zero_delta_action(symbol: str, delta: float, trade_action: str) -> dict[str, Any]:
    status = "ok"
    if abs(delta) <= TOLERANCE and trade_action in {"add", "trim"}:
        status = "fail"
    return {
        "name": f"{symbol}_zero_delta_not_add_or_trim",
        "status": status,
        "observed": trade_action,
        "expected": "hold_or_watch_when_delta_is_zero",
    }


def fail(name: str, detail: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "status": "fail", **detail}


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
