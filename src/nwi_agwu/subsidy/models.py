"""Groundwater-ET fraction f(d) = ET_GW / ET_0 as a function of water-table depth.

Four parameterizations, all expressed against Tan et al. (2025) Eq. 5, the unified
piecewise form:

    f(d) = 1                                       d <= d_dec
           ((d_ext - d) / (d_ext - d_dec)) ** p     d_dec < d < d_ext
           0                                        d >= d_ext

with d = water-table depth, d_dec = decoupling depth, d_ext = extinction depth,
p = empirical exponent. Special cases:

  * Banta (2000) linear (MODFLOW EVT/ETS): d_dec = 0, p = 1  ->  max(0, 1 - d/d_ext)
  * Averianov (1956) power law:            d_dec = 0          ->  max(0, 1 - d/d_ext) ** p
  * Shah et al. (2007) decoupling-only:    plateau to d_dec then exponential decay with
        no hard extinction (long tail): f = exp(-k (d - d_dec)), k = -ln(eps)/(d_ext - d_dec).

All depths must share the same length unit (this package uses metres). `d` may be a
scalar or an array (e.g. all WTD pixels inside a field); `d_dec`, `d_ext`, `p` are
per-field scalars. NaN in `d` propagates to NaN. Functions never raise on edge cases
(d_ext <= d_dec returns 0 below the plateau).

References:
  Tan et al. (2025) J. Hydrol. 658:133100. Shah et al. (2007) Ground Water 45:329-338.
  Averianov (1956). Banta (2000) MODFLOW-2000 docs.
"""

from __future__ import annotations

import numpy as np

from . import constants as config


def tan_fraction(d, d_dec, d_ext, p):
    """Tan et al. (2025) unified piecewise f(d). The general form; the others derive from it."""
    d = np.asarray(d, dtype=float)
    span = float(d_ext) - float(d_dec)
    if span <= 0:
        # Degenerate soil params: no decay band. Plateau below d_dec, else 0.
        f = np.where(d <= d_dec, 1.0, 0.0)
        return np.where(np.isnan(d), np.nan, f)
    base = np.clip((float(d_ext) - d) / span, 0.0, 1.0)
    f = np.power(base, float(p))
    f = np.where(d <= d_dec, 1.0, f)
    f = np.where(d >= d_ext, 0.0, f)
    return np.where(np.isnan(d), np.nan, f)


def banta_fraction(d, d_ext, **_):
    """Banta (2000) linear MODFLOW EVT/ETS: Tan with d_dec = 0, p = 1."""
    return tan_fraction(d, 0.0, d_ext, 1.0)


def averianov_fraction(d, d_ext, p=config.AVERIANOV_P, **_):
    """Averianov (1956) power law: Tan with d_dec = 0 and a classic small exponent p (1-3)."""
    return tan_fraction(d, 0.0, d_ext, p)


def shah_fraction(d, d_dec, d_ext, eps=config.SHAH_EPS, **_):
    """Shah et al. (2007) decoupling-only exponential: plateau to d_dec, then exp decay.

    No hard extinction; the decay rate k is anchored so f reaches `eps` at Tan's d_ext,
    after which the curve tails beyond d_ext (the behaviour the comparison figure shows).
    """
    d = np.asarray(d, dtype=float)
    span = float(d_ext) - float(d_dec)
    if span <= 0:
        f = np.where(d <= d_dec, 1.0, 0.0)
        return np.where(np.isnan(d), np.nan, f)
    k = -np.log(eps) / span
    f = np.exp(-k * np.maximum(d - float(d_dec), 0.0))
    f = np.where(d <= d_dec, 1.0, f)
    return np.where(np.isnan(d), np.nan, f)


def etgw_fraction(d, d_dec, d_ext, p, model="tan",
                  averianov_p=config.AVERIANOV_P, shah_eps=config.SHAH_EPS):
    """Dispatch to one of the four models. Returns f(d) in [0, 1]."""
    model = model.lower()
    if model == "tan":
        return tan_fraction(d, d_dec, d_ext, p)
    if model == "banta":
        return banta_fraction(d, d_ext)
    if model == "averianov":
        return averianov_fraction(d, d_ext, p=averianov_p)
    if model == "shah":
        return shah_fraction(d, d_dec, d_ext, eps=shah_eps)
    raise ValueError(f"unknown model {model!r}; expected one of {config.MODELS}")


def tan_fraction_vec(d, d_dec, d_ext, p):
    """Vectorized Tan Eq. 5 with per-element params (for applying field params across pixels)."""
    d = np.asarray(d, float)
    d_dec = np.asarray(d_dec, float)
    d_ext = np.asarray(d_ext, float)
    p = np.asarray(p, float)
    span = d_ext - d_dec
    safe_span = np.where(span > 0, span, 1.0)
    base = np.clip(np.where(span > 0, (d_ext - d) / safe_span, 0.0), 0.0, 1.0)
    f = np.power(base, p)
    f = np.where(d <= d_dec, 1.0, f)
    f = np.where(d >= d_ext, 0.0, f)
    return np.where(np.isnan(d), np.nan, f)


def fraction_vec(d, d_dec, d_ext, p, model="tan",
                 averianov_p=config.AVERIANOV_P, shah_eps=config.SHAH_EPS):
    """Vectorized dispatch with per-element params. `d`/params are equal-length arrays."""
    model = model.lower()
    d_dec = np.asarray(d_dec, float)
    d_ext = np.asarray(d_ext, float)
    if model == "tan":
        return tan_fraction_vec(d, d_dec, d_ext, p)
    if model == "banta":
        return tan_fraction_vec(d, np.zeros_like(d_ext), d_ext, np.ones_like(d_ext))
    if model == "averianov":
        return tan_fraction_vec(d, np.zeros_like(d_ext), d_ext, np.full_like(d_ext, averianov_p))
    if model == "shah":
        d = np.asarray(d, float)
        span = d_ext - d_dec
        k = np.where(span > 0, -np.log(shah_eps) / np.where(span > 0, span, 1.0), np.nan)
        f = np.exp(-k * np.maximum(d - d_dec, 0.0))
        f = np.where(d <= d_dec, 1.0, f)
        f = np.where(span > 0, f, np.where(d <= d_dec, 1.0, 0.0))
        return np.where(np.isnan(d), np.nan, f)
    raise ValueError(f"unknown model {model!r}")


def mean_fraction_over_pixels(d_pixels, d_dec, d_ext, p, model="tan", **kw):
    """Area-(equal-weight) mean of f over a field's WTD pixels.

    Applying f per pixel then averaging avoids the Jensen bias of f(mean WTD) that
    matters because f is steep (p ~ 10-25). NaN pixels are ignored; returns NaN if
    no valid pixels.
    """
    d_pixels = np.asarray(d_pixels, dtype=float)
    f = etgw_fraction(d_pixels, d_dec, d_ext, p, model=model, **kw)
    if np.all(np.isnan(f)):
        return np.nan
    return float(np.nanmean(f))
