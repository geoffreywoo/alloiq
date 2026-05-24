const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

const bucketLabels = {
  frontier_ai_platforms: "Frontier AI Platforms",
  semis_networking_hbm: "Semis / Networking / HBM",
  ai_software_winners: "AI Software Winners",
  power_grid_gas_nuclear: "Power / Grid / Nuclear",
  neocloud_datacenters: "Neocloud / Datacenters",
  ai_enabled_financials: "AI-enabled Financials",
  disrupted_incumbents: "Disrupted Incumbents",
  unmapped: "Unmapped",
};

const bucketColors = {
  frontier_ai_platforms: "#2558d5",
  semis_networking_hbm: "#08745f",
  neocloud_datacenters: "#b5681e",
  power_grid_gas_nuclear: "#0f7580",
  ai_software_winners: "#66518d",
  ai_enabled_financials: "#69752d",
  disrupted_incumbents: "#b04449",
  unmapped: "#5a6673",
};

let payload = null;

init().catch((error) => {
  document.body.insertAdjacentHTML("afterbegin", `<p class="stale-banner">Homepage failed to load: ${escapeHtml(error.message)}</p>`);
});

async function init() {
  const response = await fetch(`/data/latest.json?ts=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  payload = await response.json();
  render();
}

function render() {
  const asOf = payload.as_of || "";
  document.title = asOf ? `AlloIQ - GW AI-Max Portfolio ${asOf}` : "AlloIQ - GW AI-Max Portfolio";
  document.getElementById("homeDate").textContent = asOf ? `Snapshot ${asOf}` : "Public snapshot";
  renderSnapshot();
  renderPerformance();
  renderTopTrade();
  renderFreshness();
  renderTopWeights();
  renderBucketMix();
}

function renderSnapshot() {
  const symbols = sortedSymbols();
  const top = symbols[0];
  const count = payload.portfolio?.symbol_count || symbols.length || 0;
  document.getElementById("homeSnapshot").textContent = `${count} symbols`;
  const snapshot = document.querySelector("#homeSnapshot + small");
  if (snapshot) {
    snapshot.textContent = top ? `Largest weight: ${top.symbol} ${formatWeight(top.weight)}` : "Public weights only";
  }
}

function renderPerformance() {
  const primary = horizonFor("3M") || horizonFor("3m") || (payload.portfolio_benchmark?.horizon_returns || [])[0];
  const ytd = horizonFor("YTD") || horizonFor("ytd");
  const oneYear = horizonFor("1Y") || horizonFor("1y");
  const title = document.getElementById("homePerformance");
  const detail = document.getElementById("homePerformanceDetail");
  if (!title || !detail) return;
  if (!primary) {
    title.textContent = "n/a";
    detail.textContent = "No return windows in this snapshot.";
    return;
  }
  title.textContent = `${primary.label || "3M"} ${formatPct(primary.portfolio_return)}`;
  detail.textContent = [
    ytd ? `YTD ${formatPct(ytd.portfolio_return)}` : "",
    oneYear ? `1Y ${formatPct(oneYear.portfolio_return)}` : "",
    "current-weight proxy",
  ].filter(Boolean).join(" | ");
}

function renderTopTrade() {
  const trade = (payload.portfolio_benchmark?.action_queue || [])[0];
  const title = document.getElementById("homeTopTrade");
  const detail = document.getElementById("homeTopTradeDetail");
  if (!trade) {
    title.textContent = "Hold";
    detail.textContent = "No add/trim target in the current feed.";
    return;
  }
  title.textContent = `${trade.symbol} ${tradeLabel(trade)}`;
  detail.textContent = [
    `current ${formatWeight(trade.portfolio_weight)}`,
    `target ${formatWeight(trade.target_weight ?? trade.post_action_weight ?? trade.portfolio_weight)}`,
    `${trade.signal_family_count || 0} signals`,
  ].join(" | ");
}

function renderFreshness() {
  const builtAt = payload.site?.built_at || "";
  const status = payload.site?.stale_status || {};
  const builtDate = builtAt ? new Date(builtAt) : null;
  const fresh = builtDate && !status.is_stale_at_build;
  document.getElementById("homeFreshness").textContent = fresh ? "Fresh" : "Check run";
  document.getElementById("homeFreshnessDetail").textContent = builtDate
    ? `Built ${builtDate.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`
    : "Missing build timestamp";
}

function renderTopWeights() {
  const rows = sortedSymbols().slice(0, 10);
  document.getElementById("homeTopWeights").innerHTML = rows.length
    ? rows.map((row) => weightBar(row.symbol, row.weight, row.bucket)).join("")
    : empty("No public portfolio weights available.");
}

function renderBucketMix() {
  const rows = [...(payload.portfolio?.by_bucket || [])].sort((a, b) => Number(b.weight || 0) - Number(a.weight || 0));
  document.getElementById("homeBucketMix").innerHTML = rows.length
    ? rows.map((row) => weightBar(labelize(row.bucket), row.weight, row.bucket)).join("")
    : empty("No thesis bucket weights available.");
}

function sortedSymbols() {
  return [...(payload?.portfolio?.by_symbol || [])].sort((a, b) => Number(b.weight || 0) - Number(a.weight || 0));
}

function horizonFor(label) {
  const target = String(label || "").toLowerCase();
  return (payload?.portfolio_benchmark?.horizon_returns || []).find((row) => (
    String(row.label || "").toLowerCase() === target || String(row.key || "").toLowerCase() === target
  ));
}

function tradeLabel(trade) {
  const delta = Number(trade.recommended_delta_weight || 0);
  if (delta > 0) return `Add ${formatAbsWeight(delta)}`;
  if (delta < 0) return `Trim ${formatAbsWeight(delta)}`;
  return `Hold at ${formatWeight(trade.portfolio_weight)}`;
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

function barWidth(weight) {
  return Math.max(2, Math.min(100, Number(weight || 0) * 100));
}

function formatWeight(value) {
  if (value == null || Number.isNaN(Number(value))) return "n/a";
  return `${number.format(Number(value) * 100)}%`;
}

function formatAbsWeight(value) {
  return formatWeight(Math.abs(Number(value || 0)));
}

function formatPct(value) {
  if (value == null || Number.isNaN(Number(value))) return "n/a";
  return `${Number(value) >= 0 ? "+" : ""}${number.format(Number(value))}%`;
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
