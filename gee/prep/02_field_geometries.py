#!/usr/bin/env python3
"""
GEE app input 2: the 2026-08-20 field polygons, one per NWI_ID.

Reads the boundary layer the run used (config inputs.fields_gpkg, or --gpkg PATH for a copy such as
the one in the "RESERVOIR - GIS" OneDrive folder) or, with --from-ee, the same layer as an Earth Engine table (projects/ee-prevelle/assets/NWI/
nevada_agricultural_field_boundaries_20260820), dissolves multi-part fields by NWI_ID as
gw_subsidy.wtd_zonal does, and writes fields_20260820.parquet (GeoParquet, EPSG:4326, 2-D).

Checks that the IDs are exactly the 29,338 fields of the run (field_static.parquet from step 1).

Usage:
    python 02_field_geometries.py [--gpkg PATH | --from-ee] [--ee-project nwi-usgs] [--out DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely

from _common import CFG, out_dir

EE_FIELDS = "projects/ee-prevelle/assets/NWI/nevada_agricultural_field_boundaries_20260820"


def from_ee(project):
    """All features of the EE field table as a GeoDataFrame (EPSG:4326)."""
    import ee
    ee.Initialize(project=project)
    fc = ee.FeatureCollection(EE_FIELDS).select(["NWI_ID", "Acres", "Basin", "STATE"])
    gdf = ee.data.computeFeatures({"expression": fc, "fileFormat": "GEOPANDAS_GEODATAFRAME"})
    return gdf.set_crs(4326, allow_override=True)


def polygonal(geom):
    """
    The polygon parts of a geometry. Earth Engine returns some fields as a GeometryCollection of
    polygons and zero-area LineStrings (degenerate slivers of the source polygon); the line parts
    have no area, so dropping them leaves the field's area and pixel coverage unchanged.
    """
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    parts = [p for p in shapely.get_parts(geom) if p.geom_type in ("Polygon", "MultiPolygon")]
    return shapely.union_all(parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gpkg", default=None, help="the GeoPackage, when it is not at the config path")
    ap.add_argument("--from-ee", action="store_true", help="read the EE table instead of the run's GeoPackage")
    ap.add_argument("--ee-project", default="nwi-usgs")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    out = out_dir(args.out)

    if args.from_ee:
        gdf = from_ee(args.ee_project)
        source = EE_FIELDS
    else:
        source = args.gpkg or CFG["inputs"]["fields_gpkg"]
        gdf = gpd.read_file(source, columns=["NWI_ID", "Acres", "Basin", "STATE"]).to_crs(4326)
        md5 = hashlib.md5(Path(source).read_bytes()).hexdigest()
        print(f"[fields] {Path(source).name}: MD5 {md5}")
    print(f"[fields] {len(gdf):,} features from {source}")

    gdf = gdf[gdf["NWI_ID"].notna()].copy()
    gdf["geometry"] = shapely.force_2d(gdf.geometry.values)
    n_multi = gdf["NWI_ID"].duplicated(keep=False).sum()
    gdf = gdf.dissolve(by="NWI_ID", as_index=False, aggfunc="first")
    n_gc = (gdf.geom_type == "GeometryCollection").sum()
    area0 = gdf.to_crs(5070).geometry.area
    gdf["geometry"] = [polygonal(g) for g in gdf.geometry]
    darea = (gdf.to_crs(5070).geometry.area - area0).abs().max()
    print(f"[fields] {n_gc} GeometryCollections reduced to their polygon parts "
          f"(max area change {darea:.6f} m2); types now {gdf.geom_type.value_counts().to_dict()}")
    if darea > 1e-3:
        raise SystemExit("dropping the line parts changed a field's area")
    invalid = (~gdf.geometry.is_valid).sum()
    print(f"[fields] {len(gdf):,} fields after dissolving {n_multi} rows that share an NWI_ID; "
          f"{invalid} invalid geometries")

    run_ids = set(pd.read_parquet(out / "field_static.parquet", columns=["NWI_ID"])["NWI_ID"])
    ids = set(gdf["NWI_ID"])
    print(f"[fields] in the run but not the layer: {len(run_ids - ids)}; in the layer but not the run: "
          f"{len(ids - run_ids)}")
    if ids != run_ids:
        raise SystemExit("field IDs differ from the run")
    gdf.to_parquet(out / "fields_20260820.parquet", index=False)
    print(f"[fields] wrote {out / 'fields_20260820.parquet'}")
    (out / "02_field_geometries_checks.json").write_text(json.dumps(
        {"source": str(source), "md5": None if args.from_ee else md5, "n_fields": len(gdf)}, indent=2))


if __name__ == "__main__":
    main()
