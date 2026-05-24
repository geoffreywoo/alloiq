from __future__ import annotations

import json
import shutil
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from .managers import manager_group_label
from .util import ensure_dir


DEFAULT_WEB_DIR = Path("web")
PORTFOLIO_DISPLAY_NAME = "Geoffrey Woo Portfolio"
BENCHMARK_NAME_MAP = {
    "Tier 1 median proxy": "AI Thesis Core median proxy",
    "Tier 2 median proxy": "Manager Context Bench median proxy",
}


def build_site(
    reports_dir: Path,
    out_dir: Path = DEFAULT_WEB_DIR,
    privacy: str = "public",
    run_kind: str | None = None,
    workflow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_dir(out_dir / "data")
    report_paths = sorted(reports_dir.glob("*.json"), key=lambda path: (path.stat().st_mtime, path.name))
    if not report_paths:
        existing_snapshot = out_dir / "data" / "latest.json"
        if existing_snapshot.exists():
            ensure_static_assets(out_dir)
            return {
                "out_dir": str(out_dir),
                "latest_report": str(existing_snapshot),
                "privacy": privacy,
                "report_count": 0,
                "used_existing_snapshot": True,
            }
        raise FileNotFoundError(f"No report JSON files found in {reports_dir}")
    latest_path = report_paths[-1]
    payload = json.loads(latest_path.read_text(encoding="utf-8"))
    web_payload = sanitize_payload(payload, privacy=privacy)
    built_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    last_run_kind = run_kind or str(payload.get("session") or "manual")
    web_payload["site"] = {
        "name": "AlloIQ",
        "domain": "alloiq.com",
        "privacy": privacy,
        "source_report": latest_path.name,
        "last_run_kind": last_run_kind,
        "report_session": payload.get("session", ""),
        "built_at": built_at,
        "workflow": workflow or {"provider": "local", "name": "", "run_id": "", "sha": ""},
        "stale_status": stale_status(last_run_kind, built_at),
    }
    report_index = [
        {
            "file": path.name,
            "stem": path.stem,
            "updated_at": datetime.utcfromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds") + "Z",
        }
        for path in report_paths
    ]
    (out_dir / "data" / "latest.json").write_text(json.dumps(web_payload, indent=2, sort_keys=True), encoding="utf-8")
    (out_dir / "data" / "reports.json").write_text(json.dumps(report_index, indent=2, sort_keys=True), encoding="utf-8")
    ensure_static_assets(out_dir)
    return {
        "out_dir": str(out_dir),
        "latest_report": str(latest_path),
        "privacy": privacy,
        "report_count": len(report_paths),
    }


def sanitize_payload(payload: dict[str, Any], privacy: str = "public") -> dict[str, Any]:
    if privacy == "private":
        private_payload = deepcopy(payload)
        private_payload.setdefault("product", {})["name"] = "AlloIQ"
        private_payload.setdefault("product", {})["domain"] = "alloiq.com"
        private_payload["private_data_redacted"] = False
        private_payload["manager_radar"] = normalize_manager_radar_labels(
            private_payload.get("manager_radar") or {}
        )
        private_payload["portfolio_benchmark"] = sanitize_portfolio_benchmark(
            private_payload.get("portfolio_benchmark") or {}
        )
        return private_payload
    public_payload = deepcopy(payload)
    public_payload.setdefault("product", {})["name"] = "AlloIQ"
    public_payload.setdefault("product", {})["domain"] = "alloiq.com"
    public_payload["private_data_redacted"] = True
    public_payload["positions"] = {}
    public_payload["transactions"] = []
    if public_payload.get("latest_filing"):
        public_payload["latest_filing"] = strip_private_keys(public_payload["latest_filing"])
    public_payload["portfolio"] = sanitize_portfolio(public_payload.get("portfolio") or {})
    portfolio_weights = portfolio_weight_by_symbol(public_payload["portfolio"])
    public_payload["manager_radar"] = sanitize_manager_radar(public_payload.get("manager_radar") or {})
    public_payload["portfolio_benchmark"] = sanitize_portfolio_benchmark(
        public_payload.get("portfolio_benchmark") or {}
    )
    public_payload["decision_cards"] = [
        sanitize_card(card, portfolio_weights) for card in public_payload.get("decision_cards", [])
    ]
    public_payload["ideas"] = [sanitize_idea(idea) for idea in public_payload.get("ideas", [])]
    public_payload["approval_tickets"] = sanitize_approval_tickets(public_payload.get("approval_tickets", []))
    public_payload["earnings_events"] = sanitize_earnings_events(public_payload.get("earnings_events", []))
    public_payload["data_health"] = sanitize_data_health(
        public_payload.get("data_health") or default_data_health(public_payload)
    )
    if "weekly_research" in public_payload:
        public_payload["weekly_research"] = sanitize_weekly_research(public_payload["weekly_research"])
    public_payload.pop("stale_vanguard", None)
    public_payload["recommended_moves"] = build_public_moves(
        public_payload.get("decision_cards", []),
        public_payload.get("macro", {}),
        public_payload["portfolio"],
    )
    return public_payload


def sanitize_portfolio(portfolio: dict[str, Any]) -> dict[str, Any]:
    return {
        "display_name": PORTFOLIO_DISPLAY_NAME,
        "private_redacted": True,
        "value_basis": "weights_only",
        "position_count": portfolio.get("position_count", 0),
        "symbol_count": portfolio.get("symbol_count", 0),
        "by_bucket": [
            {
                "bucket": row.get("bucket", "unmapped"),
                "weight": round(float(row.get("weight") or 0), 6),
            }
            for row in portfolio.get("by_bucket", [])
        ],
        "by_symbol": [
            {
                "symbol": row.get("symbol", ""),
                "bucket": row.get("bucket", "unmapped"),
                "weight": round(float(row.get("weight") or 0), 6),
            }
            for row in portfolio.get("by_symbol", [])
        ],
    }


def sanitize_portfolio_benchmark(benchmark: dict[str, Any]) -> dict[str, Any]:
    clean = dict(benchmark)
    clean["benchmarks"] = [sanitize_benchmark(row) for row in clean.get("benchmarks", [])]
    clean["peer_proxies"] = [sanitize_peer_proxy(row) for row in clean.get("peer_proxies", [])]
    return clean


def sanitize_benchmark(row: dict[str, Any]) -> dict[str, Any]:
    clean = dict(row)
    clean["name"] = BENCHMARK_NAME_MAP.get(str(clean.get("name") or ""), clean.get("name", ""))
    return clean


def sanitize_peer_proxy(row: dict[str, Any]) -> dict[str, Any]:
    clean = dict(row)
    clean["manager_group"] = manager_group_label(str(clean.get("manager_tier") or "tier_2"))
    return clean


def sanitize_manager_radar(radar: dict[str, Any]) -> dict[str, Any]:
    clean = normalize_manager_radar_labels(radar)
    clean["focus_managers"] = [
        sanitize_focus_manager(row) for row in clean.get("focus_managers", [])
    ]
    clean["focus_manager_groups"] = build_public_focus_manager_groups(clean["focus_managers"])
    return clean


def normalize_manager_radar_labels(radar: dict[str, Any]) -> dict[str, Any]:
    clean = dict(radar)
    clean["focus_managers"] = [
        normalize_focus_manager_label(row) for row in clean.get("focus_managers", [])
    ]
    clean["focus_manager_groups"] = build_public_focus_manager_groups(clean["focus_managers"])
    return clean


def normalize_focus_manager_label(row: dict[str, Any]) -> dict[str, Any]:
    clean = dict(row)
    clean["manager_group"] = manager_group_label(str(clean.get("manager_tier") or "tier_2"))
    return clean


def sanitize_focus_manager(row: dict[str, Any]) -> dict[str, Any]:
    clean = normalize_focus_manager_label(row)
    clean.pop("total_common_value", None)
    clean["top_positions"] = [
        sanitize_manager_position(position)
        for position in clean.get("top_positions", [])
    ]
    if clean.get("manager_tier") == "tier_1":
        clean["positions"] = [
            sanitize_manager_position(position)
            for position in clean.get("positions", clean.get("top_positions", []))
        ]
    else:
        clean.pop("positions", None)
    return clean


def sanitize_manager_position(position: dict[str, Any]) -> dict[str, Any]:
    return {
        "rank": int(position.get("rank") or 0),
        "symbol": position.get("symbol", ""),
        "issuer": position.get("issuer", ""),
        "bucket": position.get("bucket", "unmapped"),
        "fund_weight": round(float(position.get("fund_weight") or 0), 6),
        "portfolio_weight": round(float(position.get("portfolio_weight") or 0), 6),
    }


def build_public_focus_manager_groups(focus_managers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups = [
        {
            "key": "tier_1",
            "label": manager_group_label("tier_1"),
            "description": "Leopold/Situational Awareness, Altimeter, and Dragoneer.",
            "managers": [row for row in focus_managers if row.get("manager_tier") == "tier_1"],
        },
        {
            "key": "tier_2",
            "label": manager_group_label("tier_2"),
            "description": "All other tracked public 13F managers.",
            "managers": [row for row in focus_managers if row.get("manager_tier") != "tier_1"],
        },
    ]
    return [group for group in groups if group["managers"]]


def portfolio_weight_by_symbol(portfolio: dict[str, Any]) -> dict[str, float]:
    return {
        str(row.get("symbol", "")).upper(): float(row.get("weight") or 0)
        for row in portfolio.get("by_symbol", [])
        if row.get("symbol")
    }


def portfolio_weight_by_bucket(portfolio: dict[str, Any]) -> dict[str, float]:
    return {
        str(row.get("bucket", "unmapped")): float(row.get("weight") or 0)
        for row in portfolio.get("by_bucket", [])
        if row.get("bucket")
    }


def sanitize_card(card: dict[str, Any], portfolio_weights: dict[str, float] | None = None) -> dict[str, Any]:
    portfolio_weights = portfolio_weights or {}
    clean = dict(card)
    clean.pop("portfolio_value", None)
    clean["portfolio_weight"] = round(portfolio_weights.get(str(clean.get("symbol", "")).upper(), 0.0), 6)
    clean["candidate"] = sanitize_candidate(str(clean.get("candidate", "")))
    return clean


def sanitize_idea(idea: dict[str, Any]) -> dict[str, Any]:
    clean = dict(idea)
    idea_type = str(clean.get("type", "research"))
    if "owned" in idea_type:
        clean["type"] = "portfolio-context research"
        clean["setup"] = "Portfolio context is redacted in the public build; evaluate this symbol against your own exposure."
    return clean


def sanitize_weekly_research(research: dict[str, Any]) -> dict[str, Any]:
    clean = strip_private_keys(research)
    clean["ideas"] = [strip_private_keys(idea) for idea in clean.get("ideas", [])]
    return clean


def sanitize_approval_tickets(tickets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    public_tickets = []
    for ticket in tickets:
        clean = strip_private_keys(ticket)
        clean.pop("order_execution", None)
        clean["approval_required"] = True
        clean["sizing_basis"] = "portfolio-weight research proposal; not an execution order"
        public_tickets.append(clean)
    return public_tickets


def sanitize_earnings_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized = []
    for event in events:
        clean = strip_private_keys(event)
        clean.pop("raw", None)
        sanitized.append(clean)
    return sanitized


def sanitize_data_health(health: dict[str, Any]) -> dict[str, Any]:
    clean = strip_private_keys(health)
    sources = []
    for source in clean.get("sources", []):
        row = dict(source)
        if row.get("source") == "broker_positions":
            row["source"] = "position_snapshot"
            row["label"] = "Position snapshot"
        if row.get("source") == "manual_broker_import":
            row["source"] = "manual_import"
            row["label"] = "Manual import freshness"
        sources.append(row)
    clean["sources"] = sources
    return clean


def default_data_health(payload: dict[str, Any]) -> dict[str, Any]:
    portfolio = payload.get("portfolio") or {}
    manager_radar = payload.get("manager_radar") or {}
    news = payload.get("news") or []
    prices = [card for card in payload.get("decision_cards", []) if card.get("last_price") is not None]
    return {
        "recommendation_posture": "normal" if portfolio.get("position_count") else "research_only_until_positions_refresh",
        "summary": "Public snapshot includes sanitized source-health estimates.",
        "sources": [
            {
                "source": "position_snapshot",
                "label": "Position snapshot",
                "status": "ok" if portfolio.get("position_count") else "missing",
                "detail": f"{portfolio.get('position_count', 0)} position rows represented as public weights.",
            },
            {
                "source": "manager_13f",
                "label": "Manager 13F radar",
                "status": "ok" if manager_radar.get("stored_latest_count") else "missing",
                "detail": f"{manager_radar.get('stored_latest_count', 0)}/{manager_radar.get('manager_count', 0)} managers have stored filings.",
            },
            {
                "source": "news",
                "label": "News catalysts",
                "status": "ok" if news else "missing",
                "detail": f"{len(news)} public news rows in this snapshot.",
            },
            {
                "source": "prices",
                "label": "Market prices",
                "status": "ok" if prices else "missing",
                "detail": f"{len(prices)} decision cards include last prices.",
            },
        ],
    }


def strip_private_keys(value: Any) -> Any:
    private_keys = {
        "account",
        "accounts",
        "broker",
        "brokers",
        "cost_basis",
        "estimated_notional",
        "estimated_shares",
        "external_id",
        "market_value",
        "notional",
        "portfolio_value",
        "positions",
        "quantity",
        "raw",
        "raw_json",
        "shares",
        "transaction_id",
        "transactions",
    }
    if isinstance(value, dict):
        return {
            key: strip_private_keys(item)
            for key, item in value.items()
            if key not in private_keys
        }
    if isinstance(value, list):
        return [strip_private_keys(item) for item in value]
    return value


def sanitize_candidate(candidate: str) -> str:
    lowered = candidate.lower()
    if "hold" in lowered or "add-on" in lowered:
        return "research candidate"
    return candidate


def build_public_moves(
    cards: list[dict[str, Any]],
    macro: dict[str, Any],
    portfolio: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    regime = str(macro.get("regime", "mixed macro tape"))
    bucket_weights = portfolio_weight_by_bucket(portfolio or {})
    moves = [move_from_card(card, regime, bucket_weights) for card in cards]
    moves.sort(key=lambda row: (row["conviction"], row["signal_score"]), reverse=True)
    return moves[:12]


def move_from_card(card: dict[str, Any], regime: str, bucket_weights: dict[str, float] | None = None) -> dict[str, Any]:
    bucket_weights = bucket_weights or {}
    score = float(card.get("score") or 0)
    manager_count = int(card.get("consensus_manager_count") or 0)
    news_count = int(card.get("news_count") or 0)
    event_score = float(card.get("event_score") or 0)
    event_types = [str(event) for event in card.get("top_event_types", [])]
    signal_families = [str(family) for family in card.get("signal_families", [])]
    five_day = card.get("five_day_pct")
    move_pct = float(five_day) if five_day is not None else 0.0
    put_value = float(card.get("put_value") or 0)
    call_value = float(card.get("call_value") or 0)
    consensus_value = float(card.get("consensus_value") or 0)
    portfolio_weight = float(card.get("portfolio_weight") or 0)
    bucket = str(card.get("bucket", "unmapped"))
    bucket_weight = float(bucket_weights.get(bucket, 0))
    conviction = min(100, int(score + manager_count * 4 + min(event_score * 2, 18)))
    negative_event = any(event in event_types for event in ("financing_risk", "regulatory_risk", "crowding_warning"))
    constructive_event = any(event in event_types for event in ("contract_win", "capex_signal", "earnings_revision"))
    if negative_event and portfolio_weight > 0:
        action = "Catalyst risk review"
        posture = "Risk-managed"
        rationale = "The Geoffrey Woo Portfolio owns this name, and recent catalyst classification points to financing, regulatory, or crowding risk."
    elif put_value > max(call_value * 1.25, 50_000_000) and portfolio_weight > 0:
        action = "Hedge existing exposure"
        posture = "Cautious"
        rationale = "The Geoffrey Woo Portfolio owns this name, and tracked filings show meaningful put exposure against it."
    elif put_value > max(call_value * 1.25, 50_000_000):
        action = "Hedge watch"
        posture = "Cautious"
        rationale = "Tracked filings show meaningful put exposure against the name despite manager ownership."
    elif portfolio_weight >= 0.12 and manager_count >= 3 and score >= 38:
        action = "Core position review"
        posture = "Size discipline"
        rationale = "The Geoffrey Woo Portfolio already has a large weight here; compare incremental upside against concentration risk."
    elif portfolio_weight > 0 and manager_count >= 3 and score >= 38 and move_pct < 8:
        action = "Add-on-dip research"
        posture = "Constructive"
        rationale = "The Geoffrey Woo Portfolio owns this name, manager consensus is strong, and recent price action is not yet extreme."
    elif portfolio_weight == 0 and manager_count >= 2 and constructive_event and len(signal_families) >= 2 and move_pct < 8:
        action = "Catalyst-confirmed research"
        posture = "Constructive"
        rationale = "The Geoffrey Woo Portfolio has no current weight, while manager signal and classified catalysts both confirm the research setup."
    elif portfolio_weight == 0 and manager_count >= 3 and score >= 38 and move_pct < 8:
        action = "White-space long research"
        posture = "Constructive"
        rationale = "The Geoffrey Woo Portfolio has no current weight, while multiple tracked managers own the name and the signal score is high."
    elif manager_count >= 3 and move_pct >= 8:
        action = "Wait for pullback"
        posture = "Patient"
        rationale = "Consensus is strong, but the recent move argues for better entry discipline."
    elif news_count >= 4:
        action = "Catalyst watch"
        posture = "Active monitor"
        rationale = "News velocity is elevated enough to re-underwrite the thesis or timing."
    elif manager_count >= 2:
        action = "Deep-dive queue"
        posture = "Research"
        rationale = "Manager overlap is enough to justify work, but evidence is not strong enough for an urgent move."
    else:
        action = "Monitor"
        posture = "Low urgency"
        rationale = "The signal is present but not yet differentiated."
    if bucket_weight >= 0.30 and portfolio_weight == 0 and action in {"White-space long research", "Deep-dive queue"}:
        posture = "Diversification check"
        rationale += " Bucket exposure is already high, so any new name needs to improve quality or asymmetry."
    if "rates/dollar headwind" in regime or "volatility shock" in regime:
        posture = "Risk-managed"
    return {
        "symbol": card.get("symbol", ""),
        "action": action,
        "posture": posture,
        "bucket": bucket,
        "portfolio_weight": round(portfolio_weight, 6),
        "bucket_weight": round(bucket_weight, 6),
        "conviction": conviction,
        "signal_score": round(score, 2),
        "manager_count": manager_count,
        "news_count": news_count,
        "event_score": round(event_score, 2),
        "event_types": event_types,
        "signal_families": signal_families,
        "signal_family_count": len(signal_families),
        "five_day_pct": five_day,
        "last_price": card.get("last_price"),
        "consensus_value": consensus_value,
        "rationale": rationale,
        "trigger": public_trigger(card, action),
        "risk": card.get("counterargument", ""),
        "falsifier": card.get("falsifier", ""),
    }


def public_trigger(card: dict[str, Any], action: str) -> str:
    bucket = card.get("bucket", "")
    event_types = set(card.get("top_event_types", []))
    if action == "Catalyst risk review":
        return "Read the underlying catalyst source and decide whether the risk is temporary, thesis-breaking, or already priced."
    if "contract_win" in event_types:
        return "Verify contract economics, timing, counterparty quality, and whether it changes forward revenue or margins."
    if "capex_signal" in event_types:
        return "Check whether capex guidance flows into backlog, utilization, pricing, or supply constraints for this name."
    if "financing_risk" in event_types:
        return "Review debt, dilution, covenant, and liquidity terms before upgrading the setup."
    if action == "Hedge watch":
        return "Check whether the put exposure is hedging a long book, expressing downside, or stale filing noise."
    if bucket == "power_grid_gas_nuclear":
        return "Look for signed power contracts, grid/interconnection progress, financing, or commodity-price confirmation."
    if bucket == "neocloud_datacenters":
        return "Look for utilization, customer quality, financing terms, and capex evidence."
    if bucket == "semis_networking_hbm":
        return "Look for backlog, hyperscaler capex, supply constraints, and gross-margin direction."
    if bucket == "frontier_ai_platforms":
        return "Look for AI usage translating into revenue, retention, or operating leverage."
    if bucket == "ai_software_winners":
        return "Look for AI feature adoption translating into net retention, pricing, and new workload creation."
    return "Define the variant view and the next public catalyst before acting."


def stale_status(run_kind: str, built_at: str) -> dict[str, Any]:
    max_age_hours = 192 if run_kind == "weekly" else 20
    return {
        "status": "fresh",
        "is_stale_at_build": False,
        "built_at": built_at,
        "max_age_hours": max_age_hours,
        "policy": "client marks stale when built_at exceeds max_age_hours",
    }


def ensure_static_assets(out_dir: Path) -> None:
    source_dir = DEFAULT_WEB_DIR
    if out_dir.resolve() == source_dir.resolve():
        return
    for name in (
        "index.html",
        "portfolio.html",
        "ai-thesis-core.html",
        "styles.css",
        "app.js",
        "portfolio.js",
        "ai-thesis-core.js",
        "favicon.svg",
        "logo.svg",
        "manifest.webmanifest",
        "robots.txt",
        "sitemap.xml",
    ):
        source = source_dir / name
        if source.exists():
            shutil.copyfile(source, out_dir / name)
