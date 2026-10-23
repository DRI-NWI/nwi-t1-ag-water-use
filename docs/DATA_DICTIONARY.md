# Data dictionary

For what each column replaces in the earlier packages (kept, changed, added or dropped, with
reasons), see [legacy/COLUMN_CROSSWALK.md](legacy/COLUMN_CROSSWALK.md).

The files are written under `<output_root>/<run_id>/`. File and column names follow the
original agricultural-water-forecasting outputs. Volumes are in acre-ft, depths in ft
unless marked mm, and areas in acres. "Field-month" is one field in one calendar month.

## Field files: `05_field_summaries/field_summaries_EToF_final{,_GW,_SW}.{csv,parquet}`

Every field and every month, 1985–2025, with no irrigation filter.

| File | What each row is |
|---|---|
| `field_summaries_EToF_final` | one field-month, whole field (GW + SW): 14,434,296 rows |
| `field_summaries_EToF_final_GW` | one field-month of groundwater use: pure-GW fields, plus the GW share of mixed fields; 8,203,116 rows |
| `field_summaries_EToF_final_SW` | one field-month of surface-water use: pure-SW fields, plus the SW share of mixed fields; 9,209,748 rows |

In the GW and SW files, a mixed field's volumes, `ACRES_FTR_GEOM` and `ACRES_IRR` are scaled
by its share for that year. GW + SW = whole field for every volume.

### Which values to use

Every ET value in the field files is gap-filled; the raw export values are not written.

| Use | Columns |
|---|---|
| Rates (ft per month) | `ETa_final_ft`, `NetET_final_ft`, `AR_final_ft`, `PPT_final_ft` |
| Volumes (acre-ft) | `ETa_final_acre_ft`, `NetET_final_acre_ft`, `AR_final_acre_ft`, `PPT_vol_acre_ft`, `Eff_PPT_vol_acre_ft`, `GW_subsidy_acre_ft` |

`interpolation_method` shows how each month's ETa was obtained. 1,194 of 14,434,296
field-months have no observed ETa to fill from (`unfilled_missing`) and stay missing.

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
| `IRR_SRC_ORIGINAL` | Source before basin rules |
| `IRR_SRC_ASSUMED` | True where the boundary layer has no source (non-Nevada fields); `IRR_SRC` = GW |
| `IRR_SRC_OVERRIDE` | Basin rule applied to the field, if any (e.g. "basin 153: mixed->GW") |
| `irr_eff` | Irrigation efficiency (fraction) |
| `ITYPE` | Irrigation type code |
| `CROP_major` | Dominant CDL crop code |

### Time and allocation

| Column | Description |
|---|---|
| `YEAR`, `MONTH`, `DATE` | Calendar year, month, first day of month |
| `water_source` | Whole-field file: `GW`, `SW`, `mixed`. GW/SW files: `GW`, `SW`, `GW_from_mixed`, `SW_from_mixed` |
| `allocation_method` | `pure_gw`, `pure_sw`, `default_fallback` (dynamic SPI 50/50), or a basin rule name |
| `allocation_ratio` | Share of the field in this file (1 in the whole-field file) |
| `spi_value` | Sept SPI-12 used for the year (mixed fields only) |
| `spi_source` | `prism_contributing_area` or `prism_ha` |

### Area and monthly depths

| Column | Unit | Description |
|---|---|---|
| `ACRES_FTR_GEOM` | acres | Field area (component share in GW/SW) |
| `ACRES_IRR` | acres | IrrMapper irrigated area of the field that year (component share in GW/SW) |
| `%_IRRIGATED` | fraction | IrrMapper irrigated fraction of the field that year (constant within a year) |
| `ETO_MM` | mm | Reference ET (OpenET gridMET) |
| `ETA_MM` | mm | OpenET ensemble ETa, gap-filled (same as `ETA_MM_post_interp`) |
| `EToF_interpolated` | – | ETa / ETo, gap-filled |
| `interpolation_method` | – | `original`, `linear_1month`, `climatology`, `unfilled_missing` |
| `ETA_MM_post_interp` | mm | EToF_interpolated × ETO_MM |
| `PPT_MM` | mm | PRISM 800 m precipitation |
| `EFF_PPT_MM` | mm | Effective precipitation: OTTER per field |
| `peff_method` | – | `otter`, or `ppt_085_fallback` (0.85 × PPT) for the 242 fields without OTTER soils |

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
| `Eff_PPT_vol_acre_ft` | EFF_PPT_MM × acres |
| `NetET_pre_subsidy_acre_ft` | ETa − effective precipitation |
| `GW_subsidy_potential_acre_ft` | f × ETa |
| `GW_subsidy_acre_ft` | min(f × ETa, max(NetET_pre_subsidy, 0)) |
| `NetET_vol_intermediate_acre_ft` | NetET_pre_subsidy − GW_subsidy; the net ET entering carry-forward |
| `Soil_Moisture_Cap_acre_ft` | Storage cap: PAW × acres |
| `Water_Surplus_Cumulative_acre_ft` | Stored soil water at month end (a state, not a flux) |
| `Soil_Moisture_Overflow_acre_ft` | Surplus beyond the cap |
| `Carryforward_Used_acre_ft` | Stored water used this month: max(NetET_vol_intermediate, 0) − NetET_final |
| `NetET_final_acre_ft` | Net ET after the subsidy and carry-forward (≥ 0) |
| `AR_final_acre_ft` | Applied water: NetET_final / irr_eff |

### Depths (ft)

`ETa_final_ft`, `NetET_final_ft`, `AR_final_ft` and `PPT_final_ft` are the matching volume ÷
`ACRES_FTR_GEOM`.

## HA summaries: `06_summaries/ha_summaries_EToF_final{,_GW,_SW}{,_NV_fields_only}.csv`

Each row is one HA × year × month. Only irrigated field-years (`%_IRRIGATED` > 0.5) are
included.

| File | Fields included |
|---|---|
| `ha_summaries_EToF_final{,_GW,_SW}.csv` | every field in the HA, including the out-of-state fields of HAs that cross the state line |
| `ha_summaries_EToF_final{,_GW,_SW}_NV_fields_only.csv` | Nevada fields only |

## County summaries: `06_summaries/county_wide_summaries_EToF_final{,_GW,_SW}.csv`

Each row is one Nevada county × year × month, for Nevada fields in irrigated field-years.

## Summary columns (HA and county)

| Keys | Columns |
|---|---|
| HA | `HA_NAME_CODE`, `HYD_AREA`, `HYD_AREA_N`, `YEAR`, `MONTH` |
| County | `COUNTY_NAME`, `YEAR`, `MONTH` |

| Column | Rule |
|---|---|
| `ACRES_FTR_GEOM`, `ACRES_IRR` | Sum of field acres in that month. For annual acres, take the mean over months |
| All `*_acre_ft` volumes listed above | Sum. `Soil_Moisture_Cap` and `Water_Surplus_Cumulative` are states: sum across fields, never across months |
| `field_count`, `num_fields_irrigated` | Number of field rows (the same value) |
| `is_gw_field`, `is_sw_field`, `is_mixed_field` | Number of field rows by source |
| `percent_gw_fields`, `percent_sw_fields` | % of all fields in the HA or county, with mixed fields counted by their share; same values in the whole-field, GW and SW files |
| `n_fields_irr_src_assumed`, `n_fields_eta_interpolated`, `n_fields_eta_climatology`, `n_fields_eta_missing`, `n_fields_peff_fallback`, `n_fields_gw_subsidy` | Number of field rows with that flag (`gw_frac` > 0 for the last) |
| `ETO_MM`, `PPT_MM`, `EFF_PPT_MM` | Acre-weighted mean (mm) |
| `ETa_final_ft`, `NetET_final_ft`, `AR_final_ft`, `PPT_final_ft`, `GW_subsidy_ft` | Summed volume ÷ summed acres |
| `irr_eff_effective` | NetET_final ÷ AR_final |
| `pct_acres_missing_eta` | % of acres with no ETa that month |
| `mixed_sw_ratio`, `spi_sept12`, `spi_source`, `allocation_method` | HA files only: the basin-year allocation of mixed fields (blank where the HA has no mixed fields) |

## Other run files

| File | Content |
|---|---|
| `06_summaries/dynamic_allocation_stats.csv` | Mixed-field SW share, SPI, rule, fields and acres per HA and year |
| `06_summaries/statewide_annual_totals.csv` | Annual volumes for each component (`component`) and population (`population`: `all_field_months`, `irrigated_field_months`) |
| `06_summaries/run_checks.json` | Run settings, GW + SW = whole field closure, flag counts |
| `06_summaries/field_attribute_enrichment_report.csv` | Boundary-attribute join and missing-attribute fallbacks |
| `03_spi/basin_spi_prism.csv` | SPI per basin and month, with its precipitation source |
| `04_gw_subsidy/field_params.parquet` | Per-field DTW, texture, crop, Tan parameters and f (`gw_frac`) |
| `02_otter/field_monthly_otter_peff.parquet` | Monthly OTTER effective precipitation per field |
