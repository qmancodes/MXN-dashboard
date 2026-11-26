#!/usr/bin/env bash
# Offline end-to-end test of fetch_data.py + verify_sample.py against tests/mock_server.py.
# Runs in a throwaway copy of the repo so no synthetic data ever lands in docs/data.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3}"
WORK="$(mktemp -d)"
cp -r "$REPO"/. "$WORK"/
rm -f "$WORK"/docs/data/*.csv "$WORK"/docs/data/metadata.json "$WORK"/logs/refresh_log.csv "$WORK"/VERIFICATION.md
PORT=${PORT:-8765}
start_mock() { "$PY" "$REPO/tests/mock_server.py" "$PORT" "$@" & MOCK=$!; sleep 1; }
stop_mock() { kill "$MOCK" 2>/dev/null || true; wait "$MOCK" 2>/dev/null || true; }
export BANXICO_BASE_URL="http://127.0.0.1:$PORT/banxico" INEGI_BASE_URL="http://127.0.0.1:$PORT/inegi" \
       FRED_CSV_URL="http://127.0.0.1:$PORT/fred" BANXICO_TOKEN=test-banxico-token INEGI_TOKEN=test-inegi-token \
       FETCH_FAST_RETRY=1
cd "$WORK"
echo "== Run 1: all sources healthy"; start_mock; "$PY" scripts/fetch_data.py; "$PY" scripts/verify_sample.py; stop_mock
echo "== Run 2: broken sources (500, empty, truncated, wrong title)"
start_mock --break fix inpc DGS10 SF61745-title; "$PY" scripts/fetch_data.py; stop_mock
echo "== Run 3: missing tokens, no stored file for one series"
rm -f docs/data/igae.csv
start_mock; BANXICO_TOKEN= INEGI_TOKEN= "$PY" scripts/fetch_data.py; stop_mock
echo "WORKDIR=$WORK"
