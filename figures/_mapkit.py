"""Shared map layers, projection, styles and data loaders for the figures.

Layers are reprojected once to a Nevada Albers projection and cached under
<run>/figures_cache/. Paths come from config/nwi_t1_final.yaml plus the reference layers
below.
"""

from __future__ import annotations

import pickle
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from nwi_agwu import config as C  # noqa: E402

CFG = C.load()
P = C.RunPaths(CFG)
IMG = REPO / "docs" / "images"
IMG.mkdir(parents=True, exist_ok=True)
CACHE = P.root / "figures_cache"
CACHE.mkdir(parents=True, exist_ok=True)

# Albers equal-area, standard parallels 36 and 41 N, central meridian 117 W.
CRS = "+proj=aea +lat_1=36 +lat_2=41 +lat_0=38.5 +lon_0=-117 +datum=NAD83 +units=m +no_defs"

LAYERS = {
    "ha": "/Users/Peter.ReVelle@dri.edu/Documents/projects/NWI/shapefiles/nwi_investigations_boundaries_cleaned_20260819/ha_boundaries_cleaned_20260819.shp",
    "states": "/Users/Peter.ReVelle@dri.edu/Documents/projects/UCRC/datasets/OneDrive_1_10-7-2026/state_boundaries/raw/tl_2025_us_state.shp",
    "counties": "/Users/Peter.ReVelle@dri.edu/github/agricultural-water-projects/mixed-field-allocation/data/shapefiles/nevada_counties/Counties.shp",
    "rivers": "/Users/Peter.ReVelle@dri.edu/github/agricultural-water-projects/mixed-field-allocation/data/shapefiles/ne_10m_rivers_lake_centerlines/ne_10m_rivers_lake_centerlines.shp",
}
CITIES = {"Reno": (-119.81, 39.53), "Carson City": (-119.77, 39.16), "Fallon": (-118.78, 39.47),
          "Yerington": (-119.16, 38.99), "Lovelock": (-118.47, 40.18), "Winnemucca": (-117.74, 40.97),
          "Elko": (-115.76, 40.83), "Eureka": (-115.96, 39.51), "Ely": (-114.89, 39.25),
          "Tonopah": (-117.23, 38.07), "Pahrump": (-115.98, 36.21), "Las Vegas": (-115.14, 36.17)}

SRC_COLORS = {"Surface water": "#2166ac", "Groundwater": "#d0731c", "Mixed (both right types)": "#7b52ab",
              "No right attributed (assumed GW)": "#9a9a9a"}
COMP_COLORS = {"GW": "#d0731c", "SW": "#2166ac", "ALL": "#333333"}

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.titlesize": 10,
                     "axes.spines.top": False, "axes.spines.right": False, "savefig.dpi": 200,
                     "figure.dpi": 100})


def _cached(name, build):
    f = CACHE / f"{name}.pkl"
    if f.exists():
        return pickle.loads(f.read_bytes())
    obj = build()
    f.write_bytes(pickle.dumps(obj))
    return obj


def ha_polygons() -> gpd.GeoDataFrame:
    def build():
        g = gpd.read_file(LAYERS["ha"]).to_crs(CRS)
        g["HYD_AREA"] = g["BasinID"].astype(str).str.strip()
        d = g.dissolve(by="HYD_AREA", aggfunc={"BasinName": "first"}).reset_index()
        return d
    return _cached("ha", build)


def nevada() -> gpd.GeoDataFrame:
    return _cached("nevada", lambda: states().query("STUSPS == 'NV'"))


def states() -> gpd.GeoDataFrame:
    return _cached("states", lambda: gpd.read_file(LAYERS["states"]).query(
        "STUSPS in ['NV','CA','OR','ID','UT','AZ']").to_crs(CRS)[["STUSPS", "NAME", "geometry"]])


def counties() -> gpd.GeoDataFrame:
    return _cached("counties", lambda: gpd.read_file(LAYERS["counties"]).to_crs(CRS)[["NAME", "geometry"]])


def rivers() -> gpd.GeoDataFrame:
    def build():
        r = gpd.read_file(LAYERS["rivers"]).to_crs(CRS)
        box = ha_polygons().total_bounds
        return r.cx[box[0] - 60e3: box[2] + 60e3, box[1] - 60e3: box[3] + 60e3][["name", "featurecla", "geometry"]]
    return _cached("rivers", build)


def fields() -> gpd.GeoDataFrame:
    def build():
        g = gpd.read_file(CFG["inputs"]["fields_gpkg"], columns=["NWI_ID"]).to_crs(CRS)
        a = pd.read_parquet(P.field_attrs)
        g = g.merge(a, on="NWI_ID", how="left")
        g["source_label"] = np.select(
            [g["IRR_SRC_ASSUMED"].astype(bool), g["IRR_SRC_ORIGINAL"].eq(2), g["IRR_SRC_ORIGINAL"].eq(1),
             g["IRR_SRC_ORIGINAL"].eq(3)],
            ["No right attributed (assumed GW)", "Surface water", "Groundwater", "Mixed (both right types)"], "")
        return g
    return _cached("fields", build)


def extent(pad=25e3):
    b = ha_polygons().total_bounds
    return b[0] - pad, b[2] + pad, b[1] - pad, b[3] + pad


def base_map(ax, ext=None, ha_lw=0.35, county=True, river=True, labels=True, state_fill="#f4f4f2"):
    ext = ext or extent()
    st = states()
    st.plot(ax=ax, color=state_fill, edgecolor="none", zorder=0)
    ha_polygons().plot(ax=ax, color="#fbfaf5", edgecolor="none", zorder=0.4)
    nevada().plot(ax=ax, color="white", edgecolor="none", zorder=0.5)
    if river:
        rivers().plot(ax=ax, color="#4a90c8", lw=0.7, zorder=3)
    ha_polygons().boundary.plot(ax=ax, color="#8c8c8c", lw=ha_lw, zorder=4)
    if county:
        counties().boundary.plot(ax=ax, color="#555555", lw=0.5, linestyle="--", zorder=4)
    st.boundary.plot(ax=ax, color="#222222", lw=0.9, zorder=5)
    if labels:
        for name, (lon, lat) in CITIES.items():
            x, y = gpd.GeoSeries(gpd.points_from_xy([lon], [lat]), crs=4326).to_crs(CRS).iloc[0].coords[0]
            if ext[0] < x < ext[1] and ext[2] < y < ext[3]:
                ax.plot(x, y, "s", ms=3.5, mfc="white", mec="black", mew=0.8, zorder=7)
                ax.text(x + 6e3, y, name, fontsize=7, va="center", zorder=7,
                        bbox=dict(boxstyle="square,pad=0.1", fc="white", ec="none", alpha=0.6))
        for code, xy in {"CA": (-121.0, 37.6), "OR": (-119.5, 42.35), "ID": (-115.5, 42.35),
                         "UT": (-113.5, 39.5), "AZ": (-114.0, 35.6)}.items():
            x, y = gpd.GeoSeries(gpd.points_from_xy([xy[0]], [xy[1]]), crs=4326).to_crs(CRS).iloc[0].coords[0]
            if ext[0] < x < ext[1] and ext[2] < y < ext[3]:
                ax.text(x, y, {"CA": "CALIFORNIA", "OR": "OREGON", "ID": "IDAHO", "UT": "UTAH",
                               "AZ": "ARIZONA"}[code], fontsize=8, color="#777777", ha="center", zorder=6)
    ax.set_xlim(ext[0], ext[1])
    ax.set_ylim(ext[2], ext[3])
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(True); s.set_color("#333333")
    ax.set_aspect("equal")


def scalebar(ax, km=100, loc=(0.62, 0.04)):
    x0, x1 = ax.get_xlim(); y0, y1 = ax.get_ylim()
    x = x0 + loc[0] * (x1 - x0); y = y0 + loc[1] * (y1 - y0)
    ax.plot([x, x + km * 1e3], [y, y], color="black", lw=3, solid_capstyle="butt", zorder=8)
    ax.text(x + km * 500, y + 0.012 * (y1 - y0), f"{km} km", ha="center", fontsize=7, zorder=8)


# ---- run outputs -------------------------------------------------------------------------
def ha_annual(comp="ALL") -> pd.DataFrame:
    """HA x year: volumes summed over months, acres averaged over months (irrigated field-years)."""
    def build():
        h = pd.read_csv(P.summaries / f"ha_summaries_{comp}.csv", dtype={"HYD_AREA": str})
        vol = [c for c in h.columns if c.endswith("_acre_ft") and c not in ("Soil_Moisture_Cap_acre_ft", "Soil_Storage_acre_ft")]
        g = h.groupby(["HYD_AREA", "YEAR"])
        out = g[vol].sum()
        out["acres"] = g["ACRES_FTR_GEOM"].mean()
        out["acres_irr"] = g["ACRES_IRR"].mean()
        out["n_fields"] = g["n_fields"].max()
        for c in ["mixed_sw_ratio", "spi_sept12"]:
            if c in h.columns:
                out[c] = g[c].first()
        return out.reset_index()
    return _cached(f"ha_annual_{comp}", build)


def ha_mean(comp="ALL", years=None) -> pd.DataFrame:
    """41-year mean per HA (years without irrigated fields count as zero); depths after averaging."""
    a = ha_annual(comp)
    yrs = years or list(range(CFG["run"]["start_year"], CFG["run"]["end_year"] + 1))
    a = a[a.YEAR.isin(yrs)]
    num = [c for c in a.columns if c.endswith("_acre_ft") or c in ("acres", "acres_irr")]
    m = a.groupby("HYD_AREA")[num].sum() / len(yrs)
    for v in ["ETa_final", "PPT_vol", "Peff_vol", "NetET_final", "AR_final", "GW_subsidy", "NetET_pre_subsidy",
              "Carryforward_Used"]:
        m[f"{v}_ft"] = m[f"{v}_acre_ft"] / m["acres"].replace(0, np.nan)
    return m.reset_index()


def statewide(population="irrigated_field_months") -> pd.DataFrame:
    t = pd.read_csv(P.summaries / "statewide_annual_totals.csv")
    return t[t.population.eq(population)]


def savefig(fig, name):
    fig.savefig(IMG / f"{name}.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"[fig] docs/images/{name}.png")


def ha_choropleth(ax, values: pd.Series, title, unit, cmap="viridis", norm=None, fmt=None, missing="#e6e6e6",
                  ticks=None, ticklabels=None):
    """values indexed by HYD_AREA. HAs without a value are drawn grey."""
    from matplotlib import colors as mcolors
    ha = ha_polygons()
    g = ha.merge(values.rename("val"), left_on="HYD_AREA", right_index=True, how="left")
    base_map(ax, county=False, river=False, labels=False, ha_lw=0.2)
    g[g.val.isna()].plot(ax=ax, color=missing, edgecolor="none", zorder=1)
    if norm is None:
        v = g.val.dropna()
        norm = mcolors.Normalize(v.quantile(0.02), v.quantile(0.98))
    g[g.val.notna()].plot(ax=ax, column="val", cmap=cmap, norm=norm, edgecolor="none", zorder=1)
    ha.boundary.plot(ax=ax, color="white", lw=0.2, zorder=2)
    states().boundary.plot(ax=ax, color="#222222", lw=0.7, zorder=5)
    sm = mpl.cm.ScalarMappable(norm=norm, cmap=cmap)
    cb = plt.colorbar(sm, ax=ax, orientation="horizontal", fraction=0.04, pad=0.02, aspect=30)
    cb.set_label(unit, fontsize=8)
    if ticks is not None:
        cb.set_ticks(ticks)
        cb.set_ticklabels(ticklabels)
    cb.ax.tick_params(labelsize=7)
    ax.set_title(title, fontsize=9.5)
    return g
