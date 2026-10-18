#!/usr/bin/env python
"""Single entry point for the Nevada groundwater-ET subsidy pipeline.

NWI T1: the stages used here are wtd -> soil -> params, writing the per-field groundwater-ET
fraction (field_params.parquet, column gw_frac) that process_field_summaries.py subtracts
from net ET. The well validation, well-anchored correction, basin aggregation and figure
stages of nwi-gw-subsidy are not part of this workflow and their modules are not copied.
Run from this folder:  python main.py all

Each stage caches its artifact and is skipped if present (use --force to recompute).

Examples:
    python main.py fast-track            # shallow basins, POR-mean ET, 4 slide figures
    python main.py all                   # full pipeline (all ET-covered fields)
    python main.py wtd                   # run a single stage
    python main.py all --force           # ignore caches and recompute
    python main.py soil --basins 101,105 # restrict the soil stage to specific basins

Stages: wtd -> soil -> validate -> subsidy -> basins -> figures
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # NWI T1: package is scripts/gw_subsidy

from gw_subsidy import config, polaris_gee, subsidy, wtd_zonal  # noqa: E402


def _shallow_candidate_ids():
    """Field IDs that need soil texture (have at least one shallow WTD pixel)."""
    import pandas as pd
    px = pd.read_parquet(config.FIELD_WTD_PIXELS_PARQUET)
    return sorted(px["OPENET_ID"].unique())


def stage_wtd(args):
    wtd_zonal.run(force=args.force)


def stage_soil(args):
    ids = None
    if args.basins:
        import geopandas as gpd
        want = {b.strip().zfill(3) for b in args.basins.split(",")}
        g = gpd.read_file(config.FIELDS_GPKG, columns=["NWI_ID", "Basin"]).rename(columns={"NWI_ID": "OPENET_ID"})  # NWI T1
        from gw_subsidy.basins import basin_id_from_field
        ids = g[g["Basin"].map(basin_id_from_field).isin(want)]["OPENET_ID"].dropna().tolist()
    elif config.FIELD_WTD_PIXELS_PARQUET.exists():
        ids = _shallow_candidate_ids()
    polaris_gee.run(field_ids=ids, force=args.force)


def stage_params(args):
    """Per-field Tan (2025) parameters and groundwater fraction (NWI T1: replaces stage_subsidy)."""
    subsidy.run(force=args.force)


def run_pipeline(args):
    """all: run every stage in order."""
    print("=== 1/3 WTD zonal ===");      stage_wtd(args)
    print("=== 2/3 POLARIS soil ===");   stage_soil(args)
    print("=== 3/3 Field parameters and groundwater fraction ==="); stage_params(args)
    print(f"\nDone. Outputs in {config.OUTPUT_DIR}")


STAGES = {"wtd": stage_wtd, "soil": stage_soil, "params": stage_params, "all": run_pipeline}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=list(STAGES), help="pipeline stage or 'all'")
    ap.add_argument("--basins", default=None, help="comma-separated BasinIDs to restrict (e.g. 101,105,108)")
    ap.add_argument("--force", action="store_true", help="ignore caches and recompute")
    args = ap.parse_args()
    STAGES[args.stage](args)


if __name__ == "__main__":
    main()
