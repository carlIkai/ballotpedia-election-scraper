from __future__ import annotations
from typing import List, Optional, Tuple
from bs4 import BeautifulSoup, Tag
import re

from .constants import (
    HEADER_OK_PATTERNS_GENERAL, PRIMARY_WORD_RE, LA_PRIMARY_PATTERNS,
    PRIMARY_RUNOFF_RE, YEAR_ONLY_RE, PAST_ELEX_RE
)
from .utils import nws

def _wrap_between(soup: BeautifulSoup, start: Tag, end: Optional[Tag]) -> BeautifulSoup:
    wrapper = soup.new_tag("div"); node = start.next_sibling
    while node and node is not end:
        nxt = node.next_sibling; wrapper.append(node); node = nxt
    return wrapper

PRIMARY_PARTY_MAP = {
    "democratic":"Democratic","republican":"Republican","libertarian":"Libertarian","green":"Green",
    "independent":"Independent","independent american":"Independent American","nonpartisan":"Nonpartisan",
    "working families":"Working Families","aloha":"Aloha ʻĀina","constitution":"Constitution","progressive":"Progressive",
}

def _detect_primary_party(label: str) -> Optional[str]:
    low = (label or "").lower()
    for key, proper in PRIMARY_PARTY_MAP.items():
        if re.search(rf"\b{re.escape(key)}\b.*\bprimary\b", low) or re.search(rf"\bprimary\b.*\b{re.escape(key)}\b", low):
            return proper
    if re.search(r"nonpartisan\s+blanket\s+primary", low): 
        return "Nonpartisan"
    return None

def find_result_sections(
    soup: BeautifulSoup,
    year: int,
    state: str,
    race_label_base: str,
    primary: bool=False
) -> List[Tuple[BeautifulSoup, str]]:
    headers = list(soup.find_all(["h2","h3","h4"]))
    sections: List[Tuple[BeautifulSoup, str]] = []
    in_past = False; current_explicit_year: Optional[int] = None

    def _add_section(i: int, label: str):
        end = None
        for j in range(i+1,len(headers)):
            end = headers[j]; 
            break
        sec = _wrap_between(soup, headers[i], end)
        sections.append((sec, label))

    for i, h in enumerate(headers):
        txt = nws(h.get_text(" "))

        if PAST_ELEX_RE.search(txt): 
            in_past = True; 
            continue
        ymatch = YEAR_ONLY_RE.fullmatch(txt)
        if ymatch:
            try: current_explicit_year = int(ymatch.group(0))
            except: current_explicit_year = None
            continue
        if in_past: 
            continue
        year_ok = (current_explicit_year is None) or (current_explicit_year == year)

        if not primary:
            if year_ok and any(p.search(txt) for p in HEADER_OK_PATTERNS_GENERAL):
                if PRIMARY_WORD_RE.search(txt): 
                    continue
                race_label = race_label_base
                if "special" in txt.lower() and "special" not in race_label.lower():
                    race_label = f"{race_label_base} (special)"
                if re.search(r"\brunoff\b", txt, re.I):
                    race_label = f"{race_label} — General runoff"
                _add_section(i, race_label)
        else:
            if year_ok and (PRIMARY_WORD_RE.search(txt) or any(p.search(txt) for p in LA_PRIMARY_PATTERNS)):
                party = _detect_primary_party(txt)
                is_runoff = bool(PRIMARY_RUNOFF_RE.search(txt))
                race_label = f"{race_label_base} — {party} primary" if party else f"{race_label_base} — Primary"
                if is_runoff and not race_label.lower().endswith("runoff"):
                    race_label = f"{race_label} runoff"
                existing_idx = next((k for k, (_sec, lbl) in enumerate(sections) if lbl == race_label), None)
                if existing_idx is None:
                    _add_section(i, race_label)
                else:
                    sections.pop(existing_idx); _add_section(i, race_label)

    if (not sections) and (not primary) and state.lower() == "louisiana":
        in_past = False; current_explicit_year = None
        for i, h in enumerate(headers):
            txt = nws(h.get_text(" "))
            if PAST_ELEX_RE.search(txt): in_past = True; continue
            ymatch = YEAR_ONLY_RE.fullmatch(txt)
            if ymatch:
                try: current_explicit_year = int(ymatch.group(0))
                except: current_explicit_year = None
                continue
            if in_past: continue
            year_ok = (current_explicit_year is None) or (current_explicit_year == year)
            if year_ok and any(p.search(txt) for p in LA_PRIMARY_PATTERNS):
                sections.append((_wrap_between(soup, headers[i], None), race_label_base))
                break

    return sections

def ny_primary_fallback_sections(soup: BeautifulSoup, race_label_base: str):
    headers = list(soup.find_all(["h2","h3","h4"]))
    sections: List[Tuple[BeautifulSoup, str]] = []
    for i, h in enumerate(headers):
        txt = nws(h.get_text(" "))
        if not PRIMARY_WORD_RE.search(txt): 
            continue
        party = _detect_primary_party(txt)
        label = f"{race_label_base} — {party} primary" if party else f"{race_label_base} — Primary"
        end = None
        for j in range(i+1, len(headers)):
            end = headers[j]; 
            break
        sec = _wrap_between(soup, h, end)
        sections.append((sec, label))
    return sections
