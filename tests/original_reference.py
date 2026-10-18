"""
Verbatim copies of the original agricultural-water-forecasting functions
(scripts/phase1_data_preparation/process_field_summaries.py, commit 58819af), used only by
tests/test_fast_equivalence.py to check that the faster NWI T1 versions give the same results.
"""

import os

import numpy as np
import pandas as pd

from allocation_utils import spi_to_sw_ratio

FIELD_ATTRIBUTE_COLUMNS = [
    'NWI_ID', 'NWI_PARENT', 'OPENET_ID', 'GRIDMET_ID', 'Basin', 'Acres',
    'STATE', 'IRR_SRC', 'irr_eff', 'ITYPE',
]


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
