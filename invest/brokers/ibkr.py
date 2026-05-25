from __future__ import annotations

import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from typing import Any

from ..models import Position, Transaction
from ..util import decimal_or_zero, ensure_dir, parse_date, stable_id


FLEX_BASE = "https://gdcdyn.interactivebrokers.com/Universal/servlet"
RETRYABLE_FLEX_CODES = {"1019", "1003"}


class FlexError(RuntimeError):
    def __init__(self, message: str, code: str = "", retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def fetch_flex_statement(token: str, query_id: str, raw_dir: Path, attempts: int = 10, wait_seconds: float = 3.0) -> Path:
    ensure_dir(raw_dir)
    reference_code = request_flex_reference_with_retry(token, query_id, attempts=attempts, wait_seconds=wait_seconds)
    last_error: FlexError | None = None
    for attempt in range(1, attempts + 1):
        try:
            statement = download_flex_statement(token, reference_code)
            path = raw_dir / f"ibkr-flex-{date.today().isoformat()}-{reference_code}.xml"
            path.write_bytes(statement)
            return path
        except FlexError as exc:
            last_error = exc
            if not exc.retryable or attempt == attempts:
                raise
            time.sleep(wait_seconds)
    raise last_error or FlexError("IBKR Flex statement download failed")


def request_flex_reference_with_retry(token: str, query_id: str, attempts: int = 10, wait_seconds: float = 3.0) -> str:
    last_error: FlexError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return request_flex_reference(token, query_id)
        except FlexError as exc:
            last_error = exc
            if not exc.retryable or attempt == attempts:
                raise
            time.sleep(wait_seconds)
    raise last_error or FlexError("IBKR Flex request failed")


def request_flex_reference(token: str, query_id: str) -> str:
    query = urllib.parse.urlencode({"t": token, "q": query_id, "v": "3"})
    send_url = f"{FLEX_BASE}/FlexStatementService.SendRequest?{query}"
    send_xml = urllib.request.urlopen(send_url, timeout=30).read()
    root = parse_xml_or_raise(send_xml, "IBKR Flex request")
    ensure_success(root, "IBKR Flex request")
    reference_code = root.findtext(".//ReferenceCode")
    if not reference_code:
        raise FlexError("IBKR Flex response did not include a ReferenceCode")
    return reference_code


def download_flex_statement(token: str, reference_code: str) -> bytes:
    get_query = urllib.parse.urlencode({"t": token, "q": reference_code, "v": "3"})
    get_url = f"{FLEX_BASE}/FlexStatementService.GetStatement?{get_query}"
    statement = urllib.request.urlopen(get_url, timeout=60).read()
    root = parse_xml_or_raise(statement, "IBKR Flex statement")
    if is_flex_error_response(root):
        ensure_success(root, "IBKR Flex statement")
    return statement


def parse_xml_or_raise(payload: bytes, label: str) -> ET.Element:
    try:
        return ET.fromstring(payload)
    except ET.ParseError as exc:
        preview = payload[:200].decode("utf-8", errors="replace")
        raise FlexError(f"{label} returned non-XML or malformed XML: {preview}") from exc


def is_flex_error_response(root: ET.Element) -> bool:
    tag = strip_namespace(root.tag)
    return tag in {"FlexStatementResponse", "FlexQueryResponse"} and root.findtext(".//Status") in {"Fail", "Failure"}


def ensure_success(root: ET.Element, label: str) -> None:
    status = root.findtext(".//Status")
    if status == "Success":
        return
    code = root.findtext(".//ErrorCode") or ""
    message = root.findtext(".//ErrorMessage") or ET.tostring(root, encoding="unicode")
    retryable = code in RETRYABLE_FLEX_CODES or "progress" in message.lower() or "try again" in message.lower()
    raise FlexError(f"{label} failed: {message}", code=code, retryable=retryable)


def strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def summarize_flex_xml(path: Path) -> dict[str, Any]:
    transactions, positions = parse_flex_xml(path)
    accounts = sorted({tx.account for tx in transactions} | {pos.account for pos in positions})
    symbols = sorted({tx.symbol for tx in transactions if tx.symbol} | {pos.symbol for pos in positions if pos.symbol})
    return {
        "path": str(path),
        "accounts": accounts,
        "transaction_count": len(transactions),
        "position_count": len(positions),
        "symbol_count": len(symbols),
        "symbols": symbols[:25],
    }


def parse_flex_xml(path: Path) -> tuple[list[Transaction], list[Position]]:
    root = ET.parse(path).getroot()
    transactions: list[Transaction] = []
    positions: list[Position] = []

    for trade in root.findall(".//Trade"):
        attrs = dict(trade.attrib)
        account = attrs.get("accountId") or attrs.get("account") or "IBKR"
        trade_date = parse_date(attrs.get("tradeDate") or attrs.get("dateTime") or attrs.get("settleDate")) or date.today()
        symbol = attrs.get("symbol", "").upper()
        action = normalize_ibkr_action(attrs)
        quantity = decimal_or_zero(attrs.get("quantity"))
        price = decimal_or_zero(attrs.get("tradePrice") or attrs.get("price"))
        amount = decimal_or_zero(attrs.get("proceeds") or attrs.get("netCash") or attrs.get("amount"))
        fees = decimal_or_zero(attrs.get("ibCommission") or attrs.get("commission"))
        external_id = attrs.get("tradeID") or attrs.get("orderID") or stable_id(
            ["ibkr", account, trade_date, action, symbol, quantity, price, amount]
        )
        transactions.append(
            Transaction(
                broker="ibkr",
                account=account,
                trade_date=trade_date,
                action=action,
                symbol=symbol,
                description=attrs.get("description", ""),
                quantity=quantity,
                price=price,
                amount=amount,
                fees=fees,
                currency=attrs.get("currency", "USD"),
                external_id=str(external_id),
                raw=attrs,
            )
        )

    for cash in root.findall(".//CashTransaction"):
        attrs = dict(cash.attrib)
        account = attrs.get("accountId") or attrs.get("account") or "IBKR"
        trade_date = parse_date(attrs.get("date") or attrs.get("reportDate")) or date.today()
        action = normalize_cash_action(attrs.get("type") or attrs.get("description") or "CASH")
        amount = decimal_or_zero(attrs.get("amount"))
        external_id = attrs.get("transactionID") or stable_id(["ibkr-cash", account, trade_date, action, amount, attrs])
        transactions.append(
            Transaction(
                broker="ibkr",
                account=account,
                trade_date=trade_date,
                action=action,
                symbol=attrs.get("symbol", "").upper(),
                description=attrs.get("description", attrs.get("type", "")),
                amount=amount,
                currency=attrs.get("currency", "USD"),
                external_id=str(external_id),
                raw=attrs,
            )
        )

    for node_name in ("OpenPosition", "Position"):
        for pos in root.findall(f".//{node_name}"):
            attrs = dict(pos.attrib)
            symbol = attrs.get("symbol", "").upper()
            if not symbol:
                continue
            account = attrs.get("accountId") or attrs.get("account") or "IBKR"
            positions.append(
                Position(
                    broker="ibkr",
                    account=account,
                    as_of=parse_date(attrs.get("reportDate") or attrs.get("date")) or date.today(),
                    symbol=symbol,
                    description=attrs.get("description", ""),
                    quantity=decimal_or_zero(attrs.get("position") or attrs.get("quantity")),
                    cost_basis=decimal_or_zero(attrs.get("costBasisMoney") or attrs.get("costBasis")),
                    market_value=decimal_or_zero(attrs.get("positionValue") or attrs.get("marketValue")),
                    currency=attrs.get("currency", "USD"),
                    raw=attrs,
                )
            )

    positions.extend(parse_cash_balance_positions(root))
    return transactions, positions


def normalize_ibkr_action(attrs: dict[str, str]) -> str:
    text = (attrs.get("buySell") or attrs.get("transactionType") or "").upper()
    quantity = decimal_or_zero(attrs.get("quantity"))
    if text in {"BUY", "BOT"}:
        return "BUY"
    if text in {"SELL", "SLD"}:
        return "SELL"
    if quantity > 0:
        return "BUY"
    if quantity < 0:
        return "SELL"
    return text or "TRADE"


def normalize_cash_action(value: str) -> str:
    text = value.upper()
    if "DIVIDEND" in text:
        return "DIVIDEND"
    if "INTEREST" in text:
        return "INTEREST"
    if "DEPOSIT" in text:
        return "DEPOSIT"
    if "WITHDRAW" in text:
        return "WITHDRAWAL"
    return "CASH"


def parse_cash_balance_positions(root: ET.Element) -> list[Position]:
    positions: list[Position] = []
    for statement in root.findall(".//FlexStatement"):
        statement_attrs = dict(statement.attrib)
        default_account = statement_attrs.get("accountId") or statement_attrs.get("account") or "IBKR"
        default_date = parse_date(
            statement_attrs.get("toDate")
            or statement_attrs.get("periodTo")
            or statement_attrs.get("reportDate")
            or statement_attrs.get("date")
        ) or date.today()
        for elem in statement.iter():
            tag = strip_namespace(elem.tag)
            if tag not in {"CashReport", "CashBalance", "CashSummary"}:
                continue
            attrs = dict(elem.attrib)
            currency = (attrs.get("currency") or attrs.get("currencyCode") or "USD").upper()
            amount = first_decimal(attrs, [
                "endingCash",
                "endingSettledCash",
                "settledCash",
                "totalCash",
                "cash",
                "cashBalance",
                "endingCashBalance",
                "endingBalance",
                "balance",
                "marketValue",
                "value",
                "total",
            ])
            if amount == 0:
                continue
            account = attrs.get("accountId") or attrs.get("account") or default_account
            as_of = parse_date(attrs.get("reportDate") or attrs.get("date")) or default_date
            positions.append(
                Position(
                    broker="ibkr",
                    account=account,
                    as_of=as_of,
                    symbol=cash_symbol(currency),
                    description=f"{currency} cash reserves",
                    quantity=amount,
                    cost_basis=decimal_or_zero(attrs.get("costBasis")),
                    market_value=amount,
                    currency=currency,
                    raw=attrs,
                )
            )
    return positions


def first_decimal(attrs: dict[str, str], keys: list[str]):
    for key in keys:
        value = decimal_or_zero(attrs.get(key))
        if value != 0:
            return value
    return decimal_or_zero(0)


def cash_symbol(currency: str) -> str:
    currency = currency.upper().strip() or "USD"
    return "CASH" if currency == "USD" else f"CASH_{currency}"
