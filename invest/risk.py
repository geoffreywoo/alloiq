from __future__ import annotations

from typing import Any


DEFAULT_LIMITS = {
    "max_single_name_weight": 0.15,
    "max_bucket_weight": 0.45,
    "max_daily_turnover": 0.08,
    "max_one_ticket_delta": 0.03,
    "min_signal_family_count": 2,
    "earnings_blackout_days": 2,
    "earnings_risk_window_days": 7,
    "no_add_symbols": [],
    "watch_only_symbols": [],
}


def apply_risk_controls(
    actions: list[dict[str, Any]],
    portfolio: dict[str, Any],
    cards: list[dict[str, Any]],
    earnings_events: list[dict[str, Any]] | None = None,
    limits: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    limits = normalize_limits(limits)
    cards_by_symbol = {str(card.get("symbol", "")).upper(): card for card in cards}
    bucket_weights = {
        str(row.get("bucket", "unmapped")): float(row.get("weight") or 0)
        for row in portfolio.get("by_bucket", [])
    }
    event_by_symbol = nearest_earnings_by_symbol(earnings_events or [])
    remaining_turnover = float(limits["max_daily_turnover"])
    controlled: list[dict[str, Any]] = []
    for action in actions:
        adjusted = apply_action_limits(
            dict(action),
            cards_by_symbol.get(str(action.get("symbol", "")).upper(), {}),
            bucket_weights,
            event_by_symbol.get(str(action.get("symbol", "")).upper()),
            limits,
            remaining_turnover,
        )
        remaining_turnover = max(0.0, remaining_turnover - abs(float(adjusted.get("recommended_delta_weight") or 0)))
        controlled.append(adjusted)
    return controlled


def apply_action_limits(
    action: dict[str, Any],
    card: dict[str, Any],
    bucket_weights: dict[str, float],
    earnings_event: dict[str, Any] | None,
    limits: dict[str, Any],
    remaining_turnover: float,
) -> dict[str, Any]:
    current = float(action.get("portfolio_weight", action.get("current_weight") or 0) or 0)
    delta = float(action.get("recommended_delta_weight") or 0)
    target = float(action.get("target_weight", current + delta) or 0)
    bucket = str(action.get("bucket") or card.get("bucket") or "unmapped")
    flags = list(dict.fromkeys([str(flag) for flag in action.get("risk_flags", []) if flag]))
    notes = list(action.get("constraint_notes", []))
    original_delta = delta

    if delta > 0 and str(action.get("symbol", "")).upper() in limits["no_add_symbols"]:
        delta = 0.0
        target = current
        flags.append("no_add_symbol")
        notes.append("Configured no-add symbol; add proposal converted to watch.")
    if delta > 0 and str(action.get("symbol", "")).upper() in limits["watch_only_symbols"]:
        delta = 0.0
        target = current
        flags.append("watch_only_symbol")
        notes.append("Configured watch-only symbol; no add is allowed.")

    signal_count = int(action.get("signal_family_count") or card.get("signal_family_count") or len(card.get("signal_families") or []))
    if delta > 0 and signal_count < int(limits["min_signal_family_count"]):
        delta = 0.0
        target = current
        flags.append("insufficient_signal_families")
        notes.append("Minimum independent signal-family count not met.")

    if earnings_event and delta > 0:
        days_until = earnings_event.get("days_until")
        if days_until is not None and abs(int(days_until)) <= int(limits["earnings_blackout_days"]):
            delta = 0.0
            target = current
            flags.append("earnings_blackout")
            notes.append("Add proposal blocked inside the earnings blackout window.")
        elif days_until is not None and abs(int(days_until)) <= int(limits["earnings_risk_window_days"]):
            capped = min(delta, 0.01)
            if capped < delta:
                delta = capped
                target = current + delta
                flags.append("earnings_risk_window")
                notes.append("Add proposal capped near earnings.")

    if delta > 0:
        max_single = float(limits["max_single_name_weight"])
        if current + delta > max_single:
            delta = max(0.0, max_single - current)
            target = min(target, max_single)
            flags.append("single_name_cap")
            notes.append("Target capped by max single-name weight.")

        bucket_weight = bucket_weights.get(bucket, 0.0)
        max_bucket = float(limits["max_bucket_weight"])
        if bucket_weight + delta > max_bucket:
            delta = max(0.0, max_bucket - bucket_weight)
            target = current + delta
            flags.append("bucket_cap")
            notes.append("Add capped by bucket exposure limit.")

    max_ticket_delta = float(limits["max_one_ticket_delta"])
    if abs(delta) > max_ticket_delta:
        delta = max_ticket_delta if delta > 0 else -max_ticket_delta
        target = current + delta
        flags.append("ticket_delta_cap")
        notes.append("Proposal capped by max one-ticket delta.")

    if abs(delta) > remaining_turnover:
        delta = remaining_turnover if delta > 0 else -remaining_turnover
        target = current + delta
        flags.append("daily_turnover_cap")
        notes.append("Proposal capped by remaining daily turnover budget.")

    action["current_weight"] = round_weight(current)
    action["portfolio_weight"] = round_weight(current)
    action["recommended_delta_weight"] = round_weight(delta)
    action["post_action_weight"] = round_weight(max(0.0, current + delta))
    action["target_weight"] = round_weight(max(0.0, target))
    action["risk_flags"] = sorted(set(flags))
    action["constraint_notes"] = notes
    action["confidence"] = confidence_score(action, card)
    action["approval_required"] = True
    action["order_execution"] = "none"
    action["sizing_basis"] = "portfolio-weight research proposal; approval required; no order execution"
    if original_delta != delta and delta == 0 and action.get("trade_action") == "add":
        action["trade_action"] = "watch"
        action["action"] = "Watch only; risk controls blocked the add until constraints clear."
        action["sizing_summary"] = action["action"]
    return action


def confidence_score(action: dict[str, Any], card: dict[str, Any]) -> int:
    signal_count = int(action.get("signal_family_count") or card.get("signal_family_count") or 0)
    score = float(action.get("score") or card.get("score") or 0)
    priority = float(action.get("priority") or 0)
    risk_penalty = min(25, len(action.get("risk_flags") or []) * 6)
    confidence = int(min(95, signal_count * 16 + score * 0.45 + priority * 0.22) - risk_penalty)
    return max(5, confidence)


def nearest_earnings_by_symbol(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_symbol: dict[str, dict[str, Any]] = {}
    for event in events:
        symbol = str(event.get("symbol") or "").upper()
        if not symbol or event.get("days_until") is None:
            continue
        current = by_symbol.get(symbol)
        if current is None or abs(int(event["days_until"])) < abs(int(current["days_until"])):
            by_symbol[symbol] = event
    return by_symbol


def normalize_limits(limits: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULT_LIMITS)
    merged.update(limits or {})
    for key in ("no_add_symbols", "watch_only_symbols"):
        merged[key] = {str(symbol).upper().strip() for symbol in merged.get(key, []) if str(symbol).strip()}
    return merged


def round_weight(value: float) -> float:
    return round(float(value or 0), 6)
