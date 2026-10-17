#!/usr/bin/env python3
"""Step 05: field water balance, GW subsidy, winter carry-forward, SW/GW allocation, summaries.

--variant NAME applies an override set from config/bridge_variants.yaml (bridge runs write
HA/county summaries and statewide totals only). Without --variant the final configuration
runs and writes field-level ALL / GW / SW outputs as well.

Outputs (final): run/05_field_summaries/field_summaries_{ALL,GW,SW}.{csv,parquet}
                 run/06_summaries/{ha,county}_summaries_{ALL,GW,SW}.csv, allocation_basin_year.csv
"""

import time

import numpy as np
import pandas as pd
import yaml

from _common import REPO, parse, write_json
from nwi_agwu import aggregate, allocation, config as C, fields, water_balance


def extra(ap):
    ap.add_argument("--variant", default=None)
    ap.add_argument("--no-field-files", action="store_true")


args, cfg, P = parse(__doc__, extra)
variant = args.variant or "v4_final"
if args.variant:
    v = yaml.safe_load(open(REPO / "config" / "bridge_variants.yaml"))["variants"][args.variant]
    cfg = C.deep_update(cfg, v)
out_sum = P.summaries if variant == "v4_final" else P.variants / variant
out_sum.mkdir(parents=True, exist_ok=True)
t0 = time.time()
R = cfg["run"]
years = list(range(R["start_year"], R["end_year"] + 1))

# ---- attributes, overrides, county, AWC ------------------------------------------------
attrs = pd.read_parquet(P.field_attrs)
if cfg["allocation"].get("apply_static_overrides", True):
    attrs = fields.apply_static_overrides(attrs, cfg["allocation"]["static_overrides"])
else:
    attrs["IRR_SRC_OVERRIDE"] = ""
county = fields.load_county_assignment(cfg["inputs"]["county_assignment"])
attrs = attrs.merge(county[["NWI_ID", "COUNTY_NAME", "IN_NV_COUNTY"]], on="NWI_ID", how="left")
awc = pd.read_csv(cfg["inputs"]["awc_table"])
attrs["AWC"] = attrs["NWI_ID"].map(awc.set_index("NWI_ID")["AWC"])
if cfg["carryforward"]["enabled"] and attrs["AWC"].isna().any():
    raise ValueError(f"{int(attrs.AWC.isna().sum())} fields lack AWC")

# ---- field-month table ----------------------------------------------------------------
base = pd.read_parquet(P.field_base)
keep_attr = ["NWI_ID", "NWI_PARENT", "HYD_AREA", "HYD_AREA_N", "HA_NAME_CODE", "HA_CODE", "GRIDMET_ID", "IRR_SRC",
             "IRR_SRC_ORIGINAL", "IRR_SRC_ASSUMED", "IRR_SRC_OVERRIDE", "irr_eff", "ITYPE", "CROP_major",
             "COUNTY_NAME", "IN_NV_COUNTY", "AWC"]
df = base.merge(attrs[keep_attr], on="NWI_ID", how="left", validate="many_to_one")
df = df.sort_values(["NWI_ID", "YEAR", "MONTH"], kind="mergesort").reset_index(drop=True)
df["DATE"] = pd.to_datetime(dict(year=df.YEAR, month=df.MONTH, day=1)).dt.strftime("%Y-%m-%d")

# Precipitation and effective precipitation
if cfg["precipitation"]["source"] == "prism":
    df["PPT_MM"] = df["PPT_PRISM_MM"]
elif cfg["precipitation"]["source"] != "export":
    raise ValueError("precipitation.source must be prism or export")
ep = cfg["effective_precipitation"]["source"]
if ep == "otter":
    o = pd.read_parquet(P.otter_monthly, columns=["NWI_ID", "YEAR", "MONTH", "OTTER_PEFF_MM", "OTTER_STATUS"])
    o["YEAR"] = o["YEAR"].astype(int); o["MONTH"] = o["MONTH"].astype(int)
    df = df.merge(o, on=["NWI_ID", "YEAR", "MONTH"], how="left", validate="one_to_one")
    ok = df["OTTER_STATUS"].eq("eligible") & df["OTTER_PEFF_MM"].notna()
    peff = np.where(ok, df["OTTER_PEFF_MM"].clip(lower=0), 0.85 * df["PPT_MM"])
    df["peff_method"] = np.where(ok, "otter", "ppt_085_fallback")
    df = df.drop(columns=["OTTER_PEFF_MM", "OTTER_STATUS"])
elif ep == "export_085":
    peff = df["EFF_PPT_MM"].to_numpy()
    df["peff_method"] = "export_085"
else:
    raise ValueError("effective_precipitation.source must be otter or export_085")

gwf = None
if cfg["gw_subsidy"]["enabled"]:
    gf = pd.read_parquet(P.field_gwfrac, columns=["NWI_ID", "gw_frac"]).set_index("NWI_ID")["gw_frac"]
    gwf = df["NWI_ID"].map(gf).fillna(0.0)

df = water_balance.compute(df, cfg, pd.Series(peff), gwf)

# ---- allocation ------------------------------------------------------------------------
spi_tab = pd.read_csv(P.spi_table if cfg["spi"]["source"] == "prism_d24" else P.spi / "basin_year_spi_legacy.csv",
                      dtype={"HA_CODE": str})
mixed_codes = attrs.loc[attrs["IRR_SRC"].eq(3), "HA_CODE"].dropna().unique()
ratios = allocation.basin_year_ratios(mixed_codes, years, spi_tab, cfg)
ratios.to_csv(out_sum / "allocation_basin_year.csv", index=False)
share = allocation.field_year_sw_share(attrs, ratios, years)
comps = water_balance.split_components(df, share)
del df

# ---- checks ------------------------------------------------------------------------------
checks = {"variant": variant, "config": {k: cfg[k] for k in ("precipitation", "effective_precipitation", "spi",
                                                               "allocation", "gw_subsidy", "carryforward")}}
for c in ["ETa_final_acre_ft", "NetET_final_acre_ft", "AR_final_acre_ft", "GW_subsidy_acre_ft", "ACRES_FTR_GEOM"]:
    tot = {k: float(v[c].sum()) for k, v in comps.items()}
    checks[f"closure_{c}"] = {**tot, "rel_err_gw_plus_sw_vs_all": (tot["GW"] + tot["SW"] - tot["ALL"]) / tot["ALL"] if tot["ALL"] else 0}
    if tot["ALL"] and abs((tot["GW"] + tot["SW"] - tot["ALL"]) / tot["ALL"]) > 1e-9:
        raise AssertionError(f"GW + SW != ALL for {c}")
a = comps["ALL"]
checks["fields"] = int(a.NWI_ID.nunique())
checks["peff_method_fields"] = a.groupby("peff_method")["NWI_ID"].nunique().to_dict()
checks["spi_missing_basin_years"] = int(ratios["spi_status"].eq("missing").sum())

# ---- outputs -----------------------------------------------------------------------------
basin_year = ratios.rename(columns={"sw_ratio": "mixed_sw_ratio", "spi_value": "spi_sept12"})
code_to_ha = attrs.drop_duplicates("HA_CODE").set_index("HA_CODE")["HYD_AREA"]
basin_year["HYD_AREA"] = basin_year["HA_CODE"].map(code_to_ha)
basin_year = basin_year[["HYD_AREA", "YEAR", "mixed_sw_ratio", "spi_sept12", "spi_source", "allocation_method"]]
annual = []
# Field-share percentages describe all fields in the unit, so they come from ALL.
PCT = ["pct_fields_gw_equivalent", "pct_fields_sw_equivalent"]
STATES = {"ACRES_FTR_GEOM", "Soil_Moisture_Cap_acre_ft", "Soil_Storage_acre_ft"}  # not additive over time
nv_all = comps["ALL"][comps["ALL"]["IN_NV_COUNTY"].fillna(False).astype(bool)]
pct_ha = aggregate.summarize(comps["ALL"], aggregate.HA_KEYS)[aggregate.HA_KEYS + PCT]
pct_cty = aggregate.summarize(nv_all, aggregate.COUNTY_KEYS)[aggregate.COUNTY_KEYS + PCT]
for comp in ["ALL", "GW", "SW"]:
    d = comps[comp]
    ha = aggregate.summarize(d, aggregate.HA_KEYS, basin_year).drop(columns=PCT)
    ha = ha.merge(pct_ha, on=aggregate.HA_KEYS, how="left")
    ha.to_csv(out_sum / f"ha_summaries_{comp}.csv", index=False)
    nv = d[d["IN_NV_COUNTY"].fillna(False).astype(bool)]
    cty = aggregate.summarize(nv, aggregate.COUNTY_KEYS).drop(columns=PCT)
    cty = cty.merge(pct_cty, on=aggregate.COUNTY_KEYS, how="left")
    cty.to_csv(out_sum / f"county_summaries_{comp}.csv", index=False)
    yr = ha.groupby("YEAR")[[c for c in water_balance.VOLUME_COLUMNS if c in ha.columns and c not in STATES]].sum()
    yr["ACRES_FTR_GEOM_mean_month"] = ha.groupby(["YEAR", "MONTH"])["ACRES_FTR_GEOM"].sum().groupby("YEAR").mean()
    yr.insert(0, "component", comp)
    annual.append(yr.reset_index())
    if variant == "v4_final" and not args.no_field_files:
        f = aggregate.field_output(d)
        f.to_parquet(P.fields / f"field_summaries_{comp}.parquet", index=False)
        if cfg["outputs"]["write_field_csv"]:
            f.to_csv(P.fields / f"field_summaries_{comp}.csv", index=False)
    print(f"[05] {variant} {comp}: {len(d):,} rows written ({time.time() - t0:.0f}s)", flush=True)
pd.concat(annual).to_csv(out_sum / "statewide_annual_totals.csv", index=False)
write_json(checks, out_sum / "run_checks.json")
print(f"[05] {variant} done in {time.time() - t0:.0f}s")
