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
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config, crops, models, soil_texture, wtd_correction

# Columns pulled from the (51-column) monthly CSVs.
_USECOLS = [
    "OPENET_ID", "YEAR", "MONTH", "water_source", "Basin",
    "ETA_MM", "ETO_MM", "ETNET_MM", "%_IRRIGATED", "ACRES_FTR_GEOM", "ACRES_IRR",
    "ETa_vol_intermediate_acre_ft", "NetET_vol_intermediate_acre_ft", "AR_vol_original_acre_ft",
]

# Net-ET sentinel: ETNET_MM == -999 flags months where net ET is undefined; those rows carry
# garbage NetET_vol / AR_vol (large negatives) that would corrupt the basin sums, so drop them.
_ETNET_SENTINEL = -999.0
# Field-quality filter: keep only predominantly-irrigated fields (>50% of the polygon irrigated).
_MIN_PCT_IRRIGATED = 0.5
_CHUNK = 500_000
FIELD_YEAR_ET_PARQUET = config.OUTPUT_DIR / "field_year_et.parquet"
FIELD_ET0_PARQUET = config.OUTPUT_DIR / "field_et0.parquet"


# --------------------------------------------------------------------------- #
# 1. CSV aggregation
# --------------------------------------------------------------------------- #
def _eto_vol_acft(eto_mm, acres_ftr_geom):
    return eto_mm * acres_ftr_geom * config.M2_PER_ACRE * config.MM_M2_TO_ACFT


def aggregate_csvs(force=False):
    """Stream both CSVs to field-year ET volumes and field growing-season mean ET0."""
    if FIELD_YEAR_ET_PARQUET.exists() and FIELD_ET0_PARQUET.exists() and not force:
        return pd.read_parquet(FIELD_YEAR_ET_PARQUET), pd.read_parquet(FIELD_ET0_PARQUET)

    gs = set(config.GROWING_SEASON_MONTHS)
    fy_parts, et0_parts = [], []
    for path in (config.GW_CSV, config.SW_CSV):
        print(f"[subsidy] aggregating {path.name}")
        for chunk in pd.read_csv(path, usecols=_USECOLS, chunksize=_CHUNK):
            chunk = chunk.drop_duplicates(["OPENET_ID", "YEAR", "MONTH", "water_source"])
            # Keep predominantly-irrigated fields; drop net-ET sentinel rows (-999).
            chunk = chunk[(chunk["%_IRRIGATED"] > _MIN_PCT_IRRIGATED)
                          & (chunk["ETNET_MM"].round(3) != _ETNET_SENTINEL)]
            chunk["eto_vol_acft"] = _eto_vol_acft(chunk["ETO_MM"], chunk["ACRES_FTR_GEOM"])
            in_gs = chunk["MONTH"].isin(gs)
            chunk["eta_gs"] = np.where(in_gs, chunk["ETa_vol_intermediate_acre_ft"], 0.0)

            fy = chunk.groupby(["OPENET_ID", "water_source", "YEAR", "Basin"], as_index=False).agg(
                eta_acft=("ETa_vol_intermediate_acre_ft", "sum"),
                netet_acft=("NetET_vol_intermediate_acre_ft", "sum"),
                ar_acft=("AR_vol_original_acre_ft", "sum"),
                eto_acft=("eto_vol_acft", "sum"),
                eta_gs_acft=("eta_gs", "sum"),
                acres_ftr=("ACRES_FTR_GEOM", "first"),
                acres_irr=("ACRES_IRR", "first"),
            )
            fy_parts.append(fy)

            gsc = chunk[in_gs]
            et0 = gsc.groupby("OPENET_ID", as_index=False).agg(
                eto_sum=("ETO_MM", "sum"), eto_n=("ETO_MM", "count"))
            et0_parts.append(et0)

    field_year = (pd.concat(fy_parts, ignore_index=True)
                  .groupby(["OPENET_ID", "water_source", "YEAR", "Basin"], as_index=False)
                  .agg(eta_acft=("eta_acft", "sum"), netet_acft=("netet_acft", "sum"),
                       ar_acft=("ar_acft", "sum"), eto_acft=("eto_acft", "sum"),
                       eta_gs_acft=("eta_gs_acft", "sum"),
                       acres_ftr=("acres_ftr", "first"), acres_irr=("acres_irr", "first")))

    et0 = (pd.concat(et0_parts, ignore_index=True).groupby("OPENET_ID", as_index=False)
           .agg(eto_sum=("eto_sum", "sum"), eto_n=("eto_n", "sum")))
    et0["gs_eto_mm"] = et0["eto_sum"] / et0["eto_n"].replace(0, np.nan)
    et0 = et0[["OPENET_ID", "gs_eto_mm"]]

    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    field_year.to_parquet(FIELD_YEAR_ET_PARQUET, index=False)
    et0.to_parquet(FIELD_ET0_PARQUET, index=False)
    print(f"[subsidy] field-year rows: {len(field_year):,}; fields with ET0: {len(et0):,}")
    return field_year, et0


# --------------------------------------------------------------------------- #
# 2. Field parameters + groundwater fraction (per-pixel averaged)
# --------------------------------------------------------------------------- #
def _crop_by_field():
    import geopandas as gpd
    g = gpd.read_file(config.FIELDS_GPKG, columns=["OPENET_ID", "CROP_major"])
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

    _, et0 = aggregate_csvs()
    wtd = pd.read_parquet(config.FIELD_WTD_PARQUET)
    tex = pd.read_csv(config.FIELD_TEXTURE_CSV)
    crop = _crop_by_field()

    corr = wtd_correction.run()
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

    config.FIELD_PARAMS_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(config.FIELD_PARAMS_PARQUET, index=False)
    n_sub = (df["gwFrac_tan_crop"] > 0).sum()
    print(f"[subsidy] field params for {len(df):,} fields; {n_sub:,} with gwFrac_tan > 0")
    return df


# --------------------------------------------------------------------------- #
# 3. Subsidy = gwFrac x ET
# --------------------------------------------------------------------------- #
def compute_subsidy(force=False):
    """Field-year and period-of-record subsidy volumes for all models + diagnostic measures."""
    field_year, _ = aggregate_csvs()
    params = compute_field_params(force=force)

    fy = field_year.merge(
        params[["OPENET_ID", "texture", "crop_label", "is_non_crop", "root_depth_cm",
                "wtd_mean_m", "wtd_median_m", "wtd_min_m", "valid_frac",
                "d_dec_m", "d_ext_m", "p"] + [c for c in params.columns if c.startswith("gwFrac_")]],
        on="OPENET_ID", how="left")
    for c in [c for c in fy.columns if c.startswith("gwFrac_")]:
        fy[c] = fy[c].fillna(0.0)

    # Primary subsidy = f * ETa, per model (annual). Tan is the headline.
    for model in config.MODELS:
        fy[f"ETgw_{model}_acft"] = fy[f"gwFrac_{model}_crop"] * fy["eta_acft"]
    fy["ETgw_acft"] = fy["ETgw_tan_acft"]
    model_cols = [f"ETgw_{m}_acft" for m in config.MODELS]
    fy["ETgw_model_min_acft"] = fy[model_cols].min(axis=1)
    fy["ETgw_model_max_acft"] = fy[model_cols].max(axis=1)
    # Diagnostic measures (Tan fraction): min(f*ET0, ETa) bracket and f*NetET irrigation offset.
    fy["ETgw_capped_acft"] = np.minimum(fy["gwFrac_tan_crop"] * fy["eto_acft"], fy["eta_acft"])
    fy["ETgw_netet_acft"] = fy["gwFrac_tan_crop"] * fy["netet_acft"].clip(lower=0)
    fy["ETgw_share_of_eta"] = (fy["ETgw_acft"] / fy["eta_acft"]).replace([np.inf, -np.inf], np.nan)

    fy.to_csv(config.FIELD_YEAR_CSV, index=False)

    # Period-of-record mean annual per (field, water_source).
    keys = ["OPENET_ID", "water_source", "Basin", "texture", "crop_label", "is_non_crop",
            "wtd_mean_m", "wtd_median_m", "wtd_min_m", "d_dec_m", "d_ext_m", "p",
            "gwFrac_tan_crop", "gwFrac_banta_crop", "gwFrac_averianov_crop", "gwFrac_shah_crop",
            "gwFrac_tan_bare"]
    agg = {c: "mean" for c in ["eta_acft", "netet_acft", "ar_acft", "eta_gs_acft", "acres_ftr", "acres_irr",
                               "ETgw_acft", "ETgw_banta_acft", "ETgw_averianov_acft", "ETgw_shah_acft",
                               "ETgw_model_min_acft", "ETgw_model_max_acft",
                               "ETgw_capped_acft", "ETgw_netet_acft"]}
    agg["YEAR"] = "nunique"
    por = fy.groupby(keys, as_index=False).agg(agg).rename(columns={"YEAR": "n_years"})
    por.to_parquet(config.OUTPUT_DIR / "field_por_subsidy.parquet", index=False)
    tot = por["ETgw_acft"].sum()
    model_totals = {m: por[f"ETgw_{'acft' if m == 'tan' else m + '_acft'}"].sum() for m in config.MODELS}
    print(f"[subsidy] POR mean-annual GW-ET subsidy (all fields): {tot:,.0f} acre-ft/yr "
          f"[4-model range {min(model_totals.values()):,.0f}-{max(model_totals.values()):,.0f}]")
    return por


def run(force=False):
    return compute_subsidy(force=force)


if __name__ == "__main__":
    run()
