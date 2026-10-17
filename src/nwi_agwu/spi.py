"""SPI for the dynamic mixed-field allocation.

Method (unchanged from phase-1 extend_basin_climate_spi_1979_2023.py and the
mixed-field review K_prism_spi/01_compute_spi.py): climate_indices.indices.spi with a
gamma distribution, monthly periodicity, and ONE calibration window applied to every
year (configured 1981-2024; PRISM 800 m daily starts in 1981). Each regional series
must be gap-free.

Source rule ``prism_d24`` (decision D24, confirmed in D29): PRISM precipitation over
the HA's surface-water contributing area when one is defined, otherwise PRISM over the
HA itself.
"""

from __future__ import annotations

import logging
import warnings

import numpy as np
import pandas as pd

from .fields import ha_code_norm


def _quiet():
    logging.disable(logging.CRITICAL)
    try:
        import structlog
        structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))
    except Exception:
        pass
    warnings.filterwarnings("ignore")


def spi_series(ppt: pd.DataFrame, scales, cal0: int, cal1: int) -> pd.DataFrame:
    """ppt: YEAR, MONTH, PPT_MM for one region. Returns SPI_<k>m columns."""
    from climate_indices import compute, indices

    ppt = ppt.sort_values(["YEAR", "MONTH"])
    y0, y1 = int(ppt.YEAR.min()), int(ppt.YEAR.max())
    full = pd.MultiIndex.from_product([range(y0, y1 + 1), range(1, 13)], names=["YEAR", "MONTH"])
    x = ppt.set_index(["YEAR", "MONTH"])["PPT_MM"].reindex(full).to_numpy(float)
    obs = np.flatnonzero(~np.isnan(x))
    if np.isnan(x[obs[0]: obs[-1] + 1]).any():
        raise ValueError("missing PPT inside series")
    c0, c1 = max(cal0, y0), min(cal1, y1)
    out = pd.DataFrame(index=full).reset_index()
    for k in scales:
        out[f"SPI_{k}m"] = indices.spi(x, scale=k, distribution=indices.Distribution.gamma, data_start_year=y0,
                                       calibration_year_initial=c0, calibration_year_final=c1,
                                       periodicity=compute.Periodicity.monthly)
    out["PPT_MM"] = x
    out["calibration"] = f"{c0}-{c1}"
    last = tuple(ppt[["YEAR", "MONTH"]].iloc[-1])
    return out[[tuple(r) <= last for r in out[["YEAR", "MONTH"]].to_numpy()]]


def prism_spi_monthly(region_ppt_csv: str, cfg: dict) -> pd.DataFrame:
    _quiet()
    s = cfg["spi"]
    p = pd.read_csv(region_ppt_csv, dtype={"HA": str})
    res = []
    for (rt, ha), g in p.groupby(["region_type", "HA"]):
        o = spi_series(g[["YEAR", "MONTH", "PPT_MM"]], [3, 6, 12, 24, 36, 48, 60],
                       s["calibration_start"], s["calibration_end"])
        o.insert(0, "HA", ha)
        o.insert(1, "region_type", rt)
        o.insert(2, "name", g["name"].iloc[0])
        res.append(o)
    out = pd.concat(res, ignore_index=True)
    out["HA_CODE"] = out["HA"].map(ha_code_norm)
    return out


def basin_year_prism_d24(monthly: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    s = cfg["spi"]
    col = f"SPI_{s['timescale_months']}m"
    m = monthly[monthly["MONTH"].eq(s["reference_month"])]
    ha = m[m.region_type.eq("ha_only")].set_index(["HA_CODE", "YEAR"])[col].rename("spi_ha")
    ca = m[m.region_type.eq("contributing_area")].set_index(["HA_CODE", "YEAR"])[col].rename("spi_ca")
    t = pd.concat([ha, ca], axis=1).reset_index()
    t["spi_value"] = t["spi_ca"].where(t["spi_ca"].notna(), t["spi_ha"])
    t["spi_source"] = np.where(t["spi_ca"].notna(), "prism_contributing_area", "prism_ha")
    t.loc[t["spi_value"].isna(), "spi_source"] = "missing"
    return t[["HA_CODE", "YEAR", "spi_value", "spi_source", "spi_ha", "spi_ca"]]


def basin_year_legacy(legacy_csv: str, cfg: dict) -> pd.DataFrame:
    s = cfg["spi"]
    col = f"SPI_{s['timescale_months']}m"
    d = pd.read_csv(legacy_csv)
    d = d[d["MONTH"].eq(s["reference_month"])]
    d["HA_CODE"] = d["HA_NAME_CODE"].map(ha_code_norm)
    t = d.groupby(["HA_CODE", "YEAR"], as_index=False)[col].first().rename(columns={col: "spi_value"})
    t["spi_source"] = np.where(t["spi_value"].notna(), "legacy_gridmet_ag_footprint", "missing")
    return t
