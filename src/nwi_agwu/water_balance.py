"""Field water balance: NetET, groundwater-ET subsidy, winter carry-forward, applied water.

Order (per field-month, whole field):
  1. ETa      = EToF_interpolated x ETO_MM x acres
  2. Peff     = OTTER monthly effective precipitation x acres
  3. NetET_pre_subsidy = ETa - Peff
  4. GW subsidy potential = f x ETa (f = field groundwater-ET fraction, Tan 2025)
     applied subsidy      = min(potential, max(NetET_pre_subsidy, 0))   [positive_netet_only]
     and only in irrigated field-months when gw_subsidy.irrigated_only (0 otherwise)
  5. NetET_intermediate = NetET_pre_subsidy - applied subsidy
  6. Winter carry-forward: negative
     NetET_intermediate is stored as soil water, capped at AWC x rooting depth x acres,
     reset every January, and drawn down by later positive months.
  7. AR_final = NetET_final / irr_eff (0 when NetET_final <= 0)

The carry-forward recurrence is compiled with numba.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numba import njit

from .units import FT_PER_M, mm_to_acft


@njit(cache=True)
def _carryforward(net, month, field_start, cap, reset_month):
    n = net.size
    final = np.full(n, np.nan)
    storage = np.full(n, np.nan)
    overflow = np.zeros(n)
    prev_storage = 0.0
    prev_valid = False
    for i in range(n):
        if field_start[i]:
            prev_storage = 0.0
            prev_valid = False
        x = net[i]
        if not np.isfinite(x):
            prev_storage = 0.0
            prev_valid = False
            continue
        reset = (not prev_valid) or month[i] == reset_month
        raw = x if reset else prev_storage + x
        c = cap[i]
        lower = -c if np.isfinite(c) else -np.inf
        bounded = min(0.0, max(raw, lower))
        final[i] = raw if raw >= 0 else 0.0
        storage[i] = bounded
        overflow[i] = max(lower - raw, 0.0) if np.isfinite(c) else 0.0
        prev_storage = bounded
        prev_valid = True
    return final, storage, overflow


def compute(df: pd.DataFrame, cfg: dict, peff_mm: pd.Series, gw_frac: pd.Series | None) -> pd.DataFrame:
    """df must be sorted by NWI_ID, YEAR, MONTH and hold one row per field-month."""
    out = df
    acres = out["ACRES_FTR_GEOM"]
    out["PEFF_MM"] = peff_mm.to_numpy()
    out["ETa_final_acre_ft"] = out["ETa_vol_acre_ft"]
    out["PPT_vol_acre_ft"] = mm_to_acft(out["PPT_MM"], acres)
    out["Peff_vol_acre_ft"] = mm_to_acft(out["PEFF_MM"], acres)
    out["NetET_pre_subsidy_acre_ft"] = out["ETa_final_acre_ft"] - out["Peff_vol_acre_ft"]

    g = cfg["gw_subsidy"]
    if g.get("enabled") and gw_frac is not None:
        f = gw_frac.to_numpy()
        out["gw_frac"] = f
        pot = f * out["ETa_final_acre_ft"]
        usable = pot
        if g.get("irrigated_only", False):
            # NetET is irrigation-accounting water: the subsidy only offsets it in
            # irrigated field-months. The potential is still reported for every month.
            irrigated = out["%_IRRIGATED"] > float(g["irrigated_min_fraction"])
            usable = pot.where(irrigated, 0.0)
        if g["offset"] == "positive_netet_only":
            applied = np.minimum(usable, out["NetET_pre_subsidy_acre_ft"].clip(lower=0))
        elif g["offset"] == "plain":
            applied = usable
        else:
            raise ValueError(f"unknown subsidy offset {g['offset']!r}")
        out["GW_subsidy_potential_acre_ft"] = pot
        out["GW_subsidy_acre_ft"] = applied
    else:
        out["gw_frac"] = 0.0
        out["GW_subsidy_potential_acre_ft"] = 0.0
        out["GW_subsidy_acre_ft"] = 0.0
    out["NetET_intermediate_acre_ft"] = out["NetET_pre_subsidy_acre_ft"] - out["GW_subsidy_acre_ft"]

    cf = cfg["carryforward"]
    awc = pd.to_numeric(out["AWC"], errors="coerce")
    out["PAW_ft"] = awc.where(awc > 0, cf["awc_floor"]) * float(cf["rooting_depth_m"]) * FT_PER_M
    out["Soil_Moisture_Cap_acre_ft"] = out["PAW_ft"] * acres
    eff = pd.to_numeric(out["irr_eff"], errors="coerce").to_numpy()
    if cf.get("enabled", True):
        ids = out["NWI_ID"].to_numpy()
        start = np.r_[True, ids[1:] != ids[:-1]]
        final, storage, overflow = _carryforward(
            out["NetET_intermediate_acre_ft"].to_numpy(float), out["MONTH"].to_numpy(np.int64), start,
            out["Soil_Moisture_Cap_acre_ft"].to_numpy(float), int(cf["reset_month"]))
        out["NetET_final_acre_ft"] = final
        out["Soil_Storage_acre_ft"] = -storage
        out["Soil_Moisture_Overflow_acre_ft"] = overflow
        with np.errstate(divide="ignore", invalid="ignore"):
            ar = np.where(final > 0, final / eff, 0.0)
        ar[~np.isfinite(final) | ~np.isfinite(eff)] = np.nan
        out["AR_final_acre_ft"] = ar
    else:
        out["NetET_final_acre_ft"] = out["NetET_intermediate_acre_ft"]
        out["Soil_Storage_acre_ft"] = 0.0
        out["Soil_Moisture_Overflow_acre_ft"] = 0.0
        out["AR_final_acre_ft"] = out["NetET_final_acre_ft"] / eff
    # Final volumes are non-negative.
    out.loc[out["AR_final_acre_ft"] < 0, "AR_final_acre_ft"] = 0.0
    out.loc[out["NetET_final_acre_ft"] < 0, "NetET_final_acre_ft"] = 0.0
    out["Carryforward_Used_acre_ft"] = out["NetET_intermediate_acre_ft"].clip(lower=0) - out["NetET_final_acre_ft"]
    for v in ["ETa", "NetET", "AR"]:
        out[f"{v}_final_ft"] = (out[f"{v}_final_acre_ft"] / acres.replace(0, np.nan)).fillna(0)
    out["PPT_final_ft"] = (out["PPT_vol_acre_ft"] / acres.replace(0, np.nan)).fillna(0)
    return out


VOLUME_COLUMNS = ["ACRES_FTR_GEOM", "ACRES_IRR", "ETa_final_acre_ft", "PPT_vol_acre_ft", "Peff_vol_acre_ft",
                  "NetET_pre_subsidy_acre_ft", "GW_subsidy_potential_acre_ft", "GW_subsidy_acre_ft",
                  "NetET_intermediate_acre_ft", "Soil_Moisture_Cap_acre_ft", "Soil_Storage_acre_ft",
                  "Soil_Moisture_Overflow_acre_ft", "Carryforward_Used_acre_ft", "NetET_final_acre_ft",
                  "AR_final_acre_ft"]


def split_components(all_df: pd.DataFrame, share: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Scale whole-field rows into GW and SW components by the field-year SW share.

    The carry-forward chain resets every January and the share is constant within a
    calendar year, so every volume (and the storage cap) scales linearly with the
    component acreage, and GW + SW = ALL exactly.
    """
    s = all_df[["NWI_ID", "YEAR"]].merge(share, on=["NWI_ID", "YEAR"], how="left", validate="many_to_one")
    sw = s["sw_share"].to_numpy()
    out = {}
    for comp, frac in (("GW", 1.0 - sw), ("SW", sw)):
        keep = frac > 0
        d = all_df.loc[keep].copy()
        fr = frac[keep]
        for c in VOLUME_COLUMNS:
            if c in d.columns:
                d[c] = d[c] * fr
        src = d["IRR_SRC"].astype(float).to_numpy()
        d["water_source"] = np.where(src == 3, f"{comp}_from_mixed", comp)
        d["allocation_ratio"] = fr
        d["sw_share"] = sw[keep]
        d["allocation_method"] = s.loc[keep, "allocation_method"].to_numpy()
        d["spi_value"] = s.loc[keep, "spi_value"].to_numpy()
        d["spi_source"] = s.loc[keep, "spi_source"].to_numpy()
        out[comp] = d
    a = all_df.copy()
    a["water_source"] = np.select([a["IRR_SRC"].eq(1), a["IRR_SRC"].eq(2)], ["GW", "SW"], "mixed")
    a["allocation_ratio"] = 1.0
    a["sw_share"] = sw
    a["allocation_method"] = s["allocation_method"].to_numpy()
    a["spi_value"] = s["spi_value"].to_numpy()
    a["spi_source"] = s["spi_source"].to_numpy()
    out["ALL"] = a
    return out
