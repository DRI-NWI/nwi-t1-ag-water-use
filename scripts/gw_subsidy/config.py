"""Central configuration: paths, physical constants, and model defaults.

All other modules import from here so a run is reproducible without hand-editing
code between stages. Paths are derived from this file's location so the package
works regardless of the current working directory.
"""

from __future__ import annotations

from pathlib import Path

# --- Paths -------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "Data"
INPUT_DIR = DATA_DIR / "Inputs"
OUTPUT_DIR = DATA_DIR / "Outputs"
FIG_DIR = OUTPUT_DIR / "figures"

# Inputs
FIELDS_GPKG = INPUT_DIR / "Nevada_agricultural_field_boundaries_20260526.gpkg"
WTD_TIF = INPUT_DIR / "wtd_nevada.tif"
GW_CSV = INPUT_DIR / "field_summaries_EToF_final_GW.csv"
SW_CSV = INPUT_DIR / "field_summaries_EToF_final_SW.csv"
BASINS_GEOJSON = INPUT_DIR / "Basins_State_Engineer_Admin_Boundaries.geojson"
WELL_MEASURES_CSV = INPUT_DIR / "Groundwater_Monitoring_Measures.csv"
WELL_SITES_DIR = INPUT_DIR / "Groundwater_Monitoring_Sites"
TAN_PARAMS_CSV = INPUT_DIR / "tan2025_params.csv"

# Cached intermediates / outputs
FIELD_TEXTURE_CSV = INPUT_DIR / "field_texture.csv"          # cached POLARIS sampling
FIELD_WTD_PARQUET = OUTPUT_DIR / "field_wtd.parquet"          # cached zonal WTD (summary)
FIELD_WTD_PIXELS_PARQUET = OUTPUT_DIR / "field_wtd_pixels.parquet"  # shallow-field WTD pixels
FIELD_PARAMS_PARQUET = OUTPUT_DIR / "field_params.parquet"
FIELD_YEAR_CSV = OUTPUT_DIR / "field_year_subsidy.csv"
FIELD_MONTH_PARQUET = OUTPUT_DIR / "field_month_subsidy.parquet"
FIELD_SUBSIDY_GPKG = OUTPUT_DIR / "field_subsidy.gpkg"
BASIN_YEAR_CSV = OUTPUT_DIR / "basin_year_subsidy.csv"
BASIN_SUMMARY_CSV = OUTPUT_DIR / "basin_summary.csv"
WTD_VALIDATION_NDWR_CSV = OUTPUT_DIR / "wtd_validation_ndwr.csv"  # NDWR monitoring-well validation
WTD_VALIDATION_NWIS_CSV = OUTPUT_DIR / "wtd_validation_nwis.csv"  # NWIS field-measurement validation

# --- Earth Engine ------------------------------------------------------------
GEE_PROJECT = "nwi-usgs"
POLARIS_BASE = "projects/sat-io/open-datasets/polaris"
# POLARIS depth bands (cm) and the weights used to build a root-zone-mean texture.
POLARIS_DEPTHS = ["0_5", "5_15", "15_30", "30_60", "60_100"]
POLARIS_DEPTH_WEIGHTS = [5, 10, 15, 30, 40]  # band thickness (cm), 0-100 cm root zone

# --- Physical constants / unit conversions (match nmose-gw-et conventions) ---
M3_PER_ACFT = 1233.48
M2_PER_ACRE = 4046.86
MM_PER_INCH = 25.4
MM_PER_FT = 304.8
# mm over m^2 -> acre-feet
MM_M2_TO_ACFT = 1.0 / (1000.0 * M3_PER_ACFT)

# --- WTD raster handling -----------------------------------------------------
WTD_NODATA = 3.4028235e38   # IEEE float max left by the source product
WTD_MIN_VALID = 0.0         # depth below surface, metres; keep d >= 0

# --- Model defaults ----------------------------------------------------------
AVERIANOV_P = 3.0           # classic Averianov (1956) exponent (1-3); configurable
SHAH_EPS = 0.02             # ET ratio used to anchor the Shah exponential decay rate
ET0_DEFAULT_MM_D = 5.0      # fallback ET0 level for the texture lookup (Tan Table 2)
ET0_LEVELS_MM_D = [2.5, 5.0, 10.0]  # Tan Table 2/3 ET0 rows available for interpolation

MODELS = ["tan", "banta", "averianov", "shah"]
PRIMARY_MODEL = "tan"

# --- Aggregation / selection -------------------------------------------------
GROWING_SEASON_MONTHS = list(range(4, 11))  # Apr-Oct
SHALLOW_AREA_FRAC = 0.25     # basin is "shallow" if >= this frac of irrigated acres has WTD < d_ext

# Well-anchored WTD bias correction (wtd_correction.py): off by default. The monitoring-well
# data is sparse/noisy, so instead we restrict to basins with meaningful surface-water use
# (river valleys / discharge areas with reliable shallow water tables); pure-groundwater closed
# basins -- where the continental-scale ML WTD product under-resolves the deep drawdown cone and
# reads too shallow -- are excluded.
APPLY_WTD_CORRECTION = False
SW_USE_MIN_ACRE_FRAC = 0.10  # keep basins with >= this fraction of irrigated acres SW or mixed
WTD_BIAS_SENSITIVITY_M = [-1.0, -0.5, 0.5, 1.0]  # +/- offsets for sensitivity reporting

# Rooting-depth assignment (cm) for the Tan Table 3 vegetated parameters, keyed by
# USDA CDL code (CROP_major). Deep-rooted / phreatophytic cover -> 200 cm, else 100 cm.
ROOT_DEPTH_DEEP_CM = 200
ROOT_DEPTH_SHALLOW_CM = 100
# CDL codes treated as deep-rooted (alfalfa, shrubland, wetlands, riparian forest, grapes, orchards).
CDL_DEEP_ROOT = {36, 152, 190, 195, 141, 142, 143, 69, 211, 204, 223, 75, 76, 77}
# CDL codes that are not actively-irrigated crops (flagged so the irrigated-crop subsidy headline
# can be separated from natural phreatophyte / non-ag groundwater ET). Pasture/grass (176) and
# alfalfa/hay are irrigated agriculture in Nevada and are intentionally NOT included here.
CDL_NON_CROP = {61, 63, 64, 65, 81, 82, 83, 87, 88, 92, 111, 112, 121, 122, 123, 124, 131,
                141, 142, 143, 152, 190, 195}
