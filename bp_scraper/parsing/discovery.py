from __future__ import annotations

"""
Discovery helpers for Ballotpedia election pages.

This module identifies canonical election pages starting from Ballotpedia
overview pages and expands them into state-, chamber-, and district-level
URLs suitable for scraping.

Discovery responsibilities:
- locate state-level Senate/House election pages
- expand House state pages into per-district pages
- discover state election pages by office type
"""

import re
from typing import List, Optional, Tuple

from bp_scraper.core.constants import (
    BASE,
    HOUSE_AT_LARGE_STATES,
    HOUSE_OVERVIEW_TEMPLATE,
    SENATE_OVERVIEW_TEMPLATE,
    STATE_ELECTIONS_OVERVIEW_TEMPLATE,
)
from bp_scraper.io.http import get_soup, canonicalize_url
from bp_scraper.parsing.normalize import nws, normalize_state_name, strip_parenthetical


def discover_state_pages(
    year: int,
    chamber: str,
    verbose: bool = False,
    delay: Optional[float] = None,
    retries: Optional[int] = None,
) -> List[Tuple[str, str, str]]:
    """Discover state-level Senate or House election pages.

    Args:
        year: Election year.
        chamber: "senate" or "house".
        verbose: Emit discovery summary output.
        delay: Optional request delay override.
        retries: Optional retry override.

    Returns:
        Tuples of (state, race_label, page_url).
    """
    overview_template = SENATE_OVERVIEW_TEMPLATE if chamber == "senate" else HOUSE_OVERVIEW_TEMPLATE
    overview_url = BASE + overview_template.format(year=year)

    soup = get_soup(overview_url, delay=delay, retries=retries)

    discovered_entries: List[Tuple[str, str, str]] = []

    # Extract canonical election page links from the overview page.
    for anchor_tag in soup.select("a[href]"):
        href_value = anchor_tag.get("href", "")
        full_url = canonicalize_url(href_value, year, chamber)
        if not full_url:
            continue

        # Prefer state name embedded in the URL when present.
        state_match = re.search(r"_in_([^,]+),_" + re.escape(str(year)), full_url)
        if state_match:
            raw_state_text = state_match.group(1).replace("_", " ")
        else:
            raw_state_text = nws(anchor_tag.text)

        normalized_state = normalize_state_name(strip_parenthetical(raw_state_text))
        if not normalized_state:
            continue

        low_url = full_url.lower()
        if chamber == "senate":
            race_label = (
                "U.S. Senate (special)"
                if "united_states_senate_special_election_in_" in low_url
                else "U.S. Senate"
            )
        else:
            race_label = "U.S. House"

        discovered_entries.append((normalized_state, race_label, full_url))

    # Deduplicate by (state, race) to avoid duplicate overview links.
    seen_state_race_keys: set[tuple[str, str]] = set()
    deduped_entries: List[Tuple[str, str, str]] = []
    for normalized_state, race_label, page_url in discovered_entries:
        dedupe_key = (normalized_state, race_label)
        if dedupe_key in seen_state_race_keys:
            continue
        seen_state_race_keys.add(dedupe_key)
        deduped_entries.append((normalized_state, race_label, page_url))

    if verbose:
        label = "Senate" if chamber == "senate" else "House"
        print(f"[overview] discovered {len(deduped_entries)} {label} state pages for {year}")

    return deduped_entries


def discover_house_district_pages(
    state: str,
    state_page_url: str,
    year: int,
    verbose: bool = False,
    delay: Optional[float] = None,
    retries: Optional[int] = None,
) -> List[str]:
    """Expand a House state page into district level election pages.

    Args:
        state: State name.
        state_page_url: Canonical House election page for the state.
        year: Election year.
        verbose: Emit discovery diagnostics.
        delay: Optional request delay override.
        retries: Optional retry override.

    Returns:
        List of canonical district election URLs.
    """
    from bp_scraper.core.constants import CANON_HOUSE_DISTRICT_URL

    soup = get_soup(state_page_url, delay=delay, retries=retries)

    district_links: List[str] = []

    # Collect canonical district election links.
    for anchor_tag in soup.select("a[href]"):
        href_value = anchor_tag.get("href", "")
        if not href_value:
            continue

        full_url = canonicalize_url(href_value, year, chamber="house")
        if not full_url:
            continue

        if CANON_HOUSE_DISTRICT_URL.search(full_url):
            district_links.append(full_url)

    # Deduplicate district URLs.
    seen_urls: set[str] = set()
    unique_district_links: List[str] = []
    for candidate_url in district_links:
        if candidate_url in seen_urls:
            continue
        seen_urls.add(candidate_url)
        unique_district_links.append(candidate_url)

    # At large states do not have district specific pages.
    if not unique_district_links:
        if re.search(
            r"/United_States_House(_of_Representatives)?_election_in_[^,]+,_\d{4}$",
            state_page_url,
            re.I,
        ):
            unique_district_links = [state_page_url]

    if verbose:
        print(
            f"[overview] {state_page_url} -> {len(unique_district_links)} district links"
            f"{' (check regex/markup)' if len(unique_district_links) == 0 else ''}"
        )

    return unique_district_links


# CLI office token mapped to internal office key.
_OFFICE_TOKEN_TO_OFFICE = {
    "governor": "governor",
    "lt_governor": "lt_governor",
    "attorney_general": "attorney_general",
    "state_lower": "state_lower",
    "state_upper": "state_upper",
    "state_leg_districts": "state_leg_districts",
}


def _normalize_offices(offices: Optional[List[str]]) -> Optional[set[str]]:
    """Normalize CLI office tokens into internal office keys."""
    if not offices:
        return None

    cleaned: set[str] = set()
    for raw in offices:
        tok = (raw or "").strip().lower()
        if not tok:
            continue
        mapped = _OFFICE_TOKEN_TO_OFFICE.get(tok)
        if mapped:
            cleaned.add(mapped)

    return cleaned or None


def discover_state_election_pages(
    year: int,
    state: str,
    offices: Optional[List[str]] = None,
    verbose: bool = False,
    delay: Optional[float] = None,
    retries: Optional[int] = None,
) -> List[Tuple[str, str, str]]:
    """Discover state election pages for a given state and year.

    Args:
        year: Election year.
        state: State name or abbreviation.
        offices: Optional list of office filters.
        verbose: Emit discovery summary output.
        delay: Optional request delay override.
        retries: Optional retry override.

    Returns:
        Tuples of (state, race_label, page_url).
    """
    normalized_state = normalize_state_name(state)
    state_slug = (normalized_state or "").replace(" ", "_")
    overview_url = BASE + STATE_ELECTIONS_OVERVIEW_TEMPLATE.format(state=state_slug, year=year)

    soup = get_soup(overview_url, delay=delay, retries=retries)

    wanted = _normalize_offices(offices)
    discovered: List[Tuple[str, str, str]] = []

    # Extract canonical state election pages by office type.
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if not href:
            continue

        canon = canonicalize_url(href, year, chamber="state")
        if not canon:
            continue

        office_key = _office_from_state_url(canon)
        if not office_key:
            continue
        if wanted is not None and office_key not in wanted:
            continue

        race_label = _race_label_for_state_office(normalized_state, office_key, canon)
        discovered.append((normalized_state, race_label, canon))

    # Deduplicate by URL.
    seen_urls: set[str] = set()
    out: List[Tuple[str, str, str]] = []
    for st, label, url in discovered:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        out.append((st, label, url))

    if verbose:
        offices_label = ",".join(sorted(wanted)) if wanted else "all"
        print(
            f"[overview] discovered {len(out)} state election page(s) for {normalized_state} {year} "
            f"(offices={offices_label})"
        )

    return out


def _office_from_state_url(url: str) -> Optional[str]:
    """Infer the office type from a canonical state election URL."""
    low = (url or "").lower()

    if "_gubernatorial_election,_" in low:
        return "governor"
    if "_lieutenant_gubernatorial_election,_" in low:
        return "lt_governor"
    if "_attorney_general_election,_" in low:
        return "attorney_general"

    if re.search(r"_(house_of_delegates|house_of_representatives|state_house)_election,_", low):
        return "state_lower"
    if re.search(r"_(state_senate|senate)_election,_", low):
        return "state_upper"

    if re.search(
        r"_(house_of_delegates|house_of_representatives|state_house|state_senate|senate)_district_\d+_election,_",
        low,
    ):
        return "state_leg_districts"

    return None


def _race_label_for_state_office(state_name: str, office_key: str, url: str) -> str:
    """Return a display label for a state office."""
    if office_key == "governor":
        return "Governor"
    if office_key == "lt_governor":
        return "Lieutenant Governor"
    if office_key == "attorney_general":
        return "Attorney General"
    if office_key == "state_lower":
        if normalize_state_name(state_name).lower() == "virginia":
            return "House of Delegates"
        return "State House"
    if office_key == "state_upper":
        return "State Senate"
    if office_key == "state_leg_districts":
        return "State Legislature"

    return "State Office"
