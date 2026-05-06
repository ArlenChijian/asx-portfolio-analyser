// Frontend logic for ASX Portfolio Analyser.
// Single file, no framework: form -> POST /api/portfolio -> render.

const API_BASE = "";

// Asset-class palette (deep blue family with complementary accents).
const PALETTE = [
  "#1e3a8a", "#3b82f6", "#0ea5e9", "#06b6d4", "#0891b2",
  "#0d9488", "#10b981", "#65a30d", "#f59e0b", "#dc2626",
  "#9333ea", "#6b7280",
];

let allocChart = null;
let projChart = null;

// --------------- Helpers ---------------

function fmtPct(x, digits = 1) {
  if (x === null || x === undefined || Number.isNaN(x)) return "–";
  return (x * 100).toFixed(digits) + "%";
}
// Whole-percent for display weights — easier for users to read.
function fmtWholePct(x) {
  if (x === null || x === undefined || Number.isNaN(x)) return "–";
  return Math.round(x * 100) + "%";
}
function fmtAud(x, digits = 0) {
  if (x === null || x === undefined || Number.isNaN(x)) return "–";
  return "$" + Number(x).toLocaleString("en-AU",
    { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
function fmtSharpe(x) {
  if (x === null || x === undefined || Number.isNaN(x)) return "–";
  return Number(x).toFixed(2);
}

// --------------- Bootstrapping ---------------

document.addEventListener("DOMContentLoaded", async () => {
  await loadSectors();
  document.getElementById("profile-form").addEventListener("submit", onSubmit);
});

async function loadSectors() {
  const list = document.getElementById("sector-list");
  try {
    const r = await fetch(API_BASE + "/api/sectors");
    if (!r.ok) throw new Error("HTTP " + r.status);
    const data = await r.json();
    list.innerHTML = "";
    for (const sector of data.sectors) {
      const id = "sec-" + sector.replace(/\W+/g, "-").toLowerCase();
      const wrapper = document.createElement("label");
      wrapper.innerHTML = `<input type="checkbox" name="exclude_sectors" value="${sector}" id="${id}"> <span>${sector}</span>`;
      list.appendChild(wrapper);
    }
  } catch (e) {
    list.innerHTML = `<span class="muted">Could not load sectors (${e.message}). Server may not be running yet.</span>`;
  }
}

// --------------- Submission ---------------

async function onSubmit(ev) {
  ev.preventDefault();
  const form = ev.currentTarget;
  const status = document.getElementById("status-msg");
  const btn = document.getElementById("submit-btn");

  status.textContent = "Building portfolio...";
  status.style.color = "var(--text-muted)";
  btn.disabled = true;

  // Pull min-yield as a percent and convert to fraction.
  const minYieldPct = parseFloat(form.min_dividend_yield_pct.value || "0");
  // Max-vol blank means "no limit" -> null.
  const maxVolPctRaw = form.max_volatility_pct.value;
  const maxVol = maxVolPctRaw === "" ? null : parseFloat(maxVolPctRaw) / 100;

  const data = {
    capital: parseFloat(form.capital.value),
    risk_profile: form.risk_profile.value,
    horizon_years: parseInt(form.horizon_years.value, 10),
    prefer_income: form.prefer_income.checked,
    esg_only: form.esg_only.checked,
    etfs_only: form.etfs_only.checked,
    geo_tilt: form.geo_tilt.value,
    prefer_hedged: form.prefer_hedged.checked,
    min_dividend_yield: (minYieldPct || 0) / 100,
    max_volatility: maxVol,
    max_holdings: parseInt(form.max_holdings.value, 10),
    max_position_size: parseFloat(form.max_position_size.value),
    exclude_sectors: Array.from(form.querySelectorAll('input[name="exclude_sectors"]:checked'))
                          .map(c => c.value),
  };

  try {
    const r = await fetch(API_BASE + "/api/portfolio", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || ("HTTP " + r.status));
    }
    const result = await r.json();
    renderResult(result);
    status.textContent = "Done.";
    status.style.color = "var(--success)";
  } catch (e) {
    status.textContent = "Error: " + e.message;
    status.style.color = "var(--danger)";
  } finally {
    btn.disabled = false;
  }
}

// --------------- Rendering ---------------

function renderResult(r) {
  document.getElementById("results").hidden = false;

  // Headline metrics
  document.getElementById("m-return").textContent = fmtPct(r.expected_return);
  document.getElementById("m-vol").textContent = fmtPct(r.expected_volatility);
  document.getElementById("m-dd").textContent = fmtPct(r.expected_max_drawdown);
  document.getElementById("m-yield").textContent = fmtPct(r.expected_dividend_yield);

  renderProjection(r.projection, r.capital);
  renderAllocation(r.realised_allocation);
  renderHoldings(r.holdings);
  renderNotes(r.notes);

  document.getElementById("results").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderProjection(proj, capital) {
  const card = document.getElementById("projection-card");
  if (!proj) { card.hidden = true; return; }
  card.hidden = false;

  document.getElementById("p-low").textContent = fmtAud(proj.low);
  document.getElementById("p-median").textContent = fmtAud(proj.median);
  document.getElementById("p-high").textContent = fmtAud(proj.high);
  document.getElementById("p-median-cagr").textContent =
    `${fmtPct(proj.median_return_pct)} per year over ${proj.horizon_years}y`;

  if (typeof Chart === "undefined") return;

  // Generate yearly projection paths for low/median/high using the
  // same lognormal model. Since the backend gives endpoint values,
  // we interpolate the path back assuming constant drift+spread per
  // year (i.e. the geometric path).
  const years = proj.horizon_years;
  const points = [];
  for (let t = 0; t <= years; t++) {
    const f = t / years;
    points.push({
      t,
      low:    capital * Math.pow(proj.low / capital,    f),
      median: capital * Math.pow(proj.median / capital, f),
      high:   capital * Math.pow(proj.high / capital,   f),
    });
  }

  const ctx = document.getElementById("proj-chart").getContext("2d");
  if (projChart) projChart.destroy();
  projChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: points.map(p => `Year ${p.t}`),
      datasets: [
        { label: "Optimistic (P90)", data: points.map(p => p.high),
          borderColor: "#1e3a8a40", backgroundColor: "#1e3a8a20",
          fill: "+1", pointRadius: 0, borderWidth: 1 },
        { label: "Median",          data: points.map(p => p.median),
          borderColor: "#1e3a8a", backgroundColor: "#1e3a8a",
          fill: false, pointRadius: 0, borderWidth: 2 },
        { label: "Pessimistic (P10)", data: points.map(p => p.low),
          borderColor: "#1e3a8a40", backgroundColor: "#1e3a8a20",
          fill: false, pointRadius: 0, borderWidth: 1 },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: "bottom", labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: {
          callbacks: {
            label: ctx => `${ctx.dataset.label}: ${fmtAud(ctx.parsed.y, 0)}`,
          },
        },
      },
      scales: {
        y: {
          ticks: { callback: v => fmtAud(v, 0), font: { size: 11 } },
          grid: { color: "#e2e8f0" },
        },
        x: { ticks: { font: { size: 11 } }, grid: { display: false } },
      },
    },
  });
}

function renderAllocation(realised) {
  const labels = Object.keys(realised);
  const values = labels.map(k => realised[k]);

  const legend = document.getElementById("alloc-legend");
  legend.innerHTML = "";
  labels.forEach((label, i) => {
    const row = document.createElement("div");
    row.className = "legend-row";
    row.innerHTML = `
      <span class="legend-swatch" style="background:${PALETTE[i % PALETTE.length]}"></span>
      <span class="legend-label">${label}</span>
      <span class="legend-value">${fmtWholePct(realised[label])}</span>
    `;
    legend.appendChild(row);
  });

  if (typeof Chart === "undefined") return;
  const ctx = document.getElementById("alloc-chart").getContext("2d");
  if (allocChart) allocChart.destroy();
  allocChart = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: labels.map((_, i) => PALETTE[i % PALETTE.length]),
        borderWidth: 1,
        borderColor: "#fff",
      }],
    },
    options: {
      responsive: false,
      cutout: "60%",
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: { label: (ctx) => `${ctx.label}: ${fmtWholePct(ctx.parsed)}` },
        },
      },
    },
  });
}

function renderHoldings(holdings) {
  const tbody = document.querySelector("#holdings-table tbody");
  tbody.innerHTML = "";
  for (const h of holdings) {
    const tr = document.createElement("tr");
    tr.dataset.ticker = h.ticker;
    tr.innerHTML = `
      <td class="ticker-cell">${h.ticker}</td>
      <td>${h.name || ""}</td>
      <td>${h.asset_class}</td>
      <td class="num">${fmtWholePct(h.weight)}</td>
      <td class="num">${fmtAud(h.dollars)}</td>
      <td class="num">${fmtSharpe(h.sharpe_used)}</td>
      <td class="num">${fmtPct(h.return_1y)}</td>
      <td class="num">${fmtPct(h.return_5y)}</td>
      <td class="num">${fmtPct(h.dividend_yield_ttm)}</td>
    `;
    tr.addEventListener("click", () => toggleDetail(tr, h));
    tbody.appendChild(tr);
  }
}

function toggleDetail(tr, h) {
  const next = tr.nextElementSibling;
  if (next && next.classList.contains("detail-row") && next.dataset.parent === h.ticker) {
    next.remove();
    return;
  }
  // Close any other open detail rows first.
  document.querySelectorAll("tr.detail-row").forEach(r => r.remove());

  const detail = document.createElement("tr");
  detail.className = "detail-row";
  detail.dataset.parent = h.ticker;
  detail.innerHTML = `
    <td colspan="9">
      <div class="detail-grid">
        <div class="detail-cell"><strong>1y return</strong>${fmtPct(h.return_1y)}</div>
        <div class="detail-cell"><strong>3y return</strong>${fmtPct(h.return_3y)}</div>
        <div class="detail-cell"><strong>5y return</strong>${fmtPct(h.return_5y)}</div>
        <div class="detail-cell"><strong>1y volatility</strong>${fmtPct(h.volatility_1y)}</div>
        <div class="detail-cell"><strong>5y max drawdown</strong>${fmtPct(h.max_drawdown_5y)}</div>
        <div class="detail-cell"><strong>Sharpe</strong>${fmtSharpe(h.sharpe_used)}</div>
        <div class="detail-cell"><strong>Yield (TTM)</strong>${fmtPct(h.dividend_yield_ttm)}</div>
        <div class="detail-cell"><strong>Sleeve</strong>${h.asset_class}</div>
      </div>
      <div class="rationale-row">${h.rationale}</div>
    </td>
  `;
  tr.parentNode.insertBefore(detail, tr.nextSibling);
}

function renderNotes(notes) {
  const card = document.getElementById("notes-card");
  const list = document.getElementById("notes-list");
  list.innerHTML = "";
  if (notes && notes.length) {
    for (const n of notes) {
      const li = document.createElement("li");
      li.textContent = n;
      list.appendChild(li);
    }
    card.hidden = false;
  } else {
    card.hidden = true;
  }
}
