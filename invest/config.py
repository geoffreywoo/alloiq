from __future__ import annotations

import os
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path("config/invest.toml")
EXAMPLE_CONFIG_PATH = Path("config/invest.example.toml")
DOTENV_PATH = Path(".env")


@dataclass(frozen=True)
class AppConfig:
    path: Path
    data: dict[str, Any]

    @property
    def product_name(self) -> str:
        return str(self.data.get("product", {}).get("name", "AutoInvestBot"))

    @property
    def product_domain(self) -> str:
        return str(self.data.get("product", {}).get("domain", "autoinvestbot.com"))

    @property
    def db_path(self) -> Path:
        return Path(self.data.get("database", {}).get("path", "data/invest.db"))

    @property
    def reports_dir(self) -> Path:
        return Path(self.data.get("reports", {}).get("directory", "reports"))

    @property
    def vanguard_import_dir(self) -> Path:
        return Path(self.data.get("vanguard", {}).get("import_directory", "data/imports/vanguard"))

    @property
    def ibkr_enabled(self) -> bool:
        return bool(self.data.get("ibkr", {}).get("enabled", True))

    @property
    def vanguard_enabled(self) -> bool:
        return bool(self.data.get("vanguard", {}).get("enabled", False))

    @property
    def stale_vanguard_days(self) -> int:
        return int(self.data.get("vanguard", {}).get("stale_after_days", 7))

    @property
    def watchlist_symbols(self) -> list[str]:
        symbols = self.data.get("watchlist", {}).get("symbols", [])
        seen: set[str] = set()
        ordered: list[str] = []
        for symbol in symbols:
            normalized = str(symbol).upper().strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                ordered.append(normalized)
        return ordered

    @property
    def news_queries(self) -> list[str]:
        return [str(q) for q in self.data.get("news", {}).get("queries", [])]

    @property
    def macro_symbols(self) -> list[str]:
        return [str(s).upper() for s in self.data.get("macro", {}).get("symbols", []) if str(s).strip()]

    @property
    def risk_limits(self) -> dict[str, Any]:
        risk = dict(self.data.get("risk", {}))
        risk.setdefault("max_single_name_weight", 0.15)
        risk.setdefault("max_bucket_weight", 0.45)
        risk.setdefault("max_daily_turnover", 0.08)
        risk.setdefault("max_one_ticket_delta", 0.03)
        risk.setdefault("min_signal_family_count", 2)
        risk.setdefault("earnings_blackout_days", 2)
        risk.setdefault("earnings_risk_window_days", 7)
        risk.setdefault("no_add_symbols", [])
        risk.setdefault("watch_only_symbols", [])
        return risk

    @property
    def manual_earnings_events(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.data.get("earnings", {}).get("events", [])]

    @property
    def earnings_sec_companies(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.data.get("earnings", {}).get("sec_companies", [])]

    @property
    def manual_positions(self) -> list[dict[str, Any]]:
        positions = self.data.get("portfolio", {}).get("manual_positions", [])
        normalized: list[dict[str, Any]] = []
        for row in positions:
            symbol = str(row.get("symbol", "")).upper().strip()
            quantity = row.get("quantity", row.get("shares", 0))
            if not symbol or not quantity:
                continue
            normalized.append(
                {
                    "broker": str(row.get("broker", "manual")).strip() or "manual",
                    "account": str(row.get("account", "manual-sleeve")).strip() or "manual-sleeve",
                    "symbol": symbol,
                    "description": str(row.get("description", "")).strip(),
                    "quantity": quantity,
                    "currency": str(row.get("currency", "USD")).strip() or "USD",
                    "price": row.get("price", row.get("last_price", 0)),
                    "market_value": row.get("market_value", 0),
                }
            )
        return normalized

    @property
    def thesis_buckets(self) -> dict[str, dict[str, Any]]:
        buckets: dict[str, dict[str, Any]] = {}
        for row in self.data.get("thesis_buckets", []):
            key = str(row.get("key", "")).strip()
            if key:
                buckets[key] = row
        return buckets

    @property
    def symbol_to_bucket(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for key, bucket in self.thesis_buckets.items():
            for symbol in bucket.get("symbols", []):
                mapping[str(symbol).upper()] = key
        return mapping

    def manager(self, key: str) -> dict[str, Any]:
        for manager in self.data.get("managers", []):
            if manager.get("key") == key:
                return manager
        raise KeyError(f"Unknown manager '{key}'")

    @property
    def focus_manager_keys(self) -> list[str]:
        focus = self.data.get("focus_managers", {})
        keys = focus.get("keys", [])
        tier_keys = list(focus.get("tier1_keys", [])) + list(focus.get("tier2_keys", []))
        if tier_keys:
            keys = tier_keys
        seen: set[str] = set()
        ordered: list[str] = []
        for key in keys:
            normalized = str(key).strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                ordered.append(normalized)
        return ordered

    @property
    def focus_manager_tier1_keys(self) -> list[str]:
        keys = self.data.get("focus_managers", {}).get("tier1_keys", [])
        return unique_ordered_keys(keys)

    @property
    def focus_manager_tier2_keys(self) -> list[str]:
        focus = self.data.get("focus_managers", {})
        explicit = unique_ordered_keys(focus.get("tier2_keys", []))
        if explicit:
            return explicit
        tier1 = set(self.focus_manager_tier1_keys)
        return [key for key in self.focus_manager_keys if key not in tier1]

    @property
    def focus_manager_tier_map(self) -> dict[str, str]:
        tier_map = {key: "tier_1" for key in self.focus_manager_tier1_keys}
        for key in self.focus_manager_tier2_keys:
            tier_map.setdefault(key, "tier_2")
        return tier_map

    @property
    def primary_manager(self) -> dict[str, Any]:
        for manager in self.data.get("managers", []):
            if manager.get("primary"):
                return manager
        return self.data.get("managers", [])[0]

    @property
    def ibkr_token(self) -> str:
        ibkr = self.data.get("ibkr", {})
        return os.environ.get(str(ibkr.get("token_env", "IBKR_FLEX_TOKEN")), "")

    @property
    def ibkr_activity_query_id(self) -> str:
        ibkr = self.data.get("ibkr", {})
        return os.environ.get(str(ibkr.get("activity_query_id_env", "IBKR_FLEX_ACTIVITY_QUERY_ID")), "")

    @property
    def ibkr_raw_dir(self) -> Path:
        return Path(self.data.get("ibkr", {}).get("raw_directory", "data/raw/ibkr"))


def init_config(path: Path = DEFAULT_CONFIG_PATH) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return False
    if not EXAMPLE_CONFIG_PATH.exists():
        raise FileNotFoundError(EXAMPLE_CONFIG_PATH)
    shutil.copyfile(EXAMPLE_CONFIG_PATH, path)
    return True


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    if not path.exists():
        init_config(path)
    load_dotenv(DOTENV_PATH)
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return AppConfig(path=path, data=data)


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def unique_ordered_keys(keys: list[Any]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for key in keys:
        normalized = str(key).strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    return ordered
