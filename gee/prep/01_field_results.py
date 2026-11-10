#!/usr/bin/env python3
"""
GEE app input 1: field-month, field-year and field tables from the run's whole-field file.

Reads 05_field_summaries/field_summaries_EToF_final (.parquet, .csv or .csv.gz) and writes,
under the output folder (default <run>/09_gee_app):

  field_month_core.parquet  every field-month: NWI_ID, YEAR, MONTH, irrigated, ETa_final_acre_ft,
                            NetET_pre_subsidy_acre_ft, GW_subsidy_acre_ft. Used to recompute the
                            subsidy for another f exactly as the run does it, month by month:
                            min(f x ETa, max(NetET_pre_subsidy, 0)).
  field_year.parquet        every field-year: the irrigated flag and annual volumes.
  field_static.parquet      every field: attributes, the run's f (gw_frac) and its April-October
                            mean ETo.

It then checks that the file is internally consistent (one f per field; the subsidy equals
min(f x ETa, max(NetET_pre_subsidy, 0)) in every field-month) and that it reproduces the totals
in README.md and docs/tables/ha_gw_subsidy_new_vs_ma.csv. Any failed check stops the script.

Usage:
    python 01_field_results.py [--field-file PATH] [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from _common import (DEFAULT_FIELD_FILE, END_YEAR, IRRIGATED_MIN_FRACTION, NY, REPO, START_YEAR,
                     out_dir)

# Statewide totals in README.md (irrigated field-years, 1985-2025 annual mean, acre-ft/yr).
README_TOTALS = {
    "ETa_final_acre_ft": 1_423_458,
    "Eff_PPT_vol_acre_ft": 363_361,
    "GW_subsidy_acre_ft": 34_733,
    "Carryforward_Used_acre_ft": 43_944,
    "NetET_final_acre_ft": 1_065_648,
    "AR_final_acre_ft": 1_567_158,
}
HA_TABLE = REPO / "docs" / "tables" / "ha_gw_subsidy_new_vs_ma.csv"

TEXT_COLS = ["NWI_ID", "STATE", "HYD_AREA", "HA_NAME_CODE", "IRR_SRC_ASSUMED"]
NUM_COLS = ["IRR_SRC", "CROP_major", "ACRES_FTR_GEOM", "%_IRRIGATED", "ETO_MM", "gw_frac",
            "ETa_final_acre_ft", "Eff_PPT_vol_acre_ft", "NetET_pre_subsidy_acre_ft",
            "GW_subsidy_potential_acre_ft", "GW_subsidy_acre_ft", "Carryforward_Used_acre_ft",
            "NetET_final_acre_ft", "AR_final_acre_ft"]
VOLUMES = ["ETa_final_acre_ft", "Eff_PPT_vol_acre_ft", "NetET_pre_subsidy_acre_ft",
           "GW_subsidy_potential_acre_ft", "GW_subsidy_acre_ft", "Carryforward_Used_acre_ft",
           "NetET_final_acre_ft", "AR_final_acre_ft"]


def read_field_months(path: Path) -> pd.DataFrame:
    """The columns the app needs, every field-month, typed (CSV text is cast explicitly)."""
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    if path.suffix == ".parquet":
        src = f"read_parquet('{path}')"
        cast_num = lambda c: f'CAST("{c}" AS DOUBLE)'
        cast_txt = lambda c: f'CAST("{c}" AS VARCHAR)'
    else:
        src = f"read_csv('{path}', header=true, all_varchar=true)"
        cast_num = lambda c: f"CAST(NULLIF(\"{c}\", '') AS DOUBLE)"
        cast_txt = lambda c: f"NULLIF(\"{c}\", '')"
    cols = ([f'{cast_txt(c)} AS "{c}"' for c in TEXT_COLS]
            + [f'CAST("{c}" AS INTEGER) AS "{c}"' for c in ("YEAR", "MONTH")]
            + [f'{cast_num(c)} AS "{c}"' for c in NUM_COLS])
    t0 = time.time()
    df = con.execute(f"SELECT {', '.join(cols)} FROM {src}").df()
    print(f"[read] {len(df):,} field-months, {df['NWI_ID'].nunique():,} fields "
          f"from {path.name} in {time.time() - t0:.0f} s")
    return df


def check(name, ok, detail=""):
    """Print a check result; raise if it failed."""
    print(f"  [{'ok' if ok else 'FAIL'}] {name}{(': ' + detail) if detail else ''}")
    if not ok:
        raise SystemExit(f"check failed: {name}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--field-file", type=Path, default=DEFAULT_FIELD_FILE,
                    help="whole-field file field_summaries_EToF_final (.parquet, .csv or .csv.gz)")
    ap.add_argument("--out", default=None, help="output folder (default <run>/09_gee_app)")
    args = ap.parse_args()
    out = out_dir(args.out)

    df = read_field_months(args.field_file)
    df["HYD_AREA"] = df["HYD_AREA"].str.strip()
    df["irrigated"] = df["%_IRRIGATED"] > IRRIGATED_MIN_FRACTION
    checks = {}

    print("Internal consistency")
    check("years", df["YEAR"].min() == START_YEAR and df["YEAR"].max() == END_YEAR,
          f"{df['YEAR'].min()}-{df['YEAR'].max()}")
    dup = df.duplicated(["NWI_ID", "YEAR", "MONTH"]).sum()
    check("one row per field-month", dup == 0, f"{dup} duplicates")
    nf = df.groupby("NWI_ID")["gw_frac"].nunique(dropna=False)
    check("one f per field", (nf == 1).all(), f"{(nf > 1).sum()} fields with more than one f")
    ni = df.groupby(["NWI_ID", "YEAR"])["%_IRRIGATED"].nunique(dropna=False)
    check("one irrigated fraction per field-year", (ni <= 1).all(), f"{(ni > 1).sum()} field-years vary")
    pot = df["gw_frac"] * df["ETa_final_acre_ft"]
    err = (pot - df["GW_subsidy_potential_acre_ft"]).abs().max()
    check("GW_subsidy_potential = f x ETa", err < 1e-9, f"max |diff| {err:.2e} acre-ft")
    sub = np.minimum(pot, df["NetET_pre_subsidy_acre_ft"].clip(lower=0))
    err = (sub - df["GW_subsidy_acre_ft"]).abs().max()
    check("GW_subsidy = min(f x ETa, max(NetET_pre_subsidy, 0))", err < 1e-9, f"max |diff| {err:.2e} acre-ft")
    checks["max_abs_err_subsidy_recompute_acre_ft"] = float(err)

    print("Totals against README.md (irrigated field-years, annual mean)")
    irr = df[df["irrigated"]]
    for col, expected in README_TOTALS.items():
        got = irr[col].sum() / NY
        checks[f"readme_{col}"] = got
        check(col, round(got) == expected, f"{got:,.1f} vs README {expected:,}")
    acres = irr.drop_duplicates(["NWI_ID", "YEAR"]).groupby("YEAR")["ACRES_FTR_GEOM"].sum().mean()
    checks["irrigated_acres_mean"] = acres
    print(f"  [info] irrigated acres, mean of yearly sums: {acres:,.0f} (README 540,966)")

    print("HA totals against docs/tables/ha_gw_subsidy_new_vs_ma.csv")
    ha = irr.groupby("HYD_AREA")[["ETa_final_acre_ft", "GW_subsidy_acre_ft"]].sum() / NY
    ref = pd.read_csv(HA_TABLE, dtype={"HYD_AREA": str}).set_index("HYD_AREA")
    j = ref.join(ha, how="outer")
    d_eta = (j["ETa_final_acre_ft"].round(2) - j["ETa_acre_ft_yr"]).abs().max()
    d_sub = (j["GW_subsidy_acre_ft"].round(2) - j["subsidy_new_acre_ft_yr"]).abs().max()
    check("HA list", j[["ETa_acre_ft_yr", "ETa_final_acre_ft"]].notna().all(axis=None),
          f"{len(ref)} HAs in the table, {len(ha)} in the file")
    check("HA ETa", d_eta <= 0.0051, f"max |diff| {d_eta:.3f} acre-ft/yr")
    check("HA subsidy (new DTW)", d_sub <= 0.0051, f"max |diff| {d_sub:.3f} acre-ft/yr")

    # ---- outputs -----------------------------------------------------------------------
    core = df[["NWI_ID", "YEAR", "MONTH", "irrigated", "ETa_final_acre_ft",
               "NetET_pre_subsidy_acre_ft", "GW_subsidy_acre_ft"]]
    core.to_parquet(out / "field_month_core.parquet", index=False)

    fy = df.groupby(["NWI_ID", "YEAR"], sort=True).agg(
        irrigated=("irrigated", "first"), pct_irrigated=("%_IRRIGATED", "first"),
        **{c: (c, "sum") for c in VOLUMES}).reset_index()
    fy.to_parquet(out / "field_year.parquet", index=False)

    gs = df[df["MONTH"].between(4, 10)].groupby("NWI_ID")["ETO_MM"].mean().rename("gs_eto_mm")
    static = (df.drop_duplicates("NWI_ID")
                .set_index("NWI_ID")[["STATE", "HYD_AREA", "HA_NAME_CODE", "IRR_SRC", "IRR_SRC_ASSUMED",
                                      "CROP_major", "ACRES_FTR_GEOM", "gw_frac"]]
                .join(gs).reset_index())
    n_irr = fy.groupby("NWI_ID")["irrigated"].sum().rename("n_irrigated_years")
    static = static.merge(n_irr, on="NWI_ID", how="left")
    static.to_parquet(out / "field_static.parquet", index=False)

    checks.update({"field_file": str(args.field_file), "n_field_months": len(df),
                   "n_fields": int(df["NWI_ID"].nunique()),
                   "n_fields_with_f_gt_0": int((static["gw_frac"] > 0).sum()),
                   "n_fields_ever_irrigated": int((static["n_irrigated_years"] > 0).sum())})
    (out / "01_field_results_checks.json").write_text(json.dumps(checks, indent=2, default=float))
    print(f"Wrote field_month_core ({len(core):,}), field_year ({len(fy):,}), "
          f"field_static ({len(static):,}) to {out}")


if __name__ == "__main__":
    main()
