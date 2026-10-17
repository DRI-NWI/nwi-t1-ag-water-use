"""Per-field 0-100 cm POLARIS texture via Earth Engine (vendored polaris_gee.py logic)."""

from __future__ import annotations

import pandas as pd

from . import constants as C
from .soil_texture import classify_textures

BATCH = 400
SIMPLIFY_M = 20
POLARIS_SCALE = 30


def _root_zone_mean(prop, ee):
    ic = ee.ImageCollection(f"{C.POLARIS_BASE}/{prop}_mean")
    total = float(sum(C.POLARIS_DEPTH_WEIGHTS))
    weighted = []
    for depth, w in zip(C.POLARIS_DEPTHS, C.POLARIS_DEPTH_WEIGHTS):
        img = ic.filter(ee.Filter.eq("system:index", f"{prop}_{depth}")).first().select("b1")
        weighted.append(img.multiply(w))
    return ee.ImageCollection(weighted).sum().divide(total).rename(prop)


def run(gpkg, project, cache_csv=None):
    import ee
    import geopandas as gpd
    import shapely

    have = pd.read_csv(cache_csv) if cache_csv and pd.io.common.file_exists(cache_csv) else pd.DataFrame(columns=["NWI_ID"])
    g = gpd.read_file(gpkg, columns=["NWI_ID"]).dissolve(by="NWI_ID", as_index=False)
    g = g[~g["NWI_ID"].isin(set(have["NWI_ID"]))]
    if g.empty:
        return have
    ee.Initialize(project=project)
    g = g.to_crs(32611)
    g["geometry"] = g.geometry.simplify(SIMPLIFY_M).buffer(0)
    g = g.to_crs(4326)
    g["geometry"] = shapely.force_2d(g.geometry.values)
    soil = (_root_zone_mean("sand", ee).addBands(_root_zone_mean("silt", ee)).addBands(_root_zone_mean("clay", ee)))
    rows = []
    for start in range(0, len(g), BATCH):
        b = g.iloc[start:start + BATCH]
        feats = [ee.Feature(ee.Geometry(geom.__geo_interface__, proj="EPSG:4326", geodesic=False), {"NWI_ID": fid})
                 for fid, geom in zip(b["NWI_ID"], b.geometry)]
        red = soil.reduceRegions(collection=ee.FeatureCollection(feats), reducer=ee.Reducer.mean(),
                                 scale=POLARIS_SCALE, tileScale=4)
        for f in red.getInfo()["features"]:
            p = f["properties"]
            rows.append({"NWI_ID": p.get("NWI_ID"), "sand": p.get("sand"), "silt": p.get("silt"), "clay": p.get("clay")})
        print(f"[polaris] {min(start + BATCH, len(g))}/{len(g)}", flush=True)
        if cache_csv:
            part = pd.DataFrame(rows)
            part["texture"] = classify_textures(part["sand"], part["silt"], part["clay"])
            pd.concat([have, part], ignore_index=True).to_csv(cache_csv, index=False)
    new = pd.DataFrame(rows)
    new["texture"] = classify_textures(new["sand"], new["silt"], new["clay"])
    out = pd.concat([have, new], ignore_index=True).drop_duplicates("NWI_ID", keep="last")
    if cache_csv:
        out.to_csv(cache_csv, index=False)
    return out
