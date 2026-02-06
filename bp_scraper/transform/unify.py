from __future__ import annotations

"""
Build unified PositionElection records from scraped race and candidate DataFrames.

This module converts scraper outputs into a stable, API-friendly shape:
- one PositionElection per (state, race label, year)
- nested Election and Position objects with deterministic IDs
- candidacy rows derived from candidate-level records

The output is designed to serialize cleanly to JSON and flatten cleanly to CSV.
"""

from typing import Any, Dict, List, Optional

import pandas as pd

from bp_scraper.core.constants import USPS
from bp_scraper.parsing.dates import ELECTION_DAY_BY_KEY, compute_federal_general_election_day
from bp_scraper.parsing.normalize import b64_id, display_clean_name, normalize_state_name


def _phase_from_race_label(race_label: str, state: str | None = None) -> str:
    """Infer election phase (Primary/General) from the race label."""
    low = (race_label or "").lower()

    # Louisiana nonpartisan primaries function as general elections.
    if state and state.lower() == "louisiana" and "nonpartisan" in low:
        return "General"

    return "Primary" if "primary" in low else "General"


def _is_runoff_from_label(race_label: str) -> bool:
    """Return True when the label indicates a runoff stage."""
    return "runoff" in (race_label or "").lower()


def _is_unexpired_from_label(race_label: str) -> bool:
    """Return True for special elections (unexpired term)."""
    return "(special)" in (race_label or "").lower()


def _election_day_for(state_name: str, year: int, phase: str) -> Optional[str]:
    """Look up a parsed election day from the in-memory cache."""
    return ELECTION_DAY_BY_KEY.get((state_name, year, phase))


def _election_day_or_default(state_name: str, year: int, phase: str, scope: str = "federal") -> Optional[str]:
    """Return an election day if known, with a federal general-election fallback."""
    iso = _election_day_for(state_name, year, phase)

    # Federal general election day is deterministic even when pages omit dates.
    if (not iso) and (phase == "General") and (scope == "federal"):
        return compute_federal_general_election_day(year)

    return iso


def _district_from_race_label(race_label: str) -> Optional[str]:
    """Extract district display text from a House race label."""
    import re

    m = re.search(r"\bDistrict\s+(\d+)\b", race_label or "", re.I)
    if m:
        return f"District {int(m.group(1))}"

    if re.search(r"\bAt[-\s]?large\b", race_label or "", re.I):
        return "At-large"

    return None


def build_position_elections(
    races_df: pd.DataFrame,
    candidates_df: pd.DataFrame,
    chamber: str,
    *,
    scope: str = "federal",
) -> List[Dict[str, Any]]:
    """Build PositionElection objects for each discovered race."""
    if races_df.empty and candidates_df.empty:
        return []

    # Index candidates by normalized (state, race, year).
    by_race: dict[tuple[str, str, int], list[pd.Series]] = {}
    if not candidates_df.empty:
        for _, row in candidates_df.iterrows():
            key = (normalize_state_name(row["state"]), str(row["race"]), int(row["year"]))
            by_race.setdefault(key, []).append(row)

    # Collect all race keys from both inputs so races with missing candidates are not dropped.
    keys_all: set[tuple[str, str, int]] = set()
    if not races_df.empty:
        for _, r in races_df.iterrows():
            keys_all.add((normalize_state_name(r["state"]), str(r["race"]), int(r["year"])))
    if not candidates_df.empty:
        for _, r in candidates_df.iterrows():
            keys_all.add((normalize_state_name(r["state"]), str(r["race"]), int(r["year"])))

    out: List[Dict[str, Any]] = []
    keys_seen: set[tuple[str, int, str, str]] = set()

    for state_name, race_label, year in sorted(keys_all):
        pure_state = normalize_state_name(state_name)
        state_code = USPS.get(pure_state, pure_state[:2].upper())

        phase = _phase_from_race_label(race_label, pure_state)
        is_primary = (phase == "Primary")
        is_runoff = _is_runoff_from_label(race_label)
        is_unexpired = _is_unexpired_from_label(race_label)

        # Derive a stable position identity from scope, chamber, state and district/office.
        if scope == "state":
            pos_level = "STATE"
            office = (race_label or "").strip()

            if office in {"Governor", "Lieutenant Governor", "Attorney General"}:
                position_name = f"{office} - {pure_state}"
                position_id = b64_id("Position", office, state_code)
            elif office in {"State Senate", "State House", "House of Delegates"}:
                position_name = f"{office} - {pure_state}"
                position_id = b64_id("Position", office, state_code)
            else:
                position_name = f"{office} - {pure_state}" if office else f"State Office - {pure_state}"
                position_id = b64_id("Position", position_name, state_code)
        else:
            pos_level = "FEDERAL"
            if chamber == "senate":
                position_name = f"U.S. Senate - {pure_state}"
                position_id = b64_id("Position", "U.S. Senate", state_code)
            else:
                district = _district_from_race_label(race_label)
                suffix = f" {district}" if district else ""
                position_name = f"U.S. House - {pure_state}{suffix}"
                position_id = b64_id("Position", "U.S. House", state_code, district or "")

        # Resolve election name and election day.
        if is_runoff:
            election_name = f"{pure_state} {year} {phase} Runoff Election"
            election_day_iso = _election_day_for(pure_state, year, "Runoff")
        else:
            election_name = f"{pure_state} {year} {phase} Election"
            election_day_iso = _election_day_or_default(pure_state, year, phase, scope=scope)

        election_id = b64_id("Election", state_code, str(year), f"{phase}{' Runoff' if is_runoff else ''}")
        pos_elex_id = b64_id("PositionElection", state_code, str(year), race_label)

        # Attach candidacies derived from candidate rows.
        candidacies: List[Dict[str, Any]] = []
        race_candidates = by_race.get((pure_state, race_label, year), [])

        for crow in race_candidates:
            original_full = str(crow["name"])
            full_name = display_clean_name(original_full, pure_state)

            cand_id = b64_id("Candidate", full_name)
            candidacy_id = b64_id("Candidacy", state_code, str(year), race_label, full_name)
            candidacy_result = "WON" if bool(crow.get("is_winner")) else "LOST"

            candidacies.append(
                {
                    "id": candidacy_id,
                    "withdrawn": False,
                    "result": candidacy_result,
                    "candidate": {"id": cand_id, "fullName": full_name},
                }
            )

        pe = {
            "id": pos_elex_id,
            "isPrimary": bool(is_primary),
            "isRunoff": bool(is_runoff),
            "isRecall": False,
            "isUnexpired": bool(is_unexpired),
            "seats": 1,
            "election": {
                "id": election_id,
                "name": election_name,
                "electionDay": election_day_iso,
                "state": state_code,
            },
            "position": {
                "id": position_id,
                "name": position_name,
                "level": pos_level,
                "state": state_code,
            },
            "candidacies": candidacies,
        }

        # Ensure output uniqueness when inputs contain duplicates.
        dedupe_key = (state_code, year, race_label, scope)
        if dedupe_key not in keys_seen:
            out.append(pe)
            keys_seen.add(dedupe_key)

    return out


def position_elections_to_rows(unified: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten PositionElection objects into CSV-friendly rows."""
    rows: List[Dict[str, Any]] = []

    for pe in unified:
        base = {
            "positionElectionId": pe.get("id"),
            "isPrimary": pe.get("isPrimary"),
            "isRunoff": pe.get("isRunoff"),
            "isRecall": pe.get("isRecall"),
            "isUnexpired": pe.get("isUnexpired"),
            "seats": pe.get("seats"),
            "electionId": pe.get("election", {}).get("id"),
            "electionName": pe.get("election", {}).get("name"),
            "electionDay": pe.get("election", {}).get("electionDay"),
            "electionState": pe.get("election", {}).get("state"),
            "positionId": pe.get("position", {}).get("id"),
            "positionName": pe.get("position", {}).get("name"),
            "positionLevel": pe.get("position", {}).get("level"),
            "positionState": pe.get("position", {}).get("state"),
        }

        candidacies = pe.get("candidacies") or []
        if not candidacies:
            rows.append(
                {
                    **base,
                    "candidacyId": "",
                    "candidateId": "",
                    "candidateFullName": "",
                    "candidacyWithdrawn": "",
                    "candidacyResult": "",
                }
            )
            continue

        for c in candidacies:
            rows.append(
                {
                    **base,
                    "candidacyId": c.get("id"),
                    "candidateId": c.get("candidate", {}).get("id"),
                    "candidateFullName": c.get("candidate", {}).get("fullName"),
                    "candidacyWithdrawn": c.get("withdrawn"),
                    "candidacyResult": c.get("result"),
                }
            )

    return rows