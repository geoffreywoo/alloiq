const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

const bucketColors = {
  frontier_ai_platforms: "#2558d5",
  semis_networking_hbm: "#08745f",
  neocloud_datacenters: "#b5681e",
  power_grid_gas_nuclear: "#0f7580",
  ai_software_winners: "#66518d",
  ai_enabled_financials: "#69752d",
  disrupted_incumbents: "#b04449",
  cash_reserves: "#8a8173",
  unmapped: "#5a6673",
};

const bucketThesis = {
  frontier_ai_platforms: "Own the platforms most likely to turn AI usage into durable revenue, retention, and operating leverage.",
  semis_networking_hbm: "Own the choke points in AI compute: accelerators, HBM, networking, and systems that capture hyperscaler capex.",
  ai_software_winners: "Own software names where AI can improve product velocity, pricing power, or net retention.",
  power_grid_gas_nuclear: "Own energy and grid constraints that become more valuable as data-center power demand compounds.",
  neocloud_datacenters: "Own AI infrastructure and data-center capacity when utilization, customer quality, and financing terms are attractive.",
  ai_enabled_financials: "Own financials where AI improves underwriting, distribution, fraud, or operating efficiency.",
  disrupted_incumbents: "Track vulnerable incumbents where AI may compress margins or break legacy distribution.",
  cash_reserves: "Hold dry powder separately from ex-cash equity comparisons; deploy only when explicit add targets are funded.",
  unmapped: "Positions that still need a cleaner thesis bucket before they deserve more size.",
};

const bucketLabels = {
  frontier_ai_platforms: "Frontier AI Platforms",
  semis_networking_hbm: "Semis / Networking / HBM",
  ai_software_winners: "AI Software Winners",
  power_grid_gas_nuclear: "Power / Grid / Nuclear",
  neocloud_datacenters: "Neocloud / Datacenters",
  ai_enabled_financials: "AI-enabled Financials",
  disrupted_incumbents: "Disrupted Incumbents",
  cash_reserves: "Cash Reserves",
  unmapped: "Unmapped",
};

let payload = null;

init().catch((error) => {
  document.body.insertAdjacentHTML("afterbegin", `<p class="stale-banner">Portfolio page failed to load: ${escapeHtml(error.message)}</p>`);
});

async function init() {
  const response = await fetch(`/data/latest.json?ts=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  payload = await response.json();
  render();
  document.getElementById("copyWeightsButton")?.addEventListener("click", copyWeightsCsv);
}

function render() {
  const asOf = payload.as_of || "";
  document.title = `Geoffrey Woo Portfolio - ${asOf}`;
  document.getElementById("portfolioDate").textContent = asOf ? `Snapshot ${asOf}` : "Public snapshot";
  renderSummary();
  renderWeightTable();
  renderBucketThesis();
  renderConcentrationMap();
  renderAntiFundGrowth();
  renderRebalanceList();
  renderAttribution();
}

function renderSummary() {
  const portfolio = payload.portfolio || {};
  const benchmark = payload.portfolio_benchmark || {};
  const symbols = sortedSymbols();
  const buckets = sortedBuckets();
  const topFive = sumWeights(symbols.slice(0, 5));
  const topTen = sumWeights(symbols.slice(0, 10));
  const topBucket = buckets[0] || {};
  const cashWeight = Number(portfolio.cash_weight || 0);
  const aiPeer = (benchmark.benchmarks || []).find((row) => row.name === "AI Thesis Core median proxy")
    || (benchmark.benchmarks || [])[0];
  const summary = [
    {
      label: "Public positions",
      value: String(portfolio.symbol_count || symbols.length || 0),
      detail: "Weights only, no share counts.",
    },
    {
      label: "Top 5 concentration",
      value: formatWeight(topFive),
      detail: `Top 10: ${formatWeight(topTen)}.`,
    },
    {
      label: "Largest thesis bucket",
      value: labelize(topBucket.bucket || "n/a"),
      detail: `${formatWeight(topBucket.weight)} of ex-cash public weights.`,
    },
    {
      label: "Cash reserve",
      value: formatWeight(cashWeight),
      detail: cashWeight > 0 ? "Excluded from comparison weights." : "No cash sleeve in this snapshot.",
    },
    ...["3M", "YTD", "1Y"].map((label) => returnWindowCard(benchmark, label)),
    {
      label: "Vs AI peer proxy",
      value: aiPeer ? formatPp(aiPeer.portfolio_vs_benchmark) : "n/a",
      detail: aiPeer ? `${aiPeer.name}, delayed public 13F proxy.` : "Peer proxy unavailable.",
    },
  ];
  document.getElementById("portfolioSummary").innerHTML = summary.map(summaryCard).join("");
}

function renderWeightTable() {
  const actionsBySymbol = Object.fromEntries((payload.portfolio_benchmark?.action_queue || []).map((row) => [row.symbol, row]));
  const rows = sortedSymbols();
  document.getElementById("portfolioWeightTable").innerHTML = rows.length
    ? rows.map((row, index) => {
        const action = actionsBySymbol[row.symbol] || {};
        const delta = Number(action.recommended_delta_weight || 0);
        const actionLabel = row.is_cash ? "Reserve" : action.trade_action ? labelize(action.trade_action) : "Hold / study";
        return `
          <article class="portfolio-weight-row">
            <div class="portfolio-rank">${index + 1}</div>
            <div>
              <strong>${escapeHtml(row.symbol)}</strong>
              <span>${escapeHtml(labelize(row.bucket))}</span>
            </div>
            <div class="portfolio-weight-visual">
              <div class="bar-track">
                <div class="bar-fill" style="width:${barWidth(row.weight)}%;background:${bucketColors[row.bucket] || bucketColors.unmapped}"></div>
              </div>
              <strong>${escapeHtml(formatWeight(row.weight))}</strong>
            </div>
            <div class="portfolio-copy-note">
              <span class="${delta > 0 ? "positive" : delta < 0 ? "negative" : ""}">${escapeHtml(delta ? formatSignedWeight(delta) : actionLabel)}</span>
              <small>${escapeHtml(action.post_action_weight == null ? "No active resize" : `After move ${formatWeight(action.post_action_weight)}`)}</small>
            </div>
          </article>
        `;
      }).join("")
    : empty("No public portfolio weights are available.");
}

function renderBucketThesis() {
  const buckets = sortedBuckets();
  document.getElementById("bucketThesisList").innerHTML = buckets.length
    ? buckets.map((bucket) => `
      <article class="bucket-thesis-card">
        <div class="bucket-thesis-head">
          <span class="dot" style="background:${bucketColors[bucket.bucket] || bucketColors.unmapped}"></span>
          <strong>${escapeHtml(labelize(bucket.bucket))}</strong>
          <span>${escapeHtml(formatWeight(bucket.weight))}</span>
        </div>
        <p>${escapeHtml(bucketThesis[bucket.bucket] || bucketThesis.unmapped)}</p>
      </article>
    `).join("")
    : empty("No bucket weights are available.");
}

function renderConcentrationMap() {
  const symbols = sortedSymbols();
  const buckets = sortedBuckets();
  document.getElementById("concentrationMap").innerHTML = `
    <div class="copy-stack">
      <h4>Top holdings</h4>
      <div class="bar-list">${symbols.slice(0, 12).map((row) => weightBar(row.symbol, row.weight, row.bucket)).join("")}</div>
    </div>
    <div class="copy-stack">
      <h4>Thesis mix</h4>
      <div class="bar-list">${buckets.map((row) => weightBar(labelize(row.bucket), row.weight, row.bucket)).join("")}</div>
    </div>
  `;
}

function renderAntiFundGrowth() {
  const growth = payload.anti_fund_growth || {};
  const positions = growth.positions || [];
  const title = document.getElementById("portfolioAntiFundTitle");
  const description = document.getElementById("portfolioAntiFundDescription");
  const link = document.getElementById("portfolioAntiFundLink");
  const summary = document.getElementById("portfolioAntiFundSummary");
  const target = document.getElementById("portfolioAntiFundWeights");
  if (!target) return;
  if (title) {
    title.textContent = growth.name || "Anti Fund Growth I, LP";
  }
  if (description) {
    description.textContent = growth.description
      || "Geoffrey Woo's affiliated private tech crossover fund. Growth I is not a public-stock fund and is not included in the public position weights above.";
  }
  if (link) {
    link.href = growth.marketing_url || "https://antifund.com";
  }
  if (summary) {
    summary.textContent = growth.as_of
      ? `${growth.as_of} | affiliated private fund | weights only`
      : "Affiliated private fund | weights only";
  }
  target.innerHTML = positions.length
    ? positions.map((row, index) => privateWeightTemplate(row, index)).join("")
    : empty("No affiliated private-fund weights in this snapshot.");
}

function privateWeightTemplate(row, index) {
  const palette = ["#0e151b", "#08745f", "#2558d5", "#b5681e", "#0f7580", "#66518d", "#69752d", "#b04449", "#5a6673"];
  const color = palette[index % palette.length];
  return `
    <article class="private-weight-card">
      <div class="private-weight-head">
        <strong>${escapeHtml(row.company || "Company")}</strong>
        <span>${escapeHtml(formatWeight(row.weight))}</span>
      </div>
      <div class="bar-track"><div class="bar-fill" style="width:${barWidth(row.weight)}%;background:${color}"></div></div>
      <small>Affiliated private-growth book weight</small>
    </article>
  `;
}

function renderRebalanceList() {
  const actions = payload.portfolio_benchmark?.action_queue || [];
  document.getElementById("portfolioRebalanceList").innerHTML = actions.length
    ? actions.slice(0, 8).map((action) => {
        const delta = Number(action.recommended_delta_weight || 0);
        return `
          <article class="rebalance-row">
            <div>
              <strong>${escapeHtml(action.symbol)}</strong>
              <p>${escapeHtml(action.action || action.why || "")}</p>
            </div>
            <div class="rebalance-metrics">
              <span>Current ${escapeHtml(formatWeight(action.portfolio_weight))}</span>
              <span>After ${escapeHtml(formatWeight(action.post_action_weight ?? action.portfolio_weight))}</span>
              <strong class="${delta > 0 ? "positive" : delta < 0 ? "negative" : ""}">${escapeHtml(delta ? tradeWeightLabel(delta) : "Hold")}</strong>
            </div>
          </article>
        `;
      }).join("")
    : empty("No active add/trim trades in this snapshot.");
}

function renderAttribution() {
  const rows = [
    ...(payload.portfolio_benchmark?.top_contributors || []),
    ...(payload.portfolio_benchmark?.top_detractors || []),
  ]
    .filter((row) => row.contribution_pct != null)
    .sort((a, b) => Math.abs(Number(b.contribution_pct || 0)) - Math.abs(Number(a.contribution_pct || 0)))
    .slice(0, 10);
  document.getElementById("portfolioAttribution").innerHTML = rows.length
    ? rows.map((row) => {
        const contribution = Number(row.contribution_pct || 0);
        return `
          <article class="attribution-card">
            <strong>${escapeHtml(row.symbol)}</strong>
            <span class="${contribution >= 0 ? "positive" : "negative"}">${escapeHtml(formatPp(contribution))}</span>
            <small>${escapeHtml(labelize(row.bucket || "portfolio"))} | ${escapeHtml(formatWeight(row.weight))} weight | ${escapeHtml(formatPct(row.five_day_pct))} 5D price</small>
          </article>
        `;
      }).join("")
    : empty("No recent return-driver data in this public snapshot.");
}

async function copyWeightsCsv() {
  const rows = sortedSymbols();
  const actionsBySymbol = Object.fromEntries((payload.portfolio_benchmark?.action_queue || []).map((row) => [row.symbol, row]));
  const csv = [
    ["symbol", "weight_pct", "bucket", "target_delta_pct", "after_move_weight_pct"],
    ...rows.map((row) => {
      const action = actionsBySymbol[row.symbol] || {};
      return [
        row.symbol,
        percentNumber(row.weight),
        labelize(row.bucket),
        percentNumber(action.recommended_delta_weight || 0),
        percentNumber(action.post_action_weight ?? row.weight),
      ];
    }),
  ].map((row) => row.map(csvCell).join(",")).join("\n");
  try {
    await navigator.clipboard.writeText(csv);
    setCopyStatus("Copied sanitized ticker weights. Paste into a sheet and size by your own portfolio value.");
  } catch {
    fallbackCopy(csv);
    setCopyStatus("Copied via fallback text box. Paste into a sheet and size by your own portfolio value.");
  }
}

function fallbackCopy(text) {
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "readonly");
  area.style.position = "fixed";
  area.style.left = "-9999px";
  document.body.appendChild(area);
  area.select();
  document.execCommand("copy");
  area.remove();
}

function setCopyStatus(message) {
  const element = document.getElementById("copyStatus");
  if (element) element.textContent = message;
}

function sortedSymbols() {
  return [...(payload?.portfolio?.by_symbol || [])].sort((a, b) => {
    if (Boolean(a.is_cash) !== Boolean(b.is_cash)) return a.is_cash ? 1 : -1;
    return Number(b.weight || 0) - Number(a.weight || 0);
  });
}

function sortedBuckets() {
  return [...(payload?.portfolio?.by_bucket || [])].sort((a, b) => Number(b.weight || 0) - Number(a.weight || 0));
}

function returnWindowCard(benchmark, label) {
  const row = horizonFor(benchmark, label);
  return {
    label: `${label} return proxy`,
    value: row ? formatPct(row.portfolio_return) : "n/a",
    detail: row ? `${formatPlainPct(row.price_coverage_pct)} priced | ex-cash proxy` : "Window unavailable.",
  };
}

function horizonFor(benchmark, label) {
  const target = String(label || "").toLowerCase();
  return (benchmark.horizon_returns || []).find((row) => (
    String(row.label || "").toLowerCase() === target || String(row.key || "").toLowerCase() === target
  ));
}

function summaryCard(item) {
  return `
    <article class="kpi">
      <span>${escapeHtml(item.label)}</span>
      <strong>${escapeHtml(item.value)}</strong>
      <small>${escapeHtml(item.detail)}</small>
    </article>
  `;
}

function weightBar(label, weight, bucket) {
  return `
    <div class="bar-row">
      <strong>${escapeHtml(label)}</strong>
      <div class="bar-track"><div class="bar-fill" style="width:${barWidth(weight)}%;background:${bucketColors[bucket] || bucketColors.unmapped}"></div></div>
      <div class="metric">${escapeHtml(formatWeight(weight))}</div>
    </div>
  `;
}

function sumWeights(rows) {
  return rows.reduce((sum, row) => sum + Number(row.weight || 0), 0);
}

function barWidth(weight) {
  return Math.max(2, Math.min(100, Number(weight || 0) * 100));
}

function percentNumber(value) {
  return number.format(Number(value || 0) * 100);
}

function csvCell(value) {
  const text = String(value ?? "");
  return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function formatWeight(value) {
  if (value == null || Number.isNaN(Number(value))) return "n/a";
  return `${number.format(Number(value) * 100)}%`;
}

function formatSignedWeight(value) {
  const numeric = Number(value || 0);
  return `${numeric >= 0 ? "+" : ""}${formatWeight(numeric)}`;
}

function tradeWeightLabel(value) {
  const numeric = Number(value || 0);
  if (numeric > 0) return `Add ${formatWeight(Math.abs(numeric))}`;
  if (numeric < 0) return `Trim ${formatWeight(Math.abs(numeric))}`;
  return "Hold";
}

function formatPct(value) {
  if (value == null || Number.isNaN(Number(value))) return "n/a";
  return `${Number(value) >= 0 ? "+" : ""}${number.format(Number(value))}%`;
}

function formatPlainPct(value) {
  if (value == null || Number.isNaN(Number(value))) return "n/a";
  return `${number.format(Number(value))}%`;
}

function formatPp(value) {
  if (value == null || Number.isNaN(Number(value))) return "n/a";
  return `${Number(value) >= 0 ? "+" : ""}${number.format(Number(value))} pp`;
}

function labelize(value) {
  if (bucketLabels[value]) return bucketLabels[value];
  return String(value || "")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function empty(message) {
  return `<div class="empty">${escapeHtml(message)}</div>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
