from __future__ import annotations

"""
Result section discovery for Ballotpedia election pages.

This module locates the HTML subsections that contain election results tables.
Sections are identified by scanning page headers (h2/h3/h4) and selecting the
content between headers.

Supports:
- general election results (including special and general runoff)
- primary election results (party-specific where possible)
- state-specific fallbacks (e.g., Louisiana jungle/blanket primary phrasing)
"""

import re
from typing import List, Optional, Tuple

from bs4 import BeautifulSoup, Tag

from bp_scraper.core.constants import (
    HEADER_OK_PATTERNS_GENERAL,
    PRIMARY_WORD_RE,
    LA_PRIMARY_PATTERNS,
    PRIMARY_RUNOFF_RE,
    YEAR_ONLY_RE,
    PAST_ELEX_RE,
)
from bp_scraper.parsing.normalize import nws


def _wrap_between(soup: BeautifulSoup, start: Tag, end: Optional[Tag]) -> BeautifulSoup:
    """Wrap nodes between two header tags into a single container."""
    wrapper = soup.new_tag("div")

    node = start.next_sibling
    while node and node is not end:
        nxt = node.next_sibling
        wrapper.append(node)
        node = nxt

    return wrapper


# Primary party tokens mapped to canonical party names.
PRIMARY_PARTY_MAP = {
    "democratic": "Democratic",
    "republican": "Republican",
    "libertarian": "Libertarian",
    "green": "Green",
    "independent": "Independent",
    "independent american": "Independent American",
    "nonpartisan": "Nonpartisan",
    "working families": "Working Families",
    "aloha": "Aloha ʻĀina",
    "constitution": "Constitution",
    "progressive": "Progressive",
}


def _detect_primary_party(label: str) -> Optional[str]:
    """Infer the primary party from a header label."""
    low = (label or "").lower()

    for key, proper in PRIMARY_PARTY_MAP.items():
        if re.search(rf"\b{re.escape(key)}\b.*\bprimary\b", low) or re.search(
            rf"\bprimary\b.*\b{re.escape(key)}\b", low
        ):
            return proper

    # Louisiana wording sometimes omits party but clearly indicates a nonpartisan blanket primary.
    if re.search(r"nonpartisan\s+blanket\s+primary", low):
        return "Nonpartisan"

    return None


def find_result_sections(
    soup: BeautifulSoup,
    year: int,
    state: str,
    race_label_base: str,
    primary: bool = False,
) -> List[Tuple[BeautifulSoup, str]]:
    """Locate result sections and return them with a derived race label.

    Args:
        soup: Parsed page soup.
        year: Election year to target.
        state: State name (used for state-specific fallbacks).
        race_label_base: Base label (e.g., "U.S. House", "U.S. Senate").
        primary: If True, locate primary sections; otherwise locate general sections.

    Returns:
        List of (section_soup, race_label) tuples.
    """
    headers = list(soup.find_all(["h2", "h3", "h4"]))
    sections: List[Tuple[BeautifulSoup, str]] = []

    in_past = False
    current_explicit_year: Optional[int] = None

    def _add_section(i: int, label: str) -> None:
        end = None
        for j in range(i + 1, len(headers)):
            end = headers[j]
            break
        sec = _wrap_between(soup, headers[i], end)
        sections.append((sec, label))

    for i, h in enumerate(headers):
        txt = nws(h.get_text(" "))

        # Ignore sections under "Past elections".
        if PAST_ELEX_RE.search(txt):
            in_past = True
            continue

        # Track explicit year headers when present (e.g., "2022", "2024").
        ymatch = YEAR_ONLY_RE.fullmatch(txt)
        if ymatch:
            try:
                current_explicit_year = int(ymatch.group(0))
            except Exception:
                current_explicit_year = None
            continue

        if in_past:
            continue

        year_ok = (current_explicit_year is None) or (current_explicit_year == year)

        if not primary:
            # General results headers should not be primary headers.
            if year_ok and any(p.search(txt) for p in HEADER_OK_PATTERNS_GENERAL):
                if PRIMARY_WORD_RE.search(txt):
                    continue

                race_label = race_label_base

                # Mark special elections when the header indicates "special".
                if "special" in txt.lower() and "special" not in race_label.lower():
                    race_label = f"{race_label_base} (special)"

                # Mark general runoff sections explicitly.
                if re.search(r"\brunoff\b", txt, re.I):
                    race_label = f"{race_label} — General runoff"

                _add_section(i, race_label)

        else:
            # Primary headers include "primary" or known Louisiana blanket-primary phrasing.
            if year_ok and (PRIMARY_WORD_RE.search(txt) or any(p.search(txt) for p in LA_PRIMARY_PATTERNS)):
                party = _detect_primary_party(txt)
                is_runoff = bool(PRIMARY_RUNOFF_RE.search(txt))

                race_label = (
                    f"{race_label_base} — {party} primary" if party else f"{race_label_base} — Primary"
                )
                if is_runoff and not race_label.lower().endswith("runoff"):
                    race_label = f"{race_label} runoff"

                # Prefer the most recent occurrence of the same label.
                existing_idx = next((k for k, (_sec, lbl) in enumerate(sections) if lbl == race_label), None)
                if existing_idx is None:
                    _add_section(i, race_label)
                else:
                    sections.pop(existing_idx)
                    _add_section(i, race_label)

    # Louisiana can present general results under the blanket primary header.
    if (not sections) and (not primary) and state.lower() == "louisiana":
        in_past = False
        current_explicit_year = None

        for i, h in enumerate(headers):
            txt = nws(h.get_text(" "))

            if PAST_ELEX_RE.search(txt):
                in_past = True
                continue

            ymatch = YEAR_ONLY_RE.fullmatch(txt)
            if ymatch:
                try:
                    current_explicit_year = int(ymatch.group(0))
                except Exception:
                    current_explicit_year = None
                continue

            if in_past:
                continue

            year_ok = (current_explicit_year is None) or (current_explicit_year == year)
            if year_ok and any(p.search(txt) for p in LA_PRIMARY_PATTERNS):
                sections.append((_wrap_between(soup, headers[i], None), race_label_base))
                break

    return sections


def ny_primary_fallback_sections(soup: BeautifulSoup, race_label_base: str) -> List[Tuple[BeautifulSoup, str]]:
    """Fallback primary section discovery for New York pages with atypical headings."""
    headers = list(soup.find_all(["h2", "h3", "h4"]))
    sections: List[Tuple[BeautifulSoup, str]] = []

    for i, h in enumerate(headers):
        txt = nws(h.get_text(" "))
        if not PRIMARY_WORD_RE.search(txt):
            continue

        party = _detect_primary_party(txt)
        label = f"{race_label_base} — {party} primary" if party else f"{race_label_base} — Primary"

        end = None
        for j in range(i + 1, len(headers)):
            end = headers[j]
            break

        sec = _wrap_between(soup, h, end)
        sections.append((sec, label))

    return sections
