#!/usr/bin/env python3
"""
GEE app input 6: the tables the app reads, and the checks against the report.

Builds, from steps 1-5 (under --out/app):

  fields_gw_subsidy.{parquet,csv}      one row per field: polygon (WKT, EPSG:4326) + attributes
  field_year_gw_subsidy.{parquet,csv}  one row per field-year: irrigated flag, ETa, subsidy
  ha_gw_subsidy.{parquet,csv}          one row per HA: polygon + totals
  ha_year_gw_subsidy.{parquet,csv}     one row per HA-year (irrigated field-years)
  wells_train.csv, wells_gwx_nv.csv    wells (points)
  app_summary.json                     totals and every check

f per field (F_<MAP>_<MODEL>; MAP = NEW or MA; MODEL = TAN, BANTA, AVER, SHAH):
  * F_NEW_TAN is gw_frac from the run's field file: the values in the report.
  * All other f come from step 4 (--params-new, --params-ma): the run's own code applied to the
    2026-10-08 DTW map and to Ma et al. on the same 100 m grid, for the same 2026-08-20 fields.
    F_NEW_TANR is Tan recomputed the same way; its difference from F_NEW_TAN shows how far the
    recomputed values can be from what the run's inputs would give. Passing the run's own
    04_gw_subsidy/field_params.parquet and ma_comparison/field_params.parquet as --params-new and
    --params-ma makes every f the run's.

Subsidy (S_<MAP>_<MODEL>, acre-ft/yr): month by month min(f x ETa, max(NetET_pre_subsidy, 0)) as in
the run, summed over irrigated field-years (%_IRRIGATED > 0.5) and divided by the 41 years.
D_<MODEL> = S_NEW_<MODEL> - S_MA_<MODEL>.

Checks that stop the script: the Tan subsidy with the new DTW equals the run's GW_subsidy_acre_ft in
every field-month, and the statewide and HA totals equal README.md and
docs/tables/ha_gw_subsidy_new_vs_ma.csv. Comparisons with the report's Ma et al. numbers are recorded
in app_summary.json and shown in the app.

Usage:
    python 06_app_tables.py [--out DIR] [--params-new PARQUET] [--params-ma PARQUET]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from _common import NY, REPO, START_YEAR, out_dir

MODELS = {"TAN": "tan", "BANTA": "banta", "AVER": "averianov", "SHAH": "shah"}
HA_TABLE = REPO / "docs" / "tables" / "ha_gw_subsidy_new_vs_ma.csv"
SRC_LABEL = {1: "GW", 2: "SW", 3: "Mixed"}
# Report values (README.md, docs/GW_SUBSIDY.md, Fig. 11 title)
REPORT = {"subsidy_total": 34_733, "eta_total": 1_423_458, "n_covered": 16_225, "covered_new": 24_184,
          "covered_ma": 18_563, "median_dtw_new_fig11": 7.2, "median_dtw_ma_fig11": 7.4, "r_log": 0.68}


def check(name, ok, detail=""):
    print(f"  [{'ok' if ok else 'FAIL'}] {name}{(': ' + detail) if detail else ''}")
    if not ok:
        raise SystemExit(f"check failed: {name}")


def note(summary, name, got, report):
    """Record a comparison with the report that is not required to match exactly."""
    summary["vs_report"][name] = {"app": got, "report": report}
    print(f"  [info] {name}: app {got:,.3f} vs report {report:,}" if isinstance(got, float)
          else f"  [info] {name}: app {got} vs report {report}")


def polygonal(geom):
    if geom is None or geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    parts = [p for p in shapely.get_parts(geom) if p.geom_type in ("Polygon", "MultiPolygon")]
    return shapely.union_all(parts)


def wkt_csv(gdf, path):
    df = pd.DataFrame(gdf.drop(columns="geometry"))
    df["geometry"] = gdf.geometry.to_wkt(rounding_precision=-1).to_numpy()
    df.to_csv(path, index=False, float_format="%.10g")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=None)
    ap.add_argument("--params-new", type=Path, default=None, help="default <out>/field_params_new.parquet")
    ap.add_argument("--params-ma", type=Path, default=None, help="default <out>/field_params_ma.parquet")
    args = ap.parse_args()
    out = out_dir(args.out)
    app = out / "app"
    app.mkdir(exist_ok=True)
    import nwi_config
    nwi_config.RunPaths.make = lambda self: self
    from gw_subsidy import crops
    fx = json.loads((out / "04_field_fractions_checks.json").read_text())
    summary = {"ma_candidate": fx["ma_best"], "params_new": str(args.params_new or out / "field_params_new.parquet"),
               "params_ma": str(args.params_ma or out / "field_params_ma.parquet"), "vs_report": {}}

    st = pd.read_parquet(out / "field_static.parquet").set_index("NWI_ID")
    geo = gpd.read_parquet(out / "fields_20260820.parquet").set_index("NWI_ID")
    pn = pd.read_parquet(summary["params_new"]).set_index("OPENET_ID")
    pm = pd.read_parquet(summary["params_ma"]).set_index("OPENET_ID")
    wl = pd.read_parquet(out / "field_wells.parquet").set_index("NWI_ID")
    ids = st.index.sort_values()

    f = {("NEW", "TAN"): st["gw_frac"].reindex(ids)}                    # the run's f
    f[("NEW", "TANR")] = pn["gwFrac_tan_crop"].reindex(ids).fillna(0.0)  # recomputed, for QA
    for k, m in MODELS.items():
        if k != "TAN":
            f[("NEW", k)] = pn[f"gwFrac_{m}_crop"].reindex(ids).fillna(0.0)
        f[("MA", k)] = pm[f"gwFrac_{m}_crop"].reindex(ids).fillna(0.0)

    # ---- monthly subsidy for every variant -> field-year ---------------------------------------
    mc = pd.read_parquet(out / "field_month_core.parquet")
    code = pd.Categorical(mc["NWI_ID"], categories=ids).codes.astype(np.int64)   # int16 codes would overflow
    assert (code >= 0).all()
    key = code * NY + (mc["YEAR"].to_numpy() - START_YEAR)
    nkey = len(ids) * NY
    eta = mc["ETa_final_acre_ft"].to_numpy(float)
    netpos = np.clip(mc["NetET_pre_subsidy_acre_ft"].to_numpy(float), 0, None)
    run_sub = mc["GW_subsidy_acre_ft"].to_numpy(float)

    print("Field-month subsidy")
    fy = pd.DataFrame({"NWI_ID": np.repeat(ids.to_numpy(), NY),
                       "YEAR": np.tile(np.arange(START_YEAR, START_YEAR + NY), len(ids))})
    fy["IRR"] = (np.bincount(key, weights=mc["irrigated"].to_numpy(float), minlength=nkey) > 0).astype(int)
    # The 1,194 field-months with no ETa have NaN volumes; like the run's pandas sums, they add 0.
    fy["ETA_AF"] = np.bincount(key, weights=np.nan_to_num(eta), minlength=nkey)
    for (mp, k), fv in f.items():
        sub = np.minimum(fv.to_numpy()[code] * eta, netpos)
        if (mp, k) == ("NEW", "TAN"):
            same_nan = np.array_equal(np.isnan(sub), np.isnan(run_sub))
            err = np.nanmax(np.abs(sub - run_sub))
            check("Tan, new DTW: subsidy = run GW_subsidy_acre_ft in every field-month", same_nan and err < 1e-9,
                  f"max |diff| {err:.1e}; NaN months identical: {same_nan}")
        fy[f"S_{mp}_{k}"] = np.bincount(key, weights=np.nan_to_num(sub), minlength=nkey)
    irr_y = fy["IRR"].to_numpy(bool)
    row_field = np.repeat(np.arange(len(ids)), NY)

    def field_mean(col):
        return np.bincount(row_field, weights=np.where(irr_y, fy[col], 0.0), minlength=len(ids)) / NY

    # ---- field table ----------------------------------------------------------------------------
    fld = pd.DataFrame(index=ids)
    fld.index.name = "NWI_ID"
    fld["STATE"] = st["STATE"]
    fld["HYD_AREA"] = st["HYD_AREA"]
    fld["HA_NAME"] = st["HA_NAME_CODE"].str.replace(r"\s*\([^)]*\)\s*$", "", regex=True)
    fld["IRR_SRC"] = st["IRR_SRC"].astype("Int64")
    fld["SRC"] = fld["IRR_SRC"].map(SRC_LABEL)
    fld["SRC_ASSUMED"] = st["IRR_SRC_ASSUMED"].astype(str).str.lower().eq("true").astype(int)
    fld["CROP"] = st["CROP_major"].astype("Int64")
    fld["CROP_LBL"] = st["CROP_major"].map(lambda c: crops.CDL_LABELS.get(int(c), f"CDL {int(c)}")
                                           if pd.notna(c) else "Unknown")
    fld["ACRES"] = st["ACRES_FTR_GEOM"]
    fld["N_IRR_YRS"] = st["n_irrigated_years"].astype(int)
    for col, src in (("TEXTURE", "texture"), ("ROOT_CM", "root_depth_cm"), ("ET0_MMD", "et0_mm_d"),
                     ("DDEC_M", "d_dec_m"), ("DEXT_M", "d_ext_m"), ("P", "p"),
                     ("DTW_NEW_M", "wtd_mean_m"), ("DTW_NEW_MIN_M", "wtd_min_m"), ("DTW_SIG_M", "dtw_sigma_mean_m")):
        fld[col] = pn[src].reindex(ids)
    # Ma et al. used every field's own POLARIS texture; the new-DTW parameters fall back to loam for
    # fields with no new-DTW pixel shallower than 16 m (their f is 0 either way), so keep both sets.
    for col, src in (("TEXTURE_MA", "texture"), ("DDEC_MA_M", "d_dec_m"), ("DEXT_MA_M", "d_ext_m"), ("P_MA", "p"),
                     ("DTW_MA_M", "wtd_mean_m"), ("DTW_MA_MIN_M", "wtd_min_m")):
        fld[col] = pm[src].reindex(ids)
    fld["DTW_D_M"] = fld["DTW_NEW_M"] - fld["DTW_MA_M"]
    fld["MA_COVER"] = fld["DTW_MA_M"].notna().astype(int)
    fld["ETA_AF"] = field_mean("ETA_AF")
    for (mp, k), fv in f.items():
        fld[f"F_{mp}_{k}"] = fv.to_numpy()
        fld[f"S_{mp}_{k}"] = field_mean(f"S_{mp}_{k}")
    for k in MODELS:
        fld[f"D_{k}"] = fld[f"S_NEW_{k}"] - fld[f"S_MA_{k}"]
    for c in wl.columns:
        fld[c] = wl[c].reindex(ids)

    # ---- checks against the report ------------------------------------------------------------------
    print("Checks against the report")
    tot = fld["S_NEW_TAN"].sum()
    check("Tan, new DTW, all fields = README 34,733 acre-ft/yr", round(tot) == REPORT["subsidy_total"], f"{tot:,.1f}")
    eta_tot = fld["ETA_AF"].sum()
    check("ETa = README 1,423,458 acre-ft/yr", round(eta_tot) == REPORT["eta_total"], f"{eta_tot:,.1f}")
    ref = pd.read_csv(HA_TABLE, dtype={"HYD_AREA": str}).set_index("HYD_AREA")
    irr_f = fld["N_IRR_YRS"] > 0
    ha_s = fld[irr_f].groupby("HYD_AREA")[["ETA_AF", "S_NEW_TAN", "S_MA_TAN"]].sum().round(2)
    j = ref.join(ha_s, how="left")
    for a, b in (("ETa_acre_ft_yr", "ETA_AF"), ("subsidy_new_acre_ft_yr", "S_NEW_TAN")):
        d = (j[a] - j[b]).abs().max()
        check(f"HA {a} = docs table", d <= 0.0051, f"max |diff| {d:.3f}")
    dma = (j["subsidy_ma_acre_ft_yr"] - j["S_MA_TAN"])
    summary["ha_ma_subsidy_app_minus_report"] = dma.round(2).sort_values(key=abs, ascending=False).head(15).to_dict()

    both = fld[irr_f & (fld["MA_COVER"] == 1)]
    summary["vs_report"]["n_covered"] = {"app": len(both), "report": REPORT["n_covered"]}
    print(f"  [info] irrigated fields covered by both maps: app {len(both):,} vs report {REPORT['n_covered']:,}")
    note(summary, "covered fields, Tan, new DTW (acre-ft/yr)", float(both["S_NEW_TAN"].sum()), REPORT["covered_new"])
    note(summary, "covered fields, Tan, Ma (acre-ft/yr)", float(both["S_MA_TAN"].sum()), REPORT["covered_ma"])
    note(summary, "median field DTW, new (m)", float(both["DTW_NEW_M"].median()), REPORT["median_dtw_new_fig11"])
    note(summary, "median field DTW, Ma (m)", float(both["DTW_MA_M"].median()), REPORT["median_dtw_ma_fig11"])
    r = float(np.corrcoef(np.log(both["DTW_MA_M"].clip(0.1)), np.log(both["DTW_NEW_M"].clip(0.1)))[0, 1])
    note(summary, "r(log field DTW)", r, REPORT["r_log"])
    note(summary, "Ma, Tan, all fields (acre-ft/yr)", float(fld["S_MA_TAN"].sum()), float(ref["subsidy_ma_acre_ft_yr"].sum()))
    note(summary, "Tan recomputed (TANR) vs run, all fields (acre-ft/yr)", float(fld["S_NEW_TANR"].sum()), REPORT["subsidy_total"])
    dfr = (fld["F_NEW_TANR"] - fld["F_NEW_TAN"]).abs()
    summary["tanr_f_abs_diff_quantiles"] = dfr[fld["F_NEW_TAN"] > 0].quantile([0.5, 0.9, 0.99, 1.0]).to_dict()
    cov = both.assign(w=both["ACRES"])     # figures/_mapkit.field_attributes: Acres = ACRES_FTR_GEOM
    hw = cov.groupby("HYD_AREA").apply(lambda g: pd.Series({
        "DTW_NEW_M": np.average(g["DTW_NEW_M"], weights=g["w"]),
        "DTW_MA_M": np.average(g["DTW_MA_M"], weights=g["w"])}), include_groups=False)
    j = ref.join(hw.round(2), how="left")
    summary["ha_dtw_new_max_abs_diff_m"] = float((j["dtw_new_m"] - j["DTW_NEW_M"]).abs().max())
    summary["ha_dtw_ma_max_abs_diff_m"] = float((j["dtw_ma_m"] - j["DTW_MA_M"]).abs().max())

    # ---- HA tables ------------------------------------------------------------------------------------
    hp = gpd.read_parquet(out / "ha_polygons_nwi_investigations.parquet").set_index("BasinID")
    hp["geometry"] = [polygonal(g) for g in hp.geometry]
    g_irr = fld[irr_f].groupby("HYD_AREA")
    ha = pd.DataFrame({"N_FIELDS": fld.groupby("HYD_AREA").size(), "N_IRR": g_irr.size()}).fillna(0).astype(int)
    ha["ETA_AF"] = g_irr["ETA_AF"].sum()
    for k in list(MODELS) + ["TANR"]:
        ha[f"S_NEW_{k}"] = g_irr[f"S_NEW_{k}"].sum()
        if k != "TANR":
            ha[f"S_MA_{k}"] = g_irr[f"S_MA_{k}"].sum()
            ha[f"D_{k}"] = ha[f"S_NEW_{k}"] - ha[f"S_MA_{k}"]
    ha["S_MA_TAN_RPT"] = ref["subsidy_ma_acre_ft_yr"]          # the report's table, for the checks panel
    ha["N_SUB_NEW"] = g_irr["S_NEW_TAN"].apply(lambda s: int((s > 0.5).sum()))
    ha["N_SUB_MA"] = g_irr["S_MA_TAN"].apply(lambda s: int((s > 0.5).sum()))
    ha["N_COVER"] = both.groupby("HYD_AREA").size()
    ha = ha.join(hw)
    ha["MED_WT_KM"] = g_irr["WT_DIST_KM"].median()
    ha["N_FAR5"] = g_irr["WT_DIST_KM"].apply(lambda s: int((s > 5).sum()))
    ha["S_NEW_TAN_FAR5"] = fld[irr_f & (fld["WT_DIST_KM"] > 5)].groupby("HYD_AREA")["S_NEW_TAN"].sum()
    ha["S_MA_TAN_FAR5"] = fld[irr_f & (fld["WT_DIST_KM"] > 5)].groupby("HYD_AREA")["S_MA_TAN"].sum()
    ha["HA_NAME"] = fld.groupby("HYD_AREA")["HA_NAME"].first()
    num = [c for c in ha.columns if c not in ("HA_NAME", "DTW_NEW_M", "DTW_MA_M", "MED_WT_KM", "S_MA_TAN_RPT")]
    ha[num] = ha[num].fillna(0)
    ha.index.name = "HYD_AREA"
    hag = gpd.GeoDataFrame(ha.join(hp[["geometry"]], how="left"), geometry="geometry", crs=4326)
    check("every HA has a polygon", hag.geometry.notna().all(), f"{hag.geometry.isna().sum()} missing")

    fy["HYD_AREA"] = fld["HYD_AREA"].reindex(fy["NWI_ID"]).to_numpy()
    vol = ["ETA_AF"] + [c for c in fy.columns if c.startswith("S_")]
    hy = fy[irr_y].groupby(["HYD_AREA", "YEAR"])[vol].sum()
    full = pd.MultiIndex.from_product([ha.index[ha["N_IRR"] > 0], range(START_YEAR, START_YEAR + NY)],
                                      names=["HYD_AREA", "YEAR"])
    hy = hy.reindex(full, fill_value=0.0).reset_index()
    check("HA-year sums / 41 = field means", abs(hy["S_NEW_TAN"].sum() / NY - tot) < 1e-6)

    # ---- write ------------------------------------------------------------------------------------------
    fg = gpd.GeoDataFrame(fld.join(geo[["geometry"]]), geometry="geometry", crs=4326).reset_index()
    fg.to_parquet(app / "fields_gw_subsidy.parquet", index=False)
    wkt_csv(fg, app / "fields_gw_subsidy.csv")
    fy_out = fy.drop(columns="HYD_AREA")
    fy_out.to_parquet(app / "field_year_gw_subsidy.parquet", index=False)
    fy_out.to_csv(app / "field_year_gw_subsidy.csv", index=False, float_format="%.8g")
    hag.reset_index().to_parquet(app / "ha_gw_subsidy.parquet", index=False)
    wkt_csv(hag.reset_index(), app / "ha_gw_subsidy.csv")
    hy.to_parquet(app / "ha_year_gw_subsidy.parquet", index=False)
    hy.to_csv(app / "ha_year_gw_subsidy.csv", index=False, float_format="%.10g")
    for name in ("wells_train", "wells_gwx_nv"):
        w = gpd.read_parquet(out / f"{name}.parquet")
        for c in w.columns:
            if w[c].dtype == bool:
                w[c] = w[c].astype(int)
        wkt_csv(w, app / f"{name}.csv")

    summary.update({"n_fields": len(fld), "n_fields_irrigated": int(irr_f.sum()), "subsidy_new_tan_total": tot,
                    "eta_total": eta_tot,
                    "totals": {c: float(fld[c].sum()) for c in fld.columns if c.startswith(("S_", "D_"))},
                    "covered_totals": {c: float(both[c].sum()) for c in both.columns if c.startswith(("S_", "D_"))}})
    (app / "app_summary.json").write_text(json.dumps(summary, indent=2, default=float))
    print(f"Wrote app tables to {app}")
    print(json.dumps({k: round(v, 1) for k, v in summary["totals"].items()}, indent=1))


if __name__ == "__main__":
    main()
