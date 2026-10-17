#!/usr/bin/env python3
"""Figure input: Ma et al. DTW (resampled to the parity 100 m grid) zonal stats for the same
fields, so f and the subsidy can be compared on identical fields and settings. Needs exactextract.

Output: <run>/04_gw_subsidy/ma_comparison/field_wtd_ma.parquet, field_wtd_pixels_ma.parquet"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from nwi_agwu import config as C  # noqa: E402
from nwi_agwu.subsidy import zonal  # noqa: E402

MA_TIF = "/Users/Peter.ReVelle@dri.edu/Documents/projects/NWI/dtw/nv_dtw_parity_statewide_100m/epsg5070/gw_subsidy_parity_vs_ma/inputs/ma_wtd_nv_100m_epsg5070.tif"
cfg = C.load(); P = C.RunPaths(cfg)
out = P.subsidy / "ma_comparison"; out.mkdir(exist_ok=True)
s, px = zonal.run(cfg["inputs"]["fields_gpkg"], MA_TIF, cfg["inputs"]["dtw_sigma_tif"], cfg["gw_subsidy"]["keep_depth_m"])
s.drop(columns=["dtw_sigma_mean_m"]).to_parquet(out / "field_wtd_ma.parquet", index=False)
px.to_parquet(out / "field_wtd_pixels_ma.parquet", index=False)
print(s.wtd_source.value_counts().to_string(), len(px))
