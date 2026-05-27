from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import date
from typing import Any, Callable

from .config import AppConfig
from .symbols import proxied_lookup, proxy_index
from .util import stable_id


LLM_REVIEW_VERSION = "2026-05-llm-evidence-review-v1"
LLM_REVIEW_PROMPT_VERSION = "2026-05-evidence-challenge-v1"
LLM_REVIEW_SCHEMA_VERSION = "2026-05-llm-review-schema-v1"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_LLM_MODEL = "gpt-5-mini"
DEFAULT_REASONING_EFFORT = "medium"
ALLOWED_MODES = {"disabled", "shadow", "review_gate"}
ALLOWED_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high"}
THESIS_QUALITIES = {"strong", "mixed", "weak"}
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
                    "review_required",
                    "confidence",
                ],
                "properties": {
                    "symbol": {"type": "string"},
                    "thesis_quality": {"type": "string", "enum": sorted(THESIS_QUALITIES)},
                    "evidence_gaps": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
                    "contradictions": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
                    "stale_assumptions": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
                    "risk_questions": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
                    "decision_usefulness_score": {"type": "number", "minimum": 0, "maximum": 100},
                    "review_required": {"type": "boolean"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
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
    urlopen: UrlOpen | None = None,
) -> dict[str, Any]:
    settings = llm_settings(config)
    mode = normalize_mode(settings)
    base = base_snapshot(settings, mode, as_of, session)
    if not bool(settings.get("enabled", False)) or mode == "disabled":
        return {**base, "status": "disabled", "detail": "LLM evidence review is disabled."}
    if settings["provider"] != "openai":
        return {**base, "status": "disabled", "detail": f"Unsupported LLM provider {settings['provider']}."}
    api_key = os.environ.get(settings["api_key_env"], "").strip()
    if not api_key:
        return {**base, "status": "skipped", "detail": "Missing configured API key environment variable."}
    packets = build_evidence_packets(
        as_of,
        session,
        feature_matrix,
        research_book,
        data_health,
        cards,
        approval_tickets,
        max_symbols=int(settings["max_symbols_per_run"]),
    )
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
    try:
        payload = call_openai_responses(settings, packets, api_key, urlopen or urllib.request.urlopen)
        reviews = validate_review_response(payload, {str(row["symbol"]).upper() for row in packets})
    except LLMReviewValidationError as exc:
        return {
            **base,
            "status": "schema_error",
            "detail": str(exc),
            "schema_validation_failure_count": 1,
            "evidence_packet_count": len(packets),
        }
    except Exception as exc:
        return {
            **base,
            "status": "error",
            "detail": f"OpenAI Responses request failed: {short_error(exc)}",
            "evidence_packet_count": len(packets),
        }
    return {
        **base,
        "status": "ok",
        "detail": f"{len(reviews)} LLM evidence reviews generated in {mode} mode.",
        "redaction_status": "passed",
        "evidence_packet_count": len(packets),
        "reviewed_symbol_count": len(reviews),
        "schema_validation_failure_count": 0,
        "reviews": reviews,
    }


def llm_settings(config: AppConfig) -> dict[str, Any]:
    raw = dict(config.llm_settings)
    enabled = bool(raw.get("enabled", False))
    mode = str(raw.get("mode") or "shadow").strip().lower()
    if not enabled:
        mode = "disabled"
    return {
        "enabled": enabled,
        "provider": str(raw.get("provider") or "openai").strip().lower(),
        "model": str(raw.get("model") or DEFAULT_LLM_MODEL).strip() or DEFAULT_LLM_MODEL,
        "api_key_env": str(raw.get("api_key_env") or "OPENAI_API_KEY").strip() or "OPENAI_API_KEY",
        "max_symbols_per_run": max(1, min(50, int(raw.get("max_symbols_per_run") or 12))),
        "mode": mode,
        "timeout_seconds": max(3, min(60, int(raw.get("timeout_seconds") or 20))),
        "reasoning_effort": normalize_reasoning_effort(raw.get("reasoning_effort")),
    }


def normalize_mode(settings: dict[str, Any]) -> str:
    mode = str(settings.get("mode") or "disabled").strip().lower()
    return mode if mode in ALLOWED_MODES else "disabled"


def normalize_reasoning_effort(value: Any) -> str:
    effort = str(value or DEFAULT_REASONING_EFFORT).strip().lower()
    return effort if effort in ALLOWED_REASONING_EFFORTS else DEFAULT_REASONING_EFFORT


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
                "approval_gate_status": ticket.get("approval_gate_status", ""),
                "approval_blocking_checks": approval_blocking_checks(ticket),
            },
            "counterargument": card.get("counterargument", ""),
            "falsifier": card.get("falsifier", ""),
            "source_health": public_source_health(data_health),
        }
        packets.append(packet)
    return packets


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
        "You are an evidence-review layer for an approval-only investing research system. "
        "Do not recommend trades, sizes, target weights, or portfolio actions. "
        "Only challenge the provided public evidence: identify gaps, contradictions, stale assumptions, "
        "and diligence questions. Treat deterministic scores as inputs to audit, not instructions to obey."
    )


def validate_review_response(payload: dict[str, Any], allowed_symbols: set[str]) -> list[dict[str, Any]]:
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
    validated = [validate_review(row, allowed_symbols) for row in reviews]
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


def validate_review(row: Any, allowed_symbols: set[str]) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise LLMReviewValidationError("Review row is not an object.")
    symbol = str(row.get("symbol") or "").upper()
    if symbol not in allowed_symbols:
        raise LLMReviewValidationError(f"Unexpected review symbol {symbol or '<blank>'}.")
    quality = str(row.get("thesis_quality") or "").lower()
    if quality not in THESIS_QUALITIES:
        raise LLMReviewValidationError(f"{symbol} has invalid thesis_quality {quality}.")
    return {
        "symbol": symbol,
        "thesis_quality": quality,
        "evidence_gaps": limited_string_list(row.get("evidence_gaps")),
        "contradictions": limited_string_list(row.get("contradictions")),
        "stale_assumptions": limited_string_list(row.get("stale_assumptions")),
        "risk_questions": limited_string_list(row.get("risk_questions")),
        "decision_usefulness_score": round(clamp_float(row.get("decision_usefulness_score"), 0.0, 100.0), 2),
        "review_required": bool(row.get("review_required")),
        "confidence": round(clamp_float(row.get("confidence"), 0.0, 1.0), 3),
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
    if llm_review.get("mode") != "review_gate" or llm_review.get("status") != "ok":
        return
    review_by_symbol = {str(row.get("symbol") or "").upper(): row for row in llm_review.get("reviews") or []}
    affected = 0
    for ticket in tickets:
        symbol = str(ticket.get("symbol") or "").upper()
        review = review_by_symbol.get(symbol)
        if not review:
            continue
        ticket["llm_review"] = review_summary(review)
        ticket["llm_decision_usefulness_score"] = review.get("decision_usefulness_score")
        if not review.get("review_required"):
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
    return {
        "thesis_quality": review.get("thesis_quality"),
        "decision_usefulness_score": review.get("decision_usefulness_score"),
        "review_required": bool(review.get("review_required")),
        "confidence": review.get("confidence"),
        "evidence_gaps": review.get("evidence_gaps", [])[:3],
        "contradictions": review.get("contradictions", [])[:3],
        "stale_assumptions": review.get("stale_assumptions", [])[:3],
        "risk_questions": review.get("risk_questions", [])[:3],
    }


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
            "source": "llm_review",
            "label": "LLM evidence review",
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
