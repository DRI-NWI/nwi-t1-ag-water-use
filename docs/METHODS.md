# Methods

All settings live in `config/nwi_t1_final.yaml`. Steps run in order from `scripts/`
(`run_all.sh`). Every step writes into one run directory, `<output_root>/<run_id>/`.

## Population

The population is the 2026-08-20 NWI boundary layer: 29,338 fields (24,882 NV; 2,361
CA; 1,509 UT; 275 ID; 274 OR; 37 AZ), 1985–2025, monthly. No irrigation-activity filter
is applied.

The 4,451 non-Nevada fields have no `IRR_SRC`. They are treated as groundwater and
flagged `IRR_SRC_ASSUMED`. `irr_eff` and `ITYPE` come from the OpenET export, which is
complete. The hydrographic area comes from the boundary `Basin` attribute.

## 01 Inputs

- **ETa.** Comes from the monthly OpenET ENSEMBLE export.
  - −999 → NaN; `ETA_MM == 0` → NaN.
  - EToF = ETa / ETo. One-month gaps are filled by linear interpolation.
  - Longer gaps stay missing and are flagged `unfilled_missing` (177,673 field-months).
  - No EToF climatology exists for this population.
  - ETa volume = EToF_interpolated × ETO_MM × acres.
- **PRISM precipitation.** Daily PRISM 800 m precipitation at each field centroid is summed
  to field-months. It was extracted on Earth Engine project `nasa-water` (config
  `ee_extract_nv_20260820.toml`). The same files carry OpenET gridMET daily ETo, which
  matches the export's monthly ETo (r = 0.99995).

## 02 OTTER effective precipitation

OTTER runs as a continuous daily root-zone water balance per field from 1985-01-01, with
settings NM05: `init_dru_frac = init_drl_frac = mad_frac = 0.5`.

- **Inputs:**
  - daily PRISM precipitation;
  - daily ETo;
  - daily ET = ETo × monthly EToF, where EToF is the step-01 gap-filled value forward-filled
    over the full history;
  - static soils per field-year (`aws_u`, `aws_max`, curve number).
- **Peff** on a day = precipitation − runoff − deep percolation. It is summed to months.
- **Fallback.** 242 fields have missing or invalid static soils. They use 0.85 × PRISM
  precipitation and are flagged `peff_method = ppt_085_fallback`.

## 03 SPI

- **Method.** `climate_indices` SPI, gamma distribution, monthly. One calibration window,
  1981–2024, applies to every year and every series.
- **Inputs.** PRISM monthly precipitation over each HA and over each HA's surface-water
  contributing area, 1981–2025.
- **Source per basin (decision D24/D29).** Use the contributing-area series where one is
  defined, otherwise the HA-wide series.
- **Allocation input.** Sept SPI-12 of year Y sets the share for calendar year Y.
- **Check.** The table equals the mixed-field review's PRISM SPI exactly.
- **Effect.** SPI now covers 2024–2025, so the 0.5 fallback used for 2025 in the delivered
  package is no longer needed.

## 04 Groundwater-ET subsidy

- **Groundwater fraction f.** f(d) = Tan et al. (2025) groundwater-ET fraction, using:
  - depth to water d from `nv_dtw_parity_statewide_100m` (EPSG:5070, 100 m);
  - POLARIS 0–100 cm texture, sampled fresh for the 2026-08-20 fields;
  - rooting depth from `CROP_major` (200 cm for alfalfa and phreatophytes, else 100 cm);
  - the field's April–October mean ETo.
- **Per-pixel averaging.** f is averaged over the field's DTW pixels, weighted by
  coverage, so a steep f is not biased by averaging depths first.
- **SW filter.** Pure-GW fields in basins where SW or mixed fields hold less than 10% of
  the acreage get f = 0 (`nwi-gw-subsidy` rule).

## 05 Water balance, carry-forward, allocation

Per field-month, on the whole field:

```
NetET_pre_subsidy = ETa − Peff_OTTER
GW_subsidy        = min(f × ETa, max(NetET_pre_subsidy, 0))
NetET_intermediate = NetET_pre_subsidy − GW_subsidy
```

The subsidy only offsets a positive requirement, so groundwater is never stored as soil
water.

**Winter carry-forward** follows the delivered algorithm:

- A negative NetET_intermediate is stored as soil water.
- Storage is capped at AWC × 1.5 m × acres; the excess is reported as overflow.
- Storage is drawn down by later positive months.
- The chain resets every January and breaks at a missing month.

Then:

- NetET_final ≥ 0.
- AR_final = NetET_final / irr_eff.

**Allocation** (Dynamic SPI 50/50):

- A mixed field's SW share = 0.5 + 0.15 × Sept SPI-12, clipped to [0.2, 0.8]. Equivalently,
  GW share = clip(0.5 − 0.15 × SPI, 0.2, 0.8).
- Basin rules:
  - Diamond Valley (153): mixed fields are reclassified as GW.
  - Fish Lake Valley (117): center 0.36, range 0.2–0.7.
- Pure GW/SW fields get 0/1.

**Outputs:**

- ALL is the whole field.
- GW and SW scale every volume and the acreage by the component share, so GW + SW = ALL
  exactly; this is checked on every run.

## 06 Summaries

HA and county summaries follow `docs/COLUMN_POLICY.md`.

## 07 Reproduction gate and bridge

**Reproduction gate.** Variant `v0a` reruns the delivered settings:

- export precipitation;
- 0.85 × PPT effective precipitation;
- gridMET SPI file ending 2023;
- no basin overrides;
- no subsidy.

It reproduces every HA × year × month cell of the 2026-08-23 HA summaries (ETa, NetET, AR,
acres) to within 1e-11 acre-ft.

**Bridge.** Variants v0b → v4 then add one change at a time:

1. Diamond Valley static override.
2. Fish Lake dynamic rule.
3. PRISM SPI.
4. PRISM precipitation + OTTER Peff.
5. GW subsidy.

`bridge_table.csv` gives the statewide annual-mean effect of each change.
