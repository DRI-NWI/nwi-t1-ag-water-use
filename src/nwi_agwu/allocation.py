"""Dynamic SPI 50/50 SW/GW allocation for mixed fields.

SW share of a mixed field in basin-year (b, y):
    r = clip(center + slope(SPI), min, max), piecewise linear through
        SPI = -2 -> min, SPI = 0 -> center, SPI = +2 -> max
(allocation_utils.spi_to_sw_ratio). With the defaults (0.2, 0.5, 0.8) this is
GW share = clip(0.5 - 0.15 x Sept SPI-12, 0.2, 0.8). Missing SPI -> fallback ratio.
Pure GW fields have SW share 0, pure SW fields 1.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .fields import ha_code_norm


def spi_to_sw_ratio(spi, min_ratio=0.2, max_ratio=0.8, spi_min=-2.0, spi_max=2.0,
                    center_ratio=None, spi_center=0.0) -> float:
    """Vendored verbatim in behaviour from allocation_utils.spi_to_sw_ratio (52ca0ba)."""
    if min_ratio > max_ratio:
        raise ValueError("min_ratio must be <= max_ratio")
    if pd.isna(spi) or spi_max == spi_min:
        return float(center_ratio if center_ratio is not None else (min_ratio + max_ratio) / 2.0)
    spi = float(spi)
    if center_ratio is None:
        ratio = min_ratio + (spi - spi_min) / (spi_max - spi_min) * (max_ratio - min_ratio)
    elif spi <= spi_center:
        span = spi_center - spi_min
        ratio = min_ratio if span == 0 else min_ratio + (spi - spi_min) / span * (center_ratio - min_ratio)
    else:
        span = spi_max - spi_center
        ratio = max_ratio if span == 0 else center_ratio + (spi - spi_center) / span * (max_ratio - center_ratio)
    return float(np.clip(ratio, min_ratio, max_ratio))


def basin_year_ratios(mixed_basins, years, spi_table: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    a = cfg["allocation"]
    dyn = {ha_code_norm(k): v for k, v in (a.get("dynamic_overrides") or {}).items()} \
        if a.get("apply_dynamic_overrides", True) else {}
    spi = spi_table.set_index(["HA_CODE", "YEAR"])
    rows = []
    for code in sorted(set(mixed_basins)):
        rule = dyn.get(code)
        lo = float(rule.get("min_sw_ratio", a["min_sw_ratio"])) if rule else a["min_sw_ratio"]
        hi = float(rule.get("max_sw_ratio", a["max_sw_ratio"])) if rule else a["max_sw_ratio"]
        center = float(rule["center_sw_ratio"]) if rule and "center_sw_ratio" in rule else None
        fallback = float(rule.get("fallback_ratio", center if center is not None else 0.5)) if rule else 0.5
        method = rule.get("allocation_method", "dynamic_override") if rule else "default_fallback"
        for y in years:
            v = spi["spi_value"].get((code, y), np.nan)
            src = spi["spi_source"].get((code, y), "missing")
            if pd.isna(v):
                r, status = fallback, "missing"
            else:
                r = spi_to_sw_ratio(v, lo, hi, a["spi_min"], a["spi_max"], center_ratio=center)
                status = f"SPI={v:+.2f}"
            rows.append({"HA_CODE": code, "YEAR": y, "allocation_method": method, "spi_value": v,
                         "spi_source": src, "sw_ratio": r, "spi_status": status,
                         "min_sw_ratio": lo, "max_sw_ratio": hi,
                         "center_sw_ratio": center if center is not None else 0.5})
    return pd.DataFrame(rows)


def field_year_sw_share(attrs: pd.DataFrame, ratios: pd.DataFrame, years) -> pd.DataFrame:
    """SW share per field-year (0 pure GW, 1 pure SW, r mixed)."""
    fy = pd.MultiIndex.from_product([attrs["NWI_ID"], years], names=["NWI_ID", "YEAR"]).to_frame(index=False)
    fy = fy.merge(attrs[["NWI_ID", "IRR_SRC", "HA_CODE"]], on="NWI_ID", how="left")
    fy = fy.merge(ratios[["HA_CODE", "YEAR", "sw_ratio", "spi_value", "spi_source", "allocation_method"]],
                  on=["HA_CODE", "YEAR"], how="left")
    src = fy["IRR_SRC"].astype(float)
    fy["sw_share"] = np.select([src.eq(1), src.eq(2), src.eq(3)], [0.0, 1.0, fy["sw_ratio"]], np.nan)
    fy["allocation_method"] = np.select([src.eq(1), src.eq(2)], ["pure_gw", "pure_sw"], fy["allocation_method"])
    for c in ["spi_value", "spi_source"]:
        fy.loc[~src.eq(3), c] = np.nan if c == "spi_value" else ""
    if fy["sw_share"].isna().any():
        raise ValueError(f"{int(fy.sw_share.isna().sum())} field-years without an SW share")
    return fy[["NWI_ID", "YEAR", "sw_share", "allocation_method", "spi_value", "spi_source"]]
