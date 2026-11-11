#!/usr/bin/env python3
"""
GEE app input 3: the depth-to-groundwater (DTW) rasters, in EPSG:5070.

New map (what the app calls "new DTW"): the handily NV parity DTW at 30 m,
gs://ucrc-wtd/handily/nv/handily_dtw_nv_parity_30m.tif (2026-10-21, MD5 974338d10a021dcca7e52c70e2c44fba).
It is already NAD83 / Conus Albers (EPSG:5070); the same pixels are rewritten with DEFLATE compression
(the bucket copy is ZSTD), so Earth Engine can ingest them.

Run's map: nv_dtw_parity_statewide_100m.tif from nv_dtw_parity_statewide_100m.zip (2026-10-08 build,
MD5 20af7ee2027ac234774f5a026ee9b4d8), the map the run and the report used, kept for comparison, and
its uncertainty nv_sigma_parity_statewide_100m.tif (the bucket has no sigma layer). Their coordinates
are Conus Albers but the files carry a LOCAL_CS with no EPSG code, so they are written with the
EPSG:5070 tag; no resampling. Both grids start at the same corner.

Ma et al. (2026): wtd_nevada.tif (the 24 m ParFlow-CONUS2 grid used by nwi-gw-subsidy) averaged onto
the new map's 30 m grid (area-weighted mean), so both maps go through the same steps on the same grid,
as in the report's comparison (which used the 100 m grid of the run's map).

Outputs (under --out/rasters): dtw_new_30m_epsg5070.tif, dtw_ma_30m_epsg5070.tif,
dtw_run_100m_epsg5070.tif, dtw_sigma_100m_epsg5070.tif. Nodata -9999, float32, tiled, deflate.

Usage:
    python 03_dtw_rasters.py --new-tif handily_dtw_nv_parity_30m.tif --dtw-dir DIR --ma-tif PATH
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.warp import Resampling, reproject

from _common import out_dir

EPSG5070 = CRS.from_epsg(5070)
NODATA = -9999.0
PROFILE = dict(driver="GTiff", dtype="float32", count=1, nodata=NODATA, tiled=True, blockxsize=512,
               blockysize=512, compress="deflate", predictor=2, zlevel=6, BIGTIFF="IF_SAFER")


def tag_5070(src_path: Path, dst_path: Path, res: float):
    """Copy a Conus Albers raster, setting CRS to EPSG:5070 and nodata to -9999 (values unchanged)."""
    with rasterio.open(src_path) as src:
        assert abs(src.res[0] - res) < 1e-9 and abs(src.res[1] - res) < 1e-9, src.res
        assert "Conus Albers" in src.crs.to_wkt(), src.crs.to_wkt()
        prof = dict(PROFILE, width=src.width, height=src.height, transform=src.transform, crs=EPSG5070)
        with rasterio.open(dst_path, "w", **prof) as dst:
            for _, win in src.block_windows(1):
                a = src.read(1, window=win, masked=True)
                dst.write(a.filled(NODATA).astype("float32"), 1, window=win)
        grid = (src.width, src.height, src.transform)
    print(f"[dtw] {dst_path.name}: {grid[0]} x {grid[1]} at {res:g} m, origin ({grid[2].c}, {grid[2].f})", flush=True)
    return grid


def warp_to_grid(src_path: Path, dst_path: Path, grid, method: str):
    """Warp a raster onto the given EPSG:5070 grid with the named resampling method."""
    width, height, transform = grid
    dst = np.full((height, width), NODATA, dtype="float32")
    with rasterio.open(src_path) as src:
        reproject(source=rasterio.band(src, 1), destination=dst, src_nodata=src.nodata,
                  dst_transform=transform, dst_crs=EPSG5070, dst_nodata=NODATA,
                  resampling=getattr(Resampling, method), num_threads=8, warp_mem_limit=8192)
    prof = dict(PROFILE, width=width, height=height, transform=transform, crs=EPSG5070)
    with rasterio.open(dst_path, "w", **prof) as out:
        out.write(dst, 1)
    v = dst[dst != NODATA]
    print(f"[ma] {dst_path.name}: {v.size:,} valid pixels, median {np.median(v):.2f} m", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--new-tif", type=Path, required=True, help="handily_dtw_nv_parity_30m.tif from gs://ucrc-wtd/handily/nv/")
    ap.add_argument("--dtw-dir", type=Path, required=True,
                    help="unzipped nv_dtw_parity_statewide_100m.zip: the run's map and its sigma")
    ap.add_argument("--ma-tif", type=Path, required=True, help="Ma et al. wtd_nevada.tif")
    ap.add_argument("--ma-method", default="average")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    rdir = out_dir(args.out) / "rasters"
    rdir.mkdir(exist_ok=True)

    grid = tag_5070(args.new_tif, rdir / "dtw_new_30m_epsg5070.tif", 30)
    grid_run = tag_5070(args.dtw_dir / "nv_dtw_parity_statewide_100m.tif", rdir / "dtw_run_100m_epsg5070.tif", 100)
    grid_s = tag_5070(args.dtw_dir / "nv_sigma_parity_statewide_100m.tif", rdir / "dtw_sigma_100m_epsg5070.tif", 100)
    assert grid_run == grid_s, "sigma grid differs from the run's DTW grid"
    assert (grid[2].c, grid[2].f) == (grid_run[2].c, grid_run[2].f), "the 30 m and 100 m grids should share a corner"
    warp_to_grid(args.ma_tif, rdir / "dtw_ma_30m_epsg5070.tif", grid, args.ma_method)


if __name__ == "__main__":
    main()
