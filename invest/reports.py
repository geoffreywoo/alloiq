from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from statistics import mean, median
from typing import Any

from .audit import build_audit_snapshot
from .backtest import build_backtest_summary, outcome_history_from_backtest
from .calendars import build_calendar_snapshot
from .config import AppConfig
from .earnings import build_earnings_events, earnings_health_summary
from .engine import build_engine_snapshot
from .external_signals import build_external_signal_snapshot, external_provider_health_detail
from .features import build_feature_matrix
from .instrumentation import build_instrumentation_audit
from .ideas import build_idea_book
from .macro import DEFAULT_MACRO_SYMBOLS, build_macro_dashboard
from .macro_fred import DEFAULT_FRED_SERIES, build_fred_macro_snapshot
from .managers import build_manager_radar
from .market import fetch_daily_prices, fetch_return_windows
from .news import enrich_news_item, fetch_many
from .outcomes import build_outcome_diagnostics, build_training_examples
from .paper import build_paper_portfolio
from .portfolio import build_portfolio_exposure
from .research import build_research_book
from .risk import apply_risk_controls, normalize_limits
from .sizing import annotate_action_funding, build_sizing_plan, rebalance_budget_summary
from .symbols import expand_symbol_proxies, equivalent_symbols, proxied_lookup, proxy_index, sum_equivalent_values, symbol_proxy_key
from .thesis import build_decision_cards
from .underwriting import build_underwriting_layers
from .util import ensure_dir, stable_id
from .valuation import (
    AI_MAXXI_MANAGER_KEYS,
    attach_manager_valuations,
    build_manager_valuation_snapshot,
    build_portfolio_valuation_snapshot,
    manager_valuation_symbols,
)


WEAK_SOURCE_STATUSES = {"missing", "stale", "limited", "estimated", "unknown", "failed", "error"}


NEWS_ALIASES = {
    "BE": ["BLOOM ENERGY"],
    "CRWV": ["COREWEAVE"],
    "INTC": ["INTEL"],
    "LITE": ["LUMENTUM"],
    "CORZ": ["CORE SCIENTIFIC"],
    "IREN": ["IREN"],
    "APLD": ["APPLIED DIGITAL"],
    "CIFR": ["CIPHER MINING"],
    "EQT": ["EQT"],
    "COHR": ["COHERENT"],
    "NVDA": ["NVIDIA"],
    "AVGO": ["BROADCOM"],
    "AMD": ["ADVANCED MICRO DEVICES"],
    "TSM": ["TAIWAN SEMICONDUCTOR", "TSMC"],
    "ASML": ["ASML"],
    "MU": ["MICRON"],
    "MRVL": ["MARVELL"],
    "VRT": ["VERTIV"],
    "CEG": ["CONSTELLATION ENERGY"],
    "TLN": ["TALEN"],
    "VST": ["VISTRA"],
    "GEV": ["GE VERNOVA"],
    "ORCL": ["ORACLE"],
    "MSFT": ["MICROSOFT"],
    "GOOGL": ["ALPHABET"],
    "GOOG": ["ALPHABET"],
    "META": ["META PLATFORMS"],
    "AMZN": ["AMAZON"],
    "AAPL": ["APPLE"],
    "ANET": ["ARISTA"],
    "ARM": ["ARM HOLDINGS"],
    "PLTR": ["PALANTIR"],
    "CRWD": ["CROWDSTRIKE"],
    "SHOP": ["SHOPIFY"],
    "ETN": ["EATON"],
    "HOOD": ["ROBINHOOD"],
}


def generate_brief(conn: sqlite3.Connection, config: AppConfig, session: str, as_of: date | None = None) -> tuple[Path, Path]:
    as_of = as_of or date.today()
    ensure_dir(config.reports_dir)
    news_items = fetch_many(config.news_queries, limit=int(config.data.get("news", {}).get("max_items_per_query", 5)))
    from .db import insert_news

    insert_news(conn, news_items)
    latest_filing = latest_manager_filing(conn, config.primary_manager["key"])
    filing_values = latest_filing_values(conn, config.primary_manager["key"])
    configured_universe = configured_ai_universe_symbols(config)
    price_symbols = unique_symbols(expand_symbol_proxies(configured_universe + [row["symbol"] for row in config.manual_positions]))
    prices = fetch_daily_prices(price_symbols)
    macro_symbols = config.macro_symbols or DEFAULT_MACRO_SYMBOLS
    portfolio = build_portfolio_exposure(conn, config, prices=prices, as_of=as_of)
    positions = portfolio_values_by_symbol(portfolio)
    portfolio_weights = portfolio_comparison_weights_by_symbol(portfolio)
    manager_radar = build_manager_radar(conn, config, portfolio_weights)
    research_symbols = build_research_universe(config, portfolio, manager_radar)
    missing_research_price_symbols = [symbol for symbol in expand_symbol_proxies(research_symbols) if symbol not in prices]
    if missing_research_price_symbols:
        prices.update(fetch_daily_prices(unique_symbols(missing_research_price_symbols)))
    return_windows = fetch_return_windows(unique_symbols(expand_symbol_proxies(research_symbols) + macro_symbols))
    valuation_symbols = [symbol for symbol in manager_valuation_symbols(conn, AI_MAXXI_MANAGER_KEYS) if symbol not in prices]
    if valuation_symbols:
        prices.update(fetch_daily_prices(valuation_symbols))
    manager_valuation = build_manager_valuation_snapshot(conn, config, prices, AI_MAXXI_MANAGER_KEYS)
    manager_radar = attach_manager_valuations(manager_radar, manager_valuation)
    portfolio_valuation_private = build_portfolio_valuation_snapshot(portfolio, as_of)
    recent_transactions = transactions_since(conn, as_of - timedelta(days=5))
    recent_news = [enrich_news_item(dict(row)) for row in latest_news(conn, limit=60)]
    news_counts = count_news_by_symbol(recent_news, research_symbols)
    news_events = build_news_event_signals(recent_news, research_symbols)
    earnings_events = build_earnings_events(config, research_symbols, as_of, news_events)
    macro_prices = fetch_daily_prices(macro_symbols[:30])
    fred_macro = build_fred_macro_snapshot(config.macro_fred_settings, as_of)
    macro = build_macro_dashboard(macro_prices, fred_macro)
    external_signals = build_external_signal_snapshot(config, as_of, research_symbols)
    cards = build_decision_cards(
        research_symbols,
        config.symbol_to_bucket,
        positions,
        filing_values,
        news_counts,
        prices,
        manager_radar["by_symbol"],
        news_events,
    )
    ideas = build_idea_book(cards, manager_radar, portfolio, macro)
    catalyst_signals = top_catalyst_signals(news_events)
    signal_synthesis = build_signal_synthesis(cards, macro, manager_radar, portfolio, catalyst_signals)
    underwriting_layers = build_underwriting_layers(
        as_of,
        cards,
        portfolio,
        manager_radar,
        macro,
        return_windows,
        earnings_events,
        external_signals,
    )
    company_underwriting = underwriting_layers["company_underwriting"]
    sector_underwriting = underwriting_layers["sector_underwriting"]
    feature_matrix = build_feature_matrix(
        as_of,
        cards,
        portfolio,
        manager_radar,
        macro,
        return_windows,
        earnings_events,
        external_signals,
        company_underwriting,
        sector_underwriting,
    )
    research_book = build_research_book(as_of, feature_matrix, cards, macro)
    portfolio_benchmark = build_portfolio_benchmark(
        portfolio,
        cards,
        manager_radar,
        macro,
        prices,
        return_windows,
        risk_limits=config.risk_limits,
        earnings_events=earnings_events,
        research_book=research_book,
    )
    approval_tickets = build_approval_tickets(as_of, session, portfolio, portfolio_benchmark, cards)
    weekly_research = (
        build_weekly_research(as_of, ideas, cards, portfolio_benchmark, macro)
        if session == "weekly"
        else None
    )
    stale_vanguard = vanguard_staleness(conn, config.stale_vanguard_days) if config.vanguard_enabled else None
    data_health = build_data_health(
        portfolio,
        manager_radar,
        recent_news,
        prices,
        earnings_events,
        stale_vanguard,
        filing_result_count=bool(latest_filing),
        broker_result_count=portfolio.get("position_count", 0),
        macro=macro,
        external_signals=external_signals,
    )
    calendars = build_calendar_snapshot(config, as_of, manager_radar, earnings_events)
    recommendation_training_examples = build_training_examples(
        as_of,
        session,
        approval_tickets,
        research_book,
        feature_matrix,
    )
    backtest = build_backtest_summary(
        config.reports_dir,
        as_of=as_of,
        include_current_examples=recommendation_training_examples,
    )
    outcome_history = outcome_history_from_backtest(backtest)
    engine = build_engine_snapshot(
        as_of,
        session,
        cards,
        portfolio,
        portfolio_benchmark,
        approval_tickets,
        config.risk_limits,
        outcome_history=outcome_history,
        feature_matrix=feature_matrix,
        research_book=research_book,
    )
    outcome_diagnostics = build_outcome_diagnostics(
        as_of,
        recommendation_training_examples,
        outcome_history,
        backtest,
    )
    paper_portfolio = build_paper_portfolio(as_of, session, portfolio, approval_tickets, cards, outcome_history)
    methodology = build_methodology(
        config,
        session,
        data_health,
        signal_synthesis,
        portfolio_benchmark,
        cards,
        approval_tickets,
        earnings_events,
        feature_matrix,
        research_book,
        outcome_diagnostics,
        external_signals,
        company_underwriting,
        sector_underwriting,
    )
    audit = build_audit_snapshot(as_of, session, data_health, calendars, engine, paper_portfolio, methodology, outcome_diagnostics)
    payload = {
        "as_of": as_of.isoformat(),
        "session": session,
        "latest_filing": dict(latest_filing) if latest_filing else None,
        "positions": {k: float(v) for k, v in positions.items()},
        "portfolio": portfolio,
        "manager_radar": manager_radar,
        "portfolio_valuation_private": portfolio_valuation_private,
        "macro": macro,
        "external_signals": external_signals,
        "company_underwriting": company_underwriting,
        "sector_underwriting": sector_underwriting,
        "transactions": [dict(row) for row in recent_transactions],
        "news": [dict(row) for row in recent_news],
        "news_events": news_events,
        "earnings_events": earnings_events,
        "catalyst_signals": catalyst_signals,
        "signal_synthesis": signal_synthesis,
        "feature_matrix": feature_matrix,
        "research_book": research_book,
        "portfolio_benchmark": portfolio_benchmark,
        "approval_tickets": approval_tickets,
        "recommendation_explanations": build_recommendation_explanations(as_of, portfolio_benchmark, research_book, company_underwriting, sector_underwriting),
        "review_queue": build_review_queue(research_book, portfolio_benchmark),
        "recommendation_training_examples": recommendation_training_examples,
        "outcome_diagnostics": outcome_diagnostics,
        "backtest": backtest,
        "data_health": data_health,
        "audit": audit,
        "calendars": calendars,
        "engine": engine,
        "paper_portfolio": paper_portfolio,
        "methodology": methodology,
        "decision_cards": cards[:20],
        "ideas": ideas,
        "stale_vanguard": stale_vanguard,
        "disclaimer": "Public weights, public filings, daily AI markets signals. Approval-only; no live order execution.",
        "product": {"name": config.product_name, "domain": config.product_domain},
    }
    if weekly_research:
        payload["weekly_research"] = weekly_research
    instrumentation_audit = build_instrumentation_audit(payload)
    payload["instrumentation_audit"] = instrumentation_audit
    payload["audit"]["instrumentation_health"] = {
        "status": instrumentation_audit["status"],
        "check_count": instrumentation_audit["check_count"],
        "failure_count": instrumentation_audit["failure_count"],
    }
    if instrumentation_audit["status"] != "ok":
        payload["audit"]["overall_status"] = "attention"
        payload["audit"].setdefault("data_gaps", []).extend(
            {
                "area": "instrumentation",
                "label": check.get("name", "number_wiring"),
                "status": check.get("status", "fail"),
                "detail": f"Observed {check.get('observed')} expected {check.get('expected', check.get('expected_max', 'n/a'))}",
            }
            for check in instrumentation_audit.get("failures", [])[:8]
        )
    md = render_markdown(payload, config)
    stem = f"{as_of.isoformat()}-{session}"
    md_path = config.reports_dir / f"{stem}.md"
    json_path = config.reports_dir / f"{stem}.json"
    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return md_path, json_path


def render_markdown(payload: dict[str, Any], config: AppConfig) -> str:
    session = payload["session"].title()
    lines: list[str] = []
    product = payload.get("product") or {"name": config.product_name, "domain": config.product_domain}
    lines.append(f"# {product['name']} {session} Brief - {payload['as_of']}")
    lines.append("")
    lines.append(f"`{product['domain']}`")
    lines.append("")
    lines.append("_Public weights, public filings, daily AI markets signals. Approval-only; no live order execution._")
    lines.append("")
    if payload.get("stale_vanguard") and payload["stale_vanguard"]["is_stale"]:
        lines.append(f"> Vanguard import status: stale or missing. Last import: {payload['stale_vanguard']['last_import'] or 'never'}.")
        lines.append("")
    render_data_health(lines, payload)
    render_audit_snapshot(lines, payload)
    render_instrumentation_audit(lines, payload)
    render_calendars(lines, payload)
    render_engine_snapshot(lines, payload)
    render_underwriting_layers(lines, payload)
    render_research_book(lines, payload)
    render_outcome_diagnostics(lines, payload)
    render_backtest_summary(lines, payload)
    render_portfolio_snapshot(lines, payload)
    render_portfolio_benchmark(lines, payload)
    render_macro_tape(lines, payload)
    render_external_signals(lines, payload)
    render_signal_synthesis(lines, payload)
    lines.append("## Top Decision Cards")
    for card in payload["decision_cards"][:8]:
        price = f"${card['last_price']:.2f}" if card["last_price"] is not None else "n/a"
        five_day = f"{card['five_day_pct']:.2f}%" if card["five_day_pct"] is not None else "n/a"
        consensus = (
            f", consensus {card.get('consensus_manager_count', 0)} funds"
            if card.get("consensus_manager_count")
            else ""
        )
        lines.append(
            f"- **{card['symbol']}** ({card['candidate']}, score {card['score']:.2f}) "
            f"- bucket `{card['bucket']}`, price {price}, 5d {five_day}, "
            f"primary filing value ${card['filing_value']:,.0f}, "
            f"tracked value ${card.get('consensus_value', 0):,.0f}{consensus}, "
            f"signal families {card.get('signal_family_count', 0)}, news hits {card['news_count']}."
        )
        if card.get("top_event_types"):
            lines.append(f"  Catalysts: {', '.join(card['top_event_types'][:3])}.")
        lines.append(f"  Counterargument: {card['counterargument']}")
        lines.append(f"  Falsifier: {card['falsifier']}")
    if not payload["decision_cards"]:
        lines.append("- No watchlist cards generated.")
    lines.append("")
    render_manager_radar(lines, payload, config)
    render_idea_book(lines, payload)
    render_approval_tickets(lines, payload)
    if payload.get("weekly_research"):
        render_weekly_research(lines, payload)
    render_earnings_events(lines, payload)
    lines.append("## Portfolio Activity")
    txs = payload["transactions"]
    if txs:
        for row in txs[:20]:
            symbol = row["symbol"] or "cash"
            lines.append(
                f"- {row['trade_date']}: {row['broker']} {row['action']} {symbol} "
                f"qty {row['quantity']} amount {row['amount']}."
            )
    else:
        lines.append("- No broker transactions imported for the recent lookback window.")
    lines.append("")
    lines.append("## Manager Filing Signal")
    filing = payload["latest_filing"]
    if filing:
        lines.append(
            f"- Primary manager: {config.primary_manager.get('display_name', config.primary_manager['name'])}. "
            f"Latest stored filing: [{filing['form']} {filing['accession_number']}]({filing['url']}) "
            f"filed {filing['filing_date']} for report date {filing['report_date'] or 'n/a'}."
        )
        lines.append("- 13F data is delayed and should be treated as a public filing signal, not live trading data.")
    else:
        lines.append("- No manager filings stored yet. Run `python3 -m invest filings --manager situational-awareness`.")
    lines.append("")
    lines.append("## News And Catalysts")
    catalyst_signals = payload.get("catalyst_signals") or []
    if catalyst_signals:
        lines.append("- Top catalyst signals: " + ", ".join(
            f"{row['symbol']} {row['event_score']:.1f} ({', '.join(row.get('event_types', [])[:2])})"
            for row in catalyst_signals[:8]
        ) + ".")
    news = payload["news"]
    if news:
        for item in news[:10]:
            date_text = item["published_at"] or "date unavailable"
            lines.append(
                f"- [{item['title']}]({item['url']}) - {item['source']}, {date_text}; "
                f"{item.get('event_label', 'General news')} / {item.get('source_tier', 'general')}."
            )
    else:
        lines.append("- No news items stored yet, or RSS fetch failed.")
    lines.append("")
    lines.append("## Anti-Bias Check")
    lines.append("- Do not buy a story because it sounds like the future; require evidence on revenue, margins, financing, and timing.")
    lines.append("- Treat crowded AI infrastructure trades as fragile when valuation assumes perfect execution.")
    lines.append("- Re-check whether each candidate still has asymmetric upside after the latest move, not before it.")
    lines.append("")
    lines.append("## Tomorrow Queue")
    lines.append("- Re-run broker sync, filings, and brief before trading decisions.")
    if payload.get("stale_vanguard"):
        lines.append("- Refresh Vanguard exports if stale.")
    lines.append("- Compare any proposed trade against the idea trigger, counterargument, falsifier, and current macro regime.")
    return "\n".join(lines) + "\n"


def render_portfolio_snapshot(lines: list[str], payload: dict[str, Any]) -> None:
    portfolio = payload.get("portfolio") or {}
    lines.append("## Geoffrey Woo Portfolio Snapshot")
    if not portfolio.get("position_count"):
        lines.append("- No current public-stock positions imported. Import IBKR Flex positions first.")
        lines.append("")
        return
    lines.append(
        f"- Net exposure ${portfolio['net_exposure']:,.0f}; gross exposure ${portfolio['gross_exposure']:,.0f}; "
        f"{portfolio['symbol_count']} symbols across {portfolio['position_count']} broker/account rows."
    )
    if portfolio.get("by_broker"):
        broker_text = ", ".join(f"{row['broker']} ${row['market_value']:,.0f}" for row in portfolio["by_broker"][:4])
        lines.append(f"- Broker exposure: {broker_text}.")
    if portfolio.get("by_bucket"):
        bucket_text = ", ".join(
            f"{row['bucket']} {(float(row.get('comparison_weight', row.get('ex_cash_weight', row.get('weight') or 0)) or 0) * 100):.1f}%"
            for row in portfolio["by_bucket"][:5]
            if row.get("bucket") != "cash_reserves"
        )
        lines.append(f"- Thesis buckets, ex-cash: {bucket_text}.")
    if portfolio.get("by_symbol"):
        top_text = ", ".join(
            f"{row['symbol']} ${row['market_value']:,.0f}" for row in portfolio["by_symbol"][:6]
        )
        lines.append(f"- Top positions: {top_text}.")
    if portfolio.get("unmapped_symbols"):
        lines.append(f"- Unmapped symbols to classify: {', '.join(portfolio['unmapped_symbols'][:12])}.")
    lines.append("")


def render_data_health(lines: list[str], payload: dict[str, Any]) -> None:
    health = payload.get("data_health") or {}
    sources = health.get("sources") or []
    if not sources:
        return
    lines.append("## Data Health")
    lines.append(
        f"- Recommendation posture: **{health.get('recommendation_posture', 'normal')}**. "
        f"{health.get('summary', '')}"
    )
    for source in sources:
        lines.append(
            f"- {source.get('label', source.get('source', 'Source'))}: "
            f"{source.get('status', 'unknown')} - {source.get('detail', '')}"
        )
    lines.append("")


def render_audit_snapshot(lines: list[str], payload: dict[str, Any]) -> None:
    audit = payload.get("audit") or {}
    if not audit:
        return
    lines.append("## Audit And Engine Health")
    lines.append(
        f"- Overall status: **{audit.get('overall_status', 'unknown')}**; "
        f"engine {audit.get('engine_version', 'unknown')}; methodology {audit.get('methodology_version', 'unknown')}."
    )
    engine_health = audit.get("engine_health") or {}
    lines.append(
        f"- Learning status: {engine_health.get('learning_status', 'unknown')}; "
        f"{engine_health.get('feature_count', 0)} features; "
        f"{engine_health.get('paper_trade_count', 0)} paper trades tracked with proxy fills."
    )
    gaps = audit.get("data_gaps") or []
    if gaps:
        lines.append("- Gaps: " + "; ".join(f"{row.get('label')}: {row.get('status')}" for row in gaps[:6]) + ".")
    else:
        lines.append("- No audit gaps detected in the current public-safe run.")
    lines.append("")


def render_instrumentation_audit(lines: list[str], payload: dict[str, Any]) -> None:
    audit = payload.get("instrumentation_audit") or {}
    if not audit:
        return
    lines.append("## Number Wiring Audit")
    lines.append(
        f"- Status: **{audit.get('status', 'unknown')}**; "
        f"{audit.get('check_count', 0)} invariant checks; "
        f"{audit.get('failure_count', 0)} failures."
    )
    provenance = audit.get("prediction_provenance") or {}
    lines.append(
        f"- Prediction provenance: {provenance.get('policy', 'unknown')}; "
        f"ML active: {provenance.get('ml_model_active', False)}; "
        f"completed labels: {provenance.get('completed_backtest_label_count', 0)}."
    )
    for failure in audit.get("failures", [])[:8]:
        lines.append(
            f"- {failure.get('name', 'check')}: observed {failure.get('observed', 'n/a')} "
            f"expected {failure.get('expected', failure.get('expected_max', 'n/a'))}."
        )
    lines.append("")


def render_calendars(lines: list[str], payload: dict[str, Any]) -> None:
    calendars = payload.get("calendars") or {}
    if not calendars:
        return
    filings = calendars.get("filings_13f") or {}
    earnings = calendars.get("earnings") or {}
    cycle = filings.get("current_cycle") or {}
    lines.append("## Calendars")
    if cycle:
        lines.append(
            f"- 13F cycle: {cycle.get('label', '')} quarter-end {cycle.get('quarter_end', '')}; "
            f"deadline {cycle.get('deadline', '')}; filed {filings.get('filed_count', 0)}/"
            f"{filings.get('manager_count', 0)} managers."
        )
    if earnings.get("events"):
        lines.append("- Earnings/events: " + ", ".join(
            f"{row.get('symbol')} {row.get('event_date')} ({row.get('risk_window', 'unknown')})"
            for row in earnings.get("events", [])[:8]
        ) + ".")
    else:
        lines.append("- No earnings events in the current calendar snapshot.")
    lines.append("")


def render_engine_snapshot(lines: list[str], payload: dict[str, Any]) -> None:
    engine = payload.get("engine") or {}
    paper = payload.get("paper_portfolio") or {}
    if not engine:
        return
    lines.append("## Recommendation Engine")
    lines.append(
        f"- Policy: {engine.get('version', 'unknown')}; objective {engine.get('objective', '')}; "
        f"mode {engine.get('mode', '')}; target-weight engine."
    )
    learning = engine.get("learning") or {}
    lines.append(f"- Learning: {learning.get('status', 'unknown')} with {learning.get('outcome_count', 0)} completed outcomes.")
    ranked = engine.get("ranked_candidates") or []
    if ranked:
        lines.append("- Top expected-return ranks: " + ", ".join(
            f"{row.get('symbol')} {row.get('expected_return_rank_score', 0):.1f}"
            for row in ranked[:8]
        ) + ".")
    metrics = paper.get("metrics") or {}
    lines.append(f"- Paper trading: {metrics.get('paper_trade_count', 0)} trades tracked under next-close proxy fill policy.")
    lines.append("")


def render_research_book(lines: list[str], payload: dict[str, Any]) -> None:
    research = payload.get("research_book") or {}
    if not research:
        return
    lines.append("## Research Book")
    lines.append(
        f"- Version: {research.get('version', 'unknown')}; "
        f"{research.get('item_count', 0)} tickers ranked for {research.get('horizon', '3-12m')} forward return."
    )
    for item in (research.get("items") or [])[:8]:
        lines.append(
            f"- **{item.get('symbol', '')}** #{item.get('rank', '')} "
            f"{item.get('verdict', 'study')} - risk-adjusted expected return "
            f"{item.get('risk_adjusted_expected_return', 0):.2f}%; "
            f"bull/base/bear {item.get('bull_return_12m', 0):.1f}/"
            f"{item.get('base_return_12m', 0):.1f}/"
            f"{item.get('bear_return_12m', 0):.1f}%."
        )
        lines.append(f"  Thesis: {item.get('thesis_summary', '')}")
        if item.get("company_reason"):
            lines.append(f"  Company: {item.get('company_reason', '')}")
        if item.get("sector_reason"):
            lines.append(f"  Sector: {item.get('sector_reason', '')}")
        lines.append(f"  Catalyst: {item.get('catalyst_clock', '')}")
    lines.append("")


def render_underwriting_layers(lines: list[str], payload: dict[str, Any]) -> None:
    company = payload.get("company_underwriting") or {}
    sector = payload.get("sector_underwriting") or {}
    if not company and not sector:
        return
    lines.append("## Bottom-Up Underwriting")
    if company:
        lines.append(
            f"- Company layer: {company.get('item_count', 0)} tickers; "
            f"{company.get('review_count', 0)} require deeper review before adding size."
        )
        leaders = sorted(company.get("items") or [], key=lambda row: row.get("company_underwriting_score", 0), reverse=True)[:5]
        if leaders:
            lines.append("- Top company scores: " + ", ".join(
                f"{row.get('symbol')} {row.get('company_underwriting_score', 0):.1f}"
                for row in leaders
            ) + ".")
    if sector:
        leaders = sorted(sector.get("items") or [], key=lambda row: row.get("sector_setup_score", 0), reverse=True)[:5]
        if leaders:
            lines.append("- Sector setup: " + ", ".join(
                f"{row.get('label', row.get('bucket'))} {row.get('sector_setup_score', 0):.1f}"
                for row in leaders
            ) + ".")
    lines.append("")


def render_outcome_diagnostics(lines: list[str], payload: dict[str, Any]) -> None:
    diagnostics = payload.get("outcome_diagnostics") or {}
    if not diagnostics:
        return
    lines.append("## Outcome Diagnostics")
    lines.append(
        f"- Status: {diagnostics.get('status', 'unknown')}; "
        f"{diagnostics.get('current_training_example_count', 0)} current training examples; "
        f"{diagnostics.get('completed_outcome_count', 0)} completed forward outcomes; "
        f"{diagnostics.get('pending_outcome_count', 0)} pending."
    )
    maturity = diagnostics.get("label_maturity") or {}
    if maturity:
        ready = "ready" if maturity.get("learning_ready") else "not ready"
        lines.append(
            f"- Learning readiness: {ready}; "
            f"{maturity.get('completed_long_horizon_count', 0)}/"
            f"{maturity.get('minimum_long_horizon_required', 0)} required 1-12 month labels completed; "
            f"{maturity.get('additional_long_horizon_needed', 0)} more needed; "
            f"{maturity.get('pending_outcome_count', diagnostics.get('pending_outcome_count', 0))} pending labels."
        )
    projection = diagnostics.get("learning_readiness_projection") or {}
    if projection:
        projection_line = (
            "- Learning unlock projection: "
            f"{projection.get('projected_long_horizon_count_30d', 0)}/"
            f"{projection.get('minimum_long_horizon_required', 0)} labels after 30-day due window; "
            f"{projection.get('projected_additional_needed_30d', 0)} more still needed"
        )
        if projection.get("next_learning_label_due_date"):
            projection_line += (
                f"; next learning due {projection.get('next_learning_label_due_date')} adds "
                f"{projection.get('next_learning_label_due_count', 0)} labels -> "
                f"{projection.get('projected_long_horizon_count_next_learning_label', 0)}/"
                f"{projection.get('minimum_long_horizon_required', 0)}"
            )
        if projection.get("estimated_learning_ready_date"):
            projection_line += (
                f"; estimated ready {projection.get('estimated_learning_ready_date')} at "
                f"{projection.get('estimated_learning_ready_projected_count', 0)}/"
                f"{projection.get('minimum_long_horizon_required', 0)}"
            )
        elif not projection.get("learning_ready_with_scheduled_pending_labels"):
            projection_line += "; queued learning labels do not yet cover the readiness threshold"
        lines.append(projection_line + ".")
    external_projection = diagnostics.get("external_learning_readiness_projection") or {}
    if external_projection:
        external_line = (
            "- External-signal learning projection: "
            f"{external_projection.get('projected_external_long_horizon_count_all_scheduled', 0)}/"
            f"{external_projection.get('minimum_external_long_horizon_required', 0)} "
            "externally covered labels after all scheduled labels; "
            f"{external_projection.get('projected_external_additional_needed_all_scheduled', 0)} more needed"
        )
        if external_projection.get("next_external_learning_label_due_date"):
            external_line += (
                f"; next external label due {external_projection.get('next_external_learning_label_due_date')} adds "
                f"{external_projection.get('next_external_learning_label_due_count', 0)} labels"
            )
        if external_projection.get("estimated_external_learning_ready_date"):
            external_line += f"; estimated ready {external_projection.get('estimated_external_learning_ready_date')}"
        elif not external_projection.get("external_learning_ready_with_scheduled_pending_labels"):
            external_line += "; queued external labels do not yet cover the readiness threshold"
        lines.append(external_line + ".")
        if external_projection.get("next_external_fast_label_due_date"):
            lines.append(
                "- External-signal fast check: "
                f"{external_projection.get('next_external_fast_label_due_count', 0)} 5-day labels due "
                f"{external_projection.get('next_external_fast_label_due_date')}; "
                f"{external_projection.get('external_fast_labels_due_next_30d', 0)} due within 30 days."
            )
    horizon_counts = diagnostics.get("horizon_label_counts") or []
    if horizon_counts:
        lines.append("- Label maturity by horizon: " + ", ".join(
            f"{row.get('horizon')}: {row.get('completed_count', 0)} complete / "
            f"{row.get('pending_count', 0)} pending / {row.get('missing_price_count', 0)} missing"
            for row in horizon_counts[:5]
        ) + ".")
    schedule = diagnostics.get("pending_label_schedule") or {}
    if schedule:
        next_label = schedule.get("next_label") or {}
        next_learning = schedule.get("next_learning_label") or {}
        details = []
        if next_label:
            details.append(
                f"next label {next_label.get('horizon')} {pending_label_due_phrase(next_label)} "
                f"({next_label.get('due_count', 1)} labels)"
            )
        if next_learning:
            details.append(
                f"next learning-eligible label {next_learning.get('horizon')} {pending_label_due_phrase(next_learning)} "
                f"({next_learning.get('due_count', 1)} labels)"
            )
        if schedule.get("overdue_label_count"):
            details.append(f"{schedule.get('overdue_label_count')} overdue labels")
        if schedule.get("overdue_learning_label_count"):
            details.append(f"{schedule.get('overdue_learning_label_count')} overdue learning-eligible labels")
        due_windows = pending_window_phrase(schedule.get("due_window_counts") or {}, "all labels")
        if due_windows:
            details.append(due_windows)
        learning_windows = pending_window_phrase(schedule.get("learning_due_window_counts") or {}, "learning labels")
        if learning_windows:
            details.append(learning_windows)
        if details:
            lines.append("- Pending label schedule: " + "; ".join(details) + ".")
    calibration = diagnostics.get("calibration") or {}
    calibration_line = (
        f"- Calibration: {calibration.get('status', 'unknown')} - "
        f"{calibration.get('message', 'tracking expected vs realized returns')}"
    )
    calibration_metrics = calibration_metric_phrase(calibration)
    if calibration_metrics:
        calibration_line += f"; {calibration_metrics}"
    lines.append(calibration_line + ".")
    lines.append("")


def pending_label_due_phrase(label: dict[str, Any]) -> str:
    due_date = label.get("due_date", "unknown date")
    days = label.get("days_until_due")
    if days is None:
        return f"due {due_date}"
    if days < 0:
        return f"due {due_date}, overdue by {abs(days)} days"
    if days == 0:
        return f"due {due_date}, today"
    return f"due {due_date}, in {days} days"


def pending_window_phrase(counts: dict[str, Any], label: str) -> str:
    if not counts:
        return ""
    return (
        f"{label}: {counts.get('due_next_7d', 0)} due within 7 days, "
        f"{counts.get('due_next_30d', 0)} due within 30 days"
    )


def render_backtest_summary(lines: list[str], payload: dict[str, Any]) -> None:
    backtest = payload.get("backtest") or {}
    if not backtest:
        return
    lines.append("## Backtest And Calibration")
    lines.append(
        f"- Status: {backtest.get('status', 'unknown')}; "
        f"{backtest.get('trial_count', 0)} recommendation trials; "
        f"{backtest.get('completed_outcome_count', 0)} completed labels; "
        f"{backtest.get('pending_outcome_count', 0)} pending labels."
    )
    horizons = backtest.get("horizons") or []
    if horizons:
        lines.append("- Horizon hit rates: " + ", ".join(
            f"{row.get('horizon')}: "
            f"{row.get('hit_rate') if row.get('hit_rate') is not None else 'pending'}"
            for row in horizons
        ) + ".")
    calibration = backtest.get("calibration") or {}
    calibration_metrics = calibration_metric_phrase(calibration) or "mean error n/a"
    lines.append(f"- Expected-vs-realized: {calibration.get('status', 'unknown')}; {calibration_metrics}.")
    calibration_bands = calibration_band_phrases(calibration.get("buckets") or [])
    if calibration_bands:
        lines.append("- Calibration bands: " + "; ".join(calibration_bands) + ".")
    priority_bucket = calibration_priority_phrase(calibration.get("priority_bucket") or {})
    if priority_bucket:
        lines.append(f"- Calibration priority: {priority_bucket}.")
    external_status_bands = calibration_band_phrases(backtest.get("by_external_feed_status") or [])
    if external_status_bands:
        lines.append("- External feed status outcomes: " + "; ".join(external_status_bands) + ".")
    external_coverage_bands = calibration_band_phrases(backtest.get("by_external_coverage") or [])
    if external_coverage_bands:
        lines.append("- External coverage outcomes: " + "; ".join(external_coverage_bands) + ".")
    external_alignment_bands = calibration_band_phrases(backtest.get("by_external_alignment") or [])
    if external_alignment_bands:
        lines.append("- External alignment outcomes: " + "; ".join(external_alignment_bands) + ".")
    alignment_due_dates = backtest.get("pending_external_alignment_due_dates") or []
    if alignment_due_dates:
        lines.append("- Pending external alignment due dates: " + "; ".join(
            f"{row.get('due_date')}: {row.get('due_count', 0)} labels "
            f"({row.get('conflict_count', 0)} conflict, {row.get('aligned_count', 0)} aligned)"
            for row in alignment_due_dates[:5]
        ) + ".")
    gap_queue = backtest.get("pending_external_coverage_gap_queue") or []
    gap_count = backtest.get("pending_external_coverage_gap_count", len(gap_queue))
    gap_plan = backtest.get("pending_external_coverage_gap_plan") or {}
    priority_rows = gap_plan.get("priority_rows") or []
    if priority_rows:
        policy = priority_rows[0].get("external_coverage_backfill_policy") or "decision_time_only"
        projected = gap_plan.get("projected_external_long_horizon_count_after_priority_backfill")
        required = gap_plan.get("minimum_external_long_horizon_required")
        projected_ready = "ready" if gap_plan.get("external_learning_ready_after_priority_backfill") else "not ready"
        check_count = gap_plan.get("priority_acceptance_check_count") or len(priority_rows[0].get("external_coverage_acceptance_checks") or [])
        open_check_count = gap_plan.get("priority_open_acceptance_check_count", check_count)
        lines.append(
            f"- External coverage gap priority: {gap_plan.get('additional_external_coverage_needed', len(priority_rows))} "
            f"labels needed; prioritize "
            + ", ".join(
                f"{row.get('symbol')} {row.get('horizon')} due {row.get('due_date')} ({row.get('external_coverage_gap_id')})"
                for row in priority_rows[:5]
            )
            + f"; backfill policy {policy}; {open_check_count}/{check_count} acceptance checks open; "
            f"projected {projected}/{required} external labels, {projected_ready}."
        )
    if gap_queue:
        lines.append(
            f"- External coverage gap queue: {gap_count} long-horizon labels missing external coverage; "
            + ", ".join(
                f"{row.get('symbol')} {row.get('horizon')} due {row.get('due_date')}"
                for row in gap_queue[:5]
            )
            + "."
        )
    if backtest.get("top_wins"):
        lines.append("- Top wins: " + ", ".join(
            f"{row.get('symbol')} {row.get('horizon')} {row.get('decision_forward_return_pct')}%"
            for row in backtest.get("top_wins", [])[:5]
        ) + ".")
    if backtest.get("top_losses"):
        lines.append("- Top losses: " + ", ".join(
            f"{row.get('symbol')} {row.get('horizon')} {row.get('decision_forward_return_pct')}%"
            for row in backtest.get("top_losses", [])[:5]
        ) + ".")
    lines.append("")


def calibration_metric_phrase(calibration: dict[str, Any]) -> str:
    details = []
    if calibration.get("mean_error") is not None:
        details.append(f"mean error {calibration.get('mean_error')}")
    if calibration.get("mean_absolute_error") is not None:
        details.append(f"mean absolute error {calibration.get('mean_absolute_error')}")
    if calibration.get("minimum_calibration_samples"):
        details.append(
            f"samples {calibration.get('sample_count', 0)}/"
            f"{calibration.get('minimum_calibration_samples')}; "
            f"{calibration.get('additional_samples_needed', 0)} more before recalibration"
        )
    if "underprediction_count" in calibration or "overprediction_count" in calibration:
        details.append(
            f"underpredicted {calibration.get('underprediction_count', 0)}; "
            f"overpredicted {calibration.get('overprediction_count', 0)}"
        )
    return "; ".join(details)


def calibration_band_phrases(buckets: list[dict[str, Any]]) -> list[str]:
    phrases = []
    for bucket in buckets[:3]:
        label = bucket.get("key", "unknown")
        count = bucket.get("completed_count", 0)
        metrics = calibration_metric_phrase(bucket)
        if metrics:
            phrases.append(f"{label} ({count} labels, {metrics})")
        else:
            phrases.append(f"{label} ({count} labels)")
    return phrases


def calibration_priority_phrase(bucket: dict[str, Any]) -> str:
    if not bucket or not bucket.get("key"):
        return ""
    return (
        f"{bucket.get('key')} has highest absolute error "
        f"{bucket.get('mean_absolute_error', 'n/a')} across {bucket.get('completed_count', 0)} labels "
        f"({bucket.get('bias', 'unknown')} bias)"
    )


def render_macro_tape(lines: list[str], payload: dict[str, Any]) -> None:
    macro = payload.get("macro") or {}
    if not macro:
        return
    lines.append("## Macro Tape")
    scores = macro.get("scores", {})
    lines.append(
        f"- Regime: **{macro.get('regime', 'unknown')}** "
        f"(AI {scores.get('ai_momentum', 0):.2f}%, risk {scores.get('risk_momentum', 0):.2f}%, "
        f"rates {scores.get('rates_move', 0):.2f}%, dollar {scores.get('dollar_move', 0):.2f}%, "
        f"vol {scores.get('vol_move', 0):.2f}%)."
    )
    tape = [row for row in macro.get("tape", []) if row.get("five_day_pct") is not None]
    leaders = sorted(tape, key=lambda row: row["five_day_pct"], reverse=True)[:4]
    laggards = sorted(tape, key=lambda row: row["five_day_pct"])[:4]
    if leaders:
        lines.append("- Macro leaders: " + ", ".join(f"{row['symbol']} {row['five_day_pct']:.2f}%" for row in leaders) + ".")
    if laggards:
        lines.append("- Macro laggards: " + ", ".join(f"{row['symbol']} {row['five_day_pct']:.2f}%" for row in laggards) + ".")
    for item in macro.get("playbook", [])[:3]:
        lines.append(f"- {item}")
    lines.append("")


def render_portfolio_benchmark(lines: list[str], payload: dict[str, Any]) -> None:
    benchmark = payload.get("portfolio_benchmark") or {}
    if not benchmark:
        return
    primary_label = benchmark.get("primary_label", "3M")
    primary_return = float(benchmark.get("primary_portfolio_return", benchmark.get("portfolio_return_5d", 0)))
    lines.append("## Portfolio Benchmarks")
    lines.append(
        f"- Geoffrey Woo Portfolio {primary_label} ex-cash current-weight price proxy: **{primary_return:.2f}%** "
        f"with {benchmark.get('primary_price_coverage_pct', benchmark.get('price_coverage_pct', 0)):.1f}% priced-weight coverage."
    )
    if benchmark.get("horizon_returns"):
        lines.append("- Return windows, applying ex-cash current weights to trailing symbol moves: " + ", ".join(
            f"{row['label']} {row['portfolio_return']:.2f}%"
            for row in benchmark["horizon_returns"]
        ) + ".")
        analytics = benchmark.get("return_analytics") or {}
        primary = analytics.get("primary") or {}
        if primary:
            lines.append(
                f"- Ex-cash invested-equity proxy for {primary.get('label', benchmark.get('primary_label', 'primary'))}: "
                f"{primary.get('invested_equity_return', primary.get('total_portfolio_return', 0)):.2f}%; "
                f"cash is excluded from this comparison basis."
            )
        lines.append(
            "- These are not verified realized returns, TWR, or IRR; calculating actual performance requires daily account equity and cash flows."
        )
    if benchmark.get("benchmarks"):
        lines.append("- Benchmarks: " + ", ".join(
            f"{row['name']} {row['return_pct']:.2f}% ({row['active_vs_portfolio']:+.2f} pp active)"
            for row in benchmark["benchmarks"][:6]
        ) + ".")
    if benchmark.get("peer_proxies"):
        lines.append("- Focus-manager public 13F proxy returns: " + ", ".join(
            f"{row['manager_name']} {row['proxy_return']:.2f}%"
            for row in benchmark["peer_proxies"][:5]
        ) + ".")
        lines.append("- Peer proxies use priced top disclosed 13F positions only; they are not live fund returns.")
    if benchmark.get("action_queue"):
        lines.append("- Action queue: " + "; ".join(
            f"{row['symbol']} - {row.get('sizing_summary') or row['action']}" for row in benchmark["action_queue"][:5]
        ) + ".")
    lines.append("")


def render_external_signals(lines: list[str], payload: dict[str, Any]) -> None:
    external = payload.get("external_signals") or {}
    if not external:
        return
    lines.append("## External Signal Feeds")
    lines.append(
        f"- Status: **{external.get('status', 'unknown')}**; "
        f"{external.get('signal_count', 0)} normalized signals across "
        f"{external.get('provider_count', 0)} providers."
    )
    statuses = external.get("source_statuses") or []
    if statuses:
        lines.append("- Providers: " + "; ".join(
            f"{row.get('label', row.get('source', 'source'))} {row.get('status', 'unknown')}"
            for row in statuses[:8]
        ) + ".")
    top = external.get("top_signals") or []
    if top:
        lines.append("- Top signals: " + "; ".join(
            f"{row.get('symbol') or 'global'} {row.get('score', 0):+.1f} {row.get('label', '')[:80]}"
            for row in top[:8]
        ) + ".")
    lines.append("")


def render_signal_synthesis(lines: list[str], payload: dict[str, Any]) -> None:
    synthesis = payload.get("signal_synthesis") or {}
    if not synthesis:
        return
    lines.append("## Signal Synthesis")
    lines.append(
        f"- Regime gate: {synthesis.get('regime', 'unknown')}; "
        f"{synthesis.get('confirmed_card_count', 0)} cards have at least two confirming signal families."
    )
    if synthesis.get("dominant_families"):
        lines.append("- Dominant families: " + ", ".join(
            f"{row['family']} {row['count']}" for row in synthesis["dominant_families"][:5]
        ) + ".")
    if synthesis.get("top_catalyst_symbols"):
        lines.append("- Catalyst tape: " + ", ".join(
            f"{row['symbol']} {row['event_score']:.1f}" for row in synthesis["top_catalyst_symbols"][:5]
        ) + ".")
    if synthesis.get("source_mix"):
        lines.append("- Source mix: " + ", ".join(
            f"{row['tier']} {row['count']}" for row in synthesis["source_mix"]
        ) + ".")
    lines.append("")


def render_manager_radar(lines: list[str], payload: dict[str, Any], config: AppConfig) -> None:
    radar = payload.get("manager_radar") or {}
    lines.append("## Hedge Fund Radar")
    stored = radar.get("stored_latest_count", 0)
    total = radar.get("manager_count", 0)
    lines.append(f"- Stored latest filings for {stored}/{total} configured managers.")
    if radar.get("manager_status"):
        for row in radar["manager_status"][:10]:
            lines.append(
                f"- [{row['manager_name']}]({row['url']}): {row['form']} filed {row['filing_date']} "
                f"for report date {row['report_date'] or 'n/a'}."
            )
    else:
        lines.append("- No manager filings stored yet. Run `python3 -m invest filings --manager all`.")
    if radar.get("top_consensus"):
        lines.append("- Consensus common positions: " + ", ".join(
            f"{row['symbol']} ({row['common_manager_count']} funds, ${row['common_value']:,.0f})"
            for row in radar["top_consensus"][:8]
        ) + ".")
    if radar.get("focus_managers"):
        lines.append("### Focus Fund Tracking")
        groups = radar.get("focus_manager_groups") or [{"label": "Focus Managers", "managers": radar["focus_managers"]}]
        for group in groups:
            lines.append(f"#### {group.get('label', 'Focus Managers')}")
            for row in group.get("managers", []):
                if row.get("status") != "ok":
                    lines.append(f"- **{row['manager_name']}**: no latest 13F stored yet.")
                    continue
                top_positions = ", ".join(
                    f"{position.get('symbol') or position.get('issuer')} {position.get('fund_weight', 0) * 100:.1f}%"
                    for position in row.get("top_positions", [])[:5]
                )
                lines.append(
                    f"- **{row['manager_name']}**: symbols {row.get('symbol_coverage_pct', 0):.1f}%, "
                    f"AlloIQ watchlist {row.get('alloiq_watchlist_pct', 0):.1f}%, "
                    f"Geoffrey Woo Portfolio overlap {row.get('default_portfolio_overlap_pct', 0):.1f}%, "
                    f"top-10 concentration {row.get('top10_concentration_pct', 0):.1f}%"
                    f"{'; top positions: ' + top_positions if top_positions else ''}."
                )
    if radar.get("top_adds"):
        lines.append("- Largest aggregate adds: " + ", ".join(
            f"{row['symbol']} ${row['delta_value']:,.0f}" for row in radar["top_adds"][:6]
        ) + ".")
    option_watch = [row for row in radar.get("option_watch", []) if row.get("put_value") or row.get("call_value")]
    if option_watch:
        lines.append("- Option watch: " + ", ".join(
            f"{row['symbol']} calls ${row['call_value']:,.0f}/puts ${row['put_value']:,.0f}"
            for row in option_watch[:6]
        ) + ".")
    lines.append("- 13F data is delayed and should be treated as a public filing signal, not live manager trading.")
    lines.append("")


def render_idea_book(lines: list[str], payload: dict[str, Any]) -> None:
    lines.append("## Idea Book")
    ideas = payload.get("ideas", [])
    if not ideas:
        lines.append("- No ideas generated. Add fresh broker positions, manager filings, or watchlist symbols.")
        lines.append("")
        return
    for idea in ideas[:8]:
        lines.append(f"- **{idea['symbol']}** ({idea['type']}, score {idea['score']:.2f}) - {idea['setup']}")
        lines.append(f"  Evidence: {idea['evidence']}")
        if idea.get("signal_families"):
            lines.append(f"  Signal families: {', '.join(idea['signal_families'])}")
        lines.append(f"  Trigger: {idea['trigger']}")
        lines.append(f"  Risk/falsifier: {idea['risk']} Falsifier: {idea['falsifier']}")
    lines.append("")


def render_approval_tickets(lines: list[str], payload: dict[str, Any]) -> None:
    tickets = payload.get("approval_tickets") or []
    if not tickets:
        return
    lines.append("## Approval Tickets")
    lines.append("- Approval-only portfolio-weight tickets with current weight, target weight, and sizing basis. No broker order is placed by AlloIQ.")
    for ticket in tickets[:10]:
        lines.append(
            f"- **{ticket.get('symbol', '')}** `{ticket.get('ticket_id', '')}`: "
            f"{ticket.get('trade_action', 'study')} {ticket.get('recommended_delta_weight', 0):+,.2%}; "
            f"target {ticket.get('target_weight', 0):.2%}; confidence {ticket.get('confidence', 0)}/100."
        )
        if ticket.get("constraint_notes"):
            lines.append(f"  Constraints: {'; '.join(ticket['constraint_notes'][:3])}.")
        lines.append(f"  Rationale: {ticket.get('rationale') or ticket.get('action') or ''}")
    lines.append("")


def render_weekly_research(lines: list[str], payload: dict[str, Any]) -> None:
    research = payload.get("weekly_research") or {}
    ideas = research.get("ideas", [])
    if not ideas:
        return
    lines.append("## Weekly Idea Research")
    lines.append(f"- Method: {research.get('method', 'Rank ideas by signal density and falsifiability')}.")
    for idea in ideas[:10]:
        lines.append(
            f"- **{idea['symbol']}** ({idea.get('type', 'research')}, score {idea.get('score', 0):.2f}) - "
            f"{idea.get('recommended_action', 'Refresh the thesis.')}"
        )
        lines.append(f"  Setup: {idea.get('setup', '')}")
        lines.append(f"  Trigger: {idea.get('trigger', '')}")
        lines.append(f"  Risk/falsifier: {idea.get('risk', '')} Falsifier: {idea.get('falsifier', '')}")
        questions = idea.get("research_questions") or []
        if questions:
            lines.append("  Questions: " + " | ".join(questions[:3]))
    lines.append("")


def render_earnings_events(lines: list[str], payload: dict[str, Any]) -> None:
    events = payload.get("earnings_events") or []
    if not events:
        return
    lines.append("## Earnings And Filing Catalysts")
    for event in events[:12]:
        when = event.get("event_date") or "date unavailable"
        days = event.get("days_until")
        days_text = "today" if days == 0 else f"{days:+d} days" if days is not None else "timing unknown"
        lines.append(
            f"- **{event.get('symbol', '')}** {event.get('event_type', 'event')} on {when} "
            f"({days_text}): {event.get('title', '')}"
        )
    lines.append("")


def build_weekly_research(
    as_of: date,
    ideas: list[dict[str, Any]],
    cards: list[dict[str, Any]],
    portfolio_benchmark: dict[str, Any],
    macro: dict[str, Any],
) -> dict[str, Any]:
    cards_by_symbol = proxy_index(cards)
    actions_by_symbol = proxy_index(portfolio_benchmark.get("action_queue", []))
    source = ideas or [
        {
            "symbol": card["symbol"],
            "type": card.get("candidate", "research"),
            "bucket": card.get("bucket", "unmapped"),
            "score": card.get("score", 0),
            "setup": "High-scoring watchlist signal needs a full weekly underwriting pass.",
            "evidence": f"Score {card.get('score', 0):.2f}; {card.get('signal_family_count', 0)} signal families.",
            "trigger": card.get("trigger", "Define a public catalyst and variant view."),
            "risk": card.get("counterargument", ""),
            "falsifier": card.get("falsifier", ""),
            "signal_families": card.get("signal_families", []),
            "event_types": card.get("top_event_types", []),
        }
        for card in sorted(cards, key=lambda row: row.get("score", 0), reverse=True)[:12]
    ]
    research_ideas = []
    for rank, idea in enumerate(source[:15], start=1):
        symbol = str(idea.get("symbol", ""))
        card = proxied_lookup(cards_by_symbol, symbol, {})
        action = proxied_lookup(actions_by_symbol, symbol, {})
        research_ideas.append(
            {
                "rank": rank,
                "symbol": symbol,
                "type": idea.get("type", "research"),
                "bucket": idea.get("bucket") or card.get("bucket", "unmapped"),
                "score": round(float(idea.get("score") or card.get("score") or 0), 2),
                "setup": idea.get("setup", ""),
                "evidence": idea.get("evidence", ""),
                "recommended_action": action.get("action", "Build or refresh the primary thesis before changing size."),
                "trade_action": action.get("trade_action", "study"),
                "portfolio_weight": action.get("portfolio_weight", 0),
                "recommended_delta_weight": action.get("recommended_delta_weight", 0),
                "target_weight": action.get("target_weight", action.get("post_action_weight", 0)),
                "trigger": idea.get("trigger", ""),
                "risk": idea.get("risk", ""),
                "falsifier": idea.get("falsifier", ""),
                "signal_families": idea.get("signal_families", card.get("signal_families", [])),
                "event_types": idea.get("event_types", card.get("top_event_types", [])),
                "research_questions": weekly_research_questions(card, macro),
            }
        )
    return {
        "as_of": as_of.isoformat(),
        "title": "Weekly Idea Research",
        "method": "Ranks ideas by signal density, manager overlap, catalysts, portfolio context, and falsifiability.",
        "ideas": research_ideas,
    }


def build_approval_tickets(
    as_of: date,
    session: str,
    portfolio: dict[str, Any],
    portfolio_benchmark: dict[str, Any],
    cards: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    cards_by_symbol = proxy_index(cards)
    gross_exposure = float(portfolio.get("gross_exposure") or 0)
    tickets: list[dict[str, Any]] = []
    for action in portfolio_benchmark.get("action_queue", []):
        symbol = str(action.get("symbol", "")).upper()
        if not symbol:
            continue
        card = proxied_lookup(cards_by_symbol, symbol, {})
        delta = float(action.get("recommended_delta_weight") or 0)
        last_price = float(action.get("last_price") or card.get("last_price") or 0)
        estimated_notional = gross_exposure * delta if gross_exposure else None
        estimated_shares = estimated_notional / last_price if estimated_notional is not None and last_price else None
        ticket = {
            "ticket_id": stable_id([as_of.isoformat(), session, symbol, action.get("trade_action"), action.get("target_weight")]),
            "status": "open",
            "approval_required": True,
            "order_execution": "none",
            "as_of": as_of.isoformat(),
            "session": session,
            "symbol": symbol,
            "bucket": action.get("bucket") or card.get("bucket", "unmapped"),
            "model_policy_version": action.get("model_policy_version", ""),
            "trade_action": action.get("trade_action", "study"),
            "current_weight": action.get("current_weight", action.get("portfolio_weight", 0)),
            "recommended_delta_weight": action.get("recommended_delta_weight", 0),
            "post_action_weight": action.get("post_action_weight", action.get("portfolio_weight", 0)),
            "trade_target_weight": action.get("trade_target_weight", action.get("post_action_weight", 0)),
            "target_weight": action.get("target_weight", action.get("post_action_weight", 0)),
            "model_target_weight": action.get("model_target_weight", action.get("target_weight", 0)),
            "max_allowed_weight": action.get("max_allowed_weight"),
            "risk_adjusted_expected_return": action.get("risk_adjusted_expected_return"),
            "probability_weighted_return": action.get("probability_weighted_return"),
            "bull_return_12m": action.get("bull_return_12m"),
            "base_return_12m": action.get("base_return_12m"),
            "bear_return_12m": action.get("bear_return_12m"),
            "catalyst_clock": action.get("catalyst_clock", ""),
            "active_constraints": action.get("active_constraints", []),
            "why_this_size": action.get("why_this_size", action.get("sizing_rationale", "")),
            "increase_size_if": action.get("increase_size_if", ""),
            "decrease_size_if": action.get("decrease_size_if", ""),
            "confidence": action.get("confidence", 0),
            "risk_flags": action.get("risk_flags", []),
            "constraint_notes": action.get("constraint_notes", []),
            "funding_source": action.get("funding_source", ""),
            "funding_counterpart_symbols": action.get("funding_counterpart_symbols", []),
            "company_underwriting_score": action.get("company_underwriting_score"),
            "sector_setup_score": action.get("sector_setup_score"),
            "company_reason": action.get("company_reason", ""),
            "sector_reason": action.get("sector_reason", ""),
            "tertiary_signal_summary": action.get("tertiary_signal_summary", ""),
            "review_required": bool(action.get("review_required", False)),
            "review_status": action.get("review_status", ""),
            "review_reason": action.get("review_reason", ""),
            "decision_stack": action.get("decision_stack", {}),
            "rationale": action.get("why") or action.get("action") or "",
            "trigger": action.get("trigger") or card.get("trigger") or "",
            "risk": action.get("risk") or card.get("counterargument") or "",
            "falsifier": action.get("falsifier") or card.get("falsifier") or "",
            "evidence": {
                "score": action.get("score", card.get("score", 0)),
                "signal_family_count": action.get("signal_family_count", card.get("signal_family_count", 0)),
                "event_types": action.get("event_types", card.get("top_event_types", [])),
                "manager_count": card.get("consensus_manager_count", action.get("manager_count", 0)),
            },
            "estimated_notional": round(estimated_notional, 2) if estimated_notional is not None else None,
            "estimated_shares": round(estimated_shares, 4) if estimated_shares is not None else None,
            "sizing_basis": action.get("sizing_basis", "portfolio-weight target delta for the approval-only trade feed"),
        }
        tickets.append(ticket)
    return tickets


def build_recommendation_explanations(
    as_of: date,
    portfolio_benchmark: dict[str, Any],
    research_book: dict[str, Any],
    company_underwriting: dict[str, Any],
    sector_underwriting: dict[str, Any],
) -> list[dict[str, Any]]:
    research_by_symbol = proxy_index((research_book or {}).get("items") or [])
    company_by_symbol = proxy_index((company_underwriting or {}).get("items") or [])
    sector_by_bucket = {
        str(row.get("bucket") or "unmapped"): row
        for row in (sector_underwriting or {}).get("items") or []
    }
    explanations = []
    for action in (portfolio_benchmark or {}).get("action_queue") or []:
        symbol = str(action.get("symbol") or "").upper()
        if not symbol:
            continue
        research = proxied_lookup(research_by_symbol, symbol, {})
        company = proxied_lookup(company_by_symbol, symbol, {})
        sector = sector_by_bucket.get(str(action.get("bucket") or research.get("bucket") or "unmapped"), {})
        explanations.append(
            {
                "as_of": as_of.isoformat(),
                "symbol": symbol,
                "trade_action": action.get("trade_action", "watch"),
                "current_weight": action.get("current_weight", action.get("portfolio_weight", 0)),
                "target_weight": action.get("target_weight", action.get("post_action_weight", 0)),
                "recommended_delta_weight": action.get("recommended_delta_weight", 0),
                "funding_source": action.get("funding_source", ""),
                "funding_counterpart_symbols": action.get("funding_counterpart_symbols", []),
                "risk_adjusted_expected_return": action.get("risk_adjusted_expected_return"),
                "confidence": action.get("confidence", 0),
                "company_reason": action.get("company_reason") or company.get("company_reason", ""),
                "sector_reason": action.get("sector_reason") or sector.get("sector_setup", ""),
                "tertiary_signal_summary": action.get("tertiary_signal_summary") or research.get("tertiary_signal_summary", ""),
                "catalyst_clock": action.get("catalyst_clock") or company.get("catalyst_clock", ""),
                "active_constraints": action.get("active_constraints", []),
                "review_required": bool(action.get("review_required", research.get("review_required", False))),
                "review_reason": action.get("review_reason") or research.get("review_reason", ""),
            }
        )
    return explanations


def build_review_queue(research_book: dict[str, Any], portfolio_benchmark: dict[str, Any]) -> list[dict[str, Any]]:
    action_by_symbol = proxy_index((portfolio_benchmark or {}).get("action_queue") or [])
    queue = []
    for item in (research_book or {}).get("items") or []:
        action = proxied_lookup(action_by_symbol, item.get("symbol"), {})
        constraints = set(action.get("active_constraints") or [])
        if not item.get("review_required") and "bottom_up_evidence_floor" not in constraints and "company_review_required" not in constraints:
            continue
        queue.append(
            {
                "symbol": item.get("symbol", ""),
                "bucket": item.get("bucket", "unmapped"),
                "rank": item.get("rank", 0),
                "verdict": item.get("verdict", "study"),
                "review_required": True,
                "review_reason": action.get("review_reason") or item.get("review_reason", ""),
                "company_underwriting_score": item.get("company_underwriting_score"),
                "sector_setup_score": item.get("sector_setup_score"),
                "risk_adjusted_expected_return": item.get("risk_adjusted_expected_return"),
                "trade_action": action.get("trade_action", "watch"),
                "recommended_delta_weight": action.get("recommended_delta_weight", 0),
                "active_constraints": sorted(constraints),
            }
        )
    return sorted(queue, key=lambda row: (bool(row.get("recommended_delta_weight")), row.get("rank", 9999)), reverse=True)[:20]


def build_data_health(
    portfolio: dict[str, Any],
    manager_radar: dict[str, Any],
    recent_news: list[dict[str, Any]],
    prices: dict[str, dict[str, Decimal]],
    earnings_events: list[dict[str, Any]],
    stale_vanguard: dict[str, Any] | None,
    filing_result_count: bool,
    broker_result_count: int,
    macro: dict[str, Any] | None = None,
    external_signals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    earnings_health = earnings_health_summary(earnings_events)
    sources = [
        {
            "source": "broker_positions",
            "label": "Broker positions",
            "status": "ok" if broker_result_count else "missing",
            "detail": f"{broker_result_count} current broker/account rows available" if broker_result_count else "No current position rows imported.",
        },
        {
            "source": "manager_13f",
            "label": "Manager 13F radar",
            "status": "ok" if manager_radar.get("stored_latest_count") or filing_result_count else "missing",
            "detail": f"{manager_radar.get('stored_latest_count', 0)}/{manager_radar.get('manager_count', 0)} managers have stored filings.",
        },
        {
            "source": "news",
            "label": "News catalysts",
            "status": "ok" if recent_news else "missing",
            "detail": f"{len(recent_news)} recent items classified." if recent_news else "No recent news items available.",
        },
        {
            "source": "prices",
            "label": "Market prices",
            "status": "ok" if prices else "missing",
            "detail": f"{len(prices)} symbols priced." if prices else "No price data returned.",
        },
        {
            "source": "earnings",
            "label": "Earnings calendar",
            "status": earnings_health["status"],
            "detail": (
                f"{earnings_health['event_count']} events; "
                f"{earnings_health['provider_date_count']} forward date candidates; "
                f"{earnings_health['confirmed_count']} confirmed, {earnings_health['estimated_count']} estimated"
                f"{earnings_marker_detail(earnings_health)}."
                if earnings_events
                else "No manual, provider, IR, SEC, or news earnings markers available."
            ),
        },
    ]
    fred_macro = (macro or {}).get("fred_macro") or {}
    if fred_macro and fred_macro.get("status") != "disabled":
        sources.append(
            {
                "source": "fred_macro",
                "label": "FRED macro series",
                "status": fred_macro.get("status", "unknown"),
                "detail": (
                    f"{fred_macro.get('series_count', 0)}/{fred_macro.get('requested_series_count', 0)} "
                    "series available for credit, liquidity, yield-curve, and energy signals."
                ),
            }
        )
    if external_signals:
        sources.append(
            {
                "source": "external_signals",
                "label": "External signal feeds",
                "status": external_signal_health_status(external_signals),
                "detail": external_provider_health_detail(external_signals),
            }
        )
    if stale_vanguard and stale_vanguard.get("is_stale"):
        sources.append(
            {
                "source": "manual_broker_import",
                "label": "Manual broker import",
                "status": "stale",
                "detail": "A manually imported broker source is stale or missing.",
            }
        )
    weak_sources = [row for row in sources if row["status"] in WEAK_SOURCE_STATUSES]
    posture = "reduced_confidence" if weak_sources else "normal"
    if not portfolio.get("position_count"):
        posture = "research_only_until_positions_refresh"
    return {
        "recommendation_posture": posture,
        "summary": (
            "Recommendations are constrained by data freshness and remain approval-only."
            if posture != "normal"
            else "Core scheduled sources are available for this run."
        ),
        "sources": sources,
        "weak_source_count": len(weak_sources),
    }


def earnings_marker_detail(earnings_health: dict[str, Any]) -> str:
    marker_count = int(earnings_health.get("catalyst_marker_count") or 0)
    return f"; {marker_count} catalyst markers" if marker_count else ""


def external_signal_health_status(external_signals: dict[str, Any]) -> str:
    base_status = str(external_signals.get("status") or "unknown")
    provider_statuses = [
        str(row.get("status") or "unknown")
        for row in external_signals.get("source_statuses", [])
        if str(row.get("status") or "") != "disabled"
    ]
    provider_count = int(external_signals.get("provider_count") or len(provider_statuses))
    if base_status in {"missing", "failed", "error"}:
        return base_status
    if provider_count and any(status in WEAK_SOURCE_STATUSES for status in provider_statuses):
        return "limited"
    if provider_count and not any(status == "ok" for status in provider_statuses):
        return "limited"
    return base_status


def build_methodology(
    config: AppConfig,
    session: str,
    data_health: dict[str, Any],
    signal_synthesis: dict[str, Any],
    portfolio_benchmark: dict[str, Any],
    cards: list[dict[str, Any]],
    approval_tickets: list[dict[str, Any]],
    earnings_events: list[dict[str, Any]],
    feature_matrix: dict[str, Any] | None = None,
    research_book: dict[str, Any] | None = None,
    outcome_diagnostics: dict[str, Any] | None = None,
    external_signals: dict[str, Any] | None = None,
    company_underwriting: dict[str, Any] | None = None,
    sector_underwriting: dict[str, Any] | None = None,
) -> dict[str, Any]:
    limits = normalize_public_limits(config.risk_limits)
    score_keys = sorted(
        {
            key
            for card in cards[:20]
            for key in (card.get("score_components") or {}).keys()
        }
    ) or ["manager", "catalyst", "portfolio_fit", "price_action", "option_tilt"]
    action_queue = portfolio_benchmark.get("action_queue") or []
    constraint_flags = sorted(
        {
            str(flag)
            for action in action_queue
            for flag in (action.get("risk_flags", []) + action.get("active_constraints", []))
            if str(flag).strip()
        }
    )
    source_statuses = [
        {
            "source": row.get("source", ""),
            "label": row.get("label", ""),
            "status": row.get("status", "unknown"),
            "detail": row.get("detail", ""),
        }
        for row in data_health.get("sources", [])
    ]
    earnings_provider_settings = config.earnings_provider_settings
    alpha_vantage_key_env = str(earnings_provider_settings.get("alpha_vantage_api_key_env") or "ALPHA_VANTAGE_API_KEY")
    fred_settings = config.macro_fred_settings
    external_settings = config.external_signal_settings
    external_source_statuses = (external_signals or {}).get("source_statuses", [])
    return {
        "version": "2026-05-live-assistant-v1",
        "updated_by_backend": True,
        "session": session,
        "summary": "AlloIQ ranks the AI-max universe company-first, then sector setup, then manager 13F and macro timing, with ML-ready features and forward outcome tracking.",
        "pipeline": {
            "commands": [
                f"python3 -m invest pipeline --kind {kind} --privacy public"
                for kind in ["premarket", "market_open", "intraday", "midday", "market_close", "postmarket", "weekly"]
            ],
            "cadence": [
                {"kind": "premarket", "when": "8:00 AM ET on NYSE trading days", "purpose": "Refresh holdings, filings, overnight catalysts, macro tape, and trade tickets before the open."},
                {"kind": "market_open", "when": "9:30 AM ET on NYSE trading days", "purpose": "Refresh live open prices, position weights, risk moves, and opening-bell add/trim changes."},
                {"kind": "intraday", "when": "10:00 AM, 11:00 AM, 1:00 PM, 2:00 PM, and 3:00 PM ET on NYSE trading days", "purpose": "Refresh hourly price action, catalyst changes, risk gates, and recommendation deltas during market hours."},
                {"kind": "midday", "when": "12:00 PM ET on NYSE trading days", "purpose": "Refresh intraday price moves, catalysts, risk gates, and add/trim tickets for midday trade decisions."},
                {"kind": "market_close", "when": "4:00 PM ET on NYSE trading days", "purpose": "Refresh close-of-session prices, risk changes, and urgent add/trim alerts before the post-close brief."},
                {"kind": "postmarket", "when": "4:30 PM ET on NYSE trading days", "purpose": "Refresh end-of-day price action, attribution, catalysts, and follow-up ticket state."},
                {"kind": "weekly", "when": "Sunday morning ET", "purpose": "Run full idea research, thesis/falsifier review, and weekly opportunity/risk queue."},
            ],
            "steps": [
                {"key": "filings", "label": "SEC 13F refresh", "source": "Public EDGAR manager filings"},
                {"key": "broker_sync", "label": "Private position sync", "source": "Private read-only position feed plus optional manual sleeves"},
                {"key": "news", "label": "Catalyst classification", "source": "Configured RSS/news queries and event rules"},
                {"key": "prices", "label": "Price and return windows", "source": "Public chart data for watchlist and macro symbols"},
                {"key": "fred_macro", "label": "FRED macro stress signals", "source": "No-key FRED CSV series for credit, liquidity, yield curve, and energy pressure"},
                {"key": "earnings", "label": "Earnings and filing windows", "source": "Manual dates, company IR feeds, Alpha Vantage/Nasdaq expected-date providers, SEC company submissions, and news-derived guidance signals"},
                {"key": "external_signals", "label": "External market signal feeds", "source": "Alpha Vantage news sentiment, GDELT global events, SEC company facts/Form 4, EIA power, FINRA short interest, and CFTC COT when available"},
                {"key": "company_underwriting", "label": "Company underwriting", "source": "Bottom-up KPI, valuation, balance-sheet, catalyst, and falsifier evidence"},
                {"key": "sector_underwriting", "label": "Sector underwriting", "source": "AI-max sector KPI templates, power/credit pressure, and sector price context"},
                {"key": "features", "label": "ML-ready feature matrix", "source": "Normalized company, sector, portfolio, manager, catalyst, price, macro, valuation, and event features"},
                {"key": "research_book", "label": "Scenario research book", "source": "Bull/base/bear expected returns anchored by company underwriting and sector setup"},
                {"key": "risk", "label": "Risk and sizing controls", "source": "Configured portfolio limits before publishing tickets"},
                {"key": "privacy", "label": "Public sanitizer", "source": "Weights-only JSON and privacy scan"},
                {"key": "warehouse", "label": "Private warehouse sync", "source": "Neon Postgres run history and decision ledger"},
            ],
            "configured_inputs": {
                "watchlist_symbol_count": len(config.watchlist_symbols),
                "news_query_count": len(config.news_queries),
                "macro_symbol_count": len(config.macro_symbols or DEFAULT_MACRO_SYMBOLS),
                "fred_macro_enabled": bool(fred_settings.get("enabled", True)),
                "fred_series_count": len(fred_settings.get("series") or DEFAULT_FRED_SERIES),
                "manager_count": len(config.data.get("managers", [])),
                "focus_manager_count": len(config.focus_manager_keys),
                "manual_earnings_event_count": len(config.manual_earnings_events),
                "sec_company_marker_count": len(config.earnings_sec_companies),
                "ir_feed_count": len(config.earnings_ir_feeds),
                "alpha_vantage_earnings_enabled": bool(earnings_provider_settings.get("alpha_vantage_enabled", True)),
                "alpha_vantage_api_key_configured": bool(os.environ.get(alpha_vantage_key_env, "").strip()),
                "nasdaq_earnings_enabled": bool(earnings_provider_settings.get("nasdaq_enabled", True)),
                "nasdaq_max_requests": int(earnings_provider_settings.get("nasdaq_max_requests") or 20),
                "nasdaq_retries": int(earnings_provider_settings.get("nasdaq_retries") or 2),
                "external_signal_provider_count": len(external_source_statuses),
                "external_signal_count": int((external_signals or {}).get("signal_count") or 0),
                "company_underwriting_count": int((company_underwriting or {}).get("item_count") or 0),
                "sector_underwriting_count": int((sector_underwriting or {}).get("item_count") or 0),
                "alpha_vantage_news_enabled": bool(external_settings.get("alpha_vantage_news_enabled", True)),
                "gdelt_enabled": bool(external_settings.get("gdelt_enabled", True)),
                "sec_company_data_enabled": bool(external_settings.get("sec_company_enabled", True)),
                "eia_enabled": bool(external_settings.get("eia_enabled", True)),
                "finra_short_interest_enabled": bool(external_settings.get("finra_short_interest_enabled", True)),
                "cftc_cot_enabled": bool(external_settings.get("cftc_cot_enabled", True)),
            },
        },
        "current_run": {
            "recommendation_posture": data_health.get("recommendation_posture", "unknown"),
            "confirmed_card_count": signal_synthesis.get("confirmed_card_count", 0),
            "dominant_signal_families": signal_synthesis.get("dominant_families", []),
            "open_approval_ticket_count": len(approval_tickets),
            "earnings_event_count": len(earnings_events),
            "feature_count": (feature_matrix or {}).get("feature_count", 0),
            "research_item_count": (research_book or {}).get("item_count", 0),
            "training_example_count": (outcome_diagnostics or {}).get("current_training_example_count", 0),
            "external_signal_count": int((external_signals or {}).get("signal_count") or 0),
            "company_underwriting_count": int((company_underwriting or {}).get("item_count") or 0),
            "company_review_count": int((company_underwriting or {}).get("review_count") or 0),
            "sector_underwriting_count": int((sector_underwriting or {}).get("item_count") or 0),
            "source_statuses": source_statuses,
        },
        "scoring_model": {
            "score_components_seen": score_keys,
            "decision_stack_weights": {
                "company_underwriting": 0.60,
                "sector_setup": 0.20,
                "manager_13f": 0.10,
                "macro_timing_risk": 0.10,
            },
            "components": [
                {"key": "company_underwriting", "max_points": 100, "rule": "Growth durability, margin trajectory, cash generation, balance sheet, capex ROIC, revisions, valuation, and falsifiers drive the first 60% of the decision stack."},
                {"key": "sector_setup", "max_points": 100, "rule": "Sector-specific KPI templates and AI-max tailwinds/headwinds drive the next 20% of the decision stack."},
                {"key": "manager_13f", "max_points": 100, "rule": "13F ownership is a tertiary prior and confirmation signal; it cannot create an add without bottom-up evidence."},
                {"key": "macro_timing_risk", "max_points": 100, "rule": "Macro, timing, earnings proximity, and liquidity/rates pressure affect timing, drawdown risk, and max allowed size."},
                {"key": "expected_return", "max_points": None, "rule": "Bull/base/bear 12-month scenarios create a probability-weighted forward return anchored by company and sector work."},
                {"key": "drawdown_risk", "max_points": 100, "rule": "Concentration, bucket exposure, crowding, hard-risk events, put tilt, and extreme price moves penalize target size."},
                {"key": "sizing", "max_points": None, "rule": "Risk-adjusted expected return converts to normalized model target weights; every add must be funded by named trims, cash reserve draw, or both before publication."},
            ],
            "promotion_rules": [
                "Every ranked ticker receives a company underwriting row, sector row, versioned feature row, and scenario research record.",
                "A ticker cannot receive Add unless company underwriting clears the bottom-up evidence floor.",
                "Strong 13F ownership without company evidence produces Watch or Study, not Add.",
                "Company deterioration can trigger Trim even when manager ownership remains strong.",
                "Higher target weights require positive risk-adjusted expected return, company evidence quality, sector support, and timing.",
                "Weak valuation support, stale evidence, crowding, earnings proximity, and drawdown risk reduce target size.",
                "Every trade item carries current weight, post-action target, model target weight, delta, funding source, constraints, catalyst clock, company reason, sector reason, tertiary signals, and falsifier.",
            ],
        },
        "risk_and_sizing": {
            "limits": limits,
            "constraint_flags_observed": constraint_flags,
            "sizing_unit": "portfolio_weight",
            "approval_required": True,
            "order_execution": "none",
            "private_ticket_fields": ["estimated notional", "estimated share count"],
        },
        "public_privacy": {
            "mode": "weights_only",
            "published_artifacts": ["web/data/latest.json", "web/data/reports.json"],
            "stripped_fields": [
                "account identifiers",
                "broker names",
                "share quantities",
                "cost basis",
                "market value",
                "estimated notional",
                "estimated share count",
                "transactions",
                "raw private payloads",
            ],
        },
    }


def normalize_public_limits(raw_limits: dict[str, Any]) -> dict[str, Any]:
    limits = normalize_limits(raw_limits)
    return {
        "max_single_name_weight": float(limits["max_single_name_weight"]),
        "max_bucket_weight": float(limits["max_bucket_weight"]),
        "max_daily_turnover": float(limits["max_daily_turnover"]),
        "max_one_ticket_delta": float(limits["max_one_ticket_delta"]),
        "max_cash_deploy_weight": float(limits["max_cash_deploy_weight"]),
        "min_signal_family_count": int(limits["min_signal_family_count"]),
        "earnings_blackout_days": int(limits["earnings_blackout_days"]),
        "earnings_risk_window_days": int(limits["earnings_risk_window_days"]),
        "no_add_symbol_count": len(limits["no_add_symbols"]),
        "watch_only_symbol_count": len(limits["watch_only_symbols"]),
    }


def weekly_research_questions(card: dict[str, Any], macro: dict[str, Any]) -> list[str]:
    symbol = card.get("symbol", "This name")
    bucket = card.get("bucket", "")
    questions = [
        f"What has to be true for {symbol} to compound from here, and what public evidence would disprove it?",
        "Is the next catalyst business-driven, financing-driven, macro-driven, or just positioning noise?",
        f"Does the current macro regime ({macro.get('regime', 'mixed macro tape')}) improve or reduce the expected value?",
    ]
    if bucket == "neocloud_datacenters":
        questions.append("Are utilization, customer concentration, financing terms, and GPU supply improving together?")
    elif bucket == "power_grid_gas_nuclear":
        questions.append("Are power contracts, interconnection milestones, fuel/input costs, and financing terms aligned?")
    elif bucket == "semis_networking_hbm":
        questions.append("Are backlog, hyperscaler capex, HBM/networking constraints, and margins confirming the thesis?")
    elif bucket == "ai_software_winners":
        questions.append("Is AI usage showing up in retention, pricing, workload expansion, or sales efficiency?")
    return questions


def portfolio_values_by_symbol(portfolio: dict[str, Any]) -> dict[str, Decimal]:
    return {
        str(row.get("symbol", "")).upper(): Decimal(str(row.get("market_value") or 0))
        for row in portfolio.get("by_symbol", [])
        if row.get("symbol")
    }


def configured_ai_universe_symbols(config: AppConfig) -> list[str]:
    symbols = list(config.watchlist_symbols)
    for bucket in config.thesis_buckets.values():
        symbols.extend(str(symbol).upper() for symbol in bucket.get("symbols", []) if str(symbol).strip())
    return unique_symbols(symbols)


def build_research_universe(config: AppConfig, portfolio: dict[str, Any], manager_radar: dict[str, Any], max_symbols: int = 90) -> list[str]:
    symbols = configured_ai_universe_symbols(config)
    symbols.extend(
        str(row.get("symbol") or "").upper()
        for row in portfolio.get("by_symbol", [])
        if row.get("symbol") and not row.get("is_cash")
    )
    for manager in manager_radar.get("focus_managers", []):
        if manager.get("status") != "ok":
            continue
        positions = manager.get("positions") or manager.get("top_positions") or []
        limit = 30 if manager.get("manager_tier") == "tier_1" else 12
        for position in positions[:limit]:
            symbol = str(position.get("symbol") or "").upper()
            bucket = str(position.get("bucket") or config.symbol_to_bucket.get(symbol, "unmapped"))
            if symbol and (bucket != "unmapped" or manager.get("manager_tier") == "tier_1"):
                symbols.append(symbol)
    for row in manager_radar.get("top_consensus", [])[:40]:
        symbol = str(row.get("symbol") or "").upper()
        if symbol and config.symbol_to_bucket.get(symbol, "unmapped") != "unmapped":
            symbols.append(symbol)
    return unique_symbols(symbols)[:max_symbols]


def unique_symbols(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for symbol in symbols:
        normalized = str(symbol).upper().strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def latest_manager_filing(conn: sqlite3.Connection, manager_key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM filings WHERE manager_key = ? ORDER BY filing_date DESC, accession_number DESC LIMIT 1",
        (manager_key,),
    ).fetchone()


def latest_filing_values(conn: sqlite3.Connection, manager_key: str) -> dict[str, Decimal]:
    row = latest_manager_filing(conn, manager_key)
    if not row:
        return {}
    rows = conn.execute(
        """
        SELECT symbol, SUM(CAST(value_usd AS REAL)) AS value_usd
        FROM filing_holdings
        WHERE filing_id = ? AND symbol != '' AND COALESCE(put_call, '') = ''
        GROUP BY symbol
        """,
        (row["id"],),
    ).fetchall()
    return {r["symbol"]: Decimal(str(r["value_usd"] or 0)) for r in rows}


def latest_positions(conn: sqlite3.Connection) -> dict[str, Decimal]:
    rows = conn.execute(
        """
        WITH latest AS (
          SELECT broker, account, symbol, MAX(as_of) AS as_of
          FROM positions
          GROUP BY broker, account, symbol
        )
        SELECT p.symbol, SUM(CAST(p.market_value AS REAL)) AS market_value
        FROM positions p
        JOIN latest l ON p.broker = l.broker AND p.account = l.account AND p.symbol = l.symbol AND p.as_of = l.as_of
        GROUP BY p.symbol
        """
    ).fetchall()
    return {r["symbol"]: Decimal(str(r["market_value"] or 0)) for r in rows}


def transactions_since(conn: sqlite3.Connection, start_date: date) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT broker, account, trade_date, action, symbol, description, quantity, price, amount, fees, currency
        FROM transactions
        WHERE trade_date >= ?
        ORDER BY trade_date DESC, id DESC
        LIMIT 50
        """,
        (start_date.isoformat(),),
    ).fetchall()


def latest_news(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT source, title, url, published_at, summary, query
        FROM news_items
        ORDER BY COALESCE(published_at, imported_at) DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def count_news_by_symbol(news: list[sqlite3.Row], symbols: list[str]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for item in news:
        haystack = f"{item['title']} {item['summary']}".upper()
        for symbol in symbols:
            aliases = news_aliases_for_symbol(symbol)
            if any(alias_matches(haystack, alias) for alias in aliases):
                counts[symbol.upper()] += 1
    return dict(counts)


def build_news_event_signals(news: list[dict[str, Any]], symbols: list[str]) -> dict[str, dict[str, Any]]:
    signals: dict[str, dict[str, Any]] = {}
    for item in news:
        haystack = f"{item.get('title', '')} {item.get('summary', '')}".upper()
        for symbol in symbols:
            normalized = symbol.upper()
            aliases = news_aliases_for_symbol(normalized)
            if not any(alias_matches(haystack, alias) for alias in aliases):
                continue
            data = signals.setdefault(
                normalized,
                {
                    "symbol": normalized,
                    "event_count": 0,
                    "event_score": 0.0,
                    "event_types": Counter(),
                    "source_tiers": Counter(),
                    "positive_count": 0,
                    "negative_count": 0,
                    "top_items": [],
                },
            )
            data["event_count"] += 1
            data["event_score"] += float(item.get("event_score") or 0)
            event_type = str(item.get("event_type") or "general_news")
            data["event_types"][event_type] += 1
            source_tier = str(item.get("source_tier") or "general")
            data["source_tiers"][source_tier] += 1
            if item.get("event_direction") == "positive":
                data["positive_count"] += 1
            elif item.get("event_direction") == "negative":
                data["negative_count"] += 1
            if len(data["top_items"]) < 5:
                data["top_items"].append(
                    {
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "source": item.get("source", ""),
                        "published_at": item.get("published_at"),
                        "event_type": event_type,
                        "event_label": item.get("event_label", "General news"),
                        "source_tier": source_tier,
                    }
                )

    rendered: dict[str, dict[str, Any]] = {}
    for symbol, data in signals.items():
        ordered_event_types = [event for event, _ in data["event_types"].most_common() if event != "general_news"]
        if not ordered_event_types and data["event_types"].get("general_news"):
            ordered_event_types = ["general_news"]
        rendered[symbol] = {
            "symbol": symbol,
            "event_count": data["event_count"],
            "event_score": round(float(data["event_score"]), 2),
            "event_types": ordered_event_types,
            "event_type_counts": dict(data["event_types"]),
            "source_tiers": [tier for tier, _ in data["source_tiers"].most_common()],
            "source_tier_counts": dict(data["source_tiers"]),
            "positive_count": data["positive_count"],
            "negative_count": data["negative_count"],
            "top_items": data["top_items"],
        }
    return rendered


def top_catalyst_signals(news_events: dict[str, dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    rows = sorted(news_events.values(), key=lambda row: (row["event_score"], row["event_count"]), reverse=True)
    return rows[:limit]


def news_aliases_for_symbol(symbol: str) -> list[str]:
    aliases: list[str] = []
    seen: set[str] = set()
    for candidate in equivalent_symbols(symbol):
        for alias in [candidate, *NEWS_ALIASES.get(candidate, [])]:
            normalized = alias.upper()
            if normalized and normalized not in seen:
                seen.add(normalized)
                aliases.append(normalized)
    return aliases


def build_signal_synthesis(
    cards: list[dict[str, Any]],
    macro: dict[str, Any],
    manager_radar: dict[str, Any],
    portfolio: dict[str, Any],
    catalyst_signals: list[dict[str, Any]],
) -> dict[str, Any]:
    family_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    confirmed = 0
    for card in cards:
        families = card.get("signal_families", [])
        family_counts.update(families)
        if len(families) >= 2:
            confirmed += 1
    for row in catalyst_signals:
        source_counts.update(row.get("source_tiers", []))
    return {
        "regime": macro.get("regime", "mixed macro tape"),
        "manager_coverage": {
            "stored_latest_count": manager_radar.get("stored_latest_count", 0),
            "manager_count": manager_radar.get("manager_count", 0),
        },
        "portfolio_context": {
            "display_name": "Geoffrey Woo Portfolio",
            "value_basis": portfolio.get("value_basis", "private"),
            "position_count": portfolio.get("position_count", 0),
            "symbol_count": portfolio.get("symbol_count", 0),
            "equity_weight": portfolio.get("equity_weight", 1.0),
            "cash_weight": portfolio.get("cash_weight", 0.0),
            "weight_basis": portfolio.get("comparison_weight_basis", "invested_equity_ex_cash"),
        },
        "confirmed_card_count": confirmed,
        "dominant_families": [
            {"family": family, "count": count} for family, count in family_counts.most_common()
        ],
        "top_catalyst_symbols": catalyst_signals[:8],
        "source_mix": [
            {"tier": tier, "count": count} for tier, count in source_counts.most_common()
        ],
    }


def build_portfolio_benchmark(
    portfolio: dict[str, Any],
    cards: list[dict[str, Any]],
    manager_radar: dict[str, Any],
    macro: dict[str, Any],
    prices: dict[str, dict[str, Decimal]],
    return_windows: dict[str, dict[str, Decimal]] | None = None,
    risk_limits: dict[str, Any] | None = None,
    earnings_events: list[dict[str, Any]] | None = None,
    research_book: dict[str, Any] | None = None,
) -> dict[str, Any]:
    window_data = return_windows or legacy_return_windows(prices, macro)
    portfolio_return, price_coverage, components = portfolio_return_components_for_window(
        portfolio,
        window_data,
        "5d",
        prices,
    )
    total_portfolio_return, total_price_coverage = portfolio_total_window_return(portfolio, window_data, "5d")
    horizon_returns = build_horizon_returns(portfolio, window_data)
    total_horizon_returns = build_total_horizon_returns(portfolio, window_data)
    equity_horizon_returns = build_equity_horizon_returns(portfolio, window_data)
    primary = choose_primary_horizon(horizon_returns)
    primary_equity = matching_horizon(equity_horizon_returns, primary["key"]) or (
        choose_primary_horizon(equity_horizon_returns) if equity_horizon_returns else {}
    )
    primary_return = float(primary.get("portfolio_return", portfolio_return))
    peer_weights = peer_symbol_weights(manager_radar)
    peer_proxies = build_peer_proxies(manager_radar, window_data, primary["key"], primary_return)
    benchmarks = build_return_benchmarks(macro, peer_proxies, primary_return, window_data, primary["key"])
    gaps = build_exposure_gaps(cards, portfolio, peer_weights)
    sizing_plan = build_sizing_plan(
        research_book or {},
        portfolio,
        components,
        gaps,
        risk_limits,
    ) if research_book else {}
    raw_actions = sizing_plan.get("action_queue") if sizing_plan else None
    action_queue = apply_risk_controls(
        raw_actions or build_action_queue(cards, components, gaps, peer_weights),
        portfolio,
        cards,
        earnings_events or [],
        risk_limits,
    )
    if sizing_plan:
        target_by_symbol = proxy_index(sizing_plan.get("targets", []))
        for action in action_queue:
            target = target_by_symbol.get(str(action.get("symbol") or "").upper())
            if target:
                target.update(
                    {
                        "recommended_delta_weight": action.get("recommended_delta_weight", target.get("recommended_delta_weight", 0)),
                        "post_action_weight": action.get("post_action_weight", target.get("post_action_weight", 0)),
                        "target_weight": action.get("target_weight", target.get("target_weight", 0)),
                        "trade_action": action.get("trade_action", target.get("trade_action", "study")),
                        "risk_flags": action.get("risk_flags", target.get("risk_flags", [])),
                        "constraint_notes": action.get("constraint_notes", target.get("constraint_notes", [])),
                        "confidence": action.get("confidence", target.get("confidence", 0)),
                    }
                )
        sizing_plan["action_queue"] = action_queue
        sizing_plan["action_count"] = len(action_queue)
        sizing_plan["rebalance_budget"] = rebalance_budget_summary(
            action_queue,
            normalize_limits(risk_limits),
            starting_cash_weight=float(sizing_plan.get("cash_reserve_weight") or portfolio.get("cash_weight") or 0),
            max_cash_deploy_weight=float(sizing_plan.get("cash_deployable_weight") or 0),
        )
        annotate_action_funding(action_queue, sizing_plan["rebalance_budget"])
        sizing_plan["post_trade_cash_weight"] = sizing_plan["rebalance_budget"].get("post_trade_cash_weight", sizing_plan.get("cash_reserve_weight", 0))
    study_queue = build_study_queue(components, gaps)
    return {
        "portfolio_return_5d": round(portfolio_return, 2),
        "total_portfolio_return_5d": round(total_portfolio_return, 2),
        "price_coverage_pct": round(price_coverage * 100, 2),
        "total_price_coverage_pct": round(total_price_coverage * 100, 2),
        "primary_horizon": primary["key"],
        "primary_label": primary["label"],
        "primary_portfolio_return": round(primary_return, 2),
        "primary_price_coverage_pct": primary.get("price_coverage_pct", round(price_coverage * 100, 2)),
        "horizon_returns": horizon_returns,
        "total_horizon_returns": total_horizon_returns,
        "equity_horizon_returns": equity_horizon_returns,
        "primary_equity_return": primary_equity.get("portfolio_return") if primary_equity else None,
        "primary_equity_price_coverage_pct": primary_equity.get("price_coverage_pct") if primary_equity else None,
        "return_analytics": build_return_analytics(portfolio, total_horizon_returns, equity_horizon_returns, primary["key"]),
        "actual_return_available": False,
        "actual_return_required_data": "daily account equity and cash-flow history",
        "return_basis": "ex-cash current-weight public-price proxy; not realized/TWR/IRR account performance",
        "peer_basis": "priced top disclosed focus-manager 13F positions; delayed and incomplete",
        "benchmarks": benchmarks,
        "peer_proxies": peer_proxies,
        "top_contributors": sorted(components, key=lambda row: row["contribution_pct"], reverse=True)[:8],
        "top_detractors": sorted(components, key=lambda row: row["contribution_pct"])[:8],
        "exposure_gaps": gaps,
        "sizing_plan": sizing_plan,
        "action_queue": action_queue,
        "study_queue": study_queue,
    }


def legacy_return_windows(
    prices: dict[str, dict[str, Decimal]],
    macro: dict[str, Any],
) -> dict[str, dict[str, Decimal]]:
    windows: dict[str, dict[str, Decimal]] = {}
    for symbol, quote in prices.items():
        if quote.get("five_day_pct") is not None:
            windows[symbol] = {"5d": quote["five_day_pct"], "last": quote.get("last", Decimal("0"))}
    for row in macro.get("tape", []):
        if row.get("five_day_pct") is not None:
            windows[str(row["symbol"])] = {"5d": Decimal(str(row["five_day_pct"]))}
    return windows


def build_horizon_returns(
    portfolio: dict[str, Any],
    return_windows: dict[str, dict[str, Decimal]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, label in [("5d", "5D"), ("1m", "1M"), ("3m", "3M"), ("ytd", "YTD"), ("1y", "1Y")]:
        portfolio_return, coverage = portfolio_window_return(portfolio, return_windows, key)
        if coverage <= 0:
            continue
        rows.append(
            {
                "key": key,
                "label": label,
                "basis": "current_weight_price_proxy_ex_cash",
                "is_actual_return": False,
                "portfolio_return": round(portfolio_return, 2),
                "price_coverage_pct": round(coverage * 100, 2),
            }
        )
    return rows


def build_total_horizon_returns(
    portfolio: dict[str, Any],
    return_windows: dict[str, dict[str, Decimal]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, label in [("5d", "5D"), ("1m", "1M"), ("3m", "3M"), ("ytd", "YTD"), ("1y", "1Y")]:
        portfolio_return, coverage = portfolio_total_window_return(portfolio, return_windows, key)
        if coverage <= 0:
            continue
        rows.append(
            {
                "key": key,
                "label": label,
                "basis": "current_weight_price_proxy_total_including_cash",
                "is_actual_return": False,
                "portfolio_return": round(portfolio_return, 2),
                "price_coverage_pct": round(coverage * 100, 2),
            }
        )
    return rows


def build_equity_horizon_returns(
    portfolio: dict[str, Any],
    return_windows: dict[str, dict[str, Decimal]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    equity_weight = portfolio_equity_weight(portfolio)
    cash_weight = portfolio_cash_weight(portfolio)
    for key, label in [("5d", "5D"), ("1m", "1M"), ("3m", "3M"), ("ytd", "YTD"), ("1y", "1Y")]:
        equity_return, coverage = portfolio_window_return(portfolio, return_windows, key)
        if coverage <= 0:
            continue
        rows.append(
            {
                "key": key,
                "label": label,
                "basis": "current_weight_price_proxy_ex_cash",
                "is_actual_return": False,
                "portfolio_return": round(equity_return, 2),
                "equity_return": round(equity_return, 2),
                "price_coverage_pct": round(coverage * 100, 2),
                "covered_equity_weight": round(coverage, 6),
                "equity_weight": round(equity_weight, 6),
                "cash_weight_excluded": round(cash_weight, 6),
            }
        )
    return rows


def build_return_analytics(
    portfolio: dict[str, Any],
    total_returns: list[dict[str, Any]],
    equity_returns: list[dict[str, Any]],
    primary_horizon: str,
) -> dict[str, Any]:
    total_by_key = {str(row.get("key")): row for row in total_returns}
    equity_by_key = {str(row.get("key")): row for row in equity_returns}
    horizons = []
    for key, total in total_by_key.items():
        equity = equity_by_key.get(key)
        if not equity:
            continue
        total_return = float(total.get("portfolio_return") or 0)
        equity_return = float(equity.get("portfolio_return") or 0)
        horizons.append(
            {
                "key": key,
                "label": total.get("label", equity.get("label", key.upper())),
                "total_portfolio_return": round(total_return, 2),
                "invested_equity_return": round(equity_return, 2),
                "cash_effect_pct": round(total_return - equity_return, 2),
                "ex_cash_uplift_pct": round(equity_return - total_return, 2),
                "total_price_coverage_pct": total.get("price_coverage_pct"),
                "equity_price_coverage_pct": equity.get("price_coverage_pct"),
            }
        )
    primary = next((row for row in horizons if row["key"] == primary_horizon), horizons[0] if horizons else None)
    return {
        "basis": "current-weight public-price proxy on invested equity, excluding cash from comparison weights",
        "equity_weight": round(portfolio_equity_weight(portfolio), 6),
        "cash_weight": round(portfolio_cash_weight(portfolio), 6),
        "primary": primary,
        "horizons": horizons,
        "note": "Return, peer, and exposure comparisons normalize priced public-stock weights to the invested equity sleeve. It is still a price proxy, not realized TWR/IRR.",
    }


def choose_primary_horizon(horizon_returns: list[dict[str, Any]]) -> dict[str, Any]:
    by_key = {row["key"]: row for row in horizon_returns}
    for key in ["3m", "1m", "ytd", "5d"]:
        row = by_key.get(key)
        if row and float(row.get("price_coverage_pct") or 0) >= 60:
            return row
    return horizon_returns[0] if horizon_returns else {"key": "5d", "label": "5D", "portfolio_return": 0.0, "price_coverage_pct": 0.0}


def matching_horizon(horizon_returns: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    return next((row for row in horizon_returns if row.get("key") == key), None)


def portfolio_comparison_weights_by_symbol(portfolio: dict[str, Any]) -> dict[str, float]:
    weights: dict[str, float] = {}
    for row in portfolio.get("by_symbol", []):
        symbol = str(row.get("symbol", "")).upper()
        if not symbol or is_cash_position(row):
            continue
        weights[symbol] = weights.get(symbol, 0.0) + portfolio_comparison_weight(portfolio, row)
    return weights


def portfolio_comparison_weight(portfolio: dict[str, Any], row: dict[str, Any]) -> float:
    if is_cash_position(row):
        return 0.0
    for key in ("comparison_weight", "ex_cash_weight"):
        if row.get(key) is not None:
            return max(0.0, float(row.get(key) or 0))
    weight = max(0.0, float(row.get("weight") or 0))
    cash_weight = portfolio_cash_weight(portfolio)
    raw_equity_weight = portfolio_equity_weight(portfolio)
    if cash_weight > 0 and raw_equity_weight > 0:
        return weight / raw_equity_weight
    return weight


def portfolio_window_return(
    portfolio: dict[str, Any],
    return_windows: dict[str, dict[str, Decimal]],
    horizon: str,
) -> tuple[float, float]:
    weighted = 0.0
    covered = 0.0
    for row in portfolio.get("by_symbol", []):
        if is_cash_position(row):
            continue
        symbol = str(row.get("symbol", "")).upper()
        weight = portfolio_comparison_weight(portfolio, row)
        value = window_return(return_windows, symbol, horizon)
        if value is None:
            continue
        weighted += weight * value
        covered += weight
    return weighted, min(covered, 1.0)


def portfolio_total_window_return(
    portfolio: dict[str, Any],
    return_windows: dict[str, dict[str, Decimal]],
    horizon: str,
) -> tuple[float, float]:
    weighted = 0.0
    covered = 0.0
    for row in portfolio.get("by_symbol", []):
        weight = portfolio_total_weight(portfolio, row)
        if weight <= 0:
            continue
        if is_cash_position(row):
            covered += weight
            continue
        symbol = str(row.get("symbol", "")).upper()
        value = window_return(return_windows, symbol, horizon)
        if value is None:
            continue
        weighted += weight * value
        covered += weight
    return weighted, min(covered, 1.0)


def equity_window_return(
    portfolio: dict[str, Any],
    return_windows: dict[str, dict[str, Decimal]],
    horizon: str,
) -> tuple[float, float, float]:
    weighted = 0.0
    covered = 0.0
    equity_weight = portfolio_equity_weight(portfolio)
    if equity_weight <= 0:
        return 0.0, 0.0, 0.0
    for row in portfolio.get("by_symbol", []):
        if is_cash_position(row):
            continue
        symbol = str(row.get("symbol", "")).upper()
        weight = portfolio_comparison_weight(portfolio, row)
        value = window_return(return_windows, symbol, horizon)
        if value is None:
            continue
        weighted += weight * value
        covered += weight
    if covered <= 0:
        return 0.0, 0.0, 0.0
    return weighted / covered, min(covered / equity_weight, 1.0), covered


def portfolio_equity_weight(portfolio: dict[str, Any]) -> float:
    if portfolio.get("equity_weight") is not None:
        return max(0.0, float(portfolio.get("equity_weight") or 0))
    total = sum(
        float(row.get("weight") or 0)
        for row in portfolio.get("by_symbol", [])
        if not is_cash_position(row)
    )
    return max(0.0, total)


def portfolio_cash_weight(portfolio: dict[str, Any]) -> float:
    if portfolio.get("cash_weight") is not None:
        return max(0.0, float(portfolio.get("cash_weight") or 0))
    total = sum(
        float(row.get("weight") or 0)
        for row in portfolio.get("by_symbol", [])
        if is_cash_position(row)
    )
    return max(0.0, total)


def portfolio_total_weight(portfolio: dict[str, Any], row: dict[str, Any]) -> float:
    if row.get("total_weight") is not None:
        return max(0.0, float(row.get("total_weight") or 0))
    weight = max(0.0, float(row.get("weight") or 0))
    if is_cash_position(row):
        return weight or portfolio_cash_weight(portfolio)
    weight_basis = str(portfolio.get("weight_basis") or "").lower()
    if "ex_cash" in weight_basis or "invested_equity" in weight_basis:
        return weight * portfolio_equity_weight(portfolio)
    return weight


def window_return(
    return_windows: dict[str, dict[str, Decimal]],
    symbol: str,
    horizon: str,
) -> float | None:
    data = proxied_lookup(return_windows, symbol)
    if not data or data.get(horizon) is None:
        return None
    return float(data[horizon])


def portfolio_return_components(
    portfolio: dict[str, Any],
    prices: dict[str, dict[str, Decimal]],
) -> tuple[float, float, list[dict[str, Any]]]:
    return portfolio_return_components_for_window(portfolio, {}, "5d", prices)


def portfolio_return_components_for_window(
    portfolio: dict[str, Any],
    return_windows: dict[str, dict[str, Decimal]],
    horizon: str,
    fallback_prices: dict[str, dict[str, Decimal]] | None = None,
) -> tuple[float, float, list[dict[str, Any]]]:
    weighted_return = 0.0
    priced_weight = 0.0
    components: list[dict[str, Any]] = []
    for row in portfolio.get("by_symbol", []):
        if is_cash_position(row):
            continue
        symbol = str(row.get("symbol", "")).upper()
        weight = portfolio_comparison_weight(portfolio, row)
        five_day = window_return(return_windows, symbol, horizon)
        if five_day is None:
            five_day = quote_move(fallback_prices or {}, symbol)
        if five_day is None:
            continue
        contribution = weight * five_day
        weighted_return += contribution
        priced_weight += weight
        components.append(
            {
                "symbol": symbol,
                "bucket": row.get("bucket", "unmapped"),
                "asset_class": row.get("asset_class", "cash" if is_cash_position(row) else "equity"),
                "is_cash": bool(is_cash_position(row)),
                "weight": round(weight, 6),
                "five_day_pct": round(five_day, 2),
                "contribution_pct": round(contribution, 2),
            }
        )
    return weighted_return, min(priced_weight, 1.0), components


def quote_move(prices: dict[str, dict[str, Decimal]], symbol: str) -> float | None:
    quote = proxied_lookup(prices, symbol)
    if not quote or quote.get("five_day_pct") is None:
        return None
    return float(quote["five_day_pct"])


def macro_tape_return(macro: dict[str, Any], symbol: str) -> float | None:
    for row in macro.get("tape", []):
        if row.get("symbol") == symbol and row.get("five_day_pct") is not None:
            return float(row["five_day_pct"])
    return None


def build_return_benchmarks(
    macro: dict[str, Any],
    peer_proxies: list[dict[str, Any]],
    portfolio_return: float,
    return_windows: dict[str, dict[str, Decimal]],
    horizon: str,
) -> list[dict[str, Any]]:
    benchmarks: list[dict[str, Any]] = []
    for symbol, name in [
        ("SPY", "S&P 500"),
        ("QQQ", "Nasdaq 100"),
        ("SMH", "Semiconductors"),
        ("IGV", "Software"),
    ]:
        value = window_return(return_windows, symbol, horizon)
        if value is None and horizon == "5d":
            value = macro_tape_return(macro, symbol)
        if value is not None:
            benchmarks.append(return_benchmark(name, symbol, value, portfolio_return, horizon))
    ai_basket = average_window_return(return_windows, ["QQQ", "SMH", "IGV"], horizon)
    if ai_basket is None and horizon == "5d":
        ai_basket = (macro.get("scores") or {}).get("ai_momentum")
    if ai_basket is not None:
        benchmarks.append(return_benchmark("AI beta basket", "QQQ/SMH/IGV", float(ai_basket), portfolio_return, horizon))
    peer_returns = [row["proxy_return"] for row in peer_proxies]
    if peer_returns:
        benchmarks.append(return_benchmark("Focus-manager median proxy", "13F proxy", median(peer_returns), portfolio_return, horizon))
        tier1_returns = [row["proxy_return"] for row in peer_proxies if row.get("manager_tier") == "tier_1"]
        tier2_returns = [row["proxy_return"] for row in peer_proxies if row.get("manager_tier") != "tier_1"]
        if tier1_returns:
            benchmarks.append(return_benchmark("AI Thesis Core median proxy", "13F proxy", median(tier1_returns), portfolio_return, horizon))
        if tier2_returns:
            benchmarks.append(return_benchmark("Manager Context Bench median proxy", "13F proxy", median(tier2_returns), portfolio_return, horizon))
        benchmarks.append(return_benchmark("Focus-manager best proxy", "13F proxy", max(peer_returns), portfolio_return, horizon))
    return benchmarks


def average_window_return(
    return_windows: dict[str, dict[str, Decimal]],
    symbols: list[str],
    horizon: str,
) -> float | None:
    values = [value for symbol in symbols if (value := window_return(return_windows, symbol, horizon)) is not None]
    if not values:
        return None
    return mean(values)


def return_benchmark(name: str, symbol: str, value: float, portfolio_return: float, horizon: str) -> dict[str, Any]:
    active = portfolio_return - value
    return {
        "name": name,
        "symbol": symbol,
        "horizon": horizon,
        "return_pct": round(value, 2),
        "return_5d": round(value, 2),
        "portfolio_vs_benchmark": round(active, 2),
        "active_vs_portfolio": round(active, 2),
    }


def build_peer_proxies(
    manager_radar: dict[str, Any],
    return_windows: dict[str, dict[str, Decimal]],
    horizon: str,
    portfolio_return: float,
) -> list[dict[str, Any]]:
    proxies: list[dict[str, Any]] = []
    for manager in manager_radar.get("focus_managers", []):
        if manager.get("status") != "ok":
            continue
        weighted = 0.0
        covered = 0.0
        priced_symbols: list[str] = []
        for position in manager_public_positions(manager):
            symbol = str(position.get("symbol") or "").upper()
            fund_weight = float(position.get("fund_weight") or 0)
            period_return = window_return(return_windows, symbol, horizon)
            if not symbol or period_return is None or fund_weight <= 0:
                continue
            weighted += fund_weight * period_return
            covered += fund_weight
            priced_symbols.append(symbol)
        if covered <= 0:
            continue
        proxy_return = weighted / covered
        proxies.append(
            {
                "manager_key": manager.get("manager_key", ""),
                "manager_name": manager.get("manager_name", ""),
                "manager_tier": manager.get("manager_tier", "tier_2"),
                "manager_group": manager.get("manager_group", "Manager Context Bench"),
                "horizon": horizon,
                "proxy_return": round(proxy_return, 2),
                "proxy_return_5d": round(proxy_return, 2),
                "portfolio_vs_peer": round(portfolio_return - proxy_return, 2),
                "peer_vs_portfolio": round(proxy_return - portfolio_return, 2),
                "priced_top_weight_pct": round(covered * 100, 2),
                "portfolio_overlap_pct": manager.get("default_portfolio_overlap_pct", 0),
                "priced_symbols": priced_symbols[:8],
            }
        )
    return sorted(proxies, key=lambda row: row["proxy_return_5d"], reverse=True)


def peer_symbol_weights(manager_radar: dict[str, Any]) -> dict[str, float]:
    weights_by_group: dict[str, list[float]] = {}
    for manager in manager_radar.get("focus_managers", []):
        if manager.get("status") != "ok":
            continue
        manager_weights: dict[str, float] = {}
        for position in manager_public_positions(manager):
            symbol = str(position.get("symbol") or "").upper()
            fund_weight = float(position.get("fund_weight") or 0)
            if symbol and fund_weight > 0:
                key = symbol_proxy_key(symbol)
                manager_weights[key] = manager_weights.get(key, 0.0) + fund_weight
        for key, fund_weight in manager_weights.items():
            weights_by_group.setdefault(key, []).append(fund_weight)
    weights: dict[str, float] = {}
    for key, values in weights_by_group.items():
        if not values:
            continue
        average = mean(values)
        for candidate in equivalent_symbols(key):
            weights[candidate] = average
    return weights


def manager_public_positions(manager: dict[str, Any]) -> list[dict[str, Any]]:
    return manager.get("positions") or manager.get("top_positions") or []


def build_exposure_gaps(
    cards: list[dict[str, Any]],
    portfolio: dict[str, Any],
    peer_weights: dict[str, float],
) -> list[dict[str, Any]]:
    portfolio_weights = portfolio_comparison_weights_by_symbol(portfolio)
    gaps_by_symbol: dict[str, dict[str, Any]] = {}
    for card in cards:
        symbol = str(card.get("symbol", "")).upper()
        if not symbol:
            continue
        portfolio_weight = float(sum_equivalent_values(portfolio_weights, symbol, 0.0) or 0.0)
        peer_weight = float(proxied_lookup(peer_weights, symbol, 0.0) or 0.0)
        manager_count = int(card.get("consensus_manager_count") or 0)
        signal_count = int(card.get("signal_family_count") or len(card.get("signal_families", [])))
        score = float(card.get("score") or 0)
        event_types = set(card.get("top_event_types") or [])
        put_value = float(card.get("put_value") or 0)
        call_value = float(card.get("call_value") or 0)
        risk_event = bool(event_types & {"financing_risk", "regulatory_risk", "crowding_warning"}) or (
            put_value > max(call_value * 1.25, 50_000_000) and portfolio_weight > 0
        )
        risk_flags: list[str] = []
        if event_types & {"financing_risk", "regulatory_risk", "crowding_warning"}:
            risk_flags.extend(sorted(event_types & {"financing_risk", "regulatory_risk", "crowding_warning"}))
        if put_value > max(call_value * 1.25, 50_000_000) and portfolio_weight > 0:
            risk_flags.append("put_heavy_13f")
        gap: dict[str, Any] | None = None
        if portfolio_weight > 0 and risk_event:
            gap = {
                "type": "risk_review",
                "priority": 95,
                "symbol": symbol,
                "bucket": card.get("bucket", "unmapped"),
                "portfolio_weight": round(portfolio_weight, 6),
                "peer_avg_weight": round(peer_weight, 6),
                "score": round(score, 2),
                "signal_family_count": signal_count,
                "risk_flags": risk_flags,
                "reason": "Owned position has a risk catalyst or put-heavy 13F signal.",
                "action": "Re-underwrite sizing, hedge need, and falsifier before adding exposure.",
            }
        elif portfolio_weight == 0 and manager_count >= 3 and signal_count >= 2 and score >= 38:
            gap = {
                "type": "white_space",
                "priority": 82 + min(manager_count, 8),
                "symbol": symbol,
                "bucket": card.get("bucket", "unmapped"),
                "portfolio_weight": 0.0,
                "peer_avg_weight": round(peer_weight, 6),
                "score": round(score, 2),
                "signal_family_count": signal_count,
                "reason": "Multiple tracked managers own it and at least two signal families confirm, but the portfolio has no weight.",
                "action": "Research whether this deserves a starter slot or belongs on the watch-only list.",
            }
        elif peer_weight > portfolio_weight + 0.03 and score >= 38 and signal_count >= 2:
            gap = {
                "type": "underweight_vs_focus",
                "priority": 72,
                "symbol": symbol,
                "bucket": card.get("bucket", "unmapped"),
                "portfolio_weight": round(portfolio_weight, 6),
                "peer_avg_weight": round(peer_weight, 6),
                "score": round(score, 2),
                "signal_family_count": signal_count,
                "reason": "Focus-manager top-position weight is materially above current portfolio weight.",
                "action": "Study why peers size it larger and whether the edge, timing, and downside fit the portfolio.",
            }
        elif portfolio_weight >= 0.10 and score < 42:
            gap = {
                "type": "concentration_check",
                "priority": 68,
                "symbol": symbol,
                "bucket": card.get("bucket", "unmapped"),
                "portfolio_weight": round(portfolio_weight, 6),
                "peer_avg_weight": round(peer_weight, 6),
                "score": round(score, 2),
                "signal_family_count": signal_count,
                "reason": "Large current weight without a correspondingly high fresh signal score.",
                "action": "Define what evidence would justify keeping, adding, trimming, or hedging the position.",
            }
        if gap:
            proxy_key = symbol_proxy_key(symbol)
            existing = gaps_by_symbol.get(proxy_key)
            if not existing or gap["priority"] > existing["priority"]:
                gaps_by_symbol[proxy_key] = gap
    return sorted(gaps_by_symbol.values(), key=lambda row: row["priority"], reverse=True)[:12]


def build_action_queue(
    cards: list[dict[str, Any]],
    components: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    peer_weights: dict[str, float],
) -> list[dict[str, Any]]:
    contribution_by_symbol = {row["symbol"]: row for row in components}
    card_by_symbol = proxy_index(cards)
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for gap in gaps:
        symbol = gap["symbol"]
        card = proxied_lookup(card_by_symbol, symbol, {})
        component = proxied_lookup(contribution_by_symbol, symbol, {})
        action = {
            "symbol": symbol,
            "why": gap["reason"],
            "priority": gap["priority"],
            "portfolio_weight": gap.get("portfolio_weight", 0),
            "peer_avg_weight": gap.get("peer_avg_weight", 0),
            "five_day_pct": component.get("five_day_pct", card.get("five_day_pct")),
            "contribution_pct": component.get("contribution_pct", 0),
            "signal_family_count": gap.get("signal_family_count", 0),
            "score": gap.get("score", card.get("score", 0)),
            "event_types": card.get("top_event_types", []),
            "risk_flags": gap.get("risk_flags", []),
        }
        action.update(size_gap_action(gap, card, component))
        actions.append(action)
        seen.add(symbol_proxy_key(symbol))
    for component in sorted(components, key=lambda row: abs(row["contribution_pct"]), reverse=True):
        symbol = component["symbol"]
        if symbol_proxy_key(symbol) in seen:
            continue
        card = proxied_lookup(card_by_symbol, symbol, {})
        contribution = float(component.get("contribution_pct") or 0)
        if contribution < -0.15:
            why = "This owned name is one of the largest 5-day return drags."
            priority = 60 + min(abs(contribution) * 10, 15)
        elif contribution > 0.25:
            why = "This owned name is one of the largest 5-day return contributors."
            priority = 55 + min(contribution * 10, 15)
        else:
            continue
        action = {
            "symbol": symbol,
            "why": why,
            "priority": round(priority, 2),
            "portfolio_weight": component.get("weight", 0),
            "peer_avg_weight": round(float(proxied_lookup(peer_weights, symbol, 0.0) or 0.0), 6),
            "five_day_pct": component.get("five_day_pct"),
            "contribution_pct": component.get("contribution_pct", 0),
            "signal_family_count": card.get("signal_family_count", 0),
            "score": card.get("score", 0),
            "event_types": card.get("top_event_types", []),
            "risk_flags": [],
        }
        action.update(size_attribution_action(component, card, float(proxied_lookup(peer_weights, symbol, 0.0) or 0.0), contribution))
        actions.append(action)
        seen.add(symbol_proxy_key(symbol))
    return sorted(actions, key=lambda row: row["priority"], reverse=True)[:10]


def size_gap_action(gap: dict[str, Any], card: dict[str, Any], component: dict[str, Any]) -> dict[str, Any]:
    current = float(gap.get("portfolio_weight") or 0)
    peer = float(gap.get("peer_avg_weight") or 0)
    score = float(gap.get("score") or card.get("score") or 0)
    signal_count = int(gap.get("signal_family_count") or card.get("signal_family_count") or 0)
    five_day = component.get("five_day_pct", card.get("five_day_pct"))
    move = float(five_day or 0)
    gap_type = str(gap.get("type") or "")
    if gap_type == "white_space":
        target = clamp_weight(max(starter_weight(score, signal_count), min(peer * 0.20, 0.03)))
        immediate_delta = target if move <= 8 else min(target, 0.01)
        post_action = current + immediate_delta
        if immediate_delta < target:
            summary = (
                f"Add {signed_weight_label(immediate_delta)} starter now to {weight_label(post_action)}; "
                f"build toward {weight_label(target)} target on a cleaner entry."
            )
        else:
            summary = f"Add {signed_weight_label(immediate_delta)} to initiate a {weight_label(target)} target weight."
        return sized_payload("add", current, immediate_delta, post_action, target, summary)

    if gap_type == "underweight_vs_focus":
        peer_anchor = min(peer * 0.65, 0.12)
        target = clamp_weight(max(current + 0.01, min(peer_anchor, current + 0.03)))
        immediate_delta = max(0.0, target - current)
        if move > 8:
            immediate_delta = min(immediate_delta, 0.01)
            post_action = current + immediate_delta
            summary = (
                f"Add {signed_weight_label(immediate_delta)} now to {weight_label(post_action)}; "
                f"use {weight_label(target)} as the pullback target versus focus-fund sizing."
            )
            return sized_payload("add", current, immediate_delta, post_action, target, summary)
        post_action = current + immediate_delta
        summary = f"Add {signed_weight_label(immediate_delta)} to {weight_label(post_action)} target versus focus-fund sizing."
        return sized_payload("add", current, immediate_delta, post_action, target, summary)

    if gap_type == "risk_review":
        risk_flags = set(gap.get("risk_flags") or [])
        has_hard_risk = bool(risk_flags - {"put_heavy_13f"})
        if has_hard_risk or score < 40 or current >= 0.10:
            trim = min(0.03, max(0.01, current * 0.20))
            post_action = clamp_weight(current - trim)
            summary = f"Trim {weight_label(trim)} to {weight_label(post_action)} until risk flags clear."
            return sized_payload("trim", current, -trim, post_action, post_action, summary)
        hedge = min(0.01, max(0.005, current * 0.15)) if current > 0 else 0.0
        summary = f"Hold at {weight_label(current)}; do not add, and keep risk budget at {weight_label(hedge)}."
        return sized_payload("hold_hedge", current, 0.0, current, current, summary, hedge)

    if gap_type == "concentration_check":
        trim = min(0.03, max(0.01, current * 0.15))
        post_action = clamp_weight(current - trim)
        summary = f"Trim {weight_label(trim)} to {weight_label(post_action)} and re-earn any add with fresh signal confirmation."
        return sized_payload("trim", current, -trim, post_action, post_action, summary)

    summary = f"Hold at {weight_label(current)} until the signal is underwritten."
    return sized_payload("hold", current, 0.0, current, current, summary)


def size_attribution_action(
    component: dict[str, Any],
    card: dict[str, Any],
    peer_weight: float,
    contribution: float,
) -> dict[str, Any]:
    current = float(component.get("weight") or 0)
    score = float(card.get("score") or 0)
    signal_count = int(card.get("signal_family_count") or len(card.get("signal_families", [])) or 0)
    if contribution < 0 and score >= 45 and signal_count >= 2:
        add = min(0.015, max(0.005, current * 0.12))
        post_action = clamp_weight(current + add)
        summary = f"Add {signed_weight_label(add)} on the drawdown to {weight_label(post_action)} if thesis checks pass."
        return sized_payload("add", current, add, post_action, post_action, summary)
    if contribution < 0:
        trim = min(0.015, max(0.005, current * 0.10))
        post_action = clamp_weight(current - trim)
        summary = f"Trim {weight_label(trim)} to {weight_label(post_action)} unless the drawdown is explicitly a better entry."
        return sized_payload("trim", current, -trim, post_action, post_action, summary)
    if current >= 0.10 or score < 40 or peer_weight < current * 0.5:
        trim = min(0.015, max(0.005, current * 0.10))
        post_action = clamp_weight(current - trim)
        summary = f"Trim {weight_label(trim)} to {weight_label(post_action)} after the strength; recycle into higher-priority adds."
        return sized_payload("trim", current, -trim, post_action, post_action, summary)
    summary = f"Hold at {weight_label(current)}; no add after the move."
    return sized_payload("hold", current, 0.0, current, current, summary)


def sized_payload(
    trade_action: str,
    current: float,
    delta: float,
    post_action: float,
    target: float,
    summary: str,
    hedge_weight: float = 0.0,
) -> dict[str, Any]:
    return {
        "action": summary,
        "sizing_summary": summary,
        "trade_action": trade_action,
        "recommended_delta_weight": round_weight(delta),
        "post_action_weight": round_weight(post_action),
        "target_weight": round_weight(target),
        "hedge_weight": round_weight(hedge_weight),
        "sizing_basis": "portfolio-weight target delta for the trade feed",
    }


def starter_weight(score: float, signal_count: int) -> float:
    weight = 0.015
    if signal_count >= 3:
        weight += 0.005
    if score >= 45:
        weight += 0.005
    if score >= 55 and signal_count >= 3:
        weight += 0.005
    return min(weight, 0.03)


def clamp_weight(value: float) -> float:
    return max(0.0, min(1.0, value))


def round_weight(value: float) -> float:
    return round(value, 6)


def weight_label(value: float) -> str:
    return f"{value * 100:.1f}%"


def signed_weight_label(value: float) -> str:
    return f"{value * 100:+.1f}%"


def build_study_queue(components: list[dict[str, Any]], gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    studies: list[dict[str, Any]] = []
    for row in sorted([item for item in components if not is_cash_position(item)], key=lambda item: abs(item["contribution_pct"]), reverse=True)[:6]:
        if row["contribution_pct"] >= 0:
            question = "Did the gain improve forward expected return, or mostly reduce the margin of safety?"
        else:
            question = "Is the drawdown a thesis violation, macro beta, or a better entry?"
        studies.append(
            {
                "symbol": row["symbol"],
                "signal": "return attribution",
                "question": question,
                "portfolio_weight": row["weight"],
                "five_day_pct": row["five_day_pct"],
                "contribution_pct": row["contribution_pct"],
            }
        )
    for gap in gaps[:6]:
        studies.append(
            {
                "symbol": gap["symbol"],
                "signal": gap["type"],
                "question": gap["action"],
                "portfolio_weight": gap.get("portfolio_weight", 0),
                "peer_avg_weight": gap.get("peer_avg_weight", 0),
                "score": gap.get("score", 0),
            }
        )
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in studies:
        key = (str(item.get("symbol")), str(item.get("signal")))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique[:10]


def is_cash_position(row: dict[str, Any]) -> bool:
    symbol = str(row.get("symbol", "")).upper()
    bucket = str(row.get("bucket", ""))
    asset_class = str(row.get("asset_class", ""))
    return bool(row.get("is_cash")) or asset_class == "cash" or bucket == "cash_reserves" or symbol in {"CASH", "USD"}


def alias_matches(haystack: str, alias: str) -> bool:
    if len(alias) <= 5 and alias.isalnum():
        return re.search(rf"(?<![A-Z0-9]){re.escape(alias)}(?![A-Z0-9])", haystack) is not None
    return alias in haystack


def vanguard_staleness(conn: sqlite3.Connection, stale_after_days: int, now: datetime | None = None) -> dict[str, Any]:
    row = conn.execute(
        "SELECT MAX(imported_at) AS imported_at FROM imports WHERE source = 'vanguard'"
    ).fetchone()
    last = row["imported_at"] if row and row["imported_at"] else None
    if not last:
        return {"is_stale": True, "last_import": None}
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return {"is_stale": True, "last_import": last}
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    else:
        last_dt = last_dt.astimezone(timezone.utc)
    reference_now = now or datetime.now(timezone.utc)
    if reference_now.tzinfo is None:
        reference_now = reference_now.replace(tzinfo=timezone.utc)
    else:
        reference_now = reference_now.astimezone(timezone.utc)
    return {"is_stale": reference_now - last_dt > timedelta(days=stale_after_days), "last_import": last}
