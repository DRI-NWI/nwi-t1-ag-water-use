#!/usr/bin/env python3
"""Figs. 2-4: HA maps of the 1985-2025 mean water balance (irrigated field-years).

Fig. 2  volumes (acre-ft/yr), Fig. 3 depths (ft/yr), Fig. 4 GW and SW components."""

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import colors as mcolors

import _mapkit as K

A = K.ha_mean("ALL").set_index("HYD_AREA")
GW = K.ha_mean("GW").set_index("HYD_AREA")
SW = K.ha_mean("SW").set_index("HYD_AREA")
present = A["acres"] > 0


def logn(s, lo=None):
    v = s[s > 0]
    return mcolors.LogNorm(lo or max(v.quantile(0.02), 1), v.max())


# ---- Fig. 2 volumes -------------------------------------------------------------------------
vols = [("acres", "Irrigated field area", "acres (annual mean)", "Greens"),
        ("ETa_final_acre_ft", "ETa", "acre-ft/yr", "YlOrRd"),
        ("PPT_vol_acre_ft", "Precipitation (PRISM)", "acre-ft/yr", "Blues"),
        ("Peff_vol_acre_ft", "Effective precipitation (OTTER)", "acre-ft/yr", "PuBu"),
        ("GW_subsidy_acre_ft", "Groundwater-ET subsidy", "acre-ft/yr", "BuGn"),
        ("Carryforward_Used_acre_ft", "Winter carry-forward used", "acre-ft/yr", "GnBu"),
        ("NetET_final_acre_ft", "Net ET", "acre-ft/yr", "YlOrBr"),
        ("AR_final_acre_ft", "Applied water", "acre-ft/yr", "OrRd")]
fig, axs = plt.subplots(2, 4, figsize=(16, 11.5))
for ax, (c, t, u, cm) in zip(axs.flat, vols):
    s = A.loc[present, c]
    K.ha_choropleth(ax, s.where(s > 0), f"{t}\nstate total {s.sum():,.0f}", u, cm, logn(s))
fig.suptitle("Hydrographic-area water balance, 1985–2025 mean, irrigated field-years (log scale)", y=0.995)
fig.tight_layout()
K.savefig(fig, "fig02_ha_volumes")

# ---- Fig. 3 depths --------------------------------------------------------------------------
deps = [("ETa_final_ft", "ETa", "YlOrRd", (0, 4)), ("PPT_vol_ft", "Precipitation", "Blues", (0, 2)),
        ("Peff_vol_ft", "Effective precipitation", "PuBu", (0, 2)), ("GW_subsidy_ft", "GW-ET subsidy", "BuGn", (0, 0.5)),
        ("Carryforward_Used_ft", "Carry-forward used", "GnBu", (0, 0.3)), ("NetET_final_ft", "Net ET", "YlOrBr", (0, 3.5)),
        ("AR_final_ft", "Applied water", "OrRd", (0, 6))]
fig, axs = plt.subplots(2, 4, figsize=(16, 11.5))
for ax, (c, t, cm, (lo, hi)) in zip(axs.flat, deps):
    w = (A.loc[present, c] * A.loc[present, "acres"]).sum() / A.loc[present, "acres"].sum()
    K.ha_choropleth(ax, A.loc[present, c], f"{t}\nstate mean {w:.2f} ft/yr", "ft/yr", cm, mcolors.Normalize(lo, hi))
ax = axs.flat[7]
r = (A["NetET_final_acre_ft"] / A["ETa_final_acre_ft"]).where(present)
K.ha_choropleth(ax, r, "Net ET / ETa", "fraction", "magma_r", mcolors.Normalize(0.4, 1.0))
fig.suptitle("Hydrographic-area water balance depths (volume ÷ irrigated acres), 1985–2025 mean", y=0.995)
fig.tight_layout()
K.savefig(fig, "fig03_ha_depths")

# ---- Fig. 4 GW / SW ---------------------------------------------------------------------------
fig, axs = plt.subplots(2, 3, figsize=(13, 11.5))
gwv, swv = GW["NetET_final_acre_ft"].reindex(A.index).fillna(0), SW["NetET_final_acre_ft"].reindex(A.index).fillna(0)
norm = logn(gwv.add(swv))
K.ha_choropleth(axs[0, 0], gwv.where(gwv > 0), f"GW net ET\nstate {gwv.sum():,.0f} acre-ft/yr", "acre-ft/yr", "Oranges", norm)
K.ha_choropleth(axs[0, 1], swv.where(swv > 0), f"SW net ET\nstate {swv.sum():,.0f} acre-ft/yr", "acre-ft/yr", "Blues", norm)
share = (100 * gwv / (gwv + swv)).where(present)
K.ha_choropleth(axs[0, 2], share, "GW share of net ET", "%", "RdBu_r", mcolors.Normalize(0, 100))
gwa, swa = GW["AR_final_acre_ft"].reindex(A.index).fillna(0), SW["AR_final_acre_ft"].reindex(A.index).fillna(0)
na = logn(gwa.add(swa))
K.ha_choropleth(axs[1, 0], gwa.where(gwa > 0), f"GW applied water\nstate {gwa.sum():,.0f} acre-ft/yr", "acre-ft/yr", "Oranges", na)
K.ha_choropleth(axs[1, 1], swa.where(swa > 0), f"SW applied water\nstate {swa.sum():,.0f} acre-ft/yr", "acre-ft/yr", "Blues", na)
ann = K.ha_annual("ALL")
msr = ann.groupby("HYD_AREA")["mixed_sw_ratio"].mean()
K.ha_choropleth(axs[1, 2], msr, "Mixed-field SW share (mean of years)", "fraction", "RdBu", mcolors.Normalize(0.2, 0.8))
fig.suptitle("Groundwater and surface-water components, 1985–2025 mean, irrigated field-years", y=0.995)
fig.tight_layout()
K.savefig(fig, "fig04_ha_gw_sw")
