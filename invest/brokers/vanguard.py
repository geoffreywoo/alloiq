from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path

from ..models import Position, Transaction
from ..util import decimal_or_zero, normalize_header, parse_date, stable_id


CSV_FIELDS = {
    "date": ["tradedate", "date", "transactiondate", "processdate"],
    "action": ["transactiontype", "type", "action", "activity"],
    "symbol": ["symbol", "ticker", "investment"],
    "description": ["description", "securityname", "investmentname", "fundname"],
    "quantity": ["shares", "quantity", "units"],
    "price": ["price", "shareprice", "nav"],
    "amount": ["amount", "netamount", "totalamount"],
    "fees": ["fees", "commission", "commissionsandfees"],
    "account": ["account", "accountnumber", "accountname"],
}

POSITION_FIELDS = {
    "as_of": ["asof", "asofdate", "date", "pricedate", "holdingdate"],
    "account": ["account", "accountnumber", "accountname", "registration"],
    "symbol": ["symbol", "ticker", "investment", "holdingsymbol"],
    "description": ["description", "securityname", "investmentname", "fundname", "holdingname", "name"],
    "quantity": ["shares", "quantity", "units", "sharequantity"],
    "price": ["price", "shareprice", "marketprice", "lastprice", "nav"],
    "market_value": ["marketvalue", "currentvalue", "value", "totalvalue", "holdingvalue"],
    "cost_basis": ["costbasis", "totalcostbasis", "cost", "totalcost"],
}


def parse_vanguard_file(path: Path) -> list[Transaction]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return parse_vanguard_csv(path)
    if suffix in {".qfx", ".ofx"}:
        return parse_vanguard_qfx(path)
    return []


def parse_vanguard_csv(path: Path) -> list[Transaction]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            return []
        field_map = map_fields(reader.fieldnames)
        transactions: list[Transaction] = []
        for idx, row in enumerate(reader, start=1):
            trade_date = parse_date(row_value(row, field_map, "date"))
            if trade_date is None:
                continue
            action_raw = row_value(row, field_map, "action")
            action = normalize_vanguard_action(action_raw)
            symbol = row_value(row, field_map, "symbol").upper()
            amount = decimal_or_zero(row_value(row, field_map, "amount"))
            quantity = decimal_or_zero(row_value(row, field_map, "quantity"))
            price = decimal_or_zero(row_value(row, field_map, "price"))
            fees = decimal_or_zero(row_value(row, field_map, "fees"))
            account = row_value(row, field_map, "account") or "Vanguard"
            description = row_value(row, field_map, "description") or action_raw
            external_id = stable_id(["vanguard", path.name, idx, trade_date, action, symbol, quantity, amount])
            transactions.append(
                Transaction(
                    broker="vanguard",
                    account=account,
                    trade_date=trade_date,
                    action=action,
                    symbol=symbol,
                    description=description,
                    quantity=quantity,
                    price=price,
                    amount=amount,
                    fees=fees,
                    external_id=external_id,
                    raw=dict(row),
                )
            )
    return transactions


def parse_vanguard_positions_file(path: Path) -> list[Position]:
    if path.suffix.lower() != ".csv":
        return []
    return parse_vanguard_positions_csv(path)


def parse_vanguard_positions_csv(path: Path) -> list[Position]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            return []
        field_map = map_custom_fields(reader.fieldnames, POSITION_FIELDS)
        if "market_value" not in field_map and "cost_basis" not in field_map:
            return []
        positions: list[Position] = []
        fallback_as_of = file_mtime_date(path)
        for row in reader:
            symbol = row_value(row, field_map, "symbol").upper()
            if not symbol:
                continue
            quantity = decimal_or_zero(row_value(row, field_map, "quantity"))
            price = decimal_or_zero(row_value(row, field_map, "price"))
            market_value = decimal_or_zero(row_value(row, field_map, "market_value"))
            if market_value == 0 and quantity != 0 and price != 0:
                market_value = quantity * price
            cost_basis = decimal_or_zero(row_value(row, field_map, "cost_basis"))
            positions.append(
                Position(
                    broker="vanguard",
                    account=row_value(row, field_map, "account") or "Vanguard",
                    as_of=parse_date(row_value(row, field_map, "as_of")) or fallback_as_of,
                    symbol=symbol,
                    description=row_value(row, field_map, "description"),
                    quantity=quantity,
                    cost_basis=cost_basis,
                    market_value=market_value,
                    raw=dict(row),
                )
            )
    return positions


def parse_vanguard_qfx(path: Path) -> list[Transaction]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    transactions: list[Transaction] = []
    for block in re.findall(r"<STMTTRN>(.*?)(?=<STMTTRN>|</BANKTRANLIST>|</OFX>)", text, flags=re.I | re.S):
        values = extract_ofx_values(block)
        trade_date = parse_date(values.get("DTPOSTED"))
        if not trade_date:
            continue
        action = normalize_vanguard_action(values.get("TRNTYPE", "CASH"))
        amount = decimal_or_zero(values.get("TRNAMT"))
        external_id = values.get("FITID") or stable_id(["vanguard-qfx", path.name, trade_date, action, amount, values])
        transactions.append(
            Transaction(
                broker="vanguard",
                account="Vanguard",
                trade_date=trade_date,
                action=action,
                description=values.get("NAME") or values.get("MEMO") or action,
                amount=amount,
                external_id=external_id,
                raw=values,
            )
        )
    return transactions


def map_fields(fieldnames: list[str]) -> dict[str, str]:
    return map_custom_fields(fieldnames, CSV_FIELDS)


def map_custom_fields(fieldnames: list[str], aliases_by_field: dict[str, list[str]]) -> dict[str, str]:
    normalized = {normalize_header(name): name for name in fieldnames}
    mapped: dict[str, str] = {}
    for canonical, aliases in aliases_by_field.items():
        for alias in aliases:
            if alias in normalized:
                mapped[canonical] = normalized[alias]
                break
    return mapped


def row_value(row: dict[str, str], field_map: dict[str, str], canonical: str) -> str:
    field = field_map.get(canonical)
    if not field:
        return ""
    return (row.get(field) or "").strip()


def normalize_vanguard_action(value: str) -> str:
    text = (value or "").upper()
    if "BUY" in text or "PURCHASE" in text or "REINVEST" in text:
        return "BUY"
    if "SELL" in text or "REDEMPTION" in text:
        return "SELL"
    if "DIV" in text:
        return "DIVIDEND"
    if "INTEREST" in text:
        return "INTEREST"
    if "DEPOSIT" in text or "CONTRIBUTION" in text:
        return "DEPOSIT"
    if "WITHDRAW" in text or "DISTRIBUTION" in text:
        return "WITHDRAWAL"
    return text.replace(" ", "_") or "CASH"


def latest_import_date(paths: list[Path]) -> date | None:
    dates = []
    for path in paths:
        try:
            dates.append(date.fromtimestamp(path.stat().st_mtime))
        except OSError:
            continue
    return max(dates) if dates else None


def file_mtime_date(path: Path) -> date:
    try:
        return date.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return date.today()


def extract_ofx_values(block: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for match in re.finditer(r"<([A-Z0-9_]+)>([^<\r\n]+)", block, flags=re.I):
        values[match.group(1).upper()] = match.group(2).strip()
    return values
