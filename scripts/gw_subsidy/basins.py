"""Aggregate field subsidy to NV State Engineer hydrographic basins.

Joins the per-field period-of-record subsidy to the NV Division of Water Resources
basin polygons (perennial yield, groundwater reserve), flags shallow-water-table
basins (>= SHALLOW_AREA_FRAC of irrigated acreage with field WTD < extinction depth),
and expresses the groundwater-ET subsidy as a share of each basin's perennial yield.

NWI T1: only the SW-use basin test is used (basin_id_from_field, sw_use_by_basin,
field_keep, and field_keep_from_attributes, which feeds it from the boundary layer). The
basin aggregation and perennial-yield functions are removed; HA summaries are made by
process_field_summaries.py.

Mixed-source fields appear once per water source; ET volumes are an allocation split
(summing across sources recovers the field total, no double count), but field acreage
is repeated -- so area denominators are de-duplicated to unique fields first.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from . import config

FIELD_POR_PARQUET = config.OUTPUT_DIR / "field_por_subsidy.parquet"


def basin_id_from_field(basin_str):
    """'101_CarsonDesert' / '033A_QuinnRiverValley' -> zero-padded 3-digit BasinID '101' / '033'."""
    if pd.isna(basin_str):
        return None
    head = str(basin_str).split("_")[0]
    digits = re.sub(r"\D", "", head)
    return digits.zfill(3) if digits else None


def sw_use_by_basin(por):
    """Per-basin surface-water-use fraction over the analyzed (subsidy) fields.

    A field is SW or mixed if any of its water-balance records is not pure groundwater
    (water_source != 'GW'); the fraction is acreage-weighted. Basins below the threshold are
    predominantly groundwater (closed/pumped) -- excluded.
    """
    f = por.groupby("OPENET_ID", as_index=False).agg(
        basin_id=("basin_id", "first"), acres=("acres_ftr", "first"),
        has_sw=("water_source", lambda s: (s != "GW").any()))
    a = f.groupby("basin_id", as_index=False).apply(
        lambda x: pd.Series({"sw_acres": x.loc[x["has_sw"], "acres"].sum(),
                             "tot_acres": x["acres"].sum()}), include_groups=False)
    a["sw_acre_frac"] = a["sw_acres"] / a["tot_acres"].clip(lower=1)
    a["has_sw_use"] = a["sw_acre_frac"] >= config.SW_USE_MIN_ACRE_FRAC
    return a[["basin_id", "sw_acre_frac", "has_sw_use"]]


def field_keep(por):
    """Per-field keep flag (field-level SW filter).

    Keep every surface-water or mixed field (any record not pure 'GW'), and every field in a
    surface-water-use basin. Drop pure-groundwater fields that sit in predominantly-groundwater
    basins -- those are the deep/pumped zones where the continental-scale ML WTD product
    under-resolves the drawdown cone (reads too shallow); the SW/mixed fields cluster in the
    shallow, water-available valley bottoms.
    """
    fhs = (por.groupby("OPENET_ID")["water_source"].apply(lambda s: (s != "GW").any())
              .rename("field_has_sw").reset_index())
    bsw = sw_use_by_basin(por)[["basin_id", "has_sw_use"]]
    f = (por[["OPENET_ID", "basin_id"]].drop_duplicates("OPENET_ID")
            .merge(fhs, on="OPENET_ID", how="left").merge(bsw, on="basin_id", how="left"))
    f["field_has_sw"] = f["field_has_sw"].fillna(False)
    f["has_sw_use"] = f["has_sw_use"].fillna(False)
    f["keep"] = f["field_has_sw"] | f["has_sw_use"]
    return f[["OPENET_ID", "keep", "field_has_sw"]]


def field_keep_from_attributes():
    """
    NWI T1: field_keep() evaluated on the boundary layer instead of the field-summary records.

    A field counts as SW or mixed when its water-right source (after the fallbacks and basin
    rules of process_field_summaries.py) is SW or mixed; acreage is the boundary Acres.

    Returns
    -------
    pd.DataFrame
        OPENET_ID, field_has_sw, has_sw_use, keep.
    """
    import geopandas as gpd
    g = gpd.read_file(config.FIELDS_GPKG, columns=["NWI_ID", "Basin", "Acres", "IRR_SRC"], ignore_geometry=True)
    g = g.rename(columns={"NWI_ID": "OPENET_ID"})
    rules = config.RUN_CFG["allocation"]
    irr_src = g["IRR_SRC"].fillna(config.RUN_CFG["field_attributes"]["assumed_irr_src"])
    if rules.get("apply_static_overrides", True):
        for code, gw_ratio in rules["static_overrides"].items():
            in_basin = g["Basin"].map(basin_id_from_field).eq(str(code).zfill(3)) & irr_src.eq(3)
            irr_src = irr_src.mask(in_basin, 1 if float(gw_ratio) == 1.0 else 2)
    por = pd.DataFrame({"OPENET_ID": g["OPENET_ID"], "basin_id": g["Basin"].map(basin_id_from_field),
                        "acres_ftr": g["Acres"], "water_source": np.where(irr_src.eq(1), "GW", "SW_or_mixed")})
    return field_keep(por)
