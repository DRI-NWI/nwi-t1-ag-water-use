#!/usr/bin/env python3
"""Step 07: reproduction gate against the delivered 2026-08-23 package and the bridge table.

Compares HA x year x month volumes of each variant with the delivered HA summaries and
builds the statewide annual-mean bridge (acre-ft/yr, 1985-2025) from v0 to the final run.
Outputs: run/07_qa/reproduction_check.csv, bridge_table.csv, bridge_by_ha.csv
"""

import pandas as pd

from _common import parse

args, cfg, P = parse(__doc__)
ref_dir = cfg["inputs"]["reference_package_dir"]
V = ["ETa_final_acre_ft", "NetET_final_acre_ft", "AR_final_acre_ft", "ACRES_FTR_GEOM"]
K = ["HA_NAME_CODE", "YEAR", "MONTH"]


def ref(comp):
    r = pd.read_csv(f"{ref_dir}/ha_summaries_EToF_final_{comp}.csv", usecols=K + V)
    return r.groupby(K, as_index=False)[V].sum()


rows = []
for variant in ["v0a_reproduce_0823_no_static", "v0b_reproduce_0823_static153"]:
    d = P.variants / variant
    if not d.exists():
        continue
    for comp in ["GW", "SW"]:
        r = ref(comp)
        m = pd.read_csv(d / f"ha_summaries_{comp}.csv", usecols=K + V)
        j = r.merge(m, on=K, how="outer", suffixes=("_0823", "_new"), indicator=True)
        for v in V:
            a, b = j[f"{v}_0823"].fillna(0), j[f"{v}_new"].fillna(0)
            rows.append({"variant": variant, "component": comp, "column": v, "total_0823": a.sum(), "total_new": b.sum(),
                         "rel_diff_total": (b.sum() - a.sum()) / a.sum(), "max_abs_diff_cell": (b - a).abs().max(),
                         "cells_abs_diff_gt_0.01": int(((b - a).abs() > 0.01).sum()),
                         "cells_only_0823": int((j._merge == "left_only").sum()),
                         "cells_only_new": int((j._merge == "right_only").sum())})
rc = pd.DataFrame(rows)
rc.to_csv(P.qa / "reproduction_check.csv", index=False)
print(rc.to_string(index=False))

# Bridge: statewide annual mean (sum over years / n years) per variant and component.
order = ["v0a_reproduce_0823_no_static", "v1_basin_overrides", "v2_prism_spi_d24", "v3_prism_ppt_otter_peff", "v4_final",
         "v5_climatology_fill"]
cols = ["ETa_final_acre_ft", "PPT_vol_acre_ft", "Peff_vol_acre_ft", "GW_subsidy_acre_ft", "Carryforward_Used_acre_ft",
        "NetET_final_acre_ft", "AR_final_acre_ft"]
br = []
for v in order:
    d = P.summaries if v == "v4_final" else P.variants / v
    f = d / "statewide_annual_totals.csv"
    if not f.exists():
        continue
    t = pd.read_csv(f)
    n = t["YEAR"].nunique()
    s = t.groupby("component")[cols].sum() / n
    s.insert(0, "variant", v)
    br.append(s.reset_index())
if br:
    b = pd.concat(br)
    b.to_csv(P.qa / "bridge_table.csv", index=False)
    print(b.round(0).to_string(index=False))
