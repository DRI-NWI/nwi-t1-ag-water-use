#!/usr/bin/env python3
"""Fig. 15: field-scale precipitation, PRISM 800 m (used in the run) vs gridMET (the precipitation in the
OpenET field export, used by the earlier processing). Every field and month 1985-2025, both sources.

Writes docs/images/fig15_precip_prism_vs_gridmet.png and docs/tables/precip_prism_vs_gridmet_by_ha.csv."""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import colors as mcolors

import _mapkit as K

YEARS = range(K.CFG["run"]["start_year"], K.CFG["run"]["end_year"] + 1)
NY = len(YEARS)

# gridMET: PPT_MM of the OpenET field export; PRISM: PPT_MM of the run's field summaries
export = pd.concat([pd.read_parquet(f, columns=["NWI_ID", "YEAR", "MONTH", "PPT_MM"])
                    for f in sorted(Path(K.CFG["inputs"]["ensemble_dir"]).rglob("*.parquet"))], ignore_index=True)
export["MONTH"] = export.MONTH.astype(int)
export = export.rename(columns={"PPT_MM": "ppt_gm"})
f = pd.read_parquet(K.P.fields / "field_summaries_EToF_final.parquet",
                    columns=["NWI_ID", "YEAR", "MONTH", "HYD_AREA", "STATE", "ACRES_FTR_GEOM", "PPT_MM"]).rename(columns={"PPT_MM": "ppt_pr"})
f["MONTH"] = f.MONTH.astype(int)
d = f.merge(export, on=["NWI_ID", "YEAR", "MONTH"])
d = d[d.YEAR.isin(YEARS) & d.ppt_pr.notna() & d.ppt_gm.notna()]

# per field: mean annual precipitation from each source (fields with all 12 months in a year counted for that year)
fy = d.groupby(["NWI_ID", "YEAR"]).agg(pr=("ppt_pr", "sum"), gm=("ppt_gm", "sum"), n=("ppt_pr", "size"))
fy = fy[fy.n == 12]
fld = fy.groupby("NWI_ID")[["pr", "gm"]].mean()
attr = d.drop_duplicates("NWI_ID").set_index("NWI_ID")[["HYD_AREA", "STATE", "ACRES_FTR_GEOM"]]
fld = fld.join(attr)
fld["diff"] = fld.pr - fld.gm
fld["pct"] = 100 * (fld.pr / fld.gm - 1)

# per HA: acre-weighted field means
def wavg(g, c):
    return np.average(g[c], weights=g.ACRES_FTR_GEOM)
ha = fld.groupby("HYD_AREA").apply(lambda g: pd.Series({"fields": len(g), "prism_mm_yr": wavg(g, "pr"), "gridmet_mm_yr": wavg(g, "gm")}),
                                   include_groups=False)
ha["diff_mm_yr"] = ha.prism_mm_yr - ha.gridmet_mm_yr
ha["diff_pct"] = 100 * (ha.prism_mm_yr / ha.gridmet_mm_yr - 1)
names = K.field_attributes().drop_duplicates("HYD_AREA").set_index("HYD_AREA")["HA_NAME_CODE"]
ha.insert(0, "HA_NAME_CODE", names.reindex(ha.index))
out = Path(__file__).resolve().parents[1] / "docs/tables/precip_prism_vs_gridmet_by_ha.csv"
ha.round(2).sort_values("diff_pct").to_csv(out)

# statewide by calendar month and by year (field means, unweighted)
mon = d.groupby("MONTH")[["ppt_pr", "ppt_gm"]].mean()
yr = fy.groupby("YEAR")[["pr", "gm"]].mean()

fig = plt.figure(figsize=(17, 11.5), layout="constrained")
gs = fig.add_gridspec(2, 3, height_ratios=[1.25, 1])
cent = K.fields().set_index("NWI_ID").geometry.centroid.reindex(fld.index)
for j, (col, lab, unit, lim) in enumerate([("diff", "PRISM minus gridMET, field mean annual precipitation", "mm/yr", 120),
                                           ("pct", "PRISM minus gridMET, percent of gridMET", "%", 40)]):
    ax = fig.add_subplot(gs[0, j]); K.base_map(ax, county=False, labels=False)
    s = fld.assign(a=fld[col].abs()).sort_values("a")
    sc = ax.scatter(cent.reindex(s.index).x, cent.reindex(s.index).y, s=3, c=s[col], cmap="BrBG", norm=mcolors.TwoSlopeNorm(0, -lim, lim), zorder=7, lw=0)
    fig.colorbar(sc, ax=ax, orientation="horizontal", fraction=0.04, pad=0.02, aspect=30, label=f"{unit} (green: PRISM wetter; brown: PRISM drier)")
    ax.set_title(lab, fontsize=10.5)
ax = fig.add_subplot(gs[0, 2])
K.ha_choropleth(ax, ha.diff_pct, "PRISM minus gridMET by HA\n(field means weighted by field acres)", "%", "BrBG", mcolors.TwoSlopeNorm(0, -40, 40))

ax = fig.add_subplot(gs[1, 0])
hb = ax.hexbin(fld.gm, fld.pr, gridsize=55, bins="log", cmap="Blues", mincnt=1)
m = max(fld.gm.max(), fld.pr.max()) * 1.03; ax.plot([0, m], [0, m], "k-", lw=0.8)
r = np.corrcoef(fld.gm, fld.pr)[0, 1]
ax.set_xlabel("gridMET, mean annual precipitation (mm/yr)"); ax.set_ylabel("PRISM 800 m, mean annual precipitation (mm/yr)")
ax.set_title(f"Every field (n = {len(fld):,}), 1985–2025 mean\nPRISM {fld.pr.mean():.0f} vs gridMET {fld.gm.mean():.0f} mm/yr on average, r = {r:.2f}", fontsize=10.5)
fig.colorbar(hb, ax=ax, label="fields"); ax.grid(alpha=0.3)

ax = fig.add_subplot(gs[1, 1])
x = np.arange(1, 13); w = 0.4
ax.bar(x - w / 2, mon.ppt_gm, w, color="#a6611a", label="gridMET"); ax.bar(x + w / 2, mon.ppt_pr, w, color="#018571", label="PRISM 800 m")
ax2 = ax.twinx(); ax2.plot(x, 100 * (mon.ppt_pr / mon.ppt_gm - 1), "k-o", ms=4, label="PRISM minus gridMET, % (right axis)"); ax2.axhline(0, color="k", lw=0.5, ls=":")
ax2.set_ylabel("PRISM minus gridMET (%)")
ax.set_xticks(x, ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"]); ax.set_ylabel("field-mean precipitation (mm/month)")
ax.set_title("By calendar month, all fields, 1985–2025", fontsize=10.5)
h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
ax.legend(h1 + h2, l1 + l2, loc="lower left", fontsize=8, framealpha=0.9)

ax = fig.add_subplot(gs[1, 2])
ax.plot(yr.index, yr.gm, "-o", ms=3, color="#a6611a", label="gridMET"); ax.plot(yr.index, yr.pr, "-o", ms=3, color="#018571", label="PRISM 800 m")
ax.set_ylabel("field-mean annual precipitation (mm/yr)"); ax.set_title(f"By year, all fields\nPRISM / gridMET = {yr.pr.sum() / yr.gm.sum():.3f}", fontsize=10.5)
ax.legend(); ax.grid(alpha=0.3)
fig.suptitle("Precipitation at the fields: PRISM 800 m (this run) vs gridMET (OpenET field export, earlier processing)")
K.savefig(fig, "fig15_precip_prism_vs_gridmet")

big = ha[ha.fields >= 20]
print(f"fields {len(fld):,}; median diff {fld['diff'].median():+.1f} mm/yr ({fld.pct.median():+.1f}%); "
      f"mean PRISM {fld.pr.mean():.0f} vs gridMET {fld.gm.mean():.0f} mm/yr; r {r:.3f}")
print("share of fields PRISM wetter by >10%:", round((fld.pct > 10).mean(), 3), " drier by >10%:", round((fld.pct < -10).mean(), 3))
print("by month %:", (100 * (mon.ppt_pr / mon.ppt_gm - 1)).round(1).to_dict())
print("HAs with >= 20 fields, driest PRISM vs gridMET:\n", big.sort_values("diff_pct").head(8)[["HA_NAME_CODE", "fields", "prism_mm_yr", "gridmet_mm_yr", "diff_pct"]].round(1).to_string())
print("wettest:\n", big.sort_values("diff_pct").tail(8)[["HA_NAME_CODE", "fields", "prism_mm_yr", "gridmet_mm_yr", "diff_pct"]].round(1).to_string())
print("by state %:", fld.groupby("STATE").apply(lambda g: round(100 * (g.pr.sum() / g.gm.sum() - 1), 1), include_groups=False).to_dict())
