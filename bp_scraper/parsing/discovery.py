from __future__ import annotations
import re
from typing import List, Optional, Tuple
from bp_scraper.core.constants import (
    BASE, HOUSE_AT_LARGE_STATES, HOUSE_OVERVIEW_TEMPLATE, SENATE_OVERVIEW_TEMPLATE,
)
from bp_scraper.io.http import get_soup, canonicalize_url
from bp_scraper.parsing.normalize import nws, normalize_state_name, strip_parenthetical


def discover_state_pages(
    year: int,
    chamber: str,
    verbose: bool = False,
    delay: Optional[float] = None,
    retries: Optional[int] = None
) -> List[Tuple[str, str, str]]:
    overview_template = SENATE_OVERVIEW_TEMPLATE if chamber == "senate" else HOUSE_OVERVIEW_TEMPLATE
    overview_url = BASE + overview_template.format(year=year)
    soup = get_soup(overview_url, delay=delay, retries=retries)

    discovered_entries: List[Tuple[str, str, str]] = []
    for anchor_tag in soup.select("a[href]"):
        href_value = anchor_tag.get("href", "")
        full_url = canonicalize_url(href_value, year, chamber)
        if not full_url:
            continue

        state_match = re.search(r"_in_([^,]+),_" + re.escape(str(year)), full_url)
        if state_match:
            raw_state_text = state_match.group(1).replace("_", " ")
        else:
            raw_state_text = nws(anchor_tag.text)

        normalized_state = normalize_state_name(strip_parenthetical(raw_state_text))
        if not normalized_state:
            continue

        race_label = "U.S. Senate" if chamber == "senate" else "U.S. House"
        if chamber == "senate" and "United_States_Senate_special_election_in_" in full_url:
            race_label = "U.S. Senate (special)"

        discovered_entries.append((normalized_state, race_label, full_url))

    seen_state_race_keys, deduped_entries = set(), []
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
    retries: Optional[int] = None
) -> List[str]:
    from bp_scraper.core.constants import CANON_HOUSE_DISTRICT_URL
    soup = get_soup(state_page_url, delay=delay, retries=retries)

    district_links: List[str] = []
    for anchor_tag in soup.select("a[href]"):
        href_value = anchor_tag.get("href", "")
        if not href_value:
            continue
        full_url = canonicalize_url(href_value, year, chamber="house")
        if not full_url:
            continue
        if CANON_HOUSE_DISTRICT_URL.search(full_url):
            district_links.append(full_url)

    seen_urls, unique_district_links = set(), []
    for candidate_url in district_links:
        if candidate_url in seen_urls:
            continue
        seen_urls.add(candidate_url)
        unique_district_links.append(candidate_url)

    if not unique_district_links:
        if re.search(r"/United_States_House(_of_Representatives)?_election_in_[^,]+,_\d{4}$", state_page_url, re.I):
            unique_district_links = [state_page_url]

    if verbose:
        print(f"[overview] {state_page_url} -> {len(unique_district_links)} district links"
              f"{' (check regex/markup)' if len(unique_district_links) == 0 else ''}")
    return unique_district_links
