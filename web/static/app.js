/* ASX Portfolio Analyser - frontend logic v0.5
 * Single file, no framework. Form -> POST /api/portfolio -> render.
 * Adds: tooltips, quick-chips, recent searches, share via URL,
 *       print, sparklines, risk meter, benchmark overlay, mobile cards.
 */

const API_BASE = "";

// Asset class -> CSS variable mapping (must match :root in style.css).
const AC_COLOR = {
  "AU stocks":        "#1e3a8a",
  "AU equity":        "#3b82f6",
  "Global equity":    "#0ea5e9",
  "US equity":        "#06b6d4",
  "EM equity":        "#0d9488",
  "Thematic":         "#9333ea",
  "AU bonds":         "#65a30d",
  "Global bonds":     "#84cc16",
  "Cash":             "#6b7280",
  "AU property":      "#f59e0b",
  "Global property":  "#ea580c",
  "Commodities":      "#dc2626",
};
const FALLBACK = "#94a3b8";
const colorFor = ac => AC_COLOR[ac] || FALLBACK;

const ADVANCED_FIELDS = new Set([
  "geo_tilt", "min_history_years", "prefer_income", "prefer_hedged",
  "esg_only", "etfs_only", "min_dividend_yield", "max_volatility",
  "max_position_size", "exclude_sectors", "include_only_sectors",
  "preferred_themes", "exclude_tickers",
]);

const RECENT_KEY = "asx_recent_searches_v1";
let allocChart = null, projChart = null;
let lastResultData = null;  // for print/share

// --------------- Helpers ---------------

function fmtPct(x, digits = 1) {
  if (x === null || x === undefined || Number.isNaN(x)) return "–";
  return (x * 100).toFixed(digits) + "%";
}
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
function fmtDate(iso) {
  if (!iso) return "unknown";
  try {
    const d = new Date(iso);
    return d.toLocaleDateString("en-AU", { day: "numeric", month: "short", year: "numeric" });
  } catch (e) { return iso; }
}

// --------------- Bootstrapping ---------------

document.addEventListener("DOMContentLoaded", async () => {
  await Promise.all([loadSectors(), loadThemes(), loadDataInfo(), loadMacro()]);
  document.getElementById("profile-form").addEventListener("submit", onSubmit);
  document.getElementById("describe-btn").addEventListener("click", onAutoFill);
  document.getElementById("reset-btn").addEventListener("click", onReset);
  document.getElementById("share-btn").addEventListener("click", onShare);
  document.getElementById("print-btn").addEventListener("click", () => window.print());
  document.querySelectorAll(".chip").forEach(c => c.addEventListener("click", () => onChip(c)));
  initTooltips();
  loadFromUrlParams();
  renderRecentSearches();
  await checkAiAvailable();
  // Cmd/Ctrl+Enter submits.
  document.getElementById("describe-input").addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") onAutoFill();
  });
});

// --------------- Tooltips ---------------

function initTooltips() {
  const tip = document.getElementById("tooltip");
  document.body.addEventListener("mouseover", (e) => {
    const el = e.target.closest("[data-tip]");
    if (!el) return;
    tip.textContent = el.dataset.tip;
    tip.classList.add("visible");
    tip.setAttribute("aria-hidden", "false");
    positionTip(el, tip);
  });
  document.body.addEventListener("mouseout", (e) => {
    if (!e.target.closest("[data-tip]")) return;
    tip.classList.remove("visible");
    tip.setAttribute("aria-hidden", "true");
  });
  // Keyboard: focus shows tooltip too.
  document.body.addEventListener("focusin", (e) => {
    if (!e.target.dataset || !e.target.dataset.tip) return;
    tip.textContent = e.target.dataset.tip;
    tip.classList.add("visible");
    positionTip(e.target, tip);
  });
  document.body.addEventListener("focusout", () => tip.classList.remove("visible"));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") tip.classList.remove("visible"); });
}
function positionTip(el, tip) {
  const r = el.getBoundingClientRect();
  tip.style.left = Math.max(8, r.left) + "px";
  tip.style.top = (r.top - tip.offsetHeight - 8) + "px";
}

// --------------- Header data pill ---------------

async function loadDataInfo() {
  const pill = document.getElementById("data-pill");
  try {
    const r = await fetch(API_BASE + "/api/data_info");
    if (!r.ok) throw new Error("HTTP " + r.status);
    const d = await r.json();
    pill.textContent = `${d.instruments_total} instruments · last refreshed ${fmtDate(d.last_refreshed)}`;
  } catch (e) {
    pill.textContent = "Dataset info unavailable";
  }
}

async function loadMacro() {
  const strip = document.getElementById("macro-strip");
  if (!strip) return;
  try {
    const r = await fetch(API_BASE + "/api/macro");
    if (!r.ok) return;
    const d = await r.json();
    const cards = [];
    if (d.rba_cash_rate)  cards.push({label: "RBA cash", value: (d.rba_cash_rate.value*100).toFixed(2)+"%"});
    if (d.audusd)         cards.push({label: "AUD/USD", value: d.audusd.value.toFixed(4)});
    if (d.asx200)         cards.push({label: "ASX 200", value: Math.round(d.asx200.value).toLocaleString()});
    if (d.us10y)          cards.push({label: "US 10y", value: (d.us10y.value*100).toFixed(2)+"%"});
    if (d.vix)            cards.push({label: "VIX", value: d.vix.value.toFixed(1)});
    if (d.gold_usd)       cards.push({label: "Gold $/oz", value: "$" + Math.round(d.gold_usd.value).toLocaleString()});
    strip.innerHTML = cards.map(c =>
      `<span class="macro-pill"><span class="macro-key">${c.label}</span> <strong>${c.value}</strong></span>`
    ).join("");
  } catch (e) { /* macro is optional */ }
}

async function checkAiAvailable() {
  try {
    const r = await fetch(API_BASE + "/api/health");
    const data = await r.json();
    if (!data.ai_available) {
      const card = document.getElementById("describe-card");
      card.querySelector(".card-lede").textContent =
        "AI features are unavailable (ANTHROPIC_API_KEY not set on the server).";
      document.getElementById("describe-input").disabled = true;
      document.getElementById("describe-btn").disabled = true;
      document.querySelectorAll(".chip").forEach(c => c.disabled = true);
    }
  } catch (e) { /* server might be unreachable; ignore */ }
}

// --------------- Sectors / themes / chips ---------------

async function loadSectors() {
  const excList = document.getElementById("sector-list");
  const incList = document.getElementById("sector-include-list");
  try {
    const r = await fetch(API_BASE + "/api/sectors");
    if (!r.ok) throw new Error("HTTP " + r.status);
    const data = await r.json();
    excList.innerHTML = ""; incList.innerHTML = "";
    for (const sector of data.sectors) {
      const safe = sector.replace(/\W+/g, "-").toLowerCase();
      const exc = document.createElement("label");
      exc.innerHTML = `<input type="checkbox" name="exclude_sectors" value="${sector}" id="sec-exc-${safe}"><span>${sector}</span>`;
      excList.appendChild(exc);
      const inc = document.createElement("label");
      inc.innerHTML = `<input type="checkbox" name="include_only_sectors" value="${sector}" id="sec-inc-${safe}"><span>${sector}</span>`;
      incList.appendChild(inc);
    }
  } catch (e) {
    excList.innerHTML = `<span class="muted">Could not load sectors.</span>`;
    incList.innerHTML = `<span class="muted">Could not load sectors.</span>`;
  }
}

async function loadThemes() {
  const list = document.getElementById("theme-list");
  try {
    const r = await fetch(API_BASE + "/api/themes");
    if (!r.ok) throw new Error("HTTP " + r.status);
    const data = await r.json();
    list.innerHTML = "";
    for (const theme of data.themes) {
      const wrapper = document.createElement("label");
      const display = theme.replace(/_/g, " ");
      wrapper.innerHTML = `<input type="checkbox" name="preferred_themes" value="${theme}"><span>${display}</span>`;
      list.appendChild(wrapper);
    }
  } catch (e) {
    list.innerHTML = `<span class="muted">Could not load themes.</span>`;
  }
}

function onChip(c) {
  const text = c.dataset.example;
  document.getElementById("describe-input").value = text;
  onAutoFill();
}

// --------------- Reset / share / URL params ---------------

function onReset() {
  const form = document.getElementById("profile-form");
  form.reset();
  form.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
  document.getElementById("describe-input").value = "";
  document.getElementById("status-msg").textContent = "Reset to defaults.";
  document.getElementById("status-msg").style.color = "var(--text-muted)";
  document.getElementById("result-content").hidden = true;
  document.getElementById("empty-state").hidden = false;
  history.replaceState(null, "", location.pathname);
}

function buildUrlParams() {
  const data = collectFormData();
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(data)) {
    if (Array.isArray(v)) { if (v.length) u.set(k, v.join(",")); }
    else if (typeof v === "boolean") { if (v) u.set(k, "1"); }
    else if (v !== null && v !== "" && v !== undefined) u.set(k, String(v));
  }
  return u.toString();
}

async function onShare() {
  const params = buildUrlParams();
  const url = `${location.origin}${location.pathname}?${params}`;
  try {
    await navigator.clipboard.writeText(url);
    const s = document.getElementById("status-msg");
    s.textContent = "Shareable URL copied to clipboard.";
    s.style.color = "var(--success)";
  } catch (e) {
    prompt("Copy this URL:", url);
  }
}

function loadFromUrlParams() {
  if (!location.search) return;
  const u = new URLSearchParams(location.search);
  const form = document.getElementById("profile-form");
  let touched = false;

  function setIf(name, transform = v => v) {
    if (!u.has(name)) return;
    const el = form[name];
    if (el) { el.value = transform(u.get(name)); touched = true; }
  }
  for (const k of ["capital", "risk_profile", "horizon_years", "max_holdings",
                   "geo_tilt", "min_history_years", "max_position_size"]) setIf(k);
  for (const k of ["prefer_income", "prefer_hedged", "esg_only", "etfs_only"]) {
    if (u.has(k) && form[k]) { form[k].checked = true; touched = true; }
  }
  if (u.has("min_dividend_yield") && form.min_dividend_yield_pct) {
    form.min_dividend_yield_pct.value = (parseFloat(u.get("min_dividend_yield")) * 100).toFixed(1);
    touched = true;
  }
  if (u.has("max_volatility") && form.max_volatility_pct) {
    form.max_volatility_pct.value = (parseFloat(u.get("max_volatility")) * 100).toFixed(0);
    touched = true;
  }
  for (const arrField of ["exclude_sectors", "include_only_sectors", "preferred_themes"]) {
    if (u.has(arrField)) {
      const wanted = new Set(u.get(arrField).split(",").filter(Boolean));
      form.querySelectorAll(`input[name="${arrField}"]`).forEach(cb => {
        cb.checked = wanted.has(cb.value);
      });
      if (wanted.size) touched = true;
    }
  }
  if (u.has("exclude_tickers") && form.exclude_tickers) {
    form.exclude_tickers.value = u.get("exclude_tickers").split(",").join(", ");
    touched = true;
  }
  if (touched) {
    document.querySelector("details.advanced").open = true;
    // Auto-submit to render the result.
    setTimeout(() => form.requestSubmit(), 200);
  }
}

// --------------- Recent searches ---------------

function pushRecent(label, params) {
  let list = [];
  try { list = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]"); } catch (e) {}
  list = [{ label, params, at: Date.now() }, ...list.filter(r => r.params !== params)].slice(0, 5);
  localStorage.setItem(RECENT_KEY, JSON.stringify(list));
  renderRecentSearches();
}
function renderRecentSearches() {
  const card = document.getElementById("recent-card");
  const ul = document.getElementById("recent-list");
  let list = [];
  try { list = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]"); } catch (e) {}
  if (!list.length) { card.hidden = true; return; }
  card.hidden = false;
  ul.innerHTML = "";
  for (const r of list) {
    const li = document.createElement("li");
    li.textContent = r.label;
    li.addEventListener("click", () => { location.search = r.params; });
    ul.appendChild(li);
  }
}

// --------------- AI: natural-language auto-fill ---------------

async function onAutoFill() {
  const input = document.getElementById("describe-input");
  const btn = document.getElementById("describe-btn");
  const status = document.getElementById("describe-status");
  const text = (input.value || "").trim();
  if (!text) {
    status.textContent = "Type something first."; status.style.color = "var(--danger)"; return;
  }
  btn.disabled = true;
  status.textContent = "Asking Claude..."; status.style.color = "var(--text-muted)";
  try {
    const r = await fetch(API_BASE + "/api/parse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description: text }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || ("HTTP " + r.status));
    }
    const data = await r.json();
    const fields = data.fields || {};
    const filled = applyFieldsToForm(fields);
    if (filled.length === 0) {
      status.textContent = "No fields detected. Try being more specific.";
      status.style.color = "var(--danger)";
    } else {
      status.textContent = "Filled: " + filled.join(", ");
      status.style.color = "var(--success)";
      if (filled.some(f => ADVANCED_FIELDS.has(f))) {
        document.querySelector("details.advanced").open = true;
      }
    }
  } catch (e) {
    status.textContent = "Error: " + e.message;
    status.style.color = "var(--danger)";
  } finally {
    btn.disabled = false;
  }
}

function applyFieldsToForm(fields) {
  const form = document.getElementById("profile-form");
  const filled = [];
  function setIf(name, transform = v => v) {
    if (fields[name] === undefined || fields[name] === null) return;
    const el = form[name];
    if (el) { el.value = transform(fields[name]); filled.push(name); }
  }
  setIf("capital");
  setIf("risk_profile");
  setIf("horizon_years");
  setIf("max_holdings");
  setIf("geo_tilt");
  setIf("min_history_years");
  setIf("max_position_size");
  for (const k of ["prefer_income", "prefer_hedged", "esg_only", "etfs_only"]) {
    if (fields[k] !== undefined && form[k]) { form[k].checked = !!fields[k]; filled.push(k); }
  }
  if (fields.min_dividend_yield !== undefined && form.min_dividend_yield_pct) {
    form.min_dividend_yield_pct.value = (fields.min_dividend_yield * 100).toFixed(1);
    filled.push("min_dividend_yield");
  }
  if (fields.max_volatility !== undefined && form.max_volatility_pct) {
    form.max_volatility_pct.value = (fields.max_volatility * 100).toFixed(0);
    filled.push("max_volatility");
  }
  for (const arrField of ["exclude_sectors", "include_only_sectors", "preferred_themes"]) {
    if (Array.isArray(fields[arrField]) && fields[arrField].length) {
      form.querySelectorAll(`input[name="${arrField}"]`).forEach(cb => {
        cb.checked = fields[arrField].includes(cb.value);
      });
      filled.push(arrField);
    }
  }
  if (Array.isArray(fields.exclude_tickers) && fields.exclude_tickers.length && form.exclude_tickers) {
    form.exclude_tickers.value = fields.exclude_tickers.join(", ");
    filled.push("exclude_tickers");
  }
  return filled;
}

// --------------- Form submission ---------------

function collectFormData() {
  const form = document.getElementById("profile-form");
  const minYieldPct = parseFloat(form.min_dividend_yield_pct.value || "0");
  const maxVolPctRaw = form.max_volatility_pct.value;
  const maxVol = maxVolPctRaw === "" ? null : parseFloat(maxVolPctRaw) / 100;
  const rawTickers = (form.exclude_tickers && form.exclude_tickers.value) || "";
  const excludeTickers = rawTickers.split(/[,\s]+/).map(s => s.trim().toUpperCase())
    .filter(Boolean).map(s => s.endsWith(".AX") ? s : s + ".AX");
  return {
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
    min_history_years: parseInt(form.min_history_years.value, 10),
    max_holdings: parseInt(form.max_holdings.value, 10),
    max_position_size: parseFloat(form.max_position_size.value),
    exclude_sectors: Array.from(form.querySelectorAll('input[name="exclude_sectors"]:checked')).map(c => c.value),
    include_only_sectors: Array.from(form.querySelectorAll('input[name="include_only_sectors"]:checked')).map(c => c.value),
    preferred_themes: Array.from(form.querySelectorAll('input[name="preferred_themes"]:checked')).map(c => c.value),
    exclude_tickers: excludeTickers,
  };
}

async function onSubmit(ev) {
  ev.preventDefault();
  const status = document.getElementById("status-msg");
  const btn = document.getElementById("submit-btn");
  status.textContent = "Building portfolio..."; status.style.color = "var(--text-muted)";
  btn.disabled = true;
  const data = collectFormData();
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
    lastResultData = { profile: data, result };
    renderResult(result, data);
    status.textContent = "Done."; status.style.color = "var(--success)";
    pushRecent(`$${Math.round(data.capital/1000)}k · ${data.risk_profile} · ${data.horizon_years}y`,
               buildUrlParams());
    history.replaceState(null, "", location.pathname + "?" + buildUrlParams());
  } catch (e) {
    status.textContent = "Error: " + e.message;
    status.style.color = "var(--danger)";
  } finally {
    btn.disabled = false;
  }
}

// --------------- Rendering ---------------

function renderResult(r, profile) {
  document.getElementById("empty-state").hidden = true;
  document.getElementById("result-content").hidden = false;

  document.getElementById("m-return").textContent = fmtPct(r.expected_return);
  document.getElementById("m-vol").textContent = fmtPct(r.expected_volatility);
  document.getElementById("m-dd").textContent = fmtPct(r.expected_max_drawdown);
  document.getElementById("m-yield").textContent = fmtPct(r.expected_dividend_yield);

  document.getElementById("screen-meta").textContent =
    `${r.holdings.length} holdings selected · ${profile.min_history_years}y minimum history filter`;

  renderRiskMeter(r.expected_volatility);
  renderProjection(r.projection, r.capital, profile.horizon_years);
  renderAllocation(r.realised_allocation);
  renderHoldings(r.holdings);
  renderHoldingsMobile(r.holdings);
  renderNotes(r.notes);

  document.getElementById("result-content").scrollIntoView({ behavior: "smooth", block: "start" });
  requestExplanation(r, profile);
}

function renderRiskMeter(vol) {
  const fill = document.getElementById("risk-fill");
  if (vol === null || vol === undefined) { fill.style.left = "0%"; return; }
  // Map 0..0.40 -> 0..100% (clamp).
  const pct = Math.max(0, Math.min(1, vol / 0.40)) * 100;
  fill.style.left = pct + "%";
}

async function renderProjection(proj, capital, horizon) {
  const card = document.getElementById("projection-card");
  if (!proj) { card.hidden = true; return; }
  card.hidden = false;

  document.getElementById("p-low").textContent = fmtAud(proj.low);
  document.getElementById("p-median").textContent = fmtAud(proj.median);
  document.getElementById("p-high").textContent = fmtAud(proj.high);
  document.getElementById("p-median-cagr").textContent =
    `${fmtPct(proj.median_return_pct)} per year over ${proj.horizon_years}y`;

  // Pull benchmark in parallel.
  let benchmark = null;
  try {
    const r = await fetch(`${API_BASE}/api/benchmark_projection?horizon_years=${horizon}&capital=${capital}`);
    if (r.ok) {
      const b = await r.json();
      if (b.available) benchmark = b;
    }
  } catch (e) { /* ignore */ }

  if (typeof Chart === "undefined") return;

  const years = proj.horizon_years;
  const points = [];
  for (let t = 0; t <= years; t++) {
    const f = t / years;
    points.push({
      t,
      low: capital * Math.pow(proj.low / capital, f),
      median: capital * Math.pow(proj.median / capital, f),
      high: capital * Math.pow(proj.high / capital, f),
      benchmark: benchmark ? capital * Math.pow(benchmark.median / capital, f) : null,
    });
  }

  const datasets = [
    { label: "Optimistic (P90)", data: points.map(p => p.high),
      borderColor: "#1e3a8a40", backgroundColor: "#1e3a8a20",
      fill: "+1", pointRadius: 0, borderWidth: 1 },
    { label: "Median",          data: points.map(p => p.median),
      borderColor: "#1e3a8a", backgroundColor: "#1e3a8a",
      fill: false, pointRadius: 0, borderWidth: 2 },
    { label: "Pessimistic (P10)", data: points.map(p => p.low),
      borderColor: "#1e3a8a40", backgroundColor: "#1e3a8a20",
      fill: false, pointRadius: 0, borderWidth: 1 },
  ];
  if (benchmark) {
    datasets.push({
      label: "ASX 200 baseline",
      data: points.map(p => p.benchmark),
      borderColor: "#dc2626", backgroundColor: "#dc262620",
      fill: false, pointRadius: 0, borderWidth: 1.5, borderDash: [6, 4],
    });
  }

  const ctx = document.getElementById("proj-chart").getContext("2d");
  if (projChart) projChart.destroy();
  projChart = new Chart(ctx, {
    type: "line",
    data: { labels: points.map(p => `Year ${p.t}`), datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position: "bottom", labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: ${fmtAud(ctx.parsed.y, 0)}` } },
      },
      scales: {
        y: { ticks: { callback: v => fmtAud(v, 0), font: { size: 11 } }, grid: { color: "#e2e8f0" } },
        x: { ticks: { font: { size: 11 } }, grid: { display: false } },
      },
    },
  });
}

function renderAllocation(realised) {
  const labels = Object.keys(realised);
  const values = labels.map(k => realised[k]);
  const colors = labels.map(colorFor);

  const legend = document.getElementById("alloc-legend");
  legend.innerHTML = "";
  labels.forEach((label, i) => {
    const row = document.createElement("div");
    row.className = "legend-row";
    row.innerHTML = `
      <span class="legend-swatch" style="background:${colors[i]}"></span>
      <span class="legend-label">${label}</span>
      <span class="legend-value">${fmtWholePct(realised[label])}</span>`;
    legend.appendChild(row);
  });

  if (typeof Chart === "undefined") return;
  const ctx = document.getElementById("alloc-chart").getContext("2d");
  if (allocChart) allocChart.destroy();
  allocChart = new Chart(ctx, {
    type: "doughnut",
    data: { labels, datasets: [{ data: values, backgroundColor: colors, borderWidth: 1, borderColor: "#fff" }] },
    options: {
      responsive: false, cutout: "60%",
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: ctx => `${ctx.label}: ${fmtWholePct(ctx.parsed)}` } },
      },
    },
  });
}

function renderHoldings(holdings) {
  const tbody = document.querySelector("#holdings-table tbody");
  tbody.innerHTML = "";
  for (const h of holdings) {
    const color = colorFor(h.asset_class);
    const tr = document.createElement("tr");
    tr.dataset.ticker = h.ticker;
    const cellId = `spark-${h.ticker.replace(/\W/g, "")}`;
    tr.innerHTML = `
      <td class="ticker-cell">${h.ticker}</td>
      <td>${h.name || ""}</td>
      <td><span class="asset-class-pill" style="background:${color}">${h.asset_class}</span></td>
      <td class="num">${fmtWholePct(h.weight)}</td>
      <td class="num">${fmtAud(h.dollars)}</td>
      <td class="num">${fmtSharpe(h.sharpe_used)}</td>
      <td class="num">${fmtPct(h.return_1y)}</td>
      <td class="num">${fmtPct(h.return_5y)}</td>
      <td class="num">${fmtPct(h.dividend_yield_ttm)}</td>
      <td class="sparkline-cell"><canvas id="${cellId}"></canvas></td>`;
    tr.addEventListener("click", () => toggleDetail(tr, h));
    tbody.appendChild(tr);
    fetchAndDrawSparkline(h.ticker, cellId, color);
  }
}

function renderHoldingsMobile(holdings) {
  const c = document.getElementById("holdings-mobile");
  c.innerHTML = "";
  for (const h of holdings) {
    const color = colorFor(h.asset_class);
    const card = document.createElement("div");
    card.className = "holding-card";
    card.innerHTML = `
      <div class="holding-card-header">
        <span class="holding-card-ticker">${h.ticker}</span>
        <span class="holding-card-weight">${fmtWholePct(h.weight)}</span>
      </div>
      <div class="holding-card-name">${h.name || ""}</div>
      <div class="holding-card-meta">
        <span><span class="asset-class-pill" style="background:${color}">${h.asset_class}</span></span>
        <span><strong>${fmtAud(h.dollars)}</strong> capital</span>
        <span><strong>${fmtPct(h.return_5y)}</strong> 5y CAGR</span>
        <span><strong>${fmtPct(h.dividend_yield_ttm)}</strong> yield</span>
        <span><strong>${fmtSharpe(h.sharpe_used)}</strong> Sharpe</span>
      </div>`;
    c.appendChild(card);
  }
}

function toggleDetail(tr, h) {
  const next = tr.nextElementSibling;
  if (next && next.classList.contains("detail-row") && next.dataset.parent === h.ticker) {
    next.remove(); return;
  }
  document.querySelectorAll("tr.detail-row").forEach(r => r.remove());
  const detail = document.createElement("tr");
  detail.className = "detail-row"; detail.dataset.parent = h.ticker;
  detail.innerHTML = `
    <td colspan="10">
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
      <div class="fundamentals-row" data-ticker="${h.ticker}"><span class="muted">Loading fundamentals&hellip;</span></div>
    </td>`;
  tr.parentNode.insertBefore(detail, tr.nextSibling);
  fetchAndRenderFundamentals(h.ticker, detail.querySelector(".fundamentals-row"));
}

async function fetchAndRenderFundamentals(ticker, target) {
  if (!target) return;
  try {
    const r = await fetch(API_BASE + "/api/fundamentals/" + encodeURIComponent(ticker));
    if (!r.ok) throw new Error("HTTP " + r.status);
    const d = await r.json();
    if (!d.available) {
      target.innerHTML = `<span class="muted">No fundamentals available for this instrument.</span>`;
      return;
    }
    const cells = [
      { label: "Trailing P/E",  value: d.trailing_pe != null ? d.trailing_pe.toFixed(1) : "–" },
      { label: "Forward P/E",   value: d.forward_pe != null ? d.forward_pe.toFixed(1) : "–" },
      { label: "Price / Book",  value: d.price_to_book != null ? d.price_to_book.toFixed(2) : "–" },
      { label: "Return on Equity", value: d.return_on_equity != null ? (d.return_on_equity * 100).toFixed(1)+"%" : "–" },
      { label: "Profit margin", value: d.profit_margin != null ? (d.profit_margin * 100).toFixed(1)+"%" : "–" },
      { label: "Debt / Equity", value: d.debt_to_equity != null ? d.debt_to_equity.toFixed(2) : "–" },
      { label: "Forward yield", value: d.forward_dividend_yield != null ? (d.forward_dividend_yield * 100).toFixed(2)+"%" : "–" },
      { label: "Payout ratio",  value: d.payout_ratio != null ? (d.payout_ratio * 100).toFixed(0)+"%" : "–" },
    ];
    target.innerHTML = cells.map(c => `
      <div class="fund-cell"><span class="fund-label">${c.label}</span><strong>${c.value}</strong></div>
    `).join("");
  } catch (e) {
    target.innerHTML = `<span class="muted">Fundamentals unavailable.</span>`;
  }
}

async function fetchAndDrawSparkline(ticker, canvasId, color) {
  try {
    const r = await fetch(API_BASE + "/api/sparkline/" + encodeURIComponent(ticker));
    if (!r.ok) return;
    const d = await r.json();
    const values = d.values || [];
    if (values.length < 2) return;
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 80, h = 24;
    canvas.width = w * dpr; canvas.height = h * dpr;
    const ctx = canvas.getContext("2d"); ctx.scale(dpr, dpr);
    const min = Math.min(...values), max = Math.max(...values);
    const range = max - min || 1;
    ctx.beginPath();
    values.forEach((v, i) => {
      const x = (i / (values.length - 1)) * w;
      const y = h - ((v - min) / range) * (h - 4) - 2;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.strokeStyle = values[values.length - 1] >= values[0] ? "#10b981" : "#dc2626";
    ctx.lineWidth = 1.5; ctx.stroke();
  } catch (e) { /* sparkline failure is silent */ }
}

function renderNotes(notes) {
  const card = document.getElementById("notes-card");
  const list = document.getElementById("notes-list");
  list.innerHTML = "";
  if (notes && notes.length) {
    for (const n of notes) {
      const li = document.createElement("li"); li.textContent = n; list.appendChild(li);
    }
    card.hidden = false;
  } else { card.hidden = true; }
}

async function requestExplanation(result, profile) {
  const card = document.getElementById("explain-card");
  const body = document.getElementById("explain-body");
  card.hidden = false;
  body.textContent = "Generating...";
  try {
    const r = await fetch(API_BASE + "/api/explain", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile, result }),
    });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const data = await r.json();
    if (!data.ai_available) { card.hidden = true; return; }
    if (!data.text) { body.textContent = "Explanation could not be generated."; return; }
    body.innerHTML = "";
    const paras = data.text.split(/\n\s*\n/).map(s => s.trim()).filter(Boolean);
    for (const p of paras) {
      const el = document.createElement("p"); el.textContent = p; body.appendChild(el);
    }
  } catch (e) {
    body.textContent = "Could not generate explanation: " + e.message;
  }
}
