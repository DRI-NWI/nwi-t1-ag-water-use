"""USDA texture classification and the Tan et al. (2025) parameter lookup.

Two responsibilities:
  1. Classify sand/silt/clay percentages (POLARIS) into the 12 USDA texture classes.
  2. Map (texture, rooting depth, ET0) -> (d_dec, d_ext, p) for f(d), using the
     committed Tan Table 2 (bare soil, ET0 = 2.5/5/10) and Table 3 (vegetated, root
     depth 100/200 cm, ET0 = 5) parameters.

Parameter modes:
  * "bare"     - Table 2 bare-soil row, linearly interpolated across ET0. Used for the
                 4-model comparison figure that reproduces the attached plot.
  * "crop_et0" - headline. Table 3 vegetated row at the field's rooting depth (ET0 = 5),
                 with d_dec rescaled by the bare-soil d_dec(ET0)/d_dec(5) ratio so the
                 documented ET0 sensitivity (Tan: d_dec falls as ET0 rises; d_ext, p ~
                 invariant) carries over to the vegetated case.

Depths are returned in METRES (the CSV is in cm).
"""

from __future__ import annotations

import functools

import numpy as np
import pandas as pd

from . import constants as config

# Set by the caller (configured inputs.tan_params_csv) before the first lookup.
TAN_PARAMS_CSV = None

# 12 USDA classes, matching the `texture` values in tan2025_params.csv.
USDA_CLASSES = [
    "sand", "loamy sand", "sandy loam", "loam", "silt loam", "silt",
    "sandy clay loam", "clay loam", "silty clay loam", "sandy clay", "silty clay", "clay",
]


def usda_texture_class(sand, silt, clay):
    """USDA texture class from sand/silt/clay percentages (0-100). Standard triangle rules.

    Inputs need not sum to exactly 100 (POLARIS layers are renormalized by the caller);
    the rules below follow the USDA Soil Survey Manual decision tree.
    """
    if any(np.isnan([sand, silt, clay])):
        return None
    # Normalize defensively.
    tot = sand + silt + clay
    if tot <= 0:
        return None
    sand, silt, clay = 100.0 * sand / tot, 100.0 * silt / tot, 100.0 * clay / tot

    if silt + 1.5 * clay < 15:
        return "sand"
    if silt + 1.5 * clay >= 15 and silt + 2.0 * clay < 30:
        return "loamy sand"
    if (7 <= clay < 20 and sand > 52 and silt + 2.0 * clay >= 30) or (clay < 7 and silt < 50 and silt + 2.0 * clay >= 30):
        return "sandy loam"
    if 7 <= clay < 27 and 28 <= silt < 50 and sand <= 52:
        return "loam"
    if (silt >= 50 and 12 <= clay < 27) or (50 <= silt < 80 and clay < 12):
        return "silt loam"
    if silt >= 80 and clay < 12:
        return "silt"
    if 20 <= clay < 35 and silt < 28 and sand > 45:
        return "sandy clay loam"
    if 27 <= clay < 40 and 20 < sand <= 45:
        return "clay loam"
    if 27 <= clay < 40 and sand <= 20:
        return "silty clay loam"
    if clay >= 35 and sand > 45:
        return "sandy clay"
    if clay >= 40 and silt >= 40:
        return "silty clay"
    if clay >= 40 and sand <= 45 and silt < 40:
        return "clay"
    return "loam"  # interior fallback (rare rounding cases)


def classify_textures(sand, silt, clay):
    """Vectorized USDA classification over equal-length arrays. Returns object array of classes."""
    sand = np.asarray(sand, float)
    silt = np.asarray(silt, float)
    clay = np.asarray(clay, float)
    return np.array([usda_texture_class(s, si, c) for s, si, c in zip(sand, silt, clay)], dtype=object)


@functools.lru_cache(maxsize=1)
def _param_table():
    df = pd.read_csv(TAN_PARAMS_CSV)
    df["texture"] = df["texture"].str.strip().str.lower()
    return df


def _interp_over_et0(sub, et0):
    """Linearly interpolate (p, d_dec_cm, d_ext_cm) over ET0 within one (texture, veg, root) group."""
    sub = sub.sort_values("ET0_mm_d")
    x = sub["ET0_mm_d"].to_numpy(float)
    et0 = float(np.clip(et0, x.min(), x.max()))
    return {
        "p": float(np.interp(et0, x, sub["p"].to_numpy(float))),
        "d_dec_cm": float(np.interp(et0, x, sub["d_dec_cm"].to_numpy(float))),
        "d_ext_cm": float(np.interp(et0, x, sub["d_ext_cm"].to_numpy(float))),
    }


def params_for(texture, root_depth_cm=config.ROOT_DEPTH_SHALLOW_CM,
               et0_mm_d=config.ET0_DEFAULT_MM_D, mode="crop_et0"):
    """Return (d_dec_m, d_ext_m, p) for a texture. See module docstring for modes."""
    df = _param_table()
    texture = str(texture).strip().lower()
    if texture not in set(df["texture"]):
        texture = "loam"  # safe default for unmapped/None

    if mode == "bare":
        sub = df[(df.texture == texture) & (df.veg == "bare")]
        vals = _interp_over_et0(sub, et0_mm_d)
        return vals["d_dec_cm"] / 100.0, vals["d_ext_cm"] / 100.0, vals["p"]

    if mode == "crop_et0":
        # Vegetated base row at ET0 = 5 for the chosen rooting depth.
        rd = config.ROOT_DEPTH_DEEP_CM if root_depth_cm and root_depth_cm >= 150 else config.ROOT_DEPTH_SHALLOW_CM
        veg = df[(df.texture == texture) & (df.veg == "veg") & (df.root_depth_cm == rd)]
        if veg.empty:
            return params_for(texture, et0_mm_d=et0_mm_d, mode="bare")
        p = float(veg["p"].iloc[0])
        d_dec_cm = float(veg["d_dec_cm"].iloc[0])
        d_ext_cm = float(veg["d_ext_cm"].iloc[0])
        # ET0 adjustment to d_dec from the bare-soil ratio (d_ext, p kept ET0-invariant).
        bare = df[(df.texture == texture) & (df.veg == "bare")]
        b = _interp_over_et0(bare, et0_mm_d)
        b5 = _interp_over_et0(bare, 5.0)
        ratio = b["d_dec_cm"] / b5["d_dec_cm"] if b5["d_dec_cm"] > 0 else 1.0
        d_dec_cm *= ratio
        return d_dec_cm / 100.0, d_ext_cm / 100.0, p

    raise ValueError(f"unknown mode {mode!r}")


def assign_params(df, mode="crop_et0"):
    """Add d_dec_m, d_ext_m, p columns to a per-field DataFrame.

    Expects columns: `texture`; for crop_et0 also `root_depth_cm` and `et0_mm_d`.
    Caches by the distinct (texture, root_depth_cm, et0-rounded) tuples for speed.
    """
    out = df.copy()
    rd = out["root_depth_cm"] if "root_depth_cm" in out else pd.Series(config.ROOT_DEPTH_SHALLOW_CM, index=out.index)
    et0 = out["et0_mm_d"] if "et0_mm_d" in out else pd.Series(config.ET0_DEFAULT_MM_D, index=out.index)
    et0r = et0.round(1)
    keys = list(zip(out["texture"].astype(str), rd.fillna(config.ROOT_DEPTH_SHALLOW_CM), et0r.fillna(config.ET0_DEFAULT_MM_D)))
    cache = {}
    d_dec = np.empty(len(out)); d_ext = np.empty(len(out)); pexp = np.empty(len(out))
    for i, k in enumerate(keys):
        if k not in cache:
            cache[k] = params_for(k[0], root_depth_cm=k[1], et0_mm_d=k[2], mode=mode)
        d_dec[i], d_ext[i], pexp[i] = cache[k]
    out["d_dec_m"], out["d_ext_m"], out["p"] = d_dec, d_ext, pexp
    return out
