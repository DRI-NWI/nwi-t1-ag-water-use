# Output column policy

## Why this exists

The delivered processor built HA and county summaries with one rule. Every numeric column
was summed, and every text column took the value of the first row in the group. Several
columns in the 2026-08-21/23 HA files and the 2026-09-02 county files have no meaning as a
result. Examples:

| Column in the delivered summaries | What it actually held |
|---|---|
| `NWI_ID`, `OPENET_ID`, `GRIDMET_ID`, `DATE`, `Basin` | the first field's ID or label |
| `irr_eff`, `ITYPE`, `IRR_SRC`, `AWC`, `PAW_ft`, `Acres` | a sum of per-field attributes (e.g. `irr_eff` = 39.25) |
| `EToF`, `EToF_interpolated`, `%_IRRIGATED` | a sum of per-field ratios |
| `spi_value`, `allocation_ratio`, `spi_timescale` | a sum over fields (e.g. `spi_value` = −15.3, `spi_timescale` = 552) |
| `ETA_MM`, `ETNET_MM`, `AR_MM`, `ETA_MM_post_interp` | a sum of per-field depths |
| `interpolation_method`, `IRR_ATTR_ASSUMPTION`, `water_source` | the first field's label |
| `ETO_MM`, `PPT_MM`, `EFF_PPT_MM` | an unweighted mean (the acre-weighted lambda was overwritten) |
| `Water_Surplus_Cumulative_acre_ft`, `Eff_PPT_Adjusted_acre_ft` | sums; meaningful, but undocumented |

## Field-level files (`field_summaries_{ALL,GW,SW}`)

One row per field × month (ALL) or per field × month × component (GW, SW). A mixed field
appears in both GW and SW with its volumes and acreage scaled by the component share.

| Group | Columns |
|---|---|
| Identity | `NWI_ID`, `NWI_PARENT`, `STATE`, `COUNTY_NAME`, `HYD_AREA`, `HYD_AREA_N`, `HA_NAME_CODE`, `GRIDMET_ID` |
| Attributes | `IRR_SRC` (after overrides), `IRR_SRC_ORIGINAL`, `IRR_SRC_ASSUMED`, `IRR_SRC_OVERRIDE`, `irr_eff`, `ITYPE`, `CROP_major` |
| Time | `YEAR`, `MONTH`, `DATE` |
| Allocation | `water_source`, `allocation_method`, `allocation_ratio` (component share; 1 in ALL), `spi_value`, `spi_source` |
| Area | `ACRES_FTR_GEOM` (component acres), `ACRES_IRR`, `%_IRRIGATED` |
| Monthly depths (mm) | `ETO_MM`, `ETA_MM_EXPORT`, `EToF`, `EToF_interpolated`, `interpolation_method`, `ETA_MM_post_interp`, `PPT_MM` (PRISM), `PEFF_MM` (OTTER), `peff_method` |
| Soil and groundwater | `gw_frac`, `AWC`, `PAW_ft` |
| Volumes (acre-ft) | `ETa_final`, `PPT_vol`, `Peff_vol`, `NetET_pre_subsidy`, `GW_subsidy_potential`, `GW_subsidy`, `NetET_intermediate`, `Soil_Moisture_Cap`, `Soil_Storage`, `Soil_Moisture_Overflow`, `Carryforward_Used`, `NetET_final`, `AR_final` |
| Depths (ft) | `ETa_final_ft`, `NetET_final_ft`, `AR_final_ft`, `PPT_final_ft` |

Removed from the field files:

- `EFF_PPT_MM`, `ETNET_MM` and `AR_MM`: the OpenET export's 0.85 × PPT products, replaced by
  OTTER Peff.
- `Eff_PPT_Adjusted_acre_ft`: it mixed Peff, storage and subsidy. It is replaced by the
  explicit `Peff_vol`, `GW_subsidy` and `Carryforward_Used` terms.
- `Water_Surplus_Cumulative_acre_ft`: renamed `Soil_Storage_acre_ft`.
- The duplicate `date` column.
- `IRR_SRC_ASSUMED`-style text columns (`IRR_ATTR_ASSUMPTION`): replaced by the boolean flags.

## HA and county summaries

Rows: irrigated field-years only (`%_IRRIGATED` > 0.5, `summaries.irrigated_only`). The
field files keep every field-month.

Keys:

- HA: `HA_NAME_CODE`, `HYD_AREA`, `HYD_AREA_N`, `YEAR`, `MONTH`.
- County: `COUNTY_NAME`, `YEAR`, `MONTH`, Nevada fields only.

| Rule | Columns |
|---|---|
| Sum (additive) | `ACRES_FTR_GEOM`, `ACRES_IRR`, every `*_acre_ft` volume above |
| Count | `n_fields`, `n_gw_fields`, `n_sw_fields`, `n_mixed_fields`, `n_fields_irr_src_assumed`, `n_fields_eta_interpolated`, `n_fields_eta_climatology`, `n_fields_eta_missing`, `n_fields_peff_fallback`, `n_fields_gw_subsidy` |
| Recomputed from sums | `ETa_final_ft`, `NetET_final_ft`, `AR_final_ft`, `PPT_final_ft`, `GW_subsidy_ft` (volume ÷ acres); `irr_eff_effective` (NetET ÷ AR); `pct_acres_missing_eta`; `pct_fields_gw_equivalent` / `pct_fields_sw_equivalent` (share of **all** fields in the unit, mixed fields counted by their share; identical in the ALL, GW and SW files) |
| Acre-weighted mean (mm) | `ETO_MM`, `PPT_MM`, `PEFF_MM` |
| Basin-year constant (HA files only) | `mixed_sw_ratio`, `spi_sept12`, `spi_source`, `allocation_method` |

Not carried into any summary: field IDs, `irr_eff`, `ITYPE`, `IRR_SRC`, `AWC`, `PAW_ft`,
`EToF*`, `%_IRRIGATED`, `interpolation_method`, `gw_frac`, `allocation_ratio`,
`spi_value`, and the raw export depths.

The county files drop the basin-year constants because a county spans several basins.
`Soil_Moisture_Cap_acre_ft` and `Soil_Storage_acre_ft` are month-end states: sum them across
fields, never across months (`statewide_annual_totals.csv` leaves them out).
