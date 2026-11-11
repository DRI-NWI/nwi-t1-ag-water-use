"""Paths and constants shared by the GEE app input scripts in gee/prep/.

The scripts read the outputs of one run of this repository (config/nwi_t1_final.yaml) and
write the tables the Earth Engine app uses. By default they read from and write to the run
folder, <output_root>/<run_id>/; both can be changed on the command line, for example to read
copies of the run files on another computer.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "utils"))
sys.path.insert(0, str(REPO / "scripts"))
from nwi_config import load_config, RunPaths  # noqa: E402

CFG = load_config()
P = RunPaths(CFG)                      # .make() is not called: no folders are created
START_YEAR = int(CFG["run"]["start_year"])
END_YEAR = int(CFG["run"]["end_year"])
NY = END_YEAR - START_YEAR + 1          # annual means = total over the period / NY
IRRIGATED_MIN_FRACTION = float(CFG["summaries"]["irrigated_min_fraction"])

DEFAULT_OUT = P.root / "09_gee_app"
DEFAULT_FIELD_FILE = P.fields / "field_summaries_EToF_final.parquet"
DEFAULT_SUBSIDY_DIR = P.subsidy        # 04_gw_subsidy: field_params.parquet, ma_comparison/

# Models in the order the app lists them; Tan et al. (2025) is the one used in the run.
MODELS = ["tan", "banta", "averianov", "shah"]
DTW_PRODUCTS = {"new": "nv_dtw_parity_statewide_100m", "ma": "Ma et al. (2026), 100 m"}


def out_dir(arg):
    """Output folder from the command-line value (or the run default), created if missing."""
    path = Path(arg) if arg else DEFAULT_OUT
    path.mkdir(parents=True, exist_ok=True)
    return path
