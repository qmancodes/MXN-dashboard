# Mexico macro dashboard

A static dashboard that answers one question:

> **How are Mexico's growth, inflation and labor market doing, and how are Banxico's policy and the peso reacting relative to the US Federal Reserve?**

Data come only from official primary sources: **Banco de México (SIE API)**, **INEGI (BIE API)** and the **Federal Reserve via FRED**. A GitHub Actions workflow refreshes the data every weekday, commits it, and GitHub Pages serves the page from `/docs`. Every run leaves an audit trail in `logs/refresh_log.csv`, `docs/data/metadata.json` and `VERIFICATION.md`.

## Repository layout

```
config/series.yaml          every series: id, source, URL, units, frequency, SA, transformation, sanity rule
scripts/fetch_data.py       fetch, validate, transform, write CSVs + metadata + log
scripts/verify_sample.py    prints check values and source URLs, fills VERIFICATION.md
docs/index.html, app.js, style.css   static front end for GitHub Pages (Plotly.js from CDN, no build step)
streamlit_app.py            the same dashboard as a Streamlit app (reads the same files)
.streamlit/config.toml      Streamlit theme and settings
docs/data/*.csv, docs/data/metadata.json
logs/refresh_log.csv
.github/workflows/refresh.yml
tests/mock_server.py, tests/run_offline_test.sh   offline test harness (synthetic data, never committed)
```

## Series

Sample: January 2015 to the latest observation. The CSVs also hold 2014 so that y/y changes and the 20-day moving average exist from January 2015; the page shows 2015 onward.

| Indicator | Source | ID | Units | Frequency | SA status | Transformation / convention |
|---|---|---|---|---|---|---|
| Real GDP | INEGI BIE | `736181` | Millions of pesos at 2018 prices | Quarterly | Seasonally adjusted | y/y = 100 × (GDP_t / GDP_{t−4} − 1); q/q = 100 × (GDP_t / GDP_{t−1} − 1), **not annualized** |
| IGAE (monthly proxy) | INEGI BIE | `737219` | Index 2018 = 100 | Monthly | Seasonally adjusted | y/y = 100 × (IGAE_t / IGAE_{t−12} − 1) |
| INPC headline | INEGI BIE | `910392` | Index, 2nd half of July 2018 = 100 | Monthly | Not SA | y/y = 100 × (INPC_t / INPC_{t−12} − 1) |
| INPC core | INEGI BIE | `910393` | Index, 2nd half of July 2018 = 100 | Monthly | Not SA | y/y = 100 × (core_t / core_{t−12} − 1) |
| Unemployment rate (ENOE) | INEGI BIE | `444884` | % of economically active population | Monthly | **Seasonally adjusted** | none; Apr–Jun 2020 left missing |
| Banxico target rate | Banxico SIE | `SF61745` | % per year | Daily | n/a | step line; decision = first day a new level is in force |
| Fed effective funds rate | FRED | `DFF` | % per year | Daily | Not SA | none |
| Fed target range lower / upper | FRED | `DFEDTARL` / `DFEDTARU` | % per year | Daily | Not SA | shaded range |
| Policy spread | computed | `policy_spread` | percentage points | Daily | n/a | Banxico target − Fed upper limit |
| FIX exchange rate | Banxico SIE | `SF43718` | MXN per 1 USD (**up = weaker peso**) | Business days | Not SA | 20-business-day moving average |
| Real exchange rate, Banxico | Banxico SIE | `SR1504` | Index (Banxico base) | Monthly | Not SA | rebased to 2015 average = 100; **up = real peso depreciation** |
| Real exchange rate, own | computed | `rer` | Index, 2015 = 100 | Monthly | n/a | RER_t = S_t × CPI_US_t / INPC_t, S_t = monthly mean FIX; **up = real peso depreciation** |
| US CPI | FRED | `CPIAUCSL` | Index 1982–84 = 100 | Monthly | Seasonally adjusted | input to own RER |
| Bono M 10-year | Banxico SIE | `SF44071` | % per year | Primary auction (≈ every 4 weeks) | n/a | auction-date points; monthly mean of auctions |
| US 10-year Treasury | FRED | `DGS10` | % per year | Business days | Not SA | monthly average for the long view |
| 10-year spread | computed | `yields` | percentage points | Auction dates | n/a | Bono M auction yield − DGS10 on the auction date (or last earlier US business day) |

Every chart footer shows units, frequency, source link, series ID, latest observation date, seasonal adjustment and the transformation formula, read from `docs/data/metadata.json`.

### Series verification and changes from the spec

All IDs were checked in the official catalogs on 2026-11-24:

- **Confirmed as given:** `SF43718` (FIX, date of determination), `SF61745` (target rate), `DFF`, `DFEDTARL`, `DFEDTARU`, `CPIAUCSL` (SA, 1982–84 = 100), `DGS10`.
- **INEGI IDs found in BIE:** `736181` (GDP, 2018 prices, path *Series desestacionalizadas y tendencia-ciclo › A precios de 2018 › Total › Serie desestacionalizada*); `737219` (IGAE, *Series desestacionalizadas › Total › Serie desestacionalizada*, 2018 = 100); `910392` / `910393` (INPC headline / core *Subyacente total*, base 2Q July 2018, under INEGI's "Actualización de Canasta y Ponderadores 2024" tree; history back to 1969); `444884` (ENOE monthly, *Series desestacionalizadas › Nacional › Tasa de desocupación › Total › Serie desestacionalizada*).
- IDs I rejected after checking: `735879` is GDP *not* seasonally adjusted; `737121` is IGAE original figures; `444612` is the unemployment rate for 32 urban areas, not national; `444603` is the national rate *not* seasonally adjusted.
- **10-year M Bono (deviation):** Banxico SIE publishes **no daily 10-year yield**. Its daily "Vector de precios" table (CF300) has only prices and coupons by maturity bucket. The dashboard therefore uses `SF44071`, the 10-year Bono M weighted yield at the primary auction (table CF107, auction results, roughly every four weeks), as the spec allows. Deriving a daily yield from bucket prices would change the series definition, so I didn't.
- **Real exchange rate:** Banxico publishes a bilateral consumer-price RER index with the US: `SR1504` in table CR183. Banxico states that an increase means depreciation. Its index uses the FIX *by settlement date* (monthly average) and US CPI *not* seasonally adjusted. The own calculation follows the spec (FIX by determination date, `CPIAUCSL` SA), so the two will differ slightly.
- Banxico series titles are checked on every run against `title_must_contain` in `config/series.yaml`. If Banxico ever reassigns an ID, the series is held back instead of plotted.

### Real exchange rate: correlation of the two versions

<!-- RER_CORRELATION_START -->
Not computed yet: it appears here after the first refresh that has both series.
<!-- RER_CORRELATION_END -->

## Streamlit app

`streamlit_app.py` shows the same seven charts, KPI tiles, footers, refresh timestamp and failed-series warning as the GitHub Pages site, plus a data table and CSV download under each chart. It **reads** `docs/data/*.csv` and `metadata.json`; it never calls the APIs and needs no tokens. The GitHub Actions refresh stays the only thing that fetches data, so the log and `VERIFICATION.md` still describe exactly what the app shows.

Run it locally (after at least one refresh has written data):

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py           # opens http://localhost:8501
```

Deploy on Streamlit Community Cloud (free):

1. Sign in at <https://share.streamlit.io> with your GitHub account.
2. **Create app → Deploy a public app from GitHub**. Pick this repository, branch `main`, main file `streamlit_app.py`.
3. Under **Advanced settings**, choose Python **3.11**. No secrets are needed.
4. Deploy. Every commit the refresh workflow pushes updates the app automatically, and the app reloads the data files when they change.

The GitHub Pages site keeps working alongside it. You can use either or both.

## Getting the API tokens

- **Banxico SIE token:** request it at <https://www.banxico.org.mx/SieAPIRest/service/v1/token> (free, by email). The script sends it in the `Bmx-Token` header.
- **INEGI token:** request it from the INEGI Indicators API page, <https://www.inegi.org.mx/servicios/api_indicadores.html> (link "Token"; free, by email). The script puts it in the request URL, as INEGI requires.
- **FRED** needs no key: the script reads `https://fred.stlouisfed.org/graph/fredgraph.csv?id=<ID>`.

### Add them as repository secrets

GitHub repository → **Settings → Secrets and variables → Actions → New repository secret**. Create `BANXICO_TOKEN` and `INEGI_TOKEN`. Tokens are only read from environment variables; they are never printed (error messages are redacted), written to files, or committed. `.env` is in `.gitignore`.

## Run locally

```bash
pip install -r requirements.txt          # Python 3.11
cp .env.example .env                     # fill in both tokens
set -a; source .env; set +a              # export them as environment variables
python scripts/fetch_data.py             # writes docs/data/*.csv, metadata.json, a log row
python scripts/verify_sample.py          # writes VERIFICATION.md
python -m http.server -d docs            # open http://localhost:8000
```

### Offline test (no tokens or network needed)

`PYTHON=python3.11 tests/run_offline_test.sh` runs the whole pipeline three times against a local mock of the three APIs, in a temporary copy of the repo: (1) all sources healthy; (2) HTTP 500, an empty response, a truncated file and a changed series title; (3) missing tokens and a series with no stored file. The mock data are **synthetic** and never touch `docs/data/`.

## Automation and GitHub Pages

`.github/workflows/refresh.yml` runs on weekdays at **19:30 UTC** (13:30 in Mexico City, after Banxico publishes the FIX at about 12:00), and on demand via **Actions → Refresh dashboard data → Run workflow**. Concurrency is set so two runs never overlap. Each run fetches, builds `VERIFICATION.md`, then commits `docs/data/`, `logs/`, `VERIFICATION.md` and `README.md` (the RER correlation line) as `github-actions[bot]` with the message `data refresh YYYY-MM-DD HH:MM UTC`. It always commits, because the refresh timestamp and the log change every run. A run with no new source data still counts as a success.

Run outcomes (`overall_status` in `metadata.json`):

| Status | Meaning | Refresh timestamp | Actions run |
|---|---|---|---|
| `success` | every series fetched and passed validation | updated | green |
| `success_with_kept_series` | some series failed or looked suspicious, so their previous CSV was kept, with the reason logged | updated | green, with a warning |
| `partial_failure` | a series failed **and** had no previous data | **not** updated | red (after committing the log) |

"Suspicious" means empty, fewer rows than the stored file, or a latest date earlier than the stored one. Sanity checks (target rate is a multiple of 0.25, FIX within 10–30, inflation within −5 to 30, unemployment within 0–15, yields within 0–20) are logged per series in `metadata.json` and `VERIFICATION.md`.

**Enable Pages:** repository → **Settings → Pages → Build and deployment → Source: Deploy from a branch → Branch: `main` (or your default), folder `/docs`** → Save. The site appears at `https://<user>.github.io/<repo>/`. `docs/.nojekyll` is included so Pages serves the files as they are. The workflow pushes with the default `GITHUB_TOKEN`; if your organization restricts it, allow **Settings → Actions → General → Workflow permissions → Read and write**.

## Capturing refresh evidence

1. **Green Actions run:** open Actions → *Refresh dashboard data* → the run → screenshot the green check and the summary table printed by `fetch_data.py` in the "Fetch data" step.
2. **Matching log row:** open `logs/refresh_log.csv` at the commit the run made (`data refresh …`) and copy the last row. Its `run_id` equals the number in the Actions run URL.
3. **Live page timestamp:** after Pages redeploys (a minute or two), screenshot the header box *Last successful refresh: YYYY-MM-DD HH:MM UTC (HH:MM Mexico City)*. It must match `last_successful_refresh_utc` in `docs/data/metadata.json` for that commit.

For value checks, fill in the "Source value", "Match?" and "Checked on" columns of `VERIFICATION.md` by hand. Rows you fill in are never overwritten by later runs.

## Known caveats

- **GDP and IGAE revisions:** INEGI revises past quarters and months (and re-estimates seasonal factors), so values can change between refreshes. The fail-safe compares row counts and dates, not values, so revisions flow through.
- **2020 ENOE gap:** INEGI suspended the ENOE in April–June 2020 and ran the ETOE telephone survey instead. Those months are forced to missing and shown as a gap; they are never interpolated.
- **FIX dating:** Banxico dates the FIX by the day it is determined (`SF43718`). It is published in the Diario Oficial the next banking day and used for payments the day after; the settlement-date series is `SF60653`.
- **Real exchange rate base and direction:** both RER lines are rebased to the 2015 average = 100. Up = real depreciation of the peso (Mexico relatively cheaper). The own calculation mixes US CPI SA with Mexican INPC NSA, as the spec asks, so it carries a small seasonal pattern that Banxico's index does not.
- **INPC basket update:** the INPC IDs are INEGI's series with the 2024 basket and weights update (same base, 2Q July 2018 = 100).
- **10-year Bono M:** auction yields, not a daily market yield (see above). The US 10-year is plotted as a monthly average of daily yields for the long view; the spread uses the US daily yield on each auction date.
- **Running month:** the own RER drops the current month until it is complete.
