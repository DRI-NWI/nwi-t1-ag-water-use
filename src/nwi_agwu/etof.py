"""Monthly OpenET ENSEMBLE field records -> EToF, gap-filled EToF and ETa.

Rules are those of the delivered processor (agricultural-water-forecasting
process_field_summaries.py, see docs/VENDORED_SOURCES.md):
  * -999 -> NaN in the variable columns; ETA_MM == 0 -> NaN (OpenET export bug).
  * EToF = ETA_MM / ETO_MM where ETO_MM > 0.
  * One-month gaps filled by linear interpolation per field (pandas
    ``interpolate(method='linear', limit=1)``), flagged ``linear_1month``.
  * Months that remain missing are flagged ``unfilled_missing``.

Optional EToF climatology fill (config ``etof.climatology_fill``, off in the final run):
  * Climatologies follow calculate_field_climatologies.calculate_etof_climatologies:
    observed EToF clipped to [0, 1.5], mean per field and calendar month within each
    gap window, clipped again. Earlier climatology sets are NV-only and keyed by May-era
    OPENET_IDs that do not match the 2026-08-20 population, so these are built from step 01's field_month_base.parquet (scripts/01b).
  * apply_climatology_fill follows apply_climatology_gap_filling_etof: every month still
    ``unfilled_missing`` after the linear fill takes the field's climatology for that
    calendar month from the window containing its year (nearest window outside them);
    flagged ``climatology``. Months with no climatology stay ``unfilled_missing``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .units import mm_to_acft

EXPORT_COLUMNS = ["NWI_ID", "OPENET_ID", "STATE", "irr_eff", "ITYPE", "YEAR", "MONTH", "ETA_MM", "PPT_MM", "EFF_PPT_MM",
                  "ETO_MM", "%_IRRIGATED", "ACRES_FTR_GEOM", "ACRES_ALL", "ACRES_IRR"]
SENTINEL_COLUMNS = ["ETA_MM", "PPT_MM", "EFF_PPT_MM", "ETO_MM"]
GAP_WINDOWS = [(1985, 1991), (1992, 1997), (1998, 2003), (2004, 2009), (2010, 2015), (2016, 2021), (2022, 2025)]
CLIMO_COLUMNS = [f"EToF_climo_{m:02d}" for m in range(1, 13)]
ETOF_CLIP = (0.0, 1.5)


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


def window_key(year: int, windows) -> str:
    """Window containing year, else the nearest one (get_field_climatology rule)."""
    for a, b in windows:
        if a <= year <= b:
            return f"{a}_{b}"
    a, b = min(windows, key=lambda w: min(abs(year - w[0]), abs(year - w[1])))
    return f"{a}_{b}"


def build_climatologies(base: pd.DataFrame, attrs: pd.DataFrame, windows=GAP_WINDOWS) -> dict[str, pd.DataFrame]:
    """Per-window field x calendar-month mean of observed EToF (delivered file shape:
    OPENET_ID index, HA_NAME_CODE, NWI_ID, EToF_climo_01..12)."""
    e = base[["NWI_ID", "YEAR", "MONTH", "EToF"]].copy()
    e["EToF"] = e["EToF"].clip(*ETOF_CLIP)
    e = e[e["EToF"].notna()]
    ids = base[["NWI_ID", "OPENET_ID"]].drop_duplicates("NWI_ID").merge(
        attrs[["NWI_ID", "HA_NAME_CODE"]], on="NWI_ID", how="left", validate="one_to_one")
    out = {}
    for a, b in windows:
        w = e[e["YEAR"].between(a, b)]
        c = w.groupby(["NWI_ID", "MONTH"])["EToF"].mean().unstack("MONTH").clip(*ETOF_CLIP)
        c = c.reindex(columns=range(1, 13))
        c.columns = CLIMO_COLUMNS
        c = ids.merge(c.reset_index(), on="NWI_ID", how="inner").set_index("OPENET_ID")
        out[f"{a}_{b}"] = c[["HA_NAME_CODE", "NWI_ID"] + CLIMO_COLUMNS]
    return out


def write_climatologies(climos: dict[str, pd.DataFrame], out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    for k, c in climos.items():
        c.to_csv(out_dir / f"etof_climatology_{k}.csv")


def load_climatologies(climo_dir: Path, windows=GAP_WINDOWS) -> dict[str, pd.DataFrame]:
    out = {}
    for a, b in windows:
        f = Path(climo_dir) / f"etof_climatology_{a}_{b}.csv"
        if not f.exists():
            raise FileNotFoundError(f"{f} missing; run scripts/01b_etof_climatology.py")
        out[f"{a}_{b}"] = pd.read_csv(f, index_col="OPENET_ID")
    return out


def apply_climatology_fill(df: pd.DataFrame, climos: dict[str, pd.DataFrame], windows=GAP_WINDOWS) -> pd.DataFrame:
    """Fill ``unfilled_missing`` months from the field's window climatology, in place."""
    gaps = df.index[df["interpolation_method"].eq("unfilled_missing")]
    if len(gaps) == 0:
        return df
    long = []
    for k, c in climos.items():
        m = c.melt(id_vars=["NWI_ID"], value_vars=CLIMO_COLUMNS, var_name="MONTH", value_name="climo")
        m["MONTH"] = m["MONTH"].str[-2:].astype(int)
        m["window"] = k
        long.append(m.dropna(subset=["climo"]))
    long = pd.concat(long, ignore_index=True)
    g = df.loc[gaps, ["NWI_ID", "YEAR", "MONTH"]]
    keys = {y: window_key(int(y), windows) for y in g["YEAR"].unique()}
    g = g.assign(window=g["YEAR"].map(keys), _row=gaps)
    g = g.merge(long, on=["NWI_ID", "window", "MONTH"], how="inner", validate="many_to_one")
    rows = g["_row"].to_numpy()
    df.loc[rows, "EToF_interpolated"] = g["climo"].to_numpy()
    df.loc[rows, "interpolation_method"] = "climatology"
    df.loc[rows, "ETA_MM_post_interp"] = df.loc[rows, "EToF_interpolated"] * df.loc[rows, "ETO_MM"]
    df.loc[rows, "ETa_vol_acre_ft"] = mm_to_acft(df.loc[rows, "ETA_MM_post_interp"], df.loc[rows, "ACRES_FTR_GEOM"])
    return df
