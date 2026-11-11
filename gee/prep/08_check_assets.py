#!/usr/bin/env python3
"""
GEE app check 8: the uploaded assets reproduce the report, and the app's expressions run.

On Earth Engine, from the assets in projects/<project>/assets/nwi_gw_subsidy/:
  1. property types survived ingestion (HA codes are text, values are numbers), and every asset is
     readable by anyone, so the app runs from any Earth Engine account (--no-public skips this);
  2. the report's numbers from the run's own values (S_RUN_TAN, made with the run's 2026-10-08 map):
     Tan subsidy 34,733 acre-ft/yr, ETa, and the HA values of docs/tables/ha_gw_subsidy_new_vs_ma.csv;
  3. field-year and HA-year rows add up to the field and HA means (x 41 years);
  4. every map layer and query the app builds (same expressions as nwi_gw_et_explorer_app.js) can be
     drawn or evaluated.

Usage:
    python 08_check_assets.py [--project nwi-usgs] [--no-public] [--out DIR]
"""

from __future__ import annotations

import argparse
import math

import pandas as pd

from _common import NY, REPO, out_dir

ROOT = "projects/{}/assets/nwi_gw_subsidy/"
HA_TABLE = REPO / "docs" / "tables" / "ha_gw_subsidy_new_vs_ma.csv"
BRK_CHG = [-300, -100, -30, -10, -3, -0.5, 0.5, 3, 10, 30, 100, 300]
BRK_F = [0.001, 0.01, 0.05, 0.1, 0.2, 0.4, 0.7]
failures = []


def check(name, ok, detail=""):
    print(f"  [{'ok' if ok else 'FAIL'}] {name}{(': ' + detail) if detail else ''}", flush=True)
    if not ok:
        failures.append(name)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default="nwi-usgs")
    ap.add_argument("--public", action=argparse.BooleanOptionalAction, default=True,
                    help="check that every asset is readable by anyone (default)")
    ap.add_argument("--out", default=None, help="folder of steps 1-7 (for the expected HA ranking)")
    args = ap.parse_args()
    import ee
    ee.Initialize(project=args.project)
    root = ROOT.format(args.project)
    fields = ee.FeatureCollection(root + "fields_gw_subsidy_20261018")
    fyears = ee.FeatureCollection(root + "field_year_gw_subsidy_20261018")
    has = ee.FeatureCollection(root + "ha_gw_subsidy_20261018")
    hyears = ee.FeatureCollection(root + "ha_year_gw_subsidy_20261018")
    wtrain = ee.FeatureCollection(root + "wells_handily_train_nv")
    wall = ee.FeatureCollection(root + "wells_gwx_nv")
    dtw = ee.Image(root + "dtw_epsg5070")

    print("1. Assets and property types")
    sizes = ee.Dictionary({"fields": fields.size(), "fyears": fyears.size(), "has": has.size(),
                           "hyears": hyears.size(), "wtrain": wtrain.size(), "wall": wall.size()}).getInfo()
    check("row counts", sizes == {"fields": 29338, "fyears": 29338 * NY, "has": 190, "hyears": 6724,
                                  "wtrain": 9448, "wall": 137138}, str(sizes))
    f0 = fields.filter(ee.Filter.eq("NWI_ID", "NV_16742")).first()
    p = f0.toDictionary().getInfo()
    check("HYD_AREA kept as text", isinstance(p["HYD_AREA"], str) and p["HYD_AREA"] == "177", repr(p["HYD_AREA"]))
    check("numbers are numbers", all(isinstance(p[k], (int, float)) for k in ("S_NEW_TAN", "F_MA_SHAH", "WT_DIST_KM")))
    check("field has a polygon", f0.geometry().type().getInfo() in ("Polygon", "MultiPolygon"))
    h0 = has.filter(ee.Filter.eq("HYD_AREA", "014")).first().toDictionary().getInfo()
    check("HA '014' found by its text code", h0.get("HA_NAME") == "SurpriseValley", h0.get("HA_NAME"))
    check("DTW bands", dtw.bandNames().getInfo() == ["dtw_new_m", "dtw_ma_m", "dtw_sigma_m", "dtw_run_m"])
    proj = dtw.select("dtw_new_m").projection().getInfo()
    check("DTW projection: new map EPSG:5070 at 30 m", proj["crs"] == "EPSG:5070" and proj["transform"][0] == 30, str(proj))
    scales = {b: round(dtw.select(b).projection().nominalScale().getInfo()) for b in ("dtw_ma_m", "dtw_sigma_m", "dtw_run_m")}
    check("other bands: Ma 30 m, sigma and run's map 100 m", scales == {"dtw_ma_m": 30, "dtw_sigma_m": 100, "dtw_run_m": 100},
          str(scales))
    if args.public:
        names = ["fields_gw_subsidy_20261018", "field_year_gw_subsidy_20261018", "ha_gw_subsidy_20261018",
                 "ha_year_gw_subsidy_20261018", "wells_handily_train_nv", "wells_gwx_nv", "dtw_epsg5070"]
        private = [n for n in names if not any("allUsers" in b.get("members", [])
                                              for b in ee.data.getIamPolicy(root + n).get("bindings", []))]
        check("every asset is readable by anyone", not private, ", ".join(private) or f"{len(names)} assets")

    print("2. The report's numbers (the run's values)")
    irr = fields.filter(ee.Filter.gt("N_IRR_YRS", 0))
    s = irr.reduceColumns(ee.Reducer.sum().repeat(2), ["S_RUN_TAN", "ETA_AF"]).get("sum").getInfo()
    check("Tan, run = 34,733 acre-ft/yr", round(s[0]) == 34733, f"{s[0]:,.2f}")
    check("ETa = 1,423,458 acre-ft/yr", round(s[1]) == 1423458, f"{s[1]:,.2f}")
    ref = pd.read_csv(HA_TABLE, dtype={"HYD_AREA": str}).set_index("HYD_AREA")
    hl = has.reduceColumns(ee.Reducer.toList().repeat(3), ["HYD_AREA", "S_RUN_TAN", "ETA_AF"]).get("list").getInfo()
    got = pd.DataFrame({"S_RUN_TAN": hl[1], "ETA_AF": hl[2]}, index=hl[0])
    j = ref.join(got, how="left")
    d1 = (j["subsidy_new_acre_ft_yr"] - j["S_RUN_TAN"].round(2)).abs().max()
    d2 = (j["ETa_acre_ft_yr"] - j["ETA_AF"].round(2)).abs().max()
    check("HA subsidy (the run) = docs table", d1 <= 0.0051, f"max |diff| {d1:.3f}")
    check("HA ETa = docs table", d2 <= 0.0051, f"max |diff| {d2:.3f}")

    print("3. Annual rows add up")
    yl = fyears.filter(ee.Filter.eq("NWI_ID", "NV_16742")).reduceColumns(
        ee.Reducer.toList().repeat(4), ["YEAR", "IRR", "S_RUN_TAN", "S_NEW_TAN"]).get("list").getInfo()
    for k, col in ((2, "S_RUN_TAN"), (3, "S_NEW_TAN")):
        tot = sum(v for irr_, v in zip(yl[1], yl[k]) if irr_ == 1) / NY
        check(f"field NV_16742, {col}: 41 years, irrigated years / 41 = field mean", len(yl[0]) == NY and
              math.isclose(tot, p[col], rel_tol=1e-6, abs_tol=1e-9), f"{tot:.6f} vs {p[col]:.6f}")
    hy = hyears.filter(ee.Filter.eq("HYD_AREA", "073")).aggregate_sum("S_RUN_TAN").getInfo() / NY
    check("HA 073: HA-year sum / 41 = HA total", math.isclose(hy, ref.loc["073", "subsidy_new_acre_ft_yr"], abs_tol=0.0051),
          f"{hy:,.2f}")

    print("4. The app's layers and queries")
    m = "TAN"

    def classify(img, brk):
        c = ee.Image(0)
        for i, b in enumerate(brk):
            c = c.where(img.gte(b), i + 1)
        return c.updateMask(img.mask()).toByte()

    def tile(img, vis, name):
        try:
            mid = img.getMapId(vis)
            check(f"layer draws: {name}", bool(mid.get("mapid")))
        except Exception as e:
            check(f"layer draws: {name}", False, str(e)[:200])

    shown = irr.filter(ee.Filter.Or(ee.Filter.gte("D_" + m, 10), ee.Filter.lte("D_" + m, -10))) \
               .filter(ee.Filter.gte("WT_DIST_KM", 5))
    tile(classify(ee.Image().float().paint(shown, "D_" + m), BRK_CHG), {"min": 0, "max": 12}, "fields, change, filtered")
    pv = irr.map(lambda ft: ft.set("v", ee.Number(ft.get("S_NEW_" + m)).multiply(100)
                                   .divide(ee.Number(ft.get("ETA_AF")).max(1e-9))))
    tile(classify(ee.Image().float().paint(pv, "v"), [0.5, 1, 2, 5, 10, 20, 40]), {"min": 0, "max": 7}, "fields, % of ETa")
    tile(classify(dtw.select("dtw_new_m").subtract(dtw.select("dtw_ma_m")), [-15, -5, -2, -1, -0.25, 0.25, 1, 2, 5, 15]),
         {"min": 0, "max": 10}, "DTW change")
    dist = wtrain.distance(searchRadius=40000, maxError=50).divide(1000).clipToCollection(has)
    tile(classify(dist, [1, 2, 5, 10, 20]), {"min": 0, "max": 5}, "distance to training wells")
    wp = fields.filter(ee.Filter.notNull(["DDEC_M", "DEXT_M", "P"]))
    dd, de, pp = (ee.Image().float().paint(wp, k) for k in ("DDEC_M", "DEXT_M", "P"))
    d = dtw.select("dtw_new_m")
    span = de.subtract(dd)
    ft = de.subtract(d).divide(span).clamp(0, 1).pow(pp).where(d.lte(dd), 1).where(d.gte(de), 0)
    fimg = ft.where(span.lte(0), d.lte(dd)).clamp(0, 1).updateMask(dd.mask())
    tile(classify(fimg, BRK_F), {"min": 0, "max": 7}, "f per pixel, Tan")
    tile(classify(ee.Image().float().paint(has.filter(ee.Filter.gt("N_IRR", 0)), "D_" + m),
                  [-3000, -1000, -300, -100, -30, -10, 10, 30, 100, 300, 1000, 3000]), {"min": 0, "max": 12}, "HA change")
    styled = wtrain.map(lambda w: w.set("style", ee.Dictionary(ee.Algorithms.If(
        ee.String(w.get("role")).equals("monitoring"), {"pointSize": 4, "color": "000000", "fillColor": "ff7f00"},
        {"pointSize": 3, "color": "000000", "fillColor": "33a6ff"}))))
    tile(styled.style(styleProperty="style"), {}, "training wells")

    # f per pixel against the field's f (coverage-weighted mean over 100 m pixels)
    geom = f0.geometry()
    mean_f = fimg.reduceRegion(ee.Reducer.mean(), geom, crs="EPSG:5070", crsTransform=proj["transform"])
    v = mean_f.getInfo().get("constant")
    check("f per 30 m pixel averaged over NV_16742 is close to its field f (new map)",
          v is not None and abs(v - p["F_NEW_TAN"]) < 0.02, f"{v:.4f} vs F_NEW_TAN {p['F_NEW_TAN']:.4f}")

    # A point inside NV_16742 (shapely representative_point; the centroid of this MultiPolygon falls in NV_16767).
    pt = ee.Geometry.Point([-115.01219460480792, 40.94969982205889])
    q = ee.Dictionary({"field": fields.filterBounds(pt).first().get("NWI_ID"),
                       "ha": has.filterBounds(pt).first().get("HYD_AREA")}).getInfo()
    check("click query finds the field and its HA", q == {"field": "NV_16742", "ha": "177"}, str(q))
    top = has.filter(ee.Filter.gt("N_IRR", 0)).sort("D_" + m, True).limit(3) \
             .reduceColumns(ee.Reducer.toList(), ["HYD_AREA"]).get("list").getInfo()
    local = pd.read_parquet(out_dir(args.out) / "app" / "ha_gw_subsidy.parquet", columns=["HYD_AREA", "N_IRR", "D_" + m])
    want = local[local["N_IRR"] > 0].sort_values("D_" + m)["HYD_AREA"].head(3).tolist()
    check("ranking: largest decreases match the app table", top == want, f"{top} vs {want}")
    url = shown.select(["NWI_ID", "D_" + m]).getDownloadURL("CSV", ["NWI_ID", "D_" + m], "check")
    check("CSV download URL", url.startswith("https://"))

    print("All checks passed." if not failures else f"FAILED: {failures}")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
