from __future__ import annotations

from datetime import date
from statistics import mean
from typing import Any

from .symbols import equivalent_symbols, proxied_lookup, symbol_proxy_key
from .util import stable_id


FEATURE_MATRIX_VERSION = "2026-05-ml-feature-matrix-v2"
MODEL_POLICY_VERSION = "2026-05-bottom-up-first-v1"

RETURN_WINDOWS = ["5d", "1m", "3m", "ytd", "1y"]
HARD_RISK_EVENTS = {"financing_risk", "regulatory_risk", "crowding_warning"}


def build_feature_matrix(
    as_of: date,
    cards: list[dict[str, Any]],
    portfolio: dict[str, Any],
    manager_radar: dict[str, Any],
    macro: dict[str, Any],
    return_windows: dict[str, dict[str, Any]],
    earnings_events: list[dict[str, Any]] | None = None,
    external_signals: dict[str, Any] | None = None,
    company_underwriting: dict[str, Any] | None = None,
    sector_underwriting: dict[str, Any] | None = None,
) -> dict[str, Any]:
    portfolio_weights = weight_by_symbol(portfolio)
    bucket_weights = weight_by_bucket(portfolio)
    peers = peer_features(manager_radar)
    flows = manager_flow_features(manager_radar)
    earnings = nearest_earnings_by_symbol(earnings_events or [])
    macro_scores = macro.get("scores") or {}
    external_by_symbol = external_features_by_symbol(external_signals or {})
    global_external = (external_signals or {}).get("global") or {}
    company_by_symbol = underwriting_by_symbol(company_underwriting or {})
    sector_by_bucket = underwriting_by_bucket(sector_underwriting or {})
    rows = [
        feature_row(
            as_of,
            card,
            portfolio_weights,
            bucket_weights,
            proxied_lookup(peers, card.get("symbol"), {}),
            proxied_lookup(flows, card.get("symbol"), {}),
            macro,
            macro_scores,
            return_windows,
            proxied_lookup(earnings, card.get("symbol")),
            proxied_lookup(external_by_symbol, card.get("symbol"), {}),
            global_external,
            proxied_lookup(company_by_symbol, card.get("symbol"), {}),
            sector_by_bucket.get(str(card.get("bucket") or "unmapped"), {}),
        )
        for card in cards
        if card.get("symbol")
    ]
    return {
        "version": FEATURE_MATRIX_VERSION,
        "model_policy_version": MODEL_POLICY_VERSION,
        "as_of": as_of.isoformat(),
        "objective": "maximize_expected_3_12m_forward_return",
        "horizons": ["3m", "6m", "12m"],
        "feature_count": len(rows),
        "rows": rows,
    }


def feature_row(
    as_of: date,
    card: dict[str, Any],
    portfolio_weights: dict[str, float],
    bucket_weights: dict[str, float],
    peer: dict[str, Any],
    flow: dict[str, Any],
    macro: dict[str, Any],
    macro_scores: dict[str, Any],
    return_windows: dict[str, dict[str, Any]],
    earnings_event: dict[str, Any] | None,
    external: dict[str, Any] | None = None,
    global_external: dict[str, Any] | None = None,
    company: dict[str, Any] | None = None,
    sector: dict[str, Any] | None = None,
) -> dict[str, Any]:
    symbol = str(card.get("symbol") or "").upper()
    bucket = str(card.get("bucket") or "unmapped")
    event_types = [str(item) for item in card.get("top_event_types") or []]
    signal_families = [str(item) for item in card.get("signal_families") or []]
    source_tiers = [str(item) for item in card.get("source_tiers") or []]
    external = external or {}
    global_external = global_external or {}
    company = company or {}
    sector = sector or {}
    external_score = float(external.get("external_signal_score") or 0)
    if external.get("signal_count") and "external_feeds" not in signal_families:
        signal_families.append("external_feeds")
    returns = returns_for_symbol(return_windows, symbol)
    current_weight = float(portfolio_weights.get(symbol, 0.0))
    bucket_weight = float(bucket_weights.get(bucket, 0.0))
    manager_count = int(card.get("consensus_manager_count") or 0)
    signal_count = max(int(card.get("signal_family_count") or 0), len(signal_families))
    event_score = float(card.get("event_score") or 0)
    put_value = float(card.get("put_value") or 0)
    call_value = float(card.get("call_value") or 0)
    option_score = option_tilt_score(call_value, put_value)
    drawdown = drawdown_risk_score(
        current_weight,
        bucket_weight,
        returns,
        event_types,
        put_value,
        call_value,
        macro_scores,
        bucket,
        external,
    )
    timing = timing_score(event_score, returns, earnings_event, macro_scores, external_score)
    raw_evidence = evidence_quality_score(signal_count, manager_count, event_score, source_tiers, returns, peer, external)
    company_evidence = numeric(company.get("evidence_quality"))
    evidence = bottom_up_weighted_evidence(raw_evidence, company_evidence)
    valuation = valuation_support_score(bucket, returns, event_types)
    company_valuation = numeric(company.get("valuation_support"))
    if company_valuation is not None:
        valuation = valuation * 0.35 + company_valuation * 0.65
    company_score = numeric(company.get("company_underwriting_score"))
    sector_score = numeric(sector.get("sector_setup_score"))
    return {
        "feature_id": stable_id([as_of.isoformat(), MODEL_POLICY_VERSION, symbol]),
        "model_policy_version": MODEL_POLICY_VERSION,
        "feature_version": FEATURE_MATRIX_VERSION,
        "symbol": symbol,
        "bucket": bucket,
        "score": round(float(card.get("score") or 0), 2),
        "current_weight": round(current_weight, 6),
        "bucket_weight": round(bucket_weight, 6),
        "peer_avg_weight": round(float(peer.get("peer_avg_weight") or 0), 6),
        "tier1_peer_avg_weight": round(float(peer.get("tier1_peer_avg_weight") or 0), 6),
        "tier1_manager_count": int(peer.get("tier1_manager_count") or 0),
        "manager_count": manager_count,
        "manager_add_signal": round(float(flow.get("manager_add_signal") or 0), 2),
        "manager_reduction_signal": round(float(flow.get("manager_reduction_signal") or 0), 2),
        "option_tilt_score": round(option_score, 2),
        "signal_family_count": signal_count,
        "signal_families": signal_families,
        "event_score": round(event_score, 2),
        "event_types": event_types,
        "source_tiers": source_tiers,
        "source_quality_score": round(source_quality_score(source_tiers), 2),
        "earnings_days_until": earnings_event.get("days_until") if earnings_event else None,
        "price_return_5d": returns.get("5d"),
        "price_return_1m": returns.get("1m"),
        "price_return_3m": returns.get("3m"),
        "price_return_ytd": returns.get("ytd"),
        "price_return_1y": returns.get("1y"),
        "valuation_support": round(valuation, 2),
        "macro_regime": macro.get("regime", ""),
        "macro_ai_momentum": numeric(macro_scores.get("ai_momentum")),
        "macro_risk_momentum": numeric(macro_scores.get("risk_momentum")),
        "macro_rates_move": numeric(macro_scores.get("rates_move")),
        "macro_yield_curve_10y2y": numeric(macro_scores.get("yield_curve_10y2y")),
        "macro_credit_stress": numeric(macro_scores.get("credit_stress_score")),
        "macro_liquidity_pressure": numeric(macro_scores.get("liquidity_pressure_score")),
        "macro_yield_curve_inversion": numeric(macro_scores.get("yield_curve_inversion_score")),
        "macro_energy_pressure": numeric(macro_scores.get("energy_pressure_score")),
        "external_signal_score": round(external_score, 2),
        "alpha_news_sentiment": numeric(external.get("alpha_news_sentiment")),
        "sec_fundamental_score": numeric(external.get("sec_fundamental_score")),
        "sec_form4_activity_score": numeric(external.get("sec_form4_activity_score")),
        "gdelt_event_score": numeric(external.get("gdelt_event_score")),
        "short_interest_risk_score": numeric(external.get("short_interest_risk_score")),
        "external_source_count": int(external.get("source_count") or 0),
        "external_signal_count": int(external.get("signal_count") or 0),
        "company_underwriting_score": round(company_score if company_score is not None else 45.0, 2),
        "company_evidence_quality": round(company_evidence if company_evidence is not None else 0.0, 2),
        "company_source_quality": round(float(company.get("source_quality") or 0), 2),
        "company_data_quality": round(float(company.get("data_quality") or 0), 2),
        "company_growth_durability": round(float(company.get("growth_durability_score") or 0), 2),
        "company_margin_trajectory": round(float(company.get("margin_trajectory_score") or 0), 2),
        "company_cash_generation": round(float(company.get("cash_generation_score") or 0), 2),
        "company_balance_sheet_financing": round(float(company.get("balance_sheet_financing_score") or 0), 2),
        "company_capex_roic": round(float(company.get("capex_roic_score") or 0), 2),
        "company_revisions_guidance": round(float(company.get("revisions_guidance_score") or 0), 2),
        "company_customer_concentration_risk": round(float(company.get("customer_concentration_risk") or 0), 2),
        "company_dilution_debt_risk": round(float(company.get("dilution_debt_risk") or 0), 2),
        "company_add_eligible": bool(company.get("add_eligible", False)),
        "company_trim_signal": bool(company.get("trim_signal", False)),
        "company_review_required": bool(company.get("review_required", False)),
        "company_review_status": company.get("review_status", "missing_company_underwriting"),
        "company_reason": company.get("company_reason", ""),
        "company_risk_falsifier": company.get("risk_falsifier", ""),
        "sector_setup_score": round(sector_score if sector_score is not None else 50.0, 2),
        "sector_target_weight_modifier": round(float(sector.get("target_weight_modifier") or 1.0), 3),
        "sector_headwind": bool(sector.get("sector_headwind", False)),
        "sector_tailwind": bool(sector.get("sector_tailwind", False)),
        "sector_risk_flags": sector.get("risk_flags", []),
        "sector_setup": sector.get("sector_setup", ""),
        "global_event_score": numeric(global_external.get("global_signal_score")),
        "global_gdelt_event_score": numeric(global_external.get("gdelt_global_event_score")),
        "global_eia_power_pressure": numeric(global_external.get("eia_power_pressure_score")),
        "global_cftc_positioning": numeric(global_external.get("cftc_positioning_score")),
        "concentration_risk": round(min(100.0, current_weight * 260 + bucket_weight * 70), 2),
        "drawdown_risk": round(drawdown, 2),
        "timing_score": round(timing, 2),
        "evidence_quality": round(evidence, 2),
        "data_quality": round(max(data_quality_score(returns, signal_count, source_tiers), float(company.get("data_quality") or 0)), 2),
    }


def weight_by_symbol(portfolio: dict[str, Any]) -> dict[str, float]:
    weights: dict[str, float] = {}
    for row in portfolio.get("by_symbol", []):
        symbol = str(row.get("symbol") or "").upper()
        if not symbol:
            continue
        weight = float(row.get("weight") or 0)
        for candidate in equivalent_symbols(symbol):
            weights[candidate] = weights.get(candidate, 0.0) + weight
    return weights


def weight_by_bucket(portfolio: dict[str, Any]) -> dict[str, float]:
    return {
        str(row.get("bucket") or "unmapped"): float(row.get("weight") or 0)
        for row in portfolio.get("by_bucket", [])
        if row.get("bucket")
    }


def peer_features(manager_radar: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_symbol: dict[str, dict[str, list[float] | set[str]]] = {}
    for manager in manager_radar.get("focus_managers", []):
        if manager.get("status") != "ok":
            continue
        tier = str(manager.get("manager_tier") or "tier_2")
        manager_name = str(manager.get("manager_name") or manager.get("manager_key") or "")
        manager_group_weights: dict[str, float] = {}
        for position in manager_public_positions(manager):
            symbol = str(position.get("symbol") or "").upper()
            weight = float(position.get("fund_weight") or 0)
            if not symbol or weight <= 0:
                continue
            key = symbol_proxy_key(symbol)
            manager_group_weights[key] = manager_group_weights.get(key, 0.0) + weight
        for symbol, weight in manager_group_weights.items():
            row = by_symbol.setdefault(
                symbol,
                {
                    "weights": [],
                    "tier1_weights": [],
                    "managers": set(),
                    "tier1_managers": set(),
                },
            )
            row["weights"].append(weight)  # type: ignore[index, union-attr]
            row["managers"].add(manager_name)  # type: ignore[index, union-attr]
            if tier == "tier_1":
                row["tier1_weights"].append(weight)  # type: ignore[index, union-attr]
                row["tier1_managers"].add(manager_name)  # type: ignore[index, union-attr]
    summary: dict[str, dict[str, Any]] = {}
    for symbol, row in by_symbol.items():
        weights = list(row["weights"])  # type: ignore[arg-type]
        tier1_weights = list(row["tier1_weights"])  # type: ignore[arg-type]
        group_summary = {
            "peer_avg_weight": mean(weights) if weights else 0.0,
            "tier1_peer_avg_weight": mean(tier1_weights) if tier1_weights else 0.0,
            "manager_count": len(row["managers"]),  # type: ignore[arg-type]
            "tier1_manager_count": len(row["tier1_managers"]),  # type: ignore[arg-type]
        }
        for candidate in equivalent_symbols(symbol):
            summary[candidate] = group_summary
    return summary


def manager_public_positions(manager: dict[str, Any]) -> list[dict[str, Any]]:
    return manager.get("positions") or manager.get("top_positions") or []


def manager_flow_features(manager_radar: dict[str, Any]) -> dict[str, dict[str, float]]:
    flows: dict[str, dict[str, float]] = {}
    for key, direction in [("top_adds", "manager_add_signal"), ("top_reductions", "manager_reduction_signal")]:
        for row in manager_radar.get(key, []):
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            signal = min(15.0, abs(float(row.get("delta_value") or 0)) / 100_000_000)
            for candidate in equivalent_symbols(symbol):
                flows.setdefault(candidate, {})[direction] = max(signal, flows.get(candidate, {}).get(direction, 0.0))
    return flows


def nearest_earnings_by_symbol(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    nearest: dict[str, dict[str, Any]] = {}
    for event in events:
        symbol = str(event.get("symbol") or "").upper()
        days = event.get("days_until")
        if not symbol or days is None:
            continue
        current = nearest.get(symbol)
        if current is None or abs(int(days)) < abs(int(current.get("days_until") or 9999)):
            for candidate in equivalent_symbols(symbol):
                nearest[candidate] = event
    return nearest


def external_features_by_symbol(external_signals: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = external_signals.get("by_symbol") or {}
    normalized: dict[str, dict[str, Any]] = {}
    if isinstance(rows, dict):
        for symbol, row in rows.items():
            for candidate in equivalent_symbols(str(symbol).upper()):
                normalized[candidate] = dict(row)
    return normalized


def underwriting_by_symbol(company_underwriting: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = company_underwriting.get("items") or []
    normalized: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        if not symbol:
            continue
        for candidate in equivalent_symbols(symbol):
            normalized[candidate] = row
    return normalized


def underwriting_by_bucket(sector_underwriting: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("bucket") or "unmapped"): row
        for row in sector_underwriting.get("items") or []
        if row.get("bucket")
    }


def returns_for_symbol(return_windows: dict[str, dict[str, Any]], symbol: str) -> dict[str, float | None]:
    data = proxied_lookup(return_windows, symbol, {}) or {}
    return {key: numeric(data.get(key)) for key in RETURN_WINDOWS}


def numeric(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def source_quality_score(source_tiers: list[str]) -> float:
    if not source_tiers:
        return 15.0
    score = 20.0
    if "primary" in source_tiers:
        score += 35.0
    if "specialist" in source_tiers:
        score += 25.0
    if "market_news" in source_tiers:
        score += 15.0
    return min(score, 100.0)


def evidence_quality_score(
    signal_count: int,
    manager_count: int,
    event_score: float,
    source_tiers: list[str],
    returns: dict[str, float | None],
    peer: dict[str, Any],
    external: dict[str, Any] | None = None,
) -> float:
    external = external or {}
    external_count = int(external.get("signal_count") or 0)
    external_score = max(0.0, float(external.get("external_signal_score") or 0))
    score = (
        signal_count * 16.0
        + min(manager_count, 6) * 6.0
        + min(event_score, 16.0) * 1.6
        + source_quality_score(source_tiers) * 0.18
        + (8.0 if returns.get("3m") is not None else 0.0)
        + min(float(peer.get("tier1_manager_count") or 0) * 5.0, 15.0)
        + min(external_count * 4.0, 12.0)
        + min(external_score * 0.35, 8.0)
    )
    return min(100.0, score)


def bottom_up_weighted_evidence(existing: float, company_evidence: float | None) -> float:
    if company_evidence is None:
        return existing
    return min(100.0, existing * 0.35 + company_evidence * 0.65)


def data_quality_score(returns: dict[str, float | None], signal_count: int, source_tiers: list[str]) -> float:
    covered_returns = sum(1 for value in returns.values() if value is not None)
    return min(100.0, 35.0 + covered_returns * 8.0 + signal_count * 7.0 + source_quality_score(source_tiers) * 0.1)


def timing_score(
    event_score: float,
    returns: dict[str, float | None],
    earnings_event: dict[str, Any] | None,
    macro_scores: dict[str, Any],
    external_signal_score: float = 0.0,
) -> float:
    score = 45.0 + min(event_score * 2.0, 24.0)
    one_month = returns.get("1m") or 0.0
    three_month = returns.get("3m") or 0.0
    if -12.0 <= one_month <= 18.0:
        score += 8.0
    if three_month > 35.0:
        score -= 9.0
    if one_month < -18.0:
        score -= 7.0
    if earnings_event and earnings_event.get("days_until") is not None:
        days = abs(int(earnings_event["days_until"]))
        if days <= 2:
            score -= 20.0
        elif days <= 7:
            score -= 8.0
        elif days <= 30:
            score += 5.0
    ai_momentum = numeric(macro_scores.get("ai_momentum")) or 0.0
    risk_momentum = numeric(macro_scores.get("risk_momentum")) or 0.0
    score += max(-8.0, min(8.0, ai_momentum))
    score += max(-6.0, min(6.0, risk_momentum * 0.5))
    credit_stress = max(0.0, numeric(macro_scores.get("credit_stress_score")) or 0.0)
    liquidity_pressure = max(0.0, numeric(macro_scores.get("liquidity_pressure_score")) or 0.0)
    curve_inversion = max(0.0, numeric(macro_scores.get("yield_curve_inversion_score")) or 0.0)
    score -= min(12.0, credit_stress * 0.6)
    score -= min(8.0, liquidity_pressure * 0.5)
    score -= min(6.0, curve_inversion * 0.35)
    score += max(-5.0, min(5.0, external_signal_score * 0.18))
    return max(0.0, min(100.0, score))


def drawdown_risk_score(
    current_weight: float,
    bucket_weight: float,
    returns: dict[str, float | None],
    event_types: list[str],
    put_value: float,
    call_value: float,
    macro_scores: dict[str, Any] | None = None,
    bucket: str = "",
    external: dict[str, Any] | None = None,
) -> float:
    risk = 22.0 + current_weight * 220.0 + bucket_weight * 55.0
    one_month = returns.get("1m") or 0.0
    three_month = returns.get("3m") or 0.0
    if three_month > 45.0:
        risk += 16.0
    if one_month < -18.0:
        risk += 12.0
    risk += len(HARD_RISK_EVENTS & set(event_types)) * 12.0
    if put_value > max(call_value * 1.25, 50_000_000):
        risk += 8.0
    macro_scores = macro_scores or {}
    credit_stress = max(0.0, numeric(macro_scores.get("credit_stress_score")) or 0.0)
    liquidity_pressure = max(0.0, numeric(macro_scores.get("liquidity_pressure_score")) or 0.0)
    curve_inversion = max(0.0, numeric(macro_scores.get("yield_curve_inversion_score")) or 0.0)
    energy_pressure = max(0.0, numeric(macro_scores.get("energy_pressure_score")) or 0.0)
    if bucket in {"neocloud_datacenters", "power_grid_gas_nuclear", "semis_networking_hbm"}:
        risk += min(14.0, credit_stress * 0.7 + liquidity_pressure * 0.5 + curve_inversion * 0.35)
    if bucket == "power_grid_gas_nuclear":
        risk += min(6.0, energy_pressure * 0.35)
    short_interest_risk = abs(min(0.0, float((external or {}).get("short_interest_risk_score") or 0.0)))
    risk += min(8.0, short_interest_risk * 0.35)
    return max(0.0, min(100.0, risk))


def option_tilt_score(call_value: float, put_value: float) -> float:
    if call_value <= 0 and put_value <= 0:
        return 0.0
    if call_value > put_value:
        return min(10.0, call_value / max(call_value + put_value, 1) * 10.0)
    return -min(10.0, put_value / max(call_value + put_value, 1) * 10.0)


def valuation_support_score(bucket: str, returns: dict[str, float | None], event_types: list[str]) -> float:
    score = 50.0
    one_year = returns.get("1y")
    three_month = returns.get("3m")
    if one_year is not None and one_year > 120.0:
        score -= 18.0
    if three_month is not None and three_month > 45.0:
        score -= 12.0
    if three_month is not None and -25.0 < three_month < 15.0:
        score += 8.0
    if bucket in {"neocloud_datacenters", "power_grid_gas_nuclear"}:
        score -= 5.0
    if HARD_RISK_EVENTS & set(event_types):
        score -= 12.0
    return max(0.0, min(100.0, score))
