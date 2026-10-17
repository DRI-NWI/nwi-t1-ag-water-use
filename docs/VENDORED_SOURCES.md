# Vendored code

This repo does not import code from other repositories at runtime. Each piece below was
copied in. The table records where it came from and what changed.

| Module here | Source | Source version | Changes |
|---|---|---|---|
| `nwi_agwu/etof.py` | `agricultural-water-forecasting/scripts/phase1_data_preparation/process_field_summaries.py`: missing-value handling, `apply_linear_interpolation_etof`, Step 3 ETa volume | commit 52ca0ba (2026-09-17) | Groups by `NWI_ID` instead of `HA_NAME_CODE` + `OPENET_ID` (they are 1:1 in the 2026-08-20 export). Runs once on the whole field, not per GW/SW component |
| `nwi_agwu/fields.py` | same file: `load_field_attributes`, `merge_field_attributes` precedence, `apply_irrigation_attribute_fallbacks`, `enforce_boundary_basin_mapping`; `scripts/utils/allocation_utils.py::apply_irr_src_overrides` | 52ca0ba | The basin is still taken from the boundary `Basin` attribute. `irr_eff`/`ITYPE` still come from the export, with the boundary layer filling nulls |
| `nwi_agwu/water_balance.py::_carryforward` | same file: `apply_winter_moisture_carryforward` | 52ca0ba | The same recurrence, compiled with numba. `tests/test_core.py` checks it against a pure-Python copy of the original |
| `nwi_agwu/allocation.py` | `allocation_utils.py::spi_to_sw_ratio`; `process_field_summaries.py::apply_gw_sw_separation_dynamic`, `get_basin_year_spi` | 52ca0ba | Basin rules come from `config/nwi_t1_final.yaml`. The delivered code read dynamic overrides through `load_config()` with no arguments, so the 117 rule never applied. The split is applied to finished whole-field volumes (exact, see below) |
| `nwi_agwu/spi.py` | `agricultural-water-forecasting/scripts/phase1_data_preparation/extend_basin_climate_spi_1979_2023.py::calculate_basin_spi_extended`, as re-implemented in the mixed-field review `K_prism_spi/01_compute_spi.py` | 2026-09-25 | None. Output equals the review's table exactly |
| `nwi_agwu/otter_water_balance.py` | `otter/src/otter/water_balance.py` | local 8338226 (2026-06-08) | None (verbatim) |
| `nwi_agwu/otter_run.py` | `Prz_Comparison/analysis/run_nv_otter_matched_continuous_monthly.py`; `nwi-t1-etdemands/prism800m/scripts/run_otter_prism.py` | 2026-09-30 / dd775fd | Precipitation is the field's own daily PRISM 800 m value. Monthly output covers every year |
| `nwi_agwu/subsidy/models.py`, `crops.py`, `soil_texture.py` | `nwi-gw-subsidy/src/gw_subsidy/` | 7262f69 (2026-06-28) | Verbatim, except the config import and the parameter-table path |
| `nwi_agwu/subsidy/zonal.py`, `polaris.py`, `gwfrac.py` | `nwi-gw-subsidy` `wtd_zonal.py`, `polaris_gee.py`, `subsidy.compute_field_params`, `basins.field_keep` | 7262f69 | Keyed by `NWI_ID`. Pixel area comes from the raster. POLARIS runs on project `nasa-water`. The SW filter uses the boundary source class |

## Why the GW/SW split can follow the water balance

The delivered processor split mixed-field acreage into GW and SW rows first, then ran
the water balance on each component. Doing the water balance on the whole field first and
then scaling gives identical results:

- Every volume and the storage cap are proportional to acres.
- The carry-forward chain resets every January.
- The SPI share is constant within a calendar year.

The bridge run `v0a` confirms it. It reproduces every HA × year × month cell of the
delivered 2026-08-23 summaries to within 1e-11 acre-ft.
