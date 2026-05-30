from __future__ import annotations

from datetime import date
from statistics import mean
from typing import Any

from .risk import normalize_limits
from .symbols import equivalent_symbols, proxied_lookup, proxy_index, symbol_proxy_key
from .util import stable_id


ENGINE_POLICY_VERSION = "2026-05-bottom-up-first-v4"
ENGINE_MODE = "approval_plus_paper"
OBJECTIVE = "maximize_expected_3_12m_forward_return"
MIN_LEARNING_OUTCOMES = 20
MIN_FAMILY_OUTCOMES = 3
FULL_FAMILY_CONFIDENCE_OUTCOMES = 10
MAX_LEARNING_RETURN_ABS = 40.0
LEARNING_RANK_MULTIPLIER = 4.0
MAX_RANK_LEARNING_ADJUSTMENT = 6.0


def build_engine_snapshot(
    as_of: date,
    session: str,
    cards: list[dict[str, Any]],
    portfolio: dict[str, Any],
    portfolio_benchmark: dict[str, Any],
    approval_tickets: list[dict[str, Any]],
    risk_limits: dict[str, Any] | None = None,
    outcome_history: list[dict[str, Any]] | None = None,
    feature_matrix: dict[str, Any] | None = None,
    research_book: dict[str, Any] | None = None,
    llm_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    features = build_engine_features(as_of, cards, portfolio, portfolio_benchmark, feature_matrix, research_book)
    learning = build_learning_state(outcome_history or [])
    ranked = rank_candidates(features, learning, approval_tickets)
    optimizer = build_allocator_output(ranked, portfolio, approval_tickets, risk_limits)
    ticket_by_symbol = {str(ticket.get("symbol") or "").upper(): ticket for ticket in approval_tickets}
    llm_review_by_symbol = {
        str(row.get("symbol") or "").upper(): row
        for row in (llm_review or {}).get("reviews") or []
    }
    provenance = [
        recommendation_provenance(row, ticket_by_symbol.get(row["symbol"]), llm_review_by_symbol.get(row["symbol"]))
        for row in ranked[:20]
    ]
    return {
        "version": ENGINE_POLICY_VERSION,
        "mode": ENGINE_MODE,
        "universe": "equities_only",
        "objective": OBJECTIVE,
        "horizons": ["3m", "6m", "12m"],
        "as_of": as_of.isoformat(),
        "session": session,
        "live_order_execution": "disabled",
        "learning": learning,
        "feature_count": len(features),
        "ranked_candidates": ranked[:20],
        "recommendation_provenance": provenance,
        "optimizer": optimizer,
    }


def build_engine_features(
    as_of: date,
    cards: list[dict[str, Any]],
    portfolio: dict[str, Any],
    portfolio_benchmark: dict[str, Any],
    feature_matrix: dict[str, Any] | None = None,
    research_book: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if feature_matrix and feature_matrix.get("rows"):
        return build_engine_features_from_matrix(as_of, feature_matrix, research_book or {})
    current_weights: dict[str, float] = {}
    for row in portfolio.get("by_symbol", []):
        if row.get("is_cash"):
            continue
        for candidate in equivalent_symbols(row.get("symbol")):
            weight = float(row.get("comparison_weight", row.get("ex_cash_weight", row.get("weight") or 0)) or 0)
            current_weights[candidate] = current_weights.get(candidate, 0.0) + weight
    peer_weight_by_symbol = {
        str(row.get("symbol") or "").upper(): float(row.get("peer_avg_weight") or 0)
        for row in portfolio_benchmark.get("exposure_gaps", [])
    }
    features = []
    for card in cards:
        symbol = str(card.get("symbol") or "").upper()
        if not symbol:
            continue
        event_types = [str(item) for item in card.get("top_event_types", [])]
        score_components = card.get("score_components") or {}
        risk_penalty = event_risk_penalty(event_types, float(card.get("put_value") or 0), float(card.get("call_value") or 0))
        peer_weight = float(proxied_lookup(peer_weight_by_symbol, symbol, 0.0) or 0.0)
        expected_return_score = expected_forward_return_score(card, peer_weight, risk_penalty)
        features.append(
            {
                "feature_id": stable_id([as_of.isoformat(), ENGINE_POLICY_VERSION, symbol]),
                "symbol": symbol,
                "bucket": card.get("bucket", "unmapped"),
                "score": round(float(card.get("score") or 0), 2),
                "expected_return_score": round(expected_return_score, 2),
                "current_weight": round(current_weights.get(symbol, 0.0), 6),
                "peer_avg_weight": round(peer_weight, 6),
                "signal_family_count": int(card.get("signal_family_count") or len(card.get("signal_families") or [])),
                "signal_families": card.get("signal_families") or [],
                "manager_count": int(card.get("consensus_manager_count") or 0),
                "event_score": round(float(card.get("event_score") or 0), 2),
                "event_types": event_types,
                "price_action_5d": card.get("five_day_pct"),
                "risk_penalty": round(risk_penalty, 2),
                "component_scores": score_components,
            }
        )
    return features


def build_engine_features_from_matrix(
    as_of: date,
    feature_matrix: dict[str, Any],
    research_book: dict[str, Any],
) -> list[dict[str, Any]]:
    research_by_symbol = proxy_index(research_book.get("items", []))
    rows = []
    for feature in feature_matrix.get("rows", []):
        symbol = str(feature.get("symbol") or "").upper()
        if not symbol:
            continue
        research = research_by_symbol.get(symbol, {})
        expected = float(research.get("risk_adjusted_expected_return", feature.get("expected_return_score") or 0) or 0)
        adjusted_external_score = adjusted_external_signal_score(feature)
        rows.append(
            {
                "feature_id": feature.get("feature_id") or stable_id([as_of.isoformat(), ENGINE_POLICY_VERSION, symbol]),
                "symbol": symbol,
                "bucket": feature.get("bucket", "unmapped"),
                "score": round(float(feature.get("score") or 0), 2),
                "expected_return_score": round(expected, 2),
                "risk_adjusted_expected_return": round(expected, 2),
                "base_risk_adjusted_expected_return": research.get("base_risk_adjusted_expected_return"),
                "base_evidence_quality": research.get("base_evidence_quality"),
                "base_drawdown_risk": research.get("base_drawdown_risk"),
                "llm_signal_applied": bool(research.get("llm_signal_applied", False)),
                "llm_expected_return_delta": research.get("llm_expected_return_delta"),
                "llm_expected_return_adjustment": research.get("llm_expected_return_adjustment"),
                "llm_evidence_quality_delta": research.get("llm_evidence_quality_delta"),
                "llm_evidence_quality_adjustment": research.get("llm_evidence_quality_adjustment"),
                "llm_drawdown_risk_delta": research.get("llm_drawdown_risk_delta"),
                "llm_drawdown_risk_adjustment": research.get("llm_drawdown_risk_adjustment"),
                "llm_conviction_score": research.get("llm_conviction_score"),
                "llm_contradiction_risk_score": research.get("llm_contradiction_risk_score"),
                "llm_staleness_risk_score": research.get("llm_staleness_risk_score"),
                "probability_weighted_return": research.get("probability_weighted_return"),
                "bull_return_12m": research.get("bull_return_12m"),
                "base_return_12m": research.get("base_return_12m"),
                "bear_return_12m": research.get("bear_return_12m"),
                "current_weight": round(float(feature.get("current_weight") or 0), 6),
                "peer_avg_weight": round(float(feature.get("peer_avg_weight") or 0), 6),
                "tier1_peer_avg_weight": round(float(feature.get("tier1_peer_avg_weight") or 0), 6),
                "signal_family_count": int(feature.get("signal_family_count") or len(feature.get("signal_families") or [])),
                "signal_families": feature.get("signal_families") or [],
                "manager_count": int(feature.get("manager_count") or 0),
                "tier1_manager_count": int(feature.get("tier1_manager_count") or 0),
                "event_score": round(float(feature.get("event_score") or 0), 2),
                "event_types": feature.get("event_types") or [],
                "price_action_5d": feature.get("price_return_5d"),
                "timing_score": feature.get("timing_score"),
                "drawdown_risk": feature.get("drawdown_risk"),
                "evidence_quality": feature.get("evidence_quality"),
                "valuation_support": feature.get("valuation_support"),
                "company_underwriting_score": feature.get("company_underwriting_score"),
                "sector_setup_score": feature.get("sector_setup_score"),
                "company_add_eligible": feature.get("company_add_eligible"),
                "company_trim_signal": feature.get("company_trim_signal"),
                "company_review_required": feature.get("company_review_required"),
                "company_reason": feature.get("company_reason"),
                "sector_headwind": feature.get("sector_headwind"),
                "sector_tailwind": feature.get("sector_tailwind"),
                "external_signal_score": feature.get("external_signal_score"),
                "coverage_adjusted_external_signal_score": adjusted_external_score,
                "external_coverage_multiplier": feature.get("external_coverage_multiplier"),
                "external_feed_status": feature.get("external_feed_status"),
                "external_provider_count": feature.get("external_provider_count"),
                "external_provider_ok_count": feature.get("external_provider_ok_count"),
                "external_provider_ok_ratio": feature.get("external_provider_ok_ratio"),
                "external_provider_gap_count": feature.get("external_provider_gap_count"),
                "external_provider_configuration_gap_count": feature.get("external_provider_configuration_gap_count"),
                "external_provider_transient_gap_count": feature.get("external_provider_transient_gap_count"),
                "external_provider_stale_gap_count": feature.get("external_provider_stale_gap_count"),
                "external_provider_runtime_gap_count": feature.get("external_provider_runtime_gap_count"),
                "external_provider_other_gap_count": feature.get("external_provider_other_gap_count"),
                "external_provider_primary_gap_severity": feature.get("external_provider_primary_gap_severity"),
                "external_provider_gap_severity_score": feature.get("external_provider_gap_severity_score"),
                "external_signal_count": feature.get("external_signal_count"),
                "external_source_count": feature.get("external_source_count"),
                "component_scores": {
                    "expected_return": round(expected, 2),
                    "timing": feature.get("timing_score"),
                    "drawdown_risk": feature.get("drawdown_risk"),
                    "evidence_quality": feature.get("evidence_quality"),
                    "valuation_support": feature.get("valuation_support"),
                    "company_underwriting": feature.get("company_underwriting_score"),
                    "sector_setup": feature.get("sector_setup_score"),
                    "external_signals": adjusted_external_score,
                    "external_signals_raw": feature.get("external_signal_score"),
                },
            }
        )
    return rows


def adjusted_external_signal_score(feature: dict[str, Any]) -> Any:
    adjusted = feature.get("coverage_adjusted_external_signal_score")
    if adjusted is not None:
        return adjusted
    return feature.get("external_signal_score")


def expected_forward_return_score(card: dict[str, Any], peer_weight: float, risk_penalty: float) -> float:
    score = float(card.get("score") or 0)
    signal_count = int(card.get("signal_family_count") or len(card.get("signal_families") or []))
    manager_count = int(card.get("consensus_manager_count") or 0)
    event_score = float(card.get("event_score") or 0)
    price_action = float(card.get("five_day_pct") or 0)
    entry_penalty = 4.0 if price_action > 12 else 1.5 if price_action > 8 else 0.0
    drawdown_bonus = 4.0 if price_action < -10 and signal_count >= 2 else 0.0
    peer_bonus = min(peer_weight * 100, 8.0)
    return score * 0.55 + signal_count * 4.0 + manager_count * 1.8 + event_score * 1.2 + peer_bonus + drawdown_bonus - risk_penalty - entry_penalty


def event_risk_penalty(event_types: list[str], put_value: float, call_value: float) -> float:
    penalty = 0.0
    hard_risks = {"financing_risk", "regulatory_risk", "crowding_warning"}
    penalty += len(hard_risks & set(event_types)) * 8.0
    if put_value > max(call_value * 1.25, 50_000_000):
        penalty += 5.0
    return penalty


def build_learning_state(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [
        row
        for row in outcomes
        if row.get("forward_return_pct") is not None and row.get("signal_families")
    ]
    short_horizon = [row for row in completed if row.get("horizon") == "5d"]
    completed = [row for row in completed if row.get("horizon") != "5d"]
    expected_scored = [row for row in completed if expected_score(row) is not None]
    if len(completed) < MIN_LEARNING_OUTCOMES:
        return {
            "status": "baseline_fallback",
            "outcome_count": len(completed),
            "short_horizon_outcome_count": len(short_horizon),
            "expected_scored_outcome_count": len(expected_scored),
            "minimum_required": MIN_LEARNING_OUTCOMES,
            "minimum_family_outcomes": MIN_FAMILY_OUTCOMES,
            "full_family_confidence_outcomes": FULL_FAMILY_CONFIDENCE_OUTCOMES,
            "learning_return_cap": MAX_LEARNING_RETURN_ABS,
            "rank_learning_multiplier": LEARNING_RANK_MULTIPLIER,
            "rank_learning_adjustment_cap": MAX_RANK_LEARNING_ADJUSTMENT,
            "family_sample_counts": {},
            "family_confidence": {},
            "weight_adjustments": {},
            "message": "Insufficient completed 1-12 month signal-family outcomes; 5-day labels are tracked for fast diagnostics but do not adjust ranking weights.",
        }
    by_family: dict[str, list[float]] = {}
    for row in completed:
        forward = learning_return(row)
        for family in row.get("signal_families") or []:
            by_family.setdefault(str(family), []).append(forward)
    family_counts = {family: len(values) for family, values in by_family.items()}
    averages = {family: mean(values) for family, values in by_family.items() if len(values) >= MIN_FAMILY_OUTCOMES}
    if not averages:
        return {
            "status": "baseline_fallback",
            "outcome_count": len(completed),
            "short_horizon_outcome_count": len(short_horizon),
            "expected_scored_outcome_count": len(expected_scored),
            "minimum_required": MIN_LEARNING_OUTCOMES,
            "minimum_family_outcomes": MIN_FAMILY_OUTCOMES,
            "full_family_confidence_outcomes": FULL_FAMILY_CONFIDENCE_OUTCOMES,
            "learning_return_cap": MAX_LEARNING_RETURN_ABS,
            "rank_learning_multiplier": LEARNING_RANK_MULTIPLIER,
            "rank_learning_adjustment_cap": MAX_RANK_LEARNING_ADJUSTMENT,
            "family_sample_counts": family_counts,
            "family_confidence": {},
            "weight_adjustments": {},
            "message": "Completed outcomes exist, but no signal family has enough samples to adjust ranking weights.",
        }
    cross_avg = mean(averages.values()) if averages else 0.0
    family_confidence = {
        family: sample_confidence(family_counts[family])
        for family in averages
    }
    adjustments = {
        family: round(max(-8.0, min(8.0, value - cross_avg)) / 8.0 * family_confidence[family], 3)
        for family, value in averages.items()
    }
    return {
        "status": "history_adjusted",
        "outcome_count": len(completed),
        "short_horizon_outcome_count": len(short_horizon),
        "expected_scored_outcome_count": len(expected_scored),
        "minimum_required": MIN_LEARNING_OUTCOMES,
        "minimum_family_outcomes": MIN_FAMILY_OUTCOMES,
        "full_family_confidence_outcomes": FULL_FAMILY_CONFIDENCE_OUTCOMES,
        "learning_return_cap": MAX_LEARNING_RETURN_ABS,
        "rank_learning_multiplier": LEARNING_RANK_MULTIPLIER,
        "rank_learning_adjustment_cap": MAX_RANK_LEARNING_ADJUSTMENT,
        "family_sample_counts": family_counts,
        "family_confidence": family_confidence,
        "weight_adjustments": adjustments,
        "message": "Signal-family weights adjusted from completed recommendation outcomes.",
    }


def expected_score(row: dict[str, Any]) -> Any:
    expected = row.get("risk_adjusted_expected_return")
    if expected is not None:
        return expected
    return row.get("expected_return_score")


def learning_return(row: dict[str, Any]) -> float:
    forward = float(row.get("forward_return_pct") or 0)
    expected = expected_score(row)
    if expected is None:
        return capped_learning_return(forward)
    return capped_learning_return(forward - float(expected or 0))


def capped_learning_return(value: float) -> float:
    return max(-MAX_LEARNING_RETURN_ABS, min(MAX_LEARNING_RETURN_ABS, value))


def sample_confidence(count: int) -> float:
    return round(min(1.0, max(0.0, count / FULL_FAMILY_CONFIDENCE_OUTCOMES)), 3)


def rank_candidates(
    features: list[dict[str, Any]],
    learning: dict[str, Any],
    tickets: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    adjustments = learning.get("weight_adjustments") or {}
    exact_ticket_by_symbol = {str(ticket.get("symbol") or "").upper(): ticket for ticket in tickets or []}
    scored = []
    for input_index, row in enumerate(features):
        learning_delta = rank_learning_adjustment(row.get("signal_families", []), adjustments)
        expected = float(row.get("risk_adjusted_expected_return", row.get("expected_return_score") or 0) or 0) + learning_delta
        readiness = decision_readiness(row)
        item = dict(row)
        item["symbol_proxy_key"] = symbol_proxy_key(item.get("symbol"))
        item["learning_adjustment"] = round(learning_delta, 2)
        item["learning_adjustment_cap"] = MAX_RANK_LEARNING_ADJUSTMENT
        item["expected_return_rank_score"] = round(expected, 2)
        item["decision_readiness_score"] = readiness["score"]
        item["decision_readiness_bucket"] = readiness["bucket"]
        item["decision_evidence_blockers"] = readiness["blockers"]
        item["_rank_input_index"] = input_index
        scored.append(item)
    ranked = dedupe_equivalent_candidates(scored, exact_ticket_by_symbol)
    ranked.sort(key=lambda item: item["expected_return_rank_score"], reverse=True)
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
        item.pop("_rank_input_index", None)
    return ranked


def dedupe_equivalent_candidates(
    candidates: list[dict[str, Any]],
    exact_ticket_by_symbol: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    by_proxy: dict[str, dict[str, Any]] = {}
    duplicate_symbols: dict[str, list[str]] = {}
    for item in candidates:
        proxy_key = str(item.get("symbol_proxy_key") or item.get("symbol") or "")
        existing = by_proxy.get(proxy_key)
        if existing is None:
            by_proxy[proxy_key] = item
            duplicate_symbols.setdefault(proxy_key, [])
            continue
        preferred, duplicate = preferred_equivalent_candidate(existing, item, exact_ticket_by_symbol)
        by_proxy[proxy_key] = preferred
        duplicate_symbols.setdefault(proxy_key, [])
        duplicate_symbols[proxy_key].append(str(duplicate.get("symbol") or ""))
    for proxy_key, item in by_proxy.items():
        symbols = sorted({symbol for symbol in duplicate_symbols.get(proxy_key, []) if symbol and symbol != item.get("symbol")})
        if symbols:
            item["deduplicated_equivalent_symbols"] = symbols
    return list(by_proxy.values())


def preferred_equivalent_candidate(
    left: dict[str, Any],
    right: dict[str, Any],
    exact_ticket_by_symbol: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    left_key = equivalent_candidate_key(left, exact_ticket_by_symbol)
    right_key = equivalent_candidate_key(right, exact_ticket_by_symbol)
    if right_key > left_key:
        return right, left
    return left, right


def equivalent_candidate_key(item: dict[str, Any], exact_ticket_by_symbol: dict[str, dict[str, Any]]) -> tuple[float, int, float, float, int]:
    symbol = str(item.get("symbol") or "").upper()
    return (
        float(item.get("expected_return_rank_score") or 0),
        actionable_ticket_priority(exact_ticket_by_symbol.get(symbol)),
        float(item.get("decision_readiness_score") or 0),
        float(item.get("current_weight") or 0),
        -int(item.get("_rank_input_index") or 0),
    )


def actionable_ticket_priority(ticket: dict[str, Any] | None) -> int:
    if not ticket:
        return 0
    delta = abs(float(ticket.get("recommended_delta_weight") or 0))
    action = str(ticket.get("trade_action") or "").lower()
    if delta > 0 or action in {"add", "trim", "reduce", "sell"}:
        return 1
    return 0


def rank_learning_adjustment(signal_families: list[Any], adjustments: dict[str, Any]) -> float:
    raw = sum(float(adjustments.get(family, 0)) for family in signal_families) * LEARNING_RANK_MULTIPLIER
    return max(-MAX_RANK_LEARNING_ADJUSTMENT, min(MAX_RANK_LEARNING_ADJUSTMENT, raw))


def decision_readiness(feature: dict[str, Any]) -> dict[str, Any]:
    score = 100.0
    blockers: list[str] = []

    if feature.get("company_review_required") or feature.get("review_required"):
        score -= 22.0
        blockers.append("company_underwriting_review_required")

    evidence_quality = optional_float(feature.get("evidence_quality"))
    if evidence_quality is not None:
        if evidence_quality < 35.0:
            score -= 20.0
            blockers.append("evidence_quality_low")
        elif evidence_quality < 50.0:
            score -= 12.0
            blockers.append("evidence_quality_watch")

    status = str(feature.get("external_feed_status") or "").strip().lower()
    gap_count = int(feature.get("external_provider_gap_count") or 0)
    gap_severity = optional_float(feature.get("external_provider_gap_severity_score")) or 0.0
    if status and status != "ok":
        score -= min(30.0, 12.0 + gap_severity * 0.18)
        blockers.append("external_feed_reliability_review_required")
    elif gap_count:
        score -= min(18.0, gap_count * 3.0)
        blockers.append("external_provider_coverage_incomplete")

    if feature.get("earnings_confirmation_required"):
        score -= 16.0
        blockers.append("earnings_confirmation_required")

    if feature.get("company_trim_signal"):
        score -= 18.0
        blockers.append("company_trim_signal")

    readiness_score = round(max(0.0, min(100.0, score)), 2)
    return {
        "score": readiness_score,
        "bucket": decision_readiness_bucket(readiness_score, blockers),
        "blockers": blockers,
    }


def decision_readiness_bucket(score: float, blockers: list[str]) -> str:
    if not blockers and score >= 85.0:
        return "approval_ready"
    if score >= 65.0:
        return "review_before_sizing"
    return "evidence_blocked"


def optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_allocator_output(
    ranked: list[dict[str, Any]],
    portfolio: dict[str, Any],
    tickets: list[dict[str, Any]],
    risk_limits: dict[str, Any] | None,
) -> dict[str, Any]:
    limits = normalize_limits(risk_limits)
    ticket_by_symbol = {str(ticket.get("symbol") or "").upper(): ticket for ticket in tickets}
    allocations = []
    turnover = 0.0
    for row in ranked[:20]:
        symbol = row["symbol"]
        ticket = ticket_by_symbol.get(symbol)
        current = float(row.get("current_weight") or 0)
        if ticket:
            delta = float(ticket.get("recommended_delta_weight") or 0)
            target = float(ticket.get("target_weight") or current + delta)
            action = ticket.get("trade_action", "study")
            risk_flags = ticket.get("risk_flags") or []
        else:
            delta = 0.0
            target = current
            action = "study"
            risk_flags = []
        turnover += abs(delta)
        allocations.append(
            {
                "symbol": symbol,
                "rank": row["rank"],
                "trade_action": action,
                "current_weight": round(current, 6),
                "recommended_delta_weight": round(delta, 6),
                "target_weight": round(target, 6),
                "model_target_weight": round(float(ticket.get("model_target_weight", target)) if ticket else target, 6),
                "expected_return_rank_score": row["expected_return_rank_score"],
                "risk_adjusted_expected_return": row.get("risk_adjusted_expected_return", row.get("expected_return_score", 0)),
                "decision_readiness_score": row.get("decision_readiness_score"),
                "decision_readiness_bucket": row.get("decision_readiness_bucket"),
                "decision_evidence_blockers": row.get("decision_evidence_blockers", []),
                "risk_flags": risk_flags,
            }
        )
    return {
        "type": "long_only_weight_optimizer",
        "objective": OBJECTIVE,
        "hard_constraints": {
            "max_single_name_weight": float(limits["max_single_name_weight"]),
            "max_bucket_weight": float(limits["max_bucket_weight"]),
            "max_daily_turnover": float(limits["max_daily_turnover"]),
            "max_one_ticket_delta": float(limits["max_one_ticket_delta"]),
            "max_cash_deploy_weight": float(limits["max_cash_deploy_weight"]),
            "earnings_blackout_days": int(limits["earnings_blackout_days"]),
            "no_shorting": True,
            "live_order_execution": False,
        },
        "estimated_turnover": round(turnover, 6),
        "allocation_count": len(allocations),
        "allocations": allocations[:12],
    }


def recommendation_provenance(
    feature: dict[str, Any],
    ticket: dict[str, Any] | None,
    llm_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provenance = {
        "symbol": feature["symbol"],
        "rank": feature["rank"],
        "model_policy_version": ENGINE_POLICY_VERSION,
        "expected_return_rank_score": feature["expected_return_rank_score"],
        "risk_adjusted_expected_return": feature.get("risk_adjusted_expected_return", feature.get("expected_return_score", 0)),
        "base_risk_adjusted_expected_return": feature.get("base_risk_adjusted_expected_return"),
        "probability_weighted_return": feature.get("probability_weighted_return"),
        "bull_return_12m": feature.get("bull_return_12m"),
        "base_return_12m": feature.get("base_return_12m"),
        "bear_return_12m": feature.get("bear_return_12m"),
        "signal_families": feature.get("signal_families", []),
        "risk_constraints": ticket.get("risk_flags", []) if ticket else [],
        "current_weight": feature.get("current_weight", 0),
        "recommended_delta_weight": ticket.get("recommended_delta_weight", 0) if ticket else 0,
        "target_weight": ticket.get("target_weight", feature.get("current_weight", 0)) if ticket else feature.get("current_weight", 0),
        "model_target_weight": ticket.get("model_target_weight", ticket.get("target_weight", feature.get("current_weight", 0))) if ticket else feature.get("current_weight", 0),
        "decision_readiness_score": feature.get("decision_readiness_score"),
        "decision_readiness_bucket": feature.get("decision_readiness_bucket"),
        "decision_evidence_blockers": feature.get("decision_evidence_blockers", []),
        "paper_tested": True,
        "status": ticket.get("status", "research_only") if ticket else "ranked",
    }
    if feature.get("llm_signal_applied"):
        provenance["llm_signal"] = {
            "llm_signal_applied": True,
            "llm_expected_return_delta": feature.get("llm_expected_return_delta"),
            "llm_expected_return_adjustment": feature.get("llm_expected_return_adjustment"),
            "llm_evidence_quality_delta": feature.get("llm_evidence_quality_delta"),
            "llm_evidence_quality_adjustment": feature.get("llm_evidence_quality_adjustment"),
            "llm_drawdown_risk_delta": feature.get("llm_drawdown_risk_delta"),
            "llm_drawdown_risk_adjustment": feature.get("llm_drawdown_risk_adjustment"),
            "llm_conviction_score": feature.get("llm_conviction_score"),
            "llm_contradiction_risk_score": feature.get("llm_contradiction_risk_score"),
            "llm_staleness_risk_score": feature.get("llm_staleness_risk_score"),
        }
    if llm_review:
        provenance["llm_review"] = {
            "thesis_quality": llm_review.get("thesis_quality"),
            "decision_usefulness_score": llm_review.get("decision_usefulness_score"),
            "review_required": bool(llm_review.get("review_required")),
            "confidence": llm_review.get("confidence"),
            "evidence_gap_count": len(llm_review.get("evidence_gaps") or []),
            "contradiction_count": len(llm_review.get("contradictions") or []),
            "stale_assumption_count": len(llm_review.get("stale_assumptions") or []),
            "risk_question_count": len(llm_review.get("risk_questions") or []),
        }
    return provenance
