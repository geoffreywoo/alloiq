from __future__ import annotations

from datetime import date
from typing import Any

from .features import MODEL_POLICY_VERSION
from .symbols import proxied_lookup, proxy_index


RESEARCH_BOOK_VERSION = "2026-05-bottom-up-research-book-v3"

BUCKET_SCENARIOS = {
    "frontier_ai_platforms": {"bull": 38.0, "base": 18.0, "bear": -22.0},
    "semis_networking_hbm": {"bull": 48.0, "base": 22.0, "bear": -30.0},
    "neocloud_datacenters": {"bull": 75.0, "base": 30.0, "bear": -45.0},
    "power_grid_gas_nuclear": {"bull": 52.0, "base": 21.0, "bear": -32.0},
    "ai_software_winners": {"bull": 42.0, "base": 18.0, "bear": -28.0},
    "ai_enabled_financials": {"bull": 36.0, "base": 16.0, "bear": -24.0},
    "disrupted_incumbents": {"bull": 18.0, "base": -4.0, "bear": -35.0},
    "unmapped": {"bull": 28.0, "base": 10.0, "bear": -28.0},
}

BUCKET_THESIS = {
    "frontier_ai_platforms": "AI platforms convert usage into revenue, retention, pricing power, and operating leverage.",
    "semis_networking_hbm": "AI compute bottlenecks keep value accruing to accelerators, HBM, networking, and foundry capacity.",
    "neocloud_datacenters": "GPU cloud and data-center capacity can compound if utilization, customer quality, and financing terms hold.",
    "power_grid_gas_nuclear": "AI data-center growth makes power availability, grid equipment, gas, and nuclear capacity more valuable.",
    "ai_software_winners": "AI-native workflows should show up in seat expansion, net retention, and product-led pricing power.",
    "ai_enabled_financials": "AI improves distribution, underwriting, fraud, support, and market-structure workflows.",
    "disrupted_incumbents": "Legacy workflow incumbents can lose pricing power as AI compresses distribution and service labor.",
    "unmapped": "This name needs cleaner bucket ownership before the model should size it aggressively.",
}


def build_research_book(
    as_of: date,
    feature_matrix: dict[str, Any],
    cards: list[dict[str, Any]],
    macro: dict[str, Any],
    llm_signal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cards_by_symbol = proxy_index(cards)
    llm_by_symbol = llm_signal_by_symbol(llm_signal)
    items = [
        research_item(as_of, feature, proxied_lookup(cards_by_symbol, feature.get("symbol"), {}), macro, llm_by_symbol.get(str(feature.get("symbol") or "").upper()))
        for feature in feature_matrix.get("rows", [])
    ]
    items.sort(key=lambda row: row["risk_adjusted_expected_return"], reverse=True)
    for rank, row in enumerate(items, start=1):
        row["rank"] = rank
    return {
        "version": RESEARCH_BOOK_VERSION,
        "model_policy_version": MODEL_POLICY_VERSION,
        "feature_version": feature_matrix.get("version", ""),
        "as_of": as_of.isoformat(),
        "objective": "maximize_expected_3_12m_forward_return_with_company_first_underwriting",
        "horizon": "3-12m",
        "llm_signal_active": bool(llm_by_symbol),
        "item_count": len(items),
        "items": items,
        "top_verdicts": verdict_counts(items),
    }


def research_item(
    as_of: date,
    feature: dict[str, Any],
    card: dict[str, Any],
    macro: dict[str, Any],
    llm_signal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bucket = str(feature.get("bucket") or "unmapped")
    scenario = scenario_returns(feature, bucket)
    probabilities = scenario_probabilities(feature)
    probability_weighted = (
        scenario["bull_return_12m"] * probabilities["bull"]
        + scenario["base_return_12m"] * probabilities["base"]
        + scenario["bear_return_12m"] * probabilities["bear"]
    )
    drawdown_risk = float(feature.get("drawdown_risk") or 0)
    evidence = float(feature.get("evidence_quality") or 0)
    timing = float(feature.get("timing_score") or 0)
    valuation = float(feature.get("valuation_support") or 0)
    company_score = float(feature.get("company_underwriting_score") or 45)
    sector_score = float(feature.get("sector_setup_score") or 50)
    manager_score = manager_confirmation_score(feature)
    macro_score = macro_timing_score(feature)
    approval_friction = float(feature.get("approval_data_friction_score") or 0)
    approval_friction_penalty = min(6.0, approval_friction * 0.06)
    decision_score = decision_stack_score(company_score, sector_score, manager_score, macro_score)
    risk_adjusted = (
        probability_weighted
        + (company_score - 50.0) * 0.12
        + (sector_score - 50.0) * 0.05
        + timing * 0.05
        + evidence * 0.04
        + valuation * 0.03
        + manager_score * 0.025
        - drawdown_risk * 0.12
        - approval_friction_penalty
    )
    base_risk_adjusted = risk_adjusted
    base_evidence = evidence
    base_drawdown_risk = drawdown_risk
    llm_adjustments = applied_llm_adjustments(llm_signal)
    if llm_adjustments:
        risk_adjusted = risk_adjusted + llm_adjustments["expected_return_adjustment"]
        evidence = clamp_score(evidence + llm_adjustments["evidence_quality_adjustment"])
        drawdown_risk = clamp_score(drawdown_risk + llm_adjustments["drawdown_risk_adjustment"])
    verdict = research_verdict(risk_adjusted, evidence, drawdown_risk, float(feature.get("current_weight") or 0), feature)
    item = {
        "research_id": f"{as_of.isoformat()}-{feature.get('symbol')}-{MODEL_POLICY_VERSION}",
        "model_policy_version": MODEL_POLICY_VERSION,
        "symbol": feature.get("symbol", ""),
        "bucket": bucket,
        "rank": 0,
        "current_weight": feature.get("current_weight", 0),
        "peer_avg_weight": feature.get("peer_avg_weight", 0),
        "tier1_peer_avg_weight": feature.get("tier1_peer_avg_weight", 0),
        "thesis_summary": BUCKET_THESIS.get(bucket, BUCKET_THESIS["unmapped"]),
        "variant_view": variant_view(feature, card),
        "decision_stack": {
            "company_underwriting_weight": 0.60,
            "sector_setup_weight": 0.20,
            "manager_13f_weight": 0.10,
            "macro_timing_risk_weight": 0.10,
            "company_underwriting_score": round(company_score, 2),
            "sector_setup_score": round(sector_score, 2),
            "manager_13f_score": round(manager_score, 2),
            "macro_timing_risk_score": round(macro_score, 2),
            "combined_score": round(decision_score, 2),
        },
        "company_underwriting_score": round(company_score, 2),
        "sector_setup_score": round(sector_score, 2),
        "sector_headwind": bool(feature.get("sector_headwind", False)),
        "sector_tailwind": bool(feature.get("sector_tailwind", False)),
        "company_add_eligible": bool(feature.get("company_add_eligible", False)),
        "company_trim_signal": bool(feature.get("company_trim_signal", False)),
        "company_review_required": bool(feature.get("company_review_required", False)),
        "company_review_status": feature.get("company_review_status", ""),
        "company_reason": feature.get("company_reason", ""),
        "sector_reason": sector_reason(feature),
        "tertiary_signal_summary": tertiary_signal_summary(feature, macro),
        "bull_return_12m": round(scenario["bull_return_12m"], 2),
        "base_return_12m": round(scenario["base_return_12m"], 2),
        "bear_return_12m": round(scenario["bear_return_12m"], 2),
        "probability_weighted_return": round(probability_weighted, 2),
        "risk_adjusted_expected_return": round(risk_adjusted, 2),
        "base_risk_adjusted_expected_return": round(base_risk_adjusted, 2),
        "timing_score": round(timing, 2),
        "drawdown_risk": round(drawdown_risk, 2),
        "base_drawdown_risk": round(base_drawdown_risk, 2),
        "evidence_quality": round(evidence, 2),
        "base_evidence_quality": round(base_evidence, 2),
        "valuation_support": round(valuation, 2),
        "price_return_1d": feature.get("price_return_1d"),
        "price_return_5d": feature.get("price_return_5d"),
        "price_return_1m": feature.get("price_return_1m"),
        "price_return_3m": feature.get("price_return_3m"),
        "price_return_ytd": feature.get("price_return_ytd"),
        "price_return_1y": feature.get("price_return_1y"),
        "earnings_days_until": feature.get("earnings_days_until"),
        "earnings_event_date": feature.get("earnings_event_date", ""),
        "earnings_event_source": feature.get("earnings_event_source", ""),
        "earnings_confirmed_or_estimated": feature.get("earnings_confirmed_or_estimated", ""),
        "earnings_risk_window": feature.get("earnings_risk_window", ""),
        "earnings_confirmation_required": bool(feature.get("earnings_confirmation_required", False)),
        "external_signal_score": feature.get("external_signal_score"),
        "coverage_adjusted_external_signal_score": feature.get("coverage_adjusted_external_signal_score"),
        "external_coverage_multiplier": feature.get("external_coverage_multiplier"),
        "external_feed_status": feature.get("external_feed_status", ""),
        "external_provider_count": feature.get("external_provider_count"),
        "external_provider_ok_count": feature.get("external_provider_ok_count"),
        "external_provider_ok_ratio": feature.get("external_provider_ok_ratio"),
        "external_provider_gap_count": feature.get("external_provider_gap_count"),
        "external_provider_configuration_gap_count": feature.get("external_provider_configuration_gap_count"),
        "external_provider_transient_gap_count": feature.get("external_provider_transient_gap_count"),
        "external_provider_stale_gap_count": feature.get("external_provider_stale_gap_count"),
        "external_provider_runtime_gap_count": feature.get("external_provider_runtime_gap_count"),
        "external_provider_other_gap_count": feature.get("external_provider_other_gap_count"),
        "external_provider_primary_gap_severity": feature.get("external_provider_primary_gap_severity", ""),
        "external_provider_gap_severity_score": feature.get("external_provider_gap_severity_score"),
        "external_signal_count": feature.get("external_signal_count"),
        "external_source_count": feature.get("external_source_count"),
        "approval_data_friction_score": round(approval_friction, 2),
        "approval_data_friction_bucket": feature.get("approval_data_friction_bucket", "clear"),
        "approval_data_friction_reasons": feature.get("approval_data_friction_reasons", []),
        "approval_data_friction_penalty": round(approval_friction_penalty, 2),
        "catalyst_clock": catalyst_clock(feature),
        "valuation_setup": valuation_setup(feature, scenario),
        "manager_signal": manager_signal(feature),
        "macro_sensitivity": macro_sensitivity(feature, macro),
        "risk": card.get("counterargument") or risk_summary(feature),
        "falsifier": feature.get("company_risk_falsifier") or card.get("falsifier") or "Forward public evidence contradicts the expected AI demand, pricing, or margin path.",
        "review_required": bool(feature.get("company_review_required", False)) or bool((llm_signal or {}).get("llm_review_required", False)),
        "review_reason": review_reason(feature, verdict),
        "verdict": verdict,
        "signal_families": feature.get("signal_families", []),
        "event_types": feature.get("event_types", []),
        "source_tiers": feature.get("source_tiers", []),
        "feature_id": feature.get("feature_id", ""),
    }
    if llm_adjustments:
        item["llm_signal"] = llm_signal_summary(llm_signal or {}, llm_adjustments)
        item.update(item["llm_signal"])
    return item


def llm_signal_by_symbol(llm_signal: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not llm_signal or llm_signal.get("status") != "ok" or llm_signal.get("mode") != "bounded_signal":
        return {}
    return {
        str(row.get("symbol") or "").upper(): row
        for row in llm_signal.get("reviews") or []
        if str(row.get("symbol") or "").strip()
    }


def applied_llm_adjustments(signal: dict[str, Any] | None) -> dict[str, float]:
    if not signal:
        return {}
    confidence = max(0.0, min(1.0, float(signal.get("confidence") or 0.0)))
    return {
        "expected_return_adjustment": round(float(signal.get("llm_expected_return_delta") or 0.0) * confidence, 4),
        "evidence_quality_adjustment": round(float(signal.get("llm_evidence_quality_delta") or 0.0) * confidence, 4),
        "drawdown_risk_adjustment": round(float(signal.get("llm_drawdown_risk_delta") or 0.0) * confidence, 4),
    }


def llm_signal_summary(signal: dict[str, Any], adjustments: dict[str, float]) -> dict[str, Any]:
    keys = (
        "thesis_quality",
        "decision_usefulness_score",
        "llm_expected_return_delta",
        "llm_evidence_quality_delta",
        "llm_drawdown_risk_delta",
        "llm_conviction_score",
        "llm_variant_quality_score",
        "llm_source_quality_score",
        "llm_contradiction_risk_score",
        "llm_staleness_risk_score",
        "llm_review_required",
        "confidence",
        "rationale",
    )
    summary = {key: signal.get(key) for key in keys if key in signal}
    summary["llm_expected_return_adjustment"] = adjustments["expected_return_adjustment"]
    summary["llm_evidence_quality_adjustment"] = adjustments["evidence_quality_adjustment"]
    summary["llm_drawdown_risk_adjustment"] = adjustments["drawdown_risk_adjustment"]
    summary["llm_signal_applied"] = True
    return summary


def clamp_score(value: float) -> float:
    return max(0.0, min(100.0, value))


def scenario_returns(feature: dict[str, Any], bucket: str) -> dict[str, float]:
    base = dict(BUCKET_SCENARIOS.get(bucket, BUCKET_SCENARIOS["unmapped"]))
    company_score = float(feature.get("company_underwriting_score") or 45.0)
    sector_score = float(feature.get("sector_setup_score") or 50.0)
    company_bonus = (company_score - 50.0) * 0.38
    sector_bonus = (sector_score - 50.0) * 0.18
    evidence_bonus = (float(feature.get("evidence_quality") or 0) - 50.0) * 0.12
    external_bonus = max(-4.0, min(4.0, external_scenario_score(feature) * 0.14))
    tier1_bonus = min(float(feature.get("tier1_manager_count") or 0) * 1.25, 3.75)
    catalyst_bonus = min(float(feature.get("event_score") or 0) * 0.55, 6.5)
    valuation_penalty = max(0.0, 50.0 - float(feature.get("valuation_support") or 50.0)) * 0.22
    risk_penalty = max(0.0, float(feature.get("drawdown_risk") or 0) - 55.0) * 0.2
    macro_penalty = macro_forward_return_penalty(feature, bucket)
    if feature.get("sector_headwind"):
        macro_penalty += 4.0
    if feature.get("company_trim_signal"):
        risk_penalty += 6.0
    one_year = feature.get("price_return_1y")
    if one_year is not None and float(one_year) > 100:
        valuation_penalty += 7.0
    return {
        "bull_return_12m": base["bull"] + company_bonus + sector_bonus + evidence_bonus + external_bonus + tier1_bonus + catalyst_bonus - valuation_penalty - macro_penalty * 0.35,
        "base_return_12m": base["base"] + company_bonus * 0.62 + sector_bonus * 0.70 + evidence_bonus * 0.55 + external_bonus * 0.55 + tier1_bonus * 0.35 + catalyst_bonus * 0.40 - valuation_penalty - risk_penalty - macro_penalty,
        "bear_return_12m": base["bear"] + min(0.0, company_bonus * 0.45) + min(0.0, sector_bonus * 0.65) + min(0.0, external_bonus * 0.5) - risk_penalty - max(0.0, valuation_penalty * 0.8) - macro_penalty * 1.35,
    }


def external_scenario_score(feature: dict[str, Any]) -> float:
    adjusted = feature.get("coverage_adjusted_external_signal_score")
    if adjusted is not None:
        return float(adjusted or 0)
    return float(feature.get("external_signal_score") or 0)


def macro_forward_return_penalty(feature: dict[str, Any], bucket: str) -> float:
    credit = max(0.0, float(feature.get("macro_credit_stress") or 0))
    liquidity = max(0.0, float(feature.get("macro_liquidity_pressure") or 0))
    curve = max(0.0, float(feature.get("macro_yield_curve_inversion") or 0))
    energy = max(0.0, float(feature.get("macro_energy_pressure") or 0))
    penalty = credit * 0.20 + liquidity * 0.16 + curve * 0.12
    if bucket in {"neocloud_datacenters", "power_grid_gas_nuclear"}:
        penalty += credit * 0.28 + liquidity * 0.18 + curve * 0.10
    if bucket == "power_grid_gas_nuclear":
        penalty += energy * 0.14
    if bucket == "semis_networking_hbm":
        penalty += credit * 0.10
    return min(10.0, penalty)


def scenario_probabilities(feature: dict[str, Any]) -> dict[str, float]:
    evidence = float(feature.get("evidence_quality") or 0)
    drawdown = float(feature.get("drawdown_risk") or 0)
    timing = float(feature.get("timing_score") or 0)
    bull = 0.24 + max(-0.08, min(0.10, (evidence - 55.0) / 500.0)) + max(-0.04, min(0.06, (timing - 50.0) / 700.0))
    bear = 0.24 + max(-0.06, min(0.12, (drawdown - 50.0) / 420.0))
    bull = max(0.12, min(0.42, bull))
    bear = max(0.12, min(0.42, bear))
    base = max(0.20, 1.0 - bull - bear)
    total = bull + base + bear
    return {"bull": bull / total, "base": base / total, "bear": bear / total}


def research_verdict(expected: float, evidence: float, drawdown: float, current_weight: float, feature: dict[str, Any] | None = None) -> str:
    feature = feature or {}
    company_score = float(feature.get("company_underwriting_score") or 45)
    company_add_eligible = bool(feature.get("company_add_eligible", False))
    company_trim_signal = bool(feature.get("company_trim_signal", False))
    if current_weight and (company_trim_signal or company_score < 38):
        return "trim"
    if expected >= 24 and evidence >= 58 and drawdown <= 72 and company_add_eligible:
        return "buy_more" if current_weight else "starter"
    if expected >= 24 and not company_add_eligible:
        return "hold" if current_weight else "study"
    if expected >= 14 and evidence >= 45 and company_score >= 48:
        return "hold" if current_weight else "study"
    if current_weight and (expected < 6 or drawdown >= 82):
        return "trim"
    if expected < 2 and not current_weight:
        return "avoid"
    return "study"


def variant_view(feature: dict[str, Any], card: dict[str, Any]) -> str:
    symbol = feature.get("symbol", "This name")
    company_score = float(feature.get("company_underwriting_score") or 0)
    if company_score >= 62:
        return f"{symbol} has company-specific evidence strong enough to matter before manager confirmation; the question is sizing versus valuation and risk."
    if feature.get("company_trim_signal"):
        return f"{symbol} has deteriorating company evidence; do not let delayed manager filings override the bottom-up warning."
    tier1_count = int(feature.get("tier1_manager_count") or 0)
    event_score = float(feature.get("event_score") or 0)
    if tier1_count:
        return f"{symbol} is validated by Tier 1 manager ownership; the question is whether current sizing and entry still offer forward alpha."
    if event_score >= 6:
        return f"{symbol} has catalyst confirmation, but the model needs proof that the news changes forward revenue, margins, or capital intensity."
    return card.get("candidate") or "The variant view must be earned with stronger evidence before the model adds size."


def catalyst_clock(feature: dict[str, Any]) -> str:
    days = feature.get("earnings_days_until")
    events = [str(item) for item in feature.get("event_types") or []]
    if days is not None:
        estimated = str(feature.get("earnings_confirmed_or_estimated") or "").lower() == "estimated"
        if abs(int(days)) <= 2:
            if estimated:
                return "Estimated earnings blackout now; confirm the date before approving adds."
            return "Earnings blackout now; do not add until the event clears."
        if abs(int(days)) <= 7:
            if estimated:
                return "Near estimated earnings; confirm the date and cap new add size."
            return "Near earnings; starter or add size should be capped."
        if int(days) > 0:
            if estimated:
                return f"{int(days)} days to estimated earnings or filing catalyst; confirm date before sizing adds."
            return f"{int(days)} days to earnings or filing catalyst."
    if events:
        return "Active catalyst tape: " + ", ".join(events[:3])
    return "No dated catalyst; require price discipline and fresh evidence."


def valuation_setup(feature: dict[str, Any], scenario: dict[str, float]) -> str:
    support = float(feature.get("valuation_support") or 0)
    if support >= 62:
        return f"Valuation support is constructive; base case is {scenario['base_return_12m']:.1f}% 12m return."
    if support <= 38:
        return "Valuation support is weak; require a better entry or stronger earnings revision evidence."
    return "Valuation setup is neutral; sizing should depend on catalyst quality and downside control."


def manager_signal(feature: dict[str, Any]) -> str:
    manager_count = int(feature.get("manager_count") or 0)
    tier1_count = int(feature.get("tier1_manager_count") or 0)
    peer = float(feature.get("peer_avg_weight") or 0)
    if tier1_count:
        return f"{tier1_count} Tier 1 managers and {manager_count} tracked managers own it; peer average weight is {peer:.1%}."
    if manager_count:
        return f"{manager_count} tracked managers own it; peer average weight is {peer:.1%}."
    return "No tracked-manager confirmation in the latest public filing set."


def manager_confirmation_score(feature: dict[str, Any]) -> float:
    manager_count = min(6.0, float(feature.get("manager_count") or 0))
    tier1_count = min(3.0, float(feature.get("tier1_manager_count") or 0))
    add_signal = max(0.0, float(feature.get("manager_add_signal") or 0))
    trim_signal = max(0.0, float(feature.get("manager_reduction_signal") or 0))
    peer = min(12.0, float(feature.get("peer_avg_weight") or 0) * 100)
    return max(0.0, min(100.0, 30.0 + manager_count * 5.0 + tier1_count * 8.0 + add_signal * 0.6 + peer - trim_signal * 0.6))


def macro_timing_score(feature: dict[str, Any]) -> float:
    timing = float(feature.get("timing_score") or 0)
    credit = max(0.0, float(feature.get("macro_credit_stress") or 0))
    liquidity = max(0.0, float(feature.get("macro_liquidity_pressure") or 0))
    curve = max(0.0, float(feature.get("macro_yield_curve_inversion") or 0))
    return max(0.0, min(100.0, timing - credit * 0.6 - liquidity * 0.4 - curve * 0.25))


def decision_stack_score(company: float, sector: float, manager: float, macro: float) -> float:
    return company * 0.60 + sector * 0.20 + manager * 0.10 + macro * 0.10


def sector_reason(feature: dict[str, Any]) -> str:
    sector = float(feature.get("sector_setup_score") or 50)
    bucket = str(feature.get("bucket") or "unmapped")
    setup = str(feature.get("sector_setup") or BUCKET_THESIS.get(bucket, ""))
    if feature.get("sector_headwind"):
        return f"{bucket} is a headwind, so target size is capped even if company work is constructive. {setup}"
    if feature.get("sector_tailwind"):
        return f"{bucket} is a tailwind; sector context supports raising targets when company evidence clears the bar. {setup}"
    return f"{bucket} sector setup is neutral at {sector:.1f}/100. {setup}"


def tertiary_signal_summary(feature: dict[str, Any], macro: dict[str, Any]) -> str:
    return (
        f"13F: {manager_signal(feature)} "
        f"Macro/timing: {macro_sensitivity(feature, macro)}"
    )


def review_reason(feature: dict[str, Any], verdict: str) -> str:
    if feature.get("company_trim_signal"):
        return "Company deterioration overrides 13F confirmation; review whether this should be trimmed faster."
    if verdict in {"buy_more", "starter"} and not feature.get("company_add_eligible"):
        return "Add blocked because company underwriting did not clear the bottom-up evidence floor."
    if feature.get("company_review_required"):
        return "Company evidence needs a deeper memo before increasing target size."
    if feature.get("sector_headwind"):
        return "Sector headwind caps sizing; review timing before adding."
    return "No special review gate beyond normal approval."


def macro_sensitivity(feature: dict[str, Any], macro: dict[str, Any]) -> str:
    bucket = str(feature.get("bucket") or "")
    regime = macro.get("regime") or "mixed"
    credit = float(feature.get("macro_credit_stress") or 0)
    liquidity = float(feature.get("macro_liquidity_pressure") or 0)
    if (credit >= 5 or liquidity >= 5) and bucket in {"neocloud_datacenters", "power_grid_gas_nuclear"}:
        return f"Macro regime is {regime}; FRED credit/liquidity stress is a direct financing and sizing constraint."
    if bucket in {"semis_networking_hbm", "neocloud_datacenters"}:
        return f"High AI beta; macro regime is {regime}, so capex momentum and rates matter."
    if bucket == "power_grid_gas_nuclear":
        return f"Sensitive to power prices, rates, and project finance; macro regime is {regime}."
    return f"Macro regime is {regime}; monitor risk appetite and discount-rate pressure."


def risk_summary(feature: dict[str, Any]) -> str:
    events = [str(item) for item in feature.get("event_types") or []]
    if events:
        return "Risk flags to underwrite: " + ", ".join(events[:3])
    if float(feature.get("short_interest_risk_score") or 0) < -5:
        return "Short-interest feed is adding downside risk; confirm whether borrow/crowding risk is thesis-relevant."
    if float(feature.get("drawdown_risk") or 0) >= 70:
        return "Drawdown risk is elevated from concentration, recent price action, or crowding."
    return "Main risk is that the AI thesis is right but already priced into forward returns."


def verdict_counts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for item in items:
        verdict = str(item.get("verdict") or "study")
        counts[verdict] = counts.get(verdict, 0) + 1
    return [{"verdict": key, "count": value} for key, value in sorted(counts.items())]
