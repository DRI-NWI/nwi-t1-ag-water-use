#!/usr/bin/env bash
# Full run: inputs -> OTTER -> SPI -> GW subsidy -> bridge variants -> final -> QA.
# Step 04a needs exactextract; set PY_EXACT to an interpreter that has it.
set -euo pipefail
cd "$(dirname "$0")"
PY=${PY:-python}
PY_EXACT=${PY_EXACT:-$PY}
$PY 01_build_inputs.py
$PY 02_run_otter.py
$PY 03_compute_spi.py
$PY_EXACT 04a_dtw_zonal.py
$PY 04b_texture_polaris.py
$PY 04c_gw_fraction.py
for v in v0a_reproduce_0823_no_static v0b_reproduce_0823_static153 v1_basin_overrides v2_prism_spi_d24 v3_prism_ppt_otter_peff; do
  $PY 05_process_fields.py --variant "$v"
done
$PY 05_process_fields.py
$PY 07_bridge_qa.py
