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
from nwi_agwu import aggregate, allocation, config as C, etof, fields, water_balance


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
interp_before = df["interpolation_method"].value_counts().to_dict()
if cfg["etof"].get("climatology_fill", False):
    windows = [tuple(w) for w in cfg["etof"]["climatology_windows"]]
    df = etof.apply_climatology_fill(df, etof.load_climatologies(P.etof_climatology, windows), windows)

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
checks["etof_climatology_fill"] = bool(cfg["etof"].get("climatology_fill", False))
checks["interpolation_method_before_climatology"] = {k: int(v) for k, v in interp_before.items()}
checks["interpolation_method"] = {k: int(v) for k, v in a["interpolation_method"].value_counts().items()}
checks["unfilled_missing_by_year"] = {int(k): int(v) for k, v in
                                      a.loc[a.interpolation_method.eq("unfilled_missing"), "YEAR"].value_counts().sort_index().items()}
# Carry-forward chains: a missing NetET month ends the chain; the next valid month restarts
# storage at zero even when it is not the January reset.
net_ok = a["NetET_intermediate_acre_ft"].notna().to_numpy()
first = np.r_[True, a["NWI_ID"].to_numpy()[1:] != a["NWI_ID"].to_numpy()[:-1]]
prev_ok = np.r_[True, net_ok[:-1]] | first
forced = net_ok & ~prev_ok & (a["MONTH"].to_numpy() != cfg["carryforward"]["reset_month"])
checks["carryforward_chains"] = {"netet_missing_months": int((~net_ok).sum()),
                                 "forced_restarts_non_reset_month": int(forced.sum()),
                                 "fields_with_forced_restart": int(a.loc[forced, "NWI_ID"].nunique())}
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
STATES = {"ACRES_FTR_GEOM", "ACRES_IRR", "Soil_Moisture_Cap_acre_ft", "Soil_Storage_acre_ft"}  # not additive over time
FLUX = [c for c in water_balance.VOLUME_COLUMNS if c not in STATES]
# HA/county summaries are irrigation accounting: only irrigated field-years (IrrMapper
# %_IRRIGATED above the threshold) are summed. Field files keep every field-month.
sm = cfg["summaries"]


def summary_rows(d):
    if not sm.get("irrigated_only", False):
        return d
    return d[d["%_IRRIGATED"] > float(sm["irrigated_min_fraction"])]


checks["summaries_irrigated_only"] = bool(sm.get("irrigated_only", False))
all_s = summary_rows(comps["ALL"])
nv_all = all_s[all_s["IN_NV_COUNTY"].fillna(False).astype(bool)]
pct_ha = aggregate.summarize(all_s, aggregate.HA_KEYS)[aggregate.HA_KEYS + PCT]
pct_cty = aggregate.summarize(nv_all, aggregate.COUNTY_KEYS)[aggregate.COUNTY_KEYS + PCT]
for comp in ["ALL", "GW", "SW"]:
    full = comps[comp]
    # Statewide totals for both populations, straight from the field rows.
    irr = full["%_IRRIGATED"] > float(sm["irrigated_min_fraction"])
    for pop, rows in (("all_field_months", full), ("irrigated_field_months", full[irr])):
        yr = rows.groupby("YEAR")[FLUX].sum()
        yr.insert(0, "population", pop)
        yr.insert(0, "component", comp)
        annual.append(yr.reset_index())
    d = summary_rows(full)
    ha = aggregate.summarize(d, aggregate.HA_KEYS, basin_year, state_split=True).drop(columns=PCT)
    ha = ha.merge(pct_ha, on=aggregate.HA_KEYS, how="left")
    ha.to_csv(out_sum / f"ha_summaries_{comp}.csv", index=False)
    nv = d[d["IN_NV_COUNTY"].fillna(False).astype(bool)]
    cty = aggregate.summarize(nv, aggregate.COUNTY_KEYS).drop(columns=PCT)
    cty = cty.merge(pct_cty, on=aggregate.COUNTY_KEYS, how="left")
    cty.to_csv(out_sum / f"county_summaries_{comp}.csv", index=False)
    if variant == "v4_final" and not args.no_field_files:
        f = aggregate.field_output(full)
        f.to_parquet(P.fields / f"field_summaries_{comp}.parquet", index=False)
        if cfg["outputs"]["write_field_csv"]:
            f.to_csv(P.fields / f"field_summaries_{comp}.csv", index=False)
    print(f"[05] {variant} {comp}: {len(full):,} field rows, {len(d):,} in summaries ({time.time() - t0:.0f}s)", flush=True)
pd.concat(annual).to_csv(out_sum / "statewide_annual_totals.csv", index=False)
write_json(checks, out_sum / "run_checks.json")
print(f"[05] {variant} done in {time.time() - t0:.0f}s")
