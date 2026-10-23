"""Combine WTD, soil texture, crop and ET into per-field groundwater-subsidy estimates.

Pipeline:
  1. aggregate_csvs()        stream the GW/SW monthly water-balance CSVs -> per
                             (field, water_source, year) ET / NetET / ETo volumes (acre-ft),
                             plus each field's growing-season mean reference ET (for ET0-aware params).
  2. compute_field_params()  join WTD + texture + crop + ET0 -> (d_dec, d_ext, p) and the
                             per-field groundwater fraction gwFrac for all four models, averaged
                             per WTD pixel inside the field (no Jensen bias). Deep fields -> 0.
  3. compute_subsidy()       gwFrac x ETa = groundwater-supplied ET (the subsidy), with the
                             model range and the min(f*ET0,ETa) / f*NetET diagnostic measures;
                             aggregate to field-year and period-of-record means.

The groundwater fraction is static per field (WTD is a long-term snapshot); only ET varies
in time, so field/year/period subsidy volumes are just gwFrac times the corresponding ET volume.

NWI T1 edits:
  * Only step 2 is used here. The subsidy volume (step 3) is computed month by month in
    process_field_summaries.py (add_gw_subsidy), so aggregate_csvs and compute_subsidy are
    removed.
  * The growing-season ETo per field comes from the OpenET ENSEMBLE export, all April-October
    months (growing_season_eto), instead of the irrigated rows of the field-summary CSVs.
  * The well-anchored WTD correction runs only when APPLY_WTD_CORRECTION is True (off, as before).
  * compute_field_params adds gw_frac, the fraction the water balance uses: gwFrac_tan_crop,
    set to 0 for fields the SW-use basin test removes when config.SW_FILTER is on.
  * Fields are read by NWI_ID (= OPENET_ID in the 2026-08-20 layer).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import basins, config, crops, models, soil_texture

# --------------------------------------------------------------------------- #
# 1. Growing-season reference ET (NWI T1: replaces aggregate_csvs)
# --------------------------------------------------------------------------- #
def growing_season_eto():
    """
    Mean monthly ETo (mm) over April-October for every field, from the OpenET ENSEMBLE export.

    Returns
    -------
    pd.DataFrame
        OPENET_ID, gs_eto_mm.
    """
    from pathlib import Path
    files = sorted(Path(config.ENSEMBLE_DIR).rglob("*.parquet"))
    parts = []
    for f in files:
        d = pd.read_parquet(f, columns=["OPENET_ID", "MONTH", "ETO_MM"])
        d["MONTH"] = d["MONTH"].astype(int)
        d["ETO_MM"] = d["ETO_MM"].replace(-999, np.nan)
        parts.append(d[d["MONTH"].isin(config.GROWING_SEASON_MONTHS)])
    eto = pd.concat(parts, ignore_index=True)
    return eto.groupby("OPENET_ID", as_index=False)["ETO_MM"].mean().rename(columns={"ETO_MM": "gs_eto_mm"})


# --------------------------------------------------------------------------- #
# 2. Field parameters + groundwater fraction (per-pixel averaged)
# --------------------------------------------------------------------------- #
def _crop_by_field():
    """Dominant CDL crop of each field, annotated with rooting depth and crop/non-crop flags."""
    import geopandas as gpd
    g = gpd.read_file(config.FIELDS_GPKG, columns=["NWI_ID", "CROP_major"]).rename(columns={"NWI_ID": "OPENET_ID"})  # NWI T1
    g = g[g["OPENET_ID"].notna()].drop_duplicates("OPENET_ID")
    return crops.annotate(g[["OPENET_ID", "CROP_major"]], code_col="CROP_major")


def _gwfrac_per_field(params, mode_label):
    """Per-field gwFrac for all four models, averaging f over each field's WTD pixels."""
    px = pd.read_parquet(config.FIELD_WTD_PIXELS_PARQUET)
    px = px.merge(params[["OPENET_ID", "d_dec_m", "d_ext_m", "p", "wtd_delta_m"]],
                  on="OPENET_ID", how="inner")
    # Well-anchored correction: translate each pixel by the field's WTD delta (clip at surface).
    d = np.clip(px["wtd_m"].to_numpy(float) + px["wtd_delta_m"].fillna(0.0).to_numpy(float), 0.0, None)
    dd, de, pe = px["d_dec_m"].to_numpy(float), px["d_ext_m"].to_numpy(float), px["p"].to_numpy(float)
    w = px["weight"].to_numpy(float)
    out = px[["OPENET_ID"]].copy()
    out["w"] = w
    for model in config.MODELS:
        out[f"wf_{model}"] = w * models.fraction_vec(d, dd, de, pe, model=model)
    agg = out.groupby("OPENET_ID", as_index=False).sum()
    res = pd.DataFrame({"OPENET_ID": agg["OPENET_ID"]})
    for model in config.MODELS:
        res[f"gwFrac_{model}_{mode_label}"] = agg[f"wf_{model}"] / agg["w"].replace(0, np.nan)
    return res


def compute_field_params(force=False):
    """Per-field texture/crop/ET0 -> (d_dec,d_ext,p) and gwFrac (headline crop/ET0 + bare-soil)."""
    if config.FIELD_PARAMS_PARQUET.exists() and not force:
        return pd.read_parquet(config.FIELD_PARAMS_PARQUET)

    et0 = growing_season_eto()   # NWI T1
    wtd = pd.read_parquet(config.FIELD_WTD_PARQUET)
    tex = pd.read_csv(config.FIELD_TEXTURE_CSV)
    crop = _crop_by_field()

    # NWI T1: the well-anchored correction needs NDWR well data; it is off by default.
    if config.APPLY_WTD_CORRECTION:
        from . import wtd_correction
        corr = wtd_correction.run()
    else:
        corr = pd.DataFrame({"OPENET_ID": wtd["OPENET_ID"], "basin_bias_m": 0.0, "confidence": "none",
                             "wtd_delta_m": 0.0, "wtd_mean_corr_m": wtd["wtd_mean_m"]})
    df = (wtd.merge(tex[["OPENET_ID", "sand", "silt", "clay", "texture"]], on="OPENET_ID", how="left")
             .merge(crop, on="OPENET_ID", how="left")
             .merge(et0, on="OPENET_ID", how="left")
             .merge(corr[["OPENET_ID", "basin_bias_m", "confidence", "wtd_delta_m", "wtd_mean_corr_m"]],
                    on="OPENET_ID", how="left"))
    df["root_depth_cm"] = df["root_depth_cm"].fillna(config.ROOT_DEPTH_SHALLOW_CM)
    df["et0_mm_d"] = (df["gs_eto_mm"] / 30.4).fillna(config.ET0_DEFAULT_MM_D)  # mm/month -> mm/day
    # Well-anchored WTD correction (optional; off by default in favour of the SW-use basin filter).
    df["wtd_mean_modeled_m"] = df["wtd_mean_m"]
    if config.APPLY_WTD_CORRECTION:
        df["wtd_delta_m"] = df["wtd_delta_m"].fillna(0.0)
        df["wtd_mean_m"] = df["wtd_mean_corr_m"].fillna(df["wtd_mean_m"])
    else:
        df["wtd_delta_m"] = 0.0

    # Headline (crop/ET0-aware) params, and bare-soil params for the comparison figure.
    headline = soil_texture.assign_params(df.assign(texture=df["texture"].fillna("loam")), mode="crop_et0")
    df["d_dec_m"], df["d_ext_m"], df["p"] = headline["d_dec_m"], headline["d_ext_m"], headline["p"]
    bare = soil_texture.assign_params(
        df.assign(texture=df["texture"].fillna("loam"), et0_mm_d=config.ET0_DEFAULT_MM_D), mode="bare")
    df["d_dec_bare_m"], df["d_ext_bare_m"], df["p_bare"] = bare["d_dec_m"], bare["d_ext_m"], bare["p"]

    # gwFrac per model (per-pixel averaged). Fields without shallow pixels -> 0.
    gw = _gwfrac_per_field(df, "crop")
    gw_bare = _gwfrac_per_field(df.assign(d_dec_m=df.d_dec_bare_m, d_ext_m=df.d_ext_bare_m, p=df.p_bare), "bare")
    df = df.merge(gw, on="OPENET_ID", how="left").merge(gw_bare, on="OPENET_ID", how="left")
    frac_cols = [c for c in df.columns if c.startswith("gwFrac_")]
    df[frac_cols] = df[frac_cols].fillna(0.0)

    # NWI T1: the fraction used by the water balance, with the optional SW-use basin test
    keep = basins.field_keep_from_attributes()
    df = df.merge(keep[["OPENET_ID", "keep"]].rename(columns={"keep": "sw_filter_keep"}), on="OPENET_ID", how="left")
    df["sw_filter_keep"] = df["sw_filter_keep"].fillna(True)
    df["gw_frac"] = df["gwFrac_tan_crop"].where(df["sw_filter_keep"] | (not config.SW_FILTER), 0.0)
    from . import wtd_zonal
    df = df.merge(wtd_zonal.field_sigma(), on="OPENET_ID", how="left")

    config.FIELD_PARAMS_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(config.FIELD_PARAMS_PARQUET, index=False)
    n_sub = (df["gwFrac_tan_crop"] > 0).sum()
    print(f"[subsidy] field params for {len(df):,} fields; {n_sub:,} with gwFrac_tan > 0")
    return df


def run(force=False):
    """Compute and cache per-field parameters and gw_frac (NWI T1: subsidy volumes are made by the processor)."""
    return compute_field_params(force=force)


if __name__ == "__main__":
    run()
