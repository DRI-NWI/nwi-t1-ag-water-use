#!/usr/bin/env python3
"""
GEE app input 5: wells, and each field's distance to them.

Training wells (the "well data" of the app): the wells the handily NV parity DTW model was
trained on, gs://ucrc-wtd/handily/nv/handily_wells_train_nv_parity.csv (9,448 wells: 8,628 NDWR
well-log depths and 820 monitoring wells). The file is dated 2026-10-21; the run's DTW map is the
2026-10-08 build, so the script checks that this map honours these wells (map minus well depth at
the training wells versus at other wells) before they are used.

All wells (context): the gwx Nevada well set shipped with the DTW map (gwx_nevada_wells.zip,
MD5 e06c9fe9ea211b438bf2722c523ea459), every well with a measured depth to water (mean_dtw).

Per field (km from the field polygon, 0 when a well is inside it; EPSG:5070):
  WT_DIST_KM, WT_N1KM, WT_N5KM   nearest training well, and training wells within 1 and 5 km
  WT_NEAR_ID, WT_NEAR_SRC, WT_NEAR_DTW_M   the nearest training well
  W_DIST_KM, W_TS_DIST_KM        nearest gwx well with mean_dtw, and with a water-level time series

Outputs (under --out): wells_train.parquet, wells_gwx_nv.parquet, field_wells.parquet,
05_wells_checks.json.

Usage:
    python 05_wells.py --train CSV --gwx PARQUET [--out DIR]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio

from _common import out_dir

GWX_KEEP = ["canonical_id", "source", "source_well_id", "h_accuracy_class", "well_depth", "obs_count",
            "has_time_series", "mean_dtw", "confinement_class", "well_class", "well_use", "geometry"]


def sample(raster: Path, pts: gpd.GeoSeries):
    """Raster value at each point (NaN for nodata or outside the raster)."""
    with rasterio.open(raster) as ds:
        v = np.array([x[0] for x in ds.sample(list(zip(pts.x, pts.y)))], dtype=float)
        return np.where(v == ds.nodata, np.nan, v)


def nearest(fields5070, wells5070, cols, tag):
    """Distance (km) from each field polygon to the nearest well of a set, with that well's columns."""
    j = gpd.sjoin_nearest(fields5070[["NWI_ID", "geometry"]], wells5070[cols + ["geometry"]],
                          how="left", distance_col="d_m")
    j = j.sort_values(["NWI_ID", "d_m"]).drop_duplicates("NWI_ID")
    out = j.set_index("NWI_ID")[["d_m"] + cols].rename(columns={"d_m": f"{tag}_DIST_KM"})
    out[f"{tag}_DIST_KM"] = out[f"{tag}_DIST_KM"] / 1000.0
    return out


def count_within(fields5070, wells5070, km):
    """Number of wells within `km` of each field polygon."""
    buf = gpd.GeoDataFrame(fields5070[["NWI_ID"]].copy(), geometry=fields5070.geometry.buffer(km * 1000.0),
                           crs=fields5070.crs)
    j = gpd.sjoin(buf, wells5070[["geometry"]], how="left", predicate="contains")
    return j.groupby("NWI_ID")["index_right"].count()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train", type=Path, required=True, help="handily_wells_train_nv_parity.csv")
    ap.add_argument("--gwx", type=Path, required=True, help="gwx_nevada/wells.geoparquet")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    out = out_dir(args.out)
    rdir = out / "rasters"
    checks = {}

    # ---- training wells ----------------------------------------------------------------------
    t = pd.read_csv(args.train)
    t = gpd.GeoDataFrame(t.drop(columns=["fillcolor"], errors="ignore"),
                         geometry=gpd.points_from_xy(t["longitude"], t["latitude"]), crs=4326)
    assert t["canonical_id"].is_unique
    t5070 = t.to_crs(5070)
    t["dtw_new_m"] = sample(rdir / "dtw_new_100m_epsg5070.tif", t5070.geometry)
    t["dtw_ma_m"] = sample(rdir / "dtw_ma_100m_epsg5070.tif", t5070.geometry)
    t["dtw_sigma_m"] = sample(rdir / "dtw_sigma_100m_epsg5070.tif", t5070.geometry)
    t["new_minus_well_m"] = t["dtw_new_m"] - t["dtw_m"]
    t["ma_minus_well_m"] = t["dtw_ma_m"] - t["dtw_m"]
    t.to_parquet(out / "wells_train.parquet", index=False)

    # ---- all gwx wells with a depth to water ----------------------------------------------------
    g = gpd.read_parquet(args.gwx)
    g = g[g["mean_dtw"].notna()].copy()
    g["por_start_year"] = pd.to_datetime(g["por_start"]).dt.year
    g["por_end_year"] = pd.to_datetime(g["por_end"]).dt.year
    g = g[GWX_KEEP[:-1] + ["por_start_year", "por_end_year", "geometry"]].reset_index(drop=True)
    g["row_id"] = np.arange(len(g))          # canonical_id is not unique in the gwx file
    g5070 = g.to_crs(5070)
    # training wells are gwx wells under other IDs: flag a gwx well as training if one lies within 2 m
    near = gpd.sjoin_nearest(g5070[["row_id", "geometry"]], t5070[["geometry"]], how="left",
                             distance_col="d", max_distance=2.0)
    g["is_train"] = near.drop_duplicates("row_id").set_index("row_id")["d"].reindex(g["row_id"]).notna().to_numpy()
    g["dtw_new_m"] = sample(rdir / "dtw_new_100m_epsg5070.tif", g5070.geometry)
    g["new_minus_well_m"] = g["dtw_new_m"] - g["mean_dtw"]
    g.to_parquet(out / "wells_gwx_nv.parquet", index=False)

    # Does the 2026-10-08 map honour this training set? Compare |map - well| at training wells
    # with the same statistic at gwx wells that are not in the training set.
    a_tr = t["new_minus_well_m"].abs().dropna()
    a_ot = g.loc[~g["is_train"], "new_minus_well_m"].abs().dropna()
    checks.update({"n_train": len(t), "n_train_in_map": int(a_tr.size), "train_abs_resid_median_m": float(a_tr.median()),
                   "train_abs_resid_p90_m": float(a_tr.quantile(0.9)), "n_other_gwx": int(a_ot.size),
                   "other_abs_resid_median_m": float(a_ot.median()), "other_abs_resid_p90_m": float(a_ot.quantile(0.9)),
                   "n_gwx_flagged_train": int(g["is_train"].sum())})
    print(f"[wells] {len(t):,} training wells ({(t['source'] == 'nv_ndwr').sum():,} NDWR, "
          f"{(t['source'] != 'nv_ndwr').sum():,} monitoring); {len(g):,} gwx wells with mean_dtw, "
          f"{g['is_train'].sum():,} of them at a training-well location")
    print(f"[wells] |new map - well DTW|: training wells median {a_tr.median():.2f} m (p90 {a_tr.quantile(.9):.2f}); "
          f"other gwx wells median {a_ot.median():.2f} m (p90 {a_ot.quantile(.9):.2f})")

    # ---- per-field distances ----------------------------------------------------------------------
    f = gpd.read_parquet(out / "fields_20260820.parquet")[["NWI_ID", "geometry"]].to_crs(5070)
    t5 = t5070.assign(dtw_m=t["dtw_m"].to_numpy())
    res = nearest(f, t5, ["canonical_id", "source", "dtw_m"], "WT").rename(
        columns={"canonical_id": "WT_NEAR_ID", "source": "WT_NEAR_SRC", "dtw_m": "WT_NEAR_DTW_M"})
    res["WT_N1KM"] = count_within(f, t5070, 1)
    res["WT_N5KM"] = count_within(f, t5070, 5)
    res = res.join(nearest(f, g5070, [], "W")).join(nearest(f, g5070[g["has_time_series"].to_numpy()], [], "W_TS"))
    res = res.reset_index()
    res.to_parquet(out / "field_wells.parquet", index=False)
    d = res["WT_DIST_KM"]
    checks.update({"fields_median_dist_train_km": float(d.median()), "fields_gt2km": int((d > 2).sum()),
                   "fields_gt5km": int((d > 5).sum()), "fields_gt10km": int((d > 10).sum())})
    print(f"[wells] fields: nearest training well median {d.median():.2f} km; > 2 km {(d > 2).sum():,}, "
          f"> 5 km {(d > 5).sum():,}, > 10 km {(d > 10).sum():,} of {len(res):,}")
    (out / "05_wells_checks.json").write_text(json.dumps(checks, indent=2, default=float))


if __name__ == "__main__":
    main()
