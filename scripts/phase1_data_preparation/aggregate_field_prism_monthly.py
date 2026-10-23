#!/usr/bin/env python3
"""Aggregate PRISM 800 m daily field-centroid precipitation to monthly totals per field.

NWI T1: also sums the OpenET gridMET daily ETo carried in the same files (used to check the
export ETo and by OTTER), reads paths and years from config/nwi_t1_final.yaml, and writes
01_inputs/field_monthly_prism_ppt_eto.parquet with columns NWI_ID, YEAR, MONTH,
PPT_PRISM_MM, ETO_MET_MM, N_DAYS.

Source: agricultural-water-forecasting/scripts/scripts_otter/data_folder/nwi_field_export_20260820/met/year=YYYY/met_YYYYMMDD.parquet
(config highvolume_met_extraction_share/ee_extract_nv_20260820.toml: precip_source=prism, PRISM_800_DAILY 'ppt', scale 800, field-centroid mode).
Processed one year at a time with duckdb (hive_partitioning disabled; files hold NWI_ID, date, eto, ppt only; year/month taken from `date`).
Outputs (run/01_inputs/): field_monthly_prism_ppt_eto.parquet (NWI_ID, YEAR, MONTH, PPT_PRISM_MM, ETO_MET_MM, N_DAYS)
and prism_qc_by_year.csv (daily-file checks per year).
"""
from pathlib import Path
import calendar
import sys
import duckdb, pandas as pd

# NWI T1: paths and years from the run configuration
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from nwi_config import load_config, RunPaths  # noqa: E402

cfg = load_config()
paths = RunPaths(cfg).make()
SRC = Path(cfg["inputs"]["met_daily_dir"])
OUT = paths.inputs
con = duckdb.connect(); con.execute("SET memory_limit='16GB'"); con.execute("SET threads=12")
parts, qc = [], []
for y in range(cfg["run"]["start_year"], cfg["run"]["end_year"] + 1):
    files = sorted((SRC / f"year={y}").glob("met_*.parquet"))
    ndays_cal = 366 if calendar.isleap(y) else 365
    glob = str(SRC / f"year={y}" / "met_*.parquet")
    q = f"""
      SELECT NWI_ID, CAST(substr(date,1,4) AS INT) AS year, CAST(substr(date,6,2) AS INT) AS month,
             SUM(CAST(ppt AS DOUBLE)) AS ppt_mm, COUNT(ppt) AS n_days, COUNT(*) AS n_rows,
             SUM(CAST(eto AS DOUBLE)) AS eto_mm  -- NWI T1: monthly ETo from the same files
      FROM read_parquet('{glob}', hive_partitioning=false, union_by_name=true)
      GROUP BY 1,2,3"""
    m = con.execute(q).df()
    s = con.execute(f"""
      SELECT COUNT(*) n_rows, COUNT(ppt) n_ppt, SUM(CASE WHEN ppt<0 THEN 1 ELSE 0 END) n_neg,
             MAX(ppt) max_ppt, AVG(ppt) mean_ppt, COUNT(DISTINCT date) n_dates, COUNT(DISTINCT NWI_ID) n_fields,
             SUM(CASE WHEN CAST(substr(date,1,4) AS INT)<>{y} THEN 1 ELSE 0 END) n_year_mismatch,
             SUM(CASE WHEN isnan(ppt) THEN 1 ELSE 0 END) n_nan
      FROM read_parquet('{glob}', hive_partitioning=false, union_by_name=true)""").df().iloc[0].to_dict()
    s.update(year=y, n_files=len(files), n_days_calendar=ndays_cal)
    qc.append(s)
    parts.append(m)
    print(y, len(files), int(s["n_fields"]), int(s["n_rows"]), int(s["n_ppt"]), s["max_ppt"], flush=True)
mon = pd.concat(parts, ignore_index=True)
mon["n_days"] = mon.n_days.astype("int16"); mon["year"] = mon.year.astype("int16"); mon["month"] = mon.month.astype("int8")
mon = mon.drop(columns="n_rows").sort_values(["NWI_ID", "year", "month"]).reset_index(drop=True)
# NWI T1: column names used by the field processor and OTTER
mon = mon.rename(columns={"year": "YEAR", "month": "MONTH", "ppt_mm": "PPT_PRISM_MM", "eto_mm": "ETO_MET_MM",
                          "n_days": "N_DAYS"})
mon["NWI_ID"] = mon["NWI_ID"].astype(str)
mon.to_parquet(paths.prism_monthly, index=False)
pd.DataFrame(qc).to_csv(OUT / "prism_qc_by_year.csv", index=False)
print(mon.shape, mon.NWI_ID.nunique())
