// =============================================================================
// NWI Groundwater-ET Subsidy Explorer: assessment of the NWI T1 run
// Single-file Earth Engine app. Paste into https://code.earthengine.google.com and Run.
// For internal checking of the field-level groundwater-ET (GW-ET) subsidy in the NWI T1 run
// (nwi_t1_final_20261018) against the Ma et al. (2026) depth-to-groundwater (DTW) map, with
// attention to fields far from the wells the new DTW map was trained on and fields whose subsidy
// changed most.
//
// Subsidy, per field and month: min(f x ETa, max(ETa - effective precipitation, 0)), where f is the
// fraction of ETa groundwater can supply (Tan et al. 2025 in the run). Values are 1985-2025 annual
// means over irrigated field-years (IrrMapper > 50% of the field), acre-ft/yr, as in the report.
//
//   * New DTW: the handily NV parity map at 30 m (gs://ucrc-wtd/handily/nv/handily_dtw_nv_parity_30m.tif,
//     2026-10-21). f and subsidy for every model with it, and with Ma et al. on the same 30 m grid, are
//     computed with the run's code (scripts/gw_subsidy) on the same 2026-08-20 fields.
//   * The run's own Tan values (F_RUN_TAN, S_RUN_TAN) were made with its 2026-10-08 100 m map. They
//     reproduce README.md and docs/tables/ha_gw_subsidy_new_vs_ma.csv exactly and are shown for reference.
//
// Tables and rasters are built by gee/prep/ (01-07) and checked by gee/prep/08_check_assets.py.
// This file was first copied unchanged from
// DRI-NWI/nwi-gw-subsidy (gee/nwi_gw_et_explorer_app.js, commit 7262f69) and then rewritten.
// =============================================================================

// === CONFIG ===================================================================

var ROOT = 'projects/nwi-usgs/assets/nwi_gw_subsidy/';
var RUN_ID = 'nwi_t1_final_20261018';
var ASSET = {
  fields:     ROOT + 'fields_gw_subsidy_20261018',      // 29,338 fields of the 2026-08-20 layer
  fieldYears: ROOT + 'field_year_gw_subsidy_20261018',  // one row per field-year
  has:        ROOT + 'ha_gw_subsidy_20261018',          // hydrographic areas with totals
  haYears:    ROOT + 'ha_year_gw_subsidy_20261018',     // one row per HA-year
  wellsTrain: ROOT + 'wells_handily_train_nv',          // wells the new DTW map was trained on
  wellsAll:   ROOT + 'wells_gwx_nv',                    // all gwx wells with a depth to water
  dtw:        ROOT + 'dtw_epsg5070'                     // dtw_new_m, dtw_ma_m (30 m); dtw_sigma_m, dtw_run_m (100 m)
};
var YEAR_MIN = 1985;
var YEAR_MAX = 2025;

// Report values the checks panel compares against (README.md, docs/GW_SUBSIDY.md, Fig. 11). The report
// used the run's 2026-10-08 map, so only the run's own values are expected to match exactly.
var REPORT = {
  subsidyAll: 34733, etaAll: 1423458, nCovered: 16225, coveredNew: 24184, coveredMa: 18563,
  medianDtwNew: 7.2, medianDtwMa: 7.4
};

// f(d) models. Tan is the run's model and the default.
var MODELS = [
  {key: 'TAN',   label: 'Tan et al. (2025): the run\'s model'},
  {key: 'BANTA', label: 'Banta (2000) linear'},
  {key: 'AVER',  label: 'Averianov (1956), p = 3'},
  {key: 'SHAH',  label: 'Shah et al. (2007)'}
];
var MODEL_LABEL = {};
var MODEL_KEY = {};
MODELS.forEach(function (m) { MODEL_LABEL[m.key] = m.label; MODEL_KEY[m.label] = m.key; });
var AVERIANOV_P = 3.0;  // scripts/gw_subsidy/config.py
var SHAH_EPS = 0.02;

// --- Class breaks and palettes ------------------------------------------------
var PAL_SEQ  = ['f0f0f0', 'edf8b1', 'c7e9b4', '7fcdbb', '41b6c4', '1d91c0', '225ea8', '0c2c84'];
var PAL_DIV  = ['7f3b08', 'b35806', 'e08214', 'fdb863', 'fee0b6', 'fff3e0', 'f0f0f0',
                'efedf5', 'd8daeb', 'b2abd2', '8073ac', '542788', '2d004b'];   // orange: new < Ma
var PAL_DTW  = ['08306b', '08519c', '2171b5', '4292c6', '6baed6', '9ecae1', 'c6dbef', 'f7fbff'];
var PAL_DDTW = ['053061', '2166ac', '4393c3', '92c5de', 'd1e5f0', 'f7f7f7', 'fddbc7', 'f4a582',
                'd6604d', 'b2182b', '67001f'];                                    // red: new deeper
var PAL_SIG  = ['fcfdbf', 'fec287', 'fc8961', 'e75263', 'b5367a', '812581', '4a1079'];
var PAL_DIST = ['1a9850', '91cf60', 'd9ef8b', 'fee08b', 'fc8d59', 'd73027'];
var PAL_F    = ['f0f0f0', 'ffffcc', 'c7e9b4', '7fcdbb', '41b6c4', '1d91c0', '225ea8', '0c2c84'];

var BRK_SUB  = [0.5, 1, 3, 10, 30, 100, 300];
var BRK_CHG  = [-300, -100, -30, -10, -3, -0.5, 0.5, 3, 10, 30, 100, 300];
var BRK_PCT  = [0.5, 1, 2, 5, 10, 20, 40];
var BRK_PCTD = [-20, -10, -5, -2, -1, -0.25, 0.25, 1, 2, 5, 10, 20];
var BRK_F    = [0.001, 0.01, 0.05, 0.1, 0.2, 0.4, 0.7];
var BRK_DTW  = [1, 2, 3, 5, 10, 20, 50];
var BRK_DDTW = [-15, -5, -2, -1, -0.25, 0.25, 1, 2, 5, 15];
var BRK_SIG  = [1, 2, 5, 10, 20, 50];
var BRK_DIST = [1, 2, 5, 10, 20];
var BRK_HA   = [-3000, -1000, -300, -100, -30, -10, 10, 30, 100, 300, 1000, 3000];

// What the fields can be coloured by. `prop(m)` names a stored property; `calc(ft, m)` computes one.
var FIELD_VARS = [
  {key: 'chg',    label: 'Subsidy change, new - Ma (acre-ft/yr)', brk: BRK_CHG, pal: PAL_DIV,
   prop: function (m) { return 'D_' + m; }},
  {key: 'sNew',   label: 'Subsidy, new DTW (acre-ft/yr)', brk: BRK_SUB, pal: PAL_SEQ,
   prop: function (m) { return 'S_NEW_' + m; }},
  {key: 'sMa',    label: 'Subsidy, Ma et al. (acre-ft/yr)', brk: BRK_SUB, pal: PAL_SEQ,
   prop: function (m) { return 'S_MA_' + m; }},
  {key: 'pNew',   label: 'Subsidy, new DTW (% of ETa)', brk: BRK_PCT, pal: PAL_SEQ,
   calc: function (ft, m) { return pct(ft, 'S_NEW_' + m); }},
  {key: 'pMa',    label: 'Subsidy, Ma et al. (% of ETa)', brk: BRK_PCT, pal: PAL_SEQ,
   calc: function (ft, m) { return pct(ft, 'S_MA_' + m); }},
  {key: 'pChg',   label: 'Subsidy change, new - Ma (points of % of ETa)', brk: BRK_PCTD, pal: PAL_DIV,
   calc: function (ft, m) { return pct(ft, 'D_' + m); }},
  {key: 'fNew',   label: 'f, new DTW', brk: BRK_F, pal: PAL_F,
   prop: function (m) { return 'F_NEW_' + m; }},
  {key: 'fMa',    label: 'f, Ma et al.', brk: BRK_F, pal: PAL_F,
   prop: function (m) { return 'F_MA_' + m; }},
  {key: 'dNew',   label: 'Field mean DTW, new (m)', brk: BRK_DTW, pal: PAL_DTW,
   prop: function () { return 'DTW_NEW_M'; }},
  {key: 'dMa',    label: 'Field mean DTW, Ma et al. (m)', brk: BRK_DTW, pal: PAL_DTW,
   prop: function () { return 'DTW_MA_M'; }},
  {key: 'dChg',   label: 'Field mean DTW change, new - Ma (m)', brk: BRK_DDTW, pal: PAL_DDTW,
   prop: function () { return 'DTW_D_M'; }},
  {key: 'dRun',   label: 'Field mean DTW, run\'s 2026-10-08 map (m)', brk: BRK_DTW, pal: PAL_DTW,
   prop: function () { return 'DTW_RUN_M'; }},
  {key: 'dRunChg', label: 'Field mean DTW change, new - run\'s map (m)', brk: BRK_DDTW, pal: PAL_DDTW,
   calc: function (ft) { return ee.Number(ft.get('DTW_NEW_M')).subtract(ee.Number(ft.get('DTW_RUN_M'))); }},
  {key: 'sig',    label: 'DTW uncertainty, field mean sigma of the run\'s map (m)', brk: BRK_SIG, pal: PAL_SIG,
   prop: function () { return 'DTW_SIG_M'; }},
  {key: 'well',   label: 'Distance to nearest training well (km)', brk: BRK_DIST, pal: PAL_DIST,
   prop: function () { return 'WT_DIST_KM'; }},
  {key: 'vsRun',  label: 'Tan subsidy, new DTW - the run (report) (acre-ft/yr)', brk: BRK_CHG, pal: PAL_DIV,
   calc: function (ft) { return ee.Number(ft.get('S_NEW_TAN')).subtract(ee.Number(ft.get('S_RUN_TAN'))); }},
  {key: 'vsTan',  label: 'Model - Tan, new DTW (acre-ft/yr)', brk: BRK_CHG, pal: PAL_DIV,
   calc: function (ft, m) {
     return ee.Number(ft.get('S_NEW_' + m)).subtract(ee.Number(ft.get('S_NEW_TAN')));
   }}
];
var FIELD_VAR = {};
FIELD_VARS.forEach(function (v) { FIELD_VAR[v.label] = v; });

function pct(ft, prop) {
  var eta = ee.Number(ft.get('ETA_AF'));
  return ee.Number(ft.get(prop)).multiply(100).divide(eta.max(1e-9));
}

var M2_PER_ACRE = 4046.86;
var NV_BLUE = '#1f4e79';
var HIGHLIGHT = 'ff00ff';
var OUTLINE_MIN_ZOOM = 11;   // field outlines
var ALL_WELLS_MIN_ZOOM = 9;  // 137k gwx wells

// === DATA =====================================================================

var fieldsAll  = ee.FeatureCollection(ASSET.fields);
var fieldYears = ee.FeatureCollection(ASSET.fieldYears);
var has        = ee.FeatureCollection(ASSET.has);
var haYears    = ee.FeatureCollection(ASSET.haYears);
var wellsTrain = ee.FeatureCollection(ASSET.wellsTrain);
var wellsAll   = ee.FeatureCollection(ASSET.wellsAll);
var dtw        = ee.Image(ASSET.dtw);

// === GW-ET FRACTION f(d), per pixel (display layer) =============================
// Same formulas as scripts/gw_subsidy/models.py. Field values are not computed here: they are
// coverage-weighted means of f over each field's 30 m pixels, computed offline.
function fImage(model, d, dDec, dExt, p) {
  if (model === 'BANTA') return ee.Image(1).subtract(d.divide(dExt)).clamp(0, 1);
  if (model === 'AVER') return ee.Image(1).subtract(d.divide(dExt)).clamp(0, 1).pow(AVERIANOV_P);
  var span = dExt.subtract(dDec);
  if (model === 'SHAH') {
    var k = ee.Image(Math.log(1 / SHAH_EPS)).divide(span);
    var fs = d.subtract(dDec).max(0).multiply(k).multiply(-1).exp();
    fs = fs.where(d.lte(dDec), 1);
    return fs.where(span.lte(0), d.lte(dDec)).clamp(0, 1);
  }
  var ft = dExt.subtract(d).divide(span).clamp(0, 1).pow(p);
  ft = ft.where(d.lte(dDec), 1).where(d.gte(dExt), 0);
  return ft.where(span.lte(0), d.lte(dDec)).clamp(0, 1);
}

function fPixelLayer(model, which) {
  var sfx = which === 'ma' ? '_MA' : '';
  var withParams = fieldsAll.filter(ee.Filter.notNull(['DDEC' + sfx + '_M', 'DEXT' + sfx + '_M', 'P' + sfx]));
  var dDec = ee.Image().float().paint(withParams, 'DDEC' + sfx + '_M');
  var dExt = ee.Image().float().paint(withParams, 'DEXT' + sfx + '_M');
  var p    = ee.Image().float().paint(withParams, 'P' + sfx);
  var d    = dtw.select(which === 'ma' ? 'dtw_ma_m' : 'dtw_new_m');
  return fImage(model, d, dDec, dExt, p).updateMask(dDec.mask());
}

// === HELPERS ====================================================================

function classify(img, brk) {
  var c = ee.Image(0);
  for (var i = 0; i < brk.length; i++) c = c.where(img.gte(brk[i]), i + 1);
  return c.updateMask(img.mask()).toByte();
}

function classLabels(brk) {
  var out = ['< ' + brk[0]];
  for (var i = 1; i < brk.length; i++) out.push(brk[i - 1] + ' to ' + brk[i]);
  out.push('>= ' + brk[brk.length - 1]);
  return out;
}

function fmt(x, dec) {
  if (x === null || x === undefined || isNaN(x)) return '-';
  var s = Number(x).toFixed(dec === undefined ? 0 : dec);
  var parts = s.split('.');
  parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return parts.join('.');
}

function haTitle(name, code) {
  var n = name ? String(name).replace(/([a-z])([A-Z])/g, '$1 $2') : '?';
  return n + ' (' + code + ')';
}

var SRC_TEXT = {1: 'Groundwater', 2: 'Surface water', 3: 'Mixed (GW + SW)'};

// === STATE ======================================================================

var state = {
  model: 'TAN',
  fieldVar: FIELD_VARS[0].label,
  population: 'irrigated',   // 'irrigated' (report population) or 'all'
  nvOnly: false,
  maCovered: false,
  withSubsidy: false,
  minChange: 0,              // acre-ft/yr, |new - Ma| of the selected model
  minWellKm: 0,              // km to the nearest training well
  opacity: 0.75,
  zoom: 6,
  selected: null,
  layers: {
    'Fields (coloured)': true,
    'DTW, new (handily 30 m)': false,
    'DTW, Ma et al. (30 m grid)': false,
    'DTW change, new - Ma': false,
    'DTW change, new - run\'s map': false,
    'DTW uncertainty (sigma of the run\'s map)': false,
    'f per 30 m pixel, new DTW': false,
    'f per 30 m pixel, Ma et al.': false,
    'Distance to nearest training well': false,
    'Training wells (new DTW)': true,
    'All gwx wells with a depth to water': false,
    'HA change, new - Ma': false,
    'Hydrographic areas': true
  }
};

function filteredFields() {
  var fc = fieldsAll;
  var m = state.model;
  if (state.population === 'irrigated') fc = fc.filter(ee.Filter.gt('N_IRR_YRS', 0));
  if (state.nvOnly) fc = fc.filter(ee.Filter.eq('STATE', 'NV'));
  if (state.maCovered) fc = fc.filter(ee.Filter.eq('MA_COVER', 1));
  if (state.withSubsidy) {
    fc = fc.filter(ee.Filter.or(ee.Filter.gt('S_NEW_' + m, 0.5), ee.Filter.gt('S_MA_' + m, 0.5)));
  }
  if (state.minChange > 0) {
    fc = fc.filter(ee.Filter.or(ee.Filter.gte('D_' + m, state.minChange),
                                ee.Filter.lte('D_' + m, -state.minChange)));
  }
  if (state.minWellKm > 0) fc = fc.filter(ee.Filter.gte('WT_DIST_KM', state.minWellKm));
  return fc;
}

// The coloured property of each field: a stored property, or one computed into 'v'.
function fieldValues(fc) {
  var v = FIELD_VAR[state.fieldVar];
  var m = state.model;
  if (v.prop) {
    var prop = v.prop(m);
    return {fc: fc.filter(ee.Filter.notNull([prop])), prop: prop};
  }
  return {fc: fc.map(function (ft) { return ft.set('v', v.calc(ft, m)); }), prop: 'v'};
}

// === UI: MAP ====================================================================

var map = ui.Map();
map.setOptions('HYBRID');
map.setCenter(-117, 39.3, 6);
map.setControlVisibility({layerList: false, mapTypeControl: true, fullscreenControl: false});
map.style().set('cursor', 'crosshair');

// === UI: LEFT PANEL =============================================================

var leftPanel = ui.Panel({style: {width: '470px', padding: '8px'}});
function header(text) {
  return ui.Label(text, {fontWeight: 'bold', fontSize: '13px', margin: '10px 0 2px 0', color: NV_BLUE});
}
function small(text, color) {
  return ui.Label(text, {fontSize: '11px', color: color || '#555', margin: '0 0 2px 0'});
}

leftPanel.add(ui.Label('NWI GW-ET Subsidy Explorer', {
  fontSize: '20px', fontWeight: 'bold', color: 'white', backgroundColor: NV_BLUE,
  padding: '10px', margin: '0 0 4px 0', textAlign: 'center', stretch: 'horizontal'
}));
leftPanel.add(small(
  'Internal check of the field groundwater-ET subsidy in run ' + RUN_ID + ' (2026-08-20 fields). New DTW: ' +
  'handily parity, 30 m (2026-10-21), against Ma et al. (2026) on the same grid, with the run\'s own values ' +
  '(its 2026-10-08 map) for reference. Values are 1985-2025 means over irrigated field-years, acre-ft/yr. ' +
  'Click a field, a well or a hydrographic area (HA).', '#333'));

// --- model and colouring
leftPanel.add(header('Model and map'));
var modelSelect = ui.Select({
  items: MODELS.map(function (m) { return m.label; }), value: MODEL_LABEL.TAN,
  onChange: function (v) { state.model = MODEL_KEY[v]; refresh(); }, style: {stretch: 'horizontal'}
});
leftPanel.add(ui.Panel([ui.Label('f(d) model', {margin: '6px 4px 0 0', fontSize: '12px'}), modelSelect],
  ui.Panel.Layout.flow('horizontal'), {stretch: 'horizontal'}));
var varSelect = ui.Select({
  items: FIELD_VARS.map(function (v) { return v.label; }), value: state.fieldVar,
  onChange: function (v) { state.fieldVar = v; refresh(); }, style: {stretch: 'horizontal'}
});
leftPanel.add(ui.Panel([ui.Label('Colour fields by', {margin: '6px 4px 0 0', fontSize: '12px'}), varSelect],
  ui.Panel.Layout.flow('horizontal'), {stretch: 'horizontal'}));
leftPanel.add(small('Every value with the new DTW and with Ma et al. is computed with the run\'s code; the run\'s ' +
  'own Tan values (its 2026-10-08 map) are shown for reference (see Notes).'));

// --- filters
leftPanel.add(header('Which fields'));
var popSelect = ui.Select({
  items: ['Irrigated in at least one year (report)', 'All fields'],
  value: 'Irrigated in at least one year (report)',
  onChange: function (v) { state.population = v.indexOf('All') === 0 ? 'all' : 'irrigated'; refresh(); },
  style: {stretch: 'horizontal'}
});
leftPanel.add(popSelect);
function filterBox(label, key) {
  return ui.Checkbox({label: label, value: state[key], style: {fontSize: '12px', margin: '1px 0'},
    onChange: function (v) { state[key] = v; refresh(); }});
}
leftPanel.add(filterBox('Nevada fields only', 'nvOnly'));
leftPanel.add(filterBox('Only fields covered by the Ma et al. map (like-for-like)', 'maCovered'));
leftPanel.add(filterBox('Only fields with a subsidy > 0.5 acre-ft/yr under either map', 'withSubsidy'));

var chgLabel = ui.Label('', {fontSize: '12px', margin: '6px 0 0 0'});
var chgSlider = ui.Slider({min: 0, max: 200, value: 0, step: 1, style: {stretch: 'horizontal'},
  onChange: ui.util.debounce(function (v) { state.minChange = v; setChgLabel(); refresh(); }, 500)});
function setChgLabel() {
  chgLabel.setValue('Large changes only: |new - Ma| >= ' + state.minChange + ' acre-ft/yr' +
    (state.minChange === 0 ? ' (off)' : ''));
}
setChgLabel();
leftPanel.add(chgLabel);
leftPanel.add(chgSlider);

var wellLabel = ui.Label('', {fontSize: '12px', margin: '6px 0 0 0'});
var wellSlider = ui.Slider({min: 0, max: 30, value: 0, step: 0.5, style: {stretch: 'horizontal'},
  onChange: ui.util.debounce(function (v) { state.minWellKm = v; setWellLabel(); refresh(); }, 500)});
function setWellLabel() {
  wellLabel.setValue('No well data: nearest training well >= ' + state.minWellKm + ' km' +
    (state.minWellKm === 0 ? ' (off)' : ''));
}
setWellLabel();
leftPanel.add(wellLabel);
leftPanel.add(wellSlider);

var summaryPanel = ui.Panel({style: {margin: '4px 0 0 0'}});
leftPanel.add(summaryPanel);

// --- layers
leftPanel.add(header('Layers'));
var layerBoxes = {};
Object.keys(state.layers).forEach(function (name) {
  var cb = ui.Checkbox({label: name, value: state.layers[name], style: {fontSize: '12px', margin: '1px 0'},
    onChange: function (v) { state.layers[name] = v; paintLayers(); updateLegend(); }});
  layerBoxes[name] = cb;
  leftPanel.add(cb);
});
var opacityLabel = ui.Label('Raster opacity: ' + state.opacity, {fontSize: '12px', margin: '6px 0 0 0'});
leftPanel.add(opacityLabel);
leftPanel.add(ui.Slider({min: 0.1, max: 1, value: state.opacity, step: 0.05, style: {stretch: 'horizontal'},
  onChange: function (v) {
    state.opacity = v;
    opacityLabel.setValue('Raster opacity: ' + v.toFixed(2));
    map.layers().forEach(function (l) { if (RASTER_LAYERS.indexOf(l.getName()) >= 0) l.setOpacity(v); });
  }}));

var legendPanel = ui.Panel({style: {margin: '6px 0 0 0'}});
leftPanel.add(legendPanel);

// --- click results
leftPanel.add(header('Selection'));
var statusLabel = small('Click the map.');
leftPanel.add(statusLabel);
var infoPanel = ui.Panel({style: {margin: '0'}});
leftPanel.add(infoPanel);

// --- rankings, checks, notes, references (collapsible)
function collapsible(title, build) {
  var body = ui.Panel({style: {shown: false, margin: '0 0 0 4px'}});
  var built = false;
  var btn = ui.Button({label: '+ ' + title, style: {margin: '6px 0 0 0', stretch: 'horizontal'},
    onClick: function () {
      var show = !body.style().get('shown');
      if (show && !built) { build(body); built = true; }
      body.style().set('shown', show);
      btn.setLabel((show ? '- ' : '+ ') + title);
    }});
  leftPanel.add(btn);
  leftPanel.add(body);
  return {body: body, rebuild: function () { if (built) { body.clear(); build(body); } }};
}
var rankSection = collapsible('HAs with the largest change (selected model)', buildRanking);
var checkSection = collapsible('Checks against the report', buildChecks);
collapsible('Notes: what the numbers are', buildNotes);
collapsible('References', buildReferences);

// === LAYOUT =====================================================================

ui.root.clear();
ui.root.add(ui.SplitPanel({firstPanel: leftPanel, secondPanel: map, orientation: 'horizontal',
  style: {stretch: 'both'}}));

// === MAP LAYERS =================================================================

var RASTER_LAYERS = ['DTW, new (handily 30 m)', 'DTW, Ma et al. (30 m grid)', 'DTW change, new - Ma', 'DTW change, new - run\'s map',
  'DTW uncertainty (sigma of the run\'s map)', 'f per 30 m pixel, new DTW', 'f per 30 m pixel, Ma et al.',
  'Distance to nearest training well'];

function addClassLayer(img, brk, pal, name, opacity) {
  map.addLayer(classify(img, brk), {min: 0, max: brk.length, palette: pal, opacity: opacity}, name);
}

function paintLayers() {
  map.layers().reset();
  var L = state.layers;
  var m = state.model;
  var o = state.opacity;
  if (L['DTW, new (handily 30 m)']) addClassLayer(dtw.select('dtw_new_m'), BRK_DTW, PAL_DTW, 'DTW, new (handily 30 m)', o);
  if (L['DTW, Ma et al. (30 m grid)']) addClassLayer(dtw.select('dtw_ma_m'), BRK_DTW, PAL_DTW, 'DTW, Ma et al. (30 m grid)', o);
  if (L['DTW change, new - Ma']) {
    addClassLayer(dtw.select('dtw_new_m').subtract(dtw.select('dtw_ma_m')), BRK_DDTW, PAL_DDTW, 'DTW change, new - Ma', o);
  }
  if (L['DTW change, new - run\'s map']) {
    addClassLayer(dtw.select('dtw_new_m').subtract(dtw.select('dtw_run_m')), BRK_DDTW, PAL_DDTW, 'DTW change, new - run\'s map', o);
  }
  if (L['DTW uncertainty (sigma of the run\'s map)']) addClassLayer(dtw.select('dtw_sigma_m'), BRK_SIG, PAL_SIG, 'DTW uncertainty (sigma of the run\'s map)', o);
  if (L['Distance to nearest training well']) {
    var dist = wellsTrain.distance({searchRadius: 40000, maxError: 50}).divide(1000).clipToCollection(has);
    addClassLayer(dist, BRK_DIST, PAL_DIST, 'Distance to nearest training well', o);
  }
  if (L['f per 30 m pixel, new DTW']) addClassLayer(fPixelLayer(m, 'new'), BRK_F, PAL_F, 'f per 30 m pixel, new DTW', o);
  if (L['f per 30 m pixel, Ma et al.']) addClassLayer(fPixelLayer(m, 'ma'), BRK_F, PAL_F, 'f per 30 m pixel, Ma et al.', o);
  if (L['HA change, new - Ma']) {
    var hv = has.filter(ee.Filter.gt('N_IRR', 0));
    addClassLayer(ee.Image().float().paint(hv, 'D_' + m), BRK_HA, PAL_DIV, 'HA change, new - Ma', 0.8);
  }
  if (L['Hydrographic areas']) {
    map.addLayer(has.style({color: 'ffdd00', fillColor: '00000000', width: 1.2}), {}, 'Hydrographic areas');
  }
  if (L['Fields (coloured)']) {
    var fv = fieldValues(filteredFields());
    var v = FIELD_VAR[state.fieldVar];
    addClassLayer(ee.Image().float().paint(fv.fc, fv.prop), v.brk, v.pal, 'Fields (coloured)', 0.9);
  }
  paintZoomLayers();
  if (L['Training wells (new DTW)']) {
    var styled = wellsTrain.map(function (w) {
      var mon = ee.String(w.get('role')).equals('monitoring');
      return w.set('style', ee.Dictionary(ee.Algorithms.If(mon,
        {pointSize: 4, color: '000000', fillColor: 'ff7f00', width: 0.6},
        {pointSize: 3, color: '000000', fillColor: '33a6ff', width: 0.4})));
    });
    map.addLayer(styled.style({styleProperty: 'style'}), {}, 'Training wells (new DTW)');
  }
  refreshHighlight();
}

// Field outlines and the 137k gwx wells are drawn only when zoomed in.
function paintZoomLayers() {
  var drop = [];
  map.layers().forEach(function (l) {
    if (l.getName() === 'Field outlines' || l.getName() === 'All gwx wells') drop.push(l);
  });
  drop.forEach(function (l) { map.remove(l); });
  if (state.layers['Fields (coloured)'] && state.zoom >= OUTLINE_MIN_ZOOM) {
    map.layers().insert(map.layers().length(), ui.Map.Layer(
      ee.Image().byte().paint(filteredFields(), 1, 1), {palette: ['00e5ff']}, 'Field outlines'));
  }
  if (state.layers['All gwx wells with a depth to water'] && state.zoom >= ALL_WELLS_MIN_ZOOM) {
    map.layers().insert(map.layers().length(), ui.Map.Layer(
      wellsAll.style({pointSize: 2, color: '555555', fillColor: 'dddddd', width: 0.3}), {}, 'All gwx wells'));
  }
}
map.onChangeZoom(function (z) { state.zoom = z; paintZoomLayers(); refreshHighlight(); });

function refreshHighlight() {
  var drop = [];
  map.layers().forEach(function (l) { if (l.getName() === 'Selected') drop.push(l); });
  drop.forEach(function (l) { map.remove(l); });
  if (!state.selected) return;
  var fc = ee.FeatureCollection([ee.Feature(state.selected)]);
  map.addLayer(fc.style({color: HIGHLIGHT, fillColor: '00000000', width: 3, pointSize: 7}), {}, 'Selected');
}

// === LEGEND =====================================================================

function swatch(color, text) {
  return ui.Panel([
    ui.Label('', {backgroundColor: '#' + color, padding: '6px 9px', margin: '1px 6px 1px 0',
                  border: '1px solid #999'}),
    ui.Label(text, {fontSize: '11px', margin: '1px 0'})
  ], ui.Panel.Layout.flow('horizontal'));
}
function classLegend(title, brk, pal) {
  var p = ui.Panel({style: {margin: '4px 0'}});
  p.add(ui.Label(title, {fontWeight: 'bold', fontSize: '11px', margin: '0 0 2px 0'}));
  classLabels(brk).forEach(function (t, i) { p.add(swatch(pal[i], t)); });
  return p;
}
function updateLegend() {
  legendPanel.clear();
  var L = state.layers;
  var v = FIELD_VAR[state.fieldVar];
  if (L['Fields (coloured)']) legendPanel.add(classLegend('Fields: ' + v.label + ', ' + state.model, v.brk, v.pal));
  if (L['DTW, new (handily 30 m)'] || L['DTW, Ma et al. (30 m grid)']) legendPanel.add(classLegend('DTW (m)', BRK_DTW, PAL_DTW));
  if (L['DTW change, new - Ma']) legendPanel.add(classLegend('DTW change, new - Ma (m; red = new deeper)', BRK_DDTW, PAL_DDTW));
  if (L['DTW change, new - run\'s map']) legendPanel.add(classLegend('DTW change, new - run\'s map (m; red = new deeper)', BRK_DDTW, PAL_DDTW));
  if (L['DTW uncertainty (sigma of the run\'s map)']) legendPanel.add(classLegend('DTW sigma (m)', BRK_SIG, PAL_SIG));
  if (L['Distance to nearest training well']) legendPanel.add(classLegend('Distance to nearest training well (km)', BRK_DIST, PAL_DIST));
  if (L['f per 30 m pixel, new DTW'] || L['f per 30 m pixel, Ma et al.']) legendPanel.add(classLegend('f per pixel', BRK_F, PAL_F));
  if (L['HA change, new - Ma']) legendPanel.add(classLegend('HA change, new - Ma (acre-ft/yr)', BRK_HA, PAL_DIV));
  if (L['Training wells (new DTW)']) {
    legendPanel.add(swatch('33a6ff', 'Training well: NDWR well log'));
    legendPanel.add(swatch('ff7f00', 'Training well: monitoring well'));
  }
}

// === SUMMARY OF THE FILTERED FIELDS ===============================================

function updateSummary() {
  summaryPanel.clear();
  var m = state.model;
  var label = small('Summing the fields shown...');
  summaryPanel.add(label);
  var fc = filteredFields();
  var props = ['S_NEW_' + m, 'S_MA_' + m, 'D_' + m, 'ETA_AF'];
  var sums = fc.reduceColumns(ee.Reducer.sum().repeat(4), props).get('sum');
  ee.Dictionary({n: fc.size(), s: sums}).evaluate(function (r, err) {
    if (err) { label.setValue('Summary failed: ' + err); return; }
    var s = r.s;
    label.setValue(fmt(r.n) + ' fields shown. ' + state.model + ': new ' + fmt(s[0]) + ', Ma ' + fmt(s[1]) +
      ', change ' + fmt(s[2]) + ' acre-ft/yr (' + fmt(100 * s[0] / Math.max(s[3], 1e-9), 2) + '% vs ' +
      fmt(100 * s[1] / Math.max(s[3], 1e-9), 2) + '% of ETa ' + fmt(s[3]) + ').');
  });
  var cols = ['NWI_ID', 'STATE', 'HYD_AREA', 'HA_NAME', 'SRC', 'CROP_LBL', 'ACRES', 'N_IRR_YRS', 'ETA_AF',
    'DTW_NEW_M', 'DTW_MA_M', 'DTW_RUN_M', 'DTW_SIG_M', 'WT_DIST_KM', 'WT_N5KM', 'F_NEW_' + m, 'F_MA_' + m,
    'S_NEW_' + m, 'S_MA_' + m, 'D_' + m, 'S_RUN_TAN'];
  var link = ui.Label('Preparing CSV of these fields...', {fontSize: '11px', margin: '0 0 2px 0'});
  summaryPanel.add(link);
  fc.select(cols).getDownloadURL('CSV', cols, 'nwi_gw_subsidy_fields_' + m, function (url, err) {
    if (err) { link.setValue('CSV link failed: ' + err); return; }
    link.setValue('Download these fields (CSV)');
    link.setUrl(url);
  });
}

// === CLICK: FIELD ===================================================================

function row(cells, bold) {
  return ui.Panel(cells.map(function (c, i) {
    return ui.Label(c, {fontSize: '11px', margin: '0', width: i === 0 ? '130px' : '62px',
      textAlign: i === 0 ? 'left' : 'right', fontWeight: bold ? 'bold' : 'normal'});
  }), ui.Panel.Layout.flow('horizontal'), {margin: '0'});
}

function showField(feature) {
  infoPanel.clear();
  state.selected = feature;
  refreshHighlight();
  statusLabel.setValue('Field selected.');
  var loading = small('Loading field...');
  infoPanel.add(loading);
  ee.Feature(feature).toDictionary().evaluate(function (p, err) {
    infoPanel.remove(loading);
    if (err || !p) { infoPanel.add(small('Query failed: ' + err)); return; }
    var m = state.model;
    infoPanel.add(ui.Label(p.NWI_ID + ': ' + haTitle(p.HA_NAME, p.HYD_AREA) + ', ' + p.STATE,
      {fontWeight: 'bold', fontSize: '13px', margin: '2px 0'}));
    infoPanel.add(small('Water right: ' + (SRC_TEXT[p.IRR_SRC] || '-') + (p.SRC_ASSUMED ? ' (assumed; no NV record)' : '') +
      '.  Crop: ' + p.CROP_LBL + '.  ' + fmt(p.ACRES, 1) + ' acres.  Irrigated in ' + p.N_IRR_YRS + ' of 41 years.'));
    var tex = p.TEXTURE || ('none sampled; Ma et al. used ' + (p.TEXTURE_MA || '-'));
    infoPanel.add(small('Soil (POLARIS 0-100 cm): ' + tex + '.  Roots: ' + p.ROOT_CM + ' cm.  ETo ' + fmt(p.ET0_MMD, 2) +
      ' mm/d.  Tan d_dec ' + fmt(p.DDEC_M, 2) + ' m, d_ext ' + fmt(p.DEXT_M, 2) + ' m, p ' + fmt(p.P, 2) + '.'));
    infoPanel.add(small('Field mean DTW: new ' + fmt(p.DTW_NEW_M, 2) + ' m (min ' + fmt(p.DTW_NEW_MIN_M, 2) +
      ');  Ma ' + (p.MA_COVER ? fmt(p.DTW_MA_M, 2) + ' m (min ' + fmt(p.DTW_MA_MIN_M, 2) + ')' : 'no coverage') +
      ';  run\'s map ' + fmt(p.DTW_RUN_M, 2) + ' m (sigma ' + fmt(p.DTW_SIG_M, 1) + ').  ETa ' + fmt(p.ETA_AF, 1) + ' acre-ft/yr.'));
    infoPanel.add(small('Nearest training well ' + fmt(p.WT_DIST_KM, 2) + ' km (' + p.WT_NEAR_SRC + ' ' + p.WT_NEAR_ID +
      ', well DTW ' + fmt(p.WT_NEAR_DTW_M, 1) + ' m); ' + p.WT_N1KM + ' within 1 km, ' + p.WT_N5KM + ' within 5 km.  ' +
      'Nearest gwx well ' + fmt(p.W_DIST_KM, 2) + ' km; with a time series ' + fmt(p.W_TS_DIST_KM, 2) + ' km.'));

    infoPanel.add(row(['Model (acre-ft/yr)', 'f new', 'f Ma', 'new', 'Ma', 'new - Ma'], true));
    [['TAN', 'Tan'], ['BANTA', 'Banta'], ['AVER', 'Averianov'], ['SHAH', 'Shah']].forEach(function (mm) {
      var k = mm[0];
      infoPanel.add(row([mm[1] + (k === m ? ' *' : ''), fmt(p['F_NEW_' + k], 3), fmt(p['F_MA_' + k], 3),
        fmt(p['S_NEW_' + k], 1), fmt(p['S_MA_' + k], 1), fmt(p['S_NEW_' + k] - p['S_MA_' + k], 1)], k === m));
    });
    infoPanel.add(row(['Tan, the run (report)', fmt(p.F_RUN_TAN, 3), '', fmt(p.S_RUN_TAN, 1), '', ''], false));
    infoPanel.add(row(['Tan, run\'s map recomputed', fmt(p.F_R08_TAN, 3), '', fmt(p.S_R08_TAN, 1), '', ''], false));
    infoPanel.add(small('Field f = coverage-weighted mean of f over the 30 m pixels touching the field ' +
      '(not f at the mean depth). The run used its 2026-10-08 100 m map; "recomputed" is this app\'s inputs on that ' +
      'map, so its gap to the run is the recomputation error and its gap to Tan above is the effect of the new map.'));
    infoPanel.add(fCurveChart(p, m));
    addFieldYearChart(p, m);
  });
}

// f(d) for this field's parameters, with the field-mean depths of both maps.
function fOf(model, d, dDec, dExt, p) {
  if (model === 'BANTA') return Math.max(0, Math.min(1, 1 - d / dExt));
  if (model === 'AVER') return Math.pow(Math.max(0, Math.min(1, 1 - d / dExt)), AVERIANOV_P);
  var span = dExt - dDec;
  if (span <= 0) return d <= dDec ? 1 : 0;
  if (d <= dDec) return 1;
  if (model === 'SHAH') return Math.exp(-Math.log(1 / SHAH_EPS) / span * (d - dDec));
  if (d >= dExt) return 0;
  return Math.pow((dExt - d) / span, p);
}
function fCurveChart(p, m) {
  var key = m;
  var dMax = Math.max(4, Math.min(16, (p.DEXT_M || 6) * 1.05));
  var cols = [{id: 'd', label: 'Depth to water (m)', type: 'number'},
              {id: 'fn', label: 'f, new-DTW parameters', type: 'number'},
              {id: 'fm', label: 'f, Ma et al. parameters', type: 'number'},
              {id: 'vn', label: 'Field mean DTW, new', type: 'number'},
              {id: 'vm', label: 'Field mean DTW, Ma', type: 'number'}];
  var rows = [];
  for (var i = 0; i <= 160; i++) {
    var d = dMax * i / 160;
    rows.push({c: [{v: d}, {v: fOf(key, d, p.DDEC_M, p.DEXT_M, p.P)},
      {v: p.DDEC_MA_M === null || p.DDEC_MA_M === undefined ? null : fOf(key, d, p.DDEC_MA_M, p.DEXT_MA_M, p.P_MA)},
      {v: null}, {v: null}]});
  }
  [[p.DTW_NEW_M, 3], [p.DTW_MA_M, 4]].forEach(function (x) {
    if (x[0] === null || x[0] === undefined || x[0] > dMax) return;
    [0, 1].forEach(function (y) {
      var c = [{v: x[0]}, {v: null}, {v: null}, {v: null}, {v: null}];
      c[x[1]] = {v: y};
      rows.push({c: c});
    });
  });
  rows.sort(function (a, b) { return a.c[0].v - b.c[0].v; });
  return ui.Chart({cols: cols, rows: rows}, 'LineChart', {
    title: MODEL_LABEL[key] + ': f against depth for this field', height: 200,
    hAxis: {title: 'Depth to water (m)'}, vAxis: {title: 'f', viewWindow: {min: 0, max: 1}},
    series: {0: {color: '#52514e'}, 1: {color: '#999999', lineDashStyle: [4, 3]},
             2: {color: '#2a78d6', lineWidth: 2}, 3: {color: '#eb6834', lineWidth: 2}},
    interpolateNulls: false, legend: {position: 'bottom'}, chartArea: {width: '80%'}
  });
}

// Annual subsidy, new vs Ma, for the selected model (one axis; the field's ETa is in the text above).
// For Tan the run's own values are added as a grey reference line.
function addFieldYearChart(p, m) {
  var note = small('Loading the 41 years...');
  infoPanel.add(note);
  fieldYears.filter(ee.Filter.eq('NWI_ID', p.NWI_ID)).sort('YEAR')
    .reduceColumns(ee.Reducer.toList().repeat(5), ['YEAR', 'IRR', 'S_NEW_' + m, 'S_MA_' + m, 'S_RUN_TAN'])
    .get('list').evaluate(function (L, err) {
      infoPanel.remove(note);
      if (err || !L) { infoPanel.add(small('Annual query failed: ' + err)); return; }
      var withRun = m === 'TAN';
      var cols = [{id: 'y', label: 'Year', type: 'number'},
                  {id: 'n', label: 'New DTW', type: 'number'},
                  {id: 'm', label: 'Ma et al.', type: 'number'}];
      if (withRun) cols.push({id: 'r', label: 'The run (report)', type: 'number'});
      var rows = L[0].map(function (y, i) {
        var irr = L[1][i] === 1;
        var c = [{v: y}, {v: irr ? L[2][i] : null}, {v: irr ? L[3][i] : null}];
        if (withRun) c.push({v: irr ? L[4][i] : null});
        return {c: c};
      });
      infoPanel.add(ui.Chart({cols: cols, rows: rows}, 'LineChart', {
        title: 'Annual subsidy, ' + MODEL_LABEL[m] + ' (acre-ft; gaps = not irrigated)', height: 210,
        hAxis: {title: 'Year', format: '####'}, vAxis: {title: 'acre-ft', viewWindow: {min: 0}},
        series: {0: {color: '#2a78d6', pointSize: 3}, 1: {color: '#eb6834', pointSize: 3},
                 2: {color: '#898781', lineDashStyle: [4, 3]}},
        interpolateNulls: false, legend: {position: 'bottom'}, chartArea: {width: '78%'}
      }));
    });
}

// === CLICK: HA =======================================================================

function showHa(feature) {
  infoPanel.clear();
  state.selected = feature;
  refreshHighlight();
  statusLabel.setValue('Hydrographic area selected.');
  var loading = small('Loading HA...');
  infoPanel.add(loading);
  ee.Feature(feature).toDictionary().evaluate(function (p, err) {
    infoPanel.remove(loading);
    if (err || !p) { infoPanel.add(small('Query failed: ' + err)); return; }
    var m = state.model;
    infoPanel.add(ui.Label(haTitle(p.HA_NAME, p.HYD_AREA), {fontWeight: 'bold', fontSize: '13px', margin: '2px 0'}));
    infoPanel.add(small(fmt(p.N_FIELDS) + ' fields, ' + fmt(p.N_IRR) + ' irrigated in at least one year; ETa ' +
      fmt(p.ETA_AF) + ' acre-ft/yr.  Fields with a Tan subsidy > 0.5 acre-ft/yr: new ' + fmt(p.N_SUB_NEW) +
      ', Ma ' + fmt(p.N_SUB_MA) + ', the run ' + fmt(p.N_SUB_RUN) + '.'));
    infoPanel.add(small('Acre-weighted field DTW (fields covered by both maps, ' + fmt(p.N_COVER) + '): new ' +
      fmt(p.DTW_NEW_M, 2) + ' m, Ma ' + fmt(p.DTW_MA_M, 2) + ' m.'));
    infoPanel.add(small('Training wells: median distance from irrigated fields ' + fmt(p.MED_WT_KM, 1) + ' km; ' +
      fmt(p.N_FAR5) + ' fields > 5 km away, with Tan subsidy new ' + fmt(p.S_NEW_TAN_FAR5) + ', Ma ' +
      fmt(p.S_MA_TAN_FAR5) + ', the run ' + fmt(p.S_RUN_TAN_FAR5) + ' acre-ft/yr.'));
    infoPanel.add(row(['Model (acre-ft/yr)', 'new', 'Ma', 'new - Ma', 'report'], true));
    [['TAN', 'Tan'], ['BANTA', 'Banta'], ['AVER', 'Averianov'], ['SHAH', 'Shah']].forEach(function (mm) {
      var k = mm[0];
      infoPanel.add(row([mm[1] + (k === m ? ' *' : ''), fmt(p['S_NEW_' + k]), fmt(p['S_MA_' + k]), fmt(p['D_' + k]),
        k === 'TAN' ? 'Ma ' + fmt(p.S_MA_TAN_RPT) : ''], k === m));
    });
    infoPanel.add(row(['Tan, the run (report)', fmt(p.S_RUN_TAN), '', '', ''], false));
    infoPanel.add(row(['Tan, run\'s map recomputed', fmt(p.S_R08_TAN), '', '', ''], false));
    infoPanel.add(small('"report" = docs/tables/ha_gw_subsidy_new_vs_ma.csv, made with the run\'s 2026-10-08 map and Ma et al. ' +
      'on its 100 m grid; this app uses the new 30 m map and Ma et al. on that grid.'));
    addHaYearChart(p, m);
    addTopFields(p, m);
  });
}

function addHaYearChart(p, m) {
  haYears.filter(ee.Filter.eq('HYD_AREA', p.HYD_AREA)).sort('YEAR')
    .reduceColumns(ee.Reducer.toList().repeat(4), ['YEAR', 'S_NEW_' + m, 'S_MA_' + m, 'S_RUN_TAN']).get('list')
    .evaluate(function (L, err) {
      if (err || !L || !L[0].length) return;
      var withRun = m === 'TAN';
      var data = [withRun ? ['Year', 'New DTW', 'Ma et al.', 'The run (report)'] : ['Year', 'New DTW', 'Ma et al.']];
      L[0].forEach(function (y, i) { data.push(withRun ? [y, L[1][i], L[2][i], L[3][i]] : [y, L[1][i], L[2][i]]); });
      infoPanel.add(ui.Chart(data, 'LineChart', {
        title: 'Annual subsidy, irrigated fields, ' + MODEL_LABEL[m] + ' (acre-ft)', height: 200,
        hAxis: {format: '####'}, vAxis: {viewWindow: {min: 0}},
        series: {0: {color: '#2a78d6'}, 1: {color: '#eb6834'}, 2: {color: '#898781', lineDashStyle: [4, 3]}},
        legend: {position: 'bottom'}, chartArea: {width: '78%'}
      }));
    });
}

function addTopFields(p, m) {
  var top = fieldsAll.filter(ee.Filter.eq('HYD_AREA', p.HYD_AREA)).filter(ee.Filter.gt('N_IRR_YRS', 0))
    .map(function (f) { return f.set('absD', ee.Number(f.get('D_' + m)).abs()); })
    .sort('absD', false).limit(8);
  top.reduceColumns(ee.Reducer.toList().repeat(4), ['NWI_ID', 'D_' + m, 'S_NEW_' + m, 'WT_DIST_KM']).get('list')
    .evaluate(function (L, err) {
      if (err || !L || !L[0].length) return;
      infoPanel.add(small('Fields with the largest change (click to open):', '#333'));
      L[0].forEach(function (id, i) {
        infoPanel.add(ui.Button({
          label: id + ': change ' + fmt(L[1][i], 1) + ', new ' + fmt(L[2][i], 1) + ' acre-ft/yr; well ' + fmt(L[3][i], 1) + ' km',
          style: {margin: '0', fontSize: '11px', stretch: 'horizontal'},
          onClick: function () { selectField(id); }
        }));
      });
    });
}

function selectField(id) {
  var f = ee.Feature(fieldsAll.filter(ee.Filter.eq('NWI_ID', id)).first());
  f.geometry().centroid(1).coordinates().evaluate(function (c) {
    if (c) map.setCenter(c[0], c[1], Math.max(state.zoom, 13));
  });
  showField(f);
}

// === CLICK: WELL ==================================================================

function showWell(feature) {
  infoPanel.clear();
  state.selected = feature;
  refreshHighlight();
  statusLabel.setValue('Training well selected.');
  ee.Feature(feature).toDictionary().evaluate(function (p, err) {
    if (err || !p) { infoPanel.add(small('Query failed: ' + err)); return; }
    infoPanel.add(ui.Label('Training well ' + p.canonical_id, {fontWeight: 'bold', fontSize: '13px', margin: '2px 0'}));
    infoPanel.add(small('Source ' + p.source + ', role ' + p.role + ', confinement ' + p.confine + '.'));
    infoPanel.add(small('Well depth to water ' + fmt(p.dtw_m, 2) + ' m.  At its pixel: new map ' + fmt(p.dtw_new_m, 2) +
      ' m, Ma ' + fmt(p.dtw_ma_m, 2) + ' m, run\'s map ' + fmt(p.dtw_run_m, 2) + ' m (sigma ' + fmt(p.dtw_sigma_m, 1) +
      ').  Map - well: new ' + fmt(p.new_minus_well_m, 2) + ' m, Ma ' + fmt(p.ma_minus_well_m, 2) + ' m, run\'s map ' +
      fmt(p.run_minus_well_m, 2) + ' m.'));
    infoPanel.add(small('Both handily maps were fitted to these wells, so their differences here are not an independent check.'));
  });
}

// === CLICK HANDLER =================================================================

map.onClick(function (coords) {
  var pt = ee.Geometry.Point([coords.lon, coords.lat]);
  statusLabel.setValue('Querying...');
  var tol = map.getScale() * 6;   // about 6 screen pixels
  var tryWell = state.layers['Training wells (new DTW)'];
  var well = tryWell ? wellsTrain.filterBounds(pt.buffer(tol)).first() : null;
  var field = fieldsAll.filterBounds(pt).first();
  var ha = has.filterBounds(pt).first();
  ee.Dictionary({well: tryWell ? well : null, field: field, ha: ha}).evaluate(function (r, err) {
    if (err) { statusLabel.setValue('Query failed: ' + err); return; }
    if (r.well) { showWell(well); return; }
    if (r.field) { showField(field); return; }
    if (r.ha) { showHa(ha); return; }
    infoPanel.clear();
    state.selected = null;
    refreshHighlight();
    statusLabel.setValue('Nothing here.');
  });
});

// === RANKING, CHECKS, NOTES, REFERENCES ===========================================

function buildRanking(body) {
  var m = state.model;
  body.add(small('By total change, new - Ma, over irrigated fields (' + MODEL_LABEL[m] + '). Click to open.'));
  var hv = has.filter(ee.Filter.gt('N_IRR', 0));
  [['Largest decreases (new < Ma)', true], ['Largest increases (new > Ma)', false]].forEach(function (g) {
    body.add(ui.Label(g[0], {fontSize: '11px', fontWeight: 'bold', margin: '4px 0 0 0'}));
    var top = hv.sort('D_' + m, g[1]).limit(8);
    top.reduceColumns(ee.Reducer.toList().repeat(5), ['HYD_AREA', 'HA_NAME', 'D_' + m, 'S_NEW_' + m, 'S_MA_' + m])
      .get('list').evaluate(function (L) {
        if (!L) return;
        L[0].forEach(function (code, i) {
          body.add(ui.Button({
            label: haTitle(L[1][i], code) + ': ' + fmt(L[2][i]) + ' (new ' + fmt(L[3][i]) + ', Ma ' + fmt(L[4][i]) + ')',
            style: {margin: '0', fontSize: '11px', stretch: 'horizontal'},
            onClick: function () {
              var f = ee.Feature(has.filter(ee.Filter.eq('HYD_AREA', code)).first());
              map.centerObject(f, 9);
              showHa(f);
            }
          }));
        });
      });
  });
}

function buildChecks(body) {
  body.add(small('Computed now from the app\'s tables (Tan) and compared with the report (README.md, docs/GW_SUBSIDY.md).'));
  var irr = fieldsAll.filter(ee.Filter.gt('N_IRR_YRS', 0));
  var cov = irr.filter(ee.Filter.eq('MA_COVER', 1));
  var sumAll = irr.reduceColumns(ee.Reducer.sum().repeat(4), ['S_RUN_TAN', 'ETA_AF', 'S_R08_TAN', 'S_NEW_TAN']).get('sum');
  var sumCov = cov.reduceColumns(ee.Reducer.sum().repeat(2), ['S_NEW_TAN', 'S_MA_TAN']).get('sum');  // new map, Ma
  var med = cov.reduceColumns(ee.Reducer.median().repeat(2), ['DTW_NEW_M', 'DTW_MA_M']).get('median');
  ee.Dictionary({a: sumAll, c: sumCov, n: cov.size(), md: med}).evaluate(function (r, err) {
    if (err) { body.add(small('Checks failed: ' + err)); return; }
    function line(text, app, rep, dec, exact) {
      var same = Number(app).toFixed(dec || 0) === Number(rep).toFixed(dec || 0);
      body.add(row([text, fmt(app, dec), fmt(rep, dec), exact ? (same ? 'same' : 'DIFFERS') : (same ? 'same' : 'see note')], false));
    }
    body.add(row(['', 'app', 'report', ''], true));
    line('Tan, the run, all fields', r.a[0], REPORT.subsidyAll, 0, true);
    line('ETa, irrigated field-years', r.a[1], REPORT.etaAll, 0, true);
    line('Tan, run\'s map recomputed', r.a[2], REPORT.subsidyAll, 0, false);
    line('Tan, new DTW, all fields', r.a[3], REPORT.subsidyAll, 0, false);
    line('Fields covered by both maps', r.n, REPORT.nCovered, 0, false);
    line('Covered fields, Tan, new DTW', r.c[0], REPORT.coveredNew, 0, false);
    line('Covered fields, Tan, Ma et al.', r.c[1], REPORT.coveredMa, 0, false);
    line('Median field DTW, new (m)', r.md[0], REPORT.medianDtwNew, 1, false);
    line('Median field DTW, Ma (m)', r.md[1], REPORT.medianDtwMa, 1, false);
    body.add(small('The run\'s own values match exactly. The report used the run\'s 2026-10-08 100 m map with Ma et al. ' +
      'on that grid; this app uses the new 30 m map with Ma et al. on the 30 m grid, so the other lines are not expected to ' +
      'match. "Run\'s map recomputed" applies this app\'s inputs to the run\'s map: its gap to the run (+0.75%) is the ' +
      'recomputation error, mostly growing-season ETo in Lovelock Valley (073), Carson Desert (101) and Marys River (042).'));
  });
}

function buildNotes(body) {
  [
    'Subsidy: each month min(f x ETa, max(ETa - effective precipitation, 0)); summed over field-years that ' +
      'IrrMapper marks more than 50% irrigated and divided by 41 years (1985-2025): acre-ft/yr, as in the report.',
    'f: fraction of ETa that groundwater can supply. Tan et al. (2025) parameters (d_dec, d_ext, p) from the ' +
      'field\'s POLARIS texture (0-100 cm), rooting depth (200 cm for alfalfa, orchards, vineyards, forest, ' +
      'shrubland and wetlands; else 100 cm) and April-October ETo. Banta (d_dec = 0, p = 1), Averianov ' +
      '(d_dec = 0, p = 3) and Shah (exponential decay, f = 0.02 at d_ext) use the same d_ext.',
    'Field f is the coverage-weighted mean of f over the field\'s 30 m DTW pixels. Fields with no pixel ' +
      'shallower than 16 m have f = 0 (for Shah too, as in the run).',
    'New DTW: handily NV parity map at 30 m, gs://ucrc-wtd/handily/nv/handily_dtw_nv_parity_30m.tif (2026-10-21). ' +
      'Every value with it is computed with the run\'s code on the 2026-08-20 fields from the GeoPackage the run used.',
    'The run (the report) used the 2026-10-08 statewide 100 m build of the same model; its own Tan values are ' +
      'F_RUN_TAN and S_RUN_TAN. The same code with this app\'s inputs on that map totals 34,993 acre-ft/yr against ' +
      'the run\'s 34,733 (+0.75%): the recomputation error. The run\'s ETo export and its copy of the map exist only ' +
      'on the run computer. The uncertainty layer (sigma) is that build\'s; the bucket has none for the 30 m map.',
    'Ma et al. (2026): 24 m map (wtd_nevada.tif) averaged onto the new map\'s 30 m grid; Nevada only, so fields ' +
      'outside Nevada have no Ma value (MA_COVER = 0). "Only fields covered by the Ma et al. map" gives the ' +
      'like-for-like comparison. The report averaged Ma onto the run\'s 100 m grid, which hides narrow shallow ' +
      'areas and gives much less Tan subsidy (18,563 against 28,630 acre-ft/yr on the 30 m grid).',
    'Training wells: the 9,448 wells the handily NV parity model was trained on ' +
      '(handily_wells_train_nv_parity.csv, dated 2026-10-21): 8,628 NDWR well-log depths and 820 monitoring ' +
      'wells. Distances are from the field boundary (0 = a well inside the field). The new map is within a median ' +
      '1.9 m of the training wells and 5.9 m of other wells (the run\'s map: 1.8 and 6.0 m).',
    'DTW maps are single snapshots; well depths are long-term means.',
    'Built by gee/prep/ in the nwi-t1-ag-water-use repository (branch gw-subsidy-assessments).'
  ].forEach(function (t) { body.add(small('- ' + t)); });
}

var REFERENCES = [
  {tag: 'Tan model', text: 'Tan, S.-C., Jiang, X.-W., et al. (2025). Revisiting the quantitative relationship between ' +
    'groundwater evapotranspiration and water table depth: A numerical study. J. Hydrol., 658, 133100.',
   url: 'https://doi.org/10.1016/j.jhydrol.2025.133100'},
  {tag: 'Shah model', text: 'Shah, N., Nachabe, M., & Ross, M. (2007). Extinction depth and evapotranspiration from ' +
    'ground water under selected land covers. Groundwater, 45(3), 329-338.',
   url: 'https://doi.org/10.1111/j.1745-6584.2007.00302.x'},
  {tag: 'Averianov model', text: 'Averianov, S. F. (1956). Seepage from irrigation canals and its influence on ' +
    'regime of ground water.'},
  {tag: 'Banta model', text: 'Banta, E. R. (2000). MODFLOW-2000, documentation of packages for simulating ' +
    'evapotranspiration with a segmented function (ETS1) and drains with return flow (DRT1). USGS OFR 2000-466.',
   url: 'https://doi.org/10.3133/ofr00466'},
  {tag: 'Ma et al. DTW', text: 'Ma, Y., Condon, L. E., Koch, J., et al. (2026). High resolution US water table depth ' +
    'estimates reveal quantity of accessible groundwater. Communications Earth & Environment, 7(1), 45.',
   url: 'https://doi.org/10.1038/s43247-025-03094-3'},
  {tag: 'POLARIS', text: 'Chaney, N. W., et al. (2019). POLARIS soil properties: 30-m probabilistic maps of soil ' +
    'properties over the contiguous United States. Water Resour. Res., 55(4), 2916-2938.',
   url: 'https://doi.org/10.1029/2018WR022797'},
  {tag: 'Soil hydraulics', text: 'Carsel, R. F., & Parrish, R. S. (1988). Developing joint probability distributions ' +
    'of soil water retention characteristics. Water Resour. Res., 24(5), 755-769.',
   url: 'https://doi.org/10.1029/WR024i005p00755'},
  {tag: 'OpenET', text: 'Melton, F., et al. (2022). OpenET: Filling a critical data gap in water management for the ' +
    'western United States. JAWRA, 58(6), 971-994.', url: 'https://doi.org/10.1111/1752-1688.12956'},
  {tag: 'OpenET', text: 'Volk, J. M., et al. (2024). Assessing the accuracy of OpenET satellite-based ' +
    'evapotranspiration data to support water resource and land management applications. Nature Water, 2, 193-205.',
   url: 'https://doi.org/10.1038/s44221-023-00181-7'},
  {tag: 'IrrMapper', text: 'Ketchum, D., et al. (2020). IrrMapper: A machine learning approach for high resolution ' +
    'mapping of irrigated agriculture across the western U.S. Remote Sensing, 12(14), 2328.',
   url: 'https://doi.org/10.3390/rs12142328'},
  {tag: 'CDL', text: 'Boryan, C., Yang, Z., Mueller, R., & Craig, M. (2011). Monitoring US agriculture: the USDA NASS ' +
    'Cropland Data Layer program. Geocarto International, 26(5), 341-358.',
   url: 'https://doi.org/10.1080/10106049.2011.562309'},
  {tag: 'GEE', text: 'Gorelick, N., et al. (2017). Google Earth Engine: Planetary-scale geospatial analysis for ' +
    'everyone. Remote Sensing of Environment, 202, 18-27.', url: 'https://doi.org/10.1016/j.rse.2017.06.031'},
  {tag: 'GEE community catalog', text: 'Roy, S., Majumdar, S., & Swetnam, T. (2025). ' +
    'samapriya/awesome-gee-community-datasets: Community Catalog (3.9.0). Zenodo.',
   url: 'https://doi.org/10.5281/zenodo.17641528'}
];
function buildReferences(body) {
  REFERENCES.forEach(function (r) {
    var tagStyle = {fontSize: '10px', fontWeight: 'bold', margin: '0 4px 0 0', color: r.url ? NV_BLUE : '#222'};
    body.add(ui.Panel([
      r.url ? ui.Label('[' + r.tag + ']', tagStyle, r.url) : ui.Label('[' + r.tag + ']', tagStyle),
      ui.Label(r.text, {fontSize: '10px', color: '#444', margin: '0', stretch: 'horizontal'})
    ], ui.Panel.Layout.flow('horizontal'), {margin: '0 0 4px 0', stretch: 'horizontal'}));
  });
}

// === REFRESH ======================================================================

function refresh() {
  paintLayers();
  updateLegend();
  updateSummary();
  rankSection.rebuild();
}

refresh();
