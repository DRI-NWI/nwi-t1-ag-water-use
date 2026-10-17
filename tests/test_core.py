import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nwi_agwu import allocation, water_balance  # noqa: E402


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
