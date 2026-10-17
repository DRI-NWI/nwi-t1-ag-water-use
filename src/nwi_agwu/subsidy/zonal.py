"""Per-field water-table depth from the DTW raster (vendored wtd_zonal.py logic).

Coverage-weighted pixels are kept for fields whose shallowest pixel is above
keep_depth_m, so f(d) can be averaged per pixel (no Jensen bias). Fields without
raster coverage get a centroid sample for the summary (as in the source, they get no
pixels, so their f is 0); fields outside the raster stay NaN.
Requires the ``exactextract`` package.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .constants import WTD_MIN_VALID

CHUNK = 1500


def load_fields(gpkg, raster):
    import geopandas as gpd
    import rasterio

    with rasterio.open(raster) as ds:
        crs = ds.crs
    g = gpd.read_file(gpkg, columns=["NWI_ID"])
    g = g.dissolve(by="NWI_ID", as_index=False)
    g["field_area_m2"] = g.to_crs(5070).geometry.area
    return g.to_crs(crs)


def run(gpkg, dtw_tif, sigma_tif, keep_depth_m=16.0):
    import rasterio
    from exactextract import exact_extract

    with rasterio.open(dtw_tif) as ds:
        pixel_area = abs(ds.res[0] * ds.res[1])
    gdf = load_fields(gpkg, dtw_tif)
    n = len(gdf)
    summ, pix = [], []
    for start in range(0, n, CHUNK):
        batch = gdf.iloc[start:start + CHUNK]
        res = exact_extract(str(dtw_tif), batch, ["median", "stdev", "values", "coverage"],
                            include_cols=["NWI_ID"], output="pandas")
        sig = exact_extract(str(sigma_tif), batch, ["mean"], include_cols=["NWI_ID"], output="pandas")
        sig = sig.set_index("NWI_ID")["mean"]
        for (_, r), area in zip(res.iterrows(), batch["field_area_m2"].to_numpy()):
            vals = np.asarray(r["values"], float)
            cov = np.asarray(r["coverage"], float)
            ok = np.isfinite(vals) & (vals >= WTD_MIN_VALID) & (vals < 1000.0)
            vals, cov = vals[ok], cov[ok]
            cnt = float(cov.sum())
            if cnt > 0:
                row = dict(wtd_mean_m=float(np.average(vals, weights=cov)), wtd_min_m=float(vals.min()),
                           wtd_max_m=float(vals.max()),
                           wtd_median_m=float(r["median"]) if pd.notna(r["median"]) else np.nan,
                           wtd_std_m=float(r["stdev"]) if pd.notna(r["stdev"]) else 0.0)
            else:
                row = dict(wtd_mean_m=np.nan, wtd_min_m=np.nan, wtd_max_m=np.nan, wtd_median_m=np.nan, wtd_std_m=np.nan)
            row.update(NWI_ID=r["NWI_ID"], n_valid_cells=cnt, field_area_m2=area,
                       valid_frac=cnt * pixel_area / area if area > 0 else np.nan,
                       dtw_sigma_mean_m=float(sig.get(r["NWI_ID"], np.nan)), wtd_source="zonal" if cnt > 0 else "")
            summ.append(row)
            if cnt > 0 and vals.min() < keep_depth_m:
                pix.append(pd.DataFrame({"NWI_ID": r["NWI_ID"], "wtd_m": vals.astype("float32"),
                                         "weight": cov.astype("float32")}))
        print(f"[wtd] {min(start + CHUNK, n)}/{n}", flush=True)
    summary = pd.DataFrame(summ)
    miss = summary["n_valid_cells"] <= 0
    if miss.any():
        sub = gdf.set_index("NWI_ID").loc[summary.loc[miss, "NWI_ID"]]
        pts = [(geom.centroid.x, geom.centroid.y) for geom in sub.geometry]
        with rasterio.open(dtw_tif) as ds:
            sampled = np.array([v[0] for v in ds.sample(pts)], float)
        good = np.isfinite(sampled) & (sampled >= WTD_MIN_VALID) & (sampled < 1000.0)
        idx = summary.index[miss]
        for col in ["wtd_mean_m", "wtd_median_m", "wtd_min_m", "wtd_max_m"]:
            summary.loc[idx, col] = np.where(good, sampled, np.nan)
        summary.loc[idx, "wtd_source"] = np.where(good, "centroid", "outside_raster")
    pixels = pd.concat(pix, ignore_index=True) if pix else pd.DataFrame(columns=["NWI_ID", "wtd_m", "weight"])
    summary["pixel_area_m2"] = pixel_area
    return summary, pixels
