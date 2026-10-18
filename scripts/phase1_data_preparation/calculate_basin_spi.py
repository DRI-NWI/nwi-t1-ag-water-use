#!/usr/bin/env python3
"""
Aggregate basin climate data and calculate SPI for Nevada basin training.

This script:
1. Loads field-level monthly GridMET summaries (1979-1984) when available
2. Falls back to existing basin-level HA summaries when early GridMET exports are unavailable
3. Merges any early GridMET data with basin HA summaries
4. Calculates SPI for all configured timescales using the full available period
5. Outputs drought indices to the config-managed Phase 2 input path

CONFIGURATION:
All paths and parameters loaded from config/workflow_config.yaml

Author: Peter ReVelle
Date: November 20, 2025
Updated: September 16, 2026 - Nevada/NDWR production configuration
"""

import os
import sys
import pandas as pd
import numpy as np
from pathlib import Path
import logging

# Add project root to path for config import
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Import unified configuration
from config import load_config

# Setup logging
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION FROM UNIFIED CONFIG FILE
# ============================================================================

def get_script_config():
    """Load configuration for this script."""
    cfg = load_config()
    
    # For compatibility, use SW summaries (climate is shared across SW/GW)
    ha_summaries_file = cfg.paths.ha_summaries_sw
    
    return {
        'gridmet_dir': Path(cfg.paths.gridmet_dir),
        'ha_summaries_file': Path(ha_summaries_file),
        'output_dir': Path(cfg.paths.drought_data_dir),
        'drought_indices_file': Path(cfg.paths.drought_indices_file),
        'spi_timescales': cfg.phase2.mixed_effects.spi_timescales,
        'historical_start_year': cfg.phase1.climate_extension.historical_start_year,
        'historical_end_year': cfg.phase1.climate_extension.historical_end_year,
        'spi_calibration_start_year': cfg.phase1.climate_extension.spi_calibration_start_year,
        'spi_calibration_end_year': cfg.phase1.climate_extension.spi_calibration_end_year,
    }

# Load config at module level
config = get_script_config()

GRIDMET_DIR = config['gridmet_dir']
HA_SUMMARIES_FILE = config['ha_summaries_file']
OUTPUT_DIR = config['output_dir']
DROUGHT_INDICES_FILE = config['drought_indices_file']
SPI_TIMESCALES = config['spi_timescales']
SPI_CALIBRATION_START = config['spi_calibration_start_year']
SPI_CALIBRATION_END = config['spi_calibration_end_year']

# Ensure output directory exists
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Field to basin mapping (global variable)
FIELD_TO_BASIN_MAP = None


# ============================================================================
# FIELD-TO-BASIN MAPPING
# ============================================================================

def load_field_to_basin_mapping(ha_summaries_file=None):
    """
    Load mapping from OPENET_ID to basin (HA_NAME_CODE).
    
    If HA summaries file is provided, extracts mapping from there.
    Otherwise, will use HYD_AREA_N from GridMET files.
    
    Parameters
    ----------
    ha_summaries_file : Path, optional
        Path to HA summaries CSV containing OPENET_ID and HA_NAME_CODE
    
    Returns
    -------
    dict : Mapping from OPENET_ID to HA_NAME_CODE (basin code)
    """
    global FIELD_TO_BASIN_MAP
    
    if ha_summaries_file and os.path.exists(ha_summaries_file):
        logger.info(f"Loading field-to-basin mapping from {ha_summaries_file}")
        
        try:
            # Load only needed columns to save memory
            ha_df = pd.read_csv(
                ha_summaries_file, 
                usecols=['OPENET_ID', 'HA_NAME_CODE'],
                low_memory=False
            )
            
            # Create mapping (OPENET_ID -> HA_NAME_CODE)
            FIELD_TO_BASIN_MAP = dict(
                zip(ha_df['OPENET_ID'], ha_df['HA_NAME_CODE'])
            )
            
            logger.info(f"✓ Loaded {len(FIELD_TO_BASIN_MAP)} field-to-basin mappings")
            logger.info(f"  Unique basins: {len(set(FIELD_TO_BASIN_MAP.values()))}")
            
            # Show sample mappings
            sample_items = list(FIELD_TO_BASIN_MAP.items())[:3]
            logger.info(f"  Sample mappings: {sample_items}")
            
        except ValueError as e:
            logger.info(f"No OPENET_ID mapping available in HA summaries: {e}")
            logger.info("Proceeding without field-to-basin mapping (basin summaries already available)")
            FIELD_TO_BASIN_MAP = None
        except Exception as e:
            logger.error(f"Error loading mapping: {e}")
            FIELD_TO_BASIN_MAP = None
    
    else:
        logger.warning("No HA summaries file provided - will use HYD_AREA_N as basin identifier")
        FIELD_TO_BASIN_MAP = None
    
    return FIELD_TO_BASIN_MAP


# ============================================================================
# FIELD-TO-BASIN AGGREGATION
# ============================================================================

def aggregate_field_to_basin_monthly(field_df, basin_col='HYD_AREA_N'):
    """
    Aggregate field-level data to basin-level monthly precipitation.
    Assumes field_df has ACRES_FTR_GEOM, YEAR, MONTH, PPT_MM (or ppt_mm),
    and either HYD_AREA_N or OPENET_ID + FIELD_TO_BASIN_MAP.
    """

    # If field-to-basin mapping exists, apply it (from HA summaries)
    if FIELD_TO_BASIN_MAP:
        field_df['HA_NAME_CODE'] = field_df['OPENET_ID'].map(FIELD_TO_BASIN_MAP)
        basin_col = 'HA_NAME_CODE'
        logger.info("Applied field-to-basin mapping using HA_NAME_CODE")

    # Remove fields with no basin assignment
    if basin_col not in field_df.columns:
        logger.error(f"{basin_col} column not found in field_df")
        return pd.DataFrame()

    valid_df = field_df[field_df[basin_col].notna()].copy()

    if len(valid_df) == 0:
        logger.error("No valid basin assignments found!")
        return pd.DataFrame()

    # Normalize column names: only care about precipitation
    col_mapping = {}
    for col in valid_df.columns:
        col_lower = col.lower()
        if col_lower == 'ppt_mm':
            col_mapping[col] = 'PPT_MM'

    valid_df = valid_df.rename(columns=col_mapping)

    if 'PPT_MM' not in valid_df.columns:
        logger.error("PPT_MM (or ppt_mm) column not found after renaming.")
        return pd.DataFrame()

    # Handle -999 (or other negative sentinels) as NaN, keep rows
    n_sentinel = (valid_df['PPT_MM'] < 0).sum()
    if n_sentinel > 0:
        logger.info(f"  Found {n_sentinel} sentinel values in PPT_MM - setting to NaN")
        valid_df.loc[valid_df['PPT_MM'] < 0, 'PPT_MM'] = np.nan

    logger.info(f"  Aggregating {len(valid_df)} field records to basin level")
    logger.info(f"    Unique fields: {valid_df['OPENET_ID'].nunique()}")
    logger.info(f"    Unique basins: {valid_df[basin_col].nunique()}")

    # Area-weighted precipitation
    valid_df['PPT_MM_weighted'] = valid_df['PPT_MM'] * valid_df['ACRES_FTR_GEOM']
    valid_df['PPT_area'] = valid_df['PPT_MM'].notna() * valid_df['ACRES_FTR_GEOM']

    basin_monthly = (
        valid_df
        .groupby([basin_col, 'YEAR', 'MONTH'])
        .agg({
            'PPT_MM_weighted': 'sum',
            'PPT_area': 'sum',
            'ACRES_FTR_GEOM': 'sum',   # total basin area this month
            'OPENET_ID': 'count'       # number of fields contributing
        })
        .reset_index()
    )

    # Area-weighted mean PPT
    basin_monthly['PPT_MM'] = basin_monthly['PPT_MM_weighted'] / basin_monthly['PPT_area']

    # Rename and select output columns
    basin_monthly.rename(columns={basin_col: 'HA_NAME_CODE',
                                  'OPENET_ID': 'N_FIELDS'}, inplace=True)

    basin_monthly = basin_monthly[[
        'HA_NAME_CODE', 'YEAR', 'MONTH',
        'PPT_MM',
        'ACRES_FTR_GEOM', 'N_FIELDS'
    ]]

    # Log missing precipitation at basin-month level
    n_missing = basin_monthly['PPT_MM'].isna().sum()
    if n_missing > 0:
        logger.warning(f"  PPT_MM: {n_missing}/{len(basin_monthly)} basin-months with all fields missing")

    logger.info(f"  ✓ Aggregated to {len(basin_monthly)} basin-month records")

    return basin_monthly


# ============================================================================
# DATA LOADING
# ============================================================================

def load_and_aggregate_gridmet_years(gridmet_dir, year_start=1979, year_end=1984):
    """
    Load all GridMET field summaries and aggregate to basin level.
    
    Parameters
    ----------
    gridmet_dir : Path
        Directory containing fb_gridmet_monthly_summaries_YYYY_YYYY.csv files
    year_start : int
        First year to process
    year_end : int
        Last year to process
    
    Returns
    -------
    pd.DataFrame : Combined basin-level monthly data for all years
    """
    
    logger.info(f"\nLoading GridMET data for {year_start}-{year_end}...")
    
    all_data = []
    
    for year in range(year_start, year_end + 1):
        filename = f"fb_gridmet_monthly_summaries_{year}_{year}.csv"
        filepath = gridmet_dir / filename
        
        if not filepath.exists():
            logger.warning(f"  File not found: {filename}")
            continue
        
        logger.info(f"  Loading {filename}...")
        
        try:
            # Load field-level data
            field_data = pd.read_csv(filepath, low_memory=False)
            
            logger.info(f"    {len(field_data)} field records")

            basin_level_required = {'HYD_AREA', 'YEAR', 'MONTH', 'PPT_MM', 'ACRES_FTR_GEOM'}
            if basin_level_required.issubset(field_data.columns):
                basin_data = field_data.copy()
                basin_name_col = 'HYD_AREA_N' if 'HYD_AREA_N' in basin_data.columns else 'HYD_AREA'
                basin_data['HA_NAME_CODE'] = basin_data[basin_name_col]
                if 'N_FIELDS' not in basin_data.columns:
                    basin_data['N_FIELDS'] = np.nan
                basin_data = basin_data[
                    ['HA_NAME_CODE', 'YEAR', 'MONTH', 'PPT_MM', 'ACRES_FTR_GEOM', 'N_FIELDS']
                ].copy()
                basin_data = (
                    basin_data
                    .sort_values(['HA_NAME_CODE', 'YEAR', 'MONTH'])
                    .drop_duplicates(subset=['HA_NAME_CODE', 'YEAR', 'MONTH'], keep='last')
                    .reset_index(drop=True)
                )
                logger.info("    Detected pre-aggregated basin GridMET export; using PPT_MM directly")
            else:
                # Aggregate to basin level
                basin_data = aggregate_field_to_basin_monthly(field_data)
            
            if not basin_data.empty:
                all_data.append(basin_data)
            
        except Exception as e:
            logger.error(f"  Error processing {filename}: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            continue
    
    if not all_data:
        logger.error("No data loaded!")
        return pd.DataFrame()
    
    # Combine all years
    combined = pd.concat(all_data, ignore_index=True)
    combined = combined.sort_values(['HA_NAME_CODE', 'YEAR', 'MONTH'])
    
    logger.info(f"\n✓ Combined GridMET data:")
    logger.info(f"  {len(combined)} records")
    logger.info(f"  {combined['HA_NAME_CODE'].nunique()} basins")
    logger.info(f"  Date range: {combined['YEAR'].min()}-{combined['YEAR'].max()}")
    
    return combined


def load_and_aggregate_ha_summaries(ha_summaries_file, year_start=1985):
    """
    Load existing HA summaries (1985+) and aggregate to basin-monthly format.
    
    Parameters
    ----------
    ha_summaries_file : Path
        Path to all_gw_fieldsha_summaries_EToF_final.csv
    year_start : int
        First year to include (default: 1985)
    
    Returns
    -------
    pd.DataFrame : Basin-level monthly data from HA summaries
    """
    
    logger.info(f"\nLoading HA summaries from {ha_summaries_file}...")
    
    if not os.path.exists(ha_summaries_file):
        logger.error(f"HA summaries file not found: {ha_summaries_file}")
        return pd.DataFrame()
    
    try:
        # Load HA summaries
        ha_df = pd.read_csv(ha_summaries_file, low_memory=False)
        
        logger.info(f"  Loaded {len(ha_df)} records")
        
        # Filter to year_start and later
        ha_df = ha_df[ha_df['YEAR'] >= year_start].copy()
        
        logger.info(f"  Filtered to {year_start}+: {len(ha_df)} records")
        
        basin_level_required = {'HA_NAME_CODE', 'YEAR', 'MONTH', 'PPT_MM', 'ACRES_FTR_GEOM'}
        if basin_level_required.issubset(ha_df.columns):
            basin_monthly = ha_df.copy()
            if 'N_FIELDS' not in basin_monthly.columns:
                if 'field_count' in basin_monthly.columns:
                    basin_monthly['N_FIELDS'] = basin_monthly['field_count']
                elif 'num_fields_irrigated' in basin_monthly.columns:
                    basin_monthly['N_FIELDS'] = basin_monthly['num_fields_irrigated']
                else:
                    basin_monthly['N_FIELDS'] = np.nan

            basin_monthly = basin_monthly[
                ['HA_NAME_CODE', 'YEAR', 'MONTH', 'PPT_MM', 'ACRES_FTR_GEOM', 'N_FIELDS']
            ].copy()
            basin_monthly = (
                basin_monthly
                .sort_values(['HA_NAME_CODE', 'YEAR', 'MONTH'])
                .drop_duplicates(subset=['HA_NAME_CODE', 'YEAR', 'MONTH'], keep='last')
                .reset_index(drop=True)
            )
            logger.info("  Detected pre-aggregated basin HA summaries; using PPT_MM directly")
        else:
            basin_monthly = aggregate_field_to_basin_monthly(ha_df)
        
        logger.info(f"\n✓ HA summaries aggregated:")
        logger.info(f"  {len(basin_monthly)} records")
        logger.info(f"  {basin_monthly['HA_NAME_CODE'].nunique()} basins")
        logger.info(f"  Date range: {basin_monthly['YEAR'].min()}-{basin_monthly['YEAR'].max()}")
        
        return basin_monthly
        
    except Exception as e:
        logger.error(f"Error loading HA summaries: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return pd.DataFrame()


def merge_gridmet_and_ha_summaries(gridmet_data, ha_data):
    """Debug version - show what's happening"""
    
    logger.info("\n=== MERGE DEBUG ===")
    
    # Check basin overlap
    gridmet_basins = set(gridmet_data['HA_NAME_CODE'].unique())
    ha_basins = set(ha_data['HA_NAME_CODE'].unique())
    
    common_basins = gridmet_basins.intersection(ha_basins)
    gridmet_only = gridmet_basins - ha_basins
    ha_only = ha_basins - gridmet_basins
    
    logger.info(f"GridMET basins: {len(gridmet_basins)}")
    logger.info(f"HA basins: {len(ha_basins)}")
    logger.info(f"Common basins: {len(common_basins)}")
    logger.info(f"GridMET only: {len(gridmet_only)} - {list(gridmet_only)[:5]}")
    logger.info(f"HA only: {len(ha_only)} - {list(ha_only)[:5]}")
    
    # Combine
    combined = pd.concat([gridmet_data, ha_data], ignore_index=True)
    combined = combined.sort_values(['HA_NAME_CODE', 'YEAR', 'MONTH'])
    
    # Check Amargosa Desert specifically
    test_basin = 'Carson Desert (101)'
    
    gridmet_test = gridmet_data[gridmet_data['HA_NAME_CODE'] == test_basin]
    ha_test = ha_data[ha_data['HA_NAME_CODE'] == test_basin]
    combined_test = combined[combined['HA_NAME_CODE'] == test_basin]
    
    logger.info(f"\n{test_basin} debug:")
    logger.info(f"  GridMET: {len(gridmet_test)} records, years {gridmet_test['YEAR'].min()}-{gridmet_test['YEAR'].max() if len(gridmet_test) > 0 else 'N/A'}")
    logger.info(f"  HA: {len(ha_test)} records, years {ha_test['YEAR'].min()}-{ha_test['YEAR'].max()}")
    logger.info(f"  Combined: {len(combined_test)} records, years {combined_test['YEAR'].min()}-{combined_test['YEAR'].max()}")
    
    return combined


# ============================================================================
# SPI CALCULATION
# ============================================================================

def calculate_basin_spi_extended(basin_climate_df, output_file, timescales=[3, 6, 12, 24, 36, 48, 60],
                                  calibration_start=None, calibration_end=None):
    """
    Calculate SPI for extended time period (1979-2023).
    
    Uses climate_indices package to calculate SPI for each basin and timescale.
    Gamma distribution is calibrated on the specified calibration period
    (default: from config spi_calibration_start/end - historical obs only).
    SPI values are computed for ALL years using the calibrated parameters.
    
    Parameters
    ----------
    basin_climate_df : pd.DataFrame
        Basin-level monthly climate data
    output_file : Path
        Output CSV path for results
    timescales : list of int
        SPI timescales to calculate (months)
    calibration_start : int, optional
        First year for gamma calibration (default: SPI_CALIBRATION_START from config)
    calibration_end : int, optional
        Last year for gamma calibration (default: SPI_CALIBRATION_END from config)
    
    Returns
    -------
    pd.DataFrame : Basin climate data with SPI columns added
    """
    from climate_indices import indices, compute
    import warnings
    warnings.filterwarnings('ignore')
    
    # Use config calibration period if not explicitly overridden
    if calibration_start is None:
        calibration_start = SPI_CALIBRATION_START
    if calibration_end is None:
        calibration_end = SPI_CALIBRATION_END
    
    logger.info("\n" + "="*80)
    logger.info("CALCULATING SPI FOR EXTENDED TIME PERIOD")
    logger.info(f"  Calibration period: {calibration_start}-{calibration_end} (historical obs only)")
    logger.info("="*80)
    
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Create date column
    basin_climate_df['date'] = pd.to_datetime(
        basin_climate_df[['YEAR', 'MONTH']].assign(day=1)
    )
    
    # Get unique basins
    basins = sorted(basin_climate_df['HA_NAME_CODE'].unique())
    logger.info(f"\nCalculating SPI for {len(basins)} basins")
    logger.info(f"Timescales: {timescales}")
    
    all_results = []
    failed_basins = []
    
    for i, basin in enumerate(basins, 1):
        logger.info(f"\n[{i}/{len(basins)}] Processing basin: {basin}")
        
        # Get time series
        basin_data = basin_climate_df[
            basin_climate_df['HA_NAME_CODE'] == basin
        ].sort_values('date').copy()
        
        if len(basin_data) < 36:
            logger.warning(f"  ✗ Insufficient data: {len(basin_data)} months (need ≥36)")
            failed_basins.append(basin)
            continue
        
        start_year = basin_data['YEAR'].min()
        end_year = basin_data['YEAR'].max()
        calibration_start_effective = max(calibration_start, start_year)
        calibration_end_effective = min(calibration_end, end_year)

        if calibration_start_effective > calibration_end_effective:
            logger.error(
                f"  ✗ No overlapping calibration period for basin {basin}: "
                f"requested {calibration_start}-{calibration_end}, available {start_year}-{end_year}"
            )
            failed_basins.append(basin)
            continue
        
        logger.info(f"  Date range: {start_year}-{end_year} ({len(basin_data)} months)")
        logger.info(
            f"  Calibration overlap: {calibration_start_effective}-{calibration_end_effective}"
        )
        
        # Extract precipitation
        precipitation = basin_data['PPT_MM'].values
        
        # CORRECT (replace with this):
        n_missing = np.isnan(precipitation).sum()
        if n_missing > 0:
            pct_missing = 100 * n_missing / len(precipitation)
            logger.error(f"  ✗ {n_missing} missing PPT values ({pct_missing:.1f}%)")
            logger.error(f"     Cannot calculate SPI - skipping basin {basin}")
            failed_basins.append(basin)
            continue
        
        # Calculate SPI for each timescale
        # Calibrate gamma distribution on HISTORICAL OBSERVATIONS ONLY
        # (calibration_start to calibration_end from config)
        # SPI values are computed for ALL years using calibrated parameters
        spi_success = []
        for scale in timescales:
            try:
                spi_values = indices.spi(
                    precipitation,
                    scale=scale,
                    distribution=indices.Distribution.gamma,
                    data_start_year=start_year,
                    calibration_year_initial=calibration_start_effective,
                    calibration_year_final=calibration_end_effective,
                    periodicity=compute.Periodicity.monthly
                )
                
                basin_data[f'SPI_{scale}m'] = spi_values
                
                # Count valid values
                n_valid = np.isfinite(spi_values).sum()
                pct_valid = 100 * n_valid / len(spi_values)
                
                spi_success.append(f"SPI-{scale}m: {n_valid}/{len(spi_values)} ({pct_valid:.1f}%)")
                
            except Exception as e:
                logger.error(f"  ✗ Error calculating SPI-{scale}m: {e}")
                basin_data[f'SPI_{scale}m'] = np.nan
        
        if spi_success:
            logger.info(f"  ✓ Calculated: " + ", ".join(spi_success))
        
        all_results.append(basin_data)
    
    # Combine results
    if all_results:
        final_results = pd.concat(all_results, ignore_index=True)
        
        logger.info(f"\nTotal SPI results (full period): {len(final_results)} records")
        logger.info(f"Year range in results: {final_results['YEAR'].min()}-{final_results['YEAR'].max()}")
        
        output_results = final_results.sort_values(['HA_NAME_CODE', 'YEAR', 'MONTH']).copy()
        
        logger.info("\nKeeping full available period in output...")
        logger.info(
            f"  Records from {output_results['YEAR'].min()}-{output_results['YEAR'].max()}: {len(output_results)}"
        )
        
        if len(output_results) == 0:
            logger.error("No records remain after filtering to 1985-2023!")
            logger.error("Check that data actually contains years >= 1985")
            return pd.DataFrame()
        
        # Verify that SPI columns were calculated and have values
        spi_cols = [col for col in output_results.columns if col.startswith('SPI_')]
        logger.info(
            f"\nVerifying SPI calculations for {output_results['YEAR'].min()}-{output_results['YEAR'].max()}:")
        for col in sorted(spi_cols):
            valid = output_results[col].notna().sum()
            total = len(output_results)
            pct = 100 * valid / total
            logger.info(f"  {col}: {valid:6d}/{total} ({pct:5.1f}%) valid")
            
            if valid == 0:
                logger.warning(f"    ⚠ WARNING: {col} has NO valid values for 1985-2023!")
        
        output_results.to_csv(output_file, index=False)
        
        logger.info("\n" + "="*80)
        logger.info("SPI CALCULATION COMPLETE")
        logger.info("="*80)
        logger.info(f"Output file: {output_file}")
        logger.info(f"Total records: {len(output_results)}")
        logger.info(f"Date range: {output_results['YEAR'].min()}-{output_results['YEAR'].max()}")
        
        if failed_basins:
            logger.warning(f"\n⚠ Failed basins ({len(failed_basins)}): {', '.join(failed_basins)}")
        
        logger.info("\n✓ SPI calculated on the full available Nevada basin climate period")
        logger.info("✓ Output written to the Phase 2 config-managed drought-indices path")
        logger.info("✓ Long-timescale SPI uses the maximum overlapping calibration window available")
        
        return output_results
    
    else:
        logger.error("No SPI results calculated!")
        return pd.DataFrame()


# ============================================================================
# MAIN WORKFLOW
# ============================================================================

def main():
    """
    Main workflow: Load GridMET and HA data, aggregate to basins, calculate SPI.
    """
    
    logger.info("="*80)
    logger.info("EXTENDING BASIN CLIMATE DATA WITH GRIDMET 1979-1984")
    logger.info("="*80)
    logger.info(f"GridMET directory: {GRIDMET_DIR}")
    logger.info(f"HA summaries file: {HA_SUMMARIES_FILE}")
    logger.info(f"Output directory: {OUTPUT_DIR}")
    logger.info(f"Configured drought file: {DROUGHT_INDICES_FILE}")
    
    # Step 1: Load field-to-basin mapping
    logger.info("\n" + "="*80)
    logger.info("STEP 1: LOADING FIELD-TO-BASIN MAPPING")
    logger.info("="*80)
    load_field_to_basin_mapping(HA_SUMMARIES_FILE)
    
    # Step 2: Load and aggregate GridMET data (1979-1984)
    logger.info("\n" + "="*80)
    logger.info("STEP 2: LOADING GRIDMET DATA (1979-1984)")
    logger.info("="*80)
    basin_monthly_gridmet = load_and_aggregate_gridmet_years(
        GRIDMET_DIR, 
        year_start=1979, 
        year_end=1984
    )
    
    if basin_monthly_gridmet.empty:
        logger.warning("No early GridMET basin climate data found; continuing with HA summaries only")
    else:
        gridmet_file = OUTPUT_DIR / 'basin_monthly_climate_1979_1984_gridmet.csv'
        basin_monthly_gridmet.to_csv(gridmet_file, index=False)
        logger.info(f"\n✓ Saved GridMET aggregation: {gridmet_file}")
    
    # Step 3: Load and aggregate HA summaries (1985+)
    logger.info("\n" + "="*80)
    logger.info("STEP 3: LOADING HA SUMMARIES (1985+)")
    logger.info("="*80)
    basin_monthly_ha = load_and_aggregate_ha_summaries(
        HA_SUMMARIES_FILE,
        year_start=1985
    )
    
    if basin_monthly_ha.empty:
        if basin_monthly_gridmet.empty:
            logger.error("No HA summaries data and no GridMET data available. Exiting.")
            return
        logger.warning("No HA summaries data - using GridMET only")
        full_dataset = basin_monthly_gridmet
    elif basin_monthly_gridmet.empty:
        logger.info("Using HA summaries only for SPI generation")
        full_dataset = basin_monthly_ha
    else:
        # Save intermediate HA result
        ha_file = OUTPUT_DIR / 'basin_monthly_climate_1985_plus_ha.csv'
        basin_monthly_ha.to_csv(ha_file, index=False)
        logger.info(f"\n✓ Saved HA aggregation: {ha_file}")
        
        # Step 4: Merge datasets
        logger.info("\n" + "="*80)
        logger.info("STEP 4: MERGING GRIDMET AND HA SUMMARIES")
        logger.info("="*80)
        full_dataset = merge_gridmet_and_ha_summaries(
            basin_monthly_gridmet, 
            basin_monthly_ha
        )
    
    if full_dataset.empty:
        logger.error("Failed to create combined dataset. Exiting.")
        return
    
    # Save combined dataset
    combined_file = OUTPUT_DIR / 'basin_monthly_climate_full_period_1979_2025.csv'
    full_dataset.to_csv(combined_file, index=False)
    logger.info(f"\n✓ Saved combined dataset: {combined_file}")
    
    # Step 5: Calculate SPI for full period
    logger.info("\n" + "="*80)
    logger.info("STEP 5: CALCULATING SPI FOR FULL PERIOD")
    logger.info("="*80)
    spi_results = calculate_basin_spi_extended(
        full_dataset, 
        DROUGHT_INDICES_FILE,
        timescales=SPI_TIMESCALES
    )
    
    if spi_results.empty:
        logger.error("Failed to calculate SPI. Exiting.")
        return
    
    # Final summary
    logger.info("\n" + "="*80)
    logger.info("PROCESSING COMPLETE")
    logger.info("="*80)
    logger.info(f"\nOutput directory: {OUTPUT_DIR}")
    logger.info(f"\nOutput files:")
    logger.info(f"  1. basin_monthly_climate_1979_1984_gridmet.csv (optional)")
    logger.info(f"     └─ Early GridMET aggregation when 1979-1984 exports are available")
    logger.info(f"  2. basin_monthly_climate_1985_plus_ha.csv")
    logger.info(f"     └─ HA basin climate summaries used for SPI generation")
    logger.info(f"  3. basin_monthly_climate_full_period_1979_2025.csv")
    logger.info(f"     └─ Combined basin climate dataset over the available period")
    logger.info(f"  4. {DROUGHT_INDICES_FILE} ⭐")
    logger.info(f"     └─ FINAL: SPI data for the full available basin climate record")
    logger.info(f"     └─ All configured timescales written to the Phase 2 input path")
    
    logger.info(f"\nFinal dataset summary ({spi_results['YEAR'].min()}-{spi_results['YEAR'].max()}):")
    logger.info(f"  Basins: {spi_results['HA_NAME_CODE'].nunique()}")
    logger.info(f"  Date range: {spi_results['YEAR'].min()}-{spi_results['YEAR'].max()}")
    logger.info(f"  Total records: {len(spi_results)}")
    logger.info(f"  SPI timescales: {SPI_TIMESCALES}")
    
    logger.info("\n✓ Extended SPI dataset ready for use!")
    logger.info(
        f"  ✓ Gamma calibration requested for {SPI_CALIBRATION_START}-{SPI_CALIBRATION_END} and clipped to each basin's available overlap"
    )
    logger.info("  ✓ SPI values computed for the full available basin climate period")
    logger.info("  ✓ Safe to join with Nevada basin summaries for Phase 2 models")


if __name__ == '__main__':
    import argparse
    
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description='Extend basin climate SPI data using GridMET 1979-1984 gap filling.'
    )
    parser.add_argument(
        '--gridmet-dir', 
        type=str, 
        help='Directory containing GridMET field summaries (1979-1984)'
    )
    parser.add_argument(
        '--ha-summaries', 
        type=str, 
        help='Path to HA summaries CSV (1985+)'
    )
    parser.add_argument(
        '--output-dir', 
        type=str, 
        help='Output directory for extended SPI data'
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
    
    # Reload config if custom path provided
    if args.config or args.environment:
        from config import load_config
        cfg = load_config(
            config_path=args.config,
            environment=args.environment,
            reload=True
        )
        config = get_script_config()
    
    # Override config with CLI arguments
    if args.gridmet_dir:
        config['gridmet_dir'] = Path(args.gridmet_dir)
        globals()['GRIDMET_DIR'] = Path(args.gridmet_dir)
    if args.ha_summaries:
        config['ha_summaries_file'] = Path(args.ha_summaries)
        globals()['HA_SUMMARIES_FILE'] = Path(args.ha_summaries)
    if args.output_dir:
        config['output_dir'] = Path(args.output_dir)
        globals()['OUTPUT_DIR'] = Path(args.output_dir)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    logger.info("="*80)
    logger.info("EXTEND BASIN CLIMATE SPI (1979-2023)")
    logger.info("="*80)
    logger.info(f"GridMET directory: {GRIDMET_DIR}")
    logger.info(f"HA summaries file: {HA_SUMMARIES_FILE}")
    logger.info(f"Output directory: {OUTPUT_DIR}")
    logger.info(f"SPI timescales: {SPI_TIMESCALES}")
    logger.info("="*80 + "\n")
    
    main()
