"""Field-level output columns and HA / county summaries (column policy in docs/COLUMN_POLICY.md).

The delivered processor summed every numeric column and took ``first()`` of every
text column when aggregating, so HA/county rows carried sums of per-field attributes
(irr_eff, AWC, EToF, SPI, allocation ratio, %_IRRIGATED ...) and one arbitrary field's
IDs. Here every summary column is either an additive volume or acreage (sum), a count,
a depth or rate recomputed from summed volumes, an acre-weighted mean, or a
basin-year constant (HA only). Nothing else is carried.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .water_balance import VOLUME_COLUMNS

FIELD_COLUMNS = [
    # identity and attributes (field level only)
    "NWI_ID", "NWI_PARENT", "STATE", "COUNTY_NAME", "HYD_AREA", "HYD_AREA_N", "HA_NAME_CODE", "GRIDMET_ID",
    "IRR_SRC", "IRR_SRC_ORIGINAL", "IRR_SRC_ASSUMED", "IRR_SRC_OVERRIDE", "irr_eff", "ITYPE", "CROP_major",
    "YEAR", "MONTH", "DATE", "water_source", "allocation_method", "allocation_ratio", "spi_value", "spi_source",
    # areas
    "ACRES_FTR_GEOM", "ACRES_IRR", "%_IRRIGATED",
    # monthly depths (mm)
    "ETO_MM", "ETA_MM_EXPORT", "EToF", "EToF_interpolated", "interpolation_method", "ETA_MM_post_interp",
    "PPT_MM", "PEFF_MM", "peff_method",
    # groundwater subsidy and soil terms
    "gw_frac", "AWC", "PAW_ft",
    # volumes (acre-ft)
    "ETa_final_acre_ft", "PPT_vol_acre_ft", "Peff_vol_acre_ft", "NetET_pre_subsidy_acre_ft",
    "GW_subsidy_potential_acre_ft", "GW_subsidy_acre_ft", "NetET_intermediate_acre_ft",
    "Soil_Moisture_Cap_acre_ft", "Soil_Storage_acre_ft", "Soil_Moisture_Overflow_acre_ft",
    "Carryforward_Used_acre_ft", "NetET_final_acre_ft", "AR_final_acre_ft",
    # depths (ft)
    "ETa_final_ft", "NetET_final_ft", "AR_final_ft", "PPT_final_ft",
]

WEIGHTED_MM = ["ETO_MM", "PPT_MM", "PEFF_MM"]


def field_output(df: pd.DataFrame) -> pd.DataFrame:
    return df[[c for c in FIELD_COLUMNS if c in df.columns]]


def summarize(df: pd.DataFrame, keys: list[str], basin_year: pd.DataFrame | None = None) -> pd.DataFrame:
    d = df
    w = d["ACRES_FTR_GEOM"]
    tmp = pd.DataFrame({k: d[k] for k in keys})
    for c in VOLUME_COLUMNS:
        if c in d.columns:
            tmp[c] = d[c]
    for c in WEIGHTED_MM:
        tmp[f"_{c}_w"] = d[c] * w
        tmp[f"_{c}_a"] = w.where(d[c].notna(), 0)
    src = d["IRR_SRC"].astype(float)
    tmp["n_fields"] = 1
    tmp["n_gw_fields"] = (src == 1).astype(int)
    tmp["n_sw_fields"] = (src == 2).astype(int)
    tmp["n_mixed_fields"] = (src == 3).astype(int)
    tmp["n_fields_irr_src_assumed"] = d["IRR_SRC_ASSUMED"].astype(bool).astype(int)
    tmp["n_fields_eta_interpolated"] = d["interpolation_method"].eq("linear_1month").astype(int)
    tmp["n_fields_eta_climatology"] = d["interpolation_method"].eq("climatology").astype(int)
    tmp["n_fields_eta_missing"] = d["interpolation_method"].eq("unfilled_missing").astype(int)
    tmp["n_fields_peff_fallback"] = d["peff_method"].ne("otter").astype(int) if "peff_method" in d else 0
    tmp["n_fields_gw_subsidy"] = (d["gw_frac"] > 0).astype(int)
    tmp["_acres_missing_eta"] = w.where(d["ETa_final_acre_ft"].isna(), 0)
    if "sw_share" in d.columns:
        tmp["_gw_equiv"] = np.select([src == 1, src == 2], [1.0, 0.0], 1.0 - d["sw_share"])
    g = tmp.groupby(keys, as_index=False, sort=True).sum(min_count=1)
    for c in VOLUME_COLUMNS:
        if c in g.columns and c not in ("ACRES_FTR_GEOM", "ACRES_IRR"):
            g[c] = g[c].fillna(0.0)
    a = g["ACRES_FTR_GEOM"].replace(0, np.nan)
    for c in WEIGHTED_MM:
        g[c] = g[f"_{c}_w"] / g[f"_{c}_a"].replace(0, np.nan)
    g["ETa_final_ft"] = g["ETa_final_acre_ft"] / a
    g["NetET_final_ft"] = g["NetET_final_acre_ft"] / a
    g["AR_final_ft"] = g["AR_final_acre_ft"] / a
    g["PPT_final_ft"] = g["PPT_vol_acre_ft"] / a
    g["GW_subsidy_ft"] = g["GW_subsidy_acre_ft"] / a
    g["irr_eff_effective"] = g["NetET_final_acre_ft"] / g["AR_final_acre_ft"].replace(0, np.nan)
    g["pct_acres_missing_eta"] = 100 * g["_acres_missing_eta"] / a
    if "_gw_equiv" in g.columns:
        g["pct_fields_gw_equivalent"] = 100 * g["_gw_equiv"] / g["n_fields"]
        g["pct_fields_sw_equivalent"] = 100 - g["pct_fields_gw_equivalent"]
    g = g.drop(columns=[c for c in g.columns if c.startswith("_")])
    if basin_year is not None:
        g = g.merge(basin_year, on=[k for k in ("HYD_AREA", "YEAR") if k in keys], how="left")
    return g


HA_KEYS = ["HA_NAME_CODE", "HYD_AREA", "HYD_AREA_N", "YEAR", "MONTH"]
COUNTY_KEYS = ["COUNTY_NAME", "YEAR", "MONTH"]
