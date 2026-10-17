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
| Fields | 2026-08-20 NWI boundary layer, 29,338 fields (24,882 NV), no irrigation-activity filter |
| ET | OpenET ENSEMBLE monthly ETa; one-month EToF gaps filled linearly; longer gaps flagged |
| Precipitation | PRISM 800 m daily at each field, summed by month |
| Effective precipitation | OTTER per field (NM05: `init_dru_frac = init_drl_frac = mad_frac = 0.5`), continuous from 1985, driven by the same PRISM precipitation. This replaces 0.85 × PPT |
| GW-ET subsidy | Tan et al. (2025) f(DTW) × ETa, with DTW from the parity 100 m raster. Applied only against positive NetET, before carry-forward |
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

## Reproduction and bridge (statewide, all fields, acre-ft/yr, 1985–2025 mean)

The vendored code with the delivered settings reproduces every HA × year × month cell of
the 2026-08-23 package (ETa, NetET, AR, acres) to within 1e-11 acre-ft.

| Step | ETa | Peff | GW subsidy | NetET | AR | GW NetET | SW NetET |
|---|---:|---:|---:|---:|---:|---:|---:|
| Delivered 2026-08-23 (reproduced) | 2,805,047 | 1,028,870 | 0 | 1,945,523 | 3,177,638 | 892,741 | 1,052,782 |
| + Diamond 153 and Fish Lake 117 rules | 2,805,047 | 1,028,870 | 0 | 1,945,523 | 3,177,638 | 895,702 | 1,049,821 |
| + PRISM SPI (D24), through 2025 | 2,805,047 | 1,028,870 | 0 | 1,945,523 | 3,177,638 | 895,912 | 1,049,611 |
| + PRISM precipitation and OTTER Peff | 2,805,047 | 1,057,622 | 0 | 1,914,302 | 3,130,934 | 881,311 | 1,032,991 |
| + GW-ET subsidy (final) | 2,805,047 | 1,057,622 | 104,118 | 1,811,539 | 2,938,094 | 867,805 | 943,734 |

The GW subsidy in Nevada is 24,009 acre-ft/yr on irrigated field-months (>50%
irrigated), 1.9% of their ETa. Another 75,158 acre-ft/yr falls on non-irrigated
field-months over shallow water tables, which the all-field population includes.
