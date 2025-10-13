from __future__ import annotations
import json
from pathlib import Path
from typing import List, Optional
import pandas as pd

from .utils import canonical_name, display_clean_name, display_clean_list
from .constants import USPS

def dedupe_candidates_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty: return df
    canon = df["name"].fillna("").map(canonical_name)
    df = df.assign(_canon=canon)

    def _pick_name(series: pd.Series) -> str:
        return max(series.dropna().astype(str), key=len, default="")

    def _pick_party(series: pd.Series) -> str:
        for v in series:
            if isinstance(v, str) and v.strip(): return v
        return ""

    grouped = (
        df.groupby(["state","race","year","_canon"], as_index=False)
          .agg({"name": _pick_name, "party": _pick_party, "total_votes":"max",
                "incumbent":"max","is_winner":"max","is_advancer":"max","source_url":"first"})
    )
    grouped = grouped[["state","race","year","name","party","total_votes","incumbent","is_winner","is_advancer","source_url"]]
    return grouped

def dedupe_races_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty: return df
    df = df.sort_values(["state","race","year"])
    keep = (
        df.groupby(["state","race","year"], as_index=False)
          .agg({"winner_name":"first","advancers":"first","is_jungle_primary":"first",
                "primary_party":"first","source_url":"first"})
    )
    keep = keep[["state","race","year","winner_name","advancers","is_jungle_primary","primary_party","source_url"]]
    return keep

def _trim_string_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_string_dtype(df[col]) or df[col].dtype == object:
            df[col] = df[col].apply(lambda x: x.strip() if isinstance(x, str) else x)
    return df

def _serialize_list_col_as_json(df: pd.DataFrame, col: str) -> pd.DataFrame:
    if df is None or df.empty or col not in df.columns:
        return df
    df = df.copy()
    def conv(value):
        if isinstance(value, list):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return ""
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return json.dumps(parsed, ensure_ascii=False)
            except Exception:
                return stripped
        if pd.isna(value):
            return ""
        return ""
    df[col] = df[col].apply(conv)
    return df

def _advancers_list_or_null(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or "advancers" not in df.columns:
        return df
    df = df.copy()
    def conv(value):
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                parsed = json.loads(stripped)
                return parsed if isinstance(parsed, list) else None
            except Exception:
                return None
        if pd.isna(value):
            return None
        return None
    df["advancers"] = df["advancers"].apply(conv)
    return df

def write_csv_strict(df: pd.DataFrame, path: Path, boolean_cols: Optional[List[str]] = None) -> None:
    if df is None or df.empty:
        df.to_csv(path, index=False)
        return

    df_out = df.copy()

    for col in (boolean_cols or []):
        if col in df_out.columns:
            def _b(x):
                if pd.isna(x): return ''
                return 'true' if bool(x) else 'false'
            df_out[col] = df_out[col].map(_b)

    for col in df_out.columns:
        if pd.api.types.is_numeric_dtype(df_out[col]):
            continue
        df_out[col] = df_out[col].where(pd.notnull(df_out[col]), "")

    df_out.to_csv(path, index=False)


trim_string_columns = _trim_string_columns
serialize_list_col_as_json = _serialize_list_col_as_json
advancers_list_or_null = _advancers_list_or_null
