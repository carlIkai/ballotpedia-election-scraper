from __future__ import annotations
import base64
import json
import re
from typing import Any, List, Optional
from bp_scraper.core.constants import USPS, USPS_INV

PARENS_ANY_RE = re.compile(r"\s*\([^)]*\)")

def nws(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def strip_parenthetical(s: str) -> str:
    return PARENS_ANY_RE.sub("", s or "").strip()

def norm_name(s: str) -> str:
    s = s or ""
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"incumbent", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    return s.lower()

def canonical_name(s: str) -> str:
    return norm_name(s)

def b64_id(kind: str, *parts: str) -> str:
    raw = f"{kind}:" + "|".join(parts)
    return base64.urlsafe_b64encode(raw.encode()).decode()

def display_clean_name(name: Optional[str], state: Optional[str]=None) -> str:
    nm = strip_parenthetical(name or "")
    return nm

def display_clean_list(names: Optional[List[str]], state: Optional[str]=None) -> Optional[List[str]]:
    if names is None: return None
    return [display_clean_name(n, state) for n in names]

def normalize_state_name(s: str) -> str:
    if not s: return s
    s0 = s.replace("’", "'").strip()
    for proper in sorted(USPS.keys(), key=len, reverse=True):
        if s0.lower().startswith(proper.lower()):
            return proper
    s1 = re.sub(r"'s.+$", "", s0)
    s1 = s1.strip()
    for proper in sorted(USPS.keys(), key=len, reverse=True):
        if s1.lower().startswith(proper.lower()):
            return proper
    return s

def normalize_state_filter_arg(s: Optional[str]) -> Optional[str]:
    if not s: return None
    s = s.strip()
    if not s: return None
    if len(s) == 2 and s.upper() in USPS_INV:
        return USPS_INV[s.upper()]
    return normalize_state_name(s)

def slugify(s: str) -> str:
    import re as _re
    return _re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')

def race_title_for_chamber(chamber: str, year: int) -> str:
    return f"US {'Senate' if chamber == 'senate' else 'House'} {year}"
