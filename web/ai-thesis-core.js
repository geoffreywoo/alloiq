const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const compactMoney = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 2,
});
const priceMoney = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

const coreKeys = new Set(["situational-awareness", "altimeter", "dragoneer"]);

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
  document.body.insertAdjacentHTML("afterbegin", `<p class="stale-banner">AI Core failed to load: ${escapeHtml(error.message)}</p>`);
});

async function init() {
  const response = await fetch(`/data/latest.json?ts=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  payload = await response.json();
  render();
  document.getElementById("copyCoreButton")?.addEventListener("click", copyCoreCsv);
}

function render() {
  const asOf = payload.as_of || "";
  document.title = `AI Core - ${asOf}`;
  document.getElementById("coreDate").textContent = asOf ? `Snapshot ${asOf}` : "Public snapshot";
  renderSummary();
  renderOverlap();
  renderManagers();
}

function coreManagers() {
  return (payload?.manager_radar?.focus_managers || [])
    .filter((manager) => manager.manager_tier === "tier_1" && coreKeys.has(manager.manager_key))
    .sort((a, b) => [...coreKeys].indexOf(a.manager_key) - [...coreKeys].indexOf(b.manager_key));
}

function managerPositions(manager) {
  return [...(manager.positions || manager.top_positions || [])]
    .sort((a, b) => Number(a.rank || 999) - Number(b.rank || 999) || Number(b.fund_weight || 0) - Number(a.fund_weight || 0));
}

function renderSummary() {
  const managers = coreManagers();
  const allPositions = managers.flatMap((manager) => managerPositions(manager).map((position) => ({ manager, position })));
  const owned = allPositions.filter(({ position }) => Number(position.portfolio_weight || 0) > 0);
  const priced = allPositions.filter(({ position }) => position.current_value_estimate != null);
  const overlap = overlapRows().filter((row) => row.manager_count > 1);
  const latestDate = managers.map((manager) => manager.latest_report_date).filter(Boolean).sort().at(-1);
  const summary = [
    {
      label: "Core funds",
      value: String(managers.length),
      detail: "Situational Awareness, Altimeter, Dragoneer.",
    },
    {
      label: "Published positions",
      value: String(allPositions.length),
      detail: `${priced.length} have current value estimates.`,
    },
    {
      label: "Symbols in 2+ funds",
      value: String(overlap.length),
      detail: "Consensus inside the AI Thesis Core.",
    },
    {
      label: "GW owned overlaps",
      value: String(owned.length),
      detail: "Rows where the Geoffrey Woo Portfolio has current weight.",
    },
    {
      label: "Latest report date",
      value: latestDate || "n/a",
      detail: "13F report period, not live trading.",
    },
  ];
  document.getElementById("coreSummary").innerHTML = summary.map(summaryCard).join("");
}

function renderOverlap() {
  const rows = overlapRows().slice(0, 18);
  document.getElementById("coreOverlapList").innerHTML = rows.length
    ? rows.map((row) => {
        const width = Math.max(3, Math.min(100, row.total_weight * 100));
        return `
          <article class="core-overlap-row">
            <div class="core-overlap-symbol">
              <strong>${escapeHtml(row.label)}</strong>
              <span>${escapeHtml(labelize(row.bucket))}</span>
            </div>
            <div class="core-overlap-bar">
              <div class="bar-track">
                <div class="bar-fill" style="width:${width}%;background:${bucketColors[row.bucket] || bucketColors.unmapped}"></div>
              </div>
              <small>${escapeHtml(row.managers.join(", "))}</small>
            </div>
            <div class="core-overlap-metrics">
              <span>${escapeHtml(row.manager_count)} funds</span>
              <strong>${escapeHtml(formatWeight(row.avg_weight))} avg</strong>
              <small>GW ex-cash ${escapeHtml(formatWeight(row.portfolio_weight))}</small>
            </div>
          </article>
        `;
      }).join("")
    : empty("No AI Thesis Core overlap data is available.");
}

function overlapRows() {
  const byKey = new Map();
  for (const manager of coreManagers()) {
    for (const position of managerPositions(manager)) {
      const label = position.symbol || position.issuer || "Unmapped";
      const key = (position.symbol || position.issuer || "").toUpperCase();
      if (!key) continue;
      const row = byKey.get(key) || {
        label,
        bucket: position.bucket || "unmapped",
        manager_count: 0,
        total_weight: 0,
        portfolio_weight: 0,
        managers: [],
      };
      row.manager_count += 1;
      row.total_weight += Number(position.fund_weight || 0);
      row.portfolio_weight = Math.max(row.portfolio_weight, Number(position.portfolio_weight || 0));
      row.managers.push(shortManagerName(manager));
      if (row.bucket === "unmapped" && position.bucket) row.bucket = position.bucket;
      byKey.set(key, row);
    }
  }
  return [...byKey.values()]
    .map((row) => ({ ...row, avg_weight: row.manager_count ? row.total_weight / row.manager_count : 0 }))
    .sort((a, b) => b.manager_count - a.manager_count || b.total_weight - a.total_weight);
}

function renderManagers() {
  const managers = coreManagers();
  document.getElementById("coreManagerList").innerHTML = managers.length
    ? managers.map(managerTemplate).join("")
    : empty("No AI Thesis Core managers are available in this public snapshot.");
}

function managerTemplate(manager) {
  const positions = managerPositions(manager);
  return `
    <section class="panel core-manager-panel searchable">
      <div class="core-manager-head">
        <div>
          <p class="eyebrow">${escapeHtml(shortManagerName(manager))}</p>
          <h2>${escapeHtml(manager.manager_name || manager.manager_key)}</h2>
          <p>${escapeHtml(manager.lens || "AI-market public-equity signal.")}</p>
        </div>
        <div class="core-manager-meta">
          <span>Report ${escapeHtml(manager.latest_report_date || "n/a")}</span>
          <span>Filed ${escapeHtml(manager.latest_filing_date || "n/a")}</span>
          ${manager.filing_url ? `<a href="${escapeAttribute(manager.filing_url)}" target="_blank" rel="noreferrer">SEC filing</a>` : ""}
        </div>
      </div>
      <div class="core-manager-metrics">
        ${metricTemplate("Positions", positions.length)}
        ${metricTemplate("Symbol coverage", formatPlainPct(manager.symbol_coverage_pct))}
        ${metricTemplate("GW symbol overlap", formatPlainPct(manager.default_portfolio_overlap_pct))}
        ${metricTemplate("Top 10 concentration", formatPlainPct(manager.top10_concentration_pct))}
      </div>
      <div class="core-table-wrap">
        <table class="core-position-table">
          <thead>
            <tr>
              <th>Rank</th>
              <th>Position</th>
              <th>Thesis Bucket</th>
              <th>Fund Weight</th>
              <th>GW Ex-cash Weight</th>
              <th>Entry Proxy</th>
              <th>Current Est.</th>
              <th>Est. Return</th>
              <th>Read</th>
            </tr>
          </thead>
          <tbody>
            ${positions.map(positionTemplate).join("")}
          </tbody>
        </table>
      </div>
    </section>
  `;
}

function positionTemplate(position) {
  const label = position.symbol || position.issuer || "Unmapped";
  const relationship = Number(position.portfolio_weight || 0) > 0 ? "Owned" : position.symbol ? "White space" : "Needs mapping";
  return `
    <tr>
      <td>${escapeHtml(position.rank || "")}</td>
      <td>
        <strong>${escapeHtml(label)}</strong>
        <span>${escapeHtml(position.issuer || "")}</span>
      </td>
      <td>${escapeHtml(labelize(position.bucket || "unmapped"))}</td>
      <td><strong>${escapeHtml(formatWeight(position.fund_weight))}</strong></td>
      <td>${escapeHtml(formatWeight(position.portfolio_weight))}</td>
      <td>
        <strong>${escapeHtml(formatPrice(position.entry_price_estimate))}</strong>
        <span>13F mark ${escapeHtml(formatPrice(position.latest_report_price))}</span>
      </td>
      <td>
        <strong>${escapeHtml(formatMoney(position.current_value_estimate))}</strong>
        <span>Px ${escapeHtml(formatPrice(position.current_price))}</span>
      </td>
      <td>
        <strong class="${Number(position.entry_return_estimate_pct || 0) >= 0 ? "positive" : "negative"}">${escapeHtml(formatSignedPct(position.entry_return_estimate_pct))}</strong>
        <span>${escapeHtml(position.valuation_confidence || "estimate")} confidence</span>
      </td>
      <td><span class="tag">${escapeHtml(relationship)}</span></td>
    </tr>
  `;
}

async function copyCoreCsv() {
  const rows = [
    [
      "manager",
      "rank",
      "symbol",
      "issuer",
      "fund_weight_pct",
      "bucket",
      "gw_ex_cash_weight_pct",
      "entry_price_estimate",
      "latest_report_price",
      "current_price",
      "current_value_estimate",
      "entry_return_estimate_pct",
      "valuation_confidence",
      "report_date",
      "filing_date",
      "filing_url",
    ],
  ];
  for (const manager of coreManagers()) {
    for (const position of managerPositions(manager)) {
      rows.push([
        shortManagerName(manager),
        position.rank || "",
        position.symbol || "",
        position.issuer || "",
        percentNumber(position.fund_weight),
        labelize(position.bucket || "unmapped"),
        percentNumber(position.portfolio_weight),
        rawNumber(position.entry_price_estimate),
        rawNumber(position.latest_report_price),
        rawNumber(position.current_price),
        rawNumber(position.current_value_estimate),
        rawNumber(position.entry_return_estimate_pct),
        position.valuation_confidence || "",
        manager.latest_report_date || "",
        manager.latest_filing_date || "",
        manager.filing_url || "",
      ]);
    }
  }
  const csv = rows.map((row) => row.map(csvCell).join(",")).join("\n");
  try {
    await navigator.clipboard.writeText(csv);
    document.getElementById("copyCoreButton").textContent = "Copied core CSV";
  } catch {
    fallbackCopy(csv);
    document.getElementById("copyCoreButton").textContent = "Copied";
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

function summaryCard(item) {
  return `
    <article class="kpi">
      <span>${escapeHtml(item.label)}</span>
      <strong>${escapeHtml(item.value)}</strong>
      <small>${escapeHtml(item.detail)}</small>
    </article>
  `;
}

function metricTemplate(label, value) {
  return `
    <article>
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </article>
  `;
}

function shortManagerName(manager) {
  if (manager.manager_key === "situational-awareness") return "Situational Awareness";
  if (manager.manager_key === "altimeter") return "Altimeter";
  if (manager.manager_key === "dragoneer") return "Dragoneer";
  return manager.manager_name || manager.manager_key || "Manager";
}

function formatWeight(value) {
  if (value == null || Number.isNaN(Number(value))) return "n/a";
  return `${number.format(Number(value) * 100)}%`;
}

function formatPlainPct(value) {
  if (value == null || Number.isNaN(Number(value))) return "n/a";
  return `${number.format(Number(value))}%`;
}

function percentNumber(value) {
  return number.format(Number(value || 0) * 100);
}

function rawNumber(value) {
  return value == null || Number.isNaN(Number(value)) ? "" : String(Number(value));
}

function formatPrice(value) {
  if (value == null || Number.isNaN(Number(value))) return "n/a";
  return priceMoney.format(Number(value));
}

function formatMoney(value) {
  if (value == null || Number.isNaN(Number(value))) return "n/a";
  return compactMoney.format(Number(value));
}

function formatSignedPct(value) {
  if (value == null || Number.isNaN(Number(value))) return "n/a";
  const numeric = Number(value);
  const prefix = numeric > 0 ? "+" : "";
  return `${prefix}${number.format(numeric)}%`;
}

function labelize(value) {
  if (bucketLabels[value]) return bucketLabels[value];
  return String(value || "")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function csvCell(value) {
  const text = String(value ?? "");
  return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
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
  return escapeHtml(value).replaceAll("`", "&#096;");
}
