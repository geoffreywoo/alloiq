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
  renderGroup("externalStatusTable", backtest().by_external_feed_status || [], "No external feed status labels yet.");
  renderGroup("externalCoverageTable", backtest().by_external_coverage || [], "No external coverage labels yet.");
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

function renderOutcomes(targetId, rows, emptyText) {
  document.getElementById(targetId).innerHTML = rows.length
    ? rows.map(outcomeRow).join("")
    : empty(emptyText);
}

function outcomeRow(row) {
  return `
    <article class="outcome-row">
      <div>
        <strong>${escapeHtml(row.symbol || "")}</strong>
        <span>${escapeHtml(labelize(row.trade_action || ""))} / ${escapeHtml(row.horizon || "")}</span>
      </div>
      <div>
        <strong>${row.decision_forward_return_pct == null ? escapeHtml(row.due_date || "Pending") : formatPct(row.decision_forward_return_pct)}</strong>
        <span>${escapeHtml(labelize(row.bucket || ""))}</span>
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

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;",
  }[char]));
}
