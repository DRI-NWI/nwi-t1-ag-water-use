# nwi-t1-ag-water-use

Field-level agricultural water use for the Nevada Water Initiative (NWI T1): monthly
OpenET consumptive use, effective precipitation, groundwater-ET subsidy, winter soil-water
carry-forward, applied water, and the SW/GW split, 1985–2025. Results are produced for
every field, every hydrographic area (HA), and every Nevada county.

This repository holds every processing step from the extracted inputs to the final
tables. One configuration file, `config/nwi_t1_final.yaml`, drives the whole run.

## Final settings

| Component | Setting |
|---|---|
| Fields | 2026-08-20 NWI boundary layer, 29,338 fields (24,882 NV). Field files keep every field-month; HA/county summaries sum only irrigated field-years (IrrMapper `%_IRRIGATED` > 0.5) |
| ET | OpenET ENSEMBLE monthly ETa; one-month EToF gaps filled linearly, longer gaps from per-field EToF climatologies (seven windows spanning 1985–2025); 1,194 field-months stay missing |
| Precipitation | PRISM 800 m daily at each field, summed by month |
| Effective precipitation | OTTER per field (NM05: `init_dru_frac = init_drl_frac = mad_frac = 0.5`), continuous from 1985, driven by the same PRISM precipitation. This replaces 0.85 × PPT |
| GW-ET subsidy | Tan et al. (2025) f(DTW) × ETa, with DTW from the parity 100 m raster (valid outside NV too). Every field is eligible through its water right; non-NV fields are assumed GW, with no basin test. Subtracted from NetET in every field-month, like Peff, only against positive NetET, before carry-forward |
| Winter carry-forward | Negative NetET is stored, capped at AWC × 1.5 m × acres, and reset each January |
| Applied water | NetET / field irrigation efficiency |
| SW/GW allocation | Dynamic SPI 50/50: mixed-field GW share = clip(0.5 − 0.15 × Sept SPI-12, 0.2, 0.8) |
| SPI | PRISM, `climate_indices` gamma fit, one calibration window (1981–2024) for every year. Contributing-area precipitation where defined, else HA-wide (decision D24/D29). Covers 2024–2025 |
| Basin rules | Diamond Valley (153): mixed → GW. Fish Lake Valley (117): center 0.36, range 0.2–0.7 |

Details are in [docs/METHODS.md](docs/METHODS.md), output columns in
[docs/COLUMN_POLICY.md](docs/COLUMN_POLICY.md), and code origins in
[docs/VENDORED_SOURCES.md](docs/VENDORED_SOURCES.md).

## Running

```bash
conda env create -f environment.yml
cd scripts
PY=python PY_EXACT=python ./run_all.sh
```

| Step | Script | Output (under `<output_root>/<run_id>/`) |
|---|---|---|
| 01 | `01_build_inputs.py` | `01_inputs/`: field attributes, PRISM monthly, field-month EToF/ETa base |
| 01b | `01b_etof_climatology.py` | `01_inputs/etof_climatology/`: per-window field EToF climatologies (`etof.climatology_fill`) |
| 02 | `02_run_otter.py` | `02_otter/`: monthly OTTER Peff per field |
| 03 | `03_compute_spi.py` | `03_spi/`: PRISM SPI by region, basin-year Sept SPI-12 |
| 04a–c | `04a_dtw_zonal.py`, `04b_texture_polaris.py`, `04c_gw_fraction.py` | `04_gw_subsidy/`: DTW per field, POLARIS texture, groundwater fraction f |
| 05 | `05_process_fields.py` | `05_field_summaries/field_summaries_{ALL,GW,SW}.{csv,parquet}`; `06_summaries/{ha,county}_summaries_{ALL,GW,SW}.csv` |
| 07 | `07_bridge_qa.py` | `07_qa/`: reproduction check, bridge table |

`05_process_fields.py --variant <name>` runs one of the bridge variants in
`config/bridge_variants.yaml`.

## Outputs

- **ALL** is the whole field.
- **GW** and **SW** are the components: pure-source fields, plus mixed fields scaled by
  their yearly share.
- GW + SW = ALL for every volume. Each run checks this (`run_checks.json`).
- HA and county files hold only additive volumes, counts, recomputed depths, acre-weighted
  means and basin-year constants. The delivered package's summed field attributes are gone.

## Reproduction and bridge (statewide, acre-ft/yr, 1985–2025 mean)

The vendored code with the delivered settings reproduces every HA × year × month cell of
the 2026-08-23 package (ETa, NetET, AR, acres) to within 1e-11 acre-ft.

Irrigated field-years (`%_IRRIGATED` > 0.5), the population in the HA/county summaries:

| Step | ETa | Peff | GW subsidy | NetET | AR | GW NetET | SW NetET |
|---|---:|---:|---:|---:|---:|---:|---:|
| Delivered 2026-08-23 settings | 1,420,968 | 344,479 | 0 | 1,117,471 | 1,647,285 | 639,920 | 477,552 |
| + Diamond 153 and Fish Lake 117 rules | 1,420,968 | 344,479 | 0 | 1,117,471 | 1,647,285 | 641,670 | 475,802 |
| + PRISM SPI (D24), through 2025 | 1,420,968 | 344,479 | 0 | 1,117,471 | 1,647,285 | 641,834 | 475,638 |
| + PRISM precipitation and OTTER Peff | 1,420,968 | 363,361 | 0 | 1,101,443 | 1,627,466 | 631,580 | 469,862 |
| + GW-ET subsidy | 1,420,968 | 363,361 | 34,692 | 1,066,907 | 1,569,188 | 618,349 | 448,559 |
| + EToF climatology fill (final) | 1,423,458 | 363,361 | 34,733 | 1,065,648 | 1,567,158 | 617,737 | 447,911 |

All field-months (the field files; the delivered package's summaries used this population):
NetET goes from 1,945,523 (delivered) to 1,788,345 (final), with a GW subsidy of 123,680.
Most of that subsidy falls on non-irrigated field-years over shallow water tables, which
the irrigated summaries leave out. `07_qa/bridge_table.csv` has every step for both
populations.
