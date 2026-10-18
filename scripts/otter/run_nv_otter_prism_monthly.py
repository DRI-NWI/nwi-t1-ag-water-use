#!/usr/bin/env python3
"""MONTHLY variant (2026-09-30) of run_nv_otter_matched_continuous.py: same inputs and kernel, NM05 product only,
monthly Peff for 2014-2023 from the continuous 1985 start.  Monthly sums must reproduce the annual NM05 product.

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
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd


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
    fields = gpd.read_file(path, columns=[FIELD, "STATE", "GRIDMET_ID"])
    fields = fields.loc[fields["STATE"].astype(str).str.upper().eq("NV")].copy()
    fields[FIELD] = fields[FIELD].astype(str)
    fields["GRIDMET_ID"] = pd.to_numeric(fields["GRIDMET_ID"], errors="coerce").astype("Int64")
    if fields[FIELD].duplicated().any():
        raise ValueError("Field boundary file contains duplicate NWI_ID values")
    if fields["GRIDMET_ID"].isna().any():
        raise ValueError("Field boundary file contains Nevada fields without GRIDMET_ID")
    return fields[[FIELD, "GRIDMET_ID"]].sort_values(FIELD).reset_index(drop=True)


def load_static(path: Path, field_order: list[str], start: int, end: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, pd.DataFrame]:
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


def load_etof(path: Path, field_order: list[str], start: int, end: int) -> np.ndarray:
    etof = pd.read_parquet(path, columns=[FIELD, "DATE", "etof"])
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


def load_eto_ratios(path: Path) -> pd.DataFrame:
    """Monthly ETo ratios by GridMET ID (ET-Demands et_ratios format)."""
    ratios = pd.read_csv(path, sep="\t")
    ratios = ratios.drop(columns=[c for c in ratios.columns if c.strip().lower() == "met node name"])
    ratios = ratios.set_index("Met Node ID")
    months = [pd.Timestamp(2000, m, 1).strftime("%b") for m in range(1, 13)]
    return ratios[months].astype(float)


def stage_climate(climate_dir: Path, fields: pd.DataFrame, valid_mask: np.ndarray, dates: pd.DatetimeIndex, out_dir: Path, eto_ratios: pd.DataFrame | None = None) -> tuple[Path, Path]:
    shape = (len(dates), int(valid_mask.sum()))
    pr_path, eto_path = out_dir / "gridmet_prcp_mm.dat", out_dir / "gridmet_eto_mm.dat"
    pr = np.memmap(pr_path, dtype="float32", mode="w+", shape=shape)
    eto = np.memmap(eto_path, dtype="float32", mode="w+", shape=shape)
    pr[:] = np.nan
    eto[:] = np.nan
    valid_fields = fields.loc[valid_mask].reset_index(drop=True)
    grid_to_cols: dict[int, np.ndarray] = {}
    for grid, group in valid_fields.groupby("GRIDMET_ID", sort=False):
        grid_to_cols[int(grid)] = group.index.to_numpy()
    expected_dates = pd.DatetimeIndex(dates)
    for i, (grid, columns) in enumerate(sorted(grid_to_cols.items()), start=1):
        source_grid = GRIDMET_REPLACEMENTS.get(grid, grid)
        path = climate_dir / f"gridmet_historical_{source_grid}.csv"
        if not path.exists():
            raise FileNotFoundError(path)
        table = pd.read_csv(path, usecols=["date", "prcp_mm", "eto_mm"])
        table["date"] = pd.to_datetime(table["date"])
        table = table.set_index("date").reindex(expected_dates)
        if table[["prcp_mm", "eto_mm"]].isna().any().any():
            raise ValueError(f"Missing climate data for GridMET {grid}")
        pr[:, columns] = table["prcp_mm"].to_numpy(dtype="float32")[:, None]
        eto_values = table["eto_mm"].to_numpy(dtype="float64")
        if eto_ratios is not None:
            # OpenET EToF is defined against bias-corrected GridMET ETo, so the
            # reference must carry the same monthly ratios ET-Demands applies.
            if source_grid not in eto_ratios.index:
                raise KeyError(f"No ETo ratios for GridMET {source_grid}")
            eto_values = eto_values * eto_ratios.loc[source_grid].to_numpy()[expected_dates.month - 1]
        eto[:, columns] = eto_values.astype("float32")[:, None]
        if i % 200 == 0 or i == len(grid_to_cols):
            print(f"Staged exact ET-Demands GridMET cells: {i:,}/{len(grid_to_cols):,}", flush=True)
    pr.flush()
    eto.flush()
    del pr, eto
    return pr_path, eto_path


def init_worker(pr_path: str, eto_path: str, shape: tuple[int, int], etof: np.ndarray, aws_u: np.ndarray, aws_max: np.ndarray, cn: np.ndarray, dates: np.ndarray, years: np.ndarray, start: int, init_dru: float, init_drl: float, mad: float) -> None:
    import sys

    otter_src = "/Users/Peter.ReVelle@dri.edu/github/otter/src"
    if otter_src not in sys.path:
        sys.path.insert(0, otter_src)
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
    )


def summarize(values: np.ndarray, year_index: np.ndarray, keys: range) -> dict[int, float]:
    return {int(year): float(np.nansum(values[year_index == (year - min(keys))])) for year in keys}


def run_chunk(indices: list[int]) -> pd.DataFrame:
    """NM05 product only; monthly sums for years >= MONTHLY_FROM (continuous state from the start year)."""
    from otter.water_balance import do_wb_interp

    pr = _WORKER["pr"]; eto = _WORKER["eto"]; etof = _WORKER["etof"]
    aws_u = _WORKER["aws_u"]; aws_max = _WORKER["aws_max"]; cn = _WORKER["cn"]
    dates = pd.DatetimeIndex(_WORKER["dates"])
    start = int(_WORKER["start"]); nm_init = _WORKER["nm"]
    day_year_index = dates.year.to_numpy() - start
    ym = dates.year.to_numpy() * 100 + dates.month.to_numpy()
    keep = dates.year.to_numpy() >= MONTHLY_FROM
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


MONTHLY_FROM = 2014


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fields", type=Path, required=True)
    parser.add_argument("--climate-dir", type=Path, required=True)
    parser.add_argument("--etof", type=Path, required=True)
    parser.add_argument("--static", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start-year", type=int, default=1985)
    parser.add_argument("--end-year", type=int, default=2023)
    parser.add_argument("--workers", type=int, default=max(1, int((os.cpu_count() or 2) * 0.8)))
    parser.add_argument("--chunk-fields", type=int, default=250)
    parser.add_argument("--init-dru-frac", type=float, default=0.5)
    parser.add_argument("--init-drl-frac", type=float, default=0.5)
    parser.add_argument("--mad-frac", type=float, default=0.5)
    parser.add_argument("--eto-ratios", type=Path, default=None,
                        help="Optional ET-Demands monthly ETo ratio file (e.g. OpenET_v1_eto_ratios_NV_v1.txt) applied to eto_mm")
    args = parser.parse_args()
    if not (0 < args.init_dru_frac <= args.mad_frac <= 1 and 0 < args.init_drl_frac <= 1):
        raise ValueError("Require 0 < init_dru <= mad <= 1 and 0 < init_drl <= 1")

    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    years = list(range(args.start_year, args.end_year + 1))
    dates = pd.date_range(f"{args.start_year}-01-01", f"{args.end_year}-12-31", freq="D")
    fields = load_fields(args.fields)
    field_order = fields[FIELD].tolist()
    aws_u_all, aws_max_all, cn_all, bad_static, static_audit = load_static(args.static, field_order, args.start_year, args.end_year)
    static_audit.to_csv(out / "static_field_audit.csv", index=False)
    valid_mask = ~bad_static
    valid_fields = fields.loc[valid_mask].reset_index(drop=True)
    valid_order = valid_fields[FIELD].tolist()
    etof_all = load_etof(args.etof, field_order, args.start_year, args.end_year)
    valid_indices = np.flatnonzero(valid_mask)
    etof_valid = etof_all[valid_indices]
    eto_ratios = load_eto_ratios(args.eto_ratios) if args.eto_ratios else None
    pr_path, eto_path = stage_climate(args.climate_dir, fields, valid_mask, dates, out, eto_ratios)

    # Restrict static arrays to the valid-field order used by the memmaps.
    aws_u = aws_u_all[valid_indices]
    aws_max = aws_max_all[valid_indices]
    cn = cn_all[valid_indices]
    n_fields = len(valid_order)
    chunks = [list(range(i, min(i + args.chunk_fields, n_fields))) for i in range(0, n_fields, args.chunk_fields)]
    shape = (len(dates), n_fields)
    print(f"Running continuous OTTER history for {n_fields:,} valid fields with {args.workers} workers", flush=True)
    print(f"Parameters: init_dru_frac={args.init_dru_frac}, init_drl_frac={args.init_drl_frac}, mad_frac={args.mad_frac}", flush=True)
    results: list[pd.DataFrame] = []
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=args.workers,
        mp_context=context,
        initializer=init_worker,
        initargs=(str(pr_path), str(eto_path), shape, etof_valid, aws_u, aws_max, cn, dates.to_numpy(), np.array(years), args.start_year, args.init_dru_frac, args.init_drl_frac, args.mad_frac),
    ) as executor:
        futures = {executor.submit(run_chunk, chunk): chunk for chunk in chunks}
        complete = 0
        for future in as_completed(futures):
            results.append(future.result())
            complete += len(futures[future])
            print(f"Completed continuous OTTER fields: {complete:,}/{n_fields:,}", flush=True)
    monthly = pd.concat(results, ignore_index=True)
    monthly[FIELD] = monthly["_FIELD_INDEX"].map(dict(enumerate(valid_order))).astype(str)
    monthly = monthly.drop(columns=["_FIELD_INDEX"]).sort_values([FIELD, "YEAR", "MONTH"])
    monthly.to_parquet(out / f"nv_otter_monthly_{MONTHLY_FROM}_{args.end_year}_gridmet_continuous_nm05.parquet", index=False)
    print(f"wrote {len(monthly):,} monthly rows")
    return
    summary = {
        "fields_source": str(args.fields),
        "climate_source": str(args.climate_dir),
        "precipitation_source": "ET-Demands climate_data GridMET prcp_mm after the ET-Demands GRIDMET_ID replacement map",
        "precipitation_units": "mm/day",
        "reference_eto_source": ("ET-Demands climate_data GridMET eto_mm x monthly ratios from " + str(args.eto_ratios)) if args.eto_ratios else "ET-Demands climate_data GridMET eto_mm (raw, no bias correction)",
        "etof_source": str(args.etof),
        "static_source": str(args.static),
        "start_year": args.start_year,
        "end_year": args.end_year,
        "state_history": "continuous per field across all calendar years; irrigation filtering is post-simulation",
        "etof_gap_policy": "NM-style full-history forward fill; leading missing values remain OTTER nodata",
        "nm_parameters": {"init_dru_frac": args.init_dru_frac, "init_drl_frac": args.init_drl_frac, "mad_frac": args.mad_frac},
        "canonical_sensitivity_parameters": {"init_dru_frac": 1.0, "init_drl_frac": 1.0, "mad_frac": 1.0},
        "source_fields": int(len(fields)),
        "valid_static_fields": int(n_fields),
        "skipped_static_fields": int(bad_static.sum()),
        "annual_rows": int(len(annual)),
        "water_year_rows": int(len(water)),
        "outputs": {
            "nm05_annual": str(out / "nv_otter_annual_gridmet_continuous_nm05.parquet"),
            "canonical_annual": str(out / "nv_otter_annual_gridmet_continuous.parquet"),
            "nm05_water_year": str(out / "nv_otter_water_year_gridmet_continuous_nm05.parquet"),
            "canonical_water_year": str(out / "nv_otter_water_year_gridmet_continuous.parquet"),
        },
    }
    (out / "continuous_run_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
