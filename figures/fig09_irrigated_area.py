#!/usr/bin/env python3
"""Fig. 9: irrigated area - IrrMapper (the irrigation status used in the summaries) vs the
XGBRF field irrigation-status model (field-irrigation-classification), Nevada fields.

XGBRF predictions are keyed to the 2026-05-06 field roster (OPENET_ID), so both products are
compared on that roster at HA x year, alongside IrrMapper on the 2026-08-20 roster used here."""

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import colors as mcolors

import _mapkit as K

MAY = "/Users/Peter.ReVelle@dri.edu/github/agricultural-water-projects/mixed-field-allocation/data/shapefiles/Nevada_agricultural_field_boundaries_20260506.gpkg"
OOF = "/Users/Peter.ReVelle@dri.edu/github/agricultural-water-artifacts/mixed-field-allocation/results_nlcd_mixed_field_classification/irr_status_annual/model_oof_predictions.parquet"
POOL = "/Users/Peter.ReVelle@dri.edu/github/agricultural-water-projects/field-irrigation-classification/data/model_predictions/NV_field_predictions_pooled17.parquet"

may = gpd.read_file(MAY, columns=["OPENET_ID", "Basin", "Acres", "STATE"], ignore_geometry=True)
may = may[may.STATE.eq("NV")].groupby("OPENET_ID").agg(Acres=("Acres", "sum"), Basin=("Basin", "first")).reset_index()
may["HYD_AREA"] = may.Basin.str.split("_").str[0]
oof = pd.read_parquet(OOF).merge(may, on="OPENET_ID", how="inner")
oof["irrmapper_may"] = oof.Acres * (oof.pct_irr > 0.5)
oof["xgbrf_oof"] = oof.Acres * (oof.p_irr_model > 0.5)
pool = pd.read_parquet(POOL).rename(columns={"year": "YEAR"}).merge(may, on="OPENET_ID", how="inner")
pool["xgbrf_pooled17"] = pool.Acres * (pool.xgb_status == 1)

base = pd.read_parquet(K.P.field_base, columns=["NWI_ID", "STATE", "YEAR", "MONTH", "ACRES_FTR_GEOM", "%_IRRIGATED"])
base = base[base.STATE.eq("NV") & base.MONTH.eq(7)]
attrs = pd.read_parquet(K.P.field_attrs, columns=["NWI_ID", "HYD_AREA"])
base = base.merge(attrs, on="NWI_ID")
base["irrmapper_aug"] = base.ACRES_FTR_GEOM * (base["%_IRRIGATED"] > 0.5)
base["irrmapper_frac_aug"] = base.ACRES_FTR_GEOM * base["%_IRRIGATED"]

S = pd.concat([oof.groupby("YEAR")[["irrmapper_may", "xgbrf_oof"]].sum(),
               pool.groupby("YEAR")[["xgbrf_pooled17"]].sum(),
               base.groupby("YEAR")[["irrmapper_aug", "irrmapper_frac_aug"]].sum()], axis=1) / 1e3
H = pd.concat([oof.groupby(["HYD_AREA", "YEAR"])[["irrmapper_may", "xgbrf_oof"]].sum()], axis=1)
hm = H.groupby("HYD_AREA").mean()

fig, axs = plt.subplots(2, 2, figsize=(14, 11))
ax = axs[0, 0]
for c, lab, col, ls in [("irrmapper_aug", "IrrMapper > 50%, 2026-08-20 roster (used here)", "#1b7837", "-"),
                        ("irrmapper_frac_aug", "IrrMapper irrigated fraction × acres, 2026-08-20 roster", "#1b7837", ":"),
                        ("irrmapper_may", "IrrMapper > 50%, 2026-05-06 roster", "#5aae61", "--"),
                        ("xgbrf_oof", "XGBRF P > 0.5 (out-of-fold, NV model)", "#762a83", "-"),
                        ("xgbrf_pooled17", "XGBRF pooled 17-state model", "#c2a5cf", "-")]:
    s = S[c].dropna()
    ax.plot(s.index, s, ls, color=col, lw=2, marker="o" if c == "xgbrf_pooled17" else None, ms=3, label=lab)
ax.set_ylabel("thousand acres"); ax.set_title("Nevada irrigated field area by year"); ax.legend(fontsize=7.5); ax.grid(alpha=0.3)

ax = axs[0, 1]
r = (S.xgbrf_oof / S.irrmapper_may)
ax.bar(r.index, 100 * (r - 1), color=np.where(r > 1, "#762a83", "#1b7837"))
ax.axhline(0, color="k", lw=0.7)
ax.set_ylabel("XGBRF − IrrMapper (%)"); ax.set_title(f"XGBRF vs IrrMapper, same roster (1985–2023 total ratio {S.xgbrf_oof.sum() / S.irrmapper_may.sum():.3f})")
ax.grid(alpha=0.3)

ax = axs[1, 0]
ax.loglog(hm.irrmapper_may.clip(lower=1), hm.xgbrf_oof.clip(lower=1), "o", ms=4, color="#762a83", alpha=0.7)
lim = [1, hm.max().max() * 1.3]; ax.plot(lim, lim, "k-", lw=0.8)
for h_, row in hm.sort_values("irrmapper_may").tail(8).iterrows():
    ax.annotate(h_, (row.irrmapper_may, row.xgbrf_oof), fontsize=7, xytext=(3, -8), textcoords="offset points")
ax.set_xlabel("IrrMapper irrigated acres (HA mean 1985–2023)"); ax.set_ylabel("XGBRF irrigated acres")
ax.set_title("By hydrographic area"); ax.grid(alpha=0.3)

ratio = (hm.xgbrf_oof / hm.irrmapper_may.replace(0, np.nan)).where(hm.irrmapper_may > 50)
K.ha_choropleth(axs[1, 1], ratio, "XGBRF / IrrMapper irrigated area\n(HAs with > 50 irrigated acres)", "ratio", "PuOr_r",
                mcolors.TwoSlopeNorm(1.0, 0.5, 1.5))
fig.suptitle("Irrigated area: IrrMapper and the XGBRF irrigation-status model (Nevada fields)", y=0.995)
fig.tight_layout()
K.savefig(fig, "fig09_irrigated_area_irrmapper_xgbrf")
S.round(1).to_csv(K.IMG.parent / "tables" / "irrigated_area_by_year.csv") if (K.IMG.parent / "tables").exists() else None
print(S.round(0).tail(12).to_string())
