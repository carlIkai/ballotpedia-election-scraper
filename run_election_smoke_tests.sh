#!/usr/bin/env bash
set -euo pipefail

# -----------------------------
# State scope — odd-year statewide (VA 2025)
# Exercises: state discovery, statewide office URL→label, general parsing
# -----------------------------
echo "=== VA 2025 general (gov/ltgov/ag) ==="
python -m bp_scraper.cli.main \
  --year 2025 --scope state --state VA \
  --offices governor,lt_governor,attorney_general \
  --verbose

# -----------------------------
# State scope — odd-year statewide primaries (VA 2025)
# Exercises: primary sections, party labeling, winner/advancers logic
# -----------------------------
echo "=== VA 2025 primary (gov/ltgov/ag) ==="
python -m bp_scraper.cli.main \
  --year 2025 --scope state --state VA \
  --offices governor,lt_governor,attorney_general \
  --primary --verbose

# -----------------------------
# State scope — combined ticket pages (NJ 2025)
# Exercises: NJ's combined Governor/Lt Governor page label handling
# -----------------------------
echo "=== NJ 2025 general (governor) ==="
python -m bp_scraper.cli.main \
  --year 2025 --scope state --state NJ \
  --offices governor \
  --verbose

echo "=== NJ 2025 primary (governor) ==="
python -m bp_scraper.cli.main \
  --year 2025 --scope state --state NJ \
  --offices governor \
  --primary --verbose

# -----------------------------
# State scope — jungle-ish / table fallbacks (LA 2023)
# Exercises: relaxed table detection + sweep fallback, nonpartisan labeling
# -----------------------------
echo "=== LA 2023 general (governor) ==="
python -m bp_scraper.cli.main \
  --year 2023 --scope state --state LA \
  --offices governor \
  --verbose

echo "=== LA 2023 primary (governor) ==="
python -m bp_scraper.cli.main \
  --year 2023 --scope state --state LA \
  --offices governor \
  --primary --verbose

# -----------------------------
# State scope — top-two primaries (WA 2024)
# Exercises: Nonpartisan primary labeling + advancers/winner logic
# -----------------------------
echo "=== WA 2024 primary (governor + ltgov) ==="
python -m bp_scraper.cli.main \
  --year 2024 --scope state --state WA \
  --offices governor,lt_governor \
  --primary --verbose

# -----------------------------
# State scope — CA primary (completed cycle)
# Exercises: CA top-two style primaries on a completed cycle (not future)
# NOTE: If your discovery doesn’t include this page for some reason, you’ll see 0 pages.
# -----------------------------
echo "=== CA 2022 primary (governor) ==="
python -m bp_scraper.cli.main \
  --year 2022 --scope state --state CA \
  --offices governor \
  --primary --verbose

# -----------------------------
# Federal scope — Senate general single-state
# Exercises: federal discovery + single-state filter, senate parsing
# -----------------------------
echo "=== US Senate 2024 general (VA only) ==="
python -m bp_scraper.cli.main \
  --year 2024 --scope federal --chamber senate --state VA \
  --verbose

# -----------------------------
# Federal scope — House general single-state
# Exercises: House state page discovery → district discovery → per-district scrape
# -----------------------------
echo "=== US House 2024 general (VA only) ==="
python -m bp_scraper.cli.main \
  --year 2024 --scope federal --chamber house --state VA \
  --verbose

# -----------------------------
# Federal scope — House primary mapping/runoff stress test (TX 2024)
# Exercises: primary parsing, runoff labeling, house mapping/join id plumbing
# -----------------------------
echo "=== US House 2024 primary (TX only) ==="
python -m bp_scraper.cli.main \
  --year 2024 --scope federal --chamber house --state TX \
  --primary --verbose

echo "ALL SMOKE TESTS COMPLETED"
