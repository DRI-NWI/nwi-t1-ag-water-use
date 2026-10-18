#!/usr/bin/env python3
"""
Reproduction check against the 2026-08-23 package, and the bridge table.

1. Reproduction check: the QA variants v0a and v0b (config/bridge_variants.yaml) run this
   code with the settings of the 2026-08-23 package. Their GW and SW HA summaries are
   compared, cell by cell (HA x year x month), with the delivered HA summaries.
2. Bridge table: statewide annual means (acre-ft/yr, 1985-2025) for each variant, from the
   2026-08-23 settings to the final run, one setting at a time.

Outputs (run/07_qa/): reproduction_check.csv, bridge_table.csv
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from nwi_config import load_config, RunPaths  # noqa: E402

CHECK_COLUMNS = ["ETa_final_acre_ft", "NetET_final_acre_ft", "AR_final_acre_ft", "ACRES_FTR_GEOM"]
KEYS = ["HA_NAME_CODE", "YEAR", "MONTH"]
REPRODUCTION_VARIANTS = ["v0a_reproduce_0823_no_static", "v0b_reproduce_0823_static153"]
BRIDGE_ORDER = ["v0a_reproduce_0823_no_static", "v1_basin_overrides", "v2_prism_spi_d24",
                "v3_prism_ppt_otter_peff", "v4a_gw_subsidy_no_climatology", "v4_final"]
BRIDGE_COLUMNS = ["ETa_final_acre_ft", "PPT_vol_acre_ft", "Peff_vol_acre_ft", "GW_subsidy_acre_ft",
                  "Carryforward_Used_acre_ft", "NetET_final_acre_ft", "AR_final_acre_ft"]


def delivered_ha_summary(package_dir, component):
    """
    Delivered HA summary for one component, summed to one row per HA, year and month.

    Parameters
    ----------
    package_dir : str
        Folder of the 2026-08-23 package.
    component : str
        'GW' or 'SW'.

    Returns
    -------
    pd.DataFrame
        KEYS plus CHECK_COLUMNS.
    """
    table = pd.read_csv(f"{package_dir}/ha_summaries_EToF_final_{component}.csv", usecols=KEYS + CHECK_COLUMNS)
    return table.groupby(KEYS, as_index=False)[CHECK_COLUMNS].sum()


def reproduction_check(cfg, paths):
    """
    Compare the reproduction variants with the delivered package, cell by cell.

    Returns
    -------
    pd.DataFrame
        One row per variant, component and column: totals, largest cell difference, and the
        number of cells that differ by more than 0.01 acre-ft or exist in only one table.
    """
    rows = []
    for variant in REPRODUCTION_VARIANTS:
        variant_dir = paths.variants / variant
        if not variant_dir.exists():
            continue
        for component in ["GW", "SW"]:
            delivered = delivered_ha_summary(cfg["inputs"]["reference_package_dir"], component)
            ours = pd.read_csv(variant_dir / f"ha_summaries_{component}.csv", usecols=KEYS + CHECK_COLUMNS)
            joined = delivered.merge(ours, on=KEYS, how="outer", suffixes=("_0823", "_new"), indicator=True)
            for col in CHECK_COLUMNS:
                old, new = joined[f"{col}_0823"].fillna(0), joined[f"{col}_new"].fillna(0)
                rows.append({
                    "variant": variant, "component": component, "column": col,
                    "total_0823": old.sum(), "total_new": new.sum(),
                    "rel_diff_total": (new.sum() - old.sum()) / old.sum(),
                    "max_abs_diff_cell": (new - old).abs().max(),
                    "cells_abs_diff_gt_0.01": int(((new - old).abs() > 0.01).sum()),
                    "cells_only_0823": int((joined["_merge"] == "left_only").sum()),
                    "cells_only_new": int((joined["_merge"] == "right_only").sum()),
                })
    return pd.DataFrame(rows)


def bridge_table(paths):
    """
    Statewide annual means for each variant, population and component.

    Returns
    -------
    pd.DataFrame
        variant, population, component and the mean of each BRIDGE_COLUMNS over the years.
    """
    tables = []
    for variant in BRIDGE_ORDER:
        folder = paths.summaries if variant == "v4_final" else paths.variants / variant
        totals_file = folder / "statewide_annual_totals.csv"
        if not totals_file.exists():
            continue
        totals = pd.read_csv(totals_file)
        n_years = totals["YEAR"].nunique()
        means = totals.groupby(["population", "component"])[BRIDGE_COLUMNS].sum() / n_years
        means.insert(0, "variant", variant)
        tables.append(means.reset_index())
    if not tables:
        return pd.DataFrame()
    return pd.concat(tables).sort_values(["population", "component"], kind="mergesort")


if __name__ == "__main__":
    cfg = load_config()
    paths = RunPaths(cfg).make()
    check = reproduction_check(cfg, paths)
    check.to_csv(paths.qa / "reproduction_check.csv", index=False)
    print(check.to_string(index=False))
    bridge = bridge_table(paths)
    bridge.to_csv(paths.qa / "bridge_table.csv", index=False)
    print(bridge.round(0).to_string(index=False))
