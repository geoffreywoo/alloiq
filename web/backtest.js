const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

let payload = null;

init().catch((error) => {
  document.body.insertAdjacentHTML("afterbegin", `<p class="stale-banner">Backtest failed to load: ${escapeHtml(error.message)}</p>`);
});

async function init() {
  const response = await fetch(`/data/latest.json?ts=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  payload = await response.json();
  render();
}

function render() {
  const asOf = payload.as_of || "";
  document.getElementById("backtestDate").textContent = asOf ? `Snapshot ${asOf}` : "Public snapshot";
  renderSummary();
  renderHorizons();
  renderCalibration();
  renderGroup("signalFamilyTable", backtest().by_signal_family || []);
  renderGroup("bucketTable", backtest().by_bucket || []);
  renderExternalGroup(
    "externalStatusTable",
    backtest().by_external_feed_status || [],
    backtest().pending_by_external_feed_status || [],
    "No external feed status labels yet.",
  );
  renderExternalGroup(
    "externalCoverageTable",
    backtest().by_external_coverage || [],
    backtest().pending_by_external_coverage || [],
    "No external coverage labels yet.",
  );
  renderCoverageGapPlan();
  renderOutcomes(
    "externalCoverageGapQueue",
    backtest().pending_external_coverage_gap_queue || [],
    "No external coverage gaps blocking learning.",
  );
  renderExternalGroup(
    "externalAlignmentTable",
    backtest().by_external_alignment || [],
    backtest().pending_by_external_alignment || [],
    "No external alignment labels yet.",
  );
  renderAlignmentDueDates();
  renderAlignmentReviewQueue();
  renderOutcomes(
    "externalAlignmentWatchlist",
    backtest().pending_external_alignment_watchlist || [],
    "No pending external alignment examples.",
  );
  renderOutcomes("topWins", backtest().top_wins || [], "No completed wins yet.");
  renderOutcomes("pendingOutcomes", backtest().recent_pending || [], "No pending labels.");
}

function renderSummary() {
  const bt = backtest();
  const cal = bt.calibration || {};
  const cards = [
    {
      label: "Trials",
      value: String(bt.trial_count || 0),
      detail: `${bt.source_report_count || 0} saved report snapshots`,
    },
    {
      label: "Completed labels",
      value: String(bt.completed_outcome_count || 0),
      detail: `${bt.pending_outcome_count || 0} labels still open`,
    },
    {
      label: "Status",
      value: labelize(bt.status || "awaiting_matured_outcomes"),
      detail: bt.version || "backtest v1",
    },
    {
      label: "Calibration",
      value: cal.mean_error == null ? "Pending" : `${formatPct(cal.mean_error)}`,
      detail: labelize(cal.status || "insufficient data"),
    },
  ];
  document.getElementById("backtestSummary").innerHTML = cards.map(summaryCard).join("");
}

function renderHorizons() {
  const rows = backtest().horizons || [];
  document.getElementById("horizonScoreboard").innerHTML = rows.length
    ? rows.map(horizonRow).join("")
    : empty("No horizon rows yet.");
}

function horizonRow(row) {
  const completed = Number(row.completed_count || 0);
  const total = Number(row.trial_count || 0);
  const width = total ? Math.min(100, Math.round((completed / total) * 100)) : 0;
  return `
    <article class="horizon-row">
      <div>
        <strong>${escapeHtml(String(row.horizon || ""))}</strong>
        <span>${completed}/${total} complete</span>
      </div>
      <div class="target-bar">
        <span style="width:${width}%"></span>
      </div>
      <div class="horizon-metrics">
        <span>Hit ${formatRatio(row.hit_rate)}</span>
        <span>Avg ${formatPct(row.average_decision_return)}</span>
        <span>Error ${formatPct(row.mean_error)}</span>
      </div>
    </article>
  `;
}

function renderCalibration() {
  const cal = backtest().calibration || {};
  const buckets = cal.buckets || [];
  document.getElementById("calibrationPanel").innerHTML = `
    <div class="calibration-hero">
      <strong>${escapeHtml(labelize(cal.status || "insufficient_data"))}</strong>
      <span>${escapeHtml(cal.message || "Calibration starts when forward labels mature.")}</span>
    </div>
    <div class="mini-table">
      ${buckets.length ? buckets.map(groupRow).join("") : empty("No expected-return buckets have matured yet.")}
    </div>
  `;
}

function renderGroup(targetId, rows, emptyText = "No completed labels yet.") {
  document.getElementById(targetId).innerHTML = rows.length
    ? rows.slice(0, 10).map(groupRow).join("")
    : empty(emptyText);
}

function renderExternalGroup(targetId, completedRows, pendingRows, emptyText) {
  const useCompleted = completedRows.length > 0;
  const rows = useCompleted ? completedRows : pendingRows;
  document.getElementById(targetId).innerHTML = rows.length
    ? rows.slice(0, 10).map(useCompleted ? groupRow : pendingGroupRow).join("")
    : empty(emptyText);
}

function groupRow(row) {
  const detail = row.mean_error == null
    ? `Hit ${formatRatio(row.hit_rate)}`
    : `Hit ${formatRatio(row.hit_rate)} · Error ${formatPct(row.mean_error)}`;
  return `
    <div class="mini-row">
      <span>
        <strong>${escapeHtml(labelize(row.key || ""))}</strong>
        <small>${row.completed_count || 0} labels</small>
      </span>
      <span>
        <strong>${formatPct(row.average_decision_return)}</strong>
        <small>${escapeHtml(detail)}</small>
      </span>
    </div>
  `;
}

function pendingGroupRow(row) {
  const nextDue = row.next_due_date ? `Next due ${row.next_due_date}` : "Awaiting label dates";
  return `
    <div class="mini-row">
      <span>
        <strong>${escapeHtml(labelize(row.key || ""))}</strong>
        <small>${row.pending_count || 0} pending labels</small>
      </span>
      <span>
        <strong>Pending</strong>
        <small>${escapeHtml(nextDue)}</small>
      </span>
    </div>
  `;
}

function renderAlignmentDueDates() {
  const rows = backtest().pending_external_alignment_due_dates || [];
  document.getElementById("externalAlignmentDueDates").innerHTML = rows.length
    ? rows.slice(0, 8).map(alignmentDueDateRow).join("")
    : empty("No pending external alignment due dates.");
}

function alignmentDueDateRow(row) {
  const detail = [
    `${row.conflict_count || 0} conflict`,
    `${row.aligned_count || 0} aligned`,
    `${row.engine_neutral_count || 0} engine neutral`,
    `${row.external_neutral_count || 0} external neutral`,
  ].join(" · ");
  const horizons = (row.horizons || []).join(", ") || "all horizons";
  return `
    <div class="mini-row">
      <span>
        <strong>${escapeHtml(row.due_date || "")}</strong>
        <small>${escapeHtml(horizons)}</small>
      </span>
      <span>
        <strong>${row.due_count || 0} labels</strong>
        <small>${escapeHtml(detail)}</small>
      </span>
    </div>
  `;
}

function renderAlignmentReviewQueue() {
  const bt = backtest();
  const rows = bt.pending_external_alignment_review_queue || [];
  const labelCount = Number(bt.pending_external_alignment_review_count || 0);
  const itemCount = Number(bt.pending_external_alignment_review_item_count || rows.length);
  const target = document.getElementById("externalAlignmentReviewQueue");
  if (!target) return;
  if (!rows.length) {
    target.innerHTML = empty("No non-confirming external alignment reviews.");
    return;
  }
  const summary = `${labelCount} ${pluralize("label", labelCount)} / ${itemCount} ${pluralize("work item", itemCount)}`;
  const hiddenCount = Number(bt.pending_external_alignment_review_hidden_item_count || Math.max(0, itemCount - rows.length));
  const shownCount = rows.length;
  const visibility = hiddenCount > 0
    ? `Showing ${shownCount}; ${hiddenCount} hidden`
    : `Showing ${shownCount}`;
  const dueDates = bt.pending_external_alignment_review_due_dates || [];
  const acceptance = bt.pending_external_alignment_review_acceptance_summary || {};
  const measurementGapPlan = bt.pending_external_alignment_measurement_gap_plan || {};
  const measurementGapRows = bt.pending_external_alignment_measurement_gap_queue || [];
  const nextOpenLabelCount = Number(acceptance.next_open_check_due_label_count || 0);
  const nextOpenItemCount = Number(acceptance.next_open_check_due_work_item_count || 0);
  const nextOpenVisibleItemCount = Number(acceptance.next_open_check_due_visible_work_item_count || 0);
  const nextOpenHiddenItemCount = Number(acceptance.next_open_check_due_hidden_work_item_count || 0);
  const nextOpenSymbols = (acceptance.next_open_check_due_symbols || []).slice(0, 5).filter(Boolean).join(", ");
  const nextOpenHorizons = (acceptance.next_open_check_due_horizons || []).filter(Boolean).join(", ");
  const nextOpenFocus = focusCountSummary(acceptance.next_open_check_due_focus_counts || {}, 3);
  const nextOpenMeasurementGaps = focusCountSummary(acceptance.next_open_check_due_measurement_missing_field_counts || {}, 3);
  const nextOpenQueue = nextOpenItemCount
    ? `queue ${nextOpenVisibleItemCount}/${nextOpenItemCount} visible${nextOpenHiddenItemCount ? `, ${nextOpenHiddenItemCount} hidden` : ""}`
    : "";
  const nextOpenScope = [
    nextOpenHorizons,
    nextOpenSymbols ? `symbols ${nextOpenSymbols}` : "",
    nextOpenFocus ? `focus ${nextOpenFocus}` : "",
    nextOpenMeasurementGaps ? `missing ${nextOpenMeasurementGaps}` : "",
    nextOpenQueue,
  ].filter(Boolean).join(" · ");
  const nextOpenDetail = acceptance.next_open_check_due_date
    ? ` · next ${acceptance.next_open_check_due_date} (${nextOpenLabelCount} ${pluralize("label", nextOpenLabelCount)} / ${nextOpenItemCount} ${pluralize("work item", nextOpenItemCount)}${nextOpenScope ? ` · ${nextOpenScope}` : ""})`
    : "";
  const acceptanceSummary = acceptance.check_count
    ? `${acceptance.open_check_count || 0}/${acceptance.check_count || 0} checks open · ${acceptance.open_label_count || 0} labels${nextOpenDetail}`
    : "";
  const measurementGapSummary = measurementGapRows.length
    ? `measurement gaps ${measurementGapPlan.label_count || measurementGapRows.length} labels / ${measurementGapPlan.work_item_count || measurementGapRows.length} work items`
    : "";
  const visibilityDetail = [visibility, acceptanceSummary, measurementGapSummary].filter(Boolean).join(" · ");
  target.innerHTML = [
    `<div class="mini-row">
      <span>
        <strong>Review queue</strong>
        <small>${escapeHtml(summary)}</small>
      </span>
      <span>
        <strong>${escapeHtml(rows[0]?.due_date || "Pending")}</strong>
        <small>${escapeHtml(visibilityDetail)}</small>
      </span>
    </div>`,
    ...dueDates.slice(0, 5).map(alignmentReviewDueDateRow),
    ...rows.map(alignmentReviewRow),
  ].join("");
}

function alignmentReviewDueDateRow(row) {
  const focusCounts = row.focus_counts || {};
  const detail = [
    focusCounts.external_disagreement ? `${focusCounts.external_disagreement.work_item_count || 0} disagreement` : "",
    focusCounts.missed_external_signal ? `${focusCounts.missed_external_signal.work_item_count || 0} missed external` : "",
    focusCounts.internal_signal_only ? `${focusCounts.internal_signal_only.work_item_count || 0} internal only` : "",
  ].filter(Boolean).join(" · ");
  return `
    <div class="mini-row">
      <span>
        <strong>${escapeHtml(row.due_date || "")}</strong>
        <small>${escapeHtml(detail || "No focus buckets")}</small>
      </span>
      <span>
        <strong>${row.work_item_count || 0} work items</strong>
        <small>${row.label_count || 0} labels</small>
      </span>
    </div>
  `;
}

function focusCountSummary(focusCounts, limit = 3) {
  return Object.entries(focusCounts || {})
    .map(([focus, counts]) => ({
      focus,
      labelCount: Number(counts?.label_count || 0),
      workItemCount: Number(counts?.work_item_count || 0),
    }))
    .filter((row) => row.labelCount > 0 || row.workItemCount > 0)
    .sort((a, b) => (b.labelCount - a.labelCount) || (b.workItemCount - a.workItemCount) || a.focus.localeCompare(b.focus))
    .slice(0, limit)
    .map((row) => `${labelize(row.focus)} ${row.labelCount} ${pluralize("label", row.labelCount)}/${row.workItemCount} ${pluralize("work item", row.workItemCount)}`)
    .join(" · ");
}

function alignmentReviewRow(row) {
  const labelCount = Number(row.external_alignment_review_label_count || 1);
  const checks = row.external_alignment_review_acceptance_checks || [];
  const openCheckCount = Number(row.external_alignment_review_open_check_count ?? checks.filter((check) => check.status !== "passed").length);
  const traceId = row.external_alignment_review_id || row.source_outcome_id || row.source_trial_id || "";
  const sourceToken = traceId ? ` · ${String(traceId).slice(0, 8)}` : "";
  const meta = `${row.due_date || "Pending"} · ${labelCount} ${pluralize("label", labelCount)}${sourceToken}`;
  const detail = [
    labelize(row.external_alignment_review_focus || row.external_alignment || ""),
    checks.length ? `${openCheckCount}/${checks.length} open checks` : "",
    row.external_alignment_review_measurement_plan?.summary || "",
    row.external_alignment_review_priority_reason || "",
    row.external_alignment_review_learning_action || "",
    row.external_alignment_review_reason || "",
  ].filter(Boolean).join(" · ");
  const session = row.session ? ` · ${labelize(row.session)}` : "";
  return `
    <article class="outcome-row">
      <div>
        <strong>${escapeHtml(row.symbol || "")}</strong>
        <span>${escapeHtml(labelize(row.external_alignment || ""))} / ${escapeHtml(row.horizon || "")}${escapeHtml(session)}</span>
      </div>
      <div>
        <strong>${escapeHtml(meta)}</strong>
        <span>${escapeHtml(detail)}</span>
      </div>
    </article>
  `;
}

function renderCoverageGapPlan() {
  const plan = backtest().pending_external_coverage_gap_plan || {};
  const rows = plan.priority_rows || [];
  const target = document.getElementById("externalCoverageGapPlan");
  if (!target) return;
  if (!rows.length) {
    target.innerHTML = empty("No priority coverage gaps.");
    return;
  }
  const projected = `${plan.projected_external_long_horizon_count_after_priority_backfill || 0}/${plan.minimum_external_long_horizon_required || 0}`;
  const readiness = plan.external_learning_ready_after_priority_backfill ? "ready after priority" : "still short";
  const checkCount = plan.priority_acceptance_check_count || (rows[0]?.external_coverage_acceptance_checks || []).length;
  const openCheckCount = plan.priority_open_acceptance_check_count ?? checkCount;
  const detail = `${plan.additional_external_coverage_needed || rows.length} needed · ${plan.candidate_gap_count || rows.length} candidates · ${openCheckCount}/${checkCount} checks open · ${projected} ${readiness}`;
  target.innerHTML = `
    <div class="mini-row">
      <span>
        <strong>Priority coverage</strong>
        <small>${escapeHtml(detail)}</small>
      </span>
      <span>
        <strong>${rows.length} labels</strong>
        <small>${escapeHtml((plan.priority_symbols || []).join(", "))}</small>
      </span>
    </div>
  `;
}

function renderOutcomes(targetId, rows, emptyText) {
  document.getElementById(targetId).innerHTML = rows.length
    ? rows.map(outcomeRow).join("")
    : empty(emptyText);
}

function outcomeRow(row) {
  const detail = row.external_alignment_review_reason || row.external_coverage_gap_action || row.external_coverage_gap_reason || labelize(row.bucket || "");
  const meta = row.external_coverage_gap_id || row.due_date || "Pending";
  return `
    <article class="outcome-row">
      <div>
        <strong>${escapeHtml(row.symbol || "")}</strong>
        <span>${escapeHtml(labelize(row.trade_action || ""))} / ${escapeHtml(row.horizon || "")}</span>
      </div>
      <div>
        <strong>${row.decision_forward_return_pct == null ? escapeHtml(meta) : formatPct(row.decision_forward_return_pct)}</strong>
        <span>${escapeHtml(detail)}</span>
      </div>
    </article>
  `;
}

function backtest() {
  return payload?.backtest || {};
}

function summaryCard(card) {
  return `
    <article class="summary-card">
      <span>${escapeHtml(card.label)}</span>
      <strong>${escapeHtml(card.value)}</strong>
      <small>${escapeHtml(card.detail)}</small>
    </article>
  `;
}

function empty(text) {
  return `<p class="empty">${escapeHtml(text)}</p>`;
}

function formatPct(value) {
  if (value == null || Number.isNaN(Number(value))) return "n/a";
  return `${number.format(Number(value))}%`;
}

function formatRatio(value) {
  if (value == null || Number.isNaN(Number(value))) return "n/a";
  return `${number.format(Number(value) * 100)}%`;
}

function labelize(value = "") {
  return String(value || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function pluralize(word, count) {
  return Number(count) === 1 ? word : `${word}s`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;",
  }[char]));
}
