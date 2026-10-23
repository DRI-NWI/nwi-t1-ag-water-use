#!/usr/bin/env python3
"""MONTHLY variant (2026-09-30) of run_nv_otter_matched_continuous.py: same inputs and kernel, NM05 product only,
monthly Peff for 2014-2023 from the continuous 1985 start.  Monthly sums must reproduce the annual NM05 product.

NWI T1 edits (marked "NWI T1" below):
  * Precipitation and ETo are each field's own daily PRISM 800 m precipitation and OpenET gridMET
    ETo (the nwi_field_export_20260820 met extraction), not the ET-Demands gridMET cell files.
    The OpenET ETo is already bias-corrected, so no ETo ratio file is applied.
  * Monthly EToF is the field processor's own gap-filled EToF (one-month linear interpolation,
    then the per-field climatology fill when etof.climatology_fill is on), built here with the
    processor's functions; remaining gaps are forward-filled as before.
  * All 29,338 fields (all states) and every year 1985-2025 are run; monthly Peff is written
    for every year (MONTHLY_FROM = start year).
  * The otter kernel is the copy in this folder (water_balance.py).
  * Settings and paths come from config/nwi_t1_final.yaml; the unreachable annual-summary code
    after the monthly write is removed.

Run a continuous Nevada OTTER history on ET-Demands' exact GridMET files.

This is the corrected comparison runner.  It deliberately uses the same
per-GRIDMET-ID ``prcp_mm`` and ``eto_mm`` files that ET-Demands reads, rather
than the earlier field-centroid extraction.  Every valid Nevada field is
simulated for the complete consecutive 1985--2023 history; year-specific
irrigation filtering belongs in the comparison stage, after the stateful
water balance has been run.

The script writes two annual products in one pass:

* the requested parameterization (the NM colleague workflow defaults of
  ``init_dru_frac=init_drl_frac=mad_frac=0.5``), and
* a canonical 1/1/1 sensitivity product.

Both products use the unchanged OTTER kernel.  Missing monthly EToF follows
the NM workflow's full-history forward-fill rule; leading missing values are
left as the OTTER nodata sentinel and are handled by the kernel.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd


# NWI T1: run configuration and the field processor's EToF functions
SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "utils"))
sys.path.insert(0, str(SCRIPTS / "phase1_data_preparation"))
from nwi_config import load_config, RunPaths  # noqa: E402

FIELD = "NWI_ID"
NODATA = -9999.0
GRIDMET_REPLACEMENTS = {
    439582: 438196,
    447900: 449286,
    497780: 497779,
    508870: 508869,
}

_WORKER: dict[str, object] = {}


def load_fields(path: Path) -> pd.DataFrame:
    """
    Field IDs to simulate, sorted.

    NWI T1: every field of the boundary layer (all states); GRIDMET_ID is not needed because
    climate comes from each field's own met extraction.

    Parameters
    ----------
    path : Path
        2026-08-20 NWI boundary layer.

    Returns
    -------
    pd.DataFrame
        One column, NWI_ID.
    """
    fields = gpd.read_file(path, columns=[FIELD], ignore_geometry=True)
    fields[FIELD] = fields[FIELD].astype(str)
    if fields[FIELD].duplicated().any():
        raise ValueError("Field boundary file contains duplicate NWI_ID values")
    return fields[[FIELD]].sort_values(FIELD).reset_index(drop=True)


def load_static(path: Path, field_order: list[str], start: int, end: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, pd.DataFrame]:
    """
    Static OTTER soil and crop parameters per field and year.

    Fields with a missing year, non-positive aws_u/aws_max/cn, cn > 100, aws_u > aws_max, or
    aws_max that changes between years are skipped.

    Parameters
    ----------
    path : Path
        Parquet or CSV with NWI_ID, year, aws_u, aws_max, cn.
    field_order : list of str
        Field IDs in simulation order.
    start, end : int
        First and last year.

    Returns
    -------
    tuple
        aws_u (fields x years), aws_max (fields), cn (fields x years), bad (fields, bool),
        audit (NWI_ID, status, reason).
    """
    cols = [FIELD, "year", "aws_u", "aws_max", "cn"]
    static = pd.read_parquet(path, columns=cols) if path.suffix.lower() == ".parquet" else pd.read_csv(path, usecols=cols)
    static[FIELD] = static[FIELD].astype(str)
    static["year"] = pd.to_numeric(static["year"], errors="coerce").astype("Int64")
    for col in cols[2:]:
        static[col] = pd.to_numeric(static[col], errors="coerce")
    years = list(range(start, end + 1))
    static = static.loc[static[FIELD].isin(field_order) & static["year"].isin(years)].copy()
    if static.duplicated([FIELD, "year"]).any():
        raise ValueError("Static table has duplicate NWI_ID/year keys")
    expected = {(fid, year) for fid in field_order for year in years}
    actual = set(zip(static[FIELD], static["year"].astype(int)))
    missing = expected - actual
    if missing:
        raise ValueError(f"Static table is missing {len(missing):,} NWI_ID/year rows")
    static["_field_index"] = pd.Categorical(static[FIELD], categories=field_order, ordered=True).codes
    static["_year_index"] = static["year"].astype(int) - start
    static = static.sort_values(["_field_index", "_year_index"])
    n_fields, n_years = len(field_order), len(years)
    aws_u = static["aws_u"].to_numpy(float).reshape(n_fields, n_years)
    aws_max_rows = static["aws_max"].to_numpy(float).reshape(n_fields, n_years)
    cn = static["cn"].to_numpy(float).reshape(n_fields, n_years)
    aws_max = aws_max_rows[:, 0].copy()
    aws_max_range_bad = np.zeros(n_fields, dtype=bool)
    finite_max = np.isfinite(aws_max_rows).all(axis=1)
    aws_max_range_bad[finite_max] = (
        np.nanmax(aws_max_rows[finite_max], axis=1) - np.nanmin(aws_max_rows[finite_max], axis=1) > 1e-5
    )
    bad = (
        ~np.isfinite(aws_u).all(axis=1)
        | ~np.isfinite(aws_max_rows).all(axis=1)
        | ~np.isfinite(cn).all(axis=1)
        | (aws_u <= 0).any(axis=1)
        | (aws_max_rows <= 0).any(axis=1)
        | (cn <= 0).any(axis=1)
        | (cn > 100).any(axis=1)
        | (aws_u > aws_max_rows).any(axis=1)
        | aws_max_range_bad
    )
    audit = pd.DataFrame({FIELD: field_order, "status": np.where(bad, "skipped", "eligible")})
    reasons = np.full(n_fields, "", dtype=object)
    reasons[bad] = "invalid_or_inconsistent_static"
    audit["reason"] = reasons
    return aws_u, aws_max, cn, bad, audit


def build_monthly_etof(cfg: dict, paths) -> pd.DataFrame:
    """
    NWI T1: monthly gap-filled EToF per field, made with the field processor's own functions.

    Same rules as process_field_summaries.py: -999 and zero ETa are missing; EToF = ETa / ETo;
    one-month gaps are filled linearly (apply_linear_interpolation_etof); longer gaps take the
    field's climatology for that window and calendar month (apply_climatology_gap_filling_etof)
    when etof.climatology_fill is on.

    Parameters
    ----------
    cfg : dict
        Run configuration.
    paths : RunPaths
        Run directory layout (climatologies are read from paths.etof_climatology).

    Returns
    -------
    pd.DataFrame
        NWI_ID, DATE, etof (gap-filled EToF; NaN where no fill was possible).
    """
    from process_field_summaries import (apply_climatology_gap_filling_etof, apply_linear_interpolation_etof,
                                         load_climatologies)
    from calculate_field_climatologies import load_field_basins

    files = sorted(Path(cfg["inputs"]["ensemble_dir"]).rglob("*.parquet"))
    columns = ["NWI_ID", "OPENET_ID", "YEAR", "MONTH", "ETA_MM", "ETO_MM"]
    df = pd.concat([pd.read_parquet(f, columns=columns) for f in files], ignore_index=True)
    df["YEAR"] = df["YEAR"].astype(int)
    df["MONTH"] = df["MONTH"].astype(int)
    for col in ["ETA_MM", "ETO_MM"]:
        df[col] = df[col].replace(-999, np.nan)
    df["ETA_MM"] = df["ETA_MM"].replace(0, np.nan)
    df = df.merge(load_field_basins(cfg["inputs"]["fields_gpkg"]), on="NWI_ID", how="left", validate="many_to_one")
    df["EToF"] = np.where(df["ETO_MM"] > 0, df["ETA_MM"] / df["ETO_MM"], np.nan)
    df["EToF_interpolated"] = df["EToF"].copy()
    df["interpolation_method"] = "original"
    df = apply_linear_interpolation_etof(df, group_cols=["HA_NAME_CODE", "OPENET_ID"])
    if cfg["etof"].get("climatology_fill", False):
        windows = [tuple(w) for w in cfg["etof"]["climatology_windows"]]
        df = apply_climatology_gap_filling_etof(df, load_climatologies(paths.etof_climatology, windows), windows,
                                                group_cols=["HA_NAME_CODE", "OPENET_ID"])
    df["DATE"] = pd.to_datetime(dict(year=df["YEAR"], month=df["MONTH"], day=1))
    return df[[FIELD, "DATE", "EToF_interpolated"]].rename(columns={"EToF_interpolated": "etof"})


def load_etof(etof: pd.DataFrame, field_order: list[str], start: int, end: int) -> np.ndarray:
    """
    Monthly EToF as a (fields x months) array, forward-filled over the continuous history.

    NWI T1: takes the table from build_monthly_etof instead of reading a parquet file.

    Parameters
    ----------
    etof : pd.DataFrame
        NWI_ID, DATE, etof.
    field_order : list of str
        Field IDs in simulation order.
    start, end : int
        First and last year.

    Returns
    -------
    np.ndarray
        float32 array, one row per field, one column per month from January of ``start``.
    """
    etof = etof.copy()
    etof[FIELD] = etof[FIELD].astype(str)
    etof["DATE"] = pd.to_datetime(etof["DATE"])
    etof["year"] = etof["DATE"].dt.year.astype(int)
    etof["month"] = etof["DATE"].dt.month.astype(int)
    etof = etof.loc[etof[FIELD].isin(field_order) & etof["year"].between(start, end)].copy()
    etof["etof"] = pd.to_numeric(etof["etof"], errors="coerce")
    etof = etof.sort_values([FIELD, "year", "month"])
    if etof.duplicated([FIELD, "year", "month"]).any():
        raise ValueError("EToF table has duplicate field/month keys")

    # Match NM's alignment behavior: fill monthly EToF forward over the full
    # continuous history, not separately inside each calendar year.
    periods = pd.MultiIndex.from_product(
        [field_order, range(start, end + 1), range(1, 13)],
        names=[FIELD, "year", "month"],
    ).to_frame(index=False)
    periods = periods.merge(etof[[FIELD, "year", "month", "etof"]], on=[FIELD, "year", "month"], how="left", validate="one_to_one")
    periods["etof"] = periods.groupby(FIELD, sort=False)["etof"].ffill()
    periods["_field_index"] = pd.Categorical(periods[FIELD], categories=field_order, ordered=True).codes
    periods["_period_index"] = (periods["year"].astype(int) - start) * 12 + periods["month"].astype(int) - 1
    periods = periods.sort_values(["_field_index", "_period_index"])
    return periods["etof"].to_numpy(dtype="float32").reshape(len(field_order), (end - start + 1) * 12)


def stage_climate(met_dir: Path, field_order: list[str], valid_mask: np.ndarray, dates: pd.DatetimeIndex, out_dir: Path) -> tuple[Path, Path]:
    """
    NWI T1: write (days x valid fields) memmaps of each field's daily PRISM precipitation and ETo.

    Replaces the original staging from ET-Demands gridMET cell files. Source files are
    <met_dir>/year=YYYY/met_YYYYMMDD.parquet with NWI_ID, date, ppt, eto.

    Parameters
    ----------
    met_dir : Path
        Daily met extraction (one file per day, every field).
    field_order : list of str
        All field IDs in simulation order.
    valid_mask : np.ndarray
        True for fields with valid static parameters (the memmap columns).
    dates : pd.DatetimeIndex
        Every simulated day.
    out_dir : Path
        Where the memmaps are written.

    Returns
    -------
    tuple of Path
        Precipitation and ETo memmap paths.
    """
    import duckdb

    valid_ids = [fid for fid, ok in zip(field_order, valid_mask) if ok]
    column_of = pd.Series(np.arange(len(valid_ids)), index=valid_ids)
    shape = (len(dates), len(valid_ids))
    pr_path, eto_path = out_dir / "prism_prcp_mm.dat", out_dir / "met_eto_mm.dat"
    pr = np.memmap(pr_path, dtype="float32", mode="w+", shape=shape)
    eto = np.memmap(eto_path, dtype="float32", mode="w+", shape=shape)
    pr[:] = np.nan
    eto[:] = np.nan
    con = duckdb.connect()
    con.execute("SET threads=12")
    for year in sorted(set(dates.year)):
        pattern = str(Path(met_dir) / f"year={year}" / "met_*.parquet")
        day = con.execute(f"""SELECT NWI_ID, CAST(date AS VARCHAR) AS date, CAST(ppt AS DOUBLE) AS ppt,
                              CAST(eto AS DOUBLE) AS eto
                              FROM read_parquet('{pattern}', hive_partitioning=false, union_by_name=true)""").df()
        columns = column_of.reindex(day["NWI_ID"].astype(str)).to_numpy()
        keep = ~np.isnan(columns)
        rows = (pd.to_datetime(day["date"].str[:10]) - dates[0]).dt.days.to_numpy()
        pr[rows[keep], columns[keep].astype(int)] = day["ppt"].to_numpy("float32")[keep]
        eto[rows[keep], columns[keep].astype(int)] = day["eto"].to_numpy("float32")[keep]
        print(f"Staged daily PRISM precipitation and ETo: {year}", flush=True)
    if np.isnan(pr).any() or np.isnan(eto).any():
        raise ValueError("Daily met staging has missing days")
    pr.flush()
    eto.flush()
    del pr, eto
    return pr_path, eto_path


def init_worker(pr_path: str, eto_path: str, shape: tuple[int, int], etof: np.ndarray, aws_u: np.ndarray, aws_max: np.ndarray, cn: np.ndarray, dates: np.ndarray, years: np.ndarray, start: int, init_dru: float, init_drl: float, mad: float, monthly_from: int) -> None:
    """Open the memmaps and share the static arrays with one worker process."""
    import sys

    otter_dir = str(Path(__file__).resolve().parent)   # NWI T1: kernel copied into this folder
    if otter_dir not in sys.path:
        sys.path.insert(0, otter_dir)
    _WORKER.clear()
    _WORKER.update(
        pr=np.memmap(pr_path, dtype="float32", mode="r", shape=shape),
        eto=np.memmap(eto_path, dtype="float32", mode="r", shape=shape),
        etof=etof,
        aws_u=aws_u,
        aws_max=aws_max,
        cn=cn,
        dates=dates,
        years=years,
        start=start,
        nm=(float(init_dru), float(init_drl), float(mad)),
        monthly_from=int(monthly_from),   # NWI T1: workers are spawned, so pass it explicitly
    )


def run_chunk(indices: list[int]) -> pd.DataFrame:
    """
    Run OTTER for a chunk of fields and sum daily effective precipitation to months.

    NM05 product only; monthly sums for years >= MONTHLY_FROM (continuous state from the start year).

    Parameters
    ----------
    indices : list of int
        Column indices of the fields (valid-field order).

    Returns
    -------
    pd.DataFrame
        _FIELD_INDEX, YEAR, MONTH, OTTER_PR_MM, OTTER_PEFF_MM.
    """
    from water_balance import do_wb_interp   # NWI T1: local copy of the otter kernel

    pr = _WORKER["pr"]; eto = _WORKER["eto"]; etof = _WORKER["etof"]
    aws_u = _WORKER["aws_u"]; aws_max = _WORKER["aws_max"]; cn = _WORKER["cn"]
    dates = pd.DatetimeIndex(_WORKER["dates"])
    start = int(_WORKER["start"]); nm_init = _WORKER["nm"]
    day_year_index = dates.year.to_numpy() - start
    ym = dates.year.to_numpy() * 100 + dates.month.to_numpy()
    keep = dates.year.to_numpy() >= _WORKER["monthly_from"]
    months = np.unique(ym[keep])
    rows = []
    for idx in indices:
        pr_col = np.asarray(pr[:, idx], dtype="float32").copy()
        eto_col = np.asarray(eto[:, idx], dtype="float32").copy()
        etof_col = etof[idx][(dates.year.to_numpy() - start) * 12 + dates.month.to_numpy() - 1]
        et_col = eto_col * etof_col
        et_col[~np.isfinite(et_col)] = NODATA
        aws_u_daily = aws_u[idx, day_year_index].astype("float32", copy=False)
        cn_daily = cn[idx, day_year_index].astype("float32", copy=False)
        result = do_wb_interp(float(aws_max[idx]), aws_u_daily, cn_daily, pr_col, et_col, eto_col,
                              init_dru_frac=nm_init[0], init_drl_frac=nm_init[1], mad_frac=nm_init[2])
        peff = np.asarray(result[6], dtype=float)
        s = pd.Series(peff[keep]).groupby(ym[keep]).sum()
        p = pd.Series(pr_col[keep].astype(float)).groupby(ym[keep]).sum()
        for k in months:
            rows.append({"_FIELD_INDEX": idx, "YEAR": int(k // 100), "MONTH": int(k % 100), "OTTER_PR_MM": float(p[k]),
                         "OTTER_PEFF_MM": float(s[k])})
    return pd.DataFrame(rows)


MONTHLY_FROM = None   # NWI T1: set to the start year in main(), so every year is written


def main() -> None:
    """Run OTTER for every field and write monthly effective precipitation (NWI T1: settings from config)."""
    global MONTHLY_FROM
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=max(1, int((os.cpu_count() or 2) * 0.8)))
    parser.add_argument("--chunk-fields", type=int, default=250)
    args = parser.parse_args()

    # NWI T1: paths, years and NM05 parameters from config/nwi_t1_final.yaml
    cfg = load_config()
    paths = RunPaths(cfg).make()
    otter_cfg = cfg["effective_precipitation"]["otter"]
    start_year, end_year = cfg["run"]["start_year"], cfg["run"]["end_year"]
    init_dru, init_drl, mad = otter_cfg["init_dru_frac"], otter_cfg["init_drl_frac"], otter_cfg["mad_frac"]
    if not (0 < init_dru <= mad <= 1 and 0 < init_drl <= 1):
        raise ValueError("Require 0 < init_dru <= mad <= 1 and 0 < init_drl <= 1")
    MONTHLY_FROM = start_year

    out = paths.otter
    staging = out / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    years = list(range(start_year, end_year + 1))
    dates = pd.date_range(f"{start_year}-01-01", f"{end_year}-12-31", freq="D")
    fields = load_fields(Path(cfg["inputs"]["fields_gpkg"]))
    field_order = fields[FIELD].tolist()
    aws_u_all, aws_max_all, cn_all, bad_static, static_audit = load_static(
        Path(cfg["inputs"]["otter_static_soils"]), field_order, start_year, end_year)
    static_audit.to_csv(paths.otter_audit, index=False)
    valid_mask = ~bad_static
    valid_order = [fid for fid, ok in zip(field_order, valid_mask) if ok]
    print("Building monthly EToF with the field processor's gap filling...", flush=True)
    etof_all = load_etof(build_monthly_etof(cfg, paths), field_order, start_year, end_year)
    valid_indices = np.flatnonzero(valid_mask)
    etof_valid = etof_all[valid_indices]
    pr_path, eto_path = stage_climate(Path(cfg["inputs"]["met_daily_dir"]), field_order, valid_mask, dates, staging)

    # Restrict static arrays to the valid-field order used by the memmaps.
    aws_u = aws_u_all[valid_indices]
    aws_max = aws_max_all[valid_indices]
    cn = cn_all[valid_indices]
    n_fields = len(valid_order)
    chunks = [list(range(i, min(i + args.chunk_fields, n_fields))) for i in range(0, n_fields, args.chunk_fields)]
    shape = (len(dates), n_fields)
    print(f"Running continuous OTTER history for {n_fields:,} valid fields with {args.workers} workers", flush=True)
    print(f"Parameters: init_dru_frac={init_dru}, init_drl_frac={init_drl}, mad_frac={mad}", flush=True)
    results: list[pd.DataFrame] = []
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=args.workers,
        mp_context=context,
        initializer=init_worker,
        initargs=(str(pr_path), str(eto_path), shape, etof_valid, aws_u, aws_max, cn, dates.to_numpy(), np.array(years), start_year, init_dru, init_drl, mad, MONTHLY_FROM),
    ) as executor:
        futures = {executor.submit(run_chunk, chunk): chunk for chunk in chunks}
        complete = 0
        for future in as_completed(futures):
            results.append(future.result())
            complete += len(futures[future])
            if complete % 5000 < args.chunk_fields or complete == n_fields:
                print(f"Completed continuous OTTER fields: {complete:,}/{n_fields:,}", flush=True)
    monthly = pd.concat(results, ignore_index=True)
    monthly[FIELD] = monthly["_FIELD_INDEX"].map(dict(enumerate(valid_order))).astype(str)
    monthly = monthly.drop(columns=["_FIELD_INDEX"]).sort_values([FIELD, "YEAR", "MONTH"])
    monthly.to_parquet(paths.otter_monthly, index=False)
    print(f"wrote {len(monthly):,} monthly rows to {paths.otter_monthly}")


if __name__ == "__main__":
    main()
