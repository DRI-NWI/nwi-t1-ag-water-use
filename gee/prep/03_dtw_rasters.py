#!/usr/bin/env python3
"""
GEE app input 3: the depth-to-groundwater rasters on one 100 m EPSG:5070 grid.

New map: nv_dtw_parity_statewide_100m.tif and nv_sigma_parity_statewide_100m.tif from
nv_dtw_parity_statewide_100m.zip (MD5 20af7ee2027ac234774f5a026ee9b4d8). Their coordinates are
already NAD83 / Conus Albers, but the files carry a LOCAL_CS with no EPSG code; the run used copies
tagged EPSG:5070 (nv_dtw_parity_statewide_100m_epsg5070.tif). This script writes the same pixels
with the EPSG:5070 tag; no resampling.

Ma et al. (2026): wtd_nevada.tif (the 24 m ParFlow-CONUS2 grid used by nwi-gw-subsidy) warped
onto the same 100 m grid, as the report's comparison did (ma_wtd_nv_100m_epsg5070.tif). The
resampling method of that file is not recorded; step 4 compares the candidates written here
against the report's HA values and keeps the one that reproduces them.

Outputs (under --out/rasters): dtw_new_100m_epsg5070.tif, dtw_sigma_100m_epsg5070.tif,
dtw_ma_100m_epsg5070_<method>.tif. Nodata -9999, float32, tiled, deflate.

Usage:
    python 03_dtw_rasters.py --dtw-dir DIR --ma-tif PATH [--ma-methods nearest bilinear average]
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


def tag_5070(src_path: Path, dst_path: Path):
    """Copy a Conus Albers raster, setting CRS to EPSG:5070 and nodata to -9999 (values unchanged)."""
    with rasterio.open(src_path) as src:
        assert abs(src.res[0] - 100) < 1e-9 and abs(src.res[1] - 100) < 1e-9, src.res
        assert "Conus Albers" in src.crs.to_wkt(), src.crs.to_wkt()
        prof = dict(PROFILE, width=src.width, height=src.height, transform=src.transform, crs=EPSG5070)
        with rasterio.open(dst_path, "w", **prof) as dst:
            for _, win in src.block_windows(1):
                a = src.read(1, window=win, masked=True)
                dst.write(a.filled(NODATA).astype("float32"), 1, window=win)
        grid = (src.width, src.height, src.transform)
    print(f"[dtw] {dst_path.name}: {grid[0]} x {grid[1]}, origin ({grid[2].c}, {grid[2].f})")
    return grid


def warp_to_grid(src_path: Path, dst_path: Path, grid, method: str):
    """Warp a raster onto the given 100 m EPSG:5070 grid with the named resampling method."""
    width, height, transform = grid
    dst = np.full((height, width), NODATA, dtype="float32")
    with rasterio.open(src_path) as src:
        reproject(source=rasterio.band(src, 1), destination=dst, src_nodata=src.nodata,
                  dst_transform=transform, dst_crs=EPSG5070, dst_nodata=NODATA,
                  resampling=getattr(Resampling, method), num_threads=8, warp_mem_limit=4096)
    prof = dict(PROFILE, width=width, height=height, transform=transform, crs=EPSG5070)
    with rasterio.open(dst_path, "w", **prof) as out:
        out.write(dst, 1)
    v = dst[dst != NODATA]
    print(f"[ma] {dst_path.name}: {v.size:,} valid pixels, median {np.median(v):.2f} m")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dtw-dir", type=Path, required=True, help="folder with the unzipped nv_*_parity_* rasters")
    ap.add_argument("--ma-tif", type=Path, required=True, help="Ma et al. wtd_nevada.tif")
    ap.add_argument("--ma-methods", nargs="+", default=["nearest", "bilinear", "average"])
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    rdir = out_dir(args.out) / "rasters"
    rdir.mkdir(exist_ok=True)

    grid = tag_5070(args.dtw_dir / "nv_dtw_parity_statewide_100m.tif", rdir / "dtw_new_100m_epsg5070.tif")
    grid_s = tag_5070(args.dtw_dir / "nv_sigma_parity_statewide_100m.tif", rdir / "dtw_sigma_100m_epsg5070.tif")
    assert grid == grid_s, "sigma grid differs from the DTW grid"
    for method in args.ma_methods:
        warp_to_grid(args.ma_tif, rdir / f"dtw_ma_100m_epsg5070_{method}.tif", grid, method)


if __name__ == "__main__":
    main()
