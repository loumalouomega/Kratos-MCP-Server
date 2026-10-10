# Interactive sessions

`run_simulation` runs a case to completion as a detached batch job. A
**session** is the complementary model: the server keeps a live Kratos
`Model` and an initialized `AnalysisStage` in a long-lived worker process, and
you drive it one request at a time — advance a few steps, look at the state,
change something, continue. It is the direct interface to Kratos' Python layer
that the batch runner deliberately does not expose.

| | Batch job (`run_simulation`) | Interactive session (`session_start`) |
| --- | --- | --- |
| Granularity | the whole `Run()` | one `AnalysisStage` step at a time |
| Inspect mid-run | parse logs / output files | query the live model (fields, nodes, elements) |
| Modify mid-run | no | set / fix nodal values, optional Python exec |
| Survives a server restart | yes (`~/.kratos-mcp/jobs/`) | **no** (the process is owned by the server) |
| Cases | single-stage, multi-stage and CoSimulation, MPI, studies, checkpoints | single-stage, serial/OpenMP |
| Best for | production runs, sweeps | exploration, debugging, load stepping, steering |

Everything here needs a Kratos build (*needs Kratos*), and — like every Kratos
access in this server — runs in a subprocess (`session_worker.py`), never in the
server process. See [Architecture](/guide/architecture#interactive-sessions)
for the protocol. A worked end-to-end example is the
[interactive session tutorial](/tutorials/interactive-session).

## Lifecycle at a glance

```
session_start ─▶ session_advance ─▶ session_field_summary / session_inspect_node ─┐
                       ▲                                                          │
                       └──── session_set_nodal_values (optional) ◀────────────────┘
                                         │
                                  session_close  (Finalize() + cleanup)
```

Sessions are single-threaded on the Kratos side: requests are executed in
order, and a request that exceeds its `timeout` returns `busy: true` while the
worker keeps going (see [`session_advance`](#session-advance)). At most
`KRATOS_MCP_MAX_SESSIONS` (default 4) sessions can be open at once; an idle
worker exits by itself after `KRATOS_MCP_SESSION_IDLE_TIMEOUT` seconds
(default 3600).

## session_start

*Needs Kratos.* Load the case, construct the `AnalysisStage` (class taken from
`analysis_stage`, `analysis_class`, `analysis_type` or the solver type — the
same resolution as `run_simulation`) and run `Initialize()`. Nothing is solved
yet.

| Parameter | Type | Description |
| --- | --- | --- |
| `case_dir` | string | absolute case directory |
| `parameters_file` | string | default `ProjectParameters.json` |
| `analysis_type` | string? | `structural` / `fluid` / `thermal` / `potential_flow` |
| `analysis_class` | string? | explicit `module.path:ClassName` |
| `isolate` | bool | work on a private copy of the case under `~/.kratos-mcp/sessions/<id>/execution/`, leaving the original files and outputs untouched |
| `timeout` | float | seconds to wait for `Initialize()` (default 300) |

**Returns** `session_id`, `state: "ready"`, `analysis_class`, `time`,
`end_time`, `step`, node/element/condition counts, the main `model_part` and all
`model_parts`, and the `log_file` that receives the Kratos banner and solver
output. Multi-stage cases (`orchestrator` + `stages`), CoSimulation
(coupled-solver) cases and `parallel_type: "MPI"` cases are rejected with an explanatory error — use
`run_simulation` for those.

```json
// session_start("/tmp/case") → (excerpt)
{ "session_id": "20261010-105612-f8d621", "state": "ready",
  "analysis_class": "KratosMultiphysics.StructuralMechanicsApplication.structural_mechanics_analysis:StructuralMechanicsAnalysis",
  "time": 0.0, "end_time": 1.0, "step": 0, "finished": false,
  "model_part": "Structure", "nodes": 105, "elements": 80, "conditions": 48,
  "model_parts": ["Structure", "Structure.bottom", "Structure.top",
                  "Structure.left", "Structure.right", "Structure.domain"] }
```

## session_advance

*Needs Kratos.* Run solution steps with exactly the sequence of
`AnalysisStage.RunSolutionLoop`: `AdvanceInTime` → `InitializeSolutionStep` →
`SolveSolutionStep` → `FinalizeSolutionStep` → `OutputSolutionStep`. Stops
early at `end_time`.

| Parameter | Type | Description |
| --- | --- | --- |
| `session_id` | string | from `session_start` |
| `steps` | int | number of steps (default 1) |
| `until_time` | float? | advance until the model time reaches this value instead of a step count |
| `output` | bool | run the configured output processes (VTK/GiD/restart) after each step (default true) |
| `timeout` | float | seconds to wait (default 600) |

**Returns** `advanced`, the new `time`, `end_time`, `finished`, `stop_reason`
(`steps_completed`, `until_time_reached` or `end_time_reached`) and a
per-step `history` of `{step, time, converged, wall_seconds}`. The history is
also appended to `sessions/<id>/history.jsonl`.

If the call exceeds `timeout` it returns `{"error": ..., "busy": true}`: the
worker is **still running** the request. Poll `session_status` until `busy` is
`false`, then continue; further requests queue behind it.

```json
// session_advance(sid, steps=2) → (excerpt)
{ "advanced": 2, "time": 0.25, "end_time": 1.0, "finished": false,
  "stop_reason": "steps_completed",
  "history": [ { "step": 1, "time": 0.125, "converged": true, "wall_seconds": 0.0039 },
               { "step": 2, "time": 0.25,  "converged": true, "wall_seconds": 0.0026 } ] }
```

## session_get_parameters

Read the live, defaults-applied `ProjectParameters` of the session.

| Parameter | Type | Description |
| --- | --- | --- |
| `session_id` | string | |
| `path` | string | optional JSON Pointer, e.g. `/solver_settings/time_stepping`; empty = everything |

**Returns** `{"path": ..., "parameters": ...}`.

## session_model_info

| Parameter | Type | Description |
| --- | --- | --- |
| `session_id` | string | |
| `model_part` | string? | default: the solver's main model part |
| `depth` | int | how many levels of sub model parts to list (default 4) |

**Returns** node/element/condition counts for the model part and its sub model
parts, `process_info` (`TIME`, `STEP`, `DELTA_TIME`, `DOMAIN_SIZE`),
`buffer_size`, and `available_model_parts`.

## session_field_summary

Statistics of a field at the **current** time, with *where* the extremes are.

| Parameter | Type | Description |
| --- | --- | --- |
| `session_id` | string | |
| `variable` | string | Kratos variable name, e.g. `DISPLACEMENT`, `PRESSURE`, `VON_MISES_STRESS` |
| `model_part` | string? | default: main model part; sub model parts like `Structure.left` work |
| `location` | string | `nodes` (solution-step values, default) or `elements` (integration-point results; the worst Gauss point per element) |

**Returns** `count`, `kind` (`scalar`/`vector`), `min`, `max`, `mean` (vectors by
magnitude), `argmax` / `argmin` (`id`, raw `value`, `magnitude`, node
`coordinates`) and, for vectors, `component_max` / `component_min`.

```json
// session_field_summary(sid, "DISPLACEMENT") at t = 0.25 → (excerpt)
{ "count": 105, "kind": "vector", "max": 0.0019721555749972535,
  "argmax": { "id": 21, "coordinates": [4.99985, -0.001967, 0.0],
              "value": [-0.0001468, -0.0019667, 0.0] },
  "component_min": [-0.0001468, -0.0019667, 0.0] }
```

## session_inspect_node

| Parameter | Type | Description |
| --- | --- | --- |
| `session_id` | string | |
| `node_id` | int | |
| `variables` | string[]? | default: `DISPLACEMENT`, `REACTION`, `VELOCITY`, `PRESSURE`, `TEMPERATURE` — whichever the model part has |
| `model_part` | string? | |

**Returns** current and `initial_coordinates`, the `values` of each variable,
and `fixed` (a boolean per scalar DOF, `[x, y, z]` booleans for vector DOFs).

## session_inspect_element

| Parameter | Type | Description |
| --- | --- | --- |
| `session_id` | string | |
| `element_id` | int | |
| `variables` | string[]? | integration-point results, e.g. `["VON_MISES_STRESS"]` |
| `model_part` | string? | |

**Returns** `node_ids`, `properties_id`, `active` and, per variable, the list of
values at the integration points.

## session_set_nodal_values

Edit the live state. Nothing on disk changes.

| Parameter | Type | Description |
| --- | --- | --- |
| `session_id` | string | |
| `variable` | string | nodal solution-step variable |
| `value` | number \| number[] | a number, or `[x, y, z]` for vector variables (a single number is broadcast) |
| `node_ids` | int[]? | nodes to change; omit for every node of `model_part` |
| `model_part` | string? | |
| `fix` | bool \| bool[]? | `true`/`false` fixes/frees the DOF (a list of three booleans sets each vector component); omit to leave fixity as is |

Takes effect from the next `session_advance`: pin a support, impose a
temperature, perturb a field and see the response.

## session_exec_python

Run arbitrary Python inside the session's Kratos process — the escape hatch
for anything the typed tools do not cover. Variables in scope: `KM`
(KratosMultiphysics), `model`, `stage` (the `AnalysisStage`), `session`,
`parameters`. Assign a JSON-serialisable value to `result` to receive it;
`print()` output comes back as `stdout`.

::: warning Opt-in
This executes arbitrary code as the server's user, so it is **disabled unless the
MCP server is started with `KRATOS_MCP_ALLOW_EXEC=1`**, e.g.
`claude mcp add kratos -e KRATOS_MCP_ALLOW_EXEC=1 -- uvx kratos-mcp-server`.
Without it the tool returns an explanatory error (the worker enforces this too).
:::

| Parameter | Type | Description |
| --- | --- | --- |
| `session_id` | string | |
| `code` | string | Python source |

```json
// session_exec_python(sid, "print('hi'); result = model['Structure'].NumberOfNodes()")
{ "result": 105, "stdout": "hi\n" }
```

## session_status

| Parameter | Type | Description |
| --- | --- | --- |
| `session_id` | string | |
| `log_tail` | int | include the last N lines of the session log (default 0) |

**Returns** `state` (`ready`, `closed`, `dead`, `lost`), `pid`, `case_dir`,
`busy`, `steps_recorded`, `log_file`; for a dead session also `returncode`,
`death_reason` and the `log_tail`. `lost` means the session belonged to a
previous server process.

## session_list

`session_list(state=None)` → newest-first list of `{session_id, state, case_dir,
analysis_class, age_seconds}`.

## session_close

| Parameter | Type | Description |
| --- | --- | --- |
| `session_id` | string | |
| `finalize` | bool | run `AnalysisStage.Finalize()` first (default true) so output processes flush; then the worker's whole process group is terminated |

Idempotent. Open sessions are also closed when the MCP server exits.

## Failure behaviour

- An error inside an op (unknown variable, missing node, …) is returned as
  `{"error": "KeyError: ..."}` and the session stays usable.
- If the Kratos process crashes (a C++ abort), the session becomes `dead`; the
  error and `session_status` carry the exit code and the tail of `session.log`.
  The MCP server is unaffected.
- Sessions are in-memory: after a server restart `session_list` shows them as
  `lost`. Use `isolate=True` plus `session_close` (or the saved
  `history.jsonl` and output files) to keep results; for resumable work use
  [checkpoints](/tools/simulation) with batch jobs.
