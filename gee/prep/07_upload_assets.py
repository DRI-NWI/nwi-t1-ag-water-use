#!/usr/bin/env python3
"""
GEE app input 7: upload the app's tables and rasters to Earth Engine.

Copies the files from steps 3 and 6 to Cloud Storage (gs://<bucket>/nwi_gw_subsidy/<run_id>/) and
ingests them as Earth Engine assets under projects/<project>/assets/nwi_gw_subsidy/. Every CSV
column gets an explicit type, so codes such as HYD_AREA "014" stay strings. Each asset records where
it came from in its properties. The assets are then made readable by anyone, so the app runs from any
Earth Engine account (--no-public skips this). The folder stays private, so other assets put in it are
not public.

Usage:
    python 07_upload_assets.py [--project nwi-usgs] [--bucket nwi-usgs-gee-exports] [--overwrite] [--no-public]
                               [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

import pandas as pd

from _common import CFG, REPO, out_dir

RUN_ID = CFG["run"]["run_id"]
SUFFIX = RUN_ID.rsplit("_", 1)[-1]                   # 20261018
STRING_COLS = {"NWI_ID", "STATE", "HYD_AREA", "HA_NAME", "SRC", "CROP_LBL", "TEXTURE", "TEXTURE_MA",
               "WT_NEAR_ID", "WT_NEAR_SRC", "canonical_id", "source", "source_well_id", "confine",
               "well_class", "role", "h_accuracy_class", "confinement_class", "well_use"}
LONG_COLS = {"YEAR", "IRR", "IRR_SRC", "SRC_ASSUMED", "CROP", "N_IRR_YRS", "MA_COVER", "MA_NV", "ROOT_CM", "WT_N1KM",
             "WT_N5KM", "N_FIELDS", "N_IRR", "N_SUB_NEW", "N_SUB_MA", "N_SUB_RUN", "N_COVER", "N_FAR5", "obs_count",
             "has_time_series", "is_train", "por_start_year", "por_end_year", "row_id"}


def credentials(project):
    """User credentials from the Earth Engine login (Earth Engine and Cloud Storage scopes)."""
    from google.oauth2.credentials import Credentials
    import google.auth.transport.requests as gr
    c = json.loads(Path(os.path.expanduser("~/.config/earthengine/credentials")).read_text())
    creds = Credentials(None, refresh_token=c["refresh_token"], client_id=c["client_id"],
                        client_secret=c["client_secret"], token_uri="https://oauth2.googleapis.com/token",
                        quota_project_id=project)
    creds.refresh(gr.Request())
    return creds


def git_commit():
    """Short commit of the repository: "-dirty" when the working tree has uncommitted changes,
    "-unchecked" when git cannot be run to tell."""
    commit = _head_commit()
    try:
        dirty = subprocess.check_output(["git", "-C", str(REPO), "status", "--porcelain"], text=True,
                                        stderr=subprocess.DEVNULL).strip()
    except Exception:
        return commit + "-unchecked"
    return commit + ("-dirty" if dirty else "")


def _head_commit():
    """Short commit of HEAD, read from .git (the git binary is not always callable)."""
    try:
        git = REPO / ".git"
        head = (git / "HEAD").read_text().strip()
        if not head.startswith("ref:"):
            return head[:7]
        ref = head.split(" ", 1)[1]
        if (git / ref).exists():
            return (git / ref).read_text().strip()[:7]
        for line in (git / "packed-refs").read_text().splitlines():
            if line.endswith(" " + ref):
                return line[:7]
    except Exception:
        pass
    try:
        return subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def column_types(csv_path: Path):
    """columnDataTypeOverrides for every column of a CSV (geometry column left to ingestion)."""
    cols = pd.read_csv(csv_path, nrows=0).columns
    types = {}
    for c in cols:
        if c == "geometry":
            continue
        if c in STRING_COLS:
            types[c] = "COLUMN_DATA_TYPE_STRING"
        elif c in LONG_COLS:
            types[c] = "COLUMN_DATA_TYPE_LONG"
        else:
            types[c] = "COLUMN_DATA_TYPE_NUMERIC"
    return types


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default="nwi-usgs")
    ap.add_argument("--bucket", default="nwi-usgs-gee-exports")
    ap.add_argument("--overwrite", action="store_true", help="replace assets that already exist")
    ap.add_argument("--only", nargs="*", default=None, help="asset names to (re)ingest; default all")
    ap.add_argument("--public", action=argparse.BooleanOptionalAction, default=True,
                    help="make the assets readable by anyone (default)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    out = out_dir(args.out)
    app, rdir = out / "app", out / "rasters"
    summary = json.loads((app / "app_summary.json").read_text())

    import ee
    from google.cloud import storage
    creds = credentials(args.project)
    ee.Initialize(project=args.project, credentials=creds)
    gcs = storage.Client(project=args.project, credentials=creds).bucket(args.bucket)
    root = f"projects/{args.project}/assets/nwi_gw_subsidy"
    prefix = f"nwi_gw_subsidy/{RUN_ID}"
    try:
        ee.data.getAsset(root)
    except ee.EEException:
        ee.data.createAsset({"type": "FOLDER"}, root)
        print(f"[ee] created folder {root}")

    common = {"run_id": RUN_ID, "repo_commit": git_commit(), "created_by": "gee/prep/07_upload_assets.py",
              "field_boundaries": "nevada_agricultural_field_boundaries_20260820.gpkg (MD5 ec2e7038a56c4e225ace0e0b83999d08, "
                                  "data last changed 2026-09-21; the layer of "
                                  "projects/ee-prevelle/assets/NWI/nevada_agricultural_field_boundaries_20260820)",
              "dtw_new": "handily NV parity DTW, 30 m, gs://ucrc-wtd/handily/nv/handily_dtw_nv_parity_30m.tif (2026-10-21)"}
    tables = {
        f"fields_gw_subsidy_{SUFFIX}": ("fields_gw_subsidy.csv", True,
            "One row per field: water-right source, crop, soil, Tan et al. parameters, field DTW (new map, run's map, Ma et al.), "
            "f and GW-ET subsidy (acre-ft/yr, 1985-2025 mean over irrigated field-years) for 4 models with the new map "
            "(handily 30 m, 2026-10-21) and with Ma et al. on its grid (wtd_nevada.tif in Nevada, the other state files "
            "elsewhere; MA_NV = 1 where wtd_nevada.tif alone covers the field, the report's comparison set), distance to "
            "the DTW training wells. "
            "F_RUN_TAN/S_RUN_TAN are the run's values (the report, 2026-10-08 map); *_R08_TAN is Tan recomputed with that map."),
        f"field_year_gw_subsidy_{SUFFIX}": ("field_year_gw_subsidy.csv", False,
            "One row per field-year: IRR (IrrMapper > 50%), ETa and GW-ET subsidy (acre-ft) for 4 models x 2 DTW maps, "
            "the run's Tan (S_RUN_TAN) and Tan recomputed with the run's map (S_R08_TAN)."),
        f"ha_gw_subsidy_{SUFFIX}": ("ha_gw_subsidy.csv", True,
            "One row per hydrographic area (NWI_Investigations polygons): totals over irrigated field-years; "
            "S_RUN_TAN = the run (the report's new-DTW value); S_MA_TAN_RPT = the report's Ma value "
            "(docs/tables/ha_gw_subsidy_new_vs_ma.csv)."),
        f"ha_year_gw_subsidy_{SUFFIX}": ("ha_year_gw_subsidy.csv", False,
            "One row per HA-year: ETa and GW-ET subsidy (acre-ft) over irrigated field-years."),
        "wells_handily_train_nv": ("wells_train.csv", True,
            "Wells the handily NV parity DTW was trained on (gs://ucrc-wtd/handily/nv/handily_wells_train_nv_parity.csv, "
            "2026-10-21), with the new 30 m map, the run's 100 m map, Ma et al. and sigma sampled at each well."),
        "wells_gwx_nv": ("wells_gwx_nv.csv", True,
            "gwx Nevada wells with a depth to water (gwx_nevada_wells.zip, product 2026-10-08, MD5 "
            "e06c9fe9ea211b438bf2722c523ea459); is_train = at a training-well location; dtw_new_m and dtw_run_m = the new "
            "30 m map and the run's 100 m map at the well."),
    }
    image = ("dtw_epsg5070", {
        "dtw_new_m": rdir / "dtw_new_30m_epsg5070.tif",
        "dtw_ma_m": rdir / "dtw_ma_30m_epsg5070.tif",
        "dtw_sigma_m": rdir / "dtw_sigma_100m_epsg5070.tif",
        "dtw_run_m": rdir / "dtw_run_100m_epsg5070.tif"})

    todo = set(args.only) if args.only else set(tables) | {image[0]}
    tasks = {}
    for name, (fname, has_geom, desc) in tables.items():
        if name not in todo:
            continue
        asset = f"{root}/{name}"
        if _exists(ee, asset):
            if not args.overwrite:
                print(f"[ee] {asset} exists; skipped (use --overwrite)")
                continue
            ee.data.deleteAsset(asset)
        blob = gcs.blob(f"{prefix}/{fname}")
        blob.upload_from_filename(str(app / fname), timeout=1800)
        src = {"uris": [f"gs://{args.bucket}/{prefix}/{fname}"], "charset": "UTF-8"}
        if has_geom:
            src.update({"primaryGeometryColumn": "geometry", "crs": "EPSG:4326", "geodesic": False,
                        "maxErrorMeters": 0.1})
        manifest = {"name": asset, "sources": [src], "columnDataTypeOverrides": column_types(app / fname),
                    "properties": dict(common, description=desc, source_file=fname)}
        op = ee.data.startTableIngestion(ee.data.newTaskId()[0], manifest)
        tasks[name] = op["name"]          # poll the operation, not the request ID
        print(f"[ee] table ingestion started: {name} ({op['name']})", flush=True)

    if image[0] in todo:
        asset = f"{root}/{image[0]}"
        if _exists(ee, asset) and args.overwrite:
            ee.data.deleteAsset(asset)
        if not _exists(ee, asset):
            tilesets, bands = [], []
            for band, path in image[1].items():
                gcs.blob(f"{prefix}/{path.name}").upload_from_filename(str(path), timeout=3600)
                tilesets.append({"id": band, "crs": "EPSG:5070",
                                 "sources": [{"uris": [f"gs://{args.bucket}/{prefix}/{path.name}"]}]})
                bands.append({"id": band, "tilesetId": band, "tilesetBandIndex": 0, "pyramidingPolicy": "MEAN",
                              "missingData": {"values": [-9999]}})
            manifest = {"name": asset, "tilesets": tilesets, "bands": bands, "properties": dict(
                common,
                dtw_new_m="new map: handily NV parity DTW, 30 m, gs://ucrc-wtd/handily/nv/handily_dtw_nv_parity_30m.tif "
                          "(2026-10-21, MD5 974338d10a021dcca7e52c70e2c44fba), rewritten with DEFLATE, pixels unchanged",
                dtw_ma_m="Ma et al. (2026) averaged (area-weighted) onto the 30 m grid of dtw_new_m: wtd_nevada.tif in "
                         "Nevada, the other state images of projects/sat-io/open-datasets/HRES-WTD elsewhere",
                dtw_sigma_m="sigma of the run's map: nv_sigma_parity_statewide_100m.tif (2026-10-08 build; the bucket has "
                            "no sigma layer), 100 m",
                dtw_run_m="the run's map: nv_dtw_parity_statewide_100m.tif (2026-10-08 build, zip MD5 "
                          "20af7ee2027ac234774f5a026ee9b4d8), tagged EPSG:5070, 100 m")}
            op = ee.data.startIngestion(ee.data.newTaskId()[0], manifest)
            tasks[image[0]] = op["name"]
            print(f"[ee] image ingestion started: {image[0]} ({op['name']})", flush=True)

    # wait for the ingestion operations (at most two hours)
    pending, t0 = dict(tasks), time.time()
    while pending and time.time() - t0 < 7200:
        time.sleep(20)
        for name, op_name in list(pending.items()):
            op = ee.data.getOperation(op_name)
            state = op.get("metadata", {}).get("state")
            if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
                print(f"[ee] {name}: {state} {op.get('error', {}).get('message', '')}", flush=True)
                pending.pop(name)
    print("[ee] done" if not pending else f"[ee] still running after 2 h: {sorted(pending)}")

    # A re-ingested asset starts private, so this runs on every asset of this call, new or existing.
    if args.public:
        for name in sorted(todo):
            asset = f"{root}/{name}"
            if not _exists(ee, asset):
                print(f"[ee] {name}: not on Earth Engine yet; rerun this step to make it public")
                continue
            print(f"[ee] {name}: {'made public' if make_public(ee, asset) else 'already public'}")


def make_public(ee, asset):
    """Add allUsers to the asset's readers, keeping its other bindings. False if it was already public."""
    policy = ee.data.getIamPolicy(asset)
    bindings = [dict(b) for b in policy.get("bindings", [])]
    viewer = next((b for b in bindings if b["role"] == "roles/viewer"), None)
    if viewer is None:
        bindings.append({"role": "roles/viewer", "members": ["allUsers"]})
    elif "allUsers" in viewer["members"]:
        return False
    else:
        viewer["members"] = list(viewer["members"]) + ["allUsers"]
    new = {"bindings": bindings}
    if "etag" in policy:
        new["etag"] = policy["etag"]      # refused if the policy changed since it was read
    ee.data.setIamPolicy(asset, new)
    return True


def _exists(ee, asset):
    try:
        ee.data.getAsset(asset)
        return True
    except ee.EEException:
        return False


if __name__ == "__main__":
    main()
