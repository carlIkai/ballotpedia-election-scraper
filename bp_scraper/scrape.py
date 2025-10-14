from __future__ import annotations
from typing import Dict, List, Optional, Tuple
import re

from bs4 import BeautifulSoup

from .http_urls import get_soup
from .dates import parse_election_dates_from_page
from .sections import find_result_sections, ny_primary_fallback_sections
from .tables import parse_results_table
from .candidates import parse_candidate_cards, backfill_party_from_label, scan_section_party_keywords
from .utils import normalize_state_name, norm_name
from .constants import HOUSE_AT_LARGE_STATES
from .summarize import summarize_race


def _party_from_rows_and_cards(rows: List[dict], cards: List[dict]) -> Optional[str]:
    from .tables import _infer_party_from_name
    inferred: List[str] = []
    for row in rows:
        if row.get("party_hint"):
            inferred.append(row["party_hint"])
        else:
            party = _infer_party_from_name(row.get("candidate", ""))
            if party:
                inferred.append(party)
    if not inferred and cards:
        for card in cards:
            if card.get("party"):
                inferred.append(card["party"])
    uniq = {x for x in inferred if x}
    if len(uniq) == 1:
        return next(iter(uniq))
    return None


APOS_ANY = r"(?:%27|%E2%80%99|'|\u2019)"


def _ordinal_token_to_number(tok: str) -> Optional[int]:
    from .constants import ORD_RE
    match = ORD_RE.search(tok or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _district_from_url(url: str) -> Optional[str]:
    import urllib.parse, re as _re
    path = urllib.parse.unquote(url)
    match = _re.search(
        rf"/([A-Za-z_]+){APOS_ANY}s?_((?:At[-_]large)|(?:\d{{1,2}}(?:st|nd|rd|th)))_Congressional_District_election,_\d{{4}}$",
        path,
        _re.I,
    )
    if not match:
        return None
    token = match.group(2)
    if _re.search(r"at[-_]large", token, _re.I):
        return "At-large"
    district_num = _ordinal_token_to_number(token)
    if district_num is not None:
        return f"District {district_num}"
    return None


def _state_from_url(url: str, year: int) -> str:
    import urllib.parse, re as _re
    from .utils import strip_parenthetical

    path = urllib.parse.unquote(url)
    match = _re.search(r"_in_([^,]+),_" + _re.escape(str(year)), path)
    if match:
        return normalize_state_name(strip_parenthetical(match.group(1).replace("_", " ")))
    match_district_form = _re.search(
        rf"/([A-Za-z_]+){APOS_ANY}s?_(?:At[-_]large|\d{{1,2}}(?:st|nd|rd|th))_Congressional_District_election,_\d{{4}}$",
        path,
        _re.I,
    )
    if match_district_form:
        return normalize_state_name(strip_parenthetical(match_district_form.group(1).replace("_", " ")))
    match_tail_fallback = _re.search(r"/([^/]+),_" + _re.escape(str(year)) + r"$", path)
    if match_tail_fallback:
        guess = match_tail_fallback.group(1).replace("_", " ")
        guess = guess.replace("’", "'")
        guess = _re.sub(
            r"^(United States House(?: of Representatives)? elections? in|United States Senate (?:special )?election in)\s+",
            "",
            guess,
            flags=_re.I,
        )
        return normalize_state_name(strip_parenthetical(guess))
    return normalize_state_name(path)


def _race_label_from_url(url: str, chamber: str, state_for_fallback: Optional[str] = None) -> str:
    if chamber == "senate":
        return "U.S. Senate (special)" if "United_States_Senate_special_election_in_" in url else "U.S. Senate"
    else:
        district = _district_from_url(url)
        base = "U.S. House"
        if district:
            return f"{base} — {district}"
        if state_for_fallback and normalize_state_name(state_for_fallback) in HOUSE_AT_LARGE_STATES:
            return f"{base} — At-large"
        return base


def _primary_party_from_label(label: str):
    from .constants import PRIMARY_PARTY_FROM_LABEL
    low = (label or "").lower()
    for key, val in PRIMARY_PARTY_FROM_LABEL.items():
        if key in low:
            return val
    return None


def _is_jungle_label(state: str, label: str):
    low = label.lower()
    state = normalize_state_name(state).lower()
    if "nonpartisan primary" in low:
        if state == "alaska":
            return True, 4
        if state in {"california", "washington"}:
            return True, 2
    return False, None


def la_jungle_fallback_sections(soup: BeautifulSoup, base_race_label: str) -> List[Tuple[BeautifulSoup, str]]:

    import re as _re
    out: List[Tuple[BeautifulSoup, str]] = []

    def looks_like_results_table(tbl: BeautifulSoup) -> bool:
        head = tbl.find("th")
        if not head:
            return False
        text = (head.get_text(" ") or "").lower()
        return any(k in text for k in ["candidate", "votes", "percent", "party"])

    for h in soup.find_all(["h2", "h3", "h4"]):
        htext = (h.get_text(" ") or "").strip().lower()
        if not htext:
            continue
        if not any(k in htext for k in ["nonpartisan", "primary", "results"]):
            continue

        for sib in h.next_siblings:
            if getattr(sib, "name", None) in {"h2", "h3", "h4"}:
                break
            if getattr(sib, "name", None) in {"table", "div", "section"}:
                tbl = sib if getattr(sib, "name", None) == "table" else sib.find("table")
                if tbl and "wikitable" in " ".join(tbl.get("class", [])) and looks_like_results_table(tbl):
                    out.append((tbl, base_race_label))
                    break

    seen = set()
    uniq: List[Tuple[BeautifulSoup, str]] = []
    for sec, lbl in out:
        key = id(sec)
        if key in seen:
            continue
        seen.add(key)
        uniq.append((sec, lbl))
    return uniq


def scrape_page(
    url: str,
    year: int,
    chamber: str,
    verbose: bool = False,
    primary: bool = False,
    delay: Optional[float] = None,
    retries: Optional[int] = None,
) -> Tuple[List[dict], List[Dict[str, object]], List[dict]]:
    """
    Scrape a Ballotpedia state/district page and return:
      - all_internal_rows: raw table rows (list of dicts)
      - race_summaries: one summary per race/label (winner/advancers)
      - all_cards: candidate cards (normalized)
    """
    soup = get_soup(url, delay=delay, retries=retries)
    state = normalize_state_name(_state_from_url(url, year))
    base_race_label = _race_label_from_url(url, chamber, state_for_fallback=state)

    try:
        parse_election_dates_from_page(soup, state, year)
    except Exception:
        pass

    sections = find_result_sections(soup, year, state, base_race_label, primary=primary)

    if primary and not sections and state.lower() in {"new york"}:
        sections = ny_primary_fallback_sections(soup, base_race_label)

    if (not primary) and not sections and state.lower() == "louisiana":
        la_primary_sections = find_result_sections(soup, year, state, base_race_label, primary=True)
        if la_primary_sections:
            fixed_sections: List[Tuple[BeautifulSoup, str]] = []
            for sec, _lbl in la_primary_sections:
                fixed_sections.append((sec, base_race_label))
            sections = fixed_sections

        if not sections:
            sections = la_jungle_fallback_sections(soup, base_race_label)

    if not sections:
        if verbose:
            mode = "primary" if primary else "general"
            print(f"[warn] {state}: skipped (no {year} {mode} results) — {url}")
        return [], [], []

    all_internal_rows: List[dict] = []
    race_summaries: List[Dict[str, object]] = []
    all_cards: List[dict] = []

    if not primary:
        cand_rows_all: List[dict] = []
        for sec, label in sections:
            cand_rows_all.extend(parse_results_table(sec, state, year, label, source_url=url))

        if not cand_rows_all:
            if verbose:
                print(f"[warn] {state}: {year} section(s) found but no results rows parsed; skipping — {url}")
            return [], [], []

        table_names = {r["candidate_clean"] for r in cand_rows_all}
        cards = parse_candidate_cards(soup, state, year, base_race_label, source_url=url)
        cards = [c for c in cards if c["name_clean"] in table_names]

        if not cards:
            from .tables import _infer_party_from_name
            cards = [
                {
                    "state": state,
                    "race": base_race_label,
                    "year": year,
                    "name": r["candidate"],
                    "name_clean": r["candidate_clean"],
                    "party": r.get("party_hint") or _infer_party_from_name(r["candidate"]),
                    "incumbent": bool(r.get("incumbent", False)),
                    "total_votes": r.get("total_votes"),
                    "source_url": url,
                }
                for r in cand_rows_all
            ]
        else:
            card_names = {c["name_clean"] for c in cards}
            from .tables import _infer_party_from_name
            for r in cand_rows_all:
                if r["candidate_clean"] not in card_names:
                    cards.append(
                        {
                            "state": state,
                            "race": base_race_label,
                            "year": year,
                            "name": r["candidate"],
                            "name_clean": r["candidate_clean"],
                            "party": r.get("party_hint") or _infer_party_from_name(r["candidate"]),
                            "incumbent": bool(r.get("incumbent", False)),
                            "total_votes": r.get("total_votes"),
                            "source_url": url,
                        }
                    )

        backfill_party_from_label(cards)
        votes_map: Dict[str, Optional[int]] = {}
        for r in cand_rows_all:
            key = r["candidate_clean"]
            rvotes = r.get("total_votes")
            if isinstance(rvotes, int):
                if (key not in votes_map) or (votes_map[key] is None) or (rvotes > (votes_map[key] or -1)):
                    votes_map[key] = rvotes
        incumbency_map = {r["candidate_clean"]: bool(r["incumbent"]) for r in cand_rows_all}
        for c in cards:
            ck = c["name_clean"]
            c["incumbent"] = incumbency_map.get(ck, False)
            c.setdefault("total_votes", votes_map.get(ck))

        summary = {
            "state": state,
            "race": base_race_label,
            "year": year,
            "source_url": url,
            "is_jungle_primary": False,
            "primary_party": None,
        }
        summary.update(summarize_race(cand_rows_all, allowed_clean_names=None))

        winner_norm = norm_name(summary.get("winner_name") or "")
        for c in cards:
            c["race"] = base_race_label
            c["is_winner"] = c["name_clean"] == winner_norm
            c["is_advancer"] = c["is_winner"]

        all_internal_rows.extend(cand_rows_all)
        race_summaries.append(summary)
        all_cards.extend(cards)

        if verbose:
            print(f"[{state}] winner={summary['winner_name']} ({base_race_label})")

    else:
        for sec, label_in in sections:
            label = label_in
            cand_rows = parse_results_table(sec, state, year, label, source_url=url)

            fabricated_rows: List[dict] = []
            cancelled_party: Optional[str] = None
            if not cand_rows:
                adv = _extract_canceled_primary_advancer(sec)
                if adv:
                    fabricated_rows = [
                        {
                            "state": state,
                            "race": label,
                            "year": year,
                            "candidate": adv,
                            "candidate_clean": norm_name(adv),
                            "pct": None,
                            "total_votes": None,
                            "incumbent": bool(re.search(r"\bincumbent\b", adv, flags=re.I)),
                            "party_hint": None,
                            "source_url": url,
                        }
                    ]
                    cand_rows = fabricated_rows
                    cancelled_party = _extract_canceled_primary_party(sec)

            if not cand_rows:
                continue

            table_names = {r["candidate_clean"] for r in cand_rows}
            cards = parse_candidate_cards(soup, state, year, label, source_url=url)
            cards = [c for c in cards if c["name_clean"] in table_names]
            card_names = {c["name_clean"] for c in cards}

            from .tables import _infer_party_from_name
            for r in cand_rows:
                if r["candidate_clean"] not in card_names:
                    cards.append(
                        {
                            "state": state,
                            "race": label,
                            "year": year,
                            "name": r["candidate"],
                            "name_clean": r["candidate_clean"],
                            "party": r.get("party_hint") or _infer_party_from_name(r["candidate"]),
                            "incumbent": bool(r.get("incumbent", False)),
                            "total_votes": r.get("total_votes"),
                            "source_url": url,
                        }
                    )

            backfill_party_from_label(cards)

            if label.endswith(" — Primary"):
                inferred_party = (
                    cancelled_party
                    or _party_from_rows_and_cards(cand_rows, cards)
                    or scan_section_party_keywords(sec)
                )
                if inferred_party and inferred_party.strip().lower() == "other":
                    continue
                if (state.lower() == "utah") and (inferred_party in {"Independent", "Independent American"}):
                    inferred_party = "Independent American"
                if inferred_party:
                    label = label.replace(" — Primary", f" — {inferred_party} primary")
                    for c in cards:
                        c["race"] = label
                    for r in cand_rows:
                        r["race"] = label
                else:
                    if normalize_state_name(state) in {"Alaska", "California", "Washington", "Louisiana"}:
                        label = label.replace(" — Primary", " — Nonpartisan primary")
                        for c in cards:
                            c["race"] = label
                        for r in cand_rows:
                            r["race"] = label
                    else:
                        continue

            if (" — Independent primary" in label) and (state.lower() == "utah"):
                label = label.replace(" — Independent primary", " — Independent American primary")
                for c in cards:
                    c["race"] = label
                for r in cand_rows:
                    r["race"] = label

            votes_map: Dict[str, Optional[int]] = {}
            for r in cand_rows:
                key = r["candidate_clean"]
                rvotes = r.get("total_votes")
                if isinstance(rvotes, int):
                    if (key not in votes_map) or (votes_map[key] is None) or (rvotes > (votes_map[key] or -1)):
                        votes_map[key] = rvotes
            incumbency_map = {r["candidate_clean"]: bool(r["incumbent"]) for r in cand_rows}
            for c in cards:
                ck = c["name_clean"]
                c["race"] = label
                c["incumbent"] = incumbency_map.get(ck, False)
                c.setdefault("total_votes", votes_map.get(ck))

            is_jungle, jungle_top = _is_jungle_label(state, label)
            summary = {
                "state": state,
                "race": label,
                "year": year,
                "source_url": url,
                "is_jungle_primary": bool(is_jungle),
                "primary_party": _primary_party_from_label(label),
            }
            summary.update(summarize_race(cand_rows, allowed_clean_names=None, jungle_top=jungle_top))

            winner_norm = norm_name(summary.get("winner_name") or "")
            advancers_set = set(norm_name(n) for n in (summary.get("advancers") or []))
            for c in cards:
                nm = c["name_clean"]
                c["is_winner"] = nm == winner_norm
                c["is_advancer"] = (nm in advancers_set) if is_jungle else c["is_winner"]
                if not c.get("party"):
                    pp = summary.get("primary_party")
                    if pp and pp != "Nonpartisan":
                        c["party"] = pp
                if (state.lower() == "utah") and ("Independent American primary" in label):
                    if not c.get("party") or c["party"].strip().lower() in {"independent", "other", "nonpartisan", ""}:
                        c["party"] = "Independent American"

            if state.lower() == "new york" and " — Other primary" in label:
                continue

            all_internal_rows.extend(cand_rows)
            race_summaries.append(summary)
            all_cards.extend(cards)
            if verbose:
                print(f"[{state}] winner={summary['winner_name']} ({label})")

    return all_internal_rows, race_summaries, all_cards


def _extract_canceled_primary_advancer(sec: BeautifulSoup) -> Optional[str]:
    import re
    from .utils import nws
    txt = nws(sec.get_text(" "))
    m = re.search(r"primary election was canceled\.\s*(?:Incumbent\s+)?(.+?)\s+advanced", txt, flags=re.I)
    if not m:
        return None
    candidate_guess = m.group(1).strip()
    anchors = sec.find_all("a")
    for a in anchors:
        atext = nws(a.get_text(" "))
        if atext and (atext.lower() in candidate_guess.lower() or candidate_guess.lower() in atext.lower()):
            return atext
    return candidate_guess


def _extract_canceled_primary_party(sec: BeautifulSoup) -> Optional[str]:
    import re
    from .constants import PRIMARY_PARTY_FROM_LABEL, EXTRA_PARTY_KEYS
    from .utils import nws

    CANCELLED_PRIMARY_PARTY_RE = re.compile(
        r"\b([A-Za-zʻ’\-\s]+?)\s+primary\s+election\s+was\s+canceled\b", re.I
    )
    txt = nws(sec.get_text(" "))
    m = CANCELLED_PRIMARY_PARTY_RE.search(txt)
    if not m:
        return None
    raw = m.group(1).lower().strip()
    for key, val in PRIMARY_PARTY_FROM_LABEL.items():
        base = key.replace(" primary", "")
        if base in raw:
            return val
    for key, val in EXTRA_PARTY_KEYS.items():
        if key in raw:
            return val
    for key, val in [
        ("democratic", "Democratic"),
        ("republican", "Republican"),
        ("libertarian", "Libertarian"),
        ("green", "Green"),
        ("working families", "Working Families"),
        ("constitution", "Constitution"),
        ("progressive", "Progressive"),
        ("independent american", "Independent American"),
        ("aloha aina", "Aloha ʻĀina"),
        ("aloha ʻāina", "Aloha ʻĀina"),
        ("independent", "Independent"),
    ]:
        if key in raw:
            return val
    return None
