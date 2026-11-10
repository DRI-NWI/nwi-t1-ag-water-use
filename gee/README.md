# GW-ET subsidy explorer (Earth Engine app)

An Earth Engine app for checking the field groundwater-ET (GW-ET) subsidy of run
`nwi_t1_final_20261018`. It compares the new depth-to-groundwater (DTW) map, the handily NV parity map at
30 m, with the Ma et al. (2026) map on the same 30 m grid, and keeps the run's own values for reference.
It is meant for finding where the two maps disagree, which fields are far from the wells the new map was
trained on, and how the four f(d) models compare.

**Open it.** Paste [`nwi_gw_et_explorer_app.js`](nwi_gw_et_explorer_app.js) into the
[Earth Engine Code Editor](https://code.earthengine.google.com) and press Run. It reads the
assets in `projects/nwi-usgs/assets/nwi_gw_subsidy/`. They are public, so the app runs from any
Earth Engine account. A browser version with the field polygons but no rasters, and the scripts that
build its data, are in [`web/`](web/).

The app started as an unchanged copy of the nwi-gw-subsidy app (`DRI-NWI/nwi-gw-subsidy`,
`gee/nwi_gw_et_explorer_app.js` at commit 7262f69; first commit on this branch) and was then
rewritten. The old app computed GW-ET per pixel from Earth Engine's OpenET, a 2020 CDL rooting-depth
mask, and the Ma et al. map on the 2026-05-06 fields, so its numbers could not match the report.
This app shows field results on the 2026-08-20 fields: the run's own values, and the run's code applied
to the 30 m maps.

## What it shows

| Control | What it does |
|---|---|
| f(d) model | Tan et al. (2025), the run's model (default); Banta (2000); Averianov (1956), p = 3; Shah et al. (2007) |
| Colour fields by | Subsidy change (new − Ma), subsidy with each map (acre-ft/yr or % of ETa), f with each map, field-mean DTW with each map and with the run's map, and their differences, DTW uncertainty (sigma of the run's map), distance to the nearest training well, Tan with the new map − the run, model − Tan |
| Which fields | Irrigated in at least one year (the report's population, default) or all; Nevada only; only fields covered by the Ma et al. map (like-for-like); only fields with a subsidy > 0.5 acre-ft/yr under either map |
| Large changes only | Shows only fields with \|new − Ma\| at or above the slider value (acre-ft/yr) |
| No well data | Shows only fields at least the slider value (km) from the nearest training well |
| Summary line and CSV | Number of fields shown, their subsidy with each map, and a CSV download of those fields (with the run's Tan value) |
| Layers | New DTW (30 m), Ma et al. DTW (30 m grid), their difference, the new map minus the run's map, sigma, f for each 30 m pixel inside fields, distance to the nearest training well, training wells, all gwx wells, HA change, HA outlines |
| Click a field | Water right, crop, soil, Tan parameters, DTW with every map, nearest wells, f and subsidy for every model with both maps, the run's own Tan values, f against depth for this field, and the 41-year subsidy series |
| Click a well | The well's depth to water and every map at its pixel |
| Click an HA | Totals for every model with both maps, the run's value and the report's Ma value, DTW, fields far from wells, the annual series, and the fields with the largest change |
| HAs with the largest change | Ranked increases and decreases for the selected model |
| Checks against the report | The run's values against the report's numbers (exact), with the 30 m numbers next to them |

## What the numbers are

**Subsidy** (acre-ft/yr), per field and month: min(f × ETa, max(ETa − effective precipitation, 0)),
summed over the field-years IrrMapper marks more than 50% irrigated and divided by 41 years
(1985–2025). This is the report's definition (README.md, docs/GW_SUBSIDY.md).

**f** is the fraction of ETa groundwater can supply: Tan et al. parameters (d_dec, d_ext, p) from
POLARIS texture, rooting depth and April–October ETo (docs/METHODS.md, step 04), applied to every
DTW pixel touching the field (30 m for the new map and Ma et al.) and averaged with each pixel weighted by
its share inside the field.

| Value | Source | Against the report |
|---|---|---|
| Tan, the run (`F_RUN_TAN`, `S_RUN_TAN`) | The run's field file (`gw_frac`, `GW_subsidy_acre_ft`), made with its 2026-10-08 100 m map | Exactly: statewide 34,733 acre-ft/yr and every HA in `docs/tables/ha_gw_subsidy_new_vs_ma.csv` |
| ETa (`ETA_AF`) | The run's field file | Exactly: 1,423,458 acre-ft/yr and every HA |
| Every model with the new map (`F_NEW_*`, `S_NEW_*`) | The run's code (`scripts/gw_subsidy`) on the bucket's 30 m map | Other map: Tan 31,746 acre-ft/yr |
| Every model with Ma et al. (`F_MA_*`, `S_MA_*`) | The same, with Ma et al. averaged onto the 30 m grid | Other grid: Tan 28,630 acre-ft/yr against the report's 18,563 on the 100 m grid |
| Tan with the run's map, recomputed (`F_R08_TAN`, `S_R08_TAN`) | The run's code with this app's inputs on the run's map | 34,993 against 34,733 (+0.75%): the recomputation error |
| Distances to wells (`WT_*`, `W_*`) | `gee/prep/05_wells.py` | Not in the report |

**Why the recomputed values differ from the run.** Two inputs of the run exist only on the run computer:
the EPSG:5070 copy of its DTW map and the OpenET export the growing-season ETo came from. Recomputed on
the run's map with the field file's ETo and the GeoPackage's polygons, f (Tan) agrees with the run's to
1e-9 for 25,037 of the 29,338 fields (1,795 differ by more than 1e-4). It differs most in Lovelock Valley
(073), Carson Desert (101) and Marys River (042), which is consistent with a higher growing-season ETo
there in the run (about +1 mm/d).

**The grid matters for Ma et al.** The report averaged Ma et al. onto the run's 100 m grid, where Tan with
Ma totals 18,563 acre-ft/yr. Averaged onto the 30 m grid it totals 28,630: narrow shallow areas survive,
and Tan's steep curve credits them heavily. On the fields both maps cover, the new map gives 24,210 (the
run: 24,184), so the comparison changes direction with the grid.

**Making the run's map exact.** Copy the run's `04_gw_subsidy/field_params.parquet`, then rerun steps 6
and 7 with `--params-run` pointing at it; the `R08` columns then equal the run's. Values on the 30 m maps
have no run counterpart until the run is redone with the 30 m map.

## Data

| Asset (`projects/nwi-usgs/assets/nwi_gw_subsidy/`) | Content |
|---|---|
| `fields_gw_subsidy_20261018` | 29,338 fields of `nevada_agricultural_field_boundaries_20260820.gpkg` (MD5 `ec2e7038a56c4e225ace0e0b83999d08`, data last changed 2026-09-21; the layer of `projects/ee-prevelle/assets/NWI/nevada_agricultural_field_boundaries_20260820`), with the GeoPackage's polygons and every value above |
| `field_year_gw_subsidy_20261018` | One row per field-year: irrigated flag, ETa and subsidy for every model with both maps, the run's Tan and Tan recomputed with the run's map |
| `ha_gw_subsidy_20261018` | One row per HA (polygons from `projects/ee-prevelle/assets/NV/NWI_Investigations`) with totals; `S_RUN_TAN` is the run's value and `S_MA_TAN_RPT` the report's Ma value |
| `ha_year_gw_subsidy_20261018` | One row per HA-year, irrigated field-years |
| `wells_handily_train_nv` | The 9,448 wells the handily NV parity DTW model was trained on (`gs://ucrc-wtd/handily/nv/handily_wells_train_nv_parity.csv`, dated 2026-10-21): 8,628 NDWR well-log depths and 820 monitoring wells, with every map at each well |
| `wells_gwx_nv` | 137,138 gwx wells with a depth to water (`gwx_nevada_wells.zip`, MD5 `e06c9fe9ea211b438bf2722c523ea459`); `is_train` marks those at a training-well location; the new map and the run's map at each well |
| `dtw_epsg5070` | `dtw_new_m`: the new map, handily NV parity DTW at 30 m (`gs://ucrc-wtd/handily/nv/handily_dtw_nv_parity_30m.tif`, 2026-10-21, MD5 `974338d10a021dcca7e52c70e2c44fba`), pixels unchanged. `dtw_ma_m`: Ma et al. `wtd_nevada.tif` averaged onto the same 30 m grid. `dtw_run_m`: the run's map, statewide 100 m, 2026-10-08 build (`nv_dtw_parity_statewide_100m.zip`, MD5 `20af7ee2027ac234774f5a026ee9b4d8`), tagged EPSG:5070 with no resampling. `dtw_sigma_m`: the run's map's uncertainty, 100 m (the bucket has no sigma layer) |

**Sharing.** All seven assets are readable by anyone (Earth Engine `allUsers`); step 07 sets this.
The folder is not public, so it cannot be listed, and other assets put in it stay private.

**Training wells.** The training-well file is dated 2026-10-21, like the new 30 m map. The new map is
within a median 1.9 m of these wells and 5.9 m of other gwx wells (the run's 2026-10-08 map: 1.8 m and
6.0 m). Distances are measured from the field boundary (0 = a well inside the field).

## What the app shows about wells and changes (Tan)

| | Irrigated fields | Subsidy, new map | Subsidy, Ma et al. | The run |
|---|---:|---:|---:|---:|
| All irrigated fields | 19,032 | 31,746 | 28,630 | 34,733 |
| > 2 km from a training well | 8,865 | 15,426 | 12,787 | 24,841 |
| > 5 km from a training well | 5,340 | 10,262 | 5,177 | 18,978 |
| > 10 km from a training well | 3,376 | 7,218 | 2,323 | 13,221 |

A third of the new map's subsidy is on fields more than 5 km from any training well (55% with the run's
map). The largest decreases against Ma et al. are in Lovelock and Mason valleys, about 1–2 km from training
wells. The largest increases are in Surprise, Colorado and Grouse Creek valleys, whose fields are outside
Nevada, where Ma et al. has no value, and in Carson Valley, which the report had as a decrease.
979 irrigated fields change by at least 10 acre-ft/yr; 317 of them are more than 5 km from a training well.

## Rebuilding

Scripts in [`prep/`](prep/), run in order with the `nwi-usgs` conda environment (it needs
`exactextract` and `duckdb`). They read the run folder by default (`<output_root>/<run_id>/`); every
path can be given on the command line. On another computer:

```bash
cd gee/prep
OUT=../../outputs/gee_app      # git ignores outputs/
python 01_field_results.py --field-file <run>/05_field_summaries/field_summaries_EToF_final.csv.gz --out $OUT
python 02_field_geometries.py --gpkg "<OneDrive>/RESERVOIR - GIS/nevada_agricultural_field_boundaries_20260820.gpkg" --out $OUT
python 03_dtw_rasters.py --new-tif handily_dtw_nv_parity_30m.tif --dtw-dir <unzipped nv_dtw_parity_statewide_100m.zip> \
    --ma-tif <nwi-gw-subsidy>/Data/Inputs/wtd_nevada.tif --out $OUT
python 04_field_fractions.py --ee-project nwi-usgs --out $OUT
python 05_wells.py --train handily_wells_train_nv_parity.csv --gwx gwx_nevada/wells.geoparquet --out $OUT
python 06_app_tables.py --out $OUT [--params-run <run>/04_gw_subsidy/field_params.parquet]
python 07_upload_assets.py --project nwi-usgs --bucket nwi-usgs-gee-exports --out $OUT [--overwrite]
python 08_check_assets.py --project nwi-usgs --out $OUT
```

`handily_dtw_nv_parity_30m.tif` and `handily_wells_train_nv_parity.csv` are in `gs://ucrc-wtd/handily/nv/`.

| Step | What it does | Checks (any failure stops it) |
|---|---|---|
| 01 | Field-month, field-year and field tables from the run's whole-field file | One f per field; subsidy = min(f × ETa, max(NetET_pre, 0)) in every field-month; README totals; every HA of the docs table |
| 02 | The 2026-08-20 polygons from the GeoPackage, one per `NWI_ID` | Same 29,338 IDs as the run; the GeoPackage's MD5 recorded in `02_field_geometries_checks.json` |
| 03 | The new 30 m map rewritten with DEFLATE (pixels unchanged); the run's 100 m map and sigma tagged EPSG:5070; Ma et al. averaged onto the 30 m grid | The 30 m and 100 m grids share a corner |
| 04 | f for 4 models with the new map and with Ma et al., and Tan with the run's map, with the run's `scripts/gw_subsidy` code (POLARIS texture through Earth Engine) | f (Tan, run's map) against the run's `gw_frac`, and the totals against the report, recorded in `04_field_fractions_checks.json` |
| 05 | Training wells, gwx wells, every map at each well, distances from every field | Map-minus-well depth at training and other wells, for the new map and the run's |
| 06 | The app's tables | The run's f gives the run's subsidy in every field-month; statewide and HA totals equal the report; the 30 m numbers against the report recorded in `app/app_summary.json` |
| 07 | Upload to Cloud Storage and ingestion as Earth Engine assets, which are then made public (`--no-public` skips this) | Explicit column types (HA codes stay text); provenance stored as asset properties |
| 08 | Checks the uploaded assets on Earth Engine | Types survived ingestion; every asset is public (`--no-public` skips this); the new map is EPSG:5070 at 30 m; the report's statewide and HA numbers from the run's values; annual rows add up; every app layer draws and every click query, ranking and CSV link runs |
