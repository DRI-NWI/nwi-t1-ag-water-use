# Lineage

This page records how the current workflow relates to earlier NWI processing: what was
reproduced, what differs, and why. The main documentation describes only the current
workflow. Code origins are in [VENDORED_SOURCES.md](VENDORED_SOURCES.md).

## Reproduction of the 2026-08-23 package

Bridge variant `v0a_reproduce_0823_no_static` (`config/bridge_variants.yaml`) runs this
code with the settings of the 2026-08-23 field and HA package:

- OpenET export precipitation;
- effective precipitation 0.85 × PPT;
- gridMET ag-footprint SPI ending in 2023;
- no basin rules;
- no GW subsidy;
- no EToF climatology fill;
- no irrigation filter in the summaries.

It matches every HA × year × month cell of the delivered GW and SW HA summaries (ETa,
NetET, AR, acres) to within 1e-11 acre-ft. Row counts are identical (GW 8,203,116; SW
9,237,300). `scripts/qa/bridge_qa.py` checks this and writes `07_qa/reproduction_check.csv`.

## Bridge to the current run

The variants add one setting at a time. Values are acre-ft/yr, 1985–2025 means, for
irrigated field-years (`%_IRRIGATED` > 0.5).

| Variant | Setting added | ETa | Peff | GW subsidy | NetET | AR | GW NetET | SW NetET |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| v0a | 2026-08-23 settings | 1,420,968 | 344,479 | 0 | 1,117,471 | 1,647,285 | 639,920 | 477,552 |
| v1 | Basin rules 153 and 117 | 1,420,968 | 344,479 | 0 | 1,117,471 | 1,647,285 | 641,670 | 475,802 |
| v2 | PRISM SPI (D24), through 2025 | 1,420,968 | 344,479 | 0 | 1,117,471 | 1,647,285 | 641,834 | 475,638 |
| v3 | PRISM precipitation, OTTER Peff | 1,420,968 | 363,361 | 0 | 1,101,443 | 1,627,466 | 631,580 | 469,862 |
| v4a | GW-ET subsidy | 1,420,968 | 363,361 | 34,692 | 1,066,907 | 1,569,188 | 618,349 | 448,559 |
| v4_final | EToF climatology fill | 1,423,458 | 363,361 | 34,733 | 1,065,648 | 1,567,158 | 617,737 | 447,911 |

On all field-months (the population of the 2026-08-23 summaries), NetET is 1,945,523 for
v0a and 1,788,345 for v4_final. `07_qa/bridge_table.csv` holds both populations.

## Differences from earlier processing

| Topic | Earlier processing | Current workflow |
|---|---|---|
| Precipitation | OpenET export PPT (gridMET) | PRISM 800 m daily per field |
| Effective precipitation | 0.85 × PPT | OTTER per field (NM05) |
| SPI | gridMET ag-footprint, file ending 2023 (2025 used the 0.5 fallback) | PRISM contributing area or HA (decision D24/D29), 1981–2024 calibration, through 2025 |
| Basin rules | Loaded through `load_config()` without the run config, so the 117 rule never applied; the 153 static override was not applied | Both rules read from `config/nwi_t1_final.yaml` |
| GW-ET subsidy | Separate repo (`nwi-gw-subsidy`), Ma et al. DTW, not subtracted from NetET | Parity 100 m DTW, subtracted from NetET before carry-forward |
| EToF gaps | One-month linear fill only (no climatology for the 2026-08-20 population) | Linear fill, then per-field climatologies |
| Summary population | All field-months | Irrigated field-years |
| Summary columns | Every numeric column summed and every text column `first()`-picked | Additive terms, counts, recomputed depths, acre-weighted means, basin-year constants |

Columns in the 2026-08-21/23 HA files and the 2026-09-02 county files that had no meaning
at the aggregate level:

| Column | What it held |
|---|---|
| `NWI_ID`, `OPENET_ID`, `GRIDMET_ID`, `DATE`, `Basin` | the first field's ID or label |
| `irr_eff`, `ITYPE`, `IRR_SRC`, `AWC`, `PAW_ft`, `Acres` | a sum of per-field attributes (e.g. `irr_eff` = 39.25) |
| `EToF`, `EToF_interpolated`, `%_IRRIGATED` | a sum of per-field ratios |
| `spi_value`, `allocation_ratio`, `spi_timescale` | a sum over fields (e.g. `spi_value` = −15.3) |
| `ETA_MM`, `ETNET_MM`, `AR_MM` | a sum of per-field depths |
| `interpolation_method`, `IRR_ATTR_ASSUMPTION`, `water_source` | the first field's label |
| `ETO_MM`, `PPT_MM`, `EFF_PPT_MM` | an unweighted mean |

Field-file columns from earlier packages that are not produced:

| Column | Status |
|---|---|
| `ETNET_MM`, `AR_MM` | export 0.85 × PPT products; superseded by `NetET_final` and `AR_final` |
| `Eff_PPT_Adjusted_acre_ft` | superseded by `Eff_PPT_vol`, `GW_subsidy` and `Carryforward_Used` |
| `IRR_ATTR_ASSUMPTION` | replaced by the `IRR_SRC_ASSUMED` flag |
