from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Callable

from .config import AppConfig
from .symbols import proxied_lookup, proxy_index
from .util import stable_id


LLM_REVIEW_VERSION = "2026-05-llm-evidence-review-v1"
LLM_REVIEW_PROMPT_VERSION = "2026-05-bounded-signal-v1"
LLM_REVIEW_SCHEMA_VERSION = "2026-05-llm-signal-schema-v2"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_LLM_MODEL = "gpt-5-mini"
DEFAULT_BOUNDED_SIGNAL_MODEL = "gpt-5.5"
DEFAULT_REASONING_EFFORT = "medium"
DEFAULT_BOUNDED_SIGNAL_REASONING_EFFORT = "high"
ALLOWED_MODES = {"disabled", "shadow", "review_gate", "bounded_signal"}
ALLOWED_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}
THESIS_QUALITIES = {"strong", "mixed", "weak"}
DEFAULT_MAX_EXPECTED_RETURN_DELTA = 6.0
DEFAULT_MAX_EVIDENCE_QUALITY_DELTA = 10.0
DEFAULT_MAX_DRAWDOWN_RISK_DELTA = 10.0
DEFAULT_LLM_SIGNAL_CACHE_PATH = "data/cache/llm_signal_cache.json"
LLM_SIGNAL_SCORE_FIELDS = [
    "llm_conviction_score",
    "llm_variant_quality_score",
    "llm_source_quality_score",
    "llm_contradiction_risk_score",
    "llm_staleness_risk_score",
]
LLM_SIGNAL_DELTA_FIELDS = [
    "llm_expected_return_delta",
    "llm_evidence_quality_delta",
    "llm_drawdown_risk_delta",
]
LLM_REVIEW_INPUT_FIELDS = {
    "symbol",
    "thesis_quality",
    "evidence_gaps",
    "contradictions",
    "stale_assumptions",
    "risk_questions",
    "decision_usefulness_score",
    "llm_expected_return_delta",
    "llm_evidence_quality_delta",
    "llm_drawdown_risk_delta",
    "llm_conviction_score",
    "llm_variant_quality_score",
    "llm_source_quality_score",
    "llm_contradiction_risk_score",
    "llm_staleness_risk_score",
    "llm_review_required",
    "confidence",
    "rationale",
    "risk_flags",
}
PROHIBITED_FIELD_NAMES = {
    "account",
    "account_id",
    "accounts",
    "api_key",
    "broker",
    "brokers",
    "cost_basis",
    "estimated_notional",
    "estimated_shares",
    "external_id",
    "market_value",
    "notional",
    "portfolio_value",
    "private_config",
    "quantity",
    "raw",
    "raw_json",
    "request_payload",
    "shares",
    "token",
    "transaction_id",
    "transactions",
}
PROHIBITED_TEXT_PATTERNS = [
    r"\baccount\b",
    r"\bbroker\b",
    r"\bcost basis\b",
    r"\bestimated notional\b",
    r"\bestimated shares\b",
    r"\bmarket value\b",
    r"\bquantity\b",
    r"\bshares\b",
    r"\btoken\b",
    r"\bapi key\b",
    r"\bIBKR\b",
    r"\bVanguard\b",
    r"\bOPENAI_API_KEY\b",
]


UrlOpen = Callable[..., Any]


class LLMReviewValidationError(ValueError):
    pass


class LLMReviewPrivacyError(ValueError):
    pass


LLM_REVIEW_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reviews"],
    "properties": {
        "reviews": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "symbol",
                    "thesis_quality",
                    "evidence_gaps",
                    "contradictions",
                    "stale_assumptions",
                    "risk_questions",
                    "decision_usefulness_score",
                    "llm_expected_return_delta",
                    "llm_evidence_quality_delta",
                    "llm_drawdown_risk_delta",
                    "llm_conviction_score",
                    "llm_variant_quality_score",
                    "llm_source_quality_score",
                    "llm_contradiction_risk_score",
                    "llm_staleness_risk_score",
                    "llm_review_required",
                    "confidence",
                    "rationale",
                    "risk_flags",
                ],
                "properties": {
                    "symbol": {"type": "string"},
                    "thesis_quality": {"type": "string", "enum": sorted(THESIS_QUALITIES)},
                    "evidence_gaps": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
                    "contradictions": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
                    "stale_assumptions": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
                    "risk_questions": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
                    "decision_usefulness_score": {"type": "number", "minimum": 0, "maximum": 100},
                    "llm_expected_return_delta": {"type": "number", "minimum": -25, "maximum": 25},
                    "llm_evidence_quality_delta": {"type": "number", "minimum": -100, "maximum": 100},
                    "llm_drawdown_risk_delta": {"type": "number", "minimum": -100, "maximum": 100},
                    "llm_conviction_score": {"type": "number", "minimum": 0, "maximum": 100},
                    "llm_variant_quality_score": {"type": "number", "minimum": 0, "maximum": 100},
                    "llm_source_quality_score": {"type": "number", "minimum": 0, "maximum": 100},
                    "llm_contradiction_risk_score": {"type": "number", "minimum": 0, "maximum": 100},
                    "llm_staleness_risk_score": {"type": "number", "minimum": 0, "maximum": 100},
                    "llm_review_required": {"type": "boolean"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": {"type": "string", "maxLength": 320},
                    "risk_flags": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
                },
            },
        }
    },
}


def build_llm_review_snapshot(
    config: AppConfig,
    as_of: date,
    session: str,
    feature_matrix: dict[str, Any],
    research_book: dict[str, Any],
    data_health: dict[str, Any],
    cards: list[dict[str, Any]],
    approval_tickets: list[dict[str, Any]],
    portfolio: dict[str, Any] | None = None,
    risk_limits: dict[str, Any] | None = None,
    urlopen: UrlOpen | None = None,
) -> dict[str, Any]:
    settings = llm_settings(config)
    mode = normalize_mode(settings)
    base = base_snapshot(settings, mode, as_of, session)
    if not bool(settings.get("enabled", False)) or mode == "disabled":
        return {**base, "status": "disabled", "detail": "LLM evidence review is disabled."}
    if settings["provider"] != "openai":
        return {**base, "status": "disabled", "detail": f"Unsupported LLM provider {settings['provider']}."}
    if not settings.get("caps_valid", True):
        return {
            **base,
            "status": "degraded",
            "detail": "Invalid LLM signal caps; using deterministic baseline.",
            "cap_validation_status": "failed",
        }
    packets = build_evidence_packets(
        as_of,
        session,
        feature_matrix,
        research_book,
        data_health,
        cards,
        approval_tickets,
        portfolio=portfolio,
        risk_limits=risk_limits,
        max_symbols=int(settings["max_symbols_per_run"]),
    )
    api_key = os.environ.get(settings["api_key_env"], "").strip()
    if not api_key and not packets:
        return {**base, "status": "skipped", "detail": "Missing configured API key environment variable."}
    if not packets:
        return {**base, "status": "limited", "detail": "No eligible evidence packets to review."}
    try:
        assert_privacy_safe(packets)
    except LLMReviewPrivacyError as exc:
        return {
            **base,
            "status": "privacy_blocked",
            "detail": str(exc),
            "redaction_status": "failed",
            "evidence_packet_count": len(packets),
        }
    cache, cached_reviews, request_packets = cached_llm_reviews(settings, packets)
    if not request_packets:
        return {
            **base,
            "status": "ok",
            "detail": f"{len(cached_reviews)} cached LLM evidence reviews reused in {mode} mode.",
            "redaction_status": "passed",
            "evidence_packet_count": len(packets),
            "reviewed_symbol_count": len(cached_reviews),
            "schema_validation_failure_count": 0,
            "cache_hit_count": len(cached_reviews),
            "cache_miss_count": 0,
            "cache_write_count": 0,
            "llm_signal_active": mode == "bounded_signal",
            "reviews": cached_reviews,
        }
    if not api_key:
        return {
            **base,
            "status": "skipped",
            "detail": "Missing configured API key environment variable.",
            "evidence_packet_count": len(packets),
            "cache_hit_count": len(cached_reviews),
            "cache_miss_count": len(request_packets),
        }
    try:
        payload = call_openai_responses(settings, request_packets, api_key, urlopen or urllib.request.urlopen)
        fresh_reviews = validate_review_response(payload, {str(row["symbol"]).upper() for row in request_packets}, settings)
    except LLMReviewValidationError as exc:
        return {
            **base,
            "status": "schema_error",
            "detail": str(exc),
            "schema_validation_failure_count": 1,
            "evidence_packet_count": len(packets),
            "cache_hit_count": len(cached_reviews),
            "cache_miss_count": len(request_packets),
        }
    except Exception as exc:
        return {
            **base,
            "status": "error",
            "detail": f"OpenAI Responses request failed: {short_error(exc)}",
            "evidence_packet_count": len(packets),
            "cache_hit_count": len(cached_reviews),
            "cache_miss_count": len(request_packets),
        }
    cache_write_count = write_llm_signal_cache(settings, cache, request_packets, fresh_reviews)
    reviews = merge_reviews_by_packet_order(packets, cached_reviews + fresh_reviews)
    return {
        **base,
        "status": "ok",
        "detail": f"{len(reviews)} LLM evidence reviews generated in {mode} mode.",
        "redaction_status": "passed",
        "evidence_packet_count": len(packets),
        "reviewed_symbol_count": len(reviews),
        "schema_validation_failure_count": 0,
        "cache_hit_count": len(cached_reviews),
        "cache_miss_count": len(request_packets),
        "cache_write_count": cache_write_count,
        "llm_signal_active": mode == "bounded_signal",
        "reviews": reviews,
    }


def llm_settings(config: AppConfig) -> dict[str, Any]:
    raw = dict(config.llm_settings)
    enabled = bool(raw.get("enabled", False))
    raw_mode = str(raw.get("mode") or "shadow").strip().lower()
    mode = raw_mode
    if not enabled:
        mode = "disabled"
    default_model = DEFAULT_BOUNDED_SIGNAL_MODEL if mode == "bounded_signal" else DEFAULT_LLM_MODEL
    default_reasoning = DEFAULT_BOUNDED_SIGNAL_REASONING_EFFORT if mode == "bounded_signal" else DEFAULT_REASONING_EFFORT
    expected_cap, expected_cap_valid = bounded_float_with_valid(
        raw.get("max_expected_return_delta"),
        DEFAULT_MAX_EXPECTED_RETURN_DELTA,
        0.0,
        25.0,
    )
    evidence_cap, evidence_cap_valid = bounded_float_with_valid(
        raw.get("max_evidence_quality_delta"),
        DEFAULT_MAX_EVIDENCE_QUALITY_DELTA,
        0.0,
        100.0,
    )
    drawdown_cap, drawdown_cap_valid = bounded_float_with_valid(
        raw.get("max_drawdown_risk_delta"),
        DEFAULT_MAX_DRAWDOWN_RISK_DELTA,
        0.0,
        100.0,
    )
    return {
        "enabled": enabled,
        "provider": str(raw.get("provider") or "openai").strip().lower(),
        "model": str(raw.get("model") or default_model).strip() or default_model,
        "api_key_env": str(raw.get("api_key_env") or "OPENAI_API_KEY").strip() or "OPENAI_API_KEY",
        "max_symbols_per_run": max(1, min(50, int(raw.get("max_symbols_per_run") or 12))),
        "mode": mode,
        "timeout_seconds": max(3, min(60, int(raw.get("timeout_seconds") or 20))),
        "reasoning_effort": normalize_reasoning_effort(raw.get("reasoning_effort"), default_reasoning),
        "max_expected_return_delta": expected_cap,
        "max_evidence_quality_delta": evidence_cap,
        "max_drawdown_risk_delta": drawdown_cap,
        "caps_valid": expected_cap_valid and evidence_cap_valid and drawdown_cap_valid,
        "cache_enabled": bool(raw.get("cache_enabled", True)),
        "cache_path": str(raw.get("cache_path") or DEFAULT_LLM_SIGNAL_CACHE_PATH),
        "store": False,
    }


def normalize_mode(settings: dict[str, Any]) -> str:
    mode = str(settings.get("mode") or "disabled").strip().lower()
    return mode if mode in ALLOWED_MODES else "disabled"


def normalize_reasoning_effort(value: Any, default: str = DEFAULT_REASONING_EFFORT) -> str:
    effort = str(value or default).strip().lower()
    return effort if effort in ALLOWED_REASONING_EFFORTS else default


def bounded_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = default
    return max(low, min(high, numeric))


def bounded_float_with_valid(value: Any, default: float, low: float, high: float) -> tuple[float, bool]:
    if value is None:
        return default, True
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default, False
    if numeric < low or numeric > high:
        return max(low, min(high, numeric)), False
    return numeric, True


def base_snapshot(settings: dict[str, Any], mode: str, as_of: date, session: str) -> dict[str, Any]:
    return {
        "version": LLM_REVIEW_VERSION,
        "prompt_version": LLM_REVIEW_PROMPT_VERSION,
        "schema_version": LLM_REVIEW_SCHEMA_VERSION,
        "as_of": as_of.isoformat(),
        "session": session,
        "enabled": bool(settings.get("enabled", False)),
        "provider": settings.get("provider", "openai"),
        "model": settings.get("model", DEFAULT_LLM_MODEL),
        "reasoning_effort": settings.get("reasoning_effort", DEFAULT_REASONING_EFFORT),
        "mode": mode,
        "reviewed_symbol_count": 0,
        "evidence_packet_count": 0,
        "schema_validation_failure_count": 0,
        "redaction_status": "not_applicable",
        "affected_approval_gate": False,
        "llm_signal_active": False,
        "llm_direct_sizing_allowed": False,
        "store": False,
        "cache_enabled": bool(settings.get("cache_enabled", False)),
        "cache_hit_count": 0,
        "cache_miss_count": 0,
        "cache_write_count": 0,
        "caps": {
            "max_expected_return_delta": settings.get("max_expected_return_delta", DEFAULT_MAX_EXPECTED_RETURN_DELTA),
            "max_evidence_quality_delta": settings.get("max_evidence_quality_delta", DEFAULT_MAX_EVIDENCE_QUALITY_DELTA),
            "max_drawdown_risk_delta": settings.get("max_drawdown_risk_delta", DEFAULT_MAX_DRAWDOWN_RISK_DELTA),
        },
        "reviews": [],
    }


def build_evidence_packets(
    as_of: date,
    session: str,
    feature_matrix: dict[str, Any],
    research_book: dict[str, Any],
    data_health: dict[str, Any],
    cards: list[dict[str, Any]],
    approval_tickets: list[dict[str, Any]],
    portfolio: dict[str, Any] | None = None,
    risk_limits: dict[str, Any] | None = None,
    max_symbols: int = 12,
) -> list[dict[str, Any]]:
    features_by_symbol = proxy_index(feature_matrix.get("rows") or [])
    cards_by_symbol = proxy_index(cards)
    tickets_by_symbol = {str(row.get("symbol") or "").upper(): row for row in approval_tickets}
    packets = []
    for item in (research_book.get("items") or [])[:max_symbols]:
        symbol = str(item.get("symbol") or "").upper()
        if not symbol:
            continue
        feature = proxied_lookup(features_by_symbol, symbol, {})
        card = proxied_lookup(cards_by_symbol, symbol, {})
        ticket = tickets_by_symbol.get(symbol, {})
        packet = {
            "evidence_id": stable_id([as_of.isoformat(), session, LLM_REVIEW_PROMPT_VERSION, symbol, feature.get("feature_id", "")]),
            "as_of": as_of.isoformat(),
            "session": session,
            "symbol": symbol,
            "bucket": item.get("bucket", feature.get("bucket", "unmapped")),
            "rank": item.get("rank", 0),
            "current_weight": item.get("current_weight", feature.get("current_weight", 0)),
            "peer_avg_weight": item.get("peer_avg_weight", feature.get("peer_avg_weight", 0)),
            "risk_adjusted_expected_return": item.get("risk_adjusted_expected_return"),
            "probability_weighted_return": item.get("probability_weighted_return"),
            "bull_return_12m": item.get("bull_return_12m"),
            "base_return_12m": item.get("base_return_12m"),
            "bear_return_12m": item.get("bear_return_12m"),
            "verdict": item.get("verdict", ""),
            "thesis_summary": item.get("thesis_summary", ""),
            "variant_view": item.get("variant_view", ""),
            "company_reason": item.get("company_reason", feature.get("company_reason", "")),
            "sector_reason": item.get("sector_reason", ""),
            "tertiary_signal_summary": item.get("tertiary_signal_summary", ""),
            "company_underwriting_score": item.get("company_underwriting_score", feature.get("company_underwriting_score")),
            "sector_setup_score": item.get("sector_setup_score", feature.get("sector_setup_score")),
            "timing_score": item.get("timing_score", feature.get("timing_score")),
            "drawdown_risk": item.get("drawdown_risk", feature.get("drawdown_risk")),
            "evidence_quality": item.get("evidence_quality", feature.get("evidence_quality")),
            "valuation_support": item.get("valuation_support", feature.get("valuation_support")),
            "signal_families": feature.get("signal_families", card.get("signal_families", [])),
            "event_types": feature.get("event_types", card.get("top_event_types", [])),
            "manager_summary": {
                "manager_count": feature.get("manager_count", card.get("consensus_manager_count", 0)),
                "tier1_manager_count": feature.get("tier1_manager_count", 0),
                "peer_avg_weight": feature.get("peer_avg_weight", 0),
                "tier1_peer_avg_weight": feature.get("tier1_peer_avg_weight", 0),
            },
            "price_returns": {
                "1d": item.get("price_return_1d", feature.get("price_return_1d")),
                "5d": item.get("price_return_5d", feature.get("price_return_5d")),
                "1m": item.get("price_return_1m", feature.get("price_return_1m")),
                "3m": item.get("price_return_3m", feature.get("price_return_3m")),
                "ytd": item.get("price_return_ytd", feature.get("price_return_ytd")),
            },
            "external_signal_context": {
                "feed_status": feature.get("external_feed_status", ""),
                "coverage_multiplier": feature.get("external_coverage_multiplier"),
                "provider_ok_ratio": feature.get("external_provider_ok_ratio"),
                "signal_count": feature.get("external_signal_count"),
                "source_count": feature.get("external_source_count"),
            },
            "approval_context": {
                "trade_action": ticket.get("trade_action", "watch"),
                "current_weight": ticket.get("current_weight", item.get("current_weight", 0)),
                "recommended_delta_weight": ticket.get("recommended_delta_weight", 0),
                "target_weight": ticket.get("target_weight", 0),
                "model_target_weight": ticket.get("model_target_weight", 0),
                "post_action_weight": ticket.get("post_action_weight", 0),
                "risk_flags": ticket.get("risk_flags", []),
                "approval_gate_status": ticket.get("approval_gate_status", ""),
                "approval_open_check_count": ticket.get("approval_open_check_count", 0),
                "approval_blocking_checks": approval_blocking_checks(ticket),
            },
            "portfolio_context": public_portfolio_context(portfolio or {}),
            "risk_limit_context": public_risk_limit_context(risk_limits or {}),
            "counterargument": card.get("counterargument", ""),
            "falsifier": card.get("falsifier", ""),
            "source_health": public_source_health(data_health),
        }
        packet["packet_hash"] = stable_id([json.dumps(packet, sort_keys=True, default=str)])
        packets.append(packet)
    return packets


def public_portfolio_context(portfolio: dict[str, Any]) -> dict[str, Any]:
    return {
        "equity_weight": portfolio.get("equity_weight"),
        "cash_weight": portfolio.get("cash_weight"),
        "position_count": portfolio.get("position_count"),
        "symbol_count": portfolio.get("symbol_count"),
        "comparison_weight_basis": portfolio.get("comparison_weight_basis", ""),
    }


def public_risk_limit_context(risk_limits: dict[str, Any]) -> dict[str, Any]:
    allowed = (
        "max_single_name_weight",
        "max_bucket_weight",
        "max_daily_turnover",
        "max_one_ticket_delta",
        "max_cash_deploy_weight",
        "earnings_blackout_days",
        "earnings_risk_window_days",
    )
    return {key: risk_limits.get(key) for key in allowed if key in risk_limits}


def cached_llm_reviews(
    settings: dict[str, Any],
    packets: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    cache = load_llm_signal_cache(settings)
    if not settings.get("cache_enabled", False):
        return cache, [], packets
    cached: list[dict[str, Any]] = []
    misses: list[dict[str, Any]] = []
    entries = cache.get("entries") if isinstance(cache.get("entries"), dict) else {}
    for packet in packets:
        evidence_id = str(packet.get("evidence_id") or "")
        entry = entries.get(evidence_id) if evidence_id else None
        if cache_entry_matches(entry, packet, settings):
            cached.append(dict(entry.get("review") or {}))
        else:
            misses.append(packet)
    return cache, merge_reviews_by_packet_order(packets, cached), misses


def load_llm_signal_cache(settings: dict[str, Any]) -> dict[str, Any]:
    if not settings.get("cache_enabled", False):
        return {"entries": {}}
    path = Path(str(settings.get("cache_path") or DEFAULT_LLM_SIGNAL_CACHE_PATH))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"entries": {}}
    return payload if isinstance(payload, dict) else {"entries": {}}


def cache_entry_matches(entry: Any, packet: dict[str, Any], settings: dict[str, Any]) -> bool:
    if not isinstance(entry, dict) or not isinstance(entry.get("review"), dict):
        return False
    if entry.get("packet_hash") != packet.get("packet_hash"):
        return False
    if entry.get("model") != settings.get("model"):
        return False
    if entry.get("mode") != settings.get("mode"):
        return False
    if entry.get("prompt_version") != LLM_REVIEW_PROMPT_VERSION:
        return False
    if entry.get("schema_version") != LLM_REVIEW_SCHEMA_VERSION:
        return False
    if entry.get("caps") != llm_signal_caps(settings):
        return False
    review = entry.get("review") or {}
    return str(review.get("symbol") or "").upper() == str(packet.get("symbol") or "").upper()


def write_llm_signal_cache(
    settings: dict[str, Any],
    cache: dict[str, Any],
    packets: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
) -> int:
    if not settings.get("cache_enabled", False):
        return 0
    entries = cache.setdefault("entries", {})
    if not isinstance(entries, dict):
        entries = {}
        cache["entries"] = entries
    reviews_by_symbol = {str(row.get("symbol") or "").upper(): row for row in reviews}
    write_count = 0
    for packet in packets:
        evidence_id = str(packet.get("evidence_id") or "")
        symbol = str(packet.get("symbol") or "").upper()
        review = reviews_by_symbol.get(symbol)
        if not evidence_id or not review:
            continue
        entries[evidence_id] = {
            "evidence_id": evidence_id,
            "packet_hash": packet.get("packet_hash"),
            "model": settings.get("model"),
            "mode": settings.get("mode"),
            "prompt_version": LLM_REVIEW_PROMPT_VERSION,
            "schema_version": LLM_REVIEW_SCHEMA_VERSION,
            "caps": llm_signal_caps(settings),
            "review": review,
        }
        write_count += 1
    path = Path(str(settings.get("cache_path") or DEFAULT_LLM_SIGNAL_CACHE_PATH))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, sort_keys=True, indent=2, default=str), encoding="utf-8")
    except OSError:
        return 0
    return write_count


def merge_reviews_by_packet_order(packets: list[dict[str, Any]], reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_symbol = {str(row.get("symbol") or "").upper(): row for row in reviews}
    ordered: list[dict[str, Any]] = []
    for packet in packets:
        review = by_symbol.get(str(packet.get("symbol") or "").upper())
        if review:
            ordered.append(review)
    return ordered


def llm_signal_caps(settings: dict[str, Any]) -> dict[str, float]:
    return {
        "max_expected_return_delta": float(settings.get("max_expected_return_delta", DEFAULT_MAX_EXPECTED_RETURN_DELTA)),
        "max_evidence_quality_delta": float(settings.get("max_evidence_quality_delta", DEFAULT_MAX_EVIDENCE_QUALITY_DELTA)),
        "max_drawdown_risk_delta": float(settings.get("max_drawdown_risk_delta", DEFAULT_MAX_DRAWDOWN_RISK_DELTA)),
    }


def public_source_health(data_health: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for source in data_health.get("sources") or []:
        if not isinstance(source, dict):
            continue
        source_key = str(source.get("source") or "")
        label = str(source.get("label") or source_key)
        if source_key == "broker_positions":
            source_key = "position_snapshot"
            label = "Position snapshot"
        rows.append(
            {
                "source": source_key,
                "label": label,
                "status": source.get("status", "unknown"),
                "provider_gap_count": source.get("provider_gap_count", 0),
                "confirmation_gap_count": source.get("confirmation_gap_count", 0),
            }
        )
    return rows[:10]


def approval_blocking_checks(ticket: dict[str, Any]) -> list[str]:
    names = []
    for check in ticket.get("approval_checks") or []:
        if isinstance(check, dict) and check.get("status") != "passed" and check.get("check"):
            names.append(str(check.get("check")))
    return sorted(set(names))


def assert_privacy_safe(value: Any) -> None:
    violations = privacy_violations(value)
    if violations:
        raise LLMReviewPrivacyError("LLM evidence packet contains prohibited private fields: " + "; ".join(violations[:5]))


def privacy_violations(value: Any, path: str = "$") -> list[str]:
    violations: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in PROHIBITED_FIELD_NAMES:
                violations.append(f"{path}.{key}")
            violations.extend(privacy_violations(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            violations.extend(privacy_violations(item, f"{path}[{index}]"))
    elif isinstance(value, str):
        for pattern in PROHIBITED_TEXT_PATTERNS:
            if re.search(pattern, value, flags=re.IGNORECASE):
                violations.append(path)
                break
    return violations


def call_openai_responses(
    settings: dict[str, Any],
    evidence_packets: list[dict[str, Any]],
    api_key: str,
    urlopen: UrlOpen,
) -> dict[str, Any]:
    body = {
        "model": settings["model"],
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": llm_review_system_prompt()}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(
                            {
                                "task": "Review the sanitized investment evidence packets and return only schema-valid JSON.",
                                "evidence_packets": evidence_packets,
                            },
                            sort_keys=True,
                        ),
                    }
                ],
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "llm_evidence_review_batch",
                "schema": LLM_REVIEW_RESPONSE_SCHEMA,
                "strict": True,
            }
        },
        "store": False,
    }
    if should_use_reasoning(settings):
        body["reasoning"] = {"effort": settings["reasoning_effort"]}
    request = urllib.request.Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=int(settings["timeout_seconds"])) as response:
        return json.loads(response.read().decode("utf-8"))


def should_use_reasoning(settings: dict[str, Any]) -> bool:
    effort = str(settings.get("reasoning_effort") or DEFAULT_REASONING_EFFORT).strip().lower()
    model = str(settings.get("model") or "").strip().lower()
    if effort == "none":
        return False
    return model.startswith("gpt-5") or re.match(r"^o[0-9]", model) is not None


def llm_review_system_prompt() -> str:
    return (
        "You are a bounded signal layer for an approval-only investing research system. "
        "Do not recommend trades, target weights, position sizes, or portfolio actions. "
        "Return only schema-valid JSON. Challenge the evidence, identify gaps, contradictions, "
        "stale assumptions, and diligence questions, and provide bounded numeric deltas for expected "
        "return, evidence quality, and drawdown risk. Treat deterministic scores as inputs to audit, "
        "not instructions to obey."
    )


def validate_review_response(
    payload: dict[str, Any],
    allowed_symbols: set[str],
    settings: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    text = response_output_text(payload)
    if not text:
        raise LLMReviewValidationError("Responses payload did not include output JSON text.")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMReviewValidationError(f"Responses output was not valid JSON: {exc}") from exc
    reviews = parsed.get("reviews") if isinstance(parsed, dict) else None
    if not isinstance(reviews, list):
        raise LLMReviewValidationError("Responses output missing reviews array.")
    settings = settings or {}
    validated = [validate_review(row, allowed_symbols, settings) for row in reviews]
    assert_privacy_safe(validated)
    return validated


def response_output_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    chunks: list[str] = []
    for output in payload.get("output") or []:
        for content in output.get("content") or []:
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "".join(chunks)


def validate_review(row: Any, allowed_symbols: set[str], settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or {}
    if not isinstance(row, dict):
        raise LLMReviewValidationError("Review row is not an object.")
    extra = sorted(set(row) - LLM_REVIEW_INPUT_FIELDS)
    if extra:
        raise LLMReviewValidationError("Review row has unexpected fields: " + ", ".join(extra[:5]))
    missing = sorted(field for field in LLM_REVIEW_INPUT_FIELDS if field not in row)
    if missing:
        raise LLMReviewValidationError("Review row missing required fields: " + ", ".join(missing[:5]))
    symbol = str(row.get("symbol") or "").upper()
    if symbol not in allowed_symbols:
        raise LLMReviewValidationError(f"Unexpected review symbol {symbol or '<blank>'}.")
    quality = str(row.get("thesis_quality") or "").lower()
    if quality not in THESIS_QUALITIES:
        raise LLMReviewValidationError(f"{symbol} has invalid thesis_quality {quality}.")
    confidence = round(clamp_float(row.get("confidence"), 0.0, 1.0), 3)
    review_required = bool(row.get("llm_review_required"))
    expected_cap = float(settings.get("max_expected_return_delta", DEFAULT_MAX_EXPECTED_RETURN_DELTA))
    evidence_cap = float(settings.get("max_evidence_quality_delta", DEFAULT_MAX_EVIDENCE_QUALITY_DELTA))
    drawdown_cap = float(settings.get("max_drawdown_risk_delta", DEFAULT_MAX_DRAWDOWN_RISK_DELTA))
    return {
        "symbol": symbol,
        "thesis_quality": quality,
        "evidence_gaps": limited_string_list(row.get("evidence_gaps")),
        "contradictions": limited_string_list(row.get("contradictions")),
        "stale_assumptions": limited_string_list(row.get("stale_assumptions")),
        "risk_questions": limited_string_list(row.get("risk_questions")),
        "decision_usefulness_score": round(clamp_float(row.get("decision_usefulness_score"), 0.0, 100.0), 2),
        "llm_expected_return_delta": round(clamp_float(row.get("llm_expected_return_delta"), -expected_cap, expected_cap), 2),
        "llm_evidence_quality_delta": round(clamp_float(row.get("llm_evidence_quality_delta"), -evidence_cap, evidence_cap), 2),
        "llm_drawdown_risk_delta": round(clamp_float(row.get("llm_drawdown_risk_delta"), -drawdown_cap, drawdown_cap), 2),
        "llm_conviction_score": round(clamp_float(row.get("llm_conviction_score"), 0.0, 100.0), 2),
        "llm_variant_quality_score": round(clamp_float(row.get("llm_variant_quality_score"), 0.0, 100.0), 2),
        "llm_source_quality_score": round(clamp_float(row.get("llm_source_quality_score"), 0.0, 100.0), 2),
        "llm_contradiction_risk_score": round(clamp_float(row.get("llm_contradiction_risk_score"), 0.0, 100.0), 2),
        "llm_staleness_risk_score": round(clamp_float(row.get("llm_staleness_risk_score"), 0.0, 100.0), 2),
        "llm_review_required": review_required,
        "review_required": review_required,
        "confidence": confidence,
        "rationale": str(row.get("rationale") or "").strip()[:320],
        "risk_flags": limited_string_list(row.get("risk_flags"), limit=6, max_len=120),
    }


def limited_string_list(value: Any, limit: int = 6, max_len: int = 220) -> list[str]:
    if not isinstance(value, list):
        raise LLMReviewValidationError("Expected list of strings in review row.")
    return [str(item).strip()[:max_len] for item in value[:limit] if str(item).strip()]


def clamp_float(value: Any, low: float, high: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise LLMReviewValidationError(f"Expected numeric value, got {value!r}.") from exc
    return max(low, min(high, numeric))


def apply_llm_review_to_approval_tickets(tickets: list[dict[str, Any]], llm_review: dict[str, Any]) -> None:
    if llm_review.get("status") != "ok":
        return
    if llm_review.get("mode") == "shadow":
        llm_review["affected_approval_gate"] = False
        llm_review["affected_approval_ticket_count"] = 0
        return
    review_by_symbol = {str(row.get("symbol") or "").upper(): row for row in llm_review.get("reviews") or []}
    affected = 0
    for ticket in tickets:
        symbol = str(ticket.get("symbol") or "").upper()
        review = review_by_symbol.get(symbol)
        if not review:
            continue
        ticket["llm_review"] = review_summary(review)
        ticket["llm_signal"] = review_summary(review)
        ticket["llm_decision_usefulness_score"] = review.get("decision_usefulness_score")
        if llm_review.get("mode") != "review_gate":
            continue
        if not (review.get("review_required") or review.get("llm_review_required")):
            continue
        checks = list(ticket.get("approval_checks") or [])
        if not any(check.get("check") == "llm_evidence_reviewed" for check in checks if isinstance(check, dict)):
            checks.append(
                {
                    "check": "llm_evidence_reviewed",
                    "status": "pending",
                    "detail": llm_review_detail(review),
                }
            )
        ticket["approval_checks"] = checks
        ticket["approval_open_check_count"] = sum(1 for check in checks if isinstance(check, dict) and check.get("status") != "passed")
        ticket["approval_gate_status"] = llm_approval_gate_status(checks)
        ticket["review_required"] = True
        ticket["review_status"] = "llm_evidence_review_required"
        ticket["review_reason"] = llm_review_detail(review)
        affected += 1
    llm_review["affected_approval_gate"] = affected > 0
    llm_review["affected_approval_ticket_count"] = affected


def llm_approval_gate_status(checks: list[dict[str, Any]]) -> str:
    if any(check.get("check") == "earnings_date_confirmed" and check.get("status") != "passed" for check in checks):
        return "blocked_until_confirmation"
    if any(check.get("status") != "passed" for check in checks):
        return "review_required"
    return "ready_for_review"


def review_summary(review: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "thesis_quality": review.get("thesis_quality"),
        "decision_usefulness_score": review.get("decision_usefulness_score"),
        "review_required": bool(review.get("review_required")),
        "llm_review_required": bool(review.get("llm_review_required", review.get("review_required"))),
        "confidence": review.get("confidence"),
        "evidence_gaps": review.get("evidence_gaps", [])[:3],
        "contradictions": review.get("contradictions", [])[:3],
        "stale_assumptions": review.get("stale_assumptions", [])[:3],
        "risk_questions": review.get("risk_questions", [])[:3],
        "rationale": review.get("rationale", ""),
        "risk_flags": review.get("risk_flags", [])[:3],
    }
    for key in LLM_SIGNAL_SCORE_FIELDS + LLM_SIGNAL_DELTA_FIELDS:
        if key in review:
            summary[key] = review.get(key)
    return summary


def llm_review_detail(review: dict[str, Any]) -> str:
    parts = []
    for key in ("evidence_gaps", "contradictions", "stale_assumptions", "risk_questions"):
        values = [str(item) for item in review.get(key) or [] if str(item).strip()]
        if values:
            parts.append(f"{key.replace('_', ' ')}: {values[0]}")
    return "; ".join(parts[:3]) or "LLM evidence review requested human confirmation."


def attach_llm_review_to_data_health(data_health: dict[str, Any], llm_review: dict[str, Any]) -> None:
    status = str(llm_review.get("status") or "unknown")
    if status == "disabled":
        source_status = "disabled"
    elif status == "ok":
        source_status = "ok"
    elif status in {"skipped", "limited"}:
        source_status = "limited"
    else:
        source_status = "error"
    data_health.setdefault("sources", []).append(
        {
            "source": "llm_signal" if llm_review.get("mode") == "bounded_signal" else "llm_review",
            "label": "LLM bounded signal" if llm_review.get("mode") == "bounded_signal" else "LLM evidence review",
            "status": source_status,
            "detail": llm_review.get("detail", ""),
            "reviewed_symbol_count": llm_review.get("reviewed_symbol_count", 0),
            "mode": llm_review.get("mode", "disabled"),
            "affected_approval_gate": bool(llm_review.get("affected_approval_gate", False)),
        }
    )
    weak = {"missing", "stale", "limited", "estimated", "unknown", "failed", "error"}
    data_health["weak_source_count"] = sum(
        1
        for source in data_health.get("sources") or []
        if isinstance(source, dict) and str(source.get("status") or "") in weak
    )


def short_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}: {exc.reason}"
    return f"{exc.__class__.__name__}: {str(exc)[:160]}"
