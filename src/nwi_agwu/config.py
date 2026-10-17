"""Configuration loading and run-directory layout."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "nwi_t1_final.yaml"


def deep_update(base: dict, update: dict) -> dict:
    """Return a copy of base with update merged in recursively."""
    out = copy.deepcopy(base)
    for key, value in (update or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_update(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load(path: str | Path | None = None, overrides: dict | None = None) -> dict:
    with open(path or DEFAULT_CONFIG) as fh:
        cfg = yaml.safe_load(fh)
    cfg = deep_update(cfg, overrides or {})
    cfg["_config_path"] = str(Path(path or DEFAULT_CONFIG).resolve())
    return cfg


class RunPaths:
    """Every intermediate and output location for one run."""

    def __init__(self, cfg: dict):
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
        self.field_base = self.inputs / "field_month_base.parquet"
        self.field_attrs = self.inputs / "field_attributes.parquet"
        self.attr_report = self.inputs / "field_attribute_report.csv"
        self.otter_monthly = self.otter / "field_monthly_otter_peff.parquet"
        self.otter_audit = self.otter / "otter_static_audit.csv"
        self.spi_table = self.spi / "basin_year_spi.csv"
        self.spi_monthly = self.spi / "prism_spi_monthly_by_region.csv"
        self.field_wtd = self.subsidy / "field_wtd.parquet"
        self.field_wtd_pixels = self.subsidy / "field_wtd_pixels.parquet"
        self.field_texture = self.subsidy / "field_texture.csv"
        self.field_gwfrac = self.subsidy / "field_gwfrac.parquet"

    def make(self):
        for p in [self.inputs, self.otter, self.spi, self.subsidy, self.fields,
                  self.summaries, self.qa, self.variants]:
            p.mkdir(parents=True, exist_ok=True)
        return self
