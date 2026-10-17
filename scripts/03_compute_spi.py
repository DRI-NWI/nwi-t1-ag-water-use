#!/usr/bin/env python3
"""Step 03: PRISM SPI by HA / contributing area, and the basin-year Sept SPI-12 used for allocation.

Outputs (run/03_spi/): prism_spi_monthly_by_region.csv, basin_year_spi.csv (contributing area, else HA),
basin_year_spi_legacy.csv (QA variants), spi_reference_check.csv
"""

import numpy as np
import pandas as pd

from _common import parse
from nwi_agwu import spi

args, cfg, P = parse(__doc__)
I = cfg["inputs"]
monthly = spi.prism_spi_monthly(I["prism_region_ppt"], cfg)
monthly.to_csv(P.spi_monthly, index=False)
t = spi.basin_year_prism_d24(monthly, cfg)
t.to_csv(P.spi_table, index=False)
spi.basin_year_legacy(I["legacy_spi"], cfg).to_csv(P.spi / "basin_year_spi_legacy.csv", index=False)

# Check against the mixed-field review's own SPI (same inputs, same method).
ref = pd.read_csv(I["prism_spi_reference"], dtype={"HA": str})
m = monthly.merge(ref[["HA", "region_type", "YEAR", "MONTH", "SPI_12m"]], on=["HA", "region_type", "YEAR", "MONTH"],
                  suffixes=("", "_ref"))
d = (m["SPI_12m"] - m["SPI_12m_ref"]).abs()
chk = pd.DataFrame([{"rows": len(m), "max_abs_diff": float(d.max()), "n_series": monthly.groupby(["HA", "region_type"]).ngroups,
                     "calibration": sorted(monthly["calibration"].unique().tolist()),
                     "years": f"{monthly.YEAR.min()}-{monthly.YEAR.max()}"}])
chk.to_csv(P.spi / "spi_reference_check.csv", index=False)
print(chk.T.to_string())
print(t["spi_source"].value_counts().to_string())
print("2024/2025 Sept SPI-12 present:", t[t.YEAR.isin([2024, 2025])]["spi_value"].notna().mean())
