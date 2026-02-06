from __future__ import annotations

"""
DataFrame cleanup helpers used by the CLI and pipelines.

This module provides small, deterministic transforms that make scraped output easier to
join, serialize, and diff across runs.

Key behaviors:
- dedupe candidates and races using stable grouping keys
- trim string whitespace to reduce noisy diffs
- serialize list-like columns (e.g., advancers) consistently for CSV/JSON output
- write CSV with explicit boolean formatting ("true"/"false") and empty-string nulls
"""

import json
from pathlib import Path
from typing import List, Optional

import pandas as pd

from bp_scraper.parsing.normalize import canonical_name
from bp_scraper.core.constants import USPS  # noqa: F401


def dedupe_candidates_df(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse duplicate candidate rows within a race to one row per person.

    Dedupe key is (state, race, year, canonical_name). When duplicates exist, we keep:
    - the longest non-empty display name
    - the first non-empty party value
    - max(total_votes) and boolean flags across duplicates
    - first(source_url)

    Args:
        df: Candidate card DataFrame with columns like state/race/year/name/party/etc.

    Returns:
        A deduped DataFrame with stable, ordered columns.
    """
    if df.empty:
        return df

    # Canonicalize names so "John A. Doe" and "John Doe" group together.
    canon = df["name"].fillna("").map(canonical_name)
    df = df.assign(_canon=canon)

    def _pick_name(series: pd.Series) -> str:
        # Prefer the longest label to retain middle initials / suffixes when present.
        return max(series.dropna().astype(str), key=len, default="")

    def _pick_party(series: pd.Series) -> str:
        # Keep the first non-empty party value encountered.
        for v in series:
            if isinstance(v, str) and v.strip():
                return v
        return ""

    grouped = (
        df.groupby(["state", "race", "year", "_canon"], as_index=False)
        .agg(
            {
                "name": _pick_name,
                "party": _pick_party,
                "total_votes": "max",
                "incumbent": "max",
                "is_winner": "max",
                "is_advancer": "max",
                "source_url": "first",
            }
        )
    )

    # Drop internal columns and standardize output ordering.
    grouped = grouped[
        [
            "state",
            "race",
            "year",
            "name",
            "party",
            "total_votes",
            "incumbent",
            "is_winner",
            "is_advancer",
            "source_url",
        ]
    ]
    return grouped


def dedupe_races_df(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse duplicate race summary rows to one row per (state, race, year).

    Args:
        df: Race summary DataFrame produced by scraping.

    Returns:
        A deduped DataFrame with stable, ordered columns.
    """
    if df.empty:
        return df

    # Sort first so "first" aggregations are deterministic.
    df = df.sort_values(["state", "race", "year"])

    keep = (
        df.groupby(["state", "race", "year"], as_index=False)
        .agg(
            {
                "winner_name": "first",
                "advancers": "first",
                "is_jungle_primary": "first",
                "primary_party": "first",
                "source_url": "first",
            }
        )
    )

    keep = keep[
        ["state", "race", "year", "winner_name", "advancers", "is_jungle_primary", "primary_party", "source_url"]
    ]
    return keep


def _trim_string_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Strip leading/trailing whitespace from all string-like columns."""
    if df is None or df.empty:
        return df

    df = df.copy()

    # Normalize whitespace to reduce noisy diffs across runs.
    for col in df.columns:
        if pd.api.types.is_string_dtype(df[col]) or df[col].dtype == object:
            df[col] = df[col].apply(lambda x: x.strip() if isinstance(x, str) else x)

    return df


def _serialize_list_col_as_json(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """Serialize list values in a column to JSON strings for CSV output."""
    if df is None or df.empty or col not in df.columns:
        return df

    df = df.copy()

    def conv(value):
        # Store lists as compact JSON, but keep existing non list strings unchanged.
        if isinstance(value, list):
            return json.dumps(value, ensure_ascii=False)

        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return ""
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return json.dumps(parsed, ensure_ascii=False)
            except Exception:
                return stripped
            return stripped

        if pd.isna(value):
            return ""

        return ""

    df[col] = df[col].apply(conv)
    return df


def _advancers_list_or_null(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize the 'advancers' column to either list[str] or None."""
    if df is None or df.empty or "advancers" not in df.columns:
        return df

    df = df.copy()

    def conv(value):
        if isinstance(value, list):
            return value

        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                parsed = json.loads(stripped)
                return parsed if isinstance(parsed, list) else None
            except Exception:
                return None

        if pd.isna(value):
            return None

        return None

    df["advancers"] = df["advancers"].apply(conv)
    return df


def write_csv_strict(df: pd.DataFrame, path: Path, boolean_cols: Optional[List[str]] = None) -> None:
    """Write a CSV with stable null/boolean formatting.

    Rules:
    - boolean_cols are written as "true"/"false" (empty string for nulls)
    - nulls in non-numeric columns are written as empty strings

    Args:
        df: DataFrame to write.
        path: Destination path.
        boolean_cols: Columns to treat as booleans for CSV formatting.
    """
    if df is None or df.empty:
        df.to_csv(path, index=False)
        return

    df_out = df.copy()

    # Normalize boolean output so downstream consumers don't see True/False/1/0 mixes.
    for col in (boolean_cols or []):
        if col in df_out.columns:

            def _b(x):
                if pd.isna(x):
                    return ""
                return "true" if bool(x) else "false"

            df_out[col] = df_out[col].map(_b)

    # For CSV exports, prefer empty string over NaN for non numeric columns.
    for col in df_out.columns:
        if pd.api.types.is_numeric_dtype(df_out[col]):
            continue
        df_out[col] = df_out[col].where(pd.notnull(df_out[col]), "")

    df_out.to_csv(path, index=False)


# Public aliases used by other modules.
trim_string_columns = _trim_string_columns
serialize_list_col_as_json = _serialize_list_col_as_json
advancers_list_or_null = _advancers_list_or_null
