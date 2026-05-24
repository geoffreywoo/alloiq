const state = {
  payload: null,
  query: "",
  signalPoints: [],
  signalHoverSymbol: "",
  signalSelectedSymbol: "",
};

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

const number = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 2,
});

const bucketColors = {
  frontier_ai_platforms: "#2458a6",
  semis_networking_hbm: "#1f7a5f",
  neocloud_datacenters: "#ad6b20",
  power_grid_gas_nuclear: "#206f7a",
  ai_software_winners: "#5e4b7c",
  ai_enabled_financials: "#5f6f2a",
  disrupted_incumbents: "#b64a4a",
  unmapped: "#5b6673",
};

const aiThesisCoreBenchmarkNames = new Set(["AI Thesis Core median proxy", "Tier 1 median proxy"]);

async function init() {
  wireNavigation();
  wireSearch();
  wireSignalCanvas();
  const response = await fetch("/data/latest.json", { cache: "no-store" });
  state.payload = await response.json();
  render();
  window.addEventListener("resize", () => drawSignalCanvas(filteredCards()));
}

function wireNavigation() {
  document.querySelectorAll(".rail-button").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".rail-button").forEach((item) => item.classList.remove("active"));
      document.querySelectorAll(".view").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
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
        <span>Score ${escapeHtml(number.format(point.card.score || 0))}</span>
        <span>${escapeHtml(point.card.consensus_manager_count || 0)} funds | ${escapeHtml(point.card.news_count || 0)} news</span>
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
  document.getElementById("reportDate").textContent = `${payload.session || "report"} ${payload.as_of || ""}`;
  const privacy = payload.site?.privacy || "public";
  document.getElementById("privacyBadge").textContent =
    privacy === "public" ? "Public build" : "Private build";
  document.getElementById("regimeBadge").textContent = payload.macro?.regime || "Mixed macro tape";
  renderContent();
}

function renderContent() {
  renderKpis();
  renderDashboard();
  renderPortfolioContext();
  renderMoves();
  renderManagers();
  renderMacro();
  renderNews();
  drawSignalCanvas(filteredCards());
}

function renderKpis() {
  const payload = state.payload;
  const radar = payload.manager_radar || {};
  const macro = payload.macro || {};
  const portfolio = payload.portfolio || {};
  const benchmark = payload.portfolio_benchmark || {};
  const portfolioName = portfolio.display_name || "Geoffrey Woo Portfolio";
  const primaryLabel = benchmark.primary_label || "3M";
  const primaryReturn = benchmark.primary_portfolio_return ?? benchmark.portfolio_return_5d;
  const medianPeer = (benchmark.benchmarks || []).find((row) => aiThesisCoreBenchmarkNames.has(row.name))
    || (benchmark.benchmarks || []).find((row) => row.name === "Focus-manager median proxy");
  const nasdaq = (benchmark.benchmarks || []).find((row) => row.name === "Nasdaq 100");
  const actions = benchmark.action_queue || [];
  const kpis = [
    {
      label: `${portfolioName} ${primaryLabel} proxy`,
      value: formatPct(primaryReturn),
      detail: `current weights, ${formatPlainPct(benchmark.primary_price_coverage_pct ?? benchmark.price_coverage_pct)} priced coverage`,
    },
    {
      label: "Vs Nasdaq 100",
      value: nasdaq ? formatPp(nasdaq.portfolio_vs_benchmark) : "n/a",
      detail: nasdaq ? `QQQ ${formatPct(nasdaq.return_5d)}` : "benchmark unavailable",
    },
    {
      label: medianPeer && aiThesisCoreBenchmarkNames.has(medianPeer.name) ? "Vs AI Thesis Core" : "Vs focus peers",
      value: medianPeer ? formatPp(medianPeer.portfolio_vs_benchmark) : "n/a",
      detail: medianPeer ? `median proxy ${formatPct(medianPeer.return_5d)}` : "13F proxy unavailable",
    },
    {
      label: "Action queue",
      value: String(actions.length || 0),
      detail: actions[0] ? `${actions[0].symbol}: ${actions[0].action}` : "no urgent portfolio actions",
    },
    {
      label: "Macro regime",
      value: macro.regime || "Mixed",
      detail: scoreDetail(macro.scores),
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
  if (actionCount) actionCount.textContent = `${actions.length} items`;
  if (benchmarkHorizon) benchmarkHorizon.textContent = `${benchmark.primary_label || "3M"} proxy horizon`;
  const decisionStack = document.getElementById("decisionStack");
  if (decisionStack) {
    decisionStack.innerHTML = decisionStackTemplate(benchmark, actions, macro);
  }
  document.getElementById("horizonList").innerHTML =
    horizons.length === 0 ? empty("No return windows.") : horizons.map(horizonTemplate).join("");
  const returnCurve = document.getElementById("returnCurve");
  if (returnCurve) {
    returnCurve.innerHTML = returnCurveTemplate(horizons);
  }
  document.getElementById("benchmarkList").innerHTML =
    benchmarkRows.length === 0 ? empty("No benchmark data.") : benchmarkRows.slice(0, 7).map(benchmarkTemplate).join("");
  document.getElementById("portfolioActionList").innerHTML =
    actions.length === 0 ? empty("No matching action items.") : actions.slice(0, 7).map(actionTemplate).join("");
  const actionVisual = document.getElementById("actionSizingVisual");
  if (actionVisual) {
    actionVisual.innerHTML =
      actions.length === 0 ? empty("No action sizing data.") : actionSizingVisualTemplate(actions.slice(0, 8));
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
    gaps.length === 0 ? empty("No matching exposure gaps.") : gaps.slice(0, 7).map(exposureGapTemplate).join("");
  document.getElementById("studyList").innerHTML =
    studies.length === 0 ? empty("No matching study items.") : studies.slice(0, 8).map(studyTemplate).join("");
}

function decisionStackTemplate(benchmark, actions, macro) {
  const primary = actions[0] || {};
  const peer = preferredBenchmark(benchmark.benchmarks || []);
  const delta = Number(primary.recommended_delta_weight || 0);
  const hedge = Number(primary.hedge_weight || 0);
  const confidence = primary.symbol
    ? Math.min(99, Math.round((Number(primary.signal_family_count || 0) * 17) + Math.min(Number(primary.priority || 0), 80) / 2))
    : 0;
  const actionText = primary.symbol ? decisionActionLabel(primary, delta, hedge) : "No action";
  return `
    <article class="decision-card decision-primary">
      <span>Top action</span>
      <strong class="${delta > 0 ? "positive" : delta < 0 ? "negative" : ""}">${escapeHtml(actionText)}</strong>
      <small>${escapeHtml(primary.action || "No portfolio-weight changes triggered.")}</small>
    </article>
    <article class="decision-card">
      <span>Active spread</span>
      <strong class="${Number(peer?.portfolio_vs_benchmark || 0) >= 0 ? "positive" : "negative"}">${escapeHtml(peer ? formatPp(peer.portfolio_vs_benchmark) : "n/a")}</strong>
      <small>${escapeHtml(peer ? `vs ${peer.name}` : "benchmark unavailable")}</small>
    </article>
    <article class="decision-card">
      <span>Macro gate</span>
      <strong>${escapeHtml(macro.regime || "Mixed")}</strong>
      <small>${escapeHtml(scoreDetail(macro.scores || {}))}</small>
    </article>
    <article class="decision-card">
      <span>Evidence score</span>
      <strong>${escapeHtml(confidence ? `${confidence}/100` : "n/a")}</strong>
      <small>${escapeHtml(primary.symbol ? `${primary.signal_family_count || 0} signal families, priority ${number.format(primary.priority || 0)}` : "no ranked action")}</small>
    </article>
  `;
}

function decisionActionLabel(primary, delta, hedge) {
  if (delta > 0) return `${primary.symbol} ${formatSignedWeight(delta)}`;
  if (delta < 0) return `${primary.symbol} ${formatSignedWeight(delta)}`;
  if (hedge > 0) return `${primary.symbol} hedge ${formatWeight(hedge)}`;
  return `${primary.symbol} hold`;
}

function preferredBenchmark(rows) {
  return rows.find((row) => aiThesisCoreBenchmarkNames.has(row.name))
    || rows.find((row) => row.name === "Nasdaq 100")
    || rows[0];
}

function returnCurveTemplate(horizons) {
  const rows = (horizons || []).filter((row) => row.portfolio_return != null && !Number.isNaN(Number(row.portfolio_return)));
  if (rows.length < 2) return empty("No return curve yet.");
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
      <p class="curve-note">Current-weight price proxy, not realized account performance.</p>
    </div>
  `;
}

function horizonTemplate(row) {
  return `
    <article class="horizon-tile searchable" data-search="${searchAttribute(row)}">
      <span>${escapeHtml(row.label || row.key || "Window")}</span>
      <strong>${escapeHtml(formatPct(row.portfolio_return))}</strong>
      <small>${escapeHtml(formatPlainPct(row.price_coverage_pct))} covered | current weights</small>
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
          <p>${escapeHtml(row.symbol || "")}</p>
        </div>
        <div class="benchmark-numbers">
          <span>${escapeHtml(formatPct(row.return_pct ?? row.return_5d))}</span>
          <strong class="${activeClass}">${escapeHtml(formatPp(active))}</strong>
        </div>
      </div>
    </article>
  `;
}

function actionTemplate(item) {
  const delta = Number(item.recommended_delta_weight || 0);
  const deltaClass = delta > 0 ? "positive" : delta < 0 ? "negative" : "";
  const targetText = item.target_weight == null || item.target_weight === item.post_action_weight
    ? ""
    : ` | Target ${formatWeight(item.target_weight)}`;
  const hedgeText = Number(item.hedge_weight || 0) > 0
    ? ` | Hedge ${formatWeight(item.hedge_weight)}`
    : "";
  return `
    <article class="row searchable" data-search="${searchAttribute(item)}">
      <div class="row-main">
        <div class="symbol">${escapeHtml(item.symbol)}</div>
        <div class="action-size">
          <strong class="${deltaClass}">${escapeHtml(formatSignedWeight(delta))}</strong>
          <span>Post ${escapeHtml(formatWeight(item.post_action_weight ?? item.portfolio_weight))}${escapeHtml(targetText)}${escapeHtml(hedgeText)}</span>
        </div>
      </div>
      <p><strong>${escapeHtml(item.trade_action ? labelize(item.trade_action) : "Research sizing")}:</strong> ${escapeHtml(item.action || "")}</p>
      <p>${escapeHtml(item.why || "")}</p>
      <div class="tags">
        <span class="tag">Current ${escapeHtml(formatWeight(item.portfolio_weight))}</span>
        <span class="tag">Peer avg ${escapeHtml(formatWeight(item.peer_avg_weight))}</span>
        <span class="tag">5d ${escapeHtml(formatPct(item.five_day_pct))}</span>
        <span class="tag">Contribution ${escapeHtml(formatPp(item.contribution_pct))}</span>
        <span class="tag">Priority ${escapeHtml(number.format(item.priority || 0))}</span>
        <span class="tag">${escapeHtml(item.signal_family_count || 0)} signals</span>
      </div>
    </article>
  `;
}

function actionSizingVisualTemplate(actions) {
  const maxAbs = Math.max(...actions.map((item) => Math.abs(Number(item.recommended_delta_weight || 0))), 0.01);
  return actions.map((item) => {
    const delta = Number(item.recommended_delta_weight || 0);
    const width = Math.max(2, Math.min(100, (Math.abs(delta) / maxAbs) * 100));
    const direction = delta > 0 ? "add" : delta < 0 ? "trim" : "hold";
    return `
      <article class="delta-row searchable" data-search="${searchAttribute(item)}">
        <div class="delta-head">
          <strong>${escapeHtml(item.symbol)}</strong>
          <span class="${delta > 0 ? "positive" : delta < 0 ? "negative" : ""}">${escapeHtml(formatSignedWeight(delta))}</span>
        </div>
        <div class="delta-bar delta-bar-strong" aria-label="${escapeAttribute(`${item.symbol} ${formatSignedWeight(delta)}`)}">
          <div class="delta-half delta-negative">
            ${delta < 0 ? `<span class="delta-fill ${direction}" style="width:${width}%"></span>` : ""}
          </div>
          <div class="delta-axis"></div>
          <div class="delta-half delta-positive">
            ${delta > 0 ? `<span class="delta-fill ${direction}" style="width:${width}%"></span>` : ""}
          </div>
        </div>
        <div class="delta-meta">
          <span>Current ${escapeHtml(formatWeight(item.portfolio_weight))}</span>
          <span>Post ${escapeHtml(formatWeight(item.post_action_weight ?? item.portfolio_weight))}</span>
          ${Number(item.hedge_weight || 0) > 0 ? `<span>Hedge ${escapeHtml(formatWeight(item.hedge_weight))}</span>` : ""}
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
  if (!rows.length) return empty("No attribution data available.");
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
          <span>5D ${escapeHtml(formatPct(row.five_day_pct))}</span>
          <span>Weight ${escapeHtml(formatWeight(row.weight))}</span>
        </div>
      </article>
    `;
  }).join("");
}

function peerGapTemplate(items) {
  const rows = (items || [])
    .filter((row) => row.symbol && (row.peer_avg_weight != null || row.target_weight != null))
    .slice(0, 8);
  if (!rows.length) return empty("No peer weight comparison available.");
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
          <span>${escapeHtml(formatWeight(current))} now | ${escapeHtml(formatWeight(peer))} peer</span>
        </div>
        <div class="peer-gap-track" aria-label="${escapeAttribute(`${row.symbol} current ${formatWeight(current)} peer ${formatWeight(peer)}`)}">
          <span class="peer-gap-current" style="width:${currentWidth}%"></span>
          <span class="peer-gap-peer" style="width:${peerWidth}%"></span>
          <span class="peer-gap-target" style="left:${targetLeft}%"></span>
        </div>
        <div class="peer-gap-legend">
          <span><i class="legend-current"></i>Current</span>
          <span><i class="legend-peer"></i>Peer avg</span>
          <span><i class="legend-target"></i>Target/post</span>
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
        <span class="tag">Current ${escapeHtml(formatWeight(gap.portfolio_weight))}</span>
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
        <span class="tag">Weight ${escapeHtml(formatWeight(item.portfolio_weight))}</span>
        ${item.five_day_pct == null ? "" : `<span class="tag">5d ${escapeHtml(formatPct(item.five_day_pct))}</span>`}
        ${item.contribution_pct == null ? "" : `<span class="tag">Contribution ${escapeHtml(formatPp(item.contribution_pct))}</span>`}
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
        <div class="metric">Score ${number.format(card.score || 0)} | ${price} | ${formatPct(card.five_day_pct)}</div>
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
    container.innerHTML = empty("No Geoffrey Woo Portfolio weights available.");
    return;
  }
  const bucketHtml = `
    <div>
      <h4>Bucket Weights</h4>
      <div class="bar-list">
        ${buckets.slice(0, 8).map((row) => weightBarTemplate(labelize(row.bucket), row.weight, row.bucket)).join("")}
      </div>
    </div>
  `;
  const symbolHtml = `
    <div>
      <h4>Top Symbol Weights</h4>
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
    moves.length === 0 ? empty("No matching moves.") : moves.slice(0, 12).map(moveTemplate).join("");
}

function moveTemplate(move) {
  return `
    <article class="idea searchable" data-search="${searchAttribute(move)}">
      <h3>
        <span>${escapeHtml(move.symbol || "Move")}</span>
        <span class="tag">${escapeHtml(move.action || "Research")}</span>
      </h3>
      <p>${escapeHtml(move.rationale || "")}</p>
      <p><strong>Evidence:</strong> ${escapeHtml(move.manager_count || 0)} tracked funds, ${escapeHtml(move.signal_family_count || 0)} signal families, ${escapeHtml(move.news_count || 0)} news hits, catalyst score ${escapeHtml(move.event_score || 0)}, score ${escapeHtml(move.signal_score || 0)}, 5d ${escapeHtml(formatPct(move.five_day_pct))}, current weight ${escapeHtml(formatWeight(move.portfolio_weight))}.</p>
      <p><strong>Trigger:</strong> ${escapeHtml(move.trigger || "")}</p>
      <p><strong>Risk:</strong> ${escapeHtml(move.risk || "")}</p>
      <div class="tags">
        <span class="tag">${escapeHtml(move.posture || "Research")}</span>
        <span class="tag">Bucket ${escapeHtml(formatWeight(move.bucket_weight))}</span>
        ${(move.signal_families || []).slice(0, 3).map((family) => `<span class="tag">${escapeHtml(labelize(family))}</span>`).join("")}
        ${(move.event_types || []).slice(0, 2).map((event) => `<span class="tag">${escapeHtml(labelize(event))}</span>`).join("")}
        <span class="tag">Conviction ${escapeHtml(move.conviction || 0)}</span>
        <span class="tag">${escapeHtml(labelize(move.bucket || ""))}</span>
      </div>
    </article>
  `;
}

function renderManagers() {
  const radar = state.payload.manager_radar || {};
  document.getElementById("managerSummary").textContent =
    `${radar.stored_latest_count || 0}/${radar.manager_count || 0} managers current`;
  const focusGroups = buildVisibleFocusGroups(radar);
  const focusGrid = document.getElementById("focusManagerGrid");
  if (focusGrid) {
    focusGrid.innerHTML =
      focusGroups.length === 0
        ? empty("No focus manager tracking available.")
        : focusGroups.map(focusManagerGroupTemplate).join("");
  }
  const consensus = filterItems(radar.top_consensus || []);
  const maxValue = Math.max(...consensus.map((row) => row.common_value || 0), 1);
  document.getElementById("consensusList").innerHTML =
    consensus.length === 0
      ? empty("No matching consensus positions.")
      : consensus.slice(0, 15).map((row) => consensusTemplate(row, maxValue)).join("");
  const managers = filterItems(radar.manager_status || []);
  document.getElementById("managerList").innerHTML =
    managers.length === 0 ? empty("No matching managers.") : managers.map(managerTemplate).join("");
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
  const status = row.status === "ok" ? `Filed ${dateOnly(row.latest_filing_date)}` : "No latest 13F";
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
        ${focusMetricTemplate("Coverage", row.symbol_coverage_pct)}
        ${focusMetricTemplate("Watchlist", row.alloiq_watchlist_pct)}
        ${focusMetricTemplate("GW Portfolio", row.default_portfolio_overlap_pct)}
        ${focusMetricTemplate("Top 10", row.top10_concentration_pct)}
      </div>
      <div class="mini-positions">${positionHtml || empty("No resolved top positions.")}</div>
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
      <p>Report date ${escapeHtml(row.report_date || "n/a")}</p>
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
    .map(([label, value]) => kpiTemplate({ label, value: formatPct(value), detail: "5-day proxy basket" }))
    .join("");
  const tape = filterItems(macro.tape || []);
  document.getElementById("macroTape").innerHTML =
    tape.length === 0 ? empty("No matching macro symbols.") : tape.map(macroTileTemplate).join("");
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
    items.length === 0 ? empty("No matching news.") : items.slice(0, 30).map(newsTemplate).join("");
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
  context.fillStyle = "#f8faf6";
  context.fillRect(0, 0, width, height);
  const padLeft = 44;
  const padTop = 24;
  const padRight = 26;
  const padBottom = 62;
  const plotWidth = width - padLeft - padRight;
  const plotHeight = height - padTop - padBottom;
  context.strokeStyle = "#d8ddd5";
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
  context.fillStyle = "#5b6673";
  context.font = "600 12px Geist, system-ui, sans-serif";
  context.fillText("Consensus funds", width - 136, height - 22);
  context.save();
  context.translate(14, height / 2 + 44);
  context.rotate(-Math.PI / 2);
  context.fillText("Signal score", 0, 0);
  context.restore();
  if (!cards.length) {
    context.fillStyle = "#5b6673";
    context.font = "600 13px Geist, system-ui, sans-serif";
    context.fillText("No matching signals.", padLeft + 14, padTop + 30);
    return;
  }
  const maxScore = Math.max(...cards.map((card) => card.score || 0), 60);
  const maxManagers = Math.max(...cards.map((card) => card.consensus_manager_count || 0), 10);
  const legend = [
    ["Owned", "#101820"],
    ["Add", "#1f7a5f"],
    ["Trim/Risk", "#b64a4a"],
  ];
  legend.forEach(([label, color], index) => {
    const x = padLeft + index * 82;
    const y = height - 30;
    context.beginPath();
    context.arc(x, y - 4, 4, 0, Math.PI * 2);
    context.fillStyle = color;
    context.fill();
    context.fillStyle = "#5b6673";
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
    const pointColor = delta > 0 ? "#1f7a5f" : delta < 0 ? "#b64a4a" : bucketColors[card.bucket] || bucketColors.unmapped;
    state.signalPoints.push({ x, y, radius, card });
    if (selected || hovered) {
      context.beginPath();
      context.arc(x, y, radius + 6, 0, Math.PI * 2);
      context.fillStyle = "#101820";
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
    context.strokeStyle = selected || hovered ? "#101820" : "#ffffff";
    context.lineWidth = selected || hovered ? 2 : 1;
    context.stroke();
    context.fillStyle = "#101820";
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
  return value.replaceAll("_", " ");
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
