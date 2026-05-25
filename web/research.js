const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

let payload = null;
let selectedSymbol = "";
let searchText = "";

init().catch((error) => {
  document.body.insertAdjacentHTML("afterbegin", `<p class="stale-banner">Research failed to load: ${escapeHtml(error.message)}</p>`);
});

async function init() {
  const response = await fetch(`/data/latest.json?ts=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  payload = await response.json();
  selectedSymbol = new URLSearchParams(window.location.search).get("symbol")?.toUpperCase() || "";
  render();
  document.getElementById("researchSearch")?.addEventListener("input", (event) => {
    searchText = event.target.value.toLowerCase();
    renderRanks();
  });
}

function render() {
  const asOf = payload.as_of || "";
  document.getElementById("researchDate").textContent = asOf ? `Snapshot ${asOf}` : "Public snapshot";
  renderSummary();
  renderRanks();
  renderDetail(selectedSymbol || firstItem()?.symbol || "");
}

function renderSummary() {
  const book = payload.research_book || {};
  const outcomes = payload.outcome_diagnostics || {};
  const sizing = payload.portfolio_benchmark?.sizing_plan || {};
  const company = payload.company_underwriting || {};
  const sector = payload.sector_underwriting || {};
  const top = firstItem();
  const cards = [
    {
      label: "Research names",
      value: String(book.item_count || items().length || 0),
      detail: `${book.horizon || "3-12m"} company-first scenarios`,
    },
    {
      label: "Company layer",
      value: String(company.item_count || 0),
      detail: `${company.review_count || 0} names need deeper review`,
    },
    {
      label: "Sector layer",
      value: String(sector.item_count || 0),
      detail: "Sector setup gates target size",
    },
    {
      label: "Top expected return",
      value: top ? top.symbol : "n/a",
      detail: top ? `${formatPct(top.risk_adjusted_expected_return)} risk-adjusted` : "No ranked items",
    },
    {
      label: "Sizing targets",
      value: String(sizing.target_count || 0),
      detail: `${sizing.action_count || 0} current add/trim items`,
    },
    {
      label: "Training examples",
      value: String(outcomes.current_training_example_count || 0),
      detail: labelize(outcomes.status || "awaiting_forward_returns"),
    },
  ];
  document.getElementById("researchSummary").innerHTML = cards.map(summaryCard).join("");
}

function renderRanks() {
  const rows = items().filter((item) => !searchText || JSON.stringify(item).toLowerCase().includes(searchText));
  const target = document.getElementById("researchRankList");
  document.getElementById("researchCount").textContent = `${rows.length} names`;
  target.innerHTML = rows.length ? rows.map(rankTemplate).join("") : empty("No research items match this search.");
  target.querySelectorAll("[data-symbol]").forEach((button) => {
    button.addEventListener("click", () => {
      renderDetail(button.dataset.symbol || "");
      window.history.replaceState({}, "", `/research?symbol=${encodeURIComponent(button.dataset.symbol || "")}`);
    });
  });
}

function renderDetail(symbol) {
  const item = items().find((row) => row.symbol === symbol) || firstItem();
  if (!item) {
    document.getElementById("researchDetail").innerHTML = empty("No ticker detail available.");
    return;
  }
  selectedSymbol = item.symbol;
  const action = actionsBySymbol()[item.symbol] || {};
  document.getElementById("researchDetailTitle").textContent = `${item.symbol} Deep Dive`;
  document.getElementById("researchDetailMeta").textContent = `${labelize(item.verdict)} | ${labelize(item.bucket)}`;
  document.getElementById("researchDetail").innerHTML = `
    <section class="research-score-grid">
      ${metricTile("Risk-adjusted", formatPct(item.risk_adjusted_expected_return), "12m expected return after risk")}
      ${metricTile("Company", `${number.format(item.company_underwriting_score || 0)}/100`, "Bottom-up underwriting")}
      ${metricTile("Sector", `${number.format(item.sector_setup_score || 0)}/100`, "Bucket setup")}
      ${metricTile("Bull/Base/Bear", `${formatPct(item.bull_return_12m)} / ${formatPct(item.base_return_12m)} / ${formatPct(item.bear_return_12m)}`, "Scenario range")}
      ${metricTile("Evidence", `${number.format(item.evidence_quality || 0)}/100`, "Source and signal quality")}
      ${metricTile("Drawdown Risk", `${number.format(item.drawdown_risk || 0)}/100`, "Concentration, crowding, hard-risk events")}
    </section>
    <article class="research-note">
      <h4>Company Thesis</h4>
      <p>${escapeHtml(item.thesis_summary || "")}</p>
    </article>
    <article class="research-note">
      <h4>Company Reason</h4>
      <p>${escapeHtml(item.company_reason || item.variant_view || "")}</p>
    </article>
    <article class="research-note-grid">
      ${noteBlock("Sector Setup", item.sector_reason)}
      ${noteBlock("Catalyst Clock", item.catalyst_clock)}
      ${noteBlock("Valuation Setup", item.valuation_setup)}
      ${noteBlock("13F + Macro", item.tertiary_signal_summary || `${item.manager_signal || ""} ${item.macro_sensitivity || ""}`)}
    </article>
    ${decisionStackTemplate(item)}
    <article class="research-note">
      <h4>Current Sizing</h4>
      <p>${escapeHtml(action.sizing_summary || "No active model resize in this snapshot.")}</p>
      <div class="tag-row">
        <span class="tag">Current ${formatWeight(item.current_weight || 0)}</span>
        <span class="tag">Model target ${formatWeight(action.model_target_weight ?? action.target_weight ?? item.current_weight ?? 0)}</span>
        <span class="tag">Delta ${formatSignedWeight(action.recommended_delta_weight || 0)}</span>
        <span class="tag">Max allowed ${formatWeight(action.max_allowed_weight ?? 0)}</span>
        <span class="tag">${escapeHtml(labelize(action.funding_source || "no_trade"))}</span>
      </div>
    </article>
    <article class="research-note-grid">
      ${noteBlock("Risk", item.risk)}
      ${noteBlock("Falsifier", item.falsifier)}
      ${noteBlock("Increase Size If", action.increase_size_if)}
      ${noteBlock("Decrease Size If", action.decrease_size_if)}
    </article>
  `;
}

function rankTemplate(item) {
  const selected = item.symbol === selectedSymbol ? "selected" : "";
  return `
    <button class="research-rank-row ${selected}" type="button" data-symbol="${escapeAttribute(item.symbol)}">
      <span class="research-rank-number">#${escapeHtml(item.rank || "")}</span>
      <span>
        <strong>${escapeHtml(item.symbol)}</strong>
        <small>${escapeHtml(labelize(item.bucket))}</small>
      </span>
      <span class="research-rank-metrics">
        <strong>${escapeHtml(formatPct(item.risk_adjusted_expected_return))}</strong>
        <small>${escapeHtml(labelize(item.verdict))} | Co ${escapeHtml(number.format(item.company_underwriting_score || 0))}</small>
      </span>
    </button>
  `;
}

function decisionStackTemplate(item) {
  const stack = item.decision_stack || {};
  const rows = [
    ["Company", stack.company_underwriting_score ?? item.company_underwriting_score, stack.company_underwriting_weight ?? 0.6],
    ["Sector", stack.sector_setup_score ?? item.sector_setup_score, stack.sector_setup_weight ?? 0.2],
    ["13F", stack.manager_13f_score, stack.manager_13f_weight ?? 0.1],
    ["Macro", stack.macro_timing_risk_score, stack.macro_timing_risk_weight ?? 0.1],
  ];
  return `
    <article class="research-note">
      <h4>Decision Stack</h4>
      <div class="stack-bars">
        ${rows.map(([label, score, weight]) => `
          <div>
            <span>${escapeHtml(label)} ${escapeHtml(formatWeight(weight))}</span>
            <div class="bar-track"><div class="bar-fill blue-fill" style="width:${barWidth(score || 0, 100)}%"></div></div>
            <strong>${escapeHtml(number.format(score || 0))}</strong>
          </div>
        `).join("")}
      </div>
    </article>
  `;
}

function metricTile(label, value, detail) {
  return `
    <article class="summary-card">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <small>${escapeHtml(detail)}</small>
    </article>
  `;
}

function noteBlock(label, text) {
  return `
    <div class="research-note">
      <h4>${escapeHtml(label)}</h4>
      <p>${escapeHtml(text || "No current note.")}</p>
    </div>
  `;
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

function items() {
  return payload?.research_book?.items || [];
}

function firstItem() {
  return items()[0];
}

function barWidth(value, max) {
  return Math.max(2, Math.min(100, (Number(value || 0) / Math.max(Number(max || 1), 1)) * 100));
}

function actionsBySymbol() {
  return Object.fromEntries((payload?.portfolio_benchmark?.action_queue || []).map((row) => [row.symbol, row]));
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

function escapeAttribute(value) {
  return escapeHtml(value).replace(/`/g, "&#96;");
}
