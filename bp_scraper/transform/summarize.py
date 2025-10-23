from __future__ import annotations
from typing import Any, Dict, List, Optional

def _coalesce_votes(v: Any) -> int:
    if isinstance(v, (int, float)): return int(v)
    return -1

def summarize_race(rows: List[dict], allowed_clean_names: Optional[set]=None, jungle_top: Optional[int]=None) -> Dict[str, Optional[object]]:
    if not rows: return {"winner_name": None}
    filtered = rows
    rows_with_pct = [r for r in filtered if r.get("pct") is not None]
    if rows_with_pct:
        winner_row = max(rows_with_pct, key=lambda x: (x["pct"], _coalesce_votes(x.get("total_votes"))))
    else:
        nonnull_votes = [r for r in filtered if r.get("total_votes") is not None]
        winner_row = max(nonnull_votes, key=lambda x: _coalesce_votes(x.get("total_votes"))) if nonnull_votes else filtered[0]
    result: Dict[str, Optional[object]] = {"winner_name": winner_row["candidate"]}
    if jungle_top and filtered:
        ranked = sorted(filtered, key=lambda r: ((r.get("pct") or -1), _coalesce_votes(r.get("total_votes"))), reverse=True)
        result["advancers"] = [r["candidate"] for r in ranked[:jungle_top]]
    return result
