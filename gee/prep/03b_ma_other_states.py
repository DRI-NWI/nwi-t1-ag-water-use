#!/usr/bin/env python3
"""
GEE app input 3b: Ma et al. (2026) outside Nevada, from the dataset's other state files.

Step 3 averages wtd_nevada.tif onto the new map's 30 m grid. That file covers Nevada only, so the
part of the new map outside Nevada (including the out-of-state parts of the HAs that cross the
state line) had no Ma et al. value. The dataset's other state files are in the GEE community
catalog, projects/sat-io/open-datasets/HRES-WTD: one image per state (property `state`, band b1,
depth to water in m), each its state's file reprojected to EPSG:4326 at about 1 arc-second. They are
the same 49 images as projects/nmose-openet/assets/WTD-US, which nwi-gw-subsidy used.

Every 30 m pixel where the new map has a value and the Nevada file has none is filled. The other
states' pixels are downloaded on their own grids (Earth Engine does no resampling) and averaged
onto the 30 m grid the way step 3 averages wtd_nevada.tif (area-weighted mean). The first state in
--states with a value fills the pixel. Nevada pixels are unchanged, and depths <= 0 are kept, as in
step 3.

As a check, the Nevada image of the same collection is taken through the same route for
--check-tiles tiles inside Nevada and compared with step 3's average of wtd_nevada.tif.

Input (under --out/rasters): dtw_new_30m_epsg5070.tif and dtw_ma_nv_30m_epsg5070.tif (step 3).
Outputs: rasters/dtw_ma_30m_epsg5070.tif, 03b_ma_other_states_checks.json.

Usage:
    python 03b_ma_other_states.py [--ee-project nwi-usgs] [--out DIR]
"""

from __future__ import annotations

import argparse
import io
import json
import multiprocessing as mp
import time

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import Affine
from rasterio.warp import Resampling, reproject, transform_bounds
from rasterio.windows import Window
from rasterio.windows import bounds as window_bounds
from rasterio.windows import transform as window_transform

from _common import out_dir

COLLECTION = "projects/sat-io/open-datasets/HRES-WTD"
STATES = ["CA", "OR", "ID", "UT", "AZ"]      # Nevada's neighbours, in fill order
EPSG5070, EPSG4326 = CRS.from_epsg(5070), CRS.from_epsg(4326)
NODATA = -9999.0
PROFILE = dict(driver="GTiff", dtype="float32", count=1, nodata=NODATA, tiled=True, blockxsize=512,
               blockysize=512, compress="deflate", predictor=2, zlevel=6, BIGTIFF="IF_SAFER")   # as step 3
TILE = 1024                                   # 30 m pixels per tile side
PAD = 2                                       # native pixels added around each request

_ee = None                                    # Earth Engine module, one per worker process


def _init(project):
    global _ee
    import ee
    ee.Initialize(project=project)
    _ee = ee


def _fetch(job):
    """One state's pixels on its own grid, masked pixels as NODATA."""
    img = _ee.Image(job["id"]).select("b1").unmask(NODATA, False)
    grid = {"dimensions": {"width": job["w"], "height": job["h"]},
            "affineTransform": {"scaleX": job["sx"], "shearX": 0, "translateX": job["tx"],
                                "shearY": 0, "scaleY": job["sy"], "translateY": job["ty"]},
            "crsCode": "EPSG:4326"}
    for attempt in range(6):
        try:
            a = np.load(io.BytesIO(_ee.data.computePixels({"expression": img, "fileFormat": "NPY", "grid": grid})))
            return job, np.asarray(a["b1"] if a.dtype.names else a, dtype="float32")
        except Exception:                     # transient Earth Engine errors: retry with backoff
            if attempt == 5:
                raise
            time.sleep(5 * 2 ** attempt)


def native_window(img, lonlat_bounds):
    """Request covering the lon/lat bounds (plus PAD pixels) on the image's own grid, or None."""
    sx, _, x0, _, sy, y0 = img["t"]
    width, height = img["dims"]
    lon0, lat0, lon1, lat1 = lonlat_bounds
    c0 = max(int(np.floor((lon0 - x0) / sx)) - PAD, 0)
    c1 = min(int(np.ceil((lon1 - x0) / sx)) + PAD, width)
    r0 = max(int(np.floor((lat1 - y0) / sy)) - PAD, 0)          # sy < 0: north edge first
    r1 = min(int(np.ceil((lat0 - y0) / sy)) + PAD, height)
    if c1 <= c0 or r1 <= r0:
        return None
    assert (c1 - c0) * (r1 - r0) * 4 < 40e6, "request too large for computePixels"
    return {"id": img["id"], "w": c1 - c0, "h": r1 - r0, "sx": sx, "sy": sy, "tx": x0 + c0 * sx, "ty": y0 + r0 * sy}


def average_onto(arr, job, shape, dst_transform):
    """Area-weighted mean of the native pixels on a 30 m EPSG:5070 window (as step 3)."""
    dst = np.full(shape, NODATA, dtype="float32")
    reproject(source=arr, destination=dst, src_transform=Affine(job["sx"], 0, job["tx"], 0, job["sy"], job["ty"]),
              src_crs=EPSG4326, src_nodata=NODATA, dst_transform=dst_transform, dst_crs=EPSG5070,
              dst_nodata=NODATA, resampling=Resampling.average)
    return dst


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ee-project", default="nwi-usgs")
    ap.add_argument("--collection", default=COLLECTION)
    ap.add_argument("--states", nargs="+", default=STATES, help="states that fill, in order")
    ap.add_argument("--check-tiles", type=int, default=12, help="Nevada tiles for the route check")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    out = out_dir(args.out)
    rdir = out / "rasters"
    new_tif, nv_tif, dst_tif = (rdir / "dtw_new_30m_epsg5070.tif", rdir / "dtw_ma_nv_30m_epsg5070.tif",
                                rdir / "dtw_ma_30m_epsg5070.tif")

    import ee
    ee.Initialize(project=args.ee_project)
    feats = (ee.ImageCollection(args.collection).filter(ee.Filter.inList("state", args.states + ["NV"]))
             .getInfo()["features"])
    imgs = {}
    for f in feats:
        b = f["bands"][0]
        assert b["id"] == "b1" and b["crs"] == "EPSG:4326", (f["id"], b["id"], b["crs"])
        imgs[f["properties"]["state"]] = {"id": f["id"], "t": b["crs_transform"], "dims": b["dimensions"]}
    assert set(imgs) == set(args.states) | {"NV"}, f"missing states: {set(args.states) | {'NV'} - set(imgs)}"

    # ---- tiles with pixels to fill (new map has a value, the Nevada file has none) ---------
    tiles, inside = [], []
    with rasterio.open(new_tif) as n, rasterio.open(nv_tif) as m:
        assert (n.width, n.height, n.transform, n.crs) == (m.width, m.height, m.transform, m.crs), "grids differ"
        W, H, T = m.width, m.height, m.transform
        n_nv = 0
        for r0 in range(0, H, TILE):
            for c0 in range(0, W, TILE):
                win = Window(c0, r0, min(TILE, W - c0), min(TILE, H - r0))
                nv_ok = m.read(1, window=win) != NODATA
                n_nv += int(nv_ok.sum())
                fill = (n.read(1, window=win) != NODATA) & ~nv_ok
                if fill.any():
                    tiles.append((win, fill))
                elif nv_ok.all():
                    inside.append(win)
    n_fill = int(sum(f.sum() for _, f in tiles))
    print(f"[ma] {n_fill:,} pixels to fill ({n_fill * 900 / 1e6:,.0f} km2) in {len(tiles)} tiles; "
          f"{n_nv:,} pixels from wtd_nevada.tif", flush=True)

    jobs = []
    for k, (win, fill) in enumerate(tiles):
        rows, cols = np.flatnonzero(fill.any(1)), np.flatnonzero(fill.any(0))
        sub = Window(win.col_off + cols[0], win.row_off + rows[0], cols[-1] - cols[0] + 1, rows[-1] - rows[0] + 1)
        lonlat = transform_bounds(EPSG5070, EPSG4326, *window_bounds(sub, T), densify_pts=21)
        for st in args.states:
            j = native_window(imgs[st], lonlat)
            if j:
                jobs.append(dict(j, kind="fill", tile=k, state=st, sub=(sub.col_off, sub.row_off, sub.width, sub.height)))
    step = max(1, len(inside) // max(args.check_tiles, 1))
    for win in inside[::step][:args.check_tiles]:
        lonlat = transform_bounds(EPSG5070, EPSG4326, *window_bounds(win, T), densify_pts=21)
        jobs.append(dict(native_window(imgs["NV"], lonlat), kind="check", state="NV",
                         sub=(win.col_off, win.row_off, win.width, win.height)))
    mb = sum(j["w"] * j["h"] * 4 for j in jobs) / 1e6
    print(f"[ma] {len(jobs)} requests to {args.collection} ({mb:,.0f} MB), {args.workers} workers", flush=True)

    # ---- download and average onto the 30 m grid ----------------------------------------------
    cand, check = {}, []
    t0 = time.time()
    with mp.get_context("spawn").Pool(args.workers, initializer=_init, initargs=(args.ee_project,)) as pool:
        for i, (job, arr) in enumerate(pool.imap_unordered(_fetch, jobs), 1):
            c0, r0, w, h = job["sub"]
            sub = Window(c0, r0, w, h)
            v = average_onto(arr, job, (h, w), window_transform(sub, T))
            if job["kind"] == "fill":
                cand[(job["tile"], job["state"])] = v
            else:
                check.append((sub, v))
            if i % 50 == 0 or i == len(jobs):
                print(f"[ma]   {i}/{len(jobs)} ({time.time() - t0:,.0f} s)", flush=True)

    # ---- fill, state by state in --states order ------------------------------------------------
    filled, by_state, unfilled = {}, {st: 0 for st in args.states}, 0
    for k, (win, fill) in enumerate(tiles):
        vals = np.full(fill.shape, NODATA, dtype="float32")
        todo = fill.copy()
        for st in args.states:
            v = cand.pop((k, st), None)
            if v is None:
                continue
            j = next(j for j in jobs if j["kind"] == "fill" and j["tile"] == k and j["state"] == st)
            c0, r0, w, h = j["sub"]
            full = np.full(fill.shape, NODATA, dtype="float32")
            full[r0 - win.row_off:r0 - win.row_off + h, c0 - win.col_off:c0 - win.col_off + w] = v
            take = todo & (full != NODATA)
            vals[take] = full[take]
            todo &= ~take
            by_state[st] += int(take.sum())
        filled[(win.row_off, win.col_off)] = vals
        unfilled += int(todo.sum())

    with rasterio.open(nv_tif) as m, rasterio.open(dst_tif, "w", **dict(PROFILE, width=W, height=H, transform=T,
                                                                         crs=EPSG5070)) as dst:
        for r0 in range(0, H, TILE):
            for c0 in range(0, W, TILE):
                win = Window(c0, r0, min(TILE, W - c0), min(TILE, H - r0))
                a = m.read(1, window=win)
                v = filled.get((r0, c0))
                if v is not None:
                    a = np.where(v != NODATA, v, a)
                dst.write(a.astype("float32"), 1, window=win)

    # ---- route check: Nevada's catalog image vs step 3's average of wtd_nevada.tif -------------
    d, ref_all = [], []
    with rasterio.open(nv_tif) as m:
        for sub, v in check:
            ref = m.read(1, window=sub)
            ok = (ref != NODATA) & (v != NODATA)
            d.append((v - ref)[ok])
            ref_all.append(ref[ok])
    d, ref_all = np.concatenate(d), np.concatenate(ref_all)
    ad = np.abs(d)
    route = {"tiles": len(check), "pixels": int(d.size), "median_abs_diff_m": float(np.median(ad)),
             "p95_abs_diff_m": float(np.percentile(ad, 95)), "mean_diff_m": float(d.mean()),
             "share_within_0.5m": float((ad <= 0.5).mean()), "share_within_10pct": float((ad <= 0.1 * np.abs(ref_all)).mean()),
             "r": float(np.corrcoef(ref_all, ref_all + d)[0, 1])}
    checks = {"collection": args.collection, "states": args.states,
              "pixels_from_wtd_nevada": n_nv, "pixels_to_fill": n_fill, "filled_by_state": by_state,
              "unfilled": unfilled, "route_check_nevada": route}
    (out / "03b_ma_other_states_checks.json").write_text(json.dumps(checks, indent=2))
    print(f"[ma] filled {sum(by_state.values()):,} pixels: {by_state}; {unfilled:,} left without a value", flush=True)
    print(f"[ma] route check on {route['tiles']} Nevada tiles ({route['pixels']:,} pixels): catalog image vs "
          f"wtd_nevada.tif, median |diff| {route['median_abs_diff_m']:.3f} m, 95th pct {route['p95_abs_diff_m']:.2f} m, "
          f"mean diff {route['mean_diff_m']:+.3f} m, {route['share_within_10pct']:.1%} within 10%, r {route['r']:.4f}", flush=True)
    print(f"[ma] wrote {dst_tif.name}", flush=True)


if __name__ == "__main__":
    main()
