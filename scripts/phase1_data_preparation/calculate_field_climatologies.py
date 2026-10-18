"""
Calculate field-level EToF climatologies for gap-filling.

This script generates climatological averages for field-level evapotranspiration
fractions (EToF) to support gap-filling of missing satellite data.

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
import numpy as np
from pathlib import Path
from datetime import datetime
import warnings

# Add project root to path for config import
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Import unified configuration
from config import load_config


def calculate_etof_climatologies(input_dir, output_dir, gap_windows=None, group_cols=['HA_NAME_CODE', 'OPENET_ID']):
    """
    Calculate 10-year EToF climatologies for each field and gap-filling window.
    
    Parameters:
    input_dir: Directory containing field summary CSV files
    output_dir: Directory to save climatology files
    gap_windows: List of tuples defining gap-filling windows
    group_cols: List of columns to group by for processing
    
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
    
    # Load all available data files
    files = [os.path.join(input_dir, f) for f in os.listdir(input_dir) if f.endswith('.csv')]
    
    if not files:
        print(f"No CSV files found in {input_dir}")
        return {}
    
    print(f"Loading {len(files)} files for climatology calculation...")
    
    # Combine all data
    dfs = []
    for file in files:
        try:
            df = pd.read_csv(file)
            dfs.append(df)
        except Exception as e:
            print(f"Error loading {file}: {e}")
            continue
    
    if not dfs:
        print("No valid CSV files could be loaded")
        return {}
    
    combined_df = pd.concat(dfs, ignore_index=True)
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
    
    # Create HA_NAME_CODE if not present
    if 'HA_NAME_CODE' not in combined_df.columns:
        combined_df['HA_NAME_CODE'] = combined_df['HYD_AREA_N'].astype(str) + ' (' + combined_df['HYD_AREA'].astype(str) + ')'
    
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


def calculate_etrf_climatologies(input_dir, output_dir, gap_windows=None, group_cols=['HA_NAME_CODE', 'OPENET_ID']):
    """
    Calculate 10-year ETr   F climatologies for each field and gap-filling window.
    
    Parameters:
    input_dir: Directory containing field summary CSV files
    output_dir: Directory to save climatology files
    gap_windows: List of tuples defining gap-filling windows
    group_cols: List of columns to group by for processing
    
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
    
    # Load all available data files
    files = [os.path.join(input_dir, f) for f in os.listdir(input_dir) if f.endswith('.csv')]
    
    if not files:
        print(f"No CSV files found in {input_dir}")
        return {}
    
    print(f"Loading {len(files)} files for climatology calculation...")
    
    # Combine all data
    dfs = []
    for file in files:
        try:
            df = pd.read_csv(file)
            dfs.append(df)
        except Exception as e:
            print(f"Error loading {file}: {e}")
            continue
    
    if not dfs:
        print("No valid CSV files could be loaded")
        return {}
    
    combined_df = pd.concat(dfs, ignore_index=True)
    
    # Calculate EToF if not already present
    if 'ETrF' not in combined_df.columns:
        combined_df['ETrF'] = np.where(
            combined_df['ETR_MM'] > 0, 
            combined_df['ETA_MM'] / combined_df['ETR_MM'], 
            np.nan
        )
    
    # Create HA_NAME_CODE if not present
    if 'HA_NAME_CODE' not in combined_df.columns:
        combined_df['HA_NAME_CODE'] = combined_df['HYD_AREA_N'].astype(str) + ' (' + combined_df['HYD_AREA'].astype(str) + ')'
    
    for start_year, end_year in gap_windows:
        print(f"Calculating climatologies for {start_year}-{end_year} window...")
        
        # Filter data for this window
        window_data = combined_df[
            (combined_df['YEAR'] >= start_year) & 
            (combined_df['YEAR'] <= end_year)
        ].copy()
        
        if len(window_data) == 0:
            print(f"No data found for window {start_year}-{end_year}")
            continue
        
        # Calculate monthly climatologies for each field
        climatology_data = []
        
        for month in range(1, 13):
            month_data = window_data[window_data['MONTH'] == month]
            
            if len(month_data) == 0:
                continue
                
            # Calculate mean EToF for each field for this month
            month_climo = month_data.groupby(group_cols)['ETrF'].mean().reset_index()
            month_climo['MONTH'] = month
            month_climo = month_climo.rename(columns={'ETrF': f'ETrF_climo_{month:02d}'})
            
            climatology_data.append(month_climo)
        
        if not climatology_data:
            print(f"No climatology data calculated for {start_year}-{end_year}")
            continue
        
        # Pivot to wide format (Oregon style)
        combined_climo = climatology_data[0][group_cols].copy()
        
        for month_climo in climatology_data:
            month_num = month_climo['MONTH'].iloc[0]
            col_name = f'ETrF_climo_{month_num:02d}'
            combined_climo = pd.merge(
                combined_climo,
                month_climo[group_cols + [col_name]],
                on=group_cols,
                how='outer'
            )
        
        # Save climatology file
        climo_filename = f'etrf_climatology_{start_year}_{end_year}.csv'
        climo_filepath = os.path.join(output_dir, climo_filename)
        
        # Set OPENET_ID as index for Oregon-style format
        if 'OPENET_ID' in combined_climo.columns:
            combined_climo = combined_climo.set_index('OPENET_ID')
        
        combined_climo.to_csv(climo_filepath)
        climatology_files[f'{start_year}_{end_year}'] = climo_filepath
        
        print(f"Saved climatology for {len(combined_climo)} fields in {start_year}-{end_year} to {climo_filename}")
    
    return climatology_files



def check_climatology_files_exist(climatology_dir, gap_windows=None):
    """
    Check if climatology files exist for all gap-filling windows.
    
    Returns:
    dict: Dictionary with window keys and file existence status
    """
    if gap_windows is None:
        gap_windows = [
            (1985, 1991), (1992, 1997), (1998, 2003),
            (2004, 2009), (2010, 2015), (2016, 2021), (2022, 2025)
        ]
    
    file_status = {}
    
    for start_year, end_year in gap_windows:
        climo_filename = f'etrf_climatology_{start_year}_{end_year}.csv'
        climo_filepath = os.path.join(climatology_dir, climo_filename)
        
        file_status[f'{start_year}_{end_year}'] = {
            'exists': os.path.exists(climo_filepath),
            'filepath': climo_filepath
        }
    
    return file_status

if __name__ == "__main__":
    import argparse
    
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description='Calculate field-level EToF climatologies for gap-filling.'
    )
    parser.add_argument(
        '--input-dir',
        type=str,
        required=True,
        help='Directory containing field summary CSV files'
    )
    parser.add_argument(
        '--output-dir',
        type=str,
        help='Directory to save climatology files (default: {input_dir}/../climatologies)'
    )
    parser.add_argument(
        '--config',
        type=str,
        help='Path to custom config file (default: config/workflow_config.yaml)'
    )
    parser.add_argument(
        '--environment',
        type=str,
        choices=['hpc', 'collaborator'],
        help='Environment-specific overrides (hpc, collaborator)'
    )
    
    args = parser.parse_args()
    
    # Load configuration (even though this script is mostly standalone, loads for consistency)
    cfg = load_config(
        config_path=args.config,
        environment=args.environment,
        reload=args.config or args.environment
    )
    
    # Determine paths
    input_directory = args.input_dir
    
    if args.output_dir:
        climatology_directory = args.output_dir
    else:
        # Default: ../climatologies relative to input directory
        climatology_directory = str(Path(input_directory).parent / 'climatologies')
    
    print("="*80)
    print("CALCULATE FIELD CLIMATOLOGIES")
    print("="*80)
    print(f"Input directory: {input_directory}")
    print(f"Output directory: {climatology_directory}")
    print("="*80 + "\n")
    
    print("Calculating EToF climatologies...")
    climatology_files = calculate_etof_climatologies(
        input_dir=input_directory,
        output_dir=climatology_directory,
        group_cols=['HA_NAME_CODE', 'OPENET_ID']
    )
    
    if climatology_files:
        print("\n" + "="*80)
        print("Climatology calculation completed successfully!")
        print("="*80)
        for window, filepath in climatology_files.items():
            print(f"  {window}: {filepath}")
    else:
        print("\n⚠️  No climatology files were created.")
