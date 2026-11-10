"""Field and hydrographic-area outlines for the web explorer, in a compact binary layout.

    python build_outlines.py <fields.gpkg> <ha_gw_subsidy.parquet> <data dir> [--field-tol 1] [--ha-tol 5] [--dry]

Writes <data>/fields_geom.json (fields in the order of fields.json, from build_data.py) and
<data>/has_geom.json (HAs in the order of has.json): the binary below, base64-encoded, because artifact
pages serve text types only. Removes the HA placeholder from has.json and records provenance in meta.json.
Every outline is decoded again and compared with the source polygons before anything is written.

Binary layout, little-endian, EPSG:5070:
  Int32[8]            magic 'NWIG' (0x4E574947), version 1, nFeatures, nRings, nPoints, originX, originY, grid (m)
  Uint32[nFeat+1]     first ring of each feature
  Uint32[nRings+1]    first point of each ring
  Int32[2*nRings]     first point of each ring, in grid steps from the origin
  Int16[2*(nPoints-nRings)]  every other point as a step from the previous point
Rings are open (the first point is not repeated). Exterior rings run counter-clockwise and holes clockwise,
so a nonzero fill leaves holes empty and keeps overlapping fields filled.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely

MAGIC, VERSION, GRID = 0x4E574947, 1, 1


def signed_area(c):
    x, y = c[:, 0].astype(np.float64), c[:, 1].astype(np.float64)
    return 0.5 * (np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def split_long_steps(c):
    """Insert points so that no step between consecutive points exceeds the Int16 range."""
    out = [c[0]]
    for q in c[1:]:
        p = out[-1]
        n = int(np.ceil(np.abs(q - p).max() / 32767))
        for t in range(1, n):
            out.append(np.round(p + (q - p) * t / n).astype(np.int64))
        out.append(q)
    return np.asarray(out, dtype=np.int64)


def rings_of(poly):
    return [poly.exterior] + list(poly.interiors)


def encode(geoms, tol):
    """geoms: shapely array in EPSG:5070 (None allowed).

    Returns the bytes, the original (unsimplified) coordinates of every ring kept, in file order, and the
    rings dropped because they collapse on the grid: (feature index, area m2, mean width m)."""
    simp = shapely.simplify(geoms, tol, preserve_topology=True) if tol > 0 else geoms
    b = shapely.bounds(simp)
    ox, oy = (int(np.floor(np.nanmin(b[:, 0]))), int(np.floor(np.nanmin(b[:, 1]))))
    feat_ring, ring_pt, firsts, steps, kept_orig, dropped = [0], [0], [], [], [], []
    split = 0
    for fi, (g, g0) in enumerate(zip(simp, geoms)):
        parts = [] if g is None or g.is_empty else shapely.get_parts(g)
        parts0 = [] if g0 is None or g0.is_empty else shapely.get_parts(g0)
        assert len(parts) == len(parts0)                  # topology-preserving: no part or ring removed
        for poly, poly0 in zip(parts, parts0):
            rs, rs0 = rings_of(poly), rings_of(poly0)
            assert len(rs) == len(rs0)
            for k, (ring, ring0) in enumerate(zip(rs, rs0)):
                c = np.round((np.asarray(ring.coords)[:-1, :2] - [ox, oy]) / GRID).astype(np.int64)
                keep = np.ones(len(c), bool)
                keep[1:] = np.any(c[1:] != c[:-1], axis=1)
                c = c[keep]
                while len(c) > 1 and (c[0] == c[-1]).all():
                    c = c[:-1]
                a = signed_area(c) if len(c) >= 3 else 0.0
                if len(c) < 3 or a == 0:
                    p0 = shapely.Polygon(ring0)
                    dropped.append((fi, p0.area, 2 * p0.area / max(p0.length, 1e-9)))
                    continue
                if (k == 0) != (a > 0):          # exterior counter-clockwise, holes clockwise
                    c = c[::-1]
                if np.abs(np.diff(c, axis=0)).max() > 32767:
                    c = split_long_steps(c)
                    split += 1
                firsts.append(c[0])
                steps.append(np.diff(c, axis=0))
                ring_pt.append(ring_pt[-1] + len(c))
                kept_orig.append(np.asarray(ring0.coords)[:, :2])
        feat_ring.append(len(ring_pt) - 1)
    n_feat, n_rings, n_pts = len(feat_ring) - 1, len(firsts), ring_pt[-1]
    st = np.concatenate(steps) if steps else np.zeros((0, 2), np.int64)
    assert np.abs(st).max() <= 32767
    buf = b"".join([
        np.array([MAGIC, VERSION, n_feat, n_rings, n_pts, ox, oy, GRID], "<i4").tobytes(),
        np.asarray(feat_ring, "<u4").tobytes(), np.asarray(ring_pt, "<u4").tobytes(),
        np.asarray(firsts, "<i4").reshape(-1).tobytes(), st.astype("<i2").reshape(-1).tobytes()])
    return buf, kept_orig, dropped, dict(points=n_pts, rings=n_rings, rings_split=split)


def decode(buf):
    """Same steps as decodeGeom() in the page; returns per-feature rings in metres (EPSG:5070)."""
    hd = np.frombuffer(buf, "<i4", 8)
    assert hd[0] == MAGIC and hd[1] == VERSION
    nf, nr, npt, ox, oy, q = (int(v) for v in hd[2:])
    off = 32
    fr = np.frombuffer(buf, "<u4", nf + 1, off); off += 4 * (nf + 1)
    rp = np.frombuffer(buf, "<u4", nr + 1, off); off += 4 * (nr + 1)
    first = np.frombuffer(buf, "<i4", 2 * nr, off).reshape(-1, 2); off += 8 * nr
    st16 = np.frombuffer(buf, "<i2", 2 * (npt - nr), off)
    assert off + st16.nbytes == len(buf)
    st = st16.reshape(-1, 2).astype(np.int64)
    rings, s = [], 0
    for r in range(nr):
        n = int(rp[r + 1] - rp[r])
        c = np.vstack([first[r], first[r] + np.cumsum(st[s:s + n - 1], axis=0)]) if n > 1 else first[r:r + 1]
        s += n - 1
        rings.append(np.column_stack([ox + c[:, 0] * q, oy + c[:, 1] * q]).astype(np.float64))
    return [rings[fr[i]:fr[i + 1]] for i in range(nf)]


def check(name, orig, buf, kept_orig, dropped, ids=None):
    """Round trip through the decoder: ring-by-ring shape error against the original rings, area change per
    feature, and the rings left out. Returns (max error, median error) in metres."""
    dec = decode(buf)
    flat = [r for rs in dec for r in rs]
    assert len(flat) == len(kept_orig)
    def lines(rings):
        return shapely.linestrings(np.vstack(rings), indices=np.repeat(np.arange(len(rings)), [len(r) for r in rings]))
    hd = shapely.hausdorff_distance(lines([np.vstack([r, r[:1]]) for r in flat]), lines(kept_orig))
    have = ~shapely.is_missing(orig)
    area = np.array([sum(signed_area(r) for r in rs) for rs in dec])
    ra = area[have] / shapely.area(orig[have]) - 1
    print(f"  {name}: {len(buf) / 1e6:.2f} MB; drawn rings within {hd.max():.2f} m of the original (median {np.median(hd):.2f}, "
          f"99.9% within {np.quantile(hd, 0.999):.2f}); area change median {np.median(np.abs(ra)):.1e}")
    if dropped:
        da = np.array([d[1] for d in dropped]); dw = np.array([d[2] for d in dropped])
        print(f"    left out: {len(dropped)} rings that collapse on the {GRID} m grid; largest {da.max():.1f} m2, widest {dw.max():.2f} m "
              f"(mean width); in {len({d[0] for d in dropped})} features")
    if ids is not None:
        acres = shapely.area(orig) / 4046.8564224
        for j in np.argsort(-np.abs(ra))[:3]:
            print(f"    largest area change: {ids[j]} {ra[j]:+.2%} of {acres[j]:.2f} acres")
    return float(hd.max()), float(np.median(hd)), len(dropped), (float(max(d[1] for d in dropped)) if dropped else 0.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gpkg"); ap.add_argument("ha_parquet"); ap.add_argument("data")
    ap.add_argument("--field-tol", type=float, default=1.0); ap.add_argument("--ha-tol", type=float, default=5.0)
    ap.add_argument("--dry", action="store_true", help="measure only; write nothing")
    a = ap.parse_args()
    data, gpkg = Path(a.data), Path(a.gpkg)
    fields, has, meta = (json.loads((data / f"{n}.json").read_text()) for n in ("fields", "has", "meta"))

    g = gpd.read_file(gpkg, columns=["NWI_ID"], engine="pyogrio")
    assert g.NWI_ID.is_unique and set(g.NWI_ID) == set(fields["id"]) and len(g) == len(fields["id"])
    fg = g.set_index("NWI_ID").reindex(fields["id"]).to_crs(5070).geometry.values
    fbuf, fkept, fdrop, finfo = encode(fg, a.field_tol)
    print(f"fields: {len(fields['id'])}, tolerance {a.field_tol} m, {finfo}")
    f_max, f_med, f_ndrop, f_dropmax = check("fields", fg, fbuf, fkept, fdrop, fields["id"])

    h = gpd.read_parquet(a.ha_parquet, columns=["HYD_AREA", "geometry"]).to_crs(5070).set_index("HYD_AREA")
    hg = h.reindex(has["code"]).geometry.values
    missing = [c for c, x in zip(has["code"], hg) if x is None]
    hbuf, hkept, hdrop, hinfo = encode(hg, a.ha_tol)
    print(f"HAs: {len(has['code'])}, {len(missing)} without a polygon {missing}, tolerance {a.ha_tol} m, {hinfo}")
    h_max, _, _, _ = check("HAs", hg, hbuf, hkept, hdrop)
    if a.dry:
        return

    # Artifact pages serve text types only, so the bytes travel base64-encoded inside JSON.
    for name, buf in (("fields_geom", fbuf), ("has_geom", hbuf)):
        (data / f"{name}.json").write_text(json.dumps(
            {"format": "NWIG", "version": VERSION, "encoding": "base64", "bytes": len(buf),
             "data": base64.b64encode(buf).decode("ascii")}, separators=(",", ":")))
        (data / f"{name}.bin").unlink(missing_ok=True)
    has.pop("rings", None)
    (data / "has.json").write_text(json.dumps(has, separators=(",", ":")))
    md5 = hashlib.md5(gpkg.read_bytes()).hexdigest()
    meta["geom"] = {"fields_file": "fields_geom.json", "source": gpkg.name, "md5": md5, "last_change": "2026-09-21",
                    "simplify_m": a.field_tol, "grid_m": GRID, "max_error_m": round(f_max, 2), "median_error_m": round(f_med, 2),
                    "rings_left_out": f_ndrop, "largest_left_out_m2": round(f_dropmax, 1),
                    "ha_file": "has_geom.json", "ha_source": "NWI_Investigations (projects/ee-prevelle/assets/NV/NWI_Investigations)",
                    "ha_simplify_m": a.ha_tol, "ha_max_error_m": round(h_max, 1)}
    (data / "meta.json").write_text(json.dumps(meta, separators=(",", ":")))
    for n in ("fields_geom.json", "has_geom.json", "has.json", "meta.json"):
        print(f"wrote {n}: {(data / n).stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
