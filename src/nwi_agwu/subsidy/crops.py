"""Map CDL crop codes (gpkg `CROP_major` / `CROP_YYYY`) to rooting depth and a crop flag.

Crop information lives only in the field-boundary GeoPackage (the monthly water-balance
CSVs carry no crop column); it is joined to records by `OPENET_ID`. Crop is used solely
to choose the rooting-depth class for the Tan Table 3 vegetated parameters -- the ET
itself always comes from OpenET in the CSVs.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import constants as config

# Human-readable labels for the CDL codes that occur in this field inventory (for figures/QC).
CDL_LABELS = {
    1: "Corn", 22: "Durum Wheat", 23: "Spring Wheat", 24: "Winter Wheat", 27: "Rye",
    36: "Alfalfa", 37: "Other Hay/Non-Alfalfa", 43: "Potatoes", 49: "Onions", 61: "Fallow/Idle",
    69: "Grapes", 111: "Open Water", 121: "Developed/Open", 122: "Developed/Low",
    123: "Developed/Med", 124: "Developed/High", 131: "Barren", 141: "Deciduous Forest",
    142: "Evergreen Forest", 143: "Mixed Forest", 152: "Shrubland", 176: "Grass/Pasture",
    190: "Woody Wetlands", 195: "Herbaceous Wetlands", 204: "Pistachios", 205: "Triticale",
    211: "Olives",
}


def root_depth_cm(cdl_code):
    """Rooting depth (cm) for a CDL code: deep (200) for alfalfa/phreatophytes, else 100."""
    if cdl_code is None or (isinstance(cdl_code, float) and np.isnan(cdl_code)):
        return config.ROOT_DEPTH_SHALLOW_CM
    return config.ROOT_DEPTH_DEEP_CM if int(cdl_code) in config.CDL_DEEP_ROOT else config.ROOT_DEPTH_SHALLOW_CM


def is_non_crop(cdl_code):
    """True if the CDL class is natural/non-irrigated cover (not an irrigated crop)."""
    if cdl_code is None or (isinstance(cdl_code, float) and np.isnan(cdl_code)):
        return False
    return int(cdl_code) in config.CDL_NON_CROP


def annotate(df, code_col="CROP_major"):
    """Add root_depth_cm, is_non_crop, and crop_label columns from a CDL-code column."""
    out = df.copy()
    codes = out[code_col]
    out["root_depth_cm"] = codes.map(root_depth_cm)
    out["is_non_crop"] = codes.map(is_non_crop)
    out["crop_label"] = codes.map(lambda c: CDL_LABELS.get(int(c), str(c)) if pd.notna(c) else "Unknown")
    return out
