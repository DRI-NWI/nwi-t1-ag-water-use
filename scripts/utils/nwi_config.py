"""
Configuration for the NWI T1 scripts.

Every script in this repository reads one YAML file, config/nwi_t1_final.yaml. This module
loads it, applies optional overrides (used by the QA variants in
config/bridge_variants.yaml), and defines where each step writes inside the run directory.

The original agricultural-water-forecasting scripts read their settings through
``config.load_config()``; the copied scripts call ``load_config`` from here instead.
"""

import copy
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "nwi_t1_final.yaml"


def deep_update(base, update):
    """
    Return a copy of ``base`` with ``update`` merged in.

    Nested dictionaries are merged key by key; any other value in ``update`` replaces the
    value in ``base``.

    Parameters
    ----------
    base : dict
        Configuration to start from.
    update : dict or None
        Values to override.

    Returns
    -------
    dict
        Merged configuration.
    """
    merged = copy.deepcopy(base)
    for key, value in (update or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_update(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def load_config(config_path=None, variant=None):
    """
    Load the run configuration.

    Parameters
    ----------
    config_path : str or Path, optional
        YAML file to read. Default: config/nwi_t1_final.yaml.
    variant : str, optional
        Name of a QA variant in config/bridge_variants.yaml whose settings override the
        main configuration.

    Returns
    -------
    dict
        The configuration, with the key ``_config_path`` added.
    """
    config_path = Path(config_path or DEFAULT_CONFIG)
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    if variant:
        with open(REPO_ROOT / "config" / "bridge_variants.yaml") as f:
            variants = yaml.safe_load(f)["variants"]
        cfg = deep_update(cfg, variants[variant])
    cfg["_config_path"] = str(config_path.resolve())
    return cfg


class RunPaths:
    """
    Locations of every input and output inside one run directory.

    The run directory is ``run.output_root / run.run_id`` from the configuration. Step
    folders are numbered in run order.
    """

    def __init__(self, cfg):
        self.root = Path(cfg["run"]["output_root"]) / cfg["run"]["run_id"]
        self.inputs = self.root / "01_inputs"
        self.otter = self.root / "02_otter"
        self.spi = self.root / "03_spi"
        self.subsidy = self.root / "04_gw_subsidy"
        self.fields = self.root / "05_field_summaries"
        self.summaries = self.root / "06_summaries"
        self.qa = self.root / "07_qa"
        self.variants = self.root / "08_bridge_variants"

        self.prism_monthly = self.inputs / "field_monthly_prism_ppt_eto.parquet"
        self.etof_climatology = self.inputs / "etof_climatology"
        self.otter_monthly = self.otter / "field_monthly_otter_peff.parquet"
        self.otter_audit = self.otter / "otter_static_audit.csv"
        self.spi_monthly = self.spi / "prism_spi_monthly_by_region.csv"
        self.spi_table = self.spi / "basin_spi_prism.csv"
        self.field_gwfrac = self.subsidy / "field_params.parquet"

    def make(self):
        """Create every step folder and return self."""
        for folder in [self.inputs, self.otter, self.spi, self.subsidy, self.fields,
                       self.summaries, self.qa, self.variants]:
            folder.mkdir(parents=True, exist_ok=True)
        return self
