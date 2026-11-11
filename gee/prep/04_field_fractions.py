#!/usr/bin/env python3
"""
GEE app input 4: per-field f for the four models with the new DTW map and with Ma et al., f (Tan)
with the run's own map, and the subsidy each gives.

The run's own code computes everything here: scripts/gw_subsidy/wtd_zonal.run (zonal statistics
and the per-pixel depths of shallow fields), polaris_gee (POLARIS texture), and
subsidy.compute_field_params (Tan et al. parameters and f for the Tan, Banta, Averianov and Shah
models, averaged over each field's pixels). Only paths and the inputs that live on the run
computer are replaced:

  * field polygons: fields_20260820.parquet (step 2, from the GeoPackage the run used);
  * growing-season ETo: the April-October mean of ETO_MM in the run's field file (step 1)
    instead of the OpenET export it was copied from;
  * dominant crop: CROP_major from the run's field file (copied there from the boundary layer);
  * Earth Engine project: --ee-project (the run used nasa-water).

A. The run's map (2026-10-08, 100 m), with texture as the run had it (only fields shallow under its
   map sampled; the rest take the loam fallback): f (Tan) is compared with gw_frac in the run's field
   file for every field. This measures how close the recomputation comes to the run.
B. The new map (handily 30 m, 2026-10-21, step 3), every model.
C. Ma et al. on the new map's 30 m grid (wtd_nevada.tif in Nevada, the dataset's other state files
   elsewhere: steps 3 and 3b), every model.
B and C use each field's own POLARIS texture. Their HA totals are compared with the report's table
(docs/tables/ha_gw_subsidy_new_vs_ma.csv) for the record only: the report used the run's map.

Outputs (under --out): field_params_run.parquet, field_params_new.parquet, field_params_ma.parquet,
field_texture.csv, field_dtw_sigma.parquet, field_ma_nv_cover.parquet (which fields wtd_nevada.tif alone
covers: the report's comparison set), 04_field_fractions_checks.json.

Usage:
    python 04_field_fractions.py [--ee-project nwi-usgs] [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

import _common
from _common import NY, REPO, out_dir

import nwi_config
nwi_config.RunPaths.make = lambda self: self          # do not create the run folders here
from gw_subsidy import basins, config as gcfg, crops, polaris_gee, soil_texture, subsidy, wtd_zonal  # noqa: E402

TAN_PARAMS = Path("/Users/Sayantan.Majumdar@dri.edu/VSCode/nwi-gw-subsidy/Data/Inputs/tan2025_params.csv")
HA_TABLE = REPO / "docs" / "tables" / "ha_gw_subsidy_new_vs_ma.csv"


# ---------------------------------------------------------------------------------------------
# Inputs that replace the run computer's files
# ---------------------------------------------------------------------------------------------
class Inputs:
    def __init__(self, out: Path):
        self.out = out
        self.fields = gpd.read_parquet(out / "fields_20260820.parquet").rename(columns={"NWI_ID": "OPENET_ID"})
        st = pd.read_parquet(out / "field_static.parquet").rename(columns={"NWI_ID": "OPENET_ID"})
        self.static = st
        self.gs_eto = st[["OPENET_ID", "gs_eto_mm"]]
        self.crop = st[["OPENET_ID", "CROP_major"]]

    def fields_in_crs(self, crs):
        """Stand-in for wtd_zonal._load_fields_in_raster_crs: one polygon per field, area in m2."""
        g = self.fields[["OPENET_ID", "geometry"]].to_crs(5070)
        g["field_area_m2"] = g.geometry.area
        return g.to_crs(crs)


def point_run_code_at(inp: Inputs, dtw_tif: Path, sub: Path, texture_csv: Path, sigma: pd.DataFrame):
    """Point gw_subsidy.config at this product's files and swap in the replaced inputs."""
    sub.mkdir(parents=True, exist_ok=True)
    gcfg.OUTPUT_DIR = gcfg.INPUT_DIR = sub
    gcfg.WTD_TIF = dtw_tif
    gcfg.FIELD_WTD_PARQUET = sub / "field_wtd.parquet"
    gcfg.FIELD_WTD_PIXELS_PARQUET = sub / "field_wtd_pixels.parquet"
    gcfg.FIELD_PARAMS_PARQUET = sub / "field_params.parquet"
    gcfg.FIELD_TEXTURE_CSV = texture_csv
    gcfg.TAN_PARAMS_CSV = TAN_PARAMS
    soil_texture._param_table.cache_clear()
    wtd_zonal._load_fields_in_raster_crs = lambda: inp.fields_in_crs(_raster_crs(dtw_tif))
    wtd_zonal.field_sigma = lambda force=False: sigma
    subsidy.growing_season_eto = lambda: inp.gs_eto.copy()
    subsidy._crop_by_field = lambda: crops.annotate(inp.crop.copy(), code_col="CROP_major")
    # sw_filter is false in the run, so every field keeps its f; the attribute test needs the
    # GeoPackage and is replaced by "keep all" (gw_frac = gwFrac_tan_crop either way).
    basins.field_keep_from_attributes = lambda: pd.DataFrame(
        {"OPENET_ID": inp.static["OPENET_ID"], "keep": True, "field_has_sw": False})
    assert gcfg.SW_FILTER is False and gcfg.APPLY_WTD_CORRECTION is False


def _raster_crs(path):
    import rasterio
    with rasterio.open(path) as ds:
        return ds.crs


def zonal(inp, dtw_tif, sub, sigma):
    point_run_code_at(inp, dtw_tif, sub, sub / "unused_texture.csv", sigma)
    if not (gcfg.FIELD_WTD_PARQUET.exists() and gcfg.FIELD_WTD_PIXELS_PARQUET.exists()):
        wtd_zonal.run(force=True)
    return pd.read_parquet(gcfg.FIELD_WTD_PIXELS_PARQUET, columns=["OPENET_ID"])["OPENET_ID"].unique()


def sigma_by_field(inp, sigma_tif, out):
    """Mean DTW uncertainty per field (as wtd_zonal.field_sigma), cached."""
    path = out / "field_dtw_sigma.parquet"
    if path.exists():
        return pd.read_parquet(path)
    from exactextract import exact_extract
    g = inp.fields_in_crs(_raster_crs(sigma_tif))
    res = exact_extract(str(sigma_tif), g, ["mean"], include_cols=["OPENET_ID"], output="pandas")
    s = res.rename(columns={"mean": "dtw_sigma_mean_m"})[["OPENET_ID", "dtw_sigma_mean_m"]]
    s.to_parquet(path, index=False)
    return s


def ma_nv_cover(inp, nv_tif, out):
    """Coverage of each field by wtd_nevada.tif alone (step 3, before step 3b fills the other states).

    The report compared the maps on the fields this file covers; ma_nv_px is the number of its valid
    30 m pixels in the field, each weighted by its share inside the field (0 = not covered). Cached.
    """
    path = out / "field_ma_nv_cover.parquet"
    if path.exists():
        return pd.read_parquet(path)
    from exactextract import exact_extract
    g = inp.fields_in_crs(_raster_crs(nv_tif))
    res = exact_extract(str(nv_tif), g, ["count"], include_cols=["OPENET_ID"], output="pandas")
    s = res.rename(columns={"count": "ma_nv_px"})[["OPENET_ID", "ma_nv_px"]]
    s.to_parquet(path, index=False)
    return s


def sample_texture(inp, ids, path, ee_project):
    """POLARIS root-zone texture for `ids`, sampled as polaris_gee.run does; cached in `path`."""
    have = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["OPENET_ID"])
    todo = sorted(set(ids) - set(have["OPENET_ID"]))
    if not todo:
        return have
    import ee
    import shapely
    ee.Initialize(project=ee_project)
    g = inp.fields[inp.fields["OPENET_ID"].isin(todo)][["OPENET_ID", "geometry"]].copy()
    # polaris_gee._fields_4326: simplify 20 m in UTM 11N, buffer(0), back to EPSG:4326, 2-D.
    g = g.to_crs(32611)
    g["geometry"] = g.geometry.simplify(polaris_gee.SIMPLIFY_M).buffer(0)
    g = g.to_crs(4326)
    g["geometry"] = shapely.force_2d(g.geometry.values)
    soil = polaris_gee._soil_image(ee)
    rows = []
    print(f"[soil] sampling POLARIS for {len(g):,} fields (project {ee_project})")
    for start in range(0, len(g), polaris_gee.BATCH):
        batch = g.iloc[start:start + polaris_gee.BATCH]
        feats = [ee.Feature(ee.Geometry(geom.__geo_interface__, proj="EPSG:4326", geodesic=False),
                            {"OPENET_ID": oid}) for oid, geom in zip(batch["OPENET_ID"], batch.geometry)]
        red = soil.reduceRegions(collection=ee.FeatureCollection(feats), reducer=ee.Reducer.mean(),
                                 scale=polaris_gee.POLARIS_SCALE, tileScale=4)
        for f in red.getInfo()["features"]:
            p = f["properties"]
            rows.append({"OPENET_ID": p.get("OPENET_ID"), "sand": p.get("sand"),
                         "silt": p.get("silt"), "clay": p.get("clay")})
        if (start // polaris_gee.BATCH) % 5 == 0:
            print(f"[soil]   {min(start + polaris_gee.BATCH, len(g)):,}/{len(g):,}", flush=True)
    new = pd.DataFrame(rows)
    new["texture"] = soil_texture.classify_textures(new["sand"], new["silt"], new["clay"])
    merged = pd.concat([have, new], ignore_index=True).drop_duplicates("OPENET_ID", keep="last")
    merged.to_csv(path, index=False)
    return merged


def field_params(inp, dtw_tif, sub, texture_csv, sigma):
    """subsidy.compute_field_params for one DTW raster (zonal files already in `sub`)."""
    point_run_code_at(inp, dtw_tif, sub, texture_csv, sigma)
    return subsidy.compute_field_params(force=True)


# ---------------------------------------------------------------------------------------------
# Subsidy from f, month by month, as the run computes it
# ---------------------------------------------------------------------------------------------
class Months:
    """Field-month ETa and NetET_pre_subsidy from step 1, indexed for fast per-field mapping."""

    def __init__(self, out):
        m = pd.read_parquet(out / "field_month_core.parquet")
        self.code, self.ids = pd.factorize(m["NWI_ID"], sort=True)
        self.ids = np.asarray(self.ids)
        self.year = m["YEAR"].to_numpy()
        self.irr = m["irrigated"].to_numpy(bool)
        self.eta = m["ETa_final_acre_ft"].to_numpy(float)
        self.netpos = np.clip(m["NetET_pre_subsidy_acre_ft"].to_numpy(float), 0, None)
        self.run_sub = m["GW_subsidy_acre_ft"].to_numpy(float)
        self.fy = self.code * NY + (self.year - _common.START_YEAR)   # field-year key

    def subsidy(self, f_by_field: pd.Series):
        """Field-month subsidy min(f x ETa, max(NetET_pre, 0)); fields without f get 0."""
        f = f_by_field.reindex(self.ids).fillna(0.0).to_numpy(float)[self.code]
        return np.minimum(f * self.eta, self.netpos)

    def field_mean(self, monthly):
        """Annual mean over the period, irrigated field-years only (report definition).

        The 1,194 field-months with no ETa have a NaN subsidy; like the run's pandas sums, they add 0.
        """
        s = np.bincount(self.code, weights=np.where(self.irr, np.nan_to_num(monthly), 0.0), minlength=len(self.ids))
        return pd.Series(s / NY, index=self.ids)


def ha_compare(fld, ha_col, value_col, ref, ref_col):
    """Max |difference| between HA sums and the report table (both rounded to 0.01)."""
    got = fld.groupby(ha_col)[value_col].sum().round(2)
    j = ref[[ref_col]].join(got, how="outer").fillna(0.0)
    d = (j[ref_col] - j[value_col]).abs()
    return float(d.max()), int((d > 0.0051).sum()), d.sort_values(ascending=False).head(5).round(2).to_dict()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ee-project", default="nwi-usgs")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    out = out_dir(args.out)
    rdir = out / "rasters"
    inp = Inputs(out)
    checks = {}

    sigma = sigma_by_field(inp, rdir / "dtw_sigma_100m_epsg5070.tif", out)
    nv = ma_nv_cover(inp, rdir / "dtw_ma_nv_30m_epsg5070.tif", out)
    checks["n_fields_covered_by_wtd_nevada"] = int((nv["ma_nv_px"] > 0).sum())

    # ---- zonal statistics for every raster ---------------------------------------------------
    rasters = {"run": rdir / "dtw_run_100m_epsg5070.tif",      # the run's map: 2026-10-08, 100 m
               "new": rdir / "dtw_new_30m_epsg5070.tif",       # the new map: handily 30 m, 2026-10-21
               "ma": rdir / "dtw_ma_30m_epsg5070.tif"}         # Ma et al. on the new map's 30 m grid
    shallow = {k: zonal(inp, r, out / f"zonal_{k}", sigma) for k, r in rasters.items()}
    for k, v in shallow.items():
        print(f"[zonal] {k}: {len(v):,} fields with a pixel shallower than {wtd_zonal.KEEP_DEPTH_M} m", flush=True)
    checks["n_shallow_fields"] = {k: len(v) for k, v in shallow.items()}

    # ---- texture: sampled for every field that is shallow under any of the maps ---------------
    tex_all = out / "field_texture.csv"
    tex = sample_texture(inp, set().union(*shallow.values()), tex_all, args.ee_project)
    # The run sampled only the fields shallow under its own map; the rest took the loam fallback.
    tex_run_like = out / "field_texture_run_shallow_only.csv"
    tex[tex["OPENET_ID"].isin(set(shallow["run"]))].to_csv(tex_run_like, index=False)
    print(f"[soil] {tex['texture'].notna().sum():,} of {len(tex):,} fields classified")

    months = Months(out)
    run_f = inp.static.set_index("OPENET_ID")["gw_frac"]
    tot_run = float(months.field_mean(months.run_sub).sum())

    def tan(params):
        """f (Tan) per field and the annual-mean subsidy it gives, per field."""
        f = params.set_index("OPENET_ID")["gwFrac_tan_crop"].reindex(run_f.index).fillna(0.0)
        return f, months.field_mean(months.subsidy(f))

    # ---- A. the run's map: how close the recomputation comes to the run -----------------------
    pr = field_params(inp, rasters["run"], out / "zonal_run", tex_run_like, sigma)
    f_r, s_r = tan(pr)
    d = (f_r - run_f).abs()
    checks.update({"run_map_f_max_abs_diff": float(d.max()), "run_map_f_n_fields_diff_gt_1e-9": int((d > 1e-9).sum()),
                   "run_map_f_n_fields_diff_gt_1e-4": int((d > 1e-4).sum()),
                   "run_map_tan_total_recomputed": float(s_r.sum()), "tan_total_run": tot_run})
    print(f"[A] run's map: f (Tan) vs the run's gw_frac: max |diff| {d.max():.3e}; {(d > 1e-9).sum()} fields differ "
          f"by > 1e-9, {(d > 1e-4).sum()} by > 1e-4. Tan total recomputed {s_r.sum():,.1f} vs run {tot_run:,.1f} acre-ft/yr",
          flush=True)
    pr.to_parquet(out / "field_params_run.parquet", index=False)

    # ---- B. the new map, every model; C. Ma et al. on the same 30 m grid ----------------------
    # Texture is each field's own (sampled for every shallow field), as in the app's Ma comparison.
    ref = pd.read_csv(HA_TABLE, dtype={"HYD_AREA": str}).set_index("HYD_AREA")
    ha = inp.static.set_index("OPENET_ID")["HYD_AREA"].reindex(months.ids)
    for step, name, ref_col in (("B", "new", "subsidy_new_acre_ft_yr"), ("C", "ma", "subsidy_ma_acre_ft_yr")):
        p = field_params(inp, rasters[name], out / f"zonal_{name}", tex_all, sigma)
        p.to_parquet(out / f"field_params_{name}.parquet", index=False)
        _, s = tan(p)
        dmax, nbad, worst = ha_compare(pd.DataFrame({"HYD_AREA": ha, "s": s}), "HYD_AREA", "s", ref, ref_col)
        checks[f"{name}_tan_total"] = float(s.sum())
        checks[f"{name}_vs_report_{ref_col}"] = {"ha_max_abs_diff": dmax, "n_ha_off": nbad, "worst": worst}
        print(f"[{step}] {name}: Tan total {s.sum():,.1f} acre-ft/yr; against the report's {ref_col}, "
              f"{nbad} HAs differ (largest {dmax:,.1f})", flush=True)
    (out / "04_field_fractions_checks.json").write_text(json.dumps(checks, indent=2, default=float))


if __name__ == "__main__":
    main()
