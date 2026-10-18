# nwi-t1-ag-water-use

Monthly agricultural water use for every irrigated field in the Nevada Water Initiative
(NWI T1) area, 1985–2025. For each field and month the repo computes:

- consumptive use (OpenET ETa);
- effective precipitation;
- the groundwater-ET subsidy;
- winter soil-water carry-forward;
- net ET and applied water;
- the split between groundwater and surface water.

Results are summed to hydrographic areas (HAs) and Nevada counties.

![Fields by water-right source](docs/images/fig01_fields_water_rights.png)

## What was run

Run `nwi_t1_final_20261016`, configured by [`config/nwi_t1_final.yaml`](config/nwi_t1_final.yaml).

| Component | Setting |
|---|---|
| Fields | 29,338 fields of the 2026-08-20 NWI boundary layer (24,882 NV; out-of-state fields in HAs that cross the state line) |
| Water rights | NDWR place-of-use source per field: GW, SW or mixed. Fields outside Nevada are treated as GW. Diamond Valley (153) mixed fields count as GW |
| ET | OpenET ensemble monthly ETa. EToF gaps are filled linearly for one month, otherwise from per-field climatologies over seven windows spanning 1985–2025 |
| Precipitation | PRISM 800 m, daily at each field, summed by month |
| Effective precipitation | OTTER daily root-zone water balance per field (`init_dru_frac = init_drl_frac = mad_frac = 0.5`), driven by the same PRISM precipitation and ET |
| GW-ET subsidy | f × ETa. f = Tan et al. (2025) groundwater fraction from the `nv_dtw_parity_statewide_100m` depth to water, POLARIS texture, rooting depth and ETo. Subtracted from NetET in every month, up to the month's positive requirement |
| Winter carry-forward | Negative NetET is stored as soil water, capped at AWC × 1.5 m × acres, reset each January, and used by later months |
| Applied water | NetET / field irrigation efficiency |
| SW/GW split | Mixed fields: SW share = clip(0.5 + 0.15 × Sept SPI-12, 0.2, 0.8). Fish Lake Valley (117): center 0.36, range 0.2–0.7 |
| SPI | `climate_indices` gamma SPI, calibration 1981–2024, from PRISM precipitation over each HA's surface-water contributing area (or the HA) |
| Summaries | HA and county totals of irrigated field-years (IrrMapper `%_IRRIGATED` > 0.5) |

The full method is in [docs/METHODS.md](docs/METHODS.md).

## Results (irrigated field-years, 1985–2025 mean, acre-ft/yr)

| | All | GW | SW |
|---|---:|---:|---:|
| ETa | 1,423,458 | 814,165 | 609,293 |
| Effective precipitation | 363,361 | 207,226 | 156,135 |
| GW-ET subsidy | 34,733 | 13,342 | 21,391 |
| Winter carry-forward used | 43,944 | 24,616 | 19,327 |
| **Net ET** | **1,065,648** | **617,737** | **447,911** |
| **Applied water** | **1,567,158** | **836,991** | **730,166** |
| Irrigated area (acres) | 540,966 | 304,192 | 236,773 |

These totals include the out-of-state fields of HAs that cross the state line. Nevada
fields alone have net ET 936,122 and applied water 1,366,930 acre-ft/yr. Each HA summary is
written for all fields and for Nevada fields only (`_NV_fields_only`).

Maps, time series and checks for every variable are on these pages:
- [docs/RESULTS.md](docs/RESULTS.md): the water balance, allocation, inputs, and irrigated area (IrrMapper vs XGBRF).
- [docs/GW_SUBSIDY.md](docs/GW_SUBSIDY.md): the groundwater-ET subsidy and the depth-to-water comparison with Ma et al.

## Outputs

Outputs are written to `<output_root>/<run_id>/`, outside the repository. Every column is
defined in [docs/DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md). Changes from the earlier packages, column by column: [docs/legacy/COLUMN_CROSSWALK.md](docs/legacy/COLUMN_CROSSWALK.md).

| Path | Content |
|---|---|
| `05_field_summaries/field_summaries_EToF_final{,_GW,_SW}.{csv,parquet}` | Field × month, every field and month. No suffix = whole field; `_GW` and `_SW` split mixed fields by their yearly share (GW + SW = whole field) |
| `06_summaries/ha_summaries_EToF_final{,_GW,_SW}.csv` | HA × year × month, irrigated field-years, every field in the HA (including out-of-state fields) |
| `06_summaries/ha_summaries_EToF_final{,_GW,_SW}_NV_fields_only.csv` | the same, Nevada fields only |
| `06_summaries/county_wide_summaries_EToF_final{,_GW,_SW}.csv` | Nevada county × year × month, irrigated field-years |
| `06_summaries/dynamic_allocation_stats.csv` | Mixed-field SW share and SPI per HA and year |
| `06_summaries/statewide_annual_totals.csv` | Statewide annual volumes for irrigated field-years and for all field-months |

## Running

```bash
conda env create -f environment.yml
cd scripts && PY=python PY_EXACT=python ./run_all.sh
cd ../figures && ./run_figures.sh
```

The scripts are the original agricultural-water-forecasting, OTTER and nwi-gw-subsidy
scripts, copied and edited for this workflow. Each edit is marked `# NWI T1`; sources and
versions are in [scripts/ORIGINAL_SOURCES.md](scripts/ORIGINAL_SOURCES.md).

| Step | Script | Writes |
|---|---|---|
| 1 | `phase1_data_preparation/aggregate_field_prism_monthly.py` | Monthly PRISM precipitation and ETo per field |
| 1 | `phase1_data_preparation/calculate_field_climatologies.py` | Per-field EToF climatologies |
| 1 | `phase1_data_preparation/calculate_basin_spi.py` | PRISM SPI per HA / contributing area |
| 2 | `otter/run_nv_otter_prism_monthly.py` | Monthly OTTER effective precipitation per field |
| 3 | `gw_subsidy/main.py all` | Depth to water per field, POLARIS texture, groundwater fraction f |
| 4 | `phase1_data_preparation/process_field_summaries.py` | Field water balance, carry-forward, SW/GW split, field files, HA and county summaries |
| 5 | `qa/bridge_qa.py`, `qa/compare_runs.py` | Reproduction check, bridge table, run-to-run comparison |

Step 3 needs `exactextract`; POLARIS texture uses Earth Engine (project `nasa-water`) for
fields not already in the texture cache. `tests/test_fast_equivalence.py` checks the three
faster processor steps (carry-forward, climatology fill, SW/GW split) against the original code.


---

Provenance and lineage (code sources, the reproduction check against earlier packages, and
the step-by-step bridge) are in [docs/legacy/](docs/legacy/LINEAGE.md).
