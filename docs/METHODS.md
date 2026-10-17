# Methods

All settings are in `config/nwi_t1_final.yaml`. The steps in `scripts/` run in order
(`run_all.sh`) and write into one run directory, `<output_root>/<run_id>/`.

## Fields

- **Population.** 29,338 fields of the 2026-08-20 NWI boundary layer (24,882 NV; 2,361 CA;
  1,509 UT; 275 ID; 274 OR; 37 AZ), monthly, 1985–2025.
- **Hydrographic area.** Each field belongs to the HA in its boundary `Basin` attribute. HAs
  that cross the state line include their out-of-state fields.
- **Water-right source** (`IRR_SRC`): GW, SW or mixed from the NDWR place-of-use attribution.
  The 4,451 fields outside Nevada have no attribution and are treated as GW
  (`IRR_SRC_ASSUMED`).
- **Irrigation efficiency and type** (`irr_eff`, `ITYPE`): from the OpenET field export.
- **Basin rules.**
  - Diamond Valley (153): mixed fields are allocated as GW.
  - Fish Lake Valley (117): mixed fields use center 0.36, range 0.2–0.7 (Rush & Katzer).

## 01 ET and precipitation

**ETa** comes from the monthly OpenET ensemble export:

- −999 → missing; `ETA_MM == 0` → missing.
- EToF = ETa / ETo.
- One-month gaps are filled by linear interpolation.
- Longer gaps are filled from the field's EToF climatology for that calendar month. There
  are seven climatology windows covering 1985–2025: 1985–91, 1992–97, 1998–2003, 2004–09,
  2010–15, 2016–21 and 2022–25. Each climatology is the field's mean observed EToF, clipped
  to 0–1.5 (`01b_etof_climatology.py`).
- 1,194 field-months have no observed EToF in their window and stay missing.
- ETa volume = EToF × ETo × acres.

**Precipitation** is PRISM 800 m daily precipitation at each field centroid, summed by
month. It was extracted on Earth Engine project `nasa-water`. The same extraction holds
OpenET gridMET daily ETo.

## 02 Effective precipitation (OTTER)

OTTER runs a continuous daily root-zone water balance for each field from 1985-01-01.

- **Parameters (NM05).** `init_dru_frac = init_drl_frac = mad_frac = 0.5`.
- **Inputs:**
  - daily PRISM precipitation and daily ETo;
  - daily ET = ETo × the field's monthly gap-filled EToF (the same EToF as ETa);
  - static soils per field-year: `aws_u`, `aws_max`, curve number.
- **Output.** Effective precipitation on a day = precipitation − runoff − deep percolation,
  summed by month.
- **Fallback.** 242 fields have no valid static soils. They use 0.85 × PRISM precipitation
  (`peff_method = ppt_085_fallback`).

## 03 SPI

- **Index.** `climate_indices` SPI, gamma distribution, monthly. One calibration window,
  1981–2024, for every series and every year.
- **Precipitation series.** PRISM monthly precipitation over each HA's surface-water
  contributing area where one is defined, otherwise over the HA.
- **Use.** The September SPI-12 of year Y sets the mixed-field split for calendar year Y.

## 04 Groundwater-ET fraction

f is the share of ETa supplied by groundwater, from Tan et al. (2025):

```
f(d) = 1                                   d ≤ d_dec
     = ((d_ext − d) / (d_ext − d_dec))^p   d_dec < d < d_ext
     = 0                                   d ≥ d_ext
```

- **d (depth to water).** From `nv_dtw_parity_statewide_100m` (EPSG:5070, 100 m), which
  covers every field.
- **d_dec, d_ext, p.** From the field's:
  - POLARIS 0–100 cm texture class;
  - rooting depth: 200 cm for alfalfa and phreatophytes (`CROP_major`), otherwise 100 cm;
  - April–October mean ETo.
- **Per-pixel average.** f is averaged over the field's DTW pixels, weighted by coverage.
- **Eligibility.** Every field is eligible through its water right.

## 05 Field water balance

For each field-month:

```
NetET_pre_subsidy  = ETa − Peff
GW_subsidy         = min(f × ETa, max(NetET_pre_subsidy, 0))
NetET_intermediate = NetET_pre_subsidy − GW_subsidy
```

Like Peff, the GW subsidy is subtracted in every field-month. It only offsets a positive
requirement, so groundwater never enters soil storage.

**Winter carry-forward.**

- A negative `NetET_intermediate` is stored as soil water. Storage is capped at
  AWC × 1.5 m × acres, and anything above the cap is overflow.
- Later positive months draw the storage down.
- Storage resets every January and at a missing month.

```
NetET_final = max(NetET_intermediate − storage used, 0)
AR_final    = NetET_final / irr_eff
```

**SW/GW allocation (dynamic SPI 50/50).**

- A mixed field's SW share in year Y = clip(0.5 + 0.15 × Sept SPI-12, 0.2, 0.8). Its GW
  share is 1 minus that.
- Pure GW fields are 100% GW, and pure SW fields are 100% SW.
- The GW and SW files scale every volume and the acreage by the share. GW + SW = ALL is
  checked on every run.

## 06 HA and county summaries

The summaries count irrigated field-years only: IrrMapper `%_IRRIGATED` > 0.5. Every
volume of an included field-year counts, including its GW subsidy and carry-forward.
County summaries cover Nevada fields. Column rules are in
[DATA_DICTIONARY.md](DATA_DICTIONARY.md).
