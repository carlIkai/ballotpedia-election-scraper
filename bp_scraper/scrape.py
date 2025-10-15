from __future__ import annotations
from typing import Dict, List, Optional, Tuple, Any
import re

from bs4 import BeautifulSoup

from .http_urls import get_soup
from .dates import parse_election_dates_from_page
from .sections import find_result_sections, ny_primary_fallback_sections
from .tables import parse_results_table
from .candidates import (
    parse_candidate_cards,
    backfill_party_from_label,
    scan_section_party_keywords,
)
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
    low = (label or "").lower()
    state = normalize_state_name(state).lower()
    if "nonpartisan primary" in low:
        if state == "alaska":
            return True, 4
        if state in {"california", "washington"}:
            return True, 2
    return False, None

def _looks_like_results_table_relaxed(tbl: BeautifulSoup) -> bool:
    classes = " ".join(tbl.get("class", []))
    if "results_table" in classes:
        return True
    if tbl.select_one("tr.results_row"):
        return True
    th_texts = [(th.get_text(" ") or "").strip().lower() for th in tbl.select("thead th")]
    if not th_texts:
        first_tr = tbl.find("tr")
        if first_tr:
            th_texts = [(th.get_text(" ") or "").strip().lower() for th in first_tr.find_all(["th"])]
    if th_texts:
        header_hit = any(
            k in " ".join(th_texts)
            for k in ["candidate", "candidates", "votes", "vote", "percent", "percentage", "party", "nominee"]
        )
        if header_hit:
            return True
    td_texts = [(td.get_text(" ") or "").strip() for td in tbl.find_all("td")]
    pct_like = sum(bool(re.search(r"\d+(?:\.\d+)?\s*%$", t)) for t in td_texts)
    return pct_like >= 2


_CHECKMARK_RE = re.compile(r"[✔✓★]")
def _row_has_winner_mark(tr: BeautifulSoup) -> bool:
    for el in tr.find_all(True, recursive=True):
        classes = " ".join(el.get("class", []) or [])
        if "winner" in classes.split():
            return True
        title = (el.get("title") or "").lower()
        aria = (el.get("aria-label") or "").lower()
        if "winner" in title or "winner" in aria:
            return True
    txt = (tr.get_text(" ") or "").lower()
    if _CHECKMARK_RE.search(txt):
        return True
    if "elected" in txt:
        return True
    return False


def _best_anchor_name_in_row(tr: BeautifulSoup) -> Optional[str]:
    candidates: List[str] = []
    for a in tr.find_all("a"):
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
    candidates.sort(key=lambda s: len(s), reverse=True)
    return candidates[0]


def _extract_candidate_name(tr: BeautifulSoup, default_td_text: str) -> str:
    """
    Louisiana pages sometimes place the checkmark in the first cell and the *name* in another cell.
    Prefer the best anchor text from the entire row; fall back to the first cell text.
    """
    best = _best_anchor_name_in_row(tr)
    if best:
        return best.strip()
    return (default_td_text or "").strip()

def _parse_la_results_rows(tbl: BeautifulSoup, state: str, year: int, label: str, source_url: str) -> List[dict]:
    out: List[dict] = []
    for tr in tbl.select("tr.results_row"):
        tds = tr.find_all("td")
        if not tds:
            continue

        cand_td = tds[0]
        candidate = _extract_candidate_name(tr, cand_td.get_text(" "))
        candidate_clean = norm_name(candidate)

        pct_val = None
        for td in tds[1:]:
            txt = (td.get_text(" ") or "").strip()
            m = re.search(r"(\d+(?:\.\d+)?)\s*%$", txt)
            if m:
                try:
                    pct_val = float(m.group(1))
                except Exception:
                    pass
                break

        votes_val = None
        for td in tds[1:]:
            txt = (td.get_text(" ") or "").replace(",", "").strip()
            if re.fullmatch(r"\d{1,12}", txt):
                try:
                    votes_val = int(txt)
                except Exception:
                    pass
                break

        row_txt = cand_td.get_text(" ").lower()
        inc = ("incumbent" in row_txt) or (" (i)" in row_txt) or (" (i)" in row_txt)

        out.append({
            "state": state,
            "race": label,
            "year": year,
            "candidate": candidate,
            "candidate_clean": candidate_clean,
            "pct": pct_val,
            "total_votes": votes_val,
            "incumbent": inc,
            "party_hint": None,
            "source_url": source_url,
            "is_winner_row": _row_has_winner_mark(tr),
        })
    return out


def _parse_headered_results_rows(tbl: BeautifulSoup, state: str, year: int, label: str, source_url: str) -> List[dict]:
    header_cells = tbl.select("thead th")
    if not header_cells:
        first_tr = tbl.find("tr")
        if first_tr:
            header_cells = first_tr.find_all("th")

    header_map: Dict[int, str] = {}
    if header_cells:
        for idx, th in enumerate(header_cells):
            t = (th.get_text(" ") or "").strip().lower()
            header_map[idx] = t

    def col_idx(keys: List[str]) -> Optional[int]:
        for idx, t in header_map.items():
            if any(k in t for k in keys):
                return idx
        return None

    cand_idx = col_idx(["candidate", "candidates", "nominee"])
    pct_idx = col_idx(["percent", "percentage", "%"])
    vote_idx = col_idx(["vote", "votes"])

    body_rows = tbl.select("tbody tr")
    if not body_rows:
        trs = tbl.find_all("tr")
        body_rows = trs[1:] if (header_cells and trs) else trs

    out: List[dict] = []
    for tr in body_rows:
        if tr.find("th") and not tr.find("td"):
            continue
        tds = tr.find_all("td")
        if not tds:
            continue

        def cell(i: Optional[int]) -> str:
            if i is None or i >= len(tds):
                return ""
            return (tds[i].get_text(" ") or "").strip()

        fallback_td = tds[cand_idx] if (cand_idx is not None and cand_idx < len(tds)) else tds[0]
        candidate = _extract_candidate_name(tr, fallback_td.get_text(" "))
        if not candidate:
            continue
        candidate_clean = norm_name(candidate)

        pct_val = None
        m = re.search(r"(\d+(?:\.\d+)?)\s*%$", cell(pct_idx))
        if m:
            try:
                pct_val = float(m.group(1))
            except Exception:
                pct_val = None

        votes_val = None
        vt = cell(vote_idx).replace(",", "")
        if re.fullmatch(r"\d{1,12}", vt or ""):
            try:
                votes_val = int(vt)
            except Exception:
                votes_val = None

        row_txt = fallback_td.get_text(" ").lower()
        inc = ("incumbent" in row_txt) or (" (i)" in row_txt) or (" (i)" in row_txt)

        out.append({
            "state": state,
            "race": label,
            "year": year,
            "candidate": candidate,
            "candidate_clean": candidate_clean,
            "pct": pct_val,
            "total_votes": votes_val,
            "incumbent": inc,
            "party_hint": None,
            "source_url": source_url,
            "is_winner_row": _row_has_winner_mark(tr),
        })
    return out


def _normalize_sections(sections: Any, default_label: str) -> List[Tuple[BeautifulSoup, str]]:
    out: List[Tuple[BeautifulSoup, str]] = []
    if not sections:
        return out

    def as_pair(item: Any) -> Optional[Tuple[BeautifulSoup, str]]:
        if item is None:
            return None
        if isinstance(item, (list, tuple)):
            if len(item) >= 2:
                sec, lbl = item[0], item[1]
                if sec is None:
                    return None
                return sec, (lbl if isinstance(lbl, str) and lbl else default_label)
            if len(item) == 1:
                sec = item[0]
                if sec is None:
                    return None
                return sec, default_label
            return None
        return (item, default_label)

    seen_ids = set()
    for it in sections:
        pair = as_pair(it)
        if not pair:
            continue
        sec, lbl = pair
        key = id(sec)
        if key in seen_ids:
            continue
        seen_ids.add(key)
        out.append((sec, lbl))
    return out


SYM_WINNERS = {"✔", "✓", "★", "—", "-"}

def _resolve_symbol_winner(summary: Dict[str, Any], rows: List[dict], cards: List[dict]) -> None:
    """
    If summarize_race() produced a symbol winner name, replace it with a real candidate:
      1) Any row tagged as winner
      2) Best numeric row (max pct, then votes)
      3) If exactly one row: that name
      4) If exactly one card: that name
      5) Otherwise, longest anchor-like candidate from any row
    """
    w = (summary.get("winner_name") or "").strip()
    if w and w not in SYM_WINNERS:
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

    uniq_names = [r["candidate"] for r in rows if r.get("candidate")]
    if len(uniq_names) == 1:
        summary["winner_name"] = uniq_names[0]
        return

    card_names = [c["name"] for c in cards if c.get("name")]
    if len(card_names) == 1:
        summary["winner_name"] = card_names[0]
        return

    def is_name_like(s: str) -> bool:
        s = s.strip()
        if not re.search(r"[A-Za-z]", s):
            return False
        if _CHECKMARK_RE.fullmatch(s):
            return False
        return True

    candidates = [r["candidate"] for r in rows if is_name_like(r.get("candidate", ""))]
    if candidates:
        candidates.sort(key=lambda s: len(s), reverse=True)
        summary["winner_name"] = candidates[0]

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
        la_primary_sections = find_result_sections(soup, year, state, base_race_label, primary=True)
        if la_primary_sections:
            sections = [(sec, base_race_label) for sec, *_ in _normalize_sections(la_primary_sections, base_race_label)]
        if not sections:
            sections = la_jungle_fallback_sections(soup, base_race_label)
        if not sections:
            sections = la_pagewide_results_tables(soup, base_race_label)
        if not sections:
            hard_tables = []
            for tbl in soup.select("table.results_table"):
                if tbl.select("tr.results_row") or _looks_like_results_table_relaxed(tbl):
                    hard_tables.append(tbl)
            if hard_tables:
                sections = [(t, base_race_label) for t in hard_tables]
        if not sections:
            row_tables = []
            for tr in soup.select("tr.results_row"):
                tbl = tr.find_parent("table")
                if tbl is not None:
                    row_tables.append(tbl)
            seen_ids = set()
            uniq_tables = []
            for t in row_tables:
                if id(t) not in seen_ids:
                    seen_ids.add(id(t))
                    uniq_tables.append(t)
            if uniq_tables and verbose:
                print(f"[debug] Louisiana: captured {len(uniq_tables)} results table(s) via row-up fallback — {url}")
            if uniq_tables:
                sections = [(t, base_race_label) for t in uniq_tables]

    sections = _normalize_sections(sections, base_race_label)

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
            rows = parse_results_table(sec, state, year, label, source_url=url)

            if not rows:
                tbl = sec if getattr(sec, "name", None) == "table" else sec.find("table")
                if tbl and _looks_like_results_table_relaxed(tbl):
                    rows = _parse_la_results_rows(tbl, state, year, label, source_url=url)

            if not rows:
                tbl = sec if getattr(sec, "name", None) == "table" else sec.find("table")
                if tbl and _looks_like_results_table_relaxed(tbl):
                    hdr_rows = _parse_headered_results_rows(tbl, state, year, label, source_url=url)
                    if hdr_rows:
                        rows = hdr_rows

            cand_rows_all.extend(rows)

        if not cand_rows_all:
            sweep_tables = []
            sweep_tables.extend(soup.select(".results_table_container table"))
            sweep_tables.extend(soup.select("table.results_table"))
            seen = set()
            uniq_tables = []
            for t in sweep_tables:
                if id(t) not in seen:
                    seen.add(id(t))
                    uniq_tables.append(t)
            if uniq_tables and verbose:
                print(f"[debug] sweep: parsing {len(uniq_tables)} fallback table(s) — {url}")
            for t in uniq_tables:
                cand_rows_all.extend(
                    _parse_la_results_rows(t, state, year, base_race_label, source_url=url)
                )

        if verbose:
            print(f"[debug] parsed rows: {len(cand_rows_all)} — {url}")

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

        summary = {
            "state": state,
            "race": base_race_label,
            "year": year,
            "source_url": url,
            "is_jungle_primary": False,
            "primary_party": None,
        }
        summary.update(summarize_race(cand_rows_all, allowed_clean_names=None))

        _resolve_symbol_winner(summary, cand_rows_all, cards)

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

            if not cand_rows:
                tbl = sec if getattr(sec, "name", None) == "table" else sec.find("table")
                if tbl and _looks_like_results_table_relaxed(tbl):
                    cand_rows = _parse_la_results_rows(tbl, state, year, label, source_url=url)
            if not cand_rows:
                tbl = sec if getattr(sec, "name", None) == "table" else sec.find("table")
                if tbl and _looks_like_results_table_relaxed(tbl):
                    cand_rows = _parse_headered_results_rows(tbl, state, year, label, source_url=url)

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

            from .tables import _infer_party_from_name
            card_names = {c["name_clean"] for c in cards}
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

            summary = {
                "state": state,
                "race": label,
                "year": year,
                "source_url": url,
                "is_jungle_primary": False,
                "primary_party": _primary_party_from_label(label),
            }
            summary.update(summarize_race(cand_rows, allowed_clean_names=None))

            _resolve_symbol_winner(summary, cand_rows, cards)

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

            winner_norm = norm_name(summary.get("winner_name") or "")
            is_jungle, jungle_top = _is_jungle_label(state, label)
            advancers_set = set(norm_name(n) for n in (summary.get("advancers") or []))
            for c in cards:
                nm = c["name_clean"]
                c["is_winner"] = nm == winner_norm
                c["is_advancer"] = (nm in advancers_set) if is_jungle else c["is_winner"]
                if not c.get("party"):
                    pp = summary.get("primary_party")
                    if pp and pp != "Nonpartisan":
                        c["party"] = pp

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
