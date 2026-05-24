from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from .models import NewsItem
from .util import parse_datetime


GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"


EVENT_RULES = [
    {
        "type": "financing_risk",
        "label": "Financing risk",
        "direction": "negative",
        "weight": 7,
        "patterns": [
            "debt financing",
            "convertible",
            "offering",
            "secondary",
            "dilution",
            "funding gap",
            "credit facility",
            "going concern",
        ],
    },
    {
        "type": "capex_signal",
        "label": "Capex signal",
        "direction": "positive",
        "weight": 6,
        "patterns": [
            "ai capex",
            "capital expenditure",
            "data center spending",
            "hyperscaler capex",
            "compute spending",
            "gpu cluster",
            "backlog",
        ],
    },
    {
        "type": "contract_win",
        "label": "Contract win",
        "direction": "positive",
        "weight": 7,
        "patterns": [
            "contract",
            "customer win",
            "supply agreement",
            "power purchase",
            "take-or-pay",
            "multi-year",
            "signed deal",
        ],
    },
    {
        "type": "earnings_revision",
        "label": "Earnings revision",
        "direction": "mixed",
        "weight": 6,
        "patterns": [
            "raises guidance",
            "cuts guidance",
            "earnings",
            "revenue forecast",
            "margin outlook",
            "estimates",
            "price target",
        ],
    },
    {
        "type": "supply_constraint",
        "label": "Supply constraint",
        "direction": "mixed",
        "weight": 5,
        "patterns": [
            "shortage",
            "supply constraint",
            "capacity constrained",
            "hbm supply",
            "lead times",
            "allocation",
            "bottleneck",
        ],
    },
    {
        "type": "regulatory_risk",
        "label": "Regulatory risk",
        "direction": "negative",
        "weight": 5,
        "patterns": [
            "antitrust",
            "doj",
            "ftc",
            "sec investigation",
            "export controls",
            "regulator",
            "lawsuit",
            "probe",
        ],
    },
    {
        "type": "valuation_reset",
        "label": "Valuation reset",
        "direction": "mixed",
        "weight": 4,
        "patterns": [
            "selloff",
            "pullback",
            "downgrade",
            "upgrade",
            "multiple compression",
            "valuation",
            "rally",
        ],
    },
    {
        "type": "technical_breakout",
        "label": "Technical breakout",
        "direction": "positive",
        "weight": 3,
        "patterns": [
            "breakout",
            "record high",
            "52-week high",
            "relative strength",
            "surges",
            "jumps",
            "rallies",
        ],
    },
    {
        "type": "crowding_warning",
        "label": "Crowding warning",
        "direction": "negative",
        "weight": 4,
        "patterns": [
            "crowded",
            "hedge fund favorite",
            "bubble",
            "mania",
            "short interest",
            "put options",
            "unwind",
        ],
    },
]

SOURCE_TIERS = [
    {
        "tier": "primary",
        "weight": 1.4,
        "patterns": [
            "sec.gov",
            "investor",
            "ir.",
            "press release",
            "business wire",
            "pr newswire",
            "globe newswire",
        ],
    },
    {
        "tier": "market_news",
        "weight": 1.15,
        "patterns": [
            "bloomberg",
            "reuters",
            "dow jones",
            "wall street journal",
            "cnbc",
            "benzinga",
            "marketwatch",
        ],
    },
    {
        "tier": "specialist",
        "weight": 1.25,
        "patterns": [
            "semianalysis",
            "the information",
            "datacenterdynamics",
            "utility dive",
            "rto insider",
            "stratechery",
            "eia",
            "fred",
            "treasury",
        ],
    },
]


def fetch_news(query: str, limit: int = 5) -> list[NewsItem]:
    params = urllib.parse.urlencode({"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    url = f"{GOOGLE_NEWS_RSS}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "autoinvestbot/0.1"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        root = ET.fromstring(resp.read())
    items: list[NewsItem] = []
    for item in root.findall("./channel/item")[:limit]:
        title = html.unescape(item.findtext("title", default="").strip())
        link = item.findtext("link", default="").strip()
        published = parse_datetime(item.findtext("pubDate", default=""))
        source = item.findtext("source", default="Google News").strip() or "Google News"
        summary = html.unescape(item.findtext("description", default="").strip())
        if title and link:
            items.append(NewsItem(source=source, title=title, url=link, published_at=published, summary=summary, query=query))
    return items


def fetch_many(queries: list[str], limit: int = 5) -> list[NewsItem]:
    seen: set[str] = set()
    all_items: list[NewsItem] = []
    for query in queries:
        try:
            items = fetch_news(query, limit=limit)
        except Exception:
            continue
        for item in items:
            if item.url in seen:
                continue
            seen.add(item.url)
            all_items.append(item)
    return all_items


def enrich_news_item(item: dict[str, Any]) -> dict[str, Any]:
    clean = dict(item)
    event = classify_news_event(clean)
    tier = classify_source_tier(clean)
    clean.update(event)
    clean.update(tier)
    clean["event_score"] = round(float(event["event_weight"]) * float(tier["source_weight"]), 2)
    return clean


def classify_news_event(item: dict[str, Any]) -> dict[str, Any]:
    text = normalize_text(f"{item.get('title', '')} {item.get('summary', '')} {item.get('query', '')}")
    for rule in EVENT_RULES:
        if any(pattern in text for pattern in rule["patterns"]):
            return {
                "event_type": rule["type"],
                "event_label": rule["label"],
                "event_direction": rule["direction"],
                "event_weight": rule["weight"],
            }
    return {
        "event_type": "general_news",
        "event_label": "General news",
        "event_direction": "mixed",
        "event_weight": 1,
    }


def classify_source_tier(item: dict[str, Any]) -> dict[str, Any]:
    text = normalize_text(f"{item.get('source', '')} {item.get('url', '')} {item.get('query', '')}")
    for tier in SOURCE_TIERS:
        if any(pattern in text for pattern in tier["patterns"]):
            return {"source_tier": tier["tier"], "source_weight": tier["weight"]}
    return {"source_tier": "general", "source_weight": 1.0}


def normalize_text(value: str) -> str:
    value = html.unescape(value).lower()
    return re.sub(r"\s+", " ", value)
