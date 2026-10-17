"""Constants copied from nwi-gw-subsidy src/gw_subsidy/config.py (7262f69)."""

POLARIS_BASE = "projects/sat-io/open-datasets/polaris"
POLARIS_DEPTHS = ["0_5", "5_15", "15_30", "30_60", "60_100"]
POLARIS_DEPTH_WEIGHTS = [5, 10, 15, 30, 40]
WTD_MIN_VALID = 0.0
AVERIANOV_P = 3.0
SHAH_EPS = 0.02
ET0_DEFAULT_MM_D = 5.0
MODELS = ["tan", "banta", "averianov", "shah"]
ROOT_DEPTH_DEEP_CM = 200
ROOT_DEPTH_SHALLOW_CM = 100
CDL_DEEP_ROOT = {36, 152, 190, 195, 141, 142, 143, 69, 211, 204, 223, 75, 76, 77}
CDL_NON_CROP = {61, 63, 64, 65, 81, 82, 83, 87, 88, 92, 111, 112, 121, 122, 123, 124, 131,
                141, 142, 143, 152, 190, 195}
