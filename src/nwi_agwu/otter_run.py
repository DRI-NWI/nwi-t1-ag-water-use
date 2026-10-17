"""Per-field continuous OTTER run on PRISM 800 m precipitation -> monthly Peff.

Follows the final Nevada OTTER settings (Prz_Comparison run_nv_otter_matched_continuous
_monthly.py and nwi-t1-etdemands run_otter_prism.py): unchanged kernel, NM05 parameters
(init_dru_frac = init_drl_frac = mad_frac = 0.5), continuous history from the first
year, static soils per field-year, monthly EToF forward-filled over the full history.
Differences, both intended: precipitation is each field's own daily PRISM 800 m value
(not gridMET or a PRISM cell), and ETo is the same OpenET gridMET daily ETo extracted
with it; monthly Peff is written for every year, not only 2014-2023.
"""

from __future__ import annotations

import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

NODATA = -9999.0
_W: dict = {}


def stage_daily(met_dir: str, fields: list[str], dates: pd.DatetimeIndex, out_dir: Path):
    """Write (days x fields) float32 memmaps of PRISM ppt and ETo."""
    import duckdb

    idx = pd.Series(np.arange(len(fields)), index=fields)
    shape = (len(dates), len(fields))
    pr = np.lib.format.open_memmap(out_dir / "daily_ppt_mm.npy", mode="w+", dtype="float32", shape=shape)
    et = np.lib.format.open_memmap(out_dir / "daily_eto_mm.npy", mode="w+", dtype="float32", shape=shape)
    pr[:] = np.nan
    et[:] = np.nan
    day0 = dates[0]
    con = duckdb.connect()
    con.execute("SET threads=12")
    for y in sorted(set(dates.year)):
        glob = str(Path(met_dir) / f"year={y}" / "met_*.parquet")
        d = con.execute(f"""SELECT NWI_ID, CAST(date AS VARCHAR) AS date, CAST(ppt AS DOUBLE) AS ppt,
                            CAST(eto AS DOUBLE) AS eto
                            FROM read_parquet('{glob}', hive_partitioning=false, union_by_name=true)""").df()
        col = idx.reindex(d["NWI_ID"].astype(str)).to_numpy()
        ok = ~np.isnan(col)
        row = (pd.to_datetime(d["date"].str[:10]) - day0).dt.days.to_numpy()
        pr[row[ok], col[ok].astype(int)] = d["ppt"].to_numpy("float32")[ok]
        et[row[ok], col[ok].astype(int)] = d["eto"].to_numpy("float32")[ok]
        print(f"[otter] staged {y}", flush=True)
    pr.flush(); et.flush()
    return out_dir / "daily_ppt_mm.npy", out_dir / "daily_eto_mm.npy"


def load_static(path: str, fields: list[str], start: int, end: int):
    """aws_u, aws_max, cn per field-year with the PRZ validity rule."""
    s = pd.read_parquet(path, columns=["NWI_ID", "year", "aws_u", "aws_max", "cn"])
    s["NWI_ID"] = s["NWI_ID"].astype(str)
    years = list(range(start, end + 1))
    s = s[s["NWI_ID"].isin(fields) & s["year"].isin(years)]
    if s.duplicated(["NWI_ID", "year"]).any():
        raise ValueError("static soils table has duplicate NWI_ID/year")
    full = pd.MultiIndex.from_product([fields, years], names=["NWI_ID", "year"])
    s = s.set_index(["NWI_ID", "year"]).reindex(full)
    nf, ny = len(fields), len(years)
    aws_u = s["aws_u"].to_numpy(float).reshape(nf, ny)
    aws_max_rows = s["aws_max"].to_numpy(float).reshape(nf, ny)
    cn = s["cn"].to_numpy(float).reshape(nf, ny)
    with np.errstate(invalid="ignore"):
        rng = np.nanmax(aws_max_rows, axis=1) - np.nanmin(aws_max_rows, axis=1)
        reasons = []
        bad = np.zeros(nf, bool)
        checks = {
            "missing_static": ~(np.isfinite(aws_u).all(1) & np.isfinite(aws_max_rows).all(1) & np.isfinite(cn).all(1)),
            "nonpositive_aws_u": (aws_u <= 0).any(1),
            "nonpositive_aws_max": (aws_max_rows <= 0).any(1),
            "nonpositive_cn": (cn <= 0).any(1),
            "cn_gt_100": (cn > 100).any(1),
            "aws_u_gt_aws_max": (aws_u > aws_max_rows).any(1),
            "aws_max_varies": rng > 1e-5,
        }
    for k, m in checks.items():
        bad |= m
    reason = np.array([";".join(k for k, m in checks.items() if m[i]) for i in range(nf)], dtype=object)
    audit = pd.DataFrame({"NWI_ID": fields, "otter_status": np.where(bad, "skipped", "eligible"), "reason": reason})
    return aws_u, aws_max_rows[:, 0].copy(), cn, bad, audit


def etof_matrix(base: pd.DataFrame, fields: list[str], start: int, end: int) -> np.ndarray:
    full = pd.MultiIndex.from_product([fields, range(start, end + 1), range(1, 13)], names=["NWI_ID", "YEAR", "MONTH"])
    e = base.set_index(["NWI_ID", "YEAR", "MONTH"])["EToF_interpolated"].reindex(full).reset_index()
    e["EToF_interpolated"] = e.groupby("NWI_ID", sort=False)["EToF_interpolated"].ffill()
    return e["EToF_interpolated"].to_numpy("float32").reshape(len(fields), (end - start + 1) * 12)


def _init(pr_path, eto_path, etof, aws_u, aws_max, cn, dates, start, nm):
    _W.update(pr=np.load(pr_path, mmap_mode="r"), eto=np.load(eto_path, mmap_mode="r"), etof=etof,
              aws_u=aws_u, aws_max=aws_max, cn=cn, dates=pd.DatetimeIndex(dates), start=start, nm=nm)


def _run_chunk(indices):
    from nwi_agwu.otter_water_balance import do_wb_interp

    pr, eto, etof = _W["pr"], _W["eto"], _W["etof"]
    dates, start = _W["dates"], _W["start"]
    yi = dates.year.to_numpy() - start
    mi = yi * 12 + dates.month.to_numpy() - 1
    nmon = int(mi.max()) + 1
    out = []
    for i in indices:
        p = np.asarray(pr[:, i], dtype="float32").copy()
        e0 = np.asarray(eto[:, i], dtype="float32").copy()
        et = e0 * etof[i][mi]
        et[~np.isfinite(et)] = NODATA
        res = do_wb_interp(float(_W["aws_max"][i]), _W["aws_u"][i, yi].astype("float32"),
                           _W["cn"][i, yi].astype("float32"), p, et, e0,
                           init_dru_frac=_W["nm"][0], init_drl_frac=_W["nm"][1], mad_frac=_W["nm"][2])
        peff = np.bincount(mi, weights=np.asarray(res[6], float), minlength=nmon)
        prm = np.bincount(mi, weights=p.astype(float), minlength=nmon)
        etaw = np.bincount(mi, weights=np.asarray(res[5], float), minlength=nmon)
        out.append((i, peff, prm, etaw))
    return out


def run(cfg: dict, base: pd.DataFrame, fields: list[str], paths, workers: int = 16) -> pd.DataFrame:
    start, end = cfg["run"]["start_year"], cfg["run"]["end_year"]
    o = cfg["effective_precipitation"]["otter"]
    dates = pd.date_range(f"{start}-01-01", f"{end}-12-31", freq="D")
    stage_dir = paths.otter / "staging"
    stage_dir.mkdir(parents=True, exist_ok=True)
    pr_path, eto_path = stage_dir / "daily_ppt_mm.npy", stage_dir / "daily_eto_mm.npy"
    if not (pr_path.exists() and eto_path.exists()):
        stage_daily(cfg["inputs"]["met_daily_dir"], fields, dates, stage_dir)
    pr = np.load(pr_path, mmap_mode="r")
    if np.isnan(pr).any():
        raise ValueError("daily PRISM staging has gaps")
    aws_u, aws_max, cn, bad, audit = load_static(cfg["inputs"]["otter_static_soils"], fields, start, end)
    audit.to_csv(paths.otter_audit, index=False)
    etof = etof_matrix(base, fields, start, end)
    nm = (o["init_dru_frac"], o["init_drl_frac"], o["mad_frac"])
    todo = [i for i in range(len(fields)) if not bad[i]]
    chunks = [todo[k:k + 250] for k in range(0, len(todo), 250)]
    nmon = (end - start + 1) * 12
    peff = np.full((len(fields), nmon), np.nan)
    prm = np.full((len(fields), nmon), np.nan)
    etaw = np.full((len(fields), nmon), np.nan)
    ctx = multiprocessing.get_context("spawn")
    done = 0
    with ProcessPoolExecutor(workers, mp_context=ctx, initializer=_init,
                             initargs=(str(pr_path), str(eto_path), etof, aws_u, aws_max, cn,
                                       dates.to_numpy(), start, nm)) as ex:
        for fut in as_completed([ex.submit(_run_chunk, c) for c in chunks]):
            for i, a, b, c in fut.result():
                peff[i], prm[i], etaw[i] = a, b, c
            done += 1
            if done % 10 == 0 or done == len(chunks):
                print(f"[otter] chunks {done}/{len(chunks)}", flush=True)
    years = np.repeat(np.arange(start, end + 1), 12)
    months = np.tile(np.arange(1, 13), end - start + 1)
    out = pd.DataFrame({
        "NWI_ID": np.repeat(np.asarray(fields, dtype=object), nmon),
        "YEAR": np.tile(years, len(fields)).astype("int16"),
        "MONTH": np.tile(months, len(fields)).astype("int8"),
        "OTTER_PEFF_MM": peff.ravel(), "OTTER_PR_MM": prm.ravel(), "OTTER_ETAW_MM": etaw.ravel(),
    })
    status = audit.set_index("NWI_ID")["otter_status"]
    out["OTTER_STATUS"] = out["NWI_ID"].map(status)
    return out
