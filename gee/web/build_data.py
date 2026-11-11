"""Data for the web explorer (nwi_gw_subsidy_explorer.html), from the GEE app tables (gee/prep step 6).

    python build_data.py <data dir> [--app-dir outputs/gee_app/app] [--wells outputs/gee_app/wells_train.parquet]

Writes compact JSON (one array per column) into <data dir>. Run build_outlines.py next: it adds the field
and HA outlines, removes the HA placeholder written here, and records the outlines' provenance in meta.json.

NEW = the new DTW map (handily 30 m, 2026-10-21); MA = Ma et al. on the same grid (wtd_nevada.tif in Nevada,
the other state files elsewhere; mn = 1 where wtd_nevada.tif covers the field, the report's comparison set);
RUN = the run's own Tan values (its 2026-10-08 map; the report); R08 = Tan recomputed with the run's map.
"""
import argparse
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("out", type=Path, help="folder for the JSON files (the page reads data/ next to it)")
ap.add_argument("--app-dir", type=Path, default=REPO / "outputs" / "gee_app" / "app")
ap.add_argument("--wells", type=Path, default=REPO / "outputs" / "gee_app" / "wells_train.parquet")
args = ap.parse_args()
SRC, WELLS, OUT = args.app_dir, args.wells, args.out
OUT.mkdir(parents=True, exist_ok=True)
MODELS = ['TAN', 'BANTA', 'AVER', 'SHAH']


def r(v, d):
    """Round a float series; NaN -> None."""
    a = np.round(np.asarray(v, float), d)
    return [None if not np.isfinite(x) else (int(x) if d == 0 else float(x)) for x in a]


def cat(s):
    u = sorted({x for x in s if isinstance(x, str) and x})
    return u, [u.index(x) if isinstance(x, str) and x else -1 for x in s]


f = gpd.read_parquet(SRC / 'fields_gw_subsidy.parquet').to_crs(5070)
ha_codes = sorted(f.HYD_AREA.unique())
crop_u, crop_i = cat(f.CROP_LBL.tolist())
tex_u, tex_i = cat(f.TEXTURE.tolist())
texm_i = [tex_u.index(x) if isinstance(x, str) and x in tex_u else -1 for x in f.TEXTURE_MA]
st_u, st_i = cat(f.STATE.tolist())
cols = {
    'id': f.NWI_ID.tolist(), 'st': st_i, 'ha': [ha_codes.index(h) for h in f.HYD_AREA],
    'src': [int(x) if pd.notna(x) else 0 for x in f.IRR_SRC], 'as': f.SRC_ASSUMED.astype(int).tolist(),
    'crop': crop_i, 'ac': r(f.ACRES, 1), 'ni': f.N_IRR_YRS.astype(int).tolist(), 'tx': tex_i, 'txm': texm_i,
    'rc': f.ROOT_CM.fillna(100).astype(int).tolist(), 'e0': r(f.ET0_MMD, 2),
    'dd': r(f.DDEC_M, 3), 'de': r(f.DEXT_M, 3), 'p': r(f.P, 2), 'ddm': r(f.DDEC_MA_M, 3), 'dem': r(f.DEXT_MA_M, 3), 'pm': r(f.P_MA, 2),
    'dn': r(f.DTW_NEW_M, 2), 'dnm': r(f.DTW_NEW_MIN_M, 2), 'sg': r(f.DTW_SIG_M, 1), 'dm': r(f.DTW_MA_M, 2), 'dmm': r(f.DTW_MA_MIN_M, 2),
    'dr': r(f.DTW_RUN_M, 2), 'drm': r(f.DTW_RUN_MIN_M, 2),
    'mc': f.MA_COVER.astype(int).tolist(), 'mn': f.MA_NV.astype(int).tolist(), 'eta': r(f.ETA_AF, 2),
    'wt': r(f.WT_DIST_KM, 2), 'wn1': f.WT_N1KM.astype(int).tolist(), 'wn5': f.WT_N5KM.astype(int).tolist(),
    'wsrc': [1 if s == 'nv_ndwr' else 2 for s in f.WT_NEAR_SRC], 'wdtw': r(f.WT_NEAR_DTW_M, 1),
    'wa': r(f.W_DIST_KM, 2), 'wts': r(f.W_TS_DIST_KM, 2),
    'fr': r(f.F_RUN_TAN, 5), 'sr': r(f.S_RUN_TAN, 4), 'f08': r(f.F_R08_TAN, 5), 's08': r(f.S_R08_TAN, 4),
}
for m in MODELS:
    cols['fn_' + m] = r(f['F_NEW_' + m], 5); cols['sn_' + m] = r(f['S_NEW_' + m], 4)
    cols['fm_' + m] = r(f['F_MA_' + m], 5); cols['sm_' + m] = r(f['S_MA_' + m], 4)
# rounding must not move the statewide totals
for key, col in [('sn_' + m, 'S_NEW_' + m) for m in MODELS] + [('sm_' + m, 'S_MA_' + m) for m in MODELS] + \
        [('sr', 'S_RUN_TAN'), ('s08', 'S_R08_TAN')]:
    exact, rounded = f[col].sum(), sum(cols[key])
    assert abs(exact - rounded) < 0.5, (key, exact, rounded)
assert round(f.S_RUN_TAN.sum()) == 34733

h = gpd.read_parquet(SRC / 'ha_gw_subsidy.parquet').to_crs(5070)
h = h.set_index('HYD_AREA').reindex(ha_codes).reset_index()
ha = {'code': ha_codes, 'name': h.HA_NAME.fillna('').tolist(),
      'nf': h.N_FIELDS.astype(int).tolist(), 'ni': h.N_IRR.astype(int).tolist(), 'eta': r(h.ETA_AF, 1),
      'sm_rpt': r(h.S_MA_TAN_RPT, 2), 'nsn': h.N_SUB_NEW.astype(int).tolist(), 'nsm': h.N_SUB_MA.astype(int).tolist(),
      'nsr': h.N_SUB_RUN.astype(int).tolist(),
      'ncov': h.N_COVER.fillna(0).astype(int).tolist(), 'dn': r(h.DTW_NEW_M, 2), 'dm': r(h.DTW_MA_M, 2),
      'medwt': r(h.MED_WT_KM, 2), 'nfar5': h.N_FAR5.astype(int).tolist(), 'sfar5n': r(h.S_NEW_TAN_FAR5, 2),
      'sfar5m': r(h.S_MA_TAN_FAR5, 2), 'sfar5r': r(h.S_RUN_TAN_FAR5, 2),
      'sr': r(h.S_RUN_TAN, 2), 's08': r(h.S_R08_TAN, 2)}
for m in MODELS:
    ha['sn_' + m] = r(h['S_NEW_' + m], 2); ha['sm_' + m] = r(h['S_MA_' + m], 2)

hy = pd.read_parquet(SRC / 'ha_year_gw_subsidy.parquet')
hys = {}
for code, g in hy.groupby('HYD_AREA'):
    g = g.sort_values('YEAR')
    hys[code] = {k: r(g[k], 2) for k in ['ETA_AF'] + [c for c in g.columns if c.startswith('S_')]}

fy = pd.read_parquet(SRC / 'field_year_gw_subsidy.parquet', columns=['NWI_ID', 'YEAR', 'IRR', 'S_NEW_TAN', 'S_MA_TAN', 'S_RUN_TAN'])
keep = set(f.loc[(f.N_IRR_YRS > 0) & ((f.S_NEW_TAN > 0.5) | (f.S_MA_TAN > 0.5) | (f.S_RUN_TAN > 0.5)), 'NWI_ID'])
fy = fy[fy.NWI_ID.isin(keep)].sort_values(['NWI_ID', 'YEAR'])
fys = {}
for fid, g in fy.groupby('NWI_ID'):
    irr = g.IRR.to_numpy(bool)
    fys[fid] = {k: [round(v, 3) if i else None for v, i in zip(g[c], irr)]
                for k, c in (('n', 'S_NEW_TAN'), ('m', 'S_MA_TAN'), ('r', 'S_RUN_TAN'))}

w = gpd.read_parquet(WELLS).to_crs(5070)
wells = {'id': w.canonical_id.tolist(), 'mon': [1 if x == 'monitoring' else 0 for x in w.role], 'src': w.source.tolist(),
         'dtw': r(w.dtw_m, 2), 'dn': r(w.dtw_new_m, 2), 'dr': r(w.dtw_run_m, 2), 'dm': r(w.dtw_ma_m, 2), 'sg': r(w.dtw_sigma_m, 1),
         'x': r(w.geometry.x / 1000, 3), 'y': r(w.geometry.y / 1000, 3)}
summary = json.loads((SRC / 'app_summary.json').read_text())
meta = {'crops': crop_u, 'textures': tex_u, 'states': st_u, 'n_fields': len(f), 'models': MODELS,
        'bounds_km': [round(v / 1000, 1) for v in f.total_bounds],
        'summary': {k: summary[k] for k in ('dtw_new', 'dtw_run', 'dtw_ma', 'totals', 'covered_totals', 'vs_report',
                                            'n_fields', 'n_fields_irrigated', 'n_covered', 'n_report_set',
                                            'report_set_totals')}}
# HA outlines are written by build_outlines.py (has_geom.json); keep an empty placeholder until then
ha['rings'] = []
for name, obj in (('fields', cols), ('has', ha), ('ha_years', hys), ('field_years', fys), ('wells', wells), ('meta', meta)):
    p = OUT / f'{name}.json'
    p.write_text(json.dumps(obj, separators=(',', ':'), default=float))
    print(f'{name}.json {p.stat().st_size / 1e6:.2f} MB')
print('fields with annual series:', len(fys))
