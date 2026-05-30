from __future__ import annotations

from typing import Any

from .features import MODEL_POLICY_VERSION
from .risk import normalize_limits
from .symbols import proxied_lookup, symbol_proxy_key


SIZING_VERSION = "2026-05-target-weight-sizing-v2"

CONSTRUCTIVE_CATALYST_EVENTS = {
    "capex_signal",
    "contract_win",
    "earnings_revision",
    "supply_constraint",
    "technical_breakout",
}
HARD_NEGATIVE_EVENTS = {"crowding_warning", "financing_risk", "regulatory_risk"}


def build_sizing_plan(
    research_book: dict[str, Any],
    portfolio: dict[str, Any],
    components: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    limits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_limits = normalize_limits(limits)
    component_by_symbol = {str(row.get("symbol") or "").upper(): row for row in components}
    gap_by_symbol = {str(row.get("symbol") or "").upper(): row for row in gaps}
    bucket_weights = {
        str(row.get("bucket") or "unmapped"): float(row.get("comparison_weight", row.get("ex_cash_weight", row.get("weight") or 0)) or 0)
        for row in portfolio.get("by_bucket", [])
        if str(row.get("bucket") or "unmapped") != "cash_reserves"
    }
    targets = [
        target_for_item(item, proxied_lookup(component_by_symbol, item.get("symbol"), {}), proxied_lookup(gap_by_symbol, item.get("symbol"), {}), bucket_weights, normalized_limits)
        for item in research_book.get("items", [])
        if item.get("symbol")
    ]
    targets = dedupe_proxy_targets(targets, portfolio)
    starting_equity_weight = portfolio_target_total(portfolio)
    cash_weight = portfolio_cash_weight(portfolio)
    cash_deployable_weight = cash_deployment_budget(cash_weight, normalized_limits)
    target_total = min(1.0, starting_equity_weight + cash_deployable_weight)
    normalize_model_targets(targets, target_total=target_total)
    apply_positive_catalyst_gap_trim_blocks(targets)
    apply_cash_aware_budget(targets, normalized_limits, cash_deployable_weight)
    targets.sort(key=lambda row: (abs(float(row.get("recommended_delta_weight") or 0)), float(row.get("risk_adjusted_expected_return") or 0)), reverse=True)
    actions = [row for row in targets if should_publish_action(row)]
    actions.sort(key=lambda row: (abs(float(row.get("recommended_delta_weight") or 0)), row["priority"]), reverse=True)
    published_actions = actions[:12]
    research_queue = build_research_queue(targets, {str(row.get("symbol") or "").upper() for row in published_actions})
    rebalance_budget = rebalance_budget_summary(published_actions, normalized_limits, starting_cash_weight=cash_weight, max_cash_deploy_weight=cash_deployable_weight)
    annotate_action_funding(published_actions, rebalance_budget)
    target_summary = model_target_summary(targets, target_total)
    return {
        "version": SIZING_VERSION,
        "model_policy_version": MODEL_POLICY_VERSION,
        "objective": "convert risk-adjusted expected return into a trim-and-cash-funded target-weight rebalance",
        "target_count": len(targets),
        "action_count": len(published_actions),
        "starting_equity_weight": round_weight(starting_equity_weight),
        "target_total_weight": round_weight(target_total),
        "model_target_total_weight": target_summary["model_target_total_weight"],
        "model_target_utilization_pct": target_summary["model_target_utilization_pct"],
        "model_target_unallocated_weight": target_summary["model_target_unallocated_weight"],
        "model_target_overallocated_weight": target_summary["model_target_overallocated_weight"],
        "max_allowed_binding_count": target_summary["max_allowed_binding_count"],
        "max_allowed_binding_symbols": target_summary["max_allowed_binding_symbols"],
        "cash_reserve_weight": round_weight(cash_weight),
        "cash_deployable_weight": round_weight(cash_deployable_weight),
        "post_trade_cash_weight": rebalance_budget["post_trade_cash_weight"],
        "cash_policy": "cash_available_for_high_conviction_adds_with_daily_draw_cap",
        "targets": targets,
        "action_queue": published_actions,
        "research_queue": research_queue,
        "research_queue_count": len(research_queue),
        "exploration_policy": "surface fresh research-only candidates without assigning trade deltas or target weights",
        "rebalance_budget": rebalance_budget,
        "limits": {
            "max_single_name_weight": float(normalized_limits["max_single_name_weight"]),
            "max_bucket_weight": float(normalized_limits["max_bucket_weight"]),
            "max_daily_turnover": float(normalized_limits["max_daily_turnover"]),
            "max_one_ticket_delta": float(normalized_limits["max_one_ticket_delta"]),
            "max_cash_deploy_weight": float(normalized_limits["max_cash_deploy_weight"]),
            "earnings_blackout_days": int(normalized_limits["earnings_blackout_days"]),
        },
    }


def dedupe_proxy_targets(targets: list[dict[str, Any]], portfolio: dict[str, Any]) -> list[dict[str, Any]]:
    held_symbols = held_symbol_by_proxy(portfolio)
    selected: dict[str, dict[str, Any]] = {}
    for target in targets:
        key = symbol_proxy_key(target.get("symbol"))
        if not key:
            continue
        target = dict(target)
        if held_symbol := held_symbols.get(key):
            target["proxy_symbol"] = target.get("symbol")
            target["symbol"] = held_symbol
        current = selected.get(key)
        if current is None or proxy_target_rank(target) > proxy_target_rank(current):
            selected[key] = target
    return list(selected.values())


def held_symbol_by_proxy(portfolio: dict[str, Any]) -> dict[str, str]:
    held: dict[str, tuple[str, float]] = {}
    for row in portfolio.get("by_symbol", []):
        if is_cash_position(row):
            continue
        symbol = str(row.get("symbol") or "").upper()
        weight = float(row.get("comparison_weight", row.get("ex_cash_weight", row.get("weight") or 0)) or 0)
        if not symbol or weight <= 0:
            continue
        key = symbol_proxy_key(symbol)
        current = held.get(key)
        if current is None or weight > current[1]:
            held[key] = (symbol, weight)
    return {key: symbol for key, (symbol, _) in held.items()}


def proxy_target_rank(target: dict[str, Any]) -> tuple[float, float, float]:
    return (
        abs(float(target.get("recommended_delta_weight") or 0)),
        float(target.get("risk_adjusted_expected_return") or 0),
        float(target.get("priority") or 0),
    )


def target_for_item(
    item: dict[str, Any],
    component: dict[str, Any],
    gap: dict[str, Any],
    bucket_weights: dict[str, float],
    limits: dict[str, Any],
) -> dict[str, Any]:
    symbol = str(item.get("symbol") or "").upper()
    bucket = str(item.get("bucket") or "unmapped")
    current = float(item.get("current_weight") or 0)
    expected = float(item.get("risk_adjusted_expected_return") or 0)
    evidence = float(item.get("evidence_quality") or 0)
    drawdown = float(item.get("drawdown_risk") or 0)
    timing = float(item.get("timing_score") or 0)
    peer = float(item.get("peer_avg_weight") or 0)
    tier1_peer = float(item.get("tier1_peer_avg_weight") or 0)
    max_single = float(limits["max_single_name_weight"])
    bucket_capacity = max(0.0, float(limits["max_bucket_weight"]) - max(0.0, bucket_weights.get(bucket, 0.0) - current))
    max_allowed = max(0.0, min(max_single, bucket_capacity))
    raw_target = raw_target_weight(item, current, expected, evidence, drawdown, timing, peer, tier1_peer)
    constraints = soft_constraints(item, expected, evidence, drawdown, timing)
    target = max(0.0, min(raw_target, max_allowed))
    if target < raw_target:
        constraints.append("hard_cap")
    delta_to_target = target - current
    max_delta = float(limits["max_one_ticket_delta"])
    immediate_delta = max(-max_delta, min(max_delta, delta_to_target))
    post_action = max(0.0, current + immediate_delta)
    trade_action = trade_action_for_delta(immediate_delta, current, item.get("verdict", "study"))
    priority = priority_score(item, immediate_delta, gap)
    return {
        "symbol": symbol,
        "bucket": bucket,
        "model_policy_version": MODEL_POLICY_VERSION,
        "sizing_version": SIZING_VERSION,
        "trade_action": trade_action,
        "priority": round(priority, 2),
        "current_weight": round_weight(current),
        "portfolio_weight": round_weight(current),
        "model_target_weight": round_weight(target),
        "unscaled_model_target_weight": round_weight(target),
        "risk_adjusted_target_weight": round_weight(target),
        "desired_delta_weight": round_weight(delta_to_target),
        "recommended_delta_weight": round_weight(immediate_delta),
        "post_action_weight": round_weight(post_action),
        "trade_target_weight": round_weight(post_action),
        "target_weight": round_weight(post_action),
        "max_allowed_weight": round_weight(max_allowed),
        "peer_avg_weight": round_weight(peer),
        "tier1_peer_avg_weight": round_weight(tier1_peer),
        "risk_adjusted_expected_return": round(expected, 2),
        "base_risk_adjusted_expected_return": item.get("base_risk_adjusted_expected_return"),
        "base_evidence_quality": item.get("base_evidence_quality"),
        "base_drawdown_risk": item.get("base_drawdown_risk"),
        "llm_signal": item.get("llm_signal", {}),
        "llm_signal_applied": bool(item.get("llm_signal_applied", False)),
        "llm_expected_return_delta": item.get("llm_expected_return_delta"),
        "llm_expected_return_adjustment": item.get("llm_expected_return_adjustment"),
        "llm_evidence_quality_delta": item.get("llm_evidence_quality_delta"),
        "llm_evidence_quality_adjustment": item.get("llm_evidence_quality_adjustment"),
        "llm_drawdown_risk_delta": item.get("llm_drawdown_risk_delta"),
        "llm_drawdown_risk_adjustment": item.get("llm_drawdown_risk_adjustment"),
        "llm_conviction_score": item.get("llm_conviction_score"),
        "llm_variant_quality_score": item.get("llm_variant_quality_score"),
        "llm_source_quality_score": item.get("llm_source_quality_score"),
        "llm_contradiction_risk_score": item.get("llm_contradiction_risk_score"),
        "llm_staleness_risk_score": item.get("llm_staleness_risk_score"),
        "llm_review_required": item.get("llm_review_required"),
        "probability_weighted_return": item.get("probability_weighted_return", 0),
        "bull_return_12m": item.get("bull_return_12m", 0),
        "base_return_12m": item.get("base_return_12m", 0),
        "bear_return_12m": item.get("bear_return_12m", 0),
        "price_return_1d": item.get("price_return_1d"),
        "price_return_5d": item.get("price_return_5d"),
        "price_return_1m": item.get("price_return_1m"),
        "price_return_3m": item.get("price_return_3m"),
        "price_return_ytd": item.get("price_return_ytd"),
        "price_return_1y": item.get("price_return_1y"),
        "earnings_days_until": item.get("earnings_days_until"),
        "earnings_event_date": item.get("earnings_event_date", ""),
        "earnings_event_source": item.get("earnings_event_source", ""),
        "earnings_confirmed_or_estimated": item.get("earnings_confirmed_or_estimated", ""),
        "earnings_risk_window": item.get("earnings_risk_window", ""),
        "earnings_confirmation_required": bool(item.get("earnings_confirmation_required", False)),
        "external_signal_score": item.get("external_signal_score"),
        "coverage_adjusted_external_signal_score": item.get("coverage_adjusted_external_signal_score"),
        "external_coverage_multiplier": item.get("external_coverage_multiplier"),
        "external_feed_status": item.get("external_feed_status", ""),
        "external_provider_count": item.get("external_provider_count"),
        "external_provider_ok_count": item.get("external_provider_ok_count"),
        "external_provider_ok_ratio": item.get("external_provider_ok_ratio"),
        "external_provider_gap_count": item.get("external_provider_gap_count"),
        "external_provider_configuration_gap_count": item.get("external_provider_configuration_gap_count"),
        "external_provider_transient_gap_count": item.get("external_provider_transient_gap_count"),
        "external_provider_stale_gap_count": item.get("external_provider_stale_gap_count"),
        "external_provider_runtime_gap_count": item.get("external_provider_runtime_gap_count"),
        "external_provider_other_gap_count": item.get("external_provider_other_gap_count"),
        "external_provider_primary_gap_severity": item.get("external_provider_primary_gap_severity", ""),
        "external_provider_gap_severity_score": item.get("external_provider_gap_severity_score"),
        "external_signal_count": item.get("external_signal_count"),
        "external_source_count": item.get("external_source_count"),
        "evidence_quality": round(evidence, 2),
        "timing_score": round(timing, 2),
        "drawdown_risk": round(drawdown, 2),
        "confidence": confidence_score(evidence, timing, drawdown, item),
        "company_underwriting_score": round(float(item.get("company_underwriting_score") or 0), 2),
        "sector_setup_score": round(float(item.get("sector_setup_score") or 0), 2),
        "manager_count": int(item.get("manager_count") or 0),
        "tier1_manager_count": int(item.get("tier1_manager_count") or 0),
        "company_add_eligible": bool(item.get("company_add_eligible", False)),
        "company_trim_signal": bool(item.get("company_trim_signal", False)),
        "company_review_required": bool(item.get("company_review_required", False)),
        "review_status": item.get("company_review_status", ""),
        "review_required": bool(item.get("review_required", item.get("company_review_required", False))),
        "review_reason": item.get("review_reason", ""),
        "company_reason": item.get("company_reason", ""),
        "sector_reason": item.get("sector_reason", ""),
        "tertiary_signal_summary": item.get("tertiary_signal_summary", ""),
        "decision_stack": item.get("decision_stack", {}),
        "signal_family_count": len(item.get("signal_families") or []),
        "signal_families": item.get("signal_families", []),
        "event_types": item.get("event_types", []),
        "catalyst_clock": item.get("catalyst_clock", ""),
        "verdict": item.get("verdict", "study"),
        "why": item.get("variant_view", ""),
        "action": sizing_summary(trade_action, immediate_delta, post_action, target),
        "sizing_summary": sizing_summary(trade_action, immediate_delta, post_action, target),
        "sizing_rationale": sizing_rationale(item, constraints),
        "why_this_size": sizing_rationale(item, constraints),
        "increase_size_if": increase_size_if(item),
        "decrease_size_if": decrease_size_if(item),
        "active_constraints": constraints,
        "risk_flags": gap.get("risk_flags", []),
        "five_day_pct": component.get("five_day_pct", item.get("price_return_5d")),
        "contribution_pct": component.get("contribution_pct", 0),
        "sizing_basis": "model target weight converted to approval-only portfolio-weight delta",
        "funding_source": "no_trade",
        "funding_counterpart_symbols": [],
    }


def portfolio_target_total(portfolio: dict[str, Any]) -> float:
    if portfolio.get("comparison_weight_basis") == "invested_equity_ex_cash":
        return 1.0
    if "equity_weight" in portfolio:
        total = float(portfolio.get("equity_weight") or 0)
        return max(0.0, total)
    total = sum(
        float(row.get("weight") or 0)
        for row in portfolio.get("by_symbol", [])
        if not is_cash_position(row)
    )
    return total if total > 0 else 1.0


def portfolio_cash_weight(portfolio: dict[str, Any]) -> float:
    if portfolio.get("cash_weight") is not None:
        return float(portfolio.get("cash_weight") or 0)
    return sum(
        float(row.get("weight") or 0)
        for row in portfolio.get("by_symbol", [])
        if is_cash_position(row)
    )


def cash_deployment_budget(cash_weight: float, limits: dict[str, Any]) -> float:
    configured = float(limits.get("max_cash_deploy_weight") or 0)
    max_daily = float(limits.get("max_daily_turnover") or 0)
    return round_weight(max(0.0, min(cash_weight, configured, max_daily)))


def normalize_model_targets(targets: list[dict[str, Any]], target_total: float = 1.0) -> None:
    raw_values = [
        float(row.get("unscaled_model_target_weight", row.get("model_target_weight") or 0) or 0)
        for row in targets
    ]
    raw_total = sum(raw_values)
    if raw_total <= 0:
        return
    preserved_total = 0.0
    assigned: dict[int, float] = {}
    scalable_indexes = []
    for index, row in enumerate(targets):
        raw = raw_values[index]
        current = float(row.get("current_weight") or 0)
        if row.get("company_trim_signal") or row.get("verdict") == "trim":
            target = min(raw, current, target_max_allowed(row))
            assigned[index] = target
            preserved_total += target
        else:
            scalable_indexes.append(index)
    remaining = max(0.0, target_total - preserved_total)
    active = set(scalable_indexes)
    while active and remaining > 0:
        active_raw_total = sum(raw_values[index] for index in active)
        if active_raw_total <= 0:
            break
        scale = remaining / active_raw_total
        capped_indexes = []
        for index in sorted(active):
            candidate = max(0.0, raw_values[index] * scale)
            cap = normalization_target_cap(targets[index])
            if candidate > cap:
                assigned[index] = cap
                remaining = max(0.0, remaining - cap)
                capped_indexes.append(index)
        if not capped_indexes:
            for index in sorted(active):
                assigned[index] = max(0.0, raw_values[index] * scale)
            remaining = 0.0
            break
        active.difference_update(capped_indexes)
    for index in active:
        assigned.setdefault(index, 0.0)
    for index, row in enumerate(targets):
        raw = raw_values[index]
        current = float(row.get("current_weight") or 0)
        normalized = assigned.get(index, 0.0)
        row["model_target_weight"] = round_weight(normalized)
        row["risk_adjusted_target_weight"] = row["model_target_weight"]
        row["normalization_scale"] = round(normalized / raw, 6) if raw > 0 else 0.0
        row["desired_delta_weight"] = round_weight(normalized - current)


def target_max_allowed(row: dict[str, Any]) -> float:
    value = row.get("max_allowed_weight")
    if value is None:
        return float("inf")
    return max(0.0, float(value or 0))


def normalization_target_cap(row: dict[str, Any]) -> float:
    cap = target_max_allowed(row)
    if row.get("company_add_eligible") is False:
        cap = min(cap, max(0.0, float(row.get("current_weight") or 0)))
    return cap


def model_target_summary(targets: list[dict[str, Any]], target_total: float) -> dict[str, Any]:
    model_total = sum(float(row.get("model_target_weight") or 0) for row in targets)
    unallocated = max(0.0, float(target_total or 0) - model_total)
    overallocated = max(0.0, model_total - float(target_total or 0))
    binding_symbols = []
    for row in targets:
        cap = target_max_allowed(row)
        if cap == float("inf"):
            continue
        target = float(row.get("model_target_weight") or 0)
        if target >= cap - 0.000001:
            binding_symbols.append(str(row.get("symbol") or "UNKNOWN"))
    return {
        "model_target_total_weight": round_weight(model_total),
        "model_target_utilization_pct": round((model_total / target_total) * 100.0, 2) if target_total else 0.0,
        "model_target_unallocated_weight": round_weight(unallocated),
        "model_target_overallocated_weight": round_weight(overallocated),
        "max_allowed_binding_count": len(binding_symbols),
        "max_allowed_binding_symbols": binding_symbols[:12],
    }


def apply_positive_catalyst_gap_trim_blocks(targets: list[dict[str, Any]]) -> None:
    for row in targets:
        if not positive_catalyst_gap_trim_block(row):
            continue
        current = float(row.get("current_weight") or 0)
        blocked_target = float(row.get("model_target_weight") or 0)
        constraints = list(row.get("active_constraints") or [])
        constraints.append("positive_catalyst_gap_trim_block")
        row["positive_catalyst_gap_trim_block"] = True
        row["positive_catalyst_gap_trim_block_reason"] = positive_catalyst_gap_reason(row)
        row["blocked_model_target_weight"] = round_weight(blocked_target)
        row["blocked_desired_delta_weight"] = row.get("desired_delta_weight", 0)
        row["desired_delta_weight"] = 0.0
        row["recommended_delta_weight"] = 0.0
        row["post_action_weight"] = round_weight(current)
        row["trade_target_weight"] = round_weight(current)
        row["target_weight"] = round_weight(current)
        row["trade_action"] = "hold"
        row["funding_source"] = "no_trade"
        row["funding_counterpart_symbols"] = []
        row["active_constraints"] = sorted(set(constraints))


def positive_catalyst_gap_trim_block(row: dict[str, Any]) -> bool:
    current = float(row.get("current_weight") or 0)
    desired = float(row.get("desired_delta_weight") or 0)
    if current <= 0 or desired >= -0.000001:
        return False
    if row.get("company_trim_signal"):
        return False
    events = {str(event) for event in row.get("event_types") or []}
    if events & HARD_NEGATIVE_EVENTS:
        return False
    if not events & CONSTRUCTIVE_CATALYST_EVENTS:
        return False
    one_day = optional_float(row.get("price_return_1d"))
    five_day = first_optional_float(row.get("price_return_5d"), row.get("five_day_pct"))
    has_gap = (one_day is not None and one_day >= 8.0) or (five_day is not None and five_day >= 12.0)
    if not has_gap:
        return False
    company_score = float(row.get("company_underwriting_score") or 0)
    evidence = float(row.get("evidence_quality") or 0)
    has_bottom_up_support = company_score >= 54.0 or evidence >= 70.0 or bool(row.get("company_add_eligible"))
    has_signal_support = bool({"catalyst", "price_action"} & {str(item) for item in row.get("signal_families") or []})
    return has_bottom_up_support and has_signal_support


def positive_catalyst_gap_reason(row: dict[str, Any]) -> str:
    one_day = optional_float(row.get("price_return_1d"))
    five_day = first_optional_float(row.get("price_return_5d"), row.get("five_day_pct"))
    move = f"1D {one_day:.1f}%" if one_day is not None else f"5D {five_day:.1f}%"
    events = ", ".join(str(event) for event in (row.get("event_types") or [])[:3])
    return f"{move} positive move with constructive catalyst tape ({events}); do not fund adds by trimming until the next briefing validates the move."


def apply_cash_aware_budget(targets: list[dict[str, Any]], limits: dict[str, Any], cash_deployable_weight: float = 0.0) -> None:
    max_gross = float(limits["max_daily_turnover"])
    max_self_funded_side = max_gross / 2.0
    for row in targets:
        desired = float(row.get("desired_delta_weight") or 0)
        capped = max(-float(limits["max_one_ticket_delta"]), min(float(limits["max_one_ticket_delta"]), desired))
        row["pre_funding_delta_weight"] = round_weight(capped)
        set_trade_delta(row, 0.0, "awaiting_rebalance_selection")

    desired_adds = sum(max(0.0, float(row.get("pre_funding_delta_weight") or 0)) for row in targets)
    desired_trims = sum(abs(min(0.0, float(row.get("pre_funding_delta_weight") or 0))) for row in targets)
    cash_draw_target = min(max(0.0, desired_adds - desired_trims), cash_deployable_weight)
    if desired_adds <= 0:
        trim_side_budget = min(max_gross, desired_trims)
        add_side_budget = 0.0
    else:
        trim_side_budget = min(max_self_funded_side, desired_trims, max(0.0, (max_gross - cash_draw_target) / 2.0))
        add_side_budget = min(desired_adds, trim_side_budget + cash_draw_target, max_gross - trim_side_budget)

    trim_budget = allocate_side(
        sorted(
            [row for row in targets if float(row.get("pre_funding_delta_weight") or 0) < -0.000001],
            key=trim_funding_priority,
            reverse=True,
        ),
        trim_side_budget,
        side=-1,
        cap_flag="trim_budget_cap",
    )
    add_budget = allocate_side(
        sorted(
            [row for row in targets if float(row.get("pre_funding_delta_weight") or 0) > 0.000001],
            key=add_funding_priority,
            reverse=True,
        ),
        add_side_budget,
        side=1,
        cap_flag="cash_and_turnover_cap",
    )
    if desired_adds > 0 and add_budget < trim_budget:
        reduce_side_to_budget(
            [row for row in targets if float(row.get("recommended_delta_weight") or 0) < -0.000001],
            add_budget,
            side=-1,
            cap_flag="trim_reduced_to_fund_adds",
        )

    for row in targets:
        desired = float(row.get("pre_funding_delta_weight") or 0)
        actual = float(row.get("recommended_delta_weight") or 0)
        constraints = list(row.get("active_constraints") or [])
        if desired > 0 and actual <= 0:
            constraints.append("requires_trim_or_cash_funding")
        elif desired > actual and desired > 0:
            constraints.append("cash_and_turnover_cap")
        elif desired < actual and desired < 0:
            constraints.append("trim_budget_cap")
        row["active_constraints"] = sorted(set(constraints))
        if abs(actual) <= 0.000001 and row.get("funding_role") == "awaiting_rebalance_selection":
            set_trade_delta(row, 0.0, "not_in_current_rebalance")


def allocate_side(rows: list[dict[str, Any]], budget: float, side: int, cap_flag: str) -> float:
    remaining = max(0.0, budget)
    allocated = 0.0
    for row in rows:
        if remaining <= 0:
            break
        desired_abs = abs(float(row.get("pre_funding_delta_weight") or 0))
        amount = min(desired_abs, remaining)
        set_trade_delta(row, side * amount, "funding_source" if side < 0 else "funding_use")
        if amount < desired_abs:
            row["active_constraints"] = sorted(set(list(row.get("active_constraints") or []) + [cap_flag]))
        allocated += amount
        remaining -= amount
    return round_weight(allocated)


def reduce_side_to_budget(rows: list[dict[str, Any]], budget: float, side: int, cap_flag: str) -> None:
    remaining = max(0.0, budget)
    for row in sorted(rows, key=(trim_funding_priority if side < 0 else add_funding_priority), reverse=True):
        desired_abs = abs(float(row.get("recommended_delta_weight") or 0))
        amount = min(desired_abs, remaining)
        set_trade_delta(row, side * amount, "funding_source" if side < 0 else "funding_use")
        if amount < desired_abs:
            row["active_constraints"] = sorted(set(list(row.get("active_constraints") or []) + [cap_flag]))
        remaining -= amount


def set_trade_delta(row: dict[str, Any], delta: float, funding_role: str) -> None:
    current = float(row.get("current_weight") or 0)
    model_target = float(row.get("model_target_weight") or 0)
    post_action = max(0.0, current + delta)
    trade_action = trade_action_for_delta(delta, current, row.get("verdict", "study"))
    row["recommended_delta_weight"] = round_weight(delta)
    row["post_action_weight"] = round_weight(post_action)
    row["trade_target_weight"] = round_weight(post_action)
    row["target_weight"] = round_weight(post_action)
    row["trade_action"] = trade_action
    row["funding_role"] = funding_role
    if delta > 0:
        row["funding_source"] = "trim_funding_or_cash_reserve"
    elif delta < 0:
        row["funding_source"] = "trim_source_for_add_queue"
    else:
        row["funding_source"] = "no_trade"
    row["action"] = sizing_summary(trade_action, delta, post_action, model_target)
    row["sizing_summary"] = row["action"]
    if row.get("positive_catalyst_gap_trim_block") and abs(delta) <= 0.000001:
        blocked_target = float(row.get("blocked_model_target_weight") or model_target)
        row["trade_action"] = "hold"
        row["action"] = (
            "Hold; positive catalyst gap blocks trim funding today "
            f"despite unblocked model target {weight_label(blocked_target)}."
        )
        row["sizing_summary"] = row["action"]
    row["sizing_basis"] = "trim-and-cash-funded portfolio-weight rebalance; adds can use capped cash reserves"
    row["priority"] = round(priority_with_trade_delta(row, delta), 2)


def annotate_action_funding(actions: list[dict[str, Any]], budget: dict[str, Any]) -> None:
    trim_symbols = [
        str(row.get("symbol") or "").upper()
        for row in actions
        if float(row.get("recommended_delta_weight") or 0) < -0.000001
    ]
    add_symbols = [
        str(row.get("symbol") or "").upper()
        for row in actions
        if float(row.get("recommended_delta_weight") or 0) > 0.000001
    ]
    cash_deployed = float(budget.get("cash_deployed_weight") or 0)
    total_trims = float(budget.get("total_trim_weight") or 0)
    for row in actions:
        delta = float(row.get("recommended_delta_weight") or 0)
        if delta > 0:
            if trim_symbols and cash_deployed > 0:
                source = "funded_by_named_trims_and_cash_reserve"
            elif trim_symbols:
                source = "funded_by_named_trims"
            elif cash_deployed > 0:
                source = "funded_by_cash_reserve"
            else:
                source = "add_reduced_by_funding_cap"
            row["funding_source"] = source
            row["funding_counterpart_symbols"] = trim_symbols
        elif delta < 0:
            row["funding_source"] = "funds_add_queue" if add_symbols else "raises_cash"
            row["funding_counterpart_symbols"] = add_symbols
        else:
            row["funding_source"] = "no_trade"
            row["funding_counterpart_symbols"] = []
        if total_trims == 0 and delta > 0 and cash_deployed <= 0:
            row["active_constraints"] = sorted(set(list(row.get("active_constraints") or []) + ["requires_trim_or_cash_funding"]))


def add_funding_priority(row: dict[str, Any]) -> float:
    return (
        float(row.get("risk_adjusted_expected_return") or 0) * 2.0
        + float(row.get("evidence_quality") or 0) * 0.35
        + float(row.get("timing_score") or 0) * 0.2
        + max(0.0, float(row.get("desired_delta_weight") or 0)) * 500.0
    )


def trim_funding_priority(row: dict[str, Any]) -> float:
    return (
        max(0.0, 35.0 - float(row.get("risk_adjusted_expected_return") or 0)) * 2.0
        + float(row.get("drawdown_risk") or 0) * 0.35
        + abs(min(0.0, float(row.get("desired_delta_weight") or 0))) * 650.0
        + float(row.get("current_weight") or 0) * 120.0
    )


def priority_with_trade_delta(row: dict[str, Any], delta: float) -> float:
    base = add_funding_priority(row) if delta >= 0 else trim_funding_priority(row)
    return base + abs(delta) * 420.0


def rebalance_budget_summary(
    actions: list[dict[str, Any]],
    limits: dict[str, Any],
    starting_cash_weight: float = 0.0,
    max_cash_deploy_weight: float | None = None,
) -> dict[str, Any]:
    adds = sum(max(0.0, float(row.get("recommended_delta_weight") or 0)) for row in actions)
    trims = sum(abs(min(0.0, float(row.get("recommended_delta_weight") or 0))) for row in actions)
    gross = adds + trims
    max_cash = cash_deployment_budget(starting_cash_weight, limits) if max_cash_deploy_weight is None else max(0.0, min(starting_cash_weight, max_cash_deploy_weight))
    cash_deployed = min(max(0.0, adds - trims), max_cash)
    cash_raised = max(0.0, trims - adds)
    post_cash = max(0.0, starting_cash_weight - cash_deployed + cash_raised)
    return {
        "funding_mode": "trim_and_cash_funded_rebalance",
        "total_add_weight": round_weight(adds),
        "total_trim_weight": round_weight(trims),
        "cash_deployed_weight": round_weight(cash_deployed),
        "cash_raised_weight": round_weight(cash_raised),
        "starting_cash_weight": round_weight(starting_cash_weight),
        "post_trade_cash_weight": round_weight(post_cash),
        "net_delta_weight": round_weight(adds - trims),
        "gross_turnover_weight": round_weight(gross),
        "max_gross_turnover_weight": float(limits["max_daily_turnover"]),
        "max_add_weight": round_weight(float(limits["max_daily_turnover"])),
        "max_cash_deploy_weight": round_weight(max_cash),
        "funding_status": "funded" if adds <= trims + max_cash + 0.000001 else "unfunded_adds_reduced",
    }


def raw_target_weight(
    item: dict[str, Any],
    current: float,
    expected: float,
    evidence: float,
    drawdown: float,
    timing: float,
    peer: float,
    tier1_peer: float,
) -> float:
    verdict = str(item.get("verdict") or "study")
    confidence = max(0.0, min(1.0, (evidence * 0.55 + timing * 0.25 - drawdown * 0.2) / 100.0))
    expected_unit = max(0.0, min(1.0, expected / 35.0))
    peer_anchor = max(peer * 0.65, tier1_peer * 0.75)
    if verdict == "buy_more":
        target = max(current + 0.01, 0.035 + expected_unit * 0.09 + confidence * 0.035, peer_anchor)
    elif verdict == "starter":
        target = max(0.015, min(0.04, 0.012 + expected_unit * 0.035 + confidence * 0.015), min(peer_anchor, 0.04))
    elif verdict == "hold":
        target = max(current, min(current + 0.01, peer_anchor)) if expected >= 16 else current
    elif verdict == "trim":
        target = max(0.0, current - min(0.04, max(0.01, current * 0.2)))
    elif verdict == "avoid":
        target = 0.0
    else:
        target = current if current else (0.0 if expected < 12 else min(0.0125, peer_anchor))
    if item.get("company_trim_signal") and current:
        target = min(target, max(0.0, current - min(0.04, max(0.01, current * 0.22))))
    if target > current and not item.get("company_add_eligible"):
        target = current if current else 0.0
    if item.get("sector_headwind") and target > current:
        target = current + (target - current) * 0.35
    if drawdown >= 82 and target > current:
        target = current
    return max(0.0, target)


def soft_constraints(item: dict[str, Any], expected: float, evidence: float, drawdown: float, timing: float) -> list[str]:
    constraints: list[str] = []
    if evidence < 45:
        constraints.append("weak_evidence")
    if drawdown >= 75:
        constraints.append("drawdown_risk")
    if timing < 35:
        constraints.append("poor_timing")
    if float(item.get("valuation_support") or 0) < 40:
        constraints.append("valuation_support_weak")
    if expected < 10:
        constraints.append("low_expected_return")
    if "crowding_warning" in (item.get("event_types") or []):
        constraints.append("crowding")
    if "financing_risk" in (item.get("event_types") or []):
        constraints.append("financing_risk")
    if not item.get("company_add_eligible") and (expected >= 14 or float(item.get("current_weight") or 0) <= 0):
        constraints.append("bottom_up_evidence_floor")
    if item.get("company_review_required") or item.get("review_required"):
        constraints.append("company_review_required")
    if item.get("company_trim_signal"):
        constraints.append("company_deterioration")
    if item.get("sector_headwind"):
        constraints.append("sector_headwind")
    return constraints


def confidence_score(evidence: float, timing: float, drawdown: float, item: dict[str, Any]) -> int:
    company = float(item.get("company_underwriting_score") or 0)
    sector = float(item.get("sector_setup_score") or 50)
    score = evidence * 0.36 + timing * 0.18 + company * 0.30 + sector * 0.12 - drawdown * 0.16
    if item.get("company_review_required") or item.get("review_required"):
        score -= 10.0
    if item.get("company_trim_signal"):
        score -= 8.0
    return int(max(0.0, min(100.0, round(score))))


def should_publish_action(row: dict[str, Any]) -> bool:
    delta = abs(float(row.get("recommended_delta_weight") or 0))
    return delta >= 0.005 or bool(row.get("positive_catalyst_gap_trim_block"))


def build_research_queue(targets: list[dict[str, Any]], published_symbols: set[str], limit: int = 8) -> list[dict[str, Any]]:
    candidates = [
        research_queue_item(row)
        for row in targets
        if research_queue_eligible(row, published_symbols)
    ]
    candidates.sort(key=lambda row: row["research_priority"], reverse=True)
    return candidates[:limit]


def research_queue_eligible(row: dict[str, Any], published_symbols: set[str]) -> bool:
    symbol = str(row.get("symbol") or "").upper()
    if not symbol or symbol in published_symbols:
        return False
    if float(row.get("current_weight") or 0) > 0.000001:
        return False
    if str(row.get("trade_action") or "") == "avoid":
        return False
    if row.get("company_trim_signal"):
        return False
    if {"financing_risk", "regulatory_risk", "crowding_warning"} & {str(item) for item in row.get("event_types") or []}:
        return False
    expected = float(row.get("risk_adjusted_expected_return") or 0)
    evidence = float(row.get("evidence_quality") or 0)
    signal_count = int(row.get("signal_family_count") or 0)
    manager_count = int(row.get("manager_count") or 0)
    drawdown = float(row.get("drawdown_risk") or 0)
    return drawdown < 82 and (signal_count >= 2 or manager_count >= 2 or expected >= 12 or evidence >= 58)


def research_queue_item(row: dict[str, Any]) -> dict[str, Any]:
    priority = research_queue_priority(row)
    constraints = list(row.get("active_constraints") or [])
    return {
        "symbol": row.get("symbol", ""),
        "bucket": row.get("bucket", "unmapped"),
        "recommendation_type": "research_only",
        "trade_action": "study",
        "recommended_delta_weight": 0.0,
        "target_weight": 0.0,
        "model_target_weight": row.get("model_target_weight", 0.0),
        "research_priority": round(priority, 2),
        "risk_adjusted_expected_return": row.get("risk_adjusted_expected_return"),
        "evidence_quality": row.get("evidence_quality"),
        "drawdown_risk": row.get("drawdown_risk"),
        "timing_score": row.get("timing_score"),
        "signal_family_count": row.get("signal_family_count", 0),
        "signal_families": row.get("signal_families", []),
        "event_types": row.get("event_types", []),
        "manager_count": row.get("manager_count", 0),
        "research_reason": research_queue_reason(row, constraints),
        "promotion_trigger": increase_size_if(row),
        "blocking_constraints": constraints,
    }


def research_queue_priority(row: dict[str, Any]) -> float:
    return (
        float(row.get("risk_adjusted_expected_return") or 0) * 1.8
        + float(row.get("evidence_quality") or 0) * 0.35
        + float(row.get("timing_score") or 0) * 0.2
        + int(row.get("signal_family_count") or 0) * 5.0
        + int(row.get("manager_count") or 0) * 4.0
        - float(row.get("drawdown_risk") or 0) * 0.16
    )


def research_queue_reason(row: dict[str, Any], constraints: list[str]) -> str:
    reason = (
        f"Fresh non-owned candidate with {row.get('risk_adjusted_expected_return', 0)}% risk-adjusted expected return, "
        f"{row.get('evidence_quality', 0)} evidence quality, "
        f"{row.get('signal_family_count', 0)} signal families, and "
        f"{row.get('manager_count', 0)} tracked-manager signals."
    )
    if constraints:
        reason += " Research blockers: " + ", ".join(constraints) + "."
    return reason


def trade_action_for_delta(delta: float, current: float, verdict: str) -> str:
    if delta >= 0.0025:
        return "add"
    if delta <= -0.0025:
        return "trim"
    if current:
        return "hold"
    return "watch" if verdict in {"study", "starter"} else "avoid"


def priority_score(item: dict[str, Any], delta: float, gap: dict[str, Any]) -> float:
    return (
        float(gap.get("priority") or 0) * 0.35
        + float(item.get("risk_adjusted_expected_return") or 0) * 1.2
        + abs(delta) * 420.0
        + float(item.get("evidence_quality") or 0) * 0.22
        + float(item.get("timing_score") or 0) * 0.12
    )


def sizing_summary(action: str, delta: float, post_action: float, target: float) -> str:
    if action == "add":
        return f"Add {signed_weight_label(delta)} to {weight_label(post_action)}; model target {weight_label(target)}."
    if action == "trim":
        return f"Trim {weight_label(abs(delta))} to {weight_label(post_action)}; model target {weight_label(target)}."
    if action == "hold":
        return f"Hold at {weight_label(post_action)}; model target {weight_label(target)}."
    if action == "avoid":
        return "Avoid; no model target weight."
    return f"Watch; model target {weight_label(target)}."


def sizing_rationale(item: dict[str, Any], constraints: list[str]) -> str:
    rationale = (
        f"{item.get('verdict', 'study')} verdict from {item.get('risk_adjusted_expected_return', 0)}% "
        f"risk-adjusted expected return, {item.get('evidence_quality', 0)} evidence quality, "
        f"{item.get('company_underwriting_score', 0)} company score, "
        f"{item.get('sector_setup_score', 0)} sector score, "
        f"and {item.get('drawdown_risk', 0)} drawdown risk."
    )
    if constraints:
        rationale += " Constraints: " + ", ".join(constraints) + "."
    return rationale


def increase_size_if(item: dict[str, Any]) -> str:
    return "Increase target if primary-source catalysts confirm revenue/margin acceleration and valuation support improves."


def decrease_size_if(item: dict[str, Any]) -> str:
    if item.get("event_types"):
        return "Decrease target if risk catalysts persist or the next earnings/event window fails to confirm the thesis."
    return "Decrease target if expected return falls, evidence goes stale, or the macro regime stops supporting AI beta."


def round_weight(value: float) -> float:
    return round(float(value or 0), 6)


def optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def first_optional_float(*values: Any) -> float | None:
    for value in values:
        parsed = optional_float(value)
        if parsed is not None:
            return parsed
    return None


def weight_label(value: float) -> str:
    return f"{value * 100:.1f}%"


def signed_weight_label(value: float) -> str:
    return f"{value * 100:+.1f}%"


def is_cash_position(row: dict[str, Any]) -> bool:
    symbol = str(row.get("symbol", "")).upper()
    bucket = str(row.get("bucket", ""))
    asset_class = str(row.get("asset_class", ""))
    return bool(row.get("is_cash")) or asset_class == "cash" or bucket == "cash_reserves" or symbol in {"CASH", "USD"}
