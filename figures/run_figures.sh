#!/usr/bin/env bash
# Build every figure. PY_EXACT must have exactextract (Ma DTW zonal stats).
set -euo pipefail
cd "$(dirname "$0")"
PY=${PY:-python}
PY_EXACT=${PY_EXACT:-$PY}
$PY fig01_overview_water_rights.py
$PY fig02_ha_maps.py
$PY fig05_statewide_timeseries.py
$PY fig07_allocation.py
$PY fig08_data_quality.py
$PY fig09_irrigated_area.py
$PY_EXACT prep_ma_dtw_zonal.py
$PY fig10_gw_subsidy_dtw.py
