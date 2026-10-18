#!/usr/bin/env python3
"""Fig. 7: dynamic SPI 50/50 allocation - Sept SPI-12 by HA and year, mixed-field SW share,
and the SPI-to-share rule, with basin examples."""

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import colors as mcolors

import _mapkit as K

al = pd.read_csv(K.P.summaries / "dynamic_allocation_stats.csv").rename(columns={"year": "YEAR"})
al["HA_CODE"] = al["basin_code"].str.extract(r"\(([^)]+)\)\s*$")[0].str.lstrip("0")
spi_table = pd.read_csv(K.P.spi_table, dtype={"HA": str})
al["spi_source"] = al["HA_CODE"].map(dict(zip(spi_table["HA"].str.lstrip("0"), spi_table["spi_source"])))
attrs = K.field_attributes()
mixed_ac = attrs[attrs.IRR_SRC.eq(3)].groupby("HA_CODE")["Acres"].sum()
names = attrs.drop_duplicates("HA_CODE").set_index("HA_CODE")["HA_NAME_CODE"]
order = mixed_ac.sort_values(ascending=False).index
spi = al.pivot(index="HA_CODE", columns="YEAR", values="spi_value").reindex(order)
shr = al.pivot(index="HA_CODE", columns="YEAR", values="sw_ratio").reindex(order)
top = order[:40]

fig = plt.figure(figsize=(16, 13))
gs = fig.add_gridspec(2, 3, height_ratios=[1.35, 1], hspace=0.28, wspace=0.3)
ax = fig.add_subplot(gs[0, :2])
im = ax.imshow(spi.loc[top], aspect="auto", cmap="BrBG", norm=mcolors.TwoSlopeNorm(0, -2.5, 2.5))
ax.set_yticks(range(len(top))); ax.set_yticklabels([f"{names[c]}  ({mixed_ac[c] / 1e3:.1f}k ac)" for c in top], fontsize=6.5)
yrs = spi.columns
ax.set_xticks(range(0, len(yrs), 5)); ax.set_xticklabels(yrs[::5])
plt.colorbar(im, ax=ax, fraction=0.02, pad=0.01, label="Sept SPI-12")
ax.set_title("Sept SPI-12 (PRISM contributing area or HA), 40 HAs with the most mixed-field acreage")
ax = fig.add_subplot(gs[0, 2])
x = np.linspace(-3, 3, 200)
ax.plot(x, np.clip(0.5 + 0.15 * x, 0.2, 0.8), "k-", lw=2, label="Default: 0.5 ± 0.15·SPI, 0.2–0.8")
fl = al[al.HA_CODE.eq("117")]
ax.plot(fl.spi_value, fl.sw_ratio, "o", color="#7b52ab", ms=4, label="Fish Lake Valley (117) rule")
d = al[~al.HA_CODE.eq("117")]
ax.plot(d.spi_value, d.sw_ratio, ".", color="#999999", ms=2, alpha=0.4, label="All other basin-years")
ax.set_xlabel("Sept SPI-12"); ax.set_ylabel("Mixed-field SW share"); ax.legend(fontsize=8); ax.grid(alpha=0.3)
ax.set_title("SPI-to-share rule")
ax = fig.add_subplot(gs[1, :2])
for c, col in [("101", "#2166ac"), ("108", "#7b52ab"), ("107", "#1b7837"), ("117", "#d0731c"), ("088", "#b2182b")]:
    cc = c.lstrip("0")
    if cc in shr.index:
        ax.plot(shr.columns, shr.loc[cc], "-o", ms=3, color=col, label=names.get(cc, cc))
w = (shr.mul(mixed_ac.reindex(shr.index), axis=0)).sum() / mixed_ac.reindex(shr.index).sum()
ax.plot(w.index, w.values, "k-", lw=2.5, label="Statewide, mixed-acre weighted")
ax.axhline(0.5, color="k", lw=0.6, ls=":")
ax.set_ylabel("Mixed-field SW share"); ax.set_ylim(0.15, 0.85); ax.legend(fontsize=8, ncol=3); ax.grid(alpha=0.3)
ax.set_title("Mixed-field SW share by year")
ax = fig.add_subplot(gs[1, 2])
src = al.groupby("HA_CODE")["spi_source"].first()
K.ha_choropleth(ax, src.map({"prism_contributing_area": 1, "prism_ha": 0}).rename(None)
                .set_axis(src.index.map(lambda c: attrs.drop_duplicates("HA_CODE").set_index("HA_CODE")["HYD_AREA"].get(c))),
                "Precipitation area used for SPI", "", mcolors.ListedColormap(["#c7e9c0", "#238b45"]),
                mcolors.Normalize(-0.5, 1.5), ticks=[0, 1], ticklabels=["HA", "SW contributing area"])
K.savefig(fig, "fig07_allocation_spi")
