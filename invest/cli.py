from __future__ import annotations

import argparse
import json
from pathlib import Path

from .backtest import backtest_signal
from .brokers.ibkr import FlexError, fetch_flex_statement, parse_flex_xml, summarize_flex_xml
from .brokers.vanguard import parse_vanguard_file, parse_vanguard_positions_file
from .config import DEFAULT_CONFIG_PATH, init_config, load_config
from .db import connect, init_db, insert_positions, insert_transactions, record_import, upsert_filing
from .filings.sec import DEFAULT_CUSIP_SYMBOL_MAP, DEFAULT_ISSUER_SYMBOL_MAP, fetch_13f_holdings, fetch_recent_filings
from .reports import generate_brief
from .site import build_site


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="invest")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to TOML config")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Create config/invest.toml and initialize SQLite")

    sync = sub.add_parser("sync", help="Import broker activity")
    sync.add_argument("--broker", choices=["ibkr", "vanguard", "all"], default="all")

    ibkr = sub.add_parser("ibkr", help="IBKR Flex setup and diagnostics")
    ibkr_sub = ibkr.add_subparsers(dest="ibkr_command", required=True)
    ibkr_sub.add_parser("status", help="Show IBKR credential/import status without revealing secrets")
    validate = ibkr_sub.add_parser("validate", help="Fetch and parse a live read-only IBKR Flex statement")
    validate.add_argument("--import", dest="do_import", action="store_true", help="Import the fetched statement into SQLite")
    validate.add_argument("--attempts", type=int, default=10, help="Download attempts while IBKR generates the statement")
    validate.add_argument("--wait-seconds", type=float, default=3.0, help="Seconds between statement download attempts")
    import_file = ibkr_sub.add_parser("import-file", help="Import a previously downloaded IBKR Flex XML file")
    import_file.add_argument("path", help="Path to IBKR Flex XML")

    filings = sub.add_parser("filings", help="Fetch public manager filings")
    filings.add_argument("--manager", default="all", help="Manager key or all")
    filings.add_argument("--max-filings", type=int, default=2, help="Recent filings per manager to process")
    filings.add_argument("--backfill", action="store_true", help="Process every recent SEC filing in the submissions feed")

    brief = sub.add_parser("brief", help="Generate a research brief")
    brief.add_argument("--session", choices=["premarket", "postmarket"], required=True)

    site = sub.add_parser("site", help="Build the AlloIQ static website")
    site_sub = site.add_subparsers(dest="site_command", required=True)
    site_build = site_sub.add_parser("build", help="Build web/data from report JSON")
    site_build.add_argument("--out-dir", default="web", help="Static output directory")
    site_build.add_argument("--privacy", choices=["public", "private"], default="public")

    backtest = sub.add_parser("backtest-signal", help="Run a simple stored-data signal diagnostic")
    backtest.add_argument("--signal", required=True)

    args = parser.parse_args(argv)
    config_path = Path(args.config)
    if args.command == "init":
        created = init_config(config_path)
        config = load_config(config_path)
        conn = connect(config.db_path)
        init_db(conn)
        print(f"{'Created' if created else 'Found'} {config_path}")
        print(f"Initialized database at {config.db_path}")
        return 0

    config = load_config(config_path)
    conn = connect(config.db_path)
    init_db(conn)

    if args.command == "sync":
        return command_sync(args.broker, config, conn)
    if args.command == "ibkr":
        return command_ibkr(args, config, conn)
    if args.command == "filings":
        max_filings = None if args.backfill else args.max_filings
        return command_filings(args.manager, config, conn, max_filings=max_filings)
    if args.command == "brief":
        md_path, json_path = generate_brief(conn, config, args.session)
        print(f"Wrote {md_path}")
        print(f"Wrote {json_path}")
        return 0
    if args.command == "site":
        result = build_site(config.reports_dir, Path(args.out_dir), privacy=args.privacy)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "backtest-signal":
        result = backtest_signal(conn, args.signal)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] in {"ok", "insufficient_data"} else 2
    return 2


def command_sync(broker: str, config, conn) -> int:
    total = 0
    if broker in {"ibkr", "all"}:
        if config.ibkr_enabled:
            total += sync_ibkr(config, conn)
        else:
            print("IBKR skipped: disabled in config")
    if broker in {"vanguard", "all"}:
        if config.vanguard_enabled:
            total += sync_vanguard(config, conn)
        elif broker == "vanguard":
            print("Vanguard skipped: disabled in config")
    print(f"Imported {total} broker rows")
    return 0


def sync_ibkr(config, conn) -> int:
    if not config.ibkr_token or not config.ibkr_activity_query_id:
        print("IBKR skipped: set IBKR_FLEX_TOKEN and IBKR_FLEX_ACTIVITY_QUERY_ID")
        return 0
    try:
        path = fetch_flex_statement(config.ibkr_token, config.ibkr_activity_query_id, config.ibkr_raw_dir)
    except FlexError as exc:
        print(f"IBKR failed: {exc}")
        return 0
    transactions, positions = parse_flex_xml(path)
    tx_count = insert_transactions(conn, transactions)
    pos_count = insert_positions(conn, positions)
    print(f"IBKR imported {tx_count} transactions and {pos_count} positions from {path}")
    return tx_count + pos_count


def command_ibkr(args, config, conn) -> int:
    if args.ibkr_command == "status":
        return command_ibkr_status(config, conn)
    if args.ibkr_command == "validate":
        return command_ibkr_validate(args, config, conn)
    if args.ibkr_command == "import-file":
        return command_ibkr_import_file(Path(args.path), conn)
    return 2


def command_ibkr_status(config, conn) -> int:
    raw_files = sorted(config.ibkr_raw_dir.glob("*.xml")) if config.ibkr_raw_dir.exists() else []
    latest_raw = raw_files[-1] if raw_files else None
    tx_count = conn.execute("SELECT COUNT(*) AS n FROM transactions WHERE broker = 'ibkr'").fetchone()["n"]
    pos_count = conn.execute("SELECT COUNT(*) AS n FROM positions WHERE broker = 'ibkr'").fetchone()["n"]
    latest_tx = conn.execute("SELECT MAX(trade_date) AS d FROM transactions WHERE broker = 'ibkr'").fetchone()["d"]
    latest_pos = conn.execute("SELECT MAX(as_of) AS d FROM positions WHERE broker = 'ibkr'").fetchone()["d"]
    status = {
        "token_present": bool(config.ibkr_token),
        "activity_query_id_present": bool(config.ibkr_activity_query_id),
        "raw_directory": str(config.ibkr_raw_dir),
        "latest_raw_file": str(latest_raw) if latest_raw else None,
        "imported_transactions": tx_count,
        "imported_positions": pos_count,
        "latest_transaction_date": latest_tx,
        "latest_position_date": latest_pos,
    }
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0


def command_ibkr_validate(args, config, conn) -> int:
    if not config.ibkr_token or not config.ibkr_activity_query_id:
        print("IBKR validate failed: add IBKR_FLEX_TOKEN and IBKR_FLEX_ACTIVITY_QUERY_ID to .env")
        return 2
    try:
        path = fetch_flex_statement(
            config.ibkr_token,
            config.ibkr_activity_query_id,
            config.ibkr_raw_dir,
            attempts=args.attempts,
            wait_seconds=args.wait_seconds,
        )
    except FlexError as exc:
        print(f"IBKR validate failed: {exc}")
        return 2
    summary = summarize_flex_xml(path)
    print(json.dumps(summary, indent=2, sort_keys=True))
    if args.do_import:
        imported = import_ibkr_file(path, conn)
        print(json.dumps(imported, indent=2, sort_keys=True))
    return 0


def command_ibkr_import_file(path: Path, conn) -> int:
    if not path.exists():
        print(f"IBKR import failed: file not found: {path}")
        return 2
    try:
        summary = summarize_flex_xml(path)
    except Exception as exc:
        print(f"IBKR import failed: {exc}")
        return 2
    print(json.dumps(summary, indent=2, sort_keys=True))
    imported = import_ibkr_file(path, conn)
    print(json.dumps(imported, indent=2, sort_keys=True))
    return 0


def import_ibkr_file(path: Path, conn) -> dict[str, int | str]:
    transactions, positions = parse_flex_xml(path)
    tx_count = insert_transactions(conn, transactions)
    pos_count = insert_positions(conn, positions)
    return {
        "path": str(path),
        "transactions_imported": tx_count,
        "positions_imported": pos_count,
    }


def sync_vanguard(config, conn) -> int:
    import_dir = config.vanguard_import_dir
    import_dir.mkdir(parents=True, exist_ok=True)
    files = sorted([p for p in import_dir.iterdir() if p.suffix.lower() in {".csv", ".qfx", ".ofx"}])
    if not files:
        print(f"Vanguard skipped: no CSV/QFX/OFX files in {import_dir}")
        return 0
    total = 0
    for path in files:
        transactions = parse_vanguard_file(path)
        positions = parse_vanguard_positions_file(path)
        imported = insert_transactions(conn, transactions)
        pos_imported = insert_positions(conn, positions) if positions else 0
        record_import(conn, "vanguard", path, len(transactions) + len(positions))
        print(
            f"Vanguard parsed {len(transactions)} transactions and {len(positions)} positions from {path}, "
            f"imported {imported} new transactions and {pos_imported} positions"
        )
        total += imported + pos_imported
    return total


def command_filings(manager_key: str, config, conn, max_filings: int | None = 2) -> int:
    if manager_key == "all":
        total = 0
        for manager in config.data.get("managers", []):
            key = manager.get("key")
            if not key or not manager.get("cik"):
                continue
            try:
                total += store_filings_for_manager(str(key), config, conn, max_filings=max_filings)
            except Exception as exc:
                print(f"{key} filings failed: {exc}")
        print(f"Stored filings for all configured managers: {total}")
        return 0
    store_filings_for_manager(manager_key, config, conn, max_filings=max_filings)
    return 0


def store_filings_for_manager(manager_key: str, config, conn, max_filings: int | None = 2) -> int:
    manager = config.manager(manager_key)
    if not manager.get("cik"):
        print(f"{manager_key} skipped: no CIK configured")
        return 0
    filings = fetch_recent_filings(manager, forms={"13F-HR", "13F-HR/A"})
    if max_filings is not None:
        filings = filings[:max(0, max_filings)]
    stored = 0
    for filing in filings:
        holdings = []
        if filing.form in {"13F-HR", "13F-HR/A"}:
            holdings = fetch_13f_holdings(
                filing.cik,
                filing.accession_number,
                DEFAULT_CUSIP_SYMBOL_MAP,
                config.symbol_to_bucket,
                DEFAULT_ISSUER_SYMBOL_MAP,
            )
        upsert_filing(conn, filing, holdings)
        stored += 1
        print(f"Stored {filing.form} {filing.accession_number} with {len(holdings)} holdings")
    print(f"Stored {stored} filings for {manager_key}")
    return stored


if __name__ == "__main__":
    raise SystemExit(main())
