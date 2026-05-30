from __future__ import annotations

import errno
import json
import shutil
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

from .audit import WEAK_SOURCE_STATUSES, learning_gap_detail
from .backtest import (
    BACKTEST_VERSION,
    PENDING_EXTERNAL_ALIGNMENT_REVIEW_QUEUE_LIMIT,
    estimated_label_due_date,
    external_alignment_bucket,
    external_alignment_summaries,
    pending_external_coverage_summaries,
    pending_external_coverage_gap_count,
    pending_external_coverage_gap_plan,
    pending_external_coverage_gap_queue,
    pending_external_alignment_summaries,
    pending_external_alignment_due_dates,
    pending_external_alignment_review_acceptance_summary,
    pending_external_alignment_review_count,
    pending_external_alignment_review_due_dates,
    pending_external_alignment_review_item_count,
    pending_external_alignment_review_queue,
    pending_external_alignment_measurement_gap_plan,
    pending_external_alignment_measurement_gap_queue,
    pending_external_alignment_measurement_gap_work_items,
    pending_external_alignment_watchlist,
    pending_group_summaries,
    PENDING_EXTERNAL_ALIGNMENT_MEASUREMENT_GAP_QUEUE_LIMIT,
)
from .earnings import earnings_confirmation_gaps, earnings_health_summary
from .external_signals import external_provider_gap_rows, external_provider_health_detail
from .features import external_coverage_multiplier, external_provider_gap_features
from .instrumentation import build_instrumentation_audit
from .managers import manager_group_label
from .outcomes import (
    approval_data_friction_learning_readiness_projection,
    approval_learning_readiness_projection,
    external_learning_readiness_projection,
    label_maturity,
    learning_readiness_projection,
    pending_label_schedule,
)
from .scheduler import is_nyse_trading_day
from .symbols import equivalent_symbols
from .util import ensure_dir, parse_date


DEFAULT_WEB_DIR = Path("web")
EASTERN = ZoneInfo("America/New_York")
PORTFOLIO_DISPLAY_NAME = "Geoffrey Woo Portfolio"
BENCHMARK_NAME_MAP = {
    "Tier 1 median proxy": "AI Thesis Core median proxy",
    "Tier 2 median proxy": "Manager Context Bench median proxy",
}
REPORT_SESSION_RANK = {
    "premarket": 1,
    "market_open": 2,
    "intraday": 3,
    "midday": 4,
    "market_close": 5,
    "postmarket": 6,
    "weekly": 7,
}
ANTI_FUND_GROWTH_I = {
    "name": "Anti Fund Growth I, LP",
    "as_of": "May 25, 2026",
    "basis": "mark_to_market_growth_book_weight",
    "basis_label": "Mark-to-market weight",
    "category": "affiliated_private_fund",
    "marketing_url": "https://antifund.com",
    "relationship": "Geoffrey Woo's affiliated private tech crossover fund.",
    "description": "Geoffrey Woo's affiliated private tech crossover fund, shown below the public-stock portfolio weights. Growth I is not a public-stock fund. Public weights use mark-to-market estimates; share counts, PPS, dollar marks, account records, and raw LP model fields are excluded.",
    "mark_policy": "Weights use the local Anti Fund Growth I model, live public Ventuals/Hyperliquid marks where available, reported financing or secondary indications where available, and flat marks otherwise.",
    "source_summary": "Local LP model plus public secondary-market marks and financing references.",
    "positions": [
        {
            "company": "OpenAI",
            "weight": 0.402806,
            "cost_weight": 0.367390,
            "source_label": "Hyperliquid Ventuals",
            "source_url": "https://app.hyperliquid.xyz/trade/vntl:OPENAI",
            "mark_basis": "vntl:OPENAI public mark",
        },
        {
            "company": "SpaceX",
            "weight": 0.147105,
            "cost_weight": 0.110736,
            "source_label": "Hyperliquid Ventuals",
            "source_url": "https://app.hyperliquid.xyz/trade/vntl:SPACEX",
            "mark_basis": "vntl:SPACEX public mark",
        },
        {
            "company": "Cognition",
            "weight": 0.129127,
            "cost_weight": 0.167325,
            "source_label": "Local LP model",
            "mark_basis": "Series C and prior-position model mark",
        },
        {
            "company": "Saronic",
            "weight": 0.093030,
            "cost_weight": 0.123034,
            "source_label": "Local LP model",
            "source_url": "https://www.prnewswire.com/news-releases/saronic-closes-1-75b-series-d-at-9-25b-valuation-to-accelerate-a-new-era-of-maritime-autonomy-302729238.html",
            "mark_basis": "Series D and secondary indication",
        },
        {
            "company": "Anduril",
            "weight": 0.067034,
            "cost_weight": 0.061517,
            "source_label": "Local LP model",
            "source_url": "https://techcrunch.com/2026/05/13/anduril-raises-5b-doubles-valuation-to-61b/",
            "mark_basis": "Series H and secondary indication",
        },
        {
            "company": "Anthropic",
            "weight": 0.065421,
            "cost_weight": 0.065421,
            "source_label": "Local LP model",
            "mark_basis": "$900B private valuation model mark",
        },
        {
            "company": "Helion",
            "weight": 0.044021,
            "cost_weight": 0.061517,
            "source_label": "Local LP model",
            "mark_basis": "Flat at current model mark",
        },
        {
            "company": "Erebor",
            "weight": 0.032380,
            "cost_weight": 0.024605,
            "source_label": "Local LP model",
            "mark_basis": "Series C indication",
        },
        {
            "company": "Modal",
            "weight": 0.014674,
            "cost_weight": 0.012304,
            "source_label": "Local LP model",
            "source_url": "https://modal.com/blog/modal-series-c",
            "mark_basis": "Public Series C reference",
        },
        {
            "company": "Etched",
            "weight": 0.004402,
            "cost_weight": 0.006151,
            "source_label": "Local LP model",
            "mark_basis": "Flat at current model mark",
        },
    ],
}


def build_site(
    reports_dir: Path,
    out_dir: Path = DEFAULT_WEB_DIR,
    privacy: str = "public",
    run_kind: str | None = None,
    workflow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_dir(out_dir / "data")
    report_paths = sorted(reports_dir.glob("*.json"), key=report_selection_key)
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
    built_at = utc_timestamp()
    last_run_kind = run_kind or str(payload.get("session") or "manual")
    web_payload["site"] = {
        "name": "AlloIQ",
        "domain": "alloiq.com",
        "privacy": privacy,
        "source_report": latest_path.name,
        "last_run_kind": last_run_kind,
        "report_session": payload.get("session", ""),
        "report_as_of": payload.get("as_of", ""),
        "built_at": built_at,
        "workflow": workflow or {"provider": "local", "name": "", "run_id": "", "sha": ""},
        "stale_status": stale_status(last_run_kind, built_at, payload.get("as_of")),
    }
    report_index = [
        {
            "file": path.name,
            "stem": path.stem,
            "updated_at": utc_timestamp(datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)),
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


def report_selection_key(path: Path) -> tuple[date, float, int, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    report_date = parse_date(payload.get("as_of")) or parse_date(path.stem[:10]) or date.min
    session = str(payload.get("session") or session_from_report_name(path) or "").lower()
    return (
        report_date,
        path.stat().st_mtime,
        REPORT_SESSION_RANK.get(session, 0),
        path.name,
    )


def session_from_report_name(path: Path) -> str:
    stem = path.stem.lower()
    for session in REPORT_SESSION_RANK:
        if stem.endswith(f"-{session}") or f"-{session}-" in stem:
            return session
    return ""


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
    normalize_public_external_reliability(public_payload)
    public_payload.setdefault("product", {})["name"] = "AlloIQ"
    public_payload.setdefault("product", {})["domain"] = "alloiq.com"
    public_payload["private_data_redacted"] = True
    public_payload["positions"] = {}
    public_payload["transactions"] = []
    public_payload.pop("portfolio_valuation_private", None)
    public_payload.pop("market_return_windows", None)
    public_payload["disclaimer"] = "Public weights, public filings, daily AI markets signals. Approval-only; no live order execution."
    if public_payload.get("latest_filing"):
        public_payload["latest_filing"] = strip_private_keys(public_payload["latest_filing"])
    public_payload["portfolio"] = sanitize_portfolio(public_payload.get("portfolio") or {})
    portfolio_weights = portfolio_weight_by_symbol(public_payload["portfolio"])
    public_payload["manager_radar"] = sanitize_manager_radar(public_payload.get("manager_radar") or {})
    public_payload["portfolio_benchmark"] = sanitize_portfolio_benchmark(
        public_payload.get("portfolio_benchmark") or {}
    )
    public_payload["feature_matrix"] = sanitize_public_section(public_payload.get("feature_matrix") or {})
    public_payload["company_underwriting"] = sanitize_public_section(public_payload.get("company_underwriting") or {})
    public_payload["sector_underwriting"] = sanitize_public_section(public_payload.get("sector_underwriting") or {})
    public_payload["research_book"] = sanitize_public_section(public_payload.get("research_book") or {})
    public_payload["outcome_diagnostics"] = sanitize_public_section(public_payload.get("outcome_diagnostics") or {})
    public_payload["backtest"] = sanitize_public_section(public_payload.get("backtest") or {})
    normalize_public_outcome_diagnostics(public_payload)
    public_payload["external_signals"] = sanitize_public_section(
        public_payload.get("external_signals") or default_external_signals(public_payload)
    )
    public_payload["instrumentation_audit"] = sanitize_public_section(public_payload.get("instrumentation_audit") or {})
    public_payload["llm_review"] = sanitize_public_section(public_payload.get("llm_review") or {})
    if public_payload["llm_review"]:
        public_payload["llm_review"].setdefault("llm_direct_sizing_allowed", False)
    public_payload["llm_signal"] = sanitize_public_section(public_payload.get("llm_signal") or public_payload.get("llm_review") or {})
    if public_payload["llm_signal"]:
        public_payload["llm_signal"].setdefault("llm_direct_sizing_allowed", False)
    public_payload.pop("recommendation_training_examples", None)
    public_payload["decision_cards"] = [
        sanitize_card(card, portfolio_weights) for card in public_payload.get("decision_cards", [])
    ]
    public_payload["ideas"] = [sanitize_idea(idea) for idea in public_payload.get("ideas", [])]
    public_payload["approval_tickets"] = sanitize_approval_tickets(public_payload.get("approval_tickets", []))
    public_payload["recommendation_explanations"] = sanitize_public_section(public_payload.get("recommendation_explanations") or [])
    public_payload["review_queue"] = sanitize_public_section(public_payload.get("review_queue") or [])
    public_payload["earnings_events"] = sanitize_earnings_events(public_payload.get("earnings_events", []))
    public_payload["data_health"] = sanitize_data_health(
        public_payload.get("data_health") or default_data_health(public_payload)
    )
    public_payload["calendars"] = sanitize_public_section(
        public_payload.get("calendars") or default_calendars(public_payload)
    )
    normalize_public_earnings_health(public_payload)
    sync_data_health_approval_blocker_summary(public_payload)
    public_payload["engine"] = sanitize_public_section(
        public_payload.get("engine") or default_engine(public_payload)
    )
    public_payload["paper_portfolio"] = sanitize_public_section(
        public_payload.get("paper_portfolio") or default_paper_portfolio(public_payload)
    )
    public_payload["methodology"] = sanitize_methodology(
        public_payload.get("methodology") or default_methodology(public_payload)
    )
    public_payload["anti_fund_growth"] = deepcopy(ANTI_FUND_GROWTH_I)
    public_payload["audit"] = sanitize_public_section(
        public_payload.get("audit") or default_audit(public_payload)
    )
    if "weekly_research" in public_payload:
        public_payload["weekly_research"] = sanitize_weekly_research(public_payload["weekly_research"])
    public_payload.pop("stale_vanguard", None)
    public_payload["recommended_moves"] = build_public_moves(
        public_payload.get("decision_cards", []),
        public_payload.get("macro", {}),
        public_payload["portfolio"],
    )
    refresh_public_instrumentation_audit(public_payload)
    return public_payload


def normalize_public_external_reliability(payload: dict[str, Any]) -> None:
    external = payload.get("external_signals") or {}
    source_statuses = external.get("source_statuses") or []
    status_counts = dict(external.get("provider_status_counts") or {})
    if not status_counts and source_statuses:
        for row in source_statuses:
            status = str((row or {}).get("status") or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
    provider_count = int(external.get("provider_count") or sum(status_counts.values()) or len(source_statuses) or 0)
    provider_ok_count = int(external.get("provider_ok_count") or status_counts.get("ok", 0))
    provider_ok_ratio = external.get("provider_ok_ratio")
    if provider_ok_ratio is None and provider_count:
        provider_ok_ratio = round(provider_ok_count / provider_count, 4)
    if provider_count:
        external["provider_count"] = provider_count
        external["provider_status_counts"] = status_counts
        external["provider_ok_count"] = provider_ok_count
        external["provider_ok_ratio"] = provider_ok_ratio
        external["status"] = external_status_from_counts(status_counts, int(external.get("signal_count") or 0))
        provider_gaps = external_provider_gap_rows(external, limit=None)
        external["provider_gap_count"] = len(provider_gaps)
        external["provider_gaps"] = provider_gaps[:8]
    payload["external_signals"] = external
    sync_external_signal_data_health(payload, external)

    by_symbol = external.get("by_symbol") or {}
    features_by_symbol: dict[str, dict[str, Any]] = {}
    for row in ((payload.get("feature_matrix") or {}).get("rows") or []):
        fill_external_reliability(row, by_symbol.get(str(row.get("symbol") or "").upper()) or {}, external)
        if row.get("symbol"):
            features_by_symbol[str(row.get("symbol")).upper()] = row
    for row in ((payload.get("engine") or {}).get("ranked_candidates") or []):
        feature = features_by_symbol.get(str(row.get("symbol") or "").upper())
        if feature:
            for key in EXTERNAL_RELIABILITY_KEYS:
                if row.get(key) is None or row.get(key) == "":
                    row[key] = feature.get(key)
        fill_external_reliability(row, by_symbol.get(str(row.get("symbol") or "").upper()) or {}, external)
    for rows in (
        ((payload.get("portfolio_benchmark") or {}).get("action_queue") or []),
        (payload.get("approval_tickets") or []),
    ):
        for row in rows:
            if not isinstance(row, dict):
                continue
            feature = features_by_symbol.get(str(row.get("symbol") or "").upper())
            if feature:
                for key in EXTERNAL_RELIABILITY_KEYS:
                    if row.get(key) is None or row.get(key) == "":
                        row[key] = feature.get(key)
            fill_external_reliability(row, by_symbol.get(str(row.get("symbol") or "").upper()) or {}, external)


def normalize_public_outcome_diagnostics(payload: dict[str, Any]) -> None:
    diagnostics = payload.get("outcome_diagnostics") or {}
    backtest = payload.get("backtest") or {}
    as_of = parse_date(payload.get("as_of")) or date.today()
    due_dates_changed = normalize_public_backtest_due_dates(backtest)
    normalize_public_pending_external_summaries(backtest)
    schedule = diagnostics.get("pending_label_schedule")
    if due_dates_changed or not isinstance(schedule, dict) or not schedule:
        schedule = pending_label_schedule(backtest, as_of)
        if schedule.get("pending_label_count"):
            diagnostics["pending_label_schedule"] = schedule
    maturity = diagnostics.get("label_maturity") or {}
    if not isinstance(maturity, dict) or not maturity:
        maturity = label_maturity_from_backtest(backtest)
        if maturity:
            diagnostics["label_maturity"] = maturity
    horizon_counts = diagnostics.get("horizon_label_counts")
    if not isinstance(horizon_counts, list) or not horizon_counts:
        horizon_counts = horizon_label_counts_from_backtest(backtest)
        if horizon_counts:
            diagnostics["horizon_label_counts"] = horizon_counts
    projection = diagnostics.get("learning_readiness_projection")
    if maturity and (due_dates_changed or not isinstance(projection, dict) or not projection):
        diagnostics["learning_readiness_projection"] = learning_readiness_projection(maturity, schedule or {})
    external_projection = diagnostics.get("external_learning_readiness_projection")
    if due_dates_changed or not isinstance(external_projection, dict) or not external_projection:
        external_projection = external_learning_readiness_projection(backtest, as_of)
        if external_projection:
            diagnostics["external_learning_readiness_projection"] = external_projection
    approval_projection = diagnostics.get("approval_learning_readiness_projection")
    if due_dates_changed or not isinstance(approval_projection, dict) or not approval_projection:
        approval_projection = approval_learning_readiness_projection(backtest, as_of)
        if approval_projection:
            diagnostics["approval_learning_readiness_projection"] = approval_projection
    friction_projection = diagnostics.get("approval_data_friction_learning_readiness_projection")
    if due_dates_changed or not isinstance(friction_projection, dict) or not friction_projection:
        friction_projection = approval_data_friction_learning_readiness_projection(backtest, as_of)
        if friction_projection:
            diagnostics["approval_data_friction_learning_readiness_projection"] = friction_projection
    payload["outcome_diagnostics"] = diagnostics


def normalize_public_pending_external_summaries(backtest: dict[str, Any]) -> None:
    for section in ("outcomes", "recent_pending"):
        for row in backtest.get(section) or []:
            if isinstance(row, dict) and not row.get("external_alignment"):
                row["external_alignment"] = external_alignment_bucket(row)
    outcomes = [row for row in backtest.get("outcomes") or [] if isinstance(row, dict)]
    completed = [
        row for row in outcomes
        if row.get("status") == "complete"
    ]
    pending = [
        row for row in outcomes
        if row.get("status") == "pending"
    ]
    alignment_summary = backtest.get("by_external_alignment")
    if completed and (not isinstance(alignment_summary, list) or not alignment_summary):
        backtest["by_external_alignment"] = external_alignment_summaries(completed)
    gap_count = pending_external_coverage_gap_count(outcomes)
    backtest["pending_external_coverage_gap_count"] = gap_count
    backtest["pending_external_coverage_gap_queue"] = pending_external_coverage_gap_queue(outcomes)
    backtest["pending_external_coverage_gap_plan"] = pending_external_coverage_gap_plan(outcomes)
    if not pending:
        return
    status_summary = backtest.get("pending_by_external_feed_status")
    if not isinstance(status_summary, list) or not status_summary:
        backtest["pending_by_external_feed_status"] = pending_group_summaries(pending, "external_feed_status")
    coverage_summary = backtest.get("pending_by_external_coverage")
    if not isinstance(coverage_summary, list) or not coverage_summary:
        backtest["pending_by_external_coverage"] = pending_external_coverage_summaries(pending)
    pending_alignment_summary = backtest.get("pending_by_external_alignment")
    if not isinstance(pending_alignment_summary, list) or not pending_alignment_summary:
        backtest["pending_by_external_alignment"] = pending_external_alignment_summaries(pending)
    due_dates = backtest.get("pending_external_alignment_due_dates")
    if not isinstance(due_dates, list) or not due_dates:
        backtest["pending_external_alignment_due_dates"] = pending_external_alignment_due_dates(pending)
    watchlist = backtest.get("pending_external_alignment_watchlist")
    if not isinstance(watchlist, list) or not watchlist:
        backtest["pending_external_alignment_watchlist"] = pending_external_alignment_watchlist(pending)
    backtest["pending_external_alignment_review_count"] = pending_external_alignment_review_count(pending)
    review_item_count = pending_external_alignment_review_item_count(pending)
    review_queue = pending_external_alignment_review_queue(pending)
    backtest["pending_external_alignment_review_item_count"] = review_item_count
    backtest["pending_external_alignment_review_queue_limit"] = PENDING_EXTERNAL_ALIGNMENT_REVIEW_QUEUE_LIMIT
    backtest["pending_external_alignment_review_hidden_item_count"] = max(0, review_item_count - len(review_queue))
    backtest["pending_external_alignment_review_acceptance_summary"] = pending_external_alignment_review_acceptance_summary(pending)
    backtest["pending_external_alignment_review_due_dates"] = pending_external_alignment_review_due_dates(pending)
    backtest["pending_external_alignment_review_queue"] = review_queue
    measurement_gap_items = pending_external_alignment_measurement_gap_work_items(pending)
    measurement_gap_queue = pending_external_alignment_measurement_gap_queue(pending)
    backtest["pending_external_alignment_measurement_gap_label_count"] = sum(
        int(item.get("missing_label_count") or 0) for item in measurement_gap_items
    )
    backtest["pending_external_alignment_measurement_gap_item_count"] = len(measurement_gap_items)
    backtest["pending_external_alignment_measurement_gap_queue_limit"] = PENDING_EXTERNAL_ALIGNMENT_MEASUREMENT_GAP_QUEUE_LIMIT
    backtest["pending_external_alignment_measurement_gap_hidden_item_count"] = max(
        0,
        len(measurement_gap_items) - len(measurement_gap_queue),
    )
    backtest["pending_external_alignment_measurement_gap_plan"] = pending_external_alignment_measurement_gap_plan(pending)
    backtest["pending_external_alignment_measurement_gap_queue"] = measurement_gap_queue


def label_maturity_from_backtest(backtest: dict[str, Any]) -> dict[str, Any]:
    outcomes = [row for row in backtest.get("outcomes") or [] if isinstance(row, dict)]
    completed = [row for row in outcomes if row.get("status") == "complete"]
    long_completed = [row for row in completed if row.get("horizon") != "5d"]
    short_completed = [row for row in completed if row.get("horizon") == "5d"]
    pending_count = int_value(backtest.get("pending_outcome_count"))
    if not pending_count:
        pending_count = sum(1 for row in outcomes if row.get("status") == "pending")
    missing_count = int_value(backtest.get("missing_price_count"))
    if not missing_count:
        missing_count = sum(1 for row in outcomes if row.get("status") == "missing_price")
    if not outcomes and not pending_count and not missing_count:
        return {}
    return label_maturity(long_completed, short_completed, pending_count, missing_count)


def horizon_label_counts_from_backtest(backtest: dict[str, Any]) -> list[dict[str, Any]]:
    horizons = [
        {
            "horizon": row.get("horizon"),
            "completed_count": int_value(row.get("completed_count")),
            "pending_count": int_value(row.get("pending_count")),
            "missing_price_count": int_value(row.get("missing_price_count")),
        }
        for row in backtest.get("horizons") or []
        if isinstance(row, dict) and row.get("horizon")
    ]
    if horizons:
        return horizons

    by_horizon: dict[str, dict[str, Any]] = {}
    for row in backtest.get("outcomes") or []:
        if not isinstance(row, dict):
            continue
        horizon = str(row.get("horizon") or "")
        if not horizon:
            continue
        counts = by_horizon.setdefault(
            horizon,
            {"horizon": horizon, "completed_count": 0, "pending_count": 0, "missing_price_count": 0},
        )
        status = str(row.get("status") or "")
        if status == "complete":
            counts["completed_count"] += 1
        elif status == "pending":
            counts["pending_count"] += 1
        elif status == "missing_price":
            counts["missing_price_count"] += 1
    return list(by_horizon.values())


def normalize_public_backtest_due_dates(backtest: dict[str, Any]) -> bool:
    changed = False
    checked = 0
    for section in ("outcomes", "recent_pending"):
        for row in backtest.get(section) or []:
            if not isinstance(row, dict):
                continue
            as_of = parse_date(row.get("as_of"))
            horizon = str(row.get("horizon") or "")
            if as_of is None or not horizon:
                continue
            checked += 1
            try:
                due_date = estimated_label_due_date(as_of, horizon).isoformat()
            except (KeyError, TypeError, ValueError):
                continue
            if row.get("due_date") != due_date:
                row["due_date"] = due_date
                changed = True
    if checked:
        backtest["due_date_policy"] = "xnys_trading_days"
        backtest["due_date_policy_version"] = BACKTEST_VERSION
    return changed


EXTERNAL_RELIABILITY_KEYS = [
    "external_signal_score",
    "coverage_adjusted_external_signal_score",
    "external_coverage_multiplier",
    "external_feed_status",
    "external_provider_count",
    "external_provider_ok_count",
    "external_provider_ok_ratio",
    "external_provider_gap_count",
    "external_provider_configuration_gap_count",
    "external_provider_transient_gap_count",
    "external_provider_stale_gap_count",
    "external_provider_runtime_gap_count",
    "external_provider_other_gap_count",
    "external_provider_primary_gap_severity",
    "external_provider_gap_severity_score",
    "external_signal_count",
    "external_source_count",
]


def fill_external_reliability(row: dict[str, Any], symbol_external: dict[str, Any], global_external: dict[str, Any]) -> None:
    signal_score = numeric_value(row.get("external_signal_score"), symbol_external.get("external_signal_score"), 0.0)
    signal_count = int_value(row.get("external_signal_count"), symbol_external.get("signal_count"), 0)
    source_count = int_value(row.get("external_source_count"), symbol_external.get("source_count"), 0)
    provider_count = int_value(row.get("external_provider_count"), global_external.get("provider_count"), 0)
    provider_ok_count = int_value(row.get("external_provider_ok_count"), global_external.get("provider_ok_count"), 0)
    provider_ok_ratio = numeric_value(row.get("external_provider_ok_ratio"), global_external.get("provider_ok_ratio"), None)
    gap_features = external_provider_gap_features(global_external)
    feed_status = row.get("external_feed_status") or symbol_external.get("external_status") or symbol_external.get("status") or global_external.get("status") or "unknown"
    coverage_input = {
        "provider_count": provider_count,
        "provider_ok_count": provider_ok_count,
        "provider_ok_ratio": provider_ok_ratio,
        "external_status": feed_status,
    }
    coverage = numeric_value(row.get("external_coverage_multiplier"), None, external_coverage_multiplier(coverage_input))
    adjusted_score = numeric_value(row.get("coverage_adjusted_external_signal_score"), None, signal_score * coverage)
    row["external_signal_score"] = round(signal_score, 2)
    row["coverage_adjusted_external_signal_score"] = round(adjusted_score, 2)
    row["external_coverage_multiplier"] = round(coverage, 4)
    row["external_feed_status"] = str(feed_status)
    row["external_provider_count"] = provider_count
    row["external_provider_ok_count"] = provider_ok_count
    row["external_provider_ok_ratio"] = round(float(provider_ok_ratio or 0), 4)
    row["external_provider_gap_count"] = int_value(row.get("external_provider_gap_count"), gap_features.get("gap_count"), 0)
    row["external_provider_configuration_gap_count"] = int_value(row.get("external_provider_configuration_gap_count"), gap_features.get("configuration_gap_count"), 0)
    row["external_provider_transient_gap_count"] = int_value(row.get("external_provider_transient_gap_count"), gap_features.get("transient_gap_count"), 0)
    row["external_provider_stale_gap_count"] = int_value(row.get("external_provider_stale_gap_count"), gap_features.get("stale_gap_count"), 0)
    row["external_provider_runtime_gap_count"] = int_value(row.get("external_provider_runtime_gap_count"), gap_features.get("runtime_gap_count"), 0)
    row["external_provider_other_gap_count"] = int_value(row.get("external_provider_other_gap_count"), gap_features.get("other_gap_count"), 0)
    row["external_provider_primary_gap_severity"] = str(row.get("external_provider_primary_gap_severity") or gap_features.get("primary_gap_severity") or "")
    row["external_provider_gap_severity_score"] = numeric_value(row.get("external_provider_gap_severity_score"), gap_features.get("gap_severity_score"), 0.0)
    row["external_signal_count"] = signal_count
    row["external_source_count"] = source_count


def external_status_from_counts(status_counts: dict[str, int], signal_count: int) -> str:
    if not status_counts:
        return "missing"
    ok_count = status_counts.get("ok", 0)
    weak_count = sum(status_counts.get(status, 0) for status in ("limited", "missing", "failed", "error", "unknown"))
    if ok_count and not weak_count:
        return "ok"
    if ok_count or status_counts.get("limited") or signal_count:
        return "limited"
    return "missing"


def sync_external_signal_data_health(payload: dict[str, Any], external: dict[str, Any]) -> None:
    provider_count = int(external.get("provider_count") or 0)
    if not provider_count:
        return
    provider_gaps = external_provider_gap_rows(external, limit=None)
    approval_blockers = external_feed_approval_blockers(payload, external, provider_gaps)
    row = {
        "source": "external_signals",
        "label": "External signal feeds",
        "status": str(external.get("status") or "unknown"),
        "detail": external_provider_health_detail(external),
        "provider_gap_count": len(provider_gaps),
        "provider_gaps": provider_gaps[:8],
        "approval_blocked_external_gap_count": len(approval_blockers),
        "approval_blocked_external_gaps": approval_blockers[:8],
    }
    data_health = payload.setdefault("data_health", {})
    sources = [
        source for source in data_health.get("sources", [])
        if (source or {}).get("source") != "external_signals"
    ]
    sources.append(row)
    data_health["sources"] = sources
    weak_count = sum(1 for source in sources if source.get("status") in WEAK_SOURCE_STATUSES)
    data_health["weak_source_count"] = weak_count
    if not (payload.get("portfolio") or {}).get("position_count"):
        data_health["recommendation_posture"] = "research_only_until_positions_refresh"
    elif weak_count:
        data_health["recommendation_posture"] = "reduced_confidence"
    else:
        data_health["recommendation_posture"] = "normal"
    data_health["summary"] = (
        "Recommendations are constrained by data freshness and remain approval-only."
        if data_health["recommendation_posture"] != "normal"
        else "Core scheduled sources are available for this run."
    )


def external_feed_approval_blockers(
    payload: dict[str, Any],
    external: dict[str, Any],
    provider_gaps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    provider_sources = [str(row.get("source") or "") for row in provider_gaps if row.get("source")]
    provider_severities = sorted(
        {
            str(row.get("severity") or "")
            for row in provider_gaps
            if row.get("severity")
        }
    )
    blockers = []
    for ticket in payload.get("approval_tickets") or []:
        if not isinstance(ticket, dict):
            continue
        pending_checks = [
            str(check.get("check") or "")
            for check in ticket.get("approval_checks") or []
            if isinstance(check, dict) and check.get("status") != "passed" and check.get("check")
        ]
        feed_status = str(ticket.get("external_feed_status") or external.get("status") or "").strip().lower()
        if "external_feed_reliability_reviewed" not in pending_checks and feed_status == "ok":
            continue
        symbol = str(ticket.get("symbol") or "").upper()
        if not symbol:
            continue
        blockers.append(
            {
                "symbol": symbol,
                "ticket_id": ticket.get("ticket_id", ""),
                "trade_action": ticket.get("trade_action", ""),
                "recommended_delta_weight": ticket.get("recommended_delta_weight", 0),
                "external_feed_status": feed_status or "unknown",
                "external_provider_ok_ratio": ticket.get("external_provider_ok_ratio", external.get("provider_ok_ratio")),
                "approval_gate_status": ticket.get("approval_gate_status", ""),
                "approval_open_check_count": int(ticket.get("approval_open_check_count") or 0),
                "approval_blocking_checks": pending_checks,
                "provider_gap_count": len(provider_gaps),
                "provider_gap_sources": provider_sources[:8],
                "provider_gap_severities": provider_severities,
                "remediation": "Review provider gaps before treating external signals as high-confidence evidence for this ticket.",
            }
        )
    blockers.sort(key=external_feed_approval_blocker_sort_key)
    return blockers


def external_feed_approval_blocker_sort_key(row: dict[str, Any]) -> tuple[int, float, str]:
    gate_rank = 0 if row.get("approval_gate_status") == "blocked_until_confirmation" else 1
    try:
        delta = abs(float(row.get("recommended_delta_weight") or 0))
    except (TypeError, ValueError):
        delta = 0.0
    return (gate_rank, -delta, str(row.get("symbol") or ""))


def refresh_public_instrumentation_audit(public_payload: dict[str, Any]) -> None:
    normalize_public_counted_sections(public_payload)
    audit = public_payload.get("audit") or default_audit(public_payload)
    audit["source_freshness"] = source_freshness_from_data_health(public_payload.get("data_health") or {})
    gaps = [
        row for row in audit.get("data_gaps", [])
        if (row or {}).get("area") not in {"instrumentation", "source"}
    ]
    gaps = sync_learning_gap_from_outcome_diagnostics(gaps, public_payload)
    gaps.extend(source_gaps_from_data_health(public_payload.get("data_health") or {}))
    audit["data_gaps"] = gaps
    public_payload["audit"] = audit

    instrumentation_audit = build_instrumentation_audit(public_payload)
    public_payload["instrumentation_audit"] = sanitize_public_section(instrumentation_audit)
    audit["instrumentation_health"] = {
        "status": instrumentation_audit["status"],
        "check_count": instrumentation_audit["check_count"],
        "failure_count": instrumentation_audit["failure_count"],
    }
    if instrumentation_audit["status"] != "ok":
        audit["overall_status"] = "attention"
        gaps.extend(
            {
                "area": "instrumentation",
                "label": check.get("name", "number_wiring"),
                "status": check.get("status", "fail"),
                "detail": f"Observed {check.get('observed', check.get('missing_count', 'n/a'))} expected {check.get('expected', check.get('expected_max', 'n/a'))}",
            }
            for check in instrumentation_audit.get("failures", [])[:8]
        )
    audit["data_gaps"] = gaps
    public_payload["audit"] = audit


def sync_learning_gap_from_outcome_diagnostics(gaps: list[dict[str, Any]], payload: dict[str, Any]) -> list[dict[str, Any]]:
    engine = payload.get("engine") or {}
    learning = engine.get("learning") or {}
    is_learning_gap = lambda row: (row or {}).get("area") == "engine" and (row or {}).get("label") == "Learning reranker"
    refreshed = [row for row in gaps if not is_learning_gap(row)]
    if learning.get("status") != "baseline_fallback":
        return refreshed
    refreshed.append(
        {
            "area": "engine",
            "label": "Learning reranker",
            "status": "baseline_fallback",
            "detail": learning_gap_detail(learning, outcome_diagnostics_with_backtest_gap_plan(payload)),
        }
    )
    return refreshed


def outcome_diagnostics_with_backtest_gap_plan(payload: dict[str, Any]) -> dict[str, Any]:
    diagnostics = dict(payload.get("outcome_diagnostics") or {})
    gap_plan = (payload.get("backtest") or {}).get("pending_external_coverage_gap_plan")
    if isinstance(gap_plan, dict) and gap_plan:
        diagnostics["external_coverage_gap_plan"] = gap_plan
    return diagnostics


def source_freshness_from_data_health(data_health: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "source": source.get("source", ""),
            "label": source.get("label", ""),
            "status": source.get("status", "unknown"),
            "detail": source.get("detail", ""),
            "provider_gap_count": source.get("provider_gap_count", 0),
            "provider_gaps": source.get("provider_gaps", []),
            "approval_blocked_external_gap_count": source.get("approval_blocked_external_gap_count", 0),
            "approval_blocked_external_gaps": source.get("approval_blocked_external_gaps", []),
            "confirmation_gap_count": source.get("confirmation_gap_count", 0),
            "confirmation_gaps": source.get("confirmation_gaps", []),
            "action_linked_confirmation_gap_count": source.get("action_linked_confirmation_gap_count", 0),
        }
        for source in data_health.get("sources", [])
    ]


def source_gaps_from_data_health(data_health: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "area": "source",
            "label": source.get("label", ""),
            "status": source.get("status", ""),
            "detail": source.get("detail", ""),
            "provider_gap_count": source.get("provider_gap_count", 0),
            "provider_gaps": source.get("provider_gaps", []),
            "approval_blocked_external_gap_count": source.get("approval_blocked_external_gap_count", 0),
            "approval_blocked_external_gaps": source.get("approval_blocked_external_gaps", []),
            "confirmation_gap_count": source.get("confirmation_gap_count", 0),
            "confirmation_gaps": source.get("confirmation_gaps", []),
            "action_linked_confirmation_gap_count": source.get("action_linked_confirmation_gap_count", 0),
            "approval_blocked_confirmation_gap_count": source.get("approval_blocked_confirmation_gap_count", 0),
            "approval_blocked_confirmation_gaps": source.get("approval_blocked_confirmation_gaps", []),
        }
        for source in data_health.get("sources", [])
        if source.get("status") in WEAK_SOURCE_STATUSES
    ]


def normalize_public_earnings_health(public_payload: dict[str, Any]) -> None:
    events = public_payload.get("earnings_events") or ((public_payload.get("calendars") or {}).get("earnings") or {}).get("events") or []
    data_health = public_payload.setdefault("data_health", {})
    existing_sources = [row for row in data_health.get("sources", []) if isinstance(row, dict)]
    has_existing_earnings_source = any(row.get("source") == "earnings" for row in existing_sources)
    if not events and not has_existing_earnings_source:
        return
    health = earnings_health_summary(events)
    confirmation_gaps = enrich_earnings_confirmation_gaps(
        earnings_confirmation_gaps(events, limit=None),
        public_payload,
    )
    visible_confirmation_gaps = confirmation_gaps[:8]
    action_linked_confirmation_gap_count = sum(1 for row in confirmation_gaps if row.get("action_linked"))
    approval_blocked_confirmation_gaps = [
        row for row in confirmation_gaps
        if row.get("approval_gate_status") == "blocked_until_confirmation"
    ]
    visible_approval_blockers = approval_blocked_confirmation_gaps[:8]
    calendars = public_payload.setdefault("calendars", {})
    earnings = calendars.setdefault("earnings", {})
    earnings.update(
        {
            "events": events,
            "event_count": health["event_count"],
            "confirmed_count": health["confirmed_count"],
            "estimated_count": health["estimated_count"],
            "provider_date_count": health["provider_date_count"],
            "catalyst_marker_count": health["catalyst_marker_count"],
            "confirmation_gap_count": health["confirmation_gap_count"],
            "confirmation_gaps": visible_confirmation_gaps,
            "action_linked_confirmation_gap_count": action_linked_confirmation_gap_count,
            "approval_blocked_confirmation_gap_count": len(approval_blocked_confirmation_gaps),
            "approval_blocked_confirmation_gaps": visible_approval_blockers,
            "source_quality": health["source_quality"],
        }
    )
    sources = [
        row for row in existing_sources
        if row.get("source") != "earnings"
    ]
    detail = (
        f"{health['event_count']} events; "
        f"{health['provider_date_count']} forward date candidates; "
        f"{health['confirmed_count']} confirmed, {health['estimated_count']} estimated"
        f"{earnings_marker_detail(health)}."
        if events
        else "No manual, provider, IR, SEC, or news earnings markers available."
    )
    sources.append(
        {
            "source": "earnings",
            "label": "Earnings calendar",
            "status": health["status"],
            "detail": detail,
            "confirmation_gap_count": health["confirmation_gap_count"],
            "confirmation_gaps": visible_confirmation_gaps,
            "action_linked_confirmation_gap_count": action_linked_confirmation_gap_count,
            "approval_blocked_confirmation_gap_count": len(approval_blocked_confirmation_gaps),
            "approval_blocked_confirmation_gaps": visible_approval_blockers,
        }
    )
    data_health["sources"] = sources
    data_health["weak_source_count"] = sum(1 for row in sources if row.get("status") in WEAK_SOURCE_STATUSES)
    if data_health["weak_source_count"]:
        data_health["recommendation_posture"] = "reduced_confidence"
        data_health["summary"] = "Recommendations are constrained by data freshness and remain approval-only."


def sync_data_health_approval_blocker_summary(public_payload: dict[str, Any]) -> None:
    data_health = public_payload.setdefault("data_health", {})
    sources = [row for row in data_health.get("sources", []) if isinstance(row, dict)]
    external_rows = approval_blocker_rows(sources, "approval_blocked_external_gaps")
    confirmation_rows = approval_blocker_rows(sources, "approval_blocked_confirmation_gaps")
    external_count = sum(int_value(source.get("approval_blocked_external_gap_count")) for source in sources)
    confirmation_count = sum(int_value(source.get("approval_blocked_confirmation_gap_count")) for source in sources)

    blockers: dict[str, dict[str, Any]] = {}
    for row in external_rows:
        add_approval_blocker(blockers, row, "external_provider_gap")
    for row in confirmation_rows:
        add_approval_blocker(blockers, row, "earnings_confirmation")

    open_check_counts: dict[str, int] = {}
    for blocker in blockers.values():
        for check in blocker.get("approval_blocking_checks", set()):
            increment_count(open_check_counts, check)

    provider_gap_source_counts: dict[str, int] = {}
    provider_gap_severity_counts: dict[str, int] = {}
    for row in external_rows:
        for source in row.get("provider_gap_sources") or []:
            increment_count(provider_gap_source_counts, source)
        for severity in row.get("provider_gap_severities") or []:
            increment_count(provider_gap_severity_counts, severity)

    confirmation_priority_counts: dict[str, int] = {}
    confirmation_deadline_symbols: dict[str, set[str]] = {}
    for row in confirmation_rows:
        priority = str(row.get("confirmation_priority") or "")
        if priority:
            increment_count(confirmation_priority_counts, priority)
        deadline = str(row.get("confirmation_deadline") or "")
        symbol = str(row.get("symbol") or "").upper()
        if deadline:
            confirmation_deadline_symbols.setdefault(deadline, set())
            if symbol:
                confirmation_deadline_symbols[deadline].add(symbol)

    next_deadline = min(confirmation_deadline_symbols) if confirmation_deadline_symbols else None
    blocked_symbols = sorted(
        {
            str(blocker.get("symbol") or "").upper()
            for blocker in blockers.values()
            if blocker.get("symbol")
        }
    )
    data_health["approval_blocker_summary"] = {
        "status": "attention" if external_count or confirmation_count else "ok",
        "total_source_blocker_count": external_count + confirmation_count,
        "external_gap_ticket_count": external_count,
        "earnings_confirmation_ticket_count": confirmation_count,
        "visible_blocker_row_count": len(external_rows) + len(confirmation_rows),
        "blocked_ticket_count": len(blockers),
        "blocked_symbols": blocked_symbols,
        "open_check_count": sum(int(count) for count in open_check_counts.values()),
        "open_check_counts": dict(sorted(open_check_counts.items())),
        "provider_gap_source_counts": dict(sorted(provider_gap_source_counts.items())),
        "provider_gap_severity_counts": dict(sorted(provider_gap_severity_counts.items())),
        "confirmation_priority_counts": dict(sorted(confirmation_priority_counts.items())),
        "next_confirmation_deadline": next_deadline,
        "next_confirmation_symbols": sorted(confirmation_deadline_symbols.get(next_deadline, set())) if next_deadline else [],
    }


def approval_blocker_rows(sources: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in sources:
        for row in source.get(key) or []:
            if isinstance(row, dict):
                rows.append(row)
    return rows


def add_approval_blocker(blockers: dict[str, dict[str, Any]], row: dict[str, Any], blocker_type: str) -> None:
    symbol = str(row.get("symbol") or "").upper()
    ticket_id = str(row.get("ticket_id") or "").strip()
    key = ticket_id or f"{blocker_type}:{symbol}"
    if not key:
        return
    blocker = blockers.setdefault(
        key,
        {
            "ticket_id": ticket_id,
            "symbol": symbol,
            "approval_gate_status": "",
            "approval_open_check_count": 0,
            "approval_blocking_checks": set(),
            "blocker_types": set(),
        },
    )
    if ticket_id and not blocker.get("ticket_id"):
        blocker["ticket_id"] = ticket_id
    if symbol and not blocker.get("symbol"):
        blocker["symbol"] = symbol
    gate_status = str(row.get("approval_gate_status") or "")
    if gate_status == "blocked_until_confirmation" or not blocker.get("approval_gate_status"):
        blocker["approval_gate_status"] = gate_status
    blocker["approval_open_check_count"] = max(
        int_value(blocker.get("approval_open_check_count")),
        int_value(row.get("approval_open_check_count")),
    )
    for check in row.get("approval_blocking_checks") or []:
        check_name = str(check or "")
        if check_name:
            blocker["approval_blocking_checks"].add(check_name)
    blocker["blocker_types"].add(blocker_type)


def increment_count(counts: dict[str, int], key: Any) -> None:
    item = str(key or "")
    if item:
        counts[item] = counts.get(item, 0) + 1


def enrich_earnings_confirmation_gaps(gaps: list[dict[str, Any]], public_payload: dict[str, Any]) -> list[dict[str, Any]]:
    action_by_symbol = action_queue_by_symbol(public_payload)
    ticket_by_symbol = approval_ticket_by_symbol(public_payload)
    enriched = []
    for gap in gaps:
        row = dict(gap)
        symbol = str(row.get("symbol") or "").upper()
        action = action_by_symbol.get(symbol)
        if action:
            row["action_linked"] = True
            row["trade_action"] = action.get("trade_action", "")
            row["recommended_delta_weight"] = action.get("recommended_delta_weight", 0)
            row["action_risk_flags"] = action.get("risk_flags", [])
            row["action_confirmation_required"] = bool(action.get("earnings_confirmation_required", False))
            row["remediation"] = action_linked_confirmation_remediation(row)
        else:
            row["action_linked"] = False
        ticket = ticket_by_symbol.get(symbol)
        if ticket:
            row["approval_ticket_linked"] = True
            row["ticket_id"] = ticket.get("ticket_id", "")
            row["approval_gate_status"] = ticket.get("approval_gate_status", "")
            row["approval_open_check_count"] = int(ticket.get("approval_open_check_count") or 0)
            row["approval_blocking_checks"] = [
                str(check.get("check") or "")
                for check in ticket.get("approval_checks") or []
                if isinstance(check, dict) and check.get("status") != "passed" and check.get("check")
            ]
        else:
            row["approval_ticket_linked"] = False
        enriched.append(row)
    enriched.sort(key=earnings_confirmation_gap_sort_key)
    return enriched


def action_queue_by_symbol(public_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = ((public_payload.get("portfolio_benchmark") or {}).get("action_queue") or [])
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").upper()
        if not symbol:
            continue
        for candidate in equivalent_symbols(symbol):
            indexed.setdefault(candidate, row)
    return indexed


def approval_ticket_by_symbol(public_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = public_payload.get("approval_tickets") or []
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").upper()
        if not symbol:
            continue
        for candidate in equivalent_symbols(symbol):
            indexed.setdefault(candidate, row)
    return indexed


def earnings_confirmation_gap_sort_key(row: dict[str, Any]) -> tuple[int, int, int, int, str]:
    priority_order = {
        "p0_blackout_confirmation": 0,
        "p1_risk_window_confirmation": 1,
        "p2_pre_risk_window_backfill": 2,
        "p3_scheduled_backfill": 3,
    }
    days_to_deadline = row.get("days_to_confirmation_deadline")
    try:
        deadline_days = int(days_to_deadline)
    except (TypeError, ValueError):
        deadline_days = 9999
    try:
        days_until = abs(int(row.get("days_until") or 9999))
    except (TypeError, ValueError):
        days_until = 9999
    return (
        priority_order.get(str(row.get("confirmation_priority") or ""), 4),
        deadline_days,
        0 if row.get("action_linked") else 1,
        days_until,
        str(row.get("symbol") or ""),
    )


def action_linked_confirmation_remediation(row: dict[str, Any]) -> str:
    trade_action = str(row.get("trade_action") or "trade")
    event_date = str(row.get("event_date") or "the estimated date")
    return (
        "Confirm the earnings date via company IR or a manual event before approving "
        f"the current {trade_action} ticket tied to {event_date}."
    )


def earnings_marker_detail(earnings_health: dict[str, Any]) -> str:
    marker_count = int(earnings_health.get("catalyst_marker_count") or 0)
    return f"; {marker_count} catalyst markers" if marker_count else ""


def normalize_public_counted_sections(public_payload: dict[str, Any]) -> None:
    for key in ("company_underwriting", "sector_underwriting", "research_book"):
        section = public_payload.get(key)
        if isinstance(section, dict):
            section.setdefault("items", [])
            section.setdefault("item_count", len(section.get("items") or []))
    feature_matrix = public_payload.get("feature_matrix")
    if isinstance(feature_matrix, dict):
        feature_matrix.setdefault("rows", [])
        feature_matrix.setdefault("feature_count", len(feature_matrix.get("rows") or []))


def numeric_value(*values: Any) -> float:
    for value in values:
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def int_value(*values: Any) -> int:
    return int(numeric_value(*values))


def sanitize_portfolio(portfolio: dict[str, Any]) -> dict[str, Any]:
    cash_weight = round(float(portfolio.get("cash_weight") or 0), 6)
    equity_weight = round(float(portfolio.get("equity_weight", 1.0 - cash_weight) or 0), 6)
    return {
        "display_name": PORTFOLIO_DISPLAY_NAME,
        "private_redacted": True,
        "value_basis": "weights_only",
        "weight_basis": portfolio.get("comparison_weight_basis", "invested_equity_ex_cash"),
        "total_weight_basis": portfolio.get("weight_basis", "total_portfolio_including_cash"),
        "position_count": portfolio.get("position_count", 0),
        "symbol_count": portfolio.get("symbol_count", 0),
        "security_symbol_count": portfolio.get("security_symbol_count", portfolio.get("symbol_count", 0)),
        "cash_weight": cash_weight,
        "equity_weight": equity_weight,
        "cash_reserves": {
            "symbol": "CASH",
            "bucket": "cash_reserves",
            "asset_class": "cash",
            "weight": cash_weight,
            "policy": "available_for_capped_high_conviction_adds",
        },
        "by_bucket": [
            {
                "bucket": row.get("bucket", "unmapped"),
                "weight": round(public_comparison_weight(row, equity_weight), 6),
                "total_weight": round(float(row.get("total_weight", row.get("weight") or 0) or 0), 6),
            }
            for row in portfolio.get("by_bucket", [])
            if row.get("bucket") != "cash_reserves"
        ],
        "by_symbol": [
            {
                "symbol": row.get("symbol", ""),
                "bucket": row.get("bucket", "unmapped"),
                "asset_class": row.get("asset_class", "cash" if row.get("is_cash") else "equity"),
                "is_cash": bool(row.get("is_cash", False)),
                "weight": round(public_comparison_weight(row, equity_weight), 6),
                "total_weight": round(float(row.get("total_weight", row.get("weight") or 0) or 0), 6),
            }
            for row in portfolio.get("by_symbol", [])
            if not row.get("is_cash")
        ],
    }


def sanitize_portfolio_benchmark(benchmark: dict[str, Any]) -> dict[str, Any]:
    clean = dict(benchmark)
    clean["benchmarks"] = [sanitize_benchmark(row) for row in clean.get("benchmarks", [])]
    clean["peer_proxies"] = [sanitize_peer_proxy(row) for row in clean.get("peer_proxies", [])]
    clean["action_queue"] = [
        sanitize_public_trading_row(row) for row in clean.get("action_queue", [])
    ]
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
    if isinstance(clean.get("ai_maxxi_valuation"), dict):
        clean["ai_maxxi_valuation"] = sanitize_methodology_terms(strip_private_keys(clean["ai_maxxi_valuation"]))
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
    if clean.get("manager_key") == "d1-capital":
        clean["manager_tier"] = "tier_2"
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
    clean = {
        "rank": int(position.get("rank") or 0),
        "symbol": position.get("symbol", ""),
        "issuer": position.get("issuer", ""),
        "bucket": position.get("bucket", "unmapped"),
        "fund_weight": round(float(position.get("fund_weight") or 0), 6),
        "portfolio_weight": round(float(position.get("portfolio_weight") or 0), 6),
    }
    for key in (
        "reported_amount",
        "latest_report_price",
        "entry_price_estimate",
        "current_price",
        "current_value_estimate",
        "entry_return_estimate_pct",
        "value_change_since_report_pct",
        "valuation_confidence",
        "valuation_method",
        "observed_quarters",
        "excluded_quarters",
        "first_seen_report_date",
        "source",
    ):
        if key in position:
            clean[key] = position.get(key)
    return clean


def build_public_focus_manager_groups(focus_managers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups = [
        {
            "key": "tier_1",
            "label": manager_group_label("tier_1"),
            "description": "Situational Awareness / Leopold, Altimeter, and Dragoneer.",
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
    weights: dict[str, float] = {}
    for row in portfolio.get("by_symbol", []):
        symbol = str(row.get("symbol", "")).upper()
        if not symbol or row.get("is_cash"):
            continue
        weight = comparison_weight(row)
        for candidate in equivalent_symbols(symbol):
            weights[candidate] = weights.get(candidate, 0.0) + weight
    return weights


def comparison_weight(row: dict[str, Any]) -> float:
    return float(row.get("comparison_weight", row.get("ex_cash_weight", row.get("weight") or 0)) or 0)


def public_comparison_weight(row: dict[str, Any], equity_weight: float) -> float:
    if row.get("comparison_weight") is not None or row.get("ex_cash_weight") is not None:
        return comparison_weight(row)
    weight = float(row.get("weight") or 0)
    if equity_weight > 0 and not row.get("is_cash") and row.get("bucket") != "cash_reserves":
        return weight / equity_weight
    return weight


def portfolio_weight_by_bucket(portfolio: dict[str, Any]) -> dict[str, float]:
    return {
        str(row.get("bucket", "unmapped")): comparison_weight(row)
        for row in portfolio.get("by_bucket", [])
        if row.get("bucket") and row.get("bucket") != "cash_reserves"
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
        clean["type"] = "portfolio-context study"
        clean["setup"] = "Portfolio context is redacted in the public build; evaluate this symbol against your own exposure."
    else:
        clean["type"] = public_trading_copy(str(clean.get("type", idea_type)))
    return clean


def sanitize_weekly_research(research: dict[str, Any]) -> dict[str, Any]:
    clean = public_trading_copy(strip_private_keys(research))
    clean["ideas"] = [public_trading_copy(strip_private_keys(idea)) for idea in clean.get("ideas", [])]
    return clean


def sanitize_approval_tickets(tickets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    public_tickets = []
    for ticket in tickets:
        clean = strip_private_keys(ticket)
        clean.pop("order_execution", None)
        clean["approval_required"] = True
        clean["sizing_basis"] = "approval-only portfolio-weight target delta for the trade feed"
        clean = public_trading_copy(clean)
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
    clean = public_trading_copy(strip_private_keys(health))
    if clean.get("recommendation_posture") == "research_only_until_positions_refresh":
        clean["recommendation_posture"] = "positions_refresh_needed"
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


def sanitize_public_section(section: dict[str, Any]) -> dict[str, Any]:
    return public_trading_copy(strip_private_keys(section))


def sanitize_methodology(methodology: dict[str, Any]) -> dict[str, Any]:
    clean = sanitize_methodology_terms(strip_private_keys(methodology))
    clean["updated_by_backend"] = bool(clean.get("updated_by_backend", False))
    return clean


def sanitize_methodology_terms(value: Any) -> Any:
    replacements = {
        "account": "account identifiers",
        "accounts": "account identifiers",
        "broker": "broker names",
        "brokers": "broker names",
        "quantity": "private position units",
        "shares": "share counts",
        "cost_basis": "cost basis",
        "market_value": "market value",
        "estimated_notional": "estimated notional",
        "estimated_shares": "estimated share count",
        "raw_json": "raw private payload",
    }
    if isinstance(value, dict):
        return {key: sanitize_methodology_terms(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_methodology_terms(item) for item in value]
    if isinstance(value, str):
        clean = replacements.get(value, value)
        clean = clean.replace("quantity", "private position units")
        return public_trading_copy(clean)
    return value


def sanitize_public_trading_row(row: dict[str, Any]) -> dict[str, Any]:
    clean = public_trading_copy(strip_private_keys(row))
    if clean.get("trade_action") == "hold_hedge":
        clean["trade_action"] = "hold"
    if "sizing_basis" in clean:
        clean["sizing_basis"] = "approval-only portfolio-weight target delta for the trade feed"
    if "action" in clean:
        clean["action"] = public_trading_copy(clean["action"])
    if "sizing_summary" in clean:
        clean["sizing_summary"] = public_trading_copy(clean["sizing_summary"])
    return clean


def public_trading_copy(value: Any) -> Any:
    replacements = (
        ("Research only. No order execution and no personalized financial advice.", "Public weights, public filings, daily AI markets signals. Approval-only; no live order execution."),
        ("AlloIQ ranks watchlist names by independent public-market signal families, constrains sizing with portfolio risk limits, and publishes approval-only portfolio-weight research proposals.", "AlloIQ ranks watchlist names by independent public-market signal families, constrains sizing with portfolio risk limits, and publishes approval-only portfolio-weight trade targets."),
        ("portfolio-weight research proposal; approval required; no order execution", "approval-only portfolio-weight target delta for the trade feed; no live order execution"),
        ("portfolio-weight research proposal; not an execution order", "approval-only portfolio-weight target delta for the trade feed; no live order execution"),
        ("research proposals", "trades"),
        ("Research proposals", "Trades"),
        ("research proposal", "trade"),
        ("Research proposal", "Trade"),
        ("proposal set", "trade set"),
        ("starter-weight proposal", "starter target"),
        ("size any hedge at", "keep risk budget at"),
        ("Hedge existing exposure", "Hold with risk budget"),
        ("Hedge watch", "Risk watch"),
        ("Add-on-dip research", "Add on pullback"),
        ("Catalyst-confirmed research", "Catalyst-confirmed starter"),
        ("White-space long research", "White-space long"),
        ("Weekly Idea Research", "Weekly Study Queue"),
        ("weekly idea research", "weekly study queue"),
        ("research queue", "study queue"),
        ("Research", "Study"),
        ("research", "study"),
    )
    if isinstance(value, dict):
        return {key: public_trading_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [public_trading_copy(item) for item in value]
    if isinstance(value, str):
        text = value
        for old, new in replacements:
            text = text.replace(old, new)
        return text
    return value


def default_methodology(payload: dict[str, Any]) -> dict[str, Any]:
    benchmark = payload.get("portfolio_benchmark") or {}
    signal_synthesis = payload.get("signal_synthesis") or {}
    data_health = payload.get("data_health") or default_data_health(payload)
    action_queue = benchmark.get("action_queue") or []
    flags = sorted(
        {
            str(flag)
            for action in action_queue
            for flag in action.get("risk_flags", [])
            if str(flag).strip()
        }
    )
    score_keys = sorted(
        {
            key
            for card in payload.get("decision_cards", [])
            for key in (card.get("score_components") or {}).keys()
        }
    ) or ["manager", "catalyst", "portfolio_fit", "price_action", "option_tilt"]
    return {
        "version": "derived-from-public-snapshot",
        "updated_by_backend": False,
        "session": payload.get("session", ""),
        "summary": "AlloIQ ranks watchlist names by independent public-market signal families, constrains sizing with portfolio risk limits, and publishes portfolio-weight trade targets.",
        "pipeline": {
            "cadence": [
                {"kind": "premarket", "when": "8:00 AM ET on NYSE trading days", "purpose": "Pull the live IBKR Flex feed once, then refresh filings, overnight catalysts, macro tape, and trade tickets before the open."},
                {"kind": "market_open", "when": "9:30 AM ET on NYSE trading days", "purpose": "Reuse stored broker positions while refreshing live open prices, risk moves, and opening-bell add/trim changes."},
                {"kind": "intraday", "when": "10:00 AM, 11:00 AM, 12:00 PM, 2:00 PM, and 3:00 PM ET on NYSE trading days", "purpose": "Reuse stored broker positions while refreshing hourly price action, catalyst changes, risk gates, and recommendation deltas."},
                {"kind": "midday", "when": "1:00 PM ET on NYSE trading days", "purpose": "Reuse stored broker positions while refreshing intraday price moves, catalysts, risk gates, and add/trim tickets."},
                {"kind": "market_close", "when": "4:00 PM ET on NYSE trading days", "purpose": "Reuse stored broker positions while refreshing close-of-session prices, risk changes, and urgent add/trim alerts."},
                {"kind": "postmarket", "when": "4:30 PM ET on NYSE trading days", "purpose": "Pull the live IBKR Flex feed again, then refresh end-of-day price action, attribution, catalysts, and follow-up ticket state."},
                {"kind": "weekly", "when": "Sunday morning ET", "purpose": "Reuse stored broker positions while running full idea research, thesis/falsifier review, and weekly opportunity/risk queue."},
            ],
            "steps": [
                {"key": "filings", "label": "SEC 13F refresh", "source": "Public EDGAR manager filings"},
                {"key": "broker_sync", "label": "Private position sync", "source": "IBKR Flex premarket and postmarket only plus optional manual sleeves"},
                {"key": "news", "label": "Catalyst classification", "source": "Configured RSS/news queries and event rules"},
                {"key": "prices", "label": "Price and return windows", "source": "Public chart data for watchlist and macro symbols"},
                {"key": "earnings", "label": "Earnings and filing windows", "source": "Manual dates, company IR feeds, Alpha Vantage/Nasdaq expected-date providers, SEC company submissions, and news-derived guidance signals"},
                {"key": "risk", "label": "Risk and sizing controls", "source": "Configured portfolio limits before publishing tickets"},
                {"key": "privacy", "label": "Public sanitizer", "source": "Weights-only JSON and privacy scan"},
                {"key": "warehouse", "label": "Private warehouse sync", "source": "Neon Postgres run history and decision ledger"},
            ],
        },
        "current_run": {
            "recommendation_posture": data_health.get("recommendation_posture", "unknown"),
            "confirmed_card_count": signal_synthesis.get("confirmed_card_count", 0),
            "dominant_signal_families": signal_synthesis.get("dominant_families", []),
            "open_approval_ticket_count": len(payload.get("approval_tickets") or []),
            "earnings_event_count": len(payload.get("earnings_events") or []),
            "source_statuses": data_health.get("sources", []),
        },
        "scoring_model": {
            "score_components_seen": score_keys,
            "components": [
                {"key": "manager", "max_points": 25, "rule": "Tracked-manager overlap, consensus holder count, primary-manager exposure, and option tilt from public 13F data."},
                {"key": "catalyst", "max_points": 20, "rule": "Classified news events such as capex signals, contract wins, financing risk, regulatory risk, supply constraints, and earnings revisions."},
                {"key": "portfolio_fit", "max_points": 12, "rule": "Current portfolio ownership or strong manager consensus gives context for add, trim, hold, or white-space review."},
                {"key": "price_action", "max_points": 10, "rule": "Recent price movement gates entry discipline; moderate strength and large drawdowns are treated differently."},
                {"key": "option_tilt", "max_points": 5, "rule": "Call-heavy public filings can add support; put-heavy filings can subtract or force risk review."},
            ],
            "promotion_rules": [
                "Names with at least two independent signal families are eligible for higher-priority study.",
                "Owned names with financing, regulatory, crowding, or put-heavy risk can override add logic into trim, hold, or review.",
                "Every recommendation carries a trigger, risk, and falsifier.",
            ],
        },
        "risk_and_sizing": {
            "constraint_flags_observed": flags,
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


def default_calendars(payload: dict[str, Any]) -> dict[str, Any]:
    events = payload.get("earnings_events") or []
    return {
        "version": "derived-from-public-snapshot",
        "as_of": payload.get("as_of", ""),
        "earnings": {
            "events": events,
            "event_count": len(events),
            "source_quality": "ok" if events else "limited",
            "policy": "Manual and company IR dates are canonical; Alpha Vantage and Nasdaq provide estimated forward dates; SEC/result markers and news-derived events enrich risk windows.",
        },
        "filings_13f": {
            "rule": "Form 13F is due within 45 days after each calendar quarter end; weekend/holiday deadlines move to the next NYSE business day.",
            "rule_source": "https://www.sec.gov/divisions/investment/13ffaq.htm",
            "current_cycle": {},
            "managers": [],
            "manager_count": 0,
            "filed_count": 0,
            "pending_count": 0,
            "late_count": 0,
        },
    }


def default_engine(payload: dict[str, Any]) -> dict[str, Any]:
    cards = payload.get("decision_cards") or []
    return {
        "version": "derived-from-public-snapshot",
        "mode": "approval_plus_paper",
        "universe": "equities_only",
        "objective": "maximize_expected_3_12m_forward_return",
        "live_order_execution": "disabled",
        "learning": {
            "status": "baseline_fallback",
            "outcome_count": 0,
            "minimum_required": 20,
            "weight_adjustments": {},
        },
        "feature_count": len(cards),
        "ranked_candidates": [
            {
                "rank": index,
                "symbol": card.get("symbol", ""),
                "bucket": card.get("bucket", "unmapped"),
                "expected_return_rank_score": card.get("score", 0),
                "signal_families": card.get("signal_families", []),
            }
            for index, card in enumerate(cards[:20], start=1)
        ],
        "recommendation_provenance": [],
        "optimizer": {"type": "long_only_weight_optimizer", "allocations": []},
    }


def default_paper_portfolio(payload: dict[str, Any]) -> dict[str, Any]:
    tickets = payload.get("approval_tickets") or []
    return {
        "version": "derived-from-public-snapshot",
        "mode": "paper_only",
        "live_order_execution": "disabled",
        "fill_policy": "next_available_daily_close_proxy",
        "paper_trades": [],
        "snapshots": [],
        "metrics": {"paper_trade_count": len(tickets), "status": "derived"},
    }


def default_audit(payload: dict[str, Any]) -> dict[str, Any]:
    health = payload.get("data_health") or default_data_health(payload)
    engine = payload.get("engine") or default_engine(payload)
    calendars = payload.get("calendars") or default_calendars(payload)
    return {
        "version": "derived-from-public-snapshot",
        "as_of": payload.get("as_of", ""),
        "session": payload.get("session", ""),
        "overall_status": "ok",
        "engine_version": engine.get("version", ""),
        "privacy_scan": {"status": "required_after_build", "scope": "public web assets"},
        "source_freshness": health.get("sources", []),
        "calendar_health": {
            "earnings_event_count": (calendars.get("earnings") or {}).get("event_count", 0),
            "filing_deadline": ((calendars.get("filings_13f") or {}).get("current_cycle") or {}).get("deadline", ""),
        },
        "engine_health": {
            "learning_status": (engine.get("learning") or {}).get("status", "baseline_fallback"),
            "feature_count": engine.get("feature_count", 0),
            "live_order_execution": "disabled",
        },
        "data_gaps": [],
    }


def default_external_signals(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": "derived-from-public-snapshot",
        "as_of": payload.get("as_of", ""),
        "status": "limited",
        "summary": "No external signal provider snapshot was bundled with this public report.",
        "provider_count": 0,
        "signal_count": 0,
        "source_statuses": [],
        "top_signals": [],
        "by_symbol": {},
        "global": {},
    }


def default_data_health(payload: dict[str, Any]) -> dict[str, Any]:
    portfolio = payload.get("portfolio") or {}
    manager_radar = payload.get("manager_radar") or {}
    news = payload.get("news") or []
    prices = [card for card in payload.get("decision_cards", []) if card.get("last_price") is not None]
    return {
        "recommendation_posture": "normal" if portfolio.get("position_count") else "positions_refresh_needed",
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
        "private_note",
        "private_notes",
        "positions",
        "quantity",
        "raw",
        "raw_json",
        "raw_prompt",
        "request_payload",
        "shares",
        "prompt_text",
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
        return "study candidate"
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
        action = "Hold with risk budget"
        posture = "Cautious"
        rationale = "The Geoffrey Woo Portfolio owns this name, and tracked filings show meaningful put exposure against it."
    elif put_value > max(call_value * 1.25, 50_000_000):
        action = "Risk watch"
        posture = "Cautious"
        rationale = "Tracked filings show meaningful put exposure against the name despite manager ownership."
    elif portfolio_weight >= 0.12 and manager_count >= 3 and score >= 38:
        action = "Core position review"
        posture = "Size discipline"
        rationale = "The Geoffrey Woo Portfolio already has a large weight here; compare incremental upside against concentration risk."
    elif portfolio_weight > 0 and manager_count >= 3 and score >= 38 and move_pct < 8:
        action = "Add on pullback"
        posture = "Constructive"
        rationale = "The Geoffrey Woo Portfolio owns this name, manager consensus is strong, and recent price action is not yet extreme."
    elif portfolio_weight == 0 and manager_count >= 2 and constructive_event and len(signal_families) >= 2 and move_pct < 8:
        action = "Catalyst-confirmed starter"
        posture = "Constructive"
        rationale = "The Geoffrey Woo Portfolio has no current weight, while manager signal and classified catalysts both confirm the setup."
    elif portfolio_weight == 0 and manager_count >= 3 and score >= 38 and move_pct < 8:
        action = "White-space long"
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
        posture = "Study"
        rationale = "Manager overlap is enough to justify work, but evidence is not strong enough for an urgent move."
    else:
        action = "Monitor"
        posture = "Low urgency"
        rationale = "The signal is present but not yet differentiated."
    if bucket_weight >= 0.30 and portfolio_weight == 0 and action in {"White-space long", "Deep-dive queue"}:
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
    if action == "Risk watch":
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


def stale_status(run_kind: str, built_at: str, report_as_of: Any | None = None) -> dict[str, Any]:
    if run_kind in {"market_open", "intraday", "market_close"}:
        max_age_hours = 2
    elif run_kind == "weekly":
        max_age_hours = 192
    else:
        max_age_hours = 20
    max_report_age_days = 7 if run_kind == "weekly" else 0
    status = {
        "status": "fresh",
        "is_stale_at_build": False,
        "built_at": built_at,
        "max_age_hours": max_age_hours,
        "max_report_age_days": max_report_age_days,
        "policy": "client marks stale when built_at exceeds max_age_hours; build marks stale when report_as_of lags the market-date window",
    }
    report_date = parse_date(report_as_of)
    if not report_date:
        status["reason"] = "missing report_as_of; client will still enforce build-age freshness"
        return status
    built_dt = parse_site_datetime(built_at)
    market_dt = (built_dt or datetime.now(timezone.utc)).astimezone(EASTERN)
    market_date = market_dt.date()
    expected_report_date = expected_report_as_of(run_kind, market_dt)
    report_age_days = (market_date - report_date).days
    report_lag_days = max(0, (expected_report_date - report_date).days)
    status.update(
        {
            "report_as_of": report_date.isoformat(),
            "market_date_at_build": market_date.isoformat(),
            "expected_report_as_of": expected_report_date.isoformat(),
            "report_age_days": report_age_days,
            "report_lag_days": report_lag_days,
        }
    )
    if report_lag_days > max_report_age_days:
        status.update(
            {
                "status": "stale",
                "is_stale_at_build": True,
                "reason": (
                    f"report_as_of {report_date.isoformat()} is before expected report date "
                    f"{expected_report_date.isoformat()}; allowed lag is {max_report_age_days} days"
                ),
            }
        )
    else:
        status["reason"] = "report_as_of is within the expected build window"
    return status


def expected_report_as_of(run_kind: str, market_dt: datetime) -> date:
    if run_kind == "weekly":
        return market_dt.date()
    return latest_nyse_trading_date(market_dt)


def latest_nyse_trading_date(market_dt: datetime) -> date:
    probe = market_dt
    for _ in range(10):
        if is_nyse_trading_day(probe):
            return probe.date()
        probe = probe - timedelta(days=1)
    return market_dt.date()


def utc_timestamp(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_site_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def serve_site(out_dir: Path = DEFAULT_WEB_DIR, host: str = "", port: int = 4173) -> int:
    web_dir = out_dir.resolve()
    ensure_dir(web_dir)
    ensure_static_assets(web_dir)

    class CleanUrlHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(web_dir), **kwargs)

        def translate_path(self, path: str) -> str:
            parsed = urlsplit(path)
            route = unquote(parsed.path)
            if route == "/":
                route = "/index.html"
            elif "." not in Path(route).name:
                candidate = (web_dir / route.lstrip("/")).with_suffix(".html")
                if candidate.exists():
                    route = "/" + candidate.relative_to(web_dir).as_posix()
            return super().translate_path(route)

        def end_headers(self) -> None:
            if urlsplit(self.path).path.startswith("/data/"):
                self.send_header("Cache-Control", "no-store, max-age=0")
            super().end_headers()

    display_host = host or "127.0.0.1"
    httpd: ThreadingHTTPServer | None = None
    bound_port = port
    for candidate_port in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer((host, candidate_port), CleanUrlHandler)
            bound_port = candidate_port
            break
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE or candidate_port == port + 19:
                raise
            if candidate_port == port:
                print(f"Port {port} is busy; trying the next available port.")
    if httpd is None:
        raise RuntimeError("Unable to start AlloIQ dev server.")

    with httpd:
        print(f"Serving AlloIQ on http://{display_host}:{bound_port}/ from {web_dir}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopping AlloIQ dev server")
    return 0


def ensure_static_assets(out_dir: Path) -> None:
    source_dir = DEFAULT_WEB_DIR
    if out_dir.resolve() == source_dir.resolve():
        return
    for name in (
        "index.html",
        "dashboard.html",
        "portfolio.html",
        "research.html",
        "optimizer.html",
        "backtest.html",
        "ai-thesis-core.html",
        "styles.css",
        "home.js",
        "app.js",
        "portfolio.js",
        "research.js",
        "optimizer.js",
        "backtest.js",
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
