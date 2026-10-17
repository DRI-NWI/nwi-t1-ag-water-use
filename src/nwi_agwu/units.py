"""Unit conversions: acre-ft = mm / 1000 x 3.281 ft/m x acres."""

FT_PER_M = 3.281
M2_PER_ACRE = 4046.86


def mm_to_acft(mm, acres):
    return mm / 1000.0 * FT_PER_M * acres
