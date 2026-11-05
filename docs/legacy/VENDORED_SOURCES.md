# Code sources

The scripts in `scripts/` are copies of the original scripts, edited in place. The copies
were first committed unchanged (commit 9b60d5d, "Add original scripts, unmodified"). Every
later change is marked `# NWI T1` in the code, and this command shows each edit:

    git diff 9b60d5d -- scripts/

Sources and versions: [../../scripts/ORIGINAL_SOURCES.md](../../scripts/ORIGINAL_SOURCES.md).

## Three steps made faster in process_field_summaries.py

`tests/test_fast_equivalence.py` checks that each faster version gives the same result as an
exact copy of the original function (`tests/original_reference.py`).

| Step | Original | Faster version |
|---|---|---|
| Winter carry-forward | Python loop over every field-month | The same loop, compiled to machine code with the `numba` package (`_carryforward_recurrence`) |
| Filling gaps from the field's calendar-month average EToF | Looks up each missing row one at a time | Matches all missing rows to the averages in one table merge, by the same period, field, HA and month |
| GW/SW split of mixed fields | Copies the rows of each HA and year separately | Computes the same SW share for each HA and year, then splits all rows in one table merge |

## Fixes to the original behavior

| Fix | Where |
|---|---|
| The Fish Lake Valley (117) rule was read with `load_config()` and no arguments, which loads the default settings instead of the NWI run's, so it never applied in NWI runs; it now comes from the run config | `process_field_summaries.py` |
| The calendar-month EToF averages were never built for the OpenET export in parquet format: the check for existing averages looked for files named `etrf_`, and the builder read only CSV | `calculate_field_climatologies.py`, `process_field_summaries.py` |
| `ACRES_IRR` was not multiplied by the share for mixed fields, so both the GW and the SW rows carried the field's full irrigated acres | `apply_gw_sw_separation_dynamic` |
| HA and county summaries added up every numeric column and took the first field's value for text columns | `aggregate_summaries` |
| The carry-forward wrote results back by row position after sorting by date, which is only right when the input was already sorted that way | `apply_winter_moisture_carryforward` |
