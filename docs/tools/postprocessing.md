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
(`.post.bin`/`.post.res`), `hdf5`, `json`, `dat` — sorted by name so
timesteps appear in order (Kratos names them `<ModelPart>_<rank>_<step>.vtk`).

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
