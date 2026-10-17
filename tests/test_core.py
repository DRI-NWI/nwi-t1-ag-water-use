import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nwi_agwu import allocation, etof, water_balance  # noqa: E402


def reference_carryforward(net, months, cap, reset_month=1):
    """Pure-Python copy of the delivered apply_winter_moisture_carryforward recurrence."""
    final, storage, overflow = [], [], []
    prev, valid = 0.0, False
    for x, m, c in zip(net, months, cap):
        if not np.isfinite(x):
            prev, valid = 0.0, False
            final.append(np.nan); storage.append(np.nan); overflow.append(0.0)
            continue
        reset = (not valid) or m == reset_month
        raw = x if reset else prev + x
        lower = -c
        b = min(0.0, max(raw, lower))
        final.append(raw if raw >= 0 else 0.0); storage.append(b); overflow.append(max(lower - raw, 0.0))
        prev, valid = b, True
    return np.array(final), np.array(storage), np.array(overflow)


def test_carryforward_matches_reference():
    rng = np.random.default_rng(0)
    n = 240
    net = rng.normal(0, 5, n)
    net[[17, 18, 90]] = np.nan
    months = np.tile(np.arange(1, 13), n // 12)
    cap = np.full(n, 6.0)
    start = np.zeros(n, bool); start[0] = True
    got = water_balance._carryforward(net, months.astype(np.int64), start, cap, 1)
    ref = reference_carryforward(net, months, cap)
    for a, b in zip(got, ref):
        np.testing.assert_allclose(a, b, equal_nan=True)


def test_spi_to_sw_ratio():
    assert allocation.spi_to_sw_ratio(-2.0) == 0.2
    assert allocation.spi_to_sw_ratio(0.0) == 0.5
    assert allocation.spi_to_sw_ratio(5.0) == 0.8
    assert allocation.spi_to_sw_ratio(0.0, 0.2, 0.7, center_ratio=0.36) == 0.36
    assert allocation.spi_to_sw_ratio(np.nan) == 0.5
    # GW share = clip(0.5 - 0.15 x SPI, 0.2, 0.8)
    for s in np.linspace(-3, 3, 13):
        assert abs((1 - allocation.spi_to_sw_ratio(s)) - np.clip(0.5 - 0.15 * s, 0.2, 0.8)) < 1e-12


def test_split_closure():
    df = pd.DataFrame({"NWI_ID": ["a"] * 12 + ["b"] * 12, "YEAR": 2000, "MONTH": list(range(1, 13)) * 2,
                       "IRR_SRC": [3] * 12 + [1] * 12, "ACRES_FTR_GEOM": 10.0, "NetET_final_acre_ft": 1.0})
    share = pd.DataFrame({"NWI_ID": ["a", "b"], "YEAR": 2000, "sw_share": [0.3, 0.0],
                          "allocation_method": ["default_fallback", "pure_gw"], "spi_value": [0.1, np.nan],
                          "spi_source": ["prism_ha", ""]})
    c = water_balance.split_components(df, share)
    assert abs(c["GW"].NetET_final_acre_ft.sum() + c["SW"].NetET_final_acre_ft.sum() - c["ALL"].NetET_final_acre_ft.sum()) < 1e-12
    assert len(c["SW"]) == 12


def _toy_base():
    rows = []
    for y in (1990, 1991, 1992):
        for m in range(1, 13):
            rows.append({"NWI_ID": "a", "OPENET_ID": "oa", "YEAR": y, "MONTH": m, "ETO_MM": 100.0,
                         "ACRES_FTR_GEOM": 10.0, "EToF": 0.5})
    b = pd.DataFrame(rows)
    b.loc[(b.YEAR == 1990) & (b.MONTH == 1), "EToF"] = 2.0       # clipped to 1.5 before the mean
    b.loc[(b.YEAR == 1991) & (b.MONTH.isin([1, 2])), "EToF"] = np.nan  # 2-month gap
    b.loc[(b.YEAR == 1992) & (b.MONTH.isin([3, 4])), "EToF"] = np.nan  # gap with no 1992-97 March obs
    b["EToF_interpolated"] = b["EToF"]
    b["interpolation_method"] = np.where(b["EToF"].isna(), "unfilled_missing", "original")
    b["ETA_MM_post_interp"] = b["EToF_interpolated"] * b["ETO_MM"]
    b["ETa_vol_acre_ft"] = b["ETA_MM_post_interp"] / 1000 * 3.281 * 10.0
    return b


def test_climatology_build_and_fill():
    b = _toy_base()
    attrs = pd.DataFrame({"NWI_ID": ["a"], "HA_NAME_CODE": ["X (001)"]})
    windows = [(1985, 1991), (1992, 1997)]
    c = etof.build_climatologies(b, attrs, windows)
    assert c["1985_1991"].loc["oa", "EToF_climo_01"] == 1.5   # mean of clipped 2.0 (1991 Jan missing)
    assert c["1985_1991"].loc["oa", "EToF_climo_02"] == 0.5
    assert np.isnan(c["1992_1997"].loc["oa", "EToF_climo_03"])
    assert etof.window_key(1980, windows) == "1985_1991" and etof.window_key(2030, windows) == "1992_1997"
    f = etof.apply_climatology_fill(b.copy(), c, windows)
    jan91 = f[(f.YEAR == 1991) & (f.MONTH == 1)].iloc[0]
    assert jan91.interpolation_method == "climatology" and jan91.EToF_interpolated == 1.5
    assert np.isclose(jan91.ETa_vol_acre_ft, 150 / 1000 * 3.281 * 10)
    assert np.isnan(jan91.EToF)  # observed EToF untouched
    assert f.loc[(f.YEAR == 1992) & f.MONTH.isin([3, 4]), "interpolation_method"].eq("unfilled_missing").all()
    assert f["interpolation_method"].eq("climatology").sum() == 2


def test_climatology_fill_noop_without_gaps():
    b = _toy_base()
    b["interpolation_method"] = "original"
    c = etof.build_climatologies(b, pd.DataFrame({"NWI_ID": ["a"], "HA_NAME_CODE": ["X"]}), [(1985, 1997)])
    pd.testing.assert_frame_equal(etof.apply_climatology_fill(b.copy(), c, [(1985, 1997)]), b)
