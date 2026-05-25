from __future__ import annotations

import argparse
import json
from pathlib import Path

from .backtest import build_backtest_summary, backtest_signal
from .brokers.ibkr import FlexError, fetch_flex_statement, parse_flex_xml, summarize_flex_xml
from .brokers.vanguard import parse_vanguard_file, parse_vanguard_positions_file
from .config import DEFAULT_CONFIG_PATH, init_config, load_config
from .db import connect, init_db, insert_positions, insert_transactions, record_import, upsert_filing
from .filings.sec import DEFAULT_CUSIP_SYMBOL_MAP, DEFAULT_ISSUER_SYMBOL_MAP, fetch_13f_holdings, fetch_recent_filings
from .reports import generate_brief
from .site import build_site, serve_site


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
    brief.add_argument("--session", choices=["premarket", "postmarket", "weekly"], required=True)

    pipeline = sub.add_parser("pipeline", help="Run the scheduled data, report, and public-site pipeline")
    pipeline.add_argument("--kind", choices=["premarket", "postmarket", "weekly"], required=True)
    pipeline.add_argument("--privacy", choices=["public", "private"], default="public")
    pipeline.add_argument("--out-dir", default="web", help="Static output directory")
    pipeline.add_argument("--force", action="store_true", help="Bypass schedule/trading-day gating")
    pipeline.add_argument("--scheduled-at", default=None, help="UTC timestamp for schedule gating, defaults to now")

    audit = sub.add_parser("audit", help="Inspect public-safe audit and engine health")
    audit_sub = audit.add_subparsers(dest="audit_command", required=True)
    audit_run = audit_sub.add_parser("run", help="Print the latest audit payload")
    audit_run.add_argument("--privacy", choices=["public"], default="public")

    calendar = sub.add_parser("calendar", help="Inspect earnings and 13F calendars")
    calendar_sub = calendar.add_subparsers(dest="calendar_command", required=True)
    calendar_refresh = calendar_sub.add_parser("refresh", help="Print the latest calendar payload")
    calendar_refresh.add_argument("--kind", choices=["all", "earnings", "13f"], default="all")

    engine = sub.add_parser("engine", help="Inspect recommendation-engine scores")
    engine_sub = engine.add_subparsers(dest="engine_command", required=True)
    engine_score = engine_sub.add_parser("score", help="Print the latest engine score payload")
    engine_score.add_argument("--mode", choices=["paper"], default="paper")
    engine_score.add_argument("--horizon", default="3m,6m,12m")

    paper = sub.add_parser("paper", help="Inspect approval-linked paper-trading state")
    paper_sub = paper.add_subparsers(dest="paper_command", required=True)
    paper_sub.add_parser("update", help="Print the latest paper portfolio payload")

    sources = sub.add_parser("sources", help="Inspect configured external signal feeds")
    sources_sub = sources.add_subparsers(dest="sources_command", required=True)
    sources_sub.add_parser("check", help="Fetch and print the external signal provider snapshot")

    valuation = sub.add_parser("valuation", help="Estimate 13F and private portfolio entry/current values")
    valuation_sub = valuation.add_subparsers(dest="valuation_command", required=True)
    valuation_report = valuation_sub.add_parser("report", help="Build a best-effort valuation report")
    valuation_report.add_argument("--scope", choices=["all", "managers", "portfolio"], default="all")
    valuation_report.add_argument("--manager-set", choices=["ai-maxxi", "tier1", "all"], default="ai-maxxi")
    valuation_report.add_argument("--format", choices=["markdown", "json"], default="markdown")
    valuation_report.add_argument("--out", default="", help="Optional output path")

    warehouse = sub.add_parser("warehouse", help="Manage the private Neon/Postgres warehouse")
    warehouse_sub = warehouse.add_subparsers(dest="warehouse_command", required=True)
    warehouse_sub.add_parser("migrate", help="Create or update private warehouse tables")
    warehouse_sub.add_parser("health", help="Check private warehouse connectivity")

    decisions = sub.add_parser("decisions", help="Record approval decisions for generated tickets")
    decisions_sub = decisions.add_subparsers(dest="decisions_command", required=True)
    decisions_list = decisions_sub.add_parser("list", help="List private approval tickets")
    decisions_list.add_argument("--status", default="open", help="open, approved, rejected, watch, or a custom status")
    decisions_list.add_argument("--limit", type=int, default=50)
    decisions_record = decisions_sub.add_parser("record", help="Record an approval decision")
    decisions_record.add_argument("--ticket-id", required=True)
    decisions_record.add_argument("--decision", choices=["approved", "rejected", "watch"], required=True)
    decisions_record.add_argument("--notes", default="")
    decisions_record.add_argument("--rejection-reason", default="")
    decisions_record.add_argument("--execution-status", default="not_executed")

    tickets = sub.add_parser("tickets", help="Export approval-only research tickets")
    tickets_sub = tickets.add_subparsers(dest="tickets_command", required=True)
    tickets_export = tickets_sub.add_parser("export", help="Export latest approval tickets")
    tickets_export.add_argument("--format", choices=["markdown", "json"], default="markdown")

    site = sub.add_parser("site", help="Build the AlloIQ static website")
    site_sub = site.add_subparsers(dest="site_command", required=True)
    site_build = site_sub.add_parser("build", help="Build web/data from report JSON")
    site_build.add_argument("--out-dir", default="web", help="Static output directory")
    site_build.add_argument("--privacy", choices=["public", "private"], default="public")
    site_serve = site_sub.add_parser("serve", help="Serve the static web app with clean URL support")
    site_serve.add_argument("--out-dir", default="web", help="Static web directory")
    site_serve.add_argument("--host", default="", help="Bind host, defaults to all interfaces")
    site_serve.add_argument("--port", type=int, default=4173, help="Bind port")

    backtest = sub.add_parser("backtest", help="Run recommendation outcome backtests from saved reports")
    backtest_sub = backtest.add_subparsers(dest="backtest_command", required=True)
    backtest_run = backtest_sub.add_parser("run", help="Label saved recommendations against forward price returns")
    backtest_run.add_argument("--out", default="", help="Optional JSON output path")
    backtest_run.add_argument("--format", choices=["json", "summary"], default="summary")

    backtest = sub.add_parser("backtest-signal", help="Run a simple stored-data signal diagnostic")
    backtest.add_argument("--signal", required=True)

    privacy_scan = sub.add_parser("privacy-scan", help="Validate that public web assets contain no private broker data")
    privacy_scan.add_argument("--web-dir", default="web", help="Static web directory to scan")

    args = parser.parse_args(argv)
    if args.command == "privacy-scan":
        from .privacy import assert_public_assets_safe

        assert_public_assets_safe(Path(args.web_dir))
        print("public privacy scan passed")
        return 0
    if args.command == "warehouse":
        return command_warehouse(args)
    if args.command == "site" and args.site_command == "serve":
        return serve_site(Path(args.out_dir), host=args.host, port=args.port)

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
        if args.site_command == "build":
            result = build_site(config.reports_dir, Path(args.out_dir), privacy=args.privacy)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        return 2
    if args.command == "backtest":
        return command_backtest(args, config)
    if args.command == "pipeline":
        from .pipeline import run_pipeline

        result = run_pipeline(
            conn,
            config,
            args.kind,
            privacy=args.privacy,
            out_dir=Path(args.out_dir),
            force=args.force,
            scheduled_at=args.scheduled_at,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "audit":
        return command_audit(args, config)
    if args.command == "calendar":
        return command_calendar(args, config)
    if args.command == "engine":
        return command_engine(args, config)
    if args.command == "paper":
        return command_paper(args, config)
    if args.command == "sources":
        return command_sources(args, config)
    if args.command == "valuation":
        return command_valuation(args, config, conn)
    if args.command == "decisions":
        return command_decisions(args)
    if args.command == "tickets":
        return command_tickets(args, config)
    if args.command == "backtest-signal":
        result = backtest_signal(conn, args.signal)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] in {"ok", "insufficient_data"} else 2
    return 2


def command_warehouse(args) -> int:
    from .warehouse import WarehouseDisabled, health, migrate

    if args.warehouse_command == "health":
        result = health()
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return 0 if result["status"] in {"ok", "disabled"} else 2
    if args.warehouse_command == "migrate":
        try:
            result = migrate()
        except WarehouseDisabled as exc:
            result = {"status": "disabled", "reason": str(exc)}
            print(json.dumps(result, indent=2, sort_keys=True, default=str))
            return 2
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return 0
    return 2


def latest_payload(config) -> dict:
    report_paths = sorted(config.reports_dir.glob("*.json"), key=lambda path: (path.stat().st_mtime, path.name))
    if report_paths:
        return json.loads(report_paths[-1].read_text(encoding="utf-8"))
    public_snapshot = Path("web/data/latest.json")
    if public_snapshot.exists():
        return json.loads(public_snapshot.read_text(encoding="utf-8"))
    return {}


def command_audit(args, config) -> int:
    payload = latest_payload(config)
    print(json.dumps(payload.get("audit") or {}, indent=2, sort_keys=True, default=str))
    return 0


def command_calendar(args, config) -> int:
    calendars = latest_payload(config).get("calendars") or {}
    if args.kind == "earnings":
        output = calendars.get("earnings") or {}
    elif args.kind == "13f":
        output = calendars.get("filings_13f") or {}
    else:
        output = calendars
    print(json.dumps(output, indent=2, sort_keys=True, default=str))
    return 0


def command_engine(args, config) -> int:
    payload = latest_payload(config)
    engine = dict(payload.get("engine") or {})
    engine["requested_mode"] = args.mode
    engine["requested_horizon"] = args.horizon
    print(json.dumps(engine, indent=2, sort_keys=True, default=str))
    return 0


def command_paper(args, config) -> int:
    print(json.dumps(latest_payload(config).get("paper_portfolio") or {}, indent=2, sort_keys=True, default=str))
    return 0


def command_sources(args, config) -> int:
    from datetime import date

    from .external_signals import build_external_signal_snapshot

    if args.sources_command == "check":
        snapshot = build_external_signal_snapshot(config, date.today(), config.watchlist_symbols)
        print(json.dumps(snapshot, indent=2, sort_keys=True, default=str))
        return 0
    return 2


def command_backtest(args, config) -> int:
    result = build_backtest_summary(config.reports_dir)
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2, sort_keys=True, default=str), encoding="utf-8")
        print(f"Wrote {path}")
        return 0
    if args.format == "json":
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return 0
    print(format_backtest_summary(result), end="")
    return 0


def format_backtest_summary(result: dict) -> str:
    lines = [
        "# AlloIQ Recommendation Backtest",
        "",
        f"- Status: {result.get('status', 'unknown')}",
        f"- Trials: {result.get('trial_count', 0)}",
        f"- Completed labels: {result.get('completed_outcome_count', 0)}",
        f"- Pending labels: {result.get('pending_outcome_count', 0)}",
        "",
    ]
    for row in result.get("horizons", []):
        lines.append(
            f"- {row.get('horizon')}: completed {row.get('completed_count', 0)}, "
            f"hit rate {row.get('hit_rate')}, avg return {row.get('average_decision_return')}"
        )
    lines.append("")
    return "\n".join(lines)


def command_valuation(args, config, conn) -> int:
    from datetime import date

    from .portfolio import build_portfolio_exposure
    from .valuation import (
        AI_MAXXI_MANAGER_KEYS,
        VALUATION_VERSION,
        build_manager_valuation_snapshot,
        build_portfolio_valuation_snapshot,
        format_valuation_markdown,
        manager_valuation_symbols,
        valuation_methodology,
        write_valuation_report,
    )
    from .market import fetch_daily_prices

    manager_keys = valuation_manager_keys(args.manager_set, config)
    price_symbols = []
    if args.scope in {"all", "managers"}:
        price_symbols.extend(manager_valuation_symbols(conn, manager_keys))
    if args.scope in {"all", "portfolio"}:
        price_symbols.extend(config.watchlist_symbols)
        price_symbols.extend(str(row.get("symbol", "")).upper() for row in config.manual_positions)
    prices = fetch_daily_prices(unique_symbols(price_symbols))
    snapshot = {
        "version": VALUATION_VERSION,
        "as_of": date.today().isoformat(),
        "scope": args.scope,
        "manager_set": args.manager_set,
        "methodology": valuation_methodology(),
        "managers": {},
        "portfolio": {},
    }
    if args.scope in {"all", "managers"}:
        snapshot["managers"] = build_manager_valuation_snapshot(conn, config, prices, manager_keys)
    if args.scope in {"all", "portfolio"}:
        portfolio = build_portfolio_exposure(conn, config, prices=prices, as_of=date.today())
        snapshot["portfolio"] = build_portfolio_valuation_snapshot(portfolio, date.today())
    if args.out:
        path = write_valuation_report(snapshot, Path(args.out), args.format)
        print(f"Wrote {path}")
        return 0
    if args.format == "json":
        print(json.dumps(snapshot, indent=2, sort_keys=True, default=str))
    else:
        print(format_valuation_markdown(snapshot), end="")
    return 0


def valuation_manager_keys(manager_set: str, config) -> list[str]:
    if manager_set == "ai-maxxi":
        return ["situational-awareness", "altimeter", "dragoneer"]
    if manager_set == "tier1":
        tier_map = config.focus_manager_tier_map
        return [
            key for key in config.focus_manager_keys
            if tier_map.get(key) == "tier_1" or key in {"situational-awareness", "altimeter", "dragoneer"}
        ]
    return config.focus_manager_keys


def unique_symbols(symbols) -> list[str]:
    seen = set()
    out = []
    for symbol in symbols:
        clean = str(symbol or "").upper()
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


def command_decisions(args) -> int:
    from .warehouse import list_recommendations, record_decision

    if args.decisions_command == "list":
        rows = list_recommendations(status=args.status, limit=args.limit)
        print(json.dumps(rows, indent=2, sort_keys=True, default=str))
        return 0
    if args.decisions_command == "record":
        result = record_decision(
            args.ticket_id,
            args.decision,
            notes=args.notes,
            rejection_reason=args.rejection_reason,
            execution_status=args.execution_status,
        )
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return 0
    return 2


def command_tickets(args, config) -> int:
    from .warehouse import WarehouseDisabled, export_latest_tickets_from_reports, format_tickets_markdown, list_recommendations

    try:
        tickets = list_recommendations(status="open", limit=100)
    except WarehouseDisabled:
        tickets = export_latest_tickets_from_reports(config.reports_dir)
    if args.format == "json":
        print(json.dumps(tickets, indent=2, sort_keys=True, default=str))
    else:
        print(format_tickets_markdown(tickets), end="")
    return 0


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
