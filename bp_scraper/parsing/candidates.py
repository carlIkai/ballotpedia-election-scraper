from __future__ import annotations

"""
Candidate card parsing helpers.

This module extracts candidate-level records from Ballotpedia "cc-container" cards and
applies lightweight party inference/cleanup when the page markup is incomplete.

Outputs are intentionally flat dicts to keep downstream DataFrame transforms simple.
"""

from typing import List

from bs4 import BeautifulSoup

from bp_scraper.core.constants import PARTY_FROM_CLASS
from bp_scraper.parsing.normalize import nws, norm_name
from bp_scraper.parsing.tables import _infer_party_from_name


def parse_candidate_cards(
    soup: BeautifulSoup,
    state: str,
    year: int,
    race_label: str,
    source_url: str,
) -> List[dict]:
    """Parse Ballotpedia candidate cards into a list of normalized dict records.

    Party is inferred using the following precedence:
    1) CSS class on the card header (most reliable on Ballotpedia)
    2) Explicit party element text (".cc-party")
    3) Heuristic inference from the candidate name (fallback)

    Args:
        soup: Parsed page soup.
        state: State name for the race context.
        year: Election year.
        race_label: Race label associated with these cards.
        source_url: Page URL used for traceability.

    Returns:
        List of candidate card dicts with state/race/year context and normalized fields.
    """
    out: List[dict] = []

    # Candidate cards on Ballotpedia are typically wrapped in "div.cc-container".
    for card in list(soup.select("div.cc-container")):
        header = card.select_one(".cc-header")
        name = nws(header.get_text(" ")) if header else None
        if not name:
            continue

        party = None

        # Party is often encoded as a CSS class on the header.
        if header:
            for cls in header.get("class", []):
                if cls in PARTY_FROM_CLASS:
                    party = PARTY_FROM_CLASS[cls]
                    break

        # Some pages include an explicit party string element.
        if not party:
            party_el = card.select_one(".cc-party")
            if party_el:
                party = nws(party_el.get_text(" "))

        # Infer party from name patterns/parentheticals when markup is incomplete.
        if not party:
            party = _infer_party_from_name(name)

        out.append(
            {
                "state": state,
                "race": race_label,
                "year": year,
                "name": name,
                "name_clean": norm_name(name),
                "party": party,
                "incumbent": False,
                "source_url": source_url,
            }
        )

    return out


# Keywords used to infer a party from the surrounding section text when cards omit party markup.
_SECTION_PARTY_KEYWORDS = [
    ("democratic", "Democratic"),
    ("republican", "Republican"),
    ("libertarian", "Libertarian"),
    ("green", "Green"),
    ("constitution", "Constitution"),
    ("peace and freedom", "Peace and Freedom"),
    ("socialist workers", "Socialist Workers"),
    ("working families", "Working Families"),
    ("american independent", "American Independent"),
    ("independent american", "Independent American"),
    ("progressive", "Progressive"),
    ("aloha ʻāina", "Aloha ʻĀina"),
    ("aloha aina", "Aloha ʻĀina"),
    ("independent", "Independent"),
    ("nonpartisan", "Nonpartisan"),
]


def scan_section_party_keywords(sec: BeautifulSoup) -> str | None:
    """Infer a single party from a section's visible text.

    Returns a party name only when the section text unambiguously indicates one party.
    If multiple parties are mentioned (or none), returns None.

    Args:
        sec: Section soup node to scan.

    Returns:
        Canonical party name if exactly one party keyword matches; otherwise None.
    """
    txt = nws(sec.get_text(" ")).lower()
    hits = set()

    for keyword, proper in _SECTION_PARTY_KEYWORDS:
        if keyword in txt:
            hits.add(proper)

    if len(hits) == 1:
        return next(iter(hits))

    return None


def backfill_party_from_label(cards: List[dict]) -> None:
    """Backfill missing/ambiguous party values using the race label.

    This is a cleanup pass for pages where Ballotpedia omits party markup and the
    card-level inference falls back to "Other" or "Nonpartisan".

    Mutates:
        cards: Updates card["party"] in-place when a primary party label is detected.
    """
    from bp_scraper.core.constants import PRIMARY_PARTY_FROM_LABEL

    for card in cards:
        cur = (card.get("party") or "").strip()

        # Only backfill when the current party is missing or not useful for joining.
        if cur and cur not in {"Other", "Nonpartisan"}:
            continue

        low = (card.get("race") or "").lower()
        for key, val in PRIMARY_PARTY_FROM_LABEL.items():
            if key in low:
                card["party"] = val
                break
