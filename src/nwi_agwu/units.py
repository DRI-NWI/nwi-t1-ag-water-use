"""Unit conversions. MM_ACRES_TO_ACFT keeps the delivered processor's constant
(mm / 1000 x 3.281 ft/m x acres) so volumes reproduce the 2026-08-23 package."""

FT_PER_M = 3.281
M2_PER_ACRE = 4046.86


def mm_to_acft(mm, acres):
    return mm / 1000.0 * FT_PER_M * acres
