# Figures

Each script reads the run outputs (`config/nwi_t1_final.yaml`) and writes to `docs/images/`.
`_mapkit.py` holds the shared projection (Albers, 36°/41° N, 117° W), map layers and loaders.
Map layers are cached in `<run>/figures_cache/`.

| Script | Output | Content |
|---|---|---|
| `fig01_overview_water_rights.py` | `fig01_fields_water_rights.png` | Fields by water-right source, HAs (including their extent outside Nevada), insets 101/153/108 |
| `fig02_ha_maps.py` | `fig02_ha_volumes.png`, `fig03_ha_depths.png`, `fig04_ha_gw_sw.png` | HA maps of every water-balance term (volumes, depths), GW and SW components, mixed SW share |
| `fig05_statewide_timeseries.py` | `fig05_statewide_annual.png`, `fig06_monthly_cycle.png` | Statewide annual series by term and source; mean monthly cycle, subsidy, carry-forward, storage |
| `fig07_allocation.py` | `fig07_allocation_spi.png` | Sept SPI-12 by HA and year, share rule, mixed SW share by year, SPI precipitation area |
| `fig08_data_quality.py` | `fig08_inputs_quality.png` | EToF gap filling, PRISM vs export precipitation, OTTER vs 0.85 × PPT, Peff ratio, efficiency, fallback fields |
| `fig09_irrigated_area.py` | `fig09_irrigated_area_irrmapper_xgbrf.png`, `docs/tables/irrigated_area_by_year.csv` | IrrMapper vs XGBRF irrigated area |
| `prep_ma_dtw_zonal.py` | `<run>/04_gw_subsidy/ma_comparison/` | Ma et al. DTW zonal stats for the same fields (needs `exactextract`) |
| `fig10_gw_subsidy_dtw.py` | `fig10`–`fig14`, `docs/tables/ha_gw_subsidy_new_vs_ma.csv` | GW-ET subsidy by field and HA; new DTW vs Ma et al. |

Run all with `run_figures.sh`.
