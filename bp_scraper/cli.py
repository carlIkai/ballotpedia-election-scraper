from __future__ import annotations
import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List
import pandas as pd

from .discovery import discover_state_pages, discover_house_district_pages
from .scrape import scrape_page
from .dataframes import (
    dedupe_candidates_df, dedupe_races_df,
    trim_string_columns, serialize_list_col_as_json, advancers_list_or_null,
    write_csv_strict,
)
from .unified import (
    build_position_elections, position_elections_to_rows, slugify, race_title_for_chamber
)
from .utils import normalize_state_name, normalize_state_filter_arg, display_clean_name, display_clean_list
from .constants import DEFAULT_DELAY, DEFAULT_RETRIES
# --- mapping utils (new) ---
from .mapping_utils import (
    load_races_to_house_mapping, mapping_dict,
    rewrite_race_label, canonical_join_id,
)

def main():
    ap = argparse.ArgumentParser(description="Scrape Ballotpedia U.S. Senate/House pages and output unified PositionElection JSON & CSV.")
    ap.add_argument("--year", type=int, default=2024, help="Election year to scrape.")
    ap.add_argument("--chamber", choices=["senate","house"], default="senate", help="Which chamber to scrape.")
    ap.add_argument("--state-url", default=None, help="Scrape a single state/district page by URL (skips discovery).")
    ap.add_argument("--state", default=None, help="Limit discovery to a single state (full name like 'Arizona' or USPS code like 'AZ').")
    ap.add_argument("--outdir", default="data/processed", help="Output directory.")
    ap.add_argument("--stamp", default=None, help="Optional YYYYMMDD stamp; defaults to today.")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--primary", action="store_true", help="Parse primary elections instead of general elections.")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="Base delay between requests (seconds).")
    ap.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help="Max retries per request.")
    ap.add_argument(
        "--house-mapping",
        default="data/races_to_house_label_mapping.csv",
        help="CSV mapping to rewrite House primary race labels to us-house labels for perfect joining."
    )
    args = ap.parse_args()

    stamp = args.stamp or datetime.now().strftime("%Y%m%d")
    outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)

    race_summaries: List[Dict[str, object]] = []
    all_cards: List[dict] = []
    seen_log_keys = set()

    if args.state_url:
        _, summaries, cards = scrape_page(
            args.state_url, args.year, chamber=args.chamber,
            verbose=args.verbose, primary=args.primary,
            delay=args.delay, retries=args.retries
        )
        for summary in summaries:
            if (summary.get("winner_name") is not None) or cards:
                race_summaries.append(summary)
        all_cards.extend(cards)
    else:
        state_pages = discover_state_pages(args.year, args.chamber, verbose=args.verbose, delay=args.delay, retries=args.retries)

        state_filter_norm = normalize_state_filter_arg(args.state)
        if state_filter_norm:
            before = len(state_pages)
            state_pages = [t for t in state_pages if normalize_state_name(t[0]) == state_filter_norm]
            if args.verbose:
                print(f"[overview] filtered to state: {state_filter_norm} -> {len(state_pages)} page(s) (was {before})")

        target_urls: List[tuple[str, str]] = []
        if args.chamber == "senate":
            target_urls = [(state, link) for state, _race, link in state_pages]
        else:
            for state, _race, link in state_pages:
                district_links = discover_house_district_pages(state, link, args.year, verbose=args.verbose, delay=args.delay, retries=args.retries)
                for durl in district_links:
                    target_urls.append((state, durl))

            if args.verbose:
                states = len(state_pages)
                districts = len(target_urls)
                print(f"[overview] discovered {districts} district pages for {args.year} (House) across {states} states")

        for state, link in target_urls:
            try:
                _, summaries, cards = scrape_page(
                    link, args.year, chamber=args.chamber,
                    verbose=False, primary=args.primary,
                    delay=args.delay, retries=args.retries
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
                        mode = 'primary' if args.primary else 'general'
                        print(f"[warn] {state}: skipped (no {args.year} {mode} results) — {link}")
            except KeyboardInterrupt:
                print("\n[abort] interrupted by user")
                break
            except Exception as e:
                if args.verbose:
                    print(f"[warn] {state}: {e} — {link}")

    races_df = pd.DataFrame(race_summaries).reindex(
        columns=["state","race","year","winner_name","advancers","is_jungle_primary","primary_party","source_url"]
    )
    cards_df = pd.DataFrame(all_cards).reindex(
        columns=["state","race","year","name","party","total_votes","incumbent","is_winner","is_advancer","source_url"]
    )

    if not races_df.empty:
        races_df["state"] = races_df["state"].map(normalize_state_name)
    if not cards_df.empty:
        cards_df["state"] = cards_df["state"].map(normalize_state_name)

    races_df = dedupe_races_df(races_df)
    cards_df = dedupe_candidates_df(cards_df)

    if (args.chamber == "house") and (not races_df.empty):
        is_house = races_df["race"].str.contains("U.S. House", case=False, na=False)
        is_primary = races_df["race"].str.contains("primary", case=False, na=False)
        mask = is_house & is_primary

        try:
            mapping_df = load_races_to_house_mapping(args.house_mapping)
            m = mapping_dict(mapping_df)
        except Exception:
            m = {}

        if m:
            races_df.loc[mask, "house_label"] = races_df.loc[mask].apply(
                lambda r: rewrite_race_label(r["state"], r["year"], r["race"], m), axis=1
            )
        else:
            races_df.loc[mask, "house_label"] = races_df.loc[mask, "race"]

        races_df.loc[mask, "canonical_join_id"] = races_df.loc[mask].apply(
            lambda r: canonical_join_id(r["state"], r["year"], r["house_label"], is_primary=True), axis=1
        )

    if not cards_df.empty:
        def _final_party(row):
            party = row["party"]
            if (row["state"].lower() == "utah") and ("Independent American primary" in str(row["race"]))                    and (not party or party.strip().lower() in {"independent","other","nonpartisan",""}):
                return "Independent American"
            return party
        cards_df["party"] = cards_df.apply(_final_party, axis=1)
        cards_df["name"] = cards_df.apply(lambda r: display_clean_name(str(r["name"]), r["state"]), axis=1)

    if not races_df.empty:
        races_df["winner_name"] = races_df.apply(
            lambda r: display_clean_name(r["winner_name"], r["state"]) if pd.notna(r["winner_name"]) else r["winner_name"],
            axis=1
        )
        def _clean_adv(cell, state):
            if isinstance(cell, list): return display_clean_list(cell, state)
            if isinstance(cell, str):
                try:
                    arr = json.loads(cell)
                    if isinstance(arr, list): return display_clean_list(arr, state)
                except Exception:
                    pass
            return cell
        races_df["advancers"] = races_df.apply(lambda r: _clean_adv(r["advancers"], r["state"]), axis=1)

    races_df = trim_string_columns(races_df)
    cards_df = trim_string_columns(cards_df)

    outdir = Path(args.outdir)
    stamp = args.stamp or datetime.now().strftime("%Y%m%d")
    races_csv = outdir / f"races-{stamp}.csv"
    cards_csv = outdir / f"candidates-{stamp}.csv"
    races_df_csv = serialize_list_col_as_json(races_df.copy(), "advancers")
    write_csv_strict(races_df_csv, races_csv, boolean_cols=["is_jungle_primary"])
    write_csv_strict(cards_df, cards_csv, boolean_cols=["incumbent","is_winner","is_advancer"])

    races_json = outdir / f"races-{stamp}.json"
    cards_json = outdir / f"candidates-{stamp}.json"
    races_df_json = advancers_list_or_null(races_df.copy())
    races_json.write_text(json.dumps(json.loads(races_df_json.to_json(orient="records")), indent=2))
    cards_json.write_text(json.dumps(json.loads(cards_df.to_json(orient="records")), indent=2))

    unified = build_position_elections(races_df, cards_df, chamber=args.chamber)

    race_title = race_title_for_chamber(args.chamber, args.year)
    race_slug  = slugify(race_title)

    unified_json_path = outdir / f"{race_slug}-{stamp}.json"
    unified_json_path.write_text(json.dumps(unified, indent=2))

    pe_rows = position_elections_to_rows(unified)
    pe_df = pd.DataFrame(pe_rows, columns=[
        "positionElectionId","isPrimary","isRunoff","isRecall","isUnexpired","seats",
        "electionId","electionName","electionDay","electionState",
        "positionId","positionName","positionLevel","positionState",
        "candidacyId","candidateId","candidateFullName","candidacyWithdrawn","candidacyResult"
    ])

    pe_df = trim_string_columns(pe_df)
    unified_csv_path = outdir / f"{race_slug}-{stamp}.csv"
    write_csv_strict(
        pe_df,
        unified_csv_path,
        boolean_cols=["isPrimary","isRunoff","isUnexpired","isRecall","candidacyWithdrawn"]
    )

    if args.verbose:
        label = "Senate" if args.chamber == "senate" else "House"
        print(f"[ok] wrote {unified_json_path} and {unified_csv_path} ({len(unified)} position elections) [{race_title} / {label}]")

if __name__ == "__main__":
    main()
