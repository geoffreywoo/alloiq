const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

let payload = null;

init().catch((error) => {
  document.body.insertAdjacentHTML("afterbegin", `<p class="stale-banner">Optimizer failed to load: ${escapeHtml(error.message)}</p>`);
});

async function init() {
  const response = await fetch(`/data/latest.json?ts=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  payload = await response.json();
  render();
}

function render() {
  const asOf = payload.as_of || "";
  document.getElementById("optimizerDate").textContent = asOf ? `Snapshot ${asOf}` : "Public snapshot";
  renderSummary();
  renderTargets();
  renderActions();
  renderConstraints();
}

function renderSummary() {
  const sizing = sizingPlan();
  const actions = payload.portfolio_benchmark?.action_queue || [];
  const portfolio = payload.portfolio || {};
  const budget = sizing.rebalance_budget || {};
  const turnover = actions.reduce((sum, row) => sum + Math.abs(Number(row.recommended_delta_weight || 0)), 0);
  const adds = actions.filter((row) => Number(row.recommended_delta_weight || 0) > 0);
  const trims = actions.filter((row) => Number(row.recommended_delta_weight || 0) < 0);
  const cards = [
    {
      label: "Model targets",
      value: String(sizing.target_count || targets().length || 0),
      detail: sizing.version || "target-weight sizing",
    },
    {
      label: "Trade queue",
      value: String(actions.length),
      detail: `${adds.length} adds, ${trims.length} trims`,
    },
    {
      label: "Equity target pool",
      value: formatWeight(sizing.target_total_weight ?? portfolio.equity_weight ?? 1),
      detail: "Model targets normalize inside the equity sleeve",
    },
    {
      label: "Cash reserve",
      value: formatWeight(sizing.cash_reserve_weight ?? portfolio.cash_weight ?? 0),
      detail: `Draw ${formatWeight(budget.cash_deployed_weight || 0)} today; after queue ${formatWeight(budget.post_trade_cash_weight ?? sizing.post_trade_cash_weight ?? portfolio.cash_weight)}`,
    },
    {
      label: "Cash draw cap",
      value: formatWeight(sizing.cash_deployable_weight ?? budget.max_cash_deploy_weight ?? 0),
      detail: sizing.cash_policy ? labelize(sizing.cash_policy) : "Capped high-conviction add funding",
    },
    {
      label: "Turnover",
      value: formatWeight(turnover),
      detail: "Sum of current recommended deltas",
    },
    {
      label: "One-ticket cap",
      value: formatWeight(sizing.limits?.max_one_ticket_delta),
      detail: `Single-name cap ${formatWeight(sizing.limits?.max_single_name_weight)}`,
    },
  ];
  document.getElementById("optimizerSummary").innerHTML = cards.map(summaryCard).join("");
}

function renderTargets() {
  const rows = targets();
  document.getElementById("optimizerCount").textContent = `${rows.length} targets`;
  document.getElementById("targetList").innerHTML = rows.length
    ? rows.map(targetTemplate).join("")
    : empty("No model targets in this snapshot.");
}

function renderActions() {
  const actions = payload.portfolio_benchmark?.action_queue || [];
  document.getElementById("optimizerActions").innerHTML = actions.length
    ? actions.slice(0, 12).map(actionTemplate).join("")
    : empty("No add/trim queue in this snapshot.");
}

function renderConstraints() {
  const counts = {};
  targets().forEach((row) => {
    (row.active_constraints || row.risk_flags || []).forEach((flag) => {
      counts[flag] = (counts[flag] || 0) + 1;
    });
  });
  const rows = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  document.getElementById("constraintMap").innerHTML = rows.length
    ? rows.map(([flag, count]) => `
      <article class="constraint-card">
        <strong>${escapeHtml(labelize(flag))}</strong>
        <span>${escapeHtml(String(count))} names</span>
      </article>
    `).join("")
    : empty("No active sizing constraints across the model targets.");
}

function targetTemplate(row) {
  const current = Number(row.current_weight || row.portfolio_weight || 0);
  const target = Number(row.model_target_weight ?? row.target_weight ?? current);
  const delta = Number(row.recommended_delta_weight || 0);
  const max = Math.max(current, target, 0.01);
  return `
    <article class="target-row">
      <div class="target-head">
        <div>
          <strong>${escapeHtml(row.symbol || "Symbol")}</strong>
          <span>${escapeHtml(labelize(row.bucket || "unmapped"))}</span>
        </div>
        <strong class="${delta > 0 ? "positive" : delta < 0 ? "negative" : ""}">${escapeHtml(formatSignedWeight(delta))}</strong>
      </div>
      <div class="target-bars">
        <div>
          <span>Current</span>
          <div class="bar-track"><div class="bar-fill graphite-fill" style="width:${barWidth(current, max)}%"></div></div>
          <strong>${escapeHtml(formatWeight(current))}</strong>
        </div>
        <div>
          <span>Model target</span>
          <div class="bar-track"><div class="bar-fill blue-fill" style="width:${barWidth(target, max)}%"></div></div>
          <strong>${escapeHtml(formatWeight(target))}</strong>
        </div>
      </div>
      <p>${escapeHtml(row.why_this_size || row.sizing_rationale || "")}</p>
      <div class="tag-row">
        <span class="tag">Expected ${escapeHtml(formatPct(row.risk_adjusted_expected_return))}</span>
        <span class="tag">Company ${escapeHtml(number.format(row.company_underwriting_score || 0))}</span>
        <span class="tag">Sector ${escapeHtml(number.format(row.sector_setup_score || 0))}</span>
        <span class="tag">Max ${escapeHtml(formatWeight(row.max_allowed_weight))}</span>
        <span class="tag">${escapeHtml(labelize(row.verdict || row.trade_action || "study"))}</span>
      </div>
    </article>
  `;
}

function actionTemplate(action) {
  const delta = Number(action.recommended_delta_weight || 0);
  return `
    <article class="rebalance-row">
      <div>
        <strong>${escapeHtml(action.symbol || "Action")}</strong>
        <p>${escapeHtml(action.sizing_summary || action.action || "")}</p>
      </div>
      <div class="rebalance-metrics">
        <span>Current ${escapeHtml(formatWeight(action.current_weight ?? action.portfolio_weight))}</span>
        <span>After ${escapeHtml(formatWeight(action.post_action_weight ?? action.target_weight))}</span>
        <span>Model ${escapeHtml(formatWeight(action.model_target_weight ?? action.target_weight))}</span>
        <span>${escapeHtml(labelize(action.funding_source || "no_trade"))}</span>
        <strong class="${delta > 0 ? "positive" : delta < 0 ? "negative" : ""}">${escapeHtml(formatSignedWeight(delta))}</strong>
      </div>
    </article>
  `;
}

function sizingPlan() {
  return payload?.portfolio_benchmark?.sizing_plan || {};
}

function targets() {
  return (sizingPlan().targets || []).slice().sort((a, b) => {
    const absDelta = Math.abs(Number(b.recommended_delta_weight || 0)) - Math.abs(Number(a.recommended_delta_weight || 0));
    return absDelta || Number(b.risk_adjusted_expected_return || 0) - Number(a.risk_adjusted_expected_return || 0);
  });
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

function barWidth(value, max) {
  return Math.max(2, Math.min(100, (Number(value || 0) / Math.max(Number(max || 0.01), 0.01)) * 100));
}

function empty(text) {
  return `<p class="empty">${escapeHtml(text)}</p>`;
}

function formatPct(value) {
  if (value == null || Number.isNaN(Number(value))) return "n/a";
  return `${number.format(Number(value))}%`;
}

function formatWeight(value) {
  if (value == null || Number.isNaN(Number(value))) return "n/a";
  return `${number.format(Number(value) * 100)}%`;
}

function formatSignedWeight(value) {
  if (value == null || Number.isNaN(Number(value))) return "0%";
  const numeric = Number(value) * 100;
  return `${numeric > 0 ? "+" : ""}${number.format(numeric)}%`;
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
