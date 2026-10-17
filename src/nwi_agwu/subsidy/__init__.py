"""Groundwater-ET subsidy (vendored from github/nwi-gw-subsidy 7262f69, 2026-06-28).

Changes from the source, none to the method: paths and constants come from the run
configuration instead of a module-level config; fields are keyed by NWI_ID (the
2026-08-20 canonical key) instead of OPENET_ID; the WTD pixel area is taken from the
raster (the source hardcoded the 24 m Ma et al. pixel); POLARIS uses project nasa-water.
"""
