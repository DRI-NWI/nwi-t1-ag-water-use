"""
Process field summaries and generate HA (Hydrographic Area) level aggregations.

This script processes field-level ET data and aggregates it to basin level,
including water source (SW/GW) separation and allocation optimization.

CONFIGURATION:
All paths and parameters loaded from config/nwi_t1_final.yaml

Author: Peter ReVelle
        With AI assistance from Claude, Gemini, ChatGPT, and other models
Date: 2024-2025
Updated: January 13, 2026 - Unified configuration system
Updated: October 2026 - NWI T1 final processing (edits marked "NWI T1")

NWI T1 edits to the agricultural-water-forecasting version (commit 58819af):
  * Settings come from config/nwi_t1_final.yaml through scripts/utils/nwi_config.py.
  * PPT_MM is PRISM 800 m monthly precipitation (merge_prism_precipitation).
  * EFF_PPT_MM is OTTER effective precipitation (merge_otter_effective_precipitation);
    fields without OTTER soils use 0.85 x PPT and are flagged in peff_method.
  * Step 4.5 subtracts the groundwater-ET subsidy before the carry-forward (add_gw_subsidy).
  * Step 6.1 no longer back-calculates PPT as EFF_PPT / 0.85.
  * Basin rules come from the run config. The original read the dynamic rules through
    load_config() with no arguments, so the Fish Lake Valley (117) rule never applied in
    NWI runs; fixed rules (Diamond Valley 153) are applied to the boundary attributes once
    (apply_static_basin_overrides).
  * Climatologies are built beforehand by calculate_field_climatologies.py. The original
    checked for etrf_ files, so it always recalculated them from CSV inputs, which the
    parquet export does not have.
  * Counties come from the county assignment table (merge_county_assignment).
  * GW and SW components and the whole field (ALL) are processed in one run.
  * Field files keep a documented set of columns (FIELD_OUTPUT_COLUMNS). HA and county
    summaries are built by aggregate_summaries, from irrigated field-years when
    summaries.irrigated_only is set, with Nevada and out-of-state parts in the HA files.

Removed paths that this workflow does not use: the threshold irrigation filter
(load_basin_specific_thresholds, classify_field_activity_status,
filter_historical_data_by_thresholds), the static allocation (apply_gw_sw_separation),
fill_missing_data=False, basin_codes filtering, output-folder naming by ratio, county
assignment by polygon overlay, per-basin and per-county parquet partitions
(save_results_by_basin, save_results_by_county), the unused get_field_climatology and
load_basin_info_from_shapefile, and the original Step 6.5/7/7.6 aggregation.
"""

import json
import os
import sys
import pandas as pd
import geopandas as gpd 
import numpy as np
from pathlib import Path
import warnings

# NWI T1: run configuration and the copied allocation utilities
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'utils'))
from nwi_config import load_config, RunPaths  # noqa: E402

# Import SPI-to-ratio utility (used by apply_gw_sw_separation_dynamic)
from allocation_utils import spi_to_sw_ratio  # noqa: E402


# Attributes carried by the NWI field-boundary asset.  High-volume exports
# should contain these already, but the processor defensively backfills them
# from the matching local boundary file when an older/incomplete export is
# supplied.
FIELD_ATTRIBUTE_COLUMNS = [
    'NWI_ID', 'NWI_PARENT', 'OPENET_ID', 'GRIDMET_ID', 'Basin', 'Acres',
    'STATE', 'IRR_SRC', 'irr_eff', 'ITYPE',
    'CROP_major', 'IRR_SRC_ORIGINAL', 'IRR_SRC_OVERRIDE',   # NWI T1: crop for the GW subsidy; basin-rule audit
]


# --- Helper function to load climatologies  ---
def load_climatologies(climatology_dir, gap_windows):
    climatologies = {}
    print(f"Attempting to load EToF climatologies from: {climatology_dir}")
    for start_year, end_year in gap_windows:
        window_key = f'{start_year}_{end_year}'
        climo_filename = f'etof_climatology_{start_year}_{end_year}.csv' 
        climo_filepath = os.path.join(climatology_dir, climo_filename)
        
        if os.path.exists(climo_filepath):
            try:
                climo_df = pd.read_csv(climo_filepath, index_col='OPENET_ID') # OPENET_ID as index
                climatologies[window_key] = climo_df
                print(f"Successfully loaded climatology: {climo_filename}")
            except Exception as e:
                print(f"Error loading climatology file {climo_filepath}: {e}")
        else:
            print(f"Climatology file NOT FOUND: {climo_filepath}")
    if not climatologies: print("No climatology files were loaded.")
    return climatologies

def apply_linear_interpolation_etof(df, group_cols=['HA_NAME_CODE', 'OPENET_ID']):
    print("Applying linear interpolation for EToF gaps...")
    
    if 'EToF' not in df.columns:
        df['EToF'] = np.where(df['ETO_MM'] > 0, df['ETA_MM'] / df['ETO_MM'], np.nan)
    
    # Initialize tracking columns
    df['EToF_interpolated'] = df['EToF'].copy()
    df['interpolation_method'] = 'original'
    
    if 'date' not in df.columns:
        df['date'] = pd.to_datetime(df[['YEAR', 'MONTH']].assign(day=1))
    
    # Sort by group and date
    df = df.sort_values(group_cols + ['date'])
    
    # ONE LINE INTERPOLATION - limit=1 means only fill isolated 1-month gaps
    df['EToF_interpolated'] = df.groupby(group_cols)['EToF'].transform(
        lambda x: x.interpolate(method='linear', limit=1)
    )
    
    # Mark which values were interpolated - BEFORE any index changes
    interpolated_mask = df['EToF'].isna() & df['EToF_interpolated'].notna()
    df.loc[interpolated_mask, 'interpolation_method'] = 'linear_1month'
    
    # Update ETA_MM for interpolated values
    df.loc[interpolated_mask, 'ETA_MM'] = df['EToF_interpolated'] * df['ETO_MM']
    
    filled_count = interpolated_mask.sum()
    print(f"Linear interpolation filled {filled_count} gaps")
    
    return df

def apply_climatology_gap_filling_etof(df, climatologies, gap_windows, group_cols=['HA_NAME_CODE', 'OPENET_ID']):
    print("Applying climatology EToF gap-filling to ALL remaining gaps...")
    if not climatologies:
        print("No climatologies provided. Skipping climatology fill.")
        return df

    # Find ALL gaps that were NOT filled by linear interpolation
    gaps_to_fill_mask = (df['interpolation_method'] == 'original') & df['EToF'].isna()
    print(f"Found {gaps_to_fill_mask.sum()} unfilled gaps for climatology (any length, any year)")

    if gaps_to_fill_mask.sum() == 0:
        return df
    
    # VECTORIZED: Only process rows that need filling
    gaps_df = df[gaps_to_fill_mask].copy()
    
    # Pre-create lookups for speed
    def get_climo_values_vectorized(gaps_subset):
        results = []
        for idx, row in gaps_subset.iterrows():
            # Find window
            window_key = None
            for start_year, end_year in gap_windows:
                if start_year <= row['YEAR'] <= end_year:
                    window_key = f"{start_year}_{end_year}"
                    break
            
            if window_key is None:
                closest_window = min(gap_windows, key=lambda w: min(abs(row['YEAR'] - w[0]), abs(row['YEAR'] - w[1])))
                window_key = f"{closest_window[0]}_{closest_window[1]}"
            
            # Get climatology
            climo_value = np.nan
            if window_key in climatologies:
                climo_df = climatologies[window_key]
                field_id = row['OPENET_ID']
                
                if field_id in climo_df.index:
                    field_row = climo_df.loc[[field_id]]
                    
                    if 'HA_NAME_CODE' in field_row.columns:
                        field_row = field_row[field_row['HA_NAME_CODE'] == row['HA_NAME_CODE']]
                    
                    if not field_row.empty:
                        climo_col = f"EToF_climo_{row['MONTH']:02d}"
                        if climo_col in field_row.columns:
                            climo_value = field_row[climo_col].iloc[0]
            
            results.append(climo_value)
        
        return results
    
    # Get climatology values only for gaps
    print(f"Looking up climatology values for {len(gaps_df)} gaps...")
    climo_values = get_climo_values_vectorized(gaps_df)
    
    # Update only the gaps with valid climatology values
    valid_climo_mask = ~pd.isna(climo_values)
    gaps_df_valid = gaps_df[valid_climo_mask]
    climo_values_valid = [v for v in climo_values if pd.notna(v)]
    
    # Vectorized assignment
    df.loc[gaps_df_valid.index, 'EToF_interpolated'] = climo_values_valid
    df.loc[gaps_df_valid.index, 'interpolation_method'] = 'climatology'
    df.loc[gaps_df_valid.index, 'ETA_MM'] = df.loc[gaps_df_valid.index, 'EToF_interpolated'] * df.loc[gaps_df_valid.index, 'ETO_MM']
    
    filled_count = len(gaps_df_valid)
    print(f"Climatology filled {filled_count} gaps")
    print(f"Remaining unfilled: {gaps_to_fill_mask.sum() - filled_count}")
    
    return df


# --- Winter Moisture Carry-Forward ---
def apply_winter_moisture_carryforward(df, 
                                     eta_vol_intermediate_col,           # Intermediate ETa volume after interpolation
                                     net_et_vol_intermediate_col,    # Intermediate NetET volume (ETa_interp - EffPPT)
                                     ar_original_vol_col,       # Original AR volume (from AR_MM)
                                     irr_eff_col='irr_eff',              # Field-level irrigation efficiency from shapefile
                                     awc_col='AWC',
                                     rooting_depth_m=1.5,
                                     year_reset_month=1, # Calendar month to reset carry-forward (January)
                                     group_cols=['HA_NAME_CODE', 'OPENET_ID']):
    print(
        f"Applying winter moisture carry-forward using ETa: "
        f"{eta_vol_intermediate_col}, NetET: {net_et_vol_intermediate_col}"
    )

    if irr_eff_col not in df.columns:
        raise ValueError(
            f"{irr_eff_col} column not found in dataframe. "
            "Field attributes with irrigation efficiency must be loaded "
            "before applying carry-forward."
        )
    if awc_col not in df.columns:
        raise ValueError(
            f"{awc_col} column not found in dataframe. An explicit per-field "
            "AWC table is required when soil-moisture carry-forward is enabled."
        )

    # The recurrence is sequential within each field because storage is capped
    # and therefore cannot be represented by a simple cumulative sum.  The
    # calculation below keeps that recurrence in NumPy arrays and performs no
    # per-row pandas .loc assignments.  Resetting the index also makes the
    # function safe when an upstream GW/SW split preserves duplicate indices.
    result = df.reset_index(drop=True).copy()
    if 'date' not in result.columns:
        result['date'] = pd.to_datetime(result[['YEAR', 'MONTH']].assign(day=1))
    result['date'] = pd.to_datetime(result['date'])
    order = result.sort_values(group_cols + ['date'], kind='mergesort').index.to_numpy()

    result['NetET_final_acre_ft'] = np.nan
    result['AR_final_acre_ft'] = np.nan
    result['Water_Surplus_Cumulative_acre_ft'] = np.nan
    result['Eff_PPT_Adjusted_acre_ft'] = np.nan
    awc = pd.to_numeric(result[awc_col], errors='coerce')
    result['PAW_ft'] = awc.where(awc > 0, 0.0002) * float(rooting_depth_m) * 3.281
    result['Soil_Moisture_Cap_acre_ft'] = result['PAW_ft'] * pd.to_numeric(
        result['ACRES_FTR_GEOM'], errors='coerce'
    )
    result['Soil_Moisture_Overflow_acre_ft'] = 0.0

    groups = result.loc[order].groupby(group_cols, sort=False, dropna=False).indices
    net_values = pd.to_numeric(result[net_et_vol_intermediate_col], errors='coerce').to_numpy()
    eta_values = pd.to_numeric(result[eta_vol_intermediate_col], errors='coerce').to_numpy()
    eff_values = pd.to_numeric(result[irr_eff_col], errors='coerce').to_numpy()
    cap_values = pd.to_numeric(result['Soil_Moisture_Cap_acre_ft'], errors='coerce').to_numpy()

    final_net = np.full(len(result), np.nan, dtype=float)
    final_ar = np.full(len(result), np.nan, dtype=float)
    storage = np.full(len(result), np.nan, dtype=float)
    adjusted_ppt = np.full(len(result), np.nan, dtype=float)
    overflow = np.zeros(len(result), dtype=float)

    for positions in groups.values():
        previous_storage = 0.0
        previous_valid = False
        for position in np.asarray(positions, dtype=int):
            current_net = net_values[position]
            if not np.isfinite(current_net):
                # Match the prior behavior: a missing month breaks the
                # carry-forward chain until the next valid month/reset.
                previous_storage = 0.0
                previous_valid = False
                continue

            current_date = result.at[position, 'date']
            reset = (not previous_valid) or current_date.month == year_reset_month
            raw_storage = current_net if reset else previous_storage + current_net
            cap = cap_values[position]
            lower_bound = -float(cap) if np.isfinite(cap) else -np.inf
            bounded_storage = min(0.0, max(raw_storage, lower_bound))
            final_value = raw_storage if raw_storage >= 0 else 0.0

            final_net[position] = final_value
            storage[position] = bounded_storage
            overflow[position] = (
                max(lower_bound - raw_storage, 0.0)
                if np.isfinite(cap) else 0.0
            )
            adjusted_ppt[position] = eta_values[position] - final_value
            if np.isfinite(eff_values[position]):
                final_ar[position] = (
                    final_value / eff_values[position]
                    if final_value > 0 else 0.0
                )
            else:
                final_ar[position] = np.nan
            previous_storage = bounded_storage
            previous_valid = True

    result['NetET_final_acre_ft'] = final_net
    result['AR_final_acre_ft'] = final_ar
    result['Water_Surplus_Cumulative_acre_ft'] = -storage
    result['Eff_PPT_Adjusted_acre_ft'] = adjusted_ppt
    result['Soil_Moisture_Overflow_acre_ft'] = overflow
    return result

def _normalize_field_key(series):
    """Normalize identifier columns without converting missing values to text."""
    return series.astype('string').str.strip().replace({'': pd.NA})


def _value_conflicts(left, right):
    """Compare extracted and boundary values across numeric/string dtypes."""
    left_num = pd.to_numeric(left, errors='coerce')
    right_num = pd.to_numeric(right, errors='coerce')
    numeric = left_num.notna() & right_num.notna()
    conflict = left.astype(str) != right.astype(str)
    if numeric.any():
        conflict.loc[numeric] = ~np.isclose(
            left_num.loc[numeric], right_num.loc[numeric], equal_nan=True
        )
    return conflict


def merge_field_attributes(df, fieldsdf, report_path=None, strict=True):
    """Conditionally backfill field attributes from the boundary dataset.

    ``NWI_ID`` is preferred because it is the canonical key in the August
    2026 field asset.  ``OPENET_ID`` remains the fallback for legacy exports.
    Existing non-null values are preserved, while null/missing columns are
    filled from the boundary attributes.  Conflicting non-null values and
    duplicate attribute keys are errors: silently choosing one would make a
    bad extraction look valid.

    Parameters
    ----------
    df : pandas.DataFrame
        Field-month rows from a high-volume parquet export or legacy CSV.
    fieldsdf : pandas.DataFrame
        Boundary attributes loaded by :func:`load_field_attributes`.
    report_path : str or pathlib.Path, optional
        Write a compact enrichment report for auditability.
    strict : bool, default=True
        Raise when an input field has no matching boundary attribute key.
    """
    result = df.copy()
    attrs = fieldsdf.copy()

    if 'NWI_ID' in result.columns and 'NWI_ID' in attrs.columns:
        data_key, attr_key = 'NWI_ID', 'NWI_ID'
    elif 'OPENET_ID' in result.columns and 'OPENET_ID' in attrs.columns:
        data_key, attr_key = 'OPENET_ID', 'OPENET_ID'
    else:
        raise ValueError(
            'Cannot join field attributes: need a common NWI_ID or OPENET_ID '
            f'column (data={list(result.columns)}, attrs={list(attrs.columns)})'
        )

    result['_field_join_key'] = _normalize_field_key(result[data_key])
    attrs['_field_join_key'] = _normalize_field_key(attrs[attr_key])

    # Duplicate keys are safe only when every exported attribute agrees.
    attr_columns = [c for c in FIELD_ATTRIBUTE_COLUMNS if c in attrs.columns]
    duplicate_keys = attrs.loc[
        attrs['_field_join_key'].notna()
        & attrs['_field_join_key'].duplicated(keep=False),
        '_field_join_key',
    ].unique()
    duplicate_conflicts = []
    for key in duplicate_keys:
        rows = attrs.loc[attrs['_field_join_key'].eq(key), attr_columns]
        for col in attr_columns:
            if col in (data_key, attr_key):
                continue
            values = rows[col].dropna()
            if len(values) > 1:
                first = values.iloc[0]
                if _value_conflicts(
                    pd.Series([first] * len(values), index=values.index), values
                ).any():
                    duplicate_conflicts.append((key, col))
    if duplicate_conflicts:
        raise ValueError(
            'Boundary attribute table has conflicting duplicate join keys: '
            f'{duplicate_conflicts[:10]}'
        )
    attrs = attrs.drop_duplicates('_field_join_key', keep='first')
    lookup = attrs.set_index('_field_join_key')

    input_keys = result['_field_join_key'].dropna().unique()
    matched_keys = set(input_keys).intersection(lookup.index)
    unmatched_keys = sorted(set(input_keys) - set(lookup.index))
    report = [
        {'metric': 'join_key_data', 'value': data_key},
        {'metric': 'join_key_attributes', 'value': attr_key},
        {'metric': 'input_unique_fields', 'value': len(input_keys)},
        {'metric': 'matched_unique_fields', 'value': len(matched_keys)},
        {'metric': 'unmatched_unique_fields', 'value': len(unmatched_keys)},
        {'metric': 'duplicate_attribute_keys', 'value': len(duplicate_keys)},
    ]
    if unmatched_keys and strict:
        if report_path is not None:
            pd.DataFrame(report).to_csv(report_path, index=False)
        raise ValueError(
            f'{len(unmatched_keys)} input fields do not match the supplied '
            f'boundary attributes by {data_key}; first keys: {unmatched_keys[:10]}'
        )

    for col in attr_columns:
        if col in (data_key, attr_key):
            continue
        incoming = result['_field_join_key'].map(lookup[col])
        if col in result.columns:
            both_present = result[col].notna() & incoming.notna()
            conflicts = both_present & _value_conflicts(result[col], incoming)
            conflict_count = int(conflicts.sum())
            if conflict_count:
                raise ValueError(
                    f'{conflict_count} non-null {col} values conflict with '
                    f'the supplied boundary attributes; refusing silent overwrite'
                )
            fill_mask = result[col].isna() & incoming.notna()
            result.loc[fill_mask, col] = incoming.loc[fill_mask]
            filled_count = int(fill_mask.sum())
        else:
            result[col] = incoming
            filled_count = int(incoming.notna().sum())
        report.append({'metric': f'filled_{col}', 'value': filled_count})

    result = result.drop(columns=['_field_join_key'])
    if report_path is not None:
        pd.DataFrame(report).to_csv(report_path, index=False)
    return result


def enforce_boundary_basin_mapping(df, fieldsdf, strict=True):
    """Use the legacy boundary ``Basin`` attribute as the HA authority.

    The legacy extractor carries HYD_AREA columns, but the official field
    processing contract is the Nevada boundary layer's ``Basin`` attribute.
    This helper makes that authority explicit and prevents a newer or
    cross-state basin assignment from silently entering aggregation.
    """
    result = df.copy()
    key = 'OPENET_ID' if 'OPENET_ID' in result.columns and 'OPENET_ID' in fieldsdf.columns else 'NWI_ID'
    required = {key, 'HYD_AREA_new', 'HYD_AREA_N_new'}
    missing = required.difference(fieldsdf.columns)
    if missing:
        raise ValueError(f'Boundary basin mapping missing columns: {sorted(missing)}')

    attrs = fieldsdf[[key, 'HYD_AREA_new', 'HYD_AREA_N_new']].copy()
    attrs['_field_join_key'] = _normalize_field_key(attrs[key])
    result['_field_join_key'] = _normalize_field_key(result[key])
    attrs = attrs.drop_duplicates('_field_join_key', keep='first').set_index('_field_join_key')
    unmatched = result.loc[~result['_field_join_key'].isin(attrs.index), key].dropna().unique()
    if len(unmatched) and strict:
        raise ValueError(
            f'{len(unmatched)} input fields do not match the legacy boundary Basin mapping; '
            f'first IDs: {list(unmatched[:10])}'
        )

    result['HYD_AREA'] = result['_field_join_key'].map(attrs['HYD_AREA_new'])
    result['HYD_AREA_N'] = result['_field_join_key'].map(attrs['HYD_AREA_N_new'])
    result['HA_NAME_CODE'] = (
        result['HYD_AREA_N'].astype('string') + ' (' +
        result['HYD_AREA'].astype('string') + ')'
    )
    return result.drop(columns=['_field_join_key'])


def apply_irrigation_attribute_fallbacks(
    df,
    report_path=None,
    assumed_irr_src=1,
    assumed_irr_eff=0.75,
):
    """Fill explicitly permitted missing irrigation attributes and flag them.

    This is opt-in because source-type assumptions are appropriate for the
    August NWI field reprocessing, but must not alter county/state projection
    inputs.  The flags are retained on every output row for any field that was
    filled, and the enrichment report records both row and field counts.
    """
    result = df.copy()
    if 'IRR_SRC' not in result.columns:
        result['IRR_SRC'] = np.nan
    if 'irr_eff' not in result.columns:
        result['irr_eff'] = np.nan

    source_missing = result['IRR_SRC'].isna()
    efficiency_values = pd.to_numeric(result['irr_eff'], errors='coerce')
    efficiency_missing = result['irr_eff'].isna() | (efficiency_values <= 0)

    result['IRR_SRC_ASSUMED'] = source_missing
    result['irr_eff_ASSUMED'] = efficiency_missing
    result['IRR_ATTR_ASSUMED'] = source_missing | efficiency_missing
    result['IRR_ATTR_ASSUMPTION'] = ''
    result.loc[source_missing, 'IRR_ATTR_ASSUMPTION'] = 'IRR_SRC=GW'
    result.loc[efficiency_missing, 'IRR_ATTR_ASSUMPTION'] = np.where(
        result.loc[efficiency_missing, 'IRR_ATTR_ASSUMPTION'].eq(''),
        'irr_eff=0.75',
        result.loc[efficiency_missing, 'IRR_ATTR_ASSUMPTION'] + ';irr_eff=0.75',
    )

    result.loc[source_missing, 'IRR_SRC'] = assumed_irr_src
    result.loc[efficiency_missing, 'irr_eff'] = assumed_irr_eff

    join_key = 'NWI_ID' if 'NWI_ID' in result.columns else 'OPENET_ID'
    source_fields = result.loc[source_missing, join_key].nunique(dropna=True)
    efficiency_fields = result.loc[efficiency_missing, join_key].nunique(dropna=True)
    metrics = [
        {'metric': 'fallback_enabled', 'value': True},
        {'metric': 'fallback_irr_src_value', 'value': 'GW (1)'},
        {'metric': 'fallback_irr_eff_value', 'value': assumed_irr_eff},
        {'metric': 'fallback_irr_src_rows', 'value': int(source_missing.sum())},
        {'metric': 'fallback_irr_src_unique_fields', 'value': int(source_fields)},
        {'metric': 'fallback_irr_eff_rows', 'value': int(efficiency_missing.sum())},
        {'metric': 'fallback_irr_eff_unique_fields', 'value': int(efficiency_fields)},
    ]
    if source_missing.any() or efficiency_missing.any():
        print(
            f"WARNING: applied temporary irrigation-attribute assumptions to "
            f"{int((source_missing | efficiency_missing).sum()):,} rows "
            f"({int(result.loc[source_missing | efficiency_missing, join_key].nunique()):,} fields); "
            "see IRR_ATTR_ASSUMPTION and field_attribute_enrichment_report.csv"
        )

    if report_path is not None:
        report = pd.DataFrame(metrics)
        if os.path.exists(report_path):
            report = pd.concat([pd.read_csv(report_path), report], ignore_index=True)
        report.to_csv(report_path, index=False)
    return result


def get_basin_year_spi(
    spi_data,
    basin_code,
    year,
    spi_timescale,
    reference_month=9,
):
    """
    Get SPI value for a specific basin/year/timescale.
    
    Parameters
    ----------
    spi_data : pd.DataFrame
        SPI data with columns: basin_name, YEAR, MONTH, SP_6m, SP_12m, SP_24m, etc.
    basin_code : str
        Basin identifier (e.g., 'Lovelock Valley (073)')
    year : int
        Year to get SPI for
    spi_timescale : int
        SPI timescale (6, 12, 24, 36, 48, 60)
    
    Returns
    -------
    float
        SPI value at end of water year (September), or np.nan if not found
    """
    spi_col = f'SPI_{spi_timescale}m'
    
    # The legacy field boundary and SPI tables use the same HA code but may
    # differ in the rendered name (``FishLakeValley`` vs ``Fish Lake Valley``).
    # Match by the terminal parenthetical HA code when present so the shared
    # allocation rule cannot silently fall back to its center value.
    import re

    def _spi_basin_key(value):
        text = str(value).strip()
        match = re.search(r'\(([^)]+)\)\s*$', text)
        return match.group(1).strip().upper() if match else re.sub(r'\s+', ' ', text).upper()

    basin_key = _spi_basin_key(basin_code)
    spi_basin_keys = spi_data['HA_NAME_CODE'].map(_spi_basin_key)
    basin_spi = spi_data[
        (spi_basin_keys == basin_key) &
        (spi_data['YEAR'] == year) &
        (spi_data['MONTH'] == reference_month)
    ]
    
    if len(basin_spi) > 0 and spi_col in basin_spi.columns:
        return basin_spi[spi_col].iloc[0]
    else:
        return np.nan


def apply_gw_sw_separation_dynamic(
    df, 
    fieldsdf, 
    basin_config_dict,
    spi_data,
    basin_column='HA_NAME_CODE',
    min_sw_ratio=0.2,
    max_sw_ratio=0.8,
    use_uniform_mean_ratio=True,
    allocation_overrides=None,
    spi_reference_month=9,
):
    """
    Apply GW/SW separation with DYNAMIC year-by-year allocation based on SPI.
    
    CRITICAL DIFFERENCE from static allocation:
    - Static: Same ratio for all years (e.g., always 70% to SW)
    - Dynamic: Ratio varies by year based on actual drought conditions
    
    Physical Basis:
    - Dry years → Lower SPI → Less SW available → Higher GW reliance
    - Wet years → Higher SPI → More SW available → Higher SW use
    - Normal years → SPI ≈ 0 → Balanced 50/50 split
    
    Parameters
    ----------
    df : pd.DataFrame
        Input data with YEAR column
    fieldsdf : pd.DataFrame
        Field attributes with IRR_SRC
    basin_config_dict : dict
        Basin configs with 'spi_timescale', 'allocation_method'
    spi_data : pd.DataFrame
        SPI data with basin/year/timescale values
    basin_column : str, default='HA_NAME_CODE'
        Column name for basin identifier
    min_sw_ratio : float, default=0.2
        Minimum SW allocation in driest years (20%)
    max_sw_ratio : float, default=0.8
        Maximum SW allocation in wettest years (80%)
    use_uniform_mean_ratio : bool, default=True
        If True, use 0.5 as fallback mean ratio for all basins (conservative).
        If False, use basin-specific mean ratios from optimization (clipped to range).
        
        Rationale: Using 0.5 (50/50) is more defensible when correlations are weak
        or when justifying allocation decisions. The dynamic range (0.2-0.8) still
        varies with drought conditions, but the average is consistent across basins.
    
    Returns
    -------
    tuple
        (df_gw_combined, df_sw_combined, df_mixed, allocation_stats_df)
    """
    print(f"\n{'='*80}")
    print("DYNAMIC GW/SW SEPARATION (Year-by-Year Allocation Based on SPI)")
    print(f"{'='*80}")
    print(f"Processing {df[basin_column].nunique()} basins with dynamic allocation")
    print(f"SW ratio range: {min_sw_ratio*100:.0f}% (dry years) to {max_sw_ratio*100:.0f}% (wet years)")
    print(f"Mean ratio policy: {'UNIFORM (0.5 for all basins)' if use_uniform_mean_ratio else 'BASIN-SPECIFIC (from optimization)'}")
    
    # Join with field attributes
    df_with_attrs = merge_field_attributes(df, fieldsdf)
    df_filtered = df_with_attrs[df_with_attrs['IRR_SRC'] != 0].copy()
    
    # Apply basin-specific IRR_SRC overrides before the split
    if allocation_overrides:
        from allocation_utils import apply_irr_src_overrides
        print(f"\n  Applying {len(allocation_overrides)} basin allocation override(s)...")
        df_filtered = apply_irr_src_overrides(
            df_filtered, allocation_overrides, basin_column=basin_column
        )
    
    # Separate by source type
    df_gw_pure = df_filtered[df_filtered['IRR_SRC'] == 1].copy()
    df_sw_pure = df_filtered[df_filtered['IRR_SRC'] == 2].copy()
    df_mixed = df_filtered[df_filtered['IRR_SRC'] == 3].copy()
    
    print(f"\nField type distribution:")
    print(f"  Pure GW: {df_gw_pure['OPENET_ID'].nunique()} fields")
    print(f"  Pure SW: {df_sw_pure['OPENET_ID'].nunique()} fields")
    print(f"  Mixed: {df_mixed['OPENET_ID'].nunique()} fields")
    
    # Process mixed fields BASIN-BY-BASIN, YEAR-BY-YEAR
    mixed_gw_parts = []
    mixed_sw_parts = []
    allocation_stats = []
    
    basins_processed = 0
    basins_using_optimized = 0
    total_allocations = 0
    
    def _basin_code(value):
        import re
        match = re.search(r'\(([^)]+)\)\s*$', str(value))
        return match.group(1) if match else str(value)

    for basin_code in df_mixed[basin_column].unique():
        # Get basin configuration
        config = basin_config_dict.get(basin_code) or basin_config_dict.get(_basin_code(basin_code))
        if config:
            config = dict(config)
            spi_timescale = int(config.get('spi_timescale', 12))
            allocation_method = config.get('allocation_method', 'default_fallback')
            min_ratio = float(config.get('min_sw_ratio', min_sw_ratio))
            max_ratio = float(config.get('max_sw_ratio', max_sw_ratio))
            center_ratio = config.get('center_sw_ratio', config.get('sw_ratio_center'))
            center_ratio = float(center_ratio) if center_ratio is not None else None
            fallback_ratio = float(config.get(
                'fallback_ratio',
                center_ratio if center_ratio is not None else 0.5,
            ))
            reference_month = int(config.get('spi_reference_month', spi_reference_month))
            mean_ratio_raw = float(config.get(
                'mixed_to_sw_ratio',
                center_ratio if center_ratio is not None else 0.5,
            ))

            # Apply mean ratio policy
            if center_ratio is not None:
                # Explicit center anchors (e.g. Rush & Katzer's 36%) take
                # precedence over the legacy uniform 50% policy.
                mean_ratio = center_ratio
            elif use_uniform_mean_ratio:
                # UNIFORM POLICY: Use 0.5 for all basins (more defensible)
                mean_ratio = 0.5
            else:
                # BASIN-SPECIFIC POLICY: Use optimization-derived mean (clipped to range)
                if allocation_method in ['optimized', 'conservative']:
                    mean_ratio = np.clip(mean_ratio_raw, min_sw_ratio, max_sw_ratio)
                else:
                    mean_ratio = np.clip(mean_ratio_raw, 0.2, 0.8)  # Narrower range for default
            
            if allocation_method == 'optimized':
                basins_using_optimized += 1
        else:
            # Default for missing basins
            spi_timescale = 12
            allocation_method = 'default_fallback'
            mean_ratio = 0.5
            mean_ratio_raw = 0.5
            min_ratio = min_sw_ratio
            max_ratio = max_sw_ratio
            center_ratio = None
            fallback_ratio = 0.5
            reference_month = spi_reference_month
            print(f"  ⚠️  Basin {basin_code} not in config, using default (12m SPI, dynamic)")
        
        basin_mixed = df_mixed[df_mixed[basin_column] == basin_code].copy()
        
        if len(basin_mixed) == 0:
            continue
        
        basins_processed += 1
        
        # Process EACH YEAR separately with year-specific SPI
        years = sorted(basin_mixed['YEAR'].unique())
        print(f"\n  [{basins_processed}] {basin_code}")
        print(f"      SPI timescale: {spi_timescale}m | Method: {allocation_method} | Years: {len(years)}")
        if use_uniform_mean_ratio and center_ratio is None:
            print(f"      Mean ratio: 0.5 (uniform policy) | Original from optimization: {mean_ratio_raw:.2f}")
        elif center_ratio is not None:
            print(f"      Mean ratio: {mean_ratio:.2f} (explicit center anchor) | Original from optimization: {mean_ratio_raw:.2f}")
        else:
            print(f"      Mean ratio: {mean_ratio:.2f} (basin-specific from optimization)")
        
        year_ratios = []
        
        for year in years:
            # Get SPI for this specific basin/year/timescale
            spi_value = get_basin_year_spi(
                spi_data, basin_code, year, spi_timescale,
                reference_month=reference_month,
            )

            if np.isnan(spi_value):
                # Fallback to mean ratio if SPI not available
                ratio = fallback_ratio
                spi_status = 'missing'
            else:
                ratio = spi_to_sw_ratio(
                    spi_value,
                    min_ratio=min_ratio,
                    max_ratio=max_ratio,
                    center_ratio=center_ratio,
                )
                spi_status = f'SPI={spi_value:+.2f}'
            
            year_ratios.append(ratio)
            
            # Filter to this year
            year_mixed = basin_mixed[basin_mixed['YEAR'] == year].copy()
            
            if len(year_mixed) == 0:
                continue
            
            # Create SW and GW portions for THIS YEAR with YEAR-SPECIFIC ratio
            year_mixed_sw = year_mixed.copy()
            year_mixed_gw = year_mixed.copy()
            
            # Apply YEAR-SPECIFIC ratio (THIS IS THE KEY DIFFERENCE!)
            year_mixed_sw['ACRES_FTR_GEOM'] = year_mixed_sw['ACRES_FTR_GEOM'] * ratio
            year_mixed_gw['ACRES_FTR_GEOM'] = year_mixed_gw['ACRES_FTR_GEOM'] * (1 - ratio)
            
            # Add tracking metadata
            year_mixed_sw['water_source'] = 'SW_from_mixed'
            year_mixed_gw['water_source'] = 'GW_from_mixed'
            year_mixed_sw['allocation_method'] = allocation_method
            year_mixed_gw['allocation_method'] = allocation_method
            year_mixed_sw['allocation_ratio'] = ratio
            year_mixed_gw['allocation_ratio'] = 1 - ratio
            year_mixed_sw['spi_value'] = spi_value
            year_mixed_gw['spi_value'] = spi_value
            year_mixed_sw['spi_timescale'] = spi_timescale
            year_mixed_gw['spi_timescale'] = spi_timescale
            
            mixed_sw_parts.append(year_mixed_sw)
            mixed_gw_parts.append(year_mixed_gw)
            
            total_allocations += 1
            
            # Track statistics
            allocation_stats.append({
                'basin_code': basin_code,
                'year': year,
                'allocation_method': allocation_method,
                'spi_timescale': spi_timescale,
                'spi_reference_month': reference_month,
                'spi_value': spi_value,
                'sw_ratio': ratio,
                'spi_status': spi_status,
                'n_mixed_fields': year_mixed['OPENET_ID'].nunique(),
                'total_mixed_acres': year_mixed['ACRES_FTR_GEOM'].sum(),
                'acres_to_sw': year_mixed_sw['ACRES_FTR_GEOM'].sum(),
                'acres_to_gw': year_mixed_gw['ACRES_FTR_GEOM'].sum()
            })
        
        # Basin-year summary
        if len(year_ratios) > 0:
            print(f"      SW ratio range: {min(year_ratios):.2f} to {max(year_ratios):.2f} "
                  f"(mean={np.mean(year_ratios):.2f})")
    
    print(f"\n{'='*80}")
    print(f"Dynamic allocation summary:")
    print(f"  Total basins processed: {basins_processed}")
    print(f"  Using optimized method: {basins_using_optimized}")
    print(f"  Total year-specific allocations: {total_allocations}")
    print(f"{'='*80}")
    
    # Combine all years/basins
    if mixed_gw_parts:
        df_mixed_gw_all = pd.concat(mixed_gw_parts, ignore_index=True)
    else:
        df_mixed_gw_all = pd.DataFrame()
    
    if mixed_sw_parts:
        df_mixed_sw_all = pd.concat(mixed_sw_parts, ignore_index=True)
    else:
        df_mixed_sw_all = pd.DataFrame()
    
    # Add tracking to pure fields
    df_gw_pure['water_source'] = 'GW'
    df_sw_pure['water_source'] = 'SW'
    df_gw_pure['allocation_method'] = 'pure_gw'
    df_sw_pure['allocation_method'] = 'pure_sw'
    df_gw_pure['allocation_ratio'] = 1.0
    df_sw_pure['allocation_ratio'] = 1.0
    df_gw_pure['spi_value'] = np.nan
    df_sw_pure['spi_value'] = np.nan
    df_gw_pure['spi_timescale'] = np.nan
    df_sw_pure['spi_timescale'] = np.nan
    
    # Final combination
    df_gw_combined = pd.concat([df_gw_pure, df_mixed_gw_all], ignore_index=True)
    df_sw_combined = pd.concat([df_sw_pure, df_mixed_sw_all], ignore_index=True)
    
    allocation_stats_df = pd.DataFrame(allocation_stats)
    
    print(f"\nFinal dataset sizes:")
    print(f"  GW: {len(df_gw_combined)} records, {df_gw_combined['OPENET_ID'].nunique()} fields")
    print(f"  SW: {len(df_sw_combined)} records, {df_sw_combined['OPENET_ID'].nunique()} fields")
    print(f"  Dynamic allocations tracked: {len(allocation_stats_df)} basin-years")
    
    return df_gw_combined, df_sw_combined, df_mixed, allocation_stats_df


def load_field_attributes(fields_shapefile_path):
    """
    Load field shapefile and extract IRR_SRC, irr_eff, and basin attributes
    """
    print(f"Loading field attributes from: {fields_shapefile_path}")
    
    # Read shapefile/geopackage. The newer NWI source uses NWI_ID as the
    # canonical identifier; legacy files may only provide OPENET_ID.
    fieldsdf = gpd.read_file(fields_shapefile_path)

    if 'NWI_ID' not in fieldsdf.columns and 'OPENET_ID' not in fieldsdf.columns:
        raise ValueError(
            f"{fields_shapefile_path} has neither OPENET_ID nor NWI_ID"
        )
    # The August asset calls this field NWI_ID_PARENT; normalize it to the
    # extractor's NWI_PARENT name while retaining the canonical NWI_ID key.
    if 'NWI_PARENT' not in fieldsdf.columns and 'NWI_ID_PARENT' in fieldsdf.columns:
        fieldsdf = fieldsdf.rename(columns={'NWI_ID_PARENT': 'NWI_PARENT'})

    # Keep all known source properties so the fallback join protects future
    # processing changes, not only the current IRR_SRC/irr_eff requirements.
    for col, default in [('IRR_SRC', np.nan), ('irr_eff', np.nan), ('Basin', None)]:
        if col not in fieldsdf.columns:
            fieldsdf[col] = default
    keep_cols = [c for c in FIELD_ATTRIBUTE_COLUMNS if c in fieldsdf.columns]
    fieldsdf = fieldsdf[keep_cols].copy()
    
    # Parse BASIN column to extract HYD_AREA and HYD_AREA_N
    fieldsdf[['HYD_AREA_new', 'HYD_AREA_N_new']] = fieldsdf['Basin'].astype(
        'string'
    ).str.extract(r'^([^_]+)_(.*)$')
    
    # Clean up the values
    fieldsdf['HYD_AREA_new'] = fieldsdf['HYD_AREA_new'].str.strip()
    fieldsdf['HYD_AREA_N_new'] = fieldsdf['HYD_AREA_N_new'].str.strip()
    
    print(f"Loaded attributes for {len(fieldsdf)} fields")
    print(f"IRR_SRC distribution (nulls retained for fallback enrichment): "
          f"{fieldsdf['IRR_SRC'].value_counts(dropna=False).to_dict()}")
    
    return fieldsdf

# ============================================================================
# NWI T1: inputs merged into the field-month table
# ============================================================================

def merge_prism_precipitation(df, prism_monthly_path):
    """
    NWI T1: replace the export precipitation with PRISM 800 m monthly precipitation.

    Parameters
    ----------
    df : pd.DataFrame
        Field-month rows with NWI_ID, YEAR, MONTH.
    prism_monthly_path : str or Path
        Output of aggregate_field_prism_monthly.py (NWI_ID, YEAR, MONTH, PPT_PRISM_MM).

    Returns
    -------
    pd.DataFrame
        ``df`` with PPT_MM set to the PRISM value.
    """
    prism = pd.read_parquet(prism_monthly_path, columns=['NWI_ID', 'YEAR', 'MONTH', 'PPT_PRISM_MM'])
    prism['YEAR'] = prism['YEAR'].astype(int)
    prism['MONTH'] = prism['MONTH'].astype(int)
    df = df.merge(prism, on=['NWI_ID', 'YEAR', 'MONTH'], how='left', validate='one_to_one')
    missing = df['PPT_PRISM_MM'].isna().sum()
    if missing:
        raise ValueError(f'{missing} field-months have no PRISM precipitation')
    df['PPT_MM'] = df['PPT_PRISM_MM']
    return df.drop(columns=['PPT_PRISM_MM'])


def merge_otter_effective_precipitation(df, otter_monthly_path, fallback_fraction=0.85):
    """
    NWI T1: set EFF_PPT_MM to monthly OTTER effective precipitation.

    Fields without valid OTTER static soils have no OTTER output; they keep
    ``fallback_fraction`` x PPT_MM and are flagged in ``peff_method``.

    Parameters
    ----------
    df : pd.DataFrame
        Field-month rows with NWI_ID, YEAR, MONTH, PPT_MM.
    otter_monthly_path : str or Path
        Output of run_nv_otter_prism_monthly.py (NWI_ID, YEAR, MONTH, OTTER_PEFF_MM).
    fallback_fraction : float
        Effective fraction of precipitation for fields without OTTER output.

    Returns
    -------
    pd.DataFrame
        ``df`` with EFF_PPT_MM and peff_method ('otter' or 'ppt_085_fallback').
    """
    otter = pd.read_parquet(otter_monthly_path, columns=['NWI_ID', 'YEAR', 'MONTH', 'OTTER_PEFF_MM'])
    otter['YEAR'] = otter['YEAR'].astype(int)
    otter['MONTH'] = otter['MONTH'].astype(int)
    df = df.merge(otter, on=['NWI_ID', 'YEAR', 'MONTH'], how='left', validate='one_to_one')
    has_otter = df['OTTER_PEFF_MM'].notna()
    # OTTER can return -1e-4 mm from float rounding; effective precipitation is never negative.
    df['EFF_PPT_MM'] = np.where(has_otter, df['OTTER_PEFF_MM'].clip(lower=0), fallback_fraction * df['PPT_MM'])
    df['peff_method'] = np.where(has_otter, 'otter', 'ppt_085_fallback')
    print(f"OTTER effective precipitation for {df.loc[has_otter, 'NWI_ID'].nunique():,} fields; "
          f"{df.loc[~has_otter, 'NWI_ID'].nunique():,} fields use {fallback_fraction} x PPT")
    return df.drop(columns=['OTTER_PEFF_MM'])


def merge_county_assignment(df, county_assignment_path):
    """
    NWI T1: Nevada county of each field from the county assignment table.

    Parameters
    ----------
    df : pd.DataFrame
        Field-month rows with NWI_ID.
    county_assignment_path : str or Path
        CSV with NWI_ID, COUNTY_NAME, include_in_county_run (Nevada fields).

    Returns
    -------
    pd.DataFrame
        ``df`` with COUNTY_NAME (blank outside Nevada) and IN_NV_COUNTY.
    """
    county = pd.read_csv(county_assignment_path, usecols=['NWI_ID', 'COUNTY_NAME', 'include_in_county_run'])
    county = county.rename(columns={'include_in_county_run': 'IN_NV_COUNTY'})
    county['NWI_ID'] = county['NWI_ID'].astype(str)
    return df.merge(county, on='NWI_ID', how='left', validate='many_to_one')


def apply_static_basin_overrides(fieldsdf, static_overrides):
    """
    NWI T1: reclassify mixed fields in basins with a fixed allocation (e.g. Diamond Valley 153 -> GW).

    The original applied these inside apply_gw_sw_separation_dynamic, after the attributes were
    merged, so the later per-dataset attribute merge saw a conflict. Applying them to the boundary
    attributes once keeps every merge consistent. Uses allocation_utils.apply_irr_src_overrides.

    Parameters
    ----------
    fieldsdf : pd.DataFrame
        Output of load_field_attributes.
    static_overrides : dict
        {basin code: GW share}; 1.0 makes mixed fields GW, 0.0 makes them SW.

    Returns
    -------
    pd.DataFrame
        ``fieldsdf`` with IRR_SRC reclassified, IRR_SRC_ORIGINAL (before the rule) and
        IRR_SRC_OVERRIDE (the rule applied, or '').
    """
    from allocation_utils import apply_irr_src_overrides
    fieldsdf = fieldsdf.copy()
    fieldsdf['IRR_SRC_ORIGINAL'] = fieldsdf['IRR_SRC']
    fieldsdf['_BASIN_CODE'] = fieldsdf['HYD_AREA_N_new'].astype(str) + ' (' + fieldsdf['HYD_AREA_new'].astype(str) + ')'
    fieldsdf = apply_irr_src_overrides(fieldsdf, static_overrides or {}, basin_column='_BASIN_CODE')
    changed = fieldsdf['IRR_SRC'].ne(fieldsdf['IRR_SRC_ORIGINAL']) & fieldsdf['IRR_SRC_ORIGINAL'].notna()
    fieldsdf['IRR_SRC_OVERRIDE'] = np.where(changed, 'basin rule: mixed -> ' + fieldsdf['IRR_SRC'].map({1: 'GW', 2: 'SW'}).fillna(''), '')
    return fieldsdf.drop(columns=['_BASIN_CODE'])


def add_gw_subsidy(df, gw_frac_by_field, irrigated_only=False, irrigated_min_fraction=0.5):
    """
    NWI T1: subtract the groundwater-ET subsidy from the intermediate net ET.

    The subsidy is f x ETa (f = the field's Tan et al. 2025 groundwater fraction), limited to that
    month's positive net ET so groundwater never becomes stored soil water:

        GW_subsidy = min(f x ETa_vol_intermediate, max(NetET_vol_intermediate, 0))
        NetET_vol_intermediate = NetET_vol_intermediate - GW_subsidy

    Parameters
    ----------
    df : pd.DataFrame
        Field-month rows after Step 4 (ETa_vol_intermediate_acre_ft, NetET_vol_intermediate_acre_ft).
    gw_frac_by_field : pd.Series or None
        f indexed by OPENET_ID. None means no subsidy.
    irrigated_only : bool
        If True, apply the subsidy only where %_IRRIGATED > ``irrigated_min_fraction``.
    irrigated_min_fraction : float
        Irrigated fraction above which a field-year counts as irrigated.

    Returns
    -------
    pd.DataFrame
        ``df`` with gw_frac, NetET_pre_subsidy_acre_ft, GW_subsidy_potential_acre_ft,
        GW_subsidy_acre_ft, and NetET_vol_intermediate_acre_ft after the subsidy.
    """
    df['NetET_pre_subsidy_acre_ft'] = df['NetET_vol_intermediate_acre_ft']
    if gw_frac_by_field is None:
        df['gw_frac'] = 0.0
        df['GW_subsidy_potential_acre_ft'] = 0.0
        df['GW_subsidy_acre_ft'] = 0.0
        return df
    df['gw_frac'] = df['OPENET_ID'].map(gw_frac_by_field).fillna(0.0)
    df['GW_subsidy_potential_acre_ft'] = df['gw_frac'] * df['ETa_vol_intermediate_acre_ft']
    usable = df['GW_subsidy_potential_acre_ft']
    if irrigated_only:
        usable = usable.where(df['%_IRRIGATED'] > irrigated_min_fraction, 0.0)
    df['GW_subsidy_acre_ft'] = np.minimum(usable, df['NetET_vol_intermediate_acre_ft'].clip(lower=0))
    df['NetET_vol_intermediate_acre_ft'] = df['NetET_vol_intermediate_acre_ft'] - df['GW_subsidy_acre_ft']
    return df


# NWI T1: columns written to the field files, and their names in those files
FIELD_OUTPUT_COLUMNS = {
    'NWI_ID': 'NWI_ID', 'NWI_PARENT': 'NWI_PARENT', 'STATE': 'STATE', 'COUNTY_NAME': 'COUNTY_NAME',
    'HYD_AREA': 'HYD_AREA', 'HYD_AREA_N': 'HYD_AREA_N', 'HA_NAME_CODE': 'HA_NAME_CODE', 'GRIDMET_ID': 'GRIDMET_ID',
    'IRR_SRC': 'IRR_SRC', 'IRR_SRC_ORIGINAL': 'IRR_SRC_ORIGINAL', 'IRR_SRC_ASSUMED': 'IRR_SRC_ASSUMED',
    'IRR_SRC_OVERRIDE': 'IRR_SRC_OVERRIDE', 'irr_eff': 'irr_eff', 'ITYPE': 'ITYPE', 'CROP_major': 'CROP_major',
    'YEAR': 'YEAR', 'MONTH': 'MONTH', 'DATE': 'DATE',
    'water_source': 'water_source', 'allocation_method': 'allocation_method', 'allocation_ratio': 'allocation_ratio',
    'spi_value': 'spi_value', 'spi_source': 'spi_source',
    'ACRES_FTR_GEOM': 'ACRES_FTR_GEOM', 'ACRES_IRR': 'ACRES_IRR', '%_IRRIGATED': '%_IRRIGATED',
    'ETO_MM': 'ETO_MM', 'ETA_MM_EXPORT': 'ETA_MM_EXPORT', 'EToF': 'EToF', 'EToF_interpolated': 'EToF_interpolated',
    'interpolation_method': 'interpolation_method', 'ETA_MM_post_interp': 'ETA_MM_post_interp',
    'PPT_MM': 'PPT_MM', 'EFF_PPT_MM': 'PEFF_MM', 'peff_method': 'peff_method',
    'gw_frac': 'gw_frac', 'AWC': 'AWC', 'PAW_ft': 'PAW_ft',
    'ETa_final_acre_ft': 'ETa_final_acre_ft', 'PPT_vol_acre_ft': 'PPT_vol_acre_ft',
    'Eff_PPT_vol_acre_ft': 'Peff_vol_acre_ft', 'NetET_pre_subsidy_acre_ft': 'NetET_pre_subsidy_acre_ft',
    'GW_subsidy_potential_acre_ft': 'GW_subsidy_potential_acre_ft', 'GW_subsidy_acre_ft': 'GW_subsidy_acre_ft',
    'NetET_vol_intermediate_acre_ft': 'NetET_intermediate_acre_ft',
    'Soil_Moisture_Cap_acre_ft': 'Soil_Moisture_Cap_acre_ft', 'Water_Surplus_Cumulative_acre_ft': 'Soil_Storage_acre_ft',
    'Soil_Moisture_Overflow_acre_ft': 'Soil_Moisture_Overflow_acre_ft',
    'Carryforward_Used_acre_ft': 'Carryforward_Used_acre_ft',
    'NetET_final_acre_ft': 'NetET_final_acre_ft', 'AR_final_acre_ft': 'AR_final_acre_ft',
    'ETa_final_ft': 'ETa_final_ft', 'NetET_final_ft': 'NetET_final_ft', 'AR_final_ft': 'AR_final_ft',
    'PPT_final_ft': 'PPT_final_ft',
}

# NWI T1: summary columns (names as in the field files)
SUMMARY_VOLUME_COLUMNS = [
    'ACRES_FTR_GEOM', 'ACRES_IRR', 'ETa_final_acre_ft', 'PPT_vol_acre_ft', 'Peff_vol_acre_ft',
    'NetET_pre_subsidy_acre_ft', 'GW_subsidy_potential_acre_ft', 'GW_subsidy_acre_ft', 'NetET_intermediate_acre_ft',
    'Soil_Moisture_Cap_acre_ft', 'Soil_Storage_acre_ft', 'Soil_Moisture_Overflow_acre_ft',
    'Carryforward_Used_acre_ft', 'NetET_final_acre_ft', 'AR_final_acre_ft',
]
SUMMARY_WEIGHTED_MM_COLUMNS = ['ETO_MM', 'PPT_MM', 'PEFF_MM']
SUMMARY_STATE_SPLIT_COLUMNS = [
    'ACRES_FTR_GEOM', 'ETa_final_acre_ft', 'PPT_vol_acre_ft', 'Peff_vol_acre_ft', 'GW_subsidy_acre_ft',
    'Carryforward_Used_acre_ft', 'NetET_final_acre_ft', 'AR_final_acre_ft', 'n_fields',
]


def aggregate_summaries(field_rows, keys, basin_year=None, state_split=False):
    """
    NWI T1: sum field-month rows to HA or county summaries.

    Replaces the original Step 7 aggregation, which summed every numeric column and took
    ``first()`` of every text column. Each output column here has one rule:

    * additive volumes and acres are summed;
    * counts of fields, by source and by data flag;
    * depths (ft) and ratios are recomputed from the summed volumes;
    * ETo, PPT and Peff in mm are acre-weighted means;
    * basin-year allocation constants are joined (HA summaries only).

    Parameters
    ----------
    field_rows : pd.DataFrame
        Field-month rows with the field-file column names (FIELD_OUTPUT_COLUMNS values),
        plus sw_share (SW share of the field that year) for the field-share percentages.
    keys : list of str
        Grouping columns, e.g. HA_NAME_CODE, HYD_AREA, HYD_AREA_N, YEAR, MONTH.
    basin_year : pd.DataFrame, optional
        Allocation constants per HYD_AREA and YEAR to join.
    state_split : bool
        Also report SUMMARY_STATE_SPLIT_COLUMNS for Nevada fields (``_NV``) and fields
        outside Nevada (``_outside_NV``).

    Returns
    -------
    pd.DataFrame
        One row per key combination.
    """
    acres = field_rows['ACRES_FTR_GEOM']
    table = pd.DataFrame({key: field_rows[key] for key in keys})
    for col in SUMMARY_VOLUME_COLUMNS:
        table[col] = field_rows[col]
    for col in SUMMARY_WEIGHTED_MM_COLUMNS:
        table[f'_{col}_x_acres'] = field_rows[col] * acres
        table[f'_{col}_acres'] = acres.where(field_rows[col].notna(), 0)

    source = field_rows['IRR_SRC'].astype(float)
    table['n_fields'] = 1
    table['n_gw_fields'] = (source == 1).astype(int)
    table['n_sw_fields'] = (source == 2).astype(int)
    table['n_mixed_fields'] = (source == 3).astype(int)
    table['n_fields_irr_src_assumed'] = field_rows['IRR_SRC_ASSUMED'].astype(bool).astype(int)
    table['n_fields_eta_interpolated'] = (field_rows['interpolation_method'] == 'linear_1month').astype(int)
    table['n_fields_eta_climatology'] = (field_rows['interpolation_method'] == 'climatology').astype(int)
    table['n_fields_eta_missing'] = (field_rows['interpolation_method'] == 'unfilled_missing').astype(int)
    table['n_fields_peff_fallback'] = (field_rows['peff_method'] != 'otter').astype(int)
    table['n_fields_gw_subsidy'] = (field_rows['gw_frac'] > 0).astype(int)
    table['_acres_missing_eta'] = acres.where(field_rows['ETa_final_acre_ft'].isna(), 0)
    # Field-equivalents: a mixed field counts by its GW share for the year
    table['_gw_equivalent'] = np.select([source == 1, source == 2], [1.0, 0.0], 1.0 - field_rows['sw_share'])

    if state_split:
        in_nevada = (field_rows['STATE'] == 'NV').to_numpy()
        for col in SUMMARY_STATE_SPLIT_COLUMNS:
            table[f'{col}_NV'] = table[col].where(in_nevada, 0)
            table[f'{col}_outside_NV'] = table[col].where(~in_nevada, 0)

    summary = table.groupby(keys, as_index=False, sort=True).sum(min_count=1)
    for col in summary.columns:
        if col.endswith(('_acre_ft', '_acre_ft_NV', '_acre_ft_outside_NV')):
            summary[col] = summary[col].fillna(0.0)

    area = summary['ACRES_FTR_GEOM'].replace(0, np.nan)
    for col in SUMMARY_WEIGHTED_MM_COLUMNS:
        summary[col] = summary[f'_{col}_x_acres'] / summary[f'_{col}_acres'].replace(0, np.nan)
    summary['ETa_final_ft'] = summary['ETa_final_acre_ft'] / area
    summary['NetET_final_ft'] = summary['NetET_final_acre_ft'] / area
    summary['AR_final_ft'] = summary['AR_final_acre_ft'] / area
    summary['PPT_final_ft'] = summary['PPT_vol_acre_ft'] / area
    summary['GW_subsidy_ft'] = summary['GW_subsidy_acre_ft'] / area
    summary['irr_eff_effective'] = summary['NetET_final_acre_ft'] / summary['AR_final_acre_ft'].replace(0, np.nan)
    summary['pct_acres_missing_eta'] = 100 * summary['_acres_missing_eta'] / area
    summary['pct_fields_gw_equivalent'] = 100 * summary['_gw_equivalent'] / summary['n_fields']
    summary['pct_fields_sw_equivalent'] = 100 - summary['pct_fields_gw_equivalent']
    summary = summary.drop(columns=[col for col in summary.columns if col.startswith('_')])

    if basin_year is not None:
        summary = summary.merge(basin_year, on=['HYD_AREA', 'YEAR'], how='left')
    return summary


def process_field_summaries(cfg, paths, spi_data, output_dir, summary_dir, write_field_files=True):
    """
    Process the OpenET field export into field, HA and county water-use summaries.

    Steps (as in the original, with the NWI T1 additions marked):
      Load the year-partitioned ENSEMBLE export; convert -999 and zero ETa to missing;
      NWI T1: replace precipitation with PRISM, effective precipitation with OTTER, add county;
      merge boundary attributes, basin rules and missing-attribute fallbacks;
      split mixed fields into GW and SW by the dynamic SPI 50/50 rule;
      for each of GW, SW and ALL (whole field):
        Step 1   one-month linear EToF interpolation
        Step 2   EToF climatology fill of longer gaps
        Step 3   ETa volume
        Step 4   effective-precipitation volume and intermediate net ET
                 NWI T1: groundwater-ET subsidy
        Step 5-6 winter moisture carry-forward, applied water, final volumes and depths
        Step 6.1 precipitation volume (NWI T1: PRISM, no back-calculation from 0.85 x PPT)
      NWI T1: write field files; HA and county summaries of irrigated field-years.

    Parameters
    ----------
    cfg : dict
        Run configuration (config/nwi_t1_final.yaml, with any QA variant applied).
    paths : RunPaths
        Run directory layout.
    spi_data : pd.DataFrame
        Drought indices with HA_NAME_CODE, YEAR, MONTH, SPI_12m (and spi_source).
    output_dir : Path
        Where field files are written.
    summary_dir : Path
        Where HA/county summaries, allocation stats and run checks are written.
    write_field_files : bool
        Write the field-level CSV and parquet files.

    Returns
    -------
    dict
        Run checks (closure of GW + SW = ALL, counts), also written to run_checks.json.
    """
    inputs = cfg['inputs']
    start_year, end_year = cfg['run']['start_year'], cfg['run']['end_year']
    output_dir = Path(output_dir)
    summary_dir = Path(summary_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_dir.mkdir(parents=True, exist_ok=True)

    # NWI T1: windows from the config (same seven windows as the original)
    gap_windows = [tuple(window) for window in cfg['etof']['climatology_windows']]
    moisture_carryforward = cfg['carryforward']['enabled']
    rooting_depth_m = cfg['carryforward']['rooting_depth_m']

    # AWC table for the carry-forward storage cap (unchanged from the original, path from config)
    awc_lookup = None
    awc_join_key = None
    if moisture_carryforward:
        awc_file = Path(inputs['awc_table'])
        awc_source = pd.read_csv(awc_file, usecols=lambda col: col in {'NWI_ID', 'OPENET_ID', 'AWC'})
        awc_join_key = 'NWI_ID' if 'NWI_ID' in awc_source.columns else 'OPENET_ID'
        awc_source[awc_join_key] = _normalize_field_key(awc_source[awc_join_key])
        awc_source['AWC'] = pd.to_numeric(awc_source['AWC'], errors='coerce')
        awc_source = awc_source.dropna(subset=[awc_join_key, 'AWC'])
        awc_conflicts = awc_source.groupby(awc_join_key)['AWC'].nunique()
        if (awc_conflicts > 1).any():
            raise ValueError(f'AWC table {awc_file} has conflicting values for field IDs')
        awc_lookup = awc_source.drop_duplicates(awc_join_key).set_index(awc_join_key)['AWC']
        print(f'Using per-field AWC from: {awc_file} ({len(awc_lookup):,} fields)')

    # Load high-volume parquet inputs (year=<YYYY>/...parquet), as in the original
    parquet_files = sorted(Path(inputs['ensemble_dir']).rglob('*.parquet'))
    print(f"Found {len(parquet_files)} parquet files to process")
    dfs = [pd.read_parquet(file) for file in parquet_files if file.stat().st_size > 0]
    combined_df = pd.concat(dfs, ignore_index=True)
    combined_df['YEAR'] = combined_df['YEAR'].astype(int)          # NWI T1: MONTH is text in the export
    combined_df['MONTH'] = combined_df['MONTH'].astype(int)
    combined_df = combined_df[combined_df['YEAR'].between(start_year, end_year)]
    before_rows = len(combined_df)
    combined_df = combined_df.drop_duplicates()
    dropped = before_rows - len(combined_df)
    if dropped > 0:
        print(f"Warning: dropped {dropped} duplicate rows after concatenation to prevent double counting.")
    print(f"Combined dataframe shape: {combined_df.shape}")

    # Generate HA_NAME_CODE (replaced below by the boundary layer's Basin attribute)
    combined_df['HA_NAME_CODE'] = combined_df['HYD_AREA_N'].astype(str) + ' (' + combined_df['HYD_AREA'].astype(str) + ')'

    # Convert Earth Engine missing values to pandas NaN for interpolation
    print("Converting EE missing values to NaN for interpolation...")
    var_cols = ['ETA_MM', 'PPT_MM', 'EFF_PPT_MM', 'ETO_MM', 'AR_MM', 'NDVI', 'tmin', 'tmax']
    missing_value = -999
    for col in var_cols:
        if col in combined_df.columns:
            before_count = (combined_df[col] == missing_value).sum()
            combined_df[col] = combined_df[col].replace(missing_value, np.nan)
            print(f"Converted {before_count} missing data values (-999) to NaN in {col}")

            # CRITICAL FIX: For ETA_MM, also flag zero values as missing
            # Bug in OpenET collection: ET values of 0 when count band != 0 are actually missing data
            # Zero values in other variables (like PPT_MM) are valid, so only apply to ETA_MM
            if col == 'ETA_MM':
                combined_df['ETA_MM_EXPORT'] = combined_df['ETA_MM']   # NWI T1: keep the exported value
                zero_count = (combined_df[col] == 0).sum()
                if zero_count > 0:
                    combined_df[col] = combined_df[col].replace(0, np.nan)
                    print(f"  ⚠️  CRITICAL: Also converted {zero_count} zero ET values to NaN (OpenET bug fix)")
                    print(f"  Total ETA_MM values flagged as missing: {before_count + zero_count}")

    # NWI T1: precipitation, effective precipitation and county
    if cfg['precipitation']['source'] == 'prism':
        print("NWI T1: replacing export precipitation with PRISM 800 m monthly precipitation...")
        combined_df = merge_prism_precipitation(combined_df, paths.prism_monthly)
    if cfg['effective_precipitation']['source'] == 'otter':
        print("NWI T1: replacing 0.85 x PPT with OTTER effective precipitation...")
        combined_df = merge_otter_effective_precipitation(combined_df, paths.otter_monthly)
    else:
        combined_df['peff_method'] = 'export_085'
    combined_df = merge_county_assignment(combined_df, inputs['county_assignment'])

    # Field attributes, basin mapping and fallbacks (as in the original)
    fieldsdf = load_field_attributes(inputs['fields_gpkg'])
    # NWI T1: fixed basin rules applied to the boundary attributes once
    static_overrides = cfg['allocation']['static_overrides'] if cfg['allocation'].get('apply_static_overrides', True) else {}
    fieldsdf = apply_static_basin_overrides(fieldsdf, static_overrides)
    attribute_report_path = summary_dir / 'field_attribute_enrichment_report.csv'
    combined_df = merge_field_attributes(combined_df, fieldsdf, report_path=attribute_report_path, strict=True)
    combined_df = enforce_boundary_basin_mapping(combined_df, fieldsdf, strict=True)
    print("Using legacy boundary Basin attribute for HYD_AREA/HA_NAME_CODE aggregation")
    combined_df = apply_irrigation_attribute_fallbacks(combined_df, report_path=attribute_report_path)
    combined_df['IRR_SRC_ORIGINAL'] = combined_df['IRR_SRC_ORIGINAL'].fillna(combined_df['IRR_SRC'])  # NWI T1
    combined_df['IRR_SRC_OVERRIDE'] = combined_df['IRR_SRC_OVERRIDE'].fillna('')

    # Dynamic uniform allocation (year-by-year SPI, 0.5 mean)
    print("\n" + "="*80)
    print("Using DYNAMIC UNIFORM allocation (year-by-year based on SPI)")
    print("="*80)
    allocation = cfg['allocation']
    # NWI T1: basin rules from the run config. The original read them with load_config() and
    # no arguments, i.e. from the default workflow config, so the Fish Lake Valley rule was
    # never applied in NWI runs.
    basin_config_dict = allocation['dynamic_overrides'] if allocation.get('apply_dynamic_overrides', True) else {}
    combined_df_gw, combined_df_sw, combined_df_mixed, basin_stats_df = apply_gw_sw_separation_dynamic(
        combined_df, fieldsdf,
        basin_config_dict=basin_config_dict,
        spi_data=spi_data,
        min_sw_ratio=allocation['min_sw_ratio'],
        max_sw_ratio=allocation['max_sw_ratio'],
        use_uniform_mean_ratio=True,
        allocation_overrides=None,          # NWI T1: applied to the attributes above
        spi_reference_month=cfg['spi']['reference_month'],
    )
    basin_stats_df.to_csv(summary_dir / 'dynamic_allocation_stats.csv', index=False)

    # NWI T1: SW share of every field-year, used for the ALL dataset and the summaries
    share_lookup = basin_stats_df[['basin_code', 'year', 'sw_ratio', 'spi_value', 'allocation_method']].rename(
        columns={'basin_code': 'HA_NAME_CODE', 'year': 'YEAR'})
    spi_source_by_basin = {}
    if 'spi_source' in spi_data.columns:
        spi_codes = spi_data['HA_NAME_CODE'].astype(str).str.extract(r'\(([^)]+)\)\s*$')[0]
        spi_source_by_basin = dict(zip(spi_codes, spi_data['spi_source']))
    def spi_source_for(ha_name_code):
        code = pd.Series(ha_name_code).astype(str).str.extract(r'\(([^)]+)\)\s*$')[0]
        return code.map(spi_source_by_basin).fillna('').to_numpy()

    combined_df_all = combined_df.merge(share_lookup, on=['HA_NAME_CODE', 'YEAR'], how='left')
    is_mixed = combined_df_all['IRR_SRC'] == 3
    combined_df_all['sw_share'] = np.select(
        [combined_df_all['IRR_SRC'] == 1, combined_df_all['IRR_SRC'] == 2], [0.0, 1.0], combined_df_all['sw_ratio'])
    combined_df_all['water_source'] = np.select(
        [combined_df_all['IRR_SRC'] == 1, combined_df_all['IRR_SRC'] == 2], ['GW', 'SW'], 'mixed')
    combined_df_all['allocation_method'] = np.select(
        [combined_df_all['IRR_SRC'] == 1, combined_df_all['IRR_SRC'] == 2], ['pure_gw', 'pure_sw'],
        combined_df_all['allocation_method'])
    combined_df_all['allocation_ratio'] = 1.0
    combined_df_all.loc[~is_mixed, 'spi_value'] = np.nan
    combined_df_all['spi_source'] = np.where(is_mixed, spi_source_for(combined_df_all['HA_NAME_CODE']), '')
    combined_df_all = combined_df_all.drop(columns=['sw_ratio'])
    for component in (combined_df_gw, combined_df_sw):
        component_mixed = component['IRR_SRC'] == 3
        component['spi_source'] = np.where(component_mixed, spi_source_for(component['HA_NAME_CODE']), '')
        component['sw_share'] = np.where(component['water_source'].isin(['SW', 'SW_from_mixed']),
                                         np.where(component_mixed, component['allocation_ratio'], 1.0),
                                         np.where(component_mixed, 1.0 - component['allocation_ratio'], 0.0))

    # NWI T1: groundwater-ET fraction per field
    gw_cfg = cfg['gw_subsidy']
    gw_frac_by_field = None
    if gw_cfg['enabled']:
        field_params = pd.read_parquet(paths.field_gwfrac, columns=['OPENET_ID', 'gw_frac'])
        gw_frac_by_field = field_params.set_index('OPENET_ID')['gw_frac']

    climatologies = {}
    if cfg['etof'].get('climatology_fill', False):
        climatologies = load_climatologies(paths.etof_climatology, gap_windows)

    datasets_to_process = [('GW', combined_df_gw), ('SW', combined_df_sw), ('ALL', combined_df_all)]
    del combined_df, combined_df_mixed
    summary_rows = {}

    for dataset_name, df_to_process in datasets_to_process:
        print(f"\n=== Processing {dataset_name} dataset ===")
        df_to_process = merge_field_attributes(df_to_process, fieldsdf)

        # Initialize EToF and Interpolation Tracking Columns
        df_to_process['EToF'] = np.where(df_to_process['ETO_MM'] > 0, df_to_process['ETA_MM'] / df_to_process['ETO_MM'], np.nan)
        df_to_process['EToF_interpolated'] = df_to_process['EToF'].copy()
        df_to_process['interpolation_method'] = 'original'

        # Step 1: Linear Interpolation
        print("Step 1: Applying linear interpolation for 1-month EToF gaps...")
        df_to_process = apply_linear_interpolation_etof(df_to_process, group_cols=['HA_NAME_CODE', 'OPENET_ID'])

        # Step 2: Climatology Interpolation
        # NWI T1: climatologies are built beforehand by calculate_field_climatologies.py
        if climatologies:
            print("Step 2: Applying climatology for 2+ month EToF gaps...")
            df_to_process = apply_climatology_gap_filling_etof(df_to_process, climatologies, gap_windows, group_cols=['HA_NAME_CODE', 'OPENET_ID'])
        else:
            print("Step 2: EToF climatology fill is off. Skipping 2+ month gap filling.")

        # Mark unfilled gaps
        still_missing_mask = (
            df_to_process['EToF'].isna() &
            df_to_process['EToF_interpolated'].isna() &
            (df_to_process['interpolation_method'] == 'original')
        )
        df_to_process.loc[still_missing_mask, 'interpolation_method'] = 'unfilled_missing'
        print("Step 2.5: Marked unfilled gaps after all interpolation attempts")

        # Step 3: Calculate ETa based on final EToF_interpolated
        print("Step 3: Calculating intermediate ETa volume after interpolation...")
        df_to_process['ETA_MM_post_interp'] = df_to_process['EToF_interpolated'] * df_to_process['ETO_MM']
        df_to_process['ETa_vol_intermediate_acre_ft'] = (df_to_process['ETA_MM_post_interp'] / 1000) * 3.281 * df_to_process['ACRES_FTR_GEOM']

        # Step 4: Calculate other initial volumes and pre-carryforward NetET
        print("Step 4: Calculating other initial volumes and pre-carryforward NetET...")
        df_to_process['Eff_PPT_vol_acre_ft'] = (df_to_process['EFF_PPT_MM'] / 1000) * 3.281 * df_to_process['ACRES_FTR_GEOM']
        df_to_process['NetET_vol_intermediate_acre_ft'] = df_to_process['ETa_vol_intermediate_acre_ft'] - df_to_process['Eff_PPT_vol_acre_ft']

        # NWI T1: Step 4.5, groundwater-ET subsidy before the carry-forward
        print("Step 4.5 (NWI T1): Subtracting the groundwater-ET subsidy from net ET...")
        df_to_process = add_gw_subsidy(
            df_to_process,
            gw_frac_by_field if gw_cfg['enabled'] else None,
            irrigated_only=gw_cfg.get('irrigated_only', False),
            irrigated_min_fraction=gw_cfg['irrigated_min_fraction'],
        )

        # --- STEPS 5-6: Final volumes ---
        print("Steps 5-6: Calculating final volumes using unified logic...")
        if moisture_carryforward:
            awc_values = _normalize_field_key(df_to_process[awc_join_key]).map(awc_lookup)
            df_to_process['AWC'] = awc_values
            missing_awc_fields = df_to_process.loc[df_to_process['AWC'].isna(), awc_join_key].nunique()
            if missing_awc_fields:
                raise ValueError(f'{missing_awc_fields:,} fields lack AWC; stopped before winter carry-forward.')
            print("Using moisture carry-forward for final AR and NetET...")
            df_to_process = apply_winter_moisture_carryforward(
                df_to_process,
                eta_vol_intermediate_col='ETa_vol_intermediate_acre_ft',
                net_et_vol_intermediate_col='NetET_vol_intermediate_acre_ft',
                ar_original_vol_col='Eff_PPT_vol_acre_ft',    # NWI T1: unused by the function; the export AR_MM is not carried
                irr_eff_col='irr_eff',
                rooting_depth_m=rooting_depth_m,
                year_reset_month=cfg['carryforward']['reset_month'],
                group_cols=['HA_NAME_CODE', 'OPENET_ID']
            )
            df_to_process['ETa_final_acre_ft'] = df_to_process['ETa_vol_intermediate_acre_ft']
        else:
            print("No carry-forward. Calculating final volumes from interpolated data...")
            df_to_process['ETa_final_acre_ft'] = df_to_process['ETa_vol_intermediate_acre_ft']
            df_to_process['NetET_final_acre_ft'] = df_to_process['NetET_vol_intermediate_acre_ft']
            df_to_process['AR_final_acre_ft'] = df_to_process['NetET_final_acre_ft'] / df_to_process['irr_eff']
            df_to_process['Water_Surplus_Cumulative_acre_ft'] = 0
            df_to_process['Soil_Moisture_Overflow_acre_ft'] = 0
            df_to_process['AWC'] = np.nan                       # NWI T1: keep the output columns
            df_to_process['PAW_ft'] = np.nan
            df_to_process['Soil_Moisture_Cap_acre_ft'] = np.nan

        # --- Now, set any negative AR_final_acre_ft to zero ---
        negative_count = (df_to_process['AR_final_acre_ft'] < 0).sum()
        if negative_count > 0:
            print(f"Warning: Found {negative_count} negative AR_final_acre_ft values. Setting to zero.")
            df_to_process.loc[df_to_process['AR_final_acre_ft'] < 0, 'AR_final_acre_ft'] = 0

        # -- optionally also safety for NetET --
        negative_netet_count = (df_to_process['NetET_final_acre_ft'] < 0).sum()
        if negative_netet_count > 0:
            print(f"Warning: Found {negative_netet_count} negative NetET_final_acre_ft values. Setting to zero.")
            df_to_process.loc[df_to_process['NetET_final_acre_ft'] < 0, 'NetET_final_acre_ft'] = 0

        # NWI T1: stored water used this month
        df_to_process['Carryforward_Used_acre_ft'] = (
            df_to_process['NetET_vol_intermediate_acre_ft'].clip(lower=0) - df_to_process['NetET_final_acre_ft'])

        # --- Calculate final rates ---
        print("Calculating final rates from final volumes...")
        df_to_process['AR_final_ft'] = df_to_process['AR_final_acre_ft'] / df_to_process['ACRES_FTR_GEOM'].replace(0, np.nan)
        df_to_process['NetET_final_ft'] = df_to_process['NetET_final_acre_ft'] / df_to_process['ACRES_FTR_GEOM'].replace(0, np.nan)
        df_to_process['ETa_final_ft'] = df_to_process['ETa_final_acre_ft'] / df_to_process['ACRES_FTR_GEOM'].replace(0, np.nan)

        for col in ['AR_final_ft', 'NetET_final_ft', 'ETa_final_ft']:
            df_to_process[col] = df_to_process[col].fillna(0)

        # Step 6.1: Precipitation volume
        # NWI T1: PPT_MM is PRISM precipitation; the original back-calculated PPT = EFF_PPT / 0.85
        print("Step 6.1: Calculating precipitation volume...")
        df_to_process['PPT_vol_acre_ft'] = (df_to_process['PPT_MM'] / 1000) * 3.281 * df_to_process['ACRES_FTR_GEOM']
        df_to_process['PPT_final_ft'] = df_to_process['PPT_vol_acre_ft'] / df_to_process['ACRES_FTR_GEOM'].replace(0, np.nan)
        df_to_process['PPT_final_ft'] = df_to_process['PPT_final_ft'].fillna(0)

        # NWI T1: field files with the documented column names (docs/DATA_DICTIONARY.md)
        df_to_process = df_to_process.sort_values(['NWI_ID', 'YEAR', 'MONTH'], kind='mergesort').reset_index(drop=True)
        df_to_process['DATE'] = pd.to_datetime(dict(year=df_to_process['YEAR'], month=df_to_process['MONTH'], day=1)).dt.strftime('%Y-%m-%d')
        field_output = df_to_process[list(FIELD_OUTPUT_COLUMNS)].rename(columns=FIELD_OUTPUT_COLUMNS)
        if write_field_files:
            field_output.to_parquet(output_dir / f'field_summaries_{dataset_name}.parquet', index=False)
            if cfg['outputs']['write_field_csv']:
                field_output.to_csv(output_dir / f'field_summaries_{dataset_name}.csv', index=False)
            print(f"Field summaries saved to: {output_dir / f'field_summaries_{dataset_name}.csv'}")
        summary_rows[dataset_name] = field_output.assign(
            sw_share=df_to_process['sw_share'].to_numpy(), IN_NV_COUNTY=df_to_process['IN_NV_COUNTY'].to_numpy())
        print(f"{dataset_name}: interpolation methods {df_to_process['interpolation_method'].value_counts().to_dict()}")

    # ---- NWI T1: checks, summaries and statewide totals ----------------------------------------
    checks = {'variant': cfg.get('_variant', 'final'),
              'config': {key: cfg[key] for key in ('precipitation', 'effective_precipitation', 'spi', 'allocation',
                                                    'gw_subsidy', 'carryforward', 'etof', 'summaries')}}
    for col in ['ETa_final_acre_ft', 'NetET_final_acre_ft', 'AR_final_acre_ft', 'GW_subsidy_acre_ft', 'ACRES_FTR_GEOM']:
        totals = {name: float(rows[col].sum()) for name, rows in summary_rows.items()}
        relative_error = (totals['GW'] + totals['SW'] - totals['ALL']) / totals['ALL'] if totals['ALL'] else 0.0
        checks[f'closure_{col}'] = {**totals, 'rel_err_gw_plus_sw_vs_all': relative_error}
        if abs(relative_error) > 1e-9:
            raise AssertionError(f'GW + SW != ALL for {col}')
    all_rows = summary_rows['ALL']
    checks['fields'] = int(all_rows['NWI_ID'].nunique())
    checks['peff_method_fields'] = all_rows.groupby('peff_method')['NWI_ID'].nunique().to_dict()
    checks['interpolation_method'] = {k: int(v) for k, v in all_rows['interpolation_method'].value_counts().items()}
    checks['spi_missing_basin_years'] = int(basin_stats_df['spi_status'].eq('missing').sum())

    basin_year = basin_stats_df.rename(columns={'sw_ratio': 'mixed_sw_ratio', 'spi_value': 'spi_sept12'})
    basin_year['HYD_AREA'] = basin_year['basin_code'].astype(str).str.extract(r'\(([^)]+)\)\s*$')[0]
    basin_year['spi_source'] = spi_source_for(basin_year['basin_code'])
    basin_year = basin_year.rename(columns={'year': 'YEAR'})[
        ['HYD_AREA', 'YEAR', 'mixed_sw_ratio', 'spi_sept12', 'spi_source', 'allocation_method']]

    summaries = cfg['summaries']
    threshold = float(summaries['irrigated_min_fraction'])

    def summary_population(rows):
        """Rows counted in the HA/county summaries: irrigated field-years, if configured."""
        if not summaries.get('irrigated_only', False):
            return rows
        return rows[rows['%_IRRIGATED'] > threshold]

    flux_columns = [col for col in SUMMARY_VOLUME_COLUMNS
                    if col not in ('ACRES_FTR_GEOM', 'ACRES_IRR', 'Soil_Moisture_Cap_acre_ft', 'Soil_Storage_acre_ft')]
    pct_columns = ['pct_fields_gw_equivalent', 'pct_fields_sw_equivalent']
    ha_keys = ['HA_NAME_CODE', 'HYD_AREA', 'HYD_AREA_N', 'YEAR', 'MONTH']
    county_keys = ['COUNTY_NAME', 'YEAR', 'MONTH']
    all_population = summary_population(summary_rows['ALL'])
    nevada_all = all_population[all_population['IN_NV_COUNTY'].fillna(False).astype(bool)]
    # Field-share percentages describe all fields in the HA or county, so they come from ALL.
    pct_ha = aggregate_summaries(all_population, ha_keys)[ha_keys + pct_columns]
    pct_county = aggregate_summaries(nevada_all, county_keys)[county_keys + pct_columns]
    statewide = []
    for dataset_name, rows in summary_rows.items():
        irrigated = rows['%_IRRIGATED'] > threshold
        for population, population_rows in (('all_field_months', rows), ('irrigated_field_months', rows[irrigated])):
            annual = population_rows.groupby('YEAR')[flux_columns].sum()
            annual.insert(0, 'population', population)
            annual.insert(0, 'component', dataset_name)
            statewide.append(annual.reset_index())
        population_rows = summary_population(rows)
        print(f"Step 7: Aggregating {dataset_name} by HA and county ({len(population_rows):,} field-months)...")
        ha_summary = aggregate_summaries(population_rows, ha_keys, basin_year, state_split=True).drop(columns=pct_columns)
        ha_summary.merge(pct_ha, on=ha_keys, how='left').to_csv(summary_dir / f'ha_summaries_{dataset_name}.csv', index=False)
        nevada_rows = population_rows[population_rows['IN_NV_COUNTY'].fillna(False).astype(bool)]
        county_summary = aggregate_summaries(nevada_rows, county_keys).drop(columns=pct_columns)
        county_summary.merge(pct_county, on=county_keys, how='left').to_csv(
            summary_dir / f'county_summaries_{dataset_name}.csv', index=False)
    pd.concat(statewide).to_csv(summary_dir / 'statewide_annual_totals.csv', index=False)
    (summary_dir / 'run_checks.json').write_text(json.dumps(checks, indent=2, default=str))
    print(f"Summaries saved to: {summary_dir}")
    return checks


def load_spi_data(cfg, paths):
    """
    NWI T1: drought-indices table for the dynamic allocation.

    Parameters
    ----------
    cfg : dict
        Run configuration; spi.source is 'prism_d24' (calculate_basin_spi.py output) or
        'legacy' (the gridMET ag-footprint file used by the 2026-08-23 package; QA only).
    paths : RunPaths
        Run directory layout.

    Returns
    -------
    pd.DataFrame
        HA_NAME_CODE, YEAR, MONTH, SPI_12m and, for PRISM, spi_source.
    """
    if cfg['spi']['source'] == 'prism_d24':
        spi_file = paths.spi_table
    elif cfg['spi']['source'] == 'legacy':
        spi_file = Path(cfg['inputs']['legacy_spi'])
    else:
        raise ValueError(f"Unknown spi.source {cfg['spi']['source']!r}")
    print(f"\nLoading SPI data from: {spi_file}")
    spi_data = pd.read_csv(spi_file)
    print(f"Loaded SPI data: {len(spi_data)} records, {spi_data['HA_NAME_CODE'].nunique()} basins")
    return spi_data


if __name__ == "__main__":
    import argparse

    # NWI T1: all settings come from config/nwi_t1_final.yaml; --variant applies a QA variant
    parser = argparse.ArgumentParser(
        description='Process field summaries and generate HA and county summaries with SW/GW separation.'
    )
    parser.add_argument('--config', type=str, help='Path to the run config (default config/nwi_t1_final.yaml)')
    parser.add_argument('--variant', type=str, help='QA variant from config/bridge_variants.yaml')
    parser.add_argument('--no-field-files', action='store_true', help='Write summaries only')
    args = parser.parse_args()

    cfg = load_config(args.config, variant=args.variant)
    cfg['_variant'] = args.variant or 'final'
    paths = RunPaths(cfg).make()
    final_run = args.variant is None
    output_dir = paths.fields
    summary_dir = paths.summaries if final_run else paths.variants / args.variant

    print("="*80)
    print(f"NWI T1 FIELD PROCESSING ({cfg['_variant']})")
    print(f"  Mixed fields: SPI_{cfg['spi']['timescale_months']}m, mean ratio 0.5, "
          f"range {cfg['allocation']['min_sw_ratio']} (dry) to {cfg['allocation']['max_sw_ratio']} (wet)")
    print("="*80)
    checks = process_field_summaries(
        cfg, paths, load_spi_data(cfg, paths),
        output_dir=output_dir,
        summary_dir=summary_dir,
        write_field_files=final_run and not args.no_field_files,
    )
    print("\n" + "="*80)
    print("Processing completed successfully!")
    print("="*80)
