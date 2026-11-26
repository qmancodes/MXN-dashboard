#!/usr/bin/env python3
"""Fetch, validate, transform and store every series for the Mexico macro dashboard.

Sources: Banxico SIE REST API (BANXICO_TOKEN), INEGI BIE API (INEGI_TOKEN), FRED CSV (no key).

Outputs
  docs/data/<key>.csv        one file per source series: date,value[,transformed columns...]
  docs/data/<derived>.csv    policy_spread, rer, yields, yields_monthly
  docs/data/metadata.json    per-series metadata + run information
  logs/refresh_log.csv       one row per run

Fail-safe rule: if a series cannot be fetched, or the new data looks suspicious (empty, fewer
rows than the stored file, or a latest date earlier than the stored one), the stored CSV is kept,
the reason is logged and the run continues with the other series.

Tokens are read from the environment only and are never printed.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "series.yaml"
DATA_DIR = ROOT / "docs" / "data"
LOG_PATH = ROOT / "logs" / "refresh_log.csv"
METADATA_PATH = DATA_DIR / "metadata.json"

TIMEOUT = 30
RETRIES = 3
BACKOFF_BASE = 2  # seconds: 2, 4, 8
MX_TZ = ZoneInfo("America/Mexico_City")

BANXICO_BASE = "https://www.banxico.org.mx/SieAPIRest/service/v1/series"
INEGI_BASE = "https://www.inegi.org.mx/app/api/indicadores/desarrolladores/jsonxml/INDICATOR"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"

MISSING_TOKENS = {"", "N/E", "NE", "N/D", "ND", ".", "NA", "N/A", "-", "nan", "NaN"}

# Overridable so tests can point the fetchers at a local mock server.
BANXICO_BASE = os.environ.get("BANXICO_BASE_URL", BANXICO_BASE)
INEGI_BASE = os.environ.get("INEGI_BASE_URL", INEGI_BASE)
FRED_CSV = os.environ.get("FRED_CSV_URL", FRED_CSV)


class FetchError(Exception):
    """A series could not be fetched or parsed."""


# --------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------
_SECRETS: list[str] = []


def redact(text: str) -> str:
    for s in _SECRETS:
        if s:
            text = text.replace(s, "***")
    return text


def http_get(url: str, headers: dict | None = None, params: dict | None = None) -> requests.Response:
    """GET with a 30 s timeout and 3 retries with exponential backoff. Errors are redacted."""
    last_err: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
            if resp.status_code >= 500 or resp.status_code == 429:
                raise FetchError(f"HTTP {resp.status_code}")
            if resp.status_code >= 400:
                # Client errors will not fix themselves: fail without retrying.
                raise FetchError(f"HTTP {resp.status_code} (not retried)")
            return resp
        except FetchError as e:
            last_err = e
            if "not retried" in str(e):
                break
        except requests.RequestException as e:
            last_err = e
        if attempt < RETRIES:
            time.sleep(BACKOFF_BASE ** (attempt + 1) if not os.environ.get("FETCH_FAST_RETRY") else 0)
    raise FetchError(redact(f"{type(last_err).__name__}: {last_err}"))


def to_number(raw) -> float:
    if raw is None:
        return math.nan
    s = str(raw).strip()
    if s in MISSING_TOKENS:
        return math.nan
    s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return math.nan


def tidy(rows: list[tuple[pd.Timestamp, float]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["date", "value"])
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["date"]).drop_duplicates("date", keep="last").sort_values("date")
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------------------
# Source fetchers
# --------------------------------------------------------------------------------------
def banxico_headers() -> dict:
    token = os.environ.get("BANXICO_TOKEN", "")
    if not token:
        raise FetchError("BANXICO_TOKEN is not set")
    return {"Bmx-Token": token, "Accept": "application/json"}


def fetch_banxico_title(sid: str) -> str:
    resp = http_get(f"{BANXICO_BASE}/{sid}", headers=banxico_headers())
    try:
        return resp.json()["bmx"]["series"][0].get("titulo", "")
    except (ValueError, KeyError, IndexError) as e:
        raise FetchError(f"unexpected Banxico metadata format: {e}") from None


def fetch_banxico(sid: str, start: str, end: str) -> pd.DataFrame:
    resp = http_get(f"{BANXICO_BASE}/{sid}/datos/{start}/{end}", headers=banxico_headers())
    try:
        series = resp.json()["bmx"]["series"][0]
    except (ValueError, KeyError, IndexError) as e:
        raise FetchError(f"unexpected Banxico response format: {e}") from None
    rows = []
    for obs in series.get("datos", []) or []:
        d = pd.to_datetime(obs.get("fecha"), format="%d/%m/%Y", errors="coerce")
        rows.append((d, to_number(obs.get("dato"))))
    return tidy(rows)


def inegi_period(tp: str, frequency: str) -> pd.Timestamp:
    """INEGI TIME_PERIOD: 'YYYY/MM' monthly, 'YYYY/0Q' quarterly (quarter number), 'YYYY' annual."""
    tp = str(tp).strip()
    if "/" not in tp:
        return pd.Timestamp(int(tp), 1, 1)
    y, p = tp.split("/")[:2]
    y, p = int(y), int(p)
    if frequency == "quarterly":
        if not 1 <= p <= 4:
            raise FetchError(f"quarterly period out of range: {tp}")
        return pd.Timestamp(y, 3 * (p - 1) + 1, 1)
    return pd.Timestamp(y, p, 1)


def fetch_inegi(iid: str, frequency: str) -> pd.DataFrame:
    token = os.environ.get("INEGI_TOKEN", "")
    if not token:
        raise FetchError("INEGI_TOKEN is not set")
    _SECRETS.append(token)
    last: Exception | None = None
    for bank in ("BIE", "BIE-BISE"):  # BIE = Banco de Información Económica; BIE-BISE as a fallback
        url = f"{INEGI_BASE}/{iid}/es/00/false/{bank}/2.0/{token}"
        try:
            resp = http_get(url, params={"type": "json"})
            payload = resp.json()
            obs = payload["Series"][0]["OBSERVATIONS"]
            rows = [(inegi_period(o["TIME_PERIOD"], frequency), to_number(o.get("OBS_VALUE"))) for o in obs]
            return tidy(rows)
        except (FetchError, ValueError, KeyError, IndexError, TypeError) as e:
            last = e
    raise FetchError(redact(f"INEGI {iid}: {type(last).__name__}: {last}"))


def fetch_fred(sid: str, start: str) -> pd.DataFrame:
    resp = http_get(FRED_CSV, params={"id": sid, "cosd": start})
    try:
        raw = pd.read_csv(io.StringIO(resp.text), dtype=str)
    except Exception as e:  # noqa: BLE001
        raise FetchError(f"FRED CSV unreadable: {e}") from None
    if sid not in raw.columns or len(raw.columns) < 2:
        raise FetchError(f"FRED CSV has no column {sid}: {list(raw.columns)}")
    date_col = [c for c in raw.columns if c != sid][0]
    rows = [(pd.to_datetime(d, errors="coerce"), to_number(v)) for d, v in zip(raw[date_col], raw[sid])]
    return tidy(rows)


# --------------------------------------------------------------------------------------
# Transformations (all computed here, never in the browser)
# --------------------------------------------------------------------------------------
def pct_change_by_date(df: pd.DataFrame, months: int) -> pd.Series:
    """100 × (x_t / x_{t-months} − 1), matched by calendar date so gaps never misalign lags."""
    s = df.set_index("date")["value"]
    lag_dates = df["date"] - pd.DateOffset(months=months)
    lagged = s.reindex(lag_dates).to_numpy()
    return pd.Series(100 * (df["value"].to_numpy() / lagged - 1), index=df.index)


def transform(key: str, spec: dict, df: pd.DataFrame, display_start: pd.Timestamp | None = None) -> pd.DataFrame:
    df = df.copy()
    if key == "gdp":
        df["yoy"] = pct_change_by_date(df, 12)
        df["qoq"] = pct_change_by_date(df, 3)
    elif key in ("igae", "inpc", "inpc_core"):
        df["yoy"] = pct_change_by_date(df, 12)
    elif key == "unemployment":
        gaps = pd.to_datetime(spec.get("gap_months", []))
        df = df[~df["date"].isin(gaps)]
        filler = pd.DataFrame({"date": gaps, "value": math.nan})
        df = pd.concat([df, filler]).sort_values("date").reset_index(drop=True)
        df["note"] = ""
        df.loc[df["date"].isin(gaps), "note"] = "ENOE gap: ETOE telephone survey, not interpolated"
    elif key == "fix":
        valid = df.dropna(subset=["value"])
        df["ma20"] = valid["value"].rolling(20, min_periods=20).mean()
    elif key == "banxico_target":
        prev = df["value"].shift(1)
        df["decision"] = ((df["value"] != prev) & prev.notna() & df["value"].notna()).astype(int)
    elif key == "rer_banxico":
        # 100 = historical average of the dashboard sample (display_start to the latest observation)
        in_sample = df["date"] >= display_start if display_start is not None else df["date"].notna()
        base = df.loc[in_sample, "value"].mean()
        df["rebased_avg"] = 100 * df["value"] / base
    return df


def derive(frames: dict[str, pd.DataFrame], display_start: pd.Timestamp) -> tuple[dict[str, pd.DataFrame], dict]:
    out: dict[str, pd.DataFrame] = {}
    extra: dict = {}

    # Policy spread (daily, calendar days, both step functions carried forward)
    if "banxico_target" in frames and "fed_upper" in frames:
        bx = frames["banxico_target"].dropna(subset=["value"]).set_index("date")["value"]
        fu = frames["fed_upper"].dropna(subset=["value"]).set_index("date")["value"]
        if len(bx) and len(fu):
            end = min(bx.index.max(), fu.index.max())
            idx = pd.date_range(max(bx.index.min(), fu.index.min()), end, freq="D")
            d = pd.DataFrame({"banxico_target": bx.reindex(idx, method="ffill"),
                              "fed_upper": fu.reindex(idx, method="ffill")})
            d["spread"] = d["banxico_target"] - d["fed_upper"]
            out["policy_spread"] = d.rename_axis("date").reset_index()

    # Real exchange rate (monthly)
    needed = ("fix", "cpi_us", "inpc")
    if all(k in frames for k in needed):
        fx = frames["fix"].dropna(subset=["value"]).set_index("date")["value"]
        s = fx.resample("MS").mean().rename("fix_avg")
        cpi_us = frames["cpi_us"].set_index("date")["value"].rename("cpi_us")
        inpc = frames["inpc"].set_index("date")["value"].rename("inpc")
        d = pd.concat([s, cpi_us, inpc], axis=1)
        # Only months where the FIX month is complete or in the past: drop the running month.
        today_ms = pd.Timestamp(date.today()).to_period("M").to_timestamp()
        d = d[d.index < today_ms]
        d["rer_raw"] = d["fix_avg"] * d["cpi_us"] / d["inpc"]
        # 100 = average of the dashboard sample (display_start to the latest month)
        base = d.loc[d.index >= display_start, "rer_raw"].mean()
        d["rer_own_idx"] = 100 * d["rer_raw"] / base
        if "rer_banxico" in frames:
            rb = frames["rer_banxico"].set_index("date")["value"].rename("rer_banxico_raw")
            d = d.join(rb, how="left")
            bbase = d.loc[d.index >= display_start, "rer_banxico_raw"].mean()
            d["rer_banxico_idx"] = 100 * d["rer_banxico_raw"] / bbase
            both = d.loc[d.index >= display_start, ["rer_own_idx", "rer_banxico_idx"]].dropna()
            if len(both) >= 12:
                extra["rer_correlation"] = round(float(both.corr().iloc[0, 1]), 4)
                extra["rer_correlation_n"] = int(len(both))
                extra["rer_correlation_period"] = f"{both.index.min():%Y-%m} to {both.index.max():%Y-%m}"
        d = d.dropna(subset=["rer_raw"])
        out["rer"] = d.rename_axis("date").reset_index()

    # Yields
    if "mbono10" in frames and "ust10" in frames:
        mb = frames["mbono10"].dropna(subset=["value"]).sort_values("date")
        us = frames["ust10"].dropna(subset=["value"]).sort_values("date")
        if len(mb) and len(us):
            m = pd.merge_asof(mb.rename(columns={"value": "mbono10"}),
                              us.rename(columns={"value": "ust10", "date": "ust10_date"}),
                              left_on="date", right_on="ust10_date", direction="backward")
            m["spread"] = m["mbono10"] - m["ust10"]
            out["yields"] = m[["date", "mbono10", "ust10", "ust10_date", "spread"]]
            mm = mb.set_index("date")["value"].resample("MS").mean().rename("mbono10_avg")
            um = us.set_index("date")["value"].resample("MS").mean().rename("ust10_avg")
            ym = pd.concat([mm, um], axis=1)
            ym["spread_avg"] = ym["mbono10_avg"] - ym["ust10_avg"]
            out["yields_monthly"] = ym.rename_axis("date").reset_index()
    return out, extra


# --------------------------------------------------------------------------------------
# Validation and sanity checks
# --------------------------------------------------------------------------------------
def read_stored(key: str) -> pd.DataFrame | None:
    path = DATA_DIR / f"{key}.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=["date"])
    return df[["date", "value"]] if "value" in df.columns else None


def suspicious_reason(new: pd.DataFrame, stored: pd.DataFrame | None) -> str | None:
    valid = new.dropna(subset=["value"])
    if valid.empty:
        return "empty response (no numeric observations)"
    if stored is None or stored.empty:
        return None
    stored_valid = stored.dropna(subset=["value"])
    if len(valid) < len(stored_valid):
        return f"fewer rows than stored file ({len(valid)} < {len(stored_valid)})"
    if valid["date"].max() < stored_valid["date"].max():
        return (f"latest date {valid['date'].max():%Y-%m-%d} is earlier than stored "
                f"{stored_valid['date'].max():%Y-%m-%d}")
    return None


def sanity_check(spec: dict, df: pd.DataFrame, display_start: pd.Timestamp) -> dict:
    rule = spec.get("sanity")
    if not rule:
        return {"rule": None, "passed": True, "detail": "no rule"}
    col = rule.get("column", "value")
    sub = df[df["date"] >= display_start]
    vals = sub[col].dropna() if col in sub else pd.Series(dtype=float)
    if vals.empty:
        return {"rule": rule, "passed": False, "detail": f"no values in column {col}"}
    if rule["type"] == "range":
        bad = sub[(sub[col] < rule["min"]) | (sub[col] > rule["max"])]
        desc = f"{col} between {rule['min']} and {rule['max']}"
    elif rule["type"] == "multiple_of":
        step = rule["step"]
        ratio = sub[col] / step
        bad = sub[(ratio - ratio.round()).abs() > 1e-6]
        desc = f"{col} is a multiple of {step}"
    else:
        return {"rule": rule, "passed": False, "detail": f"unknown rule {rule['type']}"}
    bad = bad.dropna(subset=[col])
    if bad.empty:
        return {"rule": desc, "passed": True,
                "detail": f"{len(vals)} values checked, min {vals.min():.4g}, max {vals.max():.4g}"}
    first = bad.iloc[0]
    return {"rule": desc, "passed": False,
            "detail": f"{len(bad)} of {len(vals)} values fail; first {first['date']:%Y-%m-%d} = {first[col]}"}


# --------------------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------------------
def write_csv(df: pd.DataFrame, path: Path) -> None:
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    for c in out.columns:
        if c.endswith("_date") and c != "date":
            out[c] = pd.to_datetime(out[c]).dt.strftime("%Y-%m-%d")
    out.to_csv(path, index=False, float_format="%.12g", na_rep="")


def latest(df: pd.DataFrame, col: str = "value") -> tuple[str | None, float | None]:
    v = df.dropna(subset=[col]) if col in df else pd.DataFrame()
    if v.empty:
        return None, None
    row = v.iloc[-1]
    return f"{row['date']:%Y-%m-%d}", float(row[col])


def append_log(row: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict] = []
    header: list[str] = []
    if LOG_PATH.exists():
        with LOG_PATH.open(newline="") as f:
            reader = csv.DictReader(f)
            header = list(reader.fieldnames or [])
            existing = list(reader)
    new_cols = [c for c in row if c not in header]
    if new_cols or not header:
        header = header + new_cols
        with LOG_PATH.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=header)
            w.writeheader()
            for r in existing:
                w.writerow(r)
            w.writerow(row)
    else:
        with LOG_PATH.open("a", newline="") as f:
            csv.DictWriter(f, fieldnames=header).writerow(row)


def clean_json(obj):
    """Make an object strict-JSON safe (NaN/inf -> null, numpy/pandas scalars -> Python)."""
    if isinstance(obj, dict):
        return {str(k): clean_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean_json(v) for v in obj]
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        obj = obj.item()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, (pd.Timestamp, datetime, date)):
        return obj.isoformat()
    return obj


def fmt_time(ts: datetime) -> dict:
    return {"utc": ts.strftime("%Y-%m-%d %H:%M"), "mexico_city": ts.astimezone(MX_TZ).strftime("%Y-%m-%d %H:%M")}


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------
@dataclass
class Result:
    key: str
    status: str = "pending"  # ok | kept_previous | failed
    reason: str = ""
    title: str = ""
    frame: pd.DataFrame | None = None
    fetched_at: str = ""
    sanity: dict = field(default_factory=dict)


def fetch_one(spec: dict, start: str, end: str) -> tuple[pd.DataFrame, str]:
    src, sid = spec["source"], str(spec["id"])
    title = ""
    if src == "banxico":
        title = fetch_banxico_title(sid)
        must = spec.get("title_must_contain")
        if must and must.lower() not in title.lower():
            raise FetchError(f"series title '{title}' does not contain '{must}': ID may have changed")
        return fetch_banxico(sid, start, end), title
    if src == "inegi":
        return fetch_inegi(sid, spec["frequency"]), title
    if src == "fred":
        return fetch_fred(sid, start), sid
    raise FetchError(f"unknown source {src}")


def main() -> int:
    for env in ("BANXICO_TOKEN", "INEGI_TOKEN"):
        _SECRETS.append(os.environ.get(env, ""))
    cfg = yaml.safe_load(CONFIG_PATH.read_text())
    settings = cfg["settings"]
    start = settings["fetch_start"]
    display_start = pd.Timestamp(settings["display_start"])
    end = date.today().isoformat()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    run_started = datetime.now(timezone.utc)
    prev_meta = json.loads(METADATA_PATH.read_text()) if METADATA_PATH.exists() else {}
    prev_series = prev_meta.get("series", {})

    results: dict[str, Result] = {}
    frames: dict[str, pd.DataFrame] = {}
    for spec in cfg["series"]:
        key = spec["key"]
        r = Result(key=key)
        stored = read_stored(key)
        try:
            new, r.title = fetch_one(spec, start, end)
            new = new[new["date"] >= pd.Timestamp(start)]
            reason = suspicious_reason(new, stored)
            if reason:
                raise FetchError(f"suspicious data: {reason}")
            r.status, r.frame = "ok", new
            r.fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except FetchError as e:
            r.reason = redact(str(e))
            if stored is not None and not stored.dropna(subset=["value"]).empty:
                r.status, r.frame = "kept_previous", stored
                r.fetched_at = prev_series.get(key, {}).get("fetched_at_utc", "")
                r.title = prev_series.get(key, {}).get("source_title", "")
            else:
                r.status = "failed"
        except Exception as e:  # noqa: BLE001 - never let one series kill the run
            r.reason = redact(f"unexpected {type(e).__name__}: {e}")
            if stored is not None and not stored.dropna(subset=["value"]).empty:
                r.status, r.frame = "kept_previous", stored
                r.fetched_at = prev_series.get(key, {}).get("fetched_at_utc", "")
            else:
                r.status = "failed"
        if r.frame is not None:
            full = transform(key, spec, r.frame, display_start)
            r.sanity = sanity_check(spec, full, display_start)
            frames[key] = full
            if r.status == "ok":
                write_csv(full, DATA_DIR / f"{key}.csv")
        results[key] = r

    derived, extra = derive(frames, display_start)
    for dkey, ddf in derived.items():
        write_csv(ddf, DATA_DIR / f"{dkey}.csv")

    # ---------------- metadata ----------------
    series_meta: dict = {}
    for spec in cfg["series"]:
        key = spec["key"]
        r = results[key]
        df = frames.get(key)
        main_col = {"gdp": "value", "rer_banxico": "rebased_avg"}.get(key, "value")
        ld, lv = latest(df, main_col) if df is not None else (None, None)
        series_meta[key] = {
            "id": str(spec["id"]),
            "name": spec["name"],
            "source": {"banxico": "Banco de México (SIE)", "inegi": "INEGI (BIE)",
                       "fred": "Federal Reserve Bank of St. Louis (FRED)"}[spec["source"]],
            "source_code": spec["source"],
            "source_url": spec["source_url"],
            "source_title": r.title,
            "units": spec["units"],
            "frequency": spec["frequency"],
            "seasonal_adjustment": spec["seasonal_adjustment"],
            "transformation": spec["transformation"],
            "latest_observation_date": ld,
            "latest_value": lv,
            "rows": int(df["value"].notna().sum()) if df is not None else 0,
            "fetch_status": r.status,
            "fetch_error": r.reason or None,
            "sanity_check": r.sanity,
            "fetched_at_utc": r.fetched_at or None,
        }
    derived_meta: dict = {}
    for d in cfg.get("derived", []):
        ddf = derived.get(d["key"])
        col = {"policy_spread": "spread", "rer": "rer_own_idx", "yields": "spread",
               "yields_monthly": "ust10_avg"}[d["key"]]
        ld, lv = latest(ddf, col) if ddf is not None else (None, None)
        derived_meta[d["key"]] = {**{k: d[k] for k in ("name", "units", "frequency", "inputs", "transformation")},
                                  "latest_observation_date": ld, "latest_value": lv,
                                  "rows": int(len(ddf)) if ddf is not None else 0}
    derived_meta.setdefault("rer", {}).update(extra)

    failed = [{"key": k, "id": series_meta[k]["id"], "status": r.status, "reason": r.reason,
               "last_good_observation_date": series_meta[k]["latest_observation_date"],
               "last_good_fetch_utc": series_meta[k]["fetched_at_utc"]}
              for k, r in results.items() if r.status != "ok"]
    hard_failed = [f for f in failed if f["status"] == "failed"]
    overall = "success" if not failed else ("success_with_kept_series" if not hard_failed else "partial_failure")

    run_finished = datetime.now(timezone.utc)
    last_success = prev_meta.get("last_successful_refresh_utc")
    last_success_mx = prev_meta.get("last_successful_refresh_mexico_city")
    if not hard_failed:
        last_success = fmt_time(run_finished)["utc"]
        last_success_mx = fmt_time(run_finished)["mexico_city"]

    meta = {
        "title": "Mexico macro dashboard",
        "last_successful_refresh_utc": last_success,
        "last_successful_refresh_mexico_city": last_success_mx,
        "run_started_utc": fmt_time(run_started)["utc"],
        "run_finished_utc": fmt_time(run_finished)["utc"],
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_url": (f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/"
                    f"{os.environ['GITHUB_RUN_ID']}" if os.environ.get("GITHUB_RUN_ID")
                    and os.environ.get("GITHUB_REPOSITORY") else None),
        "repository": os.environ.get("GITHUB_REPOSITORY"),
        "overall_status": overall,
        "failed_series": failed,
        "display_start": settings["display_start"],
        "series": series_meta,
        "derived": derived_meta,
    }
    METADATA_PATH.write_text(json.dumps(clean_json(meta), indent=2, ensure_ascii=False, allow_nan=False) + "\n")

    # ---------------- log ----------------
    log_row = {"run_timestamp_utc": fmt_time(run_started)["utc"], "run_id": meta["run_id"] or "local",
               "overall_status": overall}
    for spec in cfg["series"]:
        k = spec["key"]
        log_row[f"{k}_status"] = results[k].status
        log_row[f"{k}_latest"] = series_meta[k]["latest_observation_date"] or ""
    append_log(log_row)

    # ---------------- stdout summary ----------------
    print(f"\nMexico macro dashboard refresh — run started {meta['run_started_utc']} UTC")
    print(f"{'key':<16}{'id':<10}{'status':<15}{'latest':<12}{'value':>12}  {'rows':>5}  sanity  note")
    print("-" * 100)
    for k, m in series_meta.items():
        val = "" if m["latest_value"] is None else f"{m['latest_value']:.4f}"
        sanity = "pass" if m["sanity_check"].get("passed") else ("FAIL" if m["sanity_check"] else "-")
        note = (m["fetch_error"] or "")[:60]
        print(f"{k:<16}{m['id']:<10}{m['fetch_status']:<15}{m['latest_observation_date'] or '-':<12}"
              f"{val:>12}  {m['rows']:>5}  {sanity:<6}  {note}")
    print("-" * 100)
    for k, m in derived_meta.items():
        print(f"derived {k:<20} latest {m.get('latest_observation_date')}  rows {m.get('rows')}")
    if "rer_correlation" in derived_meta.get("rer", {}):
        print(f"RER correlation own vs Banxico: {derived_meta['rer']['rer_correlation']} "
              f"(n={derived_meta['rer']['rer_correlation_n']})")
    print(f"Overall status: {overall}. Last successful refresh: {last_success} UTC")
    # Exit 0 so the workflow can still commit the log; the workflow checks overall_status afterwards.
    return 0


if __name__ == "__main__":
    sys.exit(main())
