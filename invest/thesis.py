from __future__ import annotations

from decimal import Decimal
from typing import Any

from .symbols import equivalent_symbols, proxied_lookup, sum_equivalent_values


def build_decision_cards(
    watchlist: list[str],
    bucket_map: dict[str, str],
    holdings_by_symbol: dict[str, Decimal],
    filing_values_by_symbol: dict[str, Decimal],
    news_counts_by_symbol: dict[str, int],
    prices: dict[str, dict[str, Decimal]],
    manager_consensus_by_symbol: dict[str, dict[str, Any]] | None = None,
    news_events_by_symbol: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    manager_consensus_by_symbol = manager_consensus_by_symbol or {}
    news_events_by_symbol = news_events_by_symbol or {}
    cards: list[dict[str, Any]] = []
    for symbol in watchlist:
        filing_value = sum_equivalent_values(filing_values_by_symbol, symbol, Decimal("0"))
        portfolio_value = sum_equivalent_values(holdings_by_symbol, symbol, Decimal("0"))
        consensus = consensus_for_symbol(manager_consensus_by_symbol, symbol)
        consensus_value = Decimal(str(consensus.get("common_value", 0)))
        manager_count = int(consensus.get("common_manager_count", 0))
        put_value = Decimal(str(consensus.get("put_value", 0)))
        call_value = Decimal(str(consensus.get("call_value", 0)))
        news_count = int(proxied_lookup(news_counts_by_symbol, symbol, 0) or 0)
        news_event = proxied_lookup(news_events_by_symbol, symbol, {})
        quote = proxied_lookup(prices, symbol, {})
        momentum = quote.get("five_day_pct", Decimal("0"))
        bucket = str(proxied_lookup(bucket_map, symbol, "unmapped"))
        components = score_components(
            portfolio_value,
            filing_value,
            consensus_value,
            manager_count,
            call_value,
            put_value,
            news_event,
            momentum,
        )
        score = sum(Decimal(str(value)) for value in components.values())
        signal_families = signal_families_for_card(
            portfolio_value,
            filing_value,
            consensus_value,
            manager_count,
            news_event,
            momentum,
        )
        event_types = list(news_event.get("event_types", []))
        source_tiers = list(news_event.get("source_tiers", []))
        risk = classify_risk(symbol, bucket, momentum, filing_value)
        cards.append(
            {
                "symbol": symbol,
                "bucket": bucket,
                "score": float(round(score, 2)),
                "portfolio_value": float(portfolio_value),
                "filing_value": float(filing_value),
                "consensus_value": float(consensus_value),
                "consensus_manager_count": manager_count,
                "call_value": float(call_value),
                "put_value": float(put_value),
                "consensus_managers": consensus.get("common_managers", []),
                "news_count": news_count,
                "event_score": float(news_event.get("event_score") or 0),
                "top_event_types": event_types[:4],
                "source_tiers": source_tiers[:4],
                "signal_families": signal_families,
                "signal_family_count": len(signal_families),
                "score_components": {key: round(float(value), 2) for key, value in components.items()},
                "last_price": float(quote.get("last", Decimal("0"))) if quote else None,
                "five_day_pct": float(round(momentum, 2)) if quote else None,
                "candidate": candidate_label(score, portfolio_value, filing_value, momentum, manager_count),
                "counterargument": counterargument(symbol, bucket, risk),
                "falsifier": falsifier(bucket),
                "risk": risk,
            }
        )
    cards.sort(key=lambda row: row["score"], reverse=True)
    return cards


def consensus_for_symbol(rows: dict[str, dict[str, Any]], symbol: str) -> dict[str, Any]:
    exact = proxied_lookup(rows, symbol)
    if not exact:
        return {}
    matched = [rows[candidate] for candidate in equivalent_symbols(symbol) if candidate in rows]
    common_managers: set[str] = set()
    call_managers: set[str] = set()
    put_managers: set[str] = set()
    issuers: set[str] = set()
    for row in matched:
        common_managers.update(str(value) for value in row.get("common_managers", []) if value)
        call_managers.update(str(value) for value in row.get("call_managers", []) if value)
        put_managers.update(str(value) for value in row.get("put_managers", []) if value)
        issuers.update(str(value) for value in row.get("issuers", []) if value)
    common_count = len(common_managers) if common_managers else max(int(row.get("common_manager_count") or 0) for row in matched)
    call_count = len(call_managers) if call_managers else max(int(row.get("call_manager_count") or 0) for row in matched)
    put_count = len(put_managers) if put_managers else max(int(row.get("put_manager_count") or 0) for row in matched)
    primary = dict(exact)
    primary.update(
        {
            "common_value": sum(float(row.get("common_value") or 0) for row in matched),
            "call_value": sum(float(row.get("call_value") or 0) for row in matched),
            "put_value": sum(float(row.get("put_value") or 0) for row in matched),
            "common_manager_count": common_count,
            "call_manager_count": call_count,
            "put_manager_count": put_count,
            "common_managers": sorted(common_managers),
            "call_managers": sorted(call_managers),
            "put_managers": sorted(put_managers),
            "issuers": sorted(issuers),
        }
    )
    return primary


def score_components(
    portfolio_value: Decimal,
    filing_value: Decimal,
    consensus_value: Decimal,
    manager_count: int,
    call_value: Decimal,
    put_value: Decimal,
    news_event: dict[str, Any],
    momentum: Decimal,
) -> dict[str, float]:
    manager_score = Decimal(manager_count * 3)
    if consensus_value:
        manager_score += min(Decimal("10"), consensus_value / Decimal("1000000000"))
    if filing_value:
        manager_score += min(Decimal("8"), filing_value / Decimal("250000000"))
    manager_score = min(Decimal("25"), manager_score)

    catalyst_score = min(Decimal("20"), Decimal(str(news_event.get("event_score") or 0)))
    portfolio_score = Decimal("0")
    if portfolio_value:
        portfolio_score = Decimal("12")
    elif manager_count >= 3:
        portfolio_score = Decimal("8")

    price_score = Decimal("0")
    if Decimal("0") < momentum < Decimal("8"):
        price_score = min(Decimal("10"), momentum)
    elif momentum <= Decimal("-10"):
        price_score = Decimal("8")
    elif momentum >= Decimal("8"):
        price_score = Decimal("2")

    option_score = Decimal("0")
    if call_value > put_value and call_value:
        option_score = Decimal("5")
    elif put_value > call_value and put_value:
        option_score = Decimal("-7")

    return {
        "manager": float(manager_score),
        "catalyst": float(catalyst_score),
        "portfolio_fit": float(portfolio_score),
        "price_action": float(price_score),
        "option_tilt": float(option_score),
    }


def signal_families_for_card(
    portfolio_value: Decimal,
    filing_value: Decimal,
    consensus_value: Decimal,
    manager_count: int,
    news_event: dict[str, Any],
    momentum: Decimal,
) -> list[str]:
    families: list[str] = []
    if manager_count >= 2 or consensus_value or filing_value:
        families.append("manager")
    if news_event.get("event_score", 0) >= 2:
        families.append("catalyst")
    if portfolio_value or manager_count >= 3:
        families.append("portfolio_fit")
    if momentum >= Decimal("3") or momentum <= Decimal("-8"):
        families.append("price_action")
    return families


def candidate_label(
    score: Decimal,
    portfolio_value: Decimal,
    filing_value: Decimal,
    momentum: Decimal,
    manager_count: int = 0,
) -> str:
    if score >= 45 and manager_count >= 3 and portfolio_value == 0:
        return "consensus white-space research"
    if score >= 35 and filing_value and portfolio_value == 0:
        return "research add candidate"
    if score >= 35 and portfolio_value:
        return "research hold/add-on-dip candidate"
    if momentum < -15 and filing_value:
        return "research volatility check"
    if score >= 20:
        return "watch closely"
    return "monitor"


def classify_risk(symbol: str, bucket: str, momentum: Decimal, filing_value: Decimal) -> str:
    high_beta_buckets = {"neocloud_datacenters", "power_grid_gas_nuclear"}
    if bucket in high_beta_buckets or momentum.copy_abs() > 12:
        return "high"
    if filing_value > Decimal("500000000"):
        return "crowded"
    return "medium"


def counterargument(symbol: str, bucket: str, risk: str) -> str:
    if bucket == "power_grid_gas_nuclear":
        return "AI power demand may be real while permitting, interconnection, commodity prices, or project execution cap equity upside."
    if bucket == "neocloud_datacenters":
        return "GPU clouds and data-center operators can look cheap on demand but still face financing, customer concentration, and capex-cycle risk."
    if bucket == "semis_networking_hbm":
        return "AI hardware winners can suffer sharp multiple compression if hyperscaler capex growth slows or supply catches up."
    if bucket == "frontier_ai_platforms":
        return "Platform leaders can monetize AI slowly while spending aggressively, compressing margins before revenue catches up."
    if bucket == "ai_software_winners":
        return "Software AI narratives can outrun actual seat expansion, retention, or pricing power."
    if risk == "crowded":
        return "The setup may be right but crowded; a small disappointment can drive a large drawdown."
    return "The thesis needs fresh evidence; do not let AI enthusiasm substitute for forward return math."


def falsifier(bucket: str) -> str:
    if bucket == "power_grid_gas_nuclear":
        return "Demand contracts fail to convert into funded capacity, or power pricing/regulatory changes undermine project economics."
    if bucket == "neocloud_datacenters":
        return "Utilization, customer quality, or financing terms deteriorate despite AI demand headlines."
    if bucket == "semis_networking_hbm":
        return "Order growth, backlog, or gross margin inflects down while capex expectations stay elevated."
    if bucket == "frontier_ai_platforms":
        return "AI product usage grows without durable revenue, retention, or operating leverage."
    if bucket == "ai_software_winners":
        return "AI features fail to increase net retention, pricing, or new workload creation."
    return "New data contradicts the expected AI demand, pricing, or margin path."
