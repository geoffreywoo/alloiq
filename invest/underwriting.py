from __future__ import annotations

from datetime import date
from statistics import mean
from typing import Any

from .symbols import equivalent_symbols, proxied_lookup, proxy_index
from .util import stable_id


COMPANY_UNDERWRITING_VERSION = "2026-05-company-underwriting-v1"
SECTOR_UNDERWRITING_VERSION = "2026-05-sector-underwriting-v1"

HARD_RISK_EVENTS = {"financing_risk", "regulatory_risk", "crowding_warning"}

SECTOR_TEMPLATES: dict[str, dict[str, Any]] = {
    "frontier_ai_platforms": {
        "label": "Frontier AI Platforms",
        "setup": "AI usage must convert into durable revenue, retention, pricing power, and operating leverage.",
        "kpis": ["AI revenue attach", "cloud growth", "capex ROI", "product retention", "operating leverage"],
        "base_quality": 63.0,
        "capital_intensity": 52.0,
    },
    "semis_networking_hbm": {
        "label": "Semiconductors, HBM, Networking",
        "setup": "Compute scarcity must show up in backlog, gross margin, foundry allocation, HBM supply, and networking attach.",
        "kpis": ["backlog", "gross margin", "inventory", "lead times", "hyperscaler capex", "export controls"],
        "base_quality": 66.0,
        "capital_intensity": 58.0,
    },
    "neocloud_datacenters": {
        "label": "Neoclouds and Data Centers",
        "setup": "GPU capacity is valuable only if utilization, customer quality, financing terms, and power access hold.",
        "kpis": ["utilization", "contracted demand", "GPU depreciation", "debt cost", "customer quality", "power access"],
        "base_quality": 48.0,
        "capital_intensity": 82.0,
    },
    "power_grid_gas_nuclear": {
        "label": "Power, Grid, Gas, Nuclear",
        "setup": "AI data-center demand needs funded power capacity, grid equipment, dispatchability, and contract duration.",
        "kpis": ["load growth", "power prices", "interconnection", "PPAs", "gas supply", "nuclear availability"],
        "base_quality": 55.0,
        "capital_intensity": 74.0,
    },
    "ai_software_winners": {
        "label": "AI Software Winners",
        "setup": "AI products must translate into ARR/RPO, retention, pricing, workflow expansion, and sales efficiency.",
        "kpis": ["ARR", "RPO", "NRR", "churn", "sales efficiency", "AI attach"],
        "base_quality": 57.0,
        "capital_intensity": 35.0,
    },
    "ai_enabled_financials": {
        "label": "AI-Enabled Financials and Market Structure",
        "setup": "AI must improve distribution, underwriting, support, fraud, and market-structure economics.",
        "kpis": ["user growth", "take rate", "credit quality", "fraud loss", "support efficiency", "product attach"],
        "base_quality": 54.0,
        "capital_intensity": 42.0,
    },
    "disrupted_incumbents": {
        "label": "Disrupted Incumbents",
        "setup": "Legacy revenue must prove resilience as AI compresses distribution, services labor, and workflow switching costs.",
        "kpis": ["organic growth", "renewals", "pricing", "margin defense", "AI cannibalization", "competitive losses"],
        "base_quality": 38.0,
        "capital_intensity": 38.0,
    },
    "unmapped": {
        "label": "Unmapped",
        "setup": "Bucket ownership is unclear, so the model requires extra bottom-up evidence before adding size.",
        "kpis": ["revenue growth", "margins", "cash flow", "balance sheet", "valuation", "catalysts"],
        "base_quality": 45.0,
        "capital_intensity": 50.0,
    },
}


def build_underwriting_layers(
    as_of: date,
    cards: list[dict[str, Any]],
    portfolio: dict[str, Any],
    manager_radar: dict[str, Any],
    macro: dict[str, Any],
    return_windows: dict[str, dict[str, Any]],
    earnings_events: list[dict[str, Any]] | None = None,
    external_signals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sector = build_sector_underwriting(as_of, cards, macro, return_windows, external_signals)
    company = build_company_underwriting(
        as_of,
        cards,
        portfolio,
        manager_radar,
        macro,
        return_windows,
        earnings_events or [],
        external_signals or {},
        sector,
    )
    return {
        "company_underwriting": company,
        "sector_underwriting": sector,
    }


def build_sector_underwriting(
    as_of: date,
    cards: list[dict[str, Any]],
    macro: dict[str, Any],
    return_windows: dict[str, dict[str, Any]],
    external_signals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    global_external = (external_signals or {}).get("global") or {}
    macro_scores = macro.get("scores") or {}
    buckets = sorted({str(card.get("bucket") or "unmapped") for card in cards} | set(SECTOR_TEMPLATES))
    items = [
        sector_item(as_of, bucket, [card for card in cards if str(card.get("bucket") or "unmapped") == bucket], macro_scores, return_windows, global_external)
        for bucket in buckets
    ]
    items.sort(key=lambda row: row["sector_setup_score"], reverse=True)
    for rank, row in enumerate(items, start=1):
        row["rank"] = rank
    return {
        "version": SECTOR_UNDERWRITING_VERSION,
        "as_of": as_of.isoformat(),
        "objective": "rank_ai_max_sector_context_before_13f_and_macro_confirmation",
        "item_count": len(items),
        "items": items,
    }


def sector_item(
    as_of: date,
    bucket: str,
    bucket_cards: list[dict[str, Any]],
    macro_scores: dict[str, Any],
    return_windows: dict[str, dict[str, Any]],
    global_external: dict[str, Any],
) -> dict[str, Any]:
    template = SECTOR_TEMPLATES.get(bucket, SECTOR_TEMPLATES["unmapped"])
    returns_3m = [
        value
        for card in bucket_cards
        if (value := return_for_symbol(return_windows, str(card.get("symbol") or ""), "3m")) is not None
    ]
    returns_1y = [
        value
        for card in bucket_cards
        if (value := return_for_symbol(return_windows, str(card.get("symbol") or ""), "1y")) is not None
    ]
    event_score = mean([float(card.get("event_score") or 0) for card in bucket_cards]) if bucket_cards else 0.0
    hard_risk_count = sum(1 for card in bucket_cards if HARD_RISK_EVENTS & set(card.get("top_event_types") or []))
    credit = max(0.0, numeric(macro_scores.get("credit_stress_score")) or 0.0)
    liquidity = max(0.0, numeric(macro_scores.get("liquidity_pressure_score")) or 0.0)
    curve = max(0.0, numeric(macro_scores.get("yield_curve_inversion_score")) or 0.0)
    energy = max(0.0, numeric(macro_scores.get("energy_pressure_score")) or 0.0)
    power_pressure = max(0.0, numeric(global_external.get("eia_power_pressure_score")) or 0.0)
    cftc = numeric(global_external.get("cftc_positioning_score")) or 0.0
    score = float(template["base_quality"]) + min(15.0, event_score * 0.85)
    score += max(-8.0, min(8.0, cftc * 0.25))
    if returns_3m and mean(returns_3m) > 45:
        score -= 7.0
    if returns_1y and mean(returns_1y) > 120:
        score -= 8.0
    if bucket in {"neocloud_datacenters", "power_grid_gas_nuclear"}:
        score -= min(15.0, credit * 0.7 + liquidity * 0.5 + curve * 0.35)
    if bucket == "power_grid_gas_nuclear":
        score += min(8.0, power_pressure * 0.35)
        score -= min(5.0, energy * 0.22)
    if bucket == "semis_networking_hbm":
        score -= min(6.0, credit * 0.25)
    score -= min(14.0, hard_risk_count * 2.5)
    score = clamp(score)
    headwind = score < 45.0
    tailwind = score >= 62.0
    risk_flags = []
    if headwind:
        risk_flags.append("sector_headwind")
    if hard_risk_count:
        risk_flags.append("sector_hard_risk_events")
    if returns_3m and mean(returns_3m) > 45:
        risk_flags.append("sector_overheated_price_action")
    return {
        "underwriting_id": stable_id([as_of.isoformat(), SECTOR_UNDERWRITING_VERSION, bucket]),
        "version": SECTOR_UNDERWRITING_VERSION,
        "as_of": as_of.isoformat(),
        "bucket": bucket,
        "label": template["label"],
        "rank": 0,
        "sector_setup_score": round(score, 2),
        "sector_tailwind": tailwind,
        "sector_headwind": headwind,
        "target_weight_modifier": round(1.12 if tailwind else 0.78 if headwind else 1.0, 3),
        "sector_setup": template["setup"],
        "core_kpis": template["kpis"],
        "capital_intensity_score": round(float(template["capital_intensity"]), 2),
        "average_3m_return": round(mean(returns_3m), 2) if returns_3m else None,
        "average_1y_return": round(mean(returns_1y), 2) if returns_1y else None,
        "macro_pressure": round(min(100.0, credit * 0.7 + liquidity * 0.5 + curve * 0.35 + energy * 0.2), 2),
        "power_pressure": round(power_pressure, 2),
        "hard_risk_count": hard_risk_count,
        "risk_flags": risk_flags,
    }


def build_company_underwriting(
    as_of: date,
    cards: list[dict[str, Any]],
    portfolio: dict[str, Any],
    manager_radar: dict[str, Any],
    macro: dict[str, Any],
    return_windows: dict[str, dict[str, Any]],
    earnings_events: list[dict[str, Any]],
    external_signals: dict[str, Any],
    sector_underwriting: dict[str, Any],
) -> dict[str, Any]:
    del manager_radar, macro
    external_by_symbol = external_features_by_symbol(external_signals)
    earnings_by_symbol = nearest_earnings_by_symbol(earnings_events)
    portfolio_weights = portfolio_weight_by_symbol(portfolio)
    sector_by_bucket = {str(row.get("bucket") or "unmapped"): row for row in sector_underwriting.get("items", [])}
    items = [
        company_item(
            as_of,
            card,
            portfolio_weights,
            return_windows,
            proxied_lookup(external_by_symbol, card.get("symbol"), {}),
            proxied_lookup(earnings_by_symbol, card.get("symbol")),
            sector_by_bucket.get(str(card.get("bucket") or "unmapped"), {}),
        )
        for card in cards
        if card.get("symbol")
    ]
    items.sort(key=lambda row: row["company_underwriting_score"], reverse=True)
    for rank, row in enumerate(items, start=1):
        row["rank"] = rank
    return {
        "version": COMPANY_UNDERWRITING_VERSION,
        "as_of": as_of.isoformat(),
        "objective": "bottom_up_company_underwriting_before_sector_13f_and_macro",
        "item_count": len(items),
        "items": items,
        "review_count": sum(1 for item in items if item.get("review_required")),
    }


def company_item(
    as_of: date,
    card: dict[str, Any],
    portfolio_weights: dict[str, float],
    return_windows: dict[str, dict[str, Any]],
    external: dict[str, Any],
    earnings_event: dict[str, Any] | None,
    sector: dict[str, Any],
) -> dict[str, Any]:
    symbol = str(card.get("symbol") or "").upper()
    bucket = str(card.get("bucket") or "unmapped")
    template = SECTOR_TEMPLATES.get(bucket, SECTOR_TEMPLATES["unmapped"])
    event_types = [str(item) for item in card.get("top_event_types") or []]
    source_tiers = [str(item) for item in card.get("source_tiers") or []]
    returns = {key: return_for_symbol(return_windows, symbol, key) for key in ["5d", "1m", "3m", "ytd", "1y"]}
    event_score = float(card.get("event_score") or 0)
    source_quality = company_source_quality(source_tiers, external)
    sec_fundamental = numeric(external.get("sec_fundamental_score")) or 0.0
    news_sentiment = numeric(external.get("alpha_news_sentiment")) or 0.0
    gdelt = numeric(external.get("gdelt_event_score")) or 0.0
    short_risk = abs(min(0.0, numeric(external.get("short_interest_risk_score")) or 0.0))
    financing_risk = 1.0 if "financing_risk" in event_types else 0.0
    regulatory_risk = 1.0 if "regulatory_risk" in event_types else 0.0
    crowding_risk = 1.0 if "crowding_warning" in event_types else 0.0
    growth = clamp(float(template["base_quality"]) + event_score * 1.25 + sec_fundamental * 0.55 + news_sentiment * 0.22 + gdelt * 0.15)
    margins = clamp(52.0 + sec_fundamental * 0.42 + news_sentiment * 0.18 - financing_risk * 5.0 - regulatory_risk * 4.0)
    cash_generation = clamp(float(template["base_quality"]) - float(template["capital_intensity"]) * 0.22 + sec_fundamental * 0.35 - financing_risk * 8.0)
    balance_sheet = clamp(58.0 - float(template["capital_intensity"]) * 0.18 - financing_risk * 18.0 - short_risk * 0.35 + sec_fundamental * 0.25)
    capex_roic = clamp(58.0 - float(template["capital_intensity"]) * 0.12 + event_score * 0.65 + sec_fundamental * 0.25)
    revisions = clamp(50.0 + event_score * 1.4 + news_sentiment * 0.35 + gdelt * 0.12)
    valuation = valuation_support(bucket, returns, event_types)
    customer_risk = customer_concentration_risk(bucket, event_types, source_quality)
    risk_penalty = financing_risk * 10.0 + regulatory_risk * 9.0 + crowding_risk * 7.0 + short_risk * 0.22 + max(0.0, customer_risk - 55.0) * 0.20
    sector_modifier = float(sector.get("target_weight_modifier") or 1.0)
    score = (
        growth * 0.20
        + margins * 0.15
        + cash_generation * 0.15
        + balance_sheet * 0.12
        + capex_roic * 0.12
        + revisions * 0.12
        + valuation * 0.14
    )
    score = clamp(score * min(1.08, max(0.86, sector_modifier)) - risk_penalty)
    evidence = clamp(source_quality * 0.55 + min(24.0, event_score * 2.2) + min(16.0, int(external.get("signal_count") or 0) * 4.0) + (8.0 if returns.get("3m") is not None else 0.0))
    data_quality = clamp(36.0 + sum(1 for value in returns.values() if value is not None) * 7.0 + min(18.0, int(external.get("source_count") or 0) * 5.0) + source_quality * 0.16)
    deterioration = score < 40.0 or (financing_risk and balance_sheet < 45.0) or (regulatory_risk and revisions < 48.0)
    add_eligible = score >= 58.0 and evidence >= 50.0 and data_quality >= 45.0 and not deterioration
    review_required = data_quality < 55.0 or evidence < 52.0 or (score >= 62.0 and source_quality < 55.0)
    if deterioration:
        review_status = "deteriorating"
        review_reason = "Company evidence is deteriorating enough to underwrite a trim before relying on 13F confirmation."
    elif review_required:
        review_status = "review_required"
        review_reason = "Bottom-up score is not backed by enough source depth for an automatic high-conviction add."
    else:
        review_status = "ready"
        review_reason = "Company evidence clears the deterministic bottom-up evidence floor."
    return {
        "underwriting_id": stable_id([as_of.isoformat(), COMPANY_UNDERWRITING_VERSION, symbol]),
        "version": COMPANY_UNDERWRITING_VERSION,
        "as_of": as_of.isoformat(),
        "symbol": symbol,
        "bucket": bucket,
        "rank": 0,
        "current_weight": round(float(portfolio_weights.get(symbol, 0.0)), 6),
        "thesis_summary": company_thesis(symbol, bucket),
        "core_kpis": template["kpis"],
        "bull_case": bull_case(symbol, bucket),
        "base_case": base_case(symbol, bucket),
        "bear_case": bear_case(symbol, bucket),
        "growth_durability_score": round(growth, 2),
        "margin_trajectory_score": round(margins, 2),
        "cash_generation_score": round(cash_generation, 2),
        "balance_sheet_financing_score": round(balance_sheet, 2),
        "customer_concentration_risk": round(customer_risk, 2),
        "capex_roic_score": round(capex_roic, 2),
        "revisions_guidance_score": round(revisions, 2),
        "valuation_support": round(valuation, 2),
        "dilution_debt_risk": round(clamp(financing_risk * 75.0 + max(0.0, 55.0 - balance_sheet) + short_risk * 0.30), 2),
        "company_underwriting_score": round(score, 2),
        "evidence_quality": round(evidence, 2),
        "source_quality": round(source_quality, 2),
        "data_quality": round(data_quality, 2),
        "catalyst_clock": company_catalyst_clock(earnings_event, event_types),
        "falsifiers": falsifiers(bucket),
        "risk_falsifier": primary_falsifier(bucket),
        "company_reason": company_reason(symbol, score, growth, margins, balance_sheet, valuation, event_types),
        "source_labels": source_labels(source_tiers, external),
        "review_required": review_required,
        "review_status": review_status,
        "review_reason": review_reason,
        "bottom_up_evidence_floor_pass": add_eligible,
        "add_eligible": add_eligible,
        "trim_signal": deterioration,
        "sector_setup_score": sector.get("sector_setup_score", 50.0),
        "sector_headwind": bool(sector.get("sector_headwind", False)),
        "sector_tailwind": bool(sector.get("sector_tailwind", False)),
    }


def company_source_quality(source_tiers: list[str], external: dict[str, Any]) -> float:
    score = 20.0
    if "primary" in source_tiers:
        score += 28.0
    if "specialist" in source_tiers:
        score += 22.0
    if "market_news" in source_tiers:
        score += 12.0
    score += min(18.0, int(external.get("source_count") or 0) * 5.0)
    score += min(10.0, int(external.get("signal_count") or 0) * 2.0)
    if external.get("sec_fundamental_score") is not None:
        score += 10.0
    return clamp(score)


def valuation_support(bucket: str, returns: dict[str, float | None], event_types: list[str]) -> float:
    score = 52.0
    one_year = returns.get("1y")
    three_month = returns.get("3m")
    one_month = returns.get("1m")
    if one_year is not None and one_year > 120.0:
        score -= 18.0
    if three_month is not None and three_month > 45.0:
        score -= 12.0
    if three_month is not None and -25.0 < three_month < 18.0:
        score += 9.0
    if one_month is not None and one_month < -18.0:
        score -= 5.0
    if bucket in {"neocloud_datacenters", "power_grid_gas_nuclear"}:
        score -= 4.0
    if HARD_RISK_EVENTS & set(event_types):
        score -= 10.0
    return clamp(score)


def customer_concentration_risk(bucket: str, event_types: list[str], source_quality: float) -> float:
    risk = 34.0
    if bucket == "neocloud_datacenters":
        risk += 24.0
    if bucket == "semis_networking_hbm":
        risk += 12.0
    if "contract_win" in event_types:
        risk += 8.0
    if "financing_risk" in event_types:
        risk += 8.0
    if source_quality >= 65.0:
        risk -= 6.0
    return clamp(risk)


def company_catalyst_clock(earnings_event: dict[str, Any] | None, event_types: list[str]) -> str:
    if earnings_event and earnings_event.get("days_until") is not None:
        days = int(earnings_event["days_until"])
        if abs(days) <= 2:
            return "Earnings blackout now; bottom-up add requires the event to clear."
        if abs(days) <= 7:
            return "Near earnings; cap new add size until guidance/KPIs update."
        if days > 0:
            return f"{days} days to the next earnings or filing marker."
    if event_types:
        return "Active company catalyst tape: " + ", ".join(event_types[:3])
    return "No dated company catalyst; require fresh evidence before raising target."


def source_labels(source_tiers: list[str], external: dict[str, Any]) -> list[str]:
    labels = [str(item) for item in source_tiers if item]
    if external.get("sec_fundamental_score") is not None:
        labels.append("sec_company_data")
    if external.get("alpha_news_sentiment") is not None:
        labels.append("alpha_vantage_news")
    if external.get("gdelt_event_score") is not None:
        labels.append("gdelt")
    if external.get("short_interest_risk_score") is not None:
        labels.append("short_interest")
    return sorted(set(labels))


def company_reason(
    symbol: str,
    score: float,
    growth: float,
    margins: float,
    balance_sheet: float,
    valuation: float,
    event_types: list[str],
) -> str:
    if score >= 68:
        return f"{symbol} clears the bottom-up bar: growth durability, margin setup, and valuation support are strong enough to underwrite size."
    if score >= 58:
        return f"{symbol} has enough company evidence for an add if sizing and timing constraints permit."
    if score < 40:
        return f"{symbol} company evidence is weak; prioritize trim or avoid until fundamentals improve."
    weak = min(
        [
            ("growth durability", growth),
            ("margin trajectory", margins),
            ("balance sheet/financing", balance_sheet),
            ("valuation support", valuation),
        ],
        key=lambda item: item[1],
    )[0]
    if event_types:
        return f"{symbol} needs more company proof; weakest area is {weak} while {', '.join(event_types[:2])} is active."
    return f"{symbol} needs more company proof; weakest area is {weak}."


def company_thesis(symbol: str, bucket: str) -> str:
    setup = SECTOR_TEMPLATES.get(bucket, SECTOR_TEMPLATES["unmapped"])["setup"]
    return f"{symbol} must prove the company-specific version of the bucket thesis: {setup}"


def bull_case(symbol: str, bucket: str) -> str:
    if bucket == "neocloud_datacenters":
        return f"{symbol} signs durable customers, maintains high utilization, finances GPUs on acceptable terms, and secures power."
    if bucket == "semis_networking_hbm":
        return f"{symbol} benefits from sustained AI capex, tight supply, resilient margins, and expanding compute/networking attach."
    if bucket == "power_grid_gas_nuclear":
        return f"{symbol} converts AI load growth into contracted power, equipment demand, or dispatchable capacity economics."
    if bucket == "ai_software_winners":
        return f"{symbol} turns AI product usage into retention, expansion, pricing power, and sales efficiency."
    return f"{symbol} converts AI demand into durable growth and operating leverage."


def base_case(symbol: str, bucket: str) -> str:
    if bucket == "disrupted_incumbents":
        return f"{symbol} defends enough revenue and margin to avoid rapid multiple compression."
    return f"{symbol} compounds if company KPIs confirm the AI thesis without a major valuation reset."


def bear_case(symbol: str, bucket: str) -> str:
    if bucket == "neocloud_datacenters":
        return f"{symbol} disappoints on utilization, customer concentration, debt cost, or GPU depreciation."
    if bucket == "semis_networking_hbm":
        return f"{symbol} sells off if hyperscaler capex slows, supply catches up, or margins roll over."
    if bucket == "power_grid_gas_nuclear":
        return f"{symbol} loses upside if power contracts, regulation, commodities, or project execution fail."
    return f"{symbol} fails if AI enthusiasm is already priced and forward KPIs do not accelerate."


def falsifiers(bucket: str) -> list[str]:
    if bucket == "neocloud_datacenters":
        return ["Utilization weakens", "Financing cost rises", "Customer concentration worsens", "GPU depreciation outruns revenue"]
    if bucket == "semis_networking_hbm":
        return ["Backlog rolls over", "Gross margin compresses", "Inventory rises", "Hyperscaler capex guidance slows"]
    if bucket == "power_grid_gas_nuclear":
        return ["PPA pipeline fails", "Power prices fall", "Interconnection delays grow", "Project financing deteriorates"]
    if bucket == "ai_software_winners":
        return ["NRR weakens", "AI attach does not monetize", "Churn rises", "Sales efficiency deteriorates"]
    if bucket == "frontier_ai_platforms":
        return ["AI usage lacks revenue", "Capex ROI worsens", "Cloud growth slows", "Margins compress"]
    return ["Revenue growth slows", "Margins deteriorate", "Cash generation weakens", "Valuation support disappears"]


def primary_falsifier(bucket: str) -> str:
    return falsifiers(bucket)[0]


def external_features_by_symbol(external_signals: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = external_signals.get("by_symbol") or {}
    normalized: dict[str, dict[str, Any]] = {}
    if isinstance(rows, dict):
        for symbol, row in rows.items():
            for candidate in equivalent_symbols(str(symbol).upper()):
                normalized[candidate] = dict(row)
    return normalized


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


def portfolio_weight_by_symbol(portfolio: dict[str, Any]) -> dict[str, float]:
    weights: dict[str, float] = {}
    for row in portfolio.get("by_symbol", []):
        symbol = str(row.get("symbol") or "").upper()
        if not symbol or row.get("is_cash"):
            continue
        weight = float(row.get("comparison_weight", row.get("ex_cash_weight", row.get("weight") or 0)) or 0)
        for candidate in equivalent_symbols(symbol):
            weights[candidate] = weights.get(candidate, 0.0) + weight
    return weights


def return_for_symbol(return_windows: dict[str, dict[str, Any]], symbol: str, window: str) -> float | None:
    data = proxied_lookup(return_windows, symbol, {}) or {}
    return numeric(data.get(window))


def numeric(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))
