from __future__ import annotations

"""
Race-level summarization helpers.

This module reduces a set of parsed result rows to a minimal race summary.
It intentionally avoids page- or chamber-specific logic and relies only on
numeric signals already extracted (percentages and vote totals).

Primary responsibilities:
- select a single winner from candidate result rows
- optionally compute a list of advancers for jungle/nonpartisan primaries
"""

from typing import Any, Dict, List, Optional


def _coalesce_votes(v: Any) -> int:
    """Return an integer vote count or a sentinel value.

    Numeric values are cast to int. Non-numeric or missing values return -1
    so they safely lose comparisons when ranking candidates.
    """
    if isinstance(v, (int, float)):
        return int(v)
    return -1


def summarize_race(
    rows: List[dict],
    allowed_clean_names: Optional[set] = None,
    jungle_top: Optional[int] = None,
) -> Dict[str, Optional[object]]:
    """Compute a minimal summary for a single race.

    Winner selection rules:
    - Prefer rows with percentage values when available.
    - Break ties using total_votes when present.
    - Fall back to vote totals only if no percentages exist.
    - As a last resort, select the first row.

    For jungle/nonpartisan primaries, this function can also return a list
    of top-N advancing candidates based on the same ranking logic.

    Args:
        rows: Parsed candidate result rows for a single race.
        allowed_clean_names: Optional filter set (currently unused, kept for API stability).
        jungle_top: If provided, number of top candidates to mark as advancers.

    Returns:
        A dict containing at least:
        - winner_name: str | None

        And optionally:
        - advancers: list[str]
    """
    if not rows:
        return {"winner_name": None}

    filtered = rows

    # Prefer percentage-based ranking when available.
    rows_with_pct = [r for r in filtered if r.get("pct") is not None]
    if rows_with_pct:
        winner_row = max(
            rows_with_pct,
            key=lambda r: (r["pct"], _coalesce_votes(r.get("total_votes"))),
        )
    else:
        # Fall back to total votes when percentages are missing.
        nonnull_votes = [r for r in filtered if r.get("total_votes") is not None]
        if nonnull_votes:
            winner_row = max(
                nonnull_votes,
                key=lambda r: _coalesce_votes(r.get("total_votes")),
            )
        else:
            # Absolute fallback: take the first row.
            winner_row = filtered[0]

    result: Dict[str, Optional[object]] = {
        "winner_name": winner_row.get("candidate")
    }

    # For jungle primaries, compute the top-N advancing candidates.
    if jungle_top and filtered:
        ranked = sorted(
            filtered,
            key=lambda r: (
                (r.get("pct") or -1),
                _coalesce_votes(r.get("total_votes")),
            ),
            reverse=True,
        )
        result["advancers"] = [r.get("candidate") for r in ranked[:jungle_top]]

    return result
