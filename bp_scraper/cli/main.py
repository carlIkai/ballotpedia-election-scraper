"""
bp_scraper CLI entrypoint.

This module orchestrates:
- page discovery (federal or state scope)
- scraping pages into race summaries + candidate cards
- normalization/deduping
- writing raw outputs (races/candidates) as CSV + JSON
- building unified PositionElection objects and writing CSV + JSON

Design notes:
- This file contains orchestration logic and minimal parsing logic.
- Parsing/scraping/normalization belong in bp_scraper.* modules to keep this CLI thin.
"""


import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from bp_scraper.parsing.discovery import (
    discover_state_pages,
    discover_house_district_pages,
    discover_state_election_pages,
)
from bp_scraper.pipelines.scrape import scrape_page
from bp_scraper.transform.frames import (
    dedupe_candidates_df,
    dedupe_races_df,
    trim_string_columns,
    serialize_list_col_as_json,
    advancers_list_or_null,
    write_csv_strict,
)
from bp_scraper.transform.unify import build_position_elections, position_elections_to_rows
from bp_scraper.parsing.normalize import (
    slugify,
    race_title_for_chamber,
    normalize_state_name,
    normalize_state_filter_arg,
    display_clean_name,
    display_clean_list,
)
from bp_scraper.core.constants import DEFAULT_DELAY, DEFAULT_RETRIES
from bp_scraper.parsing.mapping import (
    load_races_to_house_mapping,
    mapping_dict,
    rewrite_race_label,
    canonical_join_id,
)


def _parse_offices_arg(raw: Optional[str]) -> Optional[List[str]]:
    """Parse --offices CLI arg into a normalized list.

    Args:
        raw: Comma-separated office keys (e.g., "governor,state_lower") or None.

    Returns:
        List of nonempty office keys or None if input was falsy.
    """
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(",")]
    return [p for p in parts if p]


def _race_title_for_run(args, *, state_name: Optional[str]) -> str:
    """Create a human-friendly run title used for slug and output names.

    Args:
        args: Parsed CLI arguments.
        state_name: Normalized state filter name , if state scope, otherwise None.

    Returns:
        A descriptive title string.
    """
    mode = "primary" if args.primary else "general"

    if args.scope == "state":
        
        # Use normalized state names so output filenames stay stable across input formats.
        st = normalize_state_name(state_name or args.state or "state")
        return f"{st} state elections {args.year} {mode}"

    return race_title_for_chamber(args.chamber, args.year)


def main() -> None:
    """CLI entrypoint.

    Side effects:
        - Makes HTTP requests via scrape/discovery functions.
        - Writes multiple CSV/JSON files under --outdir.

    Exit behavior:
        - Raises SystemExit for invalid argument combos.
        - Prints warnings when --verbose is used.
    """
    ap = argparse.ArgumentParser(
        description="Scrape Ballotpedia pages and output unified PositionElection JSON & CSV."
    )
    ap.add_argument("--year", type=int, default=2024, help="Election year to scrape.")
    ap.add_argument(
        "--scope",
        choices=["federal", "state"],
        default="federal",
        help="Scrape federal or state elections.",
    )
    ap.add_argument(
        "--chamber",
        choices=["senate", "house"],
        default="senate",
        help="Which chamber to scrape (federal scope).",
    )
    ap.add_argument("--state-url", default=None, help="Scrape a single page by URL (skips discovery).")
    ap.add_argument(
        "--state",
        default=None,
        help="Limit discovery to a single state (full name like 'Virginia' or USPS like 'VA').",
    )
    ap.add_argument(
        "--offices",
        default=None,
        help="State scope: comma-separated office keys (governor,lt_governor,attorney_general,state_lower,state_upper). Default=all.",
    )
    ap.add_argument("--outdir", default="data/processed", help="Output directory.")
    ap.add_argument("--stamp", default=None, help="Optional YYYYMMDD stamp; defaults to today.")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--primary", action="store_true", help="Parse primary elections instead of general elections.")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="Base delay between requests (seconds).")
    ap.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help="Max retries per request.")
    ap.add_argument(
        "--house-mapping",
        default="data/races_to_house_label_mapping.csv",
        help="CSV mapping to rewrite House primary race labels to us-house labels for perfect joining.",
    )
    args = ap.parse_args()

    stamp = args.stamp or datetime.now().strftime("%Y%m%d")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    office_list = _parse_offices_arg(args.offices)

    # Collect raw scrape outputs first. DataFrame normalization comes after discovery/scrape completes.
    race_summaries: List[Dict[str, object]] = []
    all_cards: List[dict] = []
    seen_log_keys = set()

    state_filter_norm: Optional[str] = None
    if args.scope == "state":
        state_filter_norm = normalize_state_filter_arg(args.state)
        if not state_filter_norm:
            raise SystemExit("State scope requires --state (e.g., --state VA or --state Virginia)")

    if args.state_url:
        
        # Single URL mode is primarily for debugging scraper output without discovery noise.
        chamber_for_scrape = "state" if args.scope == "state" else args.chamber

        _, summaries, cards = scrape_page(
            args.state_url,
            args.year,
            chamber=chamber_for_scrape,
            verbose=args.verbose,
            primary=args.primary,
            delay=args.delay,
            retries=args.retries,
            scope=args.scope,
        )
        for summary in summaries:
            
            # Keep the race if a winner or any candidate cards are found.
            if (summary.get("winner_name") is not None) or cards:
                race_summaries.append(summary)
        all_cards.extend(cards)

    else:
        target_urls: List[tuple[str, str]] = []

        if args.scope == "state":
            pages = discover_state_election_pages(
                args.year,
                state_filter_norm or "",
                offices=office_list,
                verbose=args.verbose,
                delay=args.delay,
                retries=args.retries,
            )
            target_urls = [(state, link) for state, _lbl, link in pages]

        else:
            state_pages = discover_state_pages(
                args.year,
                args.chamber,
                verbose=args.verbose,
                delay=args.delay,
                retries=args.retries,
            )

            if args.state:
                filter_norm = normalize_state_filter_arg(args.state)
                if filter_norm:
                    before = len(state_pages)
                    state_pages = [t for t in state_pages if normalize_state_name(t[0]) == filter_norm]
                    if args.verbose:
                        print(f"[overview] filtered to state: {filter_norm} -> {len(state_pages)} page(s) (was {before})")

            if args.chamber == "senate":
                target_urls = [(state, link) for state, _race, link in state_pages]
            else:
                
                # House results are reported at the district level, so expand each state page into per district URLs.
                for state, _race, link in state_pages:
                    district_links = discover_house_district_pages(
                        state,
                        link,
                        args.year,
                        verbose=args.verbose,
                        delay=args.delay,
                        retries=args.retries,
                    )
                    for durl in district_links:
                        target_urls.append((state, durl))

                if args.verbose:
                    states = len(state_pages)
                    districts = len(target_urls)
                    print(f"[overview] discovered {districts} district pages for {args.year} (House) across {states} states")

        for state, link in target_urls:
            try:
                chamber_for_scrape = "state" if args.scope == "state" else args.chamber

                _, summaries, cards = scrape_page(
                    link,
                    args.year,
                    chamber=chamber_for_scrape,
                    verbose=False,
                    primary=args.primary,
                    delay=args.delay,
                    retries=args.retries,
                    scope=args.scope,
                )

                if summaries or cards:
                    for summary in summaries:
                        race_summaries.append(summary)
                        key = (summary["state"], summary["race"], summary["year"])
                        if args.verbose and key not in seen_log_keys:
                            print(f"[{summary['state']}] winner={summary['winner_name']} ({summary['race']})")
                            seen_log_keys.add(key)
                    all_cards.extend(cards)
                else:
                    if args.verbose:
                        mode = "primary" if args.primary else "general"
                        print(f"[warn] {state}: skipped (no {args.year} {mode} results) — {link}")

            except KeyboardInterrupt:
                print("\n[abort] interrupted by user")
                break
            except Exception as e:
                
                # Failures are logged and skipped so one bad page does not kill the run.
                if args.verbose:
                    print(f"[warn] {state}: {e} — {link}")

    races_df = pd.DataFrame(race_summaries).reindex(
        columns=["state", "race", "year", "winner_name", "advancers", "is_jungle_primary", "primary_party", "source_url"]
    )
    cards_df = pd.DataFrame(all_cards).reindex(
        columns=["state", "race", "year", "name", "party", "total_votes", "incumbent", "is_winner", "is_advancer", "source_url"]
    )

    # Normalize state names before dedupe/unify to avoid mismatched joins caused by formatting differences.
    if not races_df.empty:
        races_df["state"] = races_df["state"].map(normalize_state_name)
    if not cards_df.empty:
        cards_df["state"] = cards_df["state"].map(normalize_state_name)

    races_df = dedupe_races_df(races_df)
    cards_df = dedupe_candidates_df(cards_df)

    if (args.scope == "federal") and (args.chamber == "house") and (not races_df.empty):
        
        # Ballotpedia House primary race titles vary across pages. Rewrite to a canonical label so joins are reliable.
        is_house = races_df["race"].str.contains("U.S. House", case=False, na=False)
        is_primary = races_df["race"].str.contains("primary", case=False, na=False)
        mask = is_house & is_primary

        try:
            mapping_df = load_races_to_house_mapping(args.house_mapping)
            mapped = mapping_dict(mapping_df)
        except Exception:
            mapped = {}

        if mapped:
            races_df.loc[mask, "house_label"] = races_df.loc[mask].apply(
                lambda r: rewrite_race_label(r["state"], r["year"], r["race"], mapped),
                axis=1,
            )
        else:
            races_df.loc[mask, "house_label"] = races_df.loc[mask, "race"]

        races_df.loc[mask, "canonical_join_id"] = races_df.loc[mask].apply(
            lambda r: canonical_join_id(r["state"], r["year"], r["house_label"], is_primary=True),
            axis=1,
        )

    if not cards_df.empty:
        def _final_party(row):
            """Apply known one-off corrections where Ballotpedia labeling is inconsistent."""
            party = row["party"]
            if (
                (str(row["state"]).lower() == "utah")
                and ("Independent American primary" in str(row["race"]))
                and (not party or party.strip().lower() in {"independent", "other", "nonpartisan", ""})
            ):
                return "Independent American"
            return party

        cards_df["party"] = cards_df.apply(_final_party, axis=1)
        cards_df["name"] = cards_df.apply(lambda r: display_clean_name(str(r["name"]), r["state"]), axis=1)

    if not races_df.empty:
        races_df["winner_name"] = races_df.apply(
            lambda r: display_clean_name(r["winner_name"], r["state"]) if pd.notna(r["winner_name"]) else r["winner_name"],
            axis=1,
        )

        def _clean_adv(cell, state):
            """Normalize advancers to a display-cleaned list when possible."""
            if isinstance(cell, list):
                return display_clean_list(cell, state)
            if isinstance(cell, str):
                try:
                    arr = json.loads(cell)
                    if isinstance(arr, list):
                        return display_clean_list(arr, state)
                except Exception:
                    pass
            return cell

        races_df["advancers"] = races_df.apply(lambda r: _clean_adv(r["advancers"], r["state"]), axis=1)

    races_df = trim_string_columns(races_df)
    cards_df = trim_string_columns(cards_df)

    # Save races and candidates in CSV and JSON.
    races_csv = outdir / f"races-{stamp}.csv"
    cards_csv = outdir / f"candidates-{stamp}.csv"
    races_df_csv = serialize_list_col_as_json(races_df.copy(), "advancers")
    write_csv_strict(races_df_csv, races_csv, boolean_cols=["is_jungle_primary"])
    write_csv_strict(cards_df, cards_csv, boolean_cols=["incumbent", "is_winner", "is_advancer"])

    races_json = outdir / f"races-{stamp}.json"
    cards_json = outdir / f"candidates-{stamp}.json"
    races_df_json = advancers_list_or_null(races_df.copy())
    races_json.write_text(json.dumps(json.loads(races_df_json.to_json(orient="records")), indent=2))
    cards_json.write_text(json.dumps(json.loads(cards_df.to_json(orient="records")), indent=2))

    chamber_for_unify = "state" if args.scope == "state" else args.chamber
    unified = build_position_elections(races_df, cards_df, chamber=chamber_for_unify, scope=args.scope)

    race_title = _race_title_for_run(args, state_name=state_filter_norm)
    race_slug = slugify(race_title)

    unified_json_path = outdir / f"{race_slug}-{stamp}.json"
    unified_json_path.write_text(json.dumps(unified, indent=2))

    pe_rows = position_elections_to_rows(unified)
    pe_df = pd.DataFrame(
        pe_rows,
        columns=[
            "positionElectionId",
            "isPrimary",
            "isRunoff",
            "isRecall",
            "isUnexpired",
            "seats",
            "electionId",
            "electionName",
            "electionDay",
            "electionState",
            "positionId",
            "positionName",
            "positionLevel",
            "positionState",
            "candidacyId",
            "candidateId",
            "candidateFullName",
            "candidacyWithdrawn",
            "candidacyResult",
        ],
    )

    pe_df = trim_string_columns(pe_df)
    unified_csv_path = outdir / f"{race_slug}-{stamp}.csv"
    write_csv_strict(
        pe_df,
        unified_csv_path,
        boolean_cols=["isPrimary", "isRunoff", "isUnexpired", "isRecall", "candidacyWithdrawn"],
    )

    if args.verbose:
        label = "State" if args.scope == "state" else ("Senate" if args.chamber == "senate" else "House")
        print(
            f"[ok] wrote {unified_json_path} and {unified_csv_path} "
            f"({len(unified)} position elections) [{race_title} / {label}]"
        )


if __name__ == "__main__":
    main()
