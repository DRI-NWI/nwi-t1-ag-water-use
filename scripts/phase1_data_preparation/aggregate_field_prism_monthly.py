#!/usr/bin/env python3
"""Aggregate PRISM 800 m daily field-centroid precipitation to monthly totals per field.

Source: agricultural-water-forecasting/scripts/scripts_otter/data_folder/nwi_field_export_20260820/met/year=YYYY/met_YYYYMMDD.parquet
(config highvolume_met_extraction_share/ee_extract_nv_20260820.toml: precip_source=prism, PRISM_800_DAILY 'ppt', scale 800, field-centroid mode).
Processed one year at a time with duckdb (hive_partitioning disabled; files hold NWI_ID, date, eto, ppt only; year/month taken from `date`).
Outputs: field_monthly_ppt_prism800.parquet (NWI_ID, year, month, ppt_mm, n_days) + qc_daily_by_year.csv
"""
from pathlib import Path
import calendar
import duckdb, pandas as pd

SRC = Path("/Users/Peter.ReVelle@dri.edu/github/agricultural-water-forecasting/scripts/scripts_otter/data_folder/nwi_field_export_20260820/met")
OUT = Path(__file__).resolve().parent
con = duckdb.connect(); con.execute("SET memory_limit='6GB'"); con.execute("SET threads=8")
parts, qc = [], []
for y in range(1985, 2026):
    files = sorted((SRC / f"year={y}").glob("met_*.parquet"))
    ndays_cal = 366 if calendar.isleap(y) else 365
    glob = str(SRC / f"year={y}" / "met_*.parquet")
    q = f"""
      SELECT NWI_ID, CAST(substr(date,1,4) AS INT) AS year, CAST(substr(date,6,2) AS INT) AS month,
             SUM(CAST(ppt AS DOUBLE)) AS ppt_mm, COUNT(ppt) AS n_days, COUNT(*) AS n_rows
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
mon.to_parquet(OUT / "field_monthly_ppt_prism800.parquet", index=False)
pd.DataFrame(qc).to_csv(OUT / "qc_daily_by_year.csv", index=False)
print(mon.shape, mon.NWI_ID.nunique())
