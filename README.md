# bp_scraper

A Python scraper for extracting and normalizing U.S. federal and state election results from Ballotpedia.

The project is built around a simple pipeline:
1. Discover election result pages
2. Scrape and parse results into flat race/candidate records
3. Normalize and dedupe records
4. Build unified `PositionElection` objects for downstream use

---

## Status

This project is under active development. Ballotpedia markup changes over time, so parsing logic is designed with multiple fallbacks and conservative validation.

---

## Key Features

- Scrapes **federal elections** (U.S. Senate and U.S. House)
- Scrapes **state elections** (Governor, Lieutenant Governor, Attorney General, State House, State Senate)
- Supports **general elections, primaries, runoffs, and special elections**
- Handles non-uniform markup with:
  - section detection fallbacks
  - results table parsing fallbacks
  - candidate card fallbacks
- Produces:
  - raw race and candidate outputs (CSV + JSON)
  - unified `PositionElection` outputs (CSV + JSON)
- Uses deterministic IDs suitable for joining and storage

---

## Requirements

- Python 3.10+ recommended
- Dependencies:
  - `requests`
  - `beautifulsoup4`
  - `lxml`
  - `pandas`

---

## Installation

### Option A: Virtual environment

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

### Option B: Conda

conda create -n bp_scraper python=3.10 -y
conda activate bp_scraper
pip install -r requirements.txt

---

## Quickstart

### Scrape federal Senate results (general elections)

```bash
python -m bp_scraper.cli.main \
  --year 2024 \
  --scope federal \
  --chamber senate \
  --verbose
```

### Scrape federal House results (district pages)

```bash
python -m bp_scraper.cli.main \
  --year 2024 \
  --scope federal \
  --chamber house
```

### Scrape state elections for a specific state

```bash
python -m bp_scraper.cli.main \
  --year 2024 \
  --scope state \
  --state VA \
  --offices governor,state_upper
```

### Scrape primaries instead of general elections

```bash
python -m bp_scraper.cli.main \
  --year 2024 \
  --primary
```

### Scrape a single page URL

```bash
python -m bp_scraper.cli.main \
  --year 2024 \
  --state-url "https://ballotpedia.org/United_States_Senate_election_in_Arizona,_2024" \
  --verbose
```

## CLI Reference

### Arguments

#### `--year`
Election year to scrape.

#### `--scope` (`federal` or `state`)
Controls whether discovery targets federal overview pages or state election overview pages.

#### `--chamber` (`senate` or `house`)
Used only when `--scope federal`.

#### `--state`
Limits discovery to one state. Accepts full names (e.g. `Virginia`) or USPS codes (e.g. `VA`).

#### `--offices`
State scope only. Comma-separated office keys:

- `governor`
- `lt_governor`
- `attorney_general`
- `state_lower`
- `state_upper`

#### `--primary`
Parses primary election sections rather than general election sections.

#### `--state-url`
Skips discovery and scrapes a single Ballotpedia URL.

#### `--outdir`
Output directory (default: `data/processed`).

#### `--stamp`
Optional `YYYYMMDD` stamp for output filenames. Defaults to the current date.

#### `--delay`, `--retries`
Request pacing and retry behavior.

#### `--house-mapping`
Path to a CSV mapping used to rewrite House primary labels into canonical forms for joining.

---

## Outputs

All outputs are written under `--outdir` (default: `data/processed`).

### Raw Outputs

- `races-YYYYMMDD.csv`
- `candidates-YYYYMMDD.csv`
- `races-YYYYMMDD.json`
- `candidates-YYYYMMDD.json`

These are intended to be:

- easily inspectable
- easy to load into pandas or SQL
- stable for debugging and regression tests

### Unified Outputs

- `{race-slug}-YYYYMMDD.json`
- `{race-slug}-YYYYMMDD.csv`

These contain structured `PositionElection` objects and a flattened row form.

---

## Output Schemas

### `races-*.csv` fields

- `state`
- `race`
- `year`
- `winner_name`
- `advancers`
- `is_jungle_primary`
- `primary_party`
- `source_url`

### `candidates-*.csv` fields

- `state`
- `race`
- `year`
- `name`
- `party`
- `total_votes`
- `incumbent`
- `is_winner`
- `is_advancer`
- `source_url`

---

## Unified `PositionElection` JSON Shape

Each object includes:

- `id`
- `isPrimary`
- `isRunoff`
- `isRecall`
- `isUnexpired`
- `seats`
- `election`
  - `id`
  - `name`
  - `electionDay`
  - `state`
- `position`
  - `id`
  - `name`
  - `level`
  - `state`
- `candidacies`
  - `id`
  - `withdrawn`
  - `result`
  - `candidate`
    - `id`
    - `fullName`

---

## Data Quality Notes

Ballotpedia sometimes:

- encodes party in CSS rather than text
- varies header naming (e.g. “General election results”, “Runoff election”)
- moves results into different table formats across states and years

The scraper uses conservative heuristics to avoid mixing “Past elections” tables with current-year results.

Primaries can be:

- partisan (separate Democratic and Republican sections)
- nonpartisan or jungle-style (advancers instead of a single winner)

---

## Troubleshooting

### I’m getting skipped pages

If `--verbose` logs show:
skipped (no YEAR MODE results)

This usually means the page does not contain a parseable results section for the requested mode (primary vs general), or Ballotpedia has not published results in the expected format.

Try:

- using `--state-url` for targeted debugging
- checking whether the page is a different year
- confirming the section is not under “Past elections”

### Requests are getting blocked

The HTTP layer includes:

- realistic headers
- delays with jitter
- retries with exponential backoff

If blocking still occurs:

- increase `--delay`
- reduce request frequency (the scraper is single-threaded)
- try again later

---

## Development

### Style

- Comments are short and direct, especially in constants and low-level helpers.
- Docstrings are used where they improve readability and maintenance.
- Orchestration stays in `cli/`.
- Parsing logic stays in `parsing/`.
- DataFrame transforms stay in `transform/`.

### Adding New Office Types

For state scope:

1. Update canonical URL patterns in `core/constants.py`
2. Update discovery mapping in `parsing/discovery.py`
3. Update race label inference in the scrape pipeline (`pipelines/scrape.py` or equivalent logic)

## Testing

This project includes a structured test suite built with `pytest`.

The test suite validates core parsing logic, normalization behavior, URL canonicalization, summarization logic, and integration-level scraping behavior.

#### Unit Tests

- Validate small, deterministic functions  
- Cover normalization helpers, URL canonicalization, summarization logic, and label parsing utilities  

#### Integration Tests

- Validate higher-level scraping behavior  
- Use controlled fixture HTML files  
- Ensure correct section detection, table parsing, and race summarization  

### Running Tests

Run the full test suite:

```bash
pytest -v
```

Run with coverage:

```bash
pytest --cov=bp_scraper --cov-report=term-missing
```

### Coverage

Coverage configuration is defined in `pyproject.toml`.

The suite is designed to:

- Protect parsing logic from regressions  
- Validate expected behavior across markup variations  
- Support safe refactoring of scraping and transformation logic  
