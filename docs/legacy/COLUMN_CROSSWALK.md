# Column crosswalk: earlier packages → current outputs

Every column of the earlier packages, and what it is in the current outputs. The earlier
packages are:

- field and HA summaries: `Will_documentation/2026-08-23/`;
- county summaries: `Will_documentation/2026-09-02_county_NV/`.

Current columns are defined in [../DATA_DICTIONARY.md](../DATA_DICTIONARY.md).

Status:

- **kept**: same name and meaning.
- **renamed**: same quantity, new name.
- **replaced**: a different, documented quantity takes its place.
- **dropped**: not written. The reason is given.

## Field files (`field_summaries_{GW,SW}` → `field_summaries_{ALL,GW,SW}`)

Earlier: 60 columns. Current: 55 columns. ALL (the whole field) is new.

| Earlier column | Status | Current column / reason |
|---|---|---|
| `NWI_ID`, `NWI_PARENT`, `STATE`, `HYD_AREA`, `HYD_AREA_N`, `HA_NAME_CODE`, `GRIDMET_ID` | kept | |
| `irr_eff`, `ITYPE`, `IRR_SRC`, `IRR_SRC_ASSUMED` | kept | |
| `YEAR`, `MONTH`, `DATE` | kept | |
| `water_source`, `allocation_method`, `allocation_ratio`, `spi_value` | kept | |
| `ACRES_FTR_GEOM`, `ACRES_IRR`, `%_IRRIGATED` | kept | |
| `ETO_MM`, `EToF`, `EToF_interpolated`, `interpolation_method`, `ETA_MM_post_interp` | kept | |
| `PPT_MM` | kept, new source | PRISM 800 m. Earlier: the export precipitation, recomputed as `EFF_PPT_MM` / 0.85 |
| `AWC`, `PAW_ft`, `Soil_Moisture_Cap_acre_ft`, `Soil_Moisture_Overflow_acre_ft` | kept | |
| `ETa_final_acre_ft`, `NetET_final_acre_ft`, `AR_final_acre_ft`, `PPT_vol_acre_ft` | kept | Net ET and applied water now include OTTER Peff and the GW subsidy |
| `ETa_final_ft`, `NetET_final_ft`, `AR_final_ft`, `PPT_final_ft` | kept | |
| `ETA_MM` | replaced | `ETA_MM_EXPORT` (the value as exported). Earlier `ETA_MM` was overwritten by the gap-filled value, which is `ETA_MM_post_interp` |
| `EFF_PPT_MM` | replaced | `PEFF_MM`, OTTER effective precipitation. Earlier: 0.85 × PPT |
| `Eff_PPT_vol_acre_ft` | renamed | `Peff_vol_acre_ft` |
| `NetET_vol_intermediate_acre_ft` | renamed | `NetET_intermediate_acre_ft`, net ET entering carry-forward, after the GW subsidy. Before the subsidy: `NetET_pre_subsidy_acre_ft` |
| `Water_Surplus_Cumulative_acre_ft` | renamed | `Soil_Storage_acre_ft`, stored soil water at month end |
| `Eff_PPT_Adjusted_acre_ft` | replaced | It was ETa − final net ET, which mixes Peff, carry-forward and subsidy. The terms are now separate: `Peff_vol_acre_ft`, `Carryforward_Used_acre_ft`, `GW_subsidy_acre_ft` |
| `ETa_vol_intermediate_acre_ft` | dropped | identical to `ETa_final_acre_ft` |
| `ETNET_MM`, `AR_MM`, `AR_vol_original_acre_ft` | dropped | the OpenET export's own net ET and applied water, built on 0.85 × PPT; superseded by `NetET_final` and `AR_final` |
| `OPENET_ID` | dropped | identical to `NWI_ID` for every 2026-08-20 field |
| `Basin` | dropped | the same information as `HYD_AREA` + `HYD_AREA_N` |
| `HYD_AREA_new`, `HYD_AREA_N_new` | dropped | packaging copies of `HYD_AREA` / `HYD_AREA_N` |
| `Acres` | dropped | boundary-layer acres; every volume uses `ACRES_FTR_GEOM` |
| `ACRES_ALL` | dropped | the export's total-area column, not used in any calculation; available in the OpenET export |
| `irr_eff_ASSUMED`, `IRR_ATTR_ASSUMED`, `IRR_ATTR_ASSUMPTION` | dropped | `irr_eff` is complete in the export, so it is never assumed; the source assumption is `IRR_SRC_ASSUMED` |
| `spi_timescale` | dropped | always 12 (`spi.timescale_months` in the config) |
| `date` | dropped | duplicate of `DATE` |

New field columns:

| Column | Content |
|---|---|
| `COUNTY_NAME` | Nevada county of the field |
| `IRR_SRC_ORIGINAL` | source in the boundary layer, before basin rules |
| `IRR_SRC_OVERRIDE` | basin rule applied (Diamond Valley mixed → GW) |
| `CROP_major` | dominant CDL crop, used for the subsidy rooting depth |
| `spi_source` | PRISM contributing area or HA |
| `peff_method` | `otter`, or the 0.85 × PPT fallback |
| `gw_frac` | groundwater-ET fraction f |
| `NetET_pre_subsidy_acre_ft`, `GW_subsidy_potential_acre_ft`, `GW_subsidy_acre_ft`, `Carryforward_Used_acre_ft` | water-balance terms |

## HA summaries (`ha_summaries_EToF_final_{GW,SW}` → `ha_summaries_{ALL,GW,SW}`)

Earlier: 67 columns. Current: 64 columns. Rows are now irrigated field-years
(`%_IRRIGATED` > 0.5); earlier rows were all field-months.

| Earlier column | Status | Current column / reason |
|---|---|---|
| `HA_NAME_CODE`, `HYD_AREA`, `HYD_AREA_N`, `YEAR`, `MONTH` | kept | keys |
| `ACRES_FTR_GEOM`, `ACRES_IRR`, `ETa_final_acre_ft`, `NetET_final_acre_ft`, `AR_final_acre_ft`, `PPT_vol_acre_ft` | kept | sums |
| `Soil_Moisture_Cap_acre_ft`, `Soil_Moisture_Overflow_acre_ft` | kept | sums |
| `ETa_final_ft`, `NetET_final_ft`, `AR_final_ft`, `PPT_final_ft` | kept | summed volume ÷ summed acres |
| `ETO_MM`, `PPT_MM` | kept, fixed | now acre-weighted means (earlier: unweighted) |
| `EFF_PPT_MM` | replaced | `PEFF_MM`, acre-weighted OTTER Peff |
| `Eff_PPT_vol_acre_ft`, `NetET_vol_intermediate_acre_ft`, `Water_Surplus_Cumulative_acre_ft` | renamed | `Peff_vol_acre_ft`, `NetET_intermediate_acre_ft`, `Soil_Storage_acre_ft` |
| `field_count`, `num_fields_irrigated` | renamed | `n_fields` |
| `is_gw_field`, `is_sw_field`, `is_mixed_field` | renamed | `n_gw_fields`, `n_sw_fields`, `n_mixed_fields` |
| `percent_gw_fields`, `percent_sw_fields` | renamed | `pct_fields_gw_equivalent`, `pct_fields_sw_equivalent` |
| `spi_value` | replaced | `spi_sept12`, the basin-year SPI. Earlier: a sum over fields |
| `allocation_ratio` | replaced | `mixed_sw_ratio`, the basin-year mixed SW share. Earlier: a sum over fields |
| `allocation_method` | kept | basin-year rule |
| `NWI_ID`, `OPENET_ID`, `NWI_PARENT`, `GRIDMET_ID`, `Basin`, `DATE`, `date`, `STATE`, `HYD_AREA_new`, `HYD_AREA_N_new` | dropped | one arbitrary field's value (`first()`) |
| `irr_eff`, `ITYPE`, `IRR_SRC`, `Acres`, `AWC`, `PAW_ft`, `ACRES_ALL` | dropped | sums of per-field attributes (e.g. `irr_eff` = 39.25). Use `irr_eff_effective` and the counts |
| `EToF`, `EToF_interpolated`, `%_IRRIGATED`, `spi_timescale` | dropped | sums of per-field ratios |
| `ETA_MM`, `ETNET_MM`, `AR_MM`, `ETA_MM_post_interp` | dropped | sums of per-field depths; use the `_ft` depths |
| `ETa_vol_intermediate_acre_ft`, `AR_vol_original_acre_ft`, `Eff_PPT_Adjusted_acre_ft` | dropped | as in the field files |
| `IRR_SRC_ASSUMED`, `irr_eff_ASSUMED`, `IRR_ATTR_ASSUMED`, `IRR_ATTR_ASSUMPTION`, `interpolation_method`, `water_source` | dropped | one field's flag or label; replaced by counts (`n_fields_irr_src_assumed`, `n_fields_eta_*`) |

New HA columns:

| Column | Content |
|---|---|
| `GW_subsidy_ft` | GW subsidy depth |
| `irr_eff_effective` | net ET ÷ applied water |
| `pct_acres_missing_eta` | share of acres with no ETa that month |
| `n_fields_irr_src_assumed`, `n_fields_eta_interpolated`, `n_fields_eta_climatology`, `n_fields_eta_missing`, `n_fields_peff_fallback`, `n_fields_gw_subsidy` | field counts by data flag |
| `spi_source` | PRISM contributing area or HA |
| `<col>_NV`, `<col>_outside_NV` | Nevada and out-of-state parts of acres, ETa, PPT, Peff, GW subsidy, carry-forward, net ET, applied water and field count |
| `NetET_pre_subsidy_acre_ft`, `GW_subsidy_potential_acre_ft`, `GW_subsidy_acre_ft`, `Carryforward_Used_acre_ft` | water-balance terms |

## County summaries (`county_wide_summaries_EToF_final_{GW,SW}` → `county_summaries_{ALL,GW,SW}`)

Earlier: 67 columns. Current: 40 columns. Nevada fields only, as before; rows are now
irrigated field-years.

- **Same rules as the HA summaries.** Every earlier column is treated as in the HA table
  above.
- **No basin-year columns.** `allocation_method`, `spi_value` and `allocation_ratio` are
  dropped, because a county spans several basins.
- **No HA keys.** `HYD_AREA` and `HYD_AREA_N` are not county columns.
- **No state split.** The `_NV` / `_outside_NV` columns are not needed: a county holds only
  Nevada fields.
