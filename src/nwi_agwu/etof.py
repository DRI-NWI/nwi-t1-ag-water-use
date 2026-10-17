"""Monthly OpenET ENSEMBLE field records -> EToF, gap-filled EToF and ETa.

Rules are those of the delivered processor (agricultural-water-forecasting
process_field_summaries.py, see docs/VENDORED_SOURCES.md):
  * -999 -> NaN in the variable columns; ETA_MM == 0 -> NaN (OpenET export bug).
  * EToF = ETA_MM / ETO_MM where ETO_MM > 0.
  * One-month gaps filled by linear interpolation per field (pandas
    ``interpolate(method='linear', limit=1)``), flagged ``linear_1month``.
  * Months that remain missing are flagged ``unfilled_missing``.
No EToF climatology was available for the 2026-08-20 population, so none is used.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .units import mm_to_acft

EXPORT_COLUMNS = ["NWI_ID", "OPENET_ID", "STATE", "irr_eff", "ITYPE", "YEAR", "MONTH", "ETA_MM", "PPT_MM", "EFF_PPT_MM",
                  "ETO_MM", "%_IRRIGATED", "ACRES_FTR_GEOM", "ACRES_ALL", "ACRES_IRR"]
SENTINEL_COLUMNS = ["ETA_MM", "PPT_MM", "EFF_PPT_MM", "ETO_MM"]


def load_ensemble(ensemble_dir: str, start: int, end: int) -> pd.DataFrame:
    files = sorted(Path(ensemble_dir).rglob("*.parquet"))
    frames = []
    for f in files:
        d = pd.read_parquet(f, columns=EXPORT_COLUMNS)
        d = d[d["YEAR"].between(start, end)]
        frames.append(d)
    df = pd.concat(frames, ignore_index=True)
    df["MONTH"] = df["MONTH"].astype(int)
    df["YEAR"] = df["YEAR"].astype(int)
    before = len(df)
    df = df.drop_duplicates()
    if df.duplicated(["NWI_ID", "YEAR", "MONTH"]).any():
        raise ValueError("ENSEMBLE export has conflicting duplicate field-months")
    df.attrs["dropped_exact_duplicates"] = before - len(df)
    return df


def build_etof(df: pd.DataFrame, limit: int = 1) -> pd.DataFrame:
    out = df.copy()
    for col in SENTINEL_COLUMNS:
        out[col] = out[col].replace(-999, np.nan)
    out["ETA_MM_EXPORT"] = out["ETA_MM"]
    out.loc[out["ETA_MM"] == 0, "ETA_MM"] = np.nan
    out["EToF"] = np.where(out["ETO_MM"] > 0, out["ETA_MM"] / out["ETO_MM"], np.nan)
    out = out.sort_values(["NWI_ID", "YEAR", "MONTH"], kind="mergesort").reset_index(drop=True)
    out["EToF_interpolated"] = out.groupby("NWI_ID", sort=False)["EToF"].transform(
        lambda x: x.interpolate(method="linear", limit=limit))
    filled = out["EToF"].isna() & out["EToF_interpolated"].notna()
    out["interpolation_method"] = np.where(filled, "linear_1month", "original")
    out.loc[out["EToF_interpolated"].isna(), "interpolation_method"] = "unfilled_missing"
    out["ETA_MM_post_interp"] = out["EToF_interpolated"] * out["ETO_MM"]
    out["ETa_vol_acre_ft"] = mm_to_acft(out["ETA_MM_post_interp"], out["ACRES_FTR_GEOM"])
    return out
