const state = {
  payload: null,
  query: "",
  signalPoints: [],
  signalHoverSymbol: "",
  signalSelectedSymbol: "",
  loadedAt: null,
  refreshError: "",
};

const REFRESH_INTERVAL_MS = 5 * 60 * 1000;

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

const number = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 2,
});

const brandColors = {
  ink: "#0b1117",
  muted: "#5a6673",
  surface: "#fffffb",
  surfaceAlt: "#f8faf3",
  line: "#d8dfd2",
  blue: "#2558d5",
  green: "#08745f",
  red: "#b04449",
  amber: "#b5681e",
  plum: "#66518d",
  olive: "#69752d",
  teal: "#0f7580",
  terminal: "#0e151b",
};

const bucketColors = {
  frontier_ai_platforms: brandColors.blue,
  semis_networking_hbm: brandColors.green,
  neocloud_datacenters: brandColors.amber,
  power_grid_gas_nuclear: brandColors.teal,
  ai_software_winners: brandColors.plum,
  ai_enabled_financials: brandColors.olive,
  disrupted_incumbents: brandColors.red,
  unmapped: brandColors.muted,
};

const aiThesisCoreBenchmarkNames = new Set(["AI Thesis Core median proxy", "Tier 1 median proxy"]);

async function init() {
  wireNavigation();
  wireSearch();
  wireSignalCanvas();
  await loadLatestData();
  window.setInterval(() => loadLatestData({ silent: true }), REFRESH_INTERVAL_MS);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) loadLatestData({ silent: true });
  });
  window.addEventListener("resize", () => drawSignalCanvas(filteredCards()));
}

async function loadLatestData({ silent = false } = {}) {
  try {
    const response = await fetch(`/data/latest.json?ts=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.payload = await response.json();
    state.loadedAt = new Date();
    state.refreshError = "";
    render();
  } catch (error) {
    state.refreshError = error.message || "refresh failed";
    updateRefreshStatus();
    if (!silent) throw error;
  }
}

function wireNavigation() {
  document.querySelectorAll(".rail-button[data-view]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".rail-button").forEach((item) => {
        item.classList.remove("active");
        item.removeAttribute("aria-current");
      });
      document.querySelectorAll(".view").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      button.setAttribute("aria-current", "page");
      document.getElementById(button.dataset.view).classList.add("active");
      drawSignalCanvas(filteredCards());
    });
  });
}

function wireSearch() {
  const input = document.getElementById("searchInput");
  input.addEventListener("input", () => {
    state.query = input.value.trim().toLowerCase();
    renderContent();
  });
}

function wireSignalCanvas() {
  const canvas = document.getElementById("signalCanvas");
  const tooltip = document.getElementById("signalTooltip");
  if (!canvas || !tooltip) return;
  canvas.addEventListener("mousemove", (event) => {
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const point = nearestSignalPoint(x, y);
    state.signalHoverSymbol = point?.card?.symbol || "";
    if (point) {
      tooltip.hidden = false;
      tooltip.style.left = `${Math.min(rect.width - 168, Math.max(8, x + 12))}px`;
      tooltip.style.top = `${Math.max(8, y - 12)}px`;
      tooltip.innerHTML = `
        <strong>${escapeHtml(point.card.symbol)}</strong>
        <span>Signal score ${escapeHtml(number.format(point.card.score || 0))}</span>
        <span>${escapeHtml(point.card.consensus_manager_count || 0)} managers | ${escapeHtml(point.card.news_count || 0)} news hits</span>
      `;
    } else {
      tooltip.hidden = true;
    }
    drawSignalCanvas(filteredCards());
  });
  canvas.addEventListener("mouseleave", () => {
    state.signalHoverSymbol = "";
    tooltip.hidden = true;
    drawSignalCanvas(filteredCards());
  });
  canvas.addEventListener("click", () => {
    state.signalSelectedSymbol = state.signalHoverSymbol || "";
    drawSignalCanvas(filteredCards());
  });
}

function nearestSignalPoint(x, y) {
  let best = null;
  let bestDistance = Infinity;
  state.signalPoints.forEach((point) => {
    const distance = Math.hypot(point.x - x, point.y - y);
    if (distance <= point.radius + 8 && distance < bestDistance) {
      best = point;
      bestDistance = distance;
    }
  });
  return best;
}

function render() {
  const payload = state.payload;
  document.title = "AlloIQ - " + (payload.as_of || "Markets");
  const runKind = payload.site?.last_run_kind || payload.session || "report";
  document.getElementById("reportDate").textContent = `${labelize(runKind)} report ${payload.as_of || ""}`;
  const privacy = payload.site?.privacy || "public";
  document.getElementById("privacyBadge").textContent =
    privacy === "public" ? "Public weights-only" : "Private source data";
  document.getElementById("regimeBadge").textContent = payload.macro?.regime || "Mixed macro tape";
  updateRefreshStatus();
  renderStaleBanner();
  renderContent();
}

function renderContent() {
  renderKpis();
  renderDashboard();
  renderPortfolioContext();
  renderMoves();
  renderResearch();
  renderEarnings();
  renderManagers();
  renderMacro();
  renderNews();
  drawSignalCanvas(filteredCards());
}

function updateRefreshStatus() {
  const element = document.getElementById("refreshStatus");
  if (!element) return;
  if (state.refreshError) {
    element.textContent = `Refresh failed: ${state.refreshError}`;
    element.classList.add("refresh-error");
    return;
  }
  element.classList.remove("refresh-error");
  const builtAt = state.payload?.site?.built_at;
  const loaded = state.loadedAt ? timeOnly(state.loadedAt) : "loading";
  element.textContent = builtAt ? `Built ${dateTimeShort(builtAt)} | loaded ${loaded}` : `Loaded ${loaded}`;
}

function renderStaleBanner() {
  const banner = document.getElementById("staleBanner");
  if (!banner) return;
  const freshness = payloadFreshness(state.payload);
  if (!freshness.isStale) {
    banner.hidden = true;
    banner.textContent = "";
    return;
  }
  banner.hidden = false;
  banner.textContent = `Data may be stale: ${freshness.reason}. Check the scheduled run before relying on today's research proposals.`;
}

function payloadFreshness(payload) {
  const site = payload?.site || {};
  const status = site.stale_status || {};
  const maxAgeHours = Number(status.max_age_hours || (site.last_run_kind === "weekly" ? 192 : 20));
  const builtAt = Date.parse(site.built_at || "");
  if (!builtAt) return { isStale: true, reason: "missing build timestamp" };
  const ageHours = (Date.now() - builtAt) / 36e5;
  if (status.is_stale_at_build) return { isStale: true, reason: "build marked stale" };
  if (ageHours > maxAgeHours) {
    return { isStale: true, reason: `last build is ${number.format(ageHours)} hours old` };
  }
  return { isStale: false, reason: "fresh" };
}

function renderKpis() {
  const payload = state.payload;
  const radar = payload.manager_radar || {};
  const macro = payload.macro || {};
  const portfolio = payload.portfolio || {};
  const benchmark = payload.portfolio_benchmark || {};
  const dataHealth = payload.data_health || {};
  const dataPosture = dataHealth.recommendation_posture === "research_only_until_positions_refresh"
    ? "Research Only"
    : labelize(dataHealth.recommendation_posture || "normal");
  const portfolioName = portfolio.display_name || "Geoffrey Woo Portfolio";
  const primaryLabel = benchmark.primary_label || "3M";
  const primaryReturn = benchmark.primary_portfolio_return ?? benchmark.portfolio_return_5d;
  const medianPeer = (benchmark.benchmarks || []).find((row) => aiThesisCoreBenchmarkNames.has(row.name))
    || (benchmark.benchmarks || []).find((row) => row.name === "Focus-manager median proxy");
  const nasdaq = (benchmark.benchmarks || []).find((row) => row.name === "Nasdaq 100");
  const actions = benchmark.action_queue || [];
  const kpis = [
    {
      label: `${portfolioName} ${primaryLabel} return proxy`,
      value: formatPct(primaryReturn),
      detail: `Current weights priced for ${formatPlainPct(benchmark.primary_price_coverage_pct ?? benchmark.price_coverage_pct)} of the portfolio`,
    },
    {
      label: "Active vs Nasdaq 100",
      value: nasdaq ? formatPp(nasdaq.portfolio_vs_benchmark) : "n/a",
      detail: nasdaq ? `QQQ 5D return ${formatPct(nasdaq.return_5d)}` : "Benchmark unavailable",
    },
    {
      label: medianPeer && aiThesisCoreBenchmarkNames.has(medianPeer.name) ? "Active vs AI Thesis Core" : "Active vs focus peers",
      value: medianPeer ? formatPp(medianPeer.portfolio_vs_benchmark) : "n/a",
      detail: medianPeer ? `Peer median 13F proxy ${formatPct(medianPeer.return_5d)}` : "13F proxy unavailable",
    },
    {
      label: "Recommended changes",
      value: String(actions.length || 0),
      detail: actions[0] ? `Top proposal: ${actions[0].symbol} ${actions[0].action}` : "No urgent portfolio-weight changes",
    },
    {
      label: "Macro risk gate",
      value: macro.regime || "Mixed",
      detail: scoreDetail(macro.scores),
    },
    {
      label: "Data posture",
      value: dataPosture,
      detail: dataHealth.summary || "Scheduled source-health checks",
    },
  ];
  document.getElementById("kpiGrid").innerHTML = kpis.map(kpiTemplate).join("");
}

function scoreDetail(scores = {}) {
  const ai = formatPct(scores.ai_momentum);
  const risk = formatPct(scores.risk_momentum);
  return `AI ${ai}, risk ${risk}`;
}

function kpiTemplate(item) {
  return `
    <article class="kpi">
      <span>${escapeHtml(item.label)}</span>
      <strong>${escapeHtml(item.value)}</strong>
      <small>${escapeHtml(item.detail || "")}</small>
    </article>
  `;
}

function renderDashboard() {
  const benchmark = state.payload.portfolio_benchmark || {};
  const actions = filterItems(benchmark.action_queue || []);
  const gaps = filterItems(benchmark.exposure_gaps || []);
  const studies = filterItems(benchmark.study_queue || []);
  const benchmarkRows = filterItems(benchmark.benchmarks || []);
  const horizons = benchmark.horizon_returns || [];
  const macro = state.payload.macro || {};
  const actionCount = document.getElementById("actionCount");
  const benchmarkHorizon = document.getElementById("benchmarkHorizon");
  if (actionCount) actionCount.textContent = `${actions.length} proposals`;
  if (benchmarkHorizon) benchmarkHorizon.textContent = `${benchmark.primary_label || "3M"} current-weight horizon`;
  const decisionStack = document.getElementById("decisionStack");
  if (decisionStack) {
    decisionStack.innerHTML = decisionStackTemplate(benchmark, actions, macro);
  }
  document.getElementById("horizonList").innerHTML =
    horizons.length === 0 ? empty("No return windows in this public snapshot.") : horizons.map(horizonTemplate).join("");
  const returnCurve = document.getElementById("returnCurve");
  if (returnCurve) {
    returnCurve.innerHTML = returnCurveTemplate(horizons);
  }
  document.getElementById("benchmarkList").innerHTML =
    benchmarkRows.length === 0 ? empty("No benchmark comparison in this public snapshot.") : benchmarkRows.slice(0, 7).map(benchmarkTemplate).join("");
  document.getElementById("portfolioActionList").innerHTML =
    actions.length === 0 ? empty("No recommended weight changes match this search.") : actions.slice(0, 7).map(actionTemplate).join("");
  const actionVisual = document.getElementById("actionSizingVisual");
  if (actionVisual) {
    actionVisual.innerHTML =
      actions.length === 0 ? empty("No add/trim sizing to show.") : actionSizingVisualTemplate(actions.slice(0, 8));
  }
  const attribution = document.getElementById("attributionWaterfall");
  if (attribution) {
    attribution.innerHTML = attributionWaterfallTemplate(benchmark);
  }
  const peerGap = document.getElementById("peerGapChart");
  if (peerGap) {
    peerGap.innerHTML = peerGapTemplate(actions.length ? actions : gaps);
  }
  document.getElementById("exposureGapList").innerHTML =
    gaps.length === 0 ? empty("No underweight or overweight gaps match this search.") : gaps.slice(0, 7).map(exposureGapTemplate).join("");
  document.getElementById("studyList").innerHTML =
    studies.length === 0 ? empty("No underwriting questions match this search.") : studies.slice(0, 8).map(studyTemplate).join("");
  renderDataHealth();
  renderRiskControls(actions);
}

function decisionStackTemplate(benchmark, actions, macro) {
  const primary = actions[0] || {};
  const peer = preferredBenchmark(benchmark.benchmarks || []);
  const delta = Number(primary.recommended_delta_weight || 0);
  const hedge = Number(primary.hedge_weight || 0);
  const confidence = primary.symbol
    ? Math.min(99, Math.round((Number(primary.signal_family_count || 0) * 17) + Math.min(Number(primary.priority || 0), 80) / 2))
    : 0;
  const actionText = primary.symbol ? decisionActionLabel(primary, delta, hedge) : "No weight change";
  return `
    <article class="decision-card decision-primary">
      <span>Primary weight proposal</span>
      <strong class="${delta > 0 ? "positive" : delta < 0 ? "negative" : ""}">${escapeHtml(actionText)}</strong>
      <small>${escapeHtml(primary.action || "No portfolio-weight changes triggered in this report.")}</small>
    </article>
    <article class="decision-card">
      <span>Peer spread</span>
      <strong class="${Number(peer?.portfolio_vs_benchmark || 0) >= 0 ? "positive" : "negative"}">${escapeHtml(peer ? formatPp(peer.portfolio_vs_benchmark) : "n/a")}</strong>
      <small>${escapeHtml(peer ? `Portfolio return proxy vs ${peer.name}` : "Benchmark unavailable")}</small>
    </article>
    <article class="decision-card">
      <span>Macro risk gate</span>
      <strong>${escapeHtml(macro.regime || "Mixed")}</strong>
      <small>${escapeHtml(scoreDetail(macro.scores || {}))}</small>
    </article>
    <article class="decision-card">
      <span>Evidence confidence</span>
      <strong>${escapeHtml(confidence ? `${confidence}/100` : "n/a")}</strong>
      <small>${escapeHtml(primary.symbol ? `${primary.signal_family_count || 0} signal families, priority ${number.format(primary.priority || 0)}` : "No ranked proposal")}</small>
    </article>
  `;
}

function decisionActionLabel(primary, delta, hedge) {
  if (delta > 0) return `${primary.symbol} ${formatSignedWeight(delta)}`;
  if (delta < 0) return `${primary.symbol} ${formatSignedWeight(delta)}`;
  if (hedge > 0) return `${primary.symbol} hedge ${formatWeight(hedge)}`;
  return `${primary.symbol} hold`;
}

function actionSizeLabel(delta, hedge = 0) {
  if (delta > 0 || delta < 0) return formatSignedWeight(delta);
  if (hedge > 0) return `Hedge ${formatWeight(hedge)}`;
  return "Hold";
}

function preferredBenchmark(rows) {
  return rows.find((row) => aiThesisCoreBenchmarkNames.has(row.name))
    || rows.find((row) => row.name === "Nasdaq 100")
    || rows[0];
}

function returnCurveTemplate(horizons) {
  const rows = (horizons || []).filter((row) => row.portfolio_return != null && !Number.isNaN(Number(row.portfolio_return)));
  if (rows.length < 2) return empty("Not enough return windows to draw a curve yet.");
  const width = 420;
  const height = 126;
  const padX = 36;
  const padY = 18;
  const values = rows.map((row) => Number(row.portfolio_return));
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const span = Math.max(1, max - min);
  const points = rows.map((row, index) => {
    const x = rows.length === 1 ? width / 2 : padX + (index / (rows.length - 1)) * (width - padX * 2);
    const y = padY + (1 - ((Number(row.portfolio_return) - min) / span)) * (height - padY * 2);
    return { x, y, row };
  });
  const zeroY = padY + (1 - ((0 - min) / span)) * (height - padY * 2);
  const pointString = points.map((point) => `${point.x},${point.y}`).join(" ");
  return `
    <div class="return-chart" aria-label="Portfolio return curve">
      <svg viewBox="0 0 ${width} ${height}" role="img" aria-hidden="true">
        <line x1="${padX}" y1="${zeroY}" x2="${width - padX}" y2="${zeroY}" class="curve-zero"></line>
        <polyline points="${pointString}" class="curve-line"></polyline>
        ${points.map((point) => `
          <g>
            <circle cx="${point.x}" cy="${point.y}" r="4.5" class="curve-point"></circle>
            <text x="${point.x}" y="${height - 4}" text-anchor="middle">${escapeHtml(point.row.label || point.row.key)}</text>
          </g>
        `).join("")}
      </svg>
      <div class="curve-labels">
        ${rows.map((row) => `<span>${escapeHtml(row.label || row.key)} ${escapeHtml(formatPct(row.portfolio_return))}</span>`).join("")}
      </div>
      <p class="curve-note">Uses current public weights and price moves. This is not realized account performance.</p>
    </div>
  `;
}

function horizonTemplate(row) {
  return `
    <article class="horizon-tile searchable" data-search="${searchAttribute(row)}">
      <span>${escapeHtml(row.label || row.key || "Window")}</span>
      <strong>${escapeHtml(formatPct(row.portfolio_return))}</strong>
      <small>${escapeHtml(formatPlainPct(row.price_coverage_pct))} price coverage | current weights</small>
    </article>
  `;
}

function benchmarkTemplate(row) {
  const active = Number(row.portfolio_vs_benchmark || row.active_vs_portfolio || 0);
  const activeClass = active >= 0 ? "positive" : "negative";
  return `
    <article class="benchmark-row searchable" data-search="${searchAttribute(row)}">
      <div class="row-main">
        <div>
          <strong>${escapeHtml(row.name || row.symbol || "Benchmark")}</strong>
          <p>${escapeHtml(row.symbol ? `${row.symbol} benchmark` : "benchmark proxy")}</p>
        </div>
        <div class="benchmark-numbers">
          <span>Return ${escapeHtml(formatPct(row.return_pct ?? row.return_5d))}</span>
          <strong class="${activeClass}">Spread ${escapeHtml(formatPp(active))}</strong>
        </div>
      </div>
    </article>
  `;
}

function actionTemplate(item) {
  const delta = Number(item.recommended_delta_weight || 0);
  const hedge = Number(item.hedge_weight || 0);
  const deltaClass = delta > 0 ? "positive" : delta < 0 ? "negative" : "";
  const targetText = item.target_weight == null || item.target_weight === item.post_action_weight
    ? ""
    : ` | Target weight ${formatWeight(item.target_weight)}`;
  const hedgeText = hedge > 0
    ? ` | Hedge budget ${formatWeight(item.hedge_weight)}`
    : "";
  return `
    <article class="row searchable" data-search="${searchAttribute(item)}">
      <div class="row-main">
        <div class="symbol">${escapeHtml(item.symbol)}</div>
        <div class="action-size">
          <strong class="${deltaClass}">${escapeHtml(actionSizeLabel(delta, hedge))}</strong>
          <span>After move ${escapeHtml(formatWeight(item.post_action_weight ?? item.portfolio_weight))}${escapeHtml(targetText)}${escapeHtml(hedgeText)}</span>
        </div>
      </div>
      <p><strong>${escapeHtml(item.trade_action ? labelize(item.trade_action) : "Research proposal")}:</strong> ${escapeHtml(item.action || "")}</p>
      <p>${escapeHtml(item.why || "")}</p>
      <div class="tags">
        <span class="tag">Current weight ${escapeHtml(formatWeight(item.portfolio_weight))}</span>
        <span class="tag">Peer avg ${escapeHtml(formatWeight(item.peer_avg_weight))}</span>
        <span class="tag">5D price ${escapeHtml(formatPct(item.five_day_pct))}</span>
        <span class="tag">Return contribution ${escapeHtml(formatPp(item.contribution_pct))}</span>
        <span class="tag">Priority score ${escapeHtml(number.format(item.priority || 0))}</span>
        ${item.confidence ? `<span class="tag">Confidence ${escapeHtml(item.confidence)}/100</span>` : ""}
        <span class="tag">${escapeHtml(item.signal_family_count || 0)} signals</span>
        ${(item.risk_flags || []).slice(0, 3).map((flag) => `<span class="tag">${escapeHtml(labelize(flag))}</span>`).join("")}
      </div>
    </article>
  `;
}

function actionSizingVisualTemplate(actions) {
  const maxAbs = Math.max(...actions.map((item) => Math.abs(Number(item.recommended_delta_weight || 0))), 0.01);
  return actions.map((item) => {
    const delta = Number(item.recommended_delta_weight || 0);
    const hedge = Number(item.hedge_weight || 0);
    const width = Math.max(2, Math.min(100, (Math.abs(delta) / maxAbs) * 100));
    const direction = delta > 0 ? "add" : delta < 0 ? "trim" : "hold";
    const size = actionSizeLabel(delta, hedge);
    return `
      <article class="delta-row searchable" data-search="${searchAttribute(item)}">
        <div class="delta-head">
          <strong>${escapeHtml(item.symbol)}</strong>
          <span class="${delta > 0 ? "positive" : delta < 0 ? "negative" : ""}">${escapeHtml(size)}</span>
        </div>
        <div class="delta-bar delta-bar-strong" aria-label="${escapeAttribute(`${item.symbol} ${size}`)}">
          <div class="delta-half delta-negative">
            ${delta < 0 ? `<span class="delta-fill ${direction}" style="width:${width}%"></span>` : ""}
          </div>
          <div class="delta-axis"></div>
          <div class="delta-half delta-positive">
            ${delta > 0 ? `<span class="delta-fill ${direction}" style="width:${width}%"></span>` : ""}
          </div>
        </div>
        <div class="delta-meta">
          <span>Current weight ${escapeHtml(formatWeight(item.portfolio_weight))}</span>
          <span>After move ${escapeHtml(formatWeight(item.post_action_weight ?? item.portfolio_weight))}</span>
          ${hedge > 0 ? `<span>Hedge budget ${escapeHtml(formatWeight(item.hedge_weight))}</span>` : ""}
        </div>
      </article>
    `;
  }).join("");
}

function attributionWaterfallTemplate(benchmark) {
  const rows = [
    ...(benchmark.top_contributors || []),
    ...(benchmark.top_detractors || []),
  ]
    .filter((row) => row.contribution_pct != null)
    .sort((a, b) => Math.abs(Number(b.contribution_pct || 0)) - Math.abs(Number(a.contribution_pct || 0)))
    .slice(0, 10);
  if (!rows.length) return empty("No 5-day return driver data in this public snapshot.");
  const maxAbs = Math.max(...rows.map((row) => Math.abs(Number(row.contribution_pct || 0))), 0.1);
  return rows.map((row) => {
    const contribution = Number(row.contribution_pct || 0);
    const width = Math.max(3, Math.min(100, (Math.abs(contribution) / maxAbs) * 100));
    return `
      <article class="waterfall-row searchable" data-search="${searchAttribute(row)}">
        <div class="waterfall-head">
          <strong>${escapeHtml(row.symbol)}</strong>
          <span class="${contribution >= 0 ? "positive" : "negative"}">${escapeHtml(formatPp(contribution))}</span>
        </div>
        <div class="waterfall-track" aria-label="${escapeAttribute(`${row.symbol} ${formatPp(contribution)}`)}">
          <div class="waterfall-half waterfall-negative">
            ${contribution < 0 ? `<span class="waterfall-fill drag" style="width:${width}%"></span>` : ""}
          </div>
          <div class="waterfall-axis"></div>
          <div class="waterfall-half waterfall-positive">
            ${contribution >= 0 ? `<span class="waterfall-fill lift" style="width:${width}%"></span>` : ""}
          </div>
        </div>
        <div class="waterfall-meta">
          <span>${escapeHtml(labelize(row.bucket || "portfolio"))}</span>
          <span>5D price move ${escapeHtml(formatPct(row.five_day_pct))}</span>
          <span>Portfolio weight ${escapeHtml(formatWeight(row.weight))}</span>
        </div>
      </article>
    `;
  }).join("");
}

function peerGapTemplate(items) {
  const rows = (items || [])
    .filter((row) => row.symbol && (row.peer_avg_weight != null || row.target_weight != null))
    .slice(0, 8);
  if (!rows.length) return empty("No peer weight comparison available for these symbols.");
  const maxWeight = Math.max(
    ...rows.flatMap((row) => [
      Number(row.portfolio_weight || 0),
      Number(row.peer_avg_weight || 0),
      Number(row.target_weight || row.post_action_weight || 0),
    ]),
    0.05,
  );
  return rows.map((row) => {
    const current = Number(row.portfolio_weight || 0);
    const peer = Number(row.peer_avg_weight || 0);
    const target = Number(row.target_weight ?? row.post_action_weight ?? current);
    const currentWidth = Math.min(100, (current / maxWeight) * 100);
    const peerWidth = Math.min(100, (peer / maxWeight) * 100);
    const targetLeft = Math.min(100, (target / maxWeight) * 100);
    return `
      <article class="peer-gap-row searchable" data-search="${searchAttribute(row)}">
        <div class="peer-gap-head">
          <strong>${escapeHtml(row.symbol)}</strong>
          <span>Your weight ${escapeHtml(formatWeight(current))} | peer avg ${escapeHtml(formatWeight(peer))}</span>
        </div>
        <div class="peer-gap-track" aria-label="${escapeAttribute(`${row.symbol} current ${formatWeight(current)} peer ${formatWeight(peer)}`)}">
          <span class="peer-gap-current" style="width:${currentWidth}%"></span>
          <span class="peer-gap-peer" style="width:${peerWidth}%"></span>
          <span class="peer-gap-target" style="left:${targetLeft}%"></span>
        </div>
        <div class="peer-gap-legend">
          <span><i class="legend-current"></i>Your weight</span>
          <span><i class="legend-peer"></i>Peer avg</span>
          <span><i class="legend-target"></i>Target / after move</span>
        </div>
      </article>
    `;
  }).join("");
}

function exposureGapTemplate(gap) {
  return `
    <article class="row searchable" data-search="${searchAttribute(gap)}">
      <div class="row-main">
        <div class="symbol"><span class="dot" style="background:${bucketColors[gap.bucket] || bucketColors.unmapped}"></span>${escapeHtml(gap.symbol)}</div>
        <div class="metric">${escapeHtml(labelize(gap.type || "gap"))}</div>
      </div>
      <p>${escapeHtml(gap.reason || "")}</p>
      <div class="tags">
        <span class="tag">Your weight ${escapeHtml(formatWeight(gap.portfolio_weight))}</span>
        <span class="tag">Peer avg ${escapeHtml(formatWeight(gap.peer_avg_weight))}</span>
        <span class="tag">Score ${escapeHtml(gap.score || 0)}</span>
        <span class="tag">${escapeHtml(gap.signal_family_count || 0)} signals</span>
      </div>
    </article>
  `;
}

function studyTemplate(item) {
  return `
    <article class="row searchable" data-search="${searchAttribute(item)}">
      <div class="row-main">
        <div class="symbol">${escapeHtml(item.symbol)}</div>
        <div class="metric">${escapeHtml(labelize(item.signal || "study"))}</div>
      </div>
      <p>${escapeHtml(item.question || "")}</p>
      <div class="tags">
        <span class="tag">Portfolio weight ${escapeHtml(formatWeight(item.portfolio_weight))}</span>
        ${item.five_day_pct == null ? "" : `<span class="tag">5D price ${escapeHtml(formatPct(item.five_day_pct))}</span>`}
        ${item.contribution_pct == null ? "" : `<span class="tag">Return contribution ${escapeHtml(formatPp(item.contribution_pct))}</span>`}
        ${item.peer_avg_weight == null ? "" : `<span class="tag">Peer avg ${escapeHtml(formatWeight(item.peer_avg_weight))}</span>`}
      </div>
    </article>
  `;
}

function decisionTemplate(card) {
  const color = bucketColors[card.bucket] || bucketColors.unmapped;
  const price = card.last_price == null ? "n/a" : money.format(card.last_price);
  const consensus = `${card.consensus_manager_count || 0} funds`;
  return `
    <article class="row searchable" data-search="${searchAttribute(card)}">
      <div class="row-main">
        <div class="symbol"><span class="dot" style="background:${color}"></span>${escapeHtml(card.symbol)}</div>
        <div class="metric">Signal score ${number.format(card.score || 0)} | last price ${price} | 5D ${formatPct(card.five_day_pct)}</div>
      </div>
      <p>${escapeHtml(card.counterargument || "")}</p>
      <div class="tags">
        <span class="tag">${escapeHtml(labelize(card.bucket))}</span>
        <span class="tag">${escapeHtml(consensus)}</span>
        <span class="tag">${escapeHtml(card.candidate || "research")}</span>
      </div>
    </article>
  `;
}

function renderPortfolioContext() {
  const portfolio = state.payload.portfolio || {};
  const buckets = portfolio.by_bucket || [];
  const symbols = portfolio.by_symbol || [];
  const container = document.getElementById("portfolioContext");
  if (!container) return;
  if (!buckets.length && !symbols.length) {
    container.innerHTML = empty("No public Geoffrey Woo Portfolio weights available.");
    return;
  }
  const bucketHtml = `
    <div>
      <h4>Portfolio Bucket Weights</h4>
      <div class="bar-list">
        ${buckets.slice(0, 8).map((row) => weightBarTemplate(labelize(row.bucket), row.weight, row.bucket)).join("")}
      </div>
    </div>
  `;
  const symbolHtml = `
    <div>
      <h4>Top Position Weights</h4>
      <div class="bar-list">
        ${symbols.slice(0, 10).map((row) => weightBarTemplate(row.symbol, row.weight, row.bucket)).join("")}
      </div>
    </div>
  `;
  container.innerHTML = bucketHtml + symbolHtml;
}

function weightBarTemplate(label, weight, bucket) {
  const width = Math.max(2, Math.min(100, Number(weight || 0) * 100));
  const color = bucketColors[bucket] || bucketColors.unmapped;
  return `
    <div class="bar-row">
      <strong>${escapeHtml(label)}</strong>
      <div class="bar-track"><div class="bar-fill" style="width:${width}%;background:${color}"></div></div>
      <div class="metric">${escapeHtml(formatWeight(weight))}</div>
    </div>
  `;
}

function renderMoves() {
  const moves = filterItems(state.payload.recommended_moves || []);
  document.getElementById("moveList").innerHTML =
    moves.length === 0 ? empty("No move proposals match this search.") : moves.slice(0, 12).map(moveTemplate).join("");
}

function renderResearch() {
  const weekly = state.payload.weekly_research || {};
  const ideas = filterItems(weekly.ideas || state.payload.ideas || []);
  const summary = document.getElementById("researchSummary");
  if (summary) {
    summary.textContent = weekly.as_of
      ? `Weekly thesis research as of ${weekly.as_of}`
      : "Thesis, trigger, and risk";
  }
  const container = document.getElementById("weeklyResearchList");
  if (!container) return;
  container.innerHTML =
    ideas.length === 0 ? empty("No weekly thesis research in this snapshot.") : ideas.slice(0, 15).map(researchTemplate).join("");
}

function researchTemplate(idea) {
  const questions = idea.research_questions || [];
  return `
    <article class="idea research-card searchable" data-search="${searchAttribute(idea)}">
      <h3>
        <span>${escapeHtml(idea.symbol || "Idea")}</span>
        <span class="tag">${escapeHtml(idea.trade_action ? labelize(idea.trade_action) : idea.type || "Research")}</span>
      </h3>
      <p>${escapeHtml(idea.setup || "")}</p>
      <p><strong>Proposed action:</strong> ${escapeHtml(idea.recommended_action || "Refresh thesis and catalyst path.")}</p>
      <p><strong>Evidence base:</strong> ${escapeHtml(idea.evidence || "")}</p>
      <p><strong>Trigger to watch:</strong> ${escapeHtml(idea.trigger || "")}</p>
      <p><strong>Main risk:</strong> ${escapeHtml(idea.risk || "")}</p>
      <div class="tags">
        <span class="tag">Rank ${escapeHtml(idea.rank || "n/a")}</span>
        <span class="tag">Score ${escapeHtml(idea.score || 0)}</span>
        <span class="tag">Current weight ${escapeHtml(formatWeight(idea.portfolio_weight))}</span>
        <span class="tag">Suggested delta ${escapeHtml(formatSignedWeight(idea.recommended_delta_weight))}</span>
        ${(idea.signal_families || []).slice(0, 3).map((family) => `<span class="tag">${escapeHtml(labelize(family))}</span>`).join("")}
      </div>
      ${questions.length ? `<ul class="research-questions">${questions.slice(0, 4).map((question) => `<li>${escapeHtml(question)}</li>`).join("")}</ul>` : ""}
    </article>
  `;
}

function moveTemplate(move) {
  return `
    <article class="idea searchable" data-search="${searchAttribute(move)}">
      <h3>
        <span>${escapeHtml(move.symbol || "Move")}</span>
        <span class="tag">${escapeHtml(move.action || "Research")}</span>
      </h3>
      <p><strong>Why this move:</strong> ${escapeHtml(move.rationale || "")}</p>
      <p><strong>Evidence base:</strong> ${escapeHtml(move.manager_count || 0)} tracked funds, ${escapeHtml(move.signal_family_count || 0)} signal families, ${escapeHtml(move.news_count || 0)} news hits, catalyst score ${escapeHtml(move.event_score || 0)}, signal score ${escapeHtml(move.signal_score || 0)}, 5D price ${escapeHtml(formatPct(move.five_day_pct))}, current weight ${escapeHtml(formatWeight(move.portfolio_weight))}.</p>
      <p><strong>Trigger to watch:</strong> ${escapeHtml(move.trigger || "")}</p>
      <p><strong>Main risk:</strong> ${escapeHtml(move.risk || "")}</p>
      <div class="tags">
        <span class="tag">${escapeHtml(move.posture || "Research")}</span>
        <span class="tag">Bucket weight ${escapeHtml(formatWeight(move.bucket_weight))}</span>
        ${(move.signal_families || []).slice(0, 3).map((family) => `<span class="tag">${escapeHtml(labelize(family))}</span>`).join("")}
        ${(move.event_types || []).slice(0, 2).map((event) => `<span class="tag">${escapeHtml(labelize(event))}</span>`).join("")}
        <span class="tag">Conviction ${escapeHtml(move.conviction || 0)}</span>
        <span class="tag">${escapeHtml(labelize(move.bucket || ""))}</span>
      </div>
    </article>
  `;
}

function renderDataHealth() {
  const health = state.payload.data_health || {};
  const container = document.getElementById("dataHealthList");
  if (!container) return;
  const sources = filterItems(health.sources || []);
  container.innerHTML = sources.length === 0
    ? empty("No source-health summary in this snapshot.")
    : sources.map(dataHealthTemplate).join("");
}

function dataHealthTemplate(source) {
  const status = source.status || "unknown";
  const statusClass = status === "ok" ? "positive" : status === "missing" || status === "stale" ? "negative" : "";
  return `
    <article class="row searchable" data-search="${searchAttribute(source)}">
      <div class="row-main">
        <div class="symbol">${escapeHtml(source.label || source.source || "Source")}</div>
        <div class="metric ${statusClass}">${escapeHtml(labelize(status))}</div>
      </div>
      <p>${escapeHtml(source.detail || "")}</p>
    </article>
  `;
}

function renderRiskControls(actions = []) {
  const container = document.getElementById("riskControlList");
  if (!container) return;
  const controlled = filterItems(actions.filter((item) => (item.risk_flags || []).length || (item.constraint_notes || []).length));
  container.innerHTML = controlled.length === 0
    ? empty("No risk-control caps were applied to the current proposal set.")
    : controlled.slice(0, 8).map(riskControlTemplate).join("");
}

function riskControlTemplate(item) {
  const flags = item.risk_flags || [];
  const notes = item.constraint_notes || [];
  return `
    <article class="row searchable" data-search="${searchAttribute(item)}">
      <div class="row-main">
        <div class="symbol">${escapeHtml(item.symbol || "Proposal")}</div>
        <div class="metric">Confidence ${escapeHtml(item.confidence || 0)}/100</div>
      </div>
      <p>${escapeHtml(notes[0] || item.sizing_basis || "Approval required before any position change.")}</p>
      <div class="tags">
        ${flags.slice(0, 5).map((flag) => `<span class="tag">${escapeHtml(labelize(flag))}</span>`).join("")}
        <span class="tag">Delta ${escapeHtml(formatSignedWeight(item.recommended_delta_weight))}</span>
        <span class="tag">Target ${escapeHtml(formatWeight(item.target_weight))}</span>
      </div>
    </article>
  `;
}

function renderEarnings() {
  const events = filterItems(state.payload.earnings_events || []);
  const container = document.getElementById("earningsList");
  if (!container) return;
  container.innerHTML = events.length === 0
    ? empty("No earnings dates or result markers in this public snapshot.")
    : events.slice(0, 24).map(earningsTemplate).join("");
}

function earningsTemplate(event) {
  const days = event.days_until;
  const timing = days === 0
    ? "Today"
    : days == null
      ? "Timing unknown"
      : `${Number(days) > 0 ? "+" : ""}${days} days`;
  return `
    <article class="idea searchable" data-search="${searchAttribute(event)}">
      <h3>
        <span>${escapeHtml(event.symbol || "Event")}</span>
        <span class="tag">${escapeHtml(labelize(event.event_type || "earnings"))}</span>
      </h3>
      <p><strong>Date:</strong> ${escapeHtml(event.event_date || "n/a")} | ${escapeHtml(timing)}</p>
      <p>${escapeHtml(event.title || "")}</p>
      <div class="tags">
        <span class="tag">${escapeHtml(labelize(event.status || "scheduled"))}</span>
        <span class="tag">${escapeHtml(labelize(event.source || "source"))}</span>
        ${(event.catalyst_types || []).slice(0, 4).map((type) => `<span class="tag">${escapeHtml(labelize(type))}</span>`).join("")}
      </div>
    </article>
  `;
}

function renderManagers() {
  const radar = state.payload.manager_radar || {};
  document.getElementById("managerSummary").textContent =
    `${radar.stored_latest_count || 0} of ${radar.manager_count || 0} managers have current filings`;
  const focusGroups = buildVisibleFocusGroups(radar);
  const focusGrid = document.getElementById("focusManagerGrid");
  if (focusGrid) {
    focusGrid.innerHTML =
      focusGroups.length === 0
        ? empty("No focus manager coverage in this public snapshot.")
        : focusGroups.map(focusManagerGroupTemplate).join("");
  }
  const consensus = filterItems(radar.top_consensus || []);
  const maxValue = Math.max(...consensus.map((row) => row.common_value || 0), 1);
  document.getElementById("consensusList").innerHTML =
    consensus.length === 0
      ? empty("No crowded focus positions match this search.")
      : consensus.slice(0, 15).map((row) => consensusTemplate(row, maxValue)).join("");
  const managers = filterItems(radar.manager_status || []);
  document.getElementById("managerList").innerHTML =
    managers.length === 0 ? empty("No manager filing rows match this search.") : managers.map(managerTemplate).join("");
}

function buildVisibleFocusGroups(radar) {
  const groups = radar.focus_manager_groups || [
    { key: "focus", label: "Focus Managers", description: "", managers: radar.focus_managers || [] },
  ];
  return groups.map((group) => ({
    ...group,
    managers: filterItems(group.managers || []),
  })).filter((group) => group.managers.length);
}

function focusManagerGroupTemplate(group) {
  return `
    <section class="focus-group">
      <div class="focus-group-head">
        <div>
          <h4>${escapeHtml(group.label || "Focus Managers")}</h4>
          <p>${escapeHtml(group.description || "")}</p>
        </div>
        <span class="tag">${escapeHtml(group.managers.length)} managers</span>
      </div>
      <div class="focus-grid-inner">
        ${group.managers.map(focusManagerTemplate).join("")}
      </div>
    </section>
  `;
}

function focusManagerTemplate(row) {
  const positions = row.top_positions || [];
  const positionHtml = positions.slice(0, 5).map((position) => {
    const label = position.symbol || position.issuer || "Unresolved";
    const portfolio = Number(position.portfolio_weight || 0) > 0
      ? `<span>GW Port ${escapeHtml(formatWeight(position.portfolio_weight))}</span>`
      : "";
    return `
      <div class="mini-position">
        <strong>${escapeHtml(label)}</strong>
        <span>${escapeHtml(formatWeight(position.fund_weight))}</span>
        ${portfolio}
      </div>
    `;
  }).join("");
  const status = row.status === "ok" ? `Latest filing ${dateOnly(row.latest_filing_date)}` : "No current 13F on file";
  return `
    <article class="focus-card searchable" data-search="${searchAttribute(row)}">
      <div class="focus-title">
        <div>
          <strong>${escapeHtml(row.manager_name || row.manager_key)}</strong>
          <span>${escapeHtml(status)}</span>
        </div>
        <span class="tag">${escapeHtml(managerGroupLabel(row))}</span>
      </div>
      <div class="focus-metrics">
        ${focusMetricTemplate("13F coverage", row.symbol_coverage_pct)}
        ${focusMetricTemplate("Watchlist overlap", row.alloiq_watchlist_pct)}
        ${focusMetricTemplate("Portfolio overlap", row.default_portfolio_overlap_pct)}
        ${focusMetricTemplate("Top-10 concentration", row.top10_concentration_pct)}
      </div>
      <div class="mini-positions">${positionHtml || empty("No resolved top holdings for this manager.")}</div>
    </article>
  `;
}

function focusMetricTemplate(label, value) {
  return `
    <div>
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(formatPlainPct(value))}</strong>
    </div>
  `;
}

function consensusTemplate(row, maxValue) {
  const width = Math.max(4, ((row.common_value || 0) / maxValue) * 100);
  return `
    <div class="bar-row searchable" data-search="${searchAttribute(row)}">
      <strong>${escapeHtml(row.symbol)}</strong>
      <div class="bar-track"><div class="bar-fill" style="width:${width}%"></div></div>
      <div class="metric">${row.common_manager_count || 0} funds</div>
    </div>
  `;
}

function managerTemplate(row) {
  return `
    <article class="row searchable" data-search="${searchAttribute(row)}">
      <div class="row-main">
        <div class="symbol">${escapeHtml(row.manager_name || row.manager_key)}</div>
        <div class="metric">${escapeHtml(row.form || "")} | ${escapeHtml(row.filing_date || "")}</div>
      </div>
      <p>Public filing covers positions as of ${escapeHtml(row.report_date || "n/a")}.</p>
    </article>
  `;
}

function renderMacro() {
  const macro = state.payload.macro || {};
  const scores = macro.scores || {};
  const scoreItems = [
    ["AI momentum", scores.ai_momentum],
    ["Risk momentum", scores.risk_momentum],
    ["Defensive momentum", scores.defensive_momentum],
    ["Volatility move", scores.vol_move],
  ];
  document.getElementById("macroScores").innerHTML = scoreItems
    .map(([label, value]) => kpiTemplate({ label, value: formatPct(value), detail: "5-day macro proxy basket" }))
    .join("");
  const tape = filterItems(macro.tape || []);
  document.getElementById("macroTape").innerHTML =
    tape.length === 0 ? empty("No macro proxy symbols match this search.") : tape.map(macroTileTemplate).join("");
}

function macroTileTemplate(row) {
  const value = row.five_day_pct || 0;
  const color = heatColor(value);
  return `
    <article class="tile searchable" style="background:${color}" data-search="${searchAttribute(row)}">
      <strong>${escapeHtml(row.symbol)}</strong>
      <span>${escapeHtml(row.label || "")}</span>
      <strong>${formatPct(value)}</strong>
      <span>${escapeHtml(row.lens || "")}</span>
    </article>
  `;
}

function renderNews() {
  const items = filterItems(state.payload.news || []);
  document.getElementById("newsList").innerHTML =
    items.length === 0 ? empty("No linked news or catalysts match this search.") : items.slice(0, 30).map(newsTemplate).join("");
}

function newsTemplate(item) {
  return `
    <article class="news-item searchable" data-search="${searchAttribute(item)}">
      <a href="${escapeAttribute(item.url)}" target="_blank" rel="noreferrer">${escapeHtml(item.title || "Source")}</a>
      <p>${escapeHtml(item.source || "Source")} | ${escapeHtml(dateOnly(item.published_at))} | ${escapeHtml(item.event_label || "General news")} | ${escapeHtml(item.source_tier || "general")}</p>
    </article>
  `;
}

function drawSignalCanvas(cards) {
  const canvas = document.getElementById("signalCanvas");
  if (!canvas || !canvas.offsetWidth) return;
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.offsetWidth;
  const height = Math.max(320, Math.round(width * 0.44));
  state.signalPoints = [];
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  canvas.style.height = `${height}px`;
  const context = canvas.getContext("2d");
  context.scale(ratio, ratio);
  context.clearRect(0, 0, width, height);
  context.fillStyle = brandColors.surfaceAlt;
  context.fillRect(0, 0, width, height);
  const padLeft = 44;
  const padTop = 24;
  const padRight = 26;
  const padBottom = 62;
  const plotWidth = width - padLeft - padRight;
  const plotHeight = height - padTop - padBottom;
  context.strokeStyle = brandColors.line;
  context.lineWidth = 1;
  context.strokeRect(padLeft, padTop, plotWidth, plotHeight);
  context.globalAlpha = 0.72;
  for (let i = 1; i < 4; i += 1) {
    const x = padLeft + (plotWidth / 4) * i;
    const y = padTop + (plotHeight / 4) * i;
    context.beginPath();
    context.moveTo(x, padTop);
    context.lineTo(x, padTop + plotHeight);
    context.moveTo(padLeft, y);
    context.lineTo(padLeft + plotWidth, y);
    context.stroke();
  }
  context.globalAlpha = 1;
  context.fillStyle = brandColors.muted;
  context.font = "600 12px Geist, system-ui, sans-serif";
  context.fillText("Consensus funds", width - 136, height - 22);
  context.save();
  context.translate(14, height / 2 + 44);
  context.rotate(-Math.PI / 2);
  context.fillText("Signal score", 0, 0);
  context.restore();
  if (!cards.length) {
    context.fillStyle = brandColors.muted;
    context.font = "600 13px Geist, system-ui, sans-serif";
    context.fillText("No signal points match this search.", padLeft + 14, padTop + 30);
    return;
  }
  const maxScore = Math.max(...cards.map((card) => card.score || 0), 60);
  const maxManagers = Math.max(...cards.map((card) => card.consensus_manager_count || 0), 10);
  const legend = [
    ["Owned", brandColors.terminal],
    ["Add", brandColors.green],
    ["Trim/Risk", brandColors.red],
  ];
  legend.forEach(([label, color], index) => {
    const x = padLeft + index * 82;
    const y = height - 30;
    context.beginPath();
    context.arc(x, y - 4, 4, 0, Math.PI * 2);
    context.fillStyle = color;
    context.fill();
    context.fillStyle = brandColors.muted;
    context.font = "600 11px Geist, system-ui, sans-serif";
    context.fillText(label, x + 9, y);
  });
  cards.slice(0, 24).forEach((card) => {
    const x = padLeft + ((card.consensus_manager_count || 0) / maxManagers) * plotWidth;
    const y = padTop + (1 - (card.score || 0) / maxScore) * plotHeight;
    const radius = Math.max(7, Math.min(18, 7 + (card.news_count || 0) * 1.8));
    const selected = state.signalSelectedSymbol === card.symbol;
    const hovered = state.signalHoverSymbol === card.symbol;
    const action = (state.payload.portfolio_benchmark?.action_queue || []).find((item) => item.symbol === card.symbol);
    const delta = Number(action?.recommended_delta_weight || 0);
    const pointColor = delta > 0 ? brandColors.green : delta < 0 ? brandColors.red : bucketColors[card.bucket] || bucketColors.unmapped;
    state.signalPoints.push({ x, y, radius, card });
    if (selected || hovered) {
      context.beginPath();
      context.arc(x, y, radius + 6, 0, Math.PI * 2);
      context.fillStyle = brandColors.terminal;
      context.globalAlpha = selected ? 0.16 : 0.1;
      context.fill();
      context.globalAlpha = 1;
    }
    context.beginPath();
    context.arc(x, y, radius, 0, Math.PI * 2);
    context.fillStyle = pointColor;
    context.globalAlpha = selected || hovered ? 0.98 : 0.82;
    context.fill();
    context.globalAlpha = 1;
    context.strokeStyle = selected || hovered ? brandColors.terminal : brandColors.surface;
    context.lineWidth = selected || hovered ? 2 : 1;
    context.stroke();
    context.fillStyle = brandColors.ink;
    context.font = `${selected || hovered ? "800" : "700"} 12px Geist, system-ui, sans-serif`;
    context.fillText(card.symbol, Math.min(width - 48, x + radius + 5), y + 4);
  });
}

function filteredCards() {
  return filterItems(state.payload?.decision_cards || []);
}

function filterItems(items) {
  if (!state.query) return items;
  return items.filter((item) => searchText(item).includes(state.query));
}

function searchText(value) {
  return JSON.stringify(value || {}).toLowerCase();
}

function searchAttribute(value) {
  return escapeAttribute(searchText(value));
}

function labelize(value = "") {
  return String(value)
    .replace(/[_-]+/g, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ")
    .replace(/\bAi\b/g, "AI")
    .replace(/\bGw\b/g, "GW")
    .replace(/\b13f\b/gi, "13F");
}

function managerGroupLabel(row = {}) {
  if (row.manager_tier === "tier_1") return "AI Thesis Core";
  if (row.manager_tier === "tier_2") return "Manager Context Bench";
  return row.manager_group || "Manager Context Bench";
}

function formatPct(value) {
  if (value == null || Number.isNaN(Number(value))) return "n/a";
  const prefix = Number(value) > 0 ? "+" : "";
  return `${prefix}${number.format(value)}%`;
}

function formatPlainPct(value) {
  if (value == null || Number.isNaN(Number(value))) return "n/a";
  return `${number.format(value)}%`;
}

function formatPp(value) {
  if (value == null || Number.isNaN(Number(value))) return "n/a";
  const prefix = Number(value) > 0 ? "+" : "";
  return `${prefix}${number.format(value)} pp`;
}

function formatSignedWeight(value) {
  if (value == null || Number.isNaN(Number(value))) return "0.00%";
  const scaled = Number(value) * 100;
  const prefix = scaled > 0 ? "+" : "";
  return `${prefix}${number.format(scaled)}%`;
}

function formatWeight(value) {
  if (value == null || Number.isNaN(Number(value))) return "0.00%";
  return `${number.format(Number(value) * 100)}%`;
}

function dateOnly(value) {
  if (!value) return "date unavailable";
  return String(value).slice(0, 10);
}

function dateTimeShort(value) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value || "unknown");
  return parsed.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function timeOnly(value) {
  const parsed = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(parsed.getTime())) return "unknown";
  return parsed.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function heatColor(value) {
  const pct = Math.max(-8, Math.min(8, Number(value) || 0));
  if (pct >= 0) {
    const intensity = 36 + Math.round((pct / 8) * 42);
    return `hsl(157 48% ${intensity}%)`;
  }
  const intensity = 44 + Math.round((Math.abs(pct) / 8) * 26);
  return `hsl(0 42% ${intensity}%)`;
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

function escapeAttribute(value) {
  return escapeHtml(value);
}

init().catch((error) => {
  document.querySelector(".content").innerHTML = `<div class="empty">AlloIQ failed to load: ${escapeHtml(error.message)}</div>`;
});
