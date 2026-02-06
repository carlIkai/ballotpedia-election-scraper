from __future__ import annotations

"""
Text normalization and lightweight identifier helpers.

This module contains small utilities used across parsing and transform layers:
- whitespace normalization and parenthetical stripping
- name normalization for matching/deduping
- display-safe cleanup helpers for output fields
- state name normalization 
- slug/id helpers used in filenames and object identifiers
"""

import base64
import json
import re
from typing import Any, List, Optional

from bp_scraper.core.constants import USPS, USPS_INV


# Parenthetical text removal.
PARENS_ANY_RE = re.compile(r"\s*\([^)]*\)")


def nws(s: Optional[str]) -> str:
    """Normalize whitespace to single spaces."""
    return re.sub(r"\s+", " ", (s or "").strip())


def strip_parenthetical(s: str) -> str:
    """Remove parenthetical text (e.g., "(D)", "(Incumbent)") from a string."""
    return PARENS_ANY_RE.sub("", s or "").strip()


def norm_name(s: str) -> str:
    """Normalize a human name string for matching."""
    s = s or ""
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"incumbent", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    return s.lower()


def canonical_name(s: str) -> str:
    """Alias for norm_name()."""
    return norm_name(s)


def b64_id(kind: str, *parts: str) -> str:
    """Build a URL safe base64 identifier from structured parts."""
    raw = f"{kind}:" + "|".join(parts)
    return base64.urlsafe_b64encode(raw.encode()).decode()


def display_clean_name(name: Optional[str], state: Optional[str] = None) -> str:
    """Return a display safe candidate name."""
    nm = strip_parenthetical(name or "")
    return nm


def display_clean_list(names: Optional[List[str]], state: Optional[str] = None) -> Optional[List[str]]:
    """Apply display_clean_name() across a list."""
    if names is None:
        return None
    return [display_clean_name(n, state) for n in names]


def normalize_state_name(s: str) -> str:
    """Normalize a state name to canonical proper-case form."""
    if not s:
        return s

    # Straighten apostrophes for consistent comparisons.
    s0 = s.replace("’", "'").strip()

    # Direct prefix match (handles "New York", "New York (something)", etc.).
    for proper in sorted(USPS.keys(), key=len, reverse=True):
        if s0.lower().startswith(proper.lower()):
            return proper

    # Possessive forms (e.g., "Virginia's ...").
    s1 = re.sub(r"'s.+$", "", s0).strip()
    for proper in sorted(USPS.keys(), key=len, reverse=True):
        if s1.lower().startswith(proper.lower()):
            return proper

    return s


def normalize_state_filter_arg(s: Optional[str]) -> Optional[str]:
    """Normalize a --state filter arg into a full canonical state name."""
    if not s:
        return None
    s = s.strip()
    if not s:
        return None

    # USPS abbreviation input.
    if len(s) == 2 and s.upper() in USPS_INV:
        return USPS_INV[s.upper()]

    return normalize_state_name(s)


def slugify(s: str) -> str:
    """Slugify a string for filenames and identifiers."""
    import re as _re

    return _re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def race_title_for_chamber(chamber: str, year: int) -> str:
    """Return a stable title string for a chamber/year run."""
    return f"US {'Senate' if chamber == 'senate' else 'House'} {year}"
