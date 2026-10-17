#!/usr/bin/env python3
"""Figs. 10-14: groundwater-ET subsidy and the depth-to-water products.

New DTW = nv_dtw_parity_statewide_100m (used in the run). Ma = Ma et al. (2026) DTW resampled to
the same 100 m grid. Both go through the same zonal stats, Tan (2025) parameters and water
balance; subsidy volumes are 1985-2025 means over irrigated field-years."""

import sys

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import colors as mcolors
from matplotlib.lines import Line2D

import _mapkit as K
from nwi_agwu.subsidy import gwfrac, models, soil_texture

CFG, P = K.CFG, K.P
NY = CFG["run"]["end_year"] - CFG["run"]["start_year"] + 1
MA = P.subsidy / "ma_comparison"
attrs = pd.read_parquet(P.field_attrs)
new = pd.read_parquet(P.field_gwfrac)
f_ma_path = MA / "field_gwfrac_ma.parquet"
if not f_ma_path.exists():
    base = pd.read_parquet(P.field_base, columns=["NWI_ID", "MONTH", "ETO_MM"])
    from nwi_agwu import fields as F
    a2 = F.apply_static_overrides(attrs, CFG["allocation"]["static_overrides"])
    ma = gwfrac.compute(a2, pd.read_parquet(MA / "field_wtd_ma.parquet"), pd.read_parquet(MA / "field_wtd_pixels_ma.parquet"),
                        pd.read_csv(P.field_texture), gwfrac.growing_season_eto(base, CFG["gw_subsidy"]["growing_season_months"]), CFG)
    ma.to_parquet(f_ma_path, index=False)
ma = pd.read_parquet(f_ma_path)

fy_path = MA / "field_subsidy_new_vs_ma.parquet"
if not fy_path.exists():
    d = pd.read_parquet(P.fields / "field_summaries_ALL.parquet",
                        columns=["NWI_ID", "YEAR", "HYD_AREA", "STATE", "%_IRRIGATED", "ETa_final_acre_ft",
                                 "NetET_pre_subsidy_acre_ft", "GW_subsidy_acre_ft", "ACRES_FTR_GEOM"])
    d = d[d["%_IRRIGATED"] > 0.5]
    d["f_ma"] = d.NWI_ID.map(ma.set_index("NWI_ID")["gw_frac"]).fillna(0)
    d["sub_ma"] = np.minimum(d.f_ma * d.ETa_final_acre_ft, d.NetET_pre_subsidy_acre_ft.clip(lower=0))
    fy = d.groupby(["NWI_ID", "YEAR", "HYD_AREA", "STATE"])[["ETa_final_acre_ft", "GW_subsidy_acre_ft", "sub_ma"]].sum()
    fy["acres"] = d.groupby(["NWI_ID", "YEAR", "HYD_AREA", "STATE"])["ACRES_FTR_GEOM"].first()
    fy.reset_index().to_parquet(fy_path, index=False)
fy = pd.read_parquet(fy_path)
cover = set(ma.loc[ma.dtw_available, "NWI_ID"])
fld = fy.groupby(["NWI_ID", "HYD_AREA", "STATE"])[["ETa_final_acre_ft", "GW_subsidy_acre_ft", "sub_ma"]].sum().div(NY).reset_index()
fld = fld.merge(new[["NWI_ID", "wtd_mean_m", "dtw_sigma_mean_m", "gw_frac", "texture", "root_depth_cm"]], on="NWI_ID") \
         .merge(ma[["NWI_ID", "wtd_mean_m", "gw_frac"]].rename(columns={"wtd_mean_m": "wtd_ma_m", "gw_frac": "f_ma"}), on="NWI_ID")
both = fld[fld.NWI_ID.isin(cover)]
gf = K.fields()
cent = gf.set_index("NWI_ID").geometry.centroid

# ---- Fig. 10: field subsidy maps -------------------------------------------------------------
fig, axs = plt.subplots(1, 2, figsize=(15, 9.5))
norm = mcolors.Normalize(0, 50)
for ax, col, lab in [(axs[0], "GW_subsidy_acre_ft", "New DTW (parity 100 m)"), (axs[1], "sub_ma", "Ma et al. DTW (100 m)")]:
    K.base_map(ax, county=False, labels=False)
    s = both[both[col] > 0.5].sort_values(col)
    xy = cent.reindex(s.NWI_ID)
    sc = ax.scatter(xy.x, xy.y, s=np.sqrt(s[col]) * 4, c=100 * s[col] / s.ETa_final_acre_ft, cmap="YlGnBu", norm=norm,
                    edgecolor="#333333", lw=0.2, zorder=7)
    ax.set_title(f"{lab}\nGW-ET subsidy {both[col].sum():,.0f} acre-ft/yr on fields covered by both products "
                 f"({100 * both[col].sum() / both.ETa_final_acre_ft.sum():.1f}% of ETa)", fontsize=9.5)
cb = fig.colorbar(sc, ax=axs, fraction=0.025, pad=0.01, label="Groundwater share of ETa (%)")
for v in [10, 100, 1000]:
    axs[0].scatter([], [], s=np.sqrt(v) * 4, c="#bbbbbb", edgecolor="#333333", lw=0.3, label=f"{v:,} acre-ft/yr")
axs[0].legend(title="Field subsidy", loc="lower left", fontsize=8)
fig.suptitle("Groundwater-ET subsidy by field, 1985–2025 mean, irrigated field-years", y=0.98)
K.savefig(fig, "fig10_gw_subsidy_fields_new_vs_ma")

# ---- Fig. 11: depth to water ----------------------------------------------------------------
fig = plt.figure(figsize=(17, 8.2))
gs = fig.add_gridspec(2, 4, width_ratios=[1, 1.15, 1.15, 1.15])
ax = fig.add_subplot(gs[0, 0])
hb = ax.hexbin(both.wtd_ma_m.clip(0.1, 300), both.wtd_mean_m.clip(0.1, 300), gridsize=60, xscale="log", yscale="log",
               bins="log", cmap="viridis", mincnt=1)
ax.plot([0.1, 300], [0.1, 300], "w-", lw=1)
r = np.corrcoef(np.log(both.wtd_ma_m.clip(0.1)), np.log(both.wtd_mean_m.clip(0.1)))[0, 1]
ax.set_xlabel("Ma et al. DTW (m)"); ax.set_ylabel("New DTW (m)")
ax.set_title(f"Field mean DTW (n = {len(both):,})\nmedian {both.wtd_ma_m.median():.1f} vs {both.wtd_mean_m.median():.1f} m, r(log) = {r:.2f}")
ax = fig.add_subplot(gs[1, 0])
bins = np.logspace(-1, np.log10(300), 50)
ax.hist(both.wtd_ma_m.clip(0.1, 300), bins, alpha=0.6, label="Ma et al.", color="#999999")
ax.hist(both.wtd_mean_m.clip(0.1, 300), bins, alpha=0.6, label="New DTW", color="#2166ac")
ax.set_xscale("log"); ax.set_xlabel("Field mean DTW (m)"); ax.set_ylabel("fields"); ax.legend()
acres = both.merge(attrs[["NWI_ID", "Acres"]], on="NWI_ID")
hw = acres.groupby("HYD_AREA").apply(lambda g: pd.Series({
    "new": np.average(g.wtd_mean_m, weights=g.Acres), "ma": np.average(g.wtd_ma_m, weights=g.Acres)}), include_groups=False)
dn = mcolors.LogNorm(1, 100)
K.ha_choropleth(fig.add_subplot(gs[:, 1]), hw["new"], "New DTW, HA acre-weighted mean", "m", "viridis_r", dn)
K.ha_choropleth(fig.add_subplot(gs[:, 2]), hw["ma"], "Ma et al. DTW, HA acre-weighted mean", "m", "viridis_r", dn)
K.ha_choropleth(fig.add_subplot(gs[:, 3]), hw["new"] - hw["ma"], "New − Ma (positive = deeper)", "m", "RdBu_r",
                mcolors.TwoSlopeNorm(0, -15, 15))
fig.suptitle("Depth to water under the fields: new parity DTW vs Ma et al. (both on the 100 m grid)", y=0.995)
fig.tight_layout()
K.savefig(fig, "fig11_dtw_new_vs_ma")

# ---- Fig. 12: subsidy vs DTW, f curves, basin ranking ----------------------------------------
fig, axs = plt.subplots(1, 3, figsize=(17, 5.8))
ax = axs[0]
for col, w, lab, c in [("GW_subsidy_acre_ft", "wtd_mean_m", "New DTW", "#2166ac"), ("sub_ma", "wtd_ma_m", "Ma et al.", "#999999")]:
    s = both[both.ETa_final_acre_ft > 0]
    ax.scatter(s[w].clip(0.05, 50), 100 * s[col] / s.ETa_final_acre_ft, s=4, alpha=0.35, color=c, label=lab)
ax.set_xscale("log"); ax.set_xlabel("Field mean DTW (m)"); ax.set_ylabel("Subsidy (% of field ETa)")
ax.set_title("Field subsidy share vs depth to water"); ax.legend(markerscale=4); ax.grid(alpha=0.3)
ax = axs[1]
soil_texture.TAN_PARAMS_CSV = CFG["inputs"]["tan_params_csv"]
d = np.linspace(0, 16, 400)
tops = both.texture.value_counts().index[:5]
for t, c in zip(tops, plt.cm.tab10.colors):
    for rd, ls in [(100, "-"), (200, "--")]:
        dd, de, p = soil_texture.params_for(t, rd, 5.0)
        ax.plot(d, models.tan_fraction(d, dd, de, p), ls, color=c, label=f"{t}, {rd} cm roots" if rd == 100 else None)
ax.set_xlabel("Depth to water (m)"); ax.set_ylabel("f = ET from groundwater / ETa")
ax.set_title("Tan et al. (2025) f(d), five most common textures\n(solid 100 cm roots, dashed 200 cm; ETo 5 mm/d)")
ax.legend(fontsize=7); ax.grid(alpha=0.3)
ax = axs[2]
hn = both.groupby("HYD_AREA")[["GW_subsidy_acre_ft", "sub_ma"]].sum()
names = attrs.drop_duplicates("HYD_AREA").set_index("HYD_AREA")["HA_NAME_CODE"]
top = hn.max(axis=1).sort_values(ascending=False).head(15).index[::-1]
y = np.arange(len(top))
ax.barh(y + 0.2, hn.loc[top, "GW_subsidy_acre_ft"], 0.4, color="#2166ac", label="New DTW")
ax.barh(y - 0.2, hn.loc[top, "sub_ma"], 0.4, color="#999999", label="Ma et al.")
ax.set_yticks(y); ax.set_yticklabels([names.get(h, h) for h in top], fontsize=8)
ax.set_xlabel("acre-ft/yr"); ax.set_title("15 largest HA subsidies"); ax.legend()
fig.tight_layout()
K.savefig(fig, "fig12_subsidy_vs_dtw_and_basins")

# ---- Fig. 13: HA subsidy maps -----------------------------------------------------------------
hall = fld.groupby("HYD_AREA")[["ETa_final_acre_ft", "GW_subsidy_acre_ft", "sub_ma"]].sum()
cnt = fld.assign(has=fld.GW_subsidy_acre_ft > 0.5).groupby("HYD_AREA").agg(n=("NWI_ID", "size"), k=("has", "sum"))
fig, axs = plt.subplots(1, 5, figsize=(21, 7.5))
vn = mcolors.LogNorm(10, hall[["GW_subsidy_acre_ft", "sub_ma"]].max().max())
K.ha_choropleth(axs[0], hall.GW_subsidy_acre_ft.where(hall.GW_subsidy_acre_ft > 1), f"Subsidy, new DTW\n{hall.GW_subsidy_acre_ft.sum():,.0f} acre-ft/yr (all fields)", "acre-ft/yr", "YlGnBu", vn)
K.ha_choropleth(axs[1], hall.sub_ma.where(hall.sub_ma > 1), f"Subsidy, Ma et al.\n{hall.sub_ma.sum():,.0f} acre-ft/yr (NV fields)", "acre-ft/yr", "YlGnBu", vn)
K.ha_choropleth(axs[2], (hall.GW_subsidy_acre_ft - hall.sub_ma).where(hall.ETa_final_acre_ft > 0), "New − Ma", "acre-ft/yr", "PuOr",
                mcolors.SymLogNorm(10, vmin=-3000, vmax=3000))
K.ha_choropleth(axs[3], 100 * hall.GW_subsidy_acre_ft / hall.ETa_final_acre_ft, "Subsidy, % of ETa (new DTW)", "%", "YlGnBu",
                mcolors.Normalize(0, 15))
K.ha_choropleth(axs[4], 100 * cnt.k / cnt.n, "Fields with subsidy (> 0.5 acre-ft/yr)", "% of irrigated fields", "BuPu",
                mcolors.Normalize(0, 100))
fig.suptitle("Groundwater-ET subsidy by hydrographic area, 1985–2025 mean, irrigated field-years", y=0.99)
fig.tight_layout()
K.savefig(fig, "fig13_subsidy_ha_new_vs_ma")

# ---- Fig. 14: annual subsidy and DTW uncertainty ---------------------------------------------
fig, axs = plt.subplots(1, 2, figsize=(15, 6))
yr = fy[fy.NWI_ID.isin(cover)].groupby("YEAR")[["GW_subsidy_acre_ft", "sub_ma"]].sum() / 1e3
yr_all = fy.groupby("YEAR")["GW_subsidy_acre_ft"].sum() / 1e3
ax = axs[0]
ax.plot(yr_all.index, yr_all, "-", color="#08306b", lw=2, label="New DTW, all fields")
ax.plot(yr.index, yr.GW_subsidy_acre_ft, "--", color="#2166ac", lw=1.5, label="New DTW, fields covered by Ma")
ax.plot(yr.index, yr.sub_ma, "-", color="#999999", lw=2, label="Ma et al.")
ax.set_ylabel("thousand acre-ft/yr"); ax.set_title("Annual GW-ET subsidy, irrigated field-years"); ax.legend(); ax.grid(alpha=0.3)
ax = axs[1]
K.base_map(ax, county=False, labels=False)
s = fld[fld.GW_subsidy_acre_ft > 0.5]
xy = cent.reindex(s.NWI_ID)
sc = ax.scatter(xy.x, xy.y, s=6, c=s.dtw_sigma_mean_m, cmap="magma_r", norm=mcolors.Normalize(0, 15), zorder=7)
fig.colorbar(sc, ax=ax, fraction=0.03, label="DTW uncertainty (sigma, m)")
ax.set_title("New-DTW uncertainty at fields with a subsidy")
fig.tight_layout()
K.savefig(fig, "fig14_subsidy_annual_and_sigma")

# ---- table ------------------------------------------------------------------------------------
t = hall.join(hw.rename(columns={"new": "dtw_new_m", "ma": "dtw_ma_m"}), how="left")
t.insert(0, "HA_NAME_CODE", [names.get(h, h) for h in t.index])
t = t.rename(columns={"ETa_final_acre_ft": "ETa_acre_ft_yr", "GW_subsidy_acre_ft": "subsidy_new_acre_ft_yr", "sub_ma": "subsidy_ma_acre_ft_yr"})
t.round(2).sort_values("subsidy_new_acre_ft_yr", ascending=False).to_csv(K.REPO / "docs" / "tables" / "ha_gw_subsidy_new_vs_ma.csv")
print(f"covered fields {len(both):,}; subsidy new {both.GW_subsidy_acre_ft.sum():,.0f} vs Ma {both.sub_ma.sum():,.0f}; all {fld.GW_subsidy_acre_ft.sum():,.0f}")
