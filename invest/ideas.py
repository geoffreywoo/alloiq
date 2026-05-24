from __future__ import annotations

from typing import Any


def build_idea_book(
    decision_cards: list[dict[str, Any]],
    manager_radar: dict[str, Any],
    portfolio: dict[str, Any],
    macro: dict[str, Any],
) -> list[dict[str, Any]]:
    owned = {row["symbol"] for row in portfolio.get("by_symbol", [])}
    ideas: list[dict[str, Any]] = []
    regime = macro.get("regime", "mixed macro tape")
    for card in decision_cards:
        if len(ideas) >= 10:
            break
        symbol = card["symbol"]
        manager_count = card.get("consensus_manager_count", 0)
        portfolio_value = card.get("portfolio_value", 0)
        signal_families = card.get("signal_families", [])
        if len(signal_families) < 2 and card["score"] < 38:
            continue
        if card["score"] < 18 and manager_count < 2 and not portfolio_value:
            continue
        idea_type = classify_idea(card, symbol in owned, regime)
        ideas.append(
            {
                "symbol": symbol,
                "type": idea_type,
                "bucket": card["bucket"],
                "score": card["score"],
                "setup": setup_text(card, idea_type, regime),
                "evidence": evidence_text(card),
                "trigger": trigger_text(card, regime),
                "risk": card["counterargument"],
                "falsifier": card["falsifier"],
                "signal_families": signal_families,
                "event_types": card.get("top_event_types", []),
                "score_components": card.get("score_components", {}),
            }
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
                }
            )
    return ideas


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
    if card["candidate"] in {"research add candidate", "watch closely"}:
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
