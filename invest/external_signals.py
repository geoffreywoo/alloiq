from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta
from statistics import mean
from typing import Any, Callable

from .config import AppConfig
from .util import SEC_USER_AGENT, parse_date, parse_datetime, stable_id


EXTERNAL_SIGNALS_VERSION = "2026-05-external-signal-providers-v1"

ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
CFTC_LEGACY_COT_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
FINRA_SHORT_INTEREST_URL = "https://api.finra.org/data/group/otcmarket/name/consolidatedShortInterest"
EIA_POWER_URL = "https://api.eia.gov/v2/electricity/electric-power-operational-data/data/"
FINRA_SHORT_INTEREST_DEFAULT_MAX_AGE_DAYS = 75
CFTC_COT_DEFAULT_MAX_AGE_DAYS = 45

DEFAULT_GDELT_QUERIES = [
    '("artificial intelligence" OR "AI data center" OR GPU) (capex OR power OR earnings OR guidance)',
    '("semiconductor" OR "export controls" OR "NVIDIA" OR "TSMC" OR Broadcom) (AI OR datacenter)',
    '("data center" OR "electricity grid" OR "natural gas") (hyperscaler OR AI)',
]

DEFAULT_CFTC_MARKETS = [
    "NASDAQ-100",
    "S&P 500",
    "E-MINI NASDAQ",
    "NATURAL GAS",
    "WTI",
]

SIGNAL_ALIASES = {
    "BE": ["BLOOM ENERGY"],
    "CRWV": ["COREWEAVE"],
    "INTC": ["INTEL"],
    "LITE": ["LUMENTUM"],
    "CORZ": ["CORE SCIENTIFIC"],
    "IREN": ["IREN"],
    "APLD": ["APPLIED DIGITAL"],
    "CIFR": ["CIPHER MINING"],
    "EQT": ["EQT"],
    "COHR": ["COHERENT"],
    "NVDA": ["NVIDIA"],
    "AVGO": ["BROADCOM"],
    "AMD": ["ADVANCED MICRO DEVICES"],
    "TSM": ["TAIWAN SEMICONDUCTOR", "TSMC"],
    "ASML": ["ASML"],
    "MU": ["MICRON"],
    "MRVL": ["MARVELL"],
    "VRT": ["VERTIV"],
    "CEG": ["CONSTELLATION ENERGY"],
    "TLN": ["TALEN"],
    "VST": ["VISTRA"],
    "GEV": ["GE VERNOVA"],
    "ORCL": ["ORACLE"],
    "MSFT": ["MICROSOFT"],
    "GOOGL": ["ALPHABET", "GOOGLE"],
    "GOOG": ["ALPHABET", "GOOGLE"],
    "META": ["META PLATFORMS"],
    "AMZN": ["AMAZON"],
    "AAPL": ["APPLE"],
    "ANET": ["ARISTA"],
    "ARM": ["ARM HOLDINGS"],
    "PLTR": ["PALANTIR"],
    "SNOW": ["SNOWFLAKE"],
    "DDOG": ["DATADOG"],
    "MDB": ["MONGODB"],
    "NOW": ["SERVICENOW"],
    "CRM": ["SALESFORCE"],
    "APP": ["APPLOVIN"],
    "CRWD": ["CROWDSTRIKE"],
    "SHOP": ["SHOPIFY"],
    "ETN": ["EATON"],
    "HOOD": ["ROBINHOOD"],
}

POSITIVE_NEWS_TERMS = {
    "contract",
    "guidance",
    "beat",
    "raises",
    "upgrade",
    "partnership",
    "capacity",
    "backlog",
    "approval",
    "launch",
}
NEGATIVE_NEWS_TERMS = {
    "probe",
    "investigation",
    "delay",
    "cuts",
    "downgrade",
    "miss",
    "shortfall",
    "lawsuit",
    "export control",
    "financing risk",
}


UrlOpen = Callable[..., Any]


def build_external_signal_snapshot(
    config: AppConfig,
    as_of: date,
    symbols: list[str],
    urlopen: UrlOpen | None = None,
) -> dict[str, Any]:
    settings = external_signal_settings(config)
    settings["_deadline_monotonic"] = time.monotonic() + runtime_budget_seconds(settings)
    watchlist = normalize_symbols(symbols)
    urlopen = urlopen or urllib.request.urlopen

    providers = [
        alpha_vantage_news_provider(settings, as_of, watchlist, urlopen),
        sec_company_provider(config, settings, as_of, watchlist, urlopen),
        eia_provider(settings, as_of, urlopen),
        finra_short_interest_provider(settings, as_of, watchlist, urlopen),
        cftc_cot_provider(settings, as_of, urlopen),
        gdelt_provider(settings, as_of, watchlist, urlopen),
    ]
    providers = [provider for provider in providers if provider.get("status") != "disabled"]
    raw_signals = [signal for provider in providers for signal in provider.get("signals", [])]
    signals = dedupe_signals(raw_signals)
    duplicate_signal_count = len(raw_signals) - len(signals)
    symbol_features = aggregate_symbol_features(watchlist, signals)
    global_features = aggregate_global_features(signals)
    status_counts = status_counter(providers)
    status = overall_status(status_counts, signals)
    ok_count = status_counts.get("ok", 0)
    return {
        "version": EXTERNAL_SIGNALS_VERSION,
        "as_of": as_of.isoformat(),
        "symbols": watchlist,
        "symbol_count": len(watchlist),
        "status": status,
        "summary": external_summary(status_counts, signals),
        "provider_count": len(providers),
        "provider_status_counts": status_counts,
        "provider_ok_count": ok_count,
        "provider_ok_ratio": round(ok_count / len(providers), 4) if providers else 0.0,
        "signal_count": len(signals),
        "duplicate_signal_count": duplicate_signal_count,
        "source_statuses": [
            {
                "source": row.get("source", ""),
                "label": row.get("label", ""),
                "status": row.get("status", "unknown"),
                "detail": row.get("detail", ""),
                "item_count": row.get("item_count", 0),
                "signal_count": row.get("signal_count", 0),
            }
            for row in providers
        ],
        "top_signals": sorted(signals, key=lambda row: abs(float(row.get("score") or 0)), reverse=True)[:40],
        "by_symbol": symbol_features,
        "global": global_features,
    }


def alpha_vantage_news_provider(
    settings: dict[str, Any],
    as_of: date,
    symbols: list[str],
    urlopen: UrlOpen,
) -> dict[str, Any]:
    if not bool(settings.get("alpha_vantage_news_enabled", True)):
        return provider_status("alpha_vantage_news", "Alpha Vantage news sentiment", "disabled", "Disabled in config.")
    if budget_exhausted(settings):
        return provider_status("alpha_vantage_news", "Alpha Vantage news sentiment", "limited", "Skipped because external source runtime budget was exhausted.")
    api_key_env = str(settings.get("alpha_vantage_api_key_env") or "ALPHA_VANTAGE_API_KEY")
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        return provider_status(
            "alpha_vantage_news",
            "Alpha Vantage news sentiment",
            "limited",
            f"Optional API key env {api_key_env} is not set.",
        )
    max_symbols = clamp_int(settings.get("alpha_vantage_news_max_symbols"), 1, 50, 35)
    limit = clamp_int(settings.get("alpha_vantage_news_limit"), 10, 200, 50)
    timespan_days = clamp_int(settings.get("alpha_vantage_news_timespan_days"), 1, 14, 3)
    timeout = provider_timeout(settings)
    tickers = ",".join(symbols[:max_symbols])
    if not tickers:
        return provider_status("alpha_vantage_news", "Alpha Vantage news sentiment", "limited", "No symbols configured.")
    params = urllib.parse.urlencode(
        {
            "function": "NEWS_SENTIMENT",
            "tickers": tickers,
            "sort": "LATEST",
            "limit": limit,
            "apikey": api_key,
            **alpha_vantage_news_window(as_of, timespan_days),
        }
    )
    try:
        payload = fetch_json(f"{ALPHA_VANTAGE_URL}?{params}", urlopen=urlopen, timeout=timeout)
    except Exception as exc:
        return provider_status(
            "alpha_vantage_news",
            "Alpha Vantage news sentiment",
            "limited",
            f"Fetch failed: {short_error(exc)}",
        )
    if payload.get("Information") or payload.get("Note") or payload.get("Error Message"):
        return provider_status(
            "alpha_vantage_news",
            "Alpha Vantage news sentiment",
            "limited",
            str(payload.get("Information") or payload.get("Note") or payload.get("Error Message"))[:180],
        )
    items, signals = parse_alpha_vantage_news(payload, set(symbols), as_of)
    status = "ok" if signals else "limited"
    return provider_status(
        "alpha_vantage_news",
        "Alpha Vantage news sentiment",
        status,
        f"{len(items)} feed items parsed; {len(signals)} symbol sentiment signals.",
        items=items[:30],
        signals=signals,
    )


def parse_alpha_vantage_news(
    payload: dict[str, Any],
    allowed_symbols: set[str],
    as_of: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    for article in payload.get("feed") or []:
        title = clean_text(article.get("title", ""))
        url = str(article.get("url") or "")
        source = clean_text(article.get("source", "Alpha Vantage"))
        published = parse_datetime(str(article.get("time_published") or "").replace("T", "")) or parse_datetime(article.get("time_published"))
        published_at = published.isoformat() if published else ""
        article_sentiment = safe_float(article.get("overall_sentiment_score"))
        topics = [clean_text(row.get("topic", "")) for row in article.get("topics") or [] if row.get("topic")]
        matched_symbols: list[str] = []
        for ticker_row in article.get("ticker_sentiment") or []:
            symbol = normalize_symbol(str(ticker_row.get("ticker") or "").split(":")[-1])
            if not symbol or symbol not in allowed_symbols:
                continue
            matched_symbols.append(symbol)
            relevance = safe_float(ticker_row.get("relevance_score"), 0.0)
            sentiment = safe_float(ticker_row.get("ticker_sentiment_score"), article_sentiment)
            score = clamp_score(sentiment * max(0.25, relevance) * 14.0)
            signals.append(
                signal_payload(
                    source="alpha_vantage_news",
                    as_of=as_of,
                    signal_type="news_sentiment_positive" if score >= 0 else "news_sentiment_negative",
                    score=score,
                    label=title or f"{symbol} news sentiment",
                    symbol=symbol,
                    confidence=min(0.9, 0.45 + relevance * 0.45),
                    detail=f"{source}; sentiment {sentiment:.2f}, relevance {relevance:.2f}",
                    event_date=published.date().isoformat() if published else as_of.isoformat(),
                    url=url,
                    family="news_sentiment",
                )
            )
        if title:
            items.append(
                {
                    "source": source,
                    "title": title,
                    "url": url,
                    "published_at": published_at,
                    "sentiment_score": round(article_sentiment, 3),
                    "matched_symbols": sorted(set(matched_symbols)),
                    "topics": topics[:5],
                }
            )
    return items, signals


def alpha_vantage_news_window(as_of: date, timespan_days: int) -> dict[str, str]:
    days = clamp_int(timespan_days, 1, 14, 3)
    start = as_of - timedelta(days=days - 1)
    return {
        "time_from": f"{start.strftime('%Y%m%d')}T0000",
        "time_to": f"{as_of.strftime('%Y%m%d')}T2359",
    }


def gdelt_provider(
    settings: dict[str, Any],
    as_of: date,
    symbols: list[str],
    urlopen: UrlOpen,
) -> dict[str, Any]:
    if not bool(settings.get("gdelt_enabled", True)):
        return provider_status("gdelt_global_news", "GDELT global news/events", "disabled", "Disabled in config.")
    if budget_exhausted(settings):
        return provider_status("gdelt_global_news", "GDELT global news/events", "limited", "Skipped because external source runtime budget was exhausted.")
    queries = [str(q) for q in settings.get("gdelt_queries") or DEFAULT_GDELT_QUERIES]
    max_records = clamp_int(settings.get("gdelt_max_records"), 5, 75, 25)
    timespan_days = clamp_int(settings.get("gdelt_timespan_days"), 1, 14, 3)
    timeout = min(provider_timeout(settings), clamp_int(settings.get("gdelt_timeout_seconds"), 1, 15, 4))
    max_failures = clamp_int(settings.get("gdelt_max_failures"), 1, 5, 2)
    query_windows = queries[:5]
    all_items: list[dict[str, Any]] = []
    all_signals: list[dict[str, Any]] = []
    failures = 0
    failure_skipped = 0
    runtime_skipped = 0
    failure_details: list[str] = []
    for index, query in enumerate(query_windows):
        if budget_exhausted(settings):
            runtime_skipped = len(query_windows) - index
            break
        params = urllib.parse.urlencode(
            {
                "query": query,
                "mode": "ArtList",
                "format": "json",
                "maxrecords": max_records,
                **gdelt_as_of_window(as_of, timespan_days),
            }
        )
        try:
            payload = fetch_json(f"{GDELT_DOC_URL}?{params}", urlopen=urlopen, timeout=timeout)
        except Exception as exc:
            failures += 1
            if len(failure_details) < 2:
                failure_details.append(f"{type(exc).__name__}: {short_error(exc)}")
            if failures >= max_failures:
                failure_skipped = len(query_windows) - index - 1
                break
            continue
        items, signals = parse_gdelt_articles(payload, query, set(symbols), as_of)
        all_items.extend(items)
        all_signals.extend(signals)
    status = "ok" if all_items else "limited"
    window = gdelt_as_of_window(as_of, timespan_days)
    detail = (
        f"{len(all_items)} global articles parsed across {len(query_windows)} configured queries "
        f"for {window['startdatetime']}..{window['enddatetime']}."
    )
    if failures:
        summary = f" ({'; '.join(failure_details)})" if failure_details else ""
        detail += f" {failures} query windows failed{summary}."
    if failure_skipped:
        detail += f" {failure_skipped} remaining query windows skipped after failure cap."
    if runtime_skipped:
        detail += f" {runtime_skipped} remaining query windows skipped by runtime budget."
    return provider_status(
        "gdelt_global_news",
        "GDELT global news/events",
        status,
        detail,
        items=all_items[:50],
        signals=dedupe_signals(all_signals),
    )


def parse_gdelt_articles(
    payload: dict[str, Any],
    query: str,
    allowed_symbols: set[str],
    as_of: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    articles = payload.get("articles") or payload.get("items") or []
    items: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    for article in articles:
        title = clean_text(article.get("title") or article.get("seendate") or "")
        url = str(article.get("url") or "")
        source = clean_text(article.get("domain") or article.get("sourceCountry") or "GDELT")
        published_at = str(article.get("seendate") or article.get("publishedAt") or "")
        text = f"{title} {source}".upper()
        matched = symbols_in_text(text, allowed_symbols)
        score = gdelt_score(title)
        if matched:
            for symbol in matched:
                signals.append(
                    signal_payload(
                        source="gdelt_global_news",
                        as_of=as_of,
                        signal_type="global_news_event",
                        score=score,
                        label=title or f"{symbol} global news event",
                        symbol=symbol,
                        confidence=0.45,
                        detail=f"GDELT query: {query[:80]}",
                        event_date=date_from_gdelt(published_at) or as_of.isoformat(),
                        url=url,
                        family="global_news",
                    )
                )
        else:
            signals.append(
                signal_payload(
                    source="gdelt_global_news",
                    as_of=as_of,
                    signal_type="global_ai_event",
                    score=score,
                    label=title or "Global AI market event",
                    symbol="",
                    confidence=0.35,
                    detail=f"GDELT query: {query[:80]}",
                    event_date=date_from_gdelt(published_at) or as_of.isoformat(),
                    url=url,
                    family="global_news",
                )
            )
        if title:
            items.append(
                {
                    "source": source,
                    "title": title,
                    "url": url,
                    "published_at": published_at,
                    "matched_symbols": matched,
                    "query": query,
                    "event_score": score,
                }
            )
    return items, signals


def gdelt_as_of_window(as_of: date, timespan_days: int) -> dict[str, str]:
    days = clamp_int(timespan_days, 1, 14, 3)
    start = as_of - timedelta(days=days - 1)
    return {
        "startdatetime": f"{start.strftime('%Y%m%d')}000000",
        "enddatetime": f"{as_of.strftime('%Y%m%d')}235959",
    }


def sec_company_provider(
    config: AppConfig,
    settings: dict[str, Any],
    as_of: date,
    symbols: list[str],
    urlopen: UrlOpen,
) -> dict[str, Any]:
    if not bool(settings.get("sec_company_enabled", True)):
        return provider_status("sec_company_data", "SEC company facts and Form 4", "disabled", "Disabled in config.")
    if budget_exhausted(settings):
        return provider_status("sec_company_data", "SEC company facts and Form 4", "limited", "Skipped because external source runtime budget was exhausted.")
    max_symbols = clamp_int(settings.get("sec_company_max_symbols"), 1, 50, 18)
    configured = {
        normalize_symbol(row.get("symbol")): str(row.get("cik") or "").strip()
        for row in getattr(config, "earnings_sec_companies", [])
        if row.get("symbol") and row.get("cik")
    }
    cik_map = dict(configured)
    missing = [symbol for symbol in symbols[:max_symbols] if symbol not in cik_map]
    if missing and bool(settings.get("sec_auto_cik_lookup", True)):
        try:
            cik_map.update(fetch_sec_ticker_cik_map(urlopen, timeout=provider_timeout(settings)))
        except Exception:
            pass
    items: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    attempted = 0
    failures = 0
    skipped = 0
    tail_reserve = tail_provider_reserve_seconds(settings)
    for symbol in symbols[:max_symbols]:
        if budget_exhausted(settings, reserve_seconds=tail_reserve):
            skipped = len(symbols[:max_symbols]) - attempted
            break
        cik = str(cik_map.get(symbol) or "").strip()
        if not cik:
            continue
        attempted += 1
        try:
            company_item, company_signals = fetch_sec_company_signals(symbol, cik, as_of, urlopen, timeout=provider_timeout(settings))
        except Exception:
            failures += 1
            continue
        items.append(company_item)
        signals.extend(company_signals)
    if attempted == 0:
        if skipped:
            return provider_status(
                "sec_company_data",
                "SEC company facts and Form 4",
                "limited",
                f"Skipped {skipped} companies to preserve tail-provider runtime budget.",
            )
        return provider_status(
            "sec_company_data",
            "SEC company facts and Form 4",
            "limited",
            "No CIK mapping available for the configured watchlist window.",
        )
    status = "ok" if signals else "limited"
    detail = f"{len(items)}/{attempted} SEC company records parsed."
    if failures:
        detail += f" {failures} company fetches failed."
    if skipped:
        detail += f" {skipped} companies skipped by runtime budget."
    return provider_status(
        "sec_company_data",
        "SEC company facts and Form 4",
        status,
        detail,
        items=items,
        signals=signals,
    )


def fetch_sec_ticker_cik_map(urlopen: UrlOpen, timeout: int = 20) -> dict[str, str]:
    payload = fetch_json(
        SEC_TICKERS_URL,
        headers={"User-Agent": SEC_USER_AGENT},
        urlopen=urlopen,
        timeout=timeout,
    )
    mapping: dict[str, str] = {}
    rows = payload.values() if isinstance(payload, dict) else []
    for row in rows:
        symbol = normalize_symbol(row.get("ticker"))
        cik = str(row.get("cik_str") or "").strip()
        if symbol and cik:
            mapping[symbol] = cik.zfill(10)
    return mapping


def fetch_sec_company_signals(
    symbol: str,
    cik: str,
    as_of: date,
    urlopen: UrlOpen,
    timeout: int = 20,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cik_padded = str(cik).strip().lstrip("0").zfill(10)
    submissions = fetch_json(
        SEC_SUBMISSIONS_URL.format(cik=cik_padded),
        headers={"User-Agent": SEC_USER_AGENT},
        urlopen=urlopen,
        timeout=timeout,
    )
    facts = fetch_json(
        SEC_COMPANY_FACTS_URL.format(cik=cik_padded),
        headers={"User-Agent": SEC_USER_AGENT},
        urlopen=urlopen,
        timeout=timeout,
    )
    recent = submissions.get("filings", {}).get("recent", {})
    latest_result = latest_recent_filing(recent, {"10-Q", "10-K", "8-K"}, as_of=as_of)
    form4_count = count_recent_forms(recent, "4", as_of - timedelta(days=45), as_of=as_of)
    revenue_trend = latest_revenue_trend(facts, as_of=as_of)
    net_income_trend = latest_fact_trend(facts, ["NetIncomeLoss"], as_of=as_of)
    signals: list[dict[str, Any]] = []
    if latest_result:
        filed = parse_date(latest_result.get("filingDate")) or as_of
        signals.append(
            signal_payload(
                source="sec_company_data",
                as_of=as_of,
                signal_type="sec_recent_result_filing",
                score=2.0,
                label=f"{symbol} latest {latest_result.get('form', 'SEC')} filing",
                symbol=symbol,
                confidence=0.85,
                detail=f"Filed {filed.isoformat()} via SEC submissions.",
                event_date=filed.isoformat(),
                url="https://www.sec.gov/edgar/browse/?CIK=" + cik_padded,
                family="sec_filings",
            )
        )
    if form4_count:
        signals.append(
            signal_payload(
                source="sec_company_data",
                as_of=as_of,
                signal_type="sec_form4_activity",
                score=min(5.0, form4_count * 0.8),
                label=f"{symbol} Form 4 activity",
                symbol=symbol,
                confidence=0.75,
                detail=f"{form4_count} Form 4 filings in the trailing 45 days; direction requires filing-level review.",
                event_date=as_of.isoformat(),
                url="https://www.sec.gov/edgar/browse/?CIK=" + cik_padded,
                family="insider_activity",
            )
        )
    if revenue_trend.get("yoy_pct") is not None:
        yoy = float(revenue_trend["yoy_pct"])
        signals.append(
            signal_payload(
                source="sec_company_data",
                as_of=as_of,
                signal_type="sec_fundamental_trend",
                score=clamp_score(yoy / 4.0),
                label=f"{symbol} SEC revenue trend",
                symbol=symbol,
                confidence=0.7,
                detail=f"Latest SEC revenue trend {yoy:.1f}% over comparable period.",
                event_date=str(revenue_trend.get("filed_at") or as_of.isoformat()),
                url="https://www.sec.gov/edgar/browse/?CIK=" + cik_padded,
                family="fundamentals",
            )
        )
    item = {
        "symbol": symbol,
        "cik": cik_padded,
        "latest_result_form": latest_result.get("form") if latest_result else "",
        "latest_result_filed_at": latest_result.get("filingDate") if latest_result else "",
        "form4_trailing_45d": form4_count,
        "revenue_yoy_pct": revenue_trend.get("yoy_pct"),
        "net_income_yoy_pct": net_income_trend.get("yoy_pct"),
    }
    return item, signals


def eia_provider(settings: dict[str, Any], as_of: date, urlopen: UrlOpen) -> dict[str, Any]:
    if not bool(settings.get("eia_enabled", True)):
        return provider_status("eia_energy_power", "EIA energy and power", "disabled", "Disabled in config.")
    if budget_exhausted(settings):
        return provider_status("eia_energy_power", "EIA energy and power", "limited", "Skipped because external source runtime budget was exhausted.")
    api_key_env = str(settings.get("eia_api_key_env") or "EIA_API_KEY")
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        return provider_status(
            "eia_energy_power",
            "EIA energy and power",
            "limited",
            f"Optional API key env {api_key_env} is not set; FRED energy proxies remain active.",
        )
    url = str(settings.get("eia_power_url") or EIA_POWER_URL)
    length = clamp_int(settings.get("eia_length"), 12, 5000, 120)
    timeout = provider_timeout(settings)
    params = urllib.parse.urlencode(
        {
            "api_key": api_key,
            "frequency": str(settings.get("eia_frequency") or "monthly"),
            "data[0]": str(settings.get("eia_data_field") or "generation"),
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "offset": 0,
            "length": length,
        }
    )
    try:
        payload = fetch_json(f"{url}?{params}", urlopen=urlopen, timeout=timeout)
    except Exception as exc:
        return provider_status(
            "eia_energy_power",
            "EIA energy and power",
            "limited",
            f"Fetch failed: {short_error(exc)}",
        )
    rows = payload.get("response", {}).get("data") or payload.get("data") or []
    signals = parse_eia_signals(rows, as_of)
    status = "ok" if rows else "limited"
    return provider_status(
        "eia_energy_power",
        "EIA energy and power",
        status,
        f"{len(rows)} EIA rows parsed from the configured route.",
        items=public_sample_rows(rows, 20),
        signals=signals,
    )


def parse_eia_signals(rows: list[dict[str, Any]], as_of: date) -> list[dict[str, Any]]:
    if not rows:
        return []
    rows = [
        row for row in rows
        if not parse_period_date(row.get("period")) or parse_period_date(row.get("period")) <= as_of
    ]
    if not rows:
        return []
    values = []
    for row in rows[:60]:
        value = first_numeric(row, ["generation", "value", "sales", "demand", "customers", "price"])
        if value is not None:
            values.append(value)
    if len(values) < 3:
        return []
    latest = values[0]
    baseline = mean(values[1:min(len(values), 13)])
    score = clamp_score((latest - baseline) / max(abs(baseline), 1.0) * 100.0 / 2.5)
    period = str(rows[0].get("period") or as_of.isoformat())
    return [
        signal_payload(
            source="eia_energy_power",
            as_of=as_of,
            signal_type="eia_power_demand_pressure",
            score=score,
            label="EIA power/energy demand pressure",
            symbol="",
            confidence=0.65,
            detail=f"Latest configured EIA value is {((latest / max(abs(baseline), 1.0)) - 1) * 100:.1f}% vs recent baseline.",
            event_date=period,
            url="https://www.eia.gov/opendata/",
            family="energy_power",
        )
    ]


def finra_short_interest_provider(
    settings: dict[str, Any],
    as_of: date,
    symbols: list[str],
    urlopen: UrlOpen,
) -> dict[str, Any]:
    if not bool(settings.get("finra_short_interest_enabled", True)):
        return provider_status("finra_short_interest", "FINRA short interest", "disabled", "Disabled in config.")
    if budget_exhausted(settings):
        return provider_status("finra_short_interest", "FINRA short interest", "limited", "Skipped because external source runtime budget was exhausted.")
    url = str(settings.get("finra_short_interest_url") or FINRA_SHORT_INTEREST_URL)
    max_rows = clamp_int(settings.get("finra_max_rows"), 100, 10000, 5000)
    timeout = provider_timeout(settings)
    params = urllib.parse.urlencode({"limit": max_rows})
    headers = {"Accept": "application/json"}
    api_token = os.environ.get(str(settings.get("finra_api_token_env") or "FINRA_API_TOKEN"), "").strip()
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"
    try:
        payload = fetch_json(f"{url}?{params}", headers=headers, urlopen=urlopen, timeout=timeout)
    except Exception as exc:
        return provider_status(
            "finra_short_interest",
            "FINRA short interest",
            "limited",
            f"Fetch failed or endpoint needs credentials: {short_error(exc)}",
        )
    rows = payload if isinstance(payload, list) else payload.get("data") or []
    max_age_days = clamp_int(
        settings.get("finra_short_interest_max_age_days"),
        14,
        365,
        FINRA_SHORT_INTEREST_DEFAULT_MAX_AGE_DAYS,
    )
    items, signals = parse_finra_short_interest(rows, set(symbols), as_of, max_age_days=max_age_days)
    status = "ok" if signals else "limited"
    detail = f"{len(rows)} FINRA rows parsed; {len(signals)} current watchlist risk signals within {max_age_days} days."
    if rows and not signals:
        detail += " Stale or undated settlement rows were ignored."
    return provider_status(
        "finra_short_interest",
        "FINRA short interest",
        status,
        detail,
        items=items[:50],
        signals=signals,
    )


def parse_finra_short_interest(
    rows: list[dict[str, Any]],
    allowed_symbols: set[str],
    as_of: date,
    max_age_days: int = FINRA_SHORT_INTEREST_DEFAULT_MAX_AGE_DAYS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    max_age_days = clamp_int(max_age_days, 1, 3650, FINRA_SHORT_INTEREST_DEFAULT_MAX_AGE_DAYS)
    oldest_settlement = as_of - timedelta(days=max_age_days)
    by_symbol: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = normalize_symbol(
            row.get("symbolCode")
            or row.get("issueSymbolIdentifier")
            or row.get("symbol")
            or row.get("ticker")
        )
        if not symbol or symbol not in allowed_symbols:
            continue
        settlement = parse_date(
            row.get("settlementDate")
            or row.get("settlement_date")
            or row.get("recordDate")
            or row.get("date")
        )
        if settlement is None or settlement < oldest_settlement or settlement > as_of:
            continue
        current = by_symbol.get(symbol)
        current_settlement = current.get("_settlement") if current else None
        if current is None or settlement > (current_settlement if isinstance(current_settlement, date) else date.min):
            by_symbol[symbol] = dict(row, _symbol=symbol, _settlement=settlement)
    items: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    for symbol, row in by_symbol.items():
        days_to_cover = first_numeric(row, ["daysToCoverQuantity", "daysToCover", "days_to_cover"])
        pct_float = first_numeric(row, ["shortInterestPercentFloat", "shortPercentFloat", "short_interest_pct_float"])
        score = 0.0
        if pct_float is not None:
            score += min(10.0, pct_float / 4.0)
        if days_to_cover is not None:
            score += min(8.0, days_to_cover)
        score = clamp_score(-score)
        settlement = row.get("_settlement")
        signals.append(
            signal_payload(
                source="finra_short_interest",
                as_of=as_of,
                signal_type="short_interest_risk",
                score=score,
                label=f"{symbol} short-interest risk",
                symbol=symbol,
                confidence=0.65,
                detail="FINRA short-interest row normalized into risk score; public snapshot excludes raw position counts.",
                event_date=settlement.isoformat() if settlement else as_of.isoformat(),
                url="https://www.finra.org/finra-data/browse-catalog/equity-short-interest",
                family="short_interest",
            )
        )
        items.append(
            {
                "symbol": symbol,
                "settlement_date": settlement.isoformat() if settlement else "",
                "days_to_cover": days_to_cover,
                "short_interest_pct_float": pct_float,
            }
        )
    return items, signals


def cftc_cot_provider(settings: dict[str, Any], as_of: date, urlopen: UrlOpen) -> dict[str, Any]:
    if not bool(settings.get("cftc_cot_enabled", True)):
        return provider_status("cftc_cot", "CFTC Commitments of Traders", "disabled", "Disabled in config.")
    if budget_exhausted(settings):
        return provider_status("cftc_cot", "CFTC Commitments of Traders", "limited", "Skipped because external source runtime budget was exhausted.")
    url = str(settings.get("cftc_cot_url") or CFTC_LEGACY_COT_URL)
    markets = [str(item).upper() for item in settings.get("cftc_markets") or DEFAULT_CFTC_MARKETS]
    max_rows = clamp_int(settings.get("cftc_max_rows"), 100, 10000, 5000)
    timeout = provider_timeout(settings)
    where = " OR ".join(
        [f"upper(market_and_exchange_names) like '%{market.replace(chr(39), chr(39) + chr(39))}%'" for market in markets]
    )
    params = {
        "$limit": str(max_rows),
        "$order": "report_date_as_yyyy_mm_dd DESC",
    }
    if where:
        params["$where"] = where
    token = os.environ.get(str(settings.get("cftc_app_token_env") or "CFTC_APP_TOKEN"), "").strip()
    headers = {"Accept": "application/json"}
    if token:
        headers["X-App-Token"] = token
    try:
        rows = fetch_json(f"{url}?{urllib.parse.urlencode(params)}", headers=headers, urlopen=urlopen, timeout=timeout)
    except Exception as exc:
        return provider_status(
            "cftc_cot",
            "CFTC Commitments of Traders",
            "limited",
            f"Fetch failed: {short_error(exc)}",
        )
    if not isinstance(rows, list):
        rows = []
    max_age_days = clamp_int(settings.get("cftc_cot_max_age_days"), 7, 365, CFTC_COT_DEFAULT_MAX_AGE_DAYS)
    items, signals = parse_cftc_cot(rows, as_of, max_age_days=max_age_days)
    status = "ok" if signals else "limited"
    return provider_status(
        "cftc_cot",
        "CFTC Commitments of Traders",
        status,
        f"{len(rows)} CFTC COT rows parsed; {len(signals)} macro positioning signals within {max_age_days} days.",
        items=items,
        signals=signals,
    )


def parse_cftc_cot(
    rows: list[dict[str, Any]],
    as_of: date,
    max_age_days: int = CFTC_COT_DEFAULT_MAX_AGE_DAYS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    oldest_report = as_of - timedelta(days=clamp_int(max_age_days, 1, 3650, CFTC_COT_DEFAULT_MAX_AGE_DAYS))
    seen_markets: set[str] = set()
    items: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    for row in rows:
        market = clean_text(row.get("market_and_exchange_names") or row.get("market") or "")
        if not market or market in seen_markets:
            continue
        report_date = parse_date(row.get("report_date_as_yyyy_mm_dd") or row.get("report_date"))
        if report_date is None or report_date > as_of or report_date < oldest_report:
            continue
        seen_markets.add(market)
        open_interest = first_numeric(row, ["open_interest_all", "open_interest"])
        noncomm_long = first_numeric(row, ["noncomm_positions_long_all", "noncommercial_long_all"])
        noncomm_short = first_numeric(row, ["noncomm_positions_short_all", "noncommercial_short_all"])
        if open_interest is None or open_interest <= 0 or noncomm_long is None or noncomm_short is None:
            continue
        net_ratio = (noncomm_long - noncomm_short) / open_interest
        score = clamp_score(net_ratio * 24.0)
        label = cftc_label_for_market(market)
        signals.append(
            signal_payload(
                source="cftc_cot",
                as_of=as_of,
                signal_type="futures_positioning",
                score=score,
                label=f"{label} futures positioning",
                symbol="",
                confidence=0.6,
                detail=f"Non-commercial net positioning ratio {net_ratio:.2f} in {market}.",
                event_date=report_date.isoformat() if report_date else as_of.isoformat(),
                url="https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm",
                family="futures_positioning",
            )
        )
        items.append(
            {
                "market": market,
                "label": label,
                "report_date": report_date.isoformat() if report_date else "",
                "noncommercial_net_ratio": round(net_ratio, 4),
                "score": score,
            }
        )
    return items[:20], signals


def latest_recent_filing(
    recent: dict[str, Any],
    forms_allowed: set[str],
    as_of: date | None = None,
) -> dict[str, Any] | None:
    forms = recent.get("form") or []
    filing_dates = recent.get("filingDate") or []
    accessions = recent.get("accessionNumber") or []
    primary_docs = recent.get("primaryDocument") or []
    for form, filing_date, accession, primary_doc in zip(forms, filing_dates, accessions, primary_docs):
        parsed = parse_date(filing_date)
        if as_of and parsed and parsed > as_of:
            continue
        if form in forms_allowed:
            return {
                "form": form,
                "filingDate": filing_date,
                "accessionNumber": accession,
                "primaryDocument": primary_doc,
            }
    return None


def count_recent_forms(recent: dict[str, Any], target_form: str, since: date, as_of: date | None = None) -> int:
    count = 0
    for form, filing_date in zip(recent.get("form") or [], recent.get("filingDate") or []):
        parsed = parse_date(filing_date)
        if form == target_form and parsed and parsed >= since and (as_of is None or parsed <= as_of):
            count += 1
    return count


def latest_revenue_trend(facts: dict[str, Any], as_of: date | None = None) -> dict[str, Any]:
    return latest_fact_trend(
        facts,
        [
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet",
        ],
        as_of=as_of,
    )


def latest_fact_trend(facts: dict[str, Any], tags: list[str], as_of: date | None = None) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    for tag in tags:
        units = (us_gaap.get(tag) or {}).get("units", {})
        for unit_rows in units.values():
            for row in unit_rows:
                value = safe_float(row.get("val"))
                filed = parse_date(row.get("filed"))
                end = parse_date(row.get("end"))
                form = str(row.get("form") or "")
                fy = row.get("fy")
                fp = row.get("fp")
                if value is None or not filed or not end or form not in {"10-K", "10-Q"}:
                    continue
                if as_of and filed > as_of:
                    continue
                rows.append({"tag": tag, "value": value, "filed_at": filed.isoformat(), "end": end, "form": form, "fy": fy, "fp": fp})
    rows.sort(key=lambda row: (row["end"], row["filed_at"]), reverse=True)
    if not rows:
        return {}
    latest = rows[0]
    comparable = None
    for row in rows[1:]:
        if row.get("fp") == latest.get("fp") and row.get("form") == latest.get("form"):
            comparable = row
            break
    if comparable is None and len(rows) > 1:
        comparable = rows[1]
    yoy = None
    if comparable and comparable.get("value"):
        base = float(comparable["value"])
        if base:
            yoy = (float(latest["value"]) - base) / abs(base) * 100.0
    return {
        "tag": latest["tag"],
        "filed_at": latest["filed_at"],
        "period_end": latest["end"].isoformat(),
        "yoy_pct": round(yoy, 2) if yoy is not None else None,
    }


def aggregate_symbol_features(symbols: list[str], signals: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    rows = {
        symbol: {
            "symbol": symbol,
            "external_signal_score": 0.0,
            "alpha_news_sentiment": 0.0,
            "sec_fundamental_score": 0.0,
            "sec_form4_activity_score": 0.0,
            "gdelt_event_score": 0.0,
            "short_interest_risk_score": 0.0,
            "source_count": 0,
            "signal_count": 0,
            "sources": [],
            "top_signals": [],
        }
        for symbol in symbols
    }
    for signal in signals:
        symbol = normalize_symbol(signal.get("symbol"))
        if not symbol or symbol not in rows:
            continue
        row = rows[symbol]
        score = float(signal.get("score") or 0)
        row["external_signal_score"] += score
        row["signal_count"] += 1
        source = str(signal.get("source") or "")
        if source and source not in row["sources"]:
            row["sources"].append(source)
        if len(row["top_signals"]) < 5:
            row["top_signals"].append(signal)
        if source == "alpha_vantage_news":
            row["alpha_news_sentiment"] += score
        elif source == "sec_company_data":
            if signal.get("signal_type") == "sec_form4_activity":
                row["sec_form4_activity_score"] += score
            elif signal.get("signal_type") == "sec_fundamental_trend":
                row["sec_fundamental_score"] += score
        elif source == "gdelt_global_news":
            row["gdelt_event_score"] += score
        elif source == "finra_short_interest":
            row["short_interest_risk_score"] += score
    for row in rows.values():
        row["source_count"] = len(row["sources"])
        for key in (
            "external_signal_score",
            "alpha_news_sentiment",
            "sec_fundamental_score",
            "sec_form4_activity_score",
            "gdelt_event_score",
            "short_interest_risk_score",
        ):
            row[key] = round(clamp_score(float(row[key])), 2)
    return rows


def aggregate_global_features(signals: list[dict[str, Any]]) -> dict[str, Any]:
    global_signals = [row for row in signals if not row.get("symbol")]
    cftc = [float(row.get("score") or 0) for row in global_signals if row.get("source") == "cftc_cot"]
    eia = [float(row.get("score") or 0) for row in global_signals if row.get("source") == "eia_energy_power"]
    gdelt = [float(row.get("score") or 0) for row in global_signals if row.get("source") == "gdelt_global_news"]
    return {
        "global_signal_score": round(clamp_score(sum(float(row.get("score") or 0) for row in global_signals)), 2),
        "gdelt_global_event_score": round(clamp_score(sum(gdelt)), 2),
        "eia_power_pressure_score": round(clamp_score(mean(eia) if eia else 0.0), 2),
        "cftc_positioning_score": round(clamp_score(mean(cftc) if cftc else 0.0), 2),
        "signal_count": len(global_signals),
        "top_signals": sorted(global_signals, key=lambda row: abs(float(row.get("score") or 0)), reverse=True)[:10],
    }


def signal_payload(
    source: str,
    as_of: date,
    signal_type: str,
    score: float,
    label: str,
    symbol: str,
    confidence: float,
    detail: str,
    event_date: str,
    url: str,
    family: str,
) -> dict[str, Any]:
    symbol = normalize_symbol(symbol)
    return {
        "signal_id": stable_id([source, symbol, signal_type, label, event_date]),
        "source": source,
        "symbol": symbol,
        "scope": "symbol" if symbol else "global",
        "signal_type": signal_type,
        "signal_family": family,
        "score": round(clamp_score(score), 2),
        "confidence": round(max(0.0, min(1.0, confidence)), 3),
        "label": clean_text(label)[:240],
        "detail": clean_text(detail)[:300],
        "event_date": event_date,
        "last_checked_at": f"{as_of.isoformat()}T00:00:00Z",
        "url": url,
    }


def provider_status(
    source: str,
    label: str,
    status: str,
    detail: str,
    items: list[dict[str, Any]] | None = None,
    signals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    items = items or []
    signals = signals or []
    return {
        "source": source,
        "label": label,
        "status": status,
        "detail": detail,
        "item_count": len(items),
        "signal_count": len(signals),
        "items": items,
        "signals": signals,
    }


def status_counter(providers: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for provider in providers:
        status = str(provider.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def overall_status(status_counts: dict[str, int], signals: list[dict[str, Any]]) -> str:
    if not status_counts:
        return "missing"
    ok_count = status_counts.get("ok", 0)
    weak_count = sum(
        status_counts.get(status, 0)
        for status in ("limited", "missing", "failed", "error", "unknown")
    )
    if ok_count and not weak_count:
        return "ok"
    if ok_count or status_counts.get("limited") or signals:
        return "limited"
    return "missing"


def external_summary(status_counts: dict[str, int], signals: list[dict[str, Any]]) -> str:
    return (
        f"{status_counts.get('ok', 0)} providers ok, {status_counts.get('limited', 0)} limited, "
        f"{len(signals)} normalized external signals."
    )


def external_provider_health_detail(external_signals: dict[str, Any], degraded_limit: int = 3) -> str:
    source_statuses = external_signals.get("source_statuses") or []
    provider_count = int(external_signals.get("provider_count") or len(source_statuses) or 0)
    if source_statuses:
        ok_count = sum(1 for row in source_statuses if row.get("status") == "ok")
        limited_count = sum(1 for row in source_statuses if row.get("status") == "limited")
    else:
        ok_count = int(external_signals.get("provider_ok_count") or 0)
        status_counts = external_signals.get("provider_status_counts") or {}
        limited_count = int(status_counts.get("limited", 0))
    detail = (
        f"{external_signals.get('signal_count', 0)} normalized signals; "
        f"{ok_count}/{provider_count} providers ok, {limited_count} limited."
    )
    degraded = degraded_provider_summary(source_statuses, limit=degraded_limit)
    return f"{detail} {degraded}" if degraded else detail


def degraded_provider_summary(source_statuses: list[dict[str, Any]], limit: int = 3) -> str:
    rows = [
        row for row in source_statuses
        if str((row or {}).get("status") or "unknown") not in {"ok", "disabled"}
    ]
    if not rows:
        return ""
    parts = [
        f"{row.get('label') or row.get('source') or 'provider'}: {short_provider_detail(row.get('detail') or row.get('status') or 'limited')}"
        for row in rows[:limit]
    ]
    suffix = f"; +{len(rows) - limit} more" if len(rows) > limit else ""
    return "Provider gaps: " + "; ".join(parts) + suffix + "."


def short_provider_detail(detail: Any, max_length: int = 90) -> str:
    text = " ".join(str(detail or "").split())
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."


def dedupe_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for signal in signals:
        key = str(signal.get("signal_id") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(signal)
    return out


def symbols_in_text(text: str, allowed_symbols: set[str]) -> list[str]:
    matches: list[str] = []
    for symbol in allowed_symbols:
        aliases = [symbol, *SIGNAL_ALIASES.get(symbol, [])]
        if any(alias_matches(text, alias) for alias in aliases):
            matches.append(symbol)
    return sorted(set(matches))


def alias_matches(text: str, alias: str) -> bool:
    alias = re.escape(alias.upper())
    if len(alias) <= 4:
        return re.search(rf"(?<![A-Z0-9]){alias}(?![A-Z0-9])", text) is not None
    return alias in text


def gdelt_score(title: str) -> float:
    lowered = title.lower()
    score = 1.5
    score += sum(1.8 for term in POSITIVE_NEWS_TERMS if term in lowered)
    score -= sum(2.3 for term in NEGATIVE_NEWS_TERMS if term in lowered)
    if "data center" in lowered or "datacenter" in lowered:
        score += 1.0
    if "power" in lowered or "grid" in lowered:
        score += 0.8
    return clamp_score(score)


def date_from_gdelt(value: Any) -> str:
    parsed = parse_date(value)
    if parsed:
        return parsed.isoformat()
    text = str(value or "")
    if len(text) >= 8 and text[:8].isdigit():
        parsed = parse_date(text[:8])
        return parsed.isoformat() if parsed else ""
    return ""


def parse_period_date(value: Any) -> date | None:
    parsed = parse_date(value)
    if parsed:
        return parsed
    text = str(value or "").strip()
    match = re.match(r"^(\d{4})-(\d{2})$", text)
    if match:
        return date(int(match.group(1)), int(match.group(2)), 1)
    match = re.match(r"^(\d{4})Q([1-4])$", text, flags=re.IGNORECASE)
    if match:
        month = (int(match.group(2)) - 1) * 3 + 1
        return date(int(match.group(1)), month, 1)
    return None


def cftc_label_for_market(market: str) -> str:
    upper = market.upper()
    if "NASDAQ" in upper:
        return "Nasdaq"
    if "S&P" in upper or "SP 500" in upper:
        return "S&P 500"
    if "NATURAL GAS" in upper:
        return "Natural gas"
    if "CRUDE" in upper or "WTI" in upper:
        return "Oil"
    return market.title()


def first_numeric(row: dict[str, Any], keys: list[str]) -> float | None:
    for key in keys:
        value = safe_float(row.get(key))
        if value is not None:
            return value
    return None


def public_sample_rows(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    sample = []
    for row in rows[:limit]:
        clean = {}
        for key, value in row.items():
            if isinstance(value, (str, int, float)) and key not in {"api_key", "key"}:
                clean[str(key)[:60]] = value
        sample.append(clean)
    return sample


def external_signal_settings(config: AppConfig) -> dict[str, Any]:
    try:
        settings = dict(config.external_signal_settings)
    except AttributeError:
        settings = dict(config.data.get("external_signals", {}))
    return settings


def provider_timeout(settings: dict[str, Any]) -> int:
    timeout = clamp_int(settings.get("timeout_seconds"), 2, 30, 8)
    deadline = settings.get("_deadline_monotonic")
    if not deadline:
        return timeout
    remaining = int(max(1.0, float(deadline) - time.monotonic()))
    return max(1, min(timeout, remaining))


def runtime_budget_seconds(settings: dict[str, Any]) -> int:
    return clamp_int(settings.get("max_runtime_seconds"), 10, 180, 30)


def tail_provider_reserve_seconds(settings: dict[str, Any]) -> int:
    return clamp_int(settings.get("tail_provider_reserve_seconds"), 0, 60, 8)


def budget_exhausted(settings: dict[str, Any], reserve_seconds: int | float = 0) -> bool:
    deadline = settings.get("_deadline_monotonic")
    return bool(deadline and time.monotonic() >= float(deadline) - float(reserve_seconds or 0))


def fetch_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int = 20,
    urlopen: UrlOpen | None = None,
) -> Any:
    text = fetch_text(url, headers=headers, timeout=timeout, urlopen=urlopen)
    return json.loads(text)


def fetch_text(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int = 20,
    urlopen: UrlOpen | None = None,
) -> str:
    opener = urlopen or urllib.request.urlopen
    request = urllib.request.Request(
        url,
        headers=headers or {"User-Agent": "AlloIQ/0.1 external-signals https://alloiq.com"},
    )
    with opener(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def normalize_symbols(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for symbol in symbols:
        normalized = normalize_symbol(symbol)
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return out


def normalize_symbol(value: Any) -> str:
    return re.sub(r"[^A-Z0-9.\-]", "", str(value or "").upper().strip())


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\x00", " ")).strip()


def safe_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def clamp_score(value: float) -> float:
    return max(-20.0, min(20.0, float(value)))


def clamp_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def short_error(exc: Exception) -> str:
    return re.sub(r"\s+", " ", str(exc))[:160]
