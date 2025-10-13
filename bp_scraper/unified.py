from __future__ import annotations
from typing import Any, Dict, List, Optional
import pandas as pd

from .utils import (
    normalize_state_name, display_clean_name, display_clean_list, b64_id, slugify, race_title_for_chamber
)
from .dates import ELECTION_DAY_BY_KEY, compute_federal_general_election_day
from .constants import USPS

def _phase_from_race_label(race_label: str) -> str:
    low = race_label.lower()
    if "primary" in low: return "Primary"
    return "General"

def _is_runoff_from_label(race_label: str) -> bool:
    return "runoff" in (race_label or "").lower()

def _is_unexpired_from_label(race_label: str) -> bool:
    return "(special)" in (race_label or "").lower()

def _election_day_for(state_name: str, year: int, phase: str) -> Optional[str]:
    return ELECTION_DAY_BY_KEY.get((state_name, year, phase))

def _election_day_or_default(state_name: str, year: int, phase: str) -> Optional[str]:
    iso = _election_day_for(state_name, year, phase)
    if not iso and phase == "General":
        return compute_federal_general_election_day(year)
    return iso

def _district_from_race_label(race_label: str) -> Optional[str]:
    import re
    m = re.search(r"\bDistrict\s+(\d+)\b", race_label or "", re.I)
    if m: return f"District {int(m.group(1))}"
    if re.search(r"\bAt[-\s]?large\b", race_label or "", re.I): return "At-large"
    return None

def build_position_elections(races_df: pd.DataFrame, candidates_df: pd.DataFrame, chamber: str) -> List[Dict[str, Any]]:
    if races_df.empty and candidates_df.empty:
        return []

    by_race = {}
    if not candidates_df.empty:
        for _, row in candidates_df.iterrows():
            key = (normalize_state_name(row["state"]), row["race"], int(row["year"]))
            by_race.setdefault(key, []).append(row)

    out: List[Dict[str, Any]] = []
    keys_seen = set()

    keys_all = set()
    if not races_df.empty:
        for _, r in races_df.iterrows():
            keys_all.add((normalize_state_name(r["state"]), r["race"], int(r["year"])))
    if not candidates_df.empty:
        for _, r in candidates_df.iterrows():
            keys_all.add((normalize_state_name(r["state"]), r["race"], int(r["year"])))

    for state_name, race_label, year in sorted(keys_all):
        pure_state = normalize_state_name(state_name)
        state_code = USPS.get(pure_state, pure_state[:2].upper())
        phase = _phase_from_race_label(race_label)
        is_primary = (phase == "Primary")
        is_runoff = _is_runoff_from_label(race_label)
        is_unexpired = _is_unexpired_from_label(race_label)

        if chamber == "senate":
            position_name = f"U.S. Senate - {pure_state}"
            position_id = b64_id("Position", "U.S. Senate", state_code)
        else:
            district = _district_from_race_label(race_label)
            suffix = f" {district}" if district else ""
            position_name = f"U.S. House - {pure_state}{suffix}"
            position_id = b64_id("Position", "U.S. House", state_code, district or "")

        if is_runoff:
            election_name = f"{pure_state} {year} {phase} Runoff Election"
            election_day_iso = _election_day_for(pure_state, year, "Runoff")
        else:
            election_name = f"{pure_state} {year} {phase} Election"
            election_day_iso = _election_day_or_default(pure_state, year, phase)

        election_id = b64_id("Election", state_code, str(year), f"{phase}{' Runoff' if is_runoff else ''}")
        pos_elex_id = b64_id("PositionElection", state_code, str(year), race_label)

        cands = []
        for _, crow in (pd.DataFrame(by_race.get((pure_state, race_label, year), []))).iterrows():
            original_full = str(crow["name"])
            full_name = display_clean_name(original_full, pure_state)
            cand_id = b64_id("Candidate", full_name)
            cand_res = "WON" if bool(crow.get("is_winner")) else "LOST"
            candcy_id = b64_id("Candidacy", state_code, str(year), race_label, full_name)
            cands.append({
                "id": candcy_id,
                "withdrawn": False,
                "result": cand_res,
                "candidate": {"id": cand_id, "fullName": full_name}
            })

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
                "state": state_code
            },
            "position": {
                "id": position_id,
                "name": position_name,
                "level": "FEDERAL",
                "state": state_code
            },
            "candidacies": cands
        }

        key = (state_code, year, race_label)
        if key not in keys_seen:
            out.append(pe)
            keys_seen.add(key)

    return out

def position_elections_to_rows(unified: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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
        cands = pe.get("candidacies") or []
        if not cands:
            rows.append({**base,
                        "candidacyId": "", "candidateId": "", "candidateFullName": "",
                        "candidacyWithdrawn": "", "candidacyResult": ""})
            continue
        for c in cands:
            rows.append({**base,
                        "candidacyId": c.get("id"),
                        "candidateId": c.get("candidate", {}).get("id"),
                        "candidateFullName": c.get("candidate", {}).get("fullName"),
                        "candidacyWithdrawn": c.get("withdrawn"),
                        "candidacyResult": c.get("result")})
    return rows


