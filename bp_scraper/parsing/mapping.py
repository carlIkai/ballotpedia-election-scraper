from __future__ import annotations

"""
House race label mapping utilities.

This module supports stable joins between:
- raw race labels scraped from Ballotpedia pages, and
- canonical House labels used for downstream unification.

Key behaviors:
- normalize free form labels into a comparable key space
- extract district tokens from common label formats
- build a canonical join identifier for House races
- load and apply a state/year/label mapping table
"""

import re
from pathlib import Path
from typing import Dict, Tuple

import pandas as pd


# Unicode dash variants normalized to ASCII "-".
_DASHES = r"[\u2013\u2014—–]"


def _norm_label(s: str) -> str:
    """Normalize a label string for mapping-key comparisons."""
    s = (s or "").strip().lower()
    s = re.sub(_DASHES, "-", s)
    s = re.sub(r"[^\w\s-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _extract_house_district(label: str) -> str:
    """Extract a district token from a House race label."""
    s = label or ""

    if re.search(r"\bat[-\s]?large\b", s, flags=re.I):
        return "AT-LARGE"

    m = re.search(r"\b([A-Z]{2})-(\d{1,2})\b", s)
    if m:
        return f"{m.group(1).upper()}-{int(m.group(2)):02d}"

    m = re.search(r"\bdist(?:rict|\.)\s*(\d{1,2})\b", s, flags=re.I)
    if m:
        return f"D{int(m.group(1)):02d}"

    # Last numeric token fallback.
    m = re.search(r"\b(\d{1,2})\b(?!.*\b(\d{1,2})\b)", s)
    if m:
        return f"D{int(m.group(1)):02d}"

    return ""


def canonical_join_id(state: str, year: int, label: str, *, is_primary: bool = True) -> str:
    """Build a stable join identifier for House races.

    Args:
        state: State name or abbreviation.
        year: Election year.
        label: Race label text.
        is_primary: True for primary joins, False for general joins.

    Returns:
        Canonical join id string.
    """
    st = (state or "").upper()
    yr = int(year)
    dist = _extract_house_district(label).upper()
    stage = "PRIMARY" if is_primary else "GENERAL"
    special = "SPECIAL" if re.search(r"special", label or "", flags=re.I) else "REG"
    runoff = "RUNOFF" if re.search(r"runoff", label or "", flags=re.I) else "NORUN"
    return f"{st}|HOUSE|{dist}|{yr}|{stage}|{special}|{runoff}"


def load_races_to_house_mapping(path: str | Path) -> pd.DataFrame:
    """Load the races→house label mapping CSV and compute join keys."""
    df = pd.read_csv(path)

    # Mapping key: (STATE, YEAR, normalized races_label)
    df["__key"] = list(
        zip(
            df["state"].str.upper(),
            df["year"].astype(int),
            df["races_label"].map(_norm_label),
        )
    )

    return df


def mapping_dict(df: pd.DataFrame) -> Dict[Tuple[str, int, str], str]:
    """Convert a mapping DataFrame into a lookup dict."""
    return dict(zip(df["__key"], df["house_label"]))


def rewrite_race_label(
    state: str,
    year: int,
    races_label: str,
    mapping: Dict[Tuple[str, int, str], str],
) -> str:
    """Rewrite a scraped race label to its canonical House label."""
    key = ((state or "").upper(), int(year), _norm_label(races_label))
    return mapping.get(key, races_label)
