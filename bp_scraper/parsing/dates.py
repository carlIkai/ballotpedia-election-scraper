from __future__ import annotations
import re
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
from bs4 import BeautifulSoup, Tag

ELECTION_DAY_BY_KEY: Dict[Tuple[str, int, str], str] = {}

US_LONG_DATE_RE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s*(\d{4})\b"
)
LABEL_AND_DATE_RE = re.compile(
    r"\b(Primary|Nonpartisan primary|General(?: runoff)?|Runoff|Primary runoff)\b.*?\b([A-Za-z]+\s+\d{1,2},\s*\d{4})",
    re.I,
)


def _parse_us_date(date_str: str) -> Optional[str]:
    try:
        parsed_date = datetime.strptime(date_str.strip(), "%B %d, %Y").date()
        return parsed_date.isoformat()
    except Exception:
        return None


def compute_federal_general_election_day(year: int) -> str:
    first_day_of_november = date(year, 11, 1)
    while first_day_of_november.weekday() != 0:  
        first_day_of_november += timedelta(days=1)
    election_day = first_day_of_november + timedelta(days=1)
    return election_day.isoformat()


def _iso_in_year(iso_date_string: str, year: int) -> bool:
    try:
        return datetime.fromisoformat(iso_date_string).year == year
    except Exception:
        return False


def _iso_date(iso_date_string: Optional[str]) -> Optional[date]:
    try:
        return datetime.fromisoformat(iso_date_string).date() if iso_date_string else None
    except Exception:
        return None


def parse_election_dates_from_page(soup: BeautifulSoup, state: str, year: int) -> None:
    from bp_scraper.parsing.normalize import nws

    text_blocks: List[str] = []

    for element in soup.find_all(True):
        element_text = nws(element.get_text(" "))
        if not element_text:
            continue
        if "election dates" in element_text.lower():
            text_blocks.append(element_text)

    for header_tag in soup.find_all(["h2", "h3", "h4"]):
        header_text = nws(header_tag.get_text(" "))
        if re.search(r"\bElection dates\b", header_text, flags=re.I):
            for sibling in list(header_tag.next_siblings):
                if isinstance(sibling, Tag) and sibling.name in {"ul", "ol", "p", "div"}:
                    text_blocks.append(nws(sibling.get_text(" ")))
                if isinstance(sibling, Tag) and sibling.name in {"h2", "h3", "h4"}:
                    break

    for paragraph_tag in soup.select("p.results_text"):
        paragraph_text = nws(paragraph_tag.get_text(" "))
        if not paragraph_text:
            continue
        lower_text = paragraph_text.lower()
        inferred_phase: Optional[str] = None
        if "runoff" in lower_text:
            inferred_phase = "Runoff"
        elif "primary" in lower_text:
            inferred_phase = "Primary"
        elif "general" in lower_text:
            inferred_phase = "General"
        if not inferred_phase:
            continue
        date_match = re.search(r"[A-Za-z]+\s+\d{1,2},\s*\d{4}", paragraph_text)
        if not date_match:
            continue
        iso_date_str = _parse_us_date(date_match.group(0))
        if not iso_date_str or not _iso_in_year(iso_date_str, year):
            continue
        text_blocks.append(f"{inferred_phase}: {date_match.group(0)}")

    if not text_blocks:
        text_blocks = [nws(soup.get_text(" "))]

    found_raw: Dict[str, str] = {}
    for text_block in text_blocks:
        for match_obj in LABEL_AND_DATE_RE.finditer(text_block):
            label_text = match_obj.group(1).strip().lower()
            date_str = match_obj.group(2)
            iso_date_str = _parse_us_date(date_str)
            if not iso_date_str:
                continue

            if "nonpartisan" in label_text and "primary" in label_text:
                phase_label = "Primary"
            elif label_text.startswith("primary") or " primary" in label_text:
                phase_label = "Primary"
            elif label_text.startswith("general") and "runoff" in label_text:
                phase_label = "Runoff"
            elif label_text.startswith("general"):
                phase_label = "General"
            elif label_text.startswith("runoff") or "runoff" in label_text:
                phase_label = "Runoff"
            else:
                continue

            found_raw[phase_label] = iso_date_str

    if "General" in found_raw and _iso_in_year(found_raw["General"], year):
        ELECTION_DAY_BY_KEY[(state, year, "General")] = found_raw["General"]

    primary_iso_valid: Optional[str] = None
    if "Primary" in found_raw and _iso_in_year(found_raw["Primary"], year):
        primary_date = _iso_date(found_raw["Primary"])
        general_day_date = _iso_date(compute_federal_general_election_day(year))
        if primary_date and (not general_day_date or primary_date <= general_day_date):
            primary_iso_valid = found_raw["Primary"]

    runoff_iso_valid: Optional[str] = None
    if "Runoff" in found_raw and _iso_in_year(found_raw["Runoff"], year):
        runoff_date = _iso_date(found_raw["Runoff"])
        if runoff_date:
            if (primary_iso_valid and runoff_date >= _iso_date(primary_iso_valid)) or (not primary_iso_valid):
                runoff_iso_valid = found_raw["Runoff"]

    if primary_iso_valid:
        ELECTION_DAY_BY_KEY[(state, year, "Primary")] = primary_iso_valid
    if runoff_iso_valid:
        ELECTION_DAY_BY_KEY[(state, year, "Runoff")] = runoff_iso_valid
