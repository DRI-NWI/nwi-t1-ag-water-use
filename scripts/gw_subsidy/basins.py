"""Aggregate field subsidy to NV State Engineer hydrographic basins.

Joins the per-field period-of-record subsidy to the NV Division of Water Resources
basin polygons (perennial yield, groundwater reserve), flags shallow-water-table
basins (>= SHALLOW_AREA_FRAC of irrigated acreage with field WTD < extinction depth),
and expresses the groundwater-ET subsidy as a share of each basin's perennial yield.

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


def _parse_yield(series):
    """NV SE PerennialYield is a messy string ('13,000', '25000', blanks) -> numeric acre-ft."""
    return pd.to_numeric(series.astype(str).str.replace(",", "", regex=False)
                         .str.extract(r"(\d+\.?\d*)")[0], errors="coerce")


def _source_class(ws):
    if ws in ("GW",):
        return "GW"
    if ws in ("SW",):
        return "SW"
    return "mixed"  # GW_from_mixed / SW_from_mixed


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


def load_se_basins():
    import geopandas as gpd
    b = gpd.read_file(config.BASINS_GEOJSON)
    b["basin_id"] = b["BasinID"].astype(str).str.extract(r"(\d+)")[0].str.zfill(3)
    b["perennial_yield_acft"] = _parse_yield(b["PerennialYield"])
    # Sub-areas partition a basin -> sum their perennial yields and areas; union the geometry.
    # min_count=1 keeps NaN for basins whose yield is entirely blank.
    geom = b.dissolve(by="basin_id").geometry
    attrs = b.groupby("basin_id").agg(
        BasinName=("BasinName", "first"),
        HydroRegionName=("HydroRegionName", "first"),
        perennial_yield_acft=("perennial_yield_acft", lambda s: s.sum(min_count=1)),
        AreaSquareMiles=("AreaSquareMiles", "sum"))
    return gpd.GeoDataFrame(attrs.join(geom), geometry="geometry", crs=b.crs).reset_index()


def basin_total_applied_water():
    """POR-mean annual applied water (AR) summed over ALL irrigated fields in each basin.

    The denominator for 'subsidy as % of applied water' — the basin's entire irrigation, so the
    percentage is a true basin-wide offset (not restricted to the SW-filter-kept subset).
    """
    fy = pd.read_parquet(config.OUTPUT_DIR / "field_year_et.parquet")
    fy["basin_id"] = fy["Basin"].map(basin_id_from_field)
    # POR-mean annual AR per (field, water_source), then sum to the basin (allocation splits sum to the field total).
    per_field = fy.groupby(["basin_id", "OPENET_ID", "water_source"], as_index=False)["ar_acft"].mean()
    return (per_field.groupby("basin_id", as_index=False)["ar_acft"].sum()
            .rename(columns={"ar_acft": "basin_ar_acft"}))


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


def run(force=False):
    """Build basin-level POR summary with shallow flag and % of perennial yield."""
    por = pd.read_parquet(FIELD_POR_PARQUET)
    por["basin_id"] = por["Basin"].map(basin_id_from_field)
    por["source_class"] = por["water_source"].map(_source_class)

    # Field-level SW filter: keep SW/mixed fields + all fields in SW-use basins.
    keep = field_keep(por)
    por = por.merge(keep[["OPENET_ID", "keep"]], on="OPENET_ID", how="left")
    por["keep"] = por["keep"].fillna(False)
    n_drop = (~keep["keep"]).sum()
    kept = por[por["keep"]]

    # --- subsidy volumes over KEPT fields, aggregated to the 3-digit hydrographic basin
    # (sub-areas A/B/C share a BasinID and a perennial yield) ---
    vol_cols = ["ETgw_acft", "ETgw_banta_acft", "ETgw_averianov_acft", "ETgw_shah_acft",
                "ETgw_model_min_acft", "ETgw_model_max_acft",
                "ETgw_capped_acft", "ETgw_netet_acft", "eta_acft", "netet_acft", "ar_acft"]
    basin_vol = kept.groupby("basin_id", as_index=False).agg(
        {"Basin": "first", **{c: "sum" for c in vol_cols}})

    # subsidy split by source class (GW / SW / mixed)
    src = (kept.groupby(["basin_id", "source_class"], as_index=False)["ETgw_acft"].sum()
              .pivot(index="basin_id", columns="source_class", values="ETgw_acft")
              .add_prefix("ETgw_").add_suffix("_acft").reset_index())

    # --- area / shallow flag over kept unique fields ---
    fld = kept.sort_values("eta_acft", ascending=False).drop_duplicates("OPENET_ID").copy()
    fld["shallow"] = fld["wtd_mean_m"] < fld["d_ext_m"]
    fld["under_ddec"] = fld["wtd_mean_m"] < fld["d_dec_m"]
    area = fld.groupby("basin_id").apply(lambda g: pd.Series({
        "n_fields": len(g),
        "irr_acres": g["acres_irr"].sum(),
        "shallow_acres": g.loc[g["shallow"], "acres_irr"].sum(),
        "under_ddec_acres": g.loc[g["under_ddec"], "acres_irr"].sum(),
        "wtd_median_m": np.median(g["wtd_mean_m"].dropna()) if g["wtd_mean_m"].notna().any() else np.nan,
    }), include_groups=False).reset_index()
    area["shallow_frac"] = area["shallow_acres"] / area["irr_acres"].replace(0, np.nan)
    area["is_shallow_basin"] = area["shallow_frac"] >= config.SHALLOW_AREA_FRAC

    se = load_se_basins().drop(columns="geometry")
    out = (basin_vol.merge(src, on="basin_id", how="left")
                    .merge(area, on="basin_id", how="left")
                    .merge(sw_use_by_basin(por), on="basin_id", how="left")
                    .merge(se, on="basin_id", how="left"))
    out["has_sw_use"] = out["has_sw_use"].fillna(False)
    out = out.merge(basin_total_applied_water(), on="basin_id", how="left")
    out["subsidy_pct_perennial_yield"] = (
        100 * out["ETgw_acft"] / out["perennial_yield_acft"].replace(0, np.nan))
    out["subsidy_pct_of_eta"] = 100 * out["ETgw_acft"] / out["eta_acft"].replace(0, np.nan)
    # Headline basin comparison: ETgw as a share of net ET (consumptive use / crop water
    # requirement) -- the subsidy directly offsets the net ET the crop would otherwise draw from
    # irrigation. Note this is identical to "subsidy as % of applied water": applied water = net
    # ET / irrigation efficiency, so dividing the subsidy by IE to express it in applied-water
    # terms cancels the IE in the denominator and collapses back to ETgw / net ET. `basin_ar_acft`
    # (basin-wide applied water) is retained in the CSV as a reference volume.
    out["subsidy_pct_of_netet"] = 100 * out["ETgw_acft"] / out["netet_acft"].replace(0, np.nan)
    out = out.sort_values("ETgw_acft", ascending=False)

    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(config.BASIN_SUMMARY_CSV, index=False)

    shallow = out[out["is_shallow_basin"] == True]  # noqa: E712
    print(f"[basins] {len(out)} basins; dropped {n_drop:,} pure-GW fields in low-SW basins; "
          f"GW-ET subsidy {out['ETgw_acft'].sum():,.0f} acre-ft/yr "
          f"({len(shallow)} shallow basins {shallow['ETgw_acft'].sum():,.0f})")
    return out


if __name__ == "__main__":
    run()
