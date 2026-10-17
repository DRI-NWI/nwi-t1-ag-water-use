"""Per-field groundwater-ET fraction f (vendored subsidy.compute_field_params logic).

f = coverage-weighted mean over the field's DTW pixels of Tan et al. (2025) f(d) with
(d_dec, d_ext, p) from POLARIS texture, CDL rooting depth (CROP_major) and the field's
growing-season mean ETo (Apr-Oct). Fields with no shallow pixels get f = 0.

Eligibility is per field from its water right (every GW, SW or mixed field; non-NV
fields are assumed GW). The optional SW filter (gw_subsidy.sw_filter, off in the final
configuration; nwi-gw-subsidy basins.field_keep) sets f = 0 for pure-GW fields in
basins where SW or mixed fields hold less than sw_use_min_acre_frac of the acreage.
``sw_filter_keep`` records what that test would decide either way.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from . import constants as C, crops, models, soil_texture


def basin_id(basin_str):
    if pd.isna(basin_str):
        return None
    digits = re.sub(r"\D", "", str(basin_str).split("_")[0])
    return digits.zfill(3) if digits else None


def growing_season_eto(base: pd.DataFrame, months) -> pd.DataFrame:
    gs = base[base["MONTH"].isin(months)]
    e = gs.groupby("NWI_ID", as_index=False)["ETO_MM"].mean().rename(columns={"ETO_MM": "gs_eto_mm"})
    return e


def compute(attrs, wtd, pixels, texture, gs_eto, cfg):
    g = cfg["gw_subsidy"]
    soil_texture.TAN_PARAMS_CSV = cfg["inputs"]["tan_params_csv"]
    df = (attrs[["NWI_ID", "Basin", "STATE", "IRR_SRC", "Acres", "CROP_major"]]
          .merge(wtd, on="NWI_ID", how="left")
          .merge(texture[["NWI_ID", "sand", "silt", "clay", "texture"]], on="NWI_ID", how="left")
          .merge(gs_eto, on="NWI_ID", how="left"))
    df = crops.annotate(df, code_col="CROP_major")
    df["texture_assumed"] = df["texture"].isna()
    df["et0_mm_d"] = (df["gs_eto_mm"] / 30.4).fillna(C.ET0_DEFAULT_MM_D)
    p = soil_texture.assign_params(df.assign(texture=df["texture"].fillna("loam")), mode=g["param_mode"])
    df["d_dec_m"], df["d_ext_m"], df["p"] = p["d_dec_m"], p["d_ext_m"], p["p"]

    px = pixels.merge(df[["NWI_ID", "d_dec_m", "d_ext_m", "p"]], on="NWI_ID", how="inner")
    f = models.fraction_vec(px["wtd_m"].to_numpy(float), px["d_dec_m"].to_numpy(float),
                            px["d_ext_m"].to_numpy(float), px["p"].to_numpy(float), model=g["model"])
    w = px["weight"].to_numpy(float)
    agg = pd.DataFrame({"NWI_ID": px["NWI_ID"], "w": w, "wf": w * f}).groupby("NWI_ID").sum()
    df["gwFrac_raw"] = df["NWI_ID"].map(agg["wf"] / agg["w"].replace(0, np.nan)).fillna(0.0)

    df["basin_id"] = df["Basin"].map(basin_id)
    df["field_has_sw"] = df["IRR_SRC"].astype(float).isin([2, 3])
    acres = pd.to_numeric(df["Acres"], errors="coerce").fillna(0)
    b = (df.assign(a=acres, sa=acres.where(df["field_has_sw"], 0))
           .groupby("basin_id")[["a", "sa"]].sum())
    b["sw_acre_frac"] = b["sa"] / b["a"].clip(lower=1)
    df["basin_sw_acre_frac"] = df["basin_id"].map(b["sw_acre_frac"])
    df["basin_has_sw_use"] = df["basin_sw_acre_frac"] >= g["sw_use_min_acre_frac"]
    df["sw_filter_keep"] = df["field_has_sw"] | df["basin_has_sw_use"]
    df["gw_frac"] = np.where(df["sw_filter_keep"] | (not g.get("sw_filter", True)), df["gwFrac_raw"], 0.0)
    df["dtw_available"] = df["wtd_mean_m"].notna()
    return df
