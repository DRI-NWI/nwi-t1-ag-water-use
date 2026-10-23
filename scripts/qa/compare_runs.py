#!/usr/bin/env python3
"""
Compare the outputs of two runs file by file.

Used to confirm that a code change leaves the results unchanged: every column shared by
the two runs must match (numbers within a relative tolerance, text exactly) after sorting
both by their keys.

Usage:
    python compare_runs.py <reference_run_dir> <new_run_dir> [--rtol 1e-9] [--reference-2026-10-16-names]

--reference-2026-10-16-names reads the reference with the file and column names used by run
nwi_t1_final_20261016 (before the original names were restored), mapped to the current ones.

Output: a table of files and columns, with the largest difference, printed and written to
<new_run_dir>/07_qa/compare_to_<reference run id>.csv. Exit status 1 if anything differs.
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# File (relative to the run directory) -> key columns used to align rows.
FIELD_KEYS = ["NWI_ID", "YEAR", "MONTH"]
HA_KEYS = ["HA_NAME_CODE", "YEAR", "MONTH"]
COUNTY_KEYS = ["COUNTY_NAME", "YEAR", "MONTH"]
FILES = {"06_summaries/statewide_annual_totals.csv": ["component", "population", "YEAR"]}
for suffix in ["", "_GW", "_SW"]:
    FILES[f"05_field_summaries/field_summaries_EToF_final{suffix}.parquet"] = FIELD_KEYS
    FILES[f"06_summaries/ha_summaries_EToF_final{suffix}.csv"] = HA_KEYS
    FILES[f"06_summaries/county_wide_summaries_EToF_final{suffix}.csv"] = COUNTY_KEYS

# Run nwi_t1_final_20261016 used other names: current file -> its file, and its columns -> current.
REFERENCE_2026_10_16_FILES = {"06_summaries/statewide_annual_totals.csv": "06_summaries/statewide_annual_totals.csv"}
for suffix, component in [("", "ALL"), ("_GW", "GW"), ("_SW", "SW")]:
    REFERENCE_2026_10_16_FILES[f"05_field_summaries/field_summaries_EToF_final{suffix}.parquet"] = \
        f"05_field_summaries/field_summaries_{component}.parquet"
    REFERENCE_2026_10_16_FILES[f"06_summaries/ha_summaries_EToF_final{suffix}.csv"] = \
        f"06_summaries/ha_summaries_{component}.csv"
    REFERENCE_2026_10_16_FILES[f"06_summaries/county_wide_summaries_EToF_final{suffix}.csv"] = \
        f"06_summaries/county_summaries_{component}.csv"
REFERENCE_2026_10_16_COLUMNS = {
    "Peff_vol_acre_ft": "Eff_PPT_vol_acre_ft", "NetET_intermediate_acre_ft": "NetET_vol_intermediate_acre_ft",
    "Soil_Storage_acre_ft": "Water_Surplus_Cumulative_acre_ft", "PEFF_MM": "EFF_PPT_MM",
    "n_fields": "field_count", "n_gw_fields": "is_gw_field", "n_sw_fields": "is_sw_field",
    "n_mixed_fields": "is_mixed_field", "pct_fields_gw_equivalent": "percent_gw_fields",
    "pct_fields_sw_equivalent": "percent_sw_fields",
}


def read(path):
    """Read a CSV or parquet output with string HA codes."""
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path, dtype={"HYD_AREA": str}, low_memory=False)


def compare_file(reference, new, keys, rtol):
    """
    Compare two tables column by column.

    Parameters
    ----------
    reference, new : pd.DataFrame
        The two versions of one output file.
    keys : list of str
        Columns that identify a row.
    rtol : float
        Relative tolerance for numeric columns (absolute floor 1e-9).

    Returns
    -------
    list of dict
        One record per shared column: column, max_abs_diff (numeric) or n_text_diff, ok.
    """
    reference = reference.sort_values(keys).reset_index(drop=True)
    new = new.sort_values(keys).reset_index(drop=True)
    if len(reference) != len(new) or not reference[keys].astype(str).equals(new[keys].astype(str)):
        return [{"column": "<rows>", "ok": False, "detail": f"rows differ: {len(reference)} vs {len(new)}"}]
    records = []
    for col in [c for c in reference.columns if c in new.columns and c not in keys]:
        a, b = reference[col], new[col]
        if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b):
            a, b = a.to_numpy(float), b.to_numpy(float)
            same_nan = np.isnan(a) == np.isnan(b)
            diff = np.abs(np.nan_to_num(a) - np.nan_to_num(b))
            ok = bool(same_nan.all() and np.all(diff <= 1e-9 + rtol * np.abs(np.nan_to_num(a))))
            records.append({"column": col, "ok": ok, "detail": f"max abs diff {diff.max():.3g}"})
        else:
            n = int((a.fillna("").astype(str) != b.fillna("").astype(str)).sum())
            records.append({"column": col, "ok": n == 0, "detail": f"{n} text differences"})
    only_ref = [c for c in reference.columns if c not in new.columns]
    only_new = [c for c in new.columns if c not in reference.columns]
    if only_ref or only_new:
        records.append({"column": "<columns>", "ok": True,
                        "detail": f"only in reference: {only_ref}; only in new: {only_new}"})
    return records


def main():
    """Compare every output file of two runs and report."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("reference_run")
    parser.add_argument("new_run")
    parser.add_argument("--rtol", type=float, default=1e-9)
    parser.add_argument("--reference-2026-10-16-names", action="store_true")
    args = parser.parse_args()
    reference_run, new_run = Path(args.reference_run), Path(args.new_run)

    rows = []
    for relative_path, keys in FILES.items():
        reference_path = (REFERENCE_2026_10_16_FILES[relative_path] if args.reference_2026_10_16_names
                          else relative_path)
        ref_path, new_path = reference_run / reference_path, new_run / relative_path
        if not (ref_path.exists() and new_path.exists()):
            rows.append({"file": relative_path, "column": "<file>", "ok": False, "detail": "missing"})
            continue
        reference = read(ref_path)
        if args.reference_2026_10_16_names:
            reference = reference.rename(columns=REFERENCE_2026_10_16_COLUMNS)
        for record in compare_file(reference, read(new_path), keys, args.rtol):
            rows.append({"file": relative_path, **record})
        print(f"compared {relative_path}", flush=True)
    report = pd.DataFrame(rows)
    out = new_run / "07_qa" / f"compare_to_{reference_run.name}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    report.to_csv(out, index=False)
    failures = report[~report["ok"]]
    print(f"\n{len(report)} checks, {len(failures)} differences. Report: {out}")
    if len(failures):
        print(failures.to_string(index=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
