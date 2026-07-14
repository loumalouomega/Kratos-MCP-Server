# Project scaffolding

Create and edit the Kratos input files: `ProjectParameters.json` (solver +
processes), `Materials.json` (constitutive laws + properties) and complete
case directories.

## list_templates

List available case templates with descriptions, required applications and
every placeholder with its default.

**Templates**: `structural_static`, `structural_dynamic`, `structural_modal`,
`thermal_transient`, `thermal_stationary`, `fluid_transient`,
`fluid_fractional_step`, `potential_flow`.

::: tip
`potential_flow` requires `CompressiblePotentialFlowApplication` (not always
compiled). `fluid_fractional_step` uses the cheaper pressure-splitting scheme
(good for large meshes) instead of the monolithic solver.
:::

## create_project

Scaffold a complete case directory from a template.

| Parameter | Type | Description |
| --- | --- | --- |
| `directory` | string | case directory (created if missing) |
| `template` | string | template name from `list_templates` |
| `name` | string | problem name (default `case`) |
| `overrides` | object? | placeholder overrides, e.g. `{"end_time": 2.0, "young_modulus": 7e10}` |
| `create_demo_mesh` | bool | also write a small rectangle mesh wired to the defaults, so the case runs out of the box (default false) |

**Returns**: created file list, `required_applications`, and `next_steps`
(create mesh → validate → run).

```json
// create_project("/tmp/my-case", "structural_static", "cantilever",
//                {"fix_model_part": "Structure.left"})
{
  "case_dir": "/tmp/my-case",
  "created": ["/tmp/my-case/ProjectParameters.json", "/tmp/my-case/Materials.json"],
  "next_steps": ["Create the mesh at /tmp/my-case/mesh.mdpa ...", "..."]
}
```

## create_multistage_project

Scaffold a **multi-stage (orchestrated) case** that chains several analyses in
sequence, run with Kratos' native `SequentialOrchestrator`. Use it for a
continuation run (e.g. two load steps) or a coupled workflow where a later
physics reads fields the earlier one wrote on the same mesh.

| Parameter | Type | Description |
| --- | --- | --- |
| `directory` | string | case directory (created if missing) |
| `stages` | array | one entry per stage: `{"name": "<id>", "template": "<template>", "overrides": {...}}` |
| `name` | string | base problem name (default `case`) |
| `stage_checkpoints` | bool | write per-stage checkpoints (default false) |
| `create_demo_mesh` | bool | also write a small rectangle mesh (from the first stage's template) so the case runs out of the box (default false) |

**Mesh sharing**: the first stage imports its mesh; a later stage whose solver
`model_part_name` matches an earlier stage's **reuses** that already-populated
model part (`input_type: "use_input_model_part"`) — this is how state flows
between stages. A later stage with a distinct `model_part_name` imports its own
mesh instead.

The composed file uses the `orchestrator` + `stages` + `execution_list`
structure; `run_simulation` and `validate_case` handle it transparently.

```json
// create_multistage_project("/tmp/ms", [
//   {"name": "load_1", "template": "structural_static", "overrides": {"end_time": 1.0}},
//   {"name": "load_2", "template": "structural_static", "overrides": {"end_time": 2.0}}])
{ "case_dir": "/tmp/ms", "execution_list": ["load_1", "load_2"],
  "created": ["/tmp/ms/ProjectParameters.json", "/tmp/ms/Materials.json"], "next_steps": ["..."] }
```

## create_project_parameters

Render only a ProjectParameters.json (returned, and optionally written to
`output_file`). Same `template`/`overrides` semantics as `create_project`.

## create_materials

Write a Materials.json from a list of material specs.

| Parameter | Type | Description |
| --- | --- | --- |
| `output_file` | string | where to write |
| `materials` | array | one entry per model part (below) |

Each entry: `model_part_name` (e.g. `Structure.domain`); then either a
`preset` (a name from `list_material_presets`, which fills `constitutive_law`
and default `variables`) **or** an explicit `constitutive_law` (thermal
problems have none) plus `variables` (e.g. `{"YOUNG_MODULUS": 2.1e11,
"POISSON_RATIO": 0.3}`); optional `properties_id`. With a preset, any
`variables` you pass override the preset's defaults.

## list_material_presets

List the curated material presets (constitutive law + default variables) usable
as `preset` in `create_materials`: linear elastic (3D / plane strain / plane
stress), small- and finite-strain Von Mises plasticity, isotropic damage, and
Newtonian fluids. Cross-check the law names with `kratos_list_constitutive_laws`
for your compiled build. These are seeded from the sibling
[Flowgraph](https://github.com/KratosMultiphysics/Flowgraph) material node library.

## list_linear_solver_presets

List curated `linear_solver_settings` presets — drop-in blocks for
`solver_settings.linear_solver_settings`. Serial: `sparse_lu`, `skyline_lu`,
`amgcl`, `cg`, `bicgstab`. MPI/Trilinos: `amgcl_mpi`, `amesos`, `aztec`, `ml`.

## add_boundary_condition

Insert a boundary condition or load process block into an existing
ProjectParameters.json.

| Parameter | Type | Description |
| --- | --- | --- |
| `parameters_file` | string | the file to edit |
| `kind` | string | see table below |
| `model_part` | string | dotted target, e.g. `Structure.right` |
| `value` | number \| number[3]? | for fix/prescribe kinds |
| `modulus`, `direction` | number, number[3] | for directional loads |
| `interval` | [start, end]? | default `[0.0, "End"]` |
| `process_list` | string? | override the target list |

| kind | variable | typical use |
| --- | --- | --- |
| `fix_displacement` / `prescribed_displacement` | DISPLACEMENT | supports / imposed motion |
| `fix_velocity` / `inlet_velocity` | VELOCITY | fluid walls / inlets |
| `outlet_pressure` | PRESSURE | fluid outlets |
| `fix_temperature` | TEMPERATURE | thermal Dirichlet |
| `point_load` / `line_load` / `surface_load` | POINT_LOAD / LINE_LOAD / SURFACE_LOAD | directional loads on conditions (need `modulus` + `direction`) |
| `pressure_load` | POSITIVE_FACE_PRESSURE | pressure on faces |
| `surface_heat_flux` | FACE_HEAT_FLUX | thermal Neumann on conditions |
| `volume_heat_source` | HEAT_FLUX | volumetric heating |
| `self_weight` | VOLUME_ACCELERATION | gravity (`modulus` defaults to 9.81) |

```json
// add_boundary_condition(file, "line_load", "Structure.right",
//                        modulus=1e6, direction=[0, -1, 0])
{ "process_list": "loads_process_list", "added": { "python_module": "assign_vector_by_direction_to_condition_process", "...": "..." } }
```

::: warning
Condition-based loads (`point/line/surface_load`, `pressure_load`,
`surface_heat_flux`) need actual load-bearing conditions in the target
region — see [the MDPA guide](/guide/mdpa-format#naming-conventions).
:::

::: tip
When the Kratos source tree is available, the inserted block's `Parameters`
are auto-completed with the process' real `default_settings` (via
[`kratos_get_process_defaults`](/tools/environment#kratos-get-process-defaults))
for any key you did not set, so blocks stay correct even as Kratos evolves.
Without a source tree it falls back to the built-in defaults.
:::

## add_output_process

Add an output process to a ProjectParameters.json.

| Parameter | Type | Description |
| --- | --- | --- |
| `parameters_file` | string | file to edit |
| `format` | string | `vtk` (ParaView), `json` (variable time series), `point` (probe a coordinate) |
| `variables` | string[]? | variables to write |
| `model_part` | string? | defaults to the solver root |
| `output_path` | string | for `vtk` (default `vtk_output`) |
| `output_file` | string? | for `json`/`point` |
| `position` | number[3]? | for `point` |

## validate_project_parameters

*deep mode needs Kratos*

Validate a ProjectParameters.json without running anything:

1. JSON syntax and required top-level keys,
2. referenced mesh/materials files exist and parse,
3. every `model_part_name` in processes/materials matches a mesh
   submodelpart,
4. (`deep: true`, default) `solver_settings` validated against the solver's
   `GetDefaultParameters()` inside a Kratos worker.

Multi-stage (`orchestrator`/`stages`) cases are recognised automatically and
validated per stage (structure, execution list, per-stage mesh/material refs);
per-stage Kratos-side solver validation is deferred to run time.

**Returns**: `{valid, issues: [...], warnings: [...]}`.
