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


def build_macro_dashboard(
    prices: dict[str, dict[str, Decimal]],
    fred_macro: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
    fred_scores = (fred_macro or {}).get("scores") or {}
    regime = classify_regime(
        ai_momentum,
        risk_momentum,
        defensive_momentum,
        rates_move,
        dollar_move,
        vol_move,
        fred_scores,
    )
    scores = {
        "ai_momentum": float(round(ai_momentum, 2)),
        "risk_momentum": float(round(risk_momentum, 2)),
        "defensive_momentum": float(round(defensive_momentum, 2)),
        "rates_move": decimal_score(rates_move),
        "dollar_move": decimal_score(dollar_move),
        "vol_move": decimal_score(vol_move),
    }
    for key in [
        "yield_curve_10y2y",
        "credit_spread_high_yield",
        "credit_spread_change_1m",
        "credit_stress_score",
        "liquidity_pressure_score",
        "yield_curve_inversion_score",
        "energy_pressure_score",
    ]:
        if fred_scores.get(key) is not None:
            scores[key] = float(fred_scores[key])
    return {
        "regime": regime,
        "scores": scores,
        "tape": tape,
        "fred_macro": fred_macro or {},
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


def decimal_score(value: Decimal | None) -> float:
    return float(round(value or Decimal("0"), 2))


def classify_regime(
    ai_momentum: Decimal,
    risk_momentum: Decimal,
    defensive_momentum: Decimal,
    rates_move: Decimal | None,
    dollar_move: Decimal | None,
    vol_move: Decimal | None,
    fred_scores: dict[str, Any] | None = None,
) -> str:
    rates_move = rates_move or Decimal("0")
    dollar_move = dollar_move or Decimal("0")
    vol_move = vol_move or Decimal("0")
    fred_scores = fred_scores or {}
    credit_stress = Decimal(str(fred_scores.get("credit_stress_score") or 0))
    liquidity_pressure = Decimal(str(fred_scores.get("liquidity_pressure_score") or 0))
    curve_inversion = Decimal(str(fred_scores.get("yield_curve_inversion_score") or 0))
    if credit_stress >= 8 or liquidity_pressure >= 8:
        return "credit/liquidity stress"
    if curve_inversion >= 8 and rates_move > 2:
        return "curve/rates pressure"
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
    if regime == "credit/liquidity stress":
        return [
            "Raise the evidence bar for financed data-center, neocloud, and long-duration AI infrastructure exposure.",
            "Prefer names with funded balance sheets, signed contracts, and near-term cash-flow proof.",
            "Treat credit-spread widening as a sizing constraint even when AI demand indicators remain strong.",
        ]
    if regime == "curve/rates pressure":
        return [
            "Discount-rate pressure argues for smaller starter weights and stricter valuation support.",
            "Check whether earnings revisions are strong enough to offset higher duration risk.",
            "Avoid adding before financing or earnings events unless primary-source evidence improved.",
        ]
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
