"""Per-field water-table-depth zonal statistics from the high-resolution WTD raster.

Fields are dissolved by OPENET_ID (multi-polygon fields become one geometry) and
reprojected into the raster's native CRS (cheap; the 1.7 GB raster is never warped).
For every field we record summary WTD stats. For *shallow candidate* fields (any pixel
shallower than KEEP_DEPTH_M, slightly deeper than the deepest possible extinction depth)
we additionally store the raw coverage-weighted WTD pixels, so the groundwater fraction
can later be averaged per pixel (avoiding the Jensen bias of f(mean WTD)). Deep fields
are pruned -- their groundwater fraction is identically zero for any soil texture.

Outputs:
  Data/Outputs/field_wtd.parquet          one row per field: summary stats
  Data/Outputs/field_wtd_pixels.parquet   long: (OPENET_ID, wtd_m, weight) for shallow fields
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config

KEEP_DEPTH_M = 16.0   # store pixels for fields with min WTD < this (max veg d_ext ~ 15 m)
PIXEL_AREA_M2 = 24.140766313896204 ** 2
CHUNK = 1500


def _load_fields_in_raster_crs():
    import geopandas as gpd
    import rasterio
    with rasterio.open(config.WTD_TIF) as ds:
        rcrs = ds.crs
    gdf = gpd.read_file(config.FIELDS_GPKG, columns=["OPENET_ID"])
    gdf = gdf[gdf["OPENET_ID"].notna()].copy()
    # Dissolve multi-polygon fields (same OPENET_ID) into one geometry.
    gdf = gdf.dissolve(by="OPENET_ID", as_index=False)
    gdf["field_area_m2"] = gdf.geometry.area
    return gdf.to_crs(rcrs)


def run(force=False):
    """Compute per-field WTD summary + shallow-field pixels. Cached; returns the summary DataFrame."""
    if config.FIELD_WTD_PARQUET.exists() and config.FIELD_WTD_PIXELS_PARQUET.exists() and not force:
        return pd.read_parquet(config.FIELD_WTD_PARQUET)

    from exactextract import exact_extract

    gdf = _load_fields_in_raster_crs()
    n = len(gdf)
    print(f"[wtd] {n} dissolved fields; extracting in chunks of {CHUNK}")

    summ_rows = []
    pixel_frames = []
    raster = str(config.WTD_TIF)
    ops = ["mean", "median", "min", "max", "stdev", "count", "values", "coverage"]

    for start in range(0, n, CHUNK):
        batch = gdf.iloc[start:start + CHUNK]
        res = exact_extract(raster, batch, ops, include_cols=["OPENET_ID"], output="pandas")
        # field_area aligned by position
        areas = batch["field_area_m2"].to_numpy()
        for (_, r), area in zip(res.iterrows(), areas):
            vals = np.asarray(r["values"], dtype=float)
            cov = np.asarray(r["coverage"], dtype=float)
            # Guard against any residual nodata / implausible depths.
            ok = np.isfinite(vals) & (vals >= config.WTD_MIN_VALID) & (vals < 1000.0)
            vals, cov = vals[ok], cov[ok]
            cnt = float(cov.sum())
            valid_frac = (cnt * PIXEL_AREA_M2) / area if area > 0 else np.nan
            if cnt > 0:
                wmean = float(np.average(vals, weights=cov))
                wmin, wmax = float(vals.min()), float(vals.max())
                wmed = float(r["median"]) if pd.notna(r["median"]) else wmean
                wstd = float(r["stdev"]) if pd.notna(r["stdev"]) else 0.0
            else:
                wmean = wmin = wmax = wmed = wstd = np.nan
            summ_rows.append({
                "OPENET_ID": r["OPENET_ID"], "wtd_mean_m": wmean, "wtd_median_m": wmed,
                "wtd_min_m": wmin, "wtd_max_m": wmax, "wtd_std_m": wstd,
                "n_valid_cells": cnt, "valid_frac": valid_frac, "field_area_m2": area,
            })
            if cnt > 0 and wmin < KEEP_DEPTH_M:
                pixel_frames.append(pd.DataFrame({
                    "OPENET_ID": r["OPENET_ID"],
                    "wtd_m": vals.astype("float32"),
                    "weight": cov.astype("float32"),
                }))
        print(f"[wtd]   {min(start + CHUNK, n)}/{n}")

    summary = pd.DataFrame(summ_rows)
    # Centroid fallback for fields with no valid raster coverage.
    miss = summary[summary["n_valid_cells"] <= 0]
    if len(miss):
        summary = _centroid_fallback(summary, gdf, miss["OPENET_ID"].tolist())

    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary.to_parquet(config.FIELD_WTD_PARQUET, index=False)
    pixels = (pd.concat(pixel_frames, ignore_index=True) if pixel_frames
              else pd.DataFrame(columns=["OPENET_ID", "wtd_m", "weight"]))
    pixels.to_parquet(config.FIELD_WTD_PIXELS_PARQUET, index=False)
    print(f"[wtd] wrote {len(summary)} field summaries, {len(pixels):,} shallow-field pixels "
          f"({summary['wtd_mean_m'].notna().sum()} with valid WTD)")
    return summary


def _centroid_fallback(summary, gdf, miss_ids):
    """Fill fields with no zonal coverage by sampling the raster at the field centroid."""
    import rasterio
    sub = gdf[gdf["OPENET_ID"].isin(miss_ids)]
    pts = [(geom.centroid.x, geom.centroid.y) for geom in sub.geometry]
    with rasterio.open(config.WTD_TIF) as ds:
        sampled = [v[0] for v in ds.sample(pts)]
    fill = {}
    for oid, v in zip(sub["OPENET_ID"], sampled):
        v = float(v)
        fill[oid] = v if np.isfinite(v) and config.WTD_MIN_VALID <= v < 1000.0 else np.nan
    summary = summary.set_index("OPENET_ID")
    for oid, v in fill.items():
        for c in ("wtd_mean_m", "wtd_median_m", "wtd_min_m", "wtd_max_m"):
            summary.loc[oid, c] = v
        summary.loc[oid, "wtd_std_m"] = 0.0
    return summary.reset_index()


if __name__ == "__main__":
    run()
