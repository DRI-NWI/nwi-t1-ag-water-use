#!/usr/bin/env python3
"""Step 04c: per-field groundwater-ET fraction f (Tan 2025) with the SW-basin filter.

Output: run/04_gw_subsidy/field_gwfrac.parquet, gwfrac_summary.csv
"""

import pandas as pd

from _common import parse
from nwi_agwu import fields
from nwi_agwu.subsidy import gwfrac

args, cfg, P = parse(__doc__)
g = cfg["gw_subsidy"]
attrs = pd.read_parquet(P.field_attrs)
if cfg["allocation"].get("apply_static_overrides", True):
    attrs = fields.apply_static_overrides(attrs, cfg["allocation"]["static_overrides"])
base = pd.read_parquet(P.field_base, columns=["NWI_ID", "MONTH", "ETO_MM"])
gs = gwfrac.growing_season_eto(base, g["growing_season_months"])
out = gwfrac.compute(attrs, pd.read_parquet(P.field_wtd), pd.read_parquet(P.field_wtd_pixels),
                     pd.read_csv(P.field_texture), gs, cfg)
out.to_parquet(P.field_gwfrac, index=False)
summ = out.groupby("STATE").agg(fields=("NWI_ID", "size"), dtw_available=("dtw_available", "sum"),
                                texture_assumed=("texture_assumed", "sum"),
                                f_raw_gt0=("gwFrac_raw", lambda s: int((s > 0).sum())),
                                f_final_gt0=("gw_frac", lambda s: int((s > 0).sum())),
                                filtered_out=("sw_filter_keep", lambda s: int((~s).sum())),
                                mean_f=("gw_frac", "mean"),
                                median_wtd_m=("wtd_mean_m", "median"))
summ.to_csv(P.subsidy / "gwfrac_summary.csv")
print(summ.to_string())
