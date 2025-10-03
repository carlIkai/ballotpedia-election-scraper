from __future__ import annotations
import argparse, re, time, urllib.parse
from pathlib import Path
from typing import List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

BASE = "https://ballotpedia.org/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/119.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

RACE_LABEL = "U.S. Senate"
OVERVIEW_TEMPLATE = "United_States_Senate_elections,_{year}"

def nws(s: Optional[str]) -> str:
    """Normalize whitespace."""
    return re.sub(r"\s+", " ", (s or "").strip())

def get_soup(url: str, delay: float = 0.5, timeout: int = 45) -> BeautifulSoup:
    """Fetch HTML."""
    response = SESSION.get(url, timeout=timeout)
    response.raise_for_status()
    time.sleep(delay)
    return BeautifulSoup(response.text, "lxml")

CANON_STATE_URL = re.compile(
    r"/United_States_Senate_(?:special_)?election_in_[^,]+,_\d{4}$"
)

def canonicalize_url(u: str, year: int) -> Optional[str]:
    """Return a full url to state page for the given year."""
    if not u:
        return None
    if not u.startswith("http"):
        if u.startswith("/"):
            u = BASE.rstrip("/") + u
        else:
            return None

    parsed = urllib.parse.urlsplit(u)
    clean = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    low = clean.lower()
    if any(x in low for x in ("/index.php?", "/amp", ":amp", "printable", "mobileaction")):
        return None
    if not CANON_STATE_URL.search(clean):
        return None
    if not clean.endswith(f"_{year}"):
        return None
    return clean

def discover_state_pages(year: int, delay: float = 0.5, verbose: bool = False) -> List[Tuple[str, str, str]]:
    """Get year from overview and return a list of state name, race label, and state url."""
    overview_url = BASE + OVERVIEW_TEMPLATE.format(year=year)
    soup = get_soup(overview_url, delay=delay)

    found: List[Tuple[str, str, str]] = []
    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "")
        full = canonicalize_url(href, year)
        if not full:
            continue

        match = re.search(r"_in_([^,]+),_" + re.escape(str(year)), full)
        state = (match.group(1).replace("_", " ") if m else nws(a.text)) or "Unknown"
        race = "U.S. Senate (special)" if "United_States_Senate_special_election_in_" in full else "U.S. Senate"
        found.append((state, race, full))

    seen, dedup = set(), []
    for state, race, link in found:
        key = (state, race)
        if key in seen:
            continue
        seen.add(key)
        dedup.append((state, race, link))

    if verbose:
        print(f"[overview] discovered {len(dedup)} state pages for {year} — {overview_url}")
    return dedup

def fetch_state_page(url: str, delay: float = 0.5, verbose: bool = False) -> BeautifulSoup:
    """Fetch a single state page."""
    soup = get_soup(url, delay=delay)
    if verbose:
        title = nws((soup.title.string if soup.title else "") or "")
        print(f"[state] {url}")
        if title:
            print(f"        <title> {title}")
    return soup

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2022)
    ap.add_argument("--state-url", default=None)
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.state_url:
        fetch_state_page(args.state_url, delay=args.delay, verbose=args.verbose)
        return

    pages = discover_state_pages(args.year, delay=args.delay, verbose=args.verbose)
    for state, race, link in pages[:5]:
        print(f"- {state}: {race} → {link}")
    if pages:
        fetch_state_page(pages[0][2], delay=args.delay, verbose=args.verbose)

if __name__ == "__main__":
    main()
