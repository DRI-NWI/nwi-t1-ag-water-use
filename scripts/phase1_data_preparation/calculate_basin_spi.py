"""
Calculate basin SPI from PRISM monthly precipitation over the full period.

For each precipitation series (one per hydrographic area, and one per surface-water
contributing area where defined), SPI is computed with climate_indices: gamma
distribution, monthly periodicity, and one calibration window applied to every year.
The September SPI-12 of each basin-year drives the dynamic SW/GW split of mixed fields
in process_field_summaries.py.

Source per basin: the contributing-area series where one exists, otherwise the HA series.

NWI T1: copied from agricultural-water-forecasting
scripts/phase1_data_preparation/extend_basin_climate_spi_1979_2023.py. The original stitched
1979-1984 gridMET onto 1985+ HA summaries before computing SPI; here each PRISM series
already covers 1981-2025, so the extension and merge functions are removed and only the
SPI calculation (calculate_basin_spi_extended) is kept.

Inputs (config/nwi_t1_final.yaml):
    inputs.prism_region_ppt   PRISM monthly PPT per HA and contributing area (HA, region_type,
                              name, YEAR, MONTH, PPT_MM)
    spi.calibration_start / calibration_end, spi.timescale_months, spi.reference_month

Outputs (run/03_spi/):
    prism_spi_monthly_by_region.csv   SPI for every series, month and timescale
    basin_spi_prism.csv               one series per basin (contributing area, else HA),
                                      the drought-indices table read by the field processor

Author: Peter ReVelle
Updated: October 2026 - NWI T1 full-period PRISM SPI
"""

import os
import sys
import logging
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

# NWI T1: settings from the run configuration
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from nwi_config import load_config, RunPaths  # noqa: E402

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
# climate_indices logs every call; keep only this script's messages
logging.getLogger('climate_indices').setLevel(logging.ERROR)
try:
    import structlog
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR))
except ImportError:
    pass

CFG = load_config()
PATHS = RunPaths(CFG).make()
SPI_CALIBRATION_START = CFG['spi']['calibration_start']
SPI_CALIBRATION_END = CFG['spi']['calibration_end']
SPI_TIMESCALES = [3, 6, 12, 24, 36, 48, 60]


def calculate_basin_spi_extended(basin_climate_df, output_file, timescales=[3, 6, 12, 24, 36, 48, 60],
                                  calibration_start=None, calibration_end=None):
    """
    Calculate SPI for every basin over its full monthly record.

    NWI T1: each series is the full PRISM record (1981-2025) in one piece, so no extension
    or merging happens before this call.
    
    Uses climate_indices package to calculate SPI for each basin and timescale.
    Gamma distribution is calibrated on the specified calibration period
    (default: from config spi_calibration_start/end - historical obs only).
    SPI values are computed for ALL years using the calibrated parameters.
    
    Parameters
    ----------
    basin_climate_df : pd.DataFrame
        Basin-level monthly climate data with columns HA_NAME_CODE, YEAR, MONTH, PPT_MM
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
# NWI T1: PRISM INPUT AND SOURCE SELECTION
# ============================================================================

def load_prism_region_ppt(region_ppt_file, region_type):
    """
    Load PRISM monthly precipitation for one kind of region.

    Parameters
    ----------
    region_ppt_file : str or Path
        CSV with HA, region_type, name, YEAR, MONTH, PPT_MM.
    region_type : str
        'ha_only' (precipitation over the HA) or 'contributing_area' (over the HA's
        surface-water contributing area).

    Returns
    -------
    pd.DataFrame
        HA_NAME_CODE ('Name (code)'), HA, YEAR, MONTH, PPT_MM for that region type.
    """
    ppt = pd.read_csv(region_ppt_file, dtype={'HA': str})
    ppt = ppt[ppt['region_type'] == region_type].copy()
    ppt['HA_NAME_CODE'] = ppt['name'].astype(str) + ' (' + ppt['HA'] + ')'
    return ppt[['HA_NAME_CODE', 'HA', 'YEAR', 'MONTH', 'PPT_MM']]


def select_basin_spi_source(spi_ha, spi_contributing):
    """
    Pick one SPI series per basin: the contributing area where defined, else the HA.

    Parameters
    ----------
    spi_ha : pd.DataFrame
        SPI from HA precipitation (output of calculate_basin_spi_extended).
    spi_contributing : pd.DataFrame
        SPI from contributing-area precipitation.

    Returns
    -------
    pd.DataFrame
        One row per basin, year and month, with column spi_source
        ('prism_contributing_area' or 'prism_ha').
    """
    spi_contributing = spi_contributing.assign(spi_source='prism_contributing_area')
    spi_ha = spi_ha.assign(spi_source='prism_ha')
    has_contributing = set(spi_contributing['HA'])
    spi_ha = spi_ha[~spi_ha['HA'].isin(has_contributing)]
    selected = pd.concat([spi_contributing, spi_ha], ignore_index=True)
    return selected.sort_values(['HA_NAME_CODE', 'YEAR', 'MONTH']).reset_index(drop=True)


# ============================================================================
# MAIN WORKFLOW
# ============================================================================

def main():
    """
    Compute SPI for every PRISM series, then write the per-basin drought-indices table.
    """
    logger.info("=" * 80)
    logger.info("BASIN SPI FROM PRISM PRECIPITATION (FULL PERIOD)")
    logger.info("=" * 80)
    region_ppt_file = CFG['inputs']['prism_region_ppt']
    logger.info(f"PRISM regional precipitation: {region_ppt_file}")
    logger.info(f"Calibration period: {SPI_CALIBRATION_START}-{SPI_CALIBRATION_END}")

    results = {}
    for region_type in ['ha_only', 'contributing_area']:
        logger.info("\n" + "=" * 80)
        logger.info(f"STEP: SPI FOR REGION TYPE '{region_type}'")
        logger.info("=" * 80)
        ppt = load_prism_region_ppt(region_ppt_file, region_type)
        spi = calculate_basin_spi_extended(
            ppt,
            output_file=PATHS.spi / f'spi_{region_type}.csv',
            timescales=SPI_TIMESCALES,
        )
        spi['HA'] = spi['HA'].astype(str)
        spi.insert(2, 'region_type', region_type)
        results[region_type] = spi

    by_region = pd.concat(results.values(), ignore_index=True)
    by_region.to_csv(PATHS.spi_monthly, index=False)
    logger.info(f"\n✓ SPI for every series: {PATHS.spi_monthly}")

    basin_spi = select_basin_spi_source(results['ha_only'], results['contributing_area'])
    basin_spi.to_csv(PATHS.spi_table, index=False)
    sept = basin_spi[(basin_spi['MONTH'] == CFG['spi']['reference_month'])
                     & (basin_spi['YEAR'] >= CFG['run']['start_year'])]
    logger.info(f"✓ Basin SPI table: {PATHS.spi_table}")
    logger.info(f"  Basins: {basin_spi['HA_NAME_CODE'].nunique()} "
                f"({(basin_spi.drop_duplicates('HA')['spi_source'] == 'prism_contributing_area').sum()} contributing area)")
    logger.info(f"  September SPI-12 missing in {CFG['run']['start_year']}-{CFG['run']['end_year']}: "
                f"{int(sept['SPI_12m'].isna().sum())} basin-years")


if __name__ == '__main__':
    main()
