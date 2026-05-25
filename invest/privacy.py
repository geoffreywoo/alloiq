from __future__ import annotations

import re
from pathlib import Path


PUBLIC_ASSET_PATTERNS = [
    r'"account"',
    r'"account_id"',
    r'"quantity"',
    r'"shares"',
    r'"market_value"',
    r'"cost_basis"',
    r'"value_usd"',
    r'"estimated_notional"',
    r'"estimated_shares"',
    r'"external_id"',
    r'"transaction_id"',
    r'"raw_json"',
    r'"accounts"',
    r'"brokers"',
    r"IBKR",
    r"ibkr",
    r"Vanguard",
    r"vanguard",
    r"vanguard" + r"-sleeve",
    r"1443\.12",
    r"IBKR_FLEX",
    r"DATABASE_URL",
    r"postgres://",
    r"postgresql://",
    r"neon\.tech",
    r"U[0-9]{4,}",
]


def public_asset_paths(web_dir: Path = Path("web")) -> list[Path]:
    return [
        web_dir / "data" / "latest.json",
        web_dir / "data" / "reports.json",
        web_dir / "index.html",
        web_dir / "home.js",
        web_dir / "dashboard.html",
        web_dir / "app.js",
        web_dir / "portfolio.html",
        web_dir / "portfolio.js",
        web_dir / "research.html",
        web_dir / "research.js",
        web_dir / "optimizer.html",
        web_dir / "optimizer.js",
        web_dir / "backtest.html",
        web_dir / "backtest.js",
        web_dir / "ai-thesis-core.html",
        web_dir / "ai-thesis-core.js",
    ]


def scan_public_assets(web_dir: Path = Path("web"), patterns: list[str] | None = None) -> list[str]:
    patterns = patterns or PUBLIC_ASSET_PATTERNS
    failures: list[str] = []
    for target in public_asset_paths(web_dir):
        if not target.exists():
            failures.append(f"{target}: missing public asset")
            continue
        text = target.read_text(encoding="utf-8")
        for pattern in patterns:
            if re.search(pattern, text):
                failures.append(f"{target}: matched {pattern}")
    return failures


def assert_public_assets_safe(web_dir: Path = Path("web")) -> None:
    failures = scan_public_assets(web_dir)
    if failures:
        raise RuntimeError("\n".join(failures))
