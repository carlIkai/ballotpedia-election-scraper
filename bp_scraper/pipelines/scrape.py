from __future__ import annotations

"""
Page scraping orchestration.

This module ties together the parsing primitives to scrape a single Ballotpedia election page:
- fetch HTML
- infer state and race label from the URL
- locate the relevant result sections (general or primary)
- parse results tables into candidate rows
- parse candidate cards and align them to table candidates
- produce race-level summaries and candidate-level records

The output shapes are intentionally flat dicts so downstream DataFrame transforms stay simple.
"""

from typing import Any, Dict, List, Optional, Tuple
import re

from bs4 import BeautifulSoup

from bp_scraper.io.http import get_soup
from bp_scraper.parsing.dates import parse_election_dates_from_page
from bp_scraper.parsing.sections import find_result_sections, ny_primary_fallback_sections
from bp_scraper.parsing.tables import parse_results_table
from bp_scraper.parsing.candidates import (
    parse_candidate_cards,
    backfill_party_from_label,
    scan_section_party_keywords,
)
from bp_scraper.parsing.normalize import normalize_state_name, norm_name
from bp_scraper.core.constants import HOUSE_AT_LARGE_STATES, PAST_ELEX_RE
from bp_scraper.transform.summarize import summarize_race


def _party_from_rows_and_cards(rows: List[dict], cards: List[dict]) -> Optional[str]:
    """Infer a single party when all evidence agrees."""
    from bp_scraper.parsing.tables import _infer_party_from_name

    inferred: List[str] = []

    for row in rows:
        if row.get("party_hint"):
            inferred.append(row["party_hint"])
            continue

        guess = _infer_party_from_name(row.get("candidate", ""))
        if guess:
            inferred.append(guess)

    if not inferred and cards:
        for card in cards:
            if card.get("party"):
                inferred.append(card["party"])

    unique = {p for p in inferred if p}
    if len(unique) == 1:
        return next(iter(unique))
    return None


# Apostrophes may appear as straight/curly or URL-encoded in district URLs.
APOS_ANY = r"(?:%27|%E2%80%99|'|\u2019)"


def _ordinal_token_to_number(ordinal_token: str) -> Optional[int]:
    """Convert an ordinal token like '3rd' to an integer."""
    from bp_scraper.core.constants import ORD_RE

    m = ORD_RE.search(ordinal_token or "")
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _district_from_url(url: str) -> Optional[str]:
    """Extract a district label from a canonical House district URL."""
    import urllib.parse
    import re as regex_mod

    path = urllib.parse.unquote(url)
    m = regex_mod.search(
        rf"/([A-Za-z_]+){APOS_ANY}s?_((?:At[-_]large)|(?:\d{{1,2}}(?:st|nd|rd|th)))_Congressional_District_election,_\d{{4}}$",
        path,
        regex_mod.I,
    )
    if not m:
        return None

    token = m.group(2)
    if regex_mod.search(r"at[-_]large", token, regex_mod.I):
        return "At-large"

    district_num = _ordinal_token_to_number(token)
    if district_num is not None:
        return f"District {district_num}"

    return None


def _state_from_url(url: str, year: int) -> str:
    """Extract the state name from common Ballotpedia URL formats."""
    import urllib.parse
    import re as regex_mod
    from bp_scraper.parsing.normalize import strip_parenthetical

    path = urllib.parse.unquote(url)

    # Common form: "..._in_State,_YYYY"
    m = regex_mod.search(r"_in_([^,]+),_" + regex_mod.escape(str(year)), path)
    if m:
        raw = m.group(1).replace("_", " ")
        return normalize_state_name(strip_parenthetical(raw))

    # House district form: "/State's_3rd_Congressional_District_election,_YYYY"
    m = regex_mod.search(
        rf"/([A-Za-z_]+){APOS_ANY}s?_(?:At[-_]large|\d{{1,2}}(?:st|nd|rd|th))_Congressional_District_election,_\d{{4}}$",
        path,
        regex_mod.I,
    )
    if m:
        raw = m.group(1).replace("_", " ")
        return normalize_state_name(strip_parenthetical(raw))

    # Last path segment before ",_YYYY"
    m = regex_mod.search(r"/([^/]+),_" + regex_mod.escape(str(year)) + r"$", path)
    if m:
        state_guess = m.group(1).replace("_", " ").replace("’", "'")
        state_guess = regex_mod.sub(
            r"^(United States House(?: of Representatives)? elections? in|United States Senate (?:special )?election in)\s+",
            "",
            state_guess,
            flags=regex_mod.I,
        )
        return normalize_state_name(strip_parenthetical(state_guess))

    return normalize_state_name(path)


def _race_label_from_url(
    url: str,
    chamber: str,
    state_for_fallback: Optional[str] = None,
    scope: str = "federal",
) -> str:
    """Create a stable race label from the URL and scrape context."""
    low = (url or "").lower()
    state_norm = normalize_state_name(state_for_fallback).lower() if state_for_fallback else ""

    if scope == "state":
        if "lieutenant_gubernatorial_election" in low:
            return "Lieutenant Governor"
        if "gubernatorial_election" in low:
            return "Governor"
        if "attorney_general_election" in low:
            return "Attorney General"

        # Some pages bundle offices together.
        if ("gubernatorial" in low) and ("lieutenant" in low) and ("election" in low):
            return "Governor / Lieutenant Governor"

        # Upper chamber pages are not consistently named across states.
        if any(tok in low for tok in ["state_senate_election", "state_senate_elections", "state_senate", "senate_elections"]) and (
            "state" in low or "legislature" in low or "state_senate" in low
        ):
            return "State Senate"

        # Lower chamber naming varies by state.
        if any(tok in low for tok in [
            "house_of_delegates_election",
            "house_of_delegates_elections",
            "house_of_delegates",
            "state_house_election",
            "state_house_elections",
            "state_house",
            "state_assembly_election",
            "state_assembly_elections",
            "state_assembly",
            "house_of_representatives_election",
            "house_of_representatives_elections",
            "house_of_representatives",
        ]):
            if state_norm == "virginia":
                return "House of Delegates"
            return "State House"

        return "State race"

    if chamber == "senate":
        if "united_states_senate_special_election_in_" in low:
            return "U.S. Senate (special)"
        return "U.S. Senate"

    base_label = "U.S. House"
    district = _district_from_url(url or "")
    if district:
        return f"{base_label} — {district}"

    if state_for_fallback and normalize_state_name(state_for_fallback) in HOUSE_AT_LARGE_STATES:
        return f"{base_label} — At-large"

    return base_label


def _primary_party_from_label(label: str) -> Optional[str]:
    """Extract the party for a labeled primary section when present."""
    from bp_scraper.core.constants import PRIMARY_PARTY_FROM_LABEL

    low = (label or "").lower()
    for key, val in PRIMARY_PARTY_FROM_LABEL.items():
        if key in low:
            return val
    return None


def _is_jungle_label(state: str, label: str) -> Tuple[bool, Optional[int]]:
    """Detect blanket/jungle primaries and their typical advancer counts."""
    low_label = (label or "").lower()
    st = normalize_state_name(state).lower()

    if "nonpartisan primary" in low_label:
        if st == "alaska":
            return True, 4
        if st in {"california", "washington"}:
            return True, 2

    return False, None


def _looks_like_results_table_relaxed(table_node: BeautifulSoup) -> bool:
    """Loose results-table detection for pages with nonstandard markup."""
    class_names = " ".join(table_node.get("class", []))
    if "results_table" in class_names:
        return True
    if table_node.select_one("tr.results_row"):
        return True

    header_texts = [(cell.get_text(" ") or "").strip().lower() for cell in table_node.select("thead th")]
    if not header_texts:
        first_row = table_node.find("tr")
        if first_row:
            header_texts = [(cell.get_text(" ") or "").strip().lower() for cell in first_row.find_all(["th"])]

    if header_texts:
        joined = " ".join(header_texts)
        if any(k in joined for k in ["candidate", "candidates", "votes", "vote", "percent", "percentage", "party", "nominee"]):
            return True

    data_texts = [(td.get_text(" ") or "").strip() for td in table_node.find_all("td")]
    percent_like = sum(bool(re.search(r"\d+(?:\.\d+)?\s*%$", t)) for t in data_texts)
    return percent_like >= 2


def _nearby_heading_text(table_node: BeautifulSoup, max_headings: int = 4) -> str:
    """Collect a small heading window above a table for context filtering."""
    pieces: List[str] = []
    for h in table_node.find_all_previous(["h1", "h2", "h3", "h4"], limit=max_headings):
        t = (h.get_text(" ") or "").strip()
        if t:
            pieces.append(t)
    return " | ".join(pieces)


def _prefer_tables_for_year(tables: List[BeautifulSoup], year: int, primary: bool) -> List[BeautifulSoup]:
    """Prefer tables whose nearby headings match the requested year and mode."""
    want_year = str(year)
    scored: List[Tuple[int, BeautifulSoup]] = []

    for t in tables:
        ctx = (_nearby_heading_text(t) or "").lower()

        if PAST_ELEX_RE.search(ctx or ""):
            continue

        score = 0
        if want_year in ctx:
            score += 3
        if primary and ("primary" in ctx):
            score += 2
        if (not primary) and ("general" in ctx):
            score += 2

        scored.append((score, t))

    if not scored:
        return tables

    best = max(score for score, _ in scored)
    if best <= 0:
        return tables

    return [t for score, t in scored if score == best]


_CHECKMARK_RE = re.compile(r"[✔✓★]")


def _row_has_winner_mark(table_row: BeautifulSoup) -> bool:
    """Detect common winner markers used in Ballotpedia tables."""
    for el in table_row.find_all(True, recursive=True):
        classes = " ".join(el.get("class", []) or [])
        if "winner" in classes.split():
            return True

        title_attr = (el.get("title") or "").lower()
        aria_attr = (el.get("aria-label") or "").lower()
        if "winner" in title_attr or "winner" in aria_attr:
            return True

    text_low = (table_row.get_text(" ") or "").lower()
    if _CHECKMARK_RE.search(text_low):
        return True
    if "elected" in text_low:
        return True

    return False


def _best_anchor_name_in_row(table_row: BeautifulSoup) -> Optional[str]:
    """Pick the most name-like anchor text in a results row."""
    candidates: List[str] = []
    for a in table_row.find_all("a"):
        t = (a.get_text(" ") or "").strip()
        if not t:
            continue
        if _CHECKMARK_RE.fullmatch(t):
            continue
        if not re.search(r"[A-Za-z]", t):
            continue
        candidates.append(t)

    if not candidates:
        return None

    candidates.sort(key=len, reverse=True)
    return candidates[0]


def _extract_candidate_name(table_row: BeautifulSoup, default_cell_text: str) -> str:
    """Extract a candidate name, preferring anchor text."""
    best = _best_anchor_name_in_row(table_row)
    if best:
        return best.strip()
    return (default_cell_text or "").strip()


def _parse_la_results_rows(table_node: BeautifulSoup, state: str, year: int, label: str, source_url: str) -> List[dict]:
    """Parse Louisiana-style results rows that use 'tr.results_row' markup."""
    parsed: List[dict] = []

    for result_row in table_node.select("tr.results_row"):
        cells = result_row.find_all("td")
        if not cells:
            continue

        candidate_cell = cells[0]
        candidate_name = _extract_candidate_name(result_row, candidate_cell.get_text(" "))
        candidate_clean = norm_name(candidate_name)

        pct_value = None
        for c in cells[1:]:
            txt = (c.get_text(" ") or "").strip()
            m = re.search(r"(\d+(?:\.\d+)?)\s*%$", txt)
            if m:
                try:
                    pct_value = float(m.group(1))
                except Exception:
                    pct_value = None
                break

        votes_value = None
        for c in cells[1:]:
            num = (c.get_text(" ") or "").replace(",", "").strip()
            if re.fullmatch(r"\d{1,12}", num):
                try:
                    votes_value = int(num)
                except Exception:
                    votes_value = None
                break

        cand_low = candidate_cell.get_text(" ").lower()
        is_incumbent = ("incumbent" in cand_low) or (" (i)" in cand_low) or (" (i)" in cand_low)

        parsed.append(
            {
                "state": state,
                "race": label,
                "year": year,
                "candidate": candidate_name,
                "candidate_clean": candidate_clean,
                "pct": pct_value,
                "total_votes": votes_value,
                "incumbent": is_incumbent,
                "party_hint": None,
                "source_url": source_url,
                "is_winner_row": _row_has_winner_mark(result_row),
            }
        )

    return parsed


def _parse_headered_results_rows(table_node: BeautifulSoup, state: str, year: int, label: str, source_url: str) -> List[dict]:
    """Parse tables with explicit headers that label candidate/votes/percent columns."""
    header_cells = table_node.select("thead th")
    if not header_cells:
        first_row = table_node.find("tr")
        if first_row:
            header_cells = first_row.find_all("th")

    header_map: Dict[int, str] = {}
    if header_cells:
        for i, cell in enumerate(header_cells):
            header_map[i] = (cell.get_text(" ") or "").strip().lower()

    def find_column_index(keys: List[str]) -> Optional[int]:
        for idx, txt in header_map.items():
            if any(k in txt for k in keys):
                return idx
        return None

    candidate_col = find_column_index(["candidate", "candidates", "nominee"])
    percent_col = find_column_index(["percent", "percentage", "%"])
    votes_col = find_column_index(["vote", "votes"])

    body_rows = table_node.select("tbody tr")
    if not body_rows:
        all_rows = table_node.find_all("tr")
        body_rows = all_rows[1:] if (header_cells and all_rows) else all_rows

    parsed: List[dict] = []

    for r in body_rows:
        if r.find("th") and not r.find("td"):
            continue
        cells = r.find_all("td")
        if not cells:
            continue

        def cell_text(ci: Optional[int]) -> str:
            if ci is None or ci >= len(cells):
                return ""
            return (cells[ci].get_text(" ") or "").strip()

        fallback_cell = cells[candidate_col] if (candidate_col is not None and candidate_col < len(cells)) else cells[0]
        candidate_name = _extract_candidate_name(r, fallback_cell.get_text(" "))
        if not candidate_name:
            continue

        candidate_clean = norm_name(candidate_name)

        pct_value = None
        m = re.search(r"(\d+(?:\.\d+)?)\s*%$", cell_text(percent_col))
        if m:
            try:
                pct_value = float(m.group(1))
            except Exception:
                pct_value = None

        votes_value = None
        votes_text = cell_text(votes_col).replace(",", "")
        if re.fullmatch(r"\d{1,12}", votes_text or ""):
            try:
                votes_value = int(votes_text)
            except Exception:
                votes_value = None

        fallback_low = fallback_cell.get_text(" ").lower()
        is_incumbent = ("incumbent" in fallback_low) or (" (i)" in fallback_low) or (" (i)" in fallback_low)

        parsed.append(
            {
                "state": state,
                "race": label,
                "year": year,
                "candidate": candidate_name,
                "candidate_clean": candidate_clean,
                "pct": pct_value,
                "total_votes": votes_value,
                "incumbent": is_incumbent,
                "party_hint": None,
                "source_url": source_url,
                "is_winner_row": _row_has_winner_mark(r),
            }
        )

    return parsed


def _normalize_sections(sections: Any, default_label: str) -> List[Tuple[BeautifulSoup, str]]:
    """Normalize section outputs into unique (node, label) pairs."""
    out: List[Tuple[BeautifulSoup, str]] = []
    if not sections:
        return out

    def ensure_pair(item: Any) -> Optional[Tuple[BeautifulSoup, str]]:
        if item is None:
            return None
        if isinstance(item, (list, tuple)):
            if len(item) >= 2:
                node, label = item[0], item[1]
                if node is None:
                    return None
                return node, (label if isinstance(label, str) and label else default_label)
            if len(item) == 1:
                node = item[0]
                if node is None:
                    return None
                return node, default_label
            return None
        return item, default_label

    seen = set()
    for raw in sections:
        pair = ensure_pair(raw)
        if not pair:
            continue
        node, label = pair
        key = id(node)
        if key in seen:
            continue
        seen.add(key)
        out.append((node, label))

    return out


SYM_WINNERS = {"✔", "✓", "★", "—", "-"}


def _resolve_symbol_winner(summary: Dict[str, Any], rows: List[dict], cards: List[dict]) -> None:
    """Replace placeholder winner symbols with an actual candidate name when possible."""
    current = (summary.get("winner_name") or "").strip()
    if current and current not in SYM_WINNERS:
        return

    winner_rows = [r for r in rows if r.get("is_winner_row")]
    if winner_rows:
        summary["winner_name"] = winner_rows[0]["candidate"]
        return

    def score(r: dict) -> Tuple[float, int]:
        pct = r.get("pct")
        votes = r.get("total_votes")
        pct_s = float(pct) if isinstance(pct, (int, float)) else -1.0
        votes_s = int(votes) if isinstance(votes, int) else -1
        return (pct_s, votes_s)

    best = None
    for r in rows:
        if best is None or score(r) > score(best):
            best = r

    if best and (best.get("pct") is not None or best.get("total_votes") is not None):
        summary["winner_name"] = best["candidate"]
        return

    names = [r["candidate"] for r in rows if r.get("candidate")]
    if len(names) == 1:
        summary["winner_name"] = names[0]
        return

    card_names = [c["name"] for c in cards if c.get("name")]
    if len(card_names) == 1:
        summary["winner_name"] = card_names[0]
        return

    def is_name_like(t: str) -> bool:
        txt = (t or "").strip()
        if not re.search(r"[A-Za-z]", txt):
            return False
        if _CHECKMARK_RE.fullmatch(txt):
            return False
        return True

    candidates = [r["candidate"] for r in rows if is_name_like(r.get("candidate", ""))]
    if candidates:
        candidates.sort(key=len, reverse=True)
        summary["winner_name"] = candidates[0]


def scrape_page(
    url: str,
    year: int,
    chamber: str,
    verbose: bool = False,
    primary: bool = False,
    delay: Optional[float] = None,
    retries: Optional[int] = None,
    scope: str = "federal",
) -> Tuple[List[dict], List[Dict[str, object]], List[dict]]:
    """Scrape one Ballotpedia election page into rows, race summaries, and candidate cards.

    Args:
        url: Page URL to scrape.
        year: Election year to target.
        chamber: "senate", "house", or "state".
        verbose: Print debug/warn messages.
        primary: Parse primary sections instead of general sections.
        delay: Optional request delay override (seconds).
        retries: Optional retry override.
        scope: "federal" or "state" to influence label logic.

    Returns:
        Tuple of:
        - parsed candidate rows (internal parsing shape)
        - race summaries (one per section/race label)
        - candidate cards (aligned to table candidates where possible)
    """
    soup = get_soup(url, delay=delay, retries=retries)
    state = normalize_state_name(_state_from_url(url, year))
    base_race_label = _race_label_from_url(url, chamber, state_for_fallback=state, scope=scope)

    # Election dates are best-effort; scraping should not fail if the date scan breaks.
    try:
        parse_election_dates_from_page(soup, state, year)
    except Exception:
        pass

    sections = find_result_sections(soup, year, state, base_race_label, primary=primary)

    # NY pages sometimes omit clear primary headers; fall back to a looser scan.
    if primary and not sections and state.lower() in {"new york"}:
        sections = ny_primary_fallback_sections(soup, base_race_label)

    # Louisiana pages frequently use custom markup; keep explicit fallbacks.
    if (not primary) and not sections and state.lower() == "louisiana":
        louisiana_primary_sections = find_result_sections(soup, year, state, base_race_label, primary=True)
        if louisiana_primary_sections:
            sections = [
                (section_node, base_race_label)
                for section_node, *_ in _normalize_sections(louisiana_primary_sections, base_race_label)
            ]

        if not sections:
            sections = find_result_sections(soup, year, state, base_race_label, primary=False)

        if not sections:
            relaxed_tables: List[BeautifulSoup] = []
            for table_node in soup.select("table.results_table"):
                if table_node.select("tr.results_row") or _looks_like_results_table_relaxed(table_node):
                    relaxed_tables.append(table_node)
            if relaxed_tables:
                sections = [(table_node, base_race_label) for table_node in relaxed_tables]

        if not sections:
            row_tables = []
            for result_row in soup.select("tr.results_row"):
                parent_table = result_row.find_parent("table")
                if parent_table is not None:
                    row_tables.append(parent_table)

            seen_ids = set()
            unique_tables = []
            for t in row_tables:
                if id(t) not in seen_ids:
                    seen_ids.add(id(t))
                    unique_tables.append(t)

            if unique_tables and verbose:
                print(f"[debug] Louisiana: captured {len(unique_tables)} results table(s) via row-up fallback — {url}")

            if unique_tables:
                sections = [(t, base_race_label) for t in unique_tables]

    sections = _normalize_sections(sections, base_race_label)

    if not sections:
        if verbose:
            mode = "primary" if primary else "general"
            print(f"[warn] {state}: skipped (no {year} {mode} results) — {url}")
        return [], [], []

    all_internal_rows: List[dict] = []
    race_summaries: List[Dict[str, object]] = []
    all_cards: List[dict] = []

    # General election parsing produces one summary for the base race label.
    if not primary:
        all_candidate_rows: List[dict] = []

        for section_node, label in sections:
            parsed_rows = parse_results_table(section_node, state, year, label, source_url=url)

            if not parsed_rows:
                table_node = section_node if getattr(section_node, "name", None) == "table" else section_node.find("table")
                if table_node and _looks_like_results_table_relaxed(table_node):
                    parsed_rows = _parse_la_results_rows(table_node, state, year, label, source_url=url)

            if not parsed_rows:
                table_node = section_node if getattr(section_node, "name", None) == "table" else section_node.find("table")
                if table_node and _looks_like_results_table_relaxed(table_node):
                    header_rows = _parse_headered_results_rows(table_node, state, year, label, source_url=url)
                    if header_rows:
                        parsed_rows = header_rows

            all_candidate_rows.extend(parsed_rows)

        # Last-resort sweep when sections were found but table parsing yields nothing.
        if not all_candidate_rows:
            sweep_tables: List[BeautifulSoup] = []
            sweep_tables.extend(soup.select(".results_table_container table"))
            sweep_tables.extend(soup.select("table.results_table"))

            seen_ids = set()
            unique_tables = []
            for t in sweep_tables:
                if id(t) not in seen_ids:
                    seen_ids.add(id(t))
                    unique_tables.append(t)

            unique_tables = _prefer_tables_for_year(unique_tables, year=year, primary=False)

            if unique_tables and verbose:
                print(f"[debug] sweep: parsing {len(unique_tables)} fallback table(s) — {url}")

            for t in unique_tables:
                all_candidate_rows.extend(_parse_la_results_rows(t, state, year, base_race_label, source_url=url))

        if verbose:
            print(f"[debug] parsed rows: {len(all_candidate_rows)} — {url}")

        if not all_candidate_rows:
            if verbose:
                print(f"[warn] {state}: {year} section(s) found but no results rows parsed; skipping — {url}")
            return [], [], []

        # Candidate cards are used for party and metadata, but must align to table candidates.
        table_clean = {r["candidate_clean"] for r in all_candidate_rows}
        candidate_cards = parse_candidate_cards(soup, state, year, base_race_label, source_url=url)
        candidate_cards = [c for c in candidate_cards if c["name_clean"] in table_clean]

        # If cards are missing, synthesize card-like dicts from table rows.
        if not candidate_cards:
            from bp_scraper.parsing.tables import _infer_party_from_name

            candidate_cards = [
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
                for r in all_candidate_rows
            ]
        else:
            # Backfill any table candidates that are not present as cards.
            card_names = {c["name_clean"] for c in candidate_cards}
            from bp_scraper.parsing.tables import _infer_party_from_name

            for r in all_candidate_rows:
                if r["candidate_clean"] not in card_names:
                    candidate_cards.append(
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

        backfill_party_from_label(candidate_cards)

        summary: Dict[str, Any] = {
            "state": state,
            "race": base_race_label,
            "year": year,
            "source_url": url,
            "is_jungle_primary": False,
            "primary_party": None,
        }
        summary.update(summarize_race(all_candidate_rows, allowed_clean_names=None))

        _resolve_symbol_winner(summary, all_candidate_rows, candidate_cards)

        # Normalize votes/incumbency onto cards for downstream joins.
        votes_map: Dict[str, Optional[int]] = {}
        for r in all_candidate_rows:
            k = r["candidate_clean"]
            v = r.get("total_votes")
            if isinstance(v, int):
                if (k not in votes_map) or (votes_map[k] is None) or (v > (votes_map[k] or -1)):
                    votes_map[k] = v

        incumbency_map = {r["candidate_clean"]: bool(r["incumbent"]) for r in all_candidate_rows}
        for c in candidate_cards:
            k = c["name_clean"]
            c["incumbent"] = incumbency_map.get(k, False)
            c.setdefault("total_votes", votes_map.get(k))

        # Winner detection is stored on cards for candidate-level export.
        winner_norm = norm_name(summary.get("winner_name") or "")
        for c in candidate_cards:
            c["race"] = base_race_label
            c["is_winner"] = c["name_clean"] == winner_norm
            c["is_advancer"] = c["is_winner"]

        all_internal_rows.extend(all_candidate_rows)
        race_summaries.append(summary)
        all_cards.extend(candidate_cards)

        if verbose:
            print(f"[{state}] winner={summary['winner_name']} ({base_race_label})")

    # Primary parsing emits a summary per primary section (party-specific where possible).
    else:
        for section_node, section_label in sections:
            label = section_label
            candidate_rows = parse_results_table(section_node, state, year, label, source_url=url)

            if not candidate_rows:
                table_node = section_node if getattr(section_node, "name", None) == "table" else section_node.find("table")
                if table_node and _looks_like_results_table_relaxed(table_node):
                    candidate_rows = _parse_la_results_rows(table_node, state, year, label, source_url=url)

            if not candidate_rows:
                table_node = section_node if getattr(section_node, "name", None) == "table" else section_node.find("table")
                if table_node and _looks_like_results_table_relaxed(table_node):
                    candidate_rows = _parse_headered_results_rows(table_node, state, year, label, source_url=url)

            # Canceled primaries may have no table; fabricate a single-row result.
            fabricated_rows: List[dict] = []
            cancelled_party: Optional[str] = None
            if not candidate_rows:
                canceled_advancer = _extract_canceled_primary_advancer(section_node)
                if canceled_advancer:
                    fabricated_rows = [
                        {
                            "state": state,
                            "race": label,
                            "year": year,
                            "candidate": canceled_advancer,
                            "candidate_clean": norm_name(canceled_advancer),
                            "pct": None,
                            "total_votes": None,
                            "incumbent": bool(re.search(r"\bincumbent\b", canceled_advancer, flags=re.I)),
                            "party_hint": None,
                            "source_url": url,
                        }
                    ]
                    candidate_rows = fabricated_rows
                    cancelled_party = _extract_canceled_primary_party(section_node)

            if not candidate_rows:
                continue

            # Candidate cards are still useful for party, but should be aligned to parsed candidates.
            table_clean = {r["candidate_clean"] for r in candidate_rows}
            candidate_cards = parse_candidate_cards(soup, state, year, label, source_url=url)
            candidate_cards = [c for c in candidate_cards if c["name_clean"] in table_clean]

            from bp_scraper.parsing.tables import _infer_party_from_name

            card_clean = {c["name_clean"] for c in candidate_cards}
            for r in candidate_rows:
                if r["candidate_clean"] not in card_clean:
                    candidate_cards.append(
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

            backfill_party_from_label(candidate_cards)

            # Convert unlabeled primary sections into party-specific labels when possible.
            if label.endswith(" — Primary"):
                inferred_party = cancelled_party or _party_from_rows_and_cards(candidate_rows, candidate_cards) or scan_section_party_keywords(section_node)

                if inferred_party and inferred_party.strip().lower() == "other":
                    continue

                if (state.lower() == "utah") and (inferred_party in {"Independent", "Independent American"}):
                    inferred_party = "Independent American"

                if inferred_party:
                    label = label.replace(" — Primary", f" — {inferred_party} primary")
                    for c in candidate_cards:
                        c["race"] = label
                    for r in candidate_rows:
                        r["race"] = label
                else:
                    if normalize_state_name(state) in {"Alaska", "California", "Washington", "Louisiana"}:
                        label = label.replace(" — Primary", " — Nonpartisan primary")
                        for c in candidate_cards:
                            c["race"] = label
                        for r in candidate_rows:
                            r["race"] = label
                    else:
                        continue

            summary: Dict[str, Any] = {
                "state": state,
                "race": label,
                "year": year,
                "source_url": url,
                "is_jungle_primary": False,
                "primary_party": _primary_party_from_label(label),
            }
            summary.update(summarize_race(candidate_rows, allowed_clean_names=None))

            _resolve_symbol_winner(summary, candidate_rows, candidate_cards)

            # Normalize votes/incumbency onto cards for downstream joins.
            votes_map: Dict[str, Optional[int]] = {}
            for r in candidate_rows:
                k = r["candidate_clean"]
                v = r.get("total_votes")
                if isinstance(v, int):
                    if (k not in votes_map) or (votes_map[k] is None) or (v > (votes_map[k] or -1)):
                        votes_map[k] = v

            incumbency_map = {r["candidate_clean"]: bool(r["incumbent"]) for r in candidate_rows}
            for c in candidate_cards:
                k = c["name_clean"]
                c["race"] = label
                c["incumbent"] = incumbency_map.get(k, False)
                c.setdefault("total_votes", votes_map.get(k))

            # Advancers differ for jungle primaries; treat winner as advancer otherwise.
            winner_norm = norm_name(summary.get("winner_name") or "")
            is_jungle_primary, _ = _is_jungle_label(state, label)
            advancers_norm = {norm_name(n) for n in (summary.get("advancers") or [])}

            for c in candidate_cards:
                k = c["name_clean"]
                c["is_winner"] = k == winner_norm
                c["is_advancer"] = (k in advancers_norm) if is_jungle_primary else c["is_winner"]

                if not c.get("party"):
                    party = summary.get("primary_party")
                    if party and party != "Nonpartisan":
                        c["party"] = party

            # Skip NY "Other primary" buckets.
            if state.lower() == "new york" and " — Other primary" in label:
                continue

            all_internal_rows.extend(candidate_rows)
            race_summaries.append(summary)
            all_cards.extend(candidate_cards)

            if verbose:
                print(f"[{state}] winner={summary['winner_name']} ({label})")

    return all_internal_rows, race_summaries, all_cards


def _extract_canceled_primary_advancer(section_node: BeautifulSoup) -> Optional[str]:
    """Extract the advancer name from 'primary election was canceled' text."""
    import re as regex_mod
    from bp_scraper.parsing.normalize import nws

    section_text = nws(section_node.get_text(" "))
    m = regex_mod.search(
        r"primary election was canceled\.\s*(?:Incumbent\s+)?(.+?)\s+advanced",
        section_text,
        flags=regex_mod.I,
    )
    if not m:
        return None

    candidate_guess = m.group(1).strip()

    # Prefer anchor text if the candidate name is linked.
    for a in section_node.find_all("a"):
        anchor_text = nws(a.get_text(" "))
        if anchor_text and (anchor_text.lower() in candidate_guess.lower() or candidate_guess.lower() in anchor_text.lower()):
            return anchor_text

    return candidate_guess


def _extract_canceled_primary_party(section_node: BeautifulSoup) -> Optional[str]:
    """Extract the party name from 'X primary election was canceled' text."""
    import re as regex_mod
    from bp_scraper.core.constants import PRIMARY_PARTY_FROM_LABEL, EXTRA_PARTY_KEYS
    from bp_scraper.parsing.normalize import nws

    cancelled_re = regex_mod.compile(
        r"\b([A-Za-zʻ’\-\s]+?)\s+primary\s+election\s+was\s+canceled\b",
        regex_mod.I,
    )

    section_text = nws(section_node.get_text(" "))
    m = cancelled_re.search(section_text)
    if not m:
        return None

    raw = m.group(1).lower().strip()

    for key, val in PRIMARY_PARTY_FROM_LABEL.items():
        base_key = key.replace(" primary", "")
        if base_key in raw:
            return val

    for key, val in EXTRA_PARTY_KEYS.items():
        if key in raw:
            return val

    for keyword, canon in [
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
        if keyword in raw:
            return canon

    return None
