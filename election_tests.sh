#!/usr/bin/env bash
set -euo pipefail

# State scope — odd-year statewide (VA 2025)
echo "=== VA 2025 general (gov/ltgov/ag) ==="
python -m bp_scraper.cli.main \
  --year 2025 --scope state --state VA \
  --offices governor,lt_governor,attorney_general \
  --verbose

# State scope — odd-year statewide primaries (VA 2025)
echo "=== VA 2025 primary (gov/ltgov/ag) ==="
python -m bp_scraper.cli.main \
  --year 2025 --scope state --state VA \
  --offices governor,lt_governor,attorney_general \
  --primary --verbose

# State scope — combined ticket pages (NJ 2025)
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

# State scope — jungle-ish / table fallbacks (LA 2023)
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

# State scope — top-two primaries (WA 2024)
echo "=== WA 2024 primary (governor + ltgov) ==="
python -m bp_scraper.cli.main \
  --year 2024 --scope state --state WA \
  --offices governor,lt_governor \
  --primary --verbose

# State scope — CA primary (completed cycle)
echo "=== CA 2022 primary (governor) ==="
python -m bp_scraper.cli.main \
  --year 2022 --scope state --state CA \
  --offices governor \
  --primary --verbose

# Federal scope — Senate general single-state
echo "=== US Senate 2024 general (VA only) ==="
python -m bp_scraper.cli.main \
  --year 2024 --scope federal --chamber senate --state VA \
  --verbose

# Federal scope — House general single-state
echo "=== US House 2024 general (VA only) ==="
python -m bp_scraper.cli.main \
  --year 2024 --scope federal --chamber house --state VA \
  --verbose

# Federal scope — House primary mapping/runoff stress test (TX 2024)
echo "=== US House 2024 primary (TX only) ==="
python -m bp_scraper.cli.main \
  --year 2024 --scope federal --chamber house --state TX \
  --primary --verbose

echo "ALL SMOKE TESTS COMPLETED"
