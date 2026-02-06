from __future__ import annotations

"""
Results table parsing.

This module extracts candidate rows from Ballotpedia results tables and normalizes
the common fields needed downstream (candidate name, vote totals, percent, incumbent).

Tables vary across pages, so parsing relies on simple heuristics:
- identify the candidate text column
- detect the percent column by % patterns
- detect the vote total column by integer density and proximity to percent
- skip aggregate/write-in rows
"""

import re
from typing import Dict, List, Optional

from bs4 import BeautifulSoup, Tag

from bp_scraper.core.constants import PCT_RE, AGG_WRITEIN_PAT, WRITEIN_SUFFIX
from bp_scraper.parsing.normalize import norm_name


def _parse_int(s: str) -> Optional[int]:
    """Parse an integer string with optional commas."""
    s = s.replace(",", "").strip()
    if re.fullmatch(r"-?\d+", s):
        try:
            return int(s)
        except Exception:
            return None
    return None


def _determine_incumbent(cand_cell: Tag) -> bool:
    """Detect incumbent styling used in Ballotpedia candidate cells."""
    has_bold = bool(cand_cell.find(["b", "strong"]))
    has_underline = bool(cand_cell.find("u"))
    if has_bold and has_underline:
        return True

    # Some tables encode bold/underline via inline styles instead of tags.
    frag = str(cand_cell).lower()
    style_bold = ("font-weight:bold" in frag) or ("font-weight: bold" in frag)
    style_ul = ("text-decoration:underline" in frag) or ("text-decoration: underline" in frag)
    return bool(style_bold and style_ul)


def _infer_party_from_name(name: str) -> Optional[str]:
    """Infer party from trailing parenthetical tokens (e.g., 'Jane Doe (D)')."""
    from bp_scraper.core.constants import PAREN_PARTY, EXTRA_PARTY_KEYS

    m = re.search(r"\(([^)]+)\)\s*$", name or "")
    if not m:
        return None

    first = m.group(1).split("/")[0].strip().lower()

    if first in PAREN_PARTY:
        return PAREN_PARTY[first]

    for key, val in EXTRA_PARTY_KEYS.items():
        if key in first:
            return val

    # Loose fallbacks for common abbreviations.
    if first.startswith("dem") or "democrat" in first:
        return "Democratic"
    if first.startswith("rep") or "republican" in first:
        return "Republican"
    if first.startswith("lib"):
        return "Libertarian"
    if first.startswith("green"):
        return "Green"
    if "independent american" in first:
        return "Independent American"
    if "aloha" in first and "aina" in first:
        return "Aloha ʻĀina"
    if first == "independent":
        return "Independent"

    return None


def _infer_party_from_cells(tds: List[Tag]) -> Optional[str]:
    """Infer party from row text when a party column is present."""
    from bp_scraper.core.constants import EXTRA_PARTY_KEYS

    text = " ".join(re.sub(r"\s+", " ", td.get_text(" ").strip()).lower() for td in tds)

    for key, proper in EXTRA_PARTY_KEYS.items():
        if key in text:
            return proper

    for key, proper in [
        ("democrat", "Democratic"),
        ("democratic", "Democratic"),
        ("republican", "Republican"),
        ("libertarian", "Libertarian"),
        ("green", "Green"),
        ("constitution", "Constitution"),
        ("progressive", "Progressive"),
        ("working families", "Working Families"),
        ("american independent", "American Independent"),
        ("independent american", "Independent American"),
        ("aloha ʻāina", "Aloha ʻĀina"),
        ("aloha aina", "Aloha ʻĀina"),
        ("independent", "Independent"),
        ("nonpartisan", "Nonpartisan"),
    ]:
        if key in text:
            return proper

    return None


def parse_results_table(
    section_root: BeautifulSoup,
    state: str,
    year: int,
    race_label: str,
    source_url: str,
) -> List[dict]:
    """Parse candidate result rows from one results section.

    Args:
        section_root: Soup node for the results section.
        state: State name for context.
        year: Election year.
        race_label: Race label for context.
        source_url: Source page URL for traceability.

    Returns:
        List of dict rows with candidate, percent, vote totals, incumbent, and party hint.
    """
    rows: List[dict] = []

    # Support both standard results tables and ranked-choice containers.
    containers = section_root.select("div.rcvresults_table_container table, table.results_table")
    if not containers:
        containers = section_root.select("table:has(td.votebox-results-cell--text)")

    for tbl in containers:
        all_trs = [tr for tr in tbl.select("tr") if not tr.find("th")]
        if not all_trs:
            continue

        pct_hits_by_col: Dict[int, int] = {}
        int_hits_by_col: Dict[int, int] = {}
        int_values_by_col: Dict[int, List[int]] = {}
        row_cells: List[List[BeautifulSoup]] = []

        # Profile columns to identify percent and vote total positions.
        for tr in all_trs:
            tds = tr.find_all("td")
            if not tds:
                continue

            row_cells.append(tds)

            for ci, td in enumerate(tds):
                txt = re.sub(r"\s+", " ", td.get_text(" ").strip())

                if PCT_RE.search(txt):
                    pct_hits_by_col[ci] = pct_hits_by_col.get(ci, 0) + 1

                iv = _parse_int(txt)
                if iv is not None:
                    int_hits_by_col[ci] = int_hits_by_col.get(ci, 0) + 1
                    int_values_by_col.setdefault(ci, []).append(iv)

        if not row_cells:
            continue

        # Percent column is the one with the most percent hits.
        pct_col: Optional[int] = None
        if pct_hits_by_col:
            max_hit = max(pct_hits_by_col.values())
            pct_col = max([ci for ci, h in pct_hits_by_col.items() if h == max_hit])

        # Vote column is the strongest integer column, biased toward proximity to percent.
        votes_col: Optional[int] = None
        if int_values_by_col:

            def col_score(ci: int):
                vals = int_values_by_col.get(ci, [])
                return (len(vals), (max(vals) if vals else -1), ci)

            best_by_value = max(int_values_by_col.keys(), key=col_score)
            votes_col = best_by_value

            if pct_col is not None:
                hit_counts = [len(v) for v in int_values_by_col.values()]
                threshold = sorted(hit_counts)[len(hit_counts) // 2] if hit_counts else 0
                strong_cols = [ci for ci, vals in int_values_by_col.items() if len(vals) >= threshold]
                if strong_cols:
                    votes_col = min(strong_cols, key=lambda ci: abs(ci - pct_col))

        if votes_col is None and int_hits_by_col:
            votes_col = max(int_hits_by_col.items(), key=lambda kv: (kv[1], kv[0]))[0]

        for tds in row_cells:
            # Prefer the explicit candidate text cell when available.
            cand_cell = None
            for td in tds:
                if "votebox-results-cell--text" in " ".join(td.get("class", [])):
                    cand_cell = td
                    break
            if cand_cell is None:
                cand_cell = tds[0]

            candidate_raw = re.sub(r"\s+", " ", cand_cell.get_text(" ").strip())
            if (not candidate_raw) or (candidate_raw.lower() in {"candidate", "total", "overall", "source", "notes"}):
                continue
            if AGG_WRITEIN_PAT.match(candidate_raw):
                continue

            candidate = WRITEIN_SUFFIX.sub("", candidate_raw).strip()
            incumbent = _determine_incumbent(cand_cell)

            # Percent extraction prefers the inferred percent column, then falls back to scanning row cells.
            pct: Optional[float] = None
            if (pct_col is not None) and (pct_col < len(tds)):
                cell_text = re.sub(r"\s+", " ", tds[pct_col].get_text(" ").strip())
                mp = PCT_RE.search(cell_text)
                if mp:
                    try:
                        pct = float(mp.group(1))
                    except Exception:
                        pct = None

            if pct is None:
                for td in reversed(tds):
                    mp = PCT_RE.search(re.sub(r"\s+", " ", td.get_text(" ").strip()))
                    if mp:
                        try:
                            pct = float(mp.group(1))
                        except Exception:
                            pct = None
                        break

            # Vote extraction prefers the inferred votes column, then falls back to the max integer found.
            total_votes: Optional[int] = None
            if (votes_col is not None) and (votes_col < len(tds)):
                v_txt = re.sub(r"\s+", " ", tds[votes_col].get_text(" ").strip())
                total_votes = _parse_int(v_txt)

            if total_votes is None:
                best = None
                for td in tds:
                    iv = _parse_int(re.sub(r"\s+", " ", td.get_text(" ").strip()))
                    if iv is not None and (best is None or iv > best):
                        best = iv
                total_votes = best

            # Party is treated as a hint; the unify layer can apply additional corrections.
            from bp_scraper.parsing.tables import _infer_party_from_cells as infer_cells

            party_hint = _infer_party_from_name(candidate) or infer_cells(tds)

            rows.append(
                {
                    "state": state,
                    "race": race_label,
                    "year": year,
                    "candidate": candidate,
                    "candidate_clean": norm_name(candidate),
                    "pct": pct,
                    "total_votes": total_votes,
                    "incumbent": incumbent,
                    "party_hint": party_hint,
                    "source_url": source_url,
                }
            )

    return rows
