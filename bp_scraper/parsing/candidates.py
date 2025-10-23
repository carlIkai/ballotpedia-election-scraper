from __future__ import annotations
from typing import List
from bs4 import BeautifulSoup
from bp_scraper.core.constants import PARTY_FROM_CLASS
from bp_scraper.parsing.normalize import nws, norm_name
from bp_scraper.parsing.tables import _infer_party_from_name

def parse_candidate_cards(soup: BeautifulSoup, state: str, year: int, race_label: str, source_url: str) -> List[dict]:
    out: List[dict] = []
    for card in list(soup.select("div.cc-container")):
        header = card.select_one(".cc-header")
        name = nws(header.get_text(" ")) if header else None
        if not name: continue
        party = None
        if header:
            for cls in header.get("class", []):
                if cls in PARTY_FROM_CLASS:
                    party = PARTY_FROM_CLASS[cls]; break
        if not party:
            party_el = card.select_one(".cc-party")
            if party_el: party = nws(party_el.get_text(" "))
        if not party: party = _infer_party_from_name(name)

        out.append({
            "state": state, "race": race_label, "year": year, "name": name,
            "name_clean": norm_name(name), "party": party, "incumbent": False, "source_url": source_url,
        })
    return out

_SECTION_PARTY_KEYWORDS = [
    ("democratic","Democratic"),("republican","Republican"),("libertarian","Libertarian"),("green","Green"),
    ("constitution","Constitution"),("peace and freedom","Peace and Freedom"),("socialist workers","Socialist Workers"),
    ("working families","Working Families"),("american independent","American Independent"),
    ("independent american","Independent American"),("progressive","Progressive"),
    ("aloha ʻāina","Aloha ʻĀina"),("aloha aina","Aloha ʻĀina"),("independent","Independent"),("nonpartisan","Nonpartisan"),
]

def scan_section_party_keywords(sec: BeautifulSoup):
    from bp_scraper.parsing.normalize import nws
    txt = nws(sec.get_text(" ")).lower()
    hits = set()
    for keyword, proper in _SECTION_PARTY_KEYWORDS:
        if keyword in txt: hits.add(proper)
    if len(hits) == 1: 
        return next(iter(hits))
    return None

def backfill_party_from_label(cards: List[dict]) -> None:
    from bp_scraper.core.constants import PRIMARY_PARTY_FROM_LABEL
    for card in cards:
        cur = (card.get("party") or "").strip()
        if cur and cur not in {"Other","Nonpartisan"}: continue
        low = (card.get("race") or "").lower()
        for key, val in PRIMARY_PARTY_FROM_LABEL.items():
            if key in low:
                card["party"] = val
                break
