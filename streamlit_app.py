"""Mexico macro dashboard as a Streamlit app.

Reads the same files the GitHub Actions refresh writes (docs/data/*.csv and metadata.json), so the
scheduled refresh stays the single source of truth: the app never calls the APIs and needs no tokens.

Run locally:   streamlit run streamlit_app.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "docs" / "data"

MX = "#008a17"        # Mexico series
MX_SOFT = "rgba(0, 138, 23, 0.55)"
US = "#257de0"        # US series
US_SOFT = "rgba(37, 125, 224, 0.16)"
REF = "#6b6b66"       # spreads, targets, reference lines
BAND = "rgba(107, 107, 102, 0.12)"
TEXT = "#191919"
GRID = "#ececea"
AXIS = "#b9b9b4"

QUESTION = ("How are Mexico's growth, inflation and labor market doing, and how are Banxico's policy "
            "and the peso reacting relative to the US Federal Reserve?")
DATA_KEYS = ["gdp", "igae", "inpc", "inpc_core", "unemployment", "banxico_target", "dff", "fed_lower",
             "fed_upper", "policy_spread", "fix", "rer", "yields", "yields_monthly"]

st.set_page_config(page_title="Mexico macro dashboard", page_icon="📈", layout="wide")


# --------------------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------------------
def _data_version() -> tuple:
    """Changes whenever the refresh commits new files, so the cache reloads them."""
    return tuple(sorted((p.name, p.stat().st_mtime_ns) for p in DATA_DIR.glob("*") if p.is_file()))


@st.cache_data(show_spinner=False)
def load_all(version: tuple) -> tuple[dict, dict[str, pd.DataFrame]]:
    meta_path = DATA_DIR / "metadata.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    start = pd.Timestamp(meta.get("display_start", "2015-01-01"))
    frames: dict[str, pd.DataFrame] = {}
    for key in DATA_KEYS:
        path = DATA_DIR / f"{key}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path)
        for col in df.columns:
            if col == "date" or col.endswith("_date"):
                df[col] = pd.to_datetime(df[col], errors="coerce")
        # The CSVs carry one extra year (2014) for lags; the dashboard sample starts in 2015.
        frames[key] = df[df["date"] >= start].reset_index(drop=True)
    return meta, frames


meta, data = load_all(_data_version())
series_meta = meta.get("series", {})
derived_meta = meta.get("derived", {})


def window(df: pd.DataFrame | None, start: pd.Timestamp | None) -> pd.DataFrame | None:
    if df is None or start is None:
        return df
    return df[df["date"] >= start]


# --------------------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------------------
def fmt(v, d=2) -> str:
    return "–" if v is None or pd.isna(v) else f"{v:,.{d}f}"


def signed(v, d=2) -> str:
    if v is None or pd.isna(v):
        return "–"
    r = round(float(v), d)
    return ("+" if r > 0 else "−" if r < 0 else "±") + f"{abs(r):,.{d}f}"


def quarter_label(ts: pd.Timestamp) -> str:
    return f"{ts.year} Q{(ts.month - 1) // 3 + 1}"


def last_two(df: pd.DataFrame | None, col: str):
    """Latest valid value, change vs the previous valid value, and the latest date."""
    if df is None or col not in df:
        return None
    v = df.dropna(subset=[col])
    if v.empty:
        return None
    cur = v.iloc[-1]
    prev = v.iloc[-2][col] if len(v) > 1 else None
    return cur[col], (cur[col] - prev if prev is not None else None), cur["date"]


def base_layout(fig: go.Figure, height: int = 360, ytitle: str = "", hover: str = "x unified") -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=10, r=10, t=30, b=10), hovermode=hover,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(size=12, color=TEXT),
        legend=dict(orientation="h", x=0, y=1.02, yanchor="bottom", font=dict(size=11), bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(bgcolor="white", bordercolor=AXIS),
    )
    fig.update_xaxes(gridcolor=GRID, linecolor=AXIS, zeroline=False, showspikes=True, spikemode="across",
                     spikethickness=1, spikecolor=AXIS, spikedash="dot")
    fig.update_yaxes(gridcolor=GRID, linecolor=AXIS, zeroline=False)
    if ytitle:
        fig.update_yaxes(title_text=ytitle, title_font=dict(size=11), row=None)
    return fig


def line(name, x, y, color, unit="", dash="solid", shape="linear", dec=2, width=2, **kw) -> go.Scatter:
    return go.Scatter(x=x, y=y, name=name, mode=kw.pop("mode", "lines"), connectgaps=False,
                      line=dict(color=color, width=width, dash=dash, shape=shape),
                      hovertemplate=f"%{{y:.{dec}f}} {unit}<extra>{name}</extra>", **kw)


def show(fig: go.Figure) -> None:
    st.plotly_chart(fig, width="stretch", config={"displaylogo": False})


def empty_note() -> None:
    st.info("No data yet. It appears after the first successful refresh.")


def footer(items: list[dict], note: str | None = None) -> None:
    """Units, frequency, source link, ID, latest observation, SA status and transformation."""
    parts = []
    for it in items:
        if "series" in it:
            s = series_meta.get(it["series"])
            if not s:
                continue
            kept = (f" — :orange[not refreshed in the latest run ({s['fetch_status']})]"
                    if s.get("fetch_status") != "ok" else "")
            parts.append(
                f"**{it.get('label', s['name'])}**{kept}  \n"
                f"Units: {s['units']} · Frequency: {s['frequency']} · Seasonal adjustment: {s['seasonal_adjustment']}  \n"
                f"Source: [{s['source']}]({s['source_url']}) · Series ID: `{s['id']}` · "
                f"Latest observation: **{s.get('latest_observation_date') or '–'}**  \n"
                f"Transformation: {it.get('transformation', s['transformation'])}")
        else:
            d = derived_meta.get(it["derived"])
            if not d:
                continue
            parts.append(
                f"**{it.get('label', d['name'])}**  \n"
                f"Units: {d['units']} · Frequency: {d['frequency']} · Computed from: {', '.join(d.get('inputs', []))} · "
                f"Latest observation: **{d.get('latest_observation_date') or '–'}**  \n"
                f"Transformation: {d['transformation']}")
    if note:
        parts.append(note)
    with st.container(border=False):
        st.caption("\n\n".join(parts).replace("$", "\\$"))


def data_table(df: pd.DataFrame | None, key: str, cols: list[str]) -> None:
    if df is None:
        return
    with st.expander("Data table"):
        show_df = df[[c for c in ["date", *cols] if c in df]].copy()
        st.dataframe(show_df, hide_index=True, width="stretch", height=240)
        st.download_button("Download CSV", df.to_csv(index=False).encode(), file_name=f"{key}.csv",
                           mime="text/csv", key=f"dl_{key}")


# --------------------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------------------
head_l, head_r = st.columns([3, 1.2], vertical_alignment="top")
with head_l:
    st.title("Mexico macro dashboard")
    st.markdown(f"*{QUESTION}*")
with head_r:
    with st.container(border=True):
        st.caption("LAST SUCCESSFUL REFRESH")
        if meta.get("last_successful_refresh_utc"):
            mx_time = (meta.get("last_successful_refresh_mexico_city") or " ").split(" ")[-1]
            st.markdown(f"**{meta['last_successful_refresh_utc']} UTC ({mx_time} Mexico City)**")
        else:
            st.markdown("**not yet run**")
        st.caption("When the data pipeline last ran successfully. Each chart shows its own latest "
                   "observation date, which is usually earlier.")

failed = meta.get("failed_series") or []
if failed:
    st.warning("**Latest run had failed series.** " + "; ".join(
        f"{f['key']} ({f['id']}): showing data through {f.get('last_good_observation_date') or 'none'}"
        for f in failed) + ".", icon="⚠️")

ctrl_l, ctrl_r = st.columns([2, 3], vertical_alignment="center")
with ctrl_l:
    rng = st.segmented_control("Range for all charts", ["1Y", "3Y", "5Y", "All"], default="All",
                               key="range") or "All"
with ctrl_r:
    st.markdown(f":green[**━ Mexico**]&nbsp;&nbsp; :blue[**━ United States**]&nbsp;&nbsp; :gray[**━ Spread / reference**]")


def range_start(df_list: list[pd.DataFrame | None]) -> pd.Timestamp | None:
    if rng == "All":
        return None
    lasts = [d["date"].max() for d in df_list if d is not None and not d.empty]
    if not lasts:
        return None
    return max(lasts) - pd.DateOffset(years=int(rng[0]))


# --------------------------------------------------------------------------------------
# KPI tiles
# --------------------------------------------------------------------------------------
tiles = []
if (k := last_two(data.get("gdp"), "yoy")):
    tiles.append(("Real GDP, y/y", f"{fmt(k[0], 1)}%", f"{signed(k[1], 1)} pp vs previous quarter", quarter_label(k[2])))
if (k := last_two(data.get("inpc"), "yoy")):
    tiles.append(("Headline inflation, y/y", f"{fmt(k[0])}%", f"{signed(k[1])} pp vs previous month", f"{k[2]:%b %Y}"))
if (k := last_two(data.get("unemployment"), "value")):
    tiles.append(("Unemployment (SA)", f"{fmt(k[0], 1)}%", f"{signed(k[1], 1)} pp vs previous month", f"{k[2]:%b %Y}"))
if (k := last_two(data.get("banxico_target"), "value")):
    sp = last_two(data.get("policy_spread"), "spread")
    extra = f"; spread vs Fed {fmt(sp[0])} pp" if sp else ""
    tiles.append(("Banxico target rate", f"{fmt(k[0])}%", f"{signed(k[1])} pp vs previous observation{extra}", f"{k[2]:%Y-%m-%d}"))
if (k := last_two(data.get("fix"), "value")):
    tiles.append(("FIX, MXN per USD", fmt(k[0], 4), f"{signed(k[1], 4)} vs previous business day (up = weaker peso)", f"{k[2]:%Y-%m-%d}"))
if (k := last_two(data.get("rer"), "rer_own_idx")):
    tiles.append(("Real exchange rate", fmt(k[0], 1), f"sample average = 100; {signed(k[1], 1)} vs previous month (up = real depreciation)", f"{k[2]:%b %Y}"))
if (k := last_two(data.get("yields"), "mbono10")):
    tiles.append(("Bono M 10y (auction)", f"{fmt(k[0])}%", f"{signed(k[1])} pp vs previous auction", f"{k[2]:%Y-%m-%d}"))

if tiles:
    for col, (label, value, change, when) in zip(st.columns(len(tiles)), tiles):
        with col:
            with st.container(border=True):
                st.metric(label, value)
                st.caption(f"{change}  \n{when}")
else:
    st.info("No data yet. Run the GitHub Actions refresh (or `python scripts/fetch_data.py` locally) first.")

st.divider()


# --------------------------------------------------------------------------------------
# Charts
# --------------------------------------------------------------------------------------
def chart_gdp():
    st.subheader("1. Real GDP growth")
    view = st.segmented_control("View", ["GDP (quarterly)", "IGAE (monthly proxy)"], default="GDP (quarterly)",
                                key="gdp_view", label_visibility="collapsed") or "GDP (quarterly)"
    if view.startswith("GDP"):
        st.caption("Bars: y/y %. Line: q/q %, **not annualized**. Quarterly, seasonally adjusted, constant 2018 prices.")
        g = data.get("gdp")
        if g is None:
            return empty_note()
        g = window(g, range_start([g]))
        labels = [quarter_label(d) for d in g["date"]]
        fig = go.Figure([
            go.Bar(x=g["date"], y=g["yoy"], name="y/y %", marker_color=MX_SOFT, customdata=labels,
                   hovertemplate="%{customdata}: %{y:.2f}% y/y<extra>GDP</extra>", xperiod="M3", xperiodalignment="middle"),
            line("q/q %, not annualized", g["date"], g["qoq"], TEXT, unit="% q/q", mode="lines+markers",
                 marker=dict(size=5), xperiod="M3", xperiodalignment="middle"),
        ])
        fig.add_hline(y=0, line=dict(color=AXIS, width=1))
        show(base_layout(fig, ytitle="%"))
        st.caption("*INEGI revises GDP and IGAE, so past values can change between refreshes.*")
        footer([{"series": "gdp", "label": "Real GDP (INEGI)"}])
        data_table(g, "gdp", ["value", "yoy", "qoq"])
    else:
        st.caption("IGAE, INEGI's monthly economic activity index, seasonally adjusted, y/y % (monthly proxy for GDP).")
        g = data.get("igae")
        if g is None:
            return empty_note()
        g = window(g, range_start([g]))
        fig = go.Figure([line("IGAE y/y %", g["date"], g["yoy"], MX, unit="% y/y")])
        fig.add_hline(y=0, line=dict(color=AXIS, width=1))
        show(base_layout(fig, ytitle="%"))
        st.caption("*INEGI revises GDP and IGAE, so past values can change between refreshes.*")
        footer([{"series": "igae", "label": "IGAE (INEGI)"}])
        data_table(g, "igae", ["value", "yoy"])


def chart_inflation():
    st.subheader("2. Inflation")
    st.caption("INPC headline and core, y/y %. Banxico target 3% with the 2–4% variability band.")
    h, k = data.get("inpc"), data.get("inpc_core")
    if h is None and k is None:
        return empty_note()
    start = range_start([h, k])
    fig = go.Figure()
    fig.add_hrect(y0=2, y1=4, fillcolor=BAND, line_width=0, layer="below")
    fig.add_hline(y=3, line=dict(color=REF, width=1.5, dash="dash"),
                  annotation_text="3% target (band 2–4%)", annotation_position="top right",
                  annotation_font=dict(size=11, color=REF))
    if h is not None:
        h = window(h, start)
        fig.add_trace(line("Headline", h["date"], h["yoy"], MX, unit="% y/y"))
    if k is not None:
        k = window(k, start)
        fig.add_trace(line("Core", k["date"], k["yoy"], MX, unit="% y/y", dash="dot"))
    show(base_layout(fig, ytitle="% y/y"))
    footer([{"series": "inpc", "label": "Headline INPC (INEGI)"}, {"series": "inpc_core", "label": "Core INPC (INEGI)"}])
    if h is not None and k is not None:
        data_table(h[["date", "value", "yoy"]].merge(k[["date", "value", "yoy"]], on="date", how="outer",
                                                     suffixes=("_headline", "_core")), "inflation",
                   ["value_headline", "yoy_headline", "value_core", "yoy_core"])


def chart_unemployment():
    st.subheader("3. Unemployment rate")
    st.caption("ENOE monthly, national, % of the economically active population, **seasonally adjusted**.")
    u = data.get("unemployment")
    if u is None:
        return empty_note()
    u = window(u, range_start([u]))
    fig = go.Figure([line("Unemployment rate (SA)", u["date"], u["value"], MX, unit="%",
                          mode="lines+markers", marker=dict(size=3))])
    gap_start, gap_end = pd.Timestamp("2020-03-16"), pd.Timestamp("2020-06-16")
    if u["date"].min() <= gap_end:
        fig.add_vrect(x0=gap_start, x1=gap_end, fillcolor=BAND, line_width=0, layer="below",
                      annotation_text="ETOE gap, Apr–Jun 2020", annotation_position="top right",
                      annotation_font=dict(size=11, color=REF))
    show(base_layout(fig, ytitle="% of EAP"))
    st.caption("*April–June 2020 is left blank: INEGI suspended the ENOE and ran the ETOE telephone survey "
               "instead. The gap is not interpolated.*")
    footer([{"series": "unemployment", "label": "Unemployment rate, ENOE (INEGI), seasonally adjusted version"}])
    data_table(u, "unemployment", ["value", "note"])


def two_panel() -> go.Figure:
    return make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28], vertical_spacing=0.06)


def chart_policy():
    st.subheader("4. Policy rates: Banxico vs the Fed")
    st.caption("Top: Banxico target (step line, dots on decision dates) and the Fed's effective rate with its "
               "target range shaded. Bottom: spread, Banxico target − Fed upper limit.")
    bx, dff, lo, hi, sp = (data.get(k) for k in ("banxico_target", "dff", "fed_lower", "fed_upper", "policy_spread"))
    if bx is None and dff is None:
        return empty_note()
    start = range_start([bx, dff, sp])
    bx, dff, lo, hi, sp = (window(d, start) for d in (bx, dff, lo, hi, sp))
    fig = two_panel()
    if lo is not None and hi is not None:
        fig.add_trace(go.Scatter(x=lo["date"], y=lo["value"], line=dict(width=0, shape="hv"), showlegend=False,
                                 hoverinfo="skip", name="Fed lower"), 1, 1)
        fig.add_trace(go.Scatter(x=hi["date"], y=hi["value"], fill="tonexty", fillcolor=US_SOFT,
                                 line=dict(width=0, shape="hv"), name="Fed target range",
                                 customdata=lo.set_index("date")["value"].reindex(hi["date"]).to_numpy(),
                                 hovertemplate="%{customdata:.2f}–%{y:.2f}%<extra>Fed target range</extra>"), 1, 1)
    if dff is not None:
        fig.add_trace(line("Fed effective rate (DFF)", dff["date"], dff["value"], US, unit="%", width=1.5), 1, 1)
    if bx is not None:
        fig.add_trace(line("Banxico target", bx["date"], bx["value"], MX, unit="%", shape="hv"), 1, 1)
        dec = bx[bx["decision"] == 1]
        fig.add_trace(go.Scatter(x=dec["date"], y=dec["value"], mode="markers", name="Banxico decision (new level in force)",
                                 marker=dict(color=MX, size=8, line=dict(color="white", width=2)),
                                 hovertemplate="new target %{y:.2f}%<extra>Decision</extra>"), 1, 1)
    if sp is not None:
        fig.add_trace(line("Spread: Banxico − Fed upper", sp["date"], sp["spread"], REF, unit="pp", shape="hv",
                           fill="tozeroy", fillcolor=BAND), 2, 1)
        fig.add_hline(y=0, line=dict(color=AXIS, width=1), row=2, col=1)
    base_layout(fig, height=480)
    fig.update_yaxes(title_text="% per year", row=1, col=1)
    fig.update_yaxes(title_text="pp", row=2, col=1)
    show(fig)
    footer([{"series": "banxico_target", "label": "Banxico target rate"},
            {"series": "dff", "label": "Fed effective federal funds rate"},
            {"series": "fed_lower", "label": "Fed target range, lower limit"},
            {"series": "fed_upper", "label": "Fed target range, upper limit"},
            {"derived": "policy_spread"}])
    data_table(sp, "policy_spread", ["banxico_target", "fed_upper", "spread"])


def chart_fix():
    st.subheader("5. Spot exchange rate, MXN/USD")
    st.caption("**MXN per 1 USD. Up = weaker peso.** Banxico FIX, business days, with a 20-business-day moving average.")
    f = data.get("fix")
    if f is None:
        return empty_note()
    f = window(f, range_start([f]))
    fig = go.Figure([line("FIX", f["date"], f["value"], MX, unit="MXN/USD", dec=4, width=1.5),
                     line("20-business-day average", f["date"], f["ma20"], TEXT, unit="MXN/USD", dec=4,
                          dash="dash", width=1.5)])
    show(base_layout(fig, ytitle="MXN per USD"))
    footer([{"series": "fix", "label": "FIX exchange rate (Banxico)"}],
           note="Banxico dates the FIX by the day it is determined (it is published in the Diario Oficial the next "
                "banking day and used for payments the day after).")
    data_table(f, "fix", ["value", "ma20"])


def chart_rer():
    st.subheader("6. Real exchange rate, MXN vs USD")
    st.caption("Index, sample average (since Jan 2015) = 100. **Up = real depreciation of the peso** (Mexico becomes relatively cheaper).")
    r = data.get("rer")
    if r is None:
        return empty_note()
    r = window(r, range_start([r]))
    fig = go.Figure([line("Own calculation: FIX × CPI US / INPC", r["date"], r["rer_own_idx"], MX)])
    if "rer_banxico_idx" in r and r["rer_banxico_idx"].notna().any():
        fig.add_trace(line("Banxico bilateral RER (SR1504)", r["date"], r["rer_banxico_idx"], REF, dash="dash"))
    fig.add_hline(y=100, line=dict(color=AXIS, width=1))
    show(base_layout(fig, ytitle="Index, sample average = 100"))
    rr = derived_meta.get("rer", {})
    if "rer_correlation" in rr:
        st.caption(f"*Correlation between the two series (sample average = 100): {rr['rer_correlation']:.3f} over "
                   f"{rr['rer_correlation_n']} months ({rr['rer_correlation_period']}). Banxico's index uses the FIX by "
                   f"settlement date and US CPI not seasonally adjusted; the own calculation uses the FIX by "
                   f"determination date and US CPI seasonally adjusted.*")
    footer([{"derived": "rer", "label": "Own real exchange rate (rebased, sample average = 100)"},
            {"series": "rer_banxico", "label": "Banxico bilateral real exchange rate index vs the US",
             "transformation": "rebased_avg = 100 × value_t / mean(value over the dashboard sample); Banxico: an increase is a real depreciation"},
            {"series": "cpi_us", "label": "US CPI (input)"},
            {"series": "inpc", "label": "Mexico INPC headline (input)"}])
    data_table(r, "rer", ["fix_avg", "cpi_us", "inpc", "rer_own_idx", "rer_banxico_idx"])


def chart_yields():
    st.subheader("7. 10-year government bond yields")
    st.caption("Top: Bono M 10-year yield at each primary auction (Banxico publishes no daily 10-year yield) and the "
               "US 10-year Treasury as a **monthly average** of daily yields. Bottom: spread on auction dates, Mexico − US.")
    y, ym = data.get("yields"), data.get("yields_monthly")
    if y is None and ym is None:
        return empty_note()
    start = range_start([y, ym])
    y, ym = window(y, start), window(ym, start)
    fig = two_panel()
    if ym is not None:
        fig.add_trace(line("US 10-year Treasury, monthly average", ym["date"], ym["ust10_avg"], US, unit="%",
                           xperiod="M1", xperiodalignment="middle"), 1, 1)
    if y is not None:
        fig.add_trace(line("Bono M 10-year, auction yield", y["date"], y["mbono10"], MX, unit="%", width=1.5,
                           mode="lines+markers", marker=dict(size=5)), 1, 1)
        fig.add_trace(go.Bar(x=y["date"], y=y["spread"], name="Spread Mexico − US (auction dates)", marker_color=REF,
                             width=6 * 86_400_000, customdata=y["ust10"],
                             hovertemplate="%{y:.2f} pp (US 10y %{customdata:.2f}%)<extra>Spread</extra>"), 2, 1)
    base_layout(fig, height=480, hover="closest")
    fig.update_yaxes(title_text="% per year", row=1, col=1)
    fig.update_yaxes(title_text="pp", row=2, col=1)
    show(fig)
    footer([{"series": "mbono10", "label": "Bono M 10-year (Banxico, weekly auction results table; auctions roughly every 4 weeks)"},
            {"series": "ust10", "label": "US 10-year Treasury (FRED)",
             "transformation": "shown as the monthly average of daily yields (yields_monthly.csv); spread uses the daily value on each auction date"},
            {"derived": "yields"}])
    data_table(y, "yields", ["mbono10", "ust10", "ust10_date", "spread"])


charts = [chart_gdp, chart_inflation, chart_unemployment, chart_policy, chart_fix, chart_rer, chart_yields]
for i in range(0, len(charts), 2):
    cols = st.columns(2, gap="medium")
    for col, fn in zip(cols, charts[i:i + 2]):
        with col:
            with st.container(border=True):
                fn()

# --------------------------------------------------------------------------------------
# Page footer
# --------------------------------------------------------------------------------------
st.divider()
st.caption("Sources: Banco de México (SIE), INEGI (BIE) and the Federal Reserve via FRED. All transformations are "
           "computed in Python by the scheduled refresh; this app only reads and draws them.")
repo = meta.get("repository")
if repo:
    base = f"https://github.com/{repo}/blob/HEAD/"
    links = (f"[README]({base}README.md) · [VERIFICATION.md]({base}VERIFICATION.md) · "
             f"[logs/refresh_log.csv]({base}logs/refresh_log.csv)")
    if meta.get("run_url"):
        links += f" · [latest Actions run]({meta['run_url']})"
    st.caption(links)
else:
    st.caption("README, VERIFICATION.md and logs/refresh_log.csv are in the GitHub repository.")
