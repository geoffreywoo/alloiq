const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

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
  document.title = asOf ? `Geoffrey Woo Portfolio - AlloIQ ${asOf}` : "Geoffrey Woo Portfolio - AlloIQ";
  document.getElementById("homeDate").textContent = asOf ? `Snapshot ${asOf}` : "Public snapshot";
  renderSnapshot();
  renderPerformance();
  renderTopTrade();
  renderFreshness();
  renderProofSurface();
  renderHeroTrade();
  renderTopWeights();
  renderBucketMix();
  renderReturnAnalytics();
}

function renderSnapshot() {
  const symbols = sortedSymbols();
  const top = symbols[0];
  const count = payload.portfolio?.security_symbol_count || payload.portfolio?.symbol_count || symbols.length || 0;
  const cash = Number(payload.portfolio?.cash_weight || 0);
  document.getElementById("homeSnapshot").textContent = `${count} symbols`;
  const snapshot = document.querySelector("#homeSnapshot + small");
  if (snapshot) {
    snapshot.textContent = top
      ? `Largest ex-cash weight: ${top.symbol} ${formatWeight(top.weight)} | Cash reserve ${formatWeight(cash)}`
      : "Public weights only";
  }
}

function renderPerformance() {
  const primary = horizonFor("3M") || horizonFor("3m") || (payload.portfolio_benchmark?.horizon_returns || [])[0];
  const analytics = payload.portfolio_benchmark?.return_analytics || {};
  const primaryAnalytics = analytics.primary || analyticsFor(primary?.key);
  const ytd = analyticsFor("ytd") || horizonFor("YTD") || horizonFor("ytd");
  const oneYear = analyticsFor("1y") || horizonFor("1Y") || horizonFor("1y");
  const title = document.getElementById("homePerformance");
  const detail = document.getElementById("homePerformanceDetail");
  if (!title || !detail) return;
  if (!primary) {
    title.textContent = "n/a";
    detail.textContent = "No return windows in this snapshot.";
    return;
  }
  const exCashReturn = primaryAnalytics?.invested_equity_return ?? primary.portfolio_return;
  title.textContent = `${primary.label || "3M"} ${formatPct(exCashReturn)}`;
  detail.textContent = [
    "ex-cash invested-equity proxy",
    ytd ? `YTD ex-cash ${formatPct(ytd.invested_equity_return ?? ytd.portfolio_return)}` : "",
    oneYear ? `1Y ex-cash ${formatPct(oneYear.invested_equity_return ?? oneYear.portfolio_return)}` : "",
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

function renderProofSurface() {
  const benchmark = payload.portfolio_benchmark || {};
  const primary = horizonFor("3M") || horizonFor("YTD") || (benchmark.horizon_returns || [])[0];
  const primaryValue = primary ? (analyticsFor(primary.key)?.invested_equity_return ?? primary.portfolio_return) : null;
  const proofTitle = document.getElementById("homeProofTitle");
  const proofMeta = document.getElementById("homeProofMeta");
  const curve = document.getElementById("homeReturnCurve");
  const drivers = document.getElementById("homeDriverWaterfall");
  if (proofTitle) {
    proofTitle.textContent = primary ? `${primary.label || "Return"} ${formatPct(primaryValue)}` : "No return window";
  }
  if (proofMeta) {
    proofMeta.textContent = `${formatPlainPct(benchmark.primary_price_coverage_pct ?? benchmark.price_coverage_pct)} priced | ex-cash proxy`;
  }
  if (curve) {
    curve.innerHTML = returnCurveTemplate(returnRows());
  }
  if (drivers) {
    drivers.innerHTML = driverWaterfallTemplate();
  }
}

function renderHeroTrade() {
  const trade = (payload.portfolio_benchmark?.action_queue || [])[0];
  const title = document.getElementById("homeHeroTrade");
  const risk = document.getElementById("homeHeroRisk");
  const detail = document.getElementById("homeHeroTradeDetail");
  const stats = document.getElementById("homeHeroTradeStats");
  const queue = document.getElementById("homeDecisionList");
  if (risk) {
    risk.textContent = payload.macro?.regime || "Mixed macro tape";
  }
  if (!trade) {
    if (title) title.textContent = "Hold";
    if (detail) detail.textContent = "No add/trim target in the current feed.";
    if (stats) stats.innerHTML = "";
    if (queue) queue.innerHTML = empty("No active calls.");
    return;
  }
  if (title) {
    title.textContent = `${trade.symbol} ${tradeLabel(trade)}`;
  }
  if (detail) {
    detail.textContent = trade.company_reason || trade.why || trade.action || "Review the current target weight before changing size.";
  }
  if (stats) {
    stats.innerHTML = [
      ["current", formatWeight(trade.portfolio_weight)],
      ["target", formatWeight(trade.target_weight ?? trade.post_action_weight ?? trade.portfolio_weight)],
      ["expected", formatPct(trade.risk_adjusted_expected_return)],
      ["evidence", trade.evidence_quality == null ? "n/a" : `${number.format(trade.evidence_quality)}/100`],
    ].map(([label, value]) => `
      <article>
        <span>${escapeHtml(label)}</span>
        <strong>${escapeHtml(value)}</strong>
      </article>
    `).join("");
  }
  if (queue) {
    const actions = (payload.portfolio_benchmark?.action_queue || []).slice(0, 4);
    queue.innerHTML = actions.length
      ? actions.map(homeDecisionRow).join("")
      : empty("No active calls.");
  }
}

function renderTopWeights() {
  const rows = sortedSymbols().slice(0, 10);
  document.getElementById("homeTopWeights").innerHTML = rows.length
    ? rows.map((row) => weightBar(row.symbol, row.weight, row.bucket, { secondaryWeight: row.total_weight, secondaryLabel: "total" })).join("")
    : empty("No public portfolio weights available.");
}

function renderBucketMix() {
  const rows = [...(payload.portfolio?.by_bucket || [])].sort((a, b) => Number(b.weight || 0) - Number(a.weight || 0));
  document.getElementById("homeBucketMix").innerHTML = rows.length
    ? rows.map((row) => weightBar(labelize(row.bucket), row.weight, row.bucket, { secondaryWeight: row.total_weight, secondaryLabel: "total" })).join("")
    : empty("No thesis bucket weights available.");
}

function renderReturnAnalytics() {
  const target = document.getElementById("homeReturnAnalytics");
  if (!target) return;
  const rows = payload.portfolio_benchmark?.return_analytics?.horizons?.length
    ? payload.portfolio_benchmark.return_analytics.horizons
    : (payload.portfolio_benchmark?.horizon_returns || []).map((row) => ({
      ...row,
      total_portfolio_return: row.portfolio_return,
    }));
  target.innerHTML = rows.length
    ? rows.map(returnAnalyticTile).join("")
    : empty("No return windows available.");
}

function sortedSymbols() {
  return [...(payload?.portfolio?.by_symbol || [])].sort((a, b) => {
    if (Boolean(a.is_cash) !== Boolean(b.is_cash)) return a.is_cash ? 1 : -1;
    return Number(b.weight || 0) - Number(a.weight || 0);
  });
}

function returnRows() {
  const analytics = payload.portfolio_benchmark?.return_analytics?.horizons || [];
  const horizons = payload.portfolio_benchmark?.horizon_returns || [];
  const rows = analytics.length ? analytics : horizons;
  return rows
    .map((row) => ({
      label: row.label || row.key,
      value: row.invested_equity_return ?? row.total_portfolio_return ?? row.portfolio_return,
    }))
    .filter((row) => row.value != null && !Number.isNaN(Number(row.value)));
}

function horizonFor(label) {
  const target = String(label || "").toLowerCase();
  return (payload?.portfolio_benchmark?.horizon_returns || []).find((row) => (
    String(row.label || "").toLowerCase() === target || String(row.key || "").toLowerCase() === target
  ));
}

function analyticsFor(label) {
  const target = String(label || "").toLowerCase();
  return (payload?.portfolio_benchmark?.return_analytics?.horizons || []).find((row) => (
    String(row.label || "").toLowerCase() === target || String(row.key || "").toLowerCase() === target
  ));
}

function returnCurveTemplate(rows) {
  if (!rows.length) return empty("No return windows available.");
  if (rows.length === 1) {
    return `<div class="home-curve-single"><span>${escapeHtml(rows[0].label || "Return")}</span><strong>${escapeHtml(formatPct(rows[0].value))}</strong></div>`;
  }
  const width = 420;
  const height = 154;
  const padX = 34;
  const padY = 22;
  const values = rows.map((row) => Number(row.value));
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const span = Math.max(1, max - min);
  const points = rows.map((row, index) => {
    const x = padX + (index / (rows.length - 1)) * (width - padX * 2);
    const y = padY + (1 - ((Number(row.value) - min) / span)) * (height - padY * 2);
    return { x, y, row };
  });
  const zeroY = padY + (1 - ((0 - min) / span)) * (height - padY * 2);
  return `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Portfolio return proxy by horizon">
      <line x1="${padX}" y1="${zeroY}" x2="${width - padX}" y2="${zeroY}" class="curve-zero"></line>
      <polyline points="${points.map((point) => `${point.x},${point.y}`).join(" ")}" class="curve-line"></polyline>
      ${points.map((point) => `
        <g>
          <circle cx="${point.x}" cy="${point.y}" r="4.5" class="curve-point"></circle>
          <text x="${point.x}" y="${height - 6}" text-anchor="middle">${escapeHtml(point.row.label || "")}</text>
          <text x="${point.x}" y="${Math.max(12, point.y - 10)}" text-anchor="middle" class="curve-value">${escapeHtml(formatPct(point.row.value))}</text>
        </g>
      `).join("")}
    </svg>
  `;
}

function driverWaterfallTemplate() {
  const benchmark = payload.portfolio_benchmark || {};
  const rows = [
    ...(benchmark.top_contributors || []),
    ...(benchmark.top_detractors || []),
  ]
    .filter((row) => row.symbol && row.contribution_pct != null)
    .sort((a, b) => Math.abs(Number(b.contribution_pct || 0)) - Math.abs(Number(a.contribution_pct || 0)))
    .slice(0, 6);
  if (!rows.length) return empty("No return drivers in this snapshot.");
  const maxAbs = Math.max(...rows.map((row) => Math.abs(Number(row.contribution_pct || 0))), 0.1);
  return rows.map((row) => {
    const contribution = Number(row.contribution_pct || 0);
    const width = Math.max(4, Math.min(100, (Math.abs(contribution) / maxAbs) * 100));
    return `
      <div class="home-driver-row">
        <strong>${escapeHtml(row.symbol)}</strong>
        <div class="home-driver-track ${contribution < 0 ? "is-negative" : ""}">
          <span style="width:${width}%"></span>
        </div>
        <em class="${contribution >= 0 ? "positive" : "negative"}">${escapeHtml(formatPp(contribution))}</em>
      </div>
    `;
  }).join("");
}

function homeDecisionRow(trade) {
  const delta = Number(trade.recommended_delta_weight || 0);
  return `
    <article class="home-decision-row">
      <div>
        <strong>${escapeHtml(trade.symbol)}</strong>
        <span>${escapeHtml(actionWord(delta))}</span>
      </div>
      <em class="${delta >= 0 ? "positive" : "negative"}">${escapeHtml(delta === 0 ? "Hold" : formatAbsWeight(delta))}</em>
    </article>
  `;
}

function actionWord(delta) {
  if (delta > 0) return "Add";
  if (delta < 0) return "Trim";
  return "Hold";
}

function tradeLabel(trade) {
  const delta = Number(trade.recommended_delta_weight || 0);
  if (delta > 0) return `Add ${formatAbsWeight(delta)}`;
  if (delta < 0) return `Trim ${formatAbsWeight(delta)}`;
  return `Hold at ${formatWeight(trade.portfolio_weight)}`;
}

function weightBar(label, weight, bucket, options = {}) {
  const secondaryWeight = options.secondaryWeight;
  const secondaryLabel = options.secondaryLabel || "";
  return `
    <div class="bar-row">
      <strong>${escapeHtml(label)}</strong>
      <div class="bar-track"><div class="bar-fill" style="width:${barWidth(weight)}%;background:${bucketColors[bucket] || bucketColors.unmapped}"></div></div>
      <div class="metric weight-metric">
        <strong>${escapeHtml(formatWeight(weight))}</strong>
        ${secondaryWeight == null ? "" : `<small>${escapeHtml(formatWeight(secondaryWeight))}${secondaryLabel ? ` ${escapeHtml(secondaryLabel)}` : ""}</small>`}
      </div>
    </div>
  `;
}

function returnAnalyticTile(row) {
  const hasExCash = row.invested_equity_return != null;
  return `
    <article class="horizon-tile">
      <span>${escapeHtml(row.label || row.key || "Window")}${hasExCash ? " ex-cash" : ""}</span>
      <strong>${escapeHtml(formatPct(hasExCash ? row.invested_equity_return : row.total_portfolio_return))}</strong>
      <small>${escapeHtml(hasExCash ? "Invested-equity price proxy" : `${formatPlainPct(row.price_coverage_pct)} priced | ex-cash proxy`)}</small>
    </article>
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

function formatPp(value) {
  if (value == null || Number.isNaN(Number(value))) return "n/a";
  return `${Number(value) >= 0 ? "+" : ""}${number.format(Number(value))} pp`;
}

function formatPlainPct(value) {
  if (value == null || Number.isNaN(Number(value))) return "n/a";
  return `${number.format(Number(value))}%`;
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
