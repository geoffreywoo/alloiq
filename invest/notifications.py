from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from .config import AppConfig


NOTIFICATION_VERSION = "2026-05-telegram-briefing-v1"
SESSION_LABELS = {
    "premarket": "Pre-market",
    "market_open": "Market Open",
    "intraday": "Intraday",
    "midday": "Midday",
    "market_close": "Market Close",
    "postmarket": "Post-market",
    "weekly": "Weekend",
}
SESSION_ORDER = {
    "premarket": 1,
    "market_open": 2,
    "intraday": 3,
    "midday": 4,
    "market_close": 5,
    "postmarket": 6,
    "weekly": 7,
}
TELEGRAM_MAX_MESSAGE_CHARS = 3900


def send_latest_briefing(
    config: AppConfig,
    session: str | None = None,
    channel: str = "telegram",
    reports_dir: Path | None = None,
    dry_run: bool = False,
    site_url: str | None = None,
    urgent_only: bool = False,
    compare_to: Path | None = None,
) -> dict[str, Any]:
    if channel != "telegram":
        return {"status": "failed", "reason": f"unsupported notification channel: {channel}"}

    report_path, payload = latest_report_payload(reports_dir or config.reports_dir, session=session)
    if not payload:
        return {
            "status": "skipped",
            "reason": "no matching report json found",
            "channel": channel,
            "session": session or "",
        }

    resolved_site_url = site_url or configured_site_url(config)
    previous_payload = load_payload(compare_to)
    urgent_items = urgent_alert_items(payload, previous_payload=previous_payload)
    if urgent_only and not urgent_items:
        return {
            "status": "skipped",
            "reason": "no new urgent alerts",
            "channel": channel,
            "session": payload.get("session") or session or "",
            "as_of": payload.get("as_of") or "",
            "report": str(report_path),
            "site_url": resolved_site_url,
            "urgent_item_count": 0,
        }
    message = (
        format_urgent_alert_message(payload, urgent_items, site_url=resolved_site_url)
        if urgent_only
        else format_briefing_message(payload, site_url=resolved_site_url)
    )
    result: dict[str, Any] = {
        "status": "dry_run" if dry_run else "pending",
        "version": NOTIFICATION_VERSION,
        "channel": channel,
        "session": payload.get("session") or session or "",
        "as_of": payload.get("as_of") or "",
        "report": str(report_path),
        "site_url": resolved_site_url,
        "message_chars": len(message),
        "urgent_only": urgent_only,
        "urgent_item_count": len(urgent_items),
    }
    if dry_run:
        result["message"] = message
        return result

    telegram = telegram_delivery_settings(config)
    if not telegram.get("enabled", True):
        return {**result, "status": "skipped", "reason": "telegram notifications disabled"}
    token = str(telegram.get("bot_token") or "")
    chat_id = str(telegram.get("chat_id") or "")
    if not token or not chat_id:
        return {**result, "status": "skipped", "reason": "missing telegram bot token or chat id"}

    sent = send_telegram_message(
        token,
        chat_id,
        message,
        timeout_seconds=float(telegram.get("timeout_seconds") or 10),
    )
    return {**result, **sent}


def load_payload(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def latest_report_payload(reports_dir: Path, session: str | None = None) -> tuple[Path | None, dict[str, Any]]:
    if not reports_dir.exists():
        return None, {}
    candidates: list[tuple[tuple[str, int, float, str], Path, dict[str, Any]]] = []
    for path in reports_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        report_session = str(payload.get("session") or "")
        if session and report_session != session:
            continue
        key = (
            str(payload.get("as_of") or ""),
            SESSION_ORDER.get(report_session, 0),
            path.stat().st_mtime,
            path.name,
        )
        candidates.append((key, path, payload))
    if not candidates:
        return None, {}
    _, path, payload = sorted(candidates, key=lambda row: row[0])[-1]
    return path, payload


def format_briefing_message(payload: dict[str, Any], site_url: str = "https://alloiq.com") -> str:
    session = str(payload.get("session") or "")
    label = SESSION_LABELS.get(session, session.title() or "Daily")
    as_of = str(payload.get("as_of") or "latest")
    lines = [f"AlloIQ {label} Brief - {as_of}"]

    portfolio_lines = format_portfolio_lines(payload)
    if portfolio_lines:
        lines.extend(["", *portfolio_lines])

    trade_lines = format_trade_lines(payload)
    if trade_lines:
        lines.extend(["", "Trade Feed", *trade_lines])
    else:
        lines.extend(["", "Trade Feed", "No explicit add/trim tickets in the latest report."])

    risk_lines = format_risk_lines(payload)
    if risk_lines:
        lines.extend(["", "Watch", *risk_lines])

    freshness = format_freshness_line(payload)
    if freshness:
        lines.extend(["", freshness])
    lines.extend(["", f"Open: {site_url.rstrip('/')}/dashboard"])
    return truncate_message("\n".join(lines), TELEGRAM_MAX_MESSAGE_CHARS)


def format_urgent_alert_message(payload: dict[str, Any], urgent_items: list[dict[str, Any]], site_url: str) -> str:
    session = str(payload.get("session") or "")
    label = SESSION_LABELS.get(session, session.title() or "Daily")
    as_of = str(payload.get("as_of") or "latest")
    lines = [f"AlloIQ Urgent Alert - {label} {as_of}"]
    portfolio_lines = format_portfolio_lines(payload)
    if portfolio_lines:
        lines.extend(["", *portfolio_lines])
    lines.extend(["", "Urgent Triggers"])
    for item in urgent_items[:6]:
        symbol = str(item.get("symbol") or "").upper()
        reason = str(item.get("urgent_reason") or "urgent trigger")
        severity = str(item.get("urgent_severity") or "urgent")
        current = pct(number(item.get("current_weight", item.get("portfolio_weight"))))
        target = pct(number(item.get("target_weight", item.get("trade_target_weight", item.get("post_action_weight")))))
        expected = number(item.get("risk_adjusted_expected_return"))
        parts = [f"{symbol}: {compact_action(item)}", f"{current} -> {target}", severity]
        if expected is not None:
            parts.append(f"ER {signed_percent_points(expected)}")
        lines.append("; ".join(parts))
        lines.append(f"  Why: {reason}")
        company = first_sentence(str(item.get("company_reason") or item.get("why") or ""))
        if company:
            lines.append(f"  Company: {company}")
        funding = compact_funding(item)
        if funding:
            lines.append(f"  Funding: {funding}")
    lines.extend(["", f"Open: {site_url.rstrip('/')}/dashboard"])
    return truncate_message("\n".join(lines), TELEGRAM_MAX_MESSAGE_CHARS)


def urgent_alert_items(payload: dict[str, Any], previous_payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    items = [item for item in report_action_queue(payload) if isinstance(item, dict)]
    previous_keys = {urgent_key(item) for item in previous_urgent_items(previous_payload or {})}
    urgent: list[dict[str, Any]] = []
    for item in items:
        decorated = urgent_alert_item(item)
        if not decorated:
            continue
        if urgent_key(decorated) in previous_keys:
            continue
        urgent.append(decorated)
    return sorted(
        urgent,
        key=lambda item: (
            -urgent_severity_rank(str(item.get("urgent_severity") or "")),
            -abs(number(item.get("recommended_delta_weight")) or 0),
            -float(number(item.get("confidence")) or 0),
            str(item.get("symbol") or ""),
        ),
    )


def previous_urgent_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in report_action_queue(payload) if urgent_alert_item(item)]


def report_action_queue(payload: dict[str, Any]) -> list[dict[str, Any]]:
    benchmark = payload.get("portfolio_benchmark") if isinstance(payload.get("portfolio_benchmark"), dict) else {}
    queue = benchmark.get("action_queue") if isinstance(benchmark.get("action_queue"), list) else []
    if queue:
        return [row for row in queue if isinstance(row, dict)]
    explanations = payload.get("recommendation_explanations") if isinstance(payload.get("recommendation_explanations"), list) else []
    return [row for row in explanations if isinstance(row, dict)]


def urgent_alert_item(item: dict[str, Any]) -> dict[str, Any] | None:
    symbol = str(item.get("symbol") or "").upper()
    if not symbol:
        return None
    delta = number(item.get("recommended_delta_weight", item.get("pre_funding_delta_weight"))) or 0
    confidence = number(item.get("confidence")) or 0
    current_weight = number(item.get("current_weight", item.get("portfolio_weight"))) or 0
    expected_return = number(item.get("risk_adjusted_expected_return")) or 0
    action = str(item.get("trade_action") or item.get("verdict") or "").lower()
    constraints = {str(value) for value in item.get("active_constraints") or [] if value}
    five_day = abs(number(item.get("five_day_pct")) or 0)

    reason = ""
    severity = ""
    if action in {"add", "starter", "trim"} and abs(delta) >= 0.015 and confidence >= 75:
        reason = f"{compact_action(item)} ticket crossed the 1.5% portfolio-weight urgent threshold with {confidence:.0f} confidence."
        severity = "high"
    elif action == "trim" and delta <= -0.01 and current_weight >= 0.05 and constraints.intersection({"hard_cap", "drawdown_risk", "valuation_support_weak", "company_trim_signal"}):
        reason = "Trim pressure on a material holding crossed the risk/crowding guardrail."
        severity = "high"
    elif action in {"add", "starter"} and delta >= 0.01 and expected_return >= 25 and confidence >= 80:
        reason = f"High-expected-return add surfaced at {signed_percent_points(expected_return)} risk-adjusted expected return."
        severity = "medium"
    elif current_weight >= 0.05 and five_day >= 8:
        reason = f"Material holding moved {five_day:.1f}% over the 5D tape and needs review."
        severity = "medium"

    if not reason:
        return None
    return {
        **item,
        "urgent_reason": reason,
        "urgent_severity": severity,
    }


def urgent_key(item: dict[str, Any]) -> tuple[str, str, int, int]:
    return (
        str(item.get("symbol") or "").upper(),
        str(item.get("trade_action") or item.get("verdict") or "").lower(),
        round_basis_points(number(item.get("recommended_delta_weight", item.get("pre_funding_delta_weight")))),
        round_basis_points(number(item.get("target_weight", item.get("trade_target_weight", item.get("post_action_weight"))))),
    )


def urgent_severity_rank(severity: str) -> int:
    return {"high": 2, "medium": 1}.get(severity, 0)


def round_basis_points(value: float | None) -> int:
    return round((value or 0) * 10_000)


def format_portfolio_lines(payload: dict[str, Any]) -> list[str]:
    portfolio = payload.get("portfolio") if isinstance(payload.get("portfolio"), dict) else {}
    benchmark = payload.get("portfolio_benchmark") if isinstance(payload.get("portfolio_benchmark"), dict) else {}
    lines: list[str] = []
    cash_weight = number(portfolio.get("cash_weight"))
    equity_weight = number(portfolio.get("equity_weight"))
    if cash_weight is not None or equity_weight is not None:
        cash = pct(cash_weight) if cash_weight is not None else "n/a"
        equity = pct(equity_weight) if equity_weight is not None else "n/a"
        lines.append(f"Portfolio: {equity} equity / {cash} cash")

    analytics = benchmark.get("return_analytics") if isinstance(benchmark.get("return_analytics"), dict) else {}
    primary = analytics.get("primary") if isinstance(analytics.get("primary"), dict) else {}
    if primary:
        label = str(primary.get("label") or benchmark.get("primary_label") or "primary")
        total_return = number(primary.get("total_portfolio_return"))
        invested_return = number(primary.get("invested_equity_return"))
        if total_return is not None and invested_return is not None:
            lines.append(f"Return proxy {label}: {signed_percent_points(total_return)} total / {signed_percent_points(invested_return)} invested")
    return lines


def format_trade_lines(payload: dict[str, Any], limit: int = 5) -> list[str]:
    benchmark = payload.get("portfolio_benchmark") if isinstance(payload.get("portfolio_benchmark"), dict) else {}
    queue = benchmark.get("action_queue") if isinstance(benchmark.get("action_queue"), list) else []
    if not queue:
        queue = payload.get("recommendation_explanations") if isinstance(payload.get("recommendation_explanations"), list) else []
    lines: list[str] = []
    for item in [row for row in queue if isinstance(row, dict)][:limit]:
        symbol = str(item.get("symbol") or "").upper()
        if not symbol:
            continue
        action = compact_action(item)
        current = pct(number(item.get("current_weight", item.get("portfolio_weight"))))
        target = pct(number(item.get("target_weight", item.get("trade_target_weight", item.get("post_action_weight")))))
        expected = number(item.get("risk_adjusted_expected_return"))
        confidence = number(item.get("confidence"))
        parts = [f"{symbol}: {action}", f"{current} -> {target}"]
        if expected is not None:
            parts.append(f"ER {signed_percent_points(expected)}")
        if confidence is not None:
            parts.append(f"conf {confidence:.0f}")
        line = "; ".join(parts)
        funding = compact_funding(item)
        if funding:
            line = f"{line}; {funding}"
        lines.append(line)
        reason = first_sentence(str(item.get("company_reason") or item.get("why") or ""))
        if reason:
            lines.append(f"  Company: {reason}")
        catalyst = first_sentence(str(item.get("catalyst_clock") or ""))
        if catalyst:
            lines.append(f"  Catalyst: {catalyst}")
    return lines


def format_risk_lines(payload: dict[str, Any], limit: int = 3) -> list[str]:
    benchmark = payload.get("portfolio_benchmark") if isinstance(payload.get("portfolio_benchmark"), dict) else {}
    queue = benchmark.get("action_queue") if isinstance(benchmark.get("action_queue"), list) else []
    rows = [row for row in queue if isinstance(row, dict)]
    constrained = [row for row in rows if row.get("active_constraints")]
    lines: list[str] = []
    for item in constrained[:limit]:
        symbol = str(item.get("symbol") or "").upper()
        constraints = item.get("active_constraints")
        if symbol and isinstance(constraints, list):
            clean = ", ".join(str(value) for value in constraints[:4] if value)
            if clean:
                lines.append(f"{symbol}: {clean}")
    return lines


def format_freshness_line(payload: dict[str, Any]) -> str:
    site = payload.get("site") if isinstance(payload.get("site"), dict) else {}
    stale = site.get("stale_status") if isinstance(site.get("stale_status"), dict) else {}
    status = str(stale.get("status") or "")
    reason = str(stale.get("reason") or "")
    if status and reason:
        return f"Data health: {status} - {reason}"
    if status:
        return f"Data health: {status}"
    return ""


def compact_action(item: dict[str, Any]) -> str:
    delta = number(item.get("recommended_delta_weight", item.get("pre_funding_delta_weight")))
    trade_action = str(item.get("trade_action") or item.get("verdict") or "").lower()
    if delta is not None and abs(delta) >= 0.00005:
        verb = "Add" if delta > 0 else "Trim"
        return f"{verb} {abs(delta) * 100:.1f}%"
    if trade_action in {"add", "starter"}:
        return "Add"
    if trade_action == "trim":
        return "Trim"
    if trade_action in {"watch", "study"}:
        return "Watch"
    return "Hold"


def compact_funding(item: dict[str, Any]) -> str:
    source = str(item.get("funding_source") or "")
    counterparts = item.get("funding_counterpart_symbols")
    if isinstance(counterparts, list):
        symbols = ", ".join(str(symbol).upper() for symbol in counterparts[:4] if symbol)
    else:
        symbols = ""
    if source == "cash":
        return "funded from cash"
    if source == "funded_by_named_trims" and symbols:
        return f"funded by trims: {symbols}"
    if source == "funds_add_queue" and symbols:
        return f"funds adds: {symbols}"
    return source.replace("_", " ") if source and source != "no_trade" else ""


def telegram_delivery_settings(config: AppConfig) -> dict[str, Any]:
    root = config.data.get("notifications", {}) if isinstance(config.data.get("notifications"), dict) else {}
    telegram = root.get("telegram", {}) if isinstance(root.get("telegram"), dict) else {}
    token_env = str(telegram.get("bot_token_env") or root.get("telegram_bot_token_env") or "ALLOIQ_TELEGRAM_BOT_TOKEN")
    chat_id_env = str(telegram.get("chat_id_env") or root.get("telegram_chat_id_env") or "ALLOIQ_TELEGRAM_CHAT_ID")
    enabled = telegram.get("enabled", root.get("enabled", True))
    return {
        "enabled": bool(enabled),
        "bot_token": os.environ.get(token_env, ""),
        "chat_id": os.environ.get(chat_id_env, ""),
        "timeout_seconds": telegram.get("timeout_seconds", root.get("timeout_seconds", 10)),
    }


def configured_site_url(config: AppConfig) -> str:
    root = config.data.get("notifications", {}) if isinstance(config.data.get("notifications"), dict) else {}
    configured = str(root.get("site_url") or "").strip()
    if configured:
        return configured
    domain = config.product_domain.strip()
    if domain.startswith("http://") or domain.startswith("https://"):
        return domain
    return f"https://{domain or 'alloiq.com'}"


def send_telegram_message(
    bot_token: str,
    chat_id: str,
    text: str,
    timeout_seconds: float = 10,
) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    body = parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {"status": "failed", "reason": f"telegram http {exc.code}", "detail": detail[:240]}
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "failed", "reason": f"telegram send failed: {exc}"}
    if not payload.get("ok"):
        return {"status": "failed", "reason": str(payload.get("description") or "telegram rejected message")}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    return {"status": "sent", "telegram_message_id": result.get("message_id")}


def truncate_message(message: str, max_chars: int) -> str:
    if len(message) <= max_chars:
        return message
    suffix = "\n\nOpen AlloIQ for the full briefing."
    return message[: max(0, max_chars - len(suffix))].rstrip() + suffix


def first_sentence(value: str, max_chars: int = 180) -> str:
    clean = " ".join(value.split())
    if not clean:
        return ""
    stop = clean.find(". ")
    if stop != -1:
        clean = clean[: stop + 1]
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 3].rstrip() + "..."


def number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def signed_percent_points(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.1f}%"
