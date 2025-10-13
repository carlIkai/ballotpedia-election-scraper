from __future__ import annotations
import re
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
from bs4 import BeautifulSoup, Tag

ELECTION_DAY_BY_KEY: Dict[Tuple[str,int,str], str] = {}

US_LONG_DATE_RE = re.compile(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s*(\d{4})\b")
LABEL_AND_DATE_RE = re.compile(r"\b(Primary|Nonpartisan primary|General(?: runoff)?|Runoff|Primary runoff)\b.*?\b([A-Za-z]+\s+\d{1,2},\s*\d{4})", re.I)

def _parse_us_date(date_str: str) -> Optional[str]:
    try:
        parsed_date = datetime.strptime(date_str.strip(), "%B %d, %Y").date()
        return parsed_date.isoformat()
    except Exception:
        return None

def compute_federal_general_election_day(year: int) -> str:
    first_nov = date(year, 11, 1)
    while first_nov.weekday() != 0: 
        first_nov += timedelta(days=1)
    election_day = first_nov + timedelta(days=1)  
    return election_day.isoformat()


def _iso_in_year(iso: str, year: int) -> bool:
    try:
        return datetime.fromisoformat(iso).year == year
    except Exception:
        return False

def _iso_date(iso: Optional[str]) -> Optional[date]:
    try:
        return datetime.fromisoformat(iso).date() if iso else None
    except Exception:
        return None

def parse_election_dates_from_page(soup: BeautifulSoup, state: str, year: int) -> None:
    from .utils import nws
    text_blocks: List[str] = []

    for element in soup.find_all(True):
        header = nws(element.get_text(" "))
        if not header: 
            continue
        if "election dates" in header.lower():
            text_blocks.append(header)

    for h in soup.find_all(["h2","h3","h4"]):
        if re.search(r"\bElection dates\b", nws(h.get_text(" ")), flags=re.I):
            for sib in list(h.next_siblings):
                if isinstance(sib, Tag) and sib.name in {"ul","ol","p","div"}:
                    text_blocks.append(nws(sib.get_text(" ")))
                if isinstance(sib, Tag) and sib.name in {"h2","h3","h4"}:
                    break

    for p in soup.select("p.results_text"):
        txt = nws(p.get_text(" "))
        if not txt: continue
        low = txt.lower()
        phase = None
        if "runoff" in low:
            phase = "Runoff"
        elif "primary" in low:
            phase = "Primary"
        elif "general" in low:
            phase = "General"
        if not phase:
            continue
        match = re.search(r"[A-Za-z]+\s+\d{1,2},\s*\d{4}", txt)
        if not match: continue
        iso = _parse_us_date(match.group(0))
        if not iso or not _iso_in_year(iso, year):
            continue
        text_blocks.append(f"{phase}: {match.group(0)}")

    if not text_blocks:
        text_blocks = [nws(soup.get_text(" "))]

    found_raw: Dict[str,str] = {}
    for block in text_blocks:
        for m in LABEL_AND_DATE_RE.finditer(block):
            label = m.group(1).strip().lower()
            date_str = m.group(2)
            iso = _parse_us_date(date_str)
            if not iso: continue
            if "nonpartisan" in label and "primary" in label:
                phase = "Primary"
            elif label.startswith("primary") or " primary" in label:
                phase = "Primary"
            elif label.startswith("general") and "runoff" in label:
                phase = "Runoff"
            elif label.startswith("general"):
                phase = "General"
            elif label.startswith("runoff") or "runoff" in label:
                phase = "Runoff"
            else:
                continue
            found_raw[phase] = iso

    if "General" in found_raw and _iso_in_year(found_raw["General"], year):
        ELECTION_DAY_BY_KEY[(state, year, "General")] = found_raw["General"]

    primary_iso_valid: Optional[str] = None
    if "Primary" in found_raw and _iso_in_year(found_raw["Primary"], year):
        pd_ = _iso_date(found_raw["Primary"])
        gd_ = _iso_date(compute_federal_general_election_day(year))
        if pd_ and (not gd_ or pd_ <= gd_):
            primary_iso_valid = found_raw["Primary"]

    runoff_iso_valid: Optional[str] = None
    if "Runoff" in found_raw and _iso_in_year(found_raw["Runoff"], year):
        rd_ = _iso_date(found_raw["Runoff"])
        if rd_:
            if (primary_iso_valid and rd_ >= _iso_date(primary_iso_valid)) or (not primary_iso_valid):
                runoff_iso_valid = found_raw["Runoff"]

    if primary_iso_valid:
        ELECTION_DAY_BY_KEY[(state, year, "Primary")] = primary_iso_valid
    if runoff_iso_valid:
        ELECTION_DAY_BY_KEY[(state, year, "Runoff")] = runoff_iso_valid
