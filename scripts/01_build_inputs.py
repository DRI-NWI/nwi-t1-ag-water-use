#!/usr/bin/env python3
"""Step 01: field attributes, monthly PRISM precipitation, and the field-month EToF base table.

Outputs (run/01_inputs/):
  field_attributes.parquet           one row per NWI_ID (boundary layer + flagged assumptions)
  field_monthly_prism_ppt_eto.parquet PRISM 800 m ppt and met ETo per field-month
  field_month_base.parquet           ENSEMBLE ETa/ETo/EToF (gap-filled) + PRISM ppt per field-month
  input_receipts.json, prism_qc_by_year.csv, eto_consistency.csv
"""

import numpy as np
import pandas as pd

from _common import file_receipt, parse, write_json
from nwi_agwu import etof, fields, prism

args, cfg, P = parse(__doc__)
I, R = cfg["inputs"], cfg["run"]
write_json({k: file_receipt(v) for k, v in I.items()}, P.inputs / "input_receipts.json")


if not P.prism_monthly.exists():
    pm, qc = prism.monthly_from_daily(I["met_daily_dir"], R["start_year"], R["end_year"])
    pm.to_parquet(P.prism_monthly, index=False)
    qc.to_csv(P.inputs / "prism_qc_by_year.csv", index=False)
pm = pd.read_parquet(P.prism_monthly)

raw = etof.load_ensemble(I["ensemble_dir"], R["start_year"], R["end_year"])
print(f"[01] ENSEMBLE rows {len(raw):,}, fields {raw.NWI_ID.nunique():,}, "
      f"exact duplicates dropped {raw.attrs.get('dropped_exact_duplicates')}")
# irr_eff / ITYPE are per-field constants in the export; verify, then take them from it.
ex = raw[["NWI_ID", "irr_eff", "ITYPE"]].drop_duplicates()
if ex["NWI_ID"].duplicated().any():
    raise ValueError("irr_eff/ITYPE vary over time within a field in the export")
attrs, report = fields.load_attributes(I["fields_gpkg"], cfg, export_attrs=ex)
attrs.to_parquet(P.field_attrs, index=False)
report.to_csv(P.attr_report, index=False)
print(report.to_string(index=False))
raw = raw.drop(columns=["irr_eff", "ITYPE"], errors="ignore")
base = etof.build_etof(raw, limit=cfg["etof"]["linear_interp_limit"])
missing = set(base.NWI_ID) - set(attrs.NWI_ID)
if missing:
    raise ValueError(f"{len(missing)} export fields missing from boundary layer: {sorted(missing)[:5]}")
base = base.merge(pm, on=["NWI_ID", "YEAR", "MONTH"], how="left", validate="one_to_one")
if base["PPT_PRISM_MM"].isna().any():
    raise ValueError(f"{int(base.PPT_PRISM_MM.isna().sum())} field-months lack PRISM ppt")

# ETo consistency: the OTTER/PRISM met ETo and the export ETo should be the same product.
ok = base["ETO_MM"].notna()
d = base.loc[ok, "ETO_MET_MM"] - base.loc[ok, "ETO_MM"]
eto_qc = pd.DataFrame([{
    "n": int(ok.sum()), "r": float(np.corrcoef(base.loc[ok, "ETO_MET_MM"], base.loc[ok, "ETO_MM"])[0, 1]),
    "mean_export_mm": float(base.loc[ok, "ETO_MM"].mean()), "mean_met_mm": float(base.loc[ok, "ETO_MET_MM"].mean()),
    "mean_diff_mm": float(d.mean()), "p99_abs_diff_mm": float(d.abs().quantile(0.99)),
    "ppt_prism_vs_export_r": float(base[["PPT_PRISM_MM", "PPT_MM"]].dropna().corr().iloc[0, 1]),
    "ppt_prism_mean_mm": float(base["PPT_PRISM_MM"].mean()), "ppt_export_mean_mm": float(base["PPT_MM"].mean()),
}])
eto_qc.to_csv(P.inputs / "eto_consistency.csv", index=False)
print(eto_qc.T.to_string())
base.to_parquet(P.field_base, index=False)
print(f"[01] wrote {P.field_base} ({len(base):,} rows); interpolation:",
      base["interpolation_method"].value_counts().to_dict())
