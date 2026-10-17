#!/usr/bin/env python3
"""Step 04b: POLARIS 0-100 cm texture for every 2026-08-20 field (Earth Engine, nasa-water).

The 2026-10-12 parity run's texture table is keyed by May-2026 OPENET_IDs, so it is used
only as a check for IDs present in both. Output: run/04_gw_subsidy/field_texture.csv
"""

import pandas as pd

from _common import parse
from nwi_agwu.subsidy import polaris

args, cfg, P = parse(__doc__)
tex = polaris.run(cfg["inputs"]["fields_gpkg"], cfg["earth_engine"]["project"], cache_csv=str(P.field_texture))
old = pd.read_csv(cfg["inputs"]["field_texture_csv"]).rename(columns={"OPENET_ID": "NWI_ID"})
m = tex.merge(old, on="NWI_ID", suffixes=("", "_may"))
chk = pd.DataFrame([{"fields": len(tex), "classified": int(tex.texture.notna().sum()),
                     "shared_ids_with_may_table": len(m),
                     "same_texture_class_share": float((m.texture == m.texture_may).mean()) if len(m) else None}])
chk.to_csv(P.subsidy / "texture_check.csv", index=False)
print(chk.T.to_string())
