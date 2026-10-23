#!/usr/bin/env python3
"""Fig. 8: inputs and gap filling - EToF fill by year, PRISM vs export precipitation,
OTTER Peff vs 0.85 x PPT, Peff/PPT by HA, effective irrigation efficiency, fallback fields."""

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import colors as mcolors

import _mapkit as K

# OpenET export precipitation (gridMET) for comparison with PRISM
from pathlib import Path
export = pd.concat([pd.read_parquet(f, columns=["NWI_ID", "YEAR", "MONTH", "PPT_MM"])
                    for f in sorted(Path(K.CFG["inputs"]["ensemble_dir"]).rglob("*.parquet"))], ignore_index=True)
export["MONTH"] = export.MONTH.astype(int)
export = export.rename(columns={"PPT_MM": "PPT_EXPORT_MM"})
f = pd.read_parquet(K.P.fields / "field_summaries_EToF_final.parquet",
                    columns=["NWI_ID", "YEAR", "MONTH", "HYD_AREA", "ACRES_FTR_GEOM", "%_IRRIGATED", "PPT_MM",
                             "interpolation_method", "peff_method", "EFF_PPT_MM"])
d = f.merge(export, on=["NWI_ID", "YEAR", "MONTH"])
d = d[d["%_IRRIGATED"] > 0.5]
a = d["ACRES_FTR_GEOM"]
d["ppt_exp"] = d.PPT_EXPORT_MM * a; d["ppt_pr"] = d.PPT_MM * a; d["peff085"] = 0.85 * d.PPT_EXPORT_MM * a
d["peff"] = d.EFF_PPT_MM * a
hy = d.groupby(["HYD_AREA", "YEAR"])[["ppt_exp", "ppt_pr", "peff085", "peff"]].sum() / 1000 * 3.281
hm = hy.groupby("HYD_AREA").mean()

fig, axs = plt.subplots(2, 3, figsize=(16, 10.5))
ax = axs[0, 0]
allf = pd.read_parquet(K.P.fields / "field_summaries_EToF_final.parquet", columns=["YEAR", "interpolation_method"])
ct = allf.groupby(["YEAR", "interpolation_method"]).size().unstack(fill_value=0)
ct = 100 * ct.div(ct.sum(1), axis=0)
for c, col in [("linear_1month", "#4393c3"), ("climatology", "#f4a582"), ("unfilled_missing", "#b2182b")]:
    if c in ct:
        ax.plot(ct.index, ct[c], "-o", ms=3, color=col, label=c.replace("_", " "))
ax.set_ylabel("% of field-months"); ax.set_title("EToF gap filling by year (all field-months)"); ax.legend(fontsize=8)
ax.set_yscale("log"); ax.grid(alpha=0.3)

ax = axs[0, 1]
ax.loglog(hy.ppt_exp.clip(lower=1), hy.ppt_pr.clip(lower=1), ".", ms=2, alpha=0.3, color="#2166ac")
lim = [1, hy[["ppt_exp", "ppt_pr"]].max().max() * 1.2]; ax.plot(lim, lim, "k-", lw=0.8)
r = np.corrcoef(np.log(hy.ppt_exp.clip(lower=1)), np.log(hy.ppt_pr.clip(lower=1)))[0, 1]
ax.set_xlabel("OpenET export precipitation (acre-ft/yr)"); ax.set_ylabel("PRISM 800 m precipitation (acre-ft/yr)")
ax.set_title(f"Precipitation, HA × year (irrigated)\nPRISM / export = {hy.ppt_pr.sum() / hy.ppt_exp.sum():.3f}, r(log) = {r:.3f}")
ax.grid(alpha=0.3)

ax = axs[0, 2]
ax.loglog(hy.peff085.clip(lower=1), hy.peff.clip(lower=1), ".", ms=2, alpha=0.3, color="#542788")
lim = [1, hy[["peff085", "peff"]].max().max() * 1.2]; ax.plot(lim, lim, "k-", lw=0.8)
ax.set_xlabel("0.85 × export precipitation (acre-ft/yr)"); ax.set_ylabel("OTTER Peff (acre-ft/yr)")
ax.set_title(f"Effective precipitation, HA × year (irrigated)\nOTTER / (0.85 × export) = {hy.peff.sum() / hy.peff085.sum():.3f}")
ax.grid(alpha=0.3)

K.ha_choropleth(axs[1, 0], hm.peff / hm.ppt_pr, "OTTER Peff / PRISM precipitation", "fraction", "viridis",
                mcolors.Normalize(0.6, 1.0))
A = K.ha_mean("ALL").set_index("HYD_AREA")
K.ha_choropleth(axs[1, 1], (A.NetET_final_acre_ft / A.AR_final_acre_ft).where(A.acres > 0),
                "Effective irrigation efficiency\n(Net ET / applied water)", "fraction", "cividis", mcolors.Normalize(0.5, 0.85))
ax = axs[1, 2]
fb = pd.read_csv(K.P.otter_audit)
fl = K.fields()
K.base_map(ax, county=False, labels=False, river=False)
fl[fl.NWI_ID.isin(fb.loc[fb.status.eq("skipped"), "NWI_ID"])].plot(ax=ax, color="#b2182b", edgecolor="#b2182b",
                                                                        lw=1.5, zorder=6)
miss = allf[allf.interpolation_method.eq("unfilled_missing")]
ax.set_title(f"Fields without OTTER soils ({fb.status.eq('skipped').sum()}; Peff = 0.85 × PRISM)")
fig.suptitle("Inputs and gap filling", y=0.995)
fig.tight_layout()
K.savefig(fig, "fig08_inputs_quality")
