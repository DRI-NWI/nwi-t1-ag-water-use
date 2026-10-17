#!/usr/bin/env python3
"""Step 01b: per-window field EToF climatologies from step 01's field_month_base.parquet.

Used only when etof.climatology_fill is on (step 05). Same rules and file shape as the
delivered calculate_etof_climatologies (see nwi_agwu/etof.py).
Outputs: run/01_inputs/etof_climatology/etof_climatology_{start}_{end}.csv, climatology_coverage.csv
"""

import pandas as pd

from _common import parse
from nwi_agwu import etof

args, cfg, P = parse(__doc__)
windows = [tuple(w) for w in cfg["etof"]["climatology_windows"]]
base = pd.read_parquet(P.field_base, columns=["NWI_ID", "OPENET_ID", "YEAR", "MONTH", "EToF"])
attrs = pd.read_parquet(P.field_attrs, columns=["NWI_ID", "HA_NAME_CODE"])
climos = etof.build_climatologies(base, attrs, windows)
etof.write_climatologies(climos, P.etof_climatology)
cov = pd.DataFrame([{"window": k, "fields": len(c), **{m: int(c[m].notna().sum()) for m in etof.CLIMO_COLUMNS}}
                    for k, c in climos.items()])
cov.to_csv(P.etof_climatology / "climatology_coverage.csv", index=False)
print(cov.to_string(index=False))
