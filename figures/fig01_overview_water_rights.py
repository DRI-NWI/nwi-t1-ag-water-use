#!/usr/bin/env python3
"""Fig. 1: irrigated fields by water-right source, hydrographic areas (including their extent
outside Nevada), and three inset systems: SW canals (101), GW pivots (153), mixed (108)."""

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch, Rectangle
from matplotlib.lines import Line2D

import _mapkit as K

f = K.fields()
ha = K.ha_polygons()
fig = plt.figure(figsize=(13, 12.5))
ax = fig.add_axes([0.0, 0.0, 0.64, 1.0])
K.base_map(ax)
for lab, col in K.SRC_COLORS.items():
    sub = f[f.source_label.eq(lab)]
    if len(sub):
        sub.plot(ax=ax, color=col, edgecolor=col, lw=0.4, zorder=6)
K.scalebar(ax, 100, loc=(0.60, 0.035))
ax.text(0.02, 0.98, "A", transform=ax.transAxes, fontsize=16, fontweight="bold", va="top")

insets = [("101", "Lahontan Valley (Carson Desert, HA 101)", "Surface-water canal system", "B"),
          ("153", "Diamond Valley (HA 153)", "Groundwater center-pivot system", "C"),
          ("108", "Mason Valley (HA 108)", "Mixed-source (conjunctive) system", "D")]
for i, (code, title, sub, letter) in enumerate(insets):
    fi = f[f.HA_CODE.eq(code.lstrip("0"))]
    c = fi.geometry.centroid
    x0, x1 = c.x.quantile([0.03, 0.97]); y0, y1 = c.y.quantile([0.03, 0.97])
    cx, cy, half = (x0 + x1) / 2, (y0 + y1) / 2, max(x1 - x0, (y1 - y0) * 1.1) / 2 * 1.08
    e = (cx - half, cx + half, cy - half / 1.1, cy + half / 1.1)
    ax.add_patch(Rectangle((e[0], e[2]), e[1] - e[0], e[3] - e[2], fill=False, lw=1.3, ec="black", zorder=9))
    ax.text(e[1] + 4e3, e[3], letter, fontsize=11, fontweight="bold", va="top", zorder=9)
    ia = fig.add_axes([0.655, 0.675 - i * 0.325, 0.34, 0.30])
    K.base_map(ia, e, ha_lw=0.8, county=False, labels=False)
    for lab, col in K.SRC_COLORS.items():
        sub_f = f[f.source_label.eq(lab)].cx[e[0]:e[1], e[2]:e[3]]
        if len(sub_f):
            sub_f.plot(ax=ia, color=col, edgecolor="white", lw=0.15, zorder=6)
    ac = fi.groupby("source_label")["Acres"].sum()
    tot = ac.sum()
    pct = {k: 100 * ac.get(v, 0) / tot for k, v in [("SW", "Surface water"), ("GW", "Groundwater"),
                                                       ("Mixed", "Mixed (both right types)")]}
    ia.set_title(title, loc="left", fontsize=10)
    ia.text(0.98, 0.97, f"{tot / 1e3:.0f} thousand acres of fields\nSW {pct['SW']:.0f}%  GW {pct['GW']:.0f}%  "
                        f"Mixed {pct['Mixed']:.0f}%", transform=ia.transAxes, ha="right", va="top", fontsize=8,
            bbox=dict(fc="white", ec="none", alpha=0.85))
    ia.text(0.5, 0.03, sub, transform=ia.transAxes, ha="center", fontsize=8.5, style="italic",
            bbox=dict(fc="white", ec="none", alpha=0.85))
    ia.text(0.02, 0.97, letter, transform=ia.transAxes, fontsize=14, fontweight="bold", va="top")
    K.scalebar(ia, 5, loc=(0.06, 0.12))

handles = [Patch(fc=c, ec=c, label=l) for l, c in K.SRC_COLORS.items()] + [
    Line2D([], [], color="#8c8c8c", lw=0.8, label="Hydrographic area boundary"),
    Line2D([], [], color="#555555", lw=0.8, ls="--", label="Nevada county boundary"),
    Line2D([], [], color="#222222", lw=1.0, label="State boundary"),
    Line2D([], [], color="#4a90c8", lw=1.0, label="River"),
    Line2D([], [], marker="s", ls="", mfc="white", mec="black", label="City or town"),
    Patch(fc="none", ec="black", label="Inset extent (B–D)")]
ax.legend(handles=handles, loc="lower left", title="Irrigated field, by water-right source", fontsize=8,
          title_fontsize=9, frameon=True, framealpha=0.95)
n = f.groupby("source_label").agg(n=("NWI_ID", "size"), ac=("Acres", "sum"))
note = "; ".join(f"{k}: {int(r.n):,} fields, {r.ac / 1e3:,.0f}k acres" for k, r in n.iterrows())
fig.text(0.0, -0.02, f"Fields: 2026-08-20 NWI boundary layer ({len(f):,} fields). {note}.\n"
         "Hydrographic areas: NWI/NDWR boundaries, including the parts outside Nevada. "
         "Projection: Albers equal-area (36°, 41° N; 117° W), NAD83.", fontsize=7.5, color="#444444")
K.savefig(fig, "fig01_fields_water_rights")
