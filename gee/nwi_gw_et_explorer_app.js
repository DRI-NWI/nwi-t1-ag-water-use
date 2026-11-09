// =============================================================================
// NWI Groundwater ET Explorer
// Single-file GEE JS app for the Nevada Water Initiative (NWI) presentation.
// Paste into https://code.earthengine.google.com and run.
//
// Estimates the shallow-groundwater subsidy to crop ET, f(d) x ETa, on Nevada
// irrigated fields. Four f(d) models are selectable (Tan et al. 2025 is the
// default and headline); all derive their (d_dec, d_ext, p) from per-pixel soil
// texture (POLARIS) and rooting depth (CDL), matching the project pipeline.
// =============================================================================

// === CONFIG ===================================================================

var ASSET_FIELDS    = 'projects/ee-prevelle/assets/NWI/Nevada_agricultural_field_boundaries_20260506';
var ASSET_GW_BASINS = 'projects/ee-prevelle/assets/NV/NWI_Investigations';
var ASSET_WTD       = 'projects/nmose-openet/assets/WTD-US'; // Ma et al. (2026), per-state
var ID_OPENET       = 'OpenET/ENSEMBLE/CONUS/GRIDMET/MONTHLY/v2_0'; // 1999-2024
var ID_OPENET_V21   = 'projects/openet/assets/ensemble/conus/gridmet/monthly/v2_1'; // 2013-2026; used for 2025+
var ID_IRR          = 'UMT/Climate/IrrMapper_RF/v1_2';
var ID_CDL          = 'USDA/NASS/CDL';
var POLARIS_BASE    = 'projects/sat-io/open-datasets/polaris';

var YEAR_MIN = 2000;
var YEAR_MAX = 2025;
// OpenET v2.0 has no 2025 data; switch to the v2.1 ensemble for this year onward.
var OPENET_V21_MIN_YEAR = 2025;

// Contiguous month ranges [startMonth, endMonth] for each aggregation.
var AGG_MONTHS = {
  'Summer (JJA)':            [6, 8],
  'Growing Season (Apr-Oct)': [4, 10],
  'Annual':                  [1, 12]
};

var AGG_DEFAULT      = 'Annual';
var YEAR_DEFAULT     = 2025;
var DEXT_MULT_DEFAULT = 1.0;  // multiplier on the texture-derived d_ext (sensitivity)
var OPACITY_DEFAULT  = 0.6;   // raster layers (WTD / OpenET / IrrMapper) over satellite

// f(d) models. label -> key; first is the default (Tan, the headline model).
var MODELS = [
  {label: 'Tan et al. (2025)',    key: 'tan'},
  {label: 'Banta (2000) linear',  key: 'banta'},
  {label: 'Averianov (1956)',     key: 'averianov'},
  {label: 'Shah et al. (2007)',   key: 'shah'}
];
var MODEL_DEFAULT_LABEL = MODELS[0].label;
var MODEL_LABEL_BY_KEY  = {};
var MODEL_KEY_BY_LABEL  = {};
MODELS.forEach(function (m) { MODEL_LABEL_BY_KEY[m.key] = m.label; MODEL_KEY_BY_LABEL[m.label] = m.key; });

// Model constants (match src/gw_subsidy/config.py / models.py).
var AVERIANOV_P = 3.0;   // Averianov power-law exponent
var SHAH_EPS    = 0.02;  // ET ratio anchoring the Shah exponential decay rate

// Candidate property names holding the basin name on gwBasins features.
var BASIN_NAME_PROPS = ['BasinName', 'SubAreaNam', 'BasinID', 'Basin', 'NAME', 'Name', 'name'];

// --- POLARIS root-zone (0-100 cm) thickness weights (cm) ---------------------
var POLARIS_DEPTHS  = ['0_5', '5_15', '15_30', '30_60', '60_100'];
var POLARIS_WEIGHTS = [5, 10, 15, 30, 40];

// --- Tan et al. (2025) Table 3 vegetated params (ET0 = 5 mm/d) ----------------
// Index order = USDA_CLASSES below. Values in cm; converted to m at use.
var USDA_CLASSES = [
  'sand', 'loamy sand', 'sandy loam', 'loam', 'silt loam', 'silt',
  'sandy clay loam', 'clay loam', 'silty clay loam', 'sandy clay', 'silty clay', 'clay'
];
var CLASS_IDX = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11];
// Rooting depth 100 cm (shallow) and 200 cm (deep).
var P_100    = [7.06, 10.08, 7.34, 6.47, 10.05, 8.84, 9.46, 11.08, 14.46, 18.70, 19.03, 18.99];
var DDEC_100 = [29.3, 33.0, 44.9, 60.8, 67.6, 61.5, 42.8, 51.1, 38.5, 28.5, 14.1, 48.1];
var DEXT_100 = [183.4, 212.4, 295.3, 273.5, 650.0, 600.0, 360.2, 600.0, 600.0, 362.2, 585.0, 1094.7];
var P_200    = [6.51, 8.52, 7.30, 5.82, 7.56, 6.94, 8.94, 9.03, 13.49, 17.93, 13.06, 17.21];
var DDEC_200 = [29.5, 36.4, 50.5, 65.2, 72.9, 72.0, 43.5, 55.4, 40.0, 31.6, 14.2, 55.1];
var DEXT_200 = [285.6, 311.2, 388.3, 464.1, 709.2, 681.0, 482.0, 674.0, 712.0, 484.7, 730.0, 1500.0];

// CDL codes treated as deep-rooted (alfalfa, shrub, wetlands, riparian, grapes, orchards)
// -> 200 cm rooting depth row; everything else uses the 100 cm row.
var CDL_DEEP_ROOT = [36, 152, 190, 195, 141, 142, 143, 69, 211, 204, 223, 75, 76, 77];
var CDL_ROOT_YEAR = 2020; // fixed CDL year as a proxy for the field CROP_major mode

// Palettes.
var NV_BLUE       = '#1f4e79';
var PALETTE_ET    = ['#440154', '#3b528b', '#21918c', '#5ec962', '#fde725']; // viridis
var PALETTE_GWET  = ['#f7fcf5', '#c7e9c0', '#74c476', '#31a354', '#006d2c']; // light -> dark green (distinct from blue WTD)

// Discrete WTD classes. Edges are upper bounds; last class is open-ended.
var WTD_CLASSES = [
  {label: 'Very shallow (<2 m / <6.6 ft)',        edge: 2,  color: '#08306b'},
  {label: 'Shallow (2-5 m / 6.6-16.4 ft)',        edge: 5,  color: '#2171b5'},
  {label: 'Moderate (5-10 m / 16.4-32.8 ft)',     edge: 10, color: '#6baed6'},
  {label: 'Deep (>=10 m / >=32.8 ft)',            edge: null, color: '#c6dbef'}
];
var PALETTE_WTD_CLASS = WTD_CLASSES.map(function (c) { return c.color; });

var ET_MAX_MM     = 1200;
var GWET_MAX_MM   = 500;

// Coarser reduction scale for area/volume totals (basins, histograms).
var REDUCE_SCALE  = 500;

// Unit conversions.
var M2_PER_ACRE   = 4046.86;
var M3_PER_ACFT   = 1233.48;
var MM_PER_INCH   = 25.4;

// WTD NoData fill (IEEE float max) — mask it and non-positive depths.
var WTD_NODATA_MIN = 1e30;

// === DATA LOADERS =============================================================

var fields     = ee.FeatureCollection(ASSET_FIELDS);
var gwBasins   = ee.FeatureCollection(ASSET_GW_BASINS);
var nvBounds   = gwBasins.geometry().bounds();
var wtdNV      = ee.ImageCollection(ASSET_WTD).filter(ee.Filter.eq('state', 'NV')).first();
wtdNV = wtdNV.updateMask(wtdNV.gt(0).and(wtdNV.lt(WTD_NODATA_MIN)));

// --- POLARIS soil texture -> USDA class -> Tan params (static per-pixel) ------

function polarisRootZoneMean(prop) {
  var ic = ee.ImageCollection(POLARIS_BASE + '/' + prop + '_mean');
  var total = 0;
  POLARIS_WEIGHTS.forEach(function (w) { total += w; });
  var weighted = POLARIS_DEPTHS.map(function (d, i) {
    return ic.filter(ee.Filter.eq('system:index', prop + '_' + d)).first()
             .select('b1').multiply(POLARIS_WEIGHTS[i]);
  });
  return ee.ImageCollection(weighted).sum().divide(total).rename(prop);
}

// Root-zone sand/silt/clay (% by mass), renormalized to sum to 100.
var sandRaw = polarisRootZoneMean('sand');
var siltRaw = polarisRootZoneMean('silt');
var clayRaw = polarisRootZoneMean('clay');
var soilTot = sandRaw.add(siltRaw).add(clayRaw).max(1e-6);
var sand = sandRaw.divide(soilTot).multiply(100);
var silt = siltRaw.divide(soilTot).multiply(100);
var clay = clayRaw.divide(soilTot).multiply(100);

// USDA texture class index (0-11), replicating soil_texture.usda_texture_class.
// Build per-class boolean masks, then assign in REVERSE priority so the first
// matching rule (sand) wins. Default = loam (index 3).
var sc  = silt.add(clay.multiply(1.5));   // silt + 1.5*clay
var s2c = silt.add(clay.multiply(2.0));   // silt + 2*clay
var rules = [
  /* 0 sand            */ sc.lt(15),
  /* 1 loamy sand      */ sc.gte(15).and(s2c.lt(30)),
  /* 2 sandy loam      */ clay.gte(7).and(clay.lt(20)).and(sand.gt(52)).and(s2c.gte(30))
                            .or(clay.lt(7).and(silt.lt(50)).and(s2c.gte(30))),
  /* 3 loam            */ clay.gte(7).and(clay.lt(27)).and(silt.gte(28)).and(silt.lt(50)).and(sand.lte(52)),
  /* 4 silt loam       */ silt.gte(50).and(clay.gte(12)).and(clay.lt(27))
                            .or(silt.gte(50).and(silt.lt(80)).and(clay.lt(12))),
  /* 5 silt            */ silt.gte(80).and(clay.lt(12)),
  /* 6 sandy clay loam */ clay.gte(20).and(clay.lt(35)).and(silt.lt(28)).and(sand.gt(45)),
  /* 7 clay loam       */ clay.gte(27).and(clay.lt(40)).and(sand.gt(20)).and(sand.lte(45)),
  /* 8 silty clay loam */ clay.gte(27).and(clay.lt(40)).and(sand.lte(20)),
  /* 9 sandy clay      */ clay.gte(35).and(sand.gt(45)),
  /* 10 silty clay     */ clay.gte(40).and(silt.gte(40)),
  /* 11 clay           */ clay.gte(40).and(sand.lte(45)).and(silt.lt(40))
];
var classImg = ee.Image(3); // loam fallback
for (var ri = rules.length - 1; ri >= 0; ri--) {
  classImg = classImg.where(rules[ri], ri);
}
classImg = classImg.updateMask(sandRaw.mask()).toInt();

// Deep-root mask from a representative CDL year (proxy for CROP_major mode).
var cdl = ee.ImageCollection(ID_CDL)
  .filter(ee.Filter.calendarRange(CDL_ROOT_YEAR, CDL_ROOT_YEAR, 'year'))
  .first().select('cropland');
var deepRoot = cdl.remap(CDL_DEEP_ROOT, ee.List.repeat(1, CDL_DEEP_ROOT.length), 0).gt(0);

// Per-pixel Tan params: pick the 100 cm or 200 cm row per pixel via deepRoot.
function pickParam(vals100, vals200) {
  var img100 = classImg.remap(CLASS_IDX, vals100);
  var img200 = classImg.remap(CLASS_IDX, vals200);
  return img100.where(deepRoot, img200);
}
var dDec = pickParam(DDEC_100, DDEC_200).divide(100); // cm -> m
var dExt0 = pickParam(DEXT_100, DEXT_200).divide(100); // cm -> m (texture d_ext, pre-multiplier)
var pExp  = pickParam(P_100, P_200);

// === GW-ET FRACTION f(d) ======================================================

// f(d) for the selected model, per pixel. d = WTD (m); dDec/pExp from texture;
// dExt = texture d_ext scaled by the sensitivity multiplier. All four models are
// the Tan et al. (2025) Eq. 5 family (see src/gw_subsidy/models.py).
function gwFracImage(model, dExtMult) {
  var d    = wtdNV;
  var dExt = dExt0.multiply(dExtMult);

  if (model === 'banta') {
    // d_dec = 0, p = 1 (MODFLOW EVT/ETS linear).
    return ee.Image(1).subtract(d.divide(dExt)).clamp(0, 1);
  }
  if (model === 'averianov') {
    // d_dec = 0, power-law exponent AVERIANOV_P.
    return ee.Image(1).subtract(d.divide(dExt)).clamp(0, 1).pow(AVERIANOV_P);
  }

  var span = dExt.subtract(dDec);
  if (model === 'shah') {
    // Plateau to d_dec, then exponential decay; k anchored so f = eps at d_ext.
    var k = ee.Image(Math.log(1 / SHAH_EPS)).divide(span); // -ln(eps)/span
    var fs = d.subtract(dDec).max(0).multiply(k).multiply(-1).exp();
    fs = fs.where(d.lte(dDec), 1);
    fs = fs.where(span.lte(0), d.lte(dDec));
    return fs.clamp(0, 1);
  }

  // Tan (default): unified piecewise f(d).
  var base = dExt.subtract(d).divide(span).clamp(0, 1);
  var ft = base.pow(pExp);
  ft = ft.where(d.lte(dDec), 1);
  ft = ft.where(d.gte(dExt), 0);
  ft = ft.where(span.lte(0), d.lte(dDec)); // degenerate soil params guard
  return ft.clamp(0, 1);
}

// Server-side basin-name lookup: try each candidate property in order.
function resolveBasinName(feature) {
  var f = ee.Feature(feature);
  var name = ee.String('');
  for (var i = 0; i < BASIN_NAME_PROPS.length; i++) {
    name = ee.String(ee.Algorithms.If(
      name.length().gt(0), name,
      ee.Algorithms.If(f.get(BASIN_NAME_PROPS[i]),
        ee.String(f.get(BASIN_NAME_PROPS[i])),
        ee.String(''))
    ));
  }
  return ee.String(ee.Algorithms.If(
    name.length().gt(0), name, f.get('system:index')
  ));
}

function dateRangeForYear(year) {
  var start = ee.Date.fromYMD(year, 1, 1);
  var end   = ee.Date.fromYMD(year, 12, 31).advance(1, 'day');
  return [start, end];
}

// OpenET ensemble collection for a year: v2.1 for 2025+ (v2.0 stops at 2024),
// v2.0 otherwise. Both expose the same 'et_ensemble_mad' band.
function openetCollection(year) {
  return ee.ImageCollection(year >= OPENET_V21_MIN_YEAR ? ID_OPENET_V21 : ID_OPENET);
}

function etForYear(year, aggKey) {
  var m  = AGG_MONTHS[aggKey];
  var dr = dateRangeForYear(year);
  return openetCollection(year)
    .filterBounds(nvBounds)
    .filterDate(dr[0], dr[1])
    .filter(ee.Filter.calendarRange(m[0], m[1], 'month'))
    .select('et_ensemble_mad')
    .sum();
}

// Classify WTD into discrete classes using WTD_CLASSES edges (map display only).
function classifyWtd(wtdImg) {
  var b = wtdImg.select(0);
  var classified = ee.Image(WTD_CLASSES.length - 1);
  for (var i = WTD_CLASSES.length - 2; i >= 0; i--) {
    classified = classified.where(b.lt(WTD_CLASSES[i].edge), i);
  }
  return classified.updateMask(b.mask()).toInt();
}
var wtdClassified = classifyWtd(wtdNV);

function irrMaskForYear(year) {
  var dr = dateRangeForYear(year);
  return ee.ImageCollection(ID_IRR)
    .filterBounds(nvBounds)
    .filterDate(dr[0], dr[1])
    .mosaic()
    .eq(0); // class 0 = irrigated
}

// Rasterize the field polygons once, to keep fills inside tracked fields.
var fieldsMask = ee.Image().byte().paint(fields, 1).gt(0);

// === CORE COMPUTATIONS (per-pixel) ============================================

function gwComponentsForYear(year, aggKey, model, dExtMult) {
  var et     = etForYear(year, aggKey);
  var irr    = irrMaskForYear(year);
  var gwFrac = gwFracImage(model, dExtMult);
  var gwEt   = et.multiply(gwFrac);
  return {
    et:      et,
    gwFrac:  gwFrac,
    gwEt:    gwEt,
    irr:     irr,
    etIrr:   et.updateMask(irr),
    gwEtIrr: gwEt.updateMask(irr)
  };
}

function yearSummaryForGeom(year, aggKey, model, dExtMult, geom, scale) {
  var c  = gwComponentsForYear(year, aggKey, model, dExtMult);
  var px = ee.Image.pixelArea();
  var stack = c.etIrr.multiply(px).rename('et_vol_mm_m2')
    .addBands(c.gwEtIrr.multiply(px).rename('gwEt_vol_mm_m2'))
    .addBands(c.irr.multiply(px).rename('irr_area_m2'));
  var sums = stack.reduceRegion({
    reducer:   ee.Reducer.sum(),
    geometry:  geom,
    scale:     scale,
    maxPixels: 1e10,
    tileScale: 4
  });
  var meansOverIrr = wtdNV.rename('wtd_m')
    .addBands(c.gwFrac.rename('gwFrac'))
    .updateMask(c.irr)
    .reduceRegion({
      reducer:   ee.Reducer.mean(),
      geometry:  geom,
      scale:     scale,
      maxPixels: 1e10,
      tileScale: 4
    });
  var num = function (raw) { return ee.Number(ee.Algorithms.If(raw, raw, 0)); };
  var mmM2ToAcft = 1 / (1000 * M3_PER_ACFT);
  var etAcft   = num(sums.get('et_vol_mm_m2')).multiply(mmM2ToAcft);
  var gwEtAcft = num(sums.get('gwEt_vol_mm_m2')).multiply(mmM2ToAcft);
  var irrM2    = num(sums.get('irr_area_m2'));
  var denom    = irrM2.max(1);
  var acftToMm = M3_PER_ACFT * 1000;
  return ee.Feature(null, {
    year:        year,
    et_mm:       etAcft.multiply(acftToMm).divide(denom),
    gwEt_mm:     gwEtAcft.multiply(acftToMm).divide(denom),
    et_acft:     etAcft,
    gwEt_acft:   gwEtAcft,
    non_gw_acft: etAcft.subtract(gwEtAcft),
    irr_area_m2: irrM2,
    irr_acres:   irrM2.divide(M2_PER_ACRE),
    wtd_m:       meansOverIrr.get('wtd_m'),
    gwFrac:      meansOverIrr.get('gwFrac')
  });
}

// Monthly FC for a geometry: one Feature per calendar month of `year`. The f(d)
// fraction image is passed in (already model/multiplier-resolved).
function monthlySeriesForGeom(year, gwFracImg, geom, scale) {
  var irr = irrMaskForYear(year);
  var px  = ee.Image.pixelArea();
  var mmM2ToAcft = 1 / (1000 * M3_PER_ACFT);
  var num = function (raw) { return ee.Number(ee.Algorithms.If(raw, raw, 0)); };

  var months = ee.List.sequence(1, 12);
  return ee.FeatureCollection(months.map(function (m) {
    m = ee.Number(m);
    var start = ee.Date.fromYMD(year, m, 1);
    var end   = start.advance(1, 'month');
    var img = openetCollection(year).filterDate(start, end)
      .select('et_ensemble_mad').sum();
    var etMm   = img.updateMask(irr);
    var gwEtMm = img.multiply(gwFracImg).updateMask(irr);
    var meanEt = etMm.reduceRegion({
      reducer: ee.Reducer.mean(), geometry: geom, scale: scale, tileScale: 4, maxPixels: 1e10
    }).get('et_ensemble_mad');
    var meanGw = gwEtMm.reduceRegion({
      reducer: ee.Reducer.mean(), geometry: geom, scale: scale, tileScale: 4, maxPixels: 1e10
    }).get('et_ensemble_mad');
    var volEt = etMm.multiply(px).reduceRegion({
      reducer: ee.Reducer.sum(), geometry: geom, scale: scale, tileScale: 4, maxPixels: 1e10
    }).get('et_ensemble_mad');
    var volGw = gwEtMm.multiply(px).reduceRegion({
      reducer: ee.Reducer.sum(), geometry: geom, scale: scale, tileScale: 4, maxPixels: 1e10
    }).get('et_ensemble_mad');
    var etMmNum = num(meanEt);
    var gwMmNum = num(meanGw);
    return ee.Feature(null, {
      month:         m,
      ET:            etMmNum.divide(MM_PER_INCH),
      'GW-ET':       gwMmNum.divide(MM_PER_INCH),
      ET_mm_shadow:  etMmNum,
      ET_acft:       num(volEt).multiply(mmM2ToAcft),
      'GW-ET_acft':  num(volGw).multiply(mmM2ToAcft),
      nonGW_acft:    num(volEt).multiply(mmM2ToAcft).subtract(num(volGw).multiply(mmM2ToAcft))
    });
  }));
}

// === UI STATE =================================================================

var state = {
  year:     YEAR_DEFAULT,
  agg:      AGG_DEFAULT,
  model:    MODELS[0].key,        // 'tan'
  dExtMult: DEXT_MULT_DEFAULT,
  opacity:  OPACITY_DEFAULT,
  zoom:     6,                    // tracked to zoom-gate field outlines (see paintFieldOutlines)
  selected: null,
  layers: {
    'GW-ET Fields': true,
    'WTD':               true,
    'OpenET':            false,
    'IrrMapper':         false,
    'NWI Basins':        true
  }
};

var HIGHLIGHT_LAYER = 'Selected';
var HIGHLIGHT_COLOR = 'ff00ff';

var suppressToggle = false;

// === UI: MAP ==================================================================

var map = ui.Map();
map.setOptions('HYBRID');
map.setCenter(-117, 39, 6);
map.setControlVisibility({layerList: false, mapTypeControl: false, fullscreenControl: false});

var wtdCaveat = ui.Label(
  'Water table depth: long-term reference snapshot, not year-specific.',
  {
    fontSize: '11px', color: 'white',
    backgroundColor: 'rgba(0,0,0,0.65)', padding: '4px 8px',
    margin: '0', position: 'bottom-right'
  }
);
map.add(wtdCaveat);

// === UI: LEFT PANEL ===========================================================

var leftPanel = ui.Panel({style: {width: '440px', padding: '8px'}});

leftPanel.add(ui.Label('NWI Groundwater ET Explorer', {
  fontSize: '20px', fontWeight: 'bold', color: 'white',
  backgroundColor: NV_BLUE, padding: '10px', margin: '0 0 4px 0',
  textAlign: 'center', stretch: 'horizontal'
}));
leftPanel.add(ui.Label(
  'OpenET ensemble x Water-table depth x IrrMapper — estimated shallow-GW subsidy to crop ET on Nevada irrigated fields',
  {fontSize: '12px', color: '#333', margin: '0 0 8px 0'}
));

// Year slider
var yearLabel  = ui.Label('Year: ' + YEAR_DEFAULT, {fontWeight: 'bold', margin: '6px 0 0 0'});
var yearSlider = ui.Slider({
  min: YEAR_MIN, max: YEAR_MAX, value: YEAR_DEFAULT, step: 1,
  onChange: function (v) { state.year = v; yearLabel.setValue('Year: ' + v); refresh(); },
  style: {stretch: 'horizontal'}
});
leftPanel.add(yearLabel);
leftPanel.add(yearSlider);

// Aggregation
leftPanel.add(ui.Label('OpenET aggregation', {fontWeight: 'bold', margin: '6px 0 0 0'}));
var aggSelect = ui.Select({
  items: Object.keys(AGG_MONTHS), value: AGG_DEFAULT,
  onChange: function (v) { state.agg = v; refresh(); },
  style: {stretch: 'horizontal'}
});
leftPanel.add(aggSelect);

// f(d) model selector (Tan default)
leftPanel.add(ui.Label('GW-ET model f(d)', {fontWeight: 'bold', margin: '6px 0 0 0'}));
var modelSelect = ui.Select({
  items: MODELS.map(function (m) { return m.label; }),
  value: MODEL_DEFAULT_LABEL,
  onChange: function (v) { state.model = MODEL_KEY_BY_LABEL[v]; refresh(); },
  style: {stretch: 'horizontal'}
});
leftPanel.add(modelSelect);
leftPanel.add(ui.Label(
  'd_dec, d_ext, p are per-pixel from POLARIS soil texture + CDL rooting depth ' +
  '(Tan Table 3 vegetated, ET0=5). Banta (linear) and Averianov (p=3) set d_dec=0.',
  {fontSize: '11px', color: '#666', margin: '0 0 0 0'}
));

// d_ext sensitivity multiplier (replaces the fixed-metres extinction-depth slider)
var dExtLabel  = ui.Label('d_ext sensitivity: ' + DEXT_MULT_DEFAULT.toFixed(2) + ' x texture',
  {fontWeight: 'bold', margin: '6px 0 0 0'});
var dExtSlider = ui.Slider({
  min: 0.5, max: 2.0, value: DEXT_MULT_DEFAULT, step: 0.1,
  onChange: function (v) {
    state.dExtMult = v;
    dExtLabel.setValue('d_ext sensitivity: ' + Number(v).toFixed(2) + ' x texture');
    refresh();
  },
  style: {stretch: 'horizontal'}
});
leftPanel.add(dExtLabel);
leftPanel.add(dExtSlider);

// Raster opacity slider
var opacityLabel  = ui.Label('Raster opacity: ' + OPACITY_DEFAULT.toFixed(2),
  {fontWeight: 'bold', margin: '6px 0 0 0'});
var opacitySlider = ui.Slider({
  min: 0.1, max: 1.0, value: OPACITY_DEFAULT, step: 0.05,
  onChange: function (v) {
    state.opacity = v;
    opacityLabel.setValue('Raster opacity: ' + Number(v).toFixed(2));
    map.layers().forEach(function (layer) {
      var name = layer.getName();
      if (name === 'OpenET' || name === 'Water table depth' || name === 'IrrMapper irrigated') {
        layer.setOpacity(v);
      }
    });
  },
  style: {stretch: 'horizontal'}
});
leftPanel.add(opacityLabel);
leftPanel.add(opacitySlider);

// Layer checkboxes
leftPanel.add(ui.Label('Layers', {fontWeight: 'bold', margin: '8px 0 2px 0'}));
var toggleWidgets = {};
var layerNames = [
  'GW-ET Fields', 'WTD', 'OpenET', 'IrrMapper', 'NWI Basins'
];
layerNames.forEach(function (name) {
  var cb = ui.Checkbox({
    label: name, value: state.layers[name],
    onChange: function (v) {
      if (suppressToggle) return;
      state.layers[name] = v;
      if (v && name === 'GW-ET Fields' && state.layers['OpenET']) {
        suppressToggle = true;
        toggleWidgets['OpenET'].setValue(false);
        state.layers['OpenET'] = false;
        suppressToggle = false;
      }
      if (v && name === 'OpenET' && state.layers['GW-ET Fields']) {
        suppressToggle = true;
        toggleWidgets['GW-ET Fields'].setValue(false);
        state.layers['GW-ET Fields'] = false;
        suppressToggle = false;
      }
      refresh();
    }
  });
  toggleWidgets[name] = cb;
  leftPanel.add(cb);
});

var statusLabel = ui.Label('', {color: '#666', margin: '6px 0 0 0'});
leftPanel.add(statusLabel);

var chartsPanel = ui.Panel({style: {margin: '4px 0 0 0'}});
leftPanel.add(chartsPanel);

var legendPanel = ui.Panel({style: {margin: '6px 0 0 0'}});
leftPanel.add(legendPanel);

// === UI: REFERENCES ===========================================================

// Models, datasets, and methods cited by the app (mirrors README References).
// `tag` is a short bold bracketed label identifying what each reference backs.
var REFERENCES = [
  {tag: 'Tan model',
   text: 'Tan, S.-C., Jiang, X.-W., et al. (2025). Revisiting the quantitative relationship ' +
         'between groundwater evapotranspiration and water table depth: A numerical study. ' +
         'Journal of Hydrology, 658, 133100.',
   url: 'https://doi.org/10.1016/j.jhydrol.2025.133100'},
  {tag: 'Shah model',
   text: 'Shah, N., Nachabe, M., & Ross, M. (2007). Extinction Depth and Evapotranspiration from ' +
         'Ground Water under Selected Land Covers. Groundwater, 45(3), 329-338.',
   url: 'https://doi.org/10.1111/j.1745-6584.2007.00302.x'},
  {tag: 'Averianov model',
   text: 'Averianov, S.F. (1956). Seepage from irrigation canals and its influence on regime ' +
         'of ground water.'},
  {tag: 'Banta model',
   text: 'Banta, E. (2000). MODFLOW-2000, the U.S. Geological Survey Modular Ground-Water Model; ' +
         'documentation of the Evapotranspiration with a Segmented Function (ETS1) and Drains ' +
         'with Return Flow (DRT1) packages. USGS Open-File Report 2000-466.',
   url: 'https://doi.org/10.3133/ofr00466'},
  {tag: 'WTD',
   text: 'Ma, Y., Condon, L. E., Koch, J., et al. (2026). High resolution US water table depth ' +
         'estimates reveal quantity of accessible groundwater. Communications Earth & ' +
         'Environment, 7(1), 45.',
   url: 'https://doi.org/10.1038/s43247-025-03094-3'},
  {tag: 'Soil hydraulics',
   text: 'Carsel, R. F., & Parrish, R. S. (1988). Developing joint probability distributions of ' +
         'soil water retention characteristics. Water Resources Research, 24(5), 755-769.',
   url: 'https://doi.org/10.1029/WR024i005p00755'},
  {tag: 'POLARIS',
   text: 'Chaney, N. W., Minasny, B., Herman, J. D., et al. (2019). POLARIS Soil Properties: 30-m ' +
         'Probabilistic Maps of Soil Properties Over the Contiguous United States. Water ' +
         'Resources Research, 55(4), 2916-2938.',
   url: 'https://doi.org/10.1029/2018WR022797'},
  {tag: 'OpenET',
   text: 'Melton, F., Huntington, J., Grimm, R., et al. (2022). OpenET: Filling a Critical Data ' +
         'Gap in Water Management for the Western United States. JAWRA.',
   url: 'https://doi.org/10.1111/1752-1688.12956'},
  {tag: 'OpenET',
   text: 'Volk, J. M., Huntington, J. L., Melton, F. S., et al. (2024). Assessing the accuracy ' +
         'of OpenET satellite-based evapotranspiration data to support water resource and land ' +
         'management applications. Nature Water, 2(2), 193-205.',
   url: 'https://doi.org/10.1038/s44221-023-00181-7'},
  {tag: 'GW pumping',
   text: 'Ott, T. J., Majumdar, S., Huntington, J. L., et al. (2024). Toward field-scale ' +
         'groundwater pumping and improved groundwater management using remote sensing and ' +
         'climate data. Agricultural Water Management, 302, 109000.',
   url: 'https://doi.org/10.1016/j.agwat.2024.109000'},
  {tag: 'IrrMapper',
   text: 'Ketchum, D., Jencso, K., Maneta, M. P., Melton, F., Jones, M. O., & Huntington, J. ' +
         '(2020). IrrMapper: A Machine Learning Approach for High Resolution Mapping of Irrigated ' +
         'Agriculture Across the Western U.S. Remote Sensing, 12(14), 2328.',
   url: 'https://doi.org/10.3390/rs12142328'},
  {tag: 'IrrMapper',
   text: 'Ketchum, D., Hoylman, Z. H., Huntington, J., Brinkerhoff, D., & Jencso, K. G. (2023). ' +
         'Irrigation intensification impacts sustainability of streamflow in the Western United ' +
         'States. Communications Earth & Environment, 4(1), 479.',
   url: 'https://doi.org/10.1038/s43247-023-01152-2'},
  {tag: 'CDL',
   text: 'Boryan, C., Yang, Z., Mueller, R., & Craig, M. (2011). Monitoring US agriculture: the ' +
         'USDA, National Agricultural Statistics Service, Cropland Data Layer Program. Geocarto ' +
         'International, 26(5), 341-358.',
   url: 'https://doi.org/10.1080/10106049.2011.562309'},
  {tag: 'GEE',
   text: 'Gorelick, N., Hancher, M., Dixon, M., Ilyushchenko, S., Thau, D., & Moore, R. (2017). ' +
         'Google Earth Engine: Planetary-scale geospatial analysis for everyone. Remote Sensing ' +
         'of Environment, 202, 18-27.',
   url: 'https://doi.org/10.1016/j.rse.2017.06.031'},
  {tag: 'GEE community catalog',
   text: 'Roy, S., Majumdar, S., & Swetnam, T. (2025). samapriya/awesome-gee-community-datasets: ' +
         'Community Catalog (3.9.0). Zenodo.',
   url: 'https://doi.org/10.5281/zenodo.17641528'}
];

// Collapsible References section: a toggle header that shows/hides the list.
// Each entry is a horizontal row: a bold [tag] (a DOI hyperlink when one exists)
// followed by the (wrapping) citation text.
var refsExpanded = false;
var refsListPanel = ui.Panel({style: {margin: '2px 0 0 0', shown: false}});
REFERENCES.forEach(function (r) {
  // Linked tags are blue to read as hyperlinks; unlinked ones stay plain bold.
  var tagStyle = {fontSize: '10px', fontWeight: 'bold', margin: '0 4px 0 0',
                  color: r.url ? NV_BLUE : '#222'};
  var tagLabel = r.url
    ? ui.Label('[' + r.tag + ']', tagStyle, r.url)
    : ui.Label('[' + r.tag + ']', tagStyle);
  refsListPanel.add(ui.Panel({
    widgets: [
      tagLabel,
      ui.Label(r.text,
        {fontSize: '10px', color: '#444', margin: '0', stretch: 'horizontal'})
    ],
    layout: ui.Panel.Layout.flow('horizontal'),
    style: {margin: '0 0 5px 0', stretch: 'horizontal'}
  }));
});

var refsHeader = ui.Button({
  label: '▸ References',
  onClick: function () {
    refsExpanded = !refsExpanded;
    refsHeader.setLabel((refsExpanded ? '▾' : '▸') + ' References');
    refsListPanel.style().set('shown', refsExpanded);
  },
  style: {margin: '8px 0 2px 0', color: NV_BLUE, fontWeight: 'bold', stretch: 'horizontal'}
});

leftPanel.add(refsHeader);
leftPanel.add(refsListPanel);

// === UI: LAYOUT ===============================================================

ui.root.clear();
ui.root.add(ui.SplitPanel({
  firstPanel:  leftPanel,
  secondPanel: map,
  orientation: 'horizontal',
  wipe:        false,
  style:       {stretch: 'both'}
}));

// === LEGEND HELPERS ===========================================================

function gradientLegend(title, palette, minVal, maxVal, unit) {
  var panel = ui.Panel({style: {margin: '2px 0'}});
  panel.add(ui.Label(title, {fontWeight: 'bold', fontSize: '12px', margin: '0 0 2px 0'}));
  var bar = ui.Thumbnail({
    image: ee.Image.pixelLonLat().select(0),
    params: {
      bbox: [0, 0, 1, 0.1], dimensions: '200x12', format: 'png',
      min: 0, max: 1, palette: palette
    },
    style: {stretch: 'horizontal', height: '12px', margin: '0', padding: '0'}
  });
  panel.add(bar);
  var isMm      = unit === 'mm';
  var inMin     = isMm ? (minVal / MM_PER_INCH).toFixed(0) : null;
  var inMax     = isMm ? (maxVal / MM_PER_INCH).toFixed(0) : null;
  var leftText  = isMm ? (minVal + ' / ' + inMin)           : String(minVal);
  var rightText = isMm ? (maxVal + '+ / ' + inMax + '+')    : (String(maxVal) + '+');
  var midText   = isMm ? 'mm / in'                          : (unit || '');
  var labels = ui.Panel({
    widgets: [
      ui.Label(leftText,  {margin: '0', fontSize: '10px'}),
      ui.Label(midText,   {margin: '0 0 0 auto', fontSize: '10px', textAlign: 'center', stretch: 'horizontal'}),
      ui.Label(rightText, {margin: '0', fontSize: '10px'})
    ],
    layout: ui.Panel.Layout.flow('horizontal'),
    style:  {stretch: 'horizontal'}
  });
  panel.add(labels);
  return panel;
}

function swatchRow(color, label, dashed) {
  var box = ui.Label('', {
    backgroundColor: color, padding: '0', margin: '0 6px 0 0',
    border: dashed ? '1px dashed white' : ('1px solid ' + color),
    width: '18px', height: '12px'
  });
  return ui.Panel({
    widgets: [box, ui.Label(label, {fontSize: '11px', margin: '0'})],
    layout: ui.Panel.Layout.flow('horizontal'),
    style:  {margin: '1px 0'}
  });
}

function updateLegend() {
  legendPanel.clear();
  legendPanel.add(ui.Label('Legend', {fontWeight: 'bold', margin: '0 0 2px 0'}));
  legendPanel.add(ui.Label('Model: ' + MODEL_LABEL_BY_KEY[state.model],
    {fontSize: '11px', color: '#333', margin: '0 0 2px 0'}));
  if (state.layers['OpenET']) {
    legendPanel.add(gradientLegend('OpenET (' + state.agg + ')', PALETTE_ET, 0, ET_MAX_MM, 'mm'));
  }
  if (state.layers['WTD']) {
    legendPanel.add(ui.Label('Water table depth', {fontWeight: 'bold', fontSize: '12px', margin: '2px 0 2px 0'}));
    WTD_CLASSES.forEach(function (c) {
      legendPanel.add(swatchRow(c.color, c.label, false));
    });
  }
  if (state.layers['GW-ET Fields']) {
    legendPanel.add(gradientLegend('Fields: estimated GW-ET', PALETTE_GWET, 0, GWET_MAX_MM, 'mm'));
    legendPanel.add(swatchRow('#00bfff', 'Field boundary (shown when zoomed in)', false));
  }
  if (state.layers['IrrMapper']) {
    legendPanel.add(swatchRow('#00ffcc', 'IrrMapper: irrigated (class 0)', false));
  }
  if (state.layers['NWI Basins']) {
    legendPanel.add(swatchRow('#ffdd00', 'NWI basin boundary', false));
  }
}

// === MAP LAYER PAINTING =======================================================

function refreshHighlight() {
  var toRemove = [];
  map.layers().forEach(function (layer) {
    if (layer.getName() === HIGHLIGHT_LAYER) toRemove.push(layer);
  });
  toRemove.forEach(function (layer) { map.remove(layer); });

  if (!state.selected) return;
  var width = state.selected.type === 'basin' ? 3 : 2.5;
  var styled = ee.FeatureCollection([ee.Feature(state.selected.feature)])
    .style({color: HIGHLIGHT_COLOR, fillColor: '00000000', width: width});
  map.addLayer(styled, {}, HIGHLIGHT_LAYER);
}

// The 28k field-polygon outlines are expensive to rasterize as a vector layer,
// so only draw them once zoomed in enough to see individual fields. The GW-ET
// raster fill still marks field extents at the statewide view.
var FIELD_OUTLINE_MIN_ZOOM = 10;
function paintFieldOutlines() {
  var toRemove = [];
  map.layers().forEach(function (l) { if (l.getName() === 'Field outlines') toRemove.push(l); });
  toRemove.forEach(function (l) { map.remove(l); });
  if (!state.layers['GW-ET Fields'] || state.zoom < FIELD_OUTLINE_MIN_ZOOM) return;
  map.addLayer(fields.style({color: '00bfff', fillColor: '00000000', width: 1.5}),
    {}, 'Field outlines');
}

function paintLayers() {
  map.layers().reset();
  var c = gwComponentsForYear(state.year, state.agg, state.model, state.dExtMult);

  if (state.layers['OpenET']) {
    map.addLayer(c.et.clip(nvBounds),
      {min: 0, max: ET_MAX_MM, palette: PALETTE_ET, opacity: state.opacity},
      'OpenET');
  }
  if (state.layers['WTD']) {
    map.addLayer(wtdClassified.clip(nvBounds),
      {min: 0, max: WTD_CLASSES.length - 1, palette: PALETTE_WTD_CLASS, opacity: state.opacity},
      'Water table depth');
  }
  if (state.layers['IrrMapper']) {
    map.addLayer(c.irr.selfMask().clip(nvBounds),
      {palette: ['#00ffcc'], opacity: state.opacity},
      'IrrMapper irrigated');
  }
  if (state.layers['GW-ET Fields']) {
    var fillOpacity = state.layers['OpenET'] ? 0.6 : 0.85;
    var gwEtInFields = c.gwEtIrr.updateMask(fieldsMask);
    map.addLayer(gwEtInFields.clip(nvBounds),
      {min: 0, max: GWET_MAX_MM, palette: PALETTE_GWET, opacity: fillOpacity},
      'GW-ET Fields');
  }
  if (state.layers['NWI Basins']) {
    var basinStyled = gwBasins.style({color: 'ffdd00', fillColor: '00000000', width: 2});
    map.addLayer(basinStyled, {}, 'NWI Basins');
  }
  paintFieldOutlines(); // zoom-gated; cheap no-op when zoomed out

  refreshHighlight();
}

// When zoomed in past the threshold, draw field outlines (and drop them when
// zoomed back out) without a full raster refresh.
map.onChangeZoom(function (zoom) { state.zoom = zoom; paintFieldOutlines(); });

// === CHARTS ===================================================================

function renderCharts() {
  chartsPanel.clear();
  chartsPanel.add(ui.Label('Charts', {fontWeight: 'bold', margin: '4px 0 2px 0'}));
  chartsPanel.add(ui.Label(
    'Click a field on the map for per-field stats (30 m). ' +
    'Click outside any field to query the underlying NWI basin (500 m).',
    {color: '#666', fontSize: '11px', margin: '0 0 4px 0'}
  ));
}

// === CLICK HANDLERS (field at 30 m, basin at 500 m) ===========================

var STATIC_CHART_WIDGETS = 2;

function clearClickBlock() {
  while (chartsPanel.widgets().length() > STATIC_CHART_WIDGETS) {
    chartsPanel.widgets().remove(chartsPanel.widgets().get(STATIC_CHART_WIDGETS));
  }
  state.selected = null;
  refreshHighlight();
}

function fmtNum(x, dec) {
  return (x === null || x === undefined) ? '-' : Number(x).toFixed(dec);
}

function renderFieldCharts(fieldFeature) {
  clearClickBlock();
  state.selected = {feature: fieldFeature, type: 'field'};
  refreshHighlight();

  var loading = ui.Label('Querying clicked field at 30 m...', {fontSize: '11px', color: '#666'});
  chartsPanel.add(loading);

  var geom = ee.Feature(fieldFeature).geometry();
  var c = gwComponentsForYear(state.year, state.agg, state.model, state.dExtMult);

  var meansImg = c.et.rename('et_mm')
    .addBands(c.gwEt.rename('gwEt_mm'))
    .addBands(wtdNV.rename('wtd_m'))
    .addBands(c.gwFrac.rename('gwFrac'))
    .updateMask(c.irr);
  var means = meansImg.reduceRegion({
    reducer: ee.Reducer.mean(), geometry: geom, scale: 30, tileScale: 4
  });
  var irrAreaDict = c.irr.multiply(ee.Image.pixelArea()).rename('irr_area_m2').reduceRegion({
    reducer: ee.Reducer.sum(), geometry: geom, scale: 30, tileScale: 4
  });
  // Carry the field's basin name through so the summary can show it.
  var combined = ee.Dictionary(means).combine(ee.Dictionary(irrAreaDict))
    .set('basin', ee.Feature(fieldFeature).get('Basin'));

  var summaryLabel = ui.Label('Loading...', {fontSize: '11px', color: '#333'});
  combined.evaluate(function (d, err) {
    if (err)  { summaryLabel.setValue('Query failed: ' + err); return; }
    if (!d)   { summaryLabel.setValue('No stats for clicked field.'); return; }
    var irrAcres = (d.irr_area_m2 == null) ? null : d.irr_area_m2 / M2_PER_ACRE;
    var etAcft   = (d.et_mm == null || d.irr_area_m2 == null)
      ? null : d.et_mm / 1000 * d.irr_area_m2 / M3_PER_ACFT;
    var gwEtAcft = (d.gwEt_mm == null || d.irr_area_m2 == null)
      ? null : d.gwEt_mm / 1000 * d.irr_area_m2 / M3_PER_ACFT;
    // Clean the join-key form "014_SurpriseValley" -> "Surprise Valley".
    var basin = d.basin ? String(d.basin).replace(/^\d+_/, '').replace(/_/g, ' ')
                                          .replace(/([a-z])([A-Z])/g, '$1 $2') : null;
    summaryLabel.setValue(
      'Basin: '      + (basin || '-') +
      '  |  Irr acres: '  + fmtNum(irrAcres, 1) +
      '  |  WTD: '   + fmtNum(d.wtd_m, 1) + ' m' +
      '  |  ET: '    + fmtNum(etAcft, 1) + ' acre-ft' +
      '  |  GW-ET: ' + fmtNum(gwEtAcft, 1) + ' acre-ft' +
      '  (f(d) '     + fmtNum(d.gwFrac, 2) + ')'
    );
  });

  var monthlyFc = monthlySeriesForGeom(state.year, c.gwFrac, geom, 30);
  var monthlyChart = ui.Chart.feature.byFeature(monthlyFc, 'month', ['ET', 'GW-ET', 'ET_mm_shadow'])
    .setChartType('LineChart')
    .setOptions({
      title: 'Clicked field - monthly ET & GW-ET, ' + state.year + ' (' + MODEL_LABEL_BY_KEY[state.model] + ')',
      hAxis: {title: 'Month', viewWindow: {min: 1, max: 12}},
      vAxes: {0: {title: 'inches'}, 1: {title: 'mm'}},
      series: {
        0: {color: '#2171b5', lineWidth: 2, pointSize: 4, targetAxisIndex: 0},
        1: {color: '#08306b', lineWidth: 2, pointSize: 4, targetAxisIndex: 0},
        2: {color: 'transparent', lineWidth: 0, pointSize: 0,
            visibleInLegend: false, targetAxisIndex: 1}
      },
      legend: {position: 'bottom'}, height: 180
    });

  chartsPanel.widgets().remove(loading);
  chartsPanel.add(ui.Panel({
    widgets: [
      ui.Label('-- Clicked field (30 m) --', {fontWeight: 'bold', margin: '8px 0 2px 0'}),
      summaryLabel,
      monthlyChart
    ],
    style: {margin: '0'}
  }));
}

function renderBasinCharts(basinFeature) {
  clearClickBlock();
  state.selected = {feature: basinFeature, type: 'basin'};
  refreshHighlight();

  var loading = ui.Label('Querying NWI basin at 500 m...', {fontSize: '11px', color: '#666'});
  chartsPanel.add(loading);

  var basinF = ee.Feature(basinFeature);
  var geom   = basinF.geometry();
  var nameSrv = resolveBasinName(basinF);
  var c = gwComponentsForYear(state.year, state.agg, state.model, state.dExtMult);

  var summaryF = yearSummaryForGeom(state.year, state.agg, state.model, state.dExtMult, geom, REDUCE_SCALE);
  var summaryLabel = ui.Label('Loading...', {fontSize: '11px', color: '#333'});
  ee.Dictionary({
    name:      nameSrv,
    irr_acres: summaryF.get('irr_acres'),
    et_acft:   summaryF.get('et_acft'),
    gwEt_acft: summaryF.get('gwEt_acft'),
    wtd_m:     summaryF.get('wtd_m'),
    gwFrac:    summaryF.get('gwFrac')
  }).evaluate(function (d, err) {
    if (err) { summaryLabel.setValue('Query failed: ' + err); return; }
    if (!d)  { summaryLabel.setValue('No stats for clicked basin.'); return; }
    summaryLabel.setValue(
      'Basin: '       + (d.name || '-') +
      '  |  Irr ac: ' + fmtNum(d.irr_acres, 0) +
      '  |  WTD: '    + fmtNum(d.wtd_m, 1) + ' m' +
      '  |  f(d): '   + fmtNum(d.gwFrac, 2) +
      '  |  ET: '     + fmtNum(d.et_acft, 0) + ' acre-ft' +
      '  |  GW-ET: '  + fmtNum(d.gwEt_acft, 0) + ' acre-ft'
    );
  });

  var monthlyFc = monthlySeriesForGeom(state.year, c.gwFrac, geom, REDUCE_SCALE);

  var mmChart = ui.Chart.feature.byFeature(monthlyFc, 'month', ['ET', 'GW-ET', 'ET_mm_shadow'])
    .setChartType('LineChart')
    .setOptions({
      title: 'Basin - monthly ET & GW-ET, ' + state.year + ' (' + MODEL_LABEL_BY_KEY[state.model] + ')',
      hAxis: {title: 'Month', viewWindow: {min: 1, max: 12}},
      vAxes: {0: {title: 'inches'}, 1: {title: 'mm'}},
      series: {
        0: {color: '#2171b5', lineWidth: 2, pointSize: 4, targetAxisIndex: 0},
        1: {color: '#08306b', lineWidth: 2, pointSize: 4, targetAxisIndex: 0},
        2: {color: 'transparent', lineWidth: 0, pointSize: 0,
            visibleInLegend: false, targetAxisIndex: 1}
      },
      legend: {position: 'bottom'}, height: 180
    });

  var barChart = ui.Chart.feature.byFeature(monthlyFc, 'month', ['GW-ET_acft', 'nonGW_acft'])
    .setChartType('ColumnChart')
    .setOptions({
      title: 'Basin - monthly ET volume, ' + state.year + ' (acre-ft, GW vs non-GW)',
      hAxis: {title: 'Month', viewWindow: {min: 1, max: 12}},
      vAxis: {title: 'acre-ft'},
      isStacked: true,
      series: {0: {color: '#08306b'}, 1: {color: '#c6dbef'}},
      legend: {position: 'bottom'}, height: 180
    });

  chartsPanel.widgets().remove(loading);
  chartsPanel.add(ui.Panel({
    widgets: [
      ui.Label('-- Clicked basin (500 m) --', {fontWeight: 'bold', margin: '8px 0 2px 0'}),
      summaryLabel,
      mmChart,
      barChart
    ],
    style: {margin: '0'}
  }));
}

function handleMapClick(coords) {
  var pt = ee.Geometry.Point([coords.lon, coords.lat]);
  statusLabel.setValue('Querying...');
  var clickedField = fields.filterBounds(pt).first();
  clickedField.evaluate(function (f) {
    if (f) {
      statusLabel.setValue('');
      renderFieldCharts(clickedField);
      return;
    }
    var clickedBasin = gwBasins.filterBounds(pt).first();
    clickedBasin.evaluate(function (b) {
      statusLabel.setValue('');
      if (b) renderBasinCharts(clickedBasin);
      else   clearClickBlock();
    });
  });
}

map.onClick(handleMapClick);

// === REFRESH ==================================================================

function refresh() {
  statusLabel.setValue('Computing...');
  paintLayers();
  renderCharts();
  updateLegend();
  statusLabel.setValue('');
}

// === INITIAL RENDER ===========================================================

refresh();
