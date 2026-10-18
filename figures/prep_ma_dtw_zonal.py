#!/usr/bin/env python3
"""
Figure input: Ma et al. DTW zonal statistics and groundwater fraction for the same fields.

The Ma et al. (2026) DTW, resampled to the parity 100 m grid, goes through the same
nwi-gw-subsidy steps as the run's DTW (gw_subsidy.wtd_zonal.run, then
gw_subsidy.subsidy.compute_field_params) with only the raster and output paths changed, so
f can be compared on identical fields and settings. Needs exactextract.

Outputs: <run>/04_gw_subsidy/ma_comparison/field_wtd.parquet, field_wtd_pixels.parquet,
field_params.parquet
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from gw_subsidy import config, subsidy, wtd_zonal  # noqa: E402

MA_TIF = Path("/Users/Peter.ReVelle@dri.edu/Documents/projects/NWI/dtw/nv_dtw_parity_statewide_100m/epsg5070/"
              "gw_subsidy_parity_vs_ma/inputs/ma_wtd_nv_100m_epsg5070.tif")

if __name__ == "__main__":
    out = config.OUTPUT_DIR / "ma_comparison"
    out.mkdir(exist_ok=True)
    config.WTD_TIF = MA_TIF
    config.FIELD_WTD_PARQUET = out / "field_wtd.parquet"
    config.FIELD_WTD_PIXELS_PARQUET = out / "field_wtd_pixels.parquet"
    config.FIELD_PARAMS_PARQUET = out / "field_params.parquet"
    wtd_zonal.run(force=True)
    params = subsidy.compute_field_params(force=True)
    print(f"Ma et al.: {params['wtd_mean_m'].notna().sum():,} fields with DTW, "
          f"{(params['gw_frac'] > 0).sum():,} with f > 0")
