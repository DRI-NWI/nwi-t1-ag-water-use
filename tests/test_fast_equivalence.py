"""
Check the faster NWI T1 versions of three processor steps against the original code.

process_field_summaries.py replaces three slow loops of the original
(agricultural-water-forecasting, commit 58819af) with faster code:

  * apply_winter_moisture_carryforward: the per-row loop runs in numba;
  * apply_climatology_gap_filling_etof: a table join replaces the row-by-row lookup;
  * apply_gw_sw_separation_dynamic: the basin-year split is a join, not per-year copies.

Each test builds a small synthetic data set, runs the original (tests/original_reference.py,
copied verbatim) and the new function, and requires the same results.

Run:  python -m pytest tests   (or python tests/test_fast_equivalence.py)
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "utils"))
sys.path.insert(0, str(REPO / "scripts" / "phase1_data_preparation"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import original_reference as original  # noqa: E402
import process_field_summaries as current  # noqa: E402


def make_field_months(n_fields=6, years=(2000, 2001, 2002), seed=0):
    """Field-month rows sorted by field and date, with some missing net ET."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n_fields):
        for year in years:
            for month in range(1, 13):
                rows.append({"OPENET_ID": f"F{i}", "HA_NAME_CODE": f"Basin{i % 2} (10{i % 2})", "YEAR": year,
                             "MONTH": month})
    df = pd.DataFrame(rows)
    n = len(df)
    df["ETa_vol"] = rng.uniform(0, 30, n)
    df["NetET_vol"] = rng.normal(5, 15, n)
    df.loc[rng.choice(n, 8, replace=False), "NetET_vol"] = np.nan
    df["irr_eff"] = np.repeat(rng.uniform(0.5, 0.9, n_fields), n // n_fields)
    df["AWC"] = np.repeat(rng.uniform(0.05, 0.2, n_fields), n // n_fields)
    df["ACRES_FTR_GEOM"] = np.repeat(rng.uniform(10, 200, n_fields), n // n_fields)
    df["date"] = pd.to_datetime(df[["YEAR", "MONTH"]].assign(day=1))
    return df


def test_carryforward_matches_original():
    df = make_field_months()
    kwargs = dict(eta_vol_intermediate_col="ETa_vol", net_et_vol_intermediate_col="NetET_vol",
                  ar_original_vol_col="ETa_vol", irr_eff_col="irr_eff", group_cols=["HA_NAME_CODE", "OPENET_ID"])
    expected = original.apply_winter_moisture_carryforward(df.copy(), **kwargs)
    result = current.apply_winter_moisture_carryforward(df.copy(), **kwargs)
    columns = ["NetET_final_acre_ft", "AR_final_acre_ft", "Water_Surplus_Cumulative_acre_ft",
               "Eff_PPT_Adjusted_acre_ft", "Soil_Moisture_Overflow_acre_ft", "Soil_Moisture_Cap_acre_ft"]
    pd.testing.assert_frame_equal(result[columns], expected[columns], check_exact=False, rtol=1e-12, atol=1e-12)
    # The new version does not depend on the input being sorted
    shuffled = df.sample(frac=1, random_state=1)
    reordered = current.apply_winter_moisture_carryforward(shuffled.copy(), **kwargs)
    reordered = reordered.sort_values(["OPENET_ID", "date"]).reset_index(drop=True)
    in_order = result.sort_values(["OPENET_ID", "date"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(reordered[columns], in_order[columns], check_exact=False, rtol=1e-12, atol=1e-12)


def test_climatology_fill_matches_original():
    rng = np.random.default_rng(2)
    df = make_field_months(years=(1990, 2004, 2026))       # 2026 is outside every window
    df["ETO_MM"] = rng.uniform(20, 200, len(df))
    df["EToF"] = rng.uniform(0, 1.2, len(df))
    df.loc[rng.choice(len(df), 60, replace=False), "EToF"] = np.nan
    df["ETA_MM"] = df["EToF"] * df["ETO_MM"]
    df["EToF_interpolated"] = df["EToF"]
    df["interpolation_method"] = "original"
    windows = [(1985, 1991), (1992, 1997), (1998, 2003), (2004, 2009), (2010, 2015), (2016, 2021), (2022, 2025)]
    climatologies = {}
    for start, end in windows:
        fields = df[["OPENET_ID", "HA_NAME_CODE"]].drop_duplicates()
        fields = fields[fields["OPENET_ID"] != "F5"]                      # one field has no climatology
        extra = fields.iloc[[0]].assign(HA_NAME_CODE="Other (999)")       # a repeated ID in another HA
        table = pd.concat([fields, extra])
        for m in range(1, 13):
            table[f"EToF_climo_{m:02d}"] = rng.uniform(0.1, 1.0, len(table))
        table.loc[table.index[1], "EToF_climo_03"] = np.nan              # a missing climatology value
        climatologies[f"{start}_{end}"] = table.set_index("OPENET_ID")
    expected = original.apply_climatology_gap_filling_etof(df.copy(), climatologies, windows)
    result = current.apply_climatology_gap_filling_etof(df.copy(), climatologies, windows)
    columns = ["EToF_interpolated", "interpolation_method", "ETA_MM"]
    pd.testing.assert_frame_equal(result[columns], expected[columns])


def test_dynamic_split_matches_original():
    rng = np.random.default_rng(3)
    rows = []
    for i in range(12):
        basin = ["Alpha (101)", "Beta (117)", "Gamma (153)"][i % 3]
        for year in (2000, 2001, 2002):
            for month in (1, 6):
                rows.append({"OPENET_ID": f"F{i}", "HA_NAME_CODE": basin, "YEAR": year, "MONTH": month,
                             "ACRES_FTR_GEOM": 10.0 + i, "ACRES_IRR": 8.0 + i})
    df = pd.DataFrame(rows)
    fieldsdf = pd.DataFrame({"OPENET_ID": [f"F{i}" for i in range(12)],
                             "IRR_SRC": [3, 3, 1, 2, 3, 3, 1, 3, 2, 3, 3, 3]})
    spi_rows = [{"HA_NAME_CODE": name, "YEAR": year, "MONTH": 9, "SPI_12m": rng.normal()}
                for name in ["Alpha Valley (101)", "Beta (117)"] for year in (2000, 2001, 2002)]
    spi_data = pd.DataFrame(spi_rows[:-1])                               # Beta 2002 is missing
    basin_config = {"117": {"min_sw_ratio": 0.2, "max_sw_ratio": 0.7, "center_sw_ratio": 0.36,
                            "fallback_ratio": 0.36, "allocation_method": "rush_katzer_spi12"}}
    kwargs = dict(basin_config_dict=basin_config, spi_data=spi_data, min_sw_ratio=0.2, max_sw_ratio=0.8,
                  use_uniform_mean_ratio=True, allocation_overrides=None, spi_reference_month=9)
    expected = original.apply_gw_sw_separation_dynamic(df.copy(), fieldsdf, **kwargs)
    result = current.apply_gw_sw_separation_dynamic(df.copy(), fieldsdf, **kwargs)
    keys = ["OPENET_ID", "YEAR", "MONTH", "water_source"]
    same_columns = [c for c in expected[0].columns if c != "ACRES_IRR"]
    for new, old in zip(result[:2], expected[:2]):
        new = new.sort_values(keys).reset_index(drop=True)
        old = old.sort_values(keys).reset_index(drop=True)
        pd.testing.assert_frame_equal(new[same_columns], old[same_columns], check_exact=False, rtol=1e-12)
    # Intended fix: irrigated acres of a mixed field are split like ACRES_FTR_GEOM, so the
    # GW and SW halves add up to the field (the original kept the full value on both).
    gw, sw = result[0], result[1]
    mixed_total = pd.concat([gw, sw])[lambda d: d.water_source.str.endswith("_from_mixed")]
    field_total = df.merge(fieldsdf, on="OPENET_ID")[lambda d: d.IRR_SRC == 3]
    assert abs(mixed_total["ACRES_IRR"].sum() - field_total["ACRES_IRR"].sum()) < 1e-9
    stats_keys = ["basin_code", "year"]
    new_stats = result[3].sort_values(stats_keys).reset_index(drop=True)
    old_stats = expected[3].sort_values(stats_keys).reset_index(drop=True)
    pd.testing.assert_frame_equal(new_stats[old_stats.columns], old_stats, check_exact=False, rtol=1e-12)


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print(f"{name}: passed")
