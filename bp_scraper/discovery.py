from __future__ import annotations
import re
from typing import List, Optional, Tuple

from .constants import (
    BASE, HOUSE_AT_LARGE_STATES, HOUSE_OVERVIEW_TEMPLATE, SENATE_OVERVIEW_TEMPLATE,
)
from .http_urls import get_soup, canonicalize_url
from .utils import nws, normalize_state_name, strip_parenthetical

def discover_state_pages(year: int, chamber: str, verbose: bool=False, delay: Optional[float]=None, retries: Optional[int]=None) -> List[Tuple[str, str, str]]:
    overview = SENATE_OVERVIEW_TEMPLATE if chamber == "senate" else HOUSE_OVERVIEW_TEMPLATE
    url = BASE + overview.format(year=year)
    soup = get_soup(url, delay=delay, retries=retries)

    out: List[Tuple[str, str, str]] = []
    for a in soup.select("a[href]"):
        href = a.get("href",""); full = canonicalize_url(href, year, chamber)
        if not full: continue

        m = re.search(r"_in_([^,]+),_" + re.escape(str(year)), full)
        if m:
            raw_state = m.group(1).replace("_"," ")
        else:
            raw_state = nws(a.text)

        state = normalize_state_name(strip_parenthetical(raw_state))
        if not state: continue

        race = "U.S. Senate" if chamber == "senate" else "U.S. House"
        if chamber == "senate" and "United_States_Senate_special_election_in_" in full:
            race = "U.S. Senate (special)"

        out.append((state, race, full))

    seen, dedup = set(), []
    for state, race, link in out:
        key = (state, race)
        if key in seen: continue
        seen.add(key); dedup.append((state, race, link))
    if verbose:
        label = "Senate" if chamber == "senate" else "House"
        print(f"[overview] discovered {len(dedup)} {label} state pages for {year}")
    return dedup

def discover_house_district_pages(state: str, state_page_url: str, year: int, verbose: bool=False, delay: Optional[float]=None, retries: Optional[int]=None) -> List[str]:
    from .constants import CANON_HOUSE_DISTRICT_URL
    soup = get_soup(state_page_url, delay=delay, retries=retries)

    links = []
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if not href: continue
        full = canonicalize_url(href, year, chamber="house")
        if not full: continue
        if CANON_HOUSE_DISTRICT_URL.search(full):
            links.append(full)

    seen, out = set(), []
    for u in links:
        if u in seen: continue
        seen.add(u); out.append(u)

    if not out:
        if re.search(r"/United_States_House(_of_Representatives)?_election_in_[^,]+,_\d{4}$", state_page_url, re.I):
            out = [state_page_url]

    if verbose:
        print(f"[overview] {state_page_url} -> {len(out)} district links{' (check regex/markup)' if len(out)==0 else ''}")
    return out
