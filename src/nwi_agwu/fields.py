"""Field attributes from the 2026-08-20 boundary layer.

The boundary layer is the authority for source type (IRR_SRC), irrigation
efficiency, and the hydrographic area (the ``Basin`` attribute), as in the
delivered processor (``enforce_boundary_basin_mapping``).
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

ATTRIBUTE_COLUMNS = ["NWI_ID", "NWI_ID_PARENT", "STATE", "Basin", "Acres", "IRR_SRC",
                     "irr_eff", "ITYPE", "GRIDMET_ID", "CROP_major"]


def ha_code_norm(code) -> str | None:
    """'030A' / '30A' / 'Fish Lake Valley (117)' -> '30A' / '117' (leading zeros stripped)."""
    if code is None or (isinstance(code, float) and np.isnan(code)):
        return None
    text = str(code).strip()
    m = re.search(r"\(([^)]+)\)\s*$", text)
    if m:
        text = m.group(1)
    text = text.split("_")[0].strip().upper()
    return text.lstrip("0") or "0"


def load_attributes(gpkg: str, cfg: dict, export_attrs: pd.DataFrame | None = None
                    ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (attributes, report). One row per NWI_ID.

    ``export_attrs`` (NWI_ID, irr_eff, ITYPE from the OpenET export) take precedence over
    the boundary layer, as in the delivered processor (merge_field_attributes keeps
    non-null export values and only fills nulls from the boundary layer).
    """
    import geopandas as gpd

    g = gpd.read_file(gpkg, columns=ATTRIBUTE_COLUMNS, ignore_geometry=True)
    g = g.rename(columns={"NWI_ID_PARENT": "NWI_PARENT"})
    if g["NWI_ID"].duplicated().any():
        raise ValueError("boundary layer has duplicate NWI_ID")
    if export_attrs is not None:
        e = export_attrs.set_index("NWI_ID")
        for col in ["irr_eff", "ITYPE"]:
            g[col] = g["NWI_ID"].map(e[col]).combine_first(pd.to_numeric(g[col], errors="coerce"))
    parts = g["Basin"].astype("string").str.extract(r"^([^_]+)_(.*)$")
    g["HYD_AREA"] = parts[0].str.strip()
    g["HYD_AREA_N"] = parts[1].str.strip()
    g["HA_NAME_CODE"] = g["HYD_AREA_N"] + " (" + g["HYD_AREA"] + ")"
    g["HA_CODE"] = g["HYD_AREA"].map(ha_code_norm)

    fa = cfg["field_attributes"]
    g["IRR_SRC_ASSUMED"] = g["IRR_SRC"].isna()
    eff = pd.to_numeric(g["irr_eff"], errors="coerce")
    g["irr_eff_ASSUMED"] = eff.isna() | (eff <= 0)
    if fa.get("assume_missing", True):
        g.loc[g["IRR_SRC_ASSUMED"], "IRR_SRC"] = fa["assumed_irr_src"]
        g.loc[g["irr_eff_ASSUMED"], "irr_eff"] = fa["assumed_irr_eff"]
    g["IRR_SRC"] = pd.to_numeric(g["IRR_SRC"], errors="coerce").astype("Int64")
    g["IRR_SRC_ORIGINAL"] = g["IRR_SRC"]

    report = pd.DataFrame([
        {"metric": "fields", "value": len(g)},
        {"metric": "fields_by_state", "value": g["STATE"].value_counts().to_dict()},
        {"metric": "irr_src_assumed_fields", "value": int(g["IRR_SRC_ASSUMED"].sum())},
        {"metric": "irr_eff_assumed_fields", "value": int(g["irr_eff_ASSUMED"].sum())},
        {"metric": "irr_src_counts", "value": g["IRR_SRC"].value_counts(dropna=False).to_dict()},
        {"metric": "missing_basin", "value": int(g["Basin"].isna().sum())},
    ])
    return g, report


def apply_static_overrides(attrs: pd.DataFrame, overrides: dict) -> pd.DataFrame:
    """gw share 1.0 -> mixed fields become GW; 0.0 -> SW (allocation_utils.apply_irr_src_overrides)."""
    out = attrs.copy()
    out["IRR_SRC_OVERRIDE"] = ""
    for code, gw_ratio in (overrides or {}).items():
        mask = out["HA_CODE"].eq(ha_code_norm(code)) & out["IRR_SRC"].eq(3)
        if float(gw_ratio) == 1.0:
            out.loc[mask, "IRR_SRC"] = 1
            out.loc[mask, "IRR_SRC_OVERRIDE"] = f"basin {code}: mixed->GW"
        elif float(gw_ratio) == 0.0:
            out.loc[mask, "IRR_SRC"] = 2
            out.loc[mask, "IRR_SRC_OVERRIDE"] = f"basin {code}: mixed->SW"
        else:
            raise ValueError(f"static override {code}={gw_ratio}: only 0.0 or 1.0 are supported")
    return out


def load_county_assignment(path: str) -> pd.DataFrame:
    c = pd.read_csv(path, usecols=["NWI_ID", "COUNTY_NAME", "include_in_county_run", "exclusion_reason"])
    return c.rename(columns={"include_in_county_run": "IN_NV_COUNTY"})
