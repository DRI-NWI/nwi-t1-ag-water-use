#!/usr/bin/env python3
"""Step 02: continuous per-field OTTER (NM05) on daily PRISM 800 m precipitation -> monthly Peff.

With etof.climatology_fill on, OTTER's ET uses the same climatology-filled EToF as step 05's
ETa (step 01b must have run); remaining gaps are forward-filled as before.

Outputs (run/02_otter/): field_monthly_otter_peff.parquet, otter_static_audit.csv, otter_qc.csv
"""

import pandas as pd

from _common import parse
from nwi_agwu import etof, otter_run

def main():
    args, cfg, P = parse(__doc__, lambda ap: ap.add_argument("--workers", type=int, default=16))
    base = pd.read_parquet(P.field_base, columns=["NWI_ID", "YEAR", "MONTH", "ETO_MM", "EToF_interpolated",
                                                  "interpolation_method", "ETA_MM_post_interp", "ACRES_FTR_GEOM", "ETa_vol_acre_ft",
                                                  "PPT_PRISM_MM"])
    if cfg["etof"].get("climatology_fill", False):
        windows = [tuple(w) for w in cfg["etof"]["climatology_windows"]]
        base = etof.apply_climatology_fill(base, etof.load_climatologies(P.etof_climatology, windows), windows)
        print("[02] EToF climatology fill applied:", base["interpolation_method"].value_counts().to_dict())
    fields = sorted(base["NWI_ID"].unique())
    out = otter_run.run(cfg, base, fields, P, workers=args.workers)

    # QC: OTTER's monthly precipitation must equal the step-01 PRISM monthly totals.
    chk = out.merge(base[["NWI_ID", "YEAR", "MONTH", "PPT_PRISM_MM"]], on=["NWI_ID", "YEAR", "MONTH"])
    ok = chk["OTTER_STATUS"].eq("eligible")
    qc = pd.DataFrame([{
        "fields": len(fields), "eligible_fields": int(out.loc[out.OTTER_STATUS.eq("eligible"), "NWI_ID"].nunique()),
        "max_abs_ppt_diff_mm": float((chk.loc[ok, "OTTER_PR_MM"] - chk.loc[ok, "PPT_PRISM_MM"]).abs().max()),
        "peff_over_ppt": float(chk.loc[ok, "OTTER_PEFF_MM"].sum() / chk.loc[ok, "PPT_PRISM_MM"].sum()),
        "negative_peff_months": int((chk.loc[ok, "OTTER_PEFF_MM"] < -1e-6).sum()),
        "etof_climatology_fill": bool(cfg["etof"].get("climatology_fill", False)),
    }])
    qc.to_csv(P.otter / "otter_qc.csv", index=False)
    print(qc.T.to_string())
    out.to_parquet(P.otter_monthly, index=False)
    print(f"[02] wrote {P.otter_monthly}")


if __name__ == "__main__":
    main()
