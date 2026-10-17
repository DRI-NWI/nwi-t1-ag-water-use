"""Daily per-field met (PRISM 800 m ppt, OpenET gridMET ETo) -> field-month totals.

Source files: <met_daily_dir>/year=YYYY/met_YYYYMMDD.parquet with NWI_ID, date,
eto, ppt (extracted by ee_extract_nv_20260820.toml, field-centroid mode, scale 800).
Values are cast to DOUBLE before summing: DuckDB otherwise infers int64 from early
files and rounds (bug found and fixed in the mixed-field review, O_prism800_field_spi).
"""

from __future__ import annotations

import calendar
from pathlib import Path

import pandas as pd


def monthly_from_daily(met_dir: str, start: int, end: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    import duckdb

    con = duckdb.connect()
    con.execute("SET memory_limit='16GB'")
    con.execute("SET threads=12")
    parts, qc = [], []
    for y in range(start, end + 1):
        glob = str(Path(met_dir) / f"year={y}" / "met_*.parquet")
        q = f"""
          SELECT NWI_ID, CAST(substr(CAST(date AS VARCHAR),1,4) AS INT) AS YEAR,
                 CAST(substr(CAST(date AS VARCHAR),6,2) AS INT) AS MONTH,
                 SUM(CAST(ppt AS DOUBLE)) AS PPT_PRISM_MM, SUM(CAST(eto AS DOUBLE)) AS ETO_MET_MM,
                 COUNT(ppt) AS N_DAYS_PPT, COUNT(eto) AS N_DAYS_ETO
          FROM read_parquet('{glob}', hive_partitioning=false, union_by_name=true)
          GROUP BY 1,2,3"""
        m = con.execute(q).df()
        n_files = len(list((Path(met_dir) / f"year={y}").glob("met_*.parquet")))
        n_days = 366 if calendar.isleap(y) else 365
        qc.append({"YEAR": y, "files": n_files, "calendar_days": n_days, "fields": m["NWI_ID"].nunique(),
                   "min_days_ppt": int(m["N_DAYS_PPT"].min()), "neg_ppt_months": int((m["PPT_PRISM_MM"] < 0).sum())})
        parts.append(m)
        print(f"[prism] {y}: {n_files} files, {m['NWI_ID'].nunique()} fields", flush=True)
    out = pd.concat(parts, ignore_index=True)
    out["NWI_ID"] = out["NWI_ID"].astype(str)
    return out.sort_values(["NWI_ID", "YEAR", "MONTH"]).reset_index(drop=True), pd.DataFrame(qc)
