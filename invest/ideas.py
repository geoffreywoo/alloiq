from __future__ import annotations

from typing import Any


MAX_IDEAS = 12
MIN_FRESH_IDEAS = 4
MAX_OWNED_IDEAS = 5


def build_idea_book(
    decision_cards: list[dict[str, Any]],
    manager_radar: dict[str, Any],
    portfolio: dict[str, Any],
    macro: dict[str, Any],
) -> list[dict[str, Any]]:
    owned = {str(row.get("symbol") or "").upper() for row in portfolio.get("by_symbol", [])}
    card_ideas: list[dict[str, Any]] = []
    regime = macro.get("regime", "mixed macro tape")
    for card in decision_cards:
        symbol = str(card.get("symbol") or "").upper()
        if not symbol:
            continue
        manager_count = card.get("consensus_manager_count", 0)
        portfolio_value = card.get("portfolio_value", 0)
        signal_families = card.get("signal_families", [])
        if len(signal_families) < 2 and card["score"] < 38:
            continue
        if card["score"] < 18 and manager_count < 2 and not portfolio_value:
            continue
        card_ideas.append(idea_from_card(card, symbol in owned, regime))

    ideas = select_diverse_ideas(
        card_ideas
        + manager_discovery_ideas(
            manager_radar,
            owned,
            {str(idea.get("symbol") or "").upper() for idea in card_ideas},
            regime,
        ),
        owned,
    )
    if not ideas:
        for row in manager_radar.get("top_consensus", [])[:5]:
            ideas.append(
                {
                    "symbol": row["symbol"],
                    "type": "manager-overlap research",
                    "bucket": row["bucket"],
                    "score": row["consensus_score"],
                    "setup": "Multiple tracked managers own this name, so it belongs in the research queue even without a watchlist score.",
                    "evidence": f"{row['common_manager_count']} managers report common exposure.",
                    "trigger": "Build a primary thesis, valuation range, and falsifier before considering action.",
                    "risk": "13F overlap can be stale and crowded.",
                    "falsifier": "Fresh business evidence does not support the market-implied growth path.",
                    "idea_source": "manager_consensus_fallback",
                    "freshness": "fresh_research",
                    "novelty_score": 60,
                }
            )
    return ideas


def idea_from_card(card: dict[str, Any], owned: bool, regime: str) -> dict[str, Any]:
    idea_type = classify_idea(card, owned, regime)
    symbol = str(card.get("symbol") or "").upper()
    novelty = novelty_score_for_card(card, owned, idea_type)
    return {
        "symbol": symbol,
        "type": idea_type,
        "bucket": card["bucket"],
        "score": card["score"],
        "setup": setup_text(card, idea_type, regime),
        "evidence": evidence_text(card),
        "trigger": trigger_text(card, regime),
        "risk": card["counterargument"],
        "falsifier": card["falsifier"],
        "signal_families": card.get("signal_families", []),
        "event_types": card.get("top_event_types", []),
        "score_components": card.get("score_components", {}),
        "idea_source": "watchlist_card",
        "freshness": "fresh_research" if not owned else "owned_refresh",
        "novelty_score": novelty,
        "exploration_reason": exploration_reason(card, owned, idea_type),
    }


def manager_discovery_ideas(
    manager_radar: dict[str, Any],
    owned: set[str],
    existing_symbols: set[str],
    regime: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source, source_rows in (
        ("manager_new_position", manager_radar.get("new_positions") or []),
        ("manager_accumulation", manager_radar.get("top_adds") or []),
        ("manager_consensus", manager_radar.get("top_consensus") or []),
        ("option_watch", manager_radar.get("option_watch") or []),
    ):
        for row in source_rows[:12]:
            symbol = str(row.get("symbol") or "").upper()
            if not symbol or symbol in existing_symbols:
                continue
            score = manager_discovery_score(row, source)
            if score < 18:
                continue
            idea_type = manager_discovery_type(row, source)
            rows.append(
                {
                    "symbol": symbol,
                    "type": idea_type,
                    "bucket": row.get("bucket", "unmapped"),
                    "score": round(score, 2),
                    "setup": manager_discovery_setup(symbol, row, source, regime),
                    "evidence": manager_discovery_evidence(row, source),
                    "trigger": manager_discovery_trigger(row, source),
                    "risk": "Manager filings and option data are delayed public signals; validate business quality, valuation, and near-term catalyst path before sizing.",
                    "falsifier": "Primary-source business evidence does not support a durable forward-return edge.",
                    "signal_families": manager_discovery_signal_families(row, source),
                    "event_types": [],
                    "score_components": manager_discovery_components(row, source),
                    "idea_source": source,
                    "freshness": "fresh_research" if symbol not in owned else "owned_refresh",
                    "novelty_score": manager_discovery_novelty(row, source, symbol in owned),
                    "exploration_reason": "New non-portfolio signal from tracked managers/options; underwrite as research-only before any target weight.",
                }
            )
    return rows


def select_diverse_ideas(ideas: list[dict[str, Any]], owned: set[str]) -> list[dict[str, Any]]:
    ranked = sorted(ideas, key=idea_priority, reverse=True)
    selected: list[dict[str, Any]] = []
    seen_symbols: set[str] = set()
    bucket_counts: dict[str, int] = {}
    owned_count = 0

    def add(idea: dict[str, Any]) -> bool:
        nonlocal owned_count
        symbol = str(idea.get("symbol") or "").upper()
        bucket = str(idea.get("bucket") or "unmapped")
        if not symbol or symbol in seen_symbols:
            return False
        is_owned = symbol in owned or idea.get("freshness") == "owned_refresh"
        if is_owned and owned_count >= MAX_OWNED_IDEAS and not is_fresh_idea(idea, owned):
            return False
        if bucket_counts.get(bucket, 0) >= 4 and len(selected) >= MIN_FRESH_IDEAS:
            return False
        selected.append(idea)
        seen_symbols.add(symbol)
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        if is_owned:
            owned_count += 1
        return True

    for idea in [row for row in ranked if is_fresh_idea(row, owned)]:
        if len([row for row in selected if is_fresh_idea(row, owned)]) >= MIN_FRESH_IDEAS:
            break
        add(idea)
    for idea in ranked:
        if len(selected) >= MAX_IDEAS:
            break
        add(idea)
    return selected


def is_fresh_idea(idea: dict[str, Any], owned: set[str]) -> bool:
    symbol = str(idea.get("symbol") or "").upper()
    return symbol not in owned and idea.get("freshness") == "fresh_research"


def idea_priority(idea: dict[str, Any]) -> float:
    return (
        float(idea.get("score") or 0)
        + float(idea.get("novelty_score") or 0) * 0.6
        + min(4, len(idea.get("signal_families") or [])) * 2.5
    )


def novelty_score_for_card(card: dict[str, Any], owned: bool, idea_type: str) -> float:
    score = 12.0 if owned else 45.0
    if "white-space" in idea_type or "new long" in idea_type:
        score += 20.0
    if card.get("consensus_manager_count", 0) >= 3 and not owned:
        score += 10.0
    if {"contract_win", "capex_signal", "earnings_revision"} & set(card.get("top_event_types") or []):
        score += 8.0
    return min(100.0, score)


def exploration_reason(card: dict[str, Any], owned: bool, idea_type: str) -> str:
    if owned:
        return "Owned-name refresh; include only if it beats fresher white-space alternatives on evidence and risk."
    if "white-space" in idea_type:
        return "Non-owned white-space candidate with multiple confirming signals."
    if "hedge" in idea_type:
        return "Non-owned risk/hedge candidate worth studying as a portfolio-risk offset."
    return "Non-owned research candidate; underwrite variant view before any sizing."


def manager_discovery_score(row: dict[str, Any], source: str) -> float:
    common_count = float(row.get("common_manager_count", row.get("manager_count", 0)) or 0)
    common_value = float(row.get("common_value", row.get("latest_value", row.get("value", 0))) or 0)
    delta_value = max(0.0, float(row.get("delta_value") or 0))
    call_value = float(row.get("call_value") or 0)
    put_value = float(row.get("put_value") or 0)
    score = common_count * 5.0 + min(18.0, common_value / 350_000_000) + min(16.0, delta_value / 125_000_000)
    if source == "manager_new_position":
        score += 12.0
    if call_value > put_value and call_value:
        score += min(8.0, call_value / 150_000_000)
    if put_value > call_value and put_value:
        score += min(5.0, put_value / 200_000_000)
    return min(75.0, score)


def manager_discovery_type(row: dict[str, Any], source: str) -> str:
    if source == "manager_new_position":
        return "new manager position discovery"
    if source == "manager_accumulation":
        return "manager accumulation discovery"
    if source == "option_watch":
        call_value = float(row.get("call_value") or 0)
        put_value = float(row.get("put_value") or 0)
        return "call-option upside discovery" if call_value >= put_value else "hedge/avoidance research"
    return "manager consensus discovery"


def manager_discovery_setup(symbol: str, row: dict[str, Any], source: str, regime: str) -> str:
    if source == "manager_accumulation":
        return f"{symbol} is a fresh tracked-manager accumulation signal in a {regime} regime."
    if source == "manager_new_position":
        return f"{symbol} appeared as a new tracked-manager position and deserves first-principles underwriting."
    if source == "option_watch":
        return f"{symbol} has notable public 13F option exposure; study whether it is directional signal or hedge noise."
    return f"{symbol} has multi-manager public filing support outside the current portfolio focus."


def manager_discovery_evidence(row: dict[str, Any], source: str) -> str:
    pieces = []
    if row.get("manager_count") or row.get("common_manager_count"):
        pieces.append(f"{row.get('manager_count', row.get('common_manager_count'))} tracked managers")
    if row.get("delta_value"):
        pieces.append(f"aggregate add ${float(row.get('delta_value') or 0):,.0f}")
    if row.get("common_value") or row.get("latest_value") or row.get("value"):
        pieces.append(f"reported value ${float(row.get('common_value', row.get('latest_value', row.get('value', 0))) or 0):,.0f}")
    if row.get("call_value") or row.get("put_value"):
        pieces.append(f"calls ${float(row.get('call_value') or 0):,.0f}/puts ${float(row.get('put_value') or 0):,.0f}")
    pieces.append(f"source {source}")
    return "; ".join(pieces) + "."


def manager_discovery_trigger(row: dict[str, Any], source: str) -> str:
    if source == "option_watch":
        return "Separate directional call/put exposure from hedging, then look for a business catalyst that can explain the positioning."
    if source == "manager_accumulation":
        return "Find the manager-change rationale, current valuation, and catalyst timing before promoting to a starter candidate."
    return "Build the primary thesis, variant view, valuation range, catalyst calendar, and falsifier from primary sources."


def manager_discovery_signal_families(row: dict[str, Any], source: str) -> list[str]:
    families = ["manager"]
    if source in {"manager_accumulation", "manager_new_position"}:
        families.append("manager_flow")
    if source == "option_watch":
        families.append("options")
    if row.get("common_manager_count", row.get("manager_count", 0)):
        families.append("portfolio_fit")
    return families


def manager_discovery_components(row: dict[str, Any], source: str) -> dict[str, float]:
    return {
        "manager": round(float(row.get("common_manager_count", row.get("manager_count", 0)) or 0) * 5.0, 2),
        "manager_flow": round(min(16.0, max(0.0, float(row.get("delta_value") or 0)) / 125_000_000), 2),
        "option_tilt": round(min(8.0, max(float(row.get("call_value") or 0), float(row.get("put_value") or 0)) / 150_000_000), 2),
        "discovery": 12.0 if source == "manager_new_position" else 0.0,
    }


def manager_discovery_novelty(row: dict[str, Any], source: str, owned: bool) -> float:
    novelty = 20.0 if owned else 70.0
    if source in {"manager_new_position", "manager_accumulation"}:
        novelty += 15.0
    if str(row.get("bucket") or "unmapped") == "unmapped":
        novelty += 5.0
    return min(100.0, novelty)


def classify_idea(card: dict[str, Any], owned: bool, regime: str) -> str:
    event_types = set(card.get("top_event_types", []))
    if owned and {"financing_risk", "regulatory_risk", "crowding_warning"} & event_types:
        return "owned catalyst risk review"
    if not owned and {"contract_win", "capex_signal", "earnings_revision"} & event_types and card.get("consensus_manager_count", 0) >= 2:
        return "catalyst-confirmed white-space research"
    if card.get("put_value", 0) > card.get("call_value", 0) and card.get("put_value", 0) > 0:
        return "hedge/avoidance research"
    if owned and regime in {"rates/dollar headwind", "volatility shock"}:
        return "owned risk review"
    if owned:
        return "owned add/trim review"
    if card.get("consensus_manager_count", 0) >= 3:
        return "consensus white-space research"
    if card.get("candidate") in {"research add candidate", "watch closely"}:
        return "new long research"
    return "monitor"


def setup_text(card: dict[str, Any], idea_type: str, regime: str) -> str:
    event_types = ", ".join(card.get("top_event_types", [])[:2])
    if idea_type == "owned catalyst risk review":
        return f"{card['symbol']} is owned and has negative catalyst flags ({event_types or 'news risk'}) in a {regime} regime."
    if idea_type == "catalyst-confirmed white-space research":
        return f"{card['symbol']} is not owned, has manager support, and recent catalysts ({event_types}) justify fresh underwriting."
    if idea_type == "hedge/avoidance research":
        return f"{card['symbol']} has tracked option caution while the macro regime is {regime}."
    if idea_type == "owned risk review":
        return f"{card['symbol']} is already in the portfolio and the tape argues for sizing discipline."
    if idea_type == "owned add/trim review":
        return f"{card['symbol']} is owned; compare incremental upside against current portfolio weight."
    if idea_type == "consensus white-space research":
        return f"{card['symbol']} is not owned but appears across the tracked manager universe."
    if idea_type == "new long research":
        return f"{card['symbol']} has enough AI-max signal density to underwrite from first principles."
    return f"{card['symbol']} stays on watch until evidence improves."


def evidence_text(card: dict[str, Any]) -> str:
    pieces = [
        f"score {card['score']:.2f}",
        f"{card.get('signal_family_count', 0)} signal families",
        f"{card.get('news_count', 0)} news hits",
        f"{card.get('consensus_manager_count', 0)} tracked common holders",
    ]
    if card.get("top_event_types"):
        pieces.append("events " + ", ".join(card["top_event_types"][:3]))
    if card.get("filing_value"):
        pieces.append(f"primary filing value ${card['filing_value']:,.0f}")
    if card.get("consensus_value"):
        pieces.append(f"tracked common value ${card['consensus_value']:,.0f}")
    if card.get("five_day_pct") is not None:
        pieces.append(f"5d {card['five_day_pct']:.2f}%")
    return "; ".join(pieces) + "."


def trigger_text(card: dict[str, Any], regime: str) -> str:
    event_types = set(card.get("top_event_types", []))
    if "financing_risk" in event_types:
        return "Read the financing terms and dilution risk before treating the headline as investable growth."
    if "contract_win" in event_types:
        return "Verify contract size, duration, counterparty quality, margins, funding, and whether revenue timing is near term."
    if "capex_signal" in event_types:
        return "Check whether hyperscaler capex converts into orders, backlog, utilization, and pricing power for this company."
    if card["bucket"] == "power_grid_gas_nuclear":
        return "Look for signed power contracts, interconnection progress, financing terms, or commodity-price confirmation."
    if card["bucket"] == "neocloud_datacenters":
        return "Require utilization, customer quality, financing, and capex evidence before upgrading conviction."
    if card["bucket"] == "semis_networking_hbm":
        return "Watch order/backlog, hyperscaler capex, supply constraints, and gross-margin direction."
    if card["bucket"] == "frontier_ai_platforms":
        return "Require proof that AI usage is translating into durable revenue or operating leverage."
    if regime == "volatility shock":
        return "Wait for correlation and volatility to normalize before treating price weakness as opportunity."
    return "Define a variant view, expected value, and what would make the idea wrong."
