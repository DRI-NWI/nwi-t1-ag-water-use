#!/usr/bin/env bash
# Run the whole NWI T1 workflow in order. Settings: config/nwi_t1_final.yaml.
#   PY        python with pandas, geopandas, duckdb, numba, climate_indices, pyyaml
#   PY_EXACT  python that also has exactextract (groundwater-subsidy zonal statistics)
set -euo pipefail
cd "$(dirname "$0")"
PY=${PY:-python}
PY_EXACT=${PY_EXACT:-$PY}

# 1. Inputs: monthly PRISM precipitation per field, EToF climatologies, basin SPI
$PY -u phase1_data_preparation/aggregate_field_prism_monthly.py
$PY -u phase1_data_preparation/calculate_field_climatologies.py
$PY -u phase1_data_preparation/calculate_basin_spi.py

# 2. OTTER effective precipitation per field
$PY -u otter/run_nv_otter_prism_monthly.py

# 3. Groundwater-ET fraction per field (depth to water, soil texture, Tan parameters)
(cd gw_subsidy && $PY_EXACT -u main.py all)

# 4. Field water balance, SW/GW split, field files, HA and county summaries
$PY -u phase1_data_preparation/process_field_summaries.py

# 5. QA: variants that rebuild the 2026-08-23 package and the bridge to the final run
for v in v0a_reproduce_0823_no_static v0b_reproduce_0823_static153 v1_basin_overrides v2_prism_spi_d24 \
         v3_prism_ppt_otter_peff v4a_gw_subsidy_no_climatology; do
  $PY -u phase1_data_preparation/process_field_summaries.py --variant "$v"
done
$PY -u qa/bridge_qa.py
