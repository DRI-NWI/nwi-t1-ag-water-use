# Column crosswalk: earlier packages → current outputs

Every column of the earlier packages, and what it is in the current outputs. The earlier
packages are:

- field and HA summaries: `Will_documentation/2026-08-23/`;
- county summaries: `Will_documentation/2026-09-02_county_NV/`.

Current columns are defined in [../DATA_DICTIONARY.md](../DATA_DICTIONARY.md).

File and column names are the original ones. No column is renamed. Status:

- **kept**: same name and meaning.
- **new source**: same name; the value comes from a different input.
- **fixed**: same name; the earlier value was wrong at that level.
- **added**: new column.
- **dropped**: not written. The reason is given.

## File names

| Earlier | Current |
|---|---|
| `field_summaries_EToF_final_{GW,SW}.csv` | same; plus `field_summaries_EToF_final.csv` (whole field) and a `.parquet` copy of each |
| `ha_summaries_EToF_final_{GW,SW}.csv` | same (all fields in the HA, including out-of-state fields); plus `ha_summaries_EToF_final.csv` (whole field) and `..._NV_fields_only.csv` versions of each (Nevada fields only) |
| `counties/county_wide_summaries_EToF_final_{GW,SW}.csv` | `county_wide_summaries_EToF_final{,_GW,_SW}.csv` in `06_summaries/` |

## Field files

| Earlier column | Status | Current |
|---|---|---|
| `NWI_ID`, `NWI_PARENT`, `STATE`, `HYD_AREA`, `HYD_AREA_N`, `HA_NAME_CODE`, `GRIDMET_ID`, `irr_eff`, `ITYPE`, `IRR_SRC`, `IRR_SRC_ASSUMED` | kept | |
| `YEAR`, `MONTH`, `DATE`, `water_source`, `allocation_method`, `allocation_ratio`, `spi_value` | kept | |
| `ACRES_FTR_GEOM`, `%_IRRIGATED`, `ETO_MM`, `ETA_MM`, `EToF`, `EToF_interpolated`, `interpolation_method`, `ETA_MM_post_interp` | kept | |
| `AWC`, `PAW_ft`, `Soil_Moisture_Cap_acre_ft`, `Soil_Moisture_Overflow_acre_ft`, `Water_Surplus_Cumulative_acre_ft` | kept | |
| `ETa_final_acre_ft`, `Eff_PPT_vol_acre_ft`, `NetET_vol_intermediate_acre_ft`, `NetET_final_acre_ft`, `AR_final_acre_ft`, `PPT_vol_acre_ft` | kept | Net ET and applied water include OTTER effective precipitation and the GW subsidy. `NetET_vol_intermediate_acre_ft` is the net ET entering carry-forward, now after the subsidy |
| `ETa_final_ft`, `NetET_final_ft`, `AR_final_ft`, `PPT_final_ft` | kept | |
| `PPT_MM` | new source | PRISM 800 m. Earlier: the export precipitation, recomputed as `EFF_PPT_MM` / 0.85 |
| `EFF_PPT_MM` | new source | OTTER effective precipitation. Earlier: 0.85 × PPT |
| `ACRES_IRR` | fixed | In the GW and SW files a mixed field's irrigated acres are split by its share, like `ACRES_FTR_GEOM`. Earlier both halves carried the full value |
| `ETa_vol_intermediate_acre_ft` | dropped | identical to `ETa_final_acre_ft` |
| `ETNET_MM`, `AR_MM`, `AR_vol_original_acre_ft` | dropped | the OpenET export's own net ET and applied water, built on 0.85 × PPT; superseded by `NetET_final` and `AR_final` |
| `Eff_PPT_Adjusted_acre_ft` | dropped | ETa − final net ET, which mixes effective precipitation, carry-forward and subsidy; the separate terms are `Eff_PPT_vol_acre_ft`, `Carryforward_Used_acre_ft` and `GW_subsidy_acre_ft` |
| `OPENET_ID` | dropped | identical to `NWI_ID` for every 2026-08-20 field |
| `Basin` | dropped | the same information as `HYD_AREA` + `HYD_AREA_N` |
| `HYD_AREA_new`, `HYD_AREA_N_new` | dropped | packaging copies of `HYD_AREA` / `HYD_AREA_N` |
| `Acres` | dropped | boundary-layer acres; every volume uses `ACRES_FTR_GEOM` |
| `ACRES_ALL` | dropped | the export's total-area column, not used in any calculation |
| `irr_eff_ASSUMED`, `IRR_ATTR_ASSUMED`, `IRR_ATTR_ASSUMPTION` | dropped | `irr_eff` is complete in the export, so it is never assumed; the source assumption is `IRR_SRC_ASSUMED` |
| `spi_timescale` | dropped | always 12 |
| `date` | dropped | duplicate of `DATE` |

Added field columns:

| Column | Content |
|---|---|
| `COUNTY_NAME` | Nevada county |
| `IRR_SRC_ORIGINAL`, `IRR_SRC_OVERRIDE` | source before basin rules, and the rule applied |
| `CROP_major` | dominant CDL crop (subsidy rooting depth) |
| `spi_source` | PRISM contributing area or HA |
| `ETA_MM_EXPORT` | ETa as exported, before gap filling |
| `peff_method` | `otter` or the 0.85 × PPT fallback |
| `gw_frac` | groundwater-ET fraction f |
| `NetET_pre_subsidy_acre_ft`, `GW_subsidy_potential_acre_ft`, `GW_subsidy_acre_ft`, `Carryforward_Used_acre_ft` | water-balance terms |

## HA and county summaries

Rows are irrigated field-years (`%_IRRIGATED` > 0.5); earlier rows were all field-months.
County summaries cover Nevada fields, as before.

| Earlier column | Status | Current |
|---|---|---|
| keys (`HA_NAME_CODE`, `HYD_AREA`, `HYD_AREA_N` / `COUNTY_NAME`, `YEAR`, `MONTH`) | kept | |
| `ACRES_FTR_GEOM`, `ETa_final_acre_ft`, `Eff_PPT_vol_acre_ft`, `NetET_vol_intermediate_acre_ft`, `NetET_final_acre_ft`, `AR_final_acre_ft`, `PPT_vol_acre_ft`, `Soil_Moisture_Cap_acre_ft`, `Soil_Moisture_Overflow_acre_ft`, `Water_Surplus_Cumulative_acre_ft` | kept | sums |
| `ETa_final_ft`, `NetET_final_ft`, `AR_final_ft`, `PPT_final_ft` | kept | summed volume ÷ summed acres |
| `field_count`, `num_fields_irrigated`, `is_gw_field`, `is_sw_field`, `is_mixed_field`, `percent_gw_fields`, `percent_sw_fields` | kept | counts and percentages |
| `ACRES_IRR` | fixed | GW/SW files: split by share (see field files) |
| `ETO_MM`, `PPT_MM`, `EFF_PPT_MM` | fixed | acre-weighted means; earlier unweighted means |
| `allocation_method` | kept, HA only | the basin-year rule |
| `spi_value`, `allocation_ratio` | dropped | earlier a sum over fields (e.g. `spi_value` = −15.3); the basin-year values are the added `spi_sept12` and `mixed_sw_ratio` (HA only) |
| `NWI_ID`, `OPENET_ID`, `NWI_PARENT`, `GRIDMET_ID`, `Basin`, `DATE`, `date`, `STATE`, `HYD_AREA_new`, `HYD_AREA_N_new` | dropped | one arbitrary field's value (`first()`) |
| `irr_eff`, `ITYPE`, `IRR_SRC`, `Acres`, `AWC`, `PAW_ft`, `ACRES_ALL` | dropped | sums of per-field attributes (e.g. `irr_eff` = 39.25) |
| `EToF`, `EToF_interpolated`, `%_IRRIGATED`, `spi_timescale` | dropped | sums of per-field ratios |
| `ETA_MM`, `ETNET_MM`, `AR_MM`, `ETA_MM_post_interp` | dropped | sums of per-field depths; use the `_ft` depths |
| `ETa_vol_intermediate_acre_ft`, `AR_vol_original_acre_ft`, `Eff_PPT_Adjusted_acre_ft` | dropped | as in the field files |
| `IRR_SRC_ASSUMED`, `irr_eff_ASSUMED`, `IRR_ATTR_ASSUMED`, `IRR_ATTR_ASSUMPTION`, `interpolation_method`, `water_source` | dropped | one field's flag or label; replaced by the added counts |
| `total_all_fields`, `gw_field_equivalent`, `sw_field_equivalent` (county only) | dropped | intermediate columns; `field_count` and `percent_*_fields` carry the result |

Added summary columns:

| Column | Content |
|---|---|
| `NetET_pre_subsidy_acre_ft`, `GW_subsidy_potential_acre_ft`, `GW_subsidy_acre_ft`, `Carryforward_Used_acre_ft` | water-balance terms |
| `GW_subsidy_ft`, `irr_eff_effective`, `pct_acres_missing_eta` | subsidy depth, net ET ÷ applied water, share of acres with no ETa |
| `n_fields_irr_src_assumed`, `n_fields_eta_interpolated`, `n_fields_eta_climatology`, `n_fields_eta_missing`, `n_fields_peff_fallback`, `n_fields_gw_subsidy` | field counts by data flag |
| `mixed_sw_ratio`, `spi_sept12`, `spi_source` (HA only) | basin-year allocation of mixed fields |
