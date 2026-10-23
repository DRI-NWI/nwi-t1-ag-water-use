# Code sources

The scripts in `scripts/` are copies of the original scripts, edited in place. The first
copy is committed unmodified (commit 9b60d5d, "Add original scripts, unmodified"). Every
later change is marked `# NWI T1` in the code, so this command shows each edit:

    git diff 9b60d5d -- scripts/

Sources and versions: [../../scripts/ORIGINAL_SOURCES.md](../../scripts/ORIGINAL_SOURCES.md).

## Three steps made faster in process_field_summaries.py

Each faster version is checked against a verbatim copy of the original function
(`tests/original_reference.py`) by `tests/test_fast_equivalence.py`.

| Step | Original | Faster version |
|---|---|---|
| Winter carry-forward | Python loop over every field-month | The same loop compiled with numba (`_carryforward_recurrence`) |
| EToF climatology fill | Row-by-row lookup (`iterrows`) | One table join, with the same window, field, HA and month rules |
| SW/GW split of mixed fields | Copies the rows of every basin-year | Computes the same basin-year ratios, then splits all rows with one join |

## Fixes to the original behavior

| Fix | Where |
|---|---|
| Dynamic basin rules (Fish Lake Valley 117) were read with `load_config()` and no arguments, so they never applied in NWI runs; they now come from the run config | `process_field_summaries.py` |
| Climatologies were never built for the parquet export (the existence check looked for `etrf_` files and the builder read only CSV) | `calculate_field_climatologies.py`, `process_field_summaries.py` |
| `ACRES_IRR` was not split for mixed fields, so both GW and SW halves carried the full value | `apply_gw_sw_separation_dynamic` |
| HA/county summaries summed every numeric column and took `first()` of text columns | `aggregate_summaries` |
| Carry-forward indexed rows by their position in the date-sorted frame, which is only right when the input is already sorted | `apply_winter_moisture_carryforward` |
