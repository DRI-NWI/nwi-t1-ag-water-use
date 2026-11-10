# GW-ET subsidy explorer (Earth Engine app)

An Earth Engine app for checking the field groundwater-ET (GW-ET) subsidy of run
`nwi_t1_final_20261018` against the Ma et al. (2026) depth-to-groundwater (DTW) map. It is meant
for finding where the two maps disagree, which fields are far from the wells the new DTW map was
trained on, and how the four f(d) models compare.

**Open it.** Paste [`nwi_gw_et_explorer_app.js`](nwi_gw_et_explorer_app.js) into the
[Earth Engine Code Editor](https://code.earthengine.google.com) and press Run. It reads the
assets in `projects/nwi-usgs/assets/nwi_gw_subsidy/`; your account needs read access to them
(project `nwi-usgs` members have it; share the folder with others in the Code Editor's Assets tab).

The app started as an unchanged copy of the nwi-gw-subsidy app (`DRI-NWI/nwi-gw-subsidy`,
`gee/nwi_gw_et_explorer_app.js` at commit 7262f69; first commit on this branch) and was then
rewritten. The old app computed GW-ET per pixel from Earth Engine's OpenET, a 2020 CDL rooting-depth
mask, and the Ma et al. map on the 2026-05-06 fields, so its numbers could not match the report.
This app shows the run's own field results on the 2026-08-20 fields.

## What it shows

| Control | What it does |
|---|---|
| f(d) model | Tan et al. (2025), the run's model (default); Banta (2000); Averianov (1956), p = 3; Shah et al. (2007) |
| Colour fields by | Subsidy change (new − Ma), subsidy with each map (acre-ft/yr or % of ETa), f with each map, field-mean DTW with each map and their difference, DTW uncertainty (sigma), distance to the nearest training well, model − Tan |
| Which fields | Irrigated in at least one year (the report's population, default) or all; Nevada only; only fields covered by the Ma et al. map (like-for-like); only fields with a subsidy > 0.5 acre-ft/yr under either map |
| Large changes only | Shows only fields with \|new − Ma\| at or above the slider value (acre-ft/yr) |
| No well data | Shows only fields at least the slider value (km) from the nearest training well |
| Summary line and CSV | Number of fields shown, their subsidy with each map, and a CSV download of those fields |
| Layers | New DTW, Ma et al. DTW, their difference, sigma, f for each 100 m pixel inside fields, distance to the nearest training well, training wells, all gwx wells, HA change, HA outlines |
| Click a field | Water right, crop, soil, Tan parameters, DTW with both maps, nearest wells, f and subsidy for every model with both maps, f against depth for this field, and the 41-year subsidy series |
| Click a well | The well's depth to water and both maps at its 100 m pixel |
| Click an HA | Totals for every model with both maps and the report's Ma value, DTW, fields far from wells, the annual series, and the fields with the largest change |
| HAs with the largest change | Ranked increases and decreases for the selected model |
| Checks against the report | Totals computed from the app's tables next to the report's numbers |

## What the numbers are

**Subsidy** (acre-ft/yr), per field and month: min(f × ETa, max(ETa − effective precipitation, 0)),
summed over the field-years IrrMapper marks more than 50% irrigated and divided by 41 years
(1985–2025). This is the report's definition (README.md, docs/GW_SUBSIDY.md).

**f** is the fraction of ETa groundwater can supply: Tan et al. parameters (d_dec, d_ext, p) from
POLARIS texture, rooting depth and April–October ETo (docs/METHODS.md, step 04), applied to every
100 m DTW pixel touching the field and averaged with each pixel weighted by its share inside the field.

| Value | Source | Matches the report |
|---|---|---|
| f and subsidy, Tan, new DTW (`F_NEW_TAN`, `S_NEW_TAN`) | The run's field file (`gw_frac`, `GW_subsidy_acre_ft`) | Exactly: statewide 34,733 acre-ft/yr and every HA in `docs/tables/ha_gw_subsidy_new_vs_ma.csv` |
| ETa (`ETA_AF`) | The run's field file | Exactly: 1,423,458 acre-ft/yr and every HA |
| Banta, Averianov, Shah with the new DTW; Tan recomputed (`*_TANR`) | Recomputed: the run's code (`scripts/gw_subsidy`) on the same fields, from the 2026-10-08 DTW build | Tan recomputed this way totals 34,993 vs 34,733 (+0.75%); this is the likely size of the error in the other three models |
| Every model with Ma et al. (`*_MA_*`) | Recomputed the same way, Ma et al. averaged onto the same 100 m grid | Covered fields: 19,193 vs the report's 18,563 acre-ft/yr (+3.4%); see below |
| Distances to wells (`WT_*`, `W_*`) | `gee/prep/05_wells.py` | Not in the report |

**Why the recomputed values differ.** Two inputs of the run exist only on the run computer: the
EPSG:5070 copy of the DTW map (`nv_dtw_parity_statewide_100m_epsg5070.tif`) and the OpenET export
the growing-season ETo came from. Recomputed from the 2026-10-08 statewide 100 m build and the field
file's ETo, f matches the run's for most fields (median difference 0, 90th percentile 0.0004).
It differs most in Lovelock Valley (073), Carson Desert (101) and Marys River (042), which is
consistent with a higher growing-season ETo there in the run (about +1 mm/d). The same fields carry
most of the Ma et al. difference: by HA, the app's Ma values minus the report's correlate 0.88 with the
recomputed-minus-run Tan values. The rest comes from the Ma et al. 100 m file
(`ma_wtd_nv_100m_epsg5070.tif`), whose resampling is not recorded. Of the methods tried, the
area-weighted average reproduces the report's Ma et al. coverage best (16,224 of its 16,225 fields)
and is the one used.

**Making every value exact.** Copy the run's `04_gw_subsidy/field_params.parquet` and
`04_gw_subsidy/ma_comparison/field_params.parquet`, then rerun steps 6 and 7 with
`--params-new` and `--params-ma` pointing at them (see Rebuilding).

## Data

| Asset (`projects/nwi-usgs/assets/nwi_gw_subsidy/`) | Content |
|---|---|
| `fields_gw_subsidy_20261018` | 29,338 fields of `nevada_agricultural_field_boundaries_20260820` (`projects/ee-prevelle/assets/NWI/nevada_agricultural_field_boundaries_20260820`) with every value above |
| `field_year_gw_subsidy_20261018` | One row per field-year: irrigated flag, ETa and subsidy for every model and map |
| `ha_gw_subsidy_20261018` | One row per HA (polygons from `projects/ee-prevelle/assets/NV/NWI_Investigations`) with totals; `S_MA_TAN_RPT` is the report's Ma value |
| `ha_year_gw_subsidy_20261018` | One row per HA-year, irrigated field-years |
| `wells_handily_train_nv` | The 9,448 wells the handily NV parity DTW model was trained on (`gs://ucrc-wtd/handily/nv/handily_wells_train_nv_parity.csv`, dated 2026-10-21): 8,628 NDWR well-log depths and 820 monitoring wells |
| `wells_gwx_nv` | 137,138 gwx wells with a depth to water (`gwx_nevada_wells.zip`, MD5 `e06c9fe9ea211b438bf2722c523ea459`); `is_train` marks those at a training-well location |
| `dtw_100m_epsg5070` | `dtw_new_m`: handily NV parity DTW, statewide 100 m, 2026-10-08 build (`nv_dtw_parity_statewide_100m.zip`, MD5 `20af7ee2027ac234774f5a026ee9b4d8`), tagged EPSG:5070 with no resampling. `dtw_sigma_m`: its uncertainty. `dtw_ma_m`: Ma et al. `wtd_nevada.tif` averaged onto the same grid |

**Training wells.** The training-well file is dated 2026-10-21, after the 2026-10-08 map build. The
map is within a median 1.8 m of these wells and 6.0 m of other gwx wells, so this set fits the map
the run used. Distances are measured from the field boundary (0 = a well inside the field).

**The 30 m handily build.** `gs://ucrc-wtd/handily/nv/handily_dtw_nv_parity_30m.tif` (2026-10-21) is
a later build than the map the run used. Averaged onto the 100 m grid, it differs from the 2026-10-08
build by a median of 2.0 m. The app does not use it.

## What the app shows about wells and changes (Tan)

| | Irrigated fields | Subsidy, new DTW | Subsidy, Ma et al. |
|---|---:|---:|---:|
| All irrigated fields | 19,032 | 34,733 | 19,193 |
| > 2 km from a training well | 8,865 | 24,841 | 6,744 |
| > 5 km from a training well | 5,340 | 18,978 | 957 |
| > 10 km from a training well | 3,376 | 13,221 | 220 |

Over half of the new subsidy is on fields more than 5 km from any training well. The largest
increases over Ma et al. (Surprise Valley, South Fork Owyhee, Owyhee River, Grouse Creek, Salmon Falls
Creek, Clover Valley) are mostly on such fields. The largest decreases (Lovelock, Mason, Carson
Desert and Carson valleys) are in basins with training wells about 1–2 km from the fields.
1,029 irrigated fields change by at least 10 acre-ft/yr; 448 of them are more than 5 km from a
training well.

## Rebuilding

Scripts in [`prep/`](prep/), run in order with the `nwi-usgs` conda environment (it needs
`exactextract` and `duckdb`). They read the run folder by default (`<output_root>/<run_id>/`); every
path can be given on the command line. On another computer:

```bash
cd gee/prep
OUT=../../outputs/gee_app      # git ignores outputs/
python 01_field_results.py --field-file <run>/05_field_summaries/field_summaries_EToF_final.csv.gz --out $OUT
python 02_field_geometries.py --from-ee --ee-project nwi-usgs --out $OUT
python 03_dtw_rasters.py --dtw-dir <unzipped nv_dtw_parity_statewide_100m.zip> \
    --ma-tif <nwi-gw-subsidy>/Data/Inputs/wtd_nevada.tif --out $OUT
python 04_field_fractions.py --ee-project nwi-usgs --out $OUT
python 05_wells.py --train handily_wells_train_nv_parity.csv --gwx gwx_nevada/wells.geoparquet --out $OUT
python 06_app_tables.py --out $OUT [--params-new <run>/04_gw_subsidy/field_params.parquet \
    --params-ma <run>/04_gw_subsidy/ma_comparison/field_params.parquet]
python 07_upload_assets.py --project nwi-usgs --bucket nwi-usgs-gee-exports --out $OUT [--overwrite]
python 08_check_assets.py --project nwi-usgs
```

| Step | What it does | Checks (any failure stops it) |
|---|---|---|
| 01 | Field-month, field-year and field tables from the run's whole-field file | One f per field; subsidy = min(f × ETa, max(NetET_pre, 0)) in every field-month; README totals; every HA of the docs table |
| 02 | The 2026-08-20 polygons, one per `NWI_ID` | Same 29,338 IDs as the run; Earth Engine line slivers dropped with no change in area |
| 03 | New DTW and sigma tagged EPSG:5070; Ma et al. warped onto the same grid (nearest, bilinear, average) | Same grid for all rasters |
| 04 | f for 4 models × 2 maps with the run's `scripts/gw_subsidy` code (POLARIS texture through Earth Engine) | f (Tan, new DTW) against the run's `gw_frac`; each Ma candidate against the report's HA table (recorded in `04_field_fractions_checks.json`) |
| 05 | Training wells, gwx wells, distances from every field | Map-minus-well depth at training and other wells |
| 06 | The app's tables | Tan with the new DTW equals the run in every field-month; statewide and HA totals equal the report; comparisons with the report's Ma numbers recorded in `app/app_summary.json` |
| 07 | Upload to Cloud Storage and ingestion as Earth Engine assets | Explicit column types (HA codes stay text); provenance stored as asset properties |
| 08 | Checks the uploaded assets on Earth Engine | Types survived ingestion; the report's statewide and HA numbers; annual rows add up; every app layer draws and every click query, ranking and CSV link runs |
