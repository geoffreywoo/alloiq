from __future__ import annotations

import json
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .util import decimal_or_zero


def fetch_daily_prices(symbols: list[str], range_: str = "5d", interval: str = "1d", max_workers: int = 16, timeout: int = 5) -> dict[str, dict[str, Decimal]]:
    prices: dict[str, dict[str, Decimal]] = {}
    unique = unique_symbols(symbols)
    if not unique:
        return prices
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(unique)))) as pool:
        futures = {pool.submit(fetch_chart, symbol, range_, interval, timeout): symbol for symbol in unique}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                quote = future.result()
            except Exception:
                quote = {}
            if quote:
                prices[symbol] = quote
    return prices


def fetch_return_windows(symbols: list[str], range_: str = "1y", interval: str = "1d", max_workers: int = 16, timeout: int = 5) -> dict[str, dict[str, Decimal]]:
    windows: dict[str, dict[str, Decimal]] = {}
    unique = unique_symbols(symbols)
    if not unique:
        return windows
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(unique)))) as pool:
        futures = {pool.submit(fetch_chart_history, symbol, range_, interval, timeout): symbol for symbol in unique}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                history = future.result()
            except Exception:
                history = []
            if history:
                windows[symbol] = return_windows_for_history(history)
    return windows


def unique_symbols(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for symbol in symbols:
        normalized = str(symbol).upper().strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def fetch_chart(symbol: str, range_: str = "5d", interval: str = "1d", timeout: int = 6) -> dict[str, Decimal]:
    safe_symbol = urllib.parse.quote(symbol)
    params = urllib.parse.urlencode({"range": range_, "interval": interval})
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{safe_symbol}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "autoinvestbot/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}
    result = data.get("chart", {}).get("result") or []
    if not result:
        return {}
    quote = result[0].get("indicators", {}).get("quote", [{}])[0]
    closes = [decimal_or_zero(v) for v in quote.get("close", []) if v is not None]
    if not closes:
        return {}
    last = closes[-1]
    prev = closes[-2] if len(closes) > 1 else last
    change = Decimal("0") if prev == 0 else ((last - prev) / prev) * Decimal("100")
    five_day = Decimal("0")
    if len(closes) > 1 and closes[0] != 0:
        five_day = ((last - closes[0]) / closes[0]) * Decimal("100")
    return {"last": last, "change_pct": change, "five_day_pct": five_day}


def fetch_chart_history(symbol: str, range_: str = "1y", interval: str = "1d", timeout: int = 6) -> list[dict[str, Any]]:
    safe_symbol = urllib.parse.quote(symbol)
    params = urllib.parse.urlencode({"range": range_, "interval": interval})
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{safe_symbol}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "autoinvestbot/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []
    result = data.get("chart", {}).get("result") or []
    if not result:
        return []
    timestamps = result[0].get("timestamp") or []
    quote = result[0].get("indicators", {}).get("quote", [{}])[0]
    closes = quote.get("close", [])
    history: list[dict[str, Any]] = []
    for timestamp, close in zip(timestamps, closes):
        if close is None:
            continue
        history.append(
            {
                "date": datetime.fromtimestamp(int(timestamp), tz=timezone.utc).date(),
                "close": decimal_or_zero(close),
            }
        )
    return history


def return_windows_for_history(history: list[dict[str, Any]]) -> dict[str, Decimal]:
    rows = [row for row in history if row.get("close")]
    if not rows:
        return {}
    last = rows[-1]["close"]
    returns: dict[str, Decimal] = {"last": last}
    for key, lookback in [("1d", 1), ("5d", 5), ("1m", 21), ("3m", 63), ("6m", 126), ("1y", 252)]:
        reference = reference_close(rows, lookback)
        if reference:
            returns[key] = pct_return(last, reference)
    ytd_reference = ytd_close(rows)
    if ytd_reference:
        returns["ytd"] = pct_return(last, ytd_reference)
    return returns


def reference_close(rows: list[dict[str, Any]], lookback: int) -> Decimal | None:
    if len(rows) > lookback:
        return rows[-lookback - 1]["close"]
    if len(rows) >= max(2, int(lookback * 0.7)):
        return rows[0]["close"]
    return None


def ytd_close(rows: list[dict[str, Any]]) -> Decimal | None:
    last_year = rows[-1]["date"].year
    for row in rows:
        if row["date"].year == last_year:
            return row["close"]
    return rows[0]["close"] if rows else None


def pct_return(last: Decimal, reference: Decimal) -> Decimal:
    if reference == 0:
        return Decimal("0")
    return ((last - reference) / reference) * Decimal("100")
