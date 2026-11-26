/* Mexico macro dashboard front end.
 * Reads the committed CSVs and metadata.json and draws them with Plotly.
 * Every transformation is computed by scripts/fetch_data.py; this file only plots.
 */
(function () {
  "use strict";

  const DATA = "data/";
  const state = { meta: null, data: {}, charts: [], range: "all", gdpView: "gdp" };

  // ---------- helpers ----------
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const C = () => ({
    mx: css("--mx"), mxSoft: css("--mx-soft"), us: css("--us"), usSoft: css("--us-soft"),
    ref: css("--ref"), band: css("--band"), text: css("--text-primary"), text2: css("--text-secondary"),
    grid: css("--grid"), axis: css("--axis"), surface: css("--surface-1"),
  });

  function parseCSV(text) {
    const rows = [];
    let field = "", row = [], q = false;
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (q) {
        if (ch === '"' && text[i + 1] === '"') { field += '"'; i++; }
        else if (ch === '"') q = false;
        else field += ch;
      } else if (ch === '"') q = true;
      else if (ch === ",") { row.push(field); field = ""; }
      else if (ch === "\n" || ch === "\r") {
        if (ch === "\r" && text[i + 1] === "\n") i++;
        row.push(field); field = "";
        if (row.length > 1 || row[0] !== "") rows.push(row);
        row = [];
      } else field += ch;
    }
    if (field !== "" || row.length) { row.push(field); rows.push(row); }
    const head = rows.shift() || [];
    const cols = {};
    head.forEach((h) => (cols[h] = []));
    rows.forEach((r) => head.forEach((h, j) => {
      const v = r[j] === undefined ? "" : r[j];
      cols[h].push(h === "date" || h.endsWith("_date") || h === "note" ? v : (v === "" ? null : Number(v)));
    }));
    return cols;
  }

  async function loadCSV(key) {
    try {
      const r = await fetch(DATA + key + ".csv", { cache: "no-cache" });
      if (!r.ok) return null;
      const cols = parseCSV(await r.text());
      return clip(cols);
    } catch (e) { return null; }
  }

  // Keep the dashboard sample (Jan 2015 onward); the CSVs carry one earlier year for lags.
  function clip(cols) {
    const start = (state.meta && state.meta.display_start) || "2015-01-01";
    const keep = cols.date.map((d) => d >= start);
    const out = {};
    Object.keys(cols).forEach((k) => (out[k] = cols[k].filter((_, i) => keep[i])));
    return out;
  }

  const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "–" :
    v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const signed = (v, d = 2) => {
    if (v === null || v === undefined || Number.isNaN(v)) return "–";
    const r = Number(v.toFixed(d));
    return (r > 0 ? "+" : r < 0 ? "−" : "±") + fmt(Math.abs(r), d);
  };
  const quarterLabel = (iso) => { const [y, m] = iso.split("-").map(Number); return `${y} Q${Math.floor((m - 1) / 3) + 1}`; };
  const monthLabel = (iso) => new Date(iso + "T00:00:00Z").toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });

  function lastValid(x, y) {
    for (let i = y.length - 1; i >= 0; i--) if (y[i] !== null && !Number.isNaN(y[i])) return i;
    return -1;
  }
  function prevValid(y, from) {
    for (let i = from - 1; i >= 0; i--) if (y[i] !== null && !Number.isNaN(y[i])) return i;
    return -1;
  }
  function escapeHTML(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // ---------- layout ----------
  function baseLayout(extra) {
    const c = C();
    const axis = {
      type: "date", gridcolor: c.grid, linecolor: c.axis, zeroline: false, tickfont: { size: 11, color: c.text2 },
      showspikes: true, spikemode: "across", spikethickness: 1, spikecolor: c.axis, spikedash: "dot",
    };
    return Object.assign({
      margin: { l: 52, r: 14, t: 8, b: 34 },
      paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
      font: { family: css("--font") || "sans-serif", color: c.text, size: 12 },
      hovermode: "x unified",
      hoverlabel: { bgcolor: c.surface, bordercolor: c.axis, font: { color: c.text, size: 12 } },
      legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", font: { size: 11, color: c.text2 }, bgcolor: "rgba(0,0,0,0)" },
      xaxis: axis,
      yaxis: { gridcolor: c.grid, linecolor: c.axis, zeroline: false, tickfont: { size: 11, color: c.text2 }, title: { font: { size: 11, color: c.text2 } } },
      shapes: [], annotations: [],
    }, extra || {});
  }
  // Two stacked panels sharing the date axis (top: levels, bottom: spread).
  function twoPanelLayout() {
    const b = baseLayout();
    return baseLayout({
      xaxis: Object.assign(b.xaxis, { anchor: "y", showticklabels: false }),
      yaxis: Object.assign(b.yaxis, { domain: [0.36, 1] }),
      xaxis2: Object.assign(baseLayout().xaxis, { anchor: "y2", matches: "x" }),
      yaxis2: Object.assign(baseLayout().yaxis, { domain: [0, 0.26] }),
    });
  }
  const CONFIG = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d"] };

  function ytitle(layout, axis, text) { layout[axis].title = Object.assign({}, layout[axis].title, { text }); }

  function line(name, x, y, color, opts) {
    const o = opts || {};
    return Object.assign({
      type: "scatter", mode: "lines", name, x, y, connectgaps: false,
      line: { color, width: 2, shape: o.shape || "linear", dash: o.dash || "solid" },
      hovertemplate: `%{y:.${o.dec ?? 2}f} ${o.unit || ""}<extra>${name}</extra>`,
    }, o.extra || {});
  }

  function draw(id, traces, layout, meta) {
    const el = document.getElementById(id);
    if (!traces.length || traces.every((t) => !t.x || !t.x.length)) {
      el.innerHTML = '<div class="empty">No data yet. It appears after the first successful refresh.</div>';
      return;
    }
    Plotly.react(el, traces, layout, CONFIG);
    const entry = state.charts.find((c) => c.id === id);
    const rec = Object.assign({ id, el }, meta || {});
    if (entry) Object.assign(entry, rec); else state.charts.push(rec);
    applyRange(rec);
  }

  // ---------- range buttons ----------
  function rangeStart(years, lastDate) {
    if (years === "all") return (state.meta && state.meta.display_start) || "2015-01-01";
    const d = new Date(lastDate + "T00:00:00Z");
    d.setUTCFullYear(d.getUTCFullYear() - Number(years));
    return d.toISOString().slice(0, 10);
  }

  function applyRange(chart) {
    const el = chart.el;
    if (!el || !el.data) return;
    let last = "0000";
    el.data.forEach((t) => (t.x || []).forEach((x) => { if (x > last) last = x; }));
    const start = rangeStart(state.range, last);
    const pad = state.range === "all" ? 20 : 10;
    const end = new Date(new Date(last + "T00:00:00Z").getTime() + pad * 864e5).toISOString().slice(0, 10);
    const upd = {};
    Object.keys(el.layout).filter((k) => /^xaxis\d*$/.test(k)).forEach((k) => {
      upd[k + ".range"] = [start, end];
      upd[k + ".autorange"] = false;
    });
    // Recompute each y-axis from the data visible in the window.
    const ext = {};
    el.data.forEach((t) => {
      if (t.visible === false || t.skipRange) return;
      const ax = "yaxis" + (t.yaxis && t.yaxis !== "y" ? t.yaxis.slice(1) : "");
      (t.x || []).forEach((x, i) => {
        if (x < start || x > end) return;
        const v = t.y[i];
        if (v === null || v === undefined || Number.isNaN(v)) return;
        ext[ax] = ext[ax] || [Infinity, -Infinity];
        ext[ax][0] = Math.min(ext[ax][0], v);
        ext[ax][1] = Math.max(ext[ax][1], v);
      });
    });
    (chart.yInclude || []).forEach(([ax, v]) => { if (ext[ax]) { ext[ax][0] = Math.min(ext[ax][0], v); ext[ax][1] = Math.max(ext[ax][1], v); } });
    Object.entries(ext).forEach(([ax, [lo, hi]]) => {
      const span = hi - lo || Math.abs(hi) || 1;
      upd[ax + ".range"] = [lo - span * 0.08, hi + span * 0.08];
      upd[ax + ".autorange"] = false;
    });
    Plotly.relayout(el, upd);
  }

  function setupRangeButtons() {
    document.querySelectorAll("[data-range]").forEach((b) => b.addEventListener("click", () => {
      state.range = b.dataset.range;
      document.querySelectorAll("[data-range]").forEach((o) => {
        o.classList.toggle("active", o === b);
        o.setAttribute("aria-pressed", String(o === b));
      });
      state.charts.forEach(applyRange);
    }));
  }

  // ---------- footers ----------
  function footer(id, items) {
    const el = document.getElementById(id);
    const m = state.meta || {};
    const html = items.map((it) => {
      const s = it.series ? (m.series || {})[it.series] : null;
      const d = it.derived ? (m.derived || {})[it.derived] : null;
      if (!s && !d) return "";
      if (s) {
        const kept = s.fetch_status !== "ok"
          ? ` <span class="status-kept">(not refreshed in the latest run: ${escapeHTML(s.fetch_status)})</span>` : "";
        return `<dl><dt>${escapeHTML(it.label || s.name)}${kept}</dt>
          <dd><span class="k">Units:</span> ${escapeHTML(s.units)} · <span class="k">Frequency:</span> ${escapeHTML(s.frequency)} · <span class="k">Seasonal adjustment:</span> ${escapeHTML(s.seasonal_adjustment)}</dd>
          <dd><span class="k">Source:</span> <a href="${escapeHTML(s.source_url)}" target="_blank" rel="noopener">${escapeHTML(s.source)}</a> · <span class="k">Series ID:</span> <code>${escapeHTML(s.id)}</code> · <span class="k">Latest observation:</span> <strong>${escapeHTML(s.latest_observation_date || "–")}</strong></dd>
          <dd><span class="k">Transformation:</span> ${escapeHTML(it.transformation || s.transformation)}</dd></dl>`;
      }
      return `<dl><dt>${escapeHTML(it.label || d.name)}</dt>
        <dd><span class="k">Units:</span> ${escapeHTML(d.units)} · <span class="k">Frequency:</span> ${escapeHTML(d.frequency)} · <span class="k">Computed from:</span> ${escapeHTML((d.inputs || []).join(", "))} · <span class="k">Latest observation:</span> <strong>${escapeHTML(d.latest_observation_date || "–")}</strong></dd>
        <dd><span class="k">Transformation:</span> ${escapeHTML(d.transformation)}</dd></dl>`;
    }).join("") + (items.note ? `<p>${items.note}</p>` : "");
    el.innerHTML = html;
  }

  // ---------- charts ----------
  function chartGDP() {
    const c = C();
    const L = baseLayout({ barmode: "overlay", bargap: 0.25 });
    ytitle(L, "yaxis", "%");
    let traces;
    if (state.gdpView === "gdp") {
      const g = state.data.gdp;
      if (!g) return draw("chart-gdp", [], L);
      traces = [
        { type: "bar", name: "y/y %", x: g.date, y: g.yoy, marker: { color: c.mxSoft, line: { width: 0 } },
          hovertemplate: "%{y:.2f}% y/y<extra>GDP</extra>", xperiod: "M3", xperiodalignment: "middle" },
        line("q/q %, not annualized", g.date, g.qoq, c.text, { unit: "% q/q", extra: { mode: "lines+markers", marker: { size: 5 }, xperiod: "M3", xperiodalignment: "middle" } }),
      ];
      L.xaxis.hoverformat = "%Y Q%q";
      document.getElementById("gdp-subtitle").innerHTML = "Bars: y/y %. Line: q/q %, <strong>not annualized</strong>. Quarterly, seasonally adjusted, constant 2018 prices.";
      footer("foot-gdp", [{ series: "gdp", label: "Real GDP (INEGI)" }]);
    } else {
      const g = state.data.igae;
      if (!g) return draw("chart-gdp", [], L);
      traces = [line("IGAE y/y %", g.date, g.yoy, c.mx, { unit: "% y/y" })];
      L.xaxis.hoverformat = "%b %Y";
      document.getElementById("gdp-subtitle").innerHTML = "IGAE, INEGI's monthly economic activity index, seasonally adjusted, y/y % (monthly proxy for GDP).";
      footer("foot-gdp", [{ series: "igae", label: "IGAE (INEGI)" }]);
    }
    L.shapes.push({ type: "line", xref: "paper", x0: 0, x1: 1, y0: 0, y1: 0, line: { color: c.axis, width: 1 } });
    draw("chart-gdp", traces, L, { yInclude: [["yaxis", 0]] });
  }

  function chartInflation() {
    const c = C();
    const h = state.data.inpc, k = state.data.inpc_core;
    const L = baseLayout();
    ytitle(L, "yaxis", "% y/y");
    L.xaxis.hoverformat = "%b %Y";
    L.shapes.push(
      { type: "rect", xref: "paper", x0: 0, x1: 1, y0: 2, y1: 4, fillcolor: c.band, line: { width: 0 }, layer: "below" },
      { type: "line", xref: "paper", x0: 0, x1: 1, y0: 3, y1: 3, line: { color: c.ref, width: 1.5, dash: "dash" } });
    L.annotations.push({ xref: "paper", x: 0.995, y: 3, xanchor: "right", yanchor: "bottom", text: "3% target (band 2–4%)", showarrow: false, font: { size: 11, color: c.text2 } });
    const traces = [];
    if (h) traces.push(line("Headline", h.date, h.yoy, c.mx, { unit: "% y/y" }));
    if (k) traces.push(line("Core", k.date, k.yoy, c.mx, { unit: "% y/y", dash: "dot" }));
    draw("chart-inflation", traces, L, { yInclude: [["yaxis", 2], ["yaxis", 4]] });
    footer("foot-inflation", [{ series: "inpc", label: "Headline INPC (INEGI)" }, { series: "inpc_core", label: "Core INPC (INEGI)" }]);
  }

  function chartUnemployment() {
    const c = C();
    const u = state.data.unemployment;
    const L = baseLayout();
    ytitle(L, "yaxis", "% of EAP");
    L.xaxis.hoverformat = "%b %Y";
    L.shapes.push({ type: "rect", xref: "x", yref: "paper", x0: "2020-03-16", x1: "2020-06-16", y0: 0, y1: 1, fillcolor: c.band, line: { width: 0 }, layer: "below" });
    L.annotations.push({ xref: "x", yref: "paper", x: "2020-06-20", y: 0.98, xanchor: "left", yanchor: "top", text: "ETOE gap, Apr–Jun 2020", showarrow: false, font: { size: 11, color: c.text2 } });
    const traces = u ? [line("Unemployment rate (SA)", u.date, u.value, c.mx, { unit: "%", extra: { mode: "lines+markers", marker: { size: 3 } } })] : [];
    draw("chart-unemployment", traces, L);
    footer("foot-unemployment", [{ series: "unemployment", label: "Unemployment rate, ENOE (INEGI), seasonally adjusted version" }]);
  }

  function chartPolicy() {
    const c = C();
    const bx = state.data.banxico_target, dff = state.data.dff, lo = state.data.fed_lower, hi = state.data.fed_upper, sp = state.data.policy_spread;
    const L = twoPanelLayout();
    ytitle(L, "yaxis", "% per year");
    ytitle(L, "yaxis2", "pp");
    L.xaxis.hoverformat = L.xaxis2.hoverformat = "%d %b %Y";
    const traces = [];
    if (lo && hi) {
      traces.push({ type: "scatter", mode: "lines", x: lo.date, y: lo.value, line: { width: 0, shape: "hv" }, showlegend: false, hoverinfo: "skip", name: "Fed lower" });
      traces.push({ type: "scatter", mode: "lines", x: hi.date, y: hi.value, fill: "tonexty", fillcolor: c.usSoft, line: { width: 0, shape: "hv" }, name: "Fed target range",
        customdata: lo.value, hovertemplate: "%{customdata:.2f}–%{y:.2f}%<extra>Fed target range</extra>" });
    }
    if (dff) traces.push(line("Fed effective rate (DFF)", dff.date, dff.value, c.us, { unit: "%", extra: { line: { color: c.us, width: 1.5 } } }));
    if (bx) {
      traces.push(line("Banxico target", bx.date, bx.value, c.mx, { unit: "%", shape: "hv" }));
      const dx = [], dy = [];
      bx.decision.forEach((f, i) => { if (f === 1) { dx.push(bx.date[i]); dy.push(bx.value[i]); } });
      traces.push({ type: "scatter", mode: "markers", name: "Banxico decision (new level in force)", x: dx, y: dy,
        marker: { color: c.mx, size: 8, line: { color: c.surface, width: 2 } }, hovertemplate: "new target %{y:.2f}%<extra>Decision</extra>" });
    }
    if (sp) {
      traces.push(line("Spread: Banxico − Fed upper", sp.date, sp.spread, c.ref, { unit: "pp", shape: "hv",
        extra: { xaxis: "x2", yaxis: "y2", fill: "tozeroy", fillcolor: c.band } }));
    }
    L.shapes.push({ type: "line", xref: "x2 domain", yref: "y2", x0: 0, x1: 1, y0: 0, y1: 0, line: { color: c.axis, width: 1 } });
    draw("chart-policy", traces, L, { yInclude: [["yaxis2", 0]] });
    footer("foot-policy", [{ series: "banxico_target", label: "Banxico target rate" }, { series: "dff", label: "Fed effective federal funds rate" },
      { series: "fed_lower", label: "Fed target range, lower limit" }, { series: "fed_upper", label: "Fed target range, upper limit" },
      { derived: "policy_spread" }]);
  }

  function chartFix() {
    const c = C();
    const f = state.data.fix;
    const L = baseLayout();
    ytitle(L, "yaxis", "MXN per USD");
    L.xaxis.hoverformat = "%d %b %Y";
    const traces = f ? [line("FIX", f.date, f.value, c.mx, { unit: "MXN/USD", dec: 4, extra: { line: { color: c.mx, width: 1.5 } } }),
      line("20-business-day average", f.date, f.ma20, c.text, { unit: "MXN/USD", dec: 4, dash: "dash", extra: { line: { color: c.text, width: 1.5, dash: "dash" } } })] : [];
    draw("chart-fix", traces, L);
    const items = [{ series: "fix", label: "FIX exchange rate (Banxico)" }];
    items.note = "Banxico dates the FIX by the day it is determined (it is published in the Diario Oficial the next banking day and used for payments the day after).";
    footer("foot-fix", items);
  }

  function chartRER() {
    const c = C();
    const r = state.data.rer;
    const L = baseLayout();
    ytitle(L, "yaxis", "Index, 2015 = 100");
    L.xaxis.hoverformat = "%b %Y";
    L.shapes.push({ type: "line", xref: "paper", x0: 0, x1: 1, y0: 100, y1: 100, line: { color: c.axis, width: 1 } });
    const traces = [];
    if (r) {
      traces.push(line("Own calculation: FIX × CPI US / INPC", r.date, r.rer_own_2015, c.mx, { unit: "" }));
      if (r.rer_banxico_2015 && r.rer_banxico_2015.some((v) => v !== null))
        traces.push(line("Banxico bilateral RER (SR1504)", r.date, r.rer_banxico_2015, c.ref, { unit: "", dash: "dash" }));
    }
    draw("chart-rer", traces, L);
    const rer = ((state.meta || {}).derived || {}).rer || {};
    document.getElementById("rer-note").textContent = rer.rer_correlation !== undefined
      ? `Correlation between the two series (2015 = 100): ${rer.rer_correlation.toFixed(3)} over ${rer.rer_correlation_n} months (${rer.rer_correlation_period}). Banxico's index uses the FIX by settlement date and US CPI not seasonally adjusted; the own calculation uses the FIX by determination date and US CPI seasonally adjusted.`
      : "";
    footer("foot-rer", [{ derived: "rer", label: "Own real exchange rate (rebased 2015 = 100)" },
      { series: "rer_banxico", label: "Banxico bilateral real exchange rate index vs the US", transformation: "rebased_2015 = 100 × value_t / mean(value over 2015); Banxico: an increase is a real depreciation" },
      { series: "cpi_us", label: "US CPI (input)" }, { series: "inpc", label: "Mexico INPC headline (input)" }]);
  }

  function chartYields() {
    const c = C();
    const y = state.data.yields, ym = state.data.yields_monthly;
    const L = twoPanelLayout();
    ytitle(L, "yaxis", "% per year");
    ytitle(L, "yaxis2", "pp");
    L.xaxis.hoverformat = L.xaxis2.hoverformat = "%d %b %Y";
    L.hovermode = "closest";
    const traces = [];
    if (ym) traces.push(line("US 10-year Treasury, monthly average", ym.date, ym.ust10_avg, c.us, { unit: "%", extra: { xperiod: "M1", xperiodalignment: "middle" } }));
    if (y) {
      traces.push(line("Bono M 10-year, auction yield", y.date, y.mbono10, c.mx, { unit: "%", extra: { mode: "lines+markers", marker: { size: 5 }, line: { color: c.mx, width: 1.5 } } }));
      traces.push({ type: "bar", name: "Spread Mexico − US (auction dates)", x: y.date, y: y.spread, xaxis: "x2", yaxis: "y2",
        marker: { color: c.ref }, width: 6 * 864e5, customdata: y.ust10,
        hovertemplate: "%{y:.2f} pp (US 10y %{customdata:.2f}%)<extra>Spread</extra>" });
    }
    draw("chart-yields", traces, L, { yInclude: [["yaxis2", 0]] });
    footer("foot-yields", [{ series: "mbono10", label: "Bono M 10-year (Banxico, weekly auction results table; auctions roughly every 4 weeks)" },
      { series: "ust10", label: "US 10-year Treasury (FRED)", transformation: "shown as the monthly average of daily yields (yields_monthly.csv); spread uses the daily value on each auction date" },
      { derived: "yields" }]);
  }

  // ---------- KPI tiles ----------
  function tile(label, value, unit, change, date) {
    return `<div class="kpi"><div class="kpi-label">${escapeHTML(label)}</div>
      <div class="kpi-value">${value}<span class="unit">${escapeHTML(unit)}</span></div>
      <div class="kpi-change">${change}</div><div class="kpi-date">${escapeHTML(date)}</div></div>`;
  }
  function kpiFrom(d, col, opt) {
    if (!d || !d[col]) return null;
    const i = lastValid(d.date, d[col]);
    if (i < 0) return null;
    const j = prevValid(d[col], i);
    return { v: d[col][i], dv: j >= 0 ? d[col][i] - d[col][j] : null, date: d.date[i], prev: j >= 0 ? d.date[j] : null, opt };
  }
  function renderKPIs() {
    const D = state.data, out = [];
    const g = kpiFrom(D.gdp, "yoy");
    if (g) out.push(tile("Real GDP, y/y", fmt(g.v, 1), "%", `${signed(g.dv, 1)} pp vs previous quarter`, quarterLabel(g.date)));
    const p = kpiFrom(D.inpc, "yoy");
    if (p) out.push(tile("Headline inflation, y/y", fmt(p.v, 2), "%", `${signed(p.dv, 2)} pp vs previous month`, monthLabel(p.date)));
    const u = kpiFrom(D.unemployment, "value");
    if (u) out.push(tile("Unemployment (SA)", fmt(u.v, 1), "%", `${signed(u.dv, 1)} pp vs previous month`, monthLabel(u.date)));
    const b = kpiFrom(D.banxico_target, "value");
    if (b) {
      const s = kpiFrom(D.policy_spread, "spread");
      out.push(tile("Banxico target rate", fmt(b.v, 2), "%", `${signed(b.dv, 2)} pp vs previous observation${s ? ` · spread vs Fed ${fmt(s.v, 2)} pp` : ""}`, b.date));
    }
    const f = kpiFrom(D.fix, "value");
    if (f) out.push(tile("FIX, MXN per USD", fmt(f.v, 4), "", `${signed(f.dv, 4)} vs previous business day (up = weaker peso)`, f.date));
    const r = kpiFrom(D.rer, "rer_own_2015");
    if (r) out.push(tile("Real exchange rate", fmt(r.v, 1), " (2015=100)", `${signed(r.dv, 1)} vs previous month (up = real depreciation)`, monthLabel(r.date)));
    const y = kpiFrom(D.yields, "mbono10");
    if (y) out.push(tile("Bono M 10y (auction)", fmt(y.v, 2), "%", `${signed(y.dv, 2)} pp vs previous auction`, y.date));
    document.getElementById("kpis").innerHTML = out.join("") || '<div class="kpi">No data yet.</div>';
  }

  // ---------- header ----------
  function renderHeader() {
    const m = state.meta;
    const t = document.getElementById("refresh-time");
    if (!m || !m.last_successful_refresh_utc) { t.textContent = "not yet run"; return; }
    const mx = (m.last_successful_refresh_mexico_city || "").split(" ")[1] || "";
    t.textContent = `${m.last_successful_refresh_utc} UTC (${mx} Mexico City)`;
    const failed = m.failed_series || [];
    if (failed.length) {
      const w = document.getElementById("warning");
      w.hidden = false;
      w.innerHTML = "<strong>Latest run had failed series.</strong> " + failed.map((f) =>
        `${escapeHTML(f.key)} (${escapeHTML(f.id)}): showing data through ${escapeHTML(f.last_good_observation_date || "none")}` +
        (f.last_good_fetch_utc ? `, last fetched ${escapeHTML(f.last_good_fetch_utc.replace("T", " ").replace("Z", " UTC"))}` : "")).join("; ") + ".";
    }
    const repo = m.repository || inferRepo();
    if (repo) {
      const base = `https://github.com/${repo}/blob/HEAD/`;
      document.getElementById("repo-links").innerHTML =
        `<a href="${base}README.md">README</a> · <a href="${base}VERIFICATION.md">VERIFICATION.md</a> · <a href="${base}logs/refresh_log.csv">logs/refresh_log.csv</a>` +
        (m.run_url ? ` · <a href="${escapeHTML(m.run_url)}">latest Actions run</a>` : "");
    } else {
      document.getElementById("repo-links").innerHTML = "README, VERIFICATION.md and logs/refresh_log.csv are in the GitHub repository.";
    }
  }
  function inferRepo() {
    const h = location.hostname.match(/^([^.]+)\.github\.io$/);
    if (!h) return null;
    const seg = location.pathname.split("/").filter(Boolean)[0];
    return seg ? `${h[1]}/${seg}` : `${h[1]}/${h[1]}.github.io`;
  }

  function renderAll() {
    renderKPIs();
    [chartGDP, chartInflation, chartUnemployment, chartPolicy, chartFix, chartRER, chartYields].forEach((f) => {
      try { f(); } catch (e) { console.error(f.name, e); }
    });
  }

  async function init() {
    try {
      const r = await fetch(DATA + "metadata.json", { cache: "no-cache" });
      state.meta = r.ok ? await r.json() : null;
    } catch (e) { state.meta = null; }
    renderHeader();
    const keys = ["gdp", "igae", "inpc", "inpc_core", "unemployment", "banxico_target", "dff", "fed_lower", "fed_upper",
      "policy_spread", "fix", "rer", "yields", "yields_monthly"];
    const loaded = await Promise.all(keys.map(loadCSV));
    keys.forEach((k, i) => (state.data[k] = loaded[i]));
    setupRangeButtons();
    document.querySelectorAll("[data-gdp]").forEach((b) => b.addEventListener("click", () => {
      state.gdpView = b.dataset.gdp;
      document.querySelectorAll("[data-gdp]").forEach((o) => { o.classList.toggle("active", o === b); o.setAttribute("aria-pressed", String(o === b)); });
      chartGDP();
    }));
    renderAll();
    if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", renderAll);
  }

  if (typeof Plotly === "undefined") {
    document.querySelectorAll(".chart").forEach((el) => (el.innerHTML = '<div class="empty">Charts need Plotly.js from cdn.plot.ly, which did not load.</div>'));
  }
  document.addEventListener("DOMContentLoaded", init);
})();
