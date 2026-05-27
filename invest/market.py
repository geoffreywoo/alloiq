from __future__ import annotations

import json
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .symbols import equivalent_symbols
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


def fetch_return_windows(symbols: list[str], range_: str = "1y", interval: str = "1d", max_workers: int = 16, timeout: int = 5) -> dict[str, dict[str, Any]]:
    windows: dict[str, dict[str, Any]] = {}
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


def return_windows_for_history(history: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for row in history if row.get("close")]
    if not rows:
        return {}
    last = rows[-1]["close"]
    returns: dict[str, Any] = {"last": last, "last_date": rows[-1]["date"].isoformat()}
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


def build_price_audit(
    symbols: list[str],
    prices: dict[str, dict[str, Any]],
    return_windows: dict[str, dict[str, Any]],
    focus_symbols: list[str] | None = None,
    max_last_price_drift_pct: float = 1.0,
    max_1d_return_drift_pp: float = 1.0,
) -> dict[str, Any]:
    checked_at = datetime.now(timezone.utc).isoformat()
    ordered_symbols = unique_symbols(symbols)
    focus_set = set(unique_symbols(focus_symbols or []))
    rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    checked_count = 0
    max_last_drift = 0.0
    max_1d_drift = 0.0

    for symbol in ordered_symbols:
        quote = symbol_lookup(prices, symbol)
        windows = symbol_lookup(return_windows, symbol)
        row: dict[str, Any] = {
            "symbol": symbol,
            "focus": symbol in focus_set,
            "status": "ok",
            "source": "yahoo_chart",
        }
        row_issues: list[str] = []
        if not quote:
            row["status"] = "limited"
            row_issues.append("missing live quote")
        if not windows:
            row["status"] = "limited"
            row_issues.append("missing return window")

        quote_last = optional_float((quote or {}).get("last"))
        window_last = optional_float((windows or {}).get("last"))
        quote_1d = optional_float((quote or {}).get("change_pct"))
        window_1d = optional_float((windows or {}).get("1d"))
        window_5d = optional_float((windows or {}).get("5d"))
        row.update(
            {
                "quote_last": round(quote_last, 4) if quote_last is not None else None,
                "return_window_last": round(window_last, 4) if window_last is not None else None,
                "return_window_last_date": (windows or {}).get("last_date"),
                "quote_1d_pct": round(quote_1d, 4) if quote_1d is not None else None,
                "return_window_1d_pct": round(window_1d, 4) if window_1d is not None else None,
                "return_window_5d_pct": round(window_5d, 4) if window_5d is not None else None,
            }
        )

        if quote_last is not None and window_last is not None:
            checked_count += 1
            drift = abs(pct_delta_float(quote_last, window_last))
            max_last_drift = max(max_last_drift, drift)
            row["last_price_drift_pct"] = round(drift, 4)
            if drift > max_last_price_drift_pct:
                row["status"] = "stale"
                row_issues.append(f"live quote and return-window last differ by {drift:.2f}%")
        if quote_1d is not None and window_1d is not None:
            drift = abs(quote_1d - window_1d)
            max_1d_drift = max(max_1d_drift, drift)
            row["one_day_return_drift_pp"] = round(drift, 4)
            if drift > max_1d_return_drift_pp:
                row["status"] = "stale"
                row_issues.append(f"live quote and return-window 1D return differ by {drift:.2f} pp")

        if row_issues:
            row["issues"] = row_issues
            issues.append(
                {
                    "source": "yahoo_chart",
                    "label": f"{symbol} price audit",
                    "status": row["status"],
                    "detail": "; ".join(row_issues),
                    "remediation": "Refresh live quote and return-window chart data before relying on price-action metrics.",
                }
            )
        rows.append(row)

    stale_count = sum(1 for row in rows if row.get("status") == "stale")
    limited_count = sum(1 for row in rows if row.get("status") == "limited")
    status = "ok"
    if not ordered_symbols:
        status = "missing"
    elif stale_count:
        status = "stale"
    elif limited_count:
        status = "limited"

    focus_rows = sorted(
        [row for row in rows if row.get("focus") and row.get("return_window_5d_pct") is not None],
        key=lambda row: abs(float(row.get("return_window_1d_pct") or 0)),
        reverse=True,
    )
    focus_text = "; ".join(
        f"{row['symbol']} last ${row['return_window_last']:.2f}, "
        f"1D {signed_pct(row.get('return_window_1d_pct'))}, "
        f"5D {signed_pct(row.get('return_window_5d_pct'))}"
        for row in focus_rows[:4]
    )
    detail = (
        f"{checked_count}/{len(ordered_symbols)} symbols passed live quote/window coverage; "
        f"max last-price drift {max_last_drift:.2f}%; max 1D drift {max_1d_drift:.2f} pp."
    )
    if focus_text:
        detail += f" Focus: {focus_text}."

    return {
        "version": "2026-05-live-price-window-audit-v1",
        "checked_at": checked_at,
        "source": "yahoo_chart",
        "status": status,
        "symbol_count": len(ordered_symbols),
        "checked_count": checked_count,
        "stale_count": stale_count,
        "limited_count": limited_count,
        "issue_count": len(issues),
        "max_last_price_drift_pct": round(max_last_drift, 4),
        "max_1d_return_drift_pp": round(max_1d_drift, 4),
        "detail": detail,
        "issues": issues[:20],
        "rows": rows,
    }


def symbol_lookup(values: dict[str, dict[str, Any]], symbol: str) -> dict[str, Any]:
    for candidate in equivalent_symbols(symbol):
        row = values.get(candidate.upper())
        if row:
            return row
    return {}


def optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def pct_delta_float(value: float, reference: float) -> float:
    if reference == 0:
        return 0.0
    return ((value - reference) / reference) * 100.0


def signed_pct(value: Any) -> str:
    number = optional_float(value)
    if number is None:
        return "n/a"
    sign = "+" if number >= 0 else ""
    return f"{sign}{number:.2f}%"
