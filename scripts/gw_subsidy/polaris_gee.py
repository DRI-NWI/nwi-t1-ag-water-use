"""Per-field soil texture from POLARIS via the Earth Engine Python API.

Builds a 0-100 cm root-zone, thickness-weighted mean of POLARIS sand/silt/clay
(% by mass), samples it over field polygons with reduceRegions, classifies each
field into a USDA texture class, and caches the result to Data/Inputs/field_texture.csv.

POLARIS layers live on GEE (projects/sat-io/open-datasets/polaris/{sand,silt,clay}_mean),
each an ImageCollection of 6 depth bands (system:index e.g. 'sand_0_5'). Only the
field IDs passed in are sampled, so the fast-track samples just the shallow-basin fields.
A local-COG fallback is documented in the README for headless environments.

NWI T1: fields are read by NWI_ID (= OPENET_ID in the 2026-08-20 layer); the Earth Engine
project is nasa-water (config.GEE_PROJECT).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config, soil_texture

BATCH = 400          # fields per reduceRegions request
SIMPLIFY_M = 20      # geometry simplification tolerance (m); texture is smooth at 30 m
POLARIS_SCALE = 30


def _root_zone_mean(prop, ee):
    """0-100 cm thickness-weighted mean image (single band named `prop`) for sand/silt/clay."""
    ic = ee.ImageCollection(f"{config.POLARIS_BASE}/{prop}_mean")
    total = float(sum(config.POLARIS_DEPTH_WEIGHTS))
    weighted = []
    for depth, w in zip(config.POLARIS_DEPTHS, config.POLARIS_DEPTH_WEIGHTS):
        img = ic.filter(ee.Filter.eq("system:index", f"{prop}_{depth}")).first().select("b1")
        weighted.append(img.multiply(w))
    return ee.ImageCollection(weighted).sum().divide(total).rename(prop)


def _soil_image(ee):
    return (_root_zone_mean("sand", ee)
            .addBands(_root_zone_mean("silt", ee))
            .addBands(_root_zone_mean("clay", ee)))


def _fields_4326(field_ids):
    import geopandas as gpd
    import shapely
    gdf = gpd.read_file(config.FIELDS_GPKG, columns=["NWI_ID"]).rename(columns={"NWI_ID": "OPENET_ID"})  # NWI T1
    gdf = gdf[gdf["OPENET_ID"].notna()]
    if field_ids is not None:
        gdf = gdf[gdf["OPENET_ID"].isin(set(field_ids))]
    gdf = gdf.dissolve(by="OPENET_ID", as_index=False)
    gdf = gdf.to_crs(32611)
    gdf["geometry"] = gdf.geometry.simplify(SIMPLIFY_M).buffer(0)
    gdf = gdf.to_crs(4326)
    # gpkg geometries are 3D; Earth Engine rejects Z coordinates -> drop to 2D.
    gdf["geometry"] = shapely.force_2d(gdf.geometry.values)
    return gdf


def run(field_ids=None, force=False):
    """Sample POLARIS texture for `field_ids` (all if None). Cached; returns a DataFrame."""
    have = pd.DataFrame(columns=["OPENET_ID"])
    if config.FIELD_TEXTURE_CSV.exists() and not force:
        have = pd.read_csv(config.FIELD_TEXTURE_CSV)
        if field_ids is None or set(field_ids).issubset(set(have["OPENET_ID"])):
            out = have if field_ids is None else have[have["OPENET_ID"].isin(set(field_ids))]
            return out.reset_index(drop=True)

    import ee
    ee.Initialize(project=config.GEE_PROJECT)

    todo_ids = None if field_ids is None else sorted(set(field_ids) - set(have["OPENET_ID"]))
    gdf = _fields_4326(todo_ids)
    print(f"[soil] sampling POLARIS texture for {len(gdf)} fields (batch {BATCH})")
    soil = _soil_image(ee)

    rows = []
    for start in range(0, len(gdf), BATCH):
        batch = gdf.iloc[start:start + BATCH]
        feats = [ee.Feature(ee.Geometry(geom.__geo_interface__, proj="EPSG:4326", geodesic=False),
                            {"OPENET_ID": oid})
                 for oid, geom in zip(batch["OPENET_ID"], batch.geometry)]
        fc = ee.FeatureCollection(feats)
        reduced = soil.reduceRegions(collection=fc, reducer=ee.Reducer.mean(),
                                     scale=POLARIS_SCALE, tileScale=4)
        for f in reduced.getInfo()["features"]:
            p = f["properties"]
            rows.append({"OPENET_ID": p.get("OPENET_ID"),
                         "sand": p.get("sand"), "silt": p.get("silt"), "clay": p.get("clay")})
        print(f"[soil]   {min(start + BATCH, len(gdf))}/{len(gdf)}")

    new = pd.DataFrame(rows)
    new["texture"] = soil_texture.classify_textures(new["sand"], new["silt"], new["clay"])
    merged = pd.concat([have, new], ignore_index=True).drop_duplicates("OPENET_ID", keep="last")
    config.INPUT_DIR.mkdir(parents=True, exist_ok=True)
    merged.to_csv(config.FIELD_TEXTURE_CSV, index=False)
    n_ok = new["texture"].notna().sum()
    print(f"[soil] wrote {len(merged)} cached textures (+{len(new)} new, {n_ok} classified)")
    return (merged if field_ids is None else merged[merged["OPENET_ID"].isin(set(field_ids))]).reset_index(drop=True)


if __name__ == "__main__":
    run()
