from __future__ import annotations

from decimal import Decimal
from statistics import mean
from typing import Any


DEFAULT_MACRO_SYMBOLS = [
    "QQQ",
    "SMH",
    "IGV",
    "IWM",
    "SPY",
    "TLT",
    "HYG",
    "LQD",
    "UUP",
    "GLD",
    "USO",
    "XLE",
    "XLU",
    "^VIX",
    "^TNX",
    "BTC-USD",
]

MACRO_LABELS = {
    "QQQ": ("Nasdaq 100", "AI beta"),
    "SMH": ("Semiconductors", "compute cycle"),
    "IGV": ("Software", "AI software monetization"),
    "IWM": ("Small caps", "risk appetite"),
    "SPY": ("S&P 500", "broad tape"),
    "TLT": ("Long Treasuries", "duration/liquidity"),
    "HYG": ("High Yield", "credit risk"),
    "LQD": ("Investment Grade", "credit quality"),
    "UUP": ("US Dollar", "global liquidity"),
    "GLD": ("Gold", "hard-asset hedge"),
    "USO": ("Oil", "energy/input costs"),
    "XLE": ("Energy", "resource equities"),
    "XLU": ("Utilities", "power/grid proxy"),
    "^VIX": ("VIX", "volatility"),
    "^TNX": ("10Y Yield", "rates"),
    "BTC-USD": ("Bitcoin", "liquidity beta"),
}


def build_macro_dashboard(prices: dict[str, dict[str, Decimal]]) -> dict[str, Any]:
    tape = []
    for symbol in DEFAULT_MACRO_SYMBOLS:
        quote = prices.get(symbol, {})
        label, lens = MACRO_LABELS.get(symbol, (symbol, "macro"))
        tape.append(
            {
                "symbol": symbol,
                "label": label,
                "lens": lens,
                "last": float(quote["last"]) if quote.get("last") is not None else None,
                "five_day_pct": float(round(quote["five_day_pct"], 2)) if quote.get("five_day_pct") is not None else None,
                "change_pct": float(round(quote["change_pct"], 2)) if quote.get("change_pct") is not None else None,
            }
        )

    ai_momentum = average_move(prices, ["QQQ", "SMH", "IGV"])
    risk_momentum = average_move(prices, ["IWM", "HYG", "BTC-USD"])
    defensive_momentum = average_move(prices, ["TLT", "GLD", "XLU"])
    rates_move = move(prices, "^TNX")
    dollar_move = move(prices, "UUP")
    vol_move = move(prices, "^VIX")
    regime = classify_regime(ai_momentum, risk_momentum, defensive_momentum, rates_move, dollar_move, vol_move)
    return {
        "regime": regime,
        "scores": {
            "ai_momentum": float(round(ai_momentum, 2)),
            "risk_momentum": float(round(risk_momentum, 2)),
            "defensive_momentum": float(round(defensive_momentum, 2)),
            "rates_move": float(round(rates_move, 2)),
            "dollar_move": float(round(dollar_move, 2)),
            "vol_move": float(round(vol_move, 2)),
        },
        "tape": tape,
        "playbook": playbook_for_regime(regime),
    }


def average_move(prices: dict[str, dict[str, Decimal]], symbols: list[str]) -> Decimal:
    values = [move(prices, symbol) for symbol in symbols if move(prices, symbol) is not None]
    if not values:
        return Decimal("0")
    return Decimal(str(mean(values)))


def move(prices: dict[str, dict[str, Decimal]], symbol: str) -> Decimal | None:
    quote = prices.get(symbol)
    if not quote:
        return None
    return quote.get("five_day_pct")


def classify_regime(
    ai_momentum: Decimal,
    risk_momentum: Decimal,
    defensive_momentum: Decimal,
    rates_move: Decimal,
    dollar_move: Decimal,
    vol_move: Decimal,
) -> str:
    if ai_momentum > 3 and risk_momentum > 1 and vol_move < 10:
        return "risk-on AI acceleration"
    if rates_move > 4 and dollar_move > 1 and ai_momentum < 2:
        return "rates/dollar headwind"
    if defensive_momentum > 2 and risk_momentum < 0:
        return "defensive rotation"
    if vol_move > 15:
        return "volatility shock"
    return "mixed macro tape"


def playbook_for_regime(regime: str) -> list[str]:
    if regime == "risk-on AI acceleration":
        return [
            "Favor high-conviction AI beneficiaries only where forward return still beats the downside case.",
            "Look for second-derivative evidence in orders, capex, utilization, and power contracts before adding.",
            "Avoid adding solely because price confirms the story; require fresh evidence or a dislocation.",
        ]
    if regime == "rates/dollar headwind":
        return [
            "Raise the bar for long-duration equities and financed data-center stories.",
            "Prefer balance sheets, funded contracts, and near-term free-cash-flow evidence.",
            "Review position sizing before earnings or macro prints that can reprice duration.",
        ]
    if regime == "defensive rotation":
        return [
            "Separate AI demand durability from market willingness to capitalize distant cash flows.",
            "Watch power, grid, and hard-asset beneficiaries for relative strength.",
            "Use manager consensus as a research queue, not proof of timing.",
        ]
    if regime == "volatility shock":
        return [
            "Do not force new longs while correlations are rising.",
            "Identify forced-selling candidates in the AI stack and wait for liquidity to stabilize.",
            "Check whether hedge funds are using puts/calls around crowded AI names.",
        ]
    return [
        "Keep gross exposure tied to evidence quality instead of narrative confidence.",
        "Track whether AI infrastructure, software, and power baskets are confirming each other.",
        "Prefer ideas with clear falsifiers and an identifiable catalyst path.",
    ]
