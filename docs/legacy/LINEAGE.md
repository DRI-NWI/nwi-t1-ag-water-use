# Lineage

How this workflow relates to the earlier NWI processing: what was rerun to check the code,
how the totals change as each new setting is added, and what differs. The rest of the
documentation describes only the current workflow. Where the code came from is in
[VENDORED_SOURCES.md](VENDORED_SOURCES.md).

## Rerun of the 2026-08-23 settings

A test run of this code with the settings of the field and HA files delivered on 2026-08-23
(named `v0a_reproduce_0823_no_static` in `config/bridge_variants.yaml`) uses:

- the precipitation in the OpenET field export;
- effective precipitation = 0.85 × precipitation;
- SPI from gridMET precipitation averaged over each HA's agricultural fields, ending in 2023;
- no basin rules;
- no GW subsidy;
- no filling of gaps longer than one month;
- all field-months in the summaries, irrigated or not.

It matches every HA, year and month of the delivered GW and SW HA summaries (ETa, net ET,
applied water, acres) to within 1e-11 acre-ft. Row counts are identical (GW 8,203,116; SW
9,237,300). `scripts/qa/bridge_qa.py` runs this check and writes
`07_qa/reproduction_check.csv`.

## Totals as each setting is added

Each row is a test run that adds one change to the row above it; the last row is the final
run. Values are acre-ft/yr, 1985–2025 averages, counting a field in a year only if IrrMapper
marks more than half of it as irrigated (`%_IRRIGATED` > 0.5). Peff is effective
precipitation, AR is applied water.

| Test run | Change added | ETa | Peff | GW subsidy | Net ET | AR | GW net ET | SW net ET |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| v0a | 2026-08-23 settings | 1,420,968 | 344,479 | 0 | 1,117,471 | 1,647,285 | 639,920 | 477,552 |
| v1 | Basin rules for Diamond Valley (153) and Fish Lake Valley (117) | 1,420,968 | 344,479 | 0 | 1,117,471 | 1,647,285 | 641,670 | 475,802 |
| v2 | SPI from PRISM precipitation, through 2025 | 1,420,968 | 344,479 | 0 | 1,117,471 | 1,647,285 | 641,834 | 475,638 |
| v3 | PRISM precipitation and OTTER effective precipitation | 1,420,968 | 363,361 | 0 | 1,101,443 | 1,627,466 | 631,580 | 469,862 |
| v4a | GW-ET subsidy | 1,420,968 | 363,361 | 34,692 | 1,066,907 | 1,569,188 | 618,349 | 448,559 |
| v4_final | Filling gaps longer than one month from the field's calendar-month average EToF | 1,423,458 | 363,361 | 34,733 | 1,065,648 | 1,567,158 | 617,737 | 447,911 |

Counting every field and month instead (as the 2026-08-23 summaries did), net ET is
1,945,523 for v0a and 1,788,345 for v4_final. `07_qa/bridge_table.csv` has both ways of
counting.

## Differences from the earlier processing

| Topic | Earlier processing | Current workflow |
|---|---|---|
| Precipitation | The precipitation in the OpenET field export (gridMET) | PRISM 800 m daily precipitation at each field |
| Effective precipitation | 0.85 × precipitation | OTTER daily soil-water balance for each field |
| SPI | gridMET precipitation averaged over each HA's fields, weighted by field acres; the file ended in 2023, so 2025 used a 0.5 SW share | PRISM precipitation over the area that drains surface water to the HA, or over the HA; fitted to 1981–2024; through 2025 (as decided in the 2026-09-25 mixed-field allocation review) |
| Basin rules | The code read the rules without the run's settings, so the Fish Lake Valley (117) rule never applied, and the Diamond Valley (153) rule was not applied | Both rules read from `config/nwi_t1_final.yaml` |
| GW-ET subsidy | Computed separately in the `nwi-gw-subsidy` repository with the Ma et al. (2026) depth-to-groundwater map and reported as a share of net ET; never in the delivered field or HA files. A 2026-10-12 test with the new map subtracted it from the final net ET (after the winter carry-forward), using the 2026-04-15 field summaries | Computed in this workflow with the new statewide 100 m depth-to-groundwater map (`nv_dtw_parity_statewide_100m`) and subtracted from each month's net ET, like effective precipitation, before stored soil water is used |
| ETa gaps | Single missing months filled by interpolation only; longer gaps stayed missing, because no calendar-month averages existed for the 2026-08-20 fields | Single months interpolated; longer gaps filled from the field's calendar-month average EToF |
| Rows counted in the HA and county summaries | Every field and month | A field in a year only if more than half of it is irrigated |
| Summary columns | Every numeric column was summed and every text column took the first field's value | Volumes and acres summed, field counts, depths recalculated from the summed volumes, ETo and precipitation averaged by field acres, and each HA's yearly mixed-field split |

Columns in the HA files of 2026-08-21 and 2026-08-23 and the county files of 2026-09-02 that
had no meaning once fields were added together:

| Column | What it held |
|---|---|
| `NWI_ID`, `OPENET_ID`, `GRIDMET_ID`, `DATE`, `Basin` | the first field's ID or label |
| `irr_eff`, `ITYPE`, `IRR_SRC`, `AWC`, `PAW_ft`, `Acres` | per-field values added together (e.g. `irr_eff` = 39.25) |
| `EToF`, `EToF_interpolated`, `%_IRRIGATED` | per-field ratios added together |
| `spi_value`, `allocation_ratio`, `spi_timescale` | per-field values added together (e.g. `spi_value` = −15.3) |
| `ETA_MM`, `ETNET_MM`, `AR_MM` | per-field depths added together |
| `interpolation_method`, `IRR_ATTR_ASSUMPTION`, `water_source` | the first field's label |
| `ETO_MM`, `PPT_MM`, `EFF_PPT_MM` | a plain average, not weighted by field acres |

Field-file columns of the earlier files that are no longer written:

| Column | Why |
|---|---|
| `ETNET_MM`, `AR_MM` | the OpenET export's own net ET and applied water, based on 0.85 × precipitation; replaced by `NetET_final` and `AR_final` |
| `Eff_PPT_Adjusted_acre_ft` | replaced by the separate columns `Eff_PPT_vol`, `GW_subsidy` and `Carryforward_Used` |
| `IRR_ATTR_ASSUMPTION` | not written; whether the water-right source was assumed is in `IRR_SRC_ASSUMED`, which is kept |
