from __future__ import annotations

import csv
import urllib.parse
import urllib.request
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from io import StringIO
from typing import Any, Callable


FRED_MACRO_VERSION = "2026-05-fred-macro-v1"
FRED_GRAPH_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

DEFAULT_FRED_SERIES = {
    "DGS10": {"label": "10Y Treasury yield", "lens": "discount rates", "units": "percent"},
    "DGS2": {"label": "2Y Treasury yield", "lens": "policy-rate expectations", "units": "percent"},
    "BAMLH0A0HYM2": {"label": "High-yield option-adjusted spread", "lens": "credit stress", "units": "percent"},
    "NFCI": {"label": "Chicago Fed National Financial Conditions Index", "lens": "financial conditions", "units": "index"},
    "WALCL": {"label": "Federal Reserve balance sheet", "lens": "system liquidity", "units": "millions_usd"},
    "DCOILWTICO": {"label": "WTI crude oil", "lens": "energy input costs", "units": "usd_per_barrel"},
    "DHHNGSP": {"label": "Henry Hub natural gas", "lens": "power input costs", "units": "usd_per_mmbtu"},
}


UrlOpen = Callable[..., Any]


def build_fred_macro_snapshot(
    settings: dict[str, Any] | None = None,
    as_of: date | None = None,
    urlopen: UrlOpen | None = None,
) -> dict[str, Any]:
    settings = settings or {}
    as_of = as_of or date.today()
    if settings.get("enabled", True) is False:
        return {
            "version": FRED_MACRO_VERSION,
            "source": "fred_graph_csv",
            "status": "disabled",
            "as_of": as_of.isoformat(),
            "series": [],
            "scores": {},
            "regime_flags": [],
        }

    requested = settings.get("series") or list(DEFAULT_FRED_SERIES)
    series_ids = unique_series_ids(requested)
    timeout = int(settings.get("timeout_seconds") or 5)
    max_observations = int(settings.get("max_observations") or 520)
    opener = urlopen or urllib.request.urlopen
    series_rows = []
    raw: dict[str, list[dict[str, Any]]] = {}
    for series_id in series_ids:
        rows = []
        try:
            rows = fetch_fred_series(series_id, timeout=timeout, urlopen=opener)
        except Exception:
            rows = []
        if max_observations > 0:
            rows = rows[-max_observations:]
        if rows:
            raw[series_id] = rows
        series_rows.append(series_summary(series_id, rows))

    scores = fred_macro_scores(raw)
    usable_count = sum(1 for row in series_rows if row.get("latest_value") is not None)
    status = "ok" if usable_count >= 3 else "limited" if usable_count else "missing"
    return {
        "version": FRED_MACRO_VERSION,
        "source": "fred_graph_csv",
        "source_url": FRED_GRAPH_CSV_URL,
        "status": status,
        "as_of": as_of.isoformat(),
        "requested_series_count": len(series_ids),
        "series_count": usable_count,
        "series": series_rows,
        "scores": scores,
        "regime_flags": fred_regime_flags(scores),
    }


def fetch_fred_series(
    series_id: str,
    *,
    timeout: int = 5,
    urlopen: UrlOpen | None = None,
) -> list[dict[str, Any]]:
    opener = urlopen or urllib.request.urlopen
    params = urllib.parse.urlencode({"id": series_id})
    url = f"{FRED_GRAPH_CSV_URL}?{params}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "AlloIQ/0.1 FRED macro signals https://alloiq.com"},
    )
    with opener(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8")
    return parse_fred_csv(text, series_id)


def parse_fred_csv(text: str, series_id: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(StringIO(text))
    rows = []
    value_key = series_id if reader.fieldnames and series_id in reader.fieldnames else None
    if value_key is None and reader.fieldnames:
        value_key = next((key for key in reader.fieldnames if key.lower() not in {"date", "observation_date"}), None)
    for raw in reader:
        observed = parse_date(raw.get("observation_date") or raw.get("DATE") or raw.get("date"))
        value = parse_decimal(raw.get(value_key or ""))
        if observed and value is not None:
            rows.append({"date": observed, "value": value})
    return sorted(rows, key=lambda row: row["date"])


def series_summary(series_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    metadata = DEFAULT_FRED_SERIES.get(series_id, {"label": series_id, "lens": "macro", "units": ""})
    latest = rows[-1] if rows else None
    if not latest:
        return {
            "series_id": series_id,
            "label": metadata["label"],
            "lens": metadata["lens"],
            "units": metadata.get("units", ""),
            "status": "missing",
            "latest_date": None,
            "latest_value": None,
            "change_1m": None,
            "change_3m": None,
            "pct_change_1m": None,
            "pct_change_3m": None,
        }
    latest_date = latest["date"]
    latest_value = latest["value"]
    one_month = value_on_or_before(rows, latest_date - timedelta(days=30))
    three_month = value_on_or_before(rows, latest_date - timedelta(days=92))
    return {
        "series_id": series_id,
        "label": metadata["label"],
        "lens": metadata["lens"],
        "units": metadata.get("units", ""),
        "status": "ok",
        "latest_date": latest_date.isoformat(),
        "latest_value": rounded_float(latest_value),
        "change_1m": rounded_float(latest_value - one_month) if one_month is not None else None,
        "change_3m": rounded_float(latest_value - three_month) if three_month is not None else None,
        "pct_change_1m": pct_change(latest_value, one_month),
        "pct_change_3m": pct_change(latest_value, three_month),
    }


def fred_macro_scores(series: dict[str, list[dict[str, Any]]]) -> dict[str, float | None]:
    dgs10 = latest_value(series.get("DGS10", []))
    dgs2 = latest_value(series.get("DGS2", []))
    hy_oas = latest_value(series.get("BAMLH0A0HYM2", []))
    hy_oas_1m = absolute_change(series.get("BAMLH0A0HYM2", []), days=30)
    nfci = latest_value(series.get("NFCI", []))
    walcl_3m = percent_change(series.get("WALCL", []), days=92)
    oil_1m = percent_change(series.get("DCOILWTICO", []), days=30)
    gas_1m = percent_change(series.get("DHHNGSP", []), days=30)

    curve = None if dgs10 is None or dgs2 is None else dgs10 - dgs2
    credit_stress = None
    if hy_oas is not None:
        spread_level = max(Decimal("0"), hy_oas - Decimal("4.0")) * Decimal("3.0")
        spread_change = max(Decimal("-2.0"), min(Decimal("2.0"), hy_oas_1m or Decimal("0"))) * Decimal("10.0")
        credit_stress = clamp_decimal(spread_level + spread_change, Decimal("-10"), Decimal("25"))

    liquidity_pressure = None
    if nfci is not None or walcl_3m is not None:
        nfci_component = (nfci or Decimal("0")) * Decimal("6.0")
        balance_sheet_component = -(walcl_3m or Decimal("0")) * Decimal("0.35")
        liquidity_pressure = clamp_decimal(nfci_component + balance_sheet_component, Decimal("-15"), Decimal("25"))

    curve_inversion = None
    if curve is not None:
        curve_inversion = clamp_decimal(max(Decimal("0"), -curve) * Decimal("8.0"), Decimal("0"), Decimal("20"))

    energy_inputs = [value for value in [oil_1m, gas_1m] if value is not None]
    energy_pressure = None
    if energy_inputs:
        energy_pressure = clamp_decimal(sum(energy_inputs) / Decimal(len(energy_inputs)) * Decimal("0.25"), Decimal("-12"), Decimal("20"))

    return {
        "yield_curve_10y2y": rounded_float(curve),
        "credit_spread_high_yield": rounded_float(hy_oas),
        "credit_spread_change_1m": rounded_float(hy_oas_1m),
        "credit_stress_score": rounded_float(credit_stress),
        "liquidity_pressure_score": rounded_float(liquidity_pressure),
        "yield_curve_inversion_score": rounded_float(curve_inversion),
        "energy_pressure_score": rounded_float(energy_pressure),
    }


def fred_regime_flags(scores: dict[str, float | None]) -> list[str]:
    flags = []
    if (scores.get("yield_curve_10y2y") or 0) < -0.25:
        flags.append("yield_curve_inverted")
    if (scores.get("credit_stress_score") or 0) >= 5:
        flags.append("credit_spreads_widening")
    if (scores.get("liquidity_pressure_score") or 0) >= 5:
        flags.append("liquidity_tightening")
    if (scores.get("energy_pressure_score") or 0) >= 6:
        flags.append("energy_cost_pressure")
    return flags or ["fred_macro_neutral"]


def latest_value(rows: list[dict[str, Any]]) -> Decimal | None:
    return rows[-1]["value"] if rows else None


def absolute_change(rows: list[dict[str, Any]], *, days: int) -> Decimal | None:
    if not rows:
        return None
    latest = rows[-1]
    reference = value_on_or_before(rows, latest["date"] - timedelta(days=days))
    return latest["value"] - reference if reference is not None else None


def percent_change(rows: list[dict[str, Any]], *, days: int) -> Decimal | None:
    if not rows:
        return None
    latest = rows[-1]
    reference = value_on_or_before(rows, latest["date"] - timedelta(days=days))
    if reference is None or reference == 0:
        return None
    return ((latest["value"] - reference) / reference) * Decimal("100")


def value_on_or_before(rows: list[dict[str, Any]], target: date) -> Decimal | None:
    for row in reversed(rows):
        if row["date"] <= target:
            return row["value"]
    return rows[0]["value"] if rows else None


def parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def parse_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    clean = str(value).strip()
    if not clean or clean == ".":
        return None
    try:
        return Decimal(clean)
    except (InvalidOperation, ValueError):
        return None


def rounded_float(value: Decimal | None) -> float | None:
    return float(round(value, 4)) if value is not None else None


def pct_change(latest: Decimal, reference: Decimal | None) -> float | None:
    if reference is None or reference == 0:
        return None
    return rounded_float(((latest - reference) / reference) * Decimal("100"))


def clamp_decimal(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return min(high, max(low, value))


def unique_series_ids(series_ids: list[Any]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in series_ids:
        clean = str(item or "").strip().upper()
        if clean and clean not in seen:
            seen.add(clean)
            ordered.append(clean)
    return ordered
