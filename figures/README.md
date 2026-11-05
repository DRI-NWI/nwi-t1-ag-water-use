# Figures

Each script reads the run outputs (`config/nwi_t1_final.yaml`) and writes to `docs/images/`.
`_mapkit.py` holds what the maps share: the map projection (Albers equal-area, standard
parallels 36° and 41° N, centered on 117° W), the boundary layers, and functions that read
the run outputs. Map layers are saved in `<run>/figures_cache/` after the first run.

| Script | Output | Content |
|---|---|---|
| `fig01_overview_water_rights.py` | `fig01_fields_water_rights.png` | Fields by water-right source, HAs (including their extent outside Nevada), insets 101/153/108 |
| `fig02_ha_maps.py` | `fig02_ha_volumes.png`, `fig03_ha_depths.png`, `fig04_ha_gw_sw.png` | HA maps of every water-balance term (volumes, depths), GW and SW volumes, SW share of mixed fields |
| `fig05_statewide_timeseries.py` | `fig05_statewide_annual.png`, `fig06_monthly_cycle.png` | Statewide annual series by term and source; mean monthly cycle, subsidy, carry-forward, storage |
| `fig07_allocation.py` | `fig07_allocation_spi.png` | September 12-month SPI by HA and year, the share rule, SW share of mixed fields by year, area used for the SPI precipitation |
| `fig08_data_quality.py` | `fig08_inputs_quality.png` | ETa gap filling, PRISM against the OpenET export precipitation, OTTER effective precipitation against 0.85 × precipitation, effective precipitation as a share of precipitation, irrigation efficiency, fields without OTTER soil inputs |
| `fig09_irrigated_area.py` | `fig09_irrigated_area_irrmapper_xgbrf.png`, `docs/tables/irrigated_area_by_year.csv` | Irrigated area from IrrMapper and from the XGBRF irrigation classifier |
| `prep_ma_dtw_zonal.py` | `<run>/04_gw_subsidy/ma_comparison/` | Ma et al. (2026) depth to groundwater averaged over each field's pixels, for the same fields (needs `exactextract`) |
| `fig10_gw_subsidy_dtw.py` | `fig10`–`fig14`, `docs/tables/ha_gw_subsidy_new_vs_ma.csv` | GW-ET subsidy by field and HA, with the new depth-to-groundwater map and with Ma et al. (2026) |
| `fig15_precip_prism_vs_gridmet.py` | `fig15_precip_prism_vs_gridmet.png`, `docs/tables/precip_prism_vs_gridmet_by_ha.csv` | Precipitation at every field, PRISM 800 m against gridMET (the OpenET export): field maps of the difference in mm/yr and %, HA averages, all fields against each other, by calendar month and by year |
| `fig16_otter_prism_vs_gridmet.py` | `fig16_otter_prism_vs_gridmet.png`, `fig17_prism_vs_gridmet_water_balance.png`, `docs/tables/otter_prism_vs_gridmet_by_ha.csv` | OTTER effective precipitation with PRISM 800 m and with gridMET 4 km (field maps, HA map and bars, by year and month), and what the change does to net ET and applied water of irrigated fields. Needs the gridMET comparison run first: `scripts/otter/run_nv_otter_prism_monthly.py --variant v5_gridmet_otter_peff`, then `scripts/phase1_data_preparation/process_field_summaries.py --variant v5_gridmet_otter_peff` |

Run all with `run_figures.sh`.
