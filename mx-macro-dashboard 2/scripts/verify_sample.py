#!/usr/bin/env python3
"""Print check values for every series and (re)generate VERIFICATION.md.

For each source series it reports the latest raw observation and one fixed older observation
(settings.verification_date in config/series.yaml, or the nearest available observation), with
a URL where a person can look the value up by hand.

VERIFICATION.md keeps every row in which a person has filled in "Source value", "Match?" or
"Checked on": those rows are never rewritten. Unfilled rows are regenerated on every run.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = yaml.safe_load((ROOT / "config" / "series.yaml").read_text())
DATA_DIR = ROOT / "docs" / "data"
VERIFICATION = ROOT / "VERIFICATION.md"
README = ROOT / "README.md"

COLUMNS = ["Series", "Source & ID", "Observation date", "Dashboard value", "Source value",
           "Match?", "Checked on", "Notes"]
MANUAL = ("Source value", "Match?", "Checked on")
TABLE_START = "<!-- VERIFICATION_TABLE_START -->"
TABLE_END = "<!-- VERIFICATION_TABLE_END -->"

HOW_TO_CHECK = {
    "banxico": "Open the link, find the series row (ID shown in the series metadata) and read the value for this date. "
               "Daily FIX values can also be checked on Banxico's FIX history page.",
    "inegi": "The link opens INEGI's BIE with the indicator selected; open the table view and read this period.",
    "fred": "Open the link, click 'View All' or download the CSV, and read this date.",
}


def load_meta() -> dict:
    p = DATA_DIR / "metadata.json"
    return json.loads(p.read_text()) if p.exists() else {}


def pick_fixed(df: pd.DataFrame, target: pd.Timestamp, frequency: str) -> pd.Series | None:
    v = df.dropna(subset=["value"])
    if v.empty:
        return None
    if frequency in ("daily", "business-daily"):
        after = v[v["date"] >= target]
        return after.iloc[0] if len(after) else v.iloc[-1]
    if frequency in ("monthly", "quarterly"):
        hit = v[v["date"] == target]
        if len(hit):
            return hit.iloc[0]
    # auction or missing period: nearest observation in time
    idx = (v["date"] - target).abs().idxmin()
    return v.loc[idx]


def fmt_value(x: float) -> str:
    return f"{x:,.4f}".rstrip("0").rstrip(".") if abs(x) < 1e6 else f"{x:,.1f}"


def note_for(key: str, row: pd.Series, freq: str) -> str:
    parts = []
    if key == "gdp":
        parts.append(f"Quarter {row['date'].year}Q{(row['date'].month - 1) // 3 + 1}")
        if pd.notna(row.get("yoy")):
            parts.append(f"dashboard y/y {row['yoy']:.2f}%")
    elif key in ("igae", "inpc", "inpc_core") and pd.notna(row.get("yoy")):
        parts.append(f"dashboard y/y {row['yoy']:.2f}%")
    elif key == "fix":
        parts.append("FIX dated by determination date")
    elif key == "mbono10":
        parts.append("auction date")
    elif key == "rer_banxico" and pd.notna(row.get("rebased_2015")):
        parts.append(f"dashboard rebased 2015=100: {row['rebased_2015']:.2f}")
    if freq == "monthly":
        parts.append(f"month {row['date']:%Y-%m}")
    return "; ".join(parts)


def build_rows(meta: dict) -> tuple[list[dict], list[str]]:
    target = pd.Timestamp(CONFIG["settings"]["verification_date"])
    rows, printed = [], []
    for spec in CONFIG["series"]:
        key, freq = spec["key"], spec["frequency"]
        path = DATA_DIR / f"{key}.csv"
        src_label = {"banxico": "Banxico SIE", "inegi": "INEGI BIE", "fred": "FRED"}[spec["source"]]
        url = spec["source_url"]
        if not path.exists():
            printed.append(f"{key:<15} {spec['id']:<9} NO DATA FILE")
            continue
        df = pd.read_csv(path, parse_dates=["date"])
        valid = df.dropna(subset=["value"])
        if valid.empty:
            printed.append(f"{key:<15} {spec['id']:<9} NO OBSERVATIONS")
            continue
        latest = valid.iloc[-1]
        fixed = pick_fixed(df, target, freq)
        status = meta.get("series", {}).get(key, {}).get("fetch_status", "unknown")
        for label, row in (("latest", latest), ("fixed", fixed)):
            if row is None:
                continue
            note = note_for(key, row, freq)
            if label == "fixed":
                note = ("fixed check; " + note).strip("; ")
            if status != "ok":
                note += f"; fetch status {status}"
            rows.append({
                "Series": spec["name"],
                "Source & ID": f"[{src_label} {spec['id']}]({url})",
                "Observation date": f"{row['date']:%Y-%m-%d}",
                "Dashboard value": fmt_value(float(row["value"])),
                "Source value": "", "Match?": "", "Checked on": "",
                "Notes": note,
            })
        printed.append(
            f"{key:<15} {spec['id']:<9} latest {latest['date']:%Y-%m-%d} = {fmt_value(float(latest['value'])):>14}"
            f" | fixed {fixed['date']:%Y-%m-%d} = {fmt_value(float(fixed['value'])):>14} | {url}")
    return rows, printed


def parse_existing() -> list[dict]:
    if not VERIFICATION.exists():
        return []
    text = VERIFICATION.read_text()
    if TABLE_START not in text:
        return []
    block = text.split(TABLE_START, 1)[1].split(TABLE_END, 1)[0]
    out = []
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|") or set(line) <= set("|-: "):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells[0] == "Series" or len(cells) != len(COLUMNS):
            continue
        out.append(dict(zip(COLUMNS, cells)))
    return out


def merge(generated: list[dict], existing: list[dict]) -> list[dict]:
    def k(r):
        return (r["Source & ID"], r["Observation date"])
    filled = [r for r in existing if any(r[c] for c in MANUAL)]
    filled_keys = {k(r) for r in filled}
    merged = filled + [r for r in generated if k(r) not in filled_keys]
    order = {r["Series"]: i for i, r in enumerate(generated)}
    return sorted(merged, key=lambda r: (order.get(r["Series"], 999), r["Observation date"]))


def render_table(rows: list[dict]) -> str:
    lines = ["| " + " | ".join(COLUMNS) + " |", "|" + "|".join("---" for _ in COLUMNS) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(r[c].replace("|", "/") for c in COLUMNS) + " |")
    return "\n".join(lines)


def sanity_section(meta: dict) -> str:
    lines = [f"Run started {meta.get('run_started_utc', '?')} UTC; overall status "
             f"**{meta.get('overall_status', '?')}**; run ID {meta.get('run_id') or 'local'}.", "",
             "| Series | ID | Fetch status | Latest observation | Sanity rule | Result | Detail |",
             "|---|---|---|---|---|---|---|"]
    for key, m in meta.get("series", {}).items():
        sc = m.get("sanity_check") or {}
        res = "pass" if sc.get("passed") else "**FAIL**"
        lines.append(f"| {key} | {m['id']} | {m['fetch_status']} | {m.get('latest_observation_date') or '-'} | "
                     f"{sc.get('rule') or '-'} | {res} | {sc.get('detail', '')} |")
    failed = meta.get("failed_series") or []
    if failed:
        lines += ["", "Series not refreshed in this run (previous data kept where available):", ""]
        for f in failed:
            lines.append(f"- `{f['key']}` ({f['id']}): {f['status']} — {f['reason']} "
                         f"(last good observation {f.get('last_good_observation_date') or 'none'})")
    rer = meta.get("derived", {}).get("rer", {})
    if "rer_correlation" in rer:
        lines += ["", f"Real exchange rate cross-check: correlation between the own calculation and Banxico's "
                      f"SR1504 (both 2015 = 100) is **{rer['rer_correlation']:.3f}** over {rer['rer_correlation_n']} "
                      f"months ({rer['rer_correlation_period']})."]
    return "\n".join(lines)


def write_verification(rows: list[dict], meta: dict) -> None:
    text = f"""# Verification

This file is regenerated by `scripts/verify_sample.py` after every refresh.

**How to use it.** For each row, open the source link, look up the value for the observation date, and fill in
**Source value**, **Match?** (yes/no) and **Checked on** (YYYY-MM-DD) by hand. Rows you have filled in are never
overwritten; unfilled rows are regenerated with the latest observation each run.

"Dashboard value" is the **raw** value stored in `docs/data/<key>.csv` (column `value`), before any transformation,
so it can be compared directly with the source. Transformed values (y/y %, rebased indices) are in the Notes.

How to check each source:

- **Banxico SIE**: {HOW_TO_CHECK['banxico']}
- **INEGI BIE**: {HOW_TO_CHECK['inegi']}
- **FRED**: {HOW_TO_CHECK['fred']}

## Check table

{TABLE_START}
{render_table(rows)}
{TABLE_END}

## Automated sanity checks (latest run)

{sanity_section(meta)}
"""
    VERIFICATION.write_text(text)


def update_readme(meta: dict) -> None:
    if not README.exists():
        return
    rer = meta.get("derived", {}).get("rer", {})
    start, end = "<!-- RER_CORRELATION_START -->", "<!-- RER_CORRELATION_END -->"
    text = README.read_text()
    if start not in text or end not in text:
        return
    if "rer_correlation" in rer:
        body = (f"Latest run: correlation between the own RER and Banxico's SR1504, both rebased to 2015 = 100, is "
                f"**{rer['rer_correlation']:.3f}** ({rer['rer_correlation_n']} months, {rer['rer_correlation_period']}). "
                f"Updated automatically by `scripts/verify_sample.py`.")
    else:
        body = "Not computed yet: it appears here after the first refresh that has both series."
    new = re.sub(re.escape(start) + r".*?" + re.escape(end), f"{start}\n{body}\n{end}", text, flags=re.S)
    README.write_text(new)


def main() -> int:
    meta = load_meta()
    generated, printed = build_rows(meta)
    rows = merge(generated, parse_existing())
    write_verification(rows, meta)
    update_readme(meta)
    print("\nVerification sample (raw values; check these against the source by hand)")
    print("-" * 110)
    for line in printed:
        print(line)
    print("-" * 110)
    print(f"VERIFICATION.md written with {len(rows)} rows "
          f"({sum(1 for r in rows if any(r[c] for c in MANUAL))} filled in by hand and preserved).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
