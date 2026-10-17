#!/usr/bin/env python3
"""Step 04a: per-field DTW (parity 100 m) zonal statistics and shallow pixels. Needs exactextract.

Outputs (run/04_gw_subsidy/): field_wtd.parquet, field_wtd_pixels.parquet
"""

from _common import parse
from nwi_agwu.subsidy import zonal

args, cfg, P = parse(__doc__)
I, g = cfg["inputs"], cfg["gw_subsidy"]
summary, pixels = zonal.run(I["fields_gpkg"], I["dtw_tif"], I["dtw_sigma_tif"], g["keep_depth_m"])
summary.to_parquet(P.field_wtd, index=False)
pixels.to_parquet(P.field_wtd_pixels, index=False)
print(summary["wtd_source"].value_counts(dropna=False).to_string())
print(f"[04a] {len(summary):,} fields, {pixels.NWI_ID.nunique():,} shallow fields, {len(pixels):,} pixels; "
      f"pixel area {summary.pixel_area_m2.iloc[0]:.0f} m2")
