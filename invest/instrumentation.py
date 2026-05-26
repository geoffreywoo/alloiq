from __future__ import annotations

from typing import Any


INSTRUMENTATION_AUDIT_VERSION = "2026-05-external-reliability-wiring-v2"
TOLERANCE = 0.00001
EXTERNAL_RELIABILITY_FIELDS = [
    "external_signal_score",
    "coverage_adjusted_external_signal_score",
    "external_coverage_multiplier",
    "external_feed_status",
    "external_provider_count",
    "external_provider_ok_count",
    "external_provider_ok_ratio",
    "external_signal_count",
    "external_source_count",
]


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
    checks.extend(backtest_external_schema_checks(payload))
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
    return [
        check_rows_have_fields("feature_matrix_external_reliability_fields_present", feature_rows, EXTERNAL_RELIABILITY_FIELDS),
        check_rows_have_fields("engine_external_reliability_fields_present", engine_rows, EXTERNAL_RELIABILITY_FIELDS),
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


def group_completed_count(rows: Any) -> int:
    if not isinstance(rows, list):
        return 0
    return sum(int(row.get("completed_count") or 0) for row in rows if isinstance(row, dict))


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
