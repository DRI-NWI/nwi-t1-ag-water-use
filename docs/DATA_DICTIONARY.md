# Data dictionary

The files are written under `<output_root>/<run_id>/`. Volumes are in acre-ft, depths in ft
unless marked mm, and areas in acres. "Field-month" is one field in one calendar month.

## Field files: `05_field_summaries/field_summaries_{ALL,GW,SW}.{csv,parquet}`

Every field and every month, 1985–2025, with no irrigation filter.

| File | What each row is |
|---|---|
| ALL | one field-month, whole field: 14,434,296 rows |
| GW | one field-month of groundwater use: pure-GW fields, plus the GW share of mixed fields; 8,203,116 rows |
| SW | one field-month of surface-water use: pure-SW fields, plus the SW share of mixed fields; 9,209,748 rows |

In GW and SW, a mixed field's volumes and acreage are scaled by its share for that year.
GW + SW = ALL for every volume.

### Identity and attributes

| Column | Description |
|---|---|
| `NWI_ID` | Field ID (2026-08-20 NWI boundary layer) |
| `NWI_PARENT` | Parent field ID for subdivided fields |
| `STATE` | State of the field (NV, CA, UT, ID, OR, AZ) |
| `COUNTY_NAME` | Nevada county; blank outside Nevada |
| `HYD_AREA`, `HYD_AREA_N`, `HA_NAME_CODE` | Hydrographic area code, name, and "Name (code)" |
| `GRIDMET_ID` | gridMET cell of the field |
| `IRR_SRC` | Water-right source used for allocation: 1 GW, 2 SW, 3 mixed |
| `IRR_SRC_ORIGINAL` | Source in the boundary layer, before basin rules |
| `IRR_SRC_ASSUMED` | True where the boundary layer has no source (non-Nevada fields); `IRR_SRC` = GW |
| `IRR_SRC_OVERRIDE` | Basin rule applied to the field, if any |
| `irr_eff` | Irrigation efficiency (fraction) |
| `ITYPE` | Irrigation type code |
| `CROP_major` | Dominant CDL crop code |

### Time and allocation

| Column | Description |
|---|---|
| `YEAR`, `MONTH`, `DATE` | Calendar year, month, first day of month |
| `water_source` | ALL: `GW`, `SW`, `mixed`. GW/SW files: `GW`, `SW`, `GW_from_mixed`, `SW_from_mixed` |
| `allocation_method` | `pure_gw`, `pure_sw`, `default_fallback` (dynamic SPI 50/50), or a basin rule name |
| `allocation_ratio` | Share of the field in this component (1 in ALL) |
| `spi_value` | Sept SPI-12 used for the year (mixed fields only) |
| `spi_source` | `prism_contributing_area` or `prism_ha` |

### Area and monthly depths

| Column | Unit | Description |
|---|---|---|
| `ACRES_FTR_GEOM` | acres | Field area (component share in GW/SW) |
| `ACRES_IRR` | acres | IrrMapper irrigated area of the field that year |
| `%_IRRIGATED` | fraction | IrrMapper irrigated fraction of the field that year (constant within a year) |
| `ETO_MM` | mm | Reference ET (OpenET gridMET) |
| `ETA_MM_EXPORT` | mm | OpenET ensemble ETa as exported |
| `EToF` | – | ETa / ETo from the export |
| `EToF_interpolated` | – | EToF after gap filling |
| `interpolation_method` | – | `original`, `linear_1month`, `climatology`, `unfilled_missing` |
| `ETA_MM_post_interp` | mm | EToF_interpolated × ETO_MM |
| `PPT_MM` | mm | PRISM 800 m precipitation |
| `PEFF_MM` | mm | OTTER effective precipitation |
| `peff_method` | – | `otter`, or `ppt_085_fallback` for the 242 fields without OTTER soils |

### Groundwater and soil

| Column | Unit | Description |
|---|---|---|
| `gw_frac` | fraction | Groundwater-ET fraction f of the field (Tan et al. 2025, parity DTW) |
| `AWC` | fraction | Available water capacity (volume of water per volume of soil) |
| `PAW_ft` | ft | Plant-available water: AWC × 1.5 m rooting depth |

### Volumes (acre-ft)

| Column | Definition |
|---|---|
| `ETa_final_acre_ft` | ETA_MM_post_interp × acres |
| `PPT_vol_acre_ft` | PRISM precipitation × acres |
| `Peff_vol_acre_ft` | OTTER Peff × acres |
| `NetET_pre_subsidy_acre_ft` | ETa − Peff |
| `GW_subsidy_potential_acre_ft` | f × ETa |
| `GW_subsidy_acre_ft` | min(f × ETa, max(NetET_pre_subsidy, 0)) |
| `NetET_intermediate_acre_ft` | NetET_pre_subsidy − GW_subsidy; input to carry-forward |
| `Soil_Moisture_Cap_acre_ft` | Storage cap: PAW × acres |
| `Soil_Storage_acre_ft` | Stored soil water at month end (state, not a flux) |
| `Soil_Moisture_Overflow_acre_ft` | Surplus beyond the cap |
| `Carryforward_Used_acre_ft` | Stored water used this month: max(NetET_intermediate, 0) − NetET_final |
| `NetET_final_acre_ft` | Net ET after subsidy and carry-forward (≥ 0) |
| `AR_final_acre_ft` | Applied water: NetET_final / irr_eff |

### Depths (ft)

`ETa_final_ft`, `NetET_final_ft`, `AR_final_ft` and `PPT_final_ft` are the matching volume ÷
`ACRES_FTR_GEOM`.

## HA and county summaries: `06_summaries/{ha,county}_summaries_{ALL,GW,SW}.csv`

Each row is one HA (or one Nevada county) × year × month. Only irrigated field-years
(`%_IRRIGATED` > 0.5) are included. HA totals include every field mapped to the HA,
including the out-of-state fields of HAs that cross the state line.

| Keys | Columns |
|---|---|
| HA | `HA_NAME_CODE`, `HYD_AREA`, `HYD_AREA_N`, `YEAR`, `MONTH` |
| County | `COUNTY_NAME`, `YEAR`, `MONTH` (Nevada fields only) |

| Column | Rule |
|---|---|
| `ACRES_FTR_GEOM`, `ACRES_IRR` | Sum of field acres in that month. For annual acres, take the mean over months |
| All `*_acre_ft` volumes listed above | Sum. `Soil_Moisture_Cap` and `Soil_Storage` are states: sum across fields, never across months |
| `n_fields`, `n_gw_fields`, `n_sw_fields`, `n_mixed_fields` | Count of field rows, by source |
| `n_fields_irr_src_assumed`, `n_fields_eta_interpolated`, `n_fields_eta_climatology`, `n_fields_eta_missing`, `n_fields_peff_fallback`, `n_fields_gw_subsidy` | Count of field rows with that flag (`gw_frac` > 0 for the last) |
| `ETO_MM`, `PPT_MM`, `PEFF_MM` | Acre-weighted mean (mm) |
| `ETa_final_ft`, `NetET_final_ft`, `AR_final_ft`, `PPT_final_ft`, `GW_subsidy_ft` | Summed volume ÷ summed acres |
| `irr_eff_effective` | NetET_final ÷ AR_final |
| `pct_acres_missing_eta` | % of acres with no ETa that month |
| `pct_fields_gw_equivalent`, `pct_fields_sw_equivalent` | % of all fields in the unit, with mixed fields counted by their share; same values in ALL, GW and SW |
| `mixed_sw_ratio`, `spi_sept12`, `spi_source`, `allocation_method` | Basin-year allocation for mixed fields (HA files only; blank where the HA has no mixed fields) |

## Other run files

| File | Content |
|---|---|
| `06_summaries/allocation_basin_year.csv` | Mixed-field SW share, SPI and rule per HA and year |
| `06_summaries/statewide_annual_totals.csv` | Annual volumes for each component (`component`) and population (`population`: `all_field_months`, `irrigated_field_months`) |
| `06_summaries/run_checks.json` | Run settings, GW + SW = ALL closure, flag counts |
| `03_spi/basin_year_spi.csv` | Sept SPI-12 per HA and year, with its source |
| `04_gw_subsidy/field_gwfrac.parquet` | Per-field DTW, texture, crop, Tan parameters and f |
| `02_otter/field_monthly_otter_peff.parquet` | Monthly OTTER Peff, precipitation and ET from applied water per field |
| `01_inputs/input_receipts.json` | Path, modification time, size and fingerprint of every input |
