from __future__ import annotations
from typing import Dict, List, Optional, Tuple, Any
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
from bp_scraper.core.constants import HOUSE_AT_LARGE_STATES
from bp_scraper.transform.summarize import summarize_race


def _party_from_rows_and_cards(rows: List[dict], cards: List[dict]) -> Optional[str]:
    from bp_scraper.parsing.tables import _infer_party_from_name
    inferred_parties: List[str] = []
    for row in rows:
        if row.get("party_hint"):
            inferred_parties.append(row["party_hint"])
        else:
            inferred_party_from_candidate = _infer_party_from_name(row.get("candidate", ""))
            if inferred_party_from_candidate:
                inferred_parties.append(inferred_party_from_candidate)
    if not inferred_parties and cards:
        for candidate_card in cards:
            if candidate_card.get("party"):
                inferred_parties.append(candidate_card["party"])
    unique_parties = {party for party in inferred_parties if party}
    if len(unique_parties) == 1:
        return next(iter(unique_parties))
    return None


APOS_ANY = r"(?:%27|%E2%80%99|'|\u2019)"


def _ordinal_token_to_number(ordinal_token: str) -> Optional[int]:
    from bp_scraper.core.constants import ORD_RE
    match_obj = ORD_RE.search(ordinal_token or "")
    if not match_obj:
        return None
    try:
        return int(match_obj.group(1))
    except Exception:
        return None


def _district_from_url(url: str) -> Optional[str]:
    import urllib.parse, re as regex_mod
    path = urllib.parse.unquote(url)
    match_obj = regex_mod.search(
        rf"/([A-Za-z_]+){APOS_ANY}s?_((?:At[-_]large)|(?:\d{{1,2}}(?:st|nd|rd|th)))_Congressional_District_election,_\d{{4}}$",
        path,
        regex_mod.I,
    )
    if not match_obj:
        return None
    district_token = match_obj.group(2)
    if regex_mod.search(r"at[-_]large", district_token, regex_mod.I):
        return "At-large"
    district_num = _ordinal_token_to_number(district_token)
    if district_num is not None:
        return f"District {district_num}"
    return None


def _state_from_url(url: str, year: int) -> str:
    import urllib.parse, re as regex_mod
    from bp_scraper.parsing.normalize import strip_parenthetical

    path = urllib.parse.unquote(url)
    match_in_form = regex_mod.search(r"_in_([^,]+),_" + regex_mod.escape(str(year)), path)
    if match_in_form:
        return normalize_state_name(strip_parenthetical(match_in_form.group(1).replace("_", " ")))
    match_district_form = regex_mod.search(
        rf"/([A-Za-z_]+){APOS_ANY}s?_(?:At[-_]large|\d{{1,2}}(?:st|nd|rd|th))_Congressional_District_election,_\d{{4}}$",
        path,
        regex_mod.I,
    )
    if match_district_form:
        return normalize_state_name(strip_parenthetical(match_district_form.group(1).replace("_", " ")))
    match_tail_fallback = regex_mod.search(r"/([^/]+),_" + regex_mod.escape(str(year)) + r"$", path)
    if match_tail_fallback:
        state_guess = match_tail_fallback.group(1).replace("_", " ")
        state_guess = state_guess.replace("’", "'")
        state_guess = regex_mod.sub(
            r"^(United States House(?: of Representatives)? elections? in|United States Senate (?:special )?election in)\s+",
            "",
            state_guess,
            flags=regex_mod.I,
        )
        return normalize_state_name(strip_parenthetical(state_guess))
    return normalize_state_name(path)


def _race_label_from_url(url: str, chamber: str, state_for_fallback: Optional[str] = None) -> str:
    if chamber == "senate":
        return "U.S. Senate (special)" if "United_States_Senate_special_election_in_" in url else "U.S. Senate"
    else:
        district = _district_from_url(url)
        base_label = "U.S. House"
        if district:
            return f"{base_label} — {district}"
        if state_for_fallback and normalize_state_name(state_for_fallback) in HOUSE_AT_LARGE_STATES:
            return f"{base_label} — At-large"
        return base_label


def _primary_party_from_label(label: str):
    from bp_scraper.core.constants import PRIMARY_PARTY_FROM_LABEL
    label_lower = (label or "").lower()
    for party_key, party_value in PRIMARY_PARTY_FROM_LABEL.items():
        if party_key in label_lower:
            return party_value
    return None


def _is_jungle_label(state: str, label: str):
    label_lower = (label or "").lower()
    state_lower = normalize_state_name(state).lower()
    if "nonpartisan primary" in label_lower:
        if state_lower == "alaska":
            return True, 4
        if state_lower in {"california", "washington"}:
            return True, 2
    return False, None


def _looks_like_results_table_relaxed(table_node: BeautifulSoup) -> bool:
    class_names = " ".join(table_node.get("class", []))
    if "results_table" in class_names:
        return True
    if table_node.select_one("tr.results_row"):
        return True
    header_texts = [(header_cell.get_text(" ") or "").strip().lower() for header_cell in table_node.select("thead th")]
    if not header_texts:
        first_table_row = table_node.find("tr")
        if first_table_row:
            header_texts = [
                (header_cell.get_text(" ") or "").strip().lower()
                for header_cell in first_table_row.find_all(["th"])
            ]
    if header_texts:
        header_hit = any(
            keyword in " ".join(header_texts)
            for keyword in ["candidate", "candidates", "votes", "vote", "percent", "percentage", "party", "nominee"]
        )
        if header_hit:
            return True
    data_cell_texts = [(data_cell.get_text(" ") or "").strip() for data_cell in table_node.find_all("td")]
    percent_like_count = sum(bool(re.search(r"\d+(?:\.\d+)?\s*%$", text_value)) for text_value in data_cell_texts)
    return percent_like_count >= 2


_CHECKMARK_RE = re.compile(r"[✔✓★]")


def _row_has_winner_mark(table_row: BeautifulSoup) -> bool:
    for descendant in table_row.find_all(True, recursive=True):
        descendant_classes = " ".join(descendant.get("class", []) or [])
        if "winner" in descendant_classes.split():
            return True
        title_attr = (descendant.get("title") or "").lower()
        aria_attr = (descendant.get("aria-label") or "").lower()
        if "winner" in title_attr or "winner" in aria_attr:
            return True
    row_text_lower = (table_row.get_text(" ") or "").lower()
    if _CHECKMARK_RE.search(row_text_lower):
        return True
    if "elected" in row_text_lower:
        return True
    return False


def _best_anchor_name_in_row(table_row: BeautifulSoup) -> Optional[str]:
    anchor_text_candidates: List[str] = []
    for anchor_tag in table_row.find_all("a"):
        anchor_text = (anchor_tag.get_text(" ") or "").strip()
        if not anchor_text:
            continue
        if _CHECKMARK_RE.fullmatch(anchor_text):
            continue
        if not re.search(r"[A-Za-z]", anchor_text):
            continue
        anchor_text_candidates.append(anchor_text)
    if not anchor_text_candidates:
        return None
    anchor_text_candidates.sort(key=lambda candidate_text: len(candidate_text), reverse=True)
    return anchor_text_candidates[0]


def _extract_candidate_name(table_row: BeautifulSoup, default_cell_text: str) -> str:
    best_anchor_text = _best_anchor_name_in_row(table_row)
    if best_anchor_text:
        return best_anchor_text.strip()
    return (default_cell_text or "").strip()


def _parse_la_results_rows(table_node: BeautifulSoup, state: str, year: int, label: str, source_url: str) -> List[dict]:
    parsed_rows: List[dict] = []
    for result_row in table_node.select("tr.results_row"):
        data_cells = result_row.find_all("td")
        if not data_cells:
            continue

        candidate_cell = data_cells[0]
        candidate_name = _extract_candidate_name(result_row, candidate_cell.get_text(" "))
        candidate_clean = norm_name(candidate_name)

        percent_value = None
        for following_cell in data_cells[1:]:
            cell_text = (following_cell.get_text(" ") or "").strip()
            percent_match = re.search(r"(\d+(?:\.\d+)?)\s*%$", cell_text)
            if percent_match:
                try:
                    percent_value = float(percent_match.group(1))
                except Exception:
                    pass
                break

        votes_value = None
        for following_cell in data_cells[1:]:
            numeric_text = (following_cell.get_text(" ") or "").replace(",", "").strip()
            if re.fullmatch(r"\d{1,12}", numeric_text):
                try:
                    votes_value = int(numeric_text)
                except Exception:
                    pass
                break

        candidate_cell_text_lower = candidate_cell.get_text(" ").lower()
        is_incumbent = ("incumbent" in candidate_cell_text_lower) or (" (i)" in candidate_cell_text_lower) or (" (i)" in candidate_cell_text_lower)

        parsed_rows.append({
            "state": state,
            "race": label,
            "year": year,
            "candidate": candidate_name,
            "candidate_clean": candidate_clean,
            "pct": percent_value,
            "total_votes": votes_value,
            "incumbent": is_incumbent,
            "party_hint": None,
            "source_url": source_url,
            "is_winner_row": _row_has_winner_mark(result_row),
        })
    return parsed_rows


def _parse_headered_results_rows(table_node: BeautifulSoup, state: str, year: int, label: str, source_url: str) -> List[dict]:
    header_cells = table_node.select("thead th")
    if not header_cells:
        first_row = table_node.find("tr")
        if first_row:
            header_cells = first_row.find_all("th")

    header_map: Dict[int, str] = {}
    if header_cells:
        for header_index, header_cell in enumerate(header_cells):
            header_text = (header_cell.get_text(" ") or "").strip().lower()
            header_map[header_index] = header_text

    def find_column_index(header_keywords: List[str]) -> Optional[int]:
        for column_index, normalized_header_text in header_map.items():
            if any(keyword in normalized_header_text for keyword in header_keywords):
                return column_index
        return None

    candidate_col_index = find_column_index(["candidate", "candidates", "nominee"])
    percent_col_index = find_column_index(["percent", "percentage", "%"])
    votes_col_index = find_column_index(["vote", "votes"])

    body_rows = table_node.select("tbody tr")
    if not body_rows:
        all_rows = table_node.find_all("tr")
        body_rows = all_rows[1:] if (header_cells and all_rows) else all_rows

    parsed_rows: List[dict] = []
    for body_row in body_rows:
        if body_row.find("th") and not body_row.find("td"):
            continue
        data_cells = body_row.find_all("td")
        if not data_cells:
            continue

        def get_cell_text(column_index: Optional[int]) -> str:
            if column_index is None or column_index >= len(data_cells):
                return ""
            return (data_cells[column_index].get_text(" ") or "").strip()

        fallback_cell = data_cells[candidate_col_index] if (candidate_col_index is not None and candidate_col_index < len(data_cells)) else data_cells[0]
        candidate_name = _extract_candidate_name(body_row, fallback_cell.get_text(" "))
        if not candidate_name:
            continue
        candidate_clean = norm_name(candidate_name)

        percent_value = None
        percent_match = re.search(r"(\d+(?:\.\d+)?)\s*%$", get_cell_text(percent_col_index))
        if percent_match:
            try:
                percent_value = float(percent_match.group(1))
            except Exception:
                percent_value = None

        votes_value = None
        votes_text = get_cell_text(votes_col_index).replace(",", "")
        if re.fullmatch(r"\d{1,12}", votes_text or ""):
            try:
                votes_value = int(votes_text)
            except Exception:
                votes_value = None

        fallback_text_lower = fallback_cell.get_text(" ").lower()
        is_incumbent = ("incumbent" in fallback_text_lower) or (" (i)" in fallback_text_lower) or (" (i)" in fallback_text_lower)

        parsed_rows.append({
            "state": state,
            "race": label,
            "year": year,
            "candidate": candidate_name,
            "candidate_clean": candidate_clean,
            "pct": percent_value,
            "total_votes": votes_value,
            "incumbent": is_incumbent,
            "party_hint": None,
            "source_url": source_url,
            "is_winner_row": _row_has_winner_mark(body_row),
        })
    return parsed_rows


def _normalize_sections(sections: Any, default_label: str) -> List[Tuple[BeautifulSoup, str]]:
    normalized_pairs: List[Tuple[BeautifulSoup, str]] = []
    if not sections:
        return normalized_pairs

    def ensure_pair(item_any: Any) -> Optional[Tuple[BeautifulSoup, str]]:
        if item_any is None:
            return None
        if isinstance(item_any, (list, tuple)):
            if len(item_any) >= 2:
                section_node, label_value = item_any[0], item_any[1]
                if section_node is None:
                    return None
                return section_node, (label_value if isinstance(label_value, str) and label_value else default_label)
            if len(item_any) == 1:
                section_node = item_any[0]
                if section_node is None:
                    return None
                return section_node, default_label
            return None
        return (item_any, default_label)

    seen_ids = set()
    for raw_item in sections:
        section_pair = ensure_pair(raw_item)
        if not section_pair:
            continue
        section_node, label_value = section_pair
        identity_key = id(section_node)
        if identity_key in seen_ids:
            continue
        seen_ids.add(identity_key)
        normalized_pairs.append((section_node, label_value))
    return normalized_pairs


SYM_WINNERS = {"✔", "✓", "★", "—", "-"}


def _resolve_symbol_winner(summary: Dict[str, Any], rows: List[dict], cards: List[dict]) -> None:
    current_winner_name = (summary.get("winner_name") or "").strip()
    if current_winner_name and current_winner_name not in SYM_WINNERS:
        return

    winner_rows = [row for row in rows if row.get("is_winner_row")]
    if winner_rows:
        summary["winner_name"] = winner_rows[0]["candidate"]
        return

    def score(row_obj: dict) -> Tuple[float, int]:
        pct_value = row_obj.get("pct")
        votes_value = row_obj.get("total_votes")
        pct_score = float(pct_value) if isinstance(pct_value, (int, float)) else -1.0
        votes_score = int(votes_value) if isinstance(votes_value, int) else -1
        return (pct_score, votes_score)

    best_row = None
    for row_obj in rows:
        if best_row is None or score(row_obj) > score(best_row):
            best_row = row_obj
    if best_row and (best_row.get("pct") is not None or best_row.get("total_votes") is not None):
        summary["winner_name"] = best_row["candidate"]
        return

    unique_names = [row_obj["candidate"] for row_obj in rows if row_obj.get("candidate")]
    if len(unique_names) == 1:
        summary["winner_name"] = unique_names[0]
        return

    card_names = [card["name"] for card in cards if card.get("name")]
    if len(card_names) == 1:
        summary["winner_name"] = card_names[0]
        return

    def is_name_like(text_value: str) -> bool:
        stripped_text = text_value.strip()
        if not re.search(r"[A-Za-z]", stripped_text):
            return False
        if _CHECKMARK_RE.fullmatch(stripped_text):
            return False
        return True

    candidate_texts = [row_obj["candidate"] for row_obj in rows if is_name_like(row_obj.get("candidate", ""))]
    if candidate_texts:
        candidate_texts.sort(key=lambda candidate_str: len(candidate_str), reverse=True)
        summary["winner_name"] = candidate_texts[0]


def scrape_page(
    url: str,
    year: int,
    chamber: str,
    verbose: bool = False,
    primary: bool = False,
    delay: Optional[float] = None,
    retries: Optional[int] = None,
) -> Tuple[List[dict], List[Dict[str, object]], List[dict]]:
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
            for table_node in row_tables:
                if id(table_node) not in seen_ids:
                    seen_ids.add(id(table_node))
                    unique_tables.append(table_node)
            if unique_tables and verbose:
                print(f"[debug] Louisiana: captured {len(unique_tables)} results table(s) via row-up fallback — {url}")
            if unique_tables:
                sections = [(table_node, base_race_label) for table_node in unique_tables]

    sections = _normalize_sections(sections, base_race_label)

    if not sections:
        if verbose:
            mode_label = "primary" if primary else "general"
            print(f"[warn] {state}: skipped (no {year} {mode_label} results) — {url}")
        return [], [], []

    all_internal_rows: List[dict] = []
    race_summaries: List[Dict[str, object]] = []
    all_cards: List[dict] = []

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

        if not all_candidate_rows:
            sweep_tables: List[BeautifulSoup] = []
            sweep_tables.extend(soup.select(".results_table_container table"))
            sweep_tables.extend(soup.select("table.results_table"))
            seen_table_ids = set()
            unique_sweep_tables = []
            for table_node in sweep_tables:
                if id(table_node) not in seen_table_ids:
                    seen_table_ids.add(id(table_node))
                    unique_sweep_tables.append(table_node)
            if unique_sweep_tables and verbose:
                print(f"[debug] sweep: parsing {len(unique_sweep_tables)} fallback table(s) — {url}")
            for table_node in unique_sweep_tables:
                all_candidate_rows.extend(
                    _parse_la_results_rows(table_node, state, year, base_race_label, source_url=url)
                )

        if verbose:
            print(f"[debug] parsed rows: {len(all_candidate_rows)} — {url}")

        if not all_candidate_rows:
            if verbose:
                print(f"[warn] {state}: {year} section(s) found but no results rows parsed; skipping — {url}")
            return [], [], []

        table_clean_names = {row_obj["candidate_clean"] for row_obj in all_candidate_rows}
        candidate_cards = parse_candidate_cards(soup, state, year, base_race_label, source_url=url)
        candidate_cards = [card for card in candidate_cards if card["name_clean"] in table_clean_names]

        if not candidate_cards:
            from bp_scraper.parsing.tables import _infer_party_from_name
            candidate_cards = [
                {
                    "state": state,
                    "race": base_race_label,
                    "year": year,
                    "name": row_obj["candidate"],
                    "name_clean": row_obj["candidate_clean"],
                    "party": row_obj.get("party_hint") or _infer_party_from_name(row_obj["candidate"]),
                    "incumbent": bool(row_obj.get("incumbent", False)),
                    "total_votes": row_obj.get("total_votes"),
                    "source_url": url,
                }
                for row_obj in all_candidate_rows
            ]
        else:
            card_name_set = {card["name_clean"] for card in candidate_cards}
            from bp_scraper.parsing.tables import _infer_party_from_name
            for row_obj in all_candidate_rows:
                if row_obj["candidate_clean"] not in card_name_set:
                    candidate_cards.append(
                        {
                            "state": state,
                            "race": base_race_label,
                            "year": year,
                            "name": row_obj["candidate"],
                            "name_clean": row_obj["candidate_clean"],
                            "party": row_obj.get("party_hint") or _infer_party_from_name(row_obj["candidate"]),
                            "incumbent": bool(row_obj.get("incumbent", False)),
                            "total_votes": row_obj.get("total_votes"),
                            "source_url": url,
                        }
                    )

        backfill_party_from_label(candidate_cards)

        summary = {
            "state": state,
            "race": base_race_label,
            "year": year,
            "source_url": url,
            "is_jungle_primary": False,
            "primary_party": None,
        }
        summary.update(summarize_race(all_candidate_rows, allowed_clean_names=None))

        _resolve_symbol_winner(summary, all_candidate_rows, candidate_cards)

        votes_map: Dict[str, Optional[int]] = {}
        for row_obj in all_candidate_rows:
            clean_key = row_obj["candidate_clean"]
            row_votes = row_obj.get("total_votes")
            if isinstance(row_votes, int):
                if (clean_key not in votes_map) or (votes_map[clean_key] is None) or (row_votes > (votes_map[clean_key] or -1)):
                    votes_map[clean_key] = row_votes
        incumbency_map = {row_obj["candidate_clean"]: bool(row_obj["incumbent"]) for row_obj in all_candidate_rows}
        for card in candidate_cards:
            clean_name = card["name_clean"]
            card["incumbent"] = incumbency_map.get(clean_name, False)
            card.setdefault("total_votes", votes_map.get(clean_name))

        winner_norm = norm_name(summary.get("winner_name") or "")
        for card in candidate_cards:
            card["race"] = base_race_label
            card["is_winner"] = card["name_clean"] == winner_norm
            card["is_advancer"] = card["is_winner"]

        all_internal_rows.extend(all_candidate_rows)
        race_summaries.append(summary)
        all_cards.extend(candidate_cards)

        if verbose:
            print(f"[{state}] winner={summary['winner_name']} ({base_race_label})")

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

            table_clean_names = {row_obj["candidate_clean"] for row_obj in candidate_rows}
            candidate_cards = parse_candidate_cards(soup, state, year, label, source_url=url)
            candidate_cards = [card for card in candidate_cards if card["name_clean"] in table_clean_names]
            from bp_scraper.parsing.tables import _infer_party_from_name
            card_clean_name_set = {card["name_clean"] for card in candidate_cards}
            for row_obj in candidate_rows:
                if row_obj["candidate_clean"] not in card_clean_name_set:
                    candidate_cards.append(
                        {
                            "state": state,
                            "race": label,
                            "year": year,
                            "name": row_obj["candidate"],
                            "name_clean": row_obj["candidate_clean"],
                            "party": row_obj.get("party_hint") or _infer_party_from_name(row_obj["candidate"]),
                            "incumbent": bool(row_obj.get("incumbent", False)),
                            "total_votes": row_obj.get("total_votes"),
                            "source_url": url,
                        }
                    )

            backfill_party_from_label(candidate_cards)

            if label.endswith(" — Primary"):
                inferred_party = (
                    cancelled_party
                    or _party_from_rows_and_cards(candidate_rows, candidate_cards)
                    or scan_section_party_keywords(section_node)
                )
                if inferred_party and inferred_party.strip().lower() == "other":
                    continue
                if (state.lower() == "utah") and (inferred_party in {"Independent", "Independent American"}):
                    inferred_party = "Independent American"
                if inferred_party:
                    label = label.replace(" — Primary", f" — {inferred_party} primary")
                    for card in candidate_cards:
                        card["race"] = label
                    for row_obj in candidate_rows:
                        row_obj["race"] = label
                else:
                    if normalize_state_name(state) in {"Alaska", "California", "Washington", "Louisiana"}:
                        label = label.replace(" — Primary", " — Nonpartisan primary")
                        for card in candidate_cards:
                            card["race"] = label
                        for row_obj in candidate_rows:
                            row_obj["race"] = label
                    else:
                        continue

            summary = {
                "state": state,
                "race": label,
                "year": year,
                "source_url": url,
                "is_jungle_primary": False,
                "primary_party": _primary_party_from_label(label),
            }
            summary.update(summarize_race(candidate_rows, allowed_clean_names=None))

            _resolve_symbol_winner(summary, candidate_rows, candidate_cards)

            votes_map: Dict[str, Optional[int]] = {}
            for row_obj in candidate_rows:
                clean_key = row_obj["candidate_clean"]
                row_votes = row_obj.get("total_votes")
                if isinstance(row_votes, int):
                    if (clean_key not in votes_map) or (votes_map[clean_key] is None) or (row_votes > (votes_map[clean_key] or -1)):
                        votes_map[clean_key] = row_votes
            incumbency_map = {row_obj["candidate_clean"]: bool(row_obj["incumbent"]) for row_obj in candidate_rows}
            for card in candidate_cards:
                clean_name = card["name_clean"]
                card["race"] = label
                card["incumbent"] = incumbency_map.get(clean_name, False)
                card.setdefault("total_votes", votes_map.get(clean_name))

            winner_norm = norm_name(summary.get("winner_name") or "")
            is_jungle_primary, jungle_top_n = _is_jungle_label(state, label)
            advancers_norm_set = set(norm_name(name) for name in (summary.get("advancers") or []))
            for card in candidate_cards:
                clean_name = card["name_clean"]
                card["is_winner"] = clean_name == winner_norm
                card["is_advancer"] = (clean_name in advancers_norm_set) if is_jungle_primary else card["is_winner"]
                if not card.get("party"):
                    primary_party = summary.get("primary_party")
                    if primary_party and primary_party != "Nonpartisan":
                        card["party"] = primary_party

            if state.lower() == "new york" and " — Other primary" in label:
                continue

            all_internal_rows.extend(candidate_rows)
            race_summaries.append(summary)
            all_cards.extend(candidate_cards)
            if verbose:
                print(f"[{state}] winner={summary['winner_name']} ({label})")

    return all_internal_rows, race_summaries, all_cards


def _extract_canceled_primary_advancer(section_node: BeautifulSoup) -> Optional[str]:
    import re as regex_mod
    from bp_scraper.parsing.normalize import nws
    section_text = nws(section_node.get_text(" "))
    match_obj = regex_mod.search(r"primary election was canceled\.\s*(?:Incumbent\s+)?(.+?)\s+advanced", section_text, flags=regex_mod.I)
    if not match_obj:
        return None
    candidate_guess = match_obj.group(1).strip()
    anchor_tags = section_node.find_all("a")
    for anchor_tag in anchor_tags:
        anchor_text = nws(anchor_tag.get_text(" "))
        if anchor_text and (anchor_text.lower() in candidate_guess.lower() or candidate_guess.lower() in anchor_text.lower()):
            return anchor_text
    return candidate_guess


def _extract_canceled_primary_party(section_node: BeautifulSoup) -> Optional[str]:
    import re as regex_mod
    from bp_scraper.core.constants import PRIMARY_PARTY_FROM_LABEL, EXTRA_PARTY_KEYS
    from bp_scraper.parsing.normalize import nws

    CANCELLED_PRIMARY_PARTY_RE = regex_mod.compile(
        r"\b([A-Za-zʻ’\-\s]+?)\s+primary\s+election\s+was\s+canceled\b", regex_mod.I
    )
    section_text = nws(section_node.get_text(" "))
    match_obj = CANCELLED_PRIMARY_PARTY_RE.search(section_text)
    if not match_obj:
        return None
    raw_party_text = match_obj.group(1).lower().strip()
    for party_key, party_value in PRIMARY_PARTY_FROM_LABEL.items():
        base_key = party_key.replace(" primary", "")
        if base_key in raw_party_text:
            return party_value
    for extra_key, extra_value in EXTRA_PARTY_KEYS.items():
        if extra_key in raw_party_text:
            return extra_value
    for keyword_text, canonical_party in [
        ("democratic", "Democratic"),
        ("republican", "Republican"),
        ("libertarian", "Libertarian"),
        ("green", "Green"),
        ("working families", "Working Families"),
        ("constitution", "Constitution"),
        ("progressive", "Progressive"),
        ("independent american", "Independent American"),
        ("aloha aina", "Aloha ʻĀina"),
        ("aloha ʻĀina", "Aloha ʻĀina"),
        ("independent", "Independent"),
    ]:
        if keyword_text in raw_party_text:
            return canonical_party
    return None
