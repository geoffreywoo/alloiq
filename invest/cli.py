from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date
import json
from pathlib import Path

from .backtest import build_backtest_summary, backtest_signal, outcome_history_from_backtest, trials_from_payload_actions
from .brokers.ibkr import FlexError, fetch_flex_statement, parse_flex_xml, summarize_flex_xml
from .brokers.vanguard import parse_vanguard_file, parse_vanguard_positions_file
from .config import DEFAULT_CONFIG_PATH, init_config, load_config
from .db import (
    connect,
    init_db,
    insert_positions,
    insert_transactions,
    manager_filing_holding_count,
    record_import,
    upsert_filing,
)
from .features import FEATURE_MATRIX_VERSION, MODEL_POLICY_VERSION
from .filings.sec import DEFAULT_CUSIP_SYMBOL_MAP, DEFAULT_ISSUER_SYMBOL_MAP, fetch_13f_holdings, fetch_recent_filings
from .outcomes import FORWARD_HORIZONS, TRAINING_EXAMPLE_VERSION, build_outcome_diagnostics, pending_label_schedule
from .reports import generate_brief
from .site import build_site, normalize_public_external_reliability, report_selection_key, serve_site
from .util import parse_date, stable_id


COVERAGE_GAP_PLAN_EXPORT_VERSION = "2026-05-external-coverage-gap-plan-export-v2"
MEASUREMENT_GAP_PLAN_EXPORT_VERSION = "2026-05-external-alignment-measurement-gap-plan-export-v1"
PROVIDER_GAP_SEVERITY_BACKFILL_PLAN_EXPORT_VERSION = "2026-05-external-provider-gap-severity-backfill-plan-export-v1"
EXTERNAL_ALIGNMENT_REVIEW_PLAN_EXPORT_VERSION = "2026-05-external-alignment-review-plan-export-v1"
EXTERNAL_ALIGNMENT_MATURITY_TEST_VERSION = "2026-05-external-alignment-maturity-test-v1"
COVERAGE_GAP_PLAN_ROW_KEYS = (
    "external_coverage_gap_id",
    "external_coverage_gap_version",
    "symbol",
    "bucket",
    "trade_action",
    "horizon",
    "as_of",
    "due_date",
    "status",
    "source_outcome_id",
    "source_trial_id",
    "external_feed_status",
    "external_coverage_multiplier",
    "coverage_adjusted_external_signal_score",
    "external_alignment",
    "missing_external_fields",
    "minimum_external_fields_to_backfill",
    "required_external_observation_date",
    "external_coverage_gap_reason",
    "external_coverage_gap_action",
    "external_coverage_backfill_policy",
    "external_coverage_acceptance_checks",
    "residual_learning_value_score",
    "residual_learning_value_reason",
    "residual_backfill_status",
)
MEASUREMENT_GAP_PLAN_ROW_KEYS = (
    "external_alignment_measurement_gap_id",
    "external_alignment_measurement_gap_version",
    "external_alignment_review_id",
    "symbol",
    "bucket",
    "trade_action",
    "horizon",
    "as_of",
    "due_date",
    "status",
    "source_outcome_id",
    "source_trial_id",
    "session",
    "external_alignment",
    "external_alignment_review_focus",
    "external_alignment_review_label_count",
    "external_alignment_measurement_missing_label_count",
    "external_alignment_measurement_missing_fields",
    "external_alignment_measurement_missing_field_counts",
    "risk_adjusted_expected_return",
    "coverage_adjusted_external_signal_score",
    "external_coverage_multiplier",
    "external_feed_status",
    "external_alignment_measurement_gap_action",
    "external_alignment_measurement_backfill_policy",
    "external_alignment_measurement_acceptance_checks",
)
MEASUREMENT_GAP_CANDIDATE_VALUE_KEYS = (
    "risk_adjusted_expected_return",
)
EXTERNAL_ALIGNMENT_REVIEW_PLAN_ROW_KEYS = (
    "external_alignment_review_id",
    "external_alignment_review_version",
    "symbol",
    "bucket",
    "trade_action",
    "horizon",
    "as_of",
    "due_date",
    "status",
    "decision_forward_return_pct",
    "raw_forward_return_pct",
    "hit",
    "expected_vs_realized_error",
    "source_outcome_id",
    "source_trial_id",
    "external_alignment",
    "external_alignment_review_reason",
    "external_alignment_review_focus",
    "external_alignment_review_label_count",
    "external_alignment_review_priority",
    "external_alignment_review_priority_reason",
    "external_alignment_review_learning_action",
    "external_alignment_review_measurement_plan",
    "external_alignment_review_acceptance_checks",
    "external_alignment_review_open_check_count",
)
COVERAGE_GAP_CANDIDATE_VALUE_KEYS = (
    "external_signal_score",
    "external_feed_status",
    "external_coverage_multiplier",
    "coverage_adjusted_external_signal_score",
    "external_provider_count",
    "external_provider_ok_count",
    "external_provider_ok_ratio",
    "external_provider_gap_count",
    "external_provider_configuration_gap_count",
    "external_provider_transient_gap_count",
    "external_provider_stale_gap_count",
    "external_provider_runtime_gap_count",
    "external_provider_other_gap_count",
    "external_provider_primary_gap_severity",
    "external_provider_gap_severity_score",
    "external_signal_count",
    "external_source_count",
)
PROVIDER_GAP_SEVERITY_CANDIDATE_VALUE_KEYS = (
    "external_provider_gap_count",
    "external_provider_configuration_gap_count",
    "external_provider_runtime_gap_count",
    "external_provider_stale_gap_count",
    "external_provider_transient_gap_count",
    "external_provider_other_gap_count",
    "external_provider_primary_gap_severity",
    "external_provider_gap_severity_score",
)


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
    report_kinds = ["premarket", "market_open", "intraday", "midday", "market_close", "postmarket", "weekly"]
    brief.add_argument("--session", choices=report_kinds, required=True)

    pipeline = sub.add_parser("pipeline", help="Run the scheduled data, report, and public-site pipeline")
    pipeline.add_argument("--kind", choices=report_kinds, required=True)
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
    sources_check = sources_sub.add_parser("check", help="Fetch and print the external signal provider snapshot")
    sources_check.add_argument("--as-of", default="", help="Historical as-of date for provider queries, defaults to today")
    sources_check.add_argument("--symbols", default="", help="Comma-separated symbols to check, defaults to configured watchlist")
    sources_check.add_argument("--out", default="", help="Optional path to write the external signal snapshot JSON")

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

    notify = sub.add_parser("notify", help="Send the latest briefing to a configured notification channel")
    notify.add_argument("--session", choices=report_kinds, default="", help="Report session to send")
    notify.add_argument("--channel", choices=["telegram"], default="telegram")
    notify.add_argument("--reports-dir", default="", help="Override report JSON directory")
    notify.add_argument("--site-url", default="", help="Override dashboard URL used in the message")
    notify.add_argument("--dry-run", action="store_true", help="Print the message without sending it")
    notify.add_argument("--urgent-only", action="store_true", help="Send only if the latest report has a new urgent trigger")
    notify.add_argument("--compare-to", default="", help="Previous public snapshot used to suppress repeated urgent alerts")

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
    backtest_refresh = backtest_sub.add_parser("refresh-report", help="Refresh a saved report's backtest block from current reports")
    backtest_refresh.add_argument("--report", default="", help="Report JSON to refresh; defaults to latest report by as_of/session")

    backtest = sub.add_parser("backtest-signal", help="Run a simple stored-data signal diagnostic")
    backtest.add_argument("--signal", required=True)

    privacy_scan = sub.add_parser("privacy-scan", help="Validate that public web assets contain no private broker data")
    privacy_scan.add_argument("--web-dir", default="web", help="Static web directory to scan")

    quality_scan = sub.add_parser("quality-scan", help="Validate that the public snapshot passes instrumentation gates")
    quality_scan.add_argument("--web-dir", default="web", help="Static web directory to scan")

    coverage_gap_plan = sub.add_parser("coverage-gap-plan", help="Export external coverage priority backfill checklist")
    coverage_gap_plan.add_argument("--web-dir", default="web", help="Static web directory containing data/latest.json")
    coverage_gap_plan.add_argument("--reports-dir", default="reports", help="Report JSON directory used for candidate resolution")
    coverage_gap_plan.add_argument("--format", choices=["json", "text"], default="json")
    coverage_gap_plan.add_argument(
        "--candidate-source",
        choices=["source-report", "eligible-reports"],
        default="source-report",
        help="Candidate resolver source: latest source report or best per-item date-eligible report",
    )
    coverage_gap_plan.add_argument(
        "--candidate-queue",
        choices=["priority", "residual", "all"],
        default="priority",
        help="Candidate queue to resolve/apply; residual work is opt-in",
    )
    coverage_gap_plan.add_argument(
        "--candidate-limit",
        type=int,
        default=0,
        help="Maximum deduped candidate items to resolve/apply; 0 means all selected items",
    )
    coverage_gap_plan.add_argument(
        "--resolve-candidates",
        action="store_true",
        help="Resolve candidate backfill values from the decision-time source report",
    )
    coverage_gap_plan.add_argument(
        "--apply-candidates",
        action="store_true",
        help="Apply resolved candidate values to matching recommendation training examples",
    )
    coverage_gap_plan.add_argument(
        "--materialize-recovery-training-examples",
        action="store_true",
        help="Materialize missing legacy action-queue training examples for residual recovery reports",
    )
    coverage_gap_plan.add_argument(
        "--materialize-recovery-feature-skeleton",
        action="store_true",
        help="Materialize non-external feature skeleton rows from residual recovery report action queues",
    )
    coverage_gap_plan.add_argument(
        "--attach-recovery-external-signals",
        default="",
        help="Attach a saved external signal snapshot to matching residual recovery reports",
    )
    measurement_gap_plan = sub.add_parser(
        "measurement-gap-plan",
        help="Export external alignment measurement backfill checklist",
    )
    measurement_gap_plan.add_argument("--web-dir", default="web", help="Static web directory containing data/latest.json")
    measurement_gap_plan.add_argument("--reports-dir", default="reports", help="Report JSON directory used for candidate resolution")
    measurement_gap_plan.add_argument("--format", choices=["json", "text"], default="json")
    measurement_gap_plan.add_argument(
        "--candidate-source",
        choices=["eligible-reports", "source-report"],
        default="eligible-reports",
        help="Candidate resolver source: per-item date-eligible reports or latest source report",
    )
    measurement_gap_plan.add_argument(
        "--resolve-candidates",
        action="store_true",
        help="Resolve candidate measurement values without applying them",
    )
    measurement_gap_plan.add_argument(
        "--apply-candidates",
        action="store_true",
        help="Apply resolved candidate measurement values to matching recommendation training examples",
    )
    provider_gap_backfill = sub.add_parser(
        "provider-gap-severity-backfill-plan",
        help="Export provider gap severity hidden calibration backfill records",
    )
    provider_gap_backfill.add_argument("--web-dir", default="web", help="Static web directory containing data/latest.json")
    provider_gap_backfill.add_argument("--reports-dir", default="reports", help="Report JSON directory used for candidate resolution")
    provider_gap_backfill.add_argument("--format", choices=["json", "text"], default="json")
    provider_gap_backfill.add_argument(
        "--candidate-limit",
        type=int,
        default=0,
        help="Maximum queued provider gap severity records to export/apply; 0 means all queued records",
    )
    provider_gap_backfill.add_argument(
        "--apply-candidates",
        action="store_true",
        help="Apply queued provider gap severity values to matching recommendation training examples",
    )
    review_plan = sub.add_parser(
        "external-alignment-review-plan",
        help="Export pending external alignment review checklist",
    )
    review_plan.add_argument("--web-dir", default="web", help="Static web directory containing data/latest.json")
    review_plan.add_argument("--format", choices=["json", "text"], default="json")

    args = parser.parse_args(argv)
    if args.command == "privacy-scan":
        from .privacy import assert_public_assets_safe

        assert_public_assets_safe(Path(args.web_dir))
        print("public privacy scan passed")
        return 0
    if args.command == "quality-scan":
        from .quality import assert_public_snapshot_quality

        try:
            assert_public_snapshot_quality(Path(args.web_dir))
        except RuntimeError as exc:
            print(str(exc))
            return 2
        print("public snapshot quality scan passed")
        return 0
    if args.command == "coverage-gap-plan":
        try:
            result = build_coverage_gap_plan_export(
                Path(args.web_dir),
                reports_dir=Path(args.reports_dir),
                resolve_candidates=args.resolve_candidates or args.apply_candidates,
                candidate_source=args.candidate_source,
                candidate_queue=args.candidate_queue,
                candidate_limit=args.candidate_limit,
            )
            if args.materialize_recovery_training_examples:
                result["recovery_materialize_result"] = materialize_recovery_training_examples(
                    result.get("residual_recovery_plan") or {},
                    Path(args.reports_dir),
                )
            if args.materialize_recovery_feature_skeleton:
                result["recovery_feature_skeleton_result"] = materialize_recovery_feature_skeleton(
                    result.get("residual_recovery_plan") or {},
                    Path(args.reports_dir),
                )
            if args.attach_recovery_external_signals:
                result["recovery_external_signal_attach_result"] = attach_recovery_external_signals(
                    result.get("residual_recovery_plan") or {},
                    Path(args.reports_dir),
                    Path(args.attach_recovery_external_signals),
                )
            if args.apply_candidates:
                result["apply_result"] = apply_coverage_gap_candidate_backfills(result.get("candidate_items") or [])
        except RuntimeError as exc:
            print(str(exc))
            return 2
        if args.format == "text":
            print(format_coverage_gap_plan_export(result), end="")
        else:
            print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return 0
    if args.command == "measurement-gap-plan":
        try:
            result = build_measurement_gap_plan_export(
                Path(args.web_dir),
                reports_dir=Path(args.reports_dir),
                resolve_candidates=args.resolve_candidates or args.apply_candidates,
                candidate_source=args.candidate_source,
            )
            if args.apply_candidates:
                result["apply_result"] = apply_measurement_gap_candidate_backfills(result.get("backfill_items") or [])
        except RuntimeError as exc:
            print(str(exc))
            return 2
        if args.format == "text":
            print(format_measurement_gap_plan_export(result), end="")
        else:
            print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return 0
    if args.command == "provider-gap-severity-backfill-plan":
        try:
            result = build_provider_gap_severity_backfill_export(
                Path(args.web_dir),
                reports_dir=Path(args.reports_dir),
                candidate_limit=args.candidate_limit,
            )
            if args.apply_candidates:
                result["apply_result"] = apply_provider_gap_severity_candidate_backfills(result.get("backfill_items") or [])
        except RuntimeError as exc:
            print(str(exc))
            return 2
        if args.format == "text":
            print(format_provider_gap_severity_backfill_export(result), end="")
        else:
            print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return 0
    if args.command == "external-alignment-review-plan":
        try:
            result = build_external_alignment_review_plan_export(Path(args.web_dir))
        except RuntimeError as exc:
            print(str(exc))
            return 2
        if args.format == "text":
            print(format_external_alignment_review_plan_export(result), end="")
        else:
            print(json.dumps(result, indent=2, sort_keys=True, default=str))
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
    if args.command == "notify":
        return command_notify(args, config)
    if args.command == "backtest-signal":
        result = backtest_signal(conn, args.signal)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] in {"ok", "insufficient_data"} else 2
    return 2


def build_coverage_gap_plan_export(
    web_dir: Path,
    reports_dir: Path | None = None,
    resolve_candidates: bool = False,
    candidate_source: str = "source-report",
    candidate_queue: str = "priority",
    candidate_limit: int = 0,
) -> dict:
    snapshot_path = web_dir / "data" / "latest.json"
    if not snapshot_path.exists():
        raise RuntimeError(f"coverage gap plan export failed: missing {snapshot_path}")
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    backtest = payload.get("backtest") if isinstance(payload.get("backtest"), dict) else {}
    plan = backtest.get("pending_external_coverage_gap_plan") if isinstance(backtest, dict) else {}
    if not isinstance(plan, dict):
        plan = {}
    priority_rows = plan.get("priority_rows") if isinstance(plan.get("priority_rows"), list) else []
    rows = [coverage_gap_plan_export_row(row) for row in priority_rows if isinstance(row, dict)]
    source_report = None
    site = payload.get("site")
    if isinstance(site, dict):
        source_report = site.get("source_report")
    backfill_items = [coverage_gap_plan_backfill_item(row, source_report) for row in rows]
    residual_rows = [
        coverage_gap_plan_export_row(row)
        for row in (plan.get("residual_rows") if isinstance(plan.get("residual_rows"), list) else [])
        if isinstance(row, dict)
    ]
    residual_backfill_items = [coverage_gap_plan_backfill_item(row, source_report) for row in residual_rows]
    candidate_items = select_coverage_gap_candidate_items(
        backfill_items,
        residual_backfill_items,
        candidate_queue=candidate_queue,
        candidate_limit=candidate_limit,
    )
    residual_recovery_plan = build_residual_recovery_plan(
        plan.get("residual_required_observation_dates") or [],
        residual_backfill_items,
        reports_dir,
    )
    candidate_resolution = None
    if resolve_candidates:
        candidate_resolution = resolve_coverage_gap_candidates(
            candidate_items,
            snapshot_payload=payload,
            snapshot_path=snapshot_path,
            source_report=source_report,
            reports_dir=reports_dir,
            candidate_source=candidate_source,
        )
    result = {
        "version": COVERAGE_GAP_PLAN_EXPORT_VERSION,
        "status": "ready" if rows else "empty",
        "snapshot_path": str(snapshot_path),
        "source_report": source_report,
        "as_of": payload.get("as_of"),
        "session": payload.get("session"),
        "additional_external_coverage_needed": int(plan.get("additional_external_coverage_needed") or 0),
        "priority_gap_count": int(plan.get("priority_gap_count") or len(rows)),
        "priority_acceptance_check_count": int(
            plan.get("priority_acceptance_check_count") or measurement_acceptance_check_count(rows)
        ),
        "priority_open_acceptance_check_count": int(
            plan.get("priority_open_acceptance_check_count") or open_acceptance_check_count(rows)
        ),
        "priority_acceptance_check_status_counts": plan.get("priority_acceptance_check_status_counts") or {},
        "residual_gap_count": int(plan.get("residual_gap_count") or len(residual_rows)),
        "residual_gap_status": plan.get("residual_gap_status") or "none",
        "residual_ranking_version": plan.get("residual_ranking_version"),
        "residual_rank_limit": plan.get("residual_rank_limit"),
        "residual_hidden_gap_count": int(
            plan.get("residual_hidden_gap_count")
            if plan.get("residual_hidden_gap_count") is not None
            else max(0, int(plan.get("residual_gap_count") or len(residual_rows)) - len(residual_rows))
        ),
        "residual_required_observation_date_limit": plan.get("residual_required_observation_date_limit"),
        "residual_required_observation_dates": plan.get("residual_required_observation_dates") or [],
        "residual_recovery_plan": residual_recovery_plan,
        "minimum_external_long_horizon_required": plan.get("minimum_external_long_horizon_required"),
        "projected_external_long_horizon_count_after_priority_backfill": plan.get(
            "projected_external_long_horizon_count_after_priority_backfill"
        ),
        "projected_external_additional_needed_after_priority_backfill": plan.get(
            "projected_external_additional_needed_after_priority_backfill"
        ),
        "external_learning_ready_after_priority_backfill": bool(
            plan.get("external_learning_ready_after_priority_backfill")
        ),
        "projected_external_learning_ready_date_after_priority_backfill": plan.get(
            "projected_external_learning_ready_date_after_priority_backfill"
        ),
        "backfill_items": backfill_items,
        "residual_backfill_items": residual_backfill_items,
        "candidate_queue": candidate_queue,
        "candidate_limit": normalized_candidate_limit(candidate_limit),
        "candidate_item_count": len(candidate_items),
        "candidate_items": candidate_items,
        "priority_rows": rows,
        "residual_rows": residual_rows,
    }
    if candidate_resolution is not None:
        result["candidate_resolution"] = candidate_resolution
    return result


def coverage_gap_plan_export_row(row: dict) -> dict:
    return {key: row.get(key) for key in COVERAGE_GAP_PLAN_ROW_KEYS if key in row}


def coverage_gap_plan_backfill_item(row: dict, source_report: str | None) -> dict:
    checks = [check for check in row.get("external_coverage_acceptance_checks") or [] if isinstance(check, dict)]
    open_checks = [check for check in checks if check.get("status") != "passed"]
    return {
        "external_coverage_gap_id": row.get("external_coverage_gap_id"),
        "symbol": row.get("symbol"),
        "horizon": row.get("horizon"),
        "decision_as_of": row.get("as_of"),
        "due_date": row.get("due_date"),
        "source_report": source_report,
        "source_outcome_id": row.get("source_outcome_id"),
        "source_trial_id": row.get("source_trial_id"),
        "backfill_policy": row.get("external_coverage_backfill_policy"),
        "required_external_observation_date": row.get("required_external_observation_date"),
        "fields_to_backfill": row.get("minimum_external_fields_to_backfill") or [],
        "missing_external_fields": row.get("missing_external_fields") or [],
        "action": row.get("external_coverage_gap_action"),
        "acceptance_check_count": len(checks),
        "open_acceptance_check_count": len(open_checks),
        "open_acceptance_checks": open_checks,
    }


def build_measurement_gap_plan_export(
    web_dir: Path,
    reports_dir: Path | None = None,
    resolve_candidates: bool = False,
    candidate_source: str = "eligible-reports",
) -> dict:
    snapshot_path = web_dir / "data" / "latest.json"
    if not snapshot_path.exists():
        raise RuntimeError(f"measurement gap plan export failed: missing {snapshot_path}")
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    backtest = payload.get("backtest") if isinstance(payload.get("backtest"), dict) else {}
    plan = backtest.get("pending_external_alignment_measurement_gap_plan") if isinstance(backtest, dict) else {}
    if not isinstance(plan, dict):
        plan = {}
    queue = (
        backtest.get("pending_external_alignment_measurement_gap_queue")
        if isinstance(backtest.get("pending_external_alignment_measurement_gap_queue"), list)
        else []
    )
    rows = [measurement_gap_plan_export_row(row) for row in queue if isinstance(row, dict)]
    source_report = None
    site = payload.get("site")
    if isinstance(site, dict):
        source_report = site.get("source_report")
    backfill_items = [measurement_gap_plan_backfill_item(row, source_report) for row in rows]
    candidate_resolution = None
    if resolve_candidates:
        candidate_resolution = resolve_measurement_gap_candidates(
            backfill_items,
            snapshot_payload=payload,
            snapshot_path=snapshot_path,
            source_report=source_report,
            reports_dir=reports_dir,
            candidate_source=candidate_source,
        )
    result = {
        "version": MEASUREMENT_GAP_PLAN_EXPORT_VERSION,
        "status": "ready" if rows else "empty",
        "snapshot_path": str(snapshot_path),
        "source_report": source_report,
        "as_of": payload.get("as_of"),
        "session": payload.get("session"),
        "label_count": int(plan.get("label_count") or backtest.get("pending_external_alignment_measurement_gap_label_count") or 0),
        "work_item_count": int(plan.get("work_item_count") or backtest.get("pending_external_alignment_measurement_gap_item_count") or 0),
        "hidden_work_item_count": int(
            plan.get("hidden_work_item_count")
            if plan.get("hidden_work_item_count") is not None
            else backtest.get("pending_external_alignment_measurement_gap_hidden_item_count") or 0
        ),
        "queue_limit": int(plan.get("queue_limit") or backtest.get("pending_external_alignment_measurement_gap_queue_limit") or 0),
        "next_due_date": plan.get("next_due_date"),
        "next_due_label_count": int(plan.get("next_due_label_count") or 0),
        "next_due_work_item_count": int(plan.get("next_due_work_item_count") or 0),
        "next_due_field_counts": plan.get("next_due_field_counts") or {},
        "next_due_symbols": plan.get("next_due_symbols") or [],
        "next_due_horizons": plan.get("next_due_horizons") or [],
        "field_counts": plan.get("field_counts") or {},
        "due_dates": plan.get("due_dates") or [],
        "priority_acceptance_check_count": int(plan.get("priority_acceptance_check_count") or acceptance_check_count(rows)),
        "priority_open_acceptance_check_count": int(
            plan.get("priority_open_acceptance_check_count") or open_measurement_acceptance_check_count(rows)
        ),
        "priority_acceptance_check_status_counts": plan.get("priority_acceptance_check_status_counts") or {},
        "priority_symbols": plan.get("priority_symbols") or sorted({str(row.get("symbol") or "") for row in rows if row.get("symbol")}),
        "priority_rows": rows,
        "backfill_items": backfill_items,
    }
    if candidate_resolution is not None:
        result["candidate_resolution"] = candidate_resolution
    return result


def measurement_gap_plan_export_row(row: dict) -> dict:
    return {key: row.get(key) for key in MEASUREMENT_GAP_PLAN_ROW_KEYS if key in row}


def measurement_gap_plan_backfill_item(row: dict, source_report: str | None) -> dict:
    checks = [
        check
        for check in row.get("external_alignment_measurement_acceptance_checks") or []
        if isinstance(check, dict)
    ]
    open_checks = [check for check in checks if check.get("status") != "passed"]
    return {
        "external_alignment_measurement_gap_id": row.get("external_alignment_measurement_gap_id"),
        "external_alignment_review_id": row.get("external_alignment_review_id"),
        "symbol": row.get("symbol"),
        "horizon": row.get("horizon"),
        "decision_as_of": row.get("as_of"),
        "due_date": row.get("due_date"),
        "source_report": source_report,
        "source_outcome_id": row.get("source_outcome_id"),
        "source_trial_id": row.get("source_trial_id"),
        "backfill_policy": row.get("external_alignment_measurement_backfill_policy"),
        "fields_to_backfill": row.get("external_alignment_measurement_missing_fields") or [],
        "missing_measurement_fields": row.get("external_alignment_measurement_missing_fields") or [],
        "missing_label_count": int(row.get("external_alignment_measurement_missing_label_count") or 0),
        "action": row.get("external_alignment_measurement_gap_action"),
        "acceptance_check_count": len(checks),
        "open_acceptance_check_count": len(open_checks),
        "open_acceptance_checks": open_checks,
    }


def build_provider_gap_severity_backfill_export(
    web_dir: Path,
    reports_dir: Path | None = None,
    candidate_limit: int = 0,
) -> dict:
    snapshot_path = web_dir / "data" / "latest.json"
    if not snapshot_path.exists():
        raise RuntimeError(f"provider gap severity backfill export failed: missing {snapshot_path}")
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    backtest = payload.get("backtest") if isinstance(payload.get("backtest"), dict) else {}
    queue = (
        backtest.get("pending_external_provider_gap_severity_observation_gap_hidden_calibration_backfill_record_queue")
        if isinstance(
            backtest.get("pending_external_provider_gap_severity_observation_gap_hidden_calibration_backfill_record_queue"),
            list,
        )
        else []
    )
    limit = normalized_candidate_limit(candidate_limit)
    limited_queue = queue[:limit] if limit else queue
    items = [provider_gap_severity_backfill_item(row, reports_dir) for row in limited_queue if isinstance(row, dict)]
    ready_count = sum(1 for item in items if item.get("candidate_apply_status") == "ready")
    site = payload.get("site") if isinstance(payload.get("site"), dict) else {}
    return {
        "version": PROVIDER_GAP_SEVERITY_BACKFILL_PLAN_EXPORT_VERSION,
        "status": "ready" if items else "empty",
        "snapshot_path": str(snapshot_path),
        "source_report": site.get("source_report"),
        "as_of": payload.get("as_of"),
        "session": payload.get("session"),
        "record_count": int(
            backtest.get("pending_external_provider_gap_severity_observation_gap_hidden_calibration_backfill_record_count")
            or len(queue)
        ),
        "queue_limit": int(
            backtest.get("pending_external_provider_gap_severity_observation_gap_hidden_calibration_backfill_record_queue_limit")
            or 0
        ),
        "candidate_limit": limit,
        "candidate_item_count": len(items),
        "candidate_apply_ready_count": ready_count,
        "candidate_apply_blocked_count": len(items) - ready_count,
        "candidate_items": items,
        "backfill_items": items,
    }


def provider_gap_severity_backfill_item(row: dict, reports_dir: Path | None) -> dict:
    item = dict(row)
    source_path = provider_gap_severity_source_report_path(item, reports_dir)
    item["candidate_source"] = str(source_path) if source_path else ""
    source_trial_ids = provider_gap_source_trial_ids(item)
    if source_trial_ids and not item.get("source_trial_id"):
        item["source_trial_id"] = source_trial_ids[0]
    if not source_path:
        item["candidate_apply_status"] = "blocked"
        item.setdefault("candidate_block_reason", "source_report_missing")
    elif not source_path.exists():
        item["candidate_apply_status"] = "blocked"
        item.setdefault("candidate_block_reason", "candidate_source_not_found")
    elif not item.get("candidate_apply_status"):
        item["candidate_apply_status"] = "ready"
    return item


def provider_gap_severity_source_report_path(item: dict, reports_dir: Path | None) -> Path | None:
    source_report = str(item.get("source_report") or "")
    if not source_report:
        return None
    path = Path(source_report)
    if path.is_absolute():
        return path
    if reports_dir is not None:
        return reports_dir / path.name
    return path


def build_external_alignment_review_plan_export(web_dir: Path) -> dict:
    snapshot_path = web_dir / "data" / "latest.json"
    if not snapshot_path.exists():
        raise RuntimeError(f"external alignment review plan export failed: missing {snapshot_path}")
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    backtest = payload.get("backtest") if isinstance(payload.get("backtest"), dict) else {}
    queue = (
        backtest.get("pending_external_alignment_review_queue")
        if isinstance(backtest.get("pending_external_alignment_review_queue"), list)
        else []
    )
    due_dates = (
        backtest.get("pending_external_alignment_review_due_dates")
        if isinstance(backtest.get("pending_external_alignment_review_due_dates"), list)
        else []
    )
    acceptance = (
        backtest.get("pending_external_alignment_review_acceptance_summary")
        if isinstance(backtest.get("pending_external_alignment_review_acceptance_summary"), dict)
        else {}
    )
    source_report = None
    site = payload.get("site")
    if isinstance(site, dict):
        source_report = site.get("source_report")
    rows = [external_alignment_review_plan_row(row) for row in queue if isinstance(row, dict)]
    first_due = due_dates[0] if due_dates and isinstance(due_dates[0], dict) else {}
    next_due_date = acceptance.get("next_open_check_due_date") or first_due.get("due_date")
    next_due_focus_counts = acceptance.get("next_open_check_due_focus_counts") or first_due.get("focus_counts") or {}
    next_due_action_counts = acceptance.get("next_open_check_due_learning_action_counts") or first_due.get("learning_action_counts") or {}
    next_due_missing_counts = acceptance.get("next_open_check_due_measurement_missing_field_counts") or {}
    open_check_counts = acceptance.get("open_check_counts") or {}
    result = {
        "version": EXTERNAL_ALIGNMENT_REVIEW_PLAN_EXPORT_VERSION,
        "status": "ready" if rows else "empty",
        "snapshot_path": str(snapshot_path),
        "source_report": source_report,
        "as_of": payload.get("as_of"),
        "session": payload.get("session"),
        "label_count": int(backtest.get("pending_external_alignment_review_count") or acceptance.get("label_count") or 0),
        "work_item_count": int(backtest.get("pending_external_alignment_review_item_count") or acceptance.get("work_item_count") or 0),
        "hidden_work_item_count": int(backtest.get("pending_external_alignment_review_hidden_item_count") or 0),
        "queue_limit": int(backtest.get("pending_external_alignment_review_queue_limit") or len(rows)),
        "visible_work_item_count": len(rows),
        "acceptance_check_count": int(acceptance.get("check_count") or review_acceptance_check_count(rows)),
        "open_acceptance_check_count": int(acceptance.get("open_check_count") or open_review_acceptance_check_count(rows)),
        "open_label_count": int(acceptance.get("open_label_count") or 0),
        "metadata_ready_work_item_count": int(acceptance.get("metadata_ready_work_item_count") or 0),
        "open_acceptance_check_counts": open_check_counts,
        "next_due_date": next_due_date,
        "next_due_label_count": int(acceptance.get("next_open_check_due_label_count") or first_due.get("label_count") or 0),
        "next_due_work_item_count": int(acceptance.get("next_open_check_due_work_item_count") or first_due.get("work_item_count") or 0),
        "next_due_visible_work_item_count": int(acceptance.get("next_open_check_due_visible_work_item_count") or 0),
        "next_due_hidden_work_item_count": int(acceptance.get("next_open_check_due_hidden_work_item_count") or 0),
        "next_due_symbols": acceptance.get("next_open_check_due_symbols") or first_due.get("symbols") or [],
        "next_due_horizons": acceptance.get("next_open_check_due_horizons") or first_due.get("horizons") or [],
        "next_due_focus_counts": next_due_focus_counts,
        "next_due_learning_action_counts": next_due_action_counts,
        "next_due_measurement_missing_field_counts": next_due_missing_counts,
        "maturity_blocker_count": int(open_check_counts.get("matured_label_available") or 0),
        "review_bottleneck": external_alignment_review_bottleneck(open_check_counts),
        "maturity_test_target_counts": review_maturity_test_target_counts(rows),
        "maturity_test_primary_metric_counts": review_maturity_test_primary_metric_counts(rows),
        "maturity_test_status_counts": review_maturity_test_status_counts(rows),
        "maturity_test_blocker_counts": review_maturity_test_blocker_counts(rows),
        "maturity_test_result_counts": review_maturity_test_result_counts(rows),
        "due_dates": due_dates,
        "priority_rows": rows,
    }
    return result


def external_alignment_review_plan_row(row: dict) -> dict:
    compact = {key: row.get(key) for key in EXTERNAL_ALIGNMENT_REVIEW_PLAN_ROW_KEYS if key in row}
    checks = [
        check
        for check in row.get("external_alignment_review_acceptance_checks") or []
        if isinstance(check, dict)
    ]
    open_checks = [check for check in checks if check.get("status") != "passed"]
    measurement = row.get("external_alignment_review_measurement_plan")
    if not isinstance(measurement, dict):
        measurement = {}
    compact["acceptance_check_count"] = len(checks)
    compact["open_acceptance_check_count"] = len(open_checks)
    compact["open_acceptance_checks"] = open_checks
    compact["measurement_summary"] = measurement.get("summary")
    compact["measurement_missing_fields"] = measurement.get("missing_measurement_fields") or []
    compact["engine_direction"] = measurement.get("engine_direction")
    compact["external_signal_direction"] = measurement.get("external_signal_direction")
    compact["coverage_adjusted_external_signal_score"] = measurement.get("coverage_adjusted_external_signal_score")
    compact["risk_adjusted_expected_return"] = measurement.get("risk_adjusted_expected_return")
    compact["review_question"] = external_alignment_review_question(row)
    maturity_plan = external_alignment_maturity_test_plan(row)
    maturity_evaluation = external_alignment_maturity_test_evaluation(compact, maturity_plan)
    compact["maturity_test_plan"] = maturity_plan
    compact["maturity_test_status"] = maturity_evaluation["status"]
    compact["maturity_test_blockers"] = maturity_evaluation.get("blockers") or []
    compact["maturity_test_result"] = maturity_evaluation.get("result")
    return compact


def external_alignment_maturity_test_plan(row: dict) -> dict:
    focus = str(row.get("external_alignment_review_focus") or "")
    measurement = row.get("external_alignment_review_measurement_plan")
    if not isinstance(measurement, dict):
        measurement = {}
    base = {
        "version": EXTERNAL_ALIGNMENT_MATURITY_TEST_VERSION,
        "status_required": "complete",
        "required_outcome_fields": [
            "status",
            "decision_forward_return_pct",
            "raw_forward_return_pct",
            "hit",
            "expected_vs_realized_error",
        ],
        "realized_direction_rule": "positive if return > 0, negative if return < 0, flat if return == 0",
        "engine_direction": measurement.get("engine_direction"),
        "external_signal_direction": measurement.get("external_signal_direction"),
        "risk_adjusted_expected_return": measurement.get("risk_adjusted_expected_return"),
        "coverage_adjusted_external_signal_score": measurement.get("coverage_adjusted_external_signal_score"),
    }
    if focus == "external_disagreement":
        base.update(
            {
                "primary_metric": "decision_forward_return_pct",
                "calibration_target": "external_signal_trust_vs_engine_direction",
                "hypothesis": "Engine direction and external signal disagree; realized decision return determines which side earned trust.",
                "decision_rules": [
                    {
                        "outcome": "engine_validated",
                        "condition": "decision_forward_return_pct > 0",
                        "learning_update": "Do not increase trust in this external signal bucket from this label.",
                    },
                    {
                        "outcome": "external_signal_validated",
                        "condition": "decision_forward_return_pct < 0",
                        "learning_update": "Increase scrutiny of the engine signal family and consider more external-signal weight for this bucket.",
                    },
                    {
                        "outcome": "inconclusive",
                        "condition": "decision_forward_return_pct == 0 or outcome missing",
                        "learning_update": "Leave calibration unchanged; keep the label for sample-size accounting.",
                    },
                ],
            }
        )
    elif focus == "missed_external_signal":
        base.update(
            {
                "primary_metric": "raw_forward_return_pct",
                "calibration_target": "external_signal_promotion_threshold",
                "hypothesis": "Engine stayed neutral while external signal was directional; realized raw return tests whether the signal should have promoted size or timing.",
                "decision_rules": [
                    {
                        "outcome": "external_signal_would_have_helped",
                        "condition": "raw_forward_return_pct direction matches external_signal_direction",
                        "learning_update": "Lower the promotion threshold or raise rank contribution for similar covered external signals.",
                    },
                    {
                        "outcome": "neutral_engine_was_protective",
                        "condition": "raw_forward_return_pct direction opposes external_signal_direction",
                        "learning_update": "Raise the promotion threshold or require stronger corroboration for this external signal bucket.",
                    },
                    {
                        "outcome": "inconclusive",
                        "condition": "raw_forward_return_pct == 0 or outcome missing",
                        "learning_update": "Leave promotion threshold unchanged.",
                    },
                ],
            }
        )
    elif focus == "internal_signal_only":
        base.update(
            {
                "primary_metric": "decision_forward_return_pct",
                "calibration_target": "internal_signal_validation_without_external_confirmation",
                "hypothesis": "Engine acted without external confirmation; realized decision return tests whether internal signals carried the label.",
                "decision_rules": [
                    {
                        "outcome": "internal_signal_validated",
                        "condition": "decision_forward_return_pct > 0",
                        "learning_update": "Preserve room for internal-only decisions when source families match this label.",
                    },
                    {
                        "outcome": "external_neutral_warning_validated",
                        "condition": "decision_forward_return_pct < 0",
                        "learning_update": "Require more external corroboration or stronger internal evidence for this bucket.",
                    },
                    {
                        "outcome": "inconclusive",
                        "condition": "decision_forward_return_pct == 0 or outcome missing",
                        "learning_update": "Leave internal-only calibration unchanged.",
                    },
                ],
            }
        )
    else:
        base.update(
            {
                "primary_metric": "decision_forward_return_pct",
                "calibration_target": "external_alignment_bucket_calibration",
                "hypothesis": "Realized return should be assigned to the observed alignment bucket before changing trust.",
                "decision_rules": [
                    {
                        "outcome": "positive_label",
                        "condition": "decision_forward_return_pct > 0",
                        "learning_update": "Treat as supportive evidence for this alignment bucket.",
                    },
                    {
                        "outcome": "negative_label",
                        "condition": "decision_forward_return_pct < 0",
                        "learning_update": "Treat as adverse evidence for this alignment bucket.",
                    },
                ],
            }
        )
    return base


def review_maturity_test_target_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        plan = row.get("maturity_test_plan") if isinstance(row, dict) else {}
        if not isinstance(plan, dict):
            continue
        target = str(plan.get("calibration_target") or "")
        if not target:
            continue
        counts[target] = counts.get(target, 0) + 1
    return dict(sorted(counts.items()))


def review_maturity_test_primary_metric_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        plan = row.get("maturity_test_plan") if isinstance(row, dict) else {}
        if not isinstance(plan, dict):
            continue
        metric = str(plan.get("primary_metric") or "")
        if not metric:
            continue
        counts[metric] = counts.get(metric, 0) + 1
    return dict(sorted(counts.items()))


def review_maturity_test_status_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("maturity_test_status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def review_maturity_test_result_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        result = row.get("maturity_test_result")
        if not isinstance(result, dict):
            continue
        outcome = str(result.get("outcome") or "")
        if not outcome:
            continue
        counts[outcome] = counts.get(outcome, 0) + 1
    return dict(sorted(counts.items()))


def review_maturity_test_blocker_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for blocker in row.get("maturity_test_blockers") or []:
            blocker_name = str(blocker or "")
            if not blocker_name:
                continue
            counts[blocker_name] = counts.get(blocker_name, 0) + 1
    return dict(sorted(counts.items()))


def external_alignment_maturity_test_evaluation(row: dict, plan: dict) -> dict:
    required_status = str(plan.get("status_required") or "complete")
    if str(row.get("status") or "") != required_status:
        return {"status": "blocked", "blockers": ["matured_label_available"]}
    missing = [
        field
        for field in plan.get("required_outcome_fields") or []
        if field not in row or row.get(field) is None
    ]
    if missing:
        return {"status": "blocked", "blockers": [f"missing_{field}" for field in missing]}
    metric = str(plan.get("primary_metric") or "")
    value = optional_number(row.get(metric))
    if not metric or value is None:
        return {"status": "blocked", "blockers": ["missing_primary_metric"]}
    outcome = maturity_test_outcome(row, plan, value)
    return {
        "status": "classified",
        "result": {
            "outcome": outcome,
            "primary_metric": metric,
            "primary_metric_value": value,
            "learning_update": maturity_test_learning_update(plan, outcome),
            "calibration_target": plan.get("calibration_target"),
        },
    }


def maturity_test_outcome(row: dict, plan: dict, metric_value: float) -> str:
    target = str(plan.get("calibration_target") or "")
    if target == "external_signal_trust_vs_engine_direction":
        if metric_value > 0:
            return "engine_validated"
        if metric_value < 0:
            return "external_signal_validated"
        return "inconclusive"
    if target == "external_signal_promotion_threshold":
        external_direction = str(plan.get("external_signal_direction") or row.get("external_signal_direction") or "")
        realized_direction = direction_label_for_value(metric_value)
        if realized_direction == "flat" or external_direction not in {"positive", "negative"}:
            return "inconclusive"
        if realized_direction == external_direction:
            return "external_signal_would_have_helped"
        return "neutral_engine_was_protective"
    if target == "internal_signal_validation_without_external_confirmation":
        if metric_value > 0:
            return "internal_signal_validated"
        if metric_value < 0:
            return "external_neutral_warning_validated"
        return "inconclusive"
    if metric_value > 0:
        return "positive_label"
    if metric_value < 0:
        return "negative_label"
    return "inconclusive"


def maturity_test_learning_update(plan: dict, outcome: str) -> str:
    for rule in plan.get("decision_rules") or []:
        if isinstance(rule, dict) and rule.get("outcome") == outcome:
            return str(rule.get("learning_update") or "")
    return ""


def direction_label_for_value(value: float) -> str:
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "flat"


def optional_number(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def external_alignment_review_question(row: dict) -> str:
    focus = str(row.get("external_alignment_review_focus") or "")
    if focus == "external_disagreement":
        return "When the label matures, did realized direction validate the engine or the external signal?"
    if focus == "missed_external_signal":
        return "When the label matures, would the external signal have improved sizing or timing?"
    if focus == "internal_signal_only":
        return "When the label matures, did internal signal families carry the return without external confirmation?"
    return "When the label matures, should this alignment bucket change calibration or trust?"


def external_alignment_review_bottleneck(open_check_counts: dict) -> str:
    if not open_check_counts:
        return "ready_for_review"
    non_maturity = {
        str(key): value
        for key, value in open_check_counts.items()
        if key != "matured_label_available" and value
    }
    if non_maturity:
        return "metadata_or_measurement_gap"
    if open_check_counts.get("matured_label_available"):
        return "awaiting_label_maturity"
    return "ready_for_review"


def review_acceptance_check_count(rows: list[dict]) -> int:
    return sum(int(row.get("acceptance_check_count") or 0) for row in rows if isinstance(row, dict))


def open_review_acceptance_check_count(rows: list[dict]) -> int:
    return sum(int(row.get("open_acceptance_check_count") or 0) for row in rows if isinstance(row, dict))


def resolve_measurement_gap_candidates(
    backfill_items: list[dict],
    snapshot_payload: dict,
    snapshot_path: Path,
    source_report: str | None,
    reports_dir: Path | None,
    candidate_source: str = "eligible-reports",
) -> dict:
    if candidate_source == "source-report":
        source_payload = snapshot_payload
        source_path = snapshot_path
        source_status = "snapshot"
        if source_report and reports_dir is not None:
            report_path = reports_dir / source_report
            if report_path.exists():
                source_payload = json.loads(report_path.read_text(encoding="utf-8"))
                source_path = report_path
                source_status = "source_report"
            else:
                source_status = "source_report_missing"
        by_symbol = measurement_candidate_rows_by_symbol(source_payload)
        for item in backfill_items:
            candidate_row = select_measurement_candidate_row(item, by_symbol.get(str(item.get("symbol") or "").upper()) or [])
            resolve_measurement_gap_candidate_item(item, candidate_row, source_path, source_payload.get("as_of"))
            item["candidate_source_policy"] = "source-report"
        return measurement_candidate_resolution_summary(backfill_items, source_status, candidate_source, str(source_path))
    return resolve_measurement_gap_candidates_from_eligible_reports(backfill_items, reports_dir)


def resolve_measurement_gap_candidates_from_eligible_reports(backfill_items: list[dict], reports_dir: Path | None) -> dict:
    report_candidates = measurement_gap_report_candidates(reports_dir)
    report_inventory = coverage_gap_report_inventory(reports_dir)
    for item in backfill_items:
        item.update(eligible_report_search_diagnostics(item, report_candidates, report_inventory))
        candidate = select_measurement_eligible_report_candidate(item, report_candidates)
        if candidate:
            item["candidate_report_search_status"] = "eligible_report"
            source_path = candidate["path"]
            source_payload = candidate["payload"]
            candidate_row = candidate["row"]
            source_as_of = candidate["as_of"]
        else:
            item["candidate_report_search_status"] = "eligible_report_missing"
            source_path = reports_dir or Path("reports")
            source_payload = {}
            candidate_row = None
            source_as_of = None
        resolve_measurement_gap_candidate_item(item, candidate_row, source_path, source_as_of)
        item["candidate_source_policy"] = "eligible-reports"
        item["candidate_report_search_status"] = item.get("candidate_report_search_status")
    summary = measurement_candidate_resolution_summary(
        backfill_items,
        "eligible_reports",
        "eligible-reports",
        "per-item eligible report",
    )
    summary["candidate_report_count"] = len(report_candidates)
    summary["candidate_raw_report_count"] = len(report_inventory)
    summary["candidate_report_search_status_counts"] = candidate_value_counts(backfill_items, "candidate_report_search_status")
    summary["candidate_report_search_reason_counts"] = candidate_value_counts(backfill_items, "candidate_report_search_reason")
    return summary


def measurement_candidate_resolution_summary(
    backfill_items: list[dict],
    status: str,
    candidate_source_policy: str,
    candidate_source: str,
) -> dict:
    ready_count = sum(1 for item in backfill_items if item.get("candidate_resolution_status") == "ready")
    apply_ready_count = sum(1 for item in backfill_items if item.get("candidate_apply_status") == "ready")
    acceptance_counts = candidate_acceptance_status_counts(backfill_items)
    return {
        "status": status,
        "candidate_source_policy": candidate_source_policy,
        "candidate_source": candidate_source,
        "candidate_item_count": len(backfill_items),
        "candidate_ready_count": ready_count,
        "candidate_unresolved_count": len(backfill_items) - ready_count,
        "candidate_apply_ready_count": apply_ready_count,
        "candidate_apply_blocked_count": len(backfill_items) - apply_ready_count,
        "candidate_resolution_status_counts": candidate_value_counts(backfill_items, "candidate_resolution_status"),
        "candidate_apply_status_counts": candidate_value_counts(backfill_items, "candidate_apply_status"),
        "candidate_source_section_counts": candidate_value_counts(backfill_items, "candidate_source_section"),
        "candidate_derivation_counts": candidate_present_value_counts(backfill_items, "candidate_derivation"),
        "candidate_missing_required_field_counts": candidate_missing_required_field_counts(backfill_items),
        "candidate_acceptance_check_count": sum(acceptance_counts.values()),
        "candidate_acceptance_passed_count": acceptance_counts.get("passed", 0),
        "candidate_acceptance_failed_count": acceptance_counts.get("failed", 0),
        "candidate_acceptance_status_counts": acceptance_counts,
        "candidate_failed_acceptance_check_counts": candidate_failed_acceptance_check_counts(backfill_items),
    }


def measurement_gap_report_candidates(reports_dir: Path | None) -> list[dict]:
    if reports_dir is None or not reports_dir.exists():
        return []
    candidates = []
    for path in sorted(reports_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        as_of = str(payload.get("as_of") or "")[:10]
        if not as_of:
            continue
        by_symbol = measurement_candidate_rows_by_symbol(payload)
        if not by_symbol:
            continue
        training_example_ids = {
            str(example.get("example_id") or "")
            for example in payload.get("recommendation_training_examples") or []
            if isinstance(example, dict) and example.get("example_id")
        }
        candidates.append(
            {
                "path": path,
                "payload": payload,
                "as_of": as_of,
                "session": str(payload.get("session") or ""),
                "by_symbol": by_symbol,
                "training_example_ids": training_example_ids,
            }
        )
    return sorted(candidates, key=report_candidate_sort_key)


def measurement_candidate_rows_by_symbol(payload: dict) -> dict[str, list[dict]]:
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for row in measurement_candidate_rows(payload):
        symbol = str(row.get("symbol") or "").upper()
        if symbol:
            by_symbol[symbol].append(row)
    return by_symbol


def measurement_candidate_rows(payload: dict) -> list[dict]:
    sections = [
        ("recommendation_training_examples", payload.get("recommendation_training_examples") or []),
        ("approval_tickets", payload.get("approval_tickets") or []),
        ("portfolio_benchmark.action_queue", (payload.get("portfolio_benchmark") or {}).get("action_queue") or []),
        ("engine.ranked_candidates", (payload.get("engine") or {}).get("ranked_candidates") or []),
        ("research_book.items", (payload.get("research_book") or {}).get("items") or []),
        ("derived_feature_matrix.research_item", measurement_feature_matrix_candidate_rows(payload)),
    ]
    rows = []
    for section, values in sections:
        for index, value in enumerate(values if isinstance(values, list) else []):
            if not isinstance(value, dict) or not value.get("symbol"):
                continue
            row = dict(value)
            row["_candidate_section"] = section
            row["_candidate_index"] = index
            rows.append(row)
    return rows


def measurement_feature_matrix_candidate_rows(payload: dict) -> list[dict]:
    feature_matrix = payload.get("feature_matrix") if isinstance(payload.get("feature_matrix"), dict) else {}
    feature_rows = feature_matrix.get("rows") if isinstance(feature_matrix.get("rows"), list) else []
    as_of = parse_date(payload.get("as_of"))
    if not feature_rows or not as_of:
        return []
    cards = payload.get("decision_cards") if isinstance(payload.get("decision_cards"), list) else []
    if not cards:
        signal_synthesis = payload.get("signal_synthesis") if isinstance(payload.get("signal_synthesis"), dict) else {}
        cards = signal_synthesis.get("cards") if isinstance(signal_synthesis.get("cards"), list) else []
    macro = payload.get("macro") if isinstance(payload.get("macro"), dict) else {}

    from .research import build_research_book

    research_book = build_research_book(
        as_of,
        {
            "version": feature_matrix.get("version", ""),
            "rows": feature_rows,
        },
        cards,
        macro,
    )
    rows = []
    for item in research_book.get("items") or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row["candidate_derivation"] = "research_item_from_feature_matrix"
        row["candidate_derivation_policy"] = "decision_time_feature_matrix_only"
        row["candidate_feature_matrix_version"] = feature_matrix.get("version", "")
        rows.append(row)
    return rows


def select_measurement_eligible_report_candidate(item: dict, report_candidates: list[dict]) -> dict | None:
    required_date = str(item.get("decision_as_of") or "")[:10]
    symbol = str(item.get("symbol") or "").upper()
    if not required_date or not symbol:
        return None
    eligible = []
    for candidate in report_candidates:
        if str(candidate.get("as_of") or "")[:10] > required_date:
            continue
        candidate_row = select_measurement_candidate_row(item, candidate.get("by_symbol", {}).get(symbol) or [])
        if candidate_row:
            eligible.append({**candidate, "row": candidate_row})
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda candidate: (
            required_measurement_field_count(item, candidate["row"]),
            measurement_source_trial_match(item, candidate["row"]),
            measurement_candidate_section_rank(candidate["row"]),
            report_candidate_sort_key(candidate),
        ),
    )


def select_measurement_candidate_row(item: dict, rows: list[dict]) -> dict | None:
    if not rows:
        return None
    return max(
        rows,
        key=lambda row: (
            required_measurement_field_count(item, row),
            measurement_source_trial_match(item, row),
            measurement_candidate_section_rank(row),
        ),
    )


def measurement_source_trial_match(item: dict, row: dict) -> int:
    source_trial_id = str(item.get("source_trial_id") or "")
    row_ids = {
        str(row.get("example_id") or ""),
        str(row.get("trial_id") or ""),
        str(row.get("ticket_id") or ""),
        str(row.get("id") or ""),
    }
    return 1 if source_trial_id and source_trial_id in row_ids else 0


def required_measurement_field_count(item: dict, row: dict | None) -> int:
    if not row:
        return 0
    return sum(
        1 for field in unique_field_names(item.get("fields_to_backfill") or [])
        if backfill_field_present(field, row.get(field))
    )


def measurement_candidate_section_rank(row: dict) -> int:
    return {
        "recommendation_training_examples": 5,
        "approval_tickets": 4,
        "portfolio_benchmark.action_queue": 3,
        "engine.ranked_candidates": 2,
        "research_book.items": 1,
        "derived_feature_matrix.research_item": 0,
    }.get(str(row.get("_candidate_section") or ""), 0)


def resolve_measurement_gap_candidate_item(
    item: dict,
    candidate_row: dict | None,
    source_path: Path,
    source_as_of: str | None,
) -> None:
    fields = unique_field_names(
        (item.get("fields_to_backfill") or [])
        + (item.get("missing_measurement_fields") or [])
        + list(MEASUREMENT_GAP_CANDIDATE_VALUE_KEYS)
    )
    required_fields = unique_field_names(item.get("fields_to_backfill") or [])
    values = {}
    missing_fields = []
    missing_required = []
    if not candidate_row:
        missing_fields = fields
        missing_required = required_fields
    else:
        for field in fields:
            value = candidate_row.get(field)
            if backfill_field_present(field, value):
                values[field] = value
            else:
                missing_fields.append(field)
        missing_required = [field for field in required_fields if field not in values]
    item["candidate_source"] = str(source_path)
    item["candidate_source_as_of"] = source_as_of
    item["candidate_symbol_found"] = bool(candidate_row)
    item["candidate_source_section"] = candidate_row.get("_candidate_section") if candidate_row else None
    item["candidate_derivation"] = candidate_row.get("candidate_derivation") if candidate_row else None
    item["candidate_derivation_policy"] = candidate_row.get("candidate_derivation_policy") if candidate_row else None
    item["candidate_feature_matrix_version"] = candidate_row.get("candidate_feature_matrix_version") if candidate_row else None
    item["candidate_backfill_values"] = values
    item["candidate_missing_fields"] = missing_fields
    item["candidate_missing_required_fields"] = missing_required
    item["candidate_resolution_status"] = "ready" if not missing_required else "incomplete"
    item["candidate_acceptance_checks"] = candidate_acceptance_checks(item)
    item["candidate_acceptance_status_counts"] = candidate_acceptance_status_counts([item])
    item["candidate_apply_status"] = (
        "ready"
        if item["candidate_resolution_status"] == "ready"
        and item["candidate_acceptance_status_counts"].get("failed", 0) == 0
        else "blocked"
    )


def select_coverage_gap_candidate_items(
    priority_items: list[dict],
    residual_items: list[dict],
    candidate_queue: str = "priority",
    candidate_limit: int = 0,
) -> list[dict]:
    if candidate_queue == "residual":
        selected = residual_items
    elif candidate_queue == "all":
        selected = priority_items + residual_items
    else:
        selected = priority_items
    deduped = dedupe_coverage_gap_candidate_items_by_trial(selected)
    limit = normalized_candidate_limit(candidate_limit)
    return deduped[:limit] if limit else deduped


def dedupe_coverage_gap_candidate_items_by_trial(items: list[dict]) -> list[dict]:
    seen = set()
    selected = []
    for item in items:
        key = str(item.get("source_trial_id") or item.get("external_coverage_gap_id") or len(selected))
        if key in seen:
            continue
        seen.add(key)
        selected.append(item)
    return selected


def normalized_candidate_limit(candidate_limit: int) -> int:
    return max(0, int(candidate_limit or 0))


def build_residual_recovery_plan(required_date_rows: list[dict], residual_items: list[dict], reports_dir: Path | None) -> dict:
    inventory = coverage_gap_report_inventory(reports_dir)
    feature_candidates = coverage_gap_report_candidates(reports_dir)
    items = []
    for row in required_date_rows:
        if not isinstance(row, dict):
            continue
        required_date = str(row.get("required_external_observation_date") or "")[:10]
        if not required_date:
            continue
        exact_reports = [report for report in inventory if str(report.get("as_of") or "")[:10] == required_date]
        eligible_feature_reports = [
            report for report in feature_candidates if str(report.get("as_of") or "")[:10] <= required_date
        ]
        affected_items = [
            item for item in residual_items
            if str(item.get("required_external_observation_date") or item.get("decision_as_of") or "")[:10] == required_date
        ]
        items.append(
            {
                "required_external_observation_date": required_date,
                "status": residual_recovery_status(exact_reports, eligible_feature_reports),
                "gap_count": int(row.get("gap_count") or len(affected_items)),
                "source_trial_count": int(row.get("source_trial_count") or len(unique_nonempty_values(affected_items, "source_trial_id"))),
                "symbol_count": int(row.get("symbol_count") or len(unique_nonempty_values(affected_items, "symbol"))),
                "symbols": row.get("symbols") or unique_nonempty_values(affected_items, "symbol")[:12],
                "visible_candidate_count": len(affected_items),
                "exact_report_count": len(exact_reports),
                "eligible_feature_report_count": len(eligible_feature_reports),
                "exact_reports": compact_report_inventory_rows(exact_reports),
                "missing_sections": residual_recovery_missing_sections(exact_reports),
                "recovery_action": residual_recovery_action(exact_reports, eligible_feature_reports),
            }
        )
    blocked = [item for item in items if item.get("status") != "ready"]
    return {
        "status": "blocked" if blocked else "ready" if items else "empty",
        "item_count": len(items),
        "blocked_item_count": len(blocked),
        "items": items,
    }


def residual_recovery_status(exact_reports: list[dict], eligible_feature_reports: list[dict]) -> str:
    if not exact_reports:
        return "missing_required_date_report"
    if not any(int(report.get("feature_row_count") or 0) > 0 for report in exact_reports):
        return "required_date_reports_missing_feature_matrix"
    if not any(int(report.get("training_example_count") or 0) > 0 for report in exact_reports):
        return "required_date_reports_missing_training_examples"
    if not any(bool(report.get("has_external_signals")) for report in exact_reports):
        return "required_date_reports_missing_external_signals"
    if not eligible_feature_reports:
        return "eligible_feature_reports_missing"
    return "ready"


def residual_recovery_missing_sections(exact_reports: list[dict]) -> list[str]:
    if not exact_reports:
        return ["required_date_report"]
    missing = set()
    if not any(int(report.get("feature_row_count") or 0) > 0 for report in exact_reports):
        missing.add("feature_matrix.rows")
    if not any(int(report.get("training_example_count") or 0) > 0 for report in exact_reports):
        missing.add("recommendation_training_examples")
    if not any(bool(report.get("has_external_signals")) for report in exact_reports):
        missing.add("external_signals")
    return sorted(missing)


def residual_recovery_action(exact_reports: list[dict], eligible_feature_reports: list[dict]) -> str:
    if not exact_reports:
        return "Regenerate the required-date report from decision-time inputs before attempting residual backfills."
    missing = ", ".join(residual_recovery_missing_sections(exact_reports))
    if missing:
        return f"Enrich the required-date report with {missing} captured no later than the required date."
    if not eligible_feature_reports:
        return "Rebuild feature_matrix.rows for the required-date report before resolving residual candidates."
    return "Resolve residual candidates from the eligible feature report and apply only apply-ready items."


def unique_nonempty_values(items: list[dict], key: str) -> list[str]:
    return sorted({str(item.get(key) or "") for item in items if item.get(key)})


def materialize_recovery_training_examples(recovery_plan: dict, reports_dir: Path | None) -> dict:
    if not reports_dir:
        return {"status": "blocked", "reason": "missing_reports_dir", "report_count": 0, "example_count": 0}
    items = recovery_plan.get("items") if isinstance(recovery_plan, dict) else []
    if not isinstance(items, list) or not items:
        return {"status": "empty", "report_count": 0, "example_count": 0}
    report_results = []
    example_count = 0
    skipped_count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        for report in item.get("exact_reports") or []:
            if not isinstance(report, dict):
                continue
            path = Path(str(report.get("report") or ""))
            if not path.is_absolute():
                path = reports_dir / path.name
            if not path.exists():
                report_results.append({"report": str(path), "status": "missing", "example_count": 0})
                skipped_count += 1
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            existing = payload.get("recommendation_training_examples")
            if isinstance(existing, list) and existing:
                report_results.append({"report": str(path), "status": "skipped_existing_examples", "example_count": len(existing)})
                skipped_count += 1
                continue
            examples = legacy_action_training_examples(payload)
            if not examples:
                report_results.append({"report": str(path), "status": "skipped_no_legacy_actions", "example_count": 0})
                skipped_count += 1
                continue
            payload["recommendation_training_examples"] = examples
            payload["legacy_recovery"] = {
                "version": COVERAGE_GAP_PLAN_EXPORT_VERSION,
                "action": "materialize_recovery_training_examples",
                "source": "portfolio_benchmark.action_queue",
                "example_count": len(examples),
            }
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            report_results.append({"report": str(path), "status": "materialized", "example_count": len(examples)})
            example_count += len(examples)
    return {
        "status": "materialized" if example_count else "skipped",
        "report_count": len(report_results),
        "materialized_report_count": sum(1 for row in report_results if row.get("status") == "materialized"),
        "skipped_report_count": skipped_count,
        "example_count": example_count,
        "reports": report_results,
    }


def materialize_recovery_feature_skeleton(recovery_plan: dict, reports_dir: Path | None) -> dict:
    if not reports_dir:
        return {"status": "blocked", "reason": "missing_reports_dir", "report_count": 0, "feature_row_count": 0}
    items = recovery_plan.get("items") if isinstance(recovery_plan, dict) else []
    if not isinstance(items, list) or not items:
        return {"status": "empty", "report_count": 0, "feature_row_count": 0}
    report_results = []
    feature_row_count = 0
    skipped_count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        for report in item.get("exact_reports") or []:
            if not isinstance(report, dict):
                continue
            path = Path(str(report.get("report") or ""))
            if not path.is_absolute():
                path = reports_dir / path.name
            if not path.exists():
                report_results.append({"report": str(path), "status": "missing", "feature_row_count": 0})
                skipped_count += 1
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            existing_rows = ((payload.get("feature_matrix") or {}).get("rows") or []) if isinstance(payload.get("feature_matrix"), dict) else []
            if existing_rows:
                report_results.append({"report": str(path), "status": "skipped_existing_feature_rows", "feature_row_count": len(existing_rows)})
                skipped_count += 1
                continue
            rows = legacy_feature_skeleton_rows(payload)
            if not rows:
                report_results.append({"report": str(path), "status": "skipped_no_legacy_actions", "feature_row_count": 0})
                skipped_count += 1
                continue
            payload["feature_matrix"] = {
                "version": FEATURE_MATRIX_VERSION,
                "model_policy_version": MODEL_POLICY_VERSION,
                "as_of": str(payload.get("as_of") or ""),
                "objective": "legacy_recovery_non_external_feature_skeleton",
                "horizons": ["3m", "6m", "12m"],
                "feature_count": len(rows),
                "rows": rows,
            }
            recovery = dict(payload.get("legacy_recovery") or {})
            recovery.update(
                {
                    "version": COVERAGE_GAP_PLAN_EXPORT_VERSION,
                    "feature_skeleton_source": "portfolio_benchmark.action_queue+decision_cards",
                    "feature_row_count": len(rows),
                }
            )
            payload["legacy_recovery"] = recovery
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            report_results.append({"report": str(path), "status": "materialized", "feature_row_count": len(rows)})
            feature_row_count += len(rows)
    return {
        "status": "materialized" if feature_row_count else "skipped",
        "report_count": len(report_results),
        "materialized_report_count": sum(1 for row in report_results if row.get("status") == "materialized"),
        "skipped_report_count": skipped_count,
        "feature_row_count": feature_row_count,
        "reports": report_results,
    }


def attach_recovery_external_signals(recovery_plan: dict, reports_dir: Path | None, snapshot_path: Path) -> dict:
    if not reports_dir:
        return {"status": "blocked", "reason": "missing_reports_dir", "report_count": 0, "field_update_count": 0}
    if not snapshot_path.exists():
        raise RuntimeError(f"external signal attachment failed: missing snapshot {snapshot_path}")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot_as_of = str(snapshot.get("as_of") or "")[:10]
    if not snapshot_as_of:
        raise RuntimeError(f"external signal attachment failed: {snapshot_path} has no as_of")
    items = recovery_plan.get("items") if isinstance(recovery_plan, dict) else []
    if not isinstance(items, list) or not items:
        return {"status": "empty", "report_count": 0, "field_update_count": 0}
    report_results = []
    field_update_count = 0
    attached_count = 0
    synced_count = 0
    training_example_field_update_count = 0
    skipped_count = 0
    blocked_count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        required_date = str(item.get("required_external_observation_date") or "")[:10]
        if required_date != snapshot_as_of:
            report_results.append(
                {
                    "required_external_observation_date": required_date,
                    "snapshot_as_of": snapshot_as_of,
                    "status": "blocked_snapshot_date_mismatch",
                    "field_update_count": 0,
                }
            )
            blocked_count += 1
            continue
        for report in item.get("exact_reports") or []:
            if not isinstance(report, dict):
                continue
            path = Path(str(report.get("report") or ""))
            if not path.is_absolute():
                path = reports_dir / path.name
            result = attach_external_snapshot_to_report(path, snapshot)
            report_results.append(result)
            if result.get("status") == "attached":
                attached_count += 1
                field_update_count += int(result.get("field_update_count") or 0)
                training_example_field_update_count += int(result.get("training_example_field_update_count") or 0)
            elif result.get("status") == "synced_existing_external_signals":
                synced_count += 1
                training_example_field_update_count += int(result.get("training_example_field_update_count") or 0)
            elif result.get("status", "").startswith("blocked"):
                blocked_count += 1
            else:
                skipped_count += 1
    return {
        "status": "attached" if attached_count else "synced" if synced_count else "blocked" if blocked_count else "skipped",
        "snapshot": str(snapshot_path),
        "snapshot_as_of": snapshot_as_of,
        "report_count": len(report_results),
        "attached_report_count": attached_count,
        "synced_report_count": synced_count,
        "skipped_report_count": skipped_count,
        "blocked_report_count": blocked_count,
        "field_update_count": field_update_count,
        "training_example_field_update_count": training_example_field_update_count,
        "reports": report_results,
    }


def attach_external_snapshot_to_report(path: Path, snapshot: dict) -> dict:
    if not path.exists():
        return {"report": str(path), "status": "blocked_missing_report", "field_update_count": 0}
    payload = json.loads(path.read_text(encoding="utf-8"))
    feature_rows = ((payload.get("feature_matrix") or {}).get("rows") or []) if isinstance(payload.get("feature_matrix"), dict) else []
    if not feature_rows:
        return {"report": str(path), "status": "blocked_missing_feature_rows", "field_update_count": 0}
    existing_external = payload.get("external_signals")
    if isinstance(existing_external, dict) and existing_external:
        if str(existing_external.get("as_of") or "")[:10] != str(snapshot.get("as_of") or "")[:10]:
            return {"report": str(path), "status": "skipped_existing_external_signals", "field_update_count": 0}
        normalize_public_external_reliability(payload)
        training_updates = sync_training_examples_external_from_features(payload)
        if not training_updates:
            return {"report": str(path), "status": "skipped_existing_external_signals", "field_update_count": 0}
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return {
            "report": str(path),
            "status": "synced_existing_external_signals",
            "feature_row_count": len(feature_rows),
            "field_update_count": 0,
            "training_example_field_update_count": training_updates,
            "external_signal_count": int(existing_external.get("signal_count") or 0),
        }
    before = {
        str(row.get("symbol") or "").upper(): {key: row.get(key) for key in COVERAGE_GAP_CANDIDATE_VALUE_KEYS}
        for row in feature_rows
        if isinstance(row, dict) and row.get("symbol")
    }
    payload["external_signals"] = snapshot
    normalize_public_external_reliability(payload)
    after_rows = ((payload.get("feature_matrix") or {}).get("rows") or []) if isinstance(payload.get("feature_matrix"), dict) else []
    field_update_count = 0
    for row in after_rows:
        if not isinstance(row, dict) or not row.get("symbol"):
            continue
        symbol = str(row.get("symbol") or "").upper()
        previous = before.get(symbol, {})
        for key in COVERAGE_GAP_CANDIDATE_VALUE_KEYS:
            if previous.get(key) != row.get(key):
                field_update_count += 1
    training_updates = sync_training_examples_external_from_features(payload)
    recovery = dict(payload.get("legacy_recovery") or {})
    recovery.update(
        {
            "version": COVERAGE_GAP_PLAN_EXPORT_VERSION,
            "external_signals_source": "attached_snapshot",
            "external_signals_as_of": snapshot.get("as_of"),
            "external_signal_count": int(snapshot.get("signal_count") or 0),
        }
    )
    payload["legacy_recovery"] = recovery
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {
        "report": str(path),
        "status": "attached",
        "feature_row_count": len(after_rows),
        "field_update_count": field_update_count,
        "training_example_field_update_count": training_updates,
        "external_signal_count": int(snapshot.get("signal_count") or 0),
    }


def sync_training_examples_external_from_features(payload: dict) -> int:
    feature_rows = ((payload.get("feature_matrix") or {}).get("rows") or []) if isinstance(payload.get("feature_matrix"), dict) else []
    features_by_symbol = {
        str(row.get("symbol") or "").upper(): row
        for row in feature_rows
        if isinstance(row, dict) and row.get("symbol")
    }
    update_count = 0
    for example in payload.get("recommendation_training_examples") or []:
        if not isinstance(example, dict):
            continue
        feature = features_by_symbol.get(str(example.get("symbol") or "").upper())
        if not feature:
            continue
        for key in COVERAGE_GAP_CANDIDATE_VALUE_KEYS:
            value = feature.get(key)
            if backfill_apply_field_missing(example.get(key)) and backfill_field_present(key, value):
                example[key] = value
                update_count += 1
    return update_count


def legacy_feature_skeleton_rows(payload: dict) -> list[dict]:
    card_by_symbol = {
        str(card.get("symbol") or "").upper(): card
        for card in payload.get("decision_cards") or []
        if isinstance(card, dict) and card.get("symbol")
    }
    rows = []
    seen = set()
    for trial in trials_from_payload_actions(payload):
        symbol = str(trial.symbol or "").upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        card = card_by_symbol.get(symbol, {})
        rows.append(legacy_feature_skeleton_row(payload, trial, card))
    return rows


def legacy_feature_skeleton_row(payload: dict, trial, card: dict) -> dict:
    as_of = trial.as_of.isoformat()
    signal_families = list(trial.signal_families)
    event_types = list(trial.event_types)
    return {
        "feature_id": stable_id([as_of, MODEL_POLICY_VERSION, trial.symbol]),
        "model_policy_version": MODEL_POLICY_VERSION,
        "feature_version": FEATURE_MATRIX_VERSION,
        "legacy_recovery": {
            "version": COVERAGE_GAP_PLAN_EXPORT_VERSION,
            "source": "portfolio_benchmark.action_queue+decision_cards",
        },
        "symbol": trial.symbol,
        "bucket": trial.bucket,
        "score": round(float(card.get("score") or 0), 2),
        "current_weight": round(float(trial.current_weight or 0), 6),
        "recommended_delta_weight": round(float(trial.recommended_delta_weight or 0), 6),
        "target_weight": round(float(trial.target_weight or 0), 6),
        "signal_family_count": max(int(card.get("signal_family_count") or 0), len(signal_families)),
        "signal_families": signal_families,
        "event_score": round(float(card.get("event_score") or 0), 2),
        "event_types": event_types,
        "source_tiers": card.get("source_tiers") or [],
        "external_signal_score": None,
        "coverage_adjusted_external_signal_score": None,
        "external_coverage_multiplier": None,
        "external_feed_status": None,
        "external_provider_count": None,
        "external_provider_ok_count": None,
        "external_provider_ok_ratio": None,
        "external_provider_gap_count": None,
        "external_provider_configuration_gap_count": None,
        "external_provider_transient_gap_count": None,
        "external_provider_stale_gap_count": None,
        "external_provider_runtime_gap_count": None,
        "external_provider_other_gap_count": None,
        "external_provider_primary_gap_severity": None,
        "external_provider_gap_severity_score": None,
        "external_signal_count": None,
        "external_source_count": None,
        "data_quality": 0.0,
    }


def legacy_action_training_examples(payload: dict) -> list[dict]:
    return [legacy_action_training_example(trial) for trial in trials_from_payload_actions(payload)]


def legacy_action_training_example(trial) -> dict:
    return {
        "example_id": trial.trial_id,
        "version": TRAINING_EXAMPLE_VERSION,
        "legacy_recovery": {
            "version": COVERAGE_GAP_PLAN_EXPORT_VERSION,
            "source": "portfolio_benchmark.action_queue",
        },
        "ticket_id": "",
        "model_policy_version": trial.model_policy_version,
        "as_of": trial.as_of.isoformat(),
        "session": trial.session,
        "symbol": trial.symbol,
        "bucket": trial.bucket,
        "trade_action": trial.trade_action,
        "current_weight": round(float(trial.current_weight or 0), 6),
        "recommended_delta_weight": round(float(trial.recommended_delta_weight or 0), 6),
        "target_weight": round(float(trial.target_weight or 0), 6),
        "post_action_weight": round(float(trial.target_weight or 0), 6),
        "trade_target_weight": round(float(trial.target_weight or 0), 6),
        "model_target_weight": round(float(trial.target_weight or 0), 6),
        "risk_adjusted_expected_return": trial.risk_adjusted_expected_return,
        "evidence_quality": trial.evidence_quality,
        "drawdown_risk": trial.drawdown_risk,
        "timing_score": trial.timing_score,
        "company_underwriting_score": None,
        "sector_setup_score": None,
        "company_add_eligible": None,
        "company_trim_signal": None,
        "decision_stack": {},
        "signal_families": list(trial.signal_families),
        "event_types": list(trial.event_types),
        "external_signal_score": trial.external_signal_score,
        "coverage_adjusted_external_signal_score": trial.coverage_adjusted_external_signal_score,
        "external_coverage_multiplier": trial.external_coverage_multiplier,
        "external_feed_status": trial.external_feed_status or None,
        "external_provider_count": trial.external_provider_count,
        "external_provider_ok_count": trial.external_provider_ok_count,
        "external_provider_ok_ratio": trial.external_provider_ok_ratio,
        "external_provider_gap_count": trial.external_provider_gap_count,
        "external_provider_configuration_gap_count": trial.external_provider_configuration_gap_count,
        "external_provider_transient_gap_count": trial.external_provider_transient_gap_count,
        "external_provider_stale_gap_count": trial.external_provider_stale_gap_count,
        "external_provider_runtime_gap_count": trial.external_provider_runtime_gap_count,
        "external_provider_other_gap_count": trial.external_provider_other_gap_count,
        "external_provider_primary_gap_severity": trial.external_provider_primary_gap_severity or None,
        "external_provider_gap_severity_score": trial.external_provider_gap_severity_score,
        "external_signal_count": trial.external_signal_count,
        "external_source_count": trial.external_source_count,
        "forward_return_labels": {horizon: None for horizon in FORWARD_HORIZONS},
        "label_status": "pending_forward_returns",
    }


def resolve_coverage_gap_candidates(
    backfill_items: list[dict],
    snapshot_payload: dict,
    snapshot_path: Path,
    source_report: str | None,
    reports_dir: Path | None,
    candidate_source: str = "source-report",
) -> dict:
    if candidate_source == "eligible-reports":
        return resolve_coverage_gap_candidates_from_eligible_reports(backfill_items, reports_dir)
    source_payload = snapshot_payload
    source_path = snapshot_path
    source_status = "snapshot"
    if source_report and reports_dir is not None:
        report_path = reports_dir / source_report
        if report_path.exists():
            source_payload = json.loads(report_path.read_text(encoding="utf-8"))
            source_path = report_path
            source_status = "source_report"
        else:
            source_status = "source_report_missing"
    feature_rows = (source_payload.get("feature_matrix") or {}).get("rows") or []
    by_symbol = {
        str(row.get("symbol") or "").upper(): row
        for row in feature_rows
        if isinstance(row, dict) and row.get("symbol")
    }
    for item in backfill_items:
        resolve_coverage_gap_candidate_item(
            item,
            by_symbol.get(str(item.get("symbol") or "").upper()),
            source_path,
            source_payload.get("as_of"),
        )
    ready_count = sum(1 for item in backfill_items if item.get("candidate_resolution_status") == "ready")
    apply_ready_count = sum(1 for item in backfill_items if item.get("candidate_apply_status") == "ready")
    acceptance_counts = candidate_acceptance_status_counts(backfill_items)
    return {
        "status": source_status,
        "candidate_source_policy": candidate_source,
        "candidate_source": str(source_path),
        "candidate_item_count": len(backfill_items),
        "candidate_ready_count": ready_count,
        "candidate_unresolved_count": len(backfill_items) - ready_count,
        "candidate_apply_ready_count": apply_ready_count,
        "candidate_apply_blocked_count": len(backfill_items) - apply_ready_count,
        "candidate_acceptance_check_count": sum(acceptance_counts.values()),
        "candidate_acceptance_passed_count": acceptance_counts.get("passed", 0),
        "candidate_acceptance_failed_count": acceptance_counts.get("failed", 0),
        "candidate_acceptance_status_counts": acceptance_counts,
    }


def resolve_coverage_gap_candidates_from_eligible_reports(backfill_items: list[dict], reports_dir: Path | None) -> dict:
    report_candidates = coverage_gap_report_candidates(reports_dir)
    report_inventory = coverage_gap_report_inventory(reports_dir)
    for item in backfill_items:
        item.update(eligible_report_search_diagnostics(item, report_candidates, report_inventory))
        candidate = select_eligible_report_candidate(item, report_candidates)
        if candidate:
            item["candidate_report_search_status"] = "eligible_report"
            source_path = candidate["path"]
            source_payload = candidate["payload"]
            feature_row = candidate["by_symbol"].get(str(item.get("symbol") or "").upper())
            source_as_of = candidate["as_of"]
        else:
            item["candidate_report_search_status"] = "eligible_report_missing"
            source_path = reports_dir or Path("reports")
            source_payload = {}
            feature_row = None
            source_as_of = None
        resolve_coverage_gap_candidate_item(item, feature_row, source_path, source_as_of)
        item["candidate_source_policy"] = "eligible-reports"
        item["candidate_report_search_status"] = item.get("candidate_report_search_status")
    ready_count = sum(1 for item in backfill_items if item.get("candidate_resolution_status") == "ready")
    apply_ready_count = sum(1 for item in backfill_items if item.get("candidate_apply_status") == "ready")
    acceptance_counts = candidate_acceptance_status_counts(backfill_items)
    report_search_status_counts = candidate_value_counts(backfill_items, "candidate_report_search_status")
    report_search_reason_counts = candidate_value_counts(backfill_items, "candidate_report_search_reason")
    return {
        "status": "eligible_reports",
        "candidate_source_policy": "eligible-reports",
        "candidate_source": "per-item eligible report",
        "candidate_report_count": len(report_candidates),
        "candidate_raw_report_count": len(report_inventory),
        "candidate_item_count": len(backfill_items),
        "candidate_ready_count": ready_count,
        "candidate_unresolved_count": len(backfill_items) - ready_count,
        "candidate_apply_ready_count": apply_ready_count,
        "candidate_apply_blocked_count": len(backfill_items) - apply_ready_count,
        "candidate_acceptance_check_count": sum(acceptance_counts.values()),
        "candidate_acceptance_passed_count": acceptance_counts.get("passed", 0),
        "candidate_acceptance_failed_count": acceptance_counts.get("failed", 0),
        "candidate_acceptance_status_counts": acceptance_counts,
        "candidate_report_search_status_counts": report_search_status_counts,
        "candidate_report_search_reason_counts": report_search_reason_counts,
}


def eligible_report_search_diagnostics(
    item: dict,
    report_candidates: list[dict],
    report_inventory: list[dict] | None = None,
) -> dict:
    report_inventory = report_inventory or []
    required_date = str(item.get("required_external_observation_date") or item.get("decision_as_of") or "")[:10]
    symbol = str(item.get("symbol") or "").upper()
    candidate_dates = sorted(
        str(candidate.get("as_of") or "")[:10]
        for candidate in report_candidates
        if str(candidate.get("as_of") or "")[:10]
    )
    raw_candidate_dates = sorted(
        str(candidate.get("as_of") or "")[:10]
        for candidate in report_inventory
        if str(candidate.get("as_of") or "")[:10]
    )
    eligible_by_date = [
        candidate for candidate in report_candidates if required_date and str(candidate.get("as_of") or "")[:10] <= required_date
    ]
    reports_with_symbol = [
        candidate for candidate in report_candidates if symbol and symbol in candidate.get("by_symbol", {})
    ]
    eligible_with_symbol = [
        candidate for candidate in eligible_by_date if symbol and symbol in candidate.get("by_symbol", {})
    ]
    raw_eligible_by_date = [
        candidate for candidate in report_inventory if required_date and str(candidate.get("as_of") or "")[:10] <= required_date
    ]
    raw_required_date_reports = [
        candidate for candidate in report_inventory if required_date and str(candidate.get("as_of") or "")[:10] == required_date
    ]
    raw_required_feature_rows = sum(int(candidate.get("feature_row_count") or 0) for candidate in raw_required_date_reports)
    if eligible_with_symbol:
        reason = "matched"
    elif not required_date or not symbol:
        reason = "missing_required_date_or_symbol"
    elif raw_required_date_reports and raw_required_feature_rows == 0:
        reason = "required_date_reports_missing_feature_matrix"
    elif raw_eligible_by_date and not eligible_by_date:
        reason = "eligible_reports_missing_feature_matrix"
    elif not eligible_by_date:
        reason = "no_reports_on_or_before_required_date"
    elif reports_with_symbol:
        reason = "symbol_missing_from_eligible_reports"
    else:
        reason = "symbol_missing_from_candidate_reports"
    return {
        "candidate_required_date": required_date or None,
        "candidate_symbol": symbol or None,
        "candidate_report_min_as_of": candidate_dates[0] if candidate_dates else None,
        "candidate_report_max_as_of": candidate_dates[-1] if candidate_dates else None,
        "candidate_raw_report_min_as_of": raw_candidate_dates[0] if raw_candidate_dates else None,
        "candidate_raw_report_max_as_of": raw_candidate_dates[-1] if raw_candidate_dates else None,
        "candidate_report_count": len(report_candidates),
        "candidate_raw_report_count": len(report_inventory),
        "candidate_eligible_date_report_count": len(eligible_by_date),
        "candidate_raw_eligible_date_report_count": len(raw_eligible_by_date),
        "candidate_raw_required_date_report_count": len(raw_required_date_reports),
        "candidate_raw_required_date_feature_row_count": raw_required_feature_rows,
        "candidate_raw_required_date_reports": compact_report_inventory_rows(raw_required_date_reports),
        "candidate_symbol_report_count": len(reports_with_symbol),
        "candidate_symbol_eligible_report_count": len(eligible_with_symbol),
        "candidate_report_search_reason": reason,
    }


def coverage_gap_report_inventory(reports_dir: Path | None) -> list[dict]:
    if reports_dir is None or not reports_dir.exists():
        return []
    inventory = []
    for path in sorted(reports_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        as_of = str(payload.get("as_of") or "")[:10]
        if not as_of:
            continue
        feature = payload.get("feature_matrix") if isinstance(payload.get("feature_matrix"), dict) else {}
        feature_rows = feature.get("rows") if isinstance(feature.get("rows"), list) else []
        external = payload.get("external_signals") if isinstance(payload.get("external_signals"), dict) else {}
        action_queue = (payload.get("portfolio_benchmark") or {}).get("action_queue") or []
        research_items = (payload.get("research_book") or {}).get("items") or []
        source_statuses = external.get("source_statuses") if isinstance(external.get("source_statuses"), list) else []
        external_provider_count = int(external.get("provider_count") or len(source_statuses) or 0)
        inventory.append(
            {
                "path": path,
                "as_of": as_of,
                "session": str(payload.get("session") or ""),
                "feature_row_count": len(feature_rows),
                "has_feature_matrix": bool(feature_rows),
                "training_example_count": len(payload.get("recommendation_training_examples") or []),
                "approval_ticket_count": len(payload.get("approval_tickets") or []),
                "action_queue_count": len(action_queue) if isinstance(action_queue, list) else 0,
                "research_item_count": len(research_items) if isinstance(research_items, list) else 0,
                "has_external_signals": bool(external),
                "external_provider_count": external_provider_count,
                "external_signal_count": int(external.get("signal_count") or 0),
            }
        )
    return sorted(inventory, key=report_candidate_sort_key)


def compact_report_inventory_rows(rows: list[dict], limit: int = 4) -> list[dict]:
    compact = []
    for row in rows[:limit]:
        compact.append(
            {
                "report": str(row.get("path") or ""),
                "as_of": row.get("as_of"),
                "session": row.get("session"),
                "feature_row_count": int(row.get("feature_row_count") or 0),
                "training_example_count": int(row.get("training_example_count") or 0),
                "approval_ticket_count": int(row.get("approval_ticket_count") or 0),
                "action_queue_count": int(row.get("action_queue_count") or 0),
                "research_item_count": int(row.get("research_item_count") or 0),
                "has_external_signals": bool(row.get("has_external_signals")),
                "external_provider_count": int(row.get("external_provider_count") or 0),
                "external_signal_count": int(row.get("external_signal_count") or 0),
            }
        )
    return compact


def coverage_gap_report_candidates(reports_dir: Path | None) -> list[dict]:
    if reports_dir is None or not reports_dir.exists():
        return []
    candidates = []
    for path in sorted(reports_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        normalize_public_external_reliability(payload)
        as_of = str(payload.get("as_of") or "")[:10]
        if not as_of:
            continue
        rows = (payload.get("feature_matrix") or {}).get("rows") or []
        by_symbol = {
            str(row.get("symbol") or "").upper(): row
            for row in rows
            if isinstance(row, dict) and row.get("symbol")
        }
        if not by_symbol:
            continue
        training_example_ids = {
            str(example.get("example_id") or "")
            for example in payload.get("recommendation_training_examples") or []
            if isinstance(example, dict) and example.get("example_id")
        }
        candidates.append(
            {
                "path": path,
                "payload": payload,
                "as_of": as_of,
                "session": str(payload.get("session") or ""),
                "by_symbol": by_symbol,
                "training_example_ids": training_example_ids,
            }
        )
    return sorted(candidates, key=report_candidate_sort_key)


def report_candidate_sort_key(candidate: dict) -> tuple:
    session_order = {
        "premarket": 0,
        "market_open": 1,
        "intraday": 2,
        "midday": 3,
        "market_close": 4,
        "postmarket": 5,
        "weekly": 6,
    }
    return (
        str(candidate.get("as_of") or ""),
        session_order.get(str(candidate.get("session") or ""), 9),
        str(candidate.get("path") or ""),
    )


def select_eligible_report_candidate(item: dict, report_candidates: list[dict]) -> dict | None:
    required_date = str(item.get("required_external_observation_date") or item.get("decision_as_of") or "")[:10]
    symbol = str(item.get("symbol") or "").upper()
    if not required_date or not symbol:
        return None
    eligible = [
        candidate for candidate in report_candidates
        if str(candidate.get("as_of") or "")[:10] <= required_date
        and symbol in candidate.get("by_symbol", {})
    ]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda candidate: (
            source_trial_match(item, candidate),
            required_backfill_field_count(item, candidate["by_symbol"].get(symbol)),
            external_evidence_strength(candidate["by_symbol"].get(symbol)),
            report_candidate_sort_key(candidate),
        ),
    )


def source_trial_match(item: dict, candidate: dict) -> int:
    trial_id = str(item.get("source_trial_id") or "")
    return 1 if trial_id and trial_id in candidate.get("training_example_ids", set()) else 0


def required_backfill_field_count(item: dict, feature_row: dict | None) -> int:
    if not feature_row:
        return 0
    return sum(
        1 for field in unique_field_names(item.get("fields_to_backfill") or [])
        if backfill_field_present(field, feature_row.get(field))
    )


def external_evidence_strength(feature_row: dict | None) -> tuple:
    if not feature_row:
        return (0, 0.0, 0)
    return (
        int(feature_row.get("external_source_count") or 0),
        float(feature_row.get("external_signal_score") or 0),
        int(feature_row.get("external_signal_count") or 0),
    )


def apply_coverage_gap_candidate_backfills(items: list[dict]) -> dict:
    if not items:
        return {"status": "empty", "applied_item_count": 0, "report_count": 0, "field_update_count": 0}
    blocked = [item for item in items if item.get("candidate_apply_status") != "ready"]
    if blocked:
        symbols = ", ".join(str(item.get("symbol") or "UNKNOWN") for item in blocked[:5])
        raise RuntimeError(f"coverage gap apply refused: {len(blocked)} candidate items are not apply-ready ({symbols})")
    grouped: dict[Path, list[dict]] = defaultdict(list)
    for item in items:
        source = item.get("candidate_source")
        if not source:
            raise RuntimeError("coverage gap apply refused: candidate item is missing candidate_source")
        path = Path(str(source))
        if not path.exists():
            raise RuntimeError(f"coverage gap apply refused: candidate source not found: {path}")
        grouped[path].append(item)

    report_results = []
    field_update_count = 0
    for path, path_items in sorted(grouped.items(), key=lambda entry: str(entry[0])):
        payload = json.loads(path.read_text(encoding="utf-8"))
        examples = payload.get("recommendation_training_examples")
        if not isinstance(examples, list):
            raise RuntimeError(f"coverage gap apply refused: {path} has no recommendation_training_examples list")
        by_id = {
            str(example.get("example_id") or ""): example
            for example in examples
            if isinstance(example, dict) and example.get("example_id")
        }
        report_field_updates = 0
        applied_symbols = []
        for item in path_items:
            example = by_id.get(str(item.get("source_trial_id") or ""))
            if not example:
                raise RuntimeError(f"coverage gap apply refused: {path} missing example {item.get('source_trial_id')}")
            item_symbol = str(item.get("symbol") or "").upper()
            example_symbol = str(example.get("symbol") or "").upper()
            if item_symbol and example_symbol and item_symbol != example_symbol:
                raise RuntimeError(
                    f"coverage gap apply refused: {path} example {item.get('source_trial_id')} "
                    f"symbol is {example_symbol}, expected {item_symbol}"
                )
            values = item.get("candidate_backfill_values") or {}
            conflicts = []
            for field in COVERAGE_GAP_CANDIDATE_VALUE_KEYS:
                if field not in values:
                    continue
                existing = example.get(field)
                value = values[field]
                if backfill_apply_field_missing(existing):
                    example[field] = value
                    report_field_updates += 1
                    field_update_count += 1
                elif existing != value:
                    conflicts.append({"field": field, "existing": existing, "candidate": value})
            if conflicts:
                conflict_fields = ", ".join(conflict["field"] for conflict in conflicts)
                raise RuntimeError(
                    f"coverage gap apply refused: {path} example {item.get('source_trial_id')} "
                    f"has conflicting fields: {conflict_fields}"
                )
            example["external_coverage_backfill"] = {
                "version": COVERAGE_GAP_PLAN_EXPORT_VERSION,
                "external_coverage_gap_id": item.get("external_coverage_gap_id"),
                "candidate_source": item.get("candidate_source"),
                "candidate_source_as_of": item.get("candidate_source_as_of"),
                "backfill_policy": item.get("backfill_policy"),
                "required_external_observation_date": item.get("required_external_observation_date"),
                "candidate_source_policy": item.get("candidate_source_policy"),
            }
            applied_symbols.append(example_symbol or item_symbol)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        report_results.append(
            {
                "report": str(path),
                "applied_item_count": len(path_items),
                "field_update_count": report_field_updates,
                "symbols": applied_symbols,
            }
        )
    return {
        "status": "applied",
        "applied_item_count": len(items),
        "report_count": len(report_results),
        "field_update_count": field_update_count,
        "reports": report_results,
    }


def apply_measurement_gap_candidate_backfills(items: list[dict]) -> dict:
    if not items:
        return {"status": "empty", "applied_item_count": 0, "report_count": 0, "field_update_count": 0}
    blocked = [item for item in items if item.get("candidate_apply_status") != "ready"]
    if blocked:
        symbols = ", ".join(str(item.get("symbol") or "UNKNOWN") for item in blocked[:5])
        raise RuntimeError(f"measurement gap apply refused: {len(blocked)} candidate items are not apply-ready ({symbols})")
    grouped: dict[Path, list[dict]] = defaultdict(list)
    for item in items:
        source = item.get("candidate_source")
        if not source:
            raise RuntimeError("measurement gap apply refused: candidate item is missing candidate_source")
        path = Path(str(source))
        if not path.exists():
            raise RuntimeError(f"measurement gap apply refused: candidate source not found: {path}")
        grouped[path].append(item)

    report_results = []
    field_update_count = 0
    for path, path_items in sorted(grouped.items(), key=lambda entry: str(entry[0])):
        payload = json.loads(path.read_text(encoding="utf-8"))
        examples = payload.get("recommendation_training_examples")
        if not isinstance(examples, list):
            raise RuntimeError(f"measurement gap apply refused: {path} has no recommendation_training_examples list")
        by_id = {
            str(example.get("example_id") or ""): example
            for example in examples
            if isinstance(example, dict) and example.get("example_id")
        }
        report_field_updates = 0
        applied_symbols = []
        for item in path_items:
            example = by_id.get(str(item.get("source_trial_id") or ""))
            if not example:
                raise RuntimeError(f"measurement gap apply refused: {path} missing example {item.get('source_trial_id')}")
            item_symbol = str(item.get("symbol") or "").upper()
            example_symbol = str(example.get("symbol") or "").upper()
            if item_symbol and example_symbol and item_symbol != example_symbol:
                raise RuntimeError(
                    f"measurement gap apply refused: {path} example {item.get('source_trial_id')} "
                    f"symbol is {example_symbol}, expected {item_symbol}"
                )
            values = item.get("candidate_backfill_values") or {}
            conflicts = []
            for field in MEASUREMENT_GAP_CANDIDATE_VALUE_KEYS:
                if field not in values:
                    continue
                existing = example.get(field)
                value = values[field]
                if backfill_apply_field_missing(existing):
                    example[field] = value
                    report_field_updates += 1
                    field_update_count += 1
                elif existing != value:
                    conflicts.append({"field": field, "existing": existing, "candidate": value})
            if conflicts:
                conflict_fields = ", ".join(conflict["field"] for conflict in conflicts)
                raise RuntimeError(
                    f"measurement gap apply refused: {path} example {item.get('source_trial_id')} "
                    f"has conflicting fields: {conflict_fields}"
                )
            record = {
                "version": MEASUREMENT_GAP_PLAN_EXPORT_VERSION,
                "external_alignment_measurement_gap_id": item.get("external_alignment_measurement_gap_id"),
                "candidate_source": item.get("candidate_source"),
                "candidate_source_as_of": item.get("candidate_source_as_of"),
                "candidate_source_section": item.get("candidate_source_section"),
                "candidate_derivation": item.get("candidate_derivation"),
                "candidate_derivation_policy": item.get("candidate_derivation_policy"),
                "backfill_policy": item.get("backfill_policy"),
            }
            records = example.get("external_alignment_measurement_backfills")
            if not isinstance(records, list):
                records = []
            existing_gap_ids = {
                str(existing.get("external_alignment_measurement_gap_id") or "")
                for existing in records
                if isinstance(existing, dict)
            }
            if str(record.get("external_alignment_measurement_gap_id") or "") not in existing_gap_ids:
                records.append(record)
            example["external_alignment_measurement_backfills"] = records
            applied_symbols.append(example_symbol or item_symbol)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        report_results.append(
            {
                "report": str(path),
                "applied_item_count": len(path_items),
                "field_update_count": report_field_updates,
                "symbols": applied_symbols,
            }
        )
    return {
        "status": "applied",
        "applied_item_count": len(items),
        "report_count": len(report_results),
        "field_update_count": field_update_count,
        "reports": report_results,
    }


def apply_provider_gap_severity_candidate_backfills(items: list[dict]) -> dict:
    if not items:
        return {
            "status": "empty",
            "applied_item_count": 0,
            "applied_example_count": 0,
            "report_count": 0,
            "field_update_count": 0,
        }
    blocked = [item for item in items if item.get("candidate_apply_status") != "ready"]
    if blocked:
        symbols = ", ".join(str(item.get("symbol") or "UNKNOWN") for item in blocked[:5])
        raise RuntimeError(
            f"provider gap severity apply refused: {len(blocked)} candidate items are not apply-ready ({symbols})"
        )
    grouped: dict[Path, list[dict]] = defaultdict(list)
    for item in items:
        source = item.get("candidate_source")
        if not source:
            raise RuntimeError("provider gap severity apply refused: candidate item is missing candidate_source")
        path = Path(str(source))
        if not path.exists():
            raise RuntimeError(f"provider gap severity apply refused: candidate source not found: {path}")
        grouped[path].append(item)

    report_results = []
    field_update_count = 0
    applied_example_count = 0
    for path, path_items in sorted(grouped.items(), key=lambda entry: str(entry[0])):
        payload = json.loads(path.read_text(encoding="utf-8"))
        examples = payload.get("recommendation_training_examples")
        if not isinstance(examples, list):
            raise RuntimeError(f"provider gap severity apply refused: {path} has no recommendation_training_examples list")
        by_id = {
            str(example.get("example_id") or ""): example
            for example in examples
            if isinstance(example, dict) and example.get("example_id")
        }
        report_field_updates = 0
        report_applied_examples = 0
        applied_symbols = []
        for item in path_items:
            source_trial_ids = provider_gap_source_trial_ids(item)
            if not source_trial_ids:
                raise RuntimeError("provider gap severity apply refused: candidate item is missing source_trial_ids")
            for source_trial_id in source_trial_ids:
                example = by_id.get(source_trial_id)
                if not example:
                    raise RuntimeError(f"provider gap severity apply refused: {path} missing example {source_trial_id}")
                item_symbol = str(item.get("symbol") or "").upper()
                example_symbol = str(example.get("symbol") or "").upper()
                if item_symbol and example_symbol and item_symbol != example_symbol:
                    raise RuntimeError(
                        f"provider gap severity apply refused: {path} example {source_trial_id} "
                        f"symbol is {example_symbol}, expected {item_symbol}"
                    )
                values = item.get("candidate_backfill_values") or {}
                conflicts = []
                for field in PROVIDER_GAP_SEVERITY_CANDIDATE_VALUE_KEYS:
                    if field not in values:
                        continue
                    existing = example.get(field)
                    value = values[field]
                    if backfill_apply_field_missing(existing):
                        example[field] = value
                        report_field_updates += 1
                        field_update_count += 1
                    elif existing != value:
                        conflicts.append({"field": field, "existing": existing, "candidate": value})
                if conflicts:
                    conflict_fields = ", ".join(conflict["field"] for conflict in conflicts)
                    raise RuntimeError(
                        f"provider gap severity apply refused: {path} example {source_trial_id} "
                        f"has conflicting fields: {conflict_fields}"
                    )
                record = provider_gap_severity_backfill_record(item)
                records = example.get("external_provider_gap_severity_backfills")
                if not isinstance(records, list):
                    records = []
                existing_record_ids = {
                    str(existing.get("external_provider_gap_severity_observation_backfill_record_id") or "")
                    for existing in records
                    if isinstance(existing, dict)
                }
                if str(record.get("external_provider_gap_severity_observation_backfill_record_id") or "") not in existing_record_ids:
                    records.append(record)
                example["external_provider_gap_severity_backfills"] = records
                report_applied_examples += 1
                applied_example_count += 1
                applied_symbols.append(example_symbol or item_symbol)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        report_results.append(
            {
                "report": str(path),
                "applied_item_count": len(path_items),
                "applied_example_count": report_applied_examples,
                "field_update_count": report_field_updates,
                "symbols": applied_symbols,
            }
        )
    return {
        "status": "applied",
        "applied_item_count": len(items),
        "applied_example_count": applied_example_count,
        "report_count": len(report_results),
        "field_update_count": field_update_count,
        "reports": report_results,
    }


def provider_gap_source_trial_ids(item: dict) -> list[str]:
    raw_ids = item.get("source_trial_ids")
    if isinstance(raw_ids, list):
        return unique_field_names(raw_ids)
    source_trial_id = str(item.get("source_trial_id") or "")
    return [source_trial_id] if source_trial_id else []


def provider_gap_severity_backfill_record(item: dict) -> dict:
    return {
        "version": PROVIDER_GAP_SEVERITY_BACKFILL_PLAN_EXPORT_VERSION,
        "external_provider_gap_severity_observation_backfill_record_id": item.get(
            "external_provider_gap_severity_observation_backfill_record_id"
        ),
        "external_provider_gap_severity_observation_work_item_id": item.get(
            "external_provider_gap_severity_observation_work_item_id"
        ),
        "candidate_source": item.get("candidate_source"),
        "candidate_source_section": item.get("candidate_source_section"),
        "candidate_backfill_policy": item.get("candidate_backfill_policy"),
        "candidate_apply_policy": item.get("candidate_apply_policy"),
        "candidate_provider_gap_severities": item.get("candidate_provider_gap_severities") or [],
        "candidate_provider_gap_sources": item.get("candidate_provider_gap_sources") or [],
        "fields_to_backfill": item.get("fields_to_backfill") or [],
    }


def backfill_apply_field_missing(value) -> bool:
    return value is None or value == ""


def resolve_coverage_gap_candidate_item(
    item: dict,
    feature_row: dict | None,
    source_path: Path,
    source_as_of: str | None,
) -> None:
    fields = unique_field_names(
        (item.get("fields_to_backfill") or [])
        + (item.get("missing_external_fields") or [])
        + list(COVERAGE_GAP_CANDIDATE_VALUE_KEYS)
    )
    required_fields = unique_field_names(item.get("fields_to_backfill") or [])
    values = {}
    missing_fields = []
    missing_required = []
    if not feature_row:
        missing_fields = fields
        missing_required = required_fields
    else:
        for field in fields:
            value = feature_row.get(field)
            if backfill_field_present(field, value):
                values[field] = value
            else:
                missing_fields.append(field)
        missing_required = [field for field in required_fields if field not in values]
    item["candidate_source"] = str(source_path)
    item["candidate_source_as_of"] = source_as_of
    item["candidate_symbol_found"] = bool(feature_row)
    item["candidate_backfill_values"] = values
    item["candidate_missing_fields"] = missing_fields
    item["candidate_missing_required_fields"] = missing_required
    item["candidate_resolution_status"] = "ready" if not missing_required else "incomplete"
    item["candidate_acceptance_checks"] = candidate_acceptance_checks(item)
    item["candidate_acceptance_status_counts"] = candidate_acceptance_status_counts([item])
    item["candidate_apply_status"] = (
        "ready"
        if item["candidate_resolution_status"] == "ready"
        and item["candidate_acceptance_status_counts"].get("failed", 0) == 0
        else "blocked"
    )


def candidate_acceptance_checks(item: dict) -> list[dict]:
    values = item.get("candidate_backfill_values") or {}
    source_as_of = str(item.get("candidate_source_as_of") or "")[:10]
    required_date = str(item.get("required_external_observation_date") or item.get("decision_as_of") or "")[:10]
    checks = []
    for check in item.get("open_acceptance_checks") or []:
        if not isinstance(check, dict):
            continue
        check_name = str(check.get("check") or "")
        field = str(check.get("field") or "")
        observed = values.get(field)
        passed = False
        if check_name == "decision_time_only":
            observed = source_as_of
            passed = bool(source_as_of and required_date and source_as_of <= required_date)
        elif check_name == "external_feed_status_present":
            passed = backfill_field_present(field, observed)
        elif field:
            passed = backfill_field_present(field, observed)
        checks.append(
            {
                "check": check_name,
                "field": field,
                "expected": check.get("expected"),
                "observed": observed,
                "status": "passed" if passed else "failed",
            }
        )
    return checks


def candidate_acceptance_status_counts(items: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        for check in item.get("candidate_acceptance_checks") or []:
            if not isinstance(check, dict):
                continue
            status = str(check.get("status") or "failed")
            counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def candidate_value_counts(items: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def candidate_present_value_counts(items: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "")
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def candidate_missing_required_field_counts(items: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        for field in item.get("candidate_missing_required_fields") or []:
            field_name = str(field or "")
            if not field_name:
                continue
            counts[field_name] = counts.get(field_name, 0) + 1
    return dict(sorted(counts.items()))


def candidate_failed_acceptance_check_counts(items: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        for check in item.get("candidate_acceptance_checks") or []:
            if not isinstance(check, dict) or check.get("status") == "passed":
                continue
            check_name = str(check.get("check") or "unknown")
            counts[check_name] = counts.get(check_name, 0) + 1
    return dict(sorted(counts.items()))


def unique_field_names(fields: list) -> list[str]:
    seen = set()
    out = []
    for field in fields:
        clean = str(field or "")
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


def backfill_field_present(field: str, value) -> bool:
    if field == "external_feed_status":
        return str(value or "").strip().lower() not in {"", "unknown", "missing", "none", "null"}
    return value is not None


def acceptance_check_count(rows: list[dict]) -> int:
    return sum(len(row.get("external_coverage_acceptance_checks") or []) for row in rows)


def open_acceptance_check_count(rows: list[dict]) -> int:
    total = 0
    for row in rows:
        for check in row.get("external_coverage_acceptance_checks") or []:
            if isinstance(check, dict) and check.get("status") != "passed":
                total += 1
    return total


def measurement_acceptance_check_count(rows: list[dict]) -> int:
    return sum(len(row.get("external_alignment_measurement_acceptance_checks") or []) for row in rows)


def open_measurement_acceptance_check_count(rows: list[dict]) -> int:
    total = 0
    for row in rows:
        for check in row.get("external_alignment_measurement_acceptance_checks") or []:
            if isinstance(check, dict) and check.get("status") != "passed":
                total += 1
    return total


def format_coverage_gap_plan_export(result: dict) -> str:
    rows = result.get("priority_rows") or []
    lines = [
        "# External Coverage Gap Plan",
        "",
        f"- Status: {result.get('status', 'unknown')}",
        f"- Source report: {result.get('source_report') or 'unknown'}",
        f"- Priority gaps: {result.get('priority_gap_count', 0)}",
        f"- Residual gaps: {result.get('residual_gap_count', 0)}",
        f"- Hidden residual gaps: {result.get('residual_hidden_gap_count', 0)}",
        f"- Open checks: {result.get('priority_open_acceptance_check_count', 0)} / {result.get('priority_acceptance_check_count', 0)}",
        f"- Projected ready date: {result.get('projected_external_learning_ready_date_after_priority_backfill') or 'unknown'}",
        "",
    ]
    residual_required_dates = result.get("residual_required_observation_dates") or []
    if residual_required_dates:
        lines.extend(
            [
                "- Residual required dates: "
                + ", ".join(
                    f"{row.get('required_external_observation_date') or 'unknown'}="
                    f"{row.get('gap_count', 0)} gaps/"
                    f"{row.get('source_trial_count', 0)} trials"
                    for row in residual_required_dates[:5]
                    if isinstance(row, dict)
                ),
                "",
            ]
        )
    recovery_plan = result.get("residual_recovery_plan") or {}
    recovery_items = recovery_plan.get("items") or []
    if recovery_items:
        lines.extend(
            [
                f"- Residual recovery: {recovery_plan.get('status', 'unknown')} "
                f"({recovery_plan.get('blocked_item_count', 0)} blocked date buckets)",
                "",
            ]
        )
        for item in recovery_items[:3]:
            if not isinstance(item, dict):
                continue
            missing = ", ".join(item.get("missing_sections") or []) or "none"
            lines.append(
                f"  - {item.get('required_external_observation_date') or 'unknown'}: "
                f"{item.get('status') or 'unknown'}; missing {missing}; "
                f"{item.get('gap_count', 0)} gaps/{item.get('source_trial_count', 0)} trials"
            )
        lines.append("")
    candidate_resolution = result.get("candidate_resolution") or {}
    if candidate_resolution:
        lines.extend(
            [
                f"- Candidate queue: {result.get('candidate_queue') or 'priority'} ({result.get('candidate_item_count', 0)} items)",
                f"- Candidate source: {candidate_resolution.get('candidate_source') or 'unknown'}",
                f"- Candidate ready: {candidate_resolution.get('candidate_ready_count', 0)} / {candidate_resolution.get('candidate_item_count', 0)}",
                f"- Candidate apply-ready: {candidate_resolution.get('candidate_apply_ready_count', 0)} / {candidate_resolution.get('candidate_item_count', 0)}",
                f"- Candidate checks passing: {candidate_resolution.get('candidate_acceptance_passed_count', 0)} / {candidate_resolution.get('candidate_acceptance_check_count', 0)}",
                "",
            ]
        )
        search_reasons = candidate_resolution.get("candidate_report_search_reason_counts") or {}
        if search_reasons:
            lines.extend([f"- Candidate search reasons: {format_count_map(search_reasons)}", ""])
        source_sections = candidate_resolution.get("candidate_source_section_counts") or {}
        if source_sections:
            lines.extend([f"- Candidate source sections: {format_count_map(source_sections)}", ""])
        derivations = candidate_resolution.get("candidate_derivation_counts") or {}
        if derivations:
            lines.extend([f"- Candidate derivations: {format_count_map(derivations)}", ""])
        missing_required = candidate_resolution.get("candidate_missing_required_field_counts") or {}
        failed_checks = candidate_resolution.get("candidate_failed_acceptance_check_counts") or {}
        blockers = []
        if missing_required:
            blockers.append(f"missing {format_count_map(missing_required)}")
        if failed_checks:
            blockers.append(f"failed checks {format_count_map(failed_checks)}")
        if blockers:
            lines.extend([f"- Candidate blockers: {'; '.join(blockers)}", ""])
    if not rows:
        lines.append("- No priority external coverage gaps.")
        residual_rows = result.get("residual_rows") or []
        if residual_rows:
            lines.append("- Top residual external coverage gaps:")
            for row in residual_rows[:5]:
                lines.append(
                    f"  - {row.get('symbol', 'UNKNOWN')} {row.get('horizon', 'unknown')}: "
                    f"score {row.get('residual_learning_value_score')} "
                    f"due {row.get('due_date') or 'unknown'}"
                )
        return "\n".join(lines) + "\n"
    for row in rows:
        checks = row.get("external_coverage_acceptance_checks") or []
        lines.append(
            f"- {row.get('symbol', 'UNKNOWN')} {row.get('horizon', 'unknown')}: "
            f"{row.get('external_coverage_gap_id', 'missing-gap-id')} "
            f"due {row.get('due_date') or 'unknown'} "
            f"({sum(1 for check in checks if isinstance(check, dict) and check.get('status') != 'passed')}/{len(checks)} open)"
        )
    return "\n".join(lines) + "\n"


def format_measurement_gap_plan_export(result: dict) -> str:
    rows = result.get("priority_rows") or []
    field_detail = format_count_detail(result.get("field_counts") or {})
    next_due_field_detail = format_count_detail(result.get("next_due_field_counts") or {})
    lines = [
        "# External Alignment Measurement Gap Plan",
        "",
        f"- Status: {result.get('status', 'unknown')}",
        f"- Source report: {result.get('source_report') or 'unknown'}",
        f"- Measurement gaps: {result.get('label_count', 0)} labels / {result.get('work_item_count', 0)} work items",
        f"- Hidden work items: {result.get('hidden_work_item_count', 0)}",
        f"- Open checks: {result.get('priority_open_acceptance_check_count', 0)} / {result.get('priority_acceptance_check_count', 0)}",
        f"- Next due: {result.get('next_due_date') or 'unknown'} "
        f"({result.get('next_due_label_count', 0)} labels / {result.get('next_due_work_item_count', 0)} work items)",
        f"- Fields: {field_detail or 'none'}",
    ]
    if next_due_field_detail:
        lines.append(f"- Next due fields: {next_due_field_detail}")
    lines.append("")
    candidate_resolution = result.get("candidate_resolution") or {}
    if candidate_resolution:
        lines.extend(
            [
                f"- Candidate source: {candidate_resolution.get('candidate_source') or 'unknown'}",
                f"- Candidate ready: {candidate_resolution.get('candidate_ready_count', 0)} / {candidate_resolution.get('candidate_item_count', 0)}",
                f"- Candidate apply-ready: {candidate_resolution.get('candidate_apply_ready_count', 0)} / {candidate_resolution.get('candidate_item_count', 0)}",
                f"- Candidate checks passing: {candidate_resolution.get('candidate_acceptance_passed_count', 0)} / {candidate_resolution.get('candidate_acceptance_check_count', 0)}",
                "",
            ]
        )
        search_reasons = candidate_resolution.get("candidate_report_search_reason_counts") or {}
        if search_reasons:
            lines.extend([f"- Candidate search reasons: {format_count_map(search_reasons)}", ""])
        source_sections = candidate_resolution.get("candidate_source_section_counts") or {}
        if source_sections:
            lines.extend([f"- Candidate source sections: {format_count_map(source_sections)}", ""])
        derivations = candidate_resolution.get("candidate_derivation_counts") or {}
        if derivations:
            lines.extend([f"- Candidate derivations: {format_count_map(derivations)}", ""])
        missing_required = candidate_resolution.get("candidate_missing_required_field_counts") or {}
        failed_checks = candidate_resolution.get("candidate_failed_acceptance_check_counts") or {}
        blockers = []
        if missing_required:
            blockers.append(f"missing {format_count_map(missing_required)}")
        if failed_checks:
            blockers.append(f"failed checks {format_count_map(failed_checks)}")
        if blockers:
            lines.extend([f"- Candidate blockers: {'; '.join(blockers)}", ""])
    if not rows:
        lines.append("- No external alignment measurement gaps.")
        return "\n".join(lines) + "\n"
    for row in rows:
        checks = row.get("external_alignment_measurement_acceptance_checks") or []
        fields = ", ".join(row.get("external_alignment_measurement_missing_fields") or []) or "missing fields"
        lines.append(
            f"- {row.get('symbol', 'UNKNOWN')} {row.get('horizon', 'unknown')}: "
            f"{row.get('external_alignment_measurement_gap_id', 'missing-gap-id')} "
            f"due {row.get('due_date') or 'unknown'} "
            f"fields {fields} "
            f"({sum(1 for check in checks if isinstance(check, dict) and check.get('status') != 'passed')}/{len(checks)} open)"
        )
    return "\n".join(lines) + "\n"


def format_provider_gap_severity_backfill_export(result: dict) -> str:
    items = result.get("backfill_items") or []
    lines = [
        "# Provider Gap Severity Backfill Plan",
        "",
        f"- Status: {result.get('status', 'unknown')}",
        f"- Source report: {result.get('source_report') or 'unknown'}",
        f"- Records: {result.get('candidate_item_count', 0)} queued / {result.get('record_count', 0)} total",
        f"- Apply-ready: {result.get('candidate_apply_ready_count', 0)} / {result.get('candidate_item_count', 0)}",
        "",
    ]
    if not items:
        lines.append("- No queued provider gap severity backfill records.")
        return "\n".join(lines) + "\n"
    for item in items[:8]:
        values = item.get("candidate_backfill_values") or {}
        lines.append(
            f"- {item.get('symbol', 'UNKNOWN')} {item.get('horizon', 'unknown')}: "
            f"{item.get('candidate_apply_status') or 'unknown'} "
            f"{values.get('external_provider_primary_gap_severity') or 'missing-severity'} "
            f"score {values.get('external_provider_gap_severity_score', 'unknown')} "
            f"source {item.get('source_report') or 'unknown'}"
        )
    return "\n".join(lines) + "\n"


def format_external_alignment_review_plan_export(result: dict) -> str:
    rows = result.get("priority_rows") or []
    lines = [
        "# External Alignment Review Plan",
        "",
        f"- Status: {result.get('status', 'unknown')}",
        f"- Source report: {result.get('source_report') or 'unknown'}",
        f"- Review queue: {result.get('label_count', 0)} labels / {result.get('work_item_count', 0)} work items",
        f"- Visible work items: {result.get('visible_work_item_count', 0)} / {result.get('work_item_count', 0)}",
        f"- Hidden work items: {result.get('hidden_work_item_count', 0)}",
        f"- Open checks: {result.get('open_acceptance_check_count', 0)} / {result.get('acceptance_check_count', 0)}",
        f"- Metadata-ready: {result.get('metadata_ready_work_item_count', 0)} / {result.get('work_item_count', 0)} work items",
        f"- Review bottleneck: {result.get('review_bottleneck') or 'unknown'}",
    ]
    open_counts = result.get("open_acceptance_check_counts") or {}
    if open_counts:
        lines.append(f"- Blockers: {format_count_map(open_counts)}")
    maturity_targets = result.get("maturity_test_target_counts") or {}
    if maturity_targets:
        lines.append(f"- Maturity test targets: {format_count_map(maturity_targets)}")
    maturity_metrics = result.get("maturity_test_primary_metric_counts") or {}
    if maturity_metrics:
        lines.append(f"- Maturity test metrics: {format_count_map(maturity_metrics)}")
    maturity_status = result.get("maturity_test_status_counts") or {}
    if maturity_status:
        lines.append(f"- Maturity test status: {format_count_map(maturity_status)}")
    maturity_blockers = result.get("maturity_test_blocker_counts") or {}
    if maturity_blockers:
        lines.append(f"- Maturity test blockers: {format_count_map(maturity_blockers)}")
    maturity_results = result.get("maturity_test_result_counts") or {}
    if maturity_results:
        lines.append(f"- Maturity test results: {format_count_map(maturity_results)}")
    next_due = result.get("next_due_date")
    if next_due:
        lines.append(
            f"- Next due: {next_due} "
            f"({result.get('next_due_label_count', 0)} labels / "
            f"{result.get('next_due_work_item_count', 0)} work items)"
        )
    next_focus = format_external_alignment_review_focus_detail(result.get("next_due_focus_counts") or {})
    if next_focus:
        lines.append(f"- Next due focus: {next_focus}")
    next_action = format_external_alignment_review_learning_action_counts(
        result.get("next_due_learning_action_counts") or {}
    )
    if next_action:
        lines.append(next_action)
    missing_measurements = format_external_alignment_review_measurement_missing_counts(
        result.get("next_due_measurement_missing_field_counts") or {}
    )
    if missing_measurements:
        lines.append(missing_measurements)
    lines.append("")
    if not rows:
        lines.append("- No external alignment review work items.")
        return "\n".join(lines) + "\n"
    for row in rows:
        checks = row.get("external_alignment_review_acceptance_checks") or []
        open_count = int(row.get("open_acceptance_check_count") or row.get("external_alignment_review_open_check_count") or 0)
        focus = str(row.get("external_alignment_review_focus") or row.get("external_alignment") or "review").replace("_", " ")
        summary = row.get("measurement_summary") or (row.get("external_alignment_review_measurement_plan") or {}).get("summary") or ""
        lines.append(
            f"- {row.get('symbol', 'UNKNOWN')} {row.get('horizon', 'unknown')}: "
            f"{row.get('external_alignment_review_id', 'missing-review-id')} "
            f"due {row.get('due_date') or 'unknown'} "
            f"{focus}, {open_count}/{len(checks)} open"
            f"{'; ' + summary if summary else ''}"
        )
    return "\n".join(lines) + "\n"


def format_count_map(counts: dict) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "none"


def format_count_detail(counts: dict) -> str:
    rows = []
    for key, value in sorted((counts or {}).items()):
        if isinstance(value, dict):
            label_count = int(value.get("label_count") or 0)
            work_item_count = int(value.get("work_item_count") or 0)
            rows.append(f"{key}={label_count} labels/{work_item_count} work items")
        else:
            rows.append(f"{key}={value}")
    return ", ".join(rows)


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
    from .util import parse_date

    if args.sources_command == "check":
        as_of = parse_date(getattr(args, "as_of", "")) if getattr(args, "as_of", "") else date.today()
        if as_of is None:
            print(f"Invalid --as-of date: {args.as_of}")
            return 2
        symbols = source_check_symbols(getattr(args, "symbols", ""), config.watchlist_symbols)
        snapshot = build_external_signal_snapshot(config, as_of, symbols)
        out = getattr(args, "out", "")
        if out:
            path = Path(out)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(snapshot, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        print(json.dumps(snapshot, indent=2, sort_keys=True, default=str))
        return 0
    return 2


def source_check_symbols(raw_symbols: str, default_symbols: list[str]) -> list[str]:
    symbols = raw_symbols.split(",") if raw_symbols else default_symbols
    seen = set()
    out = []
    for symbol in symbols:
        clean = str(symbol or "").strip().upper()
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


def command_backtest(args, config) -> int:
    if args.backtest_command == "refresh-report":
        result = refresh_report_backtest(config.reports_dir, Path(args.report) if args.report else None)
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return 0
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


def refresh_report_backtest(
    reports_dir: Path,
    report_path: Path | None = None,
    price_history: dict[str, list[dict]] | None = None,
) -> dict:
    path = report_path or latest_report_path(reports_dir)
    if not path.is_absolute() and not path.exists():
        path = reports_dir / path.name
    if not path.exists():
        raise RuntimeError(f"backtest refresh failed: missing report {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    as_of = parse_date(payload.get("as_of"))
    if as_of is None:
        raise RuntimeError(f"backtest refresh failed: report {path} has no valid as_of")
    before = payload.get("backtest") if isinstance(payload.get("backtest"), dict) else {}
    backtest = build_backtest_summary(reports_dir, as_of=as_of, price_history=price_history)
    training_examples = payload.get("recommendation_training_examples") or []
    payload["backtest"] = backtest
    payload["outcome_diagnostics"] = build_outcome_diagnostics(
        as_of,
        training_examples if isinstance(training_examples, list) else [],
        outcome_history_from_backtest(backtest),
        backtest,
    )
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    before_plan = before.get("pending_external_coverage_gap_plan") if isinstance(before, dict) else {}
    after_plan = backtest.get("pending_external_coverage_gap_plan") or {}
    return {
        "status": "refreshed",
        "report": str(path),
        "as_of": as_of.isoformat(),
        "trial_count": backtest.get("trial_count"),
        "pending_outcome_count": backtest.get("pending_outcome_count"),
        "before_external_gap_count": int(before.get("pending_external_coverage_gap_count") or 0) if isinstance(before, dict) else 0,
        "after_external_gap_count": int(backtest.get("pending_external_coverage_gap_count") or 0),
        "before_residual_gap_count": int(before_plan.get("residual_gap_count") or 0) if isinstance(before_plan, dict) else 0,
        "after_residual_gap_count": int(after_plan.get("residual_gap_count") or 0),
        "before_observed_external_long_horizon_label_count": (
            int(before_plan.get("observed_external_long_horizon_label_count") or 0)
            if isinstance(before_plan, dict)
            else 0
        ),
        "after_observed_external_long_horizon_label_count": int(
            after_plan.get("observed_external_long_horizon_label_count") or 0
        ),
    }


def latest_report_path(reports_dir: Path) -> Path:
    reports = sorted(reports_dir.glob("*.json"), key=report_selection_key)
    if not reports:
        raise RuntimeError(f"backtest refresh failed: no report JSON files in {reports_dir}")
    return reports[-1]


def format_backtest_summary(result: dict) -> str:
    as_of = parse_date(result.get("as_of")) or date.today()
    schedule = result.get("pending_label_schedule") if isinstance(result.get("pending_label_schedule"), dict) else {}
    if not schedule:
        schedule = pending_label_schedule(result, as_of)
    lines = [
        "# AlloIQ Recommendation Backtest",
        "",
        f"- Status: {result.get('status', 'unknown')}",
        f"- Trials: {result.get('trial_count', 0)}",
        f"- Completed labels: {result.get('completed_outcome_count', 0)}",
        f"- Pending labels: {result.get('pending_outcome_count', 0)}",
    ]
    next_label = format_pending_label_schedule("Next label maturity", schedule.get("next_label") or {})
    if next_label:
        lines.append(next_label)
    next_learning_label = format_pending_label_schedule(
        "Next learning label maturity",
        schedule.get("next_learning_label") or {},
    )
    if next_learning_label:
        lines.append(next_learning_label)
    next_external_label = format_external_alignment_due_date(
        (result.get("pending_external_alignment_due_dates") or [{}])[0]
    )
    if next_external_label:
        lines.append(next_external_label)
    pending_earnings = format_pending_earnings_label_buckets(result)
    if pending_earnings:
        lines.append(pending_earnings)
    pending_approval = format_pending_approval_label_buckets(result)
    if pending_approval:
        lines.append(pending_approval)
    review_count = int(result.get("pending_external_alignment_review_count") or 0)
    if review_count:
        review_item_count = int(result.get("pending_external_alignment_review_item_count") or 0)
        hidden_item_count = int(result.get("pending_external_alignment_review_hidden_item_count") or 0)
        hidden_detail = f", {hidden_item_count} hidden" if hidden_item_count else ""
        if review_item_count and review_item_count != review_count:
            lines.append(
                f"- External alignment review queue: {review_count} non-confirming labels / "
                f"{review_item_count} {pluralize('work item', review_item_count)}{hidden_detail}"
            )
        else:
            lines.append(f"- External alignment review queue: {review_count} non-confirming labels{hidden_detail}")
        review_acceptance_summary = result.get("pending_external_alignment_review_acceptance_summary") or {}
        review_acceptance = format_external_alignment_review_acceptance_summary(review_acceptance_summary)
        if review_acceptance:
            lines.append(review_acceptance)
        review_due_dates = result.get("pending_external_alignment_review_due_dates") or []
        if review_due_dates:
            first_review_due = review_due_dates[0]
            label_count = int(first_review_due.get("label_count") or 0)
            work_item_count = int(first_review_due.get("work_item_count") or 0)
            lines.append(
                f"- Next review bucket: {first_review_due.get('due_date')} "
                f"({label_count} {pluralize('label', label_count)} / "
                f"{work_item_count} {pluralize('work item', work_item_count)})"
            )
            review_focus = format_external_alignment_review_focus_counts(first_review_due.get("focus_counts") or {})
            if review_focus:
                lines.append(review_focus)
            review_action = format_external_alignment_review_learning_action_counts(
                review_acceptance_summary.get("next_open_check_due_learning_action_counts") or {}
            )
            if review_action:
                lines.append(review_action)
            missing_measurements = format_external_alignment_review_measurement_missing_counts(
                review_acceptance_summary.get("next_open_check_due_measurement_missing_field_counts") or {}
            )
            if missing_measurements:
                lines.append(missing_measurements)
            measurement_gap = format_external_alignment_measurement_gap_plan(
                result.get("pending_external_alignment_measurement_gap_plan") or {}
            )
            if measurement_gap:
                lines.append(measurement_gap)
        review_queue = result.get("pending_external_alignment_review_queue") or []
        next_review_item = format_external_alignment_review_item(review_queue[0] if review_queue else {})
        if next_review_item:
            lines.append(next_review_item)
    lines.append("")
    for row in result.get("horizons", []):
        lines.append(
            f"- {row.get('horizon')}: completed {row.get('completed_count', 0)}, "
            f"hit rate {row.get('hit_rate')}, avg return {row.get('average_decision_return')}"
        )
    lines.append("")
    return "\n".join(lines)


def format_pending_label_schedule(label: str, item: dict) -> str | None:
    due_date = item.get("due_date")
    if not due_date:
        return None
    due_count = int(item.get("due_count") or 0)
    parts = []
    horizon = item.get("horizon")
    if horizon:
        parts.append(str(horizon))
    if due_count:
        parts.append(f"{due_count} {pluralize('label', due_count)}")
    days = format_days_until_due(item.get("days_until_due"))
    if days:
        parts.append(days)
    detail = f" ({', '.join(parts)})" if parts else ""
    return f"- {label}: {due_date}{detail}"


def format_external_alignment_due_date(item: dict) -> str | None:
    due_date = item.get("due_date")
    if not due_date:
        return None
    due_count = int(item.get("due_count") or 0)
    aligned_count = int(item.get("aligned_count") or 0)
    conflict_count = int(item.get("conflict_count") or 0)
    horizons = ", ".join(str(value) for value in item.get("horizons") or [] if value)
    symbols = ", ".join(str(value) for value in (item.get("symbols") or [])[:5] if value)
    parts = []
    if horizons:
        parts.append(horizons)
    if due_count:
        parts.append(f"{due_count} {pluralize('label', due_count)}")
    parts.append(f"{aligned_count} aligned")
    parts.append(f"{conflict_count} {pluralize('conflict', conflict_count)}")
    if symbols:
        parts.append(f"symbols {symbols}")
    return f"- Next external alignment label: {due_date} ({', '.join(parts)})"


def format_pending_earnings_label_buckets(result: dict) -> str | None:
    confirmation = pending_bucket_phrases(
        result.get("pending_by_earnings_confirmation_bucket") or [],
        skip_keys={"no_event", "unknown", ""},
    )
    risk_windows = pending_bucket_phrases(
        result.get("pending_by_earnings_risk_window") or [],
        skip_keys={"no_event", "unknown", ""},
    )
    parts = []
    if confirmation:
        parts.append("confirmation " + "; ".join(confirmation))
    if risk_windows:
        parts.append("risk windows " + "; ".join(risk_windows))
    if not parts:
        return None
    return "- Pending earnings label buckets: " + " | ".join(parts)


def format_pending_approval_label_buckets(result: dict) -> str | None:
    blockers = pending_bucket_phrases(
        result.get("pending_by_approval_blocker_bucket") or [],
        skip_keys={"no_approval_context", "ready", "unknown", ""},
    )
    if not blockers:
        return None
    return "- Pending approval label buckets: " + "; ".join(blockers)


def pending_bucket_phrases(rows: list[dict], skip_keys: set[str] | None = None, limit: int = 4) -> list[str]:
    skip_keys = skip_keys or set()
    phrases = []
    for row in rows:
        key = str(row.get("key") or "")
        if key in skip_keys:
            continue
        count = int(row.get("pending_count") or 0)
        if not count:
            continue
        label = pending_bucket_label(key)
        next_due = row.get("next_due_date")
        due = f" next {next_due}" if next_due else ""
        phrases.append(f"{label} {count} {pluralize('label', count)}{due}")
        if len(phrases) >= limit:
            break
    return phrases


def pending_bucket_label(key: str) -> str:
    if key == "confirmation_required":
        return "required"
    if key == "no_confirmation_required":
        return "not required"
    return key.replace("_", " ")


def format_external_alignment_review_item(item: dict) -> str | None:
    if not item:
        return None
    review_id = str(item.get("external_alignment_review_id") or "missing-review-id")
    symbol = str(item.get("symbol") or "UNKNOWN")
    horizon = str(item.get("horizon") or "unknown")
    due_date = str(item.get("due_date") or "unknown")
    focus = str(item.get("external_alignment_review_focus") or item.get("external_alignment") or "review")
    label_count = int(item.get("external_alignment_review_label_count") or 0)
    details = [focus.replace("_", " ")]
    if label_count:
        details.append(f"{label_count} {pluralize('label', label_count)}")
    source_id = str(item.get("source_outcome_id") or item.get("source_trial_id") or "")
    if source_id:
        details.append(f"source {source_id[:8]}")
    checks = [check for check in item.get("external_alignment_review_acceptance_checks") or [] if isinstance(check, dict)]
    if checks:
        open_check_count = int(
            item.get("external_alignment_review_open_check_count")
            if item.get("external_alignment_review_open_check_count") is not None
            else sum(1 for check in checks if check.get("status") != "passed")
        )
        details.append(f"{open_check_count}/{len(checks)} open checks")
    measurement = item.get("external_alignment_review_measurement_plan") or {}
    measurement_summary = str(measurement.get("summary") or "")
    if measurement_summary:
        details.append(measurement_summary)
    action = str(item.get("external_alignment_review_learning_action") or "")
    if action:
        details.append(action)
    return f"- Next review item: {review_id} {symbol} {horizon} due {due_date} ({', '.join(details)})"


def format_external_alignment_review_acceptance_summary(summary: dict) -> str | None:
    if not summary:
        return None
    open_count = int(summary.get("open_check_count") or 0)
    check_count = int(summary.get("check_count") or 0)
    open_labels = int(summary.get("open_label_count") or 0)
    metadata_ready = int(summary.get("metadata_ready_work_item_count") or 0)
    work_items = int(summary.get("work_item_count") or 0)
    open_counts = summary.get("open_check_counts") or {}
    blockers = ", ".join(
        f"{key}={value}" for key, value in sorted(open_counts.items()) if value
    ) or "none"
    next_due = summary.get("next_open_check_due_date")
    next_due_labels = int(summary.get("next_open_check_due_label_count") or 0)
    next_due_items = int(summary.get("next_open_check_due_work_item_count") or 0)
    next_due_visible_items = int(summary.get("next_open_check_due_visible_work_item_count") or 0)
    next_due_hidden_items = int(summary.get("next_open_check_due_hidden_work_item_count") or 0)
    next_due_symbols = [
        str(value)
        for value in (summary.get("next_open_check_due_symbols") or [])[:5]
        if value
    ]
    next_due_horizons = [
        str(value)
        for value in (summary.get("next_open_check_due_horizons") or [])
        if value
    ]
    next_due_parts = [
        f"{next_due_labels} {pluralize('label', next_due_labels)} / "
        f"{next_due_items} {pluralize('work item', next_due_items)}"
    ]
    if next_due_horizons:
        next_due_parts.append(", ".join(next_due_horizons))
    if next_due_symbols:
        next_due_parts.append(f"symbols {', '.join(next_due_symbols)}")
    next_due_focus = format_external_alignment_review_focus_detail(
        summary.get("next_open_check_due_focus_counts") or {}
    )
    if next_due_focus:
        next_due_parts.append(f"focus {next_due_focus}")
    if next_due_items:
        queue_detail = f"queue {next_due_visible_items}/{next_due_items} visible"
        if next_due_hidden_items:
            queue_detail = f"{queue_detail}, {next_due_hidden_items} hidden"
        next_due_parts.append(queue_detail)
    next_due_detail = (
        f"; next blocker due {next_due} ({', '.join(next_due_parts)})"
        if next_due
        else ""
    )
    return (
        f"- Review acceptance: {open_count}/{check_count} checks open across {open_labels} {pluralize('label', open_labels)}; "
        f"metadata-ready {metadata_ready}/{work_items} {pluralize('work item', work_items)}; "
        f"blockers {blockers}{next_due_detail}"
    )


def format_external_alignment_review_focus_detail(focus_counts: dict, limit: int = 3) -> str | None:
    rows = []
    for focus, counts in focus_counts.items():
        if not isinstance(counts, dict):
            continue
        label_count = int(counts.get("label_count") or 0)
        work_item_count = int(counts.get("work_item_count") or 0)
        if label_count <= 0 and work_item_count <= 0:
            continue
        rows.append((str(focus), label_count, work_item_count))
    if not rows:
        return None
    ordered = sorted(rows, key=lambda row: (-row[1], -row[2], row[0]))[:limit]
    return "; ".join(
        f"{focus.replace('_', ' ')} {label_count} {pluralize('label', label_count)}"
        f"/{work_item_count} {pluralize('work item', work_item_count)}"
        for focus, label_count, work_item_count in ordered
    )


def format_external_alignment_review_focus_counts(focus_counts: dict) -> str | None:
    detail = format_external_alignment_review_focus_detail(focus_counts)
    if not detail:
        return None
    return f"- Next review focus: {detail}"


def format_external_alignment_review_learning_action_counts(action_counts: dict) -> str | None:
    rows = []
    for action, counts in action_counts.items():
        if not isinstance(counts, dict):
            continue
        label_count = int(counts.get("label_count") or 0)
        work_item_count = int(counts.get("work_item_count") or 0)
        if not action or (label_count <= 0 and work_item_count <= 0):
            continue
        rows.append((str(action), label_count, work_item_count))
    if not rows:
        return None
    action, label_count, work_item_count = sorted(
        rows,
        key=lambda row: (-row[1], -row[2], row[0]),
    )[0]
    return (
        f"- Next review action: {action} "
        f"({label_count} {pluralize('label', label_count)}/"
        f"{work_item_count} {pluralize('work item', work_item_count)})"
    )


def format_external_alignment_review_measurement_missing_counts(missing_counts: dict, limit: int = 3) -> str | None:
    rows = []
    for field, counts in missing_counts.items():
        if not isinstance(counts, dict):
            continue
        label_count = int(counts.get("label_count") or 0)
        work_item_count = int(counts.get("work_item_count") or 0)
        if not field or (label_count <= 0 and work_item_count <= 0):
            continue
        rows.append((str(field), label_count, work_item_count))
    if not rows:
        return None
    ordered = sorted(rows, key=lambda row: (-row[1], -row[2], row[0]))[:limit]
    detail = "; ".join(
        f"{field.replace('_', ' ')} {label_count} {pluralize('label', label_count)}"
        f"/{work_item_count} {pluralize('work item', work_item_count)}"
        for field, label_count, work_item_count in ordered
    )
    return f"- Next review missing measurements: {detail}"


def format_external_alignment_measurement_gap_plan(plan: dict) -> str | None:
    if not plan:
        return None
    label_count = int(plan.get("label_count") or 0)
    work_item_count = int(plan.get("work_item_count") or 0)
    hidden_count = int(plan.get("hidden_work_item_count") or 0)
    if label_count <= 0 and work_item_count <= 0:
        return None
    check_count = int(plan.get("priority_acceptance_check_count") or 0)
    open_check_count = int(plan.get("priority_open_acceptance_check_count") or 0)
    next_due = str(plan.get("next_due_date") or "")
    next_due_counts = format_external_alignment_review_focus_detail(plan.get("next_due_field_counts") or {})
    symbols = [
        str(symbol)
        for symbol in (plan.get("priority_symbols") or [])[:5]
        if symbol
    ]
    parts = [
        f"{label_count} {pluralize('label', label_count)} / "
        f"{work_item_count} {pluralize('work item', work_item_count)}",
    ]
    if hidden_count:
        parts.append(f"{hidden_count} hidden")
    if check_count:
        parts.append(f"{open_check_count}/{check_count} priority checks open")
    if next_due:
        due_detail = f"next {next_due}"
        if next_due_counts:
            due_detail = f"{due_detail} {next_due_counts}"
        parts.append(due_detail)
    if symbols:
        parts.append(f"symbols {', '.join(symbols)}")
    return f"- Measurement backfill queue: {', '.join(parts)}"


def format_days_until_due(days: object) -> str:
    try:
        value = int(days)
    except (TypeError, ValueError):
        return ""
    if value < 0:
        overdue_days = abs(value)
        return f"{overdue_days} {pluralize('day', overdue_days)} overdue"
    if value == 0:
        return "due today"
    return f"in {value} {pluralize('day', value)}"


def pluralize(word: str, count: int) -> str:
    return word if count == 1 else f"{word}s"


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


def command_notify(args, config) -> int:
    from .notifications import send_latest_briefing

    result = send_latest_briefing(
        config,
        session=args.session or None,
        channel=args.channel,
        reports_dir=Path(args.reports_dir) if args.reports_dir else None,
        dry_run=args.dry_run,
        site_url=args.site_url or None,
        urgent_only=args.urgent_only,
        compare_to=Path(args.compare_to) if args.compare_to else None,
    )
    if args.dry_run and result.get("message"):
        print(result["message"])
    else:
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0 if result.get("status") in {"sent", "dry_run", "skipped"} else 2


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
            if not holdings:
                previous_count = manager_filing_holding_count(conn, filing.manager_key)
                if previous_count:
                    print(
                        f"Skipped {filing.form} {filing.accession_number}: fetched 0 holdings; "
                        "kept previous known manager holdings"
                    )
                    continue
                raise ValueError(f"{filing.accession_number} parsed 0 holdings and no previous holdings are stored")
        upsert_filing(conn, filing, holdings)
        stored += 1
        print(f"Stored {filing.form} {filing.accession_number} with {len(holdings)} holdings")
    print(f"Stored {stored} filings for {manager_key}")
    return stored


if __name__ == "__main__":
    raise SystemExit(main())
