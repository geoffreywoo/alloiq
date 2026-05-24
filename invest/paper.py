from __future__ import annotations

from datetime import date
from typing import Any

from .util import stable_id


PAPER_POLICY_VERSION = "2026-05-paper-equity-v1"


def build_paper_portfolio(
    as_of: date,
    session: str,
    portfolio: dict[str, Any],
    approval_tickets: list[dict[str, Any]],
    cards: list[dict[str, Any]],
    outcome_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    card_by_symbol = {str(card.get("symbol") or "").upper(): card for card in cards}
    trades = [paper_trade_from_ticket(as_of, session, ticket, card_by_symbol.get(str(ticket.get("symbol") or "").upper(), {})) for ticket in approval_tickets]
    snapshots = paper_weight_snapshots(portfolio, trades)
    return {
        "version": PAPER_POLICY_VERSION,
        "mode": "paper_only",
        "as_of": as_of.isoformat(),
        "session": session,
        "live_order_execution": "disabled",
        "fill_policy": "next_available_daily_close_proxy",
        "paper_trades": trades,
        "snapshots": snapshots,
        "metrics": paper_metrics(trades, outcome_history or []),
    }


def paper_trade_from_ticket(as_of: date, session: str, ticket: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
    symbol = str(ticket.get("symbol") or "").upper()
    return {
        "paper_trade_id": stable_id([as_of.isoformat(), session, symbol, ticket.get("ticket_id"), "paper"]),
        "ticket_id": ticket.get("ticket_id", ""),
        "symbol": symbol,
        "trade_action": ticket.get("trade_action", "study"),
        "status": "filled_proxy" if session == "postmarket" else "planned",
        "current_weight": round(float(ticket.get("current_weight") or 0), 6),
        "recommended_delta_weight": round(float(ticket.get("recommended_delta_weight") or 0), 6),
        "target_weight": round(float(ticket.get("target_weight") or 0), 6),
        "proxy_fill_price": card.get("last_price"),
        "fill_policy": "next_available_daily_close_proxy",
        "created_at": f"{as_of.isoformat()}T00:00:00Z",
    }


def paper_weight_snapshots(portfolio: dict[str, Any], trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    current = {
        str(row.get("symbol") or "").upper(): {
            "symbol": str(row.get("symbol") or "").upper(),
            "bucket": row.get("bucket", "unmapped"),
            "current_weight": round(float(row.get("weight") or 0), 6),
            "paper_target_weight": round(float(row.get("weight") or 0), 6),
        }
        for row in portfolio.get("by_symbol", [])
    }
    for trade in trades:
        symbol = trade["symbol"]
        row = current.setdefault(
            symbol,
            {
                "symbol": symbol,
                "bucket": "unmapped",
                "current_weight": round(float(trade.get("current_weight") or 0), 6),
                "paper_target_weight": round(float(trade.get("current_weight") or 0), 6),
            },
        )
        row["paper_target_weight"] = round(float(trade.get("target_weight") or row["paper_target_weight"]), 6)
        row["paper_delta_weight"] = round(row["paper_target_weight"] - row["current_weight"], 6)
    return sorted(current.values(), key=lambda item: abs(item.get("paper_delta_weight", 0)), reverse=True)[:20]


def paper_metrics(trades: list[dict[str, Any]], outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [row for row in outcomes if row.get("paper_trade_id") and row.get("forward_return_pct") is not None]
    winners = [row for row in completed if float(row.get("forward_return_pct") or 0) > 0]
    return {
        "paper_trade_count": len(trades),
        "planned_count": sum(1 for trade in trades if trade.get("status") == "planned"),
        "filled_proxy_count": sum(1 for trade in trades if trade.get("status") == "filled_proxy"),
        "completed_outcome_count": len(completed),
        "hit_rate": round(len(winners) / len(completed), 4) if completed else None,
        "max_drawdown": None,
        "missed_opportunity": None,
        "status": "tracking" if trades else "no_current_paper_trades",
    }
