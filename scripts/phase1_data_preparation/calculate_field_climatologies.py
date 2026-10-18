"""
Calculate field-level EToF climatologies for gap-filling.

This script generates climatological averages for field-level evapotranspiration
fractions (EToF) to support gap-filling of missing satellite data.

CONFIGURATION:
All paths and parameters loaded from config/nwi_t1_final.yaml

NWI T1 edits (marked "NWI T1" below):
  * Input is the year-partitioned OpenET ENSEMBLE parquet export instead of a folder of
    field-summary CSVs.
  * HA_NAME_CODE comes from the boundary layer's Basin attribute, the same hydrographic
    area the field processor uses, so climatology rows match the rows they fill.
  * Windows come from etof.climatology_windows; files go to run/01_inputs/etof_climatology/.
  * The unused ETrF climatology function and the ETrF file check are removed.

Author: Peter ReVelle
        With AI assistance from Claude, Gemini, ChatGPT, and other models
Date: 2024-2025
Updated: January 13, 2026 - Unified configuration system
"""

import os
import sys
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import warnings

# NWI T1: run configuration
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from nwi_config import load_config, RunPaths  # noqa: E402


def calculate_etof_climatologies(input_dir, output_dir, gap_windows=None, group_cols=['HA_NAME_CODE', 'OPENET_ID'],
                                 field_basins=None):
    """
    Calculate 10-year EToF climatologies for each field and gap-filling window.
    
    Parameters:
    input_dir: Directory containing the year-partitioned ENSEMBLE parquet files (NWI T1)
    output_dir: Directory to save climatology files
    gap_windows: List of tuples defining gap-filling windows
    group_cols: List of columns to group by for processing
    field_basins: DataFrame of NWI_ID and HA_NAME_CODE from the boundary layer (NWI T1)
    
    Returns:
    Dictionary of climatology file paths for each window
    """
    if gap_windows is None:
        gap_windows = [
            (1985, 1991), (1992, 1997), (1998, 2003),
            (2004, 2009), (2010, 2015), (2016, 2021), (2022, 2025)
        ]
    
    os.makedirs(output_dir, exist_ok=True)
    climatology_files = {}
    
    # NWI T1: read the year-partitioned ENSEMBLE parquet export
    files = sorted(Path(input_dir).rglob('*.parquet'))
    
    if not files:
        print(f"No parquet files found in {input_dir}")
        return {}
    
    print(f"Loading {len(files)} files for climatology calculation...")
    
    columns = ['OPENET_ID', 'NWI_ID', 'YEAR', 'MONTH', 'ETA_MM', 'ETO_MM']
    dfs = [pd.read_parquet(file, columns=columns) for file in files]
    combined_df = pd.concat(dfs, ignore_index=True)
    combined_df['MONTH'] = combined_df['MONTH'].astype(int)
    combined_df['YEAR'] = combined_df['YEAR'].astype(int)
    print(f"Loaded {len(combined_df)} total records")
    
    # ========================================
    # ✅ CRITICAL: CLEAN DATA BEFORE CALCULATING CLIMATOLOGIES
    # ========================================
    print("Cleaning data before climatology calculation...")
    
    # 1. Convert missing values (-999) to NaN
    var_cols = ['ETA_MM', 'ETO_MM']
    for col in var_cols:
        if col in combined_df.columns:
            missing_count = (combined_df[col] == -999).sum()
            if missing_count > 0:
                combined_df[col] = combined_df[col].replace(-999, np.nan)
                print(f"  Converted {missing_count} missing values (-999) to NaN in {col}")
            
            # 2. Replace zero ETA values (OpenET bug)
            if col == 'ETA_MM':
                zero_count = (combined_df[col] == 0).sum()
                if zero_count > 0:
                    combined_df[col] = combined_df[col].replace(0, np.nan)
                    print(f"  Converted {zero_count} zero values to NaN in {col} (OpenET bug fix)")
    
    # 3. Calculate EToF if not already present
    if 'EToF' not in combined_df.columns:
        combined_df['EToF'] = np.where(
            combined_df['ETO_MM'] > 0, 
            combined_df['ETA_MM'] / combined_df['ETO_MM'], 
            np.nan
        )
    
    # 4. ✅ CRITICAL: Clip EToF to physically valid range (0 to 1.5)
    combined_df['EToF'] = combined_df['EToF'].clip(lower=0, upper=1.5)
    
    # 5. Remove invalid EToF values
    invalid_mask = (combined_df['EToF'] < 0) | (combined_df['EToF'] > 1.5) | combined_df['EToF'].isna()
    invalid_count = invalid_mask.sum()
    combined_df = combined_df[~invalid_mask].copy()
    
    if invalid_count > 0:
        print(f"  Removed {invalid_count} records with invalid EToF values")
    
    print(f"Records with valid EToF for climatology: {len(combined_df)}")
    
    # NWI T1: hydrographic area from the boundary layer's Basin attribute (as in the processor)
    combined_df = combined_df.merge(field_basins, on='NWI_ID', how='left', validate='many_to_one')
    
    # Calculate climatologies for each window
    for start_year, end_year in gap_windows:
        print(f"\nCalculating climatologies for {start_year}-{end_year} window...")
        
        # Filter data for this window
        window_data = combined_df[
            (combined_df['YEAR'] >= start_year) & 
            (combined_df['YEAR'] <= end_year)
        ].copy()
        
        if len(window_data) == 0:
            print(f"  No data found for window {start_year}-{end_year}")
            continue
        
        print(f"  Found {len(window_data)} records in this window")
        
        # Calculate monthly climatologies for each field
        climatology_data = []
        
        for month in range(1, 13):
            month_data = window_data[window_data['MONTH'] == month]
            
            if len(month_data) == 0:
                continue
            
            # Calculate mean EToF for each field for this month
            month_climo = month_data.groupby(group_cols)['EToF'].mean().reset_index()
            month_climo['MONTH'] = month
            month_climo = month_climo.rename(columns={'EToF': f'EToF_climo_{month:02d}'})
            
            climatology_data.append(month_climo)
        
        if not climatology_data:
            print(f"  No climatology data calculated for {start_year}-{end_year}")
            continue
        
        # Pivot to wide format (Oregon style)
        combined_climo = climatology_data[0][group_cols].copy()
        
        for month_climo in climatology_data:
            month_num = month_climo['MONTH'].iloc[0]
            col_name = f'EToF_climo_{month_num:02d}'
            combined_climo = pd.merge(
                combined_climo,
                month_climo[group_cols + [col_name]],
                on=group_cols,
                how='outer'
            )
        
        # ✅ FINAL VALIDATION: Clip all climatology columns to valid range
        for month in range(1, 13):
            col_name = f'EToF_climo_{month:02d}'
            if col_name in combined_climo.columns:
                combined_climo[col_name] = combined_climo[col_name].clip(lower=0, upper=1.5)
        
        # Save climatology file
        climo_filename = f'etof_climatology_{start_year}_{end_year}.csv'
        climo_filepath = os.path.join(output_dir, climo_filename)
        
        # Set OPENET_ID as index for Oregon-style format
        if 'OPENET_ID' in combined_climo.columns:
            combined_climo = combined_climo.set_index('OPENET_ID')
        
        combined_climo.to_csv(climo_filepath)
        climatology_files[f'{start_year}_{end_year}'] = climo_filepath
        
        print(f"  Saved climatology for {len(combined_climo)} fields to {climo_filename}")
    
    print(f"\nClimatology calculation complete. Generated {len(climatology_files)} files.")
    return climatology_files


def load_field_basins(fields_gpkg):
    """
    NWI T1: hydrographic area of every field from the boundary layer.

    Parameters
    ----------
    fields_gpkg : str
        2026-08-20 NWI boundary layer.

    Returns
    -------
    pd.DataFrame
        NWI_ID and HA_NAME_CODE ('Name (code)' parsed from the Basin attribute, e.g.
        '101_CarsonDesert' -> 'CarsonDesert (101)').
    """
    import geopandas as gpd
    fields = gpd.read_file(fields_gpkg, columns=['NWI_ID', 'Basin'], ignore_geometry=True)
    parts = fields['Basin'].astype('string').str.extract(r'^([^_]+)_(.*)$')
    fields['HA_NAME_CODE'] = parts[1].str.strip() + ' (' + parts[0].str.strip() + ')'
    fields['NWI_ID'] = fields['NWI_ID'].astype(str)
    return fields[['NWI_ID', 'HA_NAME_CODE']]


if __name__ == "__main__":
    # NWI T1: all settings come from config/nwi_t1_final.yaml
    cfg = load_config()
    paths = RunPaths(cfg).make()
    windows = [tuple(w) for w in cfg['etof']['climatology_windows']]

    print("="*80)
    print("CALCULATE FIELD CLIMATOLOGIES")
    print("="*80)
    print(f"Input directory: {cfg['inputs']['ensemble_dir']}")
    print(f"Output directory: {paths.etof_climatology}")
    print("="*80 + "\n")

    climatology_files = calculate_etof_climatologies(
        input_dir=cfg['inputs']['ensemble_dir'],
        output_dir=str(paths.etof_climatology),
        gap_windows=windows,
        group_cols=['HA_NAME_CODE', 'OPENET_ID'],
        field_basins=load_field_basins(cfg['inputs']['fields_gpkg']),
    )

    if climatology_files:
        print("\n" + "="*80)
        print("Climatology calculation completed successfully!")
        print("="*80)
        for window, filepath in climatology_files.items():
            print(f"  {window}: {filepath}")
    else:
        print("\n⚠️  No climatology files were created.")
