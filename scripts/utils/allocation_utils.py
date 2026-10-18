"""
allocation_utils.py
===================
Utilities for dynamic SW/GW mixed-field allocation driven by SPI drought index.

Functions
---------
spi_to_sw_ratio         Convert an SPI value to a SW allocation fraction.
apply_dynamic_acreage   Add year-by-year dynamic acreage columns to a projection DF.
save_mixed_field_metadata  Persist mixed-field metadata as a JSON sidecar file.
load_mixed_field_metadata  Load mixed-field metadata JSON; returns None if missing.

Physical basis
--------------
Mixed-irrigation fields (IRR_SRC == 3) draw from both surface water and groundwater.
In dry years (low SPI) less surface water is available, so more comes from groundwater.
In wet years (high SPI) surface supply is plentiful, raising the SW fraction.

The SW ratio varies linearly from ``min_sw_ratio`` (at ``spi_min``) to
``max_sw_ratio`` (at ``spi_max``) and is clipped outside that SPI range.

Author: Peter ReVelle
"""

import glob
import json
import os
import re
import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml


# ---------------------------------------------------------------------------
# Basin allocation overrides (YAML-driven IRR_SRC reclassification)
# ---------------------------------------------------------------------------

def load_basin_allocation_overrides(yaml_path: str) -> dict:
    """
    Load basin-specific allocation overrides from a YAML file.

    Parameters
    ----------
    yaml_path : str
        Path to ``basin_allocation_overrides.yaml``.

    Returns
    -------
    dict
        ``{basin_code_str: gw_ratio_float}`` for each entry in
        ``basin_overrides``.  The ``default_gw_ratio`` key is excluded;
        only explicit per-basin overrides are returned.

    Raises
    ------
    FileNotFoundError
        If *yaml_path* does not exist.
    """
    path = Path(yaml_path)
    if not path.is_file():
        raise FileNotFoundError(
            f"Basin allocation overrides file not found: {yaml_path}"
        )

    with open(path, 'r') as f:
        raw = yaml.safe_load(f)

    overrides = {}
    for basin_code, gw_ratio in (raw.get('basin_overrides') or {}).items():
        overrides[str(basin_code)] = float(gw_ratio)
    return overrides


def apply_irr_src_overrides(
    df: pd.DataFrame,
    overrides: dict,
    basin_column: str = 'HA_NAME_CODE',
) -> pd.DataFrame:
    """
    Reclassify IRR_SRC for mixed fields in basins with extreme overrides.

    For each basin in *overrides*:

    * ``gw_ratio == 1.0`` → IRR_SRC 3 → 1 (mixed becomes pure GW)
    * ``gw_ratio == 0.0`` → IRR_SRC 3 → 2 (mixed becomes pure SW)
    * Other values log a warning (intermediate ratios not yet supported
      via reclassification).

    Parameters
    ----------
    df : pd.DataFrame
        Field-level data with *basin_column* and ``IRR_SRC`` columns.
    overrides : dict
        ``{basin_code_str: gw_ratio}`` from
        :func:`load_basin_allocation_overrides`.
    basin_column : str
        Column containing basin identifiers in the format
        ``"Basin Name (CODE)"``.

    Returns
    -------
    pd.DataFrame
        Modified copy of *df* with reclassified ``IRR_SRC`` values.
    """
    if not overrides:
        return df

    df = df.copy()

    # Build a lookup: numeric basin code → full HA_NAME_CODE values
    code_pattern = re.compile(r'\(([^)]+)\)\s*$')
    ha_to_code = {}
    for val in df[basin_column].dropna().unique():
        m = code_pattern.search(str(val))
        if m:
            ha_to_code[str(val)] = m.group(1)

    # Invert: numeric code → set of HA_NAME_CODE strings
    code_to_ha = {}
    for ha_name, code in ha_to_code.items():
        code_to_ha.setdefault(code, set()).add(ha_name)

    total_reclassified = 0
    for basin_code_str, gw_ratio in overrides.items():
        ha_names = code_to_ha.get(basin_code_str, set())
        if not ha_names:
            continue

        mask = (
            df[basin_column].isin(ha_names)
            & (df['IRR_SRC'] == 3)
        )
        n_rows = mask.sum()
        if n_rows == 0:
            continue

        if gw_ratio == 1.0:
            df.loc[mask, 'IRR_SRC'] = 1  # mixed → pure GW
            n_fields = df.loc[mask, 'OPENET_ID'].nunique()
            print(f"  ⚙️  Override basin {basin_code_str}: "
                  f"reclassified {n_fields} mixed fields → pure GW "
                  f"({n_rows} records)")
            total_reclassified += n_fields
        elif gw_ratio == 0.0:
            df.loc[mask, 'IRR_SRC'] = 2  # mixed → pure SW
            n_fields = df.loc[mask, 'OPENET_ID'].nunique()
            print(f"  ⚙️  Override basin {basin_code_str}: "
                  f"reclassified {n_fields} mixed fields → pure SW "
                  f"({n_rows} records)")
            total_reclassified += n_fields
        else:
            warnings.warn(
                f"Basin {basin_code_str}: intermediate gw_ratio={gw_ratio} "
                f"not supported for IRR_SRC reclassification (skipped). "
                f"Only 0.0 and 1.0 are currently handled."
            )

    if total_reclassified > 0:
        print(f"  ✓ Total fields reclassified by overrides: {total_reclassified}")

    return df


# ---------------------------------------------------------------------------
# SPI → SW ratio mapping
# ---------------------------------------------------------------------------

def resolve_dynamic_allocation_config(
    dma_config: Optional[dict] = None,
    basin_code: str = None,
    metadata: Optional[dict] = None,
) -> dict:
    """Resolve one canonical dynamic-allocation parameter set.

    Basin overrides are keyed by HYD_AREA code (for example ``117``), while
    callers may provide a formatted HA name such as ``Fish Lake Valley (117)``.
    Metadata values are used as a compatibility fallback for older runs.
    """
    import re

    dma = dict(dma_config or {})
    meta = dict(metadata or {})
    code = str(basin_code or '')
    match = re.search(r'\(([^)]+)\)\s*$', code)
    if match:
        code = match.group(1)
    overrides = dma.get('basin_overrides') or {}
    override = overrides.get(code, {}) or {}
    params = {
        'min_sw_ratio': float(override.get('min_sw_ratio', dma.get('min_sw_ratio', 0.2))),
        'max_sw_ratio': float(override.get('max_sw_ratio', dma.get('max_sw_ratio', 0.8))),
        'spi_timescale': int(override.get('spi_timescale', dma.get('spi_timescale', 12))),
        'center_ratio': override.get(
            'center_sw_ratio',
            override.get('sw_ratio_center', dma.get('center_sw_ratio')),
        ),
        'spi_center': float(override.get('spi_center', dma.get('spi_center', 0.0))),
        'fallback_ratio': override.get('fallback_ratio', dma.get('fallback_ratio')),
        'spi_reference_month': int(override.get(
            'spi_reference_month', dma.get('spi_reference_month', 9)
        )),
    }
    if params['center_ratio'] is None and meta.get('sw_ratio_center') is not None:
        params['center_ratio'] = float(meta['sw_ratio_center'])
    elif params['center_ratio'] is not None:
        params['center_ratio'] = float(params['center_ratio'])
    if params['fallback_ratio'] is None:
        params['fallback_ratio'] = (
            params['center_ratio'] if params['center_ratio'] is not None else 0.5
        )
    else:
        params['fallback_ratio'] = float(params['fallback_ratio'])
    if meta.get('spi_reference_month') is not None and code not in overrides:
        params['spi_reference_month'] = int(meta['spi_reference_month'])
    if meta.get('sw_ratio_min') is not None and code not in overrides:
        params['min_sw_ratio'] = float(meta['sw_ratio_min'])
    if meta.get('sw_ratio_max') is not None and code not in overrides:
        params['max_sw_ratio'] = float(meta['sw_ratio_max'])
    return params

def spi_to_sw_ratio(
    spi: float,
    min_ratio: float = 0.2,
    max_ratio: float = 0.8,
    spi_min: float = -2.0,
    spi_max: float = 2.0,
    center_ratio: Optional[float] = None,
    spi_center: float = 0.0,
) -> float:
    """
    Map an SPI value to a surface-water allocation fraction.

    Uses linear interpolation between ``spi_min`` → ``min_ratio`` and
    ``spi_max`` → ``max_ratio``, clipped so the result is always within
    [min_ratio, max_ratio].  When ``center_ratio`` is supplied, the mapping
    is piecewise-linear through ``spi_center`` → ``center_ratio``.  The
    optional center preserves the historical anchor while retaining explicit
    dry/wet bounds.

    Parameters
    ----------
    spi : float
        Standardized Precipitation-Evapotranspiration Index (or SPI) value.
    min_ratio : float
        Minimum SW fraction (applied in extreme drought, ``spi <= spi_min``).
    max_ratio : float
        Maximum SW fraction (applied in extreme wetness, ``spi >= spi_max``).
    spi_min : float
        SPI value that maps to ``min_ratio``.  Default -2.0.
    spi_max : float
        SPI value that maps to ``max_ratio``.  Default +2.0.
    center_ratio : float, optional
        SW fraction at ``spi_center``.  If omitted, the legacy two-point
        linear mapping is used.
    spi_center : float
        SPI value for ``center_ratio``.  Default 0.0.

    Returns
    -------
    float
        SW allocation fraction in [min_ratio, max_ratio].

    Examples
    --------
    >>> spi_to_sw_ratio(-2.0)
    0.2
    >>> spi_to_sw_ratio(0.0)
    0.5
    >>> spi_to_sw_ratio(0.0, 0.2, 0.7, center_ratio=0.36)
    0.36
    >>> spi_to_sw_ratio(2.0)
    0.8
    >>> spi_to_sw_ratio(-5.0)   # clips to min
    0.2
    >>> spi_to_sw_ratio(5.0)    # clips to max
    0.8
    """
    if min_ratio > max_ratio:
        raise ValueError("min_ratio must be <= max_ratio")
    if center_ratio is not None:
        center_ratio = float(center_ratio)
        if not min_ratio <= center_ratio <= max_ratio:
            raise ValueError("center_ratio must lie between min_ratio and max_ratio")
        if not spi_min <= spi_center <= spi_max:
            raise ValueError("spi_center must lie between spi_min and spi_max")

    if pd.isna(spi):
        return float(center_ratio if center_ratio is not None
                     else (min_ratio + max_ratio) / 2.0)

    spi = float(spi)
    if spi_max == spi_min:
        return float(center_ratio if center_ratio is not None
                     else (min_ratio + max_ratio) / 2.0)

    if center_ratio is None:
        ratio = min_ratio + (spi - spi_min) / (spi_max - spi_min) * (max_ratio - min_ratio)
    elif spi <= spi_center:
        span = spi_center - spi_min
        ratio = min_ratio if span == 0 else min_ratio + (spi - spi_min) / span * (center_ratio - min_ratio)
    else:
        span = spi_max - spi_center
        ratio = max_ratio if span == 0 else center_ratio + (spi - spi_center) / span * (max_ratio - center_ratio)
    return float(np.clip(ratio, min_ratio, max_ratio))


# ---------------------------------------------------------------------------
# SPI-dependent irrigation efficiency
# ---------------------------------------------------------------------------

def irr_eff_from_sw_ratio(
    sw_ratio,
    A: float,
    B: float,
    C: float,
    D: float,
    water_source: str,
    fallback: float = 0.75,
):
    """
    Compute basin irrigation efficiency as a function of SPI-driven allocation.

    The overall basin efficiency is decomposed into pure-field and mixed-field
    components during preprocessing (Step 3).  At projection time (Step 4),
    the allocation ratio ``r`` for this water source determines how much of
    the mixed-field weight is included.

    Formula::

        irr_eff = (A + r × B) / (C + r × D)

    where:
        A = Σ(ETa_j × eff_j) for pure fields  (constant per basin)
        B = Σ(ETa_k × eff_k) for mixed fields  (constant per basin)
        C = Σ ETa_j           for pure fields  (constant per basin)
        D = Σ ETa_k           for mixed fields  (constant per basin)
        r = allocation ratio for THIS water source:
            SW → r = sw_ratio
            GW → r = 1 − sw_ratio

    Parameters
    ----------
    sw_ratio : float or array-like
        SW allocation fraction from :func:`spi_to_sw_ratio` (0–1).
    A, B, C, D : float
        Decomposed efficiency components from preprocessing.
    water_source : str
        ``'SW'`` or ``'GW'``.
    fallback : float
        Default efficiency returned when the denominator is ≤ 0.

    Returns
    -------
    float or np.ndarray
        Irrigation efficiency, clipped to [0.3, 0.95].
    """
    # Allocation ratio for THIS water source
    if water_source.upper() == 'SW':
        r = sw_ratio
    else:  # GW
        r = 1.0 - np.asarray(sw_ratio) if not isinstance(sw_ratio, (int, float)) else 1.0 - sw_ratio

    denom = C + r * D

    # Scalar path
    if isinstance(denom, (int, float)):
        if denom <= 0:
            return fallback
        return float(np.clip((A + r * B) / denom, 0.3, 0.95))

    # Array path (vectorised over rows)
    result = np.full_like(np.asarray(denom, dtype=float), fallback)
    valid = np.asarray(denom) > 0
    r_arr = np.asarray(r, dtype=float)
    result[valid] = (A + r_arr[valid] * B) / np.asarray(denom, dtype=float)[valid]
    return np.clip(result, 0.3, 0.95)


# ---------------------------------------------------------------------------
# Dynamic acreage application
# ---------------------------------------------------------------------------

def apply_dynamic_acreage(
    df: pd.DataFrame,
    water_source: str,
    pure_acres: float,
    mixed_acres: float,
    spi_col: str = 'SPI_12m',
    spi_reference_month: int = 9,
    min_sw_ratio: float = 0.2,
    max_sw_ratio: float = 0.8,
    spi_range: list = None,
    fallback_ratio: float = 0.5,
    center_ratio: float = None,
    spi_center: float = 0.0,
) -> pd.DataFrame:
    """
    Add year-by-year dynamic acreage columns driven by SPI.

    For each calendar year the SPI value at ``spi_reference_month`` (default:
    September) is used to compute a SW allocation fraction via
    :func:`spi_to_sw_ratio`.  That fraction is applied uniformly to **all**
    months within that year.

    New columns added to the returned DataFrame
    -------------------------------------------
    total_acres        : float   Pure acres + dynamic mixed allocation.
    dynamic_acres      : bool    Always True (signals dynamic path was taken).
    sw_ratio           : float   SW fraction used for each row's year.
    mixed_acres_applied: float   Mixed acres allocated to this water source.

    Acreage conservation
    --------------------
    For a given year::

        SW.total_acres + GW.total_acres == pure_sw + pure_gw + mixed_acres

    provided the same SPI value and parameters are used for both sources.

    Parameters
    ----------
    df : pd.DataFrame
        Projection DataFrame with at minimum ``year``, ``month``, and the
        column named by ``spi_col``.
    water_source : str
        ``'SW'`` or ``'GW'``.
    pure_acres : float
        Acreage from fields that are exclusively SW or GW (excluding mixed).
    mixed_acres : float
        Total acreage of mixed-source fields for this basin.
    spi_col : str
        Name of the SPI column to use.
    spi_reference_month : int
        Month (1-12) from which the annual SPI value is sampled.
    min_sw_ratio : float
        Minimum SW fraction (extreme drought).
    max_sw_ratio : float
        Maximum SW fraction (extreme wetness).
    spi_range : list of two floats, optional
        [spi_min, spi_max] passed to :func:`spi_to_sw_ratio`.
        Defaults to [-2.0, 2.0].
    fallback_ratio : float
        SW ratio used when the reference month SPI is NaN.
    center_ratio : float, optional
        SW ratio at ``spi_center``.  Omit for the legacy linear mapping.
    spi_center : float
        SPI anchor for ``center_ratio``.

    Returns
    -------
    pd.DataFrame
        Copy of ``df`` with the four new columns described above.
    """
    if spi_range is None:
        spi_range = [-2.0, 2.0]

    df = df.copy()

    if spi_col not in df.columns:
        warnings.warn(
            f"apply_dynamic_acreage: column '{spi_col}' not found; "
            f"using fallback_ratio={fallback_ratio} for all rows."
        )
        sw_ratio_series = pd.Series(fallback_ratio, index=df.index)
    else:
        # Map SPI → sw_ratio helper
        def _year_ratio(spi_val):
            if pd.isna(spi_val):
                return fallback_ratio
            return spi_to_sw_ratio(
                spi_val, min_sw_ratio, max_sw_ratio,
                spi_range[0], spi_range[1],
                center_ratio=center_ratio, spi_center=spi_center,
            )

        has_model = 'model' in df.columns

        if has_model:
            # ---- Per-GCM allocation: each model gets its OWN SPI-driven ratio ----
            # Without this, all 20 GCMs would share the ensemble-mean SPI,
            # collapsing inter-model variation in acreage (and thus volume).
            group_keys = ['year', 'model']
            ref_cols = ['year', 'model', spi_col]
        else:
            group_keys = ['year']
            ref_cols = ['year', spi_col]

        # Sample SPI at reference month for each (year [, model])
        ref = df[df['month'] == spi_reference_month][ref_cols].copy()
        ref = ref.rename(columns={spi_col: '_ref_spi'})
        ref = ref.groupby(group_keys, as_index=False)['_ref_spi'].mean()
        ref['_sw_ratio'] = ref['_ref_spi'].apply(_year_ratio)

        if has_model:
            # Build a multi-key lookup and map row-by-row
            ratio_lookup = ref.set_index(['year', 'model'])['_sw_ratio']
            sw_ratio_series = (
                df.set_index(['year', 'model'])
                  .index
                  .map(ratio_lookup)
                  .to_series(index=df.index)
                  .fillna(fallback_ratio)
            )
        else:
            year_to_ratio = dict(zip(ref['year'], ref['_sw_ratio']))
            sw_ratio_series = df['year'].map(year_to_ratio).fillna(fallback_ratio)

    df['sw_ratio'] = sw_ratio_series.values

    if water_source.upper() == 'SW':
        df['mixed_acres_applied'] = df['sw_ratio'] * mixed_acres
    else:  # GW
        df['mixed_acres_applied'] = (1.0 - df['sw_ratio']) * mixed_acres

    df['total_acres'] = pure_acres + df['mixed_acres_applied']
    df['dynamic_acres'] = True

    return df


# ---------------------------------------------------------------------------
# Mixed-field metadata JSON sidecar helpers
# ---------------------------------------------------------------------------

def _metadata_filename(basin_code: str, water_source: str) -> str:
    """Return the standard filename for a mixed-field metadata JSON sidecar."""
    return f"{basin_code}_mixed_fields_{water_source.upper()}.json"


def save_mixed_field_metadata(
    output_dir: str,
    basin_code: str,
    water_source: str,
    pure_acres: float,
    mixed_acres: float,
    total_acres: float,
    spi_timescale: int = 12,
    allocation_method: str = 'default',
    n_mixed_fields: int = 0,
    n_pure_fields: int = 0,
    mixed_acres_shapefile: float = None,
    ha_combined_avg_acres: float = None,
    irr_eff_components: dict = None,
    sw_ratio_min: float = None,
    sw_ratio_max: float = None,
    sw_ratio_center: float = None,
    spi_center: float = 0.0,
    spi_reference_month: int = 9,
    allocation_fallback_ratio: float = None,
) -> str:
    """
    Save mixed-field allocation metadata as a JSON sidecar file.

    The file is written to ``<output_dir>/<basin_code>_mixed_fields_<WATER_SOURCE>.json``.

    Parameters
    ----------
    output_dir : str
        Directory in which to write the JSON file.
    basin_code : str
        3-digit (zero-padded) basin code, e.g. ``'105'``.
    water_source : str
        ``'SW'`` or ``'GW'``.
    pure_acres : float
        Acres from exclusively SW or GW fields.
    mixed_acres : float
        Acres from mixed-source fields (participation-scaled when available).
    total_acres : float
        Sum of pure and mixed acres.
    spi_timescale : int
        SPI accumulation period in months (e.g. 12 for SPI-12).
    allocation_method : str
        Label describing how the allocation was derived (e.g. ``'optimized'``).
    n_mixed_fields : int
        Count of mixed-source fields included.
    n_pure_fields : int
        Count of pure-source fields included.
    mixed_acres_shapefile : float, optional
        Full (unscaled) shapefile mixed-field acreage, for audit trail.
    ha_combined_avg_acres : float, optional
        Combined SW+GW historical average annual acres from HA summaries.
        This is the SPI-neutral anchor used to derive the scaled mixed pool.
    irr_eff_components : dict, optional
        Decomposed irrigation efficiency components for SPI-dependent
        efficiency.  Expected keys: ``irr_eff_pure_eta_x_eff`` (A),
        ``irr_eff_mixed_eta_x_eff`` (B), ``irr_eff_pure_eta`` (C),
        ``irr_eff_mixed_eta`` (D), ``volume_weighted_irr_eff``.

    Returns
    -------
    str
        Absolute path of the saved JSON file.
    """
    os.makedirs(output_dir, exist_ok=True)
    filename = _metadata_filename(basin_code, water_source)
    filepath = os.path.join(output_dir, filename)

    metadata = {
        'basin_code': basin_code,
        'water_source': water_source.upper(),
        'pure_acres': float(pure_acres),
        'mixed_acres': float(mixed_acres),
        'total_acres': float(total_acres),
        'spi_timescale': int(spi_timescale),
        'allocation_method': allocation_method,
        'n_mixed_fields': int(n_mixed_fields),
        'n_pure_fields': int(n_pure_fields),
    }
    if mixed_acres_shapefile is not None:
        metadata['mixed_acres_shapefile'] = float(mixed_acres_shapefile)
    if ha_combined_avg_acres is not None:
        metadata['ha_combined_avg_acres'] = float(ha_combined_avg_acres)
    if sw_ratio_min is not None:
        metadata['sw_ratio_min'] = float(sw_ratio_min)
    if sw_ratio_max is not None:
        metadata['sw_ratio_max'] = float(sw_ratio_max)
    if sw_ratio_center is not None:
        metadata['sw_ratio_center'] = float(sw_ratio_center)
        metadata['spi_center'] = float(spi_center)
    metadata['spi_reference_month'] = int(spi_reference_month)
    if allocation_fallback_ratio is not None:
        metadata['allocation_fallback_ratio'] = float(allocation_fallback_ratio)

    # SPI-dependent irrigation efficiency decomposition (A/B/C/D)
    if irr_eff_components is not None:
        for key in ('irr_eff_pure_eta_x_eff', 'irr_eff_mixed_eta_x_eff',
                     'irr_eff_pure_eta', 'irr_eff_mixed_eta',
                     'volume_weighted_irr_eff', 'irr_eff_volume_weighted_raw'):
            if key in irr_eff_components:
                metadata[key] = float(irr_eff_components[key])

    with open(filepath, 'w') as f:
        json.dump(metadata, f, indent=2)

    return filepath


def load_mixed_field_metadata(
    metadata_dir: str,
    basin_code: str,
    water_source: str,
) -> Optional[dict]:
    """
    Load mixed-field allocation metadata from a JSON sidecar file.

    Looks for ``<metadata_dir>/<basin_code>_mixed_fields_<WATER_SOURCE>.json``.
    Returns ``None`` gracefully if the file does not exist.

    Parameters
    ----------
    metadata_dir : str
        Directory that contains the JSON sidecar files.
    basin_code : str
        3-digit basin code, e.g. ``'105'``.
    water_source : str
        ``'SW'`` or ``'GW'``.

    Returns
    -------
    dict or None
        Metadata dictionary, or ``None`` if the file is missing.
    """
    filename = _metadata_filename(basin_code, water_source)
    filepath = os.path.join(metadata_dir, filename)

    if not os.path.exists(filepath):
        return None

    with open(filepath, 'r') as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Mixed-field metadata generation (cross-reference SW & GW active fields)
# ---------------------------------------------------------------------------

def _load_basin_spi_timescale(
    model_results_dir,
    basin_code,
    water_source,
    model_subdir='monthly_production_models_recursive',
):
    """
    Read the trained model JSON for a basin to get its spi_timescale.

    Returns the per-basin spi_timescale from the configured Phase 2 model
    tree, or 12 if the model file is not found.
    """
    model_dir = os.path.join(
        model_results_dir,
        'mixed_effects_monthly_comparison_core_0yr_stable_irrigation',
        water_source.upper(),
        model_subdir,
    )
    if not os.path.isdir(model_dir):
        return 12

    pattern = os.path.join(model_dir, f'*({basin_code})_monthly_production_model.json')
    matches = glob.glob(pattern)
    if not matches:
        return 12

    with open(matches[0], 'r') as f:
        model_params = json.load(f)
    return model_params.get('spi_timescale', 12)


def generate_mixed_field_metadata(
    active_fields: dict,
    output_base_dir: str,
    window_years: int,
    model_results_dir: str = None,
    allocation_config: dict = None,
):
    """
    Cross-reference SW and GW active field lists to identify mixed fields
    and generate metadata JSON sidecar files for dynamic acreage allocation.

    For every basin that appears in both SW and GW active field rosters,
    fields appearing in **both** lists are classified as "mixed" (they can
    be irrigated by either source depending on water availability).  The
    remaining fields are "pure".

    Parameters
    ----------
    active_fields : dict
        ``{water_source: {basin_code: active_info}}`` where ``active_info``
        has keys ``'field_ids'``, ``'acres'`` (dict field_id→acres), and
        ``'avg_annual_acres'``.
    output_base_dir : str
        Root directory under which per-water-source / per-basin subdirectories
        are created (e.g. ``results/basin_future_projections``).
    window_years : int
        Active-field window (used in directory naming).
    model_results_dir : str, optional
        Path to ``mixed_effects_results_walkforward_sw_gw/`` so we can read
        each basin's trained ``spi_timescale`` from its model JSON.  If
        ``None``, defaults to ``spi_timescale=12`` for all basins.
    allocation_config : dict, optional
        Dynamic allocation configuration.  ``basin_overrides`` may define
        ``min_sw_ratio``, ``max_sw_ratio``, ``center_sw_ratio``,
        ``fallback_ratio``, and ``spi_reference_month`` per basin.

    Returns
    -------
    int
        Number of metadata files written.
    """
    allocation_config = allocation_config or {}
    basin_overrides = allocation_config.get('basin_overrides', {}) or {}
    default_min = float(allocation_config.get('min_sw_ratio', 0.2))
    default_max = float(allocation_config.get('max_sw_ratio', 0.8))
    default_center = allocation_config.get('center_sw_ratio')
    default_fallback = float(allocation_config.get(
        'fallback_ratio', default_center if default_center is not None else 0.5
    ))
    default_month = int(allocation_config.get('spi_reference_month', 9))
    water_sources = sorted(active_fields.keys())

    # Collect every basin code across both water sources
    all_basins = set()
    for ws in water_sources:
        all_basins.update(active_fields[ws].keys())

    metadata_count = 0

    for basin_code in sorted(all_basins):
        basin_allocation = basin_overrides.get(str(basin_code), {}) or {}
        basin_center = basin_allocation.get('center_sw_ratio', default_center)
        basin_fallback = float(basin_allocation.get(
            'fallback_ratio', basin_center if basin_center is not None else default_fallback
        ))
        sw_info = active_fields.get('SW', {}).get(basin_code)
        gw_info = active_fields.get('GW', {}).get(basin_code)

        if sw_info and gw_info:
            # ----- Basin has both SW and GW → identify mixed fields -----
            sw_ids = set(sw_info['field_ids'])
            gw_ids = set(gw_info['field_ids'])
            mixed_ids = sw_ids & gw_ids

            for ws in water_sources:
                info = active_fields[ws].get(basin_code)
                if info is None:
                    continue

                other_ws = 'GW' if ws == 'SW' else 'SW'
                other_info = active_fields.get(other_ws, {}).get(basin_code)

                ws_ids = set(info['field_ids'])
                pure_ids = ws_ids - mixed_ids

                pure_acres = sum(
                    info['acres'].get(fid, 0.0) for fid in pure_ids
                )

                # Compute FULL (unsplit) mixed acreage by summing from BOTH
                # water sources.  Phase 1 dynamic allocation already splits
                # each mixed field's ACRES_FTR_GEOM between SW and GW
                # (e.g. 50/50).  The active-field lists therefore carry the
                # split values.  Summing both sides recovers the original
                # shapefile acreage so that apply_dynamic_acreage() can
                # correctly re-split based on future SPI without
                # double-reducing.
                mixed_acres_this_ws = 0.0
                mixed_acres_full = 0.0
                for fid in (mixed_ids & ws_ids):
                    this_acres = info['acres'].get(fid, 0.0)
                    other_acres = (other_info['acres'].get(fid, 0.0)
                                   if other_info else 0.0)
                    mixed_acres_this_ws += this_acres
                    mixed_acres_full += this_acres + other_acres

                mixed_acres = mixed_acres_full
                total_acres = pure_acres + mixed_acres

                # Normalize pure_acres and mixed_acres so that at the mean
                # SPI allocation (fallback_ratio = 0.5 for SW), the dynamic
                # total equals the observed avg_annual_acres.  The raw pool
                # (pure + mixed from the 5-year union) overcounts because
                # not all fields are active every year.  Scaling preserves
                # the SPI-driven shape while anchoring the level to the
                # historical average.
                avg_annual_acres = info.get('avg_annual_acres')
                if avg_annual_acres is not None and avg_annual_acres > 0 and total_acres > 0:
                    fallback = basin_fallback
                    if ws == 'SW':
                        baseline_dynamic = pure_acres + fallback * mixed_acres
                    else:
                        baseline_dynamic = pure_acres + (1.0 - fallback) * mixed_acres

                    if baseline_dynamic > 0:
                        scale = avg_annual_acres / baseline_dynamic
                        pure_acres_raw = pure_acres
                        mixed_acres_raw = mixed_acres
                        pure_acres = pure_acres * scale
                        mixed_acres = mixed_acres * scale
                        total_acres = pure_acres + mixed_acres
                        if abs(scale - 1.0) > 0.01:
                            print(f"    📐 Basin {basin_code} [{ws}]: Normalized acreage "
                                  f"(scale={scale:.3f}, avg_annual={avg_annual_acres:,.0f}, "
                                  f"raw_baseline={baseline_dynamic:,.0f})")

                # An explicit basin override is authoritative.  Otherwise
                # retain the trained model timescale for backwards
                # compatibility.  This is required for evidence-based
                # basins whose allocation driver differs from the Kc model's
                # fitted SPI timescale (e.g., Carson Valley SPI-6).
                spi_ts = basin_allocation.get('spi_timescale')
                if spi_ts is None:
                    spi_ts = 12
                    if model_results_dir:
                        spi_ts = _load_basin_spi_timescale(
                            model_results_dir,
                            basin_code,
                            ws,
                            model_subdir=allocation_config.get(
                                'spi_timescale_source',
                                'monthly_production_models_recursive',
                            ),
                        )
                spi_ts = int(spi_ts)

                output_dir = os.path.join(
                    output_base_dir,
                    f'monthly_climate_{window_years}yr_active_fields_{ws}',
                    basin_code,
                )

                save_mixed_field_metadata(
                    output_dir=output_dir,
                    basin_code=basin_code,
                    water_source=ws,
                    pure_acres=pure_acres,
                    mixed_acres=mixed_acres,
                    total_acres=total_acres,
                    spi_timescale=spi_ts,
                    allocation_method=str(basin_allocation.get('allocation_method', 'default')),
                    n_mixed_fields=len(mixed_ids & ws_ids),
                    n_pure_fields=len(pure_ids),
                    mixed_acres_shapefile=mixed_acres_full,
                    sw_ratio_min=float(basin_overrides.get(str(basin_code), {}).get('min_sw_ratio', default_min)),
                    sw_ratio_max=float(basin_overrides.get(str(basin_code), {}).get('max_sw_ratio', default_max)),
                    sw_ratio_center=basin_overrides.get(str(basin_code), {}).get('center_sw_ratio', default_center),
                    spi_reference_month=int(basin_overrides.get(str(basin_code), {}).get('spi_reference_month', default_month)),
                    allocation_fallback_ratio=float(basin_overrides.get(str(basin_code), {}).get('fallback_ratio', default_fallback)),
                )
                metadata_count += 1
        else:
            # ----- Basin in only one water source → all fields are pure -----
            ws = 'SW' if sw_info else 'GW'
            info = sw_info or gw_info

            total_acres = sum(info['acres'].values())

            # Normalize: use avg_annual_acres if available (anchors to
            # historical observed footprint, not the 5-year union pool)
            avg_annual_acres = info.get('avg_annual_acres')
            if avg_annual_acres is not None and avg_annual_acres > 0:
                total_acres = avg_annual_acres

            spi_ts = basin_allocation.get('spi_timescale')
            if spi_ts is None:
                spi_ts = 12
                if model_results_dir:
                    spi_ts = _load_basin_spi_timescale(
                        model_results_dir,
                        basin_code,
                        ws,
                        model_subdir=allocation_config.get(
                            'spi_timescale_source',
                            'monthly_production_models_recursive',
                        ),
                    )
            spi_ts = int(spi_ts)

            output_dir = os.path.join(
                output_base_dir,
                f'monthly_climate_{window_years}yr_active_fields_{ws}',
                basin_code,
            )

            save_mixed_field_metadata(
                output_dir=output_dir,
                basin_code=basin_code,
                water_source=ws,
                pure_acres=total_acres,
                mixed_acres=0.0,
                total_acres=total_acres,
                spi_timescale=spi_ts,
                n_mixed_fields=0,
                n_pure_fields=len(info['field_ids']),
                sw_ratio_min=float(basin_overrides.get(str(basin_code), {}).get('min_sw_ratio', default_min)),
                sw_ratio_max=float(basin_overrides.get(str(basin_code), {}).get('max_sw_ratio', default_max)),
                sw_ratio_center=basin_overrides.get(str(basin_code), {}).get('center_sw_ratio', default_center),
                spi_reference_month=int(basin_overrides.get(str(basin_code), {}).get('spi_reference_month', default_month)),
                allocation_fallback_ratio=float(basin_overrides.get(str(basin_code), {}).get('fallback_ratio', default_fallback)),
            )
            metadata_count += 1

    return metadata_count
