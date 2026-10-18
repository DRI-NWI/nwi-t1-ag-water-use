#!/usr/bin/env python3
"""
Compare the outputs of two runs file by file.

Used to confirm that a code change leaves the results unchanged: every column shared by
the two runs must match (numbers within a relative tolerance, text exactly) after sorting
both by their keys.

Usage:
    python compare_runs.py <reference_run_dir> <new_run_dir> [--rtol 1e-9]

Output: a table of files and columns, with the largest difference, printed and written to
<new_run_dir>/07_qa/compare_to_<reference run id>.csv. Exit status 1 if anything differs.
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# File (relative to the run directory) -> key columns used to align rows.
FILES = {
    "05_field_summaries/field_summaries_ALL.parquet": ["NWI_ID", "YEAR", "MONTH"],
    "05_field_summaries/field_summaries_GW.parquet": ["NWI_ID", "YEAR", "MONTH"],
    "05_field_summaries/field_summaries_SW.parquet": ["NWI_ID", "YEAR", "MONTH"],
    "06_summaries/ha_summaries_ALL.csv": ["HA_NAME_CODE", "YEAR", "MONTH"],
    "06_summaries/ha_summaries_GW.csv": ["HA_NAME_CODE", "YEAR", "MONTH"],
    "06_summaries/ha_summaries_SW.csv": ["HA_NAME_CODE", "YEAR", "MONTH"],
    "06_summaries/county_summaries_ALL.csv": ["COUNTY_NAME", "YEAR", "MONTH"],
    "06_summaries/county_summaries_GW.csv": ["COUNTY_NAME", "YEAR", "MONTH"],
    "06_summaries/county_summaries_SW.csv": ["COUNTY_NAME", "YEAR", "MONTH"],
    "06_summaries/statewide_annual_totals.csv": ["component", "population", "YEAR"],
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
    args = parser.parse_args()
    reference_run, new_run = Path(args.reference_run), Path(args.new_run)

    rows = []
    for relative_path, keys in FILES.items():
        ref_path, new_path = reference_run / relative_path, new_run / relative_path
        if not (ref_path.exists() and new_path.exists()):
            rows.append({"file": relative_path, "column": "<file>", "ok": False, "detail": "missing"})
            continue
        for record in compare_file(read(ref_path), read(new_path), keys, args.rtol):
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
