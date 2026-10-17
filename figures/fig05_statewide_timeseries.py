#!/usr/bin/env python3
"""Fig. 5: statewide annual water balance, GW vs SW, irrigated area, and the monthly cycle."""

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import _mapkit as K

irr = K.statewide("irrigated_field_months")
allm = K.statewide("all_field_months")
A = irr[irr.component.eq("ALL")].set_index("YEAR")
G = irr[irr.component.eq("GW")].set_index("YEAR")
S = irr[irr.component.eq("SW")].set_index("YEAR")
Aall = allm[allm.component.eq("ALL")].set_index("YEAR")
acres = K.ha_annual("ALL").groupby("YEAR")["acres"].sum()
acres_g = K.ha_annual("GW").groupby("YEAR")["acres"].sum()
acres_s = K.ha_annual("SW").groupby("YEAR")["acres"].sum()
k = 1e3

fig, axs = plt.subplots(3, 2, figsize=(14, 12))
ax = axs[0, 0]
for c, lab, col in [("ETa_final_acre_ft", "ETa", "#b2182b"), ("PPT_vol_acre_ft", "Precipitation (PRISM)", "#4393c3"),
                    ("Peff_vol_acre_ft", "Effective precipitation (OTTER)", "#2166ac"),
                    ("NetET_final_acre_ft", "Net ET", "#e08214"), ("AR_final_acre_ft", "Applied water", "#542788")]:
    ax.plot(A.index, A[c] / k, label=lab, color=col, lw=1.8)
ax.set_ylabel("thousand acre-ft/yr"); ax.set_title("Water balance, irrigated field-years"); ax.legend(fontsize=8, ncol=2)

ax = axs[0, 1]
ax.bar(A.index, A["GW_subsidy_acre_ft"] / k, color="#1b7837", label="GW-ET subsidy")
ax.bar(A.index, A["Carryforward_Used_acre_ft"] / k, bottom=A["GW_subsidy_acre_ft"] / k, color="#80cdc1",
       label="Winter carry-forward used")
ax.set_ylabel("thousand acre-ft/yr"); ax.set_title("Terms removed from net ET"); ax.legend(fontsize=8)

ax = axs[1, 0]
ax.stackplot(G.index, G["NetET_final_acre_ft"] / k, S["NetET_final_acre_ft"] / k, colors=["#d0731c", "#2166ac"],
             labels=["GW", "SW"], alpha=0.9)
ax.set_ylabel("thousand acre-ft/yr"); ax.set_title("Net ET by source"); ax.legend(loc="upper left", fontsize=8)
ax2 = ax.twinx(); ax2.plot(G.index, 100 * G["NetET_final_acre_ft"] / A["NetET_final_acre_ft"], "k-", lw=1.2)
ax2.set_ylabel("GW share (%)"); ax2.set_ylim(0, 100); ax2.spines["right"].set_visible(True)

ax = axs[1, 1]
ax.stackplot(G.index, G["AR_final_acre_ft"] / k, S["AR_final_acre_ft"] / k, colors=["#d0731c", "#2166ac"],
             labels=["GW", "SW"], alpha=0.9)
ax.set_ylabel("thousand acre-ft/yr"); ax.set_title("Applied water by source"); ax.legend(loc="upper left", fontsize=8)

ax = axs[2, 0]
ax.stackplot(acres_g.index, acres_g / k, acres_s / k, colors=["#d0731c", "#2166ac"], labels=["GW", "SW"], alpha=0.9)
ax.set_ylabel("thousand acres"); ax.set_title("Irrigated field area (IrrMapper > 50%), by source"); ax.legend(fontsize=8)

ax = axs[2, 1]
for c, lab, col in [("ETa_final_acre_ft", "ETa", "#b2182b"), ("NetET_final_acre_ft", "Net ET", "#e08214"),
                    ("GW_subsidy_acre_ft", "GW-ET subsidy", "#1b7837")]:
    ax.plot(A.index, A[c] / k, color=col, lw=1.8, label=f"{lab}, irrigated field-years")
    ax.plot(Aall.index, Aall[c] / k, color=col, lw=1.2, ls="--", label=f"{lab}, all field-months")
ax.set_ylabel("thousand acre-ft/yr"); ax.set_title("Irrigated field-years vs all field-months"); ax.legend(fontsize=7, ncol=2)
for a in axs.flat:
    a.set_xlim(1984.5, 2025.5); a.grid(alpha=0.3)
fig.suptitle("Statewide annual totals, 1985–2025 (all HAs, including fields outside Nevada)", y=0.995)
fig.tight_layout()
K.savefig(fig, "fig05_statewide_annual")

# ---- monthly cycle -------------------------------------------------------------------------
h = pd.read_csv(K.P.summaries / "ha_summaries_ALL.csv")
cols = ["ETa_final_acre_ft", "Peff_vol_acre_ft", "PPT_vol_acre_ft", "GW_subsidy_acre_ft", "NetET_pre_subsidy_acre_ft",
        "NetET_intermediate_acre_ft", "Carryforward_Used_acre_ft", "Soil_Storage_acre_ft", "NetET_final_acre_ft",
        "Soil_Moisture_Overflow_acre_ft"]
m = h.groupby(["YEAR", "MONTH"])[cols].sum().groupby("MONTH").mean() / k
fig, axs = plt.subplots(1, 2, figsize=(14, 4.8))
ax = axs[0]
for c, lab, col, ls in [("ETa_final_acre_ft", "ETa", "#b2182b", "-"), ("PPT_vol_acre_ft", "Precipitation", "#4393c3", "-"),
                        ("Peff_vol_acre_ft", "Effective precipitation", "#2166ac", "--"),
                        ("NetET_pre_subsidy_acre_ft", "ETa − Peff", "#999999", "-"),
                        ("NetET_final_acre_ft", "Net ET (final)", "#e08214", "-")]:
    ax.plot(m.index, m[c], ls, color=col, lw=2, label=lab, marker="o", ms=3)
ax.axhline(0, color="k", lw=0.6); ax.set_xticks(range(1, 13)); ax.set_ylabel("thousand acre-ft/month")
ax.set_title("Mean monthly water balance, irrigated field-years"); ax.legend(fontsize=8)
ax = axs[1]
ax.bar(m.index - 0.2, m["GW_subsidy_acre_ft"], 0.4, color="#1b7837", label="GW-ET subsidy")
ax.bar(m.index + 0.2, m["Carryforward_Used_acre_ft"], 0.4, color="#80cdc1", label="Carry-forward used")
ax.plot(m.index, m["Soil_Storage_acre_ft"], "k-o", ms=3, lw=1.2, label="Soil storage at month end")
ax.plot(m.index, m["Soil_Moisture_Overflow_acre_ft"], "-s", color="#8c510a", ms=3, lw=1, label="Storage overflow")
ax.set_xticks(range(1, 13)); ax.set_ylabel("thousand acre-ft/month"); ax.legend(fontsize=8)
ax.set_title("Subsidy, carry-forward and soil storage by month")
for a in axs: a.grid(alpha=0.3)
fig.tight_layout()
K.savefig(fig, "fig06_monthly_cycle")
