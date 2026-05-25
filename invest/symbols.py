from __future__ import annotations

from typing import Any, Mapping


SYMBOL_PROXY_GROUPS = (("GOOG", "GOOGL"),)
SYMBOL_PROXY_MAP = {
    symbol: group
    for group in SYMBOL_PROXY_GROUPS
    for symbol in group
}


def normalize_symbol(symbol: Any) -> str:
    return str(symbol or "").upper().strip()


def equivalent_symbols(symbol: Any) -> list[str]:
    normalized = normalize_symbol(symbol)
    if not normalized:
        return []
    group = SYMBOL_PROXY_MAP.get(normalized)
    if not group:
        return [normalized]
    return [normalized, *[candidate for candidate in group if candidate != normalized]]


def symbol_proxy_key(symbol: Any) -> str:
    normalized = normalize_symbol(symbol)
    group = SYMBOL_PROXY_MAP.get(normalized)
    return group[0] if group else normalized


def expand_symbol_proxies(symbols: list[Any]) -> list[str]:
    seen: set[str] = set()
    expanded: list[str] = []
    for symbol in symbols:
        for candidate in equivalent_symbols(symbol):
            if candidate and candidate not in seen:
                seen.add(candidate)
                expanded.append(candidate)
    return expanded


def proxied_lookup(mapping: Mapping[str, Any], symbol: Any, default: Any = None) -> Any:
    for candidate in equivalent_symbols(symbol):
        if candidate in mapping:
            return mapping[candidate]
    return default


def proxy_index(rows: list[Any], symbol_field: str = "symbol") -> dict[str, Any]:
    index: dict[str, Any] = {}
    for row in rows:
        symbol = normalize_symbol(row.get(symbol_field) if isinstance(row, dict) else getattr(row, symbol_field, ""))
        if symbol:
            index.setdefault(symbol, row)
    for row in rows:
        symbol = normalize_symbol(row.get(symbol_field) if isinstance(row, dict) else getattr(row, symbol_field, ""))
        for candidate in equivalent_symbols(symbol):
            index.setdefault(candidate, row)
    return index


def sum_equivalent_values(mapping: Mapping[str, Any], symbol: Any, default: Any = 0) -> Any:
    found = False
    total: Any = None
    for candidate in equivalent_symbols(symbol):
        if candidate not in mapping:
            continue
        value = mapping[candidate]
        total = value if total is None else total + value
        found = True
    return total if found else default
