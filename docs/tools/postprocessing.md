# Post-processing

Read simulation output without leaving the assistant. VTK/VTU files (the
default output of every template) are read with `meshio`; convergence data
comes from the solver log.

## results_list

Discover result artifacts in a case directory (recursive).

| Parameter | Type |
| --- | --- |
| `case_dir` | string |

**Returns**: files grouped by kind — `vtk` (`.vtk`/`.vtu`), `gid`
(`.post.bin`/`.post.res`), `hdf5`, `json`, `dat` — sorted lexically by name, which does not guarantee physical-time order (Kratos names them `<ModelPart>_<rank>_<step>.vtk`).

## results_summary

Summarise one VTK/VTU file.

| Parameter | Type | Description |
| --- | --- | --- |
| `file` | string | result file |
| `variable` | string? | restrict statistics to one variable |

**Returns**: `num_points`, `num_cells`, `point_variables`, `cell_variables`,
and per-variable statistics — `min`/`max`/`mean` for scalars,
`min/max/mean_magnitude` plus per-component ranges for vector fields.

```json
// results_summary(".../Structure_0_1.vtk", "DISPLACEMENT") → (excerpt)
{
  "num_points": 105,
  "statistics": { "DISPLACEMENT": {
    "max_magnitude": 4.32e-4,
    "component_min": [-6.2e-5, -4.27e-4, 0.0]
  } }
}
```

## results_probe

Read a variable at one location.

| Parameter | Type | Description |
| --- | --- | --- |
| `file` | string | result file |
| `variable` | string | e.g. `DISPLACEMENT`, `TEMPERATURE` |
| `point` | number[3]? | probe the nearest mesh point to this coordinate |
| `node_index` | int? | or an explicit 0-based point index |

**Returns**: the `value`, the `coordinates` actually used, and
`distance_from_query` so you can tell how near the requested point the
nearest node was.

## results_convergence

Extract nonlinear convergence data from a simulation log.

| Parameter | Type | Description |
| --- | --- | --- |
| `job_id` | string? | read the job's log |
| `log_file` | string? | or an explicit path |

**Returns**: per-step records (`step`, `time`, `iterations`,
`residual_ratios`, `converged`), aggregate counts, and the progress/error
summary. A step that hit `max_iteration` without converging shows
`converged: false` — the usual smoking gun for too-large time steps.

## results_time_history

Probe scalar or vector fields across a physical-time series. Supply:

- `source`: an absolute `result-index.json` path, or a list of
  `{"file": "/absolute/result.vtu", "time": 0.15, "series": "beam"}` records.
- `variable`, `association` (`point`, default, or `cell`), and exactly one of
  `point` (two/three coordinates) or `entity_index` (zero-based).
- `series` when the index contains multiple output series; optional `csv_file`.

Samples are ordered by physical time and contain the source file, time, field
association, selected entity, actual coordinates, distance, and scalar/vector
value. Cell probes use cell centroids. Nearest-location selection is repeated
at every time; it does not track a material particle. Entity indices refer to
exported ordering, not Kratos node IDs. CSV vectors use `value_0`, `value_1`, etc.
Existing CSV files are never overwritten.

The runner writes `result-index.json` in the execution directory for native
VTK output from serial, single-stage analyses, including resumed jobs. Paths
inside an index are relative to that index. Each record has `file`, `series`,
`time`, and `step`, captured after output completes. Multiple model parts or
output streams require a series selection. For external/older files, custom
output processes, and multistage runs, provide explicit file/time records.
Missing/nonfinite/duplicate times are errors; filenames never determine time.

```python
results_time_history(source="/absolute/execution/result-index.json",
                     variable="DISPLACEMENT", point=[1, 0.1, 0],
                     csv_file="/tmp/tip-history.csv")
```

## results_compare

Supply `reference` and `candidate` using the same index-or-records format,
`variable`, and optionally `reference_series` and `candidate_series`.
`mode="field"` compares entire fields; `mode="history"` compares probes selected
with `point` or `entity_index`. Both accept `association="point"` or `"cell"`.

Times must match one-to-one within `time_atol=1e-12`, without interpolation or
unmatched samples. Full-field comparisons require identical point coordinates
and ordering, cell types/connectivity, and field shapes. This also rejects
outputs whose deformed geometry differs; export undeformed meshes for such
comparisons. History comparisons report both selected locations.

Each component passes when
`abs(candidate-reference) <= atol + rtol*abs(reference)`, with defaults
`atol=1e-8` and `rtol=1e-5`. Results include `passed`, maximum absolute error,
RMS error, component failure count, worst time/entity/component, and per-time
summaries. Missing fields, nonfinite values, mesh mismatches, and ambiguous
matches return errors. Optional `csv_file` exports summaries with physical
time, association, both source paths, and component error columns; history
comparisons additionally include both sampled values.

To compare only a final time or the overlapping portion of a restarted run,
pass explicit subsets of index records. Complete indexes with different time
ranges deliberately produce an unmatched-time error.
