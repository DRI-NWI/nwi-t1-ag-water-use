"""
Process field summaries and generate HA (Hydrographic Area) level aggregations.

This script processes field-level ET data and aggregates it to basin level,
including water source (SW/GW) separation and allocation optimization.

CONFIGURATION:
All paths and parameters loaded from config/workflow_config.yaml

Author: Peter ReVelle
        With AI assistance from Claude, Gemini, ChatGPT, and other models
Date: 2024-2025
Updated: January 13, 2026 - Unified configuration system
"""

import os
import sys
import pandas as pd
import geopandas as gpd 
import numpy as np
from pathlib import Path
from datetime import datetime
import warnings
import pyarrow.parquet as pq  # For parquet support
# from basin_thresholds_dict import BASIN_THRESHOLDS

# Add project root to path for config import
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / 'scripts' / 'utils'))

# Import unified configuration
from config import load_config

# Import SPI-to-ratio utility (used by apply_gw_sw_separation_dynamic)
from allocation_utils import spi_to_sw_ratio


# Assuming calculate_field_climatologies.py is in the same directory or in python path
# and has been updated to use EToF and ETO_MM.
from calculate_field_climatologies import calculate_etof_climatologies, check_climatology_files_exist    

# Configuration
irrigation_filter = False
irrigation_filter_method = 'percentage'
fill_missing_data = True
moisture_carryforward = False
separate_gw_sw = True

# Attributes carried by the NWI field-boundary asset.  High-volume exports
# should contain these already, but the processor defensively backfills them
# from the matching local boundary file when an older/incomplete export is
# supplied.
FIELD_ATTRIBUTE_COLUMNS = [
    'NWI_ID', 'NWI_PARENT', 'OPENET_ID', 'GRIDMET_ID', 'Basin', 'Acres',
    'STATE', 'IRR_SRC', 'irr_eff', 'ITYPE',
]

def load_basin_specific_thresholds():
    """Load basin-specific ETa/ETO thresholds from validation results"""
    default_threshold = {'percentile': 54, 'threshold': 0.3358, 'accuracy': 0.901}
    return BASIN_THRESHOLDS, default_threshold

def classify_field_activity_status(field_etof, threshold):
    """Classify field activity status based on ETa/ETO ratio"""
    return 'active' if field_etof >= threshold else 'inactive'

def filter_historical_data_by_thresholds(historical_data, basin_thresholds, default_threshold):
    """
    Apply STATIC ETa/ETO threshold filtering to historical data
    Uses the pre-calculated optimal threshold value, NOT percentile calculation
    """
    print("Filtering historical data using STATIC irrigation activity thresholds...")
    
    filtered_data = []
    
    for ha_name in historical_data['HA_NAME_CODE'].unique():
        basin_data = historical_data[historical_data['HA_NAME_CODE'] == ha_name]
        
        # Get STATIC threshold for this basin (NOT percentile)
        if ha_name in basin_thresholds:
            threshold = basin_thresholds[ha_name]['threshold']  # Use pre-calculated threshold
        else:
            threshold = default_threshold['threshold']
        
        print(f"Basin {ha_name}: using STATIC threshold = {threshold:.4f}")
        
        # Apply the SAME threshold to ALL years (no year-by-year calculation)
        for year in basin_data['YEAR'].unique():
            year_data = basin_data[basin_data['YEAR'] == year]
            
            for field_id in year_data['OPENET_ID'].unique():
                field_year_data = year_data[year_data['OPENET_ID'] == field_id]
                
                if len(field_year_data) > 0:
                    # Calculate annual ETa/ETO for this field in this specific year
                    annual_eta = field_year_data['ETA_MM'].sum()
                    annual_eto = field_year_data['ETO_MM'].sum()
                    field_etof = annual_eta / annual_eto if annual_eto > 0 else 0
                    
                    # Apply STATIC threshold (not dynamic)
                    activity_status = classify_field_activity_status(field_etof, threshold)
                    
                    # Only include active fields
                    if activity_status == 'active':
                        field_year_data_copy = field_year_data.copy()
                        field_year_data_copy['activity_status'] = activity_status
                        field_year_data_copy['annual_etof'] = field_etof
                        field_year_data_copy['static_threshold'] = threshold
                        filtered_data.append(field_year_data_copy)
    
    if filtered_data:
        filtered_df = pd.concat(filtered_data, ignore_index=True)
        print(f"Static threshold filtering results: {len(filtered_df)} records from {filtered_df['OPENET_ID'].nunique()} active fields")
        return filtered_df
    else:
        return pd.DataFrame()


# --- Helper function to get climatology ---
def get_field_climatology(field_id, ha_name, month, year, climatologies, gap_windows):
    """
    Get climatology value using the correct window based on year.
    
    Uses the climatology file that matches the year of the missing data.
    For years outside defined windows, uses the nearest window as fallback.
    This ensures temporal appropriateness (e.g., 2006 gap uses 2004-2009 climatology).
    """
    # Find which climatology window this year belongs to
    window_key = None
    for start_year, end_year in gap_windows:
        if start_year <= year <= end_year:
            window_key = f"{start_year}_{end_year}"
            break
    
    # If year doesn't fall in any defined window, use the nearest one
    if window_key is None:
        # Find the closest window
        closest_window = min(gap_windows, key=lambda w: min(abs(year - w[0]), abs(year - w[1])))
        window_key = f"{closest_window[0]}_{closest_window[1]}"
        # Note: Only log this once per session to avoid spam
        # print(f"Info: Year {year} outside defined windows, using nearest: {window_key}")
    
    # Now use the appropriate climatology for this specific year
    if window_key not in climatologies:
        return np.nan
    
    climo_df_for_window = climatologies[window_key]
    
    if field_id not in climo_df_for_window.index:
        return np.nan
    
    field_data_row = climo_df_for_window.loc[[field_id]]
    
    if 'HA_NAME_CODE' in field_data_row.columns:
        field_data_row = field_data_row[field_data_row['HA_NAME_CODE'] == ha_name]
    
    if not field_data_row.empty:
        climo_col_name = f'EToF_climo_{month:02d}'
        if climo_col_name in field_data_row.columns:
            return field_data_row[climo_col_name].iloc[0]
    
    return np.nan

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


def apply_gw_sw_separation(df, fieldsdf, mixed_to_sw_ratio=0.5,
                           allocation_overrides=None):
    """
    Apply groundwater/surface water separation based on IRR_SRC attribute.
    This corrected version properly splits mixed-source fields.
    
    Parameters:
    -----------
    df : pandas.DataFrame
        Input dataframe with field data
    fieldsdf : pandas.DataFrame
        Field attributes dataframe with IRR_SRC column
    mixed_to_sw_ratio : float, default=0.5
        Fraction of mixed fields (IRR_SRC=3) to allocate to SW dataset.
        Range: 0.0 (all mixed to GW) to 1.0 (all mixed to SW)
        Examples:
          0.0: Pure GW model (100% GW + 100% mixed)
          0.5: Balanced split (50/50 as before)
          1.0: Pure SW model (100% SW + 100% mixed)
    allocation_overrides : dict, optional
        Basin-specific overrides from load_basin_allocation_overrides().
        Extreme values (0.0 or 1.0) reclassify IRR_SRC before the split;
        intermediate values are applied as the basin's static mixed-field
        GW fraction.
    """
    from allocation_utils import apply_irr_src_overrides

    print(f"Applying GW/SW separation with mixed_to_sw_ratio={mixed_to_sw_ratio:.2f}")
    print(f"  Mixed fields: {mixed_to_sw_ratio*100:.0f}% to SW, {(1-mixed_to_sw_ratio)*100:.0f}% to GW")
    
    # Join with field attributes to get IRR_SRC
    df_with_attrs = merge_field_attributes(df, fieldsdf)
    
    # Filter out fields with IRR_SRC = 0 (unattributed)
    df_filtered = df_with_attrs[df_with_attrs['IRR_SRC'] != 0].copy()
    print(f"Records after filtering IRR_SRC=0: {len(df_filtered)}")
    
    # Apply basin-specific IRR_SRC overrides before the split
    if allocation_overrides:
        print(f"  Applying {len(allocation_overrides)} basin allocation override(s)...")
        df_filtered = apply_irr_src_overrides(df_filtered, allocation_overrides)
    
    # Separate into GW, SW, and Mixed components
    df_gw_pure = df_filtered[df_filtered['IRR_SRC'] == 1].copy()
    df_sw_pure = df_filtered[df_filtered['IRR_SRC'] == 2].copy()
    df_mixed = df_filtered[df_filtered['IRR_SRC'] == 3].copy()
    
    print(f"Pure GW fields: {df_gw_pure['OPENET_ID'].nunique()}")
    print(f"Pure SW fields: {df_sw_pure['OPENET_ID'].nunique()}")
    print(f"Mixed fields: {df_mixed['OPENET_ID'].nunique()}")
    
    # Create GW and SW components from the mixed-source fields
    df_mixed_gw = df_mixed.copy()
    df_mixed_sw = df_mixed.copy()
    
    # Allocate mixed acreage using the global ratio, overridden by any
    # intermediate basin-specific static GW fraction.  The dynamic workflow
    # uses its own SPI-driven basin overrides; this branch handles legacy
    # static products consistently.
    sw_ratio = pd.Series(float(mixed_to_sw_ratio), index=df_mixed.index)
    if allocation_overrides and 'HA_NAME_CODE' in df_mixed.columns:
        basin_codes = df_mixed['HA_NAME_CODE'].astype(str).str.extract(r'\(([^)]+)\)\s*$', expand=False)
        for basin_code, gw_fraction in allocation_overrides.items():
            try:
                sw_ratio.loc[basin_codes.eq(str(basin_code))] = 1.0 - float(gw_fraction)
            except (TypeError, ValueError):
                continue
    df_mixed_sw['ACRES_FTR_GEOM'] = df_mixed_sw['ACRES_FTR_GEOM'] * sw_ratio
    df_mixed_gw['ACRES_FTR_GEOM'] = df_mixed_gw['ACRES_FTR_GEOM'] * (1.0 - sw_ratio)
    print(f"Mixed fields: mean {sw_ratio.mean()*100:.1f}% allocated to SW, "
          f"{(1-sw_ratio).mean()*100:.1f}% to GW")

    # Add water source tracking for clarity in output files
    df_gw_pure['water_source'] = 'GW'
    df_sw_pure['water_source'] = 'SW'
    df_mixed_gw['water_source'] = 'GW_from_mixed'
    df_mixed_sw['water_source'] = 'SW_from_mixed'
    
    # Combine: GW dataset gets pure GW fields + mixed fields (100% or 50% depending on mode)
    df_gw_combined = pd.concat([df_gw_pure, df_mixed_gw], ignore_index=True)
    
    # Combine: SW dataset gets pure SW fields + mixed fields (0% or 50% depending on mode)
    df_sw_combined = pd.concat([df_sw_pure, df_mixed_sw], ignore_index=True)
    
    print(f"Final GW dataset records: {len(df_gw_combined)}")
    print(f"Final SW dataset records: {len(df_sw_combined)}")
    
    # Return the separated datasets for further processing
    return df_gw_combined, df_sw_combined, df_mixed



def load_basin_info_from_shapefile(fields_shapefile_path):
    """
    Load basin information from fields shapefile to overwrite HYD_AREA values
    """
    print(f"Loading basin information from: {fields_shapefile_path}")
    
    # Read shapefile
    fieldsdf = gpd.read_file(fields_shapefile_path)
    
    # Select only needed columns for basin mapping
    basin_df = fieldsdf[['OPENET_ID', 'Basin']].copy()
    
    # Parse BASIN column to extract HYD_AREA and HYD_AREA_N
    # Assuming BASIN format is "001_Pueblo Valley"
    basin_df[['HYD_AREA_new', 'HYD_AREA_N_new']] = basin_df['Basin'].str.split('_', n=1, expand=True)
    
    # Clean up the values
    basin_df['HYD_AREA_new'] = basin_df['HYD_AREA_new'].str.strip()
    basin_df['HYD_AREA_N_new'] = basin_df['HYD_AREA_N_new'].str.strip()
    
    # Remove the original BASIN column
    basin_df = basin_df.drop('Basin', axis=1)
    
    print(f"Loaded basin information for {len(basin_df)} fields")
    print(f"Example basin mappings:")
    print(basin_df.head())
    
    return basin_df


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

def save_results_by_basin(df, output_dir, file_prefix='', file_type='field_summaries'):
    """
    Save dataframe as CSV and also create parquet files by basin in a 'parquet' subfolder.
    
    Parameters:
    - df: DataFrame to save
    - output_dir: Main output directory
    - file_prefix: Prefix for the main CSV file (e.g., 'GW_', 'SW_', etc.)
    - file_type: Type of file being saved (e.g., 'field_summaries', 'ha_summaries')
    
    Returns:
    - csv_path: Path to saved CSV file
    - parquet_files: List of saved parquet file paths
    """
    import os
    
    # Create parquet subdirectory
    parquet_dir = os.path.join(output_dir, 'parquet')
    os.makedirs(parquet_dir, exist_ok=True)
    
    # Save parquet files by basin (HYD_AREA)
    parquet_files = []
    if 'HYD_AREA' in df.columns:
        unique_basins = df['HYD_AREA'].dropna().unique()
        
        for basin in sorted(unique_basins):
            basin_str = str(basin).zfill(3)  # Zero-pad to 3 digits (001, 002, etc.)
            basin_df = df[df['HYD_AREA'] == basin].copy()
            
            if len(basin_df) > 0:  # Only save if there's data
                parquet_filename = f'{basin_str}_{file_type}{file_prefix}.parquet'
                parquet_path = os.path.join(parquet_dir, parquet_filename)
                
                # Save as parquet
                basin_df.to_parquet(parquet_path, index=False, engine='pyarrow')
                parquet_files.append(parquet_path)
                
        print(f"Saved {len(parquet_files)} parquet files by basin to: {parquet_dir}")
        
        # Show example filenames
        if parquet_files:
            example_files = [os.path.basename(f) for f in parquet_files[:3]]
            print(f"Example parquet files: {example_files}")
    else:
        print("Warning: HYD_AREA column not found. Cannot create basin-specific parquet files.")
    
    return parquet_files


def save_results_by_county(df, output_dir, file_prefix='', file_type='field_summaries'):
    """
    Save county-split Parquet files in a 'parquet_county' subfolder.
    
    Parameters:
    - df: DataFrame to save
    - output_dir: Main output directory
    - file_prefix: Prefix for the main CSV file (e.g., 'GW_', 'SW_', etc.)
    - file_type: Type of file being saved (e.g., 'field_summaries', 'county_summaries')
    
    Returns:
    - parquet_files: List of saved parquet file paths
    """
    import os
    
    county_dir = os.path.join(output_dir, 'parquet_county')
    os.makedirs(county_dir, exist_ok=True)
    
    parquet_files = []
    if 'COUNTY_NAME' in df.columns:
        unique_counties = df['COUNTY_NAME'].dropna().unique()
        
        for county in sorted(unique_counties):
            county_str = str(county).replace(' ', '_').replace('/', '_')  # Replace spaces and slashes for filenames
            county_df = df[df['COUNTY_NAME'] == county].copy()
            
            if len(county_df) > 0:  # Only save if there's data
                parquet_filename = f'{county_str}_{file_type}{file_prefix}.parquet'
                parquet_path = os.path.join(county_dir, parquet_filename)
                
                # Save as parquet
                county_df.to_parquet(parquet_path, index=False, engine='pyarrow')
                parquet_files.append(parquet_path)
                
        print(f"Saved {len(parquet_files)} parquet files by county to: {county_dir}")
        
        # Show example filenames
        if parquet_files:
            example_files = [os.path.basename(f) for f in parquet_files[:3]]
            print(f"Example county parquet files: {example_files}")
    else:
        print("Warning: COUNTY_NAME column not found. Cannot create county-specific parquet files.")
    
    return parquet_files



def process_field_summaries(
    input_dir, 
    output_dir, 
    climatology_dir, 
    filter_irrigated=True,
    irrigation_filter_method='percentage',  # 'percentage' or 'threshold'
    fill_missing_data=True,
    moisture_carryforward=True,
    separate_gw_sw=False, 
    fields_shapefile_path=None,
    mixed_to_sw_ratio=0.5,  # Continuous ratio for static mode (0.0-1.0)
    allocation_mode='dynamic_uniform',  # 'static' or 'dynamic_uniform'
    spi_data=None,  # SPI data for dynamic_uniform allocation
    min_sw_ratio=0.2,  # Minimum SW allocation in dry years (dynamic_uniform)
    max_sw_ratio=0.8,  # Maximum SW allocation in wet years (dynamic_uniform)
    output_county_summaries=True,
    counties_shapefile_path=None,
    county_name_field='NAME',
    allocation_overrides_path=None,  # Basin-specific allocation overrides YAML
    awc_path=None,
    rooting_depth_m=1.5,
    fill_missing_irrigation_attributes=False,
    basin_codes=None,
):
    
    # Load basin allocation overrides (IRR_SRC reclassification)
    _allocation_overrides = None
    if allocation_overrides_path is not None:
        from allocation_utils import load_basin_allocation_overrides
        try:
            _allocation_overrides = load_basin_allocation_overrides(
                allocation_overrides_path
            )
            if _allocation_overrides:
                print(f"\nLoaded {len(_allocation_overrides)} basin allocation override(s) "
                      f"from: {allocation_overrides_path}")
                for bcode, gw_r in _allocation_overrides.items():
                    print(f"  Basin {bcode}: gw_ratio={gw_r}")
            else:
                print(f"\nNo basin overrides found in: {allocation_overrides_path}")
        except FileNotFoundError as e:
            print(f"\n⚠️  WARNING: {e}. Continuing without overrides.")
    
    gap_windows = [
        (1985, 1991), (1992, 1997), (1998, 2003),
        (2004, 2009), (2010, 2015), (2016, 2021), (2022, 2025) 
    ]

    if not filter_irrigated:
        output_dir = os.path.join(output_dir, 'all_fields')
    elif irrigation_filter_method == 'threshold':
        output_dir = os.path.join(output_dir, 'threshold')
    
    # Handle output directory naming based on allocation ratio
    if mixed_to_sw_ratio == 0.0:
        output_dir = os.path.join(output_dir, 'all_gw_fields')
    elif mixed_to_sw_ratio == 1.0:
        output_dir = os.path.join(output_dir, 'all_sw_fields')
    elif mixed_to_sw_ratio == 0.5:
        output_dir = os.path.join(output_dir, '50_50_gw_sw_fields')
    else:
        ratio_folder = f"ratio_{mixed_to_sw_ratio:.1f}".replace('.', 'p')
        output_dir = os.path.join(output_dir, ratio_folder)


    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(climatology_dir, exist_ok=True)

    # AWC is an optional dependency until carry-forward is requested.  Prefer
    # the OTTER static soil extraction; the legacy NV table is retained as a
    # fallback for older field sets.  Coverage is audited below rather than
    # silently assigning an arbitrary cap to missing fields.
    awc_lookup = None
    awc_join_key = None
    if moisture_carryforward:
        awc_candidates = []
        if awc_path:
            awc_candidates.append(Path(awc_path))
        else:
            awc_candidates.extend([
                PROJECT_ROOT / 'scripts/scripts_otter/data_folder/nwi_field_export/soils/soils_otter_static.csv',
                PROJECT_ROOT / 'data/nv_field_awc_ha.csv',
            ])
        awc_file = next((path for path in awc_candidates if path.exists()), None)
        if awc_file is None:
            raise FileNotFoundError(
                'Soil-moisture carry-forward requires a per-field AWC table; '
                f'checked {[str(path) for path in awc_candidates]}'
            )
        awc_source = pd.read_csv(awc_file, usecols=lambda col: col in {'NWI_ID', 'OPENET_ID', 'AWC'})
        if 'AWC' not in awc_source.columns:
            raise ValueError(f'AWC column missing from {awc_file}')
        if 'NWI_ID' in awc_source.columns:
            awc_join_key = 'NWI_ID'
        elif 'OPENET_ID' in awc_source.columns:
            awc_join_key = 'OPENET_ID'
        else:
            raise ValueError(f'AWC table {awc_file} has neither NWI_ID nor OPENET_ID')
        awc_source[awc_join_key] = _normalize_field_key(awc_source[awc_join_key])
        awc_source['AWC'] = pd.to_numeric(awc_source['AWC'], errors='coerce')
        awc_source = awc_source.dropna(subset=[awc_join_key, 'AWC'])
        awc_conflicts = awc_source.groupby(awc_join_key)['AWC'].nunique()
        if (awc_conflicts > 1).any():
            raise ValueError(f'AWC table {awc_file} has conflicting values for field IDs')
        awc_lookup = awc_source.drop_duplicates(awc_join_key).set_index(awc_join_key)['AWC']
        print(f'Using per-field AWC from: {awc_file} ({len(awc_lookup):,} fields)')

    # Load high-volume parquet inputs when present.  Legacy CSV directories
    # remain supported.  Parquet files are read recursively because the NWI
    # extractor partitions them as year=<YYYY>/...parquet.
    parquet_files = sorted(Path(input_dir).rglob('*.parquet'))
    csv_files = sorted(
        Path(input_dir).glob('*.csv')
    ) if not parquet_files else []
    files = parquet_files or csv_files
    if not files:
        print(f"No parquet or CSV files found in {input_dir}")
        return None
    input_kind = 'parquet' if parquet_files else 'CSV'
    print(f"Found {len(files)} {input_kind} files to process")
    print(f"Example files: {[str(f) for f in files[:5]]}")

    if parquet_files:
        dfs = [pd.read_parquet(file) for file in files if file.stat().st_size > 0]
    else:
        dfs = [pd.read_csv(file) for file in files
               if file.stat().st_size > 0 and file.name != 'all_with_temperature.csv']
    if basin_codes:
        basin_codes = {str(code).strip() for code in basin_codes}
        filtered_dfs = []
        for frame in dfs:
            if 'HYD_AREA' not in frame.columns:
                raise ValueError('Basin filtering requested but input has no HYD_AREA column')
            mask = frame['HYD_AREA'].astype(str).str.strip().isin(basin_codes)
            filtered_dfs.append(frame.loc[mask].copy())
        dfs = filtered_dfs
        print(f"Applied basin filter {sorted(basin_codes)} before concatenation")
    if not dfs: 
        print("No valid data in CSV files.")
        return None
    combined_df = pd.concat(dfs, ignore_index=True)
    # Defensive de-duplication in case a combined file or duplicate rows slip in
    before_rows = len(combined_df)
    combined_df = combined_df.drop_duplicates()
    dropped = before_rows - len(combined_df)
    if dropped > 0:
        print(f"Warning: dropped {dropped} duplicate rows after concatenation to prevent double counting.")
    print(f"Combined dataframe shape: {combined_df.shape}")

    # Generate HA_NAME_CODE using existing HYD_AREA columns (no override from shapefile)
    combined_df['HA_NAME_CODE'] = combined_df['HYD_AREA_N'].astype(str) + ' (' + combined_df['HYD_AREA'].astype(str) + ')'
    print(f"Generated HA_NAME_CODE format. Example values:")
    print(combined_df[['HYD_AREA', 'HYD_AREA_N', 'HA_NAME_CODE']].head())

    # Apply irrigation filter first (more efficient)
    if filter_irrigated:
        if irrigation_filter_method == 'percentage' and '%_IRRIGATED' in combined_df.columns:
            print(f"Applying percentage-based irrigation filter: > 50%")
            combined_df = combined_df[combined_df['%_IRRIGATED'] > 0.50].copy()
            print(f"Records after percentage irrigation filter: {len(combined_df)}")
            
        elif irrigation_filter_method == 'threshold':
            print(f"Applying ETa/ETO threshold-based irrigation filter")
            
            # Load basin-specific thresholds
            basin_thresholds, default_threshold = load_basin_specific_thresholds()
            
            # Apply threshold filtering
            combined_df = filter_historical_data_by_thresholds(
                combined_df, 
                basin_thresholds, 
                default_threshold
            )
            print(f"Records after threshold irrigation filter: {len(combined_df)}")
            
            if len(combined_df) == 0:
                print("Warning: No records remained after threshold filtering!")
                return None
                
        elif irrigation_filter_method == 'percentage':
            print("Warning: '%_IRRIGATED' column not found. Cannot apply percentage filter.")
            print("Available columns:", list(combined_df.columns))
            
        else:
            print(f"Unknown irrigation_filter_method: {irrigation_filter_method}")
            print("Valid options: 'percentage', 'threshold'")
            return None


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
                zero_count = (combined_df[col] == 0).sum()
                if zero_count > 0:
                    combined_df[col] = combined_df[col].replace(0, np.nan)
                    print(f"  ⚠️  CRITICAL: Also converted {zero_count} zero ET values to NaN (OpenET bug fix)")
                    print(f"  Total ETA_MM values flagged as missing: {before_count + zero_count}")

    # Helper: Build field-to-county mapping if requested
    field_to_county_df = None
    field_county_join_key = None
    if output_county_summaries:
        if fields_shapefile_path is None or counties_shapefile_path is None:
            print("Warning: County summaries requested but shapefile paths missing. County aggregation will be skipped.")
            output_county_summaries = False
        else:
            try:
                print("Building field-to-county mapping via spatial overlay ...")
                fields_gdf = gpd.read_file(fields_shapefile_path)
                field_county_join_key = (
                    'NWI_ID' if 'NWI_ID' in fields_gdf.columns
                    and 'NWI_ID' in combined_df.columns
                    else 'OPENET_ID'
                )
                if field_county_join_key not in fields_gdf.columns:
                    raise ValueError(
                        f"Boundary file has no usable county join key "
                        f"({field_county_join_key})"
                    )
                fields_gdf = fields_gdf[[field_county_join_key, 'geometry']].copy()
                counties_gdf = gpd.read_file(counties_shapefile_path)[[county_name_field, 'geometry']].copy()
                if fields_gdf.crs != counties_gdf.crs:
                    fields_gdf = fields_gdf.to_crs(counties_gdf.crs)
                # Use intersection areas to assign field to county with max overlap
                inter_gdf = gpd.overlay(fields_gdf, counties_gdf, how='intersection')
                inter_gdf['inter_area'] = inter_gdf.geometry.area
                inter_sorted = inter_gdf.sort_values([field_county_join_key, 'inter_area'], ascending=[True, False])
                field_to_county_df = inter_sorted.drop_duplicates(subset=[field_county_join_key])[[field_county_join_key, county_name_field]].rename(columns={county_name_field: 'COUNTY_NAME'})
                print(f"Mapped {len(field_to_county_df)} fields to counties")
            except Exception as e:
                print(f"Error creating field-to-county mapping: {e}. County aggregation will be skipped.")
                output_county_summaries = False

    # --- NEW: GW/SW Separation (if enabled) ---
    fieldsdf = None
    if separate_gw_sw:
        if fields_shapefile_path is None:
            raise ValueError("fields_shapefile_path must be provided when separate_gw_sw=True")
        
        # Log the allocation mode being used
        print(f"GW/SW Separation Mode: Using mixed_to_sw_ratio = {mixed_to_sw_ratio:.1f}")
        print(f"  Mixed field allocation: {(1-mixed_to_sw_ratio)*100:.0f}% to GW, {mixed_to_sw_ratio*100:.0f}% to SW")
        
        # CRITICAL: Merge county names into combined_df BEFORE GW/SW separation
        # This ensures combined_df_gw and combined_df_sw inherit COUNTY_NAME column
        if output_county_summaries and field_to_county_df is not None:
            print("Merging county names into combined_df before GW/SW separation...")
            combined_df = pd.merge(combined_df, field_to_county_df, on=field_county_join_key, how='left')
            print(f"Merged county data for {combined_df['COUNTY_NAME'].notna().sum()} fields")
        
        # Load field attributes
        fieldsdf = load_field_attributes(fields_shapefile_path)
        # Defensive enrichment: current high-volume exports carry these
        # properties, while older exports may omit some or all of them.
        # Backfill once before allocation and write the join QA report.
        attribute_report_path = os.path.join(output_dir, 'field_attribute_enrichment_report.csv')
        combined_df = merge_field_attributes(
            combined_df,
            fieldsdf,
            report_path=attribute_report_path,
            strict=True,
        )
        combined_df = enforce_boundary_basin_mapping(combined_df, fieldsdf, strict=True)
        print("Using legacy boundary Basin attribute for HYD_AREA/HA_NAME_CODE aggregation")
        if fill_missing_irrigation_attributes:
            combined_df = apply_irrigation_attribute_fallbacks(
                combined_df,
                report_path=attribute_report_path,
            )
        elif 'IRR_SRC' in combined_df.columns:
            # Preserve the historical projection behavior unless the opt-in
            # NWI fallback is enabled.
            combined_df['IRR_SRC'] = combined_df['IRR_SRC'].fillna(0)
        
        # FIRST: Calculate total field statistics BEFORE separation
        print("Calculating total field statistics before separation...")
        combined_df_with_attrs = merge_field_attributes(combined_df, fieldsdf)
        if not fill_missing_irrigation_attributes:
            combined_df_with_attrs['IRR_SRC'] = combined_df_with_attrs['IRR_SRC'].fillna(0)
        
        # Calculate total field counts by basin/month for percentage calculation
        combined_df_with_attrs['field_count'] = 1
        combined_df_with_attrs['is_gw_field'] = (combined_df_with_attrs['IRR_SRC'] == 1).astype(int)
        combined_df_with_attrs['is_sw_field'] = (combined_df_with_attrs['IRR_SRC'] == 2).astype(int)
        combined_df_with_attrs['is_mixed_field'] = (combined_df_with_attrs['IRR_SRC'] == 3).astype(int)
        
        # Apply allocation logic based on mixed_to_sw_ratio parameter
        # mixed_to_sw_ratio = 0.0 means 100% to GW, 0% to SW
        # mixed_to_sw_ratio = 0.5 means 50/50 split
        # mixed_to_sw_ratio = 1.0 means 0% to GW, 100% to SW
        combined_df_with_attrs['gw_field_equivalent'] = combined_df_with_attrs['is_gw_field'] + (1.0 - mixed_to_sw_ratio) * combined_df_with_attrs['is_mixed_field']
        combined_df_with_attrs['sw_field_equivalent'] = combined_df_with_attrs['is_sw_field'] + mixed_to_sw_ratio * combined_df_with_attrs['is_mixed_field']
        
        combined_df_with_attrs['total_all_fields'] = combined_df_with_attrs['is_gw_field'] + combined_df_with_attrs['is_sw_field'] + combined_df_with_attrs['is_mixed_field']
        
        # Aggregate total field statistics by basin/month
        total_field_stats = combined_df_with_attrs.groupby(['HA_NAME_CODE', 'YEAR', 'MONTH']).agg({
            'field_count': 'sum',
            'gw_field_equivalent': 'sum',
            'sw_field_equivalent': 'sum',
            'total_all_fields': 'sum'
        }).reset_index()
        
        # Calculate true percentages based on total fields
        total_field_stats['percent_gw_fields'] = np.where(
            total_field_stats['total_all_fields'] > 0,
            (total_field_stats['gw_field_equivalent'] / total_field_stats['total_all_fields']) * 100,
            0
        )
        total_field_stats['percent_sw_fields'] = np.where(
            total_field_stats['total_all_fields'] > 0,
            (total_field_stats['sw_field_equivalent'] / total_field_stats['total_all_fields']) * 100,
            0
        )
        
        print("Total field statistics calculated")

        # County-level total field statistics (for accurate percent_gw/sw after separation)
        total_field_stats_county = None
        if output_county_summaries and field_to_county_df is not None:
            try:
                # combined_df_with_attrs already has COUNTY_NAME from earlier merge to combined_df
                total_field_stats_county = combined_df_with_attrs.groupby(['COUNTY_NAME', 'YEAR', 'MONTH']).agg({
                    'field_count': 'sum',
                    'gw_field_equivalent': 'sum',
                    'sw_field_equivalent': 'sum',
                    'total_all_fields': 'sum'
                }).reset_index()
                total_field_stats_county['percent_gw_fields'] = np.where(
                    total_field_stats_county['total_all_fields'] > 0,
                    (total_field_stats_county['gw_field_equivalent'] / total_field_stats_county['total_all_fields']) * 100,
                    0
                )
                total_field_stats_county['percent_sw_fields'] = np.where(
                    total_field_stats_county['total_all_fields'] > 0,
                    (total_field_stats_county['sw_field_equivalent'] / total_field_stats_county['total_all_fields']) * 100,
                    0
                )
                print("County-level total field statistics calculated")
            except Exception as e:
                print(f"Warning: Could not compute county total field stats: {e}")
                total_field_stats_county = None
        
        # Apply GW/SW separation
        if allocation_mode == 'dynamic_uniform' and spi_data is not None:
            # DYNAMIC UNIFORM: year-by-year SPI-based allocation, 0.5 mean for all basins
            print("\n" + "="*80)
            print("Using DYNAMIC UNIFORM allocation (year-by-year based on SPI)")
            print("="*80)
            dynamic_cfg = {}
            try:
                dynamic_cfg = load_config().phase3.dynamic_mixed_allocation.to_dict()
            except AttributeError:
                dynamic_cfg = {}
            basin_config_dict = dynamic_cfg.get('basin_overrides', {})
            combined_df_gw, combined_df_sw, combined_df_mixed, basin_stats_df = apply_gw_sw_separation_dynamic(
                combined_df, fieldsdf, 
                basin_config_dict=basin_config_dict,
                spi_data=spi_data,
                min_sw_ratio=min_sw_ratio,
                max_sw_ratio=max_sw_ratio,
                use_uniform_mean_ratio=True,
                allocation_overrides=_allocation_overrides,
                spi_reference_month=int(dynamic_cfg.get('spi_reference_month', 9)),
            )
            
            # Save dynamic allocation statistics
            allocation_stats_file = os.path.join(output_dir, 'dynamic_allocation_stats.csv')
            basin_stats_df.to_csv(allocation_stats_file, index=False)
            print(f"\nDynamic allocation stats saved to: {allocation_stats_file}")

            # Recompute field-equivalent percentages using the exact same
            # basin-year ratios used to split the mixed acreage.  The legacy
            # pre-split block used a hard-coded 50/50 value.
            ratio_lookup = basin_stats_df[['basin_code', 'year', 'sw_ratio']].copy()
            ratio_lookup = ratio_lookup.rename(columns={'basin_code': 'HA_NAME_CODE', 'year': 'YEAR'})
            ratio_lookup['HA_NAME_CODE'] = ratio_lookup['HA_NAME_CODE'].astype(str)
            ratio_lookup = ratio_lookup.drop_duplicates(['HA_NAME_CODE', 'YEAR'])
            stats_frame = combined_df_with_attrs.copy()
            stats_frame = stats_frame.merge(ratio_lookup, on=['HA_NAME_CODE', 'YEAR'], how='left')
            fallback_by_basin = stats_frame['HA_NAME_CODE'].map({
                str(k): float(v.get('center_sw_ratio', v.get('fallback_ratio', 0.5)))
                for k, v in basin_config_dict.items()
                if isinstance(v, dict)
            }).fillna(0.5)
            stats_frame['sw_ratio'] = stats_frame['sw_ratio'].fillna(fallback_by_basin)
            stats_frame['gw_field_equivalent'] = (
                stats_frame['is_gw_field'] +
                (1.0 - stats_frame['sw_ratio']) * stats_frame['is_mixed_field']
            )
            stats_frame['sw_field_equivalent'] = (
                stats_frame['is_sw_field'] +
                stats_frame['sw_ratio'] * stats_frame['is_mixed_field']
            )
            stats_frame['total_all_fields'] = (
                stats_frame['is_gw_field'] + stats_frame['is_sw_field'] +
                stats_frame['is_mixed_field']
            )
            total_field_stats = stats_frame.groupby(['HA_NAME_CODE', 'YEAR', 'MONTH']).agg({
                'field_count': 'sum', 'gw_field_equivalent': 'sum',
                'sw_field_equivalent': 'sum', 'total_all_fields': 'sum'
            }).reset_index()
            total_field_stats['percent_gw_fields'] = np.where(
                total_field_stats['total_all_fields'] > 0,
                total_field_stats['gw_field_equivalent'] / total_field_stats['total_all_fields'] * 100,
                0,
            )
            total_field_stats['percent_sw_fields'] = np.where(
                total_field_stats['total_all_fields'] > 0,
                total_field_stats['sw_field_equivalent'] / total_field_stats['total_all_fields'] * 100,
                0,
            )
            if output_county_summaries and 'COUNTY_NAME' in stats_frame.columns:
                total_field_stats_county = stats_frame.groupby(['COUNTY_NAME', 'YEAR', 'MONTH']).agg({
                    'field_count': 'sum', 'gw_field_equivalent': 'sum',
                    'sw_field_equivalent': 'sum', 'total_all_fields': 'sum'
                }).reset_index()
                total_field_stats_county['percent_gw_fields'] = np.where(
                    total_field_stats_county['total_all_fields'] > 0,
                    total_field_stats_county['gw_field_equivalent'] / total_field_stats_county['total_all_fields'] * 100,
                    0,
                )
                total_field_stats_county['percent_sw_fields'] = np.where(
                    total_field_stats_county['total_all_fields'] > 0,
                    total_field_stats_county['sw_field_equivalent'] / total_field_stats_county['total_all_fields'] * 100,
                    0,
                )
            
        else:
            # STATIC: global allocation ratio applied uniformly to all years
            if allocation_mode == 'dynamic_uniform' and spi_data is None:
                print("\n⚠️  WARNING: dynamic_uniform requested but spi_data is None")
                print("   Falling back to static allocation")
            combined_df_gw, combined_df_sw, combined_df_mixed = apply_gw_sw_separation(
                combined_df, fieldsdf, mixed_to_sw_ratio=mixed_to_sw_ratio,
                allocation_overrides=_allocation_overrides
            )
        
        # Determine field suffix for output files
        field_suffix_sw = "_SW"
        field_suffix_gw = "_GW"
        
        # Process each component separately with appropriate suffix
        datasets_to_process = [
            ('GW', combined_df_gw, total_field_stats, field_suffix_gw),
            ('SW', combined_df_sw, total_field_stats, field_suffix_sw)
        ]
    else:
        # Process as single dataset (original behavior)
        datasets_to_process = [('ALL', combined_df, None, '')]
        total_field_stats_county = None

    # Process each dataset (GW, SW, or ALL)
    results = {}

    for dataset_info in datasets_to_process:
        if len(dataset_info) == 4:
            dataset_name, df_to_process, total_field_stats, field_suffix = dataset_info
        elif len(dataset_info) == 3:
            dataset_name, df_to_process, total_field_stats = dataset_info
            field_suffix = f"_{dataset_name}" if separate_gw_sw else ""
        else:
            dataset_name, df_to_process = dataset_info
            total_field_stats = None
            field_suffix = ""
        
        print(f"\n=== Processing {dataset_name} dataset ===")
        
        # Ensure HA_NAME_CODE is present (may be needed for separated datasets)
        if 'HA_NAME_CODE' not in df_to_process.columns:
            df_to_process['HA_NAME_CODE'] = df_to_process['HYD_AREA_N'].astype(str) + ' (' + df_to_process['HYD_AREA'].astype(str) + ')'

        if 'ETO_MM' not in df_to_process.columns:
            raise ValueError("ETO_MM column is required and missing from input CSVs.")
        
        # CRITICAL FIX: Load field attributes early so irr_eff is available for all paths
        if fields_shapefile_path:
            print("Loading field attributes for processing...")
            if fieldsdf is None:
                fieldsdf = load_field_attributes(fields_shapefile_path)
            if fieldsdf is not None:
                df_to_process = merge_field_attributes(df_to_process, fieldsdf)
                
                # Fill missing values
                if not fill_missing_irrigation_attributes:
                    if 'IRR_SRC' in df_to_process.columns:
                        df_to_process['IRR_SRC'] = df_to_process['IRR_SRC'].fillna(0)
                    if 'irr_eff' in df_to_process.columns:
                        df_to_process['irr_eff'] = df_to_process['irr_eff'].replace(0, np.nan).fillna(0.75)
                    else:
                        df_to_process['irr_eff'] = 0.75  # Default value
            else:
                # Set defaults if shapefile loading fails
                df_to_process['IRR_SRC'] = 0
                df_to_process['irr_eff'] = 0.75
        
        # Merge county names early for county-based parquet saving
        if field_to_county_df is not None and 'COUNTY_NAME' not in df_to_process.columns:
            print("Merging county names for county-based parquet partitioning...")
            df_to_process = pd.merge(df_to_process, field_to_county_df, on=field_county_join_key, how='left')
            print(f"Merged county data for {df_to_process['COUNTY_NAME'].notna().sum()} fields")
        
        # Initialize EToF and Interpolation Tracking Columns
        df_to_process['EToF'] = np.where(df_to_process['ETO_MM'] > 0, df_to_process['ETA_MM'] / df_to_process['ETO_MM'], np.nan)
        df_to_process['EToF_interpolated'] = df_to_process['EToF'].copy()
        df_to_process['interpolation_method'] = 'original'

        # --- STEP 1-4: Data preparation and intermediate calculations ---
        if fill_missing_data:
            # Step 1: Linear Interpolation
            print("Step 1: Applying linear interpolation for 1-month EToF gaps...")
            df_to_process = apply_linear_interpolation_etof(df_to_process, group_cols=['HA_NAME_CODE', 'OPENET_ID'])
            
            # Step 2: Climatology Interpolation
            print("Step 2: Checking for EToF climatologies for 2+ month gaps...")
            file_status = check_climatology_files_exist(climatology_dir, gap_windows)
            missing_climatologies = [window for window, status in file_status.items() if not status['exists']]
            
            if missing_climatologies:
                print(f"Missing EToF climatology files: {missing_climatologies}. Calculating...")
                calculate_etof_climatologies(input_dir=input_dir, output_dir=climatology_dir, gap_windows=gap_windows, group_cols=['HA_NAME_CODE', 'OPENET_ID'])
            
            climatologies = load_climatologies(climatology_dir, gap_windows)
            if climatologies:
                print("Applying climatology for 2+ month EToF gaps...")
                df_to_process = apply_climatology_gap_filling_etof(df_to_process, climatologies, gap_windows, group_cols=['HA_NAME_CODE', 'OPENET_ID'])
            else:
                print("No EToF climatologies available/loaded. Skipping 2+ month gap filling.")

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
            df_to_process['AR_vol_original_acre_ft'] = (df_to_process['AR_MM'] / 1000) * 3.281 * df_to_process['ACRES_FTR_GEOM']

        # --- STEPS 5-6: UNIFIED LOGIC FOR FINAL VOLUME CALCULATION ---
        print("Steps 5-6: Calculating final volumes using unified logic...")
        
        if fill_missing_data:
            if moisture_carryforward:
                if awc_join_key not in df_to_process.columns:
                    raise ValueError(f'{awc_join_key} column required to join per-field AWC')
                awc_values = _normalize_field_key(df_to_process[awc_join_key]).map(awc_lookup)
                if 'AWC' in df_to_process.columns:
                    df_to_process['AWC'] = df_to_process['AWC'].combine_first(awc_values)
                else:
                    df_to_process['AWC'] = awc_values
                missing_awc_fields = df_to_process.loc[
                    df_to_process['AWC'].isna(), awc_join_key
                ].nunique()
                if missing_awc_fields:
                    missing_awc_path = os.path.join(output_dir, 'missing_awc_fields.csv')
                    df_to_process.loc[
                        df_to_process['AWC'].isna(), [awc_join_key]
                    ].drop_duplicates().to_csv(missing_awc_path, index=False)
                    raise ValueError(
                        f'{missing_awc_fields:,} fields lack AWC; wrote '
                        f'{missing_awc_path} and stopped before winter carry-forward. '
                        'Extract/fill those IDs or explicitly decide not to use carry-forward.'
                    )
                # Use the carry-forward final netet/ar columns already calculated
                print("Using moisture carry-forward for final AR and NetET...")
                df_to_process = apply_winter_moisture_carryforward(
                    df_to_process,
                    eta_vol_intermediate_col='ETa_vol_intermediate_acre_ft',
                    net_et_vol_intermediate_col='NetET_vol_intermediate_acre_ft',
                    ar_original_vol_col='AR_vol_original_acre_ft',
                    irr_eff_col='irr_eff',  # Use field-level efficiency from shapefile
                    rooting_depth_m=rooting_depth_m,
                    year_reset_month=1,
                    group_cols=['HA_NAME_CODE', 'OPENET_ID']
                )
                # Set ETa final from intermediate
                df_to_process['ETa_final_acre_ft'] = df_to_process['ETa_vol_intermediate_acre_ft']
            else:
                # Gap-filled, but NOT doing moisture carry-forward
                print("Gap-filled, but no carry-forward. Calculating final volumes from interpolated data...")
                df_to_process['ETa_final_acre_ft'] = df_to_process['ETa_vol_intermediate_acre_ft']
                df_to_process['NetET_final_acre_ft'] = df_to_process['NetET_vol_intermediate_acre_ft']
                # irr_eff already loaded above, just recalculate AR
                df_to_process['AR_final_acre_ft'] = (
                    df_to_process['NetET_final_acre_ft'] / df_to_process['irr_eff']
                )
                # Set other columns for consistency
                df_to_process['Water_Surplus_Cumulative_acre_ft'] = 0
                df_to_process['Eff_PPT_Adjusted_acre_ft'] = df_to_process['Eff_PPT_vol_acre_ft']
        else:
            # NOT doing any gap filling, just using raw source data, but must fix negative values
            print("No filling of missing data. Using raw source/calculated ETa, NetET, AR (set negatives to zero)...")
            
            # Fix negatives in original _MM columns
            negative_ar_count = (df_to_process['AR_MM'] < 0).sum()
            if negative_ar_count > 0:
                print(f"Found {negative_ar_count} negative AR_MM values. Setting to zero.")
                df_to_process['AR_MM'] = df_to_process['AR_MM'].clip(lower=0)
            
            # Compute from raw MM columns (irr_eff already loaded above)
            df_to_process['ETa_final_acre_ft'] = (df_to_process['ETA_MM'] / 1000) * 3.281 * df_to_process['ACRES_FTR_GEOM']
            df_to_process['Eff_PPT_vol_acre_ft'] = (df_to_process['EFF_PPT_MM'] / 1000) * 3.281 * df_to_process['ACRES_FTR_GEOM']
            df_to_process['Eff_PPT_vol_acre_ft'] = df_to_process['Eff_PPT_vol_acre_ft'].fillna(0)
            df_to_process['NetET_final_acre_ft'] = df_to_process['ETa_final_acre_ft'] - df_to_process['Eff_PPT_vol_acre_ft']
            df_to_process['AR_final_acre_ft'] = df_to_process['NetET_final_acre_ft'] / df_to_process['irr_eff']

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

        # --- Calculate final rates ---
        print("Calculating final rates from final volumes...")
        df_to_process['AR_final_ft'] = df_to_process['AR_final_acre_ft'] / df_to_process['ACRES_FTR_GEOM'].replace(0, np.nan)
        df_to_process['NetET_final_ft'] = df_to_process['NetET_final_acre_ft'] / df_to_process['ACRES_FTR_GEOM'].replace(0, np.nan)
        df_to_process['ETa_final_ft'] = df_to_process['ETa_final_acre_ft'] / df_to_process['ACRES_FTR_GEOM'].replace(0, np.nan)
        
        for col in ['AR_final_ft', 'NetET_final_ft', 'ETa_final_ft']:
            df_to_process[col] = df_to_process[col].fillna(0)

        # NEW: Step 6.1: Calculate Raw PPT from Effective PPT
        print("Step 6.1: Calculating raw PPT from effective PPT...")

        # Convert effective precipitation back to raw precipitation
        # EFF_PPT = 0.85 * PPT, so PPT = EFF_PPT / 0.85
        df_to_process['PPT_MM'] = df_to_process['EFF_PPT_MM'] / 0.85

        # Calculate raw PPT volume 
        df_to_process['PPT_vol_acre_ft'] = (df_to_process['PPT_MM'] / 1000) * 3.281 * df_to_process['ACRES_FTR_GEOM']

        # Calculate raw PPT rate
        df_to_process['PPT_final_ft'] = df_to_process['PPT_vol_acre_ft'] / df_to_process['ACRES_FTR_GEOM'].replace(0, np.nan)
        df_to_process['PPT_final_ft'] = df_to_process['PPT_final_ft'].fillna(0)

        print(f"Raw PPT calculated. Sample values: {df_to_process['PPT_MM'].head()}")

        # Save field-level results (CSV and Parquet by basin)
        # field_suffix is now passed from datasets_to_process tuple
        if not fill_missing_data:
            field_suffix += "_unfilled"
            
        field_output_file = os.path.join(output_dir, f'field_summaries_EToF_final{field_suffix}.csv')
        df_to_process.to_csv(field_output_file, index=False)
        print(f"Field summaries saved to: {field_output_file}")

        # Save parquet files by basin
        parquet_files = save_results_by_basin(
            df_to_process, 
            output_dir, 
            file_prefix=field_suffix, 
            file_type='field_summaries'
        )
        
        # Save county-based parquet files for field summaries
        if field_to_county_df is not None:
            county_parquet_files = save_results_by_county(
                df_to_process, 
                output_dir, 
                file_prefix=field_suffix, 
                file_type='field_summaries'
            )
        
        # --- Step 6.5: Add field count and SW/GW classification ---
        print("Step 6.5: Adding field count and SW/GW classification...")

        # Field attributes already loaded above, so just create classification columns
        df_to_process['field_count'] = 1
        df_to_process['is_gw_field'] = (df_to_process['IRR_SRC'] == 1).astype(int)
        df_to_process['is_sw_field'] = (df_to_process['IRR_SRC'] == 2).astype(int)
        df_to_process['is_mixed_field'] = (df_to_process['IRR_SRC'] == 3).astype(int)
        
        # Calculate total fields including all types
        df_to_process['total_all_fields'] = df_to_process['is_gw_field'] + df_to_process['is_sw_field'] + df_to_process['is_mixed_field']
        
        df_to_process['gw_field_equivalent'] = df_to_process['is_gw_field'] + 0.5 * df_to_process['is_mixed_field']
        df_to_process['sw_field_equivalent'] = df_to_process['is_sw_field'] + 0.5 * df_to_process['is_mixed_field']

        # --- Step 7: Aggregating by HA ---
        print("Step 7: Aggregating by HA...")

        agg_dict = {}
        sum_cols_for_ha = [
            'ETa_final_acre_ft',
            'NetET_final_acre_ft',
            'AR_final_acre_ft',
            'ACRES_FTR_GEOM',
            'field_count',
            'gw_field_equivalent',
            'sw_field_equivalent',
            'total_all_fields'
        ]

        # Add rate variables that should be area-weighted averaged (not summed)
        rate_cols_for_ha = ['ETO_MM', 'EFF_PPT_MM', 'PPT_MM', 'tmin', 'tmax']

        for col in df_to_process.columns:
            if col in sum_cols_for_ha:
                agg_dict[col] = 'sum'
            elif col in rate_cols_for_ha:
                # Area-weighted average for rate variables
                agg_dict[col] = lambda x: np.average(x, weights=df_to_process.loc[x.index, 'ACRES_FTR_GEOM'])
                agg_dict[col] = 'mean'
            elif df_to_process[col].dtype == 'object' or col in ['YEAR', 'MONTH', 'HA_NAME_CODE', 'OPENET_ID', 'HYD_AREA_N', 'HYD_AREA', 'STATE']:
                agg_dict[col] = 'first'
            elif pd.api.types.is_datetime64_any_dtype(df_to_process[col]): 
                agg_dict[col] = 'first'
            elif pd.api.types.is_numeric_dtype(df_to_process[col]): 
                agg_dict[col] = 'sum'
            else: 
                agg_dict[col] = 'first'

        for key_col in ['HA_NAME_CODE', 'YEAR', 'MONTH']:
            if key_col in agg_dict: 
                del agg_dict[key_col]
                
        grouped_df = df_to_process.groupby(['HA_NAME_CODE', 'YEAR', 'MONTH'], as_index=False).agg(agg_dict)
        # County summaries are optional.  The NWI field-processing run does
        # not use county projection inputs and can omit this aggregation.
        county_df = None
        if output_county_summaries and 'COUNTY_NAME' in df_to_process.columns:
            county_df = df_to_process.groupby(['COUNTY_NAME', 'YEAR', 'MONTH'], as_index=False).agg(agg_dict)

        # Calculate the new columns after aggregation
        grouped_df['num_fields_irrigated'] = grouped_df['field_count']

        # Use pre-calculated total field statistics for percentages
        if total_field_stats is not None:
            # Merge with the pre-calculated HA-level total field statistics
            grouped_df = pd.merge(
                grouped_df, 
                total_field_stats[['HA_NAME_CODE', 'YEAR', 'MONTH', 'percent_gw_fields', 'percent_sw_fields']], 
                on=['HA_NAME_CODE', 'YEAR', 'MONTH'], 
                how='left'
            )
            # For county-level merging, prefer the separately computed county totals if available
            if county_df is not None and 'total_field_stats_county' in locals() and total_field_stats_county is not None:
                county_df = pd.merge(
                    county_df,
                    total_field_stats_county[['COUNTY_NAME', 'YEAR', 'MONTH', 'percent_gw_fields', 'percent_sw_fields']],
                    on=['COUNTY_NAME', 'YEAR', 'MONTH'],
                    how='left'
                )
            else:
                # No precomputed county totals available; percent columns will be calculated from county_df below
                pass
            print(f"Merged with total field statistics for {dataset_name}")
        else:
            # Fallback for non-separated processing - calculate percentages correctly
            grouped_df['percent_sw_fields'] = np.where(
                grouped_df['total_all_fields'] > 0,
                (grouped_df['sw_field_equivalent'] / grouped_df['total_all_fields']) * 100,
                0
            )
            grouped_df['percent_gw_fields'] = np.where(
                grouped_df['total_all_fields'] > 0,
                (grouped_df['gw_field_equivalent'] / grouped_df['total_all_fields']) * 100,
                0
            )
            if county_df is not None:
                county_df['percent_sw_fields'] = np.where(
                     county_df['total_all_fields'] > 0,
                    (county_df['sw_field_equivalent'] / county_df['total_all_fields']) * 100,
                    0
                )
                county_df['percent_gw_fields'] = np.where(
                    county_df['total_all_fields'] > 0,
                    (county_df['gw_field_equivalent'] / county_df['total_all_fields']) * 100,
                    0
                )


        # Drop intermediate calculation columns
        columns_to_drop = ['gw_field_equivalent', 'sw_field_equivalent', 'total_all_fields']
        grouped_df = grouped_df.drop(columns=[col for col in columns_to_drop if col in grouped_df.columns])

        print("Recalculating area-averaged rates after HA aggregation...")
        grouped_df['ETa_final_ft'] = grouped_df['ETa_final_acre_ft'] / grouped_df['ACRES_FTR_GEOM'].replace(0, np.nan)
        grouped_df['NetET_final_ft'] = grouped_df['NetET_final_acre_ft'] / grouped_df['ACRES_FTR_GEOM'].replace(0, np.nan)
        grouped_df['AR_final_ft'] = grouped_df['AR_final_acre_ft'] / grouped_df['ACRES_FTR_GEOM'].replace(0, np.nan)

        print("Recalculating area-averaged rates after county aggregation...")
        if county_df is not None:
            county_df['ETa_final_ft'] = county_df['ETa_final_acre_ft'] / county_df['ACRES_FTR_GEOM'].replace(0, np.nan)
            county_df['NetET_final_ft'] = county_df['NetET_final_acre_ft'] / county_df['ACRES_FTR_GEOM'].replace(0, np.nan)
            county_df['AR_final_ft'] = county_df['AR_final_acre_ft'] / county_df['ACRES_FTR_GEOM'].replace(0, np.nan)

        # Fill any NaN rates with 0
        rate_columns = ['ETa_final_ft', 'NetET_final_ft', 'AR_final_ft']
        for col in rate_columns:
            if col in grouped_df.columns:
                grouped_df[col] = grouped_df[col].fillna(0)
                if county_df is not None and col in county_df.columns:
                    county_df[col] = county_df[col].fillna(0)

        # Ensure HA_NAME_CODE is set correctly in the final format
        grouped_df['HA_NAME_CODE'] = grouped_df['HYD_AREA_N'].astype(str) + ' (' + grouped_df['HYD_AREA'].astype(str) + ')'
        print(f"Final HA_NAME_CODE format: {grouped_df['HA_NAME_CODE'].head()}")

        # Save HA summaries (CSV and Parquet by basin)
        ha_output_file = os.path.join(output_dir, f'ha_summaries_EToF_final{field_suffix}.csv')
        grouped_df.to_csv(ha_output_file, index=False)
        print(f"HA summaries with EToF interpolation and final columns saved to: {ha_output_file}")

        # Save parquet files by basin
        ha_parquet_files = save_results_by_basin(
            grouped_df, 
            output_dir, 
            file_prefix=field_suffix, 
            file_type='ha_summaries'
        )
        
        # Save county-based parquet files for HA summaries (if county data is available)
        # Ungroup HA data to export county-wide summary
        if field_to_county_df is not None and 'COUNTY_NAME' in county_df.columns:
            # Save true county-wide summary (CSV and parquet)
            counties_dir = os.path.join(output_dir, 'counties')
            os.makedirs(counties_dir, exist_ok=True)
            countywide_csv = os.path.join(counties_dir, f'county_wide_summaries_EToF_final{field_suffix}.csv')
            countywide_parquet = os.path.join(counties_dir, f'county_wide_summaries_EToF_final{field_suffix}.parquet')
            county_df.to_csv(countywide_csv, index=False)
            county_df.to_parquet(countywide_parquet, index=False)
            print(f"County-wide (all basins summed) summaries saved to:\n  {countywide_csv}\n  {countywide_parquet}")
        else:
            print("True county-wide summaries not created: COUNTY_NAME missing in grouped_df.")
    

        if field_to_county_df is not None and 'COUNTY_NAME' in county_df.columns:
            ha_county_parquet_files = save_results_by_county(
                county_df, 
                output_dir, 
                file_prefix=field_suffix, 
                file_type='ha_summaries'
            )

        # VERIFICATION: Check that the new columns are present
        print(f"\nNew columns added to HA summaries:")
        final_cols = ['num_fields_irrigated', 'percent_sw_fields', 'percent_gw_fields']
        for col in final_cols:
            if col in grouped_df.columns:
                print(f"✓ {col}: present")
            else:
                print(f"✗ {col}: MISSING")

        # Show example of new data
        if len(grouped_df) > 0:
            print(f"\nExample data for first basin:")
            example_basin = grouped_df['HA_NAME_CODE'].iloc[0]
            example_data = grouped_df[grouped_df['HA_NAME_CODE'] == example_basin].head(3)
            print(example_data[['HA_NAME_CODE', 'YEAR', 'MONTH', 'num_fields_irrigated', 'percent_sw_fields', 'percent_gw_fields']])

            # Debug interpolation results for ETA_MM (the only variable we interpolate)
            print(f"\n{dataset_name} ETA_MM Interpolation Summary:")
            print("=" * 60)
            interpolation_counts = df_to_process['interpolation_method'].value_counts()
            total_records = len(df_to_process)
            for method, count in interpolation_counts.items():
                pct = (count / total_records) * 100
                print(f"  {method:20s}: {count:8,} records ({pct:5.2f}%)")
            
            # Show missing data counts for other variables (not interpolated)
            print(f"\n{dataset_name} Missing Data Counts (Other Variables):")
            print("=" * 60)
            other_vars = ['PPT_MM', 'EFF_PPT_MM', 'ETO_MM', 'AR_MM', 'NDVI', 'tmin', 'tmax']
            for var in other_vars:
                if var in df_to_process.columns:
                    missing_count = df_to_process[var].isna().sum()
                    missing_pct = (missing_count / total_records) * 100
                    if missing_count > 0:
                        print(f"  {var:20s}: {missing_count:8,} missing ({missing_pct:5.2f}%)")
                    else:
                        print(f"  {var:20s}: No missing data")

            results[dataset_name] = {
                'field_file': field_output_file,
                'ha_file': ha_output_file,
                'interpolation_counts': interpolation_counts
            }

        # # --- Optional: County aggregation and output ---
        if output_county_summaries and field_to_county_df is not None:
            print("\nStep 7.6: Aggregating by County...")
            try:
                df_with_county = pd.merge(df_to_process, field_to_county_df, on=field_county_join_key, how='left')
                print("After merge, columns present:", df_with_county.columns.tolist())
                # Defensive: always ensure correct county column
                if 'COUNTY_NAME' not in df_with_county.columns:
                    alt_cols = [col for col in df_with_county.columns if 'county' in col.lower()]
                    if alt_cols:
                        print(f"Renaming county column {alt_cols[0]} to 'COUNTY_NAME' for aggregation.")
                        df_with_county['COUNTY_NAME'] = df_with_county[alt_cols[0]]
                    else:
                        raise RuntimeError("County aggregation failed: 'COUNTY_NAME' column not found, available columns: " + str(df_with_county.columns.tolist()))

                agg_dict_county = {}
                sum_cols_for_county = [
                    'ETa_final_acre_ft',
                    'NetET_final_acre_ft',
                    'AR_final_acre_ft',
                    'ACRES_FTR_GEOM',
                    'field_count',
                    'gw_field_equivalent',
                    'sw_field_equivalent',
                    'total_all_fields'
                ]
                rate_cols_for_county = ['ETO_MM', 'EFF_PPT_MM', 'PPT_MM', 'tmin', 'tmax']
                for col in df_with_county.columns:
                    if col in sum_cols_for_county:
                        agg_dict_county[col] = 'sum'
                    elif col in rate_cols_for_county:
                        agg_dict_county[col] = 'mean'
                    elif df_with_county[col].dtype == 'object' or col in ['YEAR', 'MONTH', 'COUNTY_NAME', 'OPENET_ID', 'HYD_AREA_N', 'HYD_AREA', 'STATE']:
                        agg_dict_county[col] = 'first'
                    elif pd.api.types.is_datetime64_any_dtype(df_with_county[col]):
                        agg_dict_county[col] = 'first'
                    elif pd.api.types.is_numeric_dtype(df_with_county[col]):
                        agg_dict_county[col] = 'sum'
                    else:
                        agg_dict_county[col] = 'first'

                for key_col in ['COUNTY_NAME', 'YEAR', 'MONTH']:
                    if key_col in agg_dict_county:
                        del agg_dict_county[key_col]

                county_grouped_df = df_with_county.groupby(['COUNTY_NAME', 'YEAR', 'MONTH'], as_index=False).agg(agg_dict_county)
                county_grouped_df['num_fields_irrigated'] = county_grouped_df['field_count']

                # Use county-level totals if available for accurate percentages when separating
                if separate_gw_sw and total_field_stats_county is not None:
                    county_grouped_df = pd.merge(
                        county_grouped_df,
                        total_field_stats_county[['COUNTY_NAME', 'YEAR', 'MONTH', 'percent_gw_fields', 'percent_sw_fields']],
                        on=['COUNTY_NAME', 'YEAR', 'MONTH'],
                        how='left'
                    )
                else:
                    county_grouped_df['percent_sw_fields'] = np.where(
                        county_grouped_df['total_all_fields'] > 0,
                        (county_grouped_df['sw_field_equivalent'] / county_grouped_df['total_all_fields']) * 100,
                        0
                    )
                    county_grouped_df['percent_gw_fields'] = np.where(
                        county_grouped_df['total_all_fields'] > 0,
                        (county_grouped_df['gw_field_equivalent'] / county_grouped_df['total_all_fields']) * 100,
                        0
                    )

                # Drop intermediate calculation columns
                columns_to_drop_c = ['gw_field_equivalent', 'sw_field_equivalent', 'total_all_fields']
                county_grouped_df = county_grouped_df.drop(columns=[c for c in columns_to_drop_c if c in county_grouped_df.columns])

                # Recalculate area-averaged rates after county aggregation
                county_grouped_df['ETa_final_ft'] = county_grouped_df['ETa_final_acre_ft'] / county_grouped_df['ACRES_FTR_GEOM'].replace(0, np.nan)
                county_grouped_df['NetET_final_ft'] = county_grouped_df['NetET_final_acre_ft'] / county_grouped_df['ACRES_FTR_GEOM'].replace(0, np.nan)
                county_grouped_df['AR_final_ft'] = county_grouped_df['AR_final_acre_ft'] / county_grouped_df['ACRES_FTR_GEOM'].replace(0, np.nan)
                for col in ['ETa_final_ft', 'NetET_final_ft', 'AR_final_ft']:
                    if col in county_grouped_df.columns:
                        county_grouped_df[col] = county_grouped_df[col].fillna(0)

                # Save to counties subfolder with same filename pattern as HA summaries
                counties_dir = os.path.join(output_dir, 'counties')
                os.makedirs(counties_dir, exist_ok=True)
                county_output_file = os.path.join(counties_dir, f'ha_summaries_EToF_final{field_suffix}.csv')
                county_grouped_df.to_csv(county_output_file, index=False)
                print(f"County summaries saved to: {county_output_file}")
                
                # Save county-based parquet files for county summaries
                county_summary_parquet_files = save_results_by_county(
                    county_grouped_df, 
                    counties_dir, 
                    file_prefix=field_suffix, 
                    file_type='county_summaries'
                )

                # Add to results dict
                if dataset_name in results:
                    results[dataset_name]['county_file'] = county_output_file
                else:
                    results[dataset_name] = {
                        'field_file': field_output_file,
                        'ha_file': ha_output_file,
                        'county_file': county_output_file
                    }
            except Exception as e:
                print(f"Warning: County aggregation failed: {e}")
        
    return results

if __name__ == "__main__":
    import argparse
    
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description='Process field summaries and generate HA-level aggregations with SW/GW separation.'
    )
    parser.add_argument('--input-dir', type=str, help='Input directory containing field summary CSV files')
    parser.add_argument('--output-dir', type=str, help='Output directory for processed summaries')
    parser.add_argument('--climatology-dir', type=str, help='Directory containing climatology files')
    parser.add_argument('--fields-shapefile', type=str, help='Path to fields shapefile')
    parser.add_argument('--counties-shapefile', type=str, help='Path to counties shapefile')
    parser.add_argument('--no-county-summaries', action='store_true',
                        help='Skip county mapping and county summary outputs')
    parser.add_argument('--awc-file', type=str,
                        help='Per-field AWC CSV used to cap winter soil-moisture storage')
    parser.add_argument('--rooting-depth-m', type=float, default=1.5,
                        help='Rooting depth used for AWC storage cap in metres')
    parser.add_argument('--assume-missing-irrigation-attributes', action='store_true',
                        help='Opt in to fill missing IRR_SRC as GW and irr_eff as 0.75, with audit flags')
    parser.add_argument('--moisture-carryforward', action='store_true',
                        help='Enable AWC-capped winter soil-moisture carry-forward')
    parser.add_argument('--mode', type=str, choices=['static', 'dynamic_uniform'], default='dynamic_uniform',
                        help='Allocation mode: static (fixed ratio) or dynamic_uniform (SPI-driven year-by-year)')
    parser.add_argument('--mixed-ratio', type=float, default=0.5, help='Mixed field to SW ratio for static mode (0.0-1.0)')
    parser.add_argument('--config', type=str, help='Path to custom config file')
    parser.add_argument('--environment', type=str, choices=['hpc', 'collaborator'])
    parser.add_argument('--basin-codes', nargs='+',
                        help='Restrict processing to explicit HYD_AREA codes')
    
    args = parser.parse_args()
    
    # Load configuration
    cfg = load_config(config_path=args.config, environment=args.environment, reload=args.config or args.environment)
    
    # Get paths from config with CLI overrides
    input_dir_main = args.input_dir or str(Path(cfg.paths.irrmapper_input_dir))
    output_dir_main = args.output_dir or str(Path(cfg.paths.field_summaries_base))
    climatology_dir_main = args.climatology_dir or str(Path(cfg.paths.climatology_dir))
    fields_shapefile_path = args.fields_shapefile or str(Path(cfg.paths.fields_shapefile))
    configured_counties = getattr(cfg.paths, 'county_shapefile', None)
    configured_awc = getattr(cfg.paths, 'awc_file', None)
    configured_irrigation_fallback = bool(
        getattr(cfg.paths, 'assume_missing_irrigation_attributes', False)
    )
    counties_shapefile_path = args.counties_shapefile or (
        str(Path(configured_counties)) if configured_counties else None
    )
    output_county_summaries = not args.no_county_summaries and counties_shapefile_path is not None
    county_name_field = 'NAMELSAD'
    
    # Basin allocation overrides YAML (IRR_SRC reclassification)
    allocation_overrides_path = None
    if hasattr(cfg.paths, 'basin_allocation_overrides'):
        _override_path = str(Path(cfg.paths.basin_allocation_overrides))
        if os.path.exists(_override_path):
            allocation_overrides_path = _override_path
            print(f"Using basin allocation overrides: {allocation_overrides_path}")
        else:
            print(f"Basin allocation overrides file not found (skipping): {_override_path}")
    
    if not os.path.exists(output_dir_main):
        os.makedirs(output_dir_main)
    
    # Set execution mode and mixed-to-SW ratio from CLI arguments
    execution_mode = args.mode
    mixed_to_sw_ratio = args.mixed_ratio

    # Verify fields shapefile exists
    if not os.path.exists(fields_shapefile_path):
        raise FileNotFoundError(f"Fields shapefile not found: {fields_shapefile_path}") 
    print(f"Using fields shapefile: {fields_shapefile_path}")
    # Verify counties shapefile if provided
    if counties_shapefile_path and not os.path.exists(counties_shapefile_path):
        raise FileNotFoundError(f"Counties shapefile not found: {counties_shapefile_path}")
    if counties_shapefile_path:
        print(f"Using counties shapefile: {counties_shapefile_path}")
    
    print(f"\nExecution mode: {execution_mode}\n")

    if execution_mode == 'dynamic_uniform':
        # ====================================================================
        # DYNAMIC UNIFORM MODE (DEFAULT)
        # ====================================================================
        # Year-by-year SPI-based allocation with UNIFORM 50/50 mean for ALL basins.
        # All basins use SPI_12m, mean ratio 0.5, range 0.2-0.8.
        
        print("="*80)
        print("DYNAMIC UNIFORM ALLOCATION MODE")
        print("  All basins: SPI_12m, mean ratio 0.5, range 0.2 (dry) to 0.8 (wet)")
        print("="*80)
        
        # Load SPI data
        spi_file = str(Path(cfg.paths.drought_indices_file))
        
        if not os.path.exists(spi_file):
            print(f"\n⚠️  ERROR: SPI data file not found: {spi_file}")
            print("Dynamic allocation requires SPI data. Aborting.")
            sys.exit(1)
        
        print(f"\nLoading SPI data from: {spi_file}")
        spi_data = pd.read_csv(spi_file)
        print(f"Loaded SPI data: {len(spi_data)} records, {spi_data['HA_NAME_CODE'].nunique()} basins")
        
        print("\nProcessing with DYNAMIC UNIFORM allocation...")
        result = process_field_summaries(
            input_dir_main,
            output_dir_main,
            climatology_dir_main,
            filter_irrigated=irrigation_filter,
            irrigation_filter_method=irrigation_filter_method,
            fill_missing_data=fill_missing_data,
            moisture_carryforward=(args.moisture_carryforward or moisture_carryforward),
            separate_gw_sw=True,
            fields_shapefile_path=fields_shapefile_path,
            allocation_mode='dynamic_uniform',
            spi_data=spi_data,
            min_sw_ratio=0.2,
            max_sw_ratio=0.8,
            output_county_summaries=output_county_summaries,
            counties_shapefile_path=counties_shapefile_path,
            county_name_field=county_name_field,
            allocation_overrides_path=allocation_overrides_path,
            awc_path=args.awc_file or configured_awc,
            rooting_depth_m=args.rooting_depth_m,
            fill_missing_irrigation_attributes=(
                args.assume_missing_irrigation_attributes or configured_irrigation_fallback
            ),
            basin_codes=args.basin_codes,
        )
        
        if result:
            print("\n" + "="*80)
            print("Dynamic uniform processing completed successfully!")
            print("="*80)
            for dataset_name, files in result.items():
                print(f"{dataset_name}: {files}")
            stats_file = os.path.join(output_dir_main, '50_50_gw_sw_fields', 'dynamic_allocation_stats.csv')
            if os.path.exists(stats_file):
                print(f"\nDynamic allocation stats: {stats_file}")
        else:
            print("Processing failed.")
        
    else:
        # ====================================================================
        # STATIC MODE
        # ====================================================================
        # Use mixed_to_sw_ratio parameter for continuous control
        # Examples:
        #   0.0 = All mixed fields to GW
        #   0.5 = 50/50 split (default)
        #   1.0 = All mixed fields to SW
        
        print("Starting field summaries processing (static allocation mode)...")
        result = process_field_summaries(
            input_dir_main, 
            output_dir_main, 
            climatology_dir_main, 
            filter_irrigated=irrigation_filter,
            irrigation_filter_method=irrigation_filter_method,
            fill_missing_data=fill_missing_data,
            moisture_carryforward=(args.moisture_carryforward or moisture_carryforward),
            separate_gw_sw=separate_gw_sw, 
            fields_shapefile_path=fields_shapefile_path,
            mixed_to_sw_ratio=mixed_to_sw_ratio,
            allocation_mode='static',
            output_county_summaries=output_county_summaries,
            counties_shapefile_path=counties_shapefile_path,
            county_name_field=county_name_field,
            allocation_overrides_path=allocation_overrides_path,
            awc_path=args.awc_file or configured_awc,
            rooting_depth_m=args.rooting_depth_m,
            fill_missing_irrigation_attributes=(
                args.assume_missing_irrigation_attributes or configured_irrigation_fallback
            ),
            basin_codes=args.basin_codes,
        )

        if result: 
            print("Processing completed successfully!")
            for dataset_name, files in result.items():
                print(f"{dataset_name}: {files}")
        else: 
            print("Processing failed.")
