# Web explorer

[`nwi_gw_subsidy_explorer.html`](nwi_gw_subsidy_explorer.html) is a browser version of the Earth Engine
app, published as a Claude artifact. It reads the same tables (gee/prep step 6) from JSON files in a
`data/` folder next to the page and draws the 2026-08-20 field polygons and the hydrographic-area (HA)
outlines on a canvas in EPSG:5070. It has no DTW rasters; those are in the Earth Engine app.

Rebuild the data after rerunning gee/prep (nwi-usgs conda environment):

```bash
cd gee/web
DATA=../../outputs/gee_app/web/data      # git ignores outputs/
python build_data.py $DATA
python build_outlines.py "<OneDrive>/RESERVOIR - GIS/nevada_agricultural_field_boundaries_20260820.gpkg" \
    ../../outputs/gee_app/app/ha_gw_subsidy.parquet $DATA
```

Then publish the page with the eight files in `data/` (about 18 MB).

| File | Content |
|---|---|
| `fields.json` | One array per column, one entry per field (29,338), in `NWI_ID` order |
| `field_years.json` | Annual Tan subsidy (new map, Ma et al., the run) for fields with more than 0.5 acre-ft/yr under any of them; loaded when a field is opened |
| `has.json`, `ha_years.json` | HA totals and annual HA totals |
| `wells.json` | The 9,448 training wells, with every map at each well |
| `meta.json` | Lookup lists, totals from `app_summary.json`, and the outlines' provenance |
| `fields_geom.json`, `has_geom.json` | Field and HA outlines: a compact binary (see `build_outlines.py`), base64-encoded |

Field outlines are simplified by up to 1 m and rounded to a 1 m grid: every ring drawn is within 2.1 m
of the GeoPackage's, and 35 sliver rings of at most 0.5 m² are left out. HA outlines are simplified by 5 m.
