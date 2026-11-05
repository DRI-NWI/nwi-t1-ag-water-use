#!/usr/bin/env python3
"""Figs. 16-17: what moving OTTER from 4 km gridMET to 800 m PRISM precipitation changes.

Compares the final run (PRISM 800 m precipitation) with the QA variant v5_gridmet_otter_peff, which is
the same run with gridMET precipitation: the OpenET export's monthly precipitation, and OTTER rerun with
each field's daily gridMET cell precipitation (ET-Demands files). ETo, EToF, soils and OTTER settings are
the same. Nevada fields, 1985-2023 (the gridMET cell files end in April 2024).

Fig. 16: OTTER at the fields (every Nevada field with OTTER output, every month).
Fig. 17: water balance of irrigated fields (HA summaries, Nevada fields only, %_IRRIGATED > 0.5).
Writes docs/tables/otter_prism_vs_gridmet_by_ha.csv."""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import colors as mcolors

import _mapkit as K

VARIANT = "v5_gridmet_otter_peff"
Y0, Y1 = 1985, 2023
NY = Y1 - Y0 + 1
VDIR = K.P.variants / VARIANT
C_PR, C_GM = "#018571", "#a6611a"
MM_TO_AF = 1 / 304.8

# ---- OTTER at the fields ------------------------------------------------------------------------
cols = ["NWI_ID", "YEAR", "MONTH", "OTTER_PR_MM", "OTTER_PEFF_MM"]
pr = pd.read_parquet(K.P.otter_monthly, columns=cols)
gm = pd.read_parquet(VDIR / "field_monthly_otter_peff.parquet", columns=cols)
o = pr.merge(gm, on=["NWI_ID", "YEAR", "MONTH"], suffixes=("_pr", "_gm"))
o = o[o.YEAR.between(Y0, Y1)]
attr = K.field_attributes().set_index("NWI_ID")
o = o[o.NWI_ID.map(attr.STATE).eq("NV")]
fld = o.groupby("NWI_ID")[["OTTER_PR_MM_pr", "OTTER_PEFF_MM_pr", "OTTER_PR_MM_gm", "OTTER_PEFF_MM_gm"]].sum() / NY
fld.columns = ["p_pr", "pe_pr", "p_gm", "pe_gm"]
fld = fld.join(attr[["HYD_AREA", "Acres"]])
fld["pe_ratio"] = fld.pe_pr / fld.pe_gm
fld["fr_pr"], fld["fr_gm"] = fld.pe_pr / fld.p_pr, fld.pe_gm / fld.p_gm
fld["fr_diff"] = 100 * (fld.fr_pr - fld.fr_gm)

def wsum(g, c):
    return (g[c] * g.Acres).sum()
ha_f = fld.groupby("HYD_AREA").apply(lambda g: pd.Series({
    "fields": len(g), "p_prism_mm": wsum(g, "p_pr") / g.Acres.sum(), "p_gridmet_mm": wsum(g, "p_gm") / g.Acres.sum(),
    "peff_prism_mm": wsum(g, "pe_pr") / g.Acres.sum(), "peff_gridmet_mm": wsum(g, "pe_gm") / g.Acres.sum()}), include_groups=False)
ha_f["peff_diff_pct"] = 100 * (ha_f.peff_prism_mm / ha_f.peff_gridmet_mm - 1)
ha_f["peff_frac_prism"] = ha_f.peff_prism_mm / ha_f.p_prism_mm
ha_f["peff_frac_gridmet"] = ha_f.peff_gridmet_mm / ha_f.p_gridmet_mm

# ---- irrigated water balance (HA summaries, Nevada fields only) --------------------------------
V = ["PPT_vol_acre_ft", "Eff_PPT_vol_acre_ft", "GW_subsidy_acre_ft", "Carryforward_Used_acre_ft", "NetET_final_acre_ft",
     "AR_final_acre_ft", "ETa_final_acre_ft"]
def ha_year(folder, suffix=""):
    t = pd.read_csv(folder / f"ha_summaries_EToF_final{suffix}_NV_fields_only.csv", dtype={"HYD_AREA": str}, usecols=["HYD_AREA", "YEAR"] + V)
    t = t[t.YEAR.between(Y0, Y1)]
    return t.groupby(["HYD_AREA", "YEAR"])[V].sum()
A_pr, A_gm = ha_year(K.P.summaries), ha_year(VDIR)
G_pr, G_gm = ha_year(K.P.summaries, "_GW"), ha_year(VDIR, "_GW")
S_pr, S_gm = ha_year(K.P.summaries, "_SW"), ha_year(VDIR, "_SW")
hm_pr, hm_gm = A_pr.groupby("HYD_AREA").sum() / NY, A_gm.groupby("HYD_AREA").sum() / NY
yr_pr, yr_gm = A_pr.groupby("YEAR").sum(), A_gm.groupby("YEAR").sum()

ha = ha_f.join(pd.DataFrame({"netet_prism_af": hm_pr.NetET_final_acre_ft, "netet_gridmet_af": hm_gm.NetET_final_acre_ft,
                             "ar_prism_af": hm_pr.AR_final_acre_ft, "ar_gridmet_af": hm_gm.AR_final_acre_ft}), how="outer")
ha["ar_diff_af"] = ha.ar_prism_af - ha.ar_gridmet_af
ha["ar_diff_pct"] = 100 * (ha.ar_prism_af / ha.ar_gridmet_af - 1)
names = K.field_attributes().drop_duplicates("HYD_AREA").set_index("HYD_AREA")["HA_NAME_CODE"]
ha.insert(0, "HA_NAME_CODE", names.reindex(ha.index))
ha.round(3).to_csv(Path(__file__).resolve().parents[1] / "docs/tables/otter_prism_vs_gridmet_by_ha.csv")

# ---- Fig. 16: OTTER ------------------------------------------------------------------------------
fig = plt.figure(figsize=(17, 11.5), layout="constrained")
gs = fig.add_gridspec(2, 3, height_ratios=[1.25, 1])
cent = K.fields().set_index("NWI_ID").geometry.centroid.reindex(fld.index)
for j, (col, lab, unit, norm, cmap) in enumerate([
        ("pe_ratio", "OTTER effective precipitation, PRISM ÷ gridMET", "ratio (green: more with PRISM)", mcolors.LogNorm(0.6, 1 / 0.6), "BrBG"),
        ("fr_diff", "Share of precipitation that is effective,\nPRISM minus gridMET", "percentage points (orange: larger share with PRISM; purple: smaller)", mcolors.TwoSlopeNorm(0, -15, 15), "PuOr_r")]):
    ax = fig.add_subplot(gs[0, j]); K.base_map(ax, county=False, labels=False)
    s = fld.assign(a=np.abs(np.log(fld.pe_ratio)) if col == "pe_ratio" else fld[col].abs()).sort_values("a")
    sc = ax.scatter(cent.reindex(s.index).x, cent.reindex(s.index).y, s=3, c=s[col].clip(norm.vmin, norm.vmax), cmap=cmap, norm=norm, zorder=7, lw=0)
    cb = fig.colorbar(sc, ax=ax, orientation="horizontal", fraction=0.04, pad=0.02, aspect=30, label=unit)
    if col == "pe_ratio":
        cb.set_ticks([0.6, 0.8, 1, 1.25, 1 / 0.6]); cb.set_ticklabels(["0.6", "0.8", "1", "1.25", "1.67"])
    ax.set_title(lab, fontsize=10.5)
K.ha_choropleth(fig.add_subplot(gs[0, 2]), ha_f.peff_diff_pct, "OTTER effective precipitation by HA,\nPRISM minus gridMET (fields weighted by acres)",
                "% of gridMET", "BrBG", mcolors.TwoSlopeNorm(0, -30, 30))

ax = fig.add_subplot(gs[1, 0])
top = (hm_pr.Eff_PPT_vol_acre_ft).sort_values(ascending=False).head(15).index[::-1]
y = np.arange(len(top))
ax.barh(y + 0.2, hm_pr.loc[top, "Eff_PPT_vol_acre_ft"] / 1e3, 0.4, color=C_PR, label="PRISM 800 m")
ax.barh(y - 0.2, hm_gm.loc[top, "Eff_PPT_vol_acre_ft"] / 1e3, 0.4, color=C_GM, label="gridMET 4 km")
ax.set_yticks(y, [str(names.get(h, h)) for h in top], fontsize=8)
ax.set_xlabel("thousand acre-ft/yr"); ax.legend(fontsize=8, loc="lower right")
ax.set_title("Effective precipitation on irrigated fields,\n15 largest HAs", fontsize=10.5); ax.grid(axis="x", alpha=0.3)

ax = fig.add_subplot(gs[1, 1])
ann = o.groupby("YEAR")[["OTTER_PR_MM_pr", "OTTER_PEFF_MM_pr", "OTTER_PR_MM_gm", "OTTER_PEFF_MM_gm"]].sum() / o.NWI_ID.nunique()
ax.plot(ann.index, ann.OTTER_PR_MM_pr, "-", color=C_PR, lw=1, alpha=0.6, label="precipitation, PRISM")
ax.plot(ann.index, ann.OTTER_PR_MM_gm, "-", color=C_GM, lw=1, alpha=0.6, label="precipitation, gridMET")
ax.plot(ann.index, ann.OTTER_PEFF_MM_pr, "-o", ms=3, color=C_PR, lw=2, label="effective, PRISM")
ax.plot(ann.index, ann.OTTER_PEFF_MM_gm, "-o", ms=3, color=C_GM, lw=2, label="effective, gridMET")
ax.set_ylabel("field-mean mm/yr"); ax.legend(fontsize=8, ncol=2); ax.grid(alpha=0.3)
ax.set_title(f"By year, every Nevada field with OTTER output (n = {o.NWI_ID.nunique():,})\n"
             f"effective precipitation PRISM ÷ gridMET = {ann.OTTER_PEFF_MM_pr.sum() / ann.OTTER_PEFF_MM_gm.sum():.3f}", fontsize=10.5)

ax = fig.add_subplot(gs[1, 2])
mon = o.groupby("MONTH")[["OTTER_PR_MM_pr", "OTTER_PEFF_MM_pr", "OTTER_PR_MM_gm", "OTTER_PEFF_MM_gm"]].mean()
x = np.arange(1, 13); w = 0.4
ax.bar(x - w / 2, mon.OTTER_PEFF_MM_gm, w, color=C_GM, label="effective, gridMET"); ax.bar(x + w / 2, mon.OTTER_PEFF_MM_pr, w, color=C_PR, label="effective, PRISM")
ax2 = ax.twinx()
ax2.plot(x, 100 * mon.OTTER_PEFF_MM_gm / mon.OTTER_PR_MM_gm, "--", color=C_GM, label="effective share, gridMET (right axis)")
ax2.plot(x, 100 * mon.OTTER_PEFF_MM_pr / mon.OTTER_PR_MM_pr, "-", color=C_PR, label="effective share, PRISM (right axis)")
ax2.set_ylabel("effective share of precipitation (%)"); ax2.set_ylim(0, 105)
ax.set_xticks(x, ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"]); ax.set_ylabel("field-mean mm/month")
h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
ax.legend(h1 + h2, l1 + l2, fontsize=7.5, loc="lower left", framealpha=0.9)
ax.set_title("By calendar month", fontsize=10.5)
fig.suptitle(f"OTTER effective precipitation: PRISM 800 m (this run) vs gridMET 4 km, Nevada fields, {Y0}–{Y1}")
K.savefig(fig, "fig16_otter_prism_vs_gridmet")

# ---- Fig. 17: water balance of irrigated fields --------------------------------------------------
fig = plt.figure(figsize=(17, 11.5), layout="constrained")
gs = fig.add_gridspec(2, 3, height_ratios=[1.25, 1])
K.ha_choropleth(fig.add_subplot(gs[0, 0]), 100 * (hm_pr.NetET_final_acre_ft / hm_gm.NetET_final_acre_ft - 1).where(hm_gm.NetET_final_acre_ft > 10),
                "Net ET of irrigated fields, PRISM minus gridMET", "% of gridMET (brown: more with PRISM; green: less)", "BrBG_r", mcolors.TwoSlopeNorm(0, -10, 10))
K.ha_choropleth(fig.add_subplot(gs[0, 1]), (hm_pr.AR_final_acre_ft - hm_gm.AR_final_acre_ft).where(hm_gm.AR_final_acre_ft > 10),
                "Applied water of irrigated fields, PRISM minus gridMET", "acre-ft/yr (brown: more with PRISM; green: less)", "BrBG_r", mcolors.SymLogNorm(100, vmin=-5000, vmax=5000))
ax = fig.add_subplot(gs[0, 2])
d = (hm_pr.AR_final_acre_ft - hm_gm.AR_final_acre_ft)
top = d.abs().sort_values(ascending=False).head(20).index
top = d.loc[top].sort_values().index
ax.barh(np.arange(len(top)), d.loc[top] / 1e3, color=np.where(d.loc[top] > 0, "#8c510a", "#01665e"))
ax.set_yticks(np.arange(len(top)), [str(names.get(h, h)) for h in top], fontsize=8); ax.axvline(0, color="k", lw=0.6)
ax.set_xlabel("thousand acre-ft/yr"); ax.grid(axis="x", alpha=0.3)
ax.set_title("Applied water, PRISM minus gridMET,\n20 HAs with the largest change (brown: more with PRISM)", fontsize=10.5)

ax = fig.add_subplot(gs[1, 0:2])
for c, lab in [("Eff_PPT_vol_acre_ft", "effective precipitation"), ("NetET_final_acre_ft", "net ET"), ("AR_final_acre_ft", "applied water")]:
    l = ax.plot(yr_pr.index, yr_pr[c] / 1e3, "-", lw=2, label=f"{lab}, PRISM")[0]
    ax.plot(yr_gm.index, yr_gm[c] / 1e3, "--", lw=1.6, color=l.get_color(), label=f"{lab}, gridMET")
ax.set_ylabel("thousand acre-ft/yr"); ax.legend(fontsize=8, ncol=3); ax.grid(alpha=0.3)
ax.set_title("By year, irrigated Nevada fields", fontsize=10.5)

ax = fig.add_subplot(gs[1, 2])
tot = lambda T: T.groupby("YEAR").sum().mean()
rows = [("Precipitation", "PPT_vol_acre_ft", A_pr, A_gm), ("Effective precip.", "Eff_PPT_vol_acre_ft", A_pr, A_gm),
        ("Carry-forward used", "Carryforward_Used_acre_ft", A_pr, A_gm), ("GW-ET subsidy", "GW_subsidy_acre_ft", A_pr, A_gm),
        ("Net ET", "NetET_final_acre_ft", A_pr, A_gm), ("Applied water", "AR_final_acre_ft", A_pr, A_gm),
        ("Applied water, GW", "AR_final_acre_ft", G_pr, G_gm), ("Applied water, SW", "AR_final_acre_ft", S_pr, S_gm)]
vals = pd.DataFrame([(n, tot(a)[c], tot(b)[c]) for n, c, a, b in rows], columns=["term", "prism", "gridmet"]).set_index("term")
y = np.arange(len(vals))[::-1]
ax.barh(y + 0.2, vals.prism / 1e3, 0.4, color=C_PR, label="PRISM 800 m"); ax.barh(y - 0.2, vals.gridmet / 1e3, 0.4, color=C_GM, label="gridMET 4 km")
for yy, (p, g) in zip(y, vals.to_numpy()):
    ax.text(max(p, g) / 1e3 * 1.02, yy, f"{100 * (p / g - 1):+.1f}%", va="center", fontsize=8)
ax.set_yticks(y, vals.index, fontsize=8.5); ax.set_xlabel("thousand acre-ft/yr"); ax.legend(fontsize=8, loc="lower right")
ax.set_xlim(0, vals.max().max() / 1e3 * 1.18); ax.grid(axis="x", alpha=0.3)
ax.set_title(f"Statewide, irrigated Nevada fields, {Y0}–{Y1} mean\n(label: PRISM minus gridMET)", fontsize=10.5)
fig.suptitle(f"What PRISM 800 m instead of gridMET 4 km changes in the water balance, irrigated Nevada fields, {Y0}–{Y1}")
K.savefig(fig, "fig17_prism_vs_gridmet_water_balance")

# ---- numbers for the write-up -------------------------------------------------------------------
print(f"fields {len(fld):,}; Peff PRISM {fld.pe_pr.mean():.1f} vs gridMET {fld.pe_gm.mean():.1f} mm/yr; P {fld.p_pr.mean():.1f} vs {fld.p_gm.mean():.1f}")
print("field Peff ratio quantiles:", fld.pe_ratio.quantile([.05, .25, .5, .75, .95]).round(3).to_dict())
print("share fields Peff >20% higher / lower with PRISM:", round((fld.pe_ratio > 1.2).mean(), 3), round((fld.pe_ratio < 1 / 1.2).mean(), 3))
print("effective share: PRISM", round(fld.pe_pr.sum() / fld.p_pr.sum(), 3), "gridMET", round(fld.pe_gm.sum() / fld.p_gm.sum(), 3))
print("monthly effective share %:", pd.DataFrame({"pr": 100 * mon.OTTER_PEFF_MM_pr / mon.OTTER_PR_MM_pr, "gm": 100 * mon.OTTER_PEFF_MM_gm / mon.OTTER_PR_MM_gm}).round(1).T.to_string())
print(vals.assign(diff=vals.prism - vals.gridmet, pct=100 * (vals.prism / vals.gridmet - 1)).round(1).to_string())
big = ha[ha.fields >= 20]
print("AR change, largest:\n", ha.reindex(d.abs().sort_values(ascending=False).head(12).index)[["HA_NAME_CODE", "fields", "peff_diff_pct", "ar_prism_af", "ar_gridmet_af", "ar_diff_af", "ar_diff_pct"]].round(1).to_string())
print("Peff % change, HAs >= 20 fields, most negative / positive:\n", big.sort_values("peff_diff_pct")[["HA_NAME_CODE", "fields", "peff_diff_pct", "ar_diff_pct"]].round(1).head(6).to_string(),
      "\n", big.sort_values("peff_diff_pct")[["HA_NAME_CODE", "fields", "peff_diff_pct", "ar_diff_pct"]].round(1).tail(6).to_string())
ych = 100 * (yr_pr.AR_final_acre_ft / yr_gm.AR_final_acre_ft - 1)
print("yearly AR change %: min", round(ych.min(), 1), int(ych.idxmin()), "max", round(ych.max(), 1), int(ych.idxmax()))
