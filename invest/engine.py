from __future__ import annotations

from datetime import date
from statistics import mean
from typing import Any

from .risk import normalize_limits
from .symbols import equivalent_symbols, proxied_lookup, proxy_index
from .util import stable_id


ENGINE_POLICY_VERSION = "2026-05-bottom-up-first-v1"
ENGINE_MODE = "approval_plus_paper"
OBJECTIVE = "maximize_expected_3_12m_forward_return"


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
) -> dict[str, Any]:
    features = build_engine_features(as_of, cards, portfolio, portfolio_benchmark, feature_matrix, research_book)
    learning = build_learning_state(outcome_history or [])
    ranked = rank_candidates(features, learning)
    optimizer = build_allocator_output(ranked, portfolio, approval_tickets, risk_limits)
    ticket_by_symbol = proxy_index(approval_tickets)
    provenance = [recommendation_provenance(row, ticket_by_symbol.get(row["symbol"])) for row in ranked[:20]]
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
        for candidate in equivalent_symbols(row.get("symbol")):
            current_weights[candidate] = current_weights.get(candidate, 0.0) + float(row.get("weight") or 0)
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
        rows.append(
            {
                "feature_id": feature.get("feature_id") or stable_id([as_of.isoformat(), ENGINE_POLICY_VERSION, symbol]),
                "symbol": symbol,
                "bucket": feature.get("bucket", "unmapped"),
                "score": round(float(feature.get("score") or 0), 2),
                "expected_return_score": round(expected, 2),
                "risk_adjusted_expected_return": round(expected, 2),
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
                    "external_signals": feature.get("external_signal_score"),
                },
            }
        )
    return rows


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
        if row.get("forward_return_pct") is not None and row.get("expected_return_score") is not None
    ]
    short_horizon = [row for row in completed if row.get("horizon") == "5d"]
    completed = [row for row in completed if row.get("horizon") != "5d"]
    if len(completed) < 20:
        return {
            "status": "baseline_fallback",
            "outcome_count": len(completed),
            "short_horizon_outcome_count": len(short_horizon),
            "minimum_required": 20,
            "weight_adjustments": {},
            "message": "Insufficient completed 1-12 month outcomes; 5-day labels are tracked for fast diagnostics but do not adjust ranking weights.",
        }
    by_family: dict[str, list[float]] = {}
    for row in completed:
        forward = float(row.get("forward_return_pct") or 0)
        for family in row.get("signal_families") or []:
            by_family.setdefault(str(family), []).append(forward)
    averages = {family: mean(values) for family, values in by_family.items() if values}
    cross_avg = mean(averages.values()) if averages else 0.0
    adjustments = {
        family: round(max(-8.0, min(8.0, value - cross_avg)) / 8.0, 3)
        for family, value in averages.items()
    }
    return {
        "status": "history_adjusted",
        "outcome_count": len(completed),
        "short_horizon_outcome_count": len(short_horizon),
        "minimum_required": 20,
        "weight_adjustments": adjustments,
        "message": "Signal-family weights adjusted from completed recommendation outcomes.",
    }


def rank_candidates(features: list[dict[str, Any]], learning: dict[str, Any]) -> list[dict[str, Any]]:
    adjustments = learning.get("weight_adjustments") or {}
    ranked = []
    for row in features:
        learning_delta = sum(float(adjustments.get(family, 0)) for family in row.get("signal_families", [])) * 4.0
        expected = float(row.get("risk_adjusted_expected_return", row.get("expected_return_score") or 0) or 0) + learning_delta
        item = dict(row)
        item["learning_adjustment"] = round(learning_delta, 2)
        item["expected_return_rank_score"] = round(expected, 2)
        ranked.append(item)
    ranked.sort(key=lambda item: item["expected_return_rank_score"], reverse=True)
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
    return ranked


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


def recommendation_provenance(feature: dict[str, Any], ticket: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "symbol": feature["symbol"],
        "rank": feature["rank"],
        "model_policy_version": ENGINE_POLICY_VERSION,
        "expected_return_rank_score": feature["expected_return_rank_score"],
        "risk_adjusted_expected_return": feature.get("risk_adjusted_expected_return", feature.get("expected_return_score", 0)),
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
        "paper_tested": True,
        "status": ticket.get("status", "research_only") if ticket else "ranked",
    }
